"""Product adapters for authenticated ranges, candidates, local ASR and physical plans."""
import json
import math
from pathlib import Path
import subprocess
import tempfile
import time
from project_registry import digest, atomic_json, workspace_lock
from runtime_meta import CacheIdentityError, load_checked_json, _sha256
from product_checkpoints import load_stage, publish_stage, capture_stage, current_range_path
from codex_worker import verify_receipt
from build_expanded_codex_request import build_wide_candidates
from publish_confirmed_ranges import build_confirmed_ranges
from refine_plans_v2 import (refine_words, normalize_physical_clips, media_duration)
from sentence_boundaries import CONTRACT as SENTENCE_CONTRACT, SELECTION_RULE, bounded_edges
from compute_metrics import span
from compute_store import media_signature, identity as compute_identity, cached, containing_asr
from asr_identity import model_identity as asr_model_identity, PARAMETERS as ASR_PARAMETERS


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


def candidates(paths, range_ref, *, include_cross_boundary_cues=False):
    record = load_stage(paths, range_ref)
    row = record['body']
    from publish_confirmed_ranges import verify_confirmation
    if not verify_confirmation(row):
        raise CacheIdentityError('range_is_not_human_confirmed')
    titles, index = inputs(paths)
    found = [p for p in titles['products'] if p['product_index'] == record['identity']['product_id']]
    if len(found) != 1 or found[0]['encoding_id'] != record['identity']['encoding_id']:
        raise CacheIdentityError('range_title_identity_mismatch')
    version = ('wide-pool-v4-include-cross-boundary-cues' if include_cross_boundary_cues
               else 'wide-pool-v3')
    args = dict(product_id=record['identity']['product_id'], encoding_id=record['identity']['encoding_id'],
                stage='candidate', version=version, model_version='local-v3', dependencies=[range_ref])
    snapshot = capture_stage(paths, **args)
    pool, removed, included_cross_boundary = [], [], 0
    for part in row['ranges']:
        crossing = [c for c in index['cues'] if c['source_stem'] == part['media_stem']
                    and c['end'] > part['start'] and c['start'] < part['end']
                    and not part['start'] <= c['start'] < c['end'] <= part['end']]
        if crossing and not include_cross_boundary_cues:
            raise CacheIdentityError('subtitle_crosses_confirmed_boundary_needs_local_alignment')
        included_cross_boundary += len(crossing)
        cues = [c for c in index['cues'] if c['source_stem'] == part['media_stem']
                and c['end'] > part['start'] and c['start'] < part['end']
                and (include_cross_boundary_cues or
                     part['start'] <= c['start'] and c['end'] <= part['end'])]
        chunks, rejected = build_wide_candidates(cues, part['media_stem'], found[0]['title'])
        pool.extend(chunks); removed.extend(rejected)
    body = {'candidates': pool, 'removed_count': len(removed), 'complete_range_scanned': True,
            'boundary_policy': 'include_complete_intersecting_cues' if include_cross_boundary_cues else 'strict',
            'included_cross_boundary_cue_count': included_cross_boundary}
    return publish_stage(paths, **args, body=body, snapshot=snapshot)


def selection_task(paths, candidate_ref, issues=None, repair_attempt=0):
    candidate = load_stage(paths, candidate_ref)
    identity = candidate['identity']
    state = json.loads(paths.state_path.read_text(encoding='utf-8'))
    from project_editing_modes import resolve_editing_mode
    editing_mode, editing_policy = resolve_editing_mode(state.get('resolve', {}).get('project_name'))
    duration_policy = [float(value) for value in editing_policy['duration_exclusive_seconds']]
    override_path = paths.workspace_root / 'delivery' / 'USER_OVERRIDE.json'
    requested = editing_policy['target_variant_count']
    if override_path.is_file():
        override = json.loads(override_path.read_text(encoding='utf-8'))
        if override.get('scope') == 'this_batch_only' and override.get('confirmed_by_user') is True:
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
    task = {'role': 'selection', 'schema_version': 1, 'project_key': identity['project_key'],
            'config_reference': 'PROJECT_CONFIG.json', 'product_id': identity['product_id'],
            'encoding_id': identity['encoding_id'], 'rules_fingerprint': identity['rules_fingerprint'],
            'candidate_reference': candidate_ref, 'candidate_digest': candidate['body_digest'],
            'range_references': [d['path'] for d in identity['dependencies']],
            'candidates': candidate['body']['candidates'], 'completed_stages': ['range', 'candidate'],
            'boundary_policy': candidate['body'].get('boundary_policy', 'strict'),
            'included_cross_boundary_cue_count': candidate['body'].get('included_cross_boundary_cue_count', 0),
            'editing_mode': editing_mode, 'editing_policy': editing_policy,
            'duration_exclusive_seconds': duration_policy,
            'allowed_variant_ids': [f'variant_{index}' for index in range(1, requested+1)],
            'issues': issues, 'target_stage': 'rough',
            'repair_attempt': int(repair_attempt),
            'repair_instruction': ('重新选择候选内容，针对 issues 修复；不要复用已失败的范围。' if issues else ''),
            'sentence_opening_rule': SELECTION_RULE,
            'hard_rules': f"Use exact variant_id values from allowed_variant_ids. Produce {requested} distinct final video for this product when the full candidate pool supports it; each final duration must be >{duration_policy[0]} and <{duration_policy[1]} seconds. Respect editing mode {editing_mode}. No banned content, no repeated or unrelated content. Return selected_ranges; do not invent source times."}
    task['task_digest'] = digest(task)
    return task


