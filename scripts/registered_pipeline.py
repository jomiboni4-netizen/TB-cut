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
from sentence_boundaries import CONTRACT as SENTENCE_CONTRACT, SELECTION_RULE, bounded_edges
from compute_metrics import span
from compute_store import media_signature, identity as compute_identity, cached, containing_asr
from asr_identity import model_identity as asr_model_identity, PARAMETERS as ASR_PARAMETERS

ASR_SCOPE_POLICY = 'subtitle-text-nearby-edges-v5-short-window-no-vad'
RANGE_MATCHING_PROTOCOL = 'codex-distinctive-title-window-v3-next-product-boundary-context'


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
    from codex_worker import execute, verify_receipt

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
    for product in products:
        pid = int(product['product_index'])
        rough_product = rough_by_id.get(pid) or {}
        windows = []
        joined_windows = [extend_to_next_distinctive_boundary(
            {**window, 'product_index': pid}, rough['products'], source_bounds)
            for window in join_nearby_range_windows(rough_product.get('windows', []))]
        distinctive_windows = [window for window in joined_windows
                               if window.get('distinctive_terms')]
        # Generic feature hits can occur throughout a long live stream and
        # add no product-identity proof. When exact distinctive-title evidence
        # exists, exclude generic-only windows from the worker package.
        task_windows = distinctive_windows or joined_windows
        for window in task_windows:
            # source_stem and global bounds are already carried once by the
            # enclosing window. Repeating them on thousands of cues can push a
            # legitimate single-product range task over the worker envelope.
            cues = [[float(cue['start']), float(cue['end']),
                     str(cue.get('text') or '')[:500]] for cue in index['cues']
                    if float(window['global_start']) <= float(cue.get('global_start', 0))
                    and float(cue.get('global_end', cue.get('global_start', 0))) <= float(window['global_end'])]
            windows.append({
                'window_id': window['window_id'], 'source_stem': window['source_stem'],
                'global_start': window['global_start'], 'global_end': window['global_end'],
                'boundary_context': window.get('boundary_context', 'fixed_context'),
                'distinctive_terms': window['distinctive_terms'],
                'cue_encoding': ['local_start', 'local_end', 'exact_text'],
                'cues': cues})
        payload = {
            'schema_version': 2, 'role': 'semantic_range_matching',
            'range_matching_protocol': RANGE_MATCHING_PROTOCOL,
            'review_scope': 'current_batch_product_boundary_matching',
            'project_key': project_key, 'product': product,
            'source_timeline': [{'source_stem': row['source_stem'],
                                 'global_start': float(row['global_start']),
                                 'global_end': float(row['global_end'])}
                                for row in index['sources']],
            'lexical_candidate_windows': windows,
            'instructions': [
                'Identify the complete contiguous broadcast section for this exact product using the supplied title and subtitle-index evidence.',
                'Each window supplies one source_stem and global bounds. Each cue inside it is [local_start, local_end, exact_text]. Choose start/end anchors from supplied cues and return the enclosing source_stem, local cue time, and verbatim quote.',
                'Use one or more distinctive title anchors that occur verbatim in supplied cues and lie inside the product range.',
                'Do not infer ownership from item numbers, product order, generic words, or a single ambiguous mention.',
                'Check adjacent context to keep section boundaries from absorbing neighboring products. If evidence is ambiguous or the section is not fully represented, return REPAIRABLE with issues and range_decision null.',
                'This is a source-range decision only; do not select editing clips or produce cutpoints.']}
        payload['task_digest'] = digest(payload)
        if len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) > 120000:
            raise CacheIdentityError(f'project_range_matching_task_too_large_product_{pid}')
        receipt_ref = execute(paths, payload)
        response = verify_receipt(paths, receipt_ref, payload)['result']
        if response.get('verdict') != 'PASS' or not response.get('range_decision'):
            issues = response.get('issues') or ['Codex 未能确认完整商品范围']
            raise CacheIdentityError(f'project_range_matching_failed_product_{pid}:' + '|'.join(issues)[:500])
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
            'start_anchor': matched.get('start'), 'end_anchor': matched.get('end'),
            'title_anchors': matched.get('title_anchors')})

    manifest = {'schema_version': 1, 'authority': 'user_directed_semantic_inference',
                'manifest_source': 'codex_batch_match_v1',
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
    body = {'candidates': pool, 'removed_count': len(removed), 'complete_range_scanned': True,
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
                   previous_selected_ranges=None, visual_samples=None, visual_exclusion_ranges=None):
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
    issues = [str(issue)[:240] for issue in (issues or []) if str(issue).strip()]
    previous_selected_ranges = [dict(row) for row in (previous_selected_ranges or [])]
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
            'selection_instruction_version': 'visual-repair-samples-v3',
            'config_reference': 'PROJECT_CONFIG.json', 'product_id': identity['product_id'],
            'encoding_id': identity['encoding_id'], 'rules_fingerprint': identity['rules_fingerprint'],
            'candidate_reference': candidate_ref, 'candidate_digest': candidate['body_digest'],
            'range_references': [d['path'] for d in identity['dependencies']],
            'candidates': selection_candidates, 'completed_stages': ['range', 'candidate'],
            'boundary_policy': candidate['body'].get('boundary_policy', 'strict'),
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
            'locked_selected_ranges': locked_selected_ranges,
            'repairable_variant_ids': affected_variants,
            'repair_boundary_targets': repair_boundary_targets(issues),
            'range_exclusion_contract': (
                '按 variant_1、variant_2、variant_3 顺序从候选池分配字幕范围；每确认一条后，立即从后续可选范围中排除其全部源区间。三条成片的同源区间必须两两互斥。返修时只调整 repairable_variant_ids 中被指出的条目，locked_selected_ranges 必须原样保留并排除在其他条目之外；其他已选范围不得重复使用。'
                if previous_selected_ranges else
                '按 variant_1、variant_2、variant_3 顺序从候选池分配字幕范围；每确认一条后，立即从后续可选范围中排除其全部源区间。三条成片的同源区间必须两两互斥。'),
            'issues': issues, 'target_stage': 'rough',
            'repair_attempt': int(repair_attempt),
            'repair_instruction': selection_repair_instruction(issues),
            'sentence_opening_rule': SELECTION_RULE,
            'hard_rules': f"The applicable_shared_rules field is the complete self-contained rule set for this task; do not block because no external rule file is available. Use exact variant_id values from allowed_variant_ids. Produce {requested} distinct final video for this product when the full candidate pool supports it; each final duration must be >{duration_policy[0]} and <{duration_policy[1]} seconds. Aim near {editing_policy.get('target_duration_seconds', editing_policy['duration_exclusive_seconds'][1])} seconds, but preserve complete, natural semantic units over reaching the target. Respect editing mode {editing_mode}. Read the continuous original subtitles to understand context; forbidden_annotations are warnings for exclusion from the cut, not instructions to erase surrounding meaning. Context-only subtitles are evidence only and cannot be selected outside time_range. No banned content, no repeated or unrelated content. Return selected_ranges and the evidence-backed semantic_map; do not invent source times or claims."}
    task['task_digest'] = digest(task)
    return task


