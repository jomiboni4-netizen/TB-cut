"""Synthetic two-stage person audit tests; no media decode or detector invocation."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import adaptive_vision as vision


def fixture(duration=60.0, *, clips=1):
    fps = 10
    lengths = [int(duration*fps/clips)]*clips
    physical = []
    source = 1000
    for length in lengths:
        physical.append({'source_file': f'{len(physical)}.mp4', 'source_start_frame': source,
                         'source_end_frame': source+length, 'fps': fps})
        source += length+1000
    samples = []
    for i, t in enumerate(vision.initial_record_times(physical)):
        ci, frame = vision.map_record_time(physical, t)
        samples.append(sample(physical, ci, frame, 'initial', i, 'PERSON_VISIBLE'))
    return physical, samples


def sample(clips, clip_index, frame, kind, stratum, label):
    return {'clip_index': clip_index, 'source_file': clips[clip_index]['source_file'],
            'frame': frame, 'sample_kind': kind, 'stratum_index': stratum,
            'classification': {'label': label, 'subject_evidence': label in
                               {'HOST_VISIBLE', 'PERSON_VISIBLE', 'PERSON_CLOSEUP', 'OTHER_PERSON'}}}


def flag_strata(clips, samples, strata, *, empty_counts=None):
    empty_counts = empty_counts or {}
    by_key = {(s['sample_kind'], s['stratum_index']): s for s in samples}
    for index in strata:
        initial = by_key[('initial', index)]
        initial['classification'] = {'label': 'NO_PERSON', 'subject_evidence': False}
        times = vision.expected_refinement_times(clips, index)
        for j, t in enumerate(times):
            ci, frame = vision.map_record_time(clips, t)
            label = 'NO_PERSON' if j < empty_counts.get(index, len(times)) else 'PERSON_VISIBLE'
            samples.append(sample(clips, ci, frame, 'refinement', index, label))
    return samples


class TwoStagePersonScoreTests(unittest.TestCase):
    def test_ten_person_samples_means_zero_sampled_no_person(self):
        clips, samples = fixture()
        result = vision.estimate(clips, samples)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['person_presence_ratio_estimate'], 1.0)
        self.assertEqual(result['no_person_ratio_estimate'], 0.0)
        self.assertFalse(result['strict_framewise_certified'])

    def test_one_empty_initial_stratum_refined_to_nine_percent_passes(self):
        clips, samples = fixture()
        result = vision.estimate(clips, flag_strata(clips, samples, [4], empty_counts={4: 9}))
        self.assertAlmostEqual(result['no_person_ratio_estimate'], .09)
        self.assertEqual(result['no_person_refinement_count'], 10)
        self.assertEqual(result['status'], 'PASS')

    def test_exactly_ten_percent_empty_is_allowed(self):
        clips, samples = fixture()
        result = vision.estimate(clips, flag_strata(clips, samples, [2]))
        self.assertAlmostEqual(result['no_person_ratio_estimate'], .10)
        self.assertEqual(result['status'], 'PASS')

    def test_multiple_empty_strata_are_accumulated_across_full_variant(self):
        clips, samples = fixture()
        result = vision.estimate(clips, flag_strata(clips, samples, [2, 7], empty_counts={2: 6, 7: 6}))
        self.assertAlmostEqual(result['no_person_ratio_estimate'], .12)
        self.assertEqual(result['status'], 'REPAIRABLE')

    def test_refinement_window_scales_for_shorter_variant(self):
        clips, _ = fixture(duration=45.0)
        times = vision.expected_refinement_times(clips, 5)
        self.assertAlmostEqual(float(times[-1]-times[0]), 4.05)
        self.assertEqual(len(times), 10)

    def test_equal_record_sampling_is_independent_of_physical_cut_count(self):
        one, _ = fixture(duration=50.0, clips=1)
        many, _ = fixture(duration=50.0, clips=5)
        a = [float(t) for t in vision.initial_record_times(one)]
        b = [float(t) for t in vision.initial_record_times(many)]
        self.assertEqual(a, b)
        self.assertEqual(len(a), 10)
        self.assertEqual(vision.map_record_time(many, vision.initial_record_times(many)[-1])[0], 4)

    def test_closeup_share_has_independent_configured_cap(self):
        clips, samples = fixture(duration=50.0)
        for index in range(3):
            samples[index]['classification'] = {'label': 'PERSON_CLOSEUP', 'subject_evidence': True}
        self.assertEqual(vision.estimate(clips, samples)['status'], 'PASS')
        samples[3]['classification'] = {'label': 'PERSON_CLOSEUP', 'subject_evidence': True}
        result = vision.estimate(clips, samples)
        self.assertAlmostEqual(result['closeup_duration_ratio_estimate'], .4)
        self.assertEqual(result['status'], 'REPAIRABLE')

    def test_missing_refinement_for_empty_sample_is_uncertain(self):
        clips, samples = fixture()
        samples[0]['classification'] = {'label': 'NO_PERSON', 'subject_evidence': False}
        self.assertEqual(vision.estimate(clips, samples)['status'], 'UNCERTAIN')

    def test_uncertain_and_detector_error_do_not_pass(self):
        for label, expected in [('UNCERTAIN', 'UNCERTAIN'), ('DETECTOR_ERROR', 'BLOCKED')]:
            with self.subTest(label=label):
                clips, samples = fixture()
                samples[0]['classification'] = {'label': label, 'subject_evidence': False}
                self.assertEqual(vision.estimate(clips, samples)['status'], expected)

    def test_contract_upgrades_without_changing_frame_classification_cache(self):
        self.assertEqual(vision.CONTRACT, 'person-presence-two-stage-record-v7')
        self.assertEqual(vision.FRAME_CACHE_PROTOCOL, 'person-presence-closeup-strata-v5')


if __name__ == '__main__':
    unittest.main()
