"""Product adapters for authenticated ranges, candidates, local ASR and physical plans."""
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
import time
from project_registry import digest, atomic_json, workspace_lock
from runtime_meta import CacheIdentityError, expected_identity, load_checked_json, _sha256
from product_checkpoints import load_stage, publish_stage, capture_stage, current_range_path
from codex_worker import verify_receipt
from build_expanded_codex_request import build_wide_candidates
from publish_confirmed_ranges import (build_confirmed_ranges, build_semantic_inferred_ranges,
                                     validate_semantic_inferred_document)
from refine_plans_v2 import normalize_physical_clips, media_duration
from sentence_boundaries import SELECTION_RULE
from compute_metrics import span
from compute_store import media_signature, identity as compute_identity, cached, containing_asr
from asr_identity import model_identity as asr_model_identity, PARAMETERS as ASR_PARAMETERS

ASR_SCOPE_POLICY = 'codex-boundary-nearby-edges-v2-selected-range-identity'
RANGE_MATCHING_PROTOCOL = 'codex-selling-point-search-envelope-v5-neighbor-inference'
RANGE_MATCHING_CHUNK_PROTOCOL = 'codex-selling-point-search-envelope-v5-byte-bounded-overlap-chunks'
RANGE_BOUNDARY_REPAIR_PROTOCOL = 'codex-neighbor-boundary-inference-once-v3-search-envelope'
NEIGHBOR_INFERRED_BOUNDARY_MODE = 'neighbor_inferred_search_envelope'
RANGE_TASK_MAX_BYTES = 120000
RANGE_CHUNK_TASK_MAX_BYTES = 64000
RANGE_CHUNK_OVERLAP_CUES = 8
RANGE_CHUNK_MAX_COUNT = 48
RANGE_BOUNDARY_EXTENSION_SECONDS = 30.0


def _json_utf8_bytes(value):
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))


def extend_range_payload_with_adjacent_context(payload, windows, index, *,
                                               neighbor_boundaries=(),
                                               seconds=RANGE_BOUNDARY_EXTENSION_SECONDS,
                                               max_bytes=RANGE_TASK_MAX_BYTES):
    """Build one bounded follow-up request with exact cues around source edges.

    The first range review remains authoritative unless it explicitly reports
    incomplete evidence. This helper adds only indexed subtitle cues just
    outside candidate windows and, when a window reaches a file edge, the
    corresponding edge of one adjacent source file. It never invents times or
    clips subtitle text.
    """
    sources = list(index.get('sources') or [])
    if not sources or seconds <= 0:
        return None, {'context_windows': 0, 'cue_count': 0, 'reason': 'no_source_context'}
    source_positions = {row.get('source_stem'): i for i, row in enumerate(sources)}
    source_by_stem = {row.get('source_stem'): row for row in sources}
    additions = {}

    def add_region(stem, start, end, *, origin):
        if not stem or end <= start:
            return
        key = (str(stem), round(float(start), 3), round(float(end), 3))
        bucket = additions.setdefault(key, {'source_stem': str(stem),
            'global_start': float(start), 'global_end': float(end),
            'origin': origin, 'cues': []})
        for cue_id, cue in enumerate(index.get('cues') or []):
            if cue.get('source_stem') != stem:
                continue
            cue_start = float(cue.get('global_start', 0))
            cue_end = float(cue.get('global_end', cue_start))
            if cue_end <= start or cue_start >= end:
                continue
            bucket['cues'].append([cue_id, float(cue.get('start', 0)),
                                   float(cue.get('end', cue.get('start', 0))),
                                   str(cue.get('text') or '')])

    for window in windows:
        stem = window.get('source_stem')
        source = source_by_stem.get(stem)
        if source is None:
            continue
        source_start = float(source.get('global_start', 0))
        source_end = float(source.get('global_end', source_start))
        window_start = float(window.get('global_start', source_start))
        window_end = float(window.get('global_end', source_end))
        left = max(source_start, window_start - seconds)
        right = min(source_end, window_end + seconds)
        if left < window_start:
            add_region(stem, left, window_start, origin='same_source_before')
        if right > window_end:
            add_region(stem, window_end, right, origin='same_source_after')

        position = source_positions.get(stem)
        if position is None:
            continue
        if position > 0 and window_start <= source_start + seconds:
            previous = sources[position - 1]
            prev_start = float(previous.get('global_start', 0))
            prev_end = float(previous.get('global_end', prev_start))
            add_region(previous.get('source_stem'), max(prev_start, prev_end - seconds),
                       prev_end, origin='adjacent_source_before')
        if position + 1 < len(sources) and window_end >= source_end - seconds:
            following = sources[position + 1]
            next_start = float(following.get('global_start', 0))
            next_end = float(following.get('global_end', next_start))
            add_region(following.get('source_stem'), next_start,
                       min(next_end, next_start + seconds), origin='adjacent_source_after')

    context = []
    seen_cues = set()
    for key in sorted(additions):
        row = additions[key]
        cues = []
        for cue in row['cues']:
            cue_key = (row['source_stem'], cue[1], cue[2], cue[3])
            if cue_key not in seen_cues:
                seen_cues.add(cue_key)
                cues.append(cue)
        if cues:
            context.append({**{k: row[k] for k in ('source_stem', 'global_start',
                                                    'global_end', 'origin')},
                            'cue_encoding': ['cue_id', 'local_start', 'local_end', 'exact_text'],
                            'cues': cues})
    cue_count = sum(len(row['cues']) for row in context)
    if not context:
        return None, {'context_windows': 0, 'cue_count': 0, 'reason': 'no_adjacent_indexed_cues'}

    task = json.loads(json.dumps(payload, ensure_ascii=False))
    task.pop('task_digest', None)
    task['boundary_context_repair'] = {
        'protocol': RANGE_BOUNDARY_REPAIR_PROTOCOL,
        'attempt': 1,
        'expanded_seconds_per_edge': float(seconds),
        'evidence': context,
    }
    if neighbor_boundaries:
        task['neighbor_product_boundaries'] = list(neighbor_boundaries)
    task.setdefault('instructions', []).append(
        '这是唯一一次邻接上下文补证请求。boundary_context_repair.evidence 和 neighbor_product_boundaries 来自原字幕索引。'
        '结合上一个/下一个商品可核验的讲解边界推断当前商品的候选搜索包络；整体起点或终点允许不精确。'
        '只要当前商品身份有标题/卖点证据，就返回最佳可用搜索边界；只有商品归属仍有实质冲突时才返回 REPAIRABLE 且 range_decision 置 null。')
    task['task_digest'] = digest(task)
    payload_bytes = _json_utf8_bytes(task)
    if payload_bytes > max_bytes:
        return None, {'context_windows': len(context), 'cue_count': cue_count,
                      'payload_bytes': payload_bytes, 'reason': 'one_shot_context_exceeds_payload_budget'}
    return task, {'context_windows': len(context), 'cue_count': cue_count,
                  'payload_bytes': payload_bytes, 'reason': None}


def range_boundary_context_trigger(response):
    """Return a narrow trigger for the single adjacent-context follow-up.

    Legacy REPAIRABLE responses remain eligible. Some cached Codex receipts
    classify an absent endpoint as BLOCKED, so allow that verdict only when an
    issue explicitly says boundary/adjacent context is missing. Other BLOCKED
    causes continue to fail without another worker request.
    """
    if not isinstance(response, dict) or response.get('range_decision'):
        return None
    verdict = response.get('verdict')
    if verdict == 'REPAIRABLE':
        return 'repairable_verdict'
    if verdict != 'BLOCKED':
        return None
    raw_issues = response.get('issues') or []
    if isinstance(raw_issues, str):
        raw_issues = [raw_issues]
    if not isinstance(raw_issues, (list, tuple)):
        return None
    issues = [str(issue).casefold() for issue in raw_issues]
    missing_markers = ('缺少', '缺失', '缺乏', '不足', '未提供', '没有提供',
                       'missing', 'insufficient', 'not provided', 'lacks', 'lack of')
    boundary_markers = ('边界上下文', '结束边界', '相邻字幕', '后续字幕', '后续上下文',
                        'boundary context', 'end boundary', 'adjacent subtitle',
                        'following subtitle', 'ending context')
    if any(any(marker in issue for marker in missing_markers)
           and any(marker in issue for marker in boundary_markers)
           for issue in issues):
        return 'blocked_missing_boundary_context'
    return None


def pack_semantic_range_chunks(product, windows, source_bounds, project_key, *,
                               target_bytes=RANGE_CHUNK_TASK_MAX_BYTES,
                               overlap_cues=RANGE_CHUNK_OVERLAP_CUES,
                               max_chunks=RANGE_CHUNK_MAX_COUNT):
    """Pack all indexed cue evidence into bounded, overlapping worker tasks."""
    chunks = []
    for window_index, window in enumerate(windows, 1):
        cues = window.get('cue_rows', [])
        if not cues:
            continue
        source = source_bounds.get(window['source_stem'])
        if source is None:
            raise CacheIdentityError('range_chunk_source_bounds_missing')
        cursor = 0
        part = 0
        while cursor < len(cues):
            part += 1
            left = max(0, cursor - overlap_cues)
            accepted = None
            candidate_end = cursor + 1
            while candidate_end <= len(cues):
                right = min(len(cues), candidate_end + overlap_cues)
                core = cues[cursor:candidate_end]
                payload = {
                    'schema_version': 1, 'role': 'semantic_range_chunk',
                    'range_matching_protocol': RANGE_MATCHING_CHUNK_PROTOCOL,
                    'review_scope': 'product_boundary_chunk_review',
                    'project_key': project_key, 'product': product,
                    'source': {'source_stem': window['source_stem'],
                               'global_start': float(source['global_start']),
                               'global_end': float(source['global_end'])},
                    'candidate_window': {
                        'window_id': str(window['window_id']),
                        'global_start': float(window['global_start']),
                        'global_end': float(window['global_end']),
                        'boundary_context': window.get('boundary_context', 'fixed_context'),
                        'distinctive_terms': window.get('distinctive_terms', [])},
                    'chunk_id': f'w{window_index:02d}-p{part:03d}',
                    'core_cue_ids': [int(row[0]) for row in core],
                    'cue_encoding': ['cue_id', 'local_start', 'local_end', 'exact_text'],
                    'cues': cues[left:right],
                    'instructions': [
                        '按字幕线索识别该商品出现、持续讲解、结束转场及相邻商品边界。',
                        '逐条检查本块 cues；core_cue_ids 是本块主范围，其余是前后重叠上下文。',
                        '只报告有助于整体商品边界判断的 cue_id 和信号类型；不要决定完整商品范围。',
                        '所有 cue_id 必须来自当前块，不能改写或补造证据。']}
                size = _json_utf8_bytes({**payload, 'task_digest': '0' * 64})
                if size > target_bytes:
                    if accepted is None:
                        raise CacheIdentityError(
                            f'range_chunk_single_cue_exceeds_budget_{window["window_id"]}_{cursor}')
                    break
                accepted = (candidate_end, payload, size)
                candidate_end += 1
            if accepted is None:
                raise CacheIdentityError('range_chunk_cannot_fit_minimum_evidence')
            cursor, payload, size = accepted
            payload['task_digest'] = digest(payload)
            if _json_utf8_bytes(payload) > target_bytes:
                raise CacheIdentityError('range_chunk_digest_exceeds_budget')
            chunks.append(payload)
            if len(chunks) > max_chunks:
                raise CacheIdentityError(f'range_chunk_count_exceeds_limit_{max_chunks}')
    return chunks


def _execute_range_worker(paths, task):
    from codex_worker import execute, verify_receipt
    receipt_path = paths.project_records_dir / 'worker_receipts' / (task['task_digest'] + '.json')
    cached_receipt = receipt_path.is_file()
    started = time.perf_counter()
    reference = execute(paths, task)
    receipt = verify_receipt(paths, reference, task)
    return receipt['result'], {
        'task_digest': task['task_digest'], 'payload_bytes': _json_utf8_bytes(task),
        'cache_hit': cached_receipt, 'elapsed_seconds': round(time.perf_counter() - started, 3),
        'worker_elapsed_seconds': round(float(receipt.get('elapsed_seconds') or 0), 3),
        'receipt_ref': reference}