def rough_selection(paths, candidate_ref, receipt_ref, issues=None, repair_attempt=0):
    candidate = load_stage(paths, candidate_ref)
    task = selection_task(paths, candidate_ref, issues=issues, repair_attempt=repair_attempt)
    response = verify_receipt(paths, receipt_ref, task)['result']
    if response['verdict'] != 'PASS' or not response.get('selected_ranges'):
        raise CacheIdentityError('selection_repair_or_blocked')
    by_id = {c['candidate_id']: c for c in candidate['body']['candidates']}
    aliases = {'v1': 'variant_1', 'v2': 'variant_2', 'v3': 'variant_3'}
    selected = []
    allowed = set(task['allowed_variant_ids'])
    for row in response['selected_ranges']:
        source = by_id.get(row['candidate_id'])
        variant_id = aliases.get(row['variant_id'], row['variant_id'])
        if (not source or variant_id not in allowed
                or not source['time_range']['start'] <= row['start'] < row['end'] <= source['time_range']['end']):
            raise CacheIdentityError('selected_range_outside_candidate')
        selected.append({**row, 'variant_id': variant_id})
    body = {'selected_ranges': selected, 'selection_receipt': receipt_ref, 'selection_task': task}
    return publish(paths, candidate['identity']['product_id'], candidate['identity']['encoding_id'], 'rough', body, [candidate_ref])


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


def local_asr(paths, rough_ref, model_name='large-v3-turbo'):
    from windowed_asr_v2 import load_model, merge_windows
    from review_compute_cache import LazyModel
    rough = load_stage(paths, rough_ref)
    candidate = load_stage(paths, rough['identity']['dependencies'][0]['path'])
    _, index = inputs(paths)
    sources = {Path(s['video_path']).name: s for s in index['sources']}
    by_id = {c['candidate_id']: c for c in candidate['body']['candidates']}
    jobs = [{'source_file': by_id[r['candidate_id']]['source_file'], 'start': max(0, r['start']-2),
             'end': min(sources[by_id[r['candidate_id']]['source_file']]['duration'], r['end']+3)}
            for r in rough['body']['selected_ranges']]
    from asr_identity import snap_interval
    for job in jobs:
        job['start'], job['end'] = snap_interval(job['start'], job['end'], sources[job['source_file']]['duration'])
    weights = asr_model_identity(model_name)
    from adaptive_vision import decoder_identity
    parameters = {**ASR_PARAMETERS, 'decoder': decoder_identity(),
                  'sample_rate': 16000, 'channels': 1}
    def create_model():
        with span('asr_model_load'):
            return load_model(weights['resolved_path'])
    model = LazyModel(create_model)
    args = dict(product_id=rough['identity']['product_id'], encoding_id=rough['identity']['encoding_id'], stage='asr',
                version=digest([jobs, parameters]), model_version=digest(weights), dependencies=[rough_ref])
    snapshot = capture_stage(paths, **args)
    from product_checkpoints import stage_path
    output = stage_path(paths, snapshot)
    if output.exists():
        load_stage(paths, paths.reference(output))
        return paths.reference(output)
    windows = []
    signatures = {n: media_signature(sources[n]['video_path']) for n in {j['source_file'] for j in jobs}}
    for job in merge_windows(jobs):
        expected = compute_identity(paths, 'asr', signatures[job['source_file']], weights, parameters, [job['start'], job['end']])
        window = containing_asr(paths, expected)
        if window is None:
            def compute():
                with span('asr_transcription', job['end']-job['start']):
                    if weights.get('runtime_python'):
                        from asr_runtime import request
                        result = request({'operation': 'transcribe', 'model': model_name,
                                          'media': sources[job['source_file']]['video_path'], 'start': job['start'], 'end': job['end']})
                        expected_weights = {k:v for k,v in weights.items() if k not in ('runtime_python','runtime_sha256')}
                        if result['model'] != expected_weights:
                            raise CacheIdentityError('asr_worker_model_changed')
                        words = result['words']
                    else:
                        words = transcribe_window(Path(sources[job['source_file']]['video_path']), job['start'], job['end'], model)
                    return {**job, 'window_start': job['start'], 'window_end': job['end'], 'words': words}
            window, _ = cached(paths, expected, compute)
        windows.append(window)
    if asr_model_identity(model_name) != weights:
        raise CacheIdentityError('asr_weights_changed_during_compute')
    return publish_stage(paths, **args, snapshot=snapshot, body={'windows': windows, 'model': weights, 'parameters': parameters})


