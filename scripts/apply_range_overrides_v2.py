#!/usr/bin/env python3
"""Apply authenticated source-time overrides with fail-closed publication."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import tempfile

from runtime_meta import CacheIdentityError, invalidation_path, load_checked_json, metadata_path, validate_cache, write_metadata
from runtime_paths import resolve_runtime_paths
from runtime_publication import BuildSnapshot, generator_lock, publish_pair


class RangeOverrideError(ValueError):
    pass


# Absolute seconds: tolerate binary noise in millisecond timestamps, not a
# millisecond of overlap. Never scale this tolerance with timestamp magnitude.
OVERLAP_TOLERANCE_SECONDS = 1e-9


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise RangeOverrideError('range_time_must_be_finite_number')
    return float(value)


def product_index(value):
    if type(value) is not int or value < 1:
        raise RangeOverrideError('product_index_must_be_positive_integer')
    return value


def source_map(index):
    rows = index.get('sources')
    if not isinstance(rows, list) or not rows:
        raise RangeOverrideError('subtitle_sources_missing')
    sources = {}
    previous_end = None
    for row in rows:
        if not isinstance(row, dict):
            raise RangeOverrideError('subtitle_source_must_be_object')
        stem = row.get('source_stem')
        if not isinstance(stem, str) or not stem or stem in sources:
            raise RangeOverrideError('source_stem_missing_or_duplicate')
        start, end = number(row.get('global_start')), number(row.get('global_end'))
        if start < 0 or end <= start or previous_end is not None and start < previous_end:
            raise RangeOverrideError('source_bounds_invalid_or_overlapping')
        sources[stem] = row
        previous_end = end
    return sources


def split_range(start: float, end: float, sources: list[dict]) -> list[dict]:
    rows = []
    for source in sources:
        left = max(start, float(source['global_start']))
        right = min(end, float(source['global_end']))
        if right > left:
            rows.append({'media_stem': source['source_stem'],
                         'start': round(left - float(source['global_start']), 3),
                         'end': round(right - float(source['global_start']), 3)})
    return rows


def global_time(point: dict, sources: dict[str, dict]) -> float:
    if (not isinstance(point, dict) or not isinstance(point.get('source_stem'), str)
            or point['source_stem'] not in sources):
        raise RangeOverrideError('override_source_unknown')
    source = sources[point['source_stem']]
    local = number(point.get('time'))
    duration = number(source['global_end']) - number(source['global_start'])
    if local < 0 or local > duration + 0.001:
        raise RangeOverrideError('override_time_outside_source')
    return round(float(source['global_start']) + min(local, duration), 3)


def validate_range(row, sources):
    start, end = number(row.get('global_start')), number(row.get('global_end'))
    if end <= start:
        raise RangeOverrideError('range_end_must_exceed_start')
    expected = split_range(start, end, list(sources.values()))
    if not expected or any(item['end'] <= item['start'] for item in expected):
        raise RangeOverrideError('empty_or_subprecision_range')
    covered = sum(item['end'] - item['start'] for item in expected)
    if abs(covered - (end - start)) > 0.002 * len(expected):
        raise RangeOverrideError('range_outside_sources_or_crosses_gap')
    actual = row.get('ranges')
    if not isinstance(actual, list) or len(actual) != len(expected):
        raise RangeOverrideError('source_ranges_inconsistent')
    for item, wanted in zip(actual, expected):
        if (not isinstance(item, dict) or not isinstance(item.get('media_stem'), str)
                or item['media_stem'] not in sources):
            raise RangeOverrideError('range_source_unknown')
        left, right = number(item.get('start')), number(item.get('end'))
        if (right <= left or item['media_stem'] != wanted['media_stem']
                or abs(left - wanted['start']) > 0.001 or abs(right - wanted['end']) > 0.001):
            raise RangeOverrideError('source_ranges_inconsistent')


def validate_global_ranges(products):
    """Check all individually validated ranges in live order without editing them."""
    ordered = sorted(products, key=lambda row: (row['global_start'], row['product_index']))
    furthest = None
    for current in ordered:
        if furthest is not None:
            if current['global_start'] < furthest['global_end'] - OVERLAP_TOLERANCE_SECONDS:
                raise RangeOverrideError(
                    f"cross_product_range_overlap: product_index={furthest['product_index']},"
                    f"{current['product_index']}")
        # Keep the furthest end so a contained range cannot hide a later overlap.
        if furthest is None or current['global_end'] > furthest['global_end']:
            furthest = current


def build_model(document, index, titles, override_document):
    sources = source_map(index)
    title_by_id = {row['product_index']: row for row in titles['products']}
    products = {}
    rows = document.get('products')
    if not isinstance(rows, list):
        raise RangeOverrideError('products_must_be_list')
    for row in rows:
        if not isinstance(row, dict):
            raise RangeOverrideError('product_must_be_object')
        pid = product_index(row.get('product_index'))
        if pid not in title_by_id:
            raise RangeOverrideError('unknown_product_index')
        if pid in products:
            raise RangeOverrideError('duplicate_product_index')
        products[pid] = copy.deepcopy(row)
    overrides = override_document.get('products')
    if not isinstance(overrides, list) or not overrides:
        raise RangeOverrideError('overrides_must_be_nonempty_list')
    seen = set()
    for override in overrides:
        if not isinstance(override, dict):
            raise RangeOverrideError('override_must_be_object')
        pid = product_index(override.get('product_index'))
        if pid not in title_by_id:
            raise RangeOverrideError('unknown_product_index')
        if pid in seen:
            raise RangeOverrideError('duplicate_override')
        seen.add(pid)
        if 'encoding_id' in override or 'title' in override:
            raise RangeOverrideError('override_identity_fields_forbidden')
        reason = override.get('reason')
        if reason is not None and not isinstance(reason, str):
            raise RangeOverrideError('override_reason_must_be_text')
        start, end = global_time(override.get('start'), sources), global_time(override.get('end'), sources)
        if end <= start:
            raise RangeOverrideError('range_end_must_exceed_start')
        product = products.setdefault(pid, {})
        product.update(global_start=start, global_end=end, product_range_duration=round(end-start, 3),
                       product_start_anchor=start, range_basis='explicit_local_source_time_override',
                       ranges=split_range(start, end, list(sources.values())),
                       confidence='locally_verified_override', override_reason=reason)
    for pid, product in products.items():
        # Business identity always comes from authenticated titles, including additions.
        product.update({key: title_by_id[pid][key] for key in ('product_index', 'encoding_id', 'title')})
        validate_range(product, sources)
    validate_global_ranges(products.values())
    result = copy.deepcopy(document)
    result['products'] = [products[pid] for pid in sorted(products)]
    return result


def signature(path):
    return (hashlib.sha256(path.read_bytes()).digest(),
            hashlib.sha256(metadata_path(path).read_bytes()).digest(),
            invalidation_path(path).exists())


def apply_overrides(paths, overrides_path):
    with generator_lock(paths):
        snapshot = BuildSnapshot(paths)
        output = paths.cache_dir / 'product_ranges.json'
        inputs = [output, paths.cache_dir / 'subtitle_index.json', paths.cache_dir / 'titles.json']
        overrides_path = paths.runtime_argument(overrides_path)
        if overrides_path in {path.resolve() for path in inputs}:
            raise RangeOverrideError('override_input_aliases_pipeline_artifact')
        inputs.append(overrides_path)
        signatures, documents = {}, []
        for path in inputs:
            before = signature(path) if metadata_path(path).is_file() else None
            document = load_checked_json(paths, path, expected=snapshot.identity,
                                         artifact_schema_version=1 if path in inputs[:2] else None)
            if before != signature(path):
                raise CacheIdentityError('artifact_changed_during_load')
            signatures[path] = before
            documents.append(document)
        model = build_model(*documents)
        with tempfile.TemporaryDirectory(prefix='.range-overrides-', dir=paths.cache_dir) as temporary:
            staged = Path(temporary) / output.name
            staged.write_text(json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
            staged_meta = write_metadata(paths, staged, 'apply_range_overrides_v2.py', identity=snapshot.identity)
            validate_cache(paths, staged, expected=snapshot.identity)
            # Validate the authenticated output snapshot until publication starts;
            # subsequent checks exclude only the output pair being replaced.
            first_check = True
            def check_inputs():
                nonlocal first_check
                snapshot.check()
                for path, before in signatures.items():
                    if path == output and not first_check:
                        continue
                    if signature(path) != before:
                        raise CacheIdentityError('artifact_changed_during_generation')
                first_check = False
            publish_pair(staged, staged_meta, output, before_commit=check_inputs)
        return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.')
    parser.add_argument('--workspace')
    parser.add_argument('--overrides', required=True)
    args = parser.parse_args()
    apply_overrides(resolve_runtime_paths(args.root, args.workspace), args.overrides)
    print('Range overrides published with current identity')


if __name__ == '__main__':
    main()
