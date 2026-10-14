"""Pure scoring regression; no media decode or detector invocation."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import adaptive_vision as vision


def synthetic(parts, *, missing=None, no_person_after=None, special=None, cadence=1):
    total = 1250
    lengths = [total//parts + (i < total%parts) for i in range(parts)]
    clips, samples = [], []
    record = 0
    for i, length in enumerate(lengths):
        source = 10000+i*2000
        clips.append({'source_start_frame': source, 'source_end_frame': source+length, 'fps': 25})
        for offset in sorted({0, length-1, *range(0, length, cadence)}):
            position = record+offset
            if missing and missing[0] <= position < missing[1]:
                continue
            label = ('NO_PERSON' if no_person_after is not None and position >= no_person_after
                     else special if special and position == 625 else 'HOST_VISIBLE')
            samples.append({'clip_index': i, 'frame': source+offset,
                            'classification': {'label': label, 'subject_evidence': label == 'HOST_VISIBLE'}})
        record += length
    return clips, samples


class RecordScoreV5Tests(unittest.TestCase):
    def test_same_visible_record_does_not_fall_with_more_cuts(self):
        scores = []
        old = []
        for parts in (1, 4, 25):
            clips, samples = synthetic(parts)
            current = vision.estimate(clips, samples)
            scores.append(current['host_visible_ratio_lower_bound'])
            old.append(vision.estimate_v4(clips, samples)['host_visible_ratio_lower_bound'])
            self.assertEqual(current['status'], 'PASS')
            self.assertEqual(current['evidence_level'], 'ENGINEERING_SAMPLED')
            self.assertFalse(current['strict_framewise_certified'])
        self.assertEqual(scores[0], scores[1])
        self.assertEqual(scores[1], scores[2])
        self.assertGreater(old[0], old[1])
        self.assertGreater(old[1], old[2])

    def test_large_nonperson_range_fails(self):
        clips, samples = synthetic(4, no_person_after=750)
        result = vision.estimate(clips, samples)
        self.assertAlmostEqual(result['host_visible_ratio_estimate'], .6)
        self.assertEqual(result['status'], 'REPAIRABLE')

    def test_long_gap_and_accumulated_short_gaps_fail(self):
        clips, samples = synthetic(4, missing=(400, 600))
        self.assertEqual(vision.estimate(clips, samples)['status'], 'UNCERTAIN')
        clips, samples = synthetic(1, cadence=40)
        result = vision.estimate(clips, samples)
        self.assertEqual(result['status'], 'UNCERTAIN')
        self.assertLess(result['host_visible_ratio_lower_bound'], .70)

    def test_uncertain_and_detector_error_block(self):
        for label, expected in [('UNCERTAIN', 'UNCERTAIN'), ('DETECTOR_ERROR', 'BLOCKED')]:
            with self.subTest(label=label):
                clips, samples = synthetic(4, special=label)
                self.assertEqual(vision.estimate(clips, samples)['status'], expected)

    def test_support_does_not_cross_source_seam(self):
        clips = [{'source_start_frame': 1000, 'source_end_frame': 1050, 'fps': 25},
                 {'source_start_frame': 2000, 'source_end_frame': 2050, 'fps': 25}]
        samples = [{'clip_index': 0, 'frame': f,
                    'classification': {'label': 'HOST_VISIBLE', 'subject_evidence': True}}
                   for f in (1000, 1049)]
        result = vision.estimate(clips, samples)
        self.assertEqual(result['status'], 'UNCERTAIN')
        self.assertEqual(result['host_visible_ratio_estimate'], .5)
        self.assertEqual(result['host_visible_ratio_lower_bound'], 0)

    def test_scoring_version_keeps_unchanged_frame_cache_identity(self):
        self.assertEqual(vision.CONTRACT, 'person-subject-record-strata-v5')
        self.assertEqual(vision.FRAME_CACHE_PROTOCOL, 'person-subject-strata-v4')
        self.assertEqual(vision.classifier_adapter_digest(), vision._V4_ADAPTER_SHA256)


if __name__ == '__main__':
    unittest.main()
