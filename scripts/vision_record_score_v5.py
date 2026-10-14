"""Record-time sampled-person engineering gate; never a framewise certification."""
import math
from fractions import Fraction

from runtime_meta import CacheIdentityError


CONTRACT = 'person-subject-record-strata-v5'
POLICY = {
    'points_per_minute': 16,
    'max_rounds': 5,
    'correlation_seconds': 2.0,
    'support_radius_seconds': 0.5,
    'max_unsampled_gap_seconds': 2.0,
    'z_reference': 1.645,
    'threshold_exclusive': 0.70,
    'seed': 'tbcut-physical-v3',
}


def estimate(clips, samples):
    """Score ordered final record time; support intervals never cross source clips."""
    from adaptive_vision import validate_classification

    if not clips:
        raise CacheIdentityError('invalid_vision_physical_ranges')
    spans = []
    for clip in clips:
        lo, hi = clip['source_start_frame'], clip['source_end_frame']
        fps = Fraction(str(clip['fps']))
        if hi <= lo or fps <= 0:
            raise CacheIdentityError('invalid_vision_physical_ranges')
        spans.append((lo, hi, fps))
    duration = sum((Fraction(hi-lo, 1)/fps for lo, hi, fps in spans), Fraction())
    if duration <= 0:
        raise CacheIdentityError('invalid_vision_physical_ranges')
    block = Fraction(str(POLICY['correlation_seconds']))
    radius = Fraction(str(POLICY['support_radius_seconds']))
    maximum_gap = Fraction(str(POLICY['max_unsampled_gap_seconds']))
    seen = set()
    for sample in samples:
        index, frame = sample['clip_index'], sample['frame']
        if (not isinstance(index, int) or not 0 <= index < len(spans)
                or not isinstance(frame, int)
                or not spans[index][0] <= frame < spans[index][1]
                or (index, frame) in seen):
            raise CacheIdentityError('vision_sample_outside_or_duplicate')
        seen.add((index, frame))

    ratio = 0.0
    unsupported = Fraction()
    coverage = True
    uncertain = errors = False
    record_start = Fraction()
    occupied = set()
    for i, (lo, hi, fps) in enumerate(spans):
        points = sorted((s for s in samples if s['clip_index'] == i), key=lambda s: s['frame'])
        clip_duration = Fraction(hi-lo, 1)/fps
        if not points:
            coverage = False
            unsupported += clip_duration
            record_start += clip_duration
            continue
        frames = [s['frame'] for s in points]
        edges = [lo]+[(a+b+1)/2 for a, b in zip(frames, frames[1:])]+[hi]
        for sample, left, right in zip(points, edges, edges[1:]):
            label = validate_classification(sample['classification'])['label']
            ratio += float(Fraction(str(right-left))/fps/duration) * (label == 'HOST_VISIBLE')
            uncertain |= label == 'UNCERTAIN'
            errors |= label == 'DETECTOR_ERROR'
            occupied.add(int((record_start + Fraction(sample['frame']-lo, 1)/fps)/block))
        radius_frames = int(radius*fps)
        supported_end = lo
        for frame in frames:
            left, right = max(lo, frame-radius_frames), min(hi, frame+radius_frames+1)
            unsupported += Fraction(max(0, left-supported_end), 1)/fps
            supported_end = max(supported_end, right)
        unsupported += Fraction(max(0, hi-supported_end), 1)/fps
        longest_missing = max(frames[0]-lo, hi-1-frames[-1],
                              *(b-a-1 for a, b in zip(frames, frames[1:])))
        coverage &= frames[0] == lo and frames[-1] == hi-1 and Fraction(longest_missing, 1)/fps <= maximum_gap
        record_start += clip_duration

    expected_blocks = (duration.numerator*block.denominator + duration.denominator*block.numerator-1)//(duration.denominator*block.numerator)
    coverage &= occupied == set(range(expected_blocks))
    n = len(occupied)
    z = POLICY['z_reference']
    ratio = min(1.0, max(0.0, ratio))
    reference = ((ratio+z*z/(2*n)-z*math.sqrt(ratio*(1-ratio)/n+z*z/(4*n*n)))/(1+z*z/n)
                 if n else 0.0)
    score = max(0.0, reference-float(unsupported/duration))
    status = ('BLOCKED' if errors else 'UNCERTAIN' if uncertain or not coverage else
              'PASS' if score > POLICY['threshold_exclusive'] else
              'REPAIRABLE' if ratio <= POLICY['threshold_exclusive'] else 'UNCERTAIN')
    return {'host_visible_ratio_estimate': ratio, 'host_visible_ratio_lower_bound': score,
            'full_plan_coverage': coverage, 'person_subject_classified': not uncertain and not errors,
            'status': status, 'evidence_level': 'ENGINEERING_SAMPLED',
            'strict_framewise_certified': False,
            'statistical_claim': 'fixed record-time blocks and local support; no IID or framewise confidence guarantee'}
