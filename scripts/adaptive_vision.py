"""Deterministic source-frame sampling with versioned record-time scoring."""
import ast
import hashlib
import hmac
import json
import math
import platform
import shutil
import fcntl
from functools import lru_cache
from contextlib import nullcontext
from fractions import Fraction
from pathlib import Path
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from project_registry import digest
from runtime_meta import CacheIdentityError, _sha256
from compute_store import media_signature, identity, cached, check_media
from compute_metrics import span, count
from vision_recovery import current as recovery_controller, request_timeout, technical_failure
from vision_record_score_v5 import (CONTRACT, POLICY, ABSENT_LABELS, estimate,
                                    expected_refinement_times, initial_record_times,
                                    map_record_time)

FRAME_CACHE_PROTOCOL = 'person-presence-closeup-strata-v13-parallel-codex-sheets'
BATCH_REVIEW_PROTOCOL = 'apple-initial-codex-gate-then-apple-refinement-codex-v4-parallel-sheets'
PRESCREEN_CONTRACT = 'selection-person-prescreen-v1-three-samples'
PRESCREEN_SAMPLE_COUNT = 3
PRESCREEN_ABSENT_MINIMUM = 2
# Legacy v4 adapter digest is retained only for reproducing old evidence identities.
_V4_ADAPTER_SHA256 = '464bb0bae7b7cbc46bd996174f0d747b7d3c21e7e4deabb84f6713d292234c5d'
_V4_CLASSIFIER_AST_SHA256 = 'e61b0e6bf0972a660516580b94f94708c89ce0eada81adcc21e938fd20ddb646'
_CLASSIFIER_FUNCTIONS = {'decoder_identity', 'subject_thresholds', 'classify_observations',
                         'classify_frame',
                         'classify_with_codex', 'codex_model_identity',
                         'validate_classification', 'classify_apple_frame',
                         'compose_contact_sheet', 'classify_with_codex_batch',
                         'collect_apple_frames', 'review_pending_frames'}
LABELS = {'HOST_VISIBLE', 'PERSON_VISIBLE', 'PERSON_CLOSEUP', 'CLOTHING_CLOSEUP', 'OTHER_PERSON', 'NO_PERSON', 'UNCERTAIN', 'DETECTOR_ERROR'}
APPLE_CODEX_LABEL = 'NO_PERSON'
_decoders = {}
MAX_CONCURRENT_FRAME_CLASSIFIERS = 4
MAX_CONCURRENT_CODEX_SHEETS = 4


def map_bounded(function, records, max_workers=MAX_CONCURRENT_FRAME_CLASSIFIERS):
    """Run independent frame jobs concurrently while preserving input order and context."""
    records = list(records)
    if not records:
        return []
    if type(max_workers) is not int or max_workers < 1:
        raise CacheIdentityError('frame_classifier_worker_limit_invalid')
    with ThreadPoolExecutor(max_workers=min(max_workers, len(records))) as pool:
        futures = [pool.submit(copy_context().run, function, record) for record in records]
        return [future.result() for future in futures]


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


