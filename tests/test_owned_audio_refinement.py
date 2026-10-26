import sys
import unittest
from pathlib import Path
from copy import deepcopy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from owned_audio_refinement import choose_edge, refine


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

    def test_half_frame_tolerance_resolves_adjacent_asr_word_edge(self):
        levels = [(.96,-18),(.98,-18),(1.,-18),(1.02,-18)]
        frame, report = choose_edge(levels,25,25,1.005,1.01,frame_tolerance=.02)
        self.assertEqual(frame,25)
        self.assertEqual(report['legal_frame_bounds'],[25,25])
        self.assertEqual(report['status'],'PASS_CONTINUOUS_SPEECH_WORD_GAP')

    def test_unresolved_asr_cutpoint_keeps_provisional_plan_for_independent_visual_audit(self):
        plan={'variants':[{'variant_id':'variant_1','clips':[{
            'unit_id':'variant_1_1','source_file':'synthetic.mp4',
            'source_start_frame':100,'source_end_frame':600,'audio_start_frame':100,
            'audio_end_frame':600,'fps':10,'duration_sec':50,'words':[],
            'sentence_edges':{},'transcript':'synthetic selection',
            'source_subtitle_text':'synthetic selection','audio_boundary_verified':False,
            'cutpoint_review':{'status':'REPAIRABLE',
                'fallback':'subtitle_cue_span_for_visual_audit',
                'edge_failures':{'opening':'local edge not found'}}}]}]}
        updated,audit=refine(deepcopy(plan),{})
        clip=updated['variants'][0]['clips'][0]
        self.assertEqual(clip['source_start_frame'],100)
        self.assertEqual(clip['source_end_frame'],600)
        self.assertFalse(clip['audio_boundary_verified'])
        self.assertEqual(clip['cutpoint_review']['status'],'REPAIRABLE')
        self.assertEqual(audit['status'],'REPAIRABLE')
        self.assertIn('SENTENCE_EDGE_PROOF_MISSING_OR_STALE',audit['local_errors'])


if __name__=='__main__':unittest.main()