def _build_chunk_synthesis_payload(product, source_timeline, windows, chunk_records,
                                   cue_by_id, project_key, original_bytes,
                                   neighbor_boundaries=()):
    signal_rows = {}
    reviews = []
    for task, response in chunk_records:
        if response.get('verdict') != 'PASS':
            raise CacheIdentityError('project_range_chunk_review_blocked_' + str(task.get('chunk_id')))
        allowed = {int(row[0]): row for row in task['cues']}
        signals = []
        for signal in response.get('signals', []):
            try:
                cue_id = int(signal['cue_id'])
                if cue_id not in allowed:
                    raise ValueError('signal cue outside chunk')
                _, local_start, local_end, text = allowed[cue_id]
                original = cue_by_id.get(cue_id)
                if (original is None or original.get('source_stem') != task['source']['source_stem']
                        or float(original.get('start', -1)) != float(local_start)
                        or float(original.get('end', -1)) != float(local_end)
                        or str(original.get('text') or '') != str(text)):
                    raise ValueError('signal cue differs from current indexed evidence')
                key = (task['source']['source_stem'], cue_id)
                row = signal_rows.setdefault(key, {
                    'cue_id': cue_id, 'source_stem': task['source']['source_stem'],
                    'local_start': float(local_start), 'local_end': float(local_end),
                    'quote': str(text), 'kinds': [], 'confidence': 'low'})
                if signal.get('kind') not in row['kinds']:
                    row['kinds'].append(signal.get('kind'))
                rank = {'low': 0, 'medium': 1, 'high': 2}
                if rank.get(signal.get('confidence'), -1) > rank.get(row['confidence'], -1):
                    row['confidence'] = signal['confidence']
                signals.append({'cue_id': cue_id, 'kind': signal.get('kind'),
                                'confidence': signal.get('confidence')})
            except (KeyError, TypeError, ValueError) as exc:
                raise CacheIdentityError('project_range_chunk_signal_invalid') from exc
        core_ids = [int(value) for value in task['core_cue_ids']]
        core_rows = [allowed[value] for value in core_ids if value in allowed]
        reviews.append({
            'chunk_id': task['chunk_id'], 'source_stem': task['source']['source_stem'],
            'window_id': task['candidate_window']['window_id'],
            'window_global_start': task['candidate_window']['global_start'],
            'window_global_end': task['candidate_window']['global_end'],
            'core_local_start': float(core_rows[0][1]) if core_rows else None,
            'core_local_end': float(core_rows[-1][2]) if core_rows else None,
            'core_cue_count': len(core_rows), 'summary': str(response.get('summary') or '')[:900],
            'issues': [str(value)[:300] for value in response.get('issues', [])[:8]],
            'signals': signals})
    window_rows = [{key: window.get(key) for key in (
        'window_id', 'source_stem', 'global_start', 'global_end',
        'boundary_context', 'distinctive_terms', 'cue_count')}
        for window in windows]
    source_offsets = {row['source_stem']: float(row['global_start'])
                      for row in source_timeline}
    evidence = sorted(signal_rows.values(), key=lambda row: (
        source_offsets.get(row['source_stem'], 0.0) + row['local_start'], row['cue_id']))
    payload = {
        'schema_version': 2, 'role': 'semantic_range_matching',
        'range_matching_protocol': RANGE_MATCHING_CHUNK_PROTOCOL,
        'review_scope': 'product_search_envelope_matching',
        'project_key': project_key, 'product': product,
        'source_timeline': source_timeline,
        'neighbor_product_boundaries': list(neighbor_boundaries),
        'original_monolithic_payload_bytes': original_bytes,
        'candidate_windows': window_rows,
        'review_coverage': reviews,
        'indexed_boundary_evidence': evidence,
        'instructions': [
            '以下是按当前商品标题/卖点候选窗口完成的分块覆盖记录；每个分块已逐条检查主范围字幕，重叠上下文用于核对过渡。',
            'review_coverage 中是各分块摘要和审阅信号；indexed_boundary_evidence 的引文由原始字幕 cue_id 精确回填。',
            '目标是给当前商品的卖点语义选择提供候选搜索包络，不要求证明商品整段讲解的精确起点或终点。',
            '优先根据当前商品标题锚点和直接卖点证据定位；neighbor_product_boundaries 给出前后商品已知起止 cue，可用于估计范围，不得把商品编号或工作簿顺序当时间顺序。',
            '返回最佳可用搜索边界及当前商品标题锚点。只在商品归属存在实质冲突、无法把候选归给当前商品时返回 REPAIRABLE；整体终点不精确本身不是阻断原因。']}
    payload['task_digest'] = digest(payload)
    return payload


def join_nearby_range_windows(windows, *, bridge_seconds=180.0):
    """Retain intervening context between nearby feature hits in one source."""
    merged = []
    for source in sorted(windows, key=lambda row: (row.get('source_stem', ''),
                                                   float(row.get('global_start', 0)))):
        row = dict(source)
        if (merged and merged[-1]['source_stem'] == row.get('source_stem')
                and float(row['global_start']) <= float(merged[-1]['global_end']) + bridge_seconds):
            previous = merged[-1]
            previous['window_id'] += '+' + str(row['window_id'])
            previous['global_end'] = max(float(previous['global_end']), float(row['global_end']))
            previous['evidence_end'] = max(float(previous.get('evidence_end', previous['global_end'])),
                                            float(row.get('evidence_end', row['global_end'])))
            previous['matched_cue_count'] = int(previous.get('matched_cue_count', 0)) + int(
                row.get('matched_cue_count', 0))
            previous['distinctive_terms'] = sorted(set(previous.get('distinctive_terms', []))
                                                   | set(row.get('distinctive_terms', [])))
            previous['feature_terms'] = sorted(set(previous.get('feature_terms', []))
                                               | set(row.get('feature_terms', [])))
        else:
            merged.append(row)
    return merged


def extend_to_next_distinctive_boundary(window, catalog_products, sources, *, tail_seconds=60.0):
    """Include transition evidence through the next other-product title anchor."""
    candidates = [float(other['evidence_start'])
                  for product in catalog_products
                  if int(product.get('product_index') or 0) != int(window.get('product_index') or 0)
                  for other in product.get('windows', [])
                  if other.get('distinctive_terms')
                  and other.get('source_stem') == window.get('source_stem')
                  and float(other.get('evidence_start', 0)) > float(window.get('evidence_end', 0))]
    if not candidates:
        return dict(window)
    source_end = float(sources[window['source_stem']]['global_end'])
    return {**window, 'global_end': round(min(source_end, min(candidates) + tail_seconds), 3),
            'boundary_context': 'through_next_other_product_distinctive_anchor'}


def adjacent_product_boundary_evidence(product_id, windows, catalog_products, index, *, limit=12):
    """Return exact neighboring product cues that help infer a search envelope."""
    cues = list(index.get('cues') or [])
    result, seen = [], set()
    neighbors = []
    for other in catalog_products:
        if int(other.get('product_index') or 0) == int(product_id):
            continue
        for window in other.get('windows', []):
            if (not window.get('distinctive_terms')
                    or window.get('status') == 'PRODUCT_UNCERTAIN'
                    or float(window.get('conflict_ratio', 0.0)) >= 0.35):
                continue
            neighbors.append((int(other.get('product_index') or 0), window))

    def exact_cue(window, edge, global_time):
        stem = window.get('source_stem')
        field = 'global_end' if edge == 'end' else 'global_start'
        eligible = [cue for cue in cues if cue.get('source_stem') == stem
                    and abs(float(cue.get(field, cue.get('global_start', 0)))
                            - float(global_time)) <= 0.01]
        if not eligible:
            return None
        return min(eligible, key=lambda cue: abs(float(cue.get(field, cue.get('global_start', 0)))
                                                  - float(global_time)))

    for target in windows:
        target_start = float(target.get('evidence_start', target.get('global_start', 0)))
        target_end = float(target.get('evidence_end', target.get('global_end', target_start)))
        previous, following = [], []
        for other_id, window in neighbors:
            start = float(window.get('evidence_start', window.get('global_start', 0)))
            end = float(window.get('evidence_end', window.get('global_end', start)))
            if end <= target_start:
                previous.append((end, other_id, window))
            if start >= target_end:
                following.append((start, other_id, window))
        candidates = []
        if previous:
            at, other_id, window = max(previous, key=lambda row: row[0])
            candidates.append(('previous_end', at, other_id, window, 'end'))
        if following:
            at, other_id, window = min(following, key=lambda row: row[0])
            candidates.append(('next_start', at, other_id, window, 'start'))
        for side, at, other_id, window, cue_edge in candidates:
            cue = exact_cue(window, cue_edge, at)
            if cue is None or not str(cue.get('text') or '').strip():
                continue
            local_time = float(cue['end'] if cue_edge == 'end' else cue['start'])
            key = (str(target.get('window_id')), side, other_id,
                   str(cue['source_stem']), round(local_time, 3))
            if key in seen:
                continue
            seen.add(key)
            result.append({
                'target_window_id': str(target.get('window_id')),
                'neighbor_product_id': other_id, 'edge': side,
                'source_stem': str(cue['source_stem']), 'time': local_time,
                'global_time': round(float(at), 3), 'quote': str(cue['text']),
                'confidence': str(window.get('status') or 'ROUGH_CANDIDATE')})
    result.sort(key=lambda row: (row['global_time'], row['neighbor_product_id'], row['edge']))
    return result[:max(0, int(limit))]


def neighbor_inferred_search_decision(product, windows, neighbor_boundaries, index):
    """Build bounded candidate windows when only the whole-product edge is unclear.

    These are search windows for semantic selling-point selection, not claims that
    the complete product speech starts or ends at the outermost cue.
    """
    sources = {row['source_stem']: row for row in index.get('sources') or []}
    cues = list(index.get('cues') or [])
    accepted = []
    notes = []
    for window in windows:
        if (not window.get('distinctive_terms')
                or window.get('status') == 'PRODUCT_UNCERTAIN'
                or float(window.get('conflict_ratio', 0.0)) >= 0.35):
            continue
        stem = window.get('source_stem')
        source = sources.get(stem)
        if not source:
            continue
        evidence_start = float(window.get('evidence_start', window.get('global_start', 0)))
        evidence_end = float(window.get('evidence_end', window.get('global_end', evidence_start)))
        lower = float(window.get('global_start', source['global_start']))
        upper = float(window.get('global_end', source['global_end']))
        limits = [row for row in neighbor_boundaries
                  if row.get('target_window_id') == str(window.get('window_id'))]
        previous = [row for row in limits if row.get('edge') == 'previous_end'
                    and float(row.get('global_time', 0)) <= evidence_start]
        following = [row for row in limits if row.get('edge') == 'next_start'
                     and float(row.get('global_time', 0)) >= evidence_end]
        previous = max(previous, key=lambda row: float(row['global_time'])) if previous else None
        following = min(following, key=lambda row: float(row['global_time'])) if following else None
        if previous:
            lower = max(lower, (float(previous['global_time']) + evidence_start) / 2.0)
        if following:
            upper = min(upper, (evidence_end + float(following['global_time'])) / 2.0)
        source_start = float(source['global_start'])
        local_lower, local_upper = lower - source_start, upper - source_start
        local_cues = [cue for cue in cues if cue.get('source_stem') == stem
                      and float(cue.get('start', 0)) >= local_lower - 0.001
                      and float(cue.get('end', cue.get('start', 0))) <= local_upper + 0.001
                      and float(cue.get('end', cue.get('start', 0))) > float(cue.get('start', 0))]
        if not local_cues:
            continue
        local_cues.sort(key=lambda cue: (float(cue['start']), float(cue['end'])))
        first, last = local_cues[0], local_cues[-1]
        start = round(float(first['start']), 3)
        end = round(float(last['end']), 3)
        if end <= start:
            continue
        start_anchor = {'source_stem': stem, 'time': start, 'quote': str(first.get('text') or '')}
        end_anchor = {'source_stem': stem, 'time': end, 'quote': str(last.get('text') or '')}
        if not start_anchor['quote'].strip() or not end_anchor['quote'].strip():
            continue
        accepted.append({'source_stem': stem, 'start': start, 'end': end,
                         'start_anchor': start_anchor, 'end_anchor': end_anchor})
        notes.append({
            'window_id': str(window.get('window_id')),
            'previous_product_id': previous.get('neighbor_product_id') if previous else None,
            'previous_end_global': round(float(previous['global_time']), 3) if previous else None,
            'next_product_id': following.get('neighbor_product_id') if following else None,
            'next_start_global': round(float(following['global_time']), 3) if following else None,
            'inferred_search_start_global': round(source_start + start, 3),
            'inferred_search_end_global': round(source_start + end, 3)})
    accepted.sort(key=lambda row: (float(sources[row['source_stem']]['global_start']) + row['start'],
                                   float(sources[row['source_stem']]['global_start']) + row['end']))
    # Merge only overlapping same-source windows. Each output edge remains an
    # exact indexed cue; unrelated gaps stay excluded from the candidate pool.
    merged = []
    for window in accepted:
        if (merged and merged[-1]['source_stem'] == window['source_stem']
                and float(window['start']) <= float(merged[-1]['end'])):
            if float(window['end']) > float(merged[-1]['end']):
                merged[-1]['end'] = window['end']
                merged[-1]['end_anchor'] = window['end_anchor']
        else:
            merged.append(dict(window))
    if not merged:
        return None
    first, last = merged[0], merged[-1]
    start_anchor, end_anchor = first['start_anchor'], last['end_anchor']
    start_global = float(sources[start_anchor['source_stem']]['global_start']) + start_anchor['time']
    end_global = float(sources[end_anchor['source_stem']]['global_start']) + end_anchor['time']
    title_anchors = exact_title_anchor_evidence(
        str(product.get('title') or ''), index, min(start_global, end_global), max(start_global, end_global))
    if not title_anchors:
        return None
    return {
        'start': {'source_stem': start_anchor['source_stem'], 'time': start_anchor['time']},
        'end': {'source_stem': end_anchor['source_stem'], 'time': end_anchor['time']},
        'start_anchor': start_anchor, 'end_anchor': end_anchor,
        'title_anchors': title_anchors,
        'candidate_windows': merged,
        'boundary_mode': NEIGHBOR_INFERRED_BOUNDARY_MODE,
        'boundary_notes': notes}