def frame_clock(media, *, allow_vfr_approximate=False, preferred_fps=None):
    """Prove CFR timestamps, or use the explicitly approved metadata mean-rate approximation."""
    with span('frame_clock'):
        if allow_vfr_approximate:
            result = subprocess.run(['/opt/homebrew/bin/ffprobe', '-v', 'error', '-select_streams', 'v:0',
                '-show_entries', 'stream=avg_frame_rate,nb_frames,duration,start_time',
                '-show_entries', 'format=duration', '-of', 'json', str(media)],
                text=True, capture_output=True, check=True)
            metadata = json.loads(result.stdout)
            streams = metadata.get('streams') or []
            if len(streams) != 1:
                raise CacheIdentityError('source_video_stream_metadata_missing_or_ambiguous')
            stream = streams[0]
            try:
                average_fps = float(Fraction(stream['avg_frame_rate']))
                duration_value = stream.get('duration') or metadata.get('format', {}).get('duration')
                duration = float(duration_value)
                origin = float(stream.get('start_time') or 0)
                frame_count = int(stream['nb_frames'])
            except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
                raise CacheIdentityError('source_average_rate_metadata_invalid') from exc
            if not all(math.isfinite(value) for value in (average_fps, duration, origin)) or average_fps <= 0 or duration <= 0:
                raise CacheIdentityError('source_average_rate_metadata_invalid')
            if preferred_fps is not None:
                preferred_fps = float(preferred_fps)
                if not math.isfinite(preferred_fps) or preferred_fps <= 0:
                    raise CacheIdentityError('preferred_frame_rate_invalid')
            snap_delta = abs(average_fps-preferred_fps) if preferred_fps is not None else math.inf
            fps = preferred_fps if snap_delta <= 0.01 else average_fps
            if frame_count < 2:
                frame_count = round(duration * average_fps)
            if frame_count < 2:
                raise CacheIdentityError('source_average_rate_frame_count_unavailable')
            evidence = {'avg_frame_rate': stream['avg_frame_rate'], 'duration': duration,
                        'nb_frames': stream.get('nb_frames'), 'start_time': origin,
                        'preferred_rate_fps': preferred_fps, 'selected_rate_fps': fps,
                        'rate_rounding_delta_fps': fps-average_fps}
            return {'fps': fps, 'average_fps': average_fps, 'frame_count': frame_count,
                    'mapping': 'decoded-vfr-average-rate-approx-v2',
                    'video_first_pts': origin, 'pts_digest': None,
                    'clock_metadata_digest': digest(evidence), 'clock_metadata': evidence,
                    'max_deviation_seconds': None}
        result = subprocess.run(['/opt/homebrew/bin/ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_frames', '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', str(media)],
            text=True, capture_output=True, check=True)
        times = [float(f['best_effort_timestamp_time']) for f in json.loads(result.stdout)['frames']]
        if len(times) < 2 or not all(math.isfinite(t) for t in times):
            raise CacheIdentityError('source_frame_clock_unproven')
        delta = (times[-1]-times[0])/(len(times)-1)
        origin = times[0]
        deviations = [abs(t-origin-i*delta) for i, t in enumerate(times)]
        max_deviation = max(deviations)
        if delta <= 0 or (max_deviation > 2e-6 and not allow_vfr_approximate):
            raise CacheIdentityError('vfr_exact_mapping_not_integrated')
        mapping = ('decoded-vfr-average-rate-approx-v1' if max_deviation > 2e-6
                   else 'decoded-cfr-with-origin-v4')
        return {'fps': 1/delta, 'frame_count': len(times), 'pts_digest': digest(times),
                'video_first_pts': origin, 'mapping': mapping,
                'max_deviation_seconds': max_deviation}


def classifier_identity(detector):
    detector = Path(detector).resolve()
    if not detector.is_file():
        raise CacheIdentityError('host_detector_missing')
    return {'detector_path': str(detector), 'binary_sha256': _sha256(detector),
            'recovery_source_sha256': _sha256(Path(__file__).with_name('vision_recovery.py')),
            'adapter_sha256': classifier_adapter_digest(),
            'decoder': decoder_identity(),
            'runtime': {'os': platform.platform(), 'macos': platform.mac_ver()[0]},
            'protocol': 'apple-frame-v6-plus-codex-gated-refinement-v7-parallel-sheets',
            'batch_review_protocol': BATCH_REVIEW_PROTOCOL,
            'contact_sheet_source_sha256': _sha256(Path(__file__).with_name('vision_contact_sheet.swift')),
            'final_absence_review': codex_model_identity(),
            'escalation_policy': {
                'codex_when_apple': [APPLE_CODEX_LABEL],
            },
            'parameters': subject_thresholds()}

def compose_contact_sheet(paths, images, output):
    """Compose up to four full-resolution samples into a stable, numbered 2x2 sheet."""
    images = [Path(image).resolve() for image in images]
    output = Path(output).resolve()
    if not 1 <= len(images) <= 4 or not all(image.is_file() for image in images):
        raise CacheIdentityError('vision_contact_sheet_inputs_invalid')
    source = Path(__file__).with_name('vision_contact_sheet.swift')
    dependency_dir = paths.project_directory/'dependencies'
    dependency_dir.mkdir(parents=True, exist_ok=True)
    binary = dependency_dir/'vision_contact_sheet'
    lock_path = dependency_dir/'.vision_contact_sheet.lock'
    compiler = shutil.which('swiftc')
    if not compiler:
        raise CacheIdentityError('vision_contact_sheet_swiftc_missing')
    with lock_path.open('a+b') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if not binary.is_file() or binary.stat().st_mtime_ns < source.stat().st_mtime_ns:
            module_cache = dependency_dir/'.swift_module_cache'
            module_cache.mkdir(parents=True, exist_ok=True)
            built = subprocess.run([compiler, '-module-cache-path', str(module_cache), str(source),
                                    '-o', str(binary)], capture_output=True, text=True, timeout=120)
            if built.returncode:
                raise CacheIdentityError('vision_contact_sheet_build_failed')
        version = subprocess.run([str(binary), '--version'], capture_output=True, text=True, timeout=10)
        if version.returncode or version.stdout.strip() != 'vision-contact-sheet-v1-four-panel':
            raise CacheIdentityError('vision_contact_sheet_protocol_mismatch')
        built_sheet = subprocess.run([str(binary), '--output', str(output), *map(str, images)],
                                     capture_output=True, text=True, timeout=30)
        if built_sheet.returncode or not output.is_file():
            raise CacheIdentityError('vision_contact_sheet_render_failed')
    return output

