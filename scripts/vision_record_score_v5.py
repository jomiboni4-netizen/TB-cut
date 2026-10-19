"""Two-stage final-record person-presence gate; sample estimates are not certification."""
import json
import math
from fractions import Fraction
from pathlib import Path

from runtime_meta import CacheIdentityError


def _policy():
    path = Path(__file__).resolve().parents[1]/'config'/'vision_rules.json'
    data = json.loads(path.read_text(encoding='utf-8'))['person_audit']
    expected = {'initial_samples_per_variant', 'no_person_ratio_max_inclusive',
                'refinement_samples_per_flagged_stratum', 'refinement_window_ratio',
                'refinement_window_max_seconds', 'max_variant_duration_seconds',
                'closeup_duration_ratio_max_inclusive'}
    if set(data) != expected:
        raise CacheIdentityError('person_audit_policy_schema_invalid')
    if (data['initial_samples_per_variant'] < 1
            or data['refinement_samples_per_flagged_stratum'] < 1
            or not 0 < data['no_person_ratio_max_inclusive'] < 1
            or not 0 < data['refinement_window_ratio'] <= 1
            or data['refinement_window_max_seconds'] <= 0
            or data['max_variant_duration_seconds'] <= 0
            or not 0 <= data['closeup_duration_ratio_max_inclusive'] <= 1):
        raise CacheIdentityError('person_audit_policy_values_invalid')
    if not math.isclose(data['refinement_window_ratio'], 1/data['initial_samples_per_variant']):
        raise CacheIdentityError('refinement_window_must_match_one_initial_stratum')
    return dict(data)


CONTRACT = 'person-presence-two-stage-record-v7'
POLICY = _policy()
PERSON_LABELS = {'HOST_VISIBLE', 'PERSON_VISIBLE', 'PERSON_CLOSEUP', 'OTHER_PERSON'}
ABSENT_LABELS = {'NO_PERSON', 'CLOTHING_CLOSEUP'}


def variant_duration(clips):
    if not clips:
        raise CacheIdentityError('invalid_vision_physical_ranges')
    duration = Fraction()
    for clip in clips:
        lo, hi = clip['source_start_frame'], clip['source_end_frame']
        fps = Fraction(str(clip['fps']))
        if type(lo) is not int or type(hi) is not int or hi <= lo or fps <= 0:
            raise CacheIdentityError('invalid_vision_physical_ranges')
        duration += Fraction(hi-lo, 1)/fps
    if duration <= 0 or float(duration) > POLICY['max_variant_duration_seconds']:
        raise CacheIdentityError('vision_variant_duration_outside_sampling_policy')
    return duration


def initial_record_times(clips):
    """Ten deterministic midpoints across the rendered variant, independent of cut count."""
    duration = variant_duration(clips)
    count = POLICY['initial_samples_per_variant']
    return [duration*Fraction(2*i+1, 2*count) for i in range(count)]


def map_record_time(clips, record_time):
    """Map a rendered-record time to one exact source frame and physical clip."""
    position = Fraction()
    for index, clip in enumerate(clips):
        lo, hi = clip['source_start_frame'], clip['source_end_frame']
        fps = Fraction(str(clip['fps']))
        length = Fraction(hi-lo, 1)/fps
        if record_time < position+length or index == len(clips)-1:
            offset = max(Fraction(), min(length-Fraction(1, 1)/fps, record_time-position))
            frame = lo+int(offset*fps)
            return index, max(lo, min(hi-1, frame))
        position += length
    raise CacheIdentityError('vision_record_time_outside_variant')


def expected_refinement_times(clips, stratum_index):
    duration = variant_duration(clips)
    count = POLICY['initial_samples_per_variant']
    width = min(duration*Fraction(str(POLICY['refinement_window_ratio'])),
                Fraction(str(POLICY['refinement_window_max_seconds'])))
    center = duration*Fraction(2*stratum_index+1, 2*count)
    left = max(Fraction(), center-width/2)
    right = min(duration, left+width)
    # If clipped at the right edge, keep a full window by shifting it left.
    left = max(Fraction(), right-width)
    samples = POLICY['refinement_samples_per_flagged_stratum']
    return [left+width*Fraction(2*i+1, 2*samples) for i in range(samples)]


