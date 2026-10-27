import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import adaptive_vision as vision
import test_project_resume as fixtures
from compute_store import identity, media_signature, cached_value
from runtime_meta import CacheIdentityError
from vision_recovery import Controller, recovery_scope, request_timeout, POLICY


class VisionRecoveryTests(unittest.TestCase):
    setUp = fixtures.ProjectResumeTests.setUp
    register = fixtures.ProjectResumeTests.register

    def pending(self, count):
        result = []
        for i in range(count):
            image = self.material / f'frame_{i}.png'
            image.write_bytes(b'original frame ' + str(i).encode())
            expected = identity(self.paths, 'vision_frame', media_signature(self.video),
                                {'synthetic': 'classifier'}, {'protocol': vision.FRAME_CACHE_PROTOCOL}, i)
            result.append({'record': {'variant_id': 'v1', 'sample_kind': 'initial', 'stratum_index': i},
                           'key': ('v1', 'initial', i), 'expected': expected, 'image_path': image,
                           'apple': {'label': 'NO_PERSON', 'subject_evidence': False, 'reason': 'apple absent'},
                           'codex': None})
        return result

    def review(self, controller, pending, classify):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(vision, 'compose_contact_sheet', side_effect=lambda _p, _i, out: Path(out)), \
                patch.object(vision, 'map_bounded', side_effect=lambda f, rows, **_k: [f(r) for r in rows]), \
                patch.object(vision, 'classify_with_codex_batch', side_effect=classify):
            for item in pending:
                controller.prepare(item)
            values = {}
            vision.review_pending_frames(self.paths, pending, {'final_absence_review': {}}, folder, values)
            return values

    def test_timeout_recovers_pairs_and_keeps_original_inputs(self):
        pending = self.pending(4); sizes = []; timeouts = []
        def classify(images, *_):
            sizes.append(len(images)); timeouts.append(request_timeout())
            if len(sizes) == 1:
                raise subprocess.TimeoutExpired('synthetic', 60)
            return [{'label': 'PERSON_VISIBLE', 'subject_evidence': True, 'reason': 'person'} for _ in images]
        with recovery_scope(self.paths, 1) as controller:
            values = self.review(controller, pending, classify)
            self.assertEqual(sizes, [4, 2, 2])
            self.assertGreater(timeouts[0], 0)
            self.assertLessEqual(timeouts[0], POLICY['initial_request_seconds'])
            self.assertTrue(all(0 < t <= POLICY['recovery_request_seconds'] for t in timeouts[1:]))
            self.assertEqual(controller.summary()['requests_used'], 2)
            self.assertTrue(all(v[0]['label'] == 'PERSON_VISIBLE' for v in values.values()))

    def test_restart_does_not_reset_frame_or_request_budget(self):
        pending = self.pending(1); calls = []
        def timeout(images, *_):
            calls.append(len(images)); raise subprocess.TimeoutExpired('synthetic', 1)
        with recovery_scope(self.paths, 1) as controller:
            values = self.review(controller, pending, timeout)
            self.assertEqual(controller.summary()['requests_used'], 2)
            self.assertEqual(values[pending[0]['key']][0]['label'], 'DETECTOR_ERROR')
        with recovery_scope(self.paths, 1) as controller:
            self.review(controller, pending, timeout)
            self.assertEqual(controller.summary()['requests_used'], 2)
        self.assertEqual(len(calls), 3)  # One initial request, two bounded recoveries.
        self.assertFalse(cached_value(self.paths, pending[0]['expected'])[1])

    def test_interrupted_reservations_remain_consumed(self):
        pending = self.pending(5); controller = Controller(self.paths, 1)
        for item in pending[:4]:
            self.assertIsNotNone(controller._reserve([item]))
        restarted = Controller(self.paths, 1)
        self.assertIsNone(restarted._reserve([pending[4]]))
        self.assertEqual(restarted.summary()['recovery_seconds_used'], 180)

    def test_many_fast_failures_cannot_exceed_six_recovery_calls(self):
        pending = self.pending(12); calls = []
        def timeout(images, *_):
            calls.append(len(images)); raise subprocess.TimeoutExpired('synthetic', 1)
        with recovery_scope(self.paths, 1) as controller:
            self.review(controller, pending, timeout)
            self.assertEqual(controller.summary()['requests_used'], 6)
            self.assertTrue(all(v['attempts'] <= 2 for v in controller.state['frames'].values()))
        self.assertLessEqual(len(calls), 9)  # Exhaustion also stops queued initial calls.

    def test_success_is_cached_before_next_batch(self):
        pending = self.pending(8); calls = [0]
        def classify(images, *_):
            calls[0] += 1
            if calls[0] == 2:
                self.assertTrue(cached_value(self.paths, pending[0]['expected'])[1])
            return [{'label': 'NO_PERSON', 'subject_evidence': False, 'reason': 'confirmed absence'} for _ in images]
        with recovery_scope(self.paths, 1) as controller:
            self.review(controller, pending, classify)
            self.assertEqual(controller.summary()['requests_used'], 0)

    def test_authenticated_original_image_survives_temp_cleanup(self):
        item = self.pending(1)[0]; controller = Controller(self.paths, 1)
        controller.prepare(item); original = item['image_path'].read_bytes(); item['image_path'].unlink()
        self.assertTrue(controller.restore(item))
        self.assertEqual(item['image_path'].read_bytes(), original)
        record = controller.jobs / (__import__('project_registry').digest(item['expected']) + '.json')
        record.write_text(record.read_text().replace('apple absent', 'tampered'))
        with self.assertRaisesRegex(CacheIdentityError, 'record_unproven'):
            controller.restore(item)

    def test_confirmed_absence_never_enters_technical_recovery(self):
        pending = self.pending(1)
        with recovery_scope(self.paths, 1) as controller:
            values = self.review(controller, pending, lambda images, *_: [
                {'label': 'NO_PERSON', 'subject_evidence': False, 'reason': 'confirmed absence'} for _ in images])
            self.assertEqual(controller.summary()['requests_used'], 0)
            self.assertEqual(values[pending[0]['key']][0]['label'], 'NO_PERSON')

    def test_user_hold_blocks_recovery_without_spending_more_budget(self):
        from project_task import initialize, record_hold
        initialize(self.paths, 'synthetic bounded recovery')
        controller = Controller(self.paths, 1)
        record_hold(self.paths, ['run'], 'synthetic user hold')
        with self.assertRaisesRegex(ValueError, 'project_task_paused'):
            controller._reserve(self.pending(1))
        self.assertEqual(controller.summary()['requests_used'], 0)

    def test_policy_change_cannot_create_a_fresh_budget(self):
        controller = Controller(self.paths, 1)
        controller._reserve(self.pending(1))
        with patch.dict(POLICY, {'max_recovery_requests_per_product': 7}):
            with self.assertRaisesRegex(CacheIdentityError, 'recovery_identity_changed'):
                Controller(self.paths, 1)
        self.assertEqual(Controller(self.paths, 1).summary()['requests_used'], 1)


if __name__ == '__main__':
    unittest.main()