def _labels_from_batch_response(raw, count, model, reason_prefix):
    """Parse a numbered panel response; a missing/ambiguous panel stays uncertain."""
    try:
        payload = json.loads(raw)
        rows = payload['frames']
        mapped = {int(row['panel']): row for row in rows}
        if len(mapped) != len(rows):
            mapped = {}
        if len(mapped) != len(rows) or set(mapped)-set(range(1, count+1)):
            mapped = {}
    except (TypeError, ValueError, KeyError):
        mapped = {}
    if not isinstance(raw, str):
        raw = json.dumps(raw, ensure_ascii=False, sort_keys=True)
    response_digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    result = []
    for panel in range(1, count+1):
        row = mapped.get(panel)
        person = row.get('person_present') if isinstance(row, dict) else None
        closeup = row.get('closeup') if isinstance(row, dict) else None
        evidence = {'model': model, 'response_sha256': response_digest,
                    'panel': panel, 'batch_protocol': BATCH_REVIEW_PROTOCOL}
        if type(person) is bool:
            label = ('PERSON_CLOSEUP' if closeup is True else 'PERSON_VISIBLE') if person else 'NO_PERSON'
            finding = 'person' if person else 'no_person'
            result.append({'label': label, 'subject_evidence': person,
                           'reason': reason_prefix+'_confirms_'+finding, 'evidence': evidence})
        else:
            result.append({'label': 'UNCERTAIN', 'subject_evidence': False,
                           'reason': reason_prefix+('_response_invalid' if row is None else '_batch_panel_uncertain'), 'evidence': evidence})
    return result

@lru_cache(maxsize=1)
def codex_model_identity():
    executable = shutil.which('codex')
    if not executable:
        raise CacheIdentityError('codex_visual_reviewer_missing')
    result = subprocess.run([executable, '--version'], text=True, capture_output=True,
                            check=True, timeout=5)
    version = (result.stdout or result.stderr).strip()
    if not version:
        raise CacheIdentityError('codex_visual_reviewer_version_missing')
    return {'provider': 'codex_cli', 'version': version, 'purpose': 'final_person_presence_review'}


def classify_with_codex(image):
    """Visual second opinion for a single Apple-flagged frame."""
    executable = shutil.which('codex')
    model = codex_model_identity()
    schema = {'type': 'object', 'additionalProperties': False,
              'properties': {'person_present': {'type': ['boolean', 'null']},
                             'closeup': {'type': ['boolean', 'null']}},
              'required': ['person_present', 'closeup']}
    prompt = ('Classify this sampled video frame for visible human presence. Count partial people, hands, '
              'and people wearing or modeling clothing. Do not count a mannequin, hanger, or garment alone. '
              'If unclear, return null. Return JSON only with person_present and closeup booleans or null.')
    with tempfile.TemporaryDirectory(prefix='tbcut-codex-vision-') as folder:
        folder_path = Path(folder)
        schema_path = folder_path / 'schema.json'
        schema_path.write_text(json.dumps(schema), encoding='utf-8')
        command = [executable, 'exec', '--ephemeral', '--ignore-user-config', '--json',
                   '--sandbox', 'read-only', '--skip-git-repo-check', '-C', folder,
                   '--output-schema', str(schema_path), '--image', str(image), '-']
        result = subprocess.run(command, input=prompt, text=True, capture_output=True,
                                check=False, timeout=90)
    if result.returncode:
        raise CacheIdentityError('codex_visual_review_failed')
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if (len([e for e in events if e.get('type') == 'thread.started']) != 1
            or not any(e.get('type') == 'turn.completed' for e in events)
            or any(e.get('type') in ('turn.failed', 'error') or e.get('item', {}).get('type') in
                   ('command_execution', 'mcp_tool_call', 'file_change', 'web_search') for e in events)):
        raise CacheIdentityError('codex_visual_review_provenance_invalid')
    messages = [e['item']['text'] for e in events if e.get('type') == 'item.completed'
                and e.get('item', {}).get('type') == 'agent_message']
    if len(messages) != 1:
        raise CacheIdentityError('codex_visual_review_response_ambiguous')
    try:
        verdict = json.loads(messages[0])
        person, closeup = verdict['person_present'], verdict['closeup']
    except (ValueError, TypeError, KeyError):
        person = closeup = None
    evidence = {'model': model, 'response_sha256': hashlib.sha256(messages[0].encode()).hexdigest()}
    if person is True:
        return {'label': 'PERSON_CLOSEUP' if closeup is True else 'PERSON_VISIBLE',
                'subject_evidence': True, 'reason': 'codex_confirms_person', 'evidence': evidence}
    if person is False:
        return {'label': 'NO_PERSON', 'subject_evidence': False,
                'reason': 'codex_confirms_no_person', 'evidence': evidence}
    return {'label': 'UNCERTAIN', 'subject_evidence': False,
            'reason': 'codex_visual_review_uncertain', 'evidence': evidence}