def _sample_map(samples, clips):
    count = POLICY['initial_samples_per_variant']
    by_stratum = {}
    seen = set()
    for sample in samples:
        kind = sample.get('sample_kind')
        index = sample.get('stratum_index')
        clip_index, frame = sample.get('clip_index'), sample.get('frame')
        if (kind not in {'initial', 'refinement'} or type(index) is not int or not 0 <= index < count
                or type(clip_index) is not int or not 0 <= clip_index < len(clips)
                or sample.get('source_file') != clips[clip_index]['source_file']
                or type(frame) is not int or not clips[clip_index]['source_start_frame'] <= frame < clips[clip_index]['source_end_frame']):
            raise CacheIdentityError('vision_sample_outside_or_duplicate')
        key = (clip_index, frame)
        if key in seen:
            raise CacheIdentityError('vision_sample_outside_or_duplicate')
        seen.add(key)
        by_stratum.setdefault((kind, index), []).append(sample)
    return by_stratum


def estimate(clips, samples):
    """Estimate no-person share with equal record-time strata and targeted refinement."""
    from adaptive_vision import validate_classification

    duration = variant_duration(clips)
    n = POLICY['initial_samples_per_variant']
    refinements_per = POLICY['refinement_samples_per_flagged_stratum']
    strata = _sample_map(samples, clips)
    if any(len(strata.get(('initial', i), [])) != 1 for i in range(n)):
        return {'status': 'UNCERTAIN', 'full_plan_coverage': False,
                'evidence_level': 'ENGINEERING_SAMPLED', 'strict_framewise_certified': False}

    initial = {}
    uncertain = errors = False
    no_person_initial = []
    for i in range(n):
        sample = strata[('initial', i)][0]
        label = validate_classification(sample['classification'])['label']
        initial[i] = label
        uncertain |= label == 'UNCERTAIN'
        errors |= label == 'DETECTOR_ERROR'
        if label in ABSENT_LABELS:
            no_person_initial.append(i)

    missing_refinements = [i for i in no_person_initial
                           if len(strata.get(('refinement', i), [])) != refinements_per]
    unexpected_refinements = [i for kind, i in strata
                              if kind == 'refinement' and i not in no_person_initial]
    if missing_refinements or unexpected_refinements:
        return {'status': 'BLOCKED' if unexpected_refinements else 'UNCERTAIN',
                'full_plan_coverage': False, 'evidence_level': 'ENGINEERING_SAMPLED',
                'strict_framewise_certified': False}

    no_person_ratio = closeup_ratio = Fraction()
    for i in range(n):
        width = Fraction(1, n)
        if i in no_person_initial:
            labels = [validate_classification(s['classification'])['label']
                      for s in strata[('refinement', i)]]
            uncertain |= 'UNCERTAIN' in labels
            errors |= 'DETECTOR_ERROR' in labels
            no_person_ratio += width*Fraction(sum(label in ABSENT_LABELS for label in labels), refinements_per)
            closeup_ratio += width*Fraction(sum(label == 'PERSON_CLOSEUP' for label in labels), refinements_per)
        else:
            no_person_ratio += width*(initial[i] in ABSENT_LABELS)
            closeup_ratio += width*(initial[i] == 'PERSON_CLOSEUP')

    presence_ratio = max(Fraction(), Fraction(1)-no_person_ratio)
    no_person_limit = Fraction(str(POLICY['no_person_ratio_max_inclusive']))
    closeup_limit = Fraction(str(POLICY['closeup_duration_ratio_max_inclusive']))
    no_person_value, closeup_value = float(no_person_ratio), float(closeup_ratio)
    presence_value = float(presence_ratio)
    status = ('BLOCKED' if errors else 'UNCERTAIN' if uncertain else
              'REPAIRABLE' if no_person_ratio > no_person_limit or closeup_ratio > closeup_limit else 'PASS')
    return {'person_presence_ratio_estimate': presence_value,
            'no_person_ratio_estimate': no_person_value,
            'no_person_ratio_max_inclusive': float(no_person_limit),
            'no_person_sample_count': sum(initial[i] in ABSENT_LABELS for i in range(n)),
            'no_person_refinement_count': len(no_person_initial)*refinements_per,
            'no_person_refinement_strata': no_person_initial,
            'closeup_duration_ratio_estimate': closeup_value,
            'closeup_duration_ratio_max_inclusive': float(closeup_limit),
            'closeup_sample_count': sum(s['classification']['label'] == 'PERSON_CLOSEUP' for s in samples),
            # Compatibility name retained; the value is a sampled presence estimate, not a confidence bound.
            'host_visible_ratio_estimate': presence_value,
            'full_plan_coverage': len(initial) == n and not missing_refinements,
            'person_subject_classified': not uncertain and not errors,
            'status': status,
            'evidence_level': 'ENGINEERING_SAMPLED',
            'strict_framewise_certified': False,
            'statistical_claim': '10 equal final-record strata; flagged strata refined by 10 samples; no framewise guarantee'}