def current_asr_parameters():
    from adaptive_vision import decoder_identity
    return {**ASR_PARAMETERS, 'decoder': decoder_identity(), 'sample_rate': 16000, 'channels': 1}


def source_clock(media, paths=None, *, allow_vfr_approximate=False, preferred_fps=None):
    from adaptive_vision import frame_clock, proven_clock
    return (proven_clock(paths, media, allow_vfr_approximate=allow_vfr_approximate, preferred_fps=preferred_fps) if paths
            else frame_clock(media, allow_vfr_approximate=allow_vfr_approximate, preferred_fps=preferred_fps))


def source_fps(media, paths=None, *, allow_vfr_approximate=False, preferred_fps=None):
    return source_clock(media, paths, allow_vfr_approximate=allow_vfr_approximate,
                        preferred_fps=preferred_fps)['fps']


def physical_plan(paths, rough_ref, asr_ref, *, fps=None, allow_vfr_approximate=False):
    from refine_plans_v2 import containing_window
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
    state = json.loads(paths.state_path.read_text(encoding='utf-8'))
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
                                               'owned-gap-local-audio-v1',
                                               duration_policy, editing_mode, editing_policy,
                                               'vfr-average-rate-approx-v2' if allow_vfr_approximate else 'cfr-only']),
                model_version=SENTENCE_CONTRACT,
                dependencies=[range_ref, rough_ref, asr_ref])
    snapshot = capture_stage(paths, **args)
    variants = {}
    frame_rates = {}
    for row in rough['body']['selected_ranges']:
        source_file = by_id[row['candidate_id']]['source_file']
        window = containing_window(asr['body']['windows'], source_file, row['start'], row['end'])
        start, end, _, boundary = refine_words(window, row['start'], row['end'])
        media = Path(sources[source_file]['video_path'])
        if source_file not in frame_rates:
            frame_rates[source_file] = source_clock(media, paths,
                allow_vfr_approximate=allow_vfr_approximate, preferred_fps=fps)
            if fps is not None and abs(frame_rates[source_file]['fps']-fps) > 1e-5 and not allow_vfr_approximate:
                raise CacheIdentityError('source_average_rate_outside_registered_timeline_rate')
        clock = frame_rates[source_file]
        clip_fps = clock.get('average_fps', clock['fps']) if allow_vfr_approximate else clock['fps']
        lo, hi, edge_proof = bounded_edges(window['words'], boundary['first_word_index'], boundary['last_word_index'], clip_fps)
        start, end = lo/clip_fps, hi/clip_fps
        if not any(part['media_stem']+'.mp4' == source_file and part['start'] <= lo/clip_fps < hi/clip_fps <= part['end'] for part in ranges):
            raise CacheIdentityError('refined_plan_outside_confirmed_range')
        words = window['words']
        text = ''.join(w['text'] for w in words[boundary['first_word_index']:boundary['last_word_index']+1])
        clip = {'source_file': source_file, 'source_start_frame': lo, 'source_end_frame': hi,
                'audio_start_frame': lo, 'audio_end_frame': hi, 'fps': clip_fps, 'duration_sec': (hi-lo)/clip_fps,
                'frame_clock_mapping': clock.get('mapping'),
                'frame_clock_max_deviation_seconds': clock.get('max_deviation_seconds', 0),
                'words': words, 'sentence_edges': edge_proof, 'transcript': text,
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
            clip['transcript'] = ''.join(w['text'] for w in clip['words'][proof.get('first_word_index',0):proof.get('last_word_index',len(clip['words'])-1)+1])
        normalized.append({'variant_id': vid, 'clips': merged})
    body = {'variants': normalized, 'duration_exclusive_seconds': duration_policy,
            'editing_mode': editing_mode, 'editing_policy': editing_policy,
            'selection_receipt': rough['body']['selection_receipt'],
            'selection_task': rough['body']['selection_task']}
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