def classify_with_codex_batch(images, contact_sheet, model):
    """Review the unresolved numbered panels in one Codex request."""
    count = len(images)
    item_schema = {'type': 'object', 'additionalProperties': False,
                   'properties': {'panel': {'type': 'integer', 'minimum': 1, 'maximum': count},
                                  'person_present': {'type': ['boolean', 'null']},
                                  'closeup': {'type': ['boolean', 'null']}},
                   'required': ['panel', 'person_present', 'closeup']}
    schema = {'type': 'object', 'additionalProperties': False,
              'properties': {'frames': {'type': 'array', 'items': item_schema,
                                         'minItems': count, 'maxItems': count}},
              'required': ['frames']}
    prompt = ('Review this numbered 2x2 contact sheet. Assess each occupied video-frame panel independently. '
              'Count partial people, hands, and people wearing/modeling clothing. Do not count a mannequin, '
              'hanger, or garment alone. Do not let one panel affect another. Return JSON with exactly one '
              'frames item for each occupied panel, using its printed panel number; use null if unclear.')
    executable = shutil.which('codex')
    if not executable:
        raise CacheIdentityError('codex_visual_reviewer_missing')
    with tempfile.TemporaryDirectory(prefix='tbcut-codex-vision-batch-') as folder:
        schema_path = Path(folder)/'schema.json'
        schema_path.write_text(json.dumps(schema), encoding='utf-8')
        command = [executable, 'exec', '--ephemeral', '--ignore-user-config', '--json',
                   '--sandbox', 'read-only', '--skip-git-repo-check', '-C', folder,
                   '--output-schema', str(schema_path), '--image', str(contact_sheet), '-']
        with span('codex_fallback', count):
            result = subprocess.run(command, input=prompt, text=True, capture_output=True,
                                    check=False, timeout=request_timeout())
    if result.returncode:
        raise CacheIdentityError('codex_visual_review_failed')
    try:
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    except ValueError as exc:
        raise CacheIdentityError('codex_visual_review_response_invalid') from exc
    if (len([event for event in events if event.get('type') == 'thread.started']) != 1
            or not any(event.get('type') == 'turn.completed' for event in events)
            or any(event.get('type') in ('turn.failed', 'error')
                   or event.get('item', {}).get('type') in
                   ('command_execution', 'mcp_tool_call', 'file_change', 'web_search')
                   for event in events)):
        raise CacheIdentityError('codex_visual_review_provenance_invalid')
    messages = [event['item']['text'] for event in events
                if event.get('type') == 'item.completed'
                and event.get('item', {}).get('type') == 'agent_message']
    if len(messages) != 1:
        raise CacheIdentityError('codex_visual_review_response_ambiguous')
    return _labels_from_batch_response(messages[0], count, model, 'codex')


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
    if obs.get('pose_available') is False:
        return {'label': 'UNCERTAIN', 'subject_evidence': False,
                'reason': 'pose_detector_unavailable_without_rectangle_or_face_evidence'}
    return {'label': 'NO_PERSON', 'subject_evidence': False, 'reason': 'no_person_signal'}


def proven_clock(paths, media, signature=None, *, allow_vfr_approximate=False, preferred_fps=None):
    signature = signature or media_signature(media)
    protocol = ('decoded-cfr-with-origin-v4+vfr-average-approx-v2' if allow_vfr_approximate
                else 'decoded-cfr-with-origin-v4')
    expected = identity(paths, 'clock', signature, clock_model_identity(),
                        {'protocol': protocol, 'preferred_fps': preferred_fps}, 'all_frames')
    return cached(paths, expected, lambda: frame_clock(media, allow_vfr_approximate=allow_vfr_approximate,
                                                        preferred_fps=preferred_fps))[0]


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
            if value.get('protocol') != 'apple-frame-v6' or not isinstance(value.get('observations'), dict):
                raise CacheIdentityError('detector_frame_protocol_missing')
            classification = classify_observations(value['observations'], thresholds)
            trace = {'apple_vision': {'called': True, 'label': classification['label']},
                     'codex': {'called': False}}
            if classification['label'] != APPLE_CODEX_LABEL:
                classification['review_trace'] = trace
                return classification
            with span('codex_fallback', 1):
                opinion = classify_with_codex(image)
            trace['codex'] = {'called': True, 'label': opinion['label']}
            opinion['apple_vision'] = classification
            opinion['review_trace'] = trace
            return opinion
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


