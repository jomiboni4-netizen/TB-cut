"""Conservative stage code ownership. Dependency edges invalidate descendants.

v1 is a new identity namespace: legacy unified-store artifacts require explicit
recomputation; never rewrite their identities or migrate live records in place.
Monolithic producers remain whole-file dependencies until separately extracted.
"""
from pathlib import Path
from project_registry import digest
from runtime_meta import CacheIdentityError, _sha256

COMMON = ('product_checkpoints.py', 'stage_graph.py', 'stage_dependencies.py', 'evidence_operation.py',
          'runtime_meta.py', 'runtime_paths.py', 'project_registry.py',
          'project_editing_modes.py', 'trial_session.py', 'resolve_trial.py')
SEMANTIC = ('codex_worker.py', 'build_expanded_codex_request.py', 'sentence_boundaries.py',
            'semantic_evidence.py', 'semantic_snapshot.py', 'audit_mimo_v2.py', 'runtime_publication.py',
            'apply_range_overrides_v2.py', 'publish_confirmed_ranges.py')
VISION = ('adaptive_vision.py', 'vision_record_score_v5.py', 'apple_frame_probe.swift',
          'vision_contact_sheet.swift', 'compute_store.py', 'vision_recovery.py',
          'person_coverage_audit_v2.py', 'codex_worker.py', 'resource_limits.py')
ASR = ('windowed_asr_v2.py', 'asr_identity.py', 'asr_runtime.py', 'asr_worker.py',
       'owned_audio_refinement.py', 'audio_boundary_evidence.py', 'compute_store.py',
       'review_compute_cache.py', 'sentence_boundaries.py')
STAGES = {
    'range': ('registered_pipeline.py', 'refine_plans_v2.py', 'publish_confirmed_ranges.py', 'publish_semantic_ranges.py', *SEMANTIC),
    'candidate': ('registered_pipeline.py', *SEMANTIC),
    'rough': ('registered_pipeline.py', *SEMANTIC),
    'semantic_review': ('final_quality_gate.py', *SEMANTIC),
    'selection_vision': ('registered_pipeline.py', *VISION),
    'asr': ('registered_pipeline.py', *ASR),
    'plan': ('registered_pipeline.py', 'refine_plans_v2.py', 'variant_delivery.py',
             'adaptive_vision.py', 'compute_store.py', 'audio_boundary_evidence.py', 'sentence_boundaries.py'),
    'vision': ('registered_pipeline.py', 'variant_delivery.py', *VISION),
    'approved': ('final_quality_gate.py', 'variant_delivery.py', *SEMANTIC),
    'final_audit': ('final_quality_gate.py', 'variant_delivery.py', *SEMANTIC),
    'written': ('resolve_write_v2.py', 'delivery_resolve.py', 'resolve_session_identity.py',
                'resolve_connection.py', 'resolve_connection_doctor.py'),
    'export_verified': ('delivery_pipeline.py', 'delivery_resolve.py'),
}


def stage_code_digest(stage):
    if stage not in STAGES:
        raise CacheIdentityError('unknown_stage')
    from evidence_operation import current_operation
    operation = current_operation()
    if operation is not None and stage in operation.code_digests:
        return operation.code_digests[stage]
    folder = Path(__file__).parent
    names = sorted(set(COMMON + STAGES[stage]))
    value = digest({name: _sha256(folder / name) for name in names})
    if operation is not None:
        operation.code_digests[stage] = value
    return value