def exact_title_anchor_evidence(title, index, start, end, limit=3):
    """Find exact current subtitle cues containing distinctive title terms."""
    from semantic_evidence import title_features
    terms = sorted((term for term in title_features(str(title)) if len(term) > 1),
                   key=lambda value: (-len(value), value.casefold()))
    candidates = []
    for cue in index.get('cues', []):
        global_start = float(cue.get('global_start', 0))
        global_end = float(cue.get('global_end', global_start))
        if global_end < start or global_start > end:
            continue
        text = str(cue.get('text') or '')
        matching = [term for term in terms if term.casefold() in text.casefold()]
        if not matching:
            continue
        term = matching[0]
        candidates.append((len(term), global_start,
                           {'source_stem': cue['source_stem'], 'time': float(cue['start']),
                            'quote': text}))
    candidates.sort(key=lambda row: (-row[0], row[1]))
    result, seen = [], set()
    for _, _, anchor in candidates:
        key = (anchor['source_stem'], anchor['time'], anchor['quote'])
        if key not in seen:
            result.append(anchor); seen.add(key)
        if len(result) >= limit:
            break
    return result


def publish(paths, product_id, encoding_id, stage, body, dependencies=(), model_version='local-v3', snapshot=None):
    args = dict(product_id=product_id, encoding_id=encoding_id, stage=stage, version=digest(body),
                model_version=model_version, dependencies=dependencies)
    return publish_stage(paths, **args, body=body, snapshot=snapshot or capture_stage(paths, **args))


def inputs(paths):
    titles = load_checked_json(paths, paths.upstream_cache_dir / 'titles.json')
    index = load_checked_json(paths, paths.upstream_cache_dir / 'subtitle_index.json')
    names = [Path(s['video_path']).name for s in index['sources']]
    if len(set(names)) != len(names):
        raise CacheIdentityError('ambiguous_source_filenames')
    return titles, index


def confirm_range(paths, product_id, manifest):
    titles, index = inputs(paths)
    selected = [p for p in titles['products'] if p['product_index'] == product_id]
    if len(selected) != 1:
        raise CacheIdentityError('product_title_not_authenticated')
    # Existing real human-confirmation validation, including proof/coverage/overlap.
    from runtime_meta import expected_identity
    identity = expected_identity(paths, include_ranges=False)
    result = build_confirmed_ranges(manifest, index, {'products': selected})
    if expected_identity(paths, include_ranges=False) != identity:
        raise CacheIdentityError('inputs_changed_during_range_validation')
    row = result['products'][0]
    return publish(paths, product_id, row['encoding_id'], 'range', row)


def publish_semantic_ranges(paths, manifest, *, product_ids=None):
    """Publish one project-wide product map so later batches reuse the same ranges."""
    titles, index = inputs(paths)
    state = paths.load_state()
    identity = expected_identity(paths, include_ranges=False)
    project_key = paths.registered_scope()[0]
    batch_id = str(state['batch']['batch_id'])
    catalog_ids = {int(row['product_index']) for row in titles['products']}
    batch_ids = set(catalog_ids if product_ids is None else product_ids)
    if not batch_ids or not batch_ids.issubset(catalog_ids):
        raise CacheIdentityError('project_full_range_catalog_scope_invalid')
    confirmed_decisions = manifest.get('confirmed_products', [])
    if not isinstance(confirmed_decisions, list):
        raise CacheIdentityError('confirmed_range_manifest_products_invalid')
    confirmed_ids = {row.get('product_index') for row in confirmed_decisions
                     if isinstance(row, dict)}
    if len(confirmed_ids) != len(confirmed_decisions) or not confirmed_ids.issubset(batch_ids):
        raise CacheIdentityError('confirmed_range_manifest_scope_mismatch')
    semantic_ids = batch_ids - confirmed_ids
    rows = []
    if semantic_ids:
        semantic_manifest = {**manifest,
                             'products': [row for row in manifest.get('products', [])
                                          if row.get('product_index') in semantic_ids]}
        if len(semantic_manifest['products']) != len(semantic_ids):
            raise CacheIdentityError('semantic_range_manifest_incomplete')
        semantic_result = build_semantic_inferred_ranges(
            semantic_manifest, index, titles, sorted(semantic_ids),
            project_key=project_key, batch_id=batch_id)
        rows.extend(semantic_result['products'])
    elif manifest.get('products'):
        raise CacheIdentityError('semantic_range_manifest_scope_mismatch')
    if confirmed_ids:
        from publish_confirmed_ranges import build_confirmed_ranges, validate_global_ranges
        title_rows = [row for row in titles['products'] if row['product_index'] in confirmed_ids]
        confirmed_result = build_confirmed_ranges(
            {'products': confirmed_decisions}, index, {'products': title_rows}, confirmed_ids)
        rows.extend(confirmed_result['products'])
        validate_global_ranges(rows)
    if expected_identity(paths, include_ranges=False) != identity:
        raise CacheIdentityError('inputs_changed_during_semantic_range_validation')
    references = {}
    for row in rows:
        product_id = row['product_index']
        if product_id in semantic_ids:
            validate_semantic_inferred_document(
                {'range_authority': 'user_directed_semantic_inference', 'products': [row]},
                [row], index, [product_id], project_key=project_key, batch_id=batch_id)
        references[row['product_index']] = publish(
            paths, row['product_index'], row['encoding_id'], 'range', row,
            model_version=('human-confirmed-range-v1' if product_id in confirmed_ids
                           else 'codex-semantic-range-v1'))
    return references