def classify_apple_frame(media, frame, detector, image_path, *, fps=None):
    """Decode and run Apple Vision only; the batch review stage handles escalation."""
    image_path = Path(image_path)
    try:
        with span('video_decode', 1):
            command = ['/opt/homebrew/bin/ffmpeg', '-v', 'error']
            if fps:
                command += ['-ss', f'{frame / fps:.9f}']
            command += ['-i', str(media)]
            if not fps:
                command += ['-vf', f'select=eq(n\\,{frame})']
            command += ['-frames:v', '1', '-vsync', '0', str(image_path)]
            subprocess.run(command, capture_output=True, check=True, timeout=request_timeout())
            if not image_path.is_file():
                raise CacheIdentityError('frame_decode_empty')
        with span('apple_vision', 1):
            count('frame_classification', calls=1, units=1)
            result = subprocess.run([str(detector), '--frame', str(image_path)], text=True,
                                    capture_output=True, check=True, timeout=request_timeout())
            value = json.loads(result.stdout)
        if value.get('protocol') != 'apple-frame-v6' or not isinstance(value.get('observations'), dict):
            raise CacheIdentityError('detector_frame_protocol_missing')
        classification = classify_observations(value['observations'])
        classification['review_trace'] = {
            'apple_vision': {'called': True, 'label': classification['label']},
            'codex': {'called': False}}
        return classification
    except Exception as exc:
        return {'label': 'DETECTOR_ERROR', 'subject_evidence': False,
                'reason': type(exc).__name__, 'review_trace': {
                    'apple_vision': {'called': True, 'label': 'DETECTOR_ERROR'},
                    'codex': {'called': False}}}


def _chunks(values, size=4):
    return [values[index:index+size] for index in range(0, len(values), size)]


def sample_result_key(record):
    return (record['variant_id'], record['sample_kind'], record['stratum_index'],
            record['source_file'], record['frame'])


def collect_apple_frames(paths, records, detector, classifier, source_paths,
                         media_signatures, temp_dir, cache_values):
    """Resolve final-cache hits and complete Apple Vision for every uncached frame."""
    from compute_store import cached, cached_value, identity
    pending = []
    pending_by_identity = {}
    for record in records:
        source_name = record['source_file']
        media = media_signatures[source_name]
        frame = record['frame']
        expected = identity(paths, 'vision_frame', media, classifier,
                            {'protocol': FRAME_CACHE_PROTOCOL}, frame)
        cached_result, hit = cached_value(paths, expected)
        key = sample_result_key(record)
        if hit:
            cache_values[key] = (cached_result, True)
            continue
        frame_identity = digest(expected)
        if frame_identity in pending_by_identity:
            pending_by_identity[frame_identity].setdefault('alias_keys', []).append(key)
            continue
        clip = record['clip']
        image_key = digest([record['variant_id'], record['sample_kind'], record['stratum_index'],
                            source_name, frame])[:20]
        image_path = Path(temp_dir)/f'frame_{image_key}.png'
        pending.append({'record': record, 'key': key, 'expected': expected,
                        'media_path': source_paths[source_name],
                        'fps': clip['fps'], 'image_path': image_path})
        pending_by_identity[frame_identity] = pending[-1]

    controller = recovery_controller()
    unresolved = [item for item in pending if not (controller and controller.restore(item))]
    apple_results = map_bounded(
        lambda item: classify_apple_frame(item['media_path'], item['record']['frame'], detector,
                                          item['image_path'], fps=item['fps']), unresolved)
    for item, result in zip(unresolved, apple_results):
        item['apple'] = result
        item['codex'] = None
        if controller and technical_failure(result):
            def retry_apple(items):
                return [classify_apple_frame(entry['media_path'], entry['record']['frame'], detector,
                                            entry['image_path'], fps=entry['record']['clip']['fps'])
                        for entry in items]
            item['apple'] = controller.recover([item], [result], retry_apple)[0]
        if controller:
            controller.prepare(item)
    return pending


