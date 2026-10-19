"""Deterministic source-frame sampling with versioned record-time scoring."""
import ast
import hashlib
import hmac
import json
import math
import platform
from pathlib import Path
import subprocess
import tempfile
from project_registry import digest
from runtime_meta import CacheIdentityError, _sha256
from compute_store import media_signature, identity, cached, check_media
from compute_metrics import span, count
from vision_record_score_v5 import (CONTRACT, POLICY, ABSENT_LABELS, estimate,
                                    expected_refinement_times, initial_record_times,
                                    map_record_time)

FRAME_CACHE_PROTOCOL = 'person-presence-closeup-strata-v5'
# Legacy v4 adapter digest is retained only for reproducing old evidence identities.
_V4_ADAPTER_SHA256 = '464bb0bae7b7cbc46bd996174f0d747b7d3c21e7e4deabb84f6713d292234c5d'
_V4_CLASSIFIER_AST_SHA256 = 'e61b0e6bf0972a660516580b94f94708c89ce0eada81adcc21e938fd20ddb646'
_CLASSIFIER_FUNCTIONS = {'decoder_identity', 'subject_thresholds', 'classify_observations',
                         'classify_frame', 'validate_classification'}
LABELS = {'HOST_VISIBLE', 'PERSON_VISIBLE', 'PERSON_CLOSEUP', 'CLOTHING_CLOSEUP', 'OTHER_PERSON', 'NO_PERSON', 'UNCERTAIN', 'DETECTOR_ERROR'}
_decoders = {}


def classifier_adapter_digest():
    source = Path(__file__).read_text(encoding='utf-8')
    nodes = [ast.dump(node, include_attributes=False) for node in ast.parse(source).body
             if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name in _CLASSIFIER_FUNCTIONS)
             or (isinstance(node, ast.Assign) and any(
                 isinstance(target, ast.Name) and target.id == 'LABELS' for target in node.targets))]
    current = hashlib.sha256('\n'.join(nodes).encode()).hexdigest()
    return _V4_ADAPTER_SHA256 if current == _V4_CLASSIFIER_AST_SHA256 else current


def decoder_identity():
    path = Path('/opt/homebrew/bin/ffmpeg')
    if not path.is_file():
        return {'ffmpeg': 'UNAVAILABLE'}
    st = path.stat()
    signature = (st.st_ino, st.st_size, st.st_mtime_ns)
    if signature not in _decoders:
        value = _sha256(path)
        after = path.stat()
        if signature != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise CacheIdentityError('decoder_changed_during_identity')
        _decoders[signature] = {'ffmpeg': value}
    return _decoders[signature]


def frame_clock(media):
    """Prove CFR from every decoded frame timestamp, never r_frame_rate alone. VFR blocks."""
    with span('frame_clock'):
        result = subprocess.run(['/opt/homebrew/bin/ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_frames', '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', str(media)],
            text=True, capture_output=True, check=True)
        times = [float(f['best_effort_timestamp_time']) for f in json.loads(result.stdout)['frames']]
        if len(times) < 2 or not all(math.isfinite(t) for t in times):
            raise CacheIdentityError('source_frame_clock_unproven')
        delta = (times[-1]-times[0])/(len(times)-1)
        origin = times[0]
        if delta <= 0 or any(abs(t-origin-i*delta) > 2e-6 for i, t in enumerate(times)):
            raise CacheIdentityError('vfr_exact_mapping_not_integrated')
        return {'fps': 1/delta, 'frame_count': len(times), 'pts_digest': digest(times),
                'video_first_pts': origin, 'mapping': 'decoded-cfr-with-origin-v4'}


def classifier_identity(detector):
    detector = Path(detector).resolve()
    if not detector.is_file():
        raise CacheIdentityError('host_detector_missing')
    return {'detector_path': str(detector), 'binary_sha256': _sha256(detector),
            'adapter_sha256': classifier_adapter_digest(),
            'decoder': decoder_identity(),
            'runtime': {'os': platform.platform(), 'macos': platform.mac_ver()[0]},
            'protocol': 'apple-frame-v5-person-presence-closeup',
            'parameters': subject_thresholds()}


def subject_thresholds():
    config = Path(__file__).resolve().parents[1] / 'config' / 'vision_rules.json'
    value = json.loads(config.read_text(encoding='utf-8'))
    return value['person_subject']