def generate_batch_range_manifest(paths, product_ids=None):
    """Ask Codex to match only this batch's products to exact indexed subtitle anchors."""
    from semantic_evidence import semantic_windows
    from publish_confirmed_ranges import build_semantic_inferred_ranges

    titles, index = inputs(paths)
    state = paths.load_state()
    selected_ids = set(int(pid) for pid in (product_ids or state['batch']['product_ids']))
    catalog = {int(row['product_index']): row for row in titles['products']}
    if not selected_ids or not selected_ids.issubset(catalog):
        raise CacheIdentityError('batch_range_matching_scope_invalid')
    products = [catalog[pid] for pid in sorted(selected_ids)]
    # Keep the complete broad boundary context. Encode repeated cue objects as
    # positional tuples below so a full batch-product review fits the worker
    # payload limit without dropping intervening evidence.
    rough = semantic_windows(index, titles['products'], before=120.0, after=240.0,
                             cluster_gap=180.0)
    rough_by_id = {int(row['product_index']): row for row in rough['products']}
    source_bounds = {row['source_stem']: row for row in index['sources']}
    decisions = []
    project_key = paths.registered_scope()[0]
    metrics_started = time.perf_counter()
    metrics = {'schema_version': 1, 'batch_id': str(state['batch']['batch_id']),
               'protocol': RANGE_MATCHING_CHUNK_PROTOCOL, 'started_at_epoch': time.time(),
               'products': []}
    metrics_path = paths.workspace_root / f'RANGE_MATCHING_METRICS_{time.time_ns()}.json'

    def save_metrics():
        atomic_json(metrics_path, metrics)

    try:
        for product in products:
            product_started = time.perf_counter()
            pid = int(product['product_index'])
            product_metrics = {'product_id': pid, 'calls': [], 'cue_count': 0,
                               'window_count': 0, 'mode': None,
                               '_started_monotonic': product_started}
            metrics['products'].append(product_metrics)
            rough_product = rough_by_id.get(pid) or {}
            windows, chunk_windows = [], []
            joined_windows = [extend_to_next_distinctive_boundary(
                {**window, 'product_index': pid}, rough['products'], source_bounds)
                for window in join_nearby_range_windows(rough_product.get('windows', []))]
            distinctive_windows = [window for window in joined_windows
                                   if window.get('distinctive_terms')]
            # Generic feature hits can occur throughout a long live stream and
            # add no product-identity proof. When exact distinctive-title evidence
            # exists, exclude generic-only windows from the worker package.
            task_windows = distinctive_windows or joined_windows
            neighbor_boundaries = adjacent_product_boundary_evidence(
                pid, task_windows, rough['products'], index)
            cue_by_id = {}
            for window in task_windows:
                cue_rows = [(cue_id, cue) for cue_id, cue in enumerate(index['cues'])
                            if float(window['global_start']) <= float(cue.get('global_start', 0))
                            and float(cue.get('global_end', cue.get('global_start', 0)))
                            <= float(window['global_end'])]
                # Keep this legacy representation byte-for-byte stable so a
                # previously sealed v3 receipt remains reusable for small tasks.
                cues = [[float(cue['start']), float(cue['end']),
                         str(cue.get('text') or '')[:500]] for _, cue in cue_rows]
                windows.append({
                    'window_id': window['window_id'], 'source_stem': window['source_stem'],
                    'global_start': window['global_start'], 'global_end': window['global_end'],
                    'boundary_context': window.get('boundary_context', 'fixed_context'),
                    'distinctive_terms': window['distinctive_terms'],
                    'cue_encoding': ['local_start', 'local_end', 'exact_text'],
                    'cues': cues})
                full_cues = []
                for cue_id, cue in cue_rows:
                    cue_by_id[cue_id] = cue
                    full_cues.append([int(cue_id), float(cue['start']), float(cue['end']),
                                      str(cue.get('text') or '')])
                chunk_windows.append({
                    'window_id': window['window_id'], 'source_stem': window['source_stem'],
                    'global_start': window['global_start'], 'global_end': window['global_end'],
                    'boundary_context': window.get('boundary_context', 'fixed_context'),
                    'distinctive_terms': window.get('distinctive_terms', []),
                    'cue_count': len(full_cues), 'cue_rows': full_cues})
            product_metrics['cue_count'] = sum(row['cue_count'] for row in chunk_windows)
            product_metrics['window_count'] = len(windows)
            source_timeline = [{'source_stem': row['source_stem'],
                                'global_start': float(row['global_start']),
                                'global_end': float(row['global_end'])}
                               for row in index['sources']]
            payload = {
                'schema_version': 2, 'role': 'semantic_range_matching',
                'range_matching_protocol': RANGE_MATCHING_PROTOCOL,
                'review_scope': 'product_search_envelope_matching',
                'project_key': project_key, 'product': product,
                'source_timeline': source_timeline,
                'neighbor_product_boundaries': neighbor_boundaries,
                'lexical_candidate_windows': windows,
                'instructions': [
                    'Find current-product selling-point search windows from title semantics and continuous subtitle evidence; do not require the exact beginning or final sentence of the whole product broadcast.',
                    'Each window supplies one source_stem and global bounds. Each cue inside it is [local_start, local_end, exact_text]. Return best available start/end search bounds using copied cue quotes and local times.',
                    'Use distinctive current-title anchors that occur verbatim in supplied cues. neighbor_product_boundaries contains exact previous/next product cue evidence; use it to estimate uncertain bounds and avoid neighboring product speech.',
                    'Do not infer ownership from item numbers, workbook order, generic words, or a single ambiguous mention. If a candidate clearly belongs to the current product, an uncertain outer edge is not a blocker.',
                    'Only return REPAIRABLE with range_decision null when current-product ownership itself remains materially ambiguous or no current-title evidence exists.',
                    'This stage creates candidate search context only; semantic selection chooses three distinct selling points and ASR later executes only their nearby cutpoints.']}
            payload['task_digest'] = digest(payload)
            product_metrics['monolithic_payload_bytes'] = _json_utf8_bytes(payload)
            if product_metrics['monolithic_payload_bytes'] <= RANGE_TASK_MAX_BYTES:
                product_metrics['mode'] = 'cached-compatible-monolithic-v5'
                response, call = _execute_range_worker(paths, payload)
                product_metrics['calls'].append(call)
                save_metrics()
            else:
                product_metrics['mode'] = 'overlap-chunked-v5'
                chunk_tasks = pack_semantic_range_chunks(product, chunk_windows, source_bounds,
                                                         project_key)
                if not chunk_tasks:
                    raise CacheIdentityError(f'project_range_matching_no_cue_evidence_product_{pid}')
                product_metrics['chunk_count'] = len(chunk_tasks)
                product_metrics['chunk_payload_bytes'] = [
                    _json_utf8_bytes(task) for task in chunk_tasks]
                chunk_records = []
                for task in chunk_tasks:
                    chunk_response, call = _execute_range_worker(paths, task)
                    product_metrics['calls'].append(call)
                    save_metrics()
                    if chunk_response.get('verdict') != 'PASS':
                        raise CacheIdentityError(
                            f'project_range_chunk_review_failed_product_{pid}_{task["chunk_id"]}')
                    chunk_records.append((task, chunk_response))
                synthesis = _build_chunk_synthesis_payload(
                    product, source_timeline, chunk_windows, chunk_records, cue_by_id,
                    project_key, product_metrics['monolithic_payload_bytes'],
                    neighbor_boundaries=neighbor_boundaries)
                synthesis_bytes = _json_utf8_bytes(synthesis)
                product_metrics['synthesis_payload_bytes'] = synthesis_bytes
                if synthesis_bytes > RANGE_TASK_MAX_BYTES:
                    save_metrics()
                    raise CacheIdentityError(
                        f'project_range_matching_synthesis_too_large_product_{pid}_{synthesis_bytes}')
                response, call = _execute_range_worker(paths, synthesis)
                product_metrics['calls'].append(call)
                save_metrics()
            matched = None
            if response.get('verdict') != 'PASS' or not response.get('range_decision'):
                initial_response = response
                boundary_trigger = range_boundary_context_trigger(response)
                product_metrics['boundary_context_repair'] = {
                    'protocol': RANGE_BOUNDARY_REPAIR_PROTOCOL,
                    'attempted': False,
                    'trigger': boundary_trigger,
                    'status': 'NOT_APPLICABLE',
                }
                if boundary_trigger:
                    repair_task, repair_metrics = extend_range_payload_with_adjacent_context(
                        payload, task_windows, index,
                        neighbor_boundaries=neighbor_boundaries)
                    product_metrics['boundary_context_repair'].update(repair_metrics)
                    if repair_task is not None:
                        product_metrics['boundary_context_repair'].update(
                            attempted=True, status='RUNNING', task_digest=repair_task['task_digest'])
                        save_metrics()
                        repaired, repair_call = _execute_range_worker(paths, repair_task)
                        product_metrics['calls'].append(repair_call)
                        response = repaired
                        product_metrics['boundary_context_repair'].update(
                            status=('PASS' if response.get('verdict') == 'PASS'
                                    and bool(response.get('range_decision')) else 'INSUFFICIENT_EVIDENCE'),
                            cache_hit=repair_call['cache_hit'],
                            elapsed_seconds=repair_call['elapsed_seconds'])
                        save_metrics()
                    else:
                        product_metrics['boundary_context_repair']['status'] = 'NO_SAFE_EXTENSION'
                        save_metrics()
                if response.get('verdict') != 'PASS' or not response.get('range_decision'):
                    failure_responses = [initial_response, response]
                    identity_conflict = any(
                        marker in str(issue).casefold()
                        for failure in failure_responses
                        for issue in (failure.get('issues') or [])
                        for marker in ('身份冲突', '归属冲突', '归属不唯一', '无法确认商品身份',
                                       'competing product', 'ownership conflict', 'identity conflict',
                                       'ambiguous ownership'))
                    if boundary_trigger and not identity_conflict:
                        matched = neighbor_inferred_search_decision(
                            product, task_windows, neighbor_boundaries, index)
                    if matched:
                        product_metrics['boundary_search_resolution'] = {
                            'mode': NEIGHBOR_INFERRED_BOUNDARY_MODE,
                            'status': 'PASS_BEST_EFFORT',
                            'exact_whole_product_start_required': False,
                            'exact_whole_product_end_required': False,
                            'candidate_window_count': len(matched['candidate_windows']),
                            'neighbor_boundary_count': len(neighbor_boundaries),
                            'notes': matched['boundary_notes'],
                        }
                        product_metrics['status'] = 'PASS_BEST_EFFORT'
                        product_metrics['elapsed_seconds'] = round(time.perf_counter() - product_started, 3)
                        product_metrics.pop('_started_monotonic', None)
                        save_metrics()
                    elif boundary_trigger:
                        product_metrics['status'] = 'WAITING_FOR_EVIDENCE'
                        product_metrics['next_action'] = (
                            '没有找到可验证的当前商品标题/卖点搜索窗口，或商品归属存在冲突；'
                            '先对照前后商品边界确认候选归属，不要求单独补出整商品精确终点。')
                        save_metrics()
                        reason = product_metrics['boundary_context_repair'].get('reason')
                        suffix = 'no_neighbor_inferred_search_window'
                        if reason:
                            suffix += '_' + str(reason)
                        raise CacheIdentityError(
                            f'project_range_matching_waiting_for_evidence_product_{pid}:{suffix}')
                    else:
                        issues = response.get('issues') or ['Codex 未能确认当前商品归属']
                        product_metrics['status'] = 'FAILED'
                        save_metrics()
                        raise CacheIdentityError(f'project_range_matching_failed_product_{pid}:'
                                                 + '|'.join(issues)[:500])
            if matched is None:
                matched = response['range_decision']
            try:
                from semantic_evidence import title_features
                terms = [term for term in title_features(str(product['title'])) if len(term) > 1]
                title_anchors = matched.get('title_anchors') or []
                matched['title_anchors'] = [anchor for anchor in title_anchors
                                            if isinstance(anchor, dict)
                                            and any(term.casefold() in str(anchor.get('quote') or '').casefold()
                                                    for term in terms)]
                if not matched['title_anchors']:
                    start_global = float(index['sources'][
                        next(i for i, source in enumerate(index['sources'])
                             if source['source_stem'] == matched['start']['source_stem'])]['global_start'])
                    end_source = next(source for source in index['sources']
                                      if source['source_stem'] == matched['end']['source_stem'])
                    start_global += float(matched['start']['time'])
                    end_global = float(end_source['global_start']) + float(matched['end']['time'])
                    matched['title_anchors'] = exact_title_anchor_evidence(
                        product['title'], index, start_global, end_global)
                    if not matched['title_anchors']:
                        raise CacheIdentityError('semantic_range_title_anchor_missing_current_subtitle_evidence')
            except (KeyError, StopIteration, TypeError, ValueError) as exc:
                raise CacheIdentityError('semantic_range_title_anchor_evidence_invalid') from exc

            def point(anchor):
                if not isinstance(anchor, dict):
                    raise CacheIdentityError(f'project_range_matching_anchor_invalid_product_{pid}')
                return {'source_stem': anchor.get('source_stem'), 'time': anchor.get('time')}

            decisions.append({
                'product_index': pid,
                'start': point(matched.get('start')), 'end': point(matched.get('end')),
                'start_anchor': matched.get('start_anchor') or matched.get('start'),
                'end_anchor': matched.get('end_anchor') or matched.get('end'),
                'title_anchors': matched.get('title_anchors'),
                **({'candidate_windows': matched['candidate_windows'],
                    'boundary_mode': matched['boundary_mode'],
                    'boundary_notes': matched.get('boundary_notes', [])}
                   if matched.get('boundary_mode') == NEIGHBOR_INFERRED_BOUNDARY_MODE else {})})
            if product_metrics.get('status') != 'PASS_BEST_EFFORT':
                product_metrics['status'] = 'PASS'
            product_metrics['elapsed_seconds'] = round(time.perf_counter() - product_started, 3)
            product_metrics.pop('_started_monotonic', None)
            save_metrics()
    except Exception as exc:
        metrics['status'] = 'FAILED'
        metrics['error'] = str(exc)[:800]
        if metrics['products'] and 'status' not in metrics['products'][-1]:
            metrics['products'][-1]['status'] = 'FAILED'
        raise
    else:
        metrics['status'] = 'PASS'
    finally:
        metrics['elapsed_seconds'] = round(time.perf_counter() - metrics_started, 3)
        for row in metrics['products']:
            started = row.pop('_started_monotonic', None)
            if started is not None:
                row.setdefault('elapsed_seconds', round(time.perf_counter() - started, 3))
        metrics['finished_at_epoch'] = time.time()
        # Per-product timings are kept separately from the enclosing stage timer.
        atomic_json(metrics_path, metrics)

    manifest = {'schema_version': 1, 'authority': 'user_directed_semantic_inference',
                'manifest_source': 'codex_batch_match_v1',
                'boundary_context_repair_protocol': RANGE_BOUNDARY_REPAIR_PROTOCOL,
                'range_matching_protocols': {
                    str(row['product_id']): (
                        RANGE_MATCHING_PROTOCOL if row.get('mode') == 'cached-compatible-monolithic-v5'
                        else RANGE_MATCHING_CHUNK_PROTOCOL)
                    for row in metrics['products']},
                'batch_id': state['batch']['batch_id'],
                'product_ids': sorted(selected_ids),
                'input_identity_digest': digest(expected_identity(paths, include_ranges=False)),
                'authorization': '用户要求按当前批次商品范围执行字幕语义匹配。',
                'products': decisions}
    build_semantic_inferred_ranges(manifest, index, titles, sorted(selected_ids),
                                   project_key=project_key, batch_id=str(state['batch']['batch_id']))
    return manifest