def review_pending_frames(paths, pending, classifier, temp_dir, cache_values, *, phase='frames'):
    """After the complete Apple pass, send only confirmed Apple no-person frames to Codex."""
    from compute_store import cached

    controller = recovery_controller()
    # Prepare the seal before parallel successful batches publish their cache.
    if pending and hasattr(paths, 'project_records_dir'):
        from codex_worker import key
        key(paths)

    def publish_item(item):
        apple, codex = item['apple'], item.get('codex')
        trace = {'apple_vision': apple.get('review_trace', {}).get('apple_vision',
                           {'called': True, 'label': apple['label']}),
                 'codex': {'called': codex is not None, 'label': codex.get('label') if codex else None},
                 'batch_protocol': BATCH_REVIEW_PROTOCOL}
        if apple['label'] != APPLE_CODEX_LABEL:
            final = dict(apple)
        elif codex is not None:
            final = dict(codex)
        else:
            final = {'label': 'DETECTOR_ERROR', 'subject_evidence': False,
                     'reason': 'codex_review_result_missing'}
        final.update(apple_vision=apple, review_trace=trace)
        if codex is not None:
            final['codex'] = codex
        final = validate_classification(final)
        if final['label'] != 'DETECTOR_ERROR' and not technical_failure(final):
            final, _ = cached(paths, item['expected'], lambda: final)
        cache_values[item['key']] = (final, False)
        for alias_key in item.get('alias_keys', []):
            cache_values[alias_key] = (dict(final), False)
        if controller:
            controller.finished(item, final)

    codex_pending = [item for item in pending
                     if item['apple']['label'] == APPLE_CODEX_LABEL
                     and item['image_path'].is_file()]
    for item in pending:
        if item not in codex_pending:
            publish_item(item)
    if codex_pending:
        model = classifier['final_absence_review']
        batches = list(enumerate(_chunks(codex_pending)))

        def review_group(batch):
            batch_number, group = batch
            serial = [0]
            def request(items):
                serial[0] += 1
                try:
                    sheet = compose_contact_sheet(paths, [item['image_path'] for item in items],
                        Path(temp_dir)/f'codex_{phase}_{batch_number:04d}_{serial[0]}.png')
                    with controller.request_slot() if controller else nullcontext():
                        values = classify_with_codex_batch([item['image_path'] for item in items], sheet, model)
                    if len(values) != len(items):
                        raise CacheIdentityError('codex_batch_count_mismatch')
                    for value in values:
                        validate_classification(value)
                    return values
                except Exception as exc:
                    reason = str(exc) if isinstance(exc, CacheIdentityError) else type(exc).__name__
                    return [{'label': 'DETECTOR_ERROR', 'subject_evidence': False,
                             'reason': 'codex_batch_error_'+reason} for _ in items]
            verdicts = controller.initial(group, request) if controller else request(group)
            # Cache successful evidence before any retry, not after the slowest batch.
            for item, verdict in zip(group, verdicts):
                item['codex'] = verdict
                publish_item(item)
            if controller and any(technical_failure(v) for v in verdicts):
                def recover_request(items):
                    values = request(items)
                    for item, value in zip(items, values):
                        item['codex'] = value
                        publish_item(item)
                    return values
                verdicts = controller.recover(group, verdicts, recover_request)
                for item, verdict in zip(group, verdicts):
                    item['codex'] = verdict
                    publish_item(item)
            return verdicts

        batch_results = map_bounded(review_group, batches,
                                    max_workers=MAX_CONCURRENT_CODEX_SHEETS)
        for (_batch_number, group), verdicts in zip(batches, batch_results):
            for item, verdict in zip(group, verdicts):
                item['codex'] = verdict


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


def _prescreen_times(clips):
    from vision_record_score_v5 import variant_duration
    duration = variant_duration(clips)
    return [duration * Fraction(2 * index + 1, 2 * PRESCREEN_SAMPLE_COUNT)
            for index in range(PRESCREEN_SAMPLE_COUNT)]