def classify_observations(obs, thresholds=None):
    thresholds = thresholds or subject_thresholds()
    if not isinstance(obs, dict):
        raise CacheIdentityError('detector_observations_invalid')
    bodies = obs.get('body_candidates') or [obs.get('best_body') or {}]
    faces = obs.get('face_candidates') or [obs.get('best_face') or {}]
    poses = obs.get('pose_candidates') or [obs.get('best_pose') or {}]
    t = thresholds
    # Presence is independent of facing direction, frame occupancy, and full-body visibility.
    body = any(b.get('confidence', 0) >= t['presence_confidence'] for b in bodies)
    face = any(f.get('confidence', 0) >= t['presence_confidence'] for f in faces)
    pose = any(p.get('points', 0) >= t['presence_pose_points'] for p in poses)
    closeup = (any(f.get('confidence', 0) >= t['presence_confidence']
                   and f.get('area', 0) >= t['closeup_face_area_min'] for f in faces)
               or any(b.get('confidence', 0) >= t['presence_confidence']
                      and b.get('height', 0) >= t['closeup_person_height_min'] for b in bodies))
    any_person = body or face or pose or bool(obs.get('has_person_signal'))
    if any_person:
        return {'label': 'PERSON_CLOSEUP' if closeup else 'PERSON_VISIBLE',
                'subject_evidence': True,
                'reason': 'person_presence_closeup' if closeup else 'person_presence',
                'evidence': {'body': body, 'face': face, 'pose': pose}}
    return {'label': 'NO_PERSON', 'subject_evidence': False, 'reason': 'no_person_signal'}


def proven_clock(paths, media, signature=None):
    signature = signature or media_signature(media)
    expected = identity(paths, 'clock', signature, clock_model_identity(),
                        {'protocol': 'decoded-cfr-with-origin-v4'}, 'all_frames')
    return cached(paths, expected, lambda: frame_clock(media))[0]


def clock_model_identity():
    return {'ffprobe': _sha256(Path('/opt/homebrew/bin/ffprobe'))}


def classify_frame(media, frame, detector, *, fps=None):
    """Classify any person presence and close-up share, without identity matching."""
    try:
        with tempfile.TemporaryDirectory(prefix='tbcut-frame-') as folder:
            image = Path(folder)/'frame.png'
            with span('video_decode', 1):
                command = ['/opt/homebrew/bin/ffmpeg', '-v', 'error']
                if fps:
                    command += ['-ss', f'{frame / fps:.9f}']
                command += ['-i', str(media)]
                if not fps:
                    command += ['-vf', f'select=eq(n\\,{frame})']
                command += ['-frames:v', '1', '-vsync', '0', str(image)]
                subprocess.run(command, capture_output=True, check=True)
                if not image.is_file():
                    raise CacheIdentityError('frame_decode_empty')
            with span('apple_vision', 1):
                thresholds = subject_thresholds()
                result = subprocess.run([str(detector), '--frame', str(image)], text=True,
                                        capture_output=True, check=True)
                value = json.loads(result.stdout)
            if value.get('protocol') != 'apple-frame-v5' or not isinstance(value.get('observations'), dict):
                raise CacheIdentityError('detector_frame_protocol_missing')
            return classify_observations(value['observations'], thresholds)
    except Exception as exc:
        detail = getattr(exc, 'stderr', '') or str(exc)
        if isinstance(detail, bytes): detail = detail.decode('utf-8', errors='replace')
        return {'label': 'DETECTOR_ERROR', 'subject_evidence': False,
                'reason': type(exc).__name__, 'diagnostic': detail[:500]}


def validate_classification(value):
    if (not isinstance(value, dict) or value.get('label') not in LABELS
            or value['label'] in ('HOST_VISIBLE', 'PERSON_VISIBLE', 'PERSON_CLOSEUP') and value.get('subject_evidence') is not True):
        raise CacheIdentityError('unproven_person_subject_classification')
    return value


def apply_manual_person_confirmation(paths, source_file, frame, value):
    """Apply narrowly scoped human evidence without rewriting the immutable detector cache."""
    folder = paths.workspace_root/'delivery'
    for record_path in folder.glob('MANUAL_VISUAL_CONFIRMATION_*.json'):
        try:
            record = json.loads(record_path.read_text(encoding='utf-8'))
            if record.get('source_frame') != frame:
                continue
            evidence = paths.workspace_root/record['frame_evidence_path']
            if (record.get('contract') != 'codex-local-manual-person-confirmation-v1'
                    or record.get('reviewer') != 'primary_codex'
                    or record.get('source_file') != Path(source_file).name
                    or record.get('manual_result') != 'PERSON_VISIBLE'
                    or record.get('detector_result', {}).get('label') != value.get('label')
                    or record.get('detector_result', {}).get('subject_evidence') != value.get('subject_evidence')
                    or not evidence.is_file()
                    or _sha256(evidence) != record.get('frame_evidence_sha256')):
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        return {'label': 'PERSON_VISIBLE', 'subject_evidence': True,
                'reason': 'manual_visual_confirmation_of_partial_person',
                'evidence': {'manual_confirmation': str(record_path),
                             'original_detector_result': value}}
    return value