def candidates(paths, range_ref, *, include_cross_boundary_cues=False):
    record = load_stage(paths, range_ref)
    row = record['body']
    from publish_confirmed_ranges import verify_confirmation, verify_semantic_inference
    if not verify_confirmation(row):
        raise CacheIdentityError('range_proof_missing_or_invalid')
    titles, index = inputs(paths)
    found = [p for p in titles['products'] if p['product_index'] == record['identity']['product_id']]
    if len(found) != 1 or found[0]['encoding_id'] != record['identity']['encoding_id']:
        raise CacheIdentityError('range_title_identity_mismatch')
    state = paths.load_state()
    if verify_semantic_inference(row):
        proof_batch_id = row.get('semantic_range_proof', {}).get('decision', {}).get('batch_id')
        validate_semantic_inferred_document(
            {'range_authority': 'user_directed_semantic_inference', 'products': [row]},
            found, index, [record['identity']['product_id']],
            project_key=record['identity']['project_key'], batch_id=proof_batch_id)
    from project_editing_modes import resolve_editing_mode
    editing_mode, _ = resolve_editing_mode(state.get('resolve', {}).get('project_name'))
    approximate_bounds = editing_mode == 'dudu_marked_range'
    version = 'wide-pool-v5-original-text-annotated-forbidden-approx-bounds'
    args = dict(product_id=record['identity']['product_id'], encoding_id=record['identity']['encoding_id'],
                stage='candidate', version=version, model_version='local-v3', dependencies=[range_ref])
    snapshot = capture_stage(paths, **args)
    pool, removed, included_cross_boundary = [], [], 0
    for part in row['ranges']:
        crossing = [c for c in index['cues'] if c['source_stem'] == part['media_stem']
                    and c['end'] > part['start'] and c['start'] < part['end']
                    and not part['start'] <= c['start'] < c['end'] <= part['end']]
        if crossing and not include_cross_boundary_cues and not approximate_bounds:
            raise CacheIdentityError('subtitle_crosses_confirmed_boundary_needs_local_alignment')
        included_cross_boundary += len(crossing)
        cues = [c for c in index['cues'] if c['source_stem'] == part['media_stem']
                and c['end'] > part['start'] and c['start'] < part['end']
                and (include_cross_boundary_cues or approximate_bounds or
                     part['start'] <= c['start'] and c['end'] <= part['end'])]
        chunks, rejected = build_wide_candidates(
            cues, part['media_stem'], found[0]['title'],
            permitted_range=part if approximate_bounds else None)
        pool.extend(chunks); removed.extend(rejected)
    inferred_search = row.get('boundary_mode') == NEIGHBOR_INFERRED_BOUNDARY_MODE
    body = {'candidates': pool, 'removed_count': len(removed),
            'complete_range_scanned': not inferred_search,
            'candidate_search_scope': ('neighbor_inferred_search_windows' if inferred_search
                                       else 'confirmed_product_range'),
            'boundary_mode': row.get('boundary_mode'),
            'boundary_notes': row.get('boundary_notes', []),
            'boundary_policy': ('approximate_markers_context_only' if approximate_bounds else
                                'include_complete_intersecting_cues' if include_cross_boundary_cues else 'strict'),
            'included_cross_boundary_cue_count': included_cross_boundary}
    return publish_stage(paths, **args, body=body, snapshot=snapshot)


def repair_boundary_targets(issues):
    """Extract explicit final-review source-time corrections for rough selection."""
    targets = []
    for issue in issues or []:
        variants = sorted(set(re.findall(r"variant_[1-3]", str(issue))))
        times = re.findall(r"([0-9]+(?:\.[0-9]+)?)秒", str(issue))
        if len(variants) != 1 or not times:
            continue
        text = str(issue)
        suggestion = text.rsplit('建议', 1)[-1] if '建议' in text else text
        if any(term in suggestion for term in ('起点', '起始', '开头', '开始', '前移')):
            edge = 'start'
        else:
            edge = 'end'
        if re.search(r"首段|第一段", suggestion):
            position = 'first'
        elif re.search(r"第二段", suggestion):
            position = 'second'
        elif re.search(r"第三段", suggestion):
            position = 'third'
        else:
            position = 'last'
        targets.append({'variant_id': variants[0], 'edge': edge, 'position': position,
                        'source_time': float(times[-1])})
    return targets


def selection_repair_instruction(issues):
    if not issues:
        return ''
    if any(('SOURCE_OVERLAP' in str(issue)
            or 'SOURCE_RANGE_REUSE' in str(issue)
            or 'HARD_SOURCE_RANGE_EXCLUSION_VIOLATION' in str(issue)) for issue in issues):
        return ('这是物理方案/字幕范围去重失败。先重新理解当前商品完整卖点和剩余字幕候选，再只重选 repairable_variant_ids；'
                'hard_excluded_source_ranges 是硬排除区间，所有受影响版本均不得再次选择其同源交集。'
                'previous_selected_ranges 仅供定位失败范围，不能原样复用失败区间；locked_selected_ranges 必须原样保留。'
                '逐对检查所有版本、所有片段的源文件和秒区间，保证同源时间段两两不重叠；不能只改 variant 名或说明文字。'
                '在 issues 中指出的双方版本重新组织自然完整的语义卖点，避免再次使用同一段字幕/源素材。')
    if all('人物画面比例超限' in str(issue) for issue in issues):
        return ('仅重组被指出的 variant 的字幕语义选段，保留 locked_selected_ranges 原样，'
                'visual_exclusion_ranges 是硬排除区间，不得再次选中其中同源时间段；'
                'visual_repair_samples 给出精确源时间证据。保持版本间源区间互斥。'
                '采样稀疏，不证明未采样画面的人物状态。'
                '本任务只判断新组合的字幕语义、相关性和时长；不要要求人物画面证据，'
                '也不要声称人物比例已修复。返回语义上可用的选段后，'
                '本机人物检测会独立复核，不通过则继续返工。')
    return ('逐条落实 issues 中审核给出的源时间点与修复方向；repair_boundary_targets 是必须落实的边界约束。'
            '只修改对应 variant 的 selected_ranges，把 start/end 移到建议边界，切掉残留的多余字或下一句起头；'
            '若建议保留完整词/因果结果，则按建议恢复。其他 variant 保持 locked_selected_ranges 原样。'
            '不要只改说明文字，也不要原样复用该 variant 已失败的范围。修复后重查各段拼接的句意与承接。')


def visual_repair_samples(vision_body):
    """Return bounded source-time hints from the failed local person audit."""
    clocks = vision_body.get('clocks', {})
    result = []
    for variant in vision_body.get('variants', []):
        if variant.get('status') == 'PASS':
            continue
        seen = set()
        for sample in variant.get('samples', []):
            source = sample.get('source_file')
            clock = clocks.get(source, {})
            fps = clock.get('fps')
            label = sample.get('classification', {}).get('label')
            if not source or not isinstance(fps, (int, float)) or fps <= 0 or label not in ('PERSON_VISIBLE', 'NO_PERSON'):
                continue
            second = round(float(sample['frame']) / fps, 1)
            key = (variant['variant_id'], source, second, label)
            if key in seen:
                continue
            seen.add(key)
            result.append({'variant_id': variant['variant_id'], 'source_file': source,
                           'source_time': second, 'label': label})
            if len(seen) >= 50:
                break
    return result


def visual_repair_ranges(vision_body, plan_body):
    """Block exact source ranges for variants that failed the person audit."""
    failed = {row['variant_id'] for row in vision_body.get('variants', [])
              if row.get('status') == 'REPAIRABLE'}
    ranges = []
    for variant in plan_body.get('variants', []):
        if variant.get('variant_id') not in failed:
            continue
        for clip in variant.get('clips', []):
            fps = float(clip.get('fps') or 0)
            if fps <= 0:
                continue
            ranges.append({'variant_id': variant['variant_id'],
                           'source_file': clip['source_file'],
                           'start': float(clip['source_start_frame']) / fps,
                           'end': float(clip['source_end_frame']) / fps,
                           'reason': 'prior_person_coverage_failure'})
    return ranges


def selection_task(paths, candidate_ref, issues=None, repair_attempt=0, semantic_map=None,
                   previous_selected_ranges=None, visual_samples=None, visual_exclusion_ranges=None,
                   range_exclusion_ranges=None, repair_route=None):
    candidate = load_stage(paths, candidate_ref)
    identity = candidate['identity']
    state = paths.load_state()
    from project_editing_modes import resolve_editing_mode
    editing_mode, editing_policy = resolve_editing_mode(state.get('resolve', {}).get('project_name'))
    duration_policy = [float(value) for value in editing_policy['duration_exclusive_seconds']]
    override_path = paths.workspace_root / 'delivery' / 'USER_OVERRIDE.json'
    requested = editing_policy['target_variant_count']
    if override_path.is_file():
        override = json.loads(override_path.read_text(encoding='utf-8'))
        if (override.get('scope') == 'this_batch_only'
                and override.get('confirmed_by_user') is True
                and 'output_count_per_product' in override):
            value = override.get('output_count_per_product')
            if type(value) is int and 1 <= value <= requested and (
                    editing_policy.get('allow_fewer_variants_when_exhausted') is True or value == requested):
                requested = value
            else:
                raise CacheIdentityError('user_output_count_override_invalid')
            candidate_policy = override.get('duration_exclusive_seconds')
            if candidate_policy is not None:
                if (not isinstance(candidate_policy, list) or len(candidate_policy) != 2
                        or not all(isinstance(x, (int, float)) for x in candidate_policy)
                        or not duration_policy[0] <= candidate_policy[0] < candidate_policy[1] <= duration_policy[1]):
                    raise CacheIdentityError('user_duration_override_outside_project_mode')
                duration_policy = [float(value) for value in candidate_policy]
    issues = [str(issue)[:420] for issue in (issues or []) if str(issue).strip()]
    previous_selected_ranges = [dict(row) for row in (previous_selected_ranges or [])]
    range_exclusion_ranges = [dict(row) for row in (range_exclusion_ranges or [])]
    affected_variants = sorted(set(re.findall(r"variant_[1-3]", " ".join(issues))))
    locked_selected_ranges = [row for row in previous_selected_ranges
                              if affected_variants and row.get('variant_id') not in affected_variants]
    selection_candidates = []
    for source in candidate['body']['candidates']:
        compact = {key: source[key] for key in ('candidate_id', 'source_file', 'time_range', 'asr_text',
                                                'local_title_matches', 'forbidden_annotations',
                                                'semantic_boundary') if key in source}
        # Keep continuous source subtitles, but omit cache-only cue references and flags.
        compact['subtitle'] = [{key: cue[key] for key in ('start', 'end', 'text', 'context_only') if key in cue}
                               for cue in source.get('subtitle', [])]
        selection_candidates.append(compact)
    task = {'role': 'selection', 'schema_version': 1, 'project_key': identity['project_key'],
            'selection_instruction_version': 'semantic-range-overlap-repair-v4',
            'config_reference': 'PROJECT_CONFIG.json', 'product_id': identity['product_id'],
            'encoding_id': identity['encoding_id'], 'rules_fingerprint': identity['rules_fingerprint'],
            'candidate_reference': candidate_ref, 'candidate_digest': candidate['body_digest'],
            'range_references': [d['path'] for d in identity['dependencies']],
            'candidates': selection_candidates, 'completed_stages': ['range', 'candidate'],
            'boundary_policy': candidate['body'].get('boundary_policy', 'strict'),
            'candidate_search_scope': candidate['body'].get('candidate_search_scope',
                                                            'confirmed_product_range'),
            'boundary_mode': candidate['body'].get('boundary_mode'),
            'boundary_notes': candidate['body'].get('boundary_notes', []),
            'included_cross_boundary_cue_count': candidate['body'].get('included_cross_boundary_cue_count', 0),
            'editing_mode': editing_mode, 'editing_policy': editing_policy,
            'applicable_shared_rules': [
                '本批切点偏好：尽量切除句首/句尾相邻的多余字词，允许略紧一点以改善听感；必须保留当前商品完整卖点句意、必要因果结果和自然语流，不得吞掉有效音节或留下悬空承接词。',
                '商品标题与当前商品范围身份一致；字幕讲解仅作为语义证据，编号词不能单独决定边界。',
                '候选必须保留连续原字幕语境；只选当前商品直接相关的完整自然语义单元。',
                '禁止编造卖点、价格、材质、颜色或商品事实；规避禁用内容，不选择无关或重复内容。',
                '每个商品目标三条不同成片，只有完整候选池耗尽后才允许减少数量。',
                '每条成片严格大于时长下限且严格小于上限；语义完整优先，不为凑时长拼接无关内容。',
                '本任务仅负责字幕语义粗选，不证明逐词时间、音画质量、主播可见比例或最终成片审核通过。',
                '本字段列出了本任务适用的全部选段规则；config_reference 和 rules_fingerprint 仅用于身份追踪，不要求读取文件或补充其他规则。'],
            'duration_exclusive_seconds': duration_policy,
            'allowed_variant_ids': [f'variant_{index}' for index in range(1, requested+1)],
            'semantic_map_from_previous_attempt': semantic_map or [],
            'semantic_map_contract': 'Return a concise product selling-point map with exact candidate_id and verbatim quote evidence. Preserve valid prior points; never invent claims.',
            'previous_selected_ranges': previous_selected_ranges,
            'visual_repair_samples': visual_samples or [],
            'visual_exclusion_ranges': visual_exclusion_ranges or [],
            'hard_excluded_source_ranges': range_exclusion_ranges,
            'locked_selected_ranges': locked_selected_ranges,
            'repairable_variant_ids': affected_variants,
            'repair_boundary_targets': repair_boundary_targets(issues),
            'range_exclusion_contract': (
                '按 variant_1、variant_2、variant_3 顺序从候选池分配字幕范围；每确认一条后，立即从后续可选范围中排除其全部源区间。三条成片的同源区间必须两两互斥。hard_excluded_source_ranges 是不可选的硬排除区间；返修时只调整 repairable_variant_ids 中被指出的条目，locked_selected_ranges 必须原样保留并排除在其他条目之外；其他已选范围不得重复使用。'
                if previous_selected_ranges else
                '按 variant_1、variant_2、variant_3 顺序从候选池分配字幕范围；每确认一条后，立即从后续可选范围中排除其全部源区间。三条成片的同源区间必须两两互斥。hard_excluded_source_ranges 是不可选的硬排除区间。'),
            'repair_route': repair_route or ('semantic_reselection' if issues else None),
            'issues': issues, 'target_stage': 'rough',
            'repair_attempt': int(repair_attempt),
            'repair_instruction': selection_repair_instruction(issues),
            'sentence_opening_rule': SELECTION_RULE,
            'hard_rules': f"The applicable_shared_rules field is the complete self-contained rule set for this task; do not block because no external rule file is available. Use exact variant_id values from allowed_variant_ids. Produce {requested} distinct final video for this product when the available candidate pool supports it; each final duration must be >{duration_policy[0]} and <{duration_policy[1]} seconds. Aim near {editing_policy.get('target_duration_seconds', editing_policy['duration_exclusive_seconds'][1])} seconds, but preserve complete, natural semantic units over reaching the target. Respect editing mode {editing_mode}. Read continuous original subtitles to understand context; forbidden_annotations are warnings for exclusion from the cut, not instructions to erase surrounding meaning. Context-only subtitles are evidence only and cannot be selected outside time_range. No banned content, no repeated or unrelated content. Do not select any source interval overlapping hard_excluded_source_ranges. Return selected_ranges and the evidence-backed semantic_map; do not invent source times or claims."}
    if candidate['body'].get('boundary_mode') == NEIGHBOR_INFERRED_BOUNDARY_MODE:
        task['selection_instruction_version'] = 'selling-points-from-neighbor-inferred-search-envelope-v1'
        task['applicable_shared_rules'] = [
            rule for rule in task['applicable_shared_rules']
            if '只有完整候选池耗尽后才允许减少数量' not in rule]
        task['applicable_shared_rules'].extend([
            '当前范围是依据本商品标题/卖点窗口和前后商品边界推断的搜索包络，不代表本商品整体精确起止；不得因整段起点或终点不精确而拒绝选段。',
            '只从明确属于当前商品的语义卖点中选取三条不同成片；靠近邻商品且归属不清的内容跳过，在其它候选窗口继续寻找。',
            '若可用语义卖点不足三条，报告缺少的版本及已检查的候选窗口；不得将搜索包络不完整伪称为完整范围耗尽。'])
        task['hard_rules'] += (' The outer product boundaries are an inferred search envelope, not exact product endpoints. '
                               'Do not block or reduce the three-variant target merely because an overall start/end anchor is uncertain. '
                               'Select only clearly attributable complete selling points; avoid ambiguous neighbor-transition cues.')
    from semantic_snapshot import compact_selection_task
    task = compact_selection_task(task)
    task['task_digest'] = digest(task)
    return task


