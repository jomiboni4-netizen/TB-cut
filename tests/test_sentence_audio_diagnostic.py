import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from audio_boundary_evidence import activity_at_cut
from unittest.mock import patch
from refine_plans_v2 import audit_audio_start, refine_audio_end
import test_project_resume as fixtures
import product_checkpoints


class ActivityAtCutTests(unittest.TestCase):
    def test_later_speech_does_not_bridge_a_quiet_cut(self):
        result = activity_at_cut([(0.98, -26), (1., -32), (1.02, -27), (1.04, -12)], 1.)
        self.assertFalse(result['activity_crosses_cut'])
        self.assertFalse(result['truncation_confirmed'])
        self.assertEqual(result['word_integrity'], 'UNVERIFIED')

    def test_prior_word_activity_does_not_imply_truncation(self):
        result = activity_at_cut([(.98, -12), (1., -36)], 1.)
        self.assertFalse(result['activity_crosses_cut'])
        self.assertFalse(result['truncation_confirmed'])

    def test_continuous_activity_is_unresolved_not_confirmed_truncation(self):
        result = activity_at_cut([(.98, -12), (1., -15)], 1.)
        self.assertTrue(result['activity_crosses_cut'])
        self.assertEqual(result['word_integrity'], 'UNVERIFIED')
        self.assertFalse(result['truncation_confirmed'])

    def test_cut_bin_is_not_misclassified_by_float_rounding(self):
        result = activity_at_cut([(.98, -40), (.99999999999, -10)], 1.)
        self.assertFalse(result['activity_crosses_cut'])

    def test_missing_audio_never_passes(self):
        result = activity_at_cut([], 1.)
        self.assertIsNone(result['activity_crosses_cut'])
        self.assertEqual(result['status'], 'INSUFFICIENT_AUDIO')

    def test_nonfinite_duplicate_and_misaligned_bins_remain_unknown(self):
        for frames in [[(.98, float('nan')), (1., -40)],
                       [(.98, -40), (1., -40), (1., -40)],
                       [(.97, -40), (.99, -40)]]:
            with self.subTest(frames=frames):
                self.assertIsNone(activity_at_cut(frames, 1.)['activity_crosses_cut'])

    def test_refiner_does_not_extend_a_quiet_cut_to_swallow_the_next_word(self):
        frames = [(.98, -26), (1., -32), (1.02, -27), (1.04, -12)]
        frames += [(1.+i*.02, -40) for i in range(3, 20)]
        with patch('refine_plans_v2.audio_level_frames', return_value=frames):
            end, audit = refine_audio_end(Path('synthetic.wav'), 0., 1., 2.)
        self.assertEqual(end, 1.)
        self.assertFalse(audit['risk'])
        self.assertFalse(audit['speech_continues_after_end'])
        self.assertEqual(audit['signal_observation']['word_integrity'], 'UNVERIFIED')

    def test_opening_ignores_deleted_word_ending_before_the_quiet_cut(self):
        with patch('refine_plans_v2.audio_level_frames', return_value=[(.98, -12), (1., -36)]):
            start, audit = audit_audio_start(Path('synthetic.wav'), 1.)
        self.assertEqual(start, 1.)
        self.assertTrue(audit['speech_before_start'])
        self.assertFalse(audit['risk'])

    def test_no_decode_evidence_remains_blocked_in_both_refiners(self):
        with patch('refine_plans_v2.audio_level_frames', return_value=[]):
            self.assertTrue(audit_audio_start(Path('synthetic.wav'), 1.)[1]['risk'])
            self.assertTrue(refine_audio_end(Path('synthetic.wav'), 0., 1., 2.)[1]['risk'])


class AudioProvenanceTests(unittest.TestCase):
    register = fixtures.ProjectResumeTests.register
    setUp = fixtures.ProjectResumeTests.setUp

    def test_audio_observer_upgrade_invalidates_registered_stage_context(self):
        original = product_checkpoints._sha256
        before = product_checkpoints.context(self.paths, 1, '001', 'plan')
        with patch.object(product_checkpoints, '_sha256', side_effect=lambda path:
                          'f'*64 if path.name == 'audio_boundary_evidence.py' else original(path)):
            after = product_checkpoints.context(self.paths, 1, '001', 'plan')
        self.assertNotEqual(before['store_version'], after['store_version'])
        self.assertEqual(before['upstream_fingerprint'], after['upstream_fingerprint'])


if __name__ == '__main__':
    unittest.main()