def estimate_v4(clips, samples):
    durations = [(c['source_end_frame']-c['source_start_frame'])/c['fps'] for c in clips]
    total = sum(durations)
    lower = ratio = 0.0
    coverage = True
    uncertain = errors = False
    for i, (clip, duration) in enumerate(zip(clips, durations)):
        points = sorted([s for s in samples if s['clip_index'] == i], key=lambda s: s['frame'])
        if not points:
            coverage = False
            continue
        lo, hi = clip['source_start_frame'], clip['source_end_frame']
        edges = [lo]+[(a['frame']+b['frame']+1)/2 for a, b in zip(points, points[1:])]+[hi]
        p = 0.0
        for sample, left, right in zip(points, edges, edges[1:]):
            label = validate_classification(sample['classification'])['label']
            p += (right-left)/(hi-lo) * (label == 'HOST_VISIBLE')
            uncertain |= label == 'UNCERTAIN'
            errors |= label == 'DETECTOR_ERROR'
        # A capped temporal-block count prevents adjacent补样 from manufacturing IID confidence.
        n = min(len(points), len({int((s['frame']-lo)/clip['fps']/POLICY['correlation_seconds']) for s in points}))
        z = POLICY['z_reference']
        reference = (p+z*z/(2*n)-z*math.sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n)
        gaps = [points[0]['frame']-lo, hi-1-points[-1]['frame']]+[b['frame']-a['frame'] for a, b in zip(points, points[1:])]
        max_gap = max(gaps)/clip['fps']
        # Additional unsampled-gap penalty: heuristic risk bound, NOT 95% video coverage guarantee.
        conservative = max(0.0, reference-max_gap/duration)
        ratio += duration/total*p
        lower += duration/total*conservative
        coverage &= points[0]['frame'] == lo and points[-1]['frame'] == hi-1 and max_gap <= 5.0
    status = ('BLOCKED' if errors else 'UNCERTAIN' if uncertain or not coverage else
              'PASS' if lower > 0.70 else 'REPAIRABLE' if ratio <= 0.70 else 'UNCERTAIN')
    return {'host_visible_ratio_estimate': ratio, 'host_visible_ratio_lower_bound': lower,
            'full_plan_coverage': coverage, 'person_subject_classified': not uncertain and not errors,
            'status': status, 'statistical_claim': 'temporal-block-capped heuristic; no IID/video confidence guarantee'}


