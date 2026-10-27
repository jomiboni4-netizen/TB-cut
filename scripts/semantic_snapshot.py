"""Freeze semantic review input from the exact subtitle task seen at selection."""
from copy import deepcopy
from project_registry import digest
from runtime_meta import CacheIdentityError

CONTRACT = 'selection-subtitle-snapshot-v1'


def assemble(task, selected_ranges):
    unsigned = {k: v for k, v in task.items() if k != 'task_digest'}
    if task.get('role') != 'selection' or task.get('task_digest') != digest(unsigned):
        raise CacheIdentityError('semantic_snapshot_selection_task_invalid')
    by_id = {row['candidate_id']: row for row in task['candidates']}
    grouped = {}
    for selected in selected_ranges:
        source = by_id.get(selected['candidate_id'])
        if source is None:
            raise CacheIdentityError('semantic_snapshot_selected_candidate_missing')
        lo, hi = float(selected['start']), float(selected['end'])
        cues = sorted(source.get('subtitle', []), key=lambda cue: (float(cue['start']), float(cue['end'])))
        chosen = [(i, cue) for i, cue in enumerate(cues)
                  if not cue.get('context_only') and float(cue['end']) > lo and float(cue['start']) < hi]
        if not chosen:
            raise CacheIdentityError('semantic_snapshot_selected_subtitles_missing')
        first, last = chosen[0][0], chosen[-1][0]
        text = ''.join(str(cue.get('text') or '') for _, cue in chosen)
        clips = grouped.setdefault(selected['variant_id'], [])
        clips.append({'unit_id': f"{selected['variant_id']}_{len(clips)+1}",
            'candidate_id': selected['candidate_id'], 'source_file': source['source_file'],
            'start': lo, 'end': hi, 'transcript': text, 'source_subtitle_text': text,
            'previous_source_context': ''.join(str(cue.get('text') or '') for cue in cues[max(0, first-2):first]),
            'following_source_context': ''.join(str(cue.get('text') or '') for cue in cues[last+1:last+3])})
    value = {'contract': CONTRACT, 'selection_task_digest': task['task_digest'],
             'candidate_digest': task['candidate_digest'], 'selected_ranges_digest': digest(selected_ranges),
             'variants': [{'variant_id': key, 'clips': clips} for key, clips in sorted(grouped.items())],
             'source_candidates': [deepcopy(row) for row in task['candidates']
                                   if row['candidate_id'] in {selected['candidate_id'] for selected in selected_ranges}]}
    return deepcopy(value)


def verify(rough):
    snapshot = rough.get('semantic_snapshot')
    if not isinstance(snapshot, dict) or snapshot != assemble(rough['selection_task'], rough['selected_ranges']):
        raise CacheIdentityError('semantic_snapshot_missing_or_changed')
    return deepcopy(snapshot)


def compact_selection_task(task, *, target_bytes=110000):
    """Remove reconstructible transport duplicates, never subtitle evidence."""
    import json
    size = lambda value: len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
    if size(task) <= target_bytes:
        return task
    compact = deepcopy(task)
    compact.pop('task_digest', None)
    for candidate in compact.get('candidates', []):
        for cue in candidate.get('subtitle', []):
            if cue.get('context_only') is False:
                del cue['context_only']  # The documented default is false.
    if size(compact) > target_bytes:
        for candidate in compact.get('candidates', []):
            original = candidate.get('asr_text')
            reconstructed = ''.join(str(cue.get('text') or '') for cue in candidate.get('subtitle', []))
            if isinstance(original, str) and original == reconstructed:
                del candidate['asr_text']
    compact['subtitle_transport_policy'] = (
        'verbatim-subtitle-no-duplicate-text-v1: subtitle retains every original cue, time and text; '
        'omitted context_only means false; redundant asr_text equals the exact concatenation of subtitle text. '
        'Read subtitle for all source meaning and quote evidence. No source cues were removed or summarized.')
    return compact