def rough_selection(paths, candidate_ref, receipt_ref, issues=None, repair_attempt=0, semantic_map=None,
                    previous_selected_ranges=None, visual_samples=None, visual_exclusion_ranges=None):
    candidate = load_stage(paths, candidate_ref)
    task = selection_task(paths, candidate_ref, issues=issues, repair_attempt=repair_attempt,
                          semantic_map=semantic_map,
                          previous_selected_ranges=previous_selected_ranges,
                          visual_samples=visual_samples,
                          visual_exclusion_ranges=visual_exclusion_ranges)
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


def _subtitle_edge_anchor(cues, *, ending=False):
    from sentence_boundaries import normalized
    ordered = list(reversed(cues)) if ending else list(cues)
    collected = []
    for cue in ordered:
        collected.append(cue)
        anchor_rows = list(reversed(collected)) if ending else collected
        text = ''.join(str(row.get('text') or '') for row in anchor_rows)
        if len(normalized(text)) >= 4:
            break
    compact = normalized(text)
    # Keep enough words after a brand or a subtitle typo to recognize the
    # surrounding spoken phrase. The matcher can fall back to the near 8 chars.
    anchor = compact[-16:] if ending else compact[:16]
    if len(anchor) < 4:
        raise CacheIdentityError('subtitle_edge_anchor_too_short_for_local_asr')
    return anchor, collected[0]