def audit(paths, plan, sources, detector):
    classifier = classifier_identity(detector)
    signatures = {name: media_signature(sources[name]) for name in {c['source_file'] for v in plan['variants'] for c in v['clips']}}
    clocks = {}
    for name, signature in signatures.items():
        clocks[name] = proven_clock(paths, sources[name], signature)
    rows = []
    for variant in plan['variants']:
        clips = variant['clips']
        for c in clips:
            clock = clocks[c['source_file']]
            if (clock.get('mapping') not in ('decoded-cfr-v3', 'decoded-cfr-with-origin-v4')
                    or abs(clock['fps']-c['fps']) > 1e-5
                    or c['source_start_frame'] < 0 or c['source_end_frame'] > clock['frame_count']):
                raise CacheIdentityError('plan_source_frame_clock_mismatch')
        samples = []

        def classify_record_time(record_time, kind, stratum_index):
            clip_index, frame = map_record_time(clips, record_time)
            name = clips[clip_index]['source_file']
            expected = identity(paths, 'vision_frame', signatures[name], classifier,
                                {'protocol': FRAME_CACHE_PROTOCOL}, frame)
            def compute():
                with span('frame_classification', 1):
                    value = validate_classification(classify_frame(sources[name], frame, detector,
                                                                   fps=clips[clip_index]['fps']))
                    if value['label'] == 'DETECTOR_ERROR':
                        # Execution errors are never reusable no-person classifications.
                        raise CacheIdentityError('detector_error: '+str(value.get('diagnostic') or value.get('reason') or 'unclassified')[:500])
                    return value
            try:
                value, hit = cached(paths, expected, compute)
                validate_classification(value)
            except CacheIdentityError as exc:
                if not str(exc).startswith('detector_error:'):
                    raise
                value, hit = {'label': 'DETECTOR_ERROR', 'reason': str(exc)}, False
            value = apply_manual_person_confirmation(paths, name, frame, value)
            samples.append({'clip_index': clip_index, 'source_file': name, 'frame': frame,
                            'record_time_seconds': float(record_time), 'sample_kind': kind,
                            'stratum_index': stratum_index, 'classification': value,
                            'cache_hit': hit})

        initial_times = initial_record_times(clips)
        for stratum_index, record_time in enumerate(initial_times):
            classify_record_time(record_time, 'initial', stratum_index)
        initial_lookup = {sample['stratum_index']: sample['classification']['label']
                          for sample in samples if sample['sample_kind'] == 'initial'}
        for stratum_index, label in initial_lookup.items():
            if label in ABSENT_LABELS:
                for record_time in expected_refinement_times(clips, stratum_index):
                    classify_record_time(record_time, 'refinement', stratum_index)

        evaluation = estimate(clips, samples)
        rounds = [{'round': 1, 'new_points': len(initial_times), 'phase': 'initial',
                   'no_person_strata': [s['stratum_index'] for s in samples
                                        if s['sample_kind'] == 'initial'
                                        and s['classification']['label'] in ABSENT_LABELS]}]
        refinement_count = sum(s['sample_kind'] == 'refinement' for s in samples)
        if refinement_count:
            rounds.append({'round': 2, 'new_points': refinement_count, 'phase': 'flagged_strata_refinement'})
        for round_row in rounds:
            round_row.update({k: v for k, v in evaluation.items()})
        rows.append({'variant_id': variant['variant_id'], 'samples': samples, 'rounds': rounds,
                     'stop_reason': 'frame_budget_exhausted' if rounds[-1]['new_points'] == 0 else evaluation['status'], **evaluation})
    for signature in signatures.values():
        check_media(signature)
    if classifier_identity(detector) != classifier:
        raise CacheIdentityError('classifier_changed_during_audit')
    body = {'contract': CONTRACT, 'plan_digest': digest(plan), 'classifier': classifier,
            'policy': POLICY, 'media': signatures, 'clocks': clocks, 'variants': rows}
    from codex_worker import key
    body['seal'] = hmac.new(key(paths), digest(body).encode(), hashlib.sha256).hexdigest()
    return body


def verify(paths, plan, body, *, require_pass=True):
    from codex_worker import key
    unsigned = {k: v for k, v in body.items() if k != 'seal'}
    secret_path = paths.project_records_dir/'worker_seal.key'
    if (not secret_path.is_file() or not isinstance(body.get('seal'), str)
            or not hmac.compare_digest(body['seal'], hmac.new(secret_path.read_bytes(), digest(unsigned).encode(), hashlib.sha256).hexdigest())):
        raise CacheIdentityError('vision_execution_seal_missing_or_invalid')
    if body.get('contract') != CONTRACT or body.get('policy') != POLICY or body.get('plan_digest') != digest(plan):
        raise CacheIdentityError('vision_contract_or_plan_changed')
    if classifier_identity(body['classifier']['detector_path']) != body['classifier']:
        raise CacheIdentityError('vision_classifier_changed')
    expected = {v['variant_id']: v for v in plan['variants']}
    rows = body.get('variants', [])
    if len(rows) != len(expected) or {r['variant_id'] for r in rows} != set(expected):
        raise CacheIdentityError('vision_variant_coverage_missing')
    for signature in body['media'].values():
        check_media(signature)
    for row in rows:
        clips = expected[row['variant_id']]['clips']
        samples = row['samples']
        if len({(s['source_file'], s['frame']) for s in samples}) != len(samples):
            raise CacheIdentityError('vision_duplicate_source_samples')
        for i, c in enumerate(clips):
            for sample in [s for s in samples if s['clip_index'] == i]:
                if sample['source_file'] != c['source_file'] or not c['source_start_frame'] <= sample['frame'] < c['source_end_frame']:
                    raise CacheIdentityError('vision_sample_outside_plan')
        evaluation = estimate(clips, samples)
        if any(row.get(k) != v for k, v in evaluation.items()) or (require_pass and evaluation['status'] != 'PASS'):
            raise CacheIdentityError('vision_unverified_uncertain_or_below_threshold')