def rough_selection(paths, candidate_ref, receipt_ref, issues=None, repair_attempt=0, semantic_map=None,
                    previous_selected_ranges=None, visual_samples=None, visual_exclusion_ranges=None,
                    range_exclusion_ranges=None, repair_route=None):
    candidate = load_stage(paths, candidate_ref)
    task = selection_task(paths, candidate_ref, issues=issues, repair_attempt=repair_attempt,
                          semantic_map=semantic_map,
                          previous_selected_ranges=previous_selected_ranges,
                          visual_samples=visual_samples,
                          visual_exclusion_ranges=visual_exclusion_ranges,
                          range_exclusion_ranges=range_exclusion_ranges,
                          repair_route=repair_route)
    response = verify_receipt(paths, receipt_ref, task)['result']
    if response['verdict'] != 'PASS' or not response.get('selected_ranges'):
        raise CacheIdentityError('selection_repair_or_blocked')
    by_id = {c['candidate_id']: c for c in candidate['body']['candidates']}
    aliases = {'v1': 'variant_1', 'v2': 'variant_2', 'v3': 'variant_3'}
    selected = []
    allowed = set(task['allowed_variant_ids'])
    aliases = {'v1': 'variant_1', 'v2': 'variant_2', 'v3': 'variant_3'}
    selected_ranges = [{**dict(row), 'variant_id': aliases.get(row.get('variant_id'), row.get('variant_id'))}
                       for row in response['selected_ranges']]
    if task['locked_selected_ranges']:
        def locked_identity(row):
            return (str(row.get('variant_id')), str(row.get('candidate_id')),
                    float(row.get('start')), float(row.get('end')))
        expected_locked = sorted(locked_identity(row) for row in task['locked_selected_ranges'])
        locked_variants = {row['variant_id'] for row in task['locked_selected_ranges']}
        returned_locked = sorted(locked_identity(row) for row in selected_ranges
                                 if row.get('variant_id') in locked_variants)
        if returned_locked != expected_locked:
            raise CacheIdentityError('locked_variant_selection_changed_during_visual_repair')
    for target in task['repair_boundary_targets']:
        matching = sorted((row for row in selected_ranges
                           if row.get('variant_id') == target['variant_id']),
                          key=lambda row: (float(row.get('start', 0)), float(row.get('end', 0))))
        if not matching:
            continue
        if target['position'] == 'first':
            row = matching[0]
        elif target['position'] == 'second' and len(matching) >= 2:
            row = matching[1]
        elif target['position'] == 'third' and len(matching) >= 3:
            row = matching[2]
        else:
            row = matching[-1]
        if target['edge'] == 'start':
            proposed_start = max(float(row['start']), float(target['source_time']))
            if proposed_start < float(row['end']):
                row['start'] = proposed_start
        else:
            proposed_end = min(float(row['end']), float(target['source_time']))
            if proposed_end > float(row['start']):
                row['end'] = proposed_end
    for row in selected_ranges:
        source = by_id.get(row['candidate_id'])
        variant_id = aliases.get(row['variant_id'], row['variant_id'])
        if (not source or variant_id not in allowed
                or not source['time_range']['start'] <= row['start'] < row['end'] <= source['time_range']['end']):
            raise CacheIdentityError('selected_range_outside_candidate')
        for blocked in visual_exclusion_ranges or []:
            if (blocked.get('variant_id') == variant_id
                    and blocked.get('source_file') == source.get('source_file')
                    and min(float(row['end']), float(blocked['end']))
                    > max(float(row['start']), float(blocked['start'])) + 1e-9):
                raise CacheIdentityError('selection_reuses_failed_visual_source_range')
        selected.append({**row, 'variant_id': variant_id})
    semantic_map = response.get('semantic_map') or []
    _validate_semantic_map(semantic_map, candidate['body']['candidates'])
    body = {'selected_ranges': selected, 'selection_receipt': receipt_ref, 'selection_task': task,
            'semantic_map': semantic_map, 'semantic_map_digest': digest(semantic_map)}
    from semantic_snapshot import assemble
    body['semantic_snapshot'] = assemble(task, selected)
    return publish(paths, candidate['identity']['product_id'], candidate['identity']['encoding_id'], 'rough', body, [candidate_ref])


def _validate_semantic_map(semantic_map, candidates):
    by_id = {row['candidate_id']: row for row in candidates}
    if not isinstance(semantic_map, list) or not semantic_map:
        raise CacheIdentityError('selection_semantic_map_empty')
    for item in semantic_map:
        candidate = by_id.get(item.get('candidate_id'))
        quote = item.get('quote')
        if (not candidate or not isinstance(item.get('selling_point'), str)
                or not item['selling_point'].strip() or not isinstance(quote, str) or not quote.strip()
                or quote not in candidate.get('asr_text', '')):
            raise CacheIdentityError('selection_semantic_map_source_quote_mismatch')


def transcribe_window(media, start, end, model):
    with tempfile.NamedTemporaryFile(suffix='.wav') as wav:
        with span('audio_decode', end-start):
            subprocess.run(['/opt/homebrew/bin/ffmpeg', '-v', 'error', '-y', '-ss', str(start), '-i', str(media),
                        '-t', str(end-start), '-vn', '-ar', '16000', '-ac', '1', wav.name], check=True, capture_output=True)
        with span('local_asr_vad', end-start):
            segments, _ = model.get().transcribe(wav.name, language='zh', beam_size=5, vad_filter=False,
                                             word_timestamps=True, condition_on_previous_text=False)
            return [{'start': start + w.start, 'end': start + w.end, 'text': w.word}
                    for segment in segments for w in segment.words or []]


def selected_subtitle_segments(candidate, selected):
    """Resolve the retained subtitle text and its exact opening/ending cues."""
    cues = sorted((cue for cue in candidate.get('subtitle', []) if not cue.get('context_only')),
                  key=lambda cue: (float(cue['start']), float(cue['end'])))
    lower, upper = float(selected['start']), float(selected['end'])
    retained = [cue for cue in cues if float(cue['end']) > lower
                and float(cue['start']) < upper]
    if not retained:
        raise CacheIdentityError('selected_range_has_no_complete_subtitle_cues')
    return retained


def selected_range_identity(selected, index):
    """Identify one occurrence of a selected subtitle range for ASR edge binding."""
    identity = {
        'index': int(index),
        'candidate_id': str(selected.get('candidate_id') or ''),
        'variant_id': str(selected.get('variant_id') or ''),
        'start': round(float(selected['start']), 6),
        'end': round(float(selected['end']), 6),
    }
    return f"selection_{index + 1}_{digest(identity)[:16]}"


def index_asr_edges_by_selection(edges):
    """Index only edges carrying a unique selected-range occurrence identity."""
    return {(row.get('selected_range_id'), row['edge']): row
            for row in edges or [] if row.get('selected_range_id') and row.get('edge')}