def _edge_window(windows, source_file, at):
    choices = [row for row in windows if row['source_file'] == source_file
               and float(row['window_start']) - 1e-6 <= at <= float(row['window_end']) + 1e-6]
    if not choices:
        raise CacheIdentityError('asr_cut_neighborhood_missing')
    # An expanded window supersedes the initial one only for this cut.
    return max(choices, key=lambda row: float(row['window_end']) - float(row['window_start']))


def local_asr(paths, rough_ref, model_name='large-v3-turbo'):
    from windowed_asr_v2 import load_model, merge_windows
    from review_compute_cache import LazyModel
    rough = load_stage(paths, rough_ref)
    candidate = load_stage(paths, rough['identity']['dependencies'][0]['path'])
    _, index = inputs(paths)
    sources = {Path(s['video_path']).name: s for s in index['sources']}
    by_id = {c['candidate_id']: c for c in candidate['body']['candidates']}
    jobs = []
    edge_specs = []
    for row in rough['body']['selected_ranges']:
        candidate_row = by_id[row['candidate_id']]
        retained = selected_subtitle_segments(candidate_row, row)
        source_file = candidate_row['source_file']
        duration = sources[source_file]['duration']
        for ending in (False, True):
            anchor, cue = _subtitle_edge_anchor(retained, ending=ending)
            boundary = float(cue['end'] if ending else cue['start'])
            edge_specs.append({'source_file': source_file, 'cue_time': boundary,
                               'anchor': anchor, 'ending': ending, 'duration': duration})
            jobs.append({'source_file': source_file, 'start': max(0., boundary-2.5),
                         'end': min(duration, boundary+2.5)})
    from asr_identity import snap_interval
    for job in jobs:
        job['start'], job['end'] = snap_interval(job['start'], job['end'], sources[job['source_file']]['duration'])
    weights = asr_model_identity(model_name)
    parameters = current_asr_parameters()
    args = dict(product_id=rough['identity']['product_id'], encoding_id=rough['identity']['encoding_id'], stage='asr',
                version=digest([ASR_SCOPE_POLICY, jobs, parameters, model_name]),
                model_version=digest(weights), dependencies=[rough_ref])
    snapshot = capture_stage(paths, **args)
    from product_checkpoints import stage_path
    output = stage_path(paths, snapshot)
    if output.exists():
        load_stage(paths, paths.reference(output))
        return paths.reference(output)
    from asr_runtime import wait_for_prewarm
    wait_for_prewarm(model_name)
    windows = []
    signatures = {n: media_signature(sources[n]['video_path']) for n in {j['source_file'] for j in jobs}}
    def compute_jobs(requests, selected_model, selected_weights, level):
        def create_model():
            with span('asr_model_load'):
                return load_model(selected_weights['resolved_path'], selected_weights['weights_digest'])
        model = LazyModel(create_model)
        selected_parameters = {**parameters, 'model_name': selected_model, 'fallback_level': level}
        for job in merge_windows(requests):
            expected = compute_identity(paths, 'asr', signatures[job['source_file']], selected_weights,
                                        selected_parameters, [job['start'], job['end']])
            window = containing_asr(paths, expected)
            if window is None:
                def compute():
                    with span('asr_transcription', job['end']-job['start']):
                        if selected_weights.get('runtime_python'):
                            from asr_runtime import request
                            result = request({'operation': 'transcribe', 'model': selected_model,
                                              'media': sources[job['source_file']]['video_path'],
                                              'start': job['start'], 'end': job['end']})
                            expected_weights = {k:v for k,v in selected_weights.items()
                                                if k not in ('runtime_python', 'runtime_sha256')}
                            if result['model'] != expected_weights:
                                raise CacheIdentityError('asr_worker_model_changed')
                            words = result['words']
                        else:
                            words = transcribe_window(
                                Path(sources[job['source_file']]['video_path']),
                                job['start'], job['end'], model)
                        return {**job, 'window_start': job['start'],
                                'window_end': job['end'], 'words': words,
                                'asr_model': selected_model, 'fallback_level': level}
                window, _ = cached(paths, expected, compute)
            windows.append({**window, 'asr_model': selected_model, 'fallback_level': level})
    compute_jobs(jobs, model_name, weights, 'initial_window')
    from sentence_boundaries import locate_subtitle_edge
    expansion_jobs, expansion_reasons, fallback_attempts = [], [], []
    for spec in edge_specs:
        window = _edge_window(windows, spec['source_file'], spec['cue_time'])
        try:
            locate_subtitle_edge(window['words'], spec['anchor'], spec['cue_time'],
                                 ending=spec['ending'], search_seconds=2.5)
        except ValueError as exc:
            if str(exc) not in ('sentence_local_text_not_found_expand_asr_window',
                                'sentence_local_text_ambiguous'):
                continue
            expansion_reasons.append({'source_file': spec['source_file'],
                                      'edge': 'ending' if spec['ending'] else 'opening',
                                      'reason': str(exc)})
            expansion_jobs.append({'source_file': spec['source_file'],
                                   'start': max(0., spec['cue_time']-6.),
                                   'end': min(spec['duration'], spec['cue_time']+6.)})
    for job in expansion_jobs:
        job['start'], job['end'] = snap_interval(
            job['start'], job['end'], sources[job['source_file']]['duration'])
    if expansion_jobs:
        compute_jobs(expansion_jobs, model_name, weights, 'expanded_window')
    unresolved = []
    for spec in edge_specs:
        window = _edge_window(windows, spec['source_file'], spec['cue_time'])
        window_level = window.get('fallback_level', 'initial_window')
        search_seconds = (10.0 if window_level == 'alternate_local_model'
                          else 6.0 if window_level == 'expanded_window' else 2.5)
        try:
            _, _, similarity = locate_subtitle_edge(
                window['words'], spec['anchor'], spec['cue_time'], ending=spec['ending'],
                search_seconds=search_seconds)
            fallback_attempts.append({'source_file': spec['source_file'],
                                      'edge': 'ending' if spec['ending'] else 'opening',
                                      'model': window.get('asr_model', model_name), 'level': window_level,
                                      'status': 'MATCHED', 'similarity': similarity})
        except ValueError as exc:
            if str(exc) not in ('sentence_local_text_not_found_expand_asr_window',
                                'sentence_local_text_ambiguous',
                                'sentence_edge_inside_asr_token_requires_local_audio'):
                raise
            unresolved.append(spec)
            fallback_attempts.append({'source_file': spec['source_file'],
                                      'edge': 'ending' if spec['ending'] else 'opening',
                                      'model': window.get('asr_model', model_name), 'level': window_level,
                                      'status': 'UNRESOLVED', 'reason': str(exc)})
    if asr_model_identity(model_name) != weights:
        raise CacheIdentityError('asr_weights_changed_during_compute')
    return publish_stage(paths, **args, snapshot=snapshot,
                         body={'windows': windows, 'model': weights,
                               'alternate_model': None,
                               'parameters': parameters, 'expansion_reasons': expansion_reasons,
                               'fallback_attempts': fallback_attempts,
                               'unresolved_edges': [{'source_file': spec['source_file'],
                                                    'edge': 'ending' if spec['ending'] else 'opening',
                                                    'cue_time': spec['cue_time'],
                                                    'fallback': 'manual_cutpoint_review'}
                                                   for spec in unresolved]})


