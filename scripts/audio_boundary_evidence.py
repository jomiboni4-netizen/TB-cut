"""Physical signal observations; neither silence nor activity certifies words."""
import math

CONTRACT = 'cut-local-activity-v1'


def activity_at_cut(frames, cut, threshold=-23., step=.02):
    """Use only complete bins immediately beside the exact cut.

    Later speech does not bridge a quiet cut. Floating-point noise must not
    move the first right-hand bin into the left-hand evidence. Missing or
    invalid bins remain unknown; an active bridge is a risk, not a verdict
    that a particular syllable has been cut.
    """
    unknown = dict(contract=CONTRACT, status='INSUFFICIENT_AUDIO',
                   activity_crosses_cut=None, truncation_confirmed=False,
                   word_integrity='UNVERIFIED')
    if (not all(math.isfinite(n) for n in (cut, threshold, step)) or step <= 0
            or any(not math.isfinite(n) for frame in frames for n in frame)):
        return unknown
    left = [db for at, db in frames if abs(at-(cut-step)) <= 1e-7]
    right = [db for at, db in frames if abs(at-cut) <= 1e-7]
    if len(left) != 1 or len(right) != 1:
        return unknown
    crosses = left[0] > threshold and right[0] > threshold
    return dict(contract=CONTRACT,
                status='ACTIVITY_CROSSES_CUT' if crosses else 'NO_ACTIVITY_BRIDGE',
                activity_crosses_cut=crosses, left_dbfs=left[0], right_dbfs=right[0],
                truncation_confirmed=False, word_integrity='UNVERIFIED')
