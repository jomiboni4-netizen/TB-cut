"""Round 3 real sampler/cache/gate consumers with synthetic external execution boundaries."""
import copy
import hashlib
import hmac
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import adaptive_vision as vision
import registered_pipeline as pipeline
import project_scheduler as scheduler
import test_project_resume as fixtures
from quality_fixtures import valid_plan, synthetic_vision, audit_fixture
import test_project_scheduler as scheduler_fixtures
from runtime_paths import resolve_runtime_paths
from runtime_meta import CacheIdentityError
from project_registry import digest, atomic_json
from product_checkpoints import (load_stage, resume, update_checkpoint, read_ledger, capture_stage,
                                 publish_stage, current_range_path)
from final_quality_gate import validate_vision, approved_product, import_final_review
from compute_store import media_signature, identity, cached, directory
from compute_metrics import Metrics, scope


def classification(label):
    return {'label': label, 'subject_evidence': label in
            {'HOST_VISIBLE', 'PERSON_VISIBLE', 'PERSON_CLOSEUP', 'OTHER_PERSON'}, 'reason': 'synthetic boundary'}


class AdaptiveVisionTests(unittest.TestCase):
    setUp = fixtures.ProjectResumeTests.setUp
    register = fixtures.ProjectResumeTests.register
    plan_ref = fixtures.ProjectResumeTests.plan

    def test_frame_clock_accepts_proven_constant_pts_origin(self):
        frames = {'frames': [{'best_effort_timestamp_time': f'{0.162+i/25:.3f}'}
                             for i in range(10)]}
        with patch.object(vision.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(frames))):
            clock = vision.frame_clock(Path('synthetic.mp4'))
        self.assertEqual(clock['mapping'], 'decoded-cfr-with-origin-v4')
        self.assertAlmostEqual(clock['video_first_pts'], 0.162)
        self.assertAlmostEqual(clock['fps'], 25)

    def test_batched_review_sends_apple_absences_directly_to_codex(self):
        import tempfile
        from compute_store import identity, media_signature
        events = []
        codex_model = {'provider': 'synthetic-codex'}
        pending = []
        cache_values = {}
        for index in range(6):
            image = self.material/f'pending-{index}.png'
            image.write_bytes(b'synthetic panel')
            record = {'variant_id': 'variant_1', 'sample_kind': 'initial',
                      'stratum_index': index, 'source_file': '001.mp4', 'frame': index,
                      'clip': {'fps': 10}}
            key = ('variant_1', 'initial', index)
            expected = identity(self.paths, 'vision_frame', media_signature(self.video),
                                {'synthetic': 'classifier'}, {'protocol': vision.FRAME_CACHE_PROTOCOL}, index)
            pending.append({'record': record, 'key': key, 'expected': expected,
                            'image_path': image, 'apple': classification('NO_PERSON'),
                            'codex': None})
        classifier = {'final_absence_review': codex_model}
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(vision, 'compose_contact_sheet', side_effect=lambda _p, images, out: events.append(('sheet', len(images))) or Path(out)), \
             patch.object(vision, 'classify_with_codex_batch',
                          side_effect=lambda images, _sheet, _model: (
                              events.append(('codex', len(images))) or
                              [classification('PERSON_VISIBLE') if str(image).endswith('0.png') else classification('NO_PERSON') for image in images])):
            vision.review_pending_frames(self.paths, pending, classifier, folder, cache_values)
        self.assertEqual(sorted(events), sorted([('sheet', 4), ('codex', 4),
                                                 ('sheet', 2), ('codex', 2)]))
        self.assertEqual(cache_values[pending[0]['key']][0]['label'], 'PERSON_VISIBLE')
        self.assertEqual(cache_values[pending[1]['key']][0]['label'], 'NO_PERSON')
        self.assertEqual(pending[0]['codex']['label'], 'PERSON_VISIBLE')

    def test_codex_person_finding_overrides_apple_absence(self):
        import tempfile
        from compute_store import identity, media_signature
        image = self.material/'codex-override.png'
        image.write_bytes(b'synthetic panel')
        record = {'variant_id': 'variant_1', 'sample_kind': 'initial', 'stratum_index': 0,
                  'source_file': '001.mp4', 'frame': 7, 'clip': {'fps': 10}}
        key = ('variant_1', 'initial', 0)
        expected = identity(self.paths, 'vision_frame', media_signature(self.video),
                            {'synthetic': 'classifier'}, {'protocol': vision.FRAME_CACHE_PROTOCOL}, 7)
        pending = [{'record': record, 'key': key, 'expected': expected, 'image_path': image,
                    'apple': classification('NO_PERSON'), 'codex': None}]
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(vision, 'compose_contact_sheet', side_effect=lambda _p, _i, out: Path(out)), \
             patch.object(vision, 'classify_with_codex_batch', return_value=[classification('PERSON_VISIBLE')]):
            cache_values = {}
            vision.review_pending_frames(self.paths, pending,
                                         {'final_absence_review': {'provider': 'codex'}}, folder, cache_values)
        self.assertEqual(cache_values[key][0]['label'], 'PERSON_VISIBLE')

    def test_batched_response_missing_duplicate_or_malformed_panels_stay_uncertain(self):
        model = {'provider': 'synthetic'}
        malformed = vision._labels_from_batch_response('not json', 2, model, 'synthetic')
        self.assertEqual([row['label'] for row in malformed], ['UNCERTAIN', 'UNCERTAIN'])
        missing = vision._labels_from_batch_response(
            '{"frames":[{"panel":1,"person_present":false,"closeup":false}]}', 2, model, 'synthetic')
        self.assertEqual([row['label'] for row in missing], ['NO_PERSON', 'UNCERTAIN'])
        duplicate = vision._labels_from_batch_response(
            '{"frames":[{"panel":1,"person_present":false,"closeup":false},'
            '{"panel":1,"person_present":true,"closeup":false}]}', 2, model, 'synthetic')
        self.assertEqual([row['label'] for row in duplicate], ['UNCERTAIN', 'UNCERTAIN'])

    def audit(self, plan=None, classify=None, paths=None, fps=10, codex_classify=None):
        plan = plan or valid_plan()
        paths = paths or self.paths
        detector = self.material/'frame-detector'
        if not detector.exists(): detector.write_text('synthetic detector v1')
        with synthetic_vision(fps, classify, codex_classify) as boundary:
            body = vision.audit(paths, plan, {'001.mp4': self.video}, detector)
        return body, boundary.call_count

    def test_codex_overturns_initial_no_person_without_generating_refinement(self):
        calls = [0]
        def apple():
            calls[0] += 1
            return classification('NO_PERSON' if calls[0] == 1 else 'HOST_VISIBLE')
        body, decoded = self.audit(
            classify=apple,
            codex_classify=lambda _apple, _image: classification('PERSON_VISIBLE'))
        row = body['variants'][0]
        self.assertEqual(decoded, 10)
        self.assertEqual([round_row['new_points'] for round_row in row['rounds']], [10])
        self.assertEqual(row['samples'][0]['classification']['label'], 'PERSON_VISIBLE')

    def test_codex_confirmed_initial_no_person_refines_only_that_stratum(self):
        calls = [0]
        def apple():
            calls[0] += 1
            return classification('NO_PERSON' if calls[0] == 1 else 'HOST_VISIBLE')
        body, decoded = self.audit(
            classify=apple,
            codex_classify=lambda apple, _image: classification(apple['label']))
        row = body['variants'][0]
        self.assertEqual(decoded, 20)
        self.assertEqual([round_row['new_points'] for round_row in row['rounds']], [10, 10])
        self.assertEqual({sample['stratum_index'] for sample in row['samples']
                          if sample['sample_kind'] == 'refinement'}, {0})

    def test_one_minute_all_people_uses_only_ten_initial_samples(self):
        plan = valid_plan(); plan['variants'][0]['clips'][0]['source_end_frame'] = 600
        body, calls = self.audit(plan)
        row = body['variants'][0]
        self.assertEqual(row['rounds'][0]['new_points'], 10)
        self.assertEqual(calls, 10)
        self.assertEqual(row['status'], 'PASS')
        validate_vision(plan, body, self.paths)

    def test_one_empty_initial_stratum_gets_ten_point_refinement(self):
        count = [0]
        def mixed(*args, **kwargs):
            count[0] += 1
            if count[0] == 1:
                return classification('NO_PERSON')
            if count[0] <= 10:
                return classification('HOST_VISIBLE')
            return classification('NO_PERSON' if count[0] < 20 else 'HOST_VISIBLE')
        body, calls = self.audit(classify=mixed)
        row = body['variants'][0]
        self.assertEqual(row['status'], 'PASS')
        self.assertEqual(row['no_person_ratio_estimate'], 0.0)
        self.assertEqual([r['new_points'] for r in row['rounds']], [10, 10])
        self.assertEqual(calls, 20)
        self.assertEqual(len({s['frame'] for s in row['samples']}), calls)
        vision.verify(self.paths, valid_plan(), body, require_pass=False)

    def test_all_initial_and_refinement_frames_empty_exceeds_ten_percent(self):
        body, calls = self.audit(classify=lambda *args, **kwargs: classification('NO_PERSON'))
        self.assertEqual(calls, 110)
        self.assertEqual(body['variants'][0]['no_person_ratio_estimate'], 1.0)
        self.assertEqual(body['variants'][0]['status'], 'REPAIRABLE')

    def test_record_time_sampling_is_not_weighted_by_physical_clip_count(self):
        plan = valid_plan()
        original = plan['variants'][0]['clips'][0]
        source_start, source_end = original['source_start_frame'], original['source_end_frame']
        plan['variants'][0]['clips'] = [
            {**copy.deepcopy(original), 'source_start_frame': source_start+i*100,
             'source_end_frame': source_start+(i+1)*100}
            for i in range(5)]
        body, calls = self.audit(plan)
        self.assertEqual(calls, 10)
        self.assertEqual(body['variants'][0]['status'], 'PASS')
        self.assertEqual(body['variants'][0]['person_presence_ratio_estimate'], 1.0)

    def test_other_person_counts_as_person_and_clothing_does_not_trigger_refinement(self):
        detector = self.material/'frame-detector'; detector.write_text('other-person classifier')
        body, calls = self.audit(classify=lambda *args, **kwargs: classification('OTHER_PERSON'))
        self.assertEqual(calls, 10)
        self.assertEqual(body['variants'][0]['person_presence_ratio_estimate'], 1.0)
        self.assertEqual(body['variants'][0]['status'], 'PASS')
        detector.write_text('clothing-only classifier')
        body, calls = self.audit(classify=lambda *args, **kwargs: classification('CLOTHING_CLOSEUP'))
        self.assertEqual(calls, 10)
        self.assertEqual(body['variants'][0]['status'], 'REPAIRABLE')

    def test_error_blocks_and_is_not_reusable_cache(self):
        body, first = self.audit(classify=lambda *args, **kwargs: classification('DETECTOR_ERROR'))
        self.assertEqual(body['variants'][0]['status'], 'BLOCKED')
        self.assertEqual(list(directory(self.paths, 'vision_frame').glob('*.json')), [])
        _, second = self.audit(classify=lambda *args, **kwargs: classification('DETECTOR_ERROR'))
        self.assertEqual(first, second)

    def test_no_identity_registry_or_qwen_is_required(self):
        body, _ = self.audit(classify=lambda *args, **kwargs: classification('UNCERTAIN'))
        self.assertEqual(body['variants'][0]['status'], 'UNCERTAIN')
        self.assertNotIn('host_reference', body['classifier'])
        self.assertNotIn('qwen', body['classifier']['parameters'])

    def test_frame_geometry_classifies_prominent_body_face_and_pose(self):
        from unittest.mock import Mock
        payloads = [
            {'best_body': {'confidence': .95, 'height': .6, 'width': .35, 'area': .25}, 'best_face': {}, 'best_pose': {}, 'has_person_signal': True},
            {'best_body': {}, 'best_face': {'confidence': .9, 'height': .12, 'area': .01}, 'best_pose': {}, 'has_person_signal': True},
            {'best_body': {}, 'best_face': {}, 'best_pose': {'points': 12, 'height': .55, 'area': .2}, 'has_person_signal': True},
            {'best_body': {'confidence': .9, 'height': .12, 'width': .08, 'area': .02}, 'best_face': {}, 'best_pose': {}, 'has_person_signal': True},
            {'best_body': {}, 'best_face': {}, 'best_pose': {}, 'has_person_signal': False},
        ]
        expected = ['PERSON_VISIBLE', 'PERSON_VISIBLE', 'PERSON_VISIBLE', 'PERSON_VISIBLE', 'NO_PERSON']
        detector = self.material/'feature-detector'; detector.write_text('synthetic')
        for observations, label in zip(payloads, expected):
            def run(command, **kwargs):
                if command[0].endswith('ffmpeg'):
                    Path(command[-1]).write_bytes(b'frame')
                    return Mock(stdout='', stderr='')
                return Mock(stdout=json.dumps({'protocol':'apple-frame-v6','observations':observations}), stderr='')
            with patch.object(vision.subprocess, 'run', side_effect=run), \
                 patch.object(vision, 'classify_with_codex', side_effect=lambda _image: classification(label)):
                result = vision.classify_frame(self.video, 0, detector)
            self.assertEqual(result['label'], label)
            self.assertEqual(result.get('subject_evidence', False), label in ('HOST_VISIBLE', 'PERSON_VISIBLE', 'PERSON_CLOSEUP'))

    def test_frame_review_sends_only_confirmed_apple_absence_to_codex(self):
        from unittest.mock import Mock
        detector = self.material/'escalation-detector'; detector.write_text('synthetic')
        def run_with_apple(label):
            has_person = label in ('PERSON_VISIBLE', 'PERSON_CLOSEUP', 'OTHER_PERSON')
            observations = {'best_body': {'confidence': .95} if has_person else {},
                            'best_face': ({'confidence': .95, 'area': .5} if label == 'PERSON_CLOSEUP' else {}),
                            'best_pose': {}, 'has_person_signal': has_person,
                            'pose_available': False if label == 'UNCERTAIN' else True}
            def run(command, **kwargs):
                if command[0].endswith('ffmpeg'):
                    Path(command[-1]).write_bytes(b'frame')
                    return Mock(stdout='', stderr='')
                return Mock(stdout=json.dumps({'protocol':'apple-frame-v6','observations':observations}), stderr='')
            with patch.object(vision.subprocess, 'run', side_effect=run), \
                 patch.object(vision, 'classify_observations', return_value=classification(label)), \
                 patch.object(vision, 'classify_with_codex', return_value=classification('PERSON_VISIBLE')) as codex_call:
                result = vision.classify_frame(self.video, 0, detector)
            return result, codex_call
        for positive in ('PERSON_VISIBLE', 'PERSON_CLOSEUP'):
            result, codex_call = run_with_apple(positive)
            codex_call.assert_not_called()
        result, codex_call = run_with_apple('NO_PERSON')
        codex_call.assert_called_once()
        self.assertEqual(result['label'], 'PERSON_VISIBLE')
        self.assertTrue(result['review_trace']['codex']['called'])
        for not_confirmed_absent in ('UNCERTAIN', 'CLOTHING_CLOSEUP', 'DETECTOR_ERROR'):
            result, codex_call = run_with_apple(not_confirmed_absent)
            codex_call.assert_not_called()
            self.assertEqual(result['label'], not_confirmed_absent)
            self.assertFalse(result['review_trace']['codex']['called'])

    def test_visual_metrics_use_codex_fallback_without_qwen(self):
        from unittest.mock import Mock
        detector = self.material/'metrics-detector'; detector.write_text('synthetic')
        observations = {'best_body': {}, 'best_face': {}, 'best_pose': {}, 'has_person_signal': False}
        def run(command, **kwargs):
            if command[0].endswith('ffmpeg'):
                Path(command[-1]).write_bytes(b'frame')
                return Mock(stdout='', stderr='')
            return Mock(stdout=json.dumps({'protocol':'apple-frame-v6','observations':observations}), stderr='')
        metrics = Metrics()
        with patch.object(vision.subprocess, 'run', side_effect=run), \
             patch.object(vision, 'classify_with_codex', return_value=classification('NO_PERSON')), \
             scope(metrics):
            result = vision.classify_frame(self.video, 0, detector)
        self.assertEqual(metrics.rows['codex_fallback']['calls'], 1)
        self.assertEqual(metrics.rows['codex_fallback']['units'], 1)
        self.assertEqual(result['review_trace']['apple_vision']['label'], 'NO_PERSON')

    def test_different_people_require_no_identity_match(self):
        body, _ = self.audit(classify=lambda *args, **kwargs: classification('HOST_VISIBLE'))
        self.assertEqual(body['variants'][0]['status'], 'PASS')

    def test_missing_identity_fields_never_block_visible_person(self):
        self.assertTrue(vision.validate_classification(classification('HOST_VISIBLE'))['subject_evidence'])
        with self.assertRaisesRegex(CacheIdentityError, 'unproven_person_subject'):
            vision.validate_classification({'label': 'HOST_VISIBLE', 'host_verified': False})

    def test_old_classifier_contract_cannot_validate_as_current(self):
        from codex_worker import key
        body, _ = self.audit()
        for old_contract in ('host-strata-v3', 'person-subject-strata-v4', 'person-subject-record-strata-v5',
                             'person-presence-closeup-record-strata-v6'):
            legacy = copy.deepcopy(body)
            legacy['contract'] = old_contract
            legacy['seal'] = hmac.new(key(self.paths), digest({k: v for k, v in legacy.items() if k != 'seal'}).encode(),
                                      hashlib.sha256).hexdigest()
            with self.assertRaisesRegex(CacheIdentityError, 'contract_or_plan_changed'):
                validate_vision(valid_plan(), legacy, self.paths)

    def test_cut_change_invalidates_sealed_vision(self):
        body, _ = self.audit()
        plan = valid_plan(); plan['variants'][0]['clips'][0]['source_start_frame'] += 1
        with self.assertRaisesRegex(CacheIdentityError, 'plan_changed'): validate_vision(plan, body, self.paths)

    def test_same_frame_cache_and_no_duplicate_decode(self):
        body, first = self.audit()
        again, second = self.audit()
        self.assertGreater(first, 0)
        self.assertEqual(second, 0)
        self.assertTrue(all(s['cache_hit'] for s in again['variants'][0]['samples']))
        self.assertEqual(body['variants'][0]['no_person_ratio_estimate'], again['variants'][0]['no_person_ratio_estimate'])

    def test_detector_version_invalidates_gate_and_computes_new_frames(self):
        body, calls = self.audit()
        (self.material/'frame-detector').write_text('synthetic detector v2')
        with self.assertRaisesRegex(CacheIdentityError, 'classifier_changed'): validate_vision(valid_plan(), body, self.paths)
        _, new_calls = self.audit()
        self.assertEqual(new_calls, calls)

    def test_classifier_parameters_get_distinct_bottom_cache(self):
        body, calls = self.audit()
        original = vision.classifier_identity
        def changed(detector):
            value = original(detector); value['parameters']['body_area'] = .9
            return value
        with patch.object(vision, 'classifier_identity', side_effect=changed):
            _, new_calls = self.audit()
            with self.assertRaises(CacheIdentityError): validate_vision(valid_plan(), body, self.paths)
        self.assertEqual(new_calls, calls)

    def test_two_projects_same_named_media_not_shared(self):
        _, first = self.audit()
        other = resolve_runtime_paths(self.repo, self.base/'other')
        self.register(other, 'other-stable-project-id')
        _, second = self.audit(paths=other)
        self.assertEqual(first, second)
        self.assertNotEqual(directory(self.paths, 'vision_frame'), directory(other, 'vision_frame'))

    def test_old_counts_and_handfilled_flags_cannot_pass(self):
        for old in ({'personSamples': 99, 'totalSamples': 100}, {'variants': [{
            'variant_id': 'variant_1', 'host_identity_verified': True, 'full_plan_coverage': True,
            'host_visible_ratio_lower_bound': .99}]}):
            with self.assertRaisesRegex(CacheIdentityError, 'seal'): validate_vision(valid_plan(), old, self.paths)

    def test_new_and_legacy_cache_coexist_without_import(self):
        old = self.paths.cache_dir/'person_probe_cache_v1'/'legacy.json'
        old.parent.mkdir(parents=True); old.write_text('{"personSamples":999,"totalSamples":999}')
        body, calls = self.audit()
        self.assertGreater(calls, 0)
        self.assertEqual(body['contract'], vision.CONTRACT)
        self.assertTrue(old.exists())

    def test_corrupt_new_cache_blocks_not_recomputed(self):
        self.audit()
        path = next(directory(self.paths, 'vision_frame').glob('*.json'))
        value = json.loads(path.read_text()); value.pop('seal'); atomic_json(path, value)
        with self.assertRaisesRegex(CacheIdentityError, 'unproven_or_corrupt'): self.audit()

    def test_duration_over_sampling_policy_blocks_and_multifps_mapping(self):
        plan = valid_plan()
        c = plan['variants'][0]['clips'][0]; c['source_end_frame'] = 610
        with self.assertRaisesRegex(CacheIdentityError, 'duration_outside_sampling_policy'):
            self.audit(plan)
        c['fps'] = 25; c['source_end_frame'] = 1250
        self.video.write_bytes(b'synthetic different 25fps media')
        body, _ = self.audit(plan, fps=25)
        self.assertEqual(len(body['variants'][0]['samples']), 10)
        self.assertTrue(all(0 <= s['frame'] < 1250 for s in body['variants'][0]['samples']))

    def test_vfr_or_mismatched_frame_clock_blocks(self):
        detector = self.material/'frame-detector'; detector.write_text('synthetic')
        with synthetic_vision(25), self.assertRaisesRegex(CacheIdentityError, 'clock_mismatch'):
            vision.audit(self.paths, valid_plan(), {'001.mp4': self.video}, detector)
        fake = {'frames': [{'best_effort_timestamp_time': t} for t in ('0', '.04', '.09')]}
        with patch.object(vision.subprocess, 'run', return_value=__import__('subprocess').CompletedProcess([], 0, json.dumps(fake))), \
             self.assertRaisesRegex(CacheIdentityError, 'vfr_exact_mapping'):
            vision.frame_clock(self.video)

    def test_cache_concurrent_writer_and_failure_protection(self):
        expected = identity(self.paths, 'synthetic', media_signature(self.video), {'weights': 'proof'}, {}, [0, 1])
        def compute():
            with ThreadPoolExecutor() as pool:
                with self.assertRaisesRegex(CacheIdentityError, 'writer_busy'):
                    pool.submit(cached, self.paths, expected, lambda: {'wrong': True}).result()
            return {'value': 1}
        self.assertEqual(cached(self.paths, expected, compute), ({'value': 1}, False))
        failed = {**expected, 'bounds': [1, 2]}
        with self.assertRaises(RuntimeError): cached(self.paths, failed, lambda: (_ for _ in ()).throw(RuntimeError('synthetic failure')))
        self.assertEqual(cached(self.paths, failed, lambda: {'value': 2}), ({'value': 2}, False))

    def test_statistics_cache_hits_and_calls_reproducible(self):
        first, second = Metrics(), Metrics()
        with scope(first): body, calls = self.audit()
        with scope(second): again, repeat_calls = self.audit()
        self.assertEqual(first.rows['frame_classification']['calls'], calls)
        self.assertEqual(second.rows['frame_classification']['calls'], 0)
        self.assertEqual(repeat_calls, 0)
        self.assertEqual(second.rows['cache']['hits'], calls+1)
        self.assertGreaterEqual(first.rows['cache']['seconds'], 0)
        self.assertFalse(any(hasattr(vision, name) for name in (
            'classify_with_qwen', 'classify_with_qwen_batch', 'qwen_model_identity',
            'unload_qwen_model')))

    def test_person_pass_without_codex_receipt_cannot_approve(self):
        ref = self.plan_ref()
        record = load_stage(self.paths, ref)
        body, _ = self.audit(record['body'])
        args = dict(product_id=1, encoding_id='001', stage='vision', version=digest(body), model_version='synthetic', dependencies=[ref])
        evidence = publish_stage(self.paths, **args, body=body, snapshot=capture_stage(self.paths, **args))
        with self.assertRaises(ValueError): import_final_review(self.paths, ref, evidence, 'missing-receipt.json')
        with self.assertRaises(CacheIdentityError): approved_product(self.paths, 1)

    def test_sealed_evidence_tamper_blocks(self):
        body, _ = self.audit()
        body['variants'][0]['host_visible_ratio_lower_bound'] = 1.0
        with self.assertRaisesRegex(CacheIdentityError, 'seal'): validate_vision(valid_plan(), body, self.paths)

    def test_legacy_person_cli_and_missing_model_safe_block(self):
        import person_coverage_audit_v2
        from asr_identity import model_identity
        with self.assertRaisesRegex(SystemExit, 'legacy_person_counts'): person_coverage_audit_v2.main()
        with patch.dict(sys.modules, {'faster_whisper': None}), self.assertRaisesRegex(CacheIdentityError, 'weights_unavailable'):
            model_identity('not-installed')


