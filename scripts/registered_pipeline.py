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

ASR_SCOPE_POLICY = 'subtitle-text-nearby-edges-v3-phonetic-tolerant'


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


def publish_semantic_ranges(paths, manifest):
    """Publish semantic-inferred ranges and any explicitly human-confirmed ranges."""
    titles, index = inputs(paths)
    state = paths.load_state()
    identity = expected_identity(paths, include_ranges=False)
    project_key = paths.registered_scope()[0]
    batch_id = state['batch']['batch_id']
    batch_ids = set(state['batch']['product_ids'])
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
        validate_semantic_inferred_document(
            {'range_authority': 'user_directed_semantic_inference', 'products': [row]},
            found, index, [record['identity']['product_id']],
            project_key=record['identity']['project_key'], batch_id=state['batch']['batch_id'])
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


def selection_task(paths, candidate_ref, issues=None, repair_attempt=0, semantic_map=None,
                   previous_selected_ranges=None):
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
            'locked_selected_ranges': locked_selected_ranges,
            'repairable_variant_ids': affected_variants,
            'repair_boundary_targets': repair_boundary_targets(issues),
            'range_exclusion_contract': (
                '按 variant_1、variant_2、variant_3 顺序从候选池分配字幕范围；每确认一条后，立即从后续可选范围中排除其全部源区间。三条成片的同源区间必须两两互斥。返修时只调整 repairable_variant_ids 中被指出的条目，locked_selected_ranges 必须原样保留并排除在其他条目之外；其他已选范围不得重复使用。'
                if previous_selected_ranges else
                '按 variant_1、variant_2、variant_3 顺序从候选池分配字幕范围；每确认一条后，立即从后续可选范围中排除其全部源区间。三条成片的同源区间必须两两互斥。'),
            'issues': issues, 'target_stage': 'rough',
            'repair_attempt': int(repair_attempt),
            'repair_instruction': ('逐条落实 issues 中审核给出的源时间点与修复方向；repair_boundary_targets 是必须落实的边界约束。只修改对应 variant 的 selected_ranges，把 start/end 移到建议边界，切掉残留的多余字或下一句起头；若建议保留完整词/因果结果，则按建议恢复。其他 variant 保持 locked_selected_ranges 原样。不要只改说明文字，也不要原样复用该 variant 已失败的范围。修复后重查各段拼接的句意与承接。' if issues else ''),
            'sentence_opening_rule': SELECTION_RULE,
            'hard_rules': f"The applicable_shared_rules field is the complete self-contained rule set for this task; do not block because no external rule file is available. Use exact variant_id values from allowed_variant_ids. Produce {requested} distinct final video for this product when the full candidate pool supports it; each final duration must be >{duration_policy[0]} and <{duration_policy[1]} seconds. Aim near {editing_policy.get('target_duration_seconds', editing_policy['duration_exclusive_seconds'][1])} seconds, but preserve complete, natural semantic units over reaching the target. Respect editing mode {editing_mode}. Read the continuous original subtitles to understand context; forbidden_annotations are warnings for exclusion from the cut, not instructions to erase surrounding meaning. Context-only subtitles are evidence only and cannot be selected outside time_range. No banned content, no repeated or unrelated content. Return selected_ranges and the evidence-backed semantic_map; do not invent source times or claims."}
    task['task_digest'] = digest(task)
    return task