def _prescreen_estimate(samples):
    labels = [validate_classification(row['classification'])['label'] for row in samples]
    if len(labels) != PRESCREEN_SAMPLE_COUNT:
        return {'status': 'BLOCKED', 'full_plan_coverage': False,
                'reason': 'selection_prescreen_sample_coverage_missing'}
    if 'DETECTOR_ERROR' in labels:
        status = 'BLOCKED'
    elif 'UNCERTAIN' in labels:
        status = 'UNCERTAIN'
    else:
        status = ('REPAIRABLE' if sum(label in ABSENT_LABELS for label in labels)
                  >= PRESCREEN_ABSENT_MINIMUM else 'PASS')
    absent = sum(label in ABSENT_LABELS for label in labels)
    closeup = sum(label == 'PERSON_CLOSEUP' for label in labels)
    return {'status': status, 'full_plan_coverage': True,
            'sample_count': len(labels), 'confirmed_absent_sample_count': absent,
            'confirmed_absent_sample_ratio': absent / len(labels),
            'closeup_sample_count': closeup,
            'closeup_sample_ratio': closeup / len(labels),
            'evidence_level': 'LIGHTWEIGHT_SELECTION_PRESCREEN',
            'strict_framewise_certified': False}


def audit(paths, plan, sources, detector, *, allow_vfr_approximate=False,
          audit_mode='final'):
    if audit_mode not in ('final', 'selection_prescreen'):
        raise CacheIdentityError('vision_audit_mode_invalid')
    editing_policy = plan.get('editing_policy') or {}
    closeup_limit = editing_policy.get('closeup_duration_ratio_max_inclusive', POLICY['closeup_duration_ratio_max_inclusive'])
    if not isinstance(closeup_limit, (int, float)) or not 0 <= closeup_limit <= 1:
        raise CacheIdentityError('project_closeup_policy_invalid')
    effective_policy = dict(POLICY, closeup_duration_ratio_max_inclusive=closeup_limit)
    classifier = classifier_identity(detector)
    signatures = {name: media_signature(sources[name]) for name in {c['source_file'] for v in plan['variants'] for c in v['clips']}}
    preferred_rates = {name: next(c['fps'] for v in plan['variants'] for c in v['clips']
                                  if c['source_file'] == name) for name in signatures}
    clocks = {}
    for name, signature in signatures.items():
        clocks[name] = proven_clock(paths, sources[name], signature,
                                    allow_vfr_approximate=allow_vfr_approximate,
                                    preferred_fps=preferred_rates[name])
    rows = []
    initial_records = []
    variant_clips = {}
    variant_times = {}
    for variant in plan['variants']:
        clips = variant['clips']
        variant_id = variant['variant_id']
        variant_clips[variant_id] = clips
        for c in clips:
            clock = clocks[c['source_file']]
            permitted_mappings = {'decoded-cfr-v3', 'decoded-cfr-with-origin-v4'}
            if allow_vfr_approximate:
                permitted_mappings.add('decoded-vfr-average-rate-approx-v2')
            if (clock.get('mapping') not in permitted_mappings
                    or clock.get('mapping') != c.get('frame_clock_mapping')
                    or abs(clock['fps']-c['fps']) > 1e-5
                    or c['source_start_frame'] < 0 or c['source_end_frame'] > clock['frame_count']):
                raise CacheIdentityError('plan_source_frame_clock_mismatch')
        times = (_prescreen_times(clips) if audit_mode == 'selection_prescreen'
                 else initial_record_times(clips))
        variant_times[variant_id] = times
        for stratum_index, record_time in enumerate(times):
            clip_index, frame = map_record_time(clips, record_time)
            initial_records.append({
                'variant_id': variant_id, 'clip': clips[clip_index], 'clip_index': clip_index,
                'source_file': clips[clip_index]['source_file'], 'frame': frame,
                'record_time_seconds': float(record_time), 'sample_kind': 'initial',
                'stratum_index': stratum_index})

    cache_values = {}
    all_pending = []
    with tempfile.TemporaryDirectory(prefix='tbcut-vision-batches-') as temp_dir:
        initial_pending = collect_apple_frames(
            paths, initial_records, detector, classifier, sources, signatures, temp_dir, cache_values)
        all_pending.extend(initial_pending)
        # Codex gates refinement. A Codex person finding ends this stratum here.
        review_pending_frames(paths, initial_pending, classifier, temp_dir, cache_values,
                              phase='initial')

        initial_labels = {
            (record['variant_id'], record['sample_kind'], record['stratum_index']):
            cache_values[sample_result_key(record)][0]['label']
            for record in initial_records
        }
        refinement_records = []
        if audit_mode == 'final':
            for variant_id, times in variant_times.items():
                clips = variant_clips[variant_id]
                for stratum_index in range(len(times)):
                    key = (variant_id, 'initial', stratum_index)
                    if initial_labels[key] != 'NO_PERSON':
                        continue
                    for record_time in expected_refinement_times(clips, stratum_index):
                        clip_index, frame = map_record_time(clips, record_time)
                        refinement_records.append({
                            'variant_id': variant_id, 'clip': clips[clip_index], 'clip_index': clip_index,
                            'source_file': clips[clip_index]['source_file'], 'frame': frame,
                            'record_time_seconds': float(record_time), 'sample_kind': 'refinement',
                            'stratum_index': stratum_index})
        refinement_pending = collect_apple_frames(
            paths, refinement_records, detector, classifier, sources, signatures, temp_dir, cache_values)
        all_pending.extend(refinement_pending)
        # Finish Apple Vision for all refinement frames, then route only NO_PERSON to Codex.
        review_pending_frames(paths, refinement_pending, classifier, temp_dir, cache_values,
                              phase='refinement')

    for variant in plan['variants']:
        clips = variant['clips']
        variant_id = variant['variant_id']
        samples = []
        initial_samples = []
        refinement_samples = []
        for record in initial_records:
            if record['variant_id'] != variant_id:
                continue
            key = sample_result_key(record)
            value, hit = cache_values[key]
            classification = apply_manual_person_confirmation(
                paths, record['source_file'], record['frame'], value)
            sample = {key: record[key] for key in
                      ('clip_index', 'source_file', 'frame', 'record_time_seconds',
                       'sample_kind', 'stratum_index')}
            sample.update({'classification': classification, 'cache_hit': hit})
            initial_samples.append(sample)
        active_refinement_strata = {sample['stratum_index'] for sample in initial_samples
                                    if sample['classification']['label'] == 'NO_PERSON'}
        for record in refinement_records:
            if record['variant_id'] != variant_id or record['stratum_index'] not in active_refinement_strata:
                continue
            key = sample_result_key(record)
            value, hit = cache_values[key]
            classification = apply_manual_person_confirmation(
                paths, record['source_file'], record['frame'], value)
            sample = {key: record[key] for key in
                      ('clip_index', 'source_file', 'frame', 'record_time_seconds',
                       'sample_kind', 'stratum_index')}
            sample.update({'classification': classification, 'cache_hit': hit})
            refinement_samples.append(sample)
        samples = initial_samples + refinement_samples

        evaluation = (_prescreen_estimate(samples) if audit_mode == 'selection_prescreen'
                      else estimate(clips, samples, closeup_ratio_max=closeup_limit))
        rounds = [{'round': 1, 'new_points': len(variant_times[variant_id]), 'phase': 'initial',
                   'no_person_strata': [s['stratum_index'] for s in samples
                                        if s['sample_kind'] == 'initial'
                                        and s['classification']['label'] in ABSENT_LABELS]}]
        refinement_count = sum(s['sample_kind'] == 'refinement' for s in samples)
        if refinement_count:
            rounds.append({'round': 2, 'new_points': refinement_count, 'phase': 'flagged_strata_refinement'})
        for round_row in rounds:
            round_row.update({k: v for k, v in evaluation.items()})
        rows.append({'variant_id': variant_id, 'samples': samples, 'rounds': rounds,
                     'stop_reason': 'frame_budget_exhausted' if rounds[-1]['new_points'] == 0 else evaluation['status'], **evaluation})
    for signature in signatures.values():
        check_media(signature)
    if classifier_identity(detector) != classifier:
        raise CacheIdentityError('classifier_changed_during_audit')
    body = {'contract': PRESCREEN_CONTRACT if audit_mode == 'selection_prescreen' else CONTRACT,
            'audit_mode': audit_mode, 'plan_digest': digest(plan), 'classifier': classifier,
            'policy': effective_policy, 'media': signatures, 'clocks': clocks, 'variants': rows}
    from codex_worker import key
    if recovery_controller():
        body['technical_recovery'] = recovery_controller().summary()
    body['seal'] = hmac.new(key(paths), digest(body).encode(), hashlib.sha256).hexdigest()
    return body


def verify(paths, plan, body, *, require_pass=True):
    from codex_worker import key
    unsigned = {k: v for k, v in body.items() if k != 'seal'}
    secret_path = paths.project_records_dir/'worker_seal.key'
    if (not secret_path.is_file() or not isinstance(body.get('seal'), str)
            or not hmac.compare_digest(body['seal'], hmac.new(secret_path.read_bytes(), digest(unsigned).encode(), hashlib.sha256).hexdigest())):
        raise CacheIdentityError('vision_execution_seal_missing_or_invalid')
    expected_policy = dict(POLICY, closeup_duration_ratio_max_inclusive=(plan.get('editing_policy') or {}).get(
        'closeup_duration_ratio_max_inclusive', POLICY['closeup_duration_ratio_max_inclusive']))
    if body.get('contract') != CONTRACT or body.get('policy') != expected_policy or body.get('plan_digest') != digest(plan):
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
