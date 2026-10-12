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
from refine_plans_v2 import (refine_words, normalize_physical_clips, audit_audio_start,
                            refine_audio_end, media_duration)


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


def candidates(paths, range_ref):
    record = load_stage(paths, range_ref)
    row = record['body']
    from publish_confirmed_ranges import verify_confirmation
    if not verify_confirmation(row):
        raise CacheIdentityError('range_is_not_human_confirmed')
    titles, index = inputs(paths)
    found = [p for p in titles['products'] if p['product_index'] == record['identity']['product_id']]
    if len(found) != 1 or found[0]['encoding_id'] != record['identity']['encoding_id']:
        raise CacheIdentityError('range_title_identity_mismatch')
    args = dict(product_id=record['identity']['product_id'], encoding_id=record['identity']['encoding_id'],
                stage='candidate', version='wide-pool-v3', model_version='local-v3', dependencies=[range_ref])
    snapshot = capture_stage(paths, **args)
    pool, removed = [], []
    for part in row['ranges']:
        if any(c['source_stem'] == part['media_stem'] and c['end'] > part['start'] and c['start'] < part['end']
               and not part['start'] <= c['start'] < c['end'] <= part['end'] for c in index['cues']):
            raise CacheIdentityError('subtitle_crosses_confirmed_boundary_needs_local_alignment')
        cues = [c for c in index['cues'] if c['source_stem'] == part['media_stem']
                and c['start'] >= part['start'] and c['end'] <= part['end']]
        chunks, rejected = build_wide_candidates(cues, part['media_stem'], found[0]['title'])
        pool.extend(chunks); removed.extend(rejected)
    body = {'candidates': pool, 'removed_count': len(removed), 'complete_range_scanned': True}
    return publish_stage(paths, **args, body=body, snapshot=snapshot)


def selection_task(paths, candidate_ref):
    candidate = load_stage(paths, candidate_ref)
    identity = candidate['identity']
    task = {'role': 'selection', 'schema_version': 1, 'project_key': identity['project_key'],
            'config_reference': 'PROJECT_CONFIG.json', 'product_id': identity['product_id'],
            'encoding_id': identity['encoding_id'], 'rules_fingerprint': identity['rules_fingerprint'],
            'candidate_reference': candidate_ref, 'candidate_digest': candidate['body_digest'],
            'range_references': [d['path'] for d in identity['dependencies']],
            'candidates': candidate['body']['candidates'], 'completed_stages': ['range', 'candidate'],
            'issues': [], 'target_stage': 'rough',
            'hard_rules': 'Up to 3 distinct variants; target 48–56 seconds, final strictly >45 and <60; no banned content, no overlap or duplicate selling points. Return selected_ranges; do not invent source times.'}
    task['task_digest'] = digest(task)
    return task


def rough_selection(paths, candidate_ref, receipt_ref):
    candidate = load_stage(paths, candidate_ref)
    task = selection_task(paths, candidate_ref)
    response = verify_receipt(paths, receipt_ref, task)['result']
    if response['verdict'] != 'PASS' or not response.get('selected_ranges'):
        raise CacheIdentityError('selection_repair_or_blocked')
    by_id = {c['candidate_id']: c for c in candidate['body']['candidates']}
    for row in response['selected_ranges']:
        source = by_id.get(row['candidate_id'])
        if (not source or row['variant_id'] not in ('variant_1', 'variant_2', 'variant_3')
                or not source['time_range']['start'] <= row['start'] < row['end'] <= source['time_range']['end']):
            raise CacheIdentityError('selected_range_outside_candidate')
    body = {'selected_ranges': response['selected_ranges'], 'selection_receipt': receipt_ref, 'selection_task': task}
    return publish(paths, candidate['identity']['product_id'], candidate['identity']['encoding_id'], 'rough', body, [candidate_ref])


def transcribe_window(media, start, end, model):
    with tempfile.NamedTemporaryFile(suffix='.wav') as wav:
        subprocess.run(['/opt/homebrew/bin/ffmpeg', '-v', 'error', '-y', '-ss', str(start), '-i', str(media),
                        '-t', str(end-start), '-vn', '-ar', '16000', '-ac', '1', wav.name], check=True, capture_output=True)
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
    model = LazyModel(lambda: load_model(model_name))
    args = dict(product_id=rough['identity']['product_id'], encoding_id=rough['identity']['encoding_id'], stage='asr',
                version=digest(jobs), model_version=model_name, dependencies=[rough_ref])
    snapshot = capture_stage(paths, **args)
    from product_checkpoints import stage_path
    output = stage_path(paths, snapshot)
    if output.exists():
        load_stage(paths, paths.reference(output))
        return paths.reference(output)
    windows = []
    for job in merge_windows(jobs):
        words = transcribe_window(Path(sources[job['source_file']]['video_path']), job['start'], job['end'], model)
        windows.append({**job, 'window_start': job['start'], 'window_end': job['end'], 'words': words})
    return publish_stage(paths, **args, snapshot=snapshot, body={'windows': windows})


