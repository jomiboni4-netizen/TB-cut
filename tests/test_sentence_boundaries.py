import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from sentence_boundaries import (anchored_words, bounded_edges, locate_subtitle_edge, verify_edges,
                                 nearest_asr_boundary, verify_cutpoint_proof, CUTPOINT_CONTRACT)
from refine_plans_v2 import refine_words, audit_audio_start, normalize_physical_clips


class SentenceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.words = [dict(start=1., end=1.5, text='上一句尾巴'),
                      dict(start=1.8, end=2.2, text='这件'),
                      dict(start=2.3, end=2.8, text='夹克'),
                      dict(start=2.9, end=3.5, text='穿着柔软'),
                      dict(start=3.9, end=4.5, text='下一句话')]

    def test_audio_grid_is_applied_before_recognition(self):
        from asr_identity import snap_interval
        lo, hi = snap_interval(114.933, 163.133, 1800.)
        self.assertAlmostEqual(lo, 114.92)
        self.assertAlmostEqual(hi, 163.14)
        self.assertLessEqual(lo, 114.933)
        self.assertGreaterEqual(hi, 163.133)
        self.assertEqual(snap_interval(0., 2.001, 2.001)[1], 2.001)

    def test_text_anchor_excludes_previous_tail(self):
        first, last, score = anchored_words(self.words, '这件夹克穿着柔软', 1.7, 3.6)
        self.assertEqual((first, last), (1, 3))
        self.assertEqual(score, 1.)

    def test_pause_cannot_select_another_opening(self):
        _, _, text, _ = refine_words({'words': self.words}, 1.7, 3.6)
        self.assertEqual(text, '这件夹克穿着柔软')

    def test_padding_cannot_invade_excluded_words(self):
        lo, hi, proof = bounded_edges(self.words, 1, 3, 25)
        self.assertGreaterEqual(lo/25, 1.5)
        self.assertLessEqual(hi/25, 3.9)
        self.assertTrue(verify_edges(dict(fps=25, source_start_frame=lo,
                                        source_end_frame=hi, words=self.words, sentence_edges=proof)))

    def test_stale_or_missing_proof_rejected(self):
        lo, hi, proof = bounded_edges(self.words, 1, 3, 25)
        clip = dict(fps=25, source_start_frame=lo-1, source_end_frame=hi,
                    words=self.words, sentence_edges=proof)
        self.assertFalse(verify_edges(clip))
        self.assertFalse(verify_edges({}))

    def test_no_safe_whole_frame_is_repairable(self):
        words = [dict(start=0., end=1.805, text='前句'),
                 dict(start=1.81, end=2.5, text='新句子完整')]
        lo, hi, proof = bounded_edges(words, 1, 1, 25)
        self.assertEqual(proof['alignment_status'], 'FRAME_ROUNDED_ESTIMATE')
        self.assertFalse(proof['phoneme_accuracy_certified'])
        self.assertEqual(words[1]['start'], 1.81)

    def test_overlapping_word_timestamps_not_rewritten(self):
        words = [dict(start=0., end=2., text='前句'),
                 dict(start=1.8, end=2.5, text='新句子完整')]
        with self.assertRaisesRegex(ValueError, 'overlapping_asr'):
            bounded_edges(words, 1, 1, 25)

    def test_missing_first_word_does_not_guess(self):
        with self.assertRaisesRegex(ValueError, 'edge_text_not_aligned'):
            anchored_words(self.words, '错误句首夹克穿着柔软', 1.7, 3.6)

    def test_caption_initial_substitution_keeps_complete_spoken_token(self):
        words = [dict(start=142.44, end=142.66, text='择'),
                 dict(start=142.66, end=143.14, text='就是'),
                 dict(start=143.14, end=143.46, text='我们'),
                 dict(start=143.46, end=143.84, text='连帽'),
                 dict(start=143.84, end=144.18, text='开插'),
                 dict(start=144.18, end=144.32, text='的'),
                 dict(start=144.32, end=144.56, text='一个'),
                 dict(start=144.56, end=144.98, text='披肩')]
        first, last, _ = locate_subtitle_edge(words, '这是我们连帽开叉的一个披肩', 143.1)
        self.assertEqual((first, last), (1, 7))
        lo, _, proof = bounded_edges(words, first, last, 25)
        self.assertLessEqual(abs(lo / 25 - words[first]['start']), .5 / 25 + 1e-8)

    def test_nearby_asr_boundary_uses_token_edges_without_subtitle_text(self):
        opening = nearest_asr_boundary(self.words, 2.25, 'opening')
        ending = nearest_asr_boundary(self.words, 3.4, 'ending')
        self.assertEqual(opening['time'], 2.3)
        self.assertEqual(ending['time'], 3.5)
        self.assertEqual(nearest_asr_boundary(self.words, 20., 'opening'), None)

    def test_codex_fallback_boundary_is_valid_without_asr_alignment(self):
        fps, lo, hi = 30., 54, 105
        clip = {'fps':fps,'source_start_frame':lo,'source_end_frame':hi,
                'audio_start_frame':lo,'audio_end_frame':hi,
                'cutpoint_review':{'status':'WAITING_FOR_MANUAL_EDGE_REVIEW',
                                   'pending_edges':['opening','ending']},
                'cutpoint_proof':{'contract':CUTPOINT_CONTRACT,'fps':fps,
                    'source_start_frame':lo,'source_end_frame':hi,
                    'chosen_start_time':lo/fps,'chosen_end_time':hi/fps,
                    'opening':{'requested_time':1.8,'status':'CODEX_FALLBACK',
                               'manual_review_required':True},
                    'ending':{'requested_time':3.5,'status':'ASR_UNAVAILABLE_FALLBACK',
                              'manual_review_required':True}}}
        self.assertTrue(verify_cutpoint_proof(clip))

    def test_contiguous_merge_preserves_word_edge_proof_and_record_length(self):
        words = [dict(start=.08, end=1.04, text='完整句子甲'),
                 dict(start=1.20, end=2.16, text='完整句子乙')]
        clips = []
        for n in (0, 1):
            lo, hi, proof = bounded_edges(words, n, n, 25)
            clips.append(dict(source_file='synthetic.mp4', fps=25, source_start_frame=lo,
                source_end_frame=hi, audio_start_frame=lo, audio_end_frame=hi,
                record_duration_frames=hi-lo, duration_sec=(hi-lo)/25, words=words,
                transcript=words[n]['text'], sentence_edges=proof, audio_boundary_verified=True))
        merged, count = normalize_physical_clips(clips)
        self.assertEqual(count, 1)
        self.assertTrue(verify_edges(merged[0]))
        self.assertEqual(merged[0]['transcript'], '完整句子甲完整句子乙')
        self.assertEqual(merged[0]['record_duration_frames'], 56)

    def test_default_audio_audit_never_extends_into_previous_sentence(self):
        levels = [(i/100, -50 if i < 160 else -10) for i in range(100, 191, 2)]
        with patch('refine_plans_v2.audio_level_frames', return_value=levels):
            adjusted, audit = audit_audio_start(Path('synthetic.mp4'), 1.8)
        self.assertEqual(adjusted, 1.8)
        self.assertTrue(audit['risk'])


if __name__ == '__main__':
    unittest.main()
