import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from owned_audio_refinement import choose_edge


class OwnedAudioTests(unittest.TestCase):
    def test_nearby_pause_outside_owned_words_is_never_selected(self):
        levels = [(i*.02, -40 if i < 50 else -10) for i in range(45,60)]
        frame, report = choose_edge(levels, 26, 25, 1.02, 1.06)
        self.assertEqual(frame,26)
        self.assertEqual(report['status'],'REPAIRABLE')

    def test_legal_quiet_frame_recovers_without_changing_words(self):
        levels = [(.98,-10),(1.,-10),(1.02,-40),(1.04,-40),(1.06,-10)]
        frame, report = choose_edge(levels, 25, 25, 1., 1.04)
        self.assertEqual(frame,26)
        self.assertEqual(report['shift_frames'],1)
        self.assertFalse(report['phoneme_accuracy_certified'])

    def test_existing_quiet_cut_is_locked(self):
        levels = [(.98,-40),(1.,-40),(1.02,-60),(1.04,-60)]
        frame, report = choose_edge(levels,25,25,.96,1.08)
        self.assertEqual(frame,25)
        self.assertEqual(report['shift_frames'],0)

    def test_empty_decode_and_continuous_speech_cannot_pass(self):
        for levels in [[],[(.98,-10),(1.,-10),(1.02,-10),(1.04,-10)]]:
            self.assertEqual(choose_edge(levels,25,25,.96,1.08)[1]['status'],'REPAIRABLE')

    def test_estimated_half_frame_can_stay_but_not_authorize_extra_padding(self):
        levels = [(.98,-40),(1.,-40),(1.02,-10),(1.04,-10)]
        frame, report = choose_edge(levels,25,25,1.005,1.01)
        self.assertEqual(frame,25)
        self.assertEqual(report['status'],'PASS_LOCAL_RISK_GATE')


if __name__=='__main__':unittest.main()