def local_asr(paths, rough_ref, model_name='large-v3-turbo'):
    from windowed_asr_v2 import load_model, merge_windows
    from review_compute_cache import LazyModel
    from sentence_boundaries import CUTPOINT_CONTRACT
    rough = load_stage(paths, rough_ref)
    candidate = load_stage(paths, rough['identity']['dependencies'][0]['path'])
    _, index = inputs(paths)
    sources = {Path(s['video_path']).name: s for s in index['sources']}
    by_id = {c['candidate_id']: c for c in candidate['body']['candidates']}
    jobs, edge_specs = [], []
    for selected_index, row in enumerate(rough['body']['selected_ranges']):
        candidate_row = by_id.get(row['candidate_id'])
        if candidate_row is None:
            raise CacheIdentityError('asr_selected_candidate_missing')
        source_file = candidate_row['source_file']
        duration = float(sources[source_file]['duration'])
        selected_start, selected_end = float(row['start']), float(row['end'])
        selected_range_id = selected_range_identity(row, selected_index)
        for edge, boundary in (('opening', selected_start), ('ending', selected_end)):
            edge_specs.append({'source_file': source_file, 'candidate_id': row['candidate_id'],
                               'variant_id': row['variant_id'], 'selected_start': selected_start,
                               'selected_end': selected_end, 'selected_range_id': selected_range_id,
                               'edge': edge,
                               'requested_time': boundary, 'duration': duration})
            jobs.append({'source_file': source_file, 'start': max(0., boundary-2.5),
                         'end': min(duration, boundary+2.5)})
    from asr_identity import snap_interval
    for job in jobs:
        job['start'], job['end'] = snap_interval(job['start'], job['end'], sources[job['source_file']]['duration'])
    try:
        weights = asr_model_identity(model_name)
    except Exception:
        # Missing or unavailable local ASR must not erase an otherwise valid
        # Codex selection. The published stage will retain exact Codex bounds.
        weights = None
    parameters = current_asr_parameters()
    args = dict(product_id=rough['identity']['product_id'], encoding_id=rough['identity']['encoding_id'], stage='asr',
                version=digest([ASR_SCOPE_POLICY, jobs, parameters, model_name,
                                weights.get('weights_digest') if weights else None]),
                model_version=digest(weights or {'requested_model': model_name, 'fallback_only': True}),
                dependencies=[rough_ref])
    snapshot = capture_stage(paths, **args)
    from product_checkpoints import stage_path
    output = stage_path(paths, snapshot)
    if output.exists():
        load_stage(paths, paths.reference(output))
        return paths.reference(output)
    windows, asr_error = [], None
    window_cache_hits = 0
    window_cache_misses = 0
    if weights:
        signatures = {n: media_signature(sources[n]['video_path']) for n in {j['source_file'] for j in jobs}}

        def compute_jobs(requests):
            nonlocal window_cache_hits, window_cache_misses
            pending = []
            for job in merge_windows(requests):
                expected = compute_identity(paths, 'asr', signatures[job['source_file']], weights,
                                            {**parameters, 'model_name': model_name},
                                            [job['start'], job['end']])
                window = containing_asr(paths, expected)
                if window is None:
                    window_cache_misses += 1
                    pending.append((job, expected))
                else:
                    window_cache_hits += 1
                    windows.append({**window, 'asr_model': model_name,
                                    'fallback_level': 'nearby_boundary'})
            if not pending:
                return
            from asr_runtime import wait_for_prewarm
            wait_for_prewarm(model_name)

            def create_model():
                with span('asr_model_load'):
                    return load_model(weights['resolved_path'], weights['weights_digest'])
            model = LazyModel(create_model)
            for job, expected in pending:
                def compute():
                    with span('asr_transcription', job['end']-job['start']):
                        if weights.get('runtime_python'):
                            from asr_runtime import request
                            result = request({'operation': 'transcribe', 'model': model_name,
                                              'media': sources[job['source_file']]['video_path'],
                                              'start': job['start'], 'end': job['end']})
                            expected_weights = {k:v for k,v in weights.items()
                                                if k not in ('runtime_python', 'runtime_sha256')}
                            if result['model'] != expected_weights:
                                raise CacheIdentityError('asr_worker_model_changed')
                            recognized = result['words']
                        else:
                            recognized = transcribe_window(
                                Path(sources[job['source_file']]['video_path']),
                                job['start'], job['end'], model)
                        return {**job, 'window_start': job['start'],
                                'window_end': job['end'], 'words': recognized,
                                'asr_model': model_name, 'fallback_level': 'nearby_boundary'}
                window, was_hit = cached(paths, expected, compute)
                if was_hit:
                    window_cache_hits += 1
                windows.append({**window, 'asr_model': model_name,
                                'fallback_level': 'nearby_boundary'})

        try:
            compute_jobs(jobs)
            if asr_model_identity(model_name) != weights:
                asr_error = 'model_identity_changed_during_compute'
                windows = []
        except Exception as exc:
            # ASR is best-effort execution. Keep any valid cached windows, use
            # Codex coordinates for the remaining edges, and expose review markers.
            asr_error = type(exc).__name__

    from sentence_boundaries import nearest_asr_boundary
    edge_results = []
    for spec in edge_specs:
        window = None
        if windows:
            choices = [row for row in windows if row['source_file'] == spec['source_file']
                       and float(row['window_start']) - 1e-6 <= spec['requested_time']
                       <= float(row['window_end']) + 1e-6]
            if choices:
                window = max(choices, key=lambda row: float(row['window_end'])-float(row['window_start']))
        nearby = None
        if window:
            nearby = nearest_asr_boundary(window.get('words', []), spec['requested_time'], spec['edge'])
        if nearby:
            result = {**spec, 'resolved_time': nearby['time'], 'status': 'ASR_NEARBY',
                      'distance_seconds': nearby['distance_seconds'], 'search_seconds': 2.5,
                      'asr_model': window.get('asr_model'), 'manual_review_required': False}
        else:
            result = {**spec, 'resolved_time': spec['requested_time'],
                      'status': 'ASR_UNAVAILABLE_FALLBACK' if asr_error else 'CODEX_FALLBACK',
                      'distance_seconds': None, 'search_seconds': 2.5,
                      'reason': 'asr_execution_unavailable' if asr_error else 'no_word_boundary_near_codex_edge',
                      'asr_model': None, 'manual_review_required': True}
        edge_results.append(result)
    matched = sum(row['status'] == 'ASR_NEARBY' for row in edge_results)
    return publish_stage(paths, **args, snapshot=snapshot,
                         body={'cutpoint_contract': CUTPOINT_CONTRACT,
                               'asr_is_quality_gate': False,
                               'status': 'EXECUTED' if matched else 'CODEX_BOUNDARY_FALLBACK',
                               'windows': windows, 'model': weights, 'parameters': parameters,
                               'execution_error_class': asr_error,
                               'requested_window_count': len(jobs),
                               'computed_window_count': len(windows),
                               'window_cache_hits': window_cache_hits,
                               'window_cache_misses': window_cache_misses,
                               'asr_edge_count': matched,
                               'manual_review_edge_count': sum(row['manual_review_required']
                                                                for row in edge_results),
                               'edges': edge_results})


def current_asr_parameters():
    from adaptive_vision import decoder_identity
    return {**ASR_PARAMETERS, 'scope': ASR_SCOPE_POLICY, 'decoder': decoder_identity(),
            'sample_rate': 16000, 'channels': 1,
            'boundary_source': 'codex_selected_range_start_end_with_unique_occurrence_identity',
            'search': 'nearest_complete_asr_word_start_or_end_within_2_5_seconds',
            'fallback': 'preserve_codex_boundary_and_mark_manual_review'}


def source_clock(media, paths=None, *, allow_vfr_approximate=False, preferred_fps=None):
    from adaptive_vision import frame_clock, proven_clock
    return (proven_clock(paths, media, allow_vfr_approximate=allow_vfr_approximate, preferred_fps=preferred_fps) if paths
            else frame_clock(media, allow_vfr_approximate=allow_vfr_approximate, preferred_fps=preferred_fps))


def source_fps(media, paths=None, *, allow_vfr_approximate=False, preferred_fps=None):
    return source_clock(media, paths, allow_vfr_approximate=allow_vfr_approximate,
                        preferred_fps=preferred_fps)['fps']