class AsrReuseIntegrationTests(unittest.TestCase):
    # Borrow fixtures, do not inherit tests and duplicate discovery.
    setUp = scheduler_fixtures.SchedulerTests.setUp
    setUpBase = fixtures.ProjectResumeTests.setUp
    register = fixtures.ProjectResumeTests.register
    engine = scheduler_fixtures.SchedulerTests.engine

    def test_asr_edges_are_bound_to_each_range_when_candidate_and_variant_repeat(self):
        import codex_worker
        with self.engine(), patch.object(codex_worker, 'launch', side_effect=lambda task: None):
            range_ref = json.loads(current_range_path(self.paths, 1, '001').read_text())['reference']
            candidate_ref = pipeline.candidates(self.paths, range_ref)
            candidate = load_stage(self.paths, candidate_ref)['body']['candidates'][0]
            selected_ranges = [
                {'candidate_id': candidate['candidate_id'], 'variant_id': 'variant_1',
                 'start': 1.0, 'end': 24.0},
                {'candidate_id': candidate['candidate_id'], 'variant_id': 'variant_1',
                 'start': 26.0, 'end': 49.0},
            ]
            rough_body = {'selected_ranges': selected_ranges, 'selection_receipt': 'synthetic-receipt',
                          'selection_task': {}, 'semantic_map': [],
                          'semantic_map_digest': digest([])}
            rough_args = dict(product_id=1, encoding_id='001', stage='rough',
                              version=digest(rough_body), model_version='synthetic-selection-v1',
                              dependencies=[candidate_ref])
            rough_ref = publish_stage(self.paths, **rough_args, body=rough_body,
                                      snapshot=capture_stage(self.paths, **rough_args))

            def transcribe(_media, start, _end, _model):
                if start < 5:
                    return [{'start': 1.0, 'end': 1.1, 'text': '开'}]
                if start < 30:
                    return [{'start': 23.8, 'end': 24.0, 'text': '一'},
                            {'start': 26.0, 'end': 26.2, 'text': '二'}]
                return [{'start': 48.9, 'end': 49.0, 'text': '尾'}]

            with patch.object(pipeline, 'transcribe_window', side_effect=transcribe):
                asr_ref = pipeline.local_asr(self.paths, rough_ref)
            asr = load_stage(self.paths, asr_ref)['body']
            range_ids = {row['selected_range_id'] for row in asr['edges']}
            self.assertEqual(len(range_ids), 2)
            self.assertEqual(len(asr['edges']), 4)

            plan_ref = pipeline.physical_plan(self.paths, rough_ref, asr_ref)
            plan = load_stage(self.paths, plan_ref)['body']
            clips = plan['variants'][0]['clips']
            self.assertEqual(len(clips), 2)
            self.assertLess(clips[0]['source_end_frame'], clips[1]['source_start_frame'])
            self.assertEqual(len({clip['selected_range_id'] for clip in clips}), 2)

    def test_identical_and_containing_asr_windows_reused_without_model_load(self):
        with self.engine():
            result = scheduler.process_product(self.paths, {'product_id': 1, 'encoding_id': '001'}, mode='codex', detector=self.detector)
            self.assertEqual(result['status'], 'FINAL_PASS', result)
            row = resume(self.paths)[1]
            plan = load_stage(self.paths, row['plan_ref'])
            rough_ref = next(d['path'] for d in plan['identity']['dependencies'] if load_stage(self.paths, d['path'])['identity']['stage'] == 'rough')
            rough = load_stage(self.paths, rough_ref)
            changed = copy.deepcopy(rough['body'])
            # Keep the exact edge requests so the newly published rough stage
            # can reuse both previously transcribed windows without inference.
            args = dict(product_id=1, encoding_id='001', stage='rough', version='narrow-window', model_version='synthetic',
                        dependencies=[d['path'] for d in rough['identity']['dependencies']])
            narrower = publish_stage(self.paths, **args, body=changed, snapshot=capture_stage(self.paths, **args))
            with patch.object(pipeline, 'transcribe_window', side_effect=AssertionError('should reuse containing ASR')):
                asr = pipeline.local_asr(self.paths, narrower)
            record = load_stage(self.paths, asr)
            self.assertIn(narrower, [d['path'] for d in record['identity']['dependencies']])
            self.assertEqual(len(record['body']['windows']), 2)
            self.assertTrue(all(window['fallback_level'] == 'nearby_boundary'
                                for window in record['body']['windows']))

    def test_model_weights_change_invalidates_approval_and_rebuilds_dependent_plan(self):
        product = {'product_id': 1, 'encoding_id': '001'}
        with self.engine():
            self.assertEqual(scheduler.process_product(self.paths, product, mode='codex', detector=self.detector)['status'], 'FINAL_PASS')
            old = resume(self.paths)[1]['plan_ref']
            weights = pipeline.asr_model_identity('large-v3-turbo')
            weights['weights_digest'] = digest('synthetic changed weights')
            with patch.object(pipeline, 'asr_model_identity', return_value=weights):
                with self.assertRaises(CacheIdentityError): approved_product(self.paths, 1)
                result = scheduler.process_product(self.paths, product, mode='codex', detector=self.detector)
                self.assertEqual(result['status'], 'FINAL_PASS', result)
                self.assertNotEqual(resume(self.paths)[1]['plan_ref'], old)
                self.assertEqual(pipeline.transcribe_window.call_count, 4)

    def test_other_product_reuses_frame_computation_but_cannot_approve_overlapping_plan(self):
        import codex_worker
        from product_checkpoints import current_range_path
        from final_quality_gate import final_task
        with self.engine():
            result = scheduler.process_product(self.paths, {'product_id': 1, 'encoding_id': '001'}, mode='codex', detector=self.detector)
            self.assertEqual(result['status'], 'FINAL_PASS', result)
            first = load_stage(self.paths, resume(self.paths)[1]['plan_ref'])
            range_ref = json.loads(current_range_path(self.paths, 2, '002').read_text())['reference']
            args = dict(product_id=2, encoding_id='002', stage='plan', version='synthetic-overlap', model_version='synthetic', dependencies=[range_ref])
            second = publish_stage(self.paths, **args, body=first['body'], snapshot=capture_stage(self.paths, **args))
            with patch.object(vision, 'classify_frame', side_effect=AssertionError('same frames should be shared')):
                evidence = pipeline.vision_audit(self.paths, second, self.detector)
            body = load_stage(self.paths, evidence)['body']
            self.assertTrue(all(s['cache_hit'] for s in body['variants'][0]['samples']))
            receipt = codex_worker.execute(self.paths, final_task(self.paths, second))
            with self.assertRaisesRegex(CacheIdentityError, 'outside_confirmed_range'):
                import_final_review(self.paths, second, evidence, receipt)


if __name__ == '__main__': unittest.main()
