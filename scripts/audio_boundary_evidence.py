"""Physical signal observations; neither silence nor activity certifies words."""
import math

CONTRACT = 'cut-local-waveform-v2'


def _row(frame, step):
    if isinstance(frame, dict):
        start = frame.get('start')
        end = frame.get('end', (start + step) if isinstance(start, (int, float)) else None)
        rms = frame.get('rms_dbfs')
        return start, end, rms, frame.get('peak_dbfs'), frame.get('waveform_min'), frame.get('waveform_max')
    if isinstance(frame, (tuple, list)) and len(frame) >= 2:
        return frame[0], frame[0]+step, frame[1], None, None, None
    return None, None, None, None, None, None


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
    if (not all(math.isfinite(n) for n in (cut, threshold, step)) or step <= 0):
        return unknown
    rows = [_row(frame, step) for frame in frames]
    if any(not isinstance(row[0], (int, float)) or not isinstance(row[2], (int, float))
           or not math.isfinite(row[0]) or not math.isfinite(row[2]) for row in rows):
        return unknown
    left = [row for row in rows if row[0] + step/2 < cut]
    right = [row for row in rows if row[0] + step/2 >= cut]
    if not left or not right:
        return unknown
    left_row = max(left, key=lambda row: row[0])
    right_row = min(right, key=lambda row: row[0])
    if cut-left_row[0] > step*1.6 or right_row[0]-cut > step*1.6:
        return unknown
    crosses = left_row[2] > threshold and right_row[2] > threshold
    return dict(contract=CONTRACT,
                status='ACTIVITY_CROSSES_CUT' if crosses else 'NO_ACTIVITY_BRIDGE',
                activity_crosses_cut=crosses, left_dbfs=left_row[2], right_dbfs=right_row[2],
                waveform={'left': {'start': left_row[0], 'rms_dbfs': left_row[2], 'peak_dbfs': left_row[3],
                                   'min_sample': left_row[4], 'max_sample': left_row[5]},
                          'right': {'start': right_row[0], 'rms_dbfs': right_row[2], 'peak_dbfs': right_row[3],
                                    'min_sample': right_row[4], 'max_sample': right_row[5]}},
                truncation_confirmed=False, word_integrity='ASR_TOKEN_BOUNDARY_ESTIMATE')
