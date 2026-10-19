"""Bounded physical refinement inside already selected, sentence-owned gaps.

Whisper times locate the retained/excluded words; local PCM locates quiet cuts.
Neither observation alone certifies speech. This is a local risk gate, never a
phoneme-accuracy claim or semantic approval. Unresolved edges fail closed.
"""
from copy import deepcopy
import math
from pathlib import Path

from audio_boundary_evidence import activity_at_cut
from project_registry import digest
from refine_plans_v2 import audio_level_frames
from sentence_boundaries import verify_edges

CONTRACT = 'owned-gap-local-audio-v1'


def choose_edge(levels, current, fps, lower, upper, *, max_shift=.16, threshold=-23.):
    """Choose only legal frames; never move a word or widen into another one."""
    lo = max(math.ceil((lower-1e-9)*fps), math.ceil(current-max_shift*fps-1e-9))
    hi = min(math.floor((upper+1e-9)*fps), math.floor(current+max_shift*fps+1e-9))
    candidates = []
    # An existing half-frame estimate can remain only with local quiet evidence;
    # it never grants permission to widen the legal search envelope.
    frames = set(range(lo, hi+1)) | {current}
    for frame in sorted(frames):
        observation = activity_at_cut(levels, frame/fps)
        if observation['activity_crosses_cut'] is None:
            continue
        quiet = max(observation['left_dbfs'], observation['right_dbfs']) <= threshold
        if quiet:
            candidates.append((abs(frame-current), frame, observation))
    if not candidates:
        return current, dict(status='REPAIRABLE', word_integrity='UNVERIFIED',
                             truncation_confirmed=False, reason='no_quiet_owned_frame',
                             legal_frame_bounds=[lo, hi])
    _, frame, observation = min(candidates, key=lambda r: (r[0], max(r[2]['left_dbfs'], r[2]['right_dbfs'])))
    return frame, dict(status='PASS_LOCAL_RISK_GATE', observation=observation,
                       word_integrity='OWNED_WORD_ALIGNMENT_AND_LOCAL_QUIET',
                       phoneme_accuracy_certified=False, truncation_confirmed=False,
                       legal_frame_bounds=[lo, hi], shift_frames=frame-current)


def refine(plan, sources):
    updated = deepcopy(plan)
    rows = []
    for variant in updated['variants']:
        for clip in variant['clips']:
            if not verify_edges(clip):
                raise ValueError('owned_audio_sentence_proof_stale')
            proof = clip['sentence_edges']
            fps = clip['fps']
            edges = {}
            for edge, field, lower, upper in [
                ('opening', 'source_start_frame', proof['previous_word_end'], proof['first_word_start']),
                ('ending', 'source_end_frame', proof['last_word_end'], proof['next_word_start'])]:
                current = clip[field]
                levels = audio_level_frames(Path(sources[clip['source_file']]),
                                            max(0., current/fps-.36), current/fps+.36)
                frame, result = choose_edge(levels, current, fps, lower, upper)
                clip[field] = proof[field] = frame
                edges[edge] = result
            clip['audio_start_frame'] = clip['source_start_frame']
            clip['audio_end_frame'] = clip['source_end_frame']
            clip['duration_sec'] = (clip['source_end_frame']-clip['source_start_frame'])/fps
            clip['record_duration_frames'] = clip['source_end_frame']-clip['source_start_frame']
            clip['audio_boundary_verified'] = all(e['status']=='PASS_LOCAL_RISK_GATE' for e in edges.values())
            clip['owned_audio_review'] = dict(contract=CONTRACT, edges=edges,
                word_context_digest=proof['word_context_digest'],
                exact_frames=[clip['source_start_frame'],clip['source_end_frame']])
            if not verify_edges(clip):
                raise ValueError('owned_audio_refinement_left_sentence_envelope')
            rows.append(dict(variant_id=variant['variant_id'],unit_id=clip['unit_id'],**clip['owned_audio_review']))
        variant['duration_seconds'] = sum(c['duration_sec'] for c in variant['clips'])
    from final_quality_gate import plan_errors
    updated['local_errors'] = plan_errors(updated)
    return updated, dict(contract=CONTRACT, parent_plan_digest=digest(plan),
        plan_digest=digest(updated), status='PASS' if not updated['local_errors'] else 'REPAIRABLE',
        local_errors=updated['local_errors'],edges=rows,phoneme_accuracy_certified=False)
