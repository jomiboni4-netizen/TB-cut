"""Shared evidence parents and invalidation edges; execution barriers stay explicit."""
PARENTS = {
    'range': (), 'candidate': ('range',), 'rough': ('candidate',),
    'semantic_review': ('rough',), 'selection_vision': ('rough', 'semantic_review'),
    'asr': ('rough',), 'plan': ('rough', 'asr'), 'vision': ('plan',),
    'final_audit': ('plan', 'vision'),
}
# A changed semantic verdict revokes execution eligibility; it cannot be bypassed
# merely because old ASR coordinates are computationally compatible.
INVALIDATION_PARENTS = {**PARENTS, 'asr': ('rough', 'semantic_review')}
EXECUTION_BARRIERS = {'asr': ('semantic_review', 'selection_vision')}


def descendants(stage):
    pending, found = [stage], set()
    while pending:
        parent = pending.pop()
        for child, parents in INVALIDATION_PARENTS.items():
            if parent in parents and child not in found:
                found.add(child)
                pending.append(child)
    return tuple(name for name in PARENTS if name in found)
