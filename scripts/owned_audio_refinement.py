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
from refine_plans_v2 import audio_waveform_frames
from sentence_boundaries import verify_edges

CONTRACT = 'owned-gap-local-audio-v3-clean-pause-else-owned-edge'


def choose_edge(levels, current, fps, lower, upper, *, frame_tolerance=0., max_shift=.16, threshold=-23.):
    """Inspect the waveform, then prefer silence or the lowest-energy word gap."""
    lo = max(math.ceil((lower-frame_tolerance-1e-9)*fps), math.ceil(current-max_shift*fps-1e-9))
    hi = min(math.floor((upper+frame_tolerance+1e-9)*fps), math.floor(current+max_shift*fps+1e-9))
    if lo > hi and lower-frame_tolerance-1e-9 <= current/fps <= upper+frame_tolerance+1e-9:
        lo = hi = current
    if lo > hi:
        return current, dict(status='REPAIRABLE', word_integrity='UNVERIFIED',
                             truncation_confirmed=False, reason='no_frame_between_adjacent_word_bounds',
                             legal_frame_bounds=[lo, hi])
    observed = []
    for frame in range(lo, hi+1):
        observation = activity_at_cut(levels, frame/fps)
        if observation['activity_crosses_cut'] is None:
            continue
        rms = max(observation['left_dbfs'], observation['right_dbfs'])
        peak_values = [row.get('peak_dbfs') for row in observation.get('waveform',{}).values()
                       if isinstance(row.get('peak_dbfs'),(int,float))]
        peak = max(peak_values) if peak_values else rms
        observed.append((frame, observation, rms, peak))
    if not observed:
        return current, dict(status='REPAIRABLE', word_integrity='UNVERIFIED',
                             truncation_confirmed=False, reason='waveform_bins_do_not_cover_legal_edge',
                             legal_frame_bounds=[lo, hi], waveform_bin_count=len(levels))
    quiet = [row for row in observed if row[2] <= threshold]
    if quiet:
        frame, observation, _, _ = min(quiet, key=lambda row: (
            abs(row[0]-current), row[2], row[3]))
        status = 'PASS_LOCAL_SILENCE'
    else:
        # No clean pause exists: use the midpoint between adjacent ASR word
        # bounds as the explicit fused-word compromise. Never chase a local
        # waveform trough, which may sit inside a syllable despite its lower
        # energy. If the midpoint is outside the bounded search window, leave
        # this edge for repair instead of inventing a cut.
        midpoint = round(((lower + upper) / 2) * fps)
        exact = [row for row in observed if row[0] == midpoint]
        if not exact:
            return current, dict(status='REPAIRABLE', word_integrity='UNVERIFIED',
                                 truncation_confirmed=False,
                                 reason='continuous_speech_midpoint_outside_bounded_search',
                                 legal_frame_bounds=[lo, hi], midpoint_frame=midpoint)
        frame, observation, _, _ = exact[0]
        status = 'PASS_CONTINUOUS_SPEECH_WORD_GAP'
    boundary_bins = [row for row in levels
                     if abs(((row.get('start') if isinstance(row,dict) else row[0]) + .01) - frame/fps) <= .07]
    waveform_excerpt = [{k: row[k] for k in ('start','end','rms_dbfs','peak_dbfs','waveform_min','waveform_max')}
                        for row in boundary_bins if isinstance(row,dict)]
    return frame, dict(status=status, observation=observation,
                       waveform_excerpt=waveform_excerpt,
                       waveform_method='20ms_rms_peak_sample_range',
                       word_integrity='ASR_TOKEN_GAP_ESTIMATE',
                       phoneme_accuracy_certified=False, truncation_confirmed=False,
                       fallback_used=status != 'PASS_LOCAL_SILENCE',
                       cut_policy='clean_pause_first_else_midpoint_of_adjacent_word_bounds',
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
            frame_tolerance = .5/fps if proof.get('alignment_status') == 'FRAME_ROUNDED_ESTIMATE' else 0.
            edges = {}
            for edge, field, lower, upper in [
                ('opening', 'source_start_frame', proof['previous_word_end'], proof['first_word_start']),
                ('ending', 'source_end_frame', proof['last_word_end'], proof['next_word_start'])]:
                current = clip[field]
                levels = audio_waveform_frames(Path(sources[clip['source_file']]),
                                               max(0., current/fps-.36), current/fps+.36)
                frame, result = choose_edge(levels, current, fps, lower, upper,
                                            frame_tolerance=frame_tolerance)
                clip[field] = proof[field] = frame
                edges[edge] = result
            clip['audio_start_frame'] = clip['source_start_frame']
            clip['audio_end_frame'] = clip['source_end_frame']
            clip['duration_sec'] = (clip['source_end_frame']-clip['source_start_frame'])/fps
            clip['record_duration_frames'] = clip['source_end_frame']-clip['source_start_frame']
            clip['audio_boundary_verified'] = all(e['status'] in (
                'PASS_LOCAL_SILENCE','PASS_CONTINUOUS_SPEECH_WORD_GAP') for e in edges.values())
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