def rough_selection(paths, candidate_ref, receipt_ref, issues=None, repair_attempt=0, semantic_map=None,
                    previous_selected_ranges=None):
    candidate = load_stage(paths, candidate_ref)
    task = selection_task(paths, candidate_ref, issues=issues, repair_attempt=repair_attempt,
                          semantic_map=semantic_map,
                          previous_selected_ranges=previous_selected_ranges)
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
            segments, _ = model.get().transcribe(wav.name, language='zh', beam_size=5, vad_filter=True,
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
    from adaptive_vision import decoder_identity
    parameters = {**ASR_PARAMETERS, 'scope': ASR_SCOPE_POLICY, 'decoder': decoder_identity(),
                  'sample_rate': 16000, 'channels': 1}
    def create_model():
        with span('asr_model_load'):
            return load_model(weights['resolved_path'])
    model = LazyModel(create_model)
    args = dict(product_id=rough['identity']['product_id'], encoding_id=rough['identity']['encoding_id'], stage='asr',
                version=digest([ASR_SCOPE_POLICY, jobs, parameters]),
                model_version=digest(weights), dependencies=[rough_ref])
    snapshot = capture_stage(paths, **args)
    from product_checkpoints import stage_path
    output = stage_path(paths, snapshot)
    if output.exists():
        load_stage(paths, paths.reference(output))
        return paths.reference(output)
    windows = []
    signatures = {n: media_signature(sources[n]['video_path']) for n in {j['source_file'] for j in jobs}}
    def compute_jobs(requests):
        for job in merge_windows(requests):
            expected = compute_identity(paths, 'asr', signatures[job['source_file']], weights,
                                        parameters, [job['start'], job['end']])
            window = containing_asr(paths, expected)
            if window is None:
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
                            words = result['words']
                        else:
                            words = transcribe_window(
                                Path(sources[job['source_file']]['video_path']),
                                job['start'], job['end'], model)
                        return {**job, 'window_start': job['start'],
                                'window_end': job['end'], 'words': words}
                window, _ = cached(paths, expected, compute)
            windows.append(window)
    compute_jobs(jobs)
    from sentence_boundaries import locate_subtitle_edge
    expansion_jobs, expansion_reasons = [], []
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
        compute_jobs(expansion_jobs)
    if asr_model_identity(model_name) != weights:
        raise CacheIdentityError('asr_weights_changed_during_compute')
    return publish_stage(paths, **args, snapshot=snapshot,
                         body={'windows': windows, 'model': weights,
                               'parameters': parameters, 'expansion_reasons': expansion_reasons})


def current_asr_parameters():
    from adaptive_vision import decoder_identity
    return {**ASR_PARAMETERS, 'scope': ASR_SCOPE_POLICY, 'decoder': decoder_identity(),
            'sample_rate': 16000, 'channels': 1}


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
                                               'subtitle-text-nearby-token-edges-v3',
                                               'owned-gap-local-audio-v1',
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
        start_first, _, opening_similarity = locate_subtitle_edge(
            start_window['words'], start_anchor, float(start_cue['start']))
        _, end_last, ending_similarity = locate_subtitle_edge(
            end_window['words'], end_anchor, float(end_cue['end']), ending=True)
        start_word = start_window['words'][start_first]
        end_word = end_window['words'][end_last]
        key = lambda word: (float(word['start']), float(word['end']), str(word['text']))
        # Only the two selected words and their immediate outer neighbors are
        # needed for the edge proof. Different ASR windows may assign slightly
        # different timestamps to the same interior word; do not splice those
        # overlapping recognitions into a fake whole-clip transcript.
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
                    'opening_anchor_similarity': opening_similarity,
                    'ending_anchor_similarity': ending_similarity,
                    'opening_anchor_text': start_anchor, 'ending_anchor_text': end_anchor,
                    'opening_cue': [float(start_cue['start']), float(start_cue['end'])],
                    'ending_cue': [float(end_cue['start']), float(end_cue['end'])],
                    'asr_scope': 'opening_and_ending_cut_neighborhoods_only'}
        media = Path(sources[source_file]['video_path'])
        if source_file not in frame_rates:
            frame_rates[source_file] = source_clock(media, paths,
                allow_vfr_approximate=allow_vfr_approximate, preferred_fps=fps)
            if fps is not None and abs(frame_rates[source_file]['fps']-fps) > 1e-5 and not allow_vfr_approximate:
                raise CacheIdentityError('source_average_rate_outside_registered_timeline_rate')
        clock = frame_rates[source_file]
        clip_fps = clock.get('average_fps', clock['fps']) if allow_vfr_approximate else clock['fps']
        lo, hi, edge_proof = bounded_edges(words, first, last, clip_fps)
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
    from owned_audio_refinement import refine as refine_owned_audio
    body, audio_audit = refine_owned_audio(body, {name: row['video_path'] for name, row in sources.items()})
    body['audio_refinement'] = audio_audit
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


def vision_model_version(detector, *, allow_vfr_approximate=False, editing_policy=None):
    from adaptive_vision import classifier_identity, POLICY
    mapping_policy = 'vfr-average-rate-approx-v2' if allow_vfr_approximate else 'cfr-only'
    effective_policy = dict(POLICY)
    if editing_policy and 'closeup_duration_ratio_max_inclusive' in editing_policy:
        effective_policy['closeup_duration_ratio_max_inclusive'] = editing_policy['closeup_duration_ratio_max_inclusive']
    return digest([classifier_identity(detector), effective_policy, mapping_policy])