def source_fps(media):
    from fractions import Fraction
    result = subprocess.run(['/opt/homebrew/bin/ffprobe', '-v', 'error', '-select_streams', 'v:0',
                             '-show_entries', 'stream=r_frame_rate', '-of', 'default=noprint_wrappers=1:nokey=1',
                             str(media)], text=True, capture_output=True, check=True)
    value = float(Fraction(result.stdout.strip()))
    if not math.isfinite(value) or value <= 0:
        raise CacheIdentityError('source_fps_unverified')
    return value


def physical_plan(paths, rough_ref, asr_ref, *, fps=None):
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
    args = dict(product_id=rough['identity']['product_id'], encoding_id=rough['identity']['encoding_id'],
                stage='plan', version=digest([rough_ref, asr_ref, fps]), model_version='word-frame-audio-v3',
                dependencies=[range_ref, rough_ref, asr_ref])
    snapshot = capture_stage(paths, **args)
    variants = {}
    frame_rates = {}
    for row in rough['body']['selected_ranges']:
        source_file = by_id[row['candidate_id']]['source_file']
        window = containing_window(asr['body']['windows'], source_file, row['start'], row['end'])
        start, end, _, boundary = refine_words(window, row['start'], row['end'])
        media = Path(sources[source_file]['video_path'])
        frame_rates.setdefault(source_file, fps if fps is not None else source_fps(media))
        clip_fps = frame_rates[source_file]
        start, opening = audit_audio_start(media, start)
        end, ending = refine_audio_end(media, start, end, sources[source_file]['duration'])
        lo, hi = math.floor(start*clip_fps), math.ceil(end*clip_fps)
        if not any(part['media_stem']+'.mp4' == source_file and part['start'] <= lo/clip_fps < hi/clip_fps <= part['end'] for part in ranges):
            raise CacheIdentityError('refined_plan_outside_confirmed_range')
        words = window['words']
        text = ''.join(w['text'] for w in words if w['end'] > lo/clip_fps and w['start'] < hi/clip_fps)
        clip = {'source_file': source_file, 'source_start_frame': lo, 'source_end_frame': hi,
                'audio_start_frame': lo, 'audio_end_frame': hi, 'fps': clip_fps, 'duration_sec': (hi-lo)/clip_fps,
                'words': words, 'transcript': text, 'audio_boundary_verified': not opening['risk'] and not ending['risk'],
                'audit': {'transcript': text, 'boundary': boundary}}
        variants.setdefault(row['variant_id'], []).append(clip)
    normalized = []
    for vid, clips in variants.items():
        merged, _ = normalize_physical_clips(clips)
        for clip in merged:
            clip_fps = clip['fps']
            clip['audio_end_frame'] = clip['source_end_frame']
            clip['audio_boundary_verified'] = all(c['audio_boundary_verified'] for c in clips)
            clip['transcript'] = ''.join(w['text'] for w in clip['words'] if w['end'] > clip['source_start_frame']/clip_fps and w['start'] < clip['source_end_frame']/clip_fps)
        normalized.append({'variant_id': vid, 'clips': merged})
    body = {'variants': normalized, 'selection_receipt': rough['body']['selection_receipt'],
            'selection_task': rough['body']['selection_task']}
    return publish_stage(paths, **args, snapshot=snapshot, body=body)


def vision_audit(paths, plan_ref, detector):
    plan = load_stage(paths, plan_ref)
    from person_coverage_audit_v2 import registered_vision
    _, index = inputs(paths)
    sources = {Path(s['video_path']).name: s['video_path'] for s in index['sources']}
    args = dict(product_id=plan['identity']['product_id'], encoding_id=plan['identity']['encoding_id'], stage='vision',
                version=plan['body_digest'], model_version=_sha256(Path(detector)), dependencies=[plan_ref])
    snapshot = capture_stage(paths, **args)
    body = registered_vision(plan['body'], sources, detector)
    return publish_stage(paths, **args, snapshot=snapshot, body=body)