def current_asr_parameters():
    from adaptive_vision import decoder_identity
    return {**ASR_PARAMETERS, 'scope': ASR_SCOPE_POLICY, 'decoder': decoder_identity(),
            'sample_rate': 16000, 'channels': 1,
            'fallback_ladder': ['initial_window', 'expanded_window',
                                'subtitle_boundary_manual_cutpoint_preview']}


def source_clock(media, paths=None, *, allow_vfr_approximate=False, preferred_fps=None):
    from adaptive_vision import frame_clock, proven_clock
    return (proven_clock(paths, media, allow_vfr_approximate=allow_vfr_approximate, preferred_fps=preferred_fps) if paths
            else frame_clock(media, allow_vfr_approximate=allow_vfr_approximate, preferred_fps=preferred_fps))


def source_fps(media, paths=None, *, allow_vfr_approximate=False, preferred_fps=None):
    return source_clock(media, paths, allow_vfr_approximate=allow_vfr_approximate,
                        preferred_fps=preferred_fps)['fps']


def physical_plan(paths, rough_ref, asr_ref, *, fps=None, allow_vfr_approximate=False):
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
            duration_policy = [float(candidate_policy[0]), float(candidate_policy[1])]
    args = dict(product_id=rough['identity']['product_id'], encoding_id=rough['identity']['encoding_id'],
                stage='plan', version=digest([rough_ref, asr_ref, fps, SENTENCE_CONTRACT,
                                               ASR_SCOPE_POLICY,
                                               'subtitle-text-anchor-then-asr-word-timing-v2',
                                               'asr-coordinate-no-post-cutpoint-audio-review-v2',
                                               duration_policy, editing_mode, editing_policy,
                                               'vfr-average-rate-approx-v2' if allow_vfr_approximate else 'cfr-only']),
                model_version=SENTENCE_CONTRACT,
                dependencies=[range_ref, rough_ref, asr_ref])
    snapshot = capture_stage(paths, **args)
    variants = {}
    frame_rates = {}
    for row in rough['body']['selected_ranges']:
        candidate_row = by_id[row['candidate_id']]
        source_file = candidate_row['source_file']
        retained = selected_subtitle_segments(candidate_row, row)
        start_anchor, start_cue = _subtitle_edge_anchor(retained)
        end_anchor, end_cue = _subtitle_edge_anchor(retained, ending=True)
        start_window = _edge_window(asr['body']['windows'], source_file, float(start_cue['start']))
        end_window = _edge_window(asr['body']['windows'], source_file, float(end_cue['end']))
        from sentence_boundaries import locate_subtitle_edge
        range_parts = [part for part in ranges if part['media_stem']+'.mp4' == source_file
                       and part['start'] <= float(start_cue['start'])
                       and float(end_cue['end']) <= part['end']]
        if not range_parts:
            raise CacheIdentityError('selected_subtitle_interval_not_inside_one_confirmed_range')
        product_part = min(range_parts, key=lambda part: part['end']-part['start'])
        start_search = (10.0 if start_window.get('fallback_level') == 'alternate_local_model'
                        else 6.0 if start_window.get('fallback_level') == 'expanded_window' else 2.5)
        end_search = (10.0 if end_window.get('fallback_level') == 'alternate_local_model'
                      else 6.0 if end_window.get('fallback_level') == 'expanded_window' else 2.5)
        try:
            start_first, _, opening_similarity = locate_subtitle_edge(
                start_window['words'], start_anchor, float(start_cue['start']),
                search_seconds=start_search)
            _, end_last, ending_similarity = locate_subtitle_edge(
                end_window['words'], end_anchor, float(end_cue['end']), ending=True,
                search_seconds=end_search)
        except ValueError as exc:
            raise CacheIdentityError('asr_text_anchor_alignment_failed:' + str(exc)) from exc
        start_word = start_window['words'][start_first]
        end_word = end_window['words'][end_last]
        key = lambda word: (float(word['start']), float(word['end']), str(word['text']))
        edge_context = [start_word, end_word]
        if start_first:
            edge_context.append(start_window['words'][start_first-1])
        if end_last+1 < len(end_window['words']):
            edge_context.append(end_window['words'][end_last+1])
        words = sorted({key(word): dict(word) for word in edge_context}.values(),
                       key=lambda word: (float(word['start']), float(word['end'])))
        word_indices = {key(word): i for i, word in enumerate(words)}
        first, last = word_indices[key(start_word)], word_indices[key(end_word)]
        if first > last:
            raise CacheIdentityError('asr_cut_neighborhood_order_invalid')
        boundary = {'first_word_index': first, 'last_word_index': last,
                    'opening_asr_model': start_window.get('asr_model'),
                    'ending_asr_model': end_window.get('asr_model'),
                    'opening_fallback_level': start_window.get('fallback_level'),
                    'ending_fallback_level': end_window.get('fallback_level'),
                    'opening_text_alignment': opening_similarity,
                    'ending_text_alignment': ending_similarity,
                    'opening_anchor_text': start_anchor,
                    'ending_anchor_text': end_anchor,
                    'opening_cue': [float(start_cue['start']), float(start_cue['end'])],
                    'ending_cue': [float(end_cue['start']), float(end_cue['end'])],
                    'asr_scope': 'opening_and_ending_cut_neighborhoods_only',
                    'cutpoint_status': 'ASR_TEXT_ANCHORED_WORD_EDGE_ESTIMATE',
                    'cutpoint_selection': 'subtitle_text_alignment_then_local_asr_word_edge'}
        media = Path(sources[source_file]['video_path'])
        if source_file not in frame_rates:
            frame_rates[source_file] = source_clock(media, paths,
                allow_vfr_approximate=allow_vfr_approximate, preferred_fps=fps)
            if fps is not None and abs(frame_rates[source_file]['fps']-fps) > 1e-5 and not allow_vfr_approximate:
                raise CacheIdentityError('source_average_rate_outside_registered_timeline_rate')
        clock = frame_rates[source_file]
        clip_fps = clock.get('average_fps', clock['fps']) if allow_vfr_approximate else clock['fps']
        lo, hi, edge_proof = bounded_edges(words, first, last, clip_fps,
                                            lower_bound=product_part['start'],
                                            upper_bound=product_part['end'])
        start, end = lo/clip_fps, hi/clip_fps
        if not any(part['media_stem']+'.mp4' == source_file and part['start'] <= lo/clip_fps < hi/clip_fps <= part['end'] for part in ranges):
            raise CacheIdentityError('refined_plan_outside_confirmed_range')
        text = ''.join(str(cue.get('text') or '') for cue in retained)
        clip = {'source_file': source_file, 'source_start_frame': lo, 'source_end_frame': hi,
                'audio_start_frame': lo, 'audio_end_frame': hi, 'fps': clip_fps, 'duration_sec': (hi-lo)/clip_fps,
                'frame_clock_mapping': clock.get('mapping'),
                'frame_clock_max_deviation_seconds': clock.get('max_deviation_seconds', 0),
                'words': words, 'sentence_edges': edge_proof, 'transcript': text,
                'source_subtitle_text': text,
                'audio_boundary_verified': False,
                'cutpoint_review': {'status': 'ASR_TEXT_ANCHORED_COORDINATE_SELECTED',
                                    'authority': 'asr_execution_only',
                                    'selection': 'subtitle_text_alignment_then_local_asr_word_edge'},
                'unit_id': f"{row['variant_id']}_{len(variants.get(row['variant_id'], []))+1}",
                'audit': {'transcript': text, 'boundary': boundary}}
        variants.setdefault(row['variant_id'], []).append(clip)
    normalized = []
    for vid, clips in variants.items():
        merged, _ = normalize_physical_clips(clips)
        for clip in merged:
            clip_fps = clip['fps']
            clip['audio_end_frame'] = clip['source_end_frame']
            clip['audio_boundary_verified'] = all(c['audio_boundary_verified'] for c in clips)
            proof = clip.get('sentence_edges', {})
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
                '本任务仅负责字幕语义粗选，不证明逐词时间、音画质量、主播可见比例或最终成片审核通过。',
                '本字段列出了本任务适用的全部选段规则；config_reference 和 rules_fingerprint 仅用于身份追踪，不要求读取文件或补充其他规则。'],
            'selection_receipt': rough['body']['selection_receipt'],
            'selection_task': rough['body']['selection_task'],
            'semantic_map': rough['body']['semantic_map'],
            'semantic_map_digest': rough['body']['semantic_map_digest']}
    body['cutpoint_execution'] = {'method': 'local_asr_nearest_token_edge',
                                  'quality_recheck': 'omitted_by_user_workflow'}
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