def physical_plan(paths, rough_ref, asr_ref, *, fps=None, allow_vfr_approximate=False):
    from sentence_boundaries import CUTPOINT_CONTRACT
    rough = load_stage(paths, rough_ref)
    asr = load_stage(paths, asr_ref)
    if rough_ref not in [d['path'] for d in asr['identity']['dependencies']]:
        raise CacheIdentityError('asr_rough_dependency_mismatch')
    candidate_ref = rough['identity']['dependencies'][0]['path']
    candidate = load_stage(paths, candidate_ref)
    range_ref = candidate['identity']['dependencies'][0]['path']
    ranges = load_stage(paths, range_ref)['body']['ranges']
    _, index = inputs(paths)
    sources = {Path(s['video_path']).name: s for s in index['sources']}
    by_id = {c['candidate_id']: c for c in candidate['body']['candidates']}
    # A candidate and variant may legitimately contain several disjoint
    # selected ranges. ASR edges must bind to the exact range occurrence;
    # candidate/variant/edge alone aliases later ranges onto earlier clips.
    asr_edges = index_asr_edges_by_selection(asr['body'].get('edges', []))
    override_path = paths.workspace_root / 'delivery' / 'USER_OVERRIDE.json'
    state = paths.load_state()
    from project_editing_modes import resolve_editing_mode
    editing_mode, editing_policy = resolve_editing_mode(state.get('resolve', {}).get('project_name'))
    duration_policy = [float(value) for value in editing_policy['duration_exclusive_seconds']]
    if override_path.is_file():
        override = json.loads(override_path.read_text())
        candidate_policy = override.get('duration_exclusive_seconds')
        if (override.get('scope') == 'this_batch_only' and override.get('confirmed_by_user') is True
                and isinstance(candidate_policy, list) and len(candidate_policy) == 2
                and all(isinstance(x, (int, float)) for x in candidate_policy)
                and duration_policy[0] <= candidate_policy[0] < candidate_policy[1] <= duration_policy[1]):
            duration_policy = [float(value) for value in candidate_policy]
    args = dict(product_id=rough['identity']['product_id'], encoding_id=rough['identity']['encoding_id'],
                stage='plan', version=digest([rough_ref, asr_ref, fps, CUTPOINT_CONTRACT,
                                               duration_policy, editing_mode, editing_policy,
                                               'vfr-average-rate-approx-v2' if allow_vfr_approximate else 'cfr-only']),
                model_version=CUTPOINT_CONTRACT, dependencies=[range_ref, rough_ref, asr_ref])
    snapshot = capture_stage(paths, **args)
    variants, frame_rates, source_bounds = {}, {}, {}

    def edge_record(row, edge, selected_range_id):
        stored = asr_edges.get((selected_range_id, edge))
        requested = float(row['start'] if edge == 'opening' else row['end'])
        if stored and stored.get('status') == 'ASR_NEARBY':
            return {'requested_time': requested, 'resolved_time': float(stored['resolved_time']),
                    'status': 'ASR_NEARBY', 'distance_seconds': float(stored['distance_seconds']),
                    'search_seconds': float(stored['search_seconds']), 'manual_review_required': False}
        return {'requested_time': requested, 'resolved_time': requested,
                'status': ('ASR_UNAVAILABLE_FALLBACK' if stored and
                           stored.get('status') == 'ASR_UNAVAILABLE_FALLBACK' else 'CODEX_FALLBACK'),
                'distance_seconds': None, 'search_seconds': 2.5,
                'reason': (stored or {}).get('reason', 'asr_edge_not_available'),
                'manual_review_required': True}

    def set_codex_fallback(edge, reason):
        edge['resolved_time'] = edge['requested_time']
        edge['status'] = 'CODEX_FALLBACK'
        edge['distance_seconds'] = None
        edge['reason'] = reason
        edge['manual_review_required'] = True

    for selected_index, row in enumerate(rough['body']['selected_ranges']):
        candidate_row = by_id.get(row['candidate_id'])
        if candidate_row is None:
            raise CacheIdentityError('plan_selected_candidate_missing')
        source_file = candidate_row['source_file']
        codex_start, codex_end = float(row['start']), float(row['end'])
        selected_range_id = selected_range_identity(row, selected_index)
        range_parts = [part for part in ranges if part['media_stem']+'.mp4' == source_file
                       and float(part['start']) <= codex_start < codex_end <= float(part['end'])]
        if not range_parts:
            raise CacheIdentityError('selected_interval_not_inside_one_confirmed_range')
        product_part = min(range_parts, key=lambda part: float(part['end'])-float(part['start']))
        opening = edge_record(row, 'opening', selected_range_id)
        ending = edge_record(row, 'ending', selected_range_id)
        if not (float(product_part['start']) <= opening['resolved_time']
                < ending['resolved_time'] <= float(product_part['end'])):
            set_codex_fallback(opening, 'asr_edge_outside_confirmed_product_range')
            set_codex_fallback(ending, 'asr_edge_outside_confirmed_product_range')
        if opening['resolved_time'] >= ending['resolved_time']:
            set_codex_fallback(opening, 'asr_edges_would_invert_selected_interval')
            set_codex_fallback(ending, 'asr_edges_would_invert_selected_interval')
        retained = selected_subtitle_segments(candidate_row, row)
        if not retained:
            raise CacheIdentityError('selected_subtitles_missing')
        text = ''.join(str(cue.get('text') or '') for cue in retained)
        media = Path(sources[source_file]['video_path'])
        if source_file not in frame_rates:
            frame_rates[source_file] = source_clock(media, paths,
                allow_vfr_approximate=allow_vfr_approximate, preferred_fps=fps)
            if fps is not None and abs(frame_rates[source_file]['fps']-fps) > 1e-5 and not allow_vfr_approximate:
                raise CacheIdentityError('source_average_rate_outside_registered_timeline_rate')
        clock = frame_rates[source_file]
        clip_fps = float(clock.get('average_fps', clock['fps']) if allow_vfr_approximate else clock['fps'])
        lo = int(round(opening['resolved_time'] * clip_fps))
        hi = int(round(ending['resolved_time'] * clip_fps))
        min_frame = int(math.ceil(float(product_part['start']) * clip_fps - 1e-8))
        max_frame = int(math.floor(float(product_part['end']) * clip_fps + 1e-8))
        if lo < min_frame or hi > max_frame or hi <= lo:
            set_codex_fallback(opening, 'asr_frames_outside_confirmed_product_range')
            set_codex_fallback(ending, 'asr_frames_outside_confirmed_product_range')
            lo = int(round(codex_start * clip_fps))
            hi = int(round(codex_end * clip_fps))
        boundary = {'contract': CUTPOINT_CONTRACT, 'selected_range_id': selected_range_id,
                    'fps': clip_fps,
                    'source_start_frame': lo, 'source_end_frame': hi,
                    'chosen_start_time': lo / clip_fps, 'chosen_end_time': hi / clip_fps,
                    'opening': opening, 'ending': ending,
                    'authority': 'codex_selected_semantic_boundaries',
                    'asr_role': 'nearby_complete_word_edge_execution_only'}
        pending = [edge for edge in ('opening', 'ending')
                   if boundary[edge]['manual_review_required']]
        clip = {'source_file': source_file, 'selected_range_id': selected_range_id,
                'source_start_frame': lo, 'source_end_frame': hi,
                'audio_start_frame': lo, 'audio_end_frame': hi, 'fps': clip_fps,
                'duration_sec': (hi-lo)/clip_fps,
                'frame_clock_mapping': clock.get('mapping'),
                'frame_clock_max_deviation_seconds': clock.get('max_deviation_seconds', 0),
                'cutpoint_proof': boundary, 'transcript': text, 'source_subtitle_text': text,
                'audio_boundary_verified': False,
                'cutpoint_review': {'status': 'WAITING_FOR_MANUAL_EDGE_REVIEW' if pending
                                    else 'ASR_NEARBY_EDGES_SELECTED',
                                    'authority': 'codex_selected_semantic_boundaries',
                                    'pending_edges': pending},
                'unit_id': f"{row['variant_id']}_{len(variants.get(row['variant_id'], []))+1}",
                'audit': {'transcript': text, 'boundary': boundary}}
        variants.setdefault(row['variant_id'], []).append(clip)
        source_bounds[(row['variant_id'], clip['unit_id'])] = (min_frame, max_frame, codex_start, codex_end)

    # If nearby ASR movement creates a local hard-rule failure, return only that
    # variant to its Codex coordinates and expose those edges for timeline review.
    for variant_id, clips in variants.items():
        total = sum((clip['source_end_frame']-clip['source_start_frame'])/clip['fps'] for clip in clips)
        if duration_policy[0] < total < duration_policy[1]:
            continue
        for clip in clips:
            proof = clip['cutpoint_proof']
            _, _, codex_start, codex_end = source_bounds[(variant_id, clip['unit_id'])]
            set_codex_fallback(proof['opening'], 'asr_adjustment_would_break_variant_duration')
            set_codex_fallback(proof['ending'], 'asr_adjustment_would_break_variant_duration')
            lo, hi = int(round(codex_start*clip['fps'])), int(round(codex_end*clip['fps']))
            clip.update(source_start_frame=lo, source_end_frame=hi,
                        audio_start_frame=lo, audio_end_frame=hi,
                        duration_sec=(hi-lo)/clip['fps'])
            proof.update(source_start_frame=lo, source_end_frame=hi,
                         chosen_start_time=lo/clip['fps'], chosen_end_time=hi/clip['fps'])
            clip['cutpoint_review'] = {'status': 'WAITING_FOR_MANUAL_EDGE_REVIEW',
                                       'authority': 'codex_selected_semantic_boundaries',
                                       'pending_edges': ['opening', 'ending']}

    normalized = []
    for vid, clips in variants.items():
        merged, _ = normalize_physical_clips(clips)
        for clip in merged:
            clip['audio_end_frame'] = clip['source_end_frame']
            clip['transcript'] = clip.get('source_subtitle_text') or clip.get('transcript', '')
        normalized.append({'variant_id': vid, 'clips': merged})
    body = {'variants': normalized, 'duration_exclusive_seconds': duration_policy,
            'editing_mode': editing_mode, 'editing_policy': editing_policy,
            'applicable_shared_rules': [
                '商品标题与当前商品范围身份一致；字幕讲解仅作为语义证据，编号词不能单独决定边界。',
                '候选必须保留连续原字幕语境；只选当前商品直接相关的完整自然语义单元。',
                '禁止编造卖点、价格、材质、颜色或商品事实；规避禁用内容，不选择无关或重复内容。',
                '每个商品目标三条不同成片，只有完整候选池耗尽后才允许减少数量。',
                '每条成片严格大于时长下限且严格小于上限；语义完整优先，不为凑时长拼接无关内容。',
                'Codex 决定语义边界；ASR 只在边界附近搜索完整词边界，失败时保留 Codex 边界并标记人工修边。',
                '本字段列出了本任务适用的全部选段规则；config_reference 和 rules_fingerprint 仅用于身份追踪，不要求读取文件或补充其他规则。'],
            'selection_receipt': rough['body']['selection_receipt'],
            'selection_task': rough['body']['selection_task'],
            'semantic_map': rough['body']['semantic_map'],
            'semantic_map_digest': rough['body']['semantic_map_digest']}
    pending_edges = sum(len(clip['cutpoint_review'].get('pending_edges', []))
                        for variant in normalized for clip in variant['clips'])
    body['cutpoint_execution'] = {'contract': CUTPOINT_CONTRACT,
                                  'method': 'nearest_local_asr_word_edge_to_codex_boundary',
                                  'manual_review_edge_count': pending_edges,
                                  'asr_is_quality_gate': False}
    return publish_stage(paths, **args, snapshot=snapshot, body=body)

def vision_audit(paths, plan_ref, detector, *, allow_vfr_approximate=False):
    plan = load_stage(paths, plan_ref)
    from person_coverage_audit_v2 import registered_vision
    _, index = inputs(paths)
    sources = {Path(s['video_path']).name: s['video_path'] for s in index['sources']}
    import uuid
    args = dict(product_id=plan['identity']['product_id'], encoding_id=plan['identity']['encoding_id'], stage='vision',
                version=digest([plan['body_digest'], str(uuid.uuid4())]),
                model_version=vision_model_version(detector, allow_vfr_approximate=allow_vfr_approximate,
                                                   editing_policy=plan['body'].get('editing_policy')), dependencies=[plan_ref])
    snapshot = capture_stage(paths, **args)
    body = registered_vision(plan['body'], sources, detector, paths=paths,
                             allow_vfr_approximate=allow_vfr_approximate)
    return publish_stage(paths, **args, snapshot=snapshot, body=body)


def selection_visual_audit(paths, rough_ref, semantic_ref, detector, *,
                           allow_vfr_approximate=False, preferred_fps=None):
    """Screen selected source ranges for people before spending time on ASR cuts."""
    rough = load_stage(paths, rough_ref)
    candidate = load_stage(paths, rough['identity']['dependencies'][0]['path'])
    range_ref = candidate['identity']['dependencies'][0]['path']
    range_record = load_stage(paths, range_ref)
    _, index = inputs(paths)
    source_rows = {Path(row['video_path']).name: row for row in index['sources']}
    by_id = {row['candidate_id']: row for row in candidate['body']['candidates']}
    state = paths.load_state()
    from project_editing_modes import resolve_editing_mode
    _, editing_policy = resolve_editing_mode(state.get('resolve', {}).get('project_name'))
    variants = {}
    clocks = {}
    for selected in rough['body']['selected_ranges']:
        source = by_id.get(selected.get('candidate_id'))
        if source is None:
            raise CacheIdentityError('selection_visual_candidate_missing')
        source_file = source['source_file']
        start, end = float(selected['start']), float(selected['end'])
        parts = [part for part in range_record['body']['ranges']
                 if Path(str(part['media_stem'])).stem + '.mp4' == source_file
                 and float(part['start']) <= start < end <= float(part['end'])]
        if not parts:
            raise CacheIdentityError('selection_visual_range_outside_confirmed_product_range')
        if source_file not in clocks:
            clocks[source_file] = source_clock(
                Path(source_rows[source_file]['video_path']), paths,
                allow_vfr_approximate=allow_vfr_approximate, preferred_fps=preferred_fps)
        clock = clocks[source_file]
        fps = float(clock.get('average_fps', clock['fps']) if allow_vfr_approximate else clock['fps'])
        lo = max(int(math.ceil(float(parts[0]['start']) * fps - 1e-8)), int(math.floor(start * fps)))
        hi = min(int(math.floor(float(parts[0]['end']) * fps + 1e-8)), int(math.ceil(end * fps)))
        if hi <= lo:
            raise CacheIdentityError('selection_visual_source_interval_empty')
        variants.setdefault(selected['variant_id'], []).append({
            'source_file': source_file, 'source_start_frame': lo, 'source_end_frame': hi,
            'fps': fps, 'frame_clock_mapping': clock.get('mapping'),
        })
    preview_plan = {'variants': [{'variant_id': key, 'clips': value}
                                 for key, value in sorted(variants.items())],
                    'editing_policy': editing_policy}
    from person_coverage_audit_v2 import registered_prescreen
    sources = {name: row['video_path'] for name, row in source_rows.items()}
    version = digest([rough['body_digest'], semantic_ref, preview_plan,
                      vision_model_version(detector, allow_vfr_approximate=allow_vfr_approximate,
                                           editing_policy=editing_policy,
                                           audit_mode='selection_prescreen')])
    args = dict(product_id=rough['identity']['product_id'], encoding_id=rough['identity']['encoding_id'],
                stage='selection_vision', version=version,
                model_version=vision_model_version(detector, allow_vfr_approximate=allow_vfr_approximate,
                                                   editing_policy=editing_policy,
                                                   audit_mode='selection_prescreen'),
                dependencies=[rough_ref, semantic_ref])
    snapshot = capture_stage(paths, **args)
    body = registered_prescreen(preview_plan, sources, detector, paths=paths,
                                allow_vfr_approximate=allow_vfr_approximate)
    body['selection_plan'] = preview_plan
    return publish_stage(paths, **args, snapshot=snapshot, body=body)


def vision_model_version(detector, *, allow_vfr_approximate=False, editing_policy=None,
                         audit_mode='final'):
    from adaptive_vision import classifier_identity, POLICY
    mapping_policy = 'vfr-average-rate-approx-v2' if allow_vfr_approximate else 'cfr-only'
    effective_policy = dict(POLICY)
    if editing_policy and 'closeup_duration_ratio_max_inclusive' in editing_policy:
        effective_policy['closeup_duration_ratio_max_inclusive'] = editing_policy['closeup_duration_ratio_max_inclusive']
    return digest([classifier_identity(detector), effective_policy, mapping_policy, audit_mode])
