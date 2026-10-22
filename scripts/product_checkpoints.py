"""Immutable product stage results and verified, resumable checkpoint records.

This foundation records evidence. It does not perform or replace final hard audits.
"""
import json
from pathlib import Path
from project_registry import ProjectIdentityError, atomic_json, checked_project, digest, workspace_lock
from runtime_meta import CacheIdentityError, expected_identity, _sha256

STATUSES = frozenset({'PENDING', 'IN_PROGRESS', 'REPAIRABLE', 'BLOCKED', 'FINAL_PASS',
                      'WRITTEN', 'EXPORT_VERIFIED'})
PASS_STAGES = {'FINAL_PASS': 'final_audit', 'WRITTEN': 'written', 'EXPORT_VERIFIED': 'export_verified'}


def context(paths, product_id, encoding_id, stage):
    if type(product_id) is not int or product_id <= 0 or not isinstance(encoding_id, str) or not encoding_id.strip():
        raise CacheIdentityError('product_identity_required')
    state = paths.load_state()
    checked_project(paths, state)
    if product_id not in state['batch']['product_ids']:
        raise CacheIdentityError('product_outside_current_batch')
    identity = expected_identity(paths, include_ranges=False)
    value = {k: identity[k] for k in ('identity_contract', 'project_key', 'config_digest',
                                     'upstream_fingerprint', 'rules_fingerprint', 'pipeline_version')}
    value.update({'product_id': product_id, 'encoding_id': encoding_id,
                  'store_version': digest([_sha256(Path(__file__).parent / name) for name in
                                          ('product_checkpoints.py', 'final_quality_gate.py', 'codex_worker.py', 'registered_pipeline.py',
                                           'refine_plans_v2.py', 'audio_boundary_evidence.py', 'audit_mimo_v2.py', 'build_expanded_codex_request.py',
                                           'person_coverage_audit_v2.py', 'windowed_asr_v2.py', 'review_compute_cache.py',
                                           'adaptive_vision.py', 'vision_record_score_v5.py', 'apple_frame_probe.swift',
                                           'compute_store.py', 'asr_identity.py',
                                          'asr_runtime.py','asr_worker.py','trial_session.py','resolve_trial.py',
                                          'owned_audio_refinement.py')]),
                  'processing_policy': digest(state.get('batch_overrides', {})),
                  'stage_policy': digest({k: state.get('runtime', {}).get(k, {}).get(stage)
                                          for k in ('stage_versions', 'model_versions')})})
    if state.get('trial'):
        from trial_session import validate_trial
        value['trial_scope'] = digest(validate_trial(paths))
    if stage in PASS_STAGES.values():
        value['batch_id'] = state['batch']['batch_id']
        value['batch_scope'] = paths.registered_scope()[1]
    return value


def stage_path(paths, identity):
    product = digest([identity['product_id'], identity['encoding_id']])
    return paths.project_records_dir / 'products' / product / identity['stage'] / (digest(identity) + '.json')


def current_plan_path(paths, product_id, encoding_id):
    return paths.project_records_dir / 'products' / digest([product_id, encoding_id]) / 'current_plan.json'


def current_range_path(paths, product_id, encoding_id):
    return paths.project_records_dir / 'products' / digest([product_id, encoding_id]) / 'current_range.json'


def check_current_range(paths, reference, value):
    identity = value['identity']
    if identity['stage'] != 'range':
        return
    pointer = current_range_path(paths, identity['product_id'], identity['encoding_id'])
    if (Path(str(pointer) + '.invalid').exists() or not pointer.is_file()
            or json.loads(pointer.read_text(encoding='utf-8')) != {'reference': reference, 'body_digest': value['body_digest']}):
        raise CacheIdentityError('stage_range_dependency_not_current')


def load_stage(paths, reference, *, product_id=None, encoding_id=None, _visited=None):
    artifact = paths.resolve_reference(reference).path
    records = paths.project_records_dir.resolve()
    if records not in artifact.parents:
        raise CacheIdentityError('stage_outside_project_records')
    visited = set() if _visited is None else set(_visited)
    if artifact in visited:
        raise CacheIdentityError('stage_dependency_cycle')
    visited.add(artifact)
    value = json.loads(artifact.read_text(encoding='utf-8'))
    if value.get('schema_version') != 2 or not isinstance(value.get('identity'), dict):
        raise CacheIdentityError('stage_schema_invalid_legacy_blocked')
    identity = value['identity']
    current = context(paths, identity['product_id'], identity['encoding_id'], identity['stage'])
    if any(identity.get(k) != v for k, v in current.items()):
        raise CacheIdentityError('stage_context_stale')
    if product_id is not None and identity['product_id'] != product_id:
        raise CacheIdentityError('stage_product_mismatch')
    if encoding_id is not None and identity['encoding_id'] != encoding_id:
        raise CacheIdentityError('stage_encoding_mismatch')
    if artifact != stage_path(paths, identity).resolve() or value.get('body_digest') != digest(value['body']):
        raise CacheIdentityError('stage_digest_or_location_mismatch')
    for dep in identity['dependencies']:
        parent = load_stage(paths, dep['path'], product_id=identity['product_id'], encoding_id=identity['encoding_id'], _visited=visited)
        check_current_range(paths, dep['path'], parent)
        if _sha256(paths.resolve_reference(dep['path']).path) != dep['sha256']:
            raise CacheIdentityError('stage_dependency_changed')
    return value


def capture_stage(paths, *, product_id, encoding_id, stage, version, model_version, dependencies=()):
    """Capture before computation; never bind an old result to new inputs afterward."""
    identity = context(paths, product_id, encoding_id, stage)
    deps = []
    for ref in dependencies:
        parent = load_stage(paths, ref, product_id=product_id, encoding_id=encoding_id)
        check_current_range(paths, ref, parent)
        deps.append({'path': ref, 'sha256': _sha256(paths.resolve_reference(ref).path)})
    identity.update({'stage': stage, 'version': str(version), 'model_version': str(model_version), 'dependencies': deps})
    return identity


def publish_stage(paths, *, product_id, encoding_id, stage, version, model_version, body, dependencies=(), snapshot=None):
    if stage not in {'range', 'candidate', 'rough', 'semantic_review', 'plan', 'asr', 'vision', 'approved', *PASS_STAGES.values()}:
        raise CacheIdentityError('unknown_stage')
    if not version or not model_version:
        raise CacheIdentityError('stage_and_model_version_required')
    if snapshot is None:
        raise CacheIdentityError('stage_precompute_snapshot_required')
    with workspace_lock(paths):
        identity = context(paths, product_id, encoding_id, stage)
        runtime = paths.load_state().get('runtime', {})
        for key, provided in (('stage_versions', version), ('model_versions', model_version)):
            configured = runtime.get(key, {}).get(stage)
            if configured is not None and str(configured) != str(provided):
                raise CacheIdentityError('configured_stage_or_model_version_mismatch')
        deps = []
        for ref in dependencies:
            parent = load_stage(paths, ref, product_id=product_id, encoding_id=encoding_id)
            check_current_range(paths, ref, parent)
            deps.append({'path': ref, 'sha256': _sha256(paths.resolve_reference(ref).path)})
        if stage in ('candidate', 'plan') and not any(
                load_stage(paths, d['path'])['identity']['stage'] == 'range' for d in deps):
            raise CacheIdentityError('product_stage_requires_current_range_dependency')
        identity.update({'stage': stage, 'version': str(version), 'model_version': str(model_version),
                         'dependencies': deps})
        if identity != snapshot:
            raise CacheIdentityError('stage_inputs_changed_since_computation')
        value = {'schema_version': 2, 'identity': identity, 'body': body, 'body_digest': digest(body)}
        output = stage_path(paths, identity)
        pointer = (current_range_path(paths, product_id, encoding_id) if stage == 'range'
                   else current_plan_path(paths, product_id, encoding_id))
        marker = Path(str(pointer) + '.invalid')
        if stage in ('plan', 'range') and marker.exists():
            raise CacheIdentityError('current_plan_publication_incomplete')
        if output.exists():
            existing = load_stage(paths, paths.reference(output), product_id=product_id, encoding_id=encoding_id)
            if existing != value:
                raise CacheIdentityError('immutable_stage_conflict_use_new_version')
            if stage in ('range', 'plan'):
                # An immutable artifact can outlive a missing or stale current
                # pointer. Re-publishing must restore the pointer after validating
                # the existing artifact against the current stage context.
                reference = paths.reference(output)
                expected_pointer = {'reference': reference, 'body_digest': value['body_digest']}
                if not pointer.is_file() or json.loads(pointer.read_text(encoding='utf-8')) != expected_pointer:
                    def check_existing():
                        current = load_stage(paths, reference, product_id=product_id,
                                             encoding_id=encoding_id)
                        if current != value:
                            raise CacheIdentityError('immutable_stage_changed_before_pointer_recovery')
                        if marker.exists():
                            raise CacheIdentityError('current_plan_publication_incomplete')
                    atomic_json(pointer, expected_pointer, before_commit=check_existing)
            return paths.reference(output)
        def check():
            if any(identity.get(k) != v for k, v in context(paths, product_id, encoding_id, stage).items()):
                raise CacheIdentityError('stage_inputs_changed_during_publication')
            for dep in deps:
                load_stage(paths, dep['path'], product_id=product_id, encoding_id=encoding_id)
                if _sha256(paths.resolve_reference(dep['path']).path) != dep['sha256']:
                    raise CacheIdentityError('stage_dependency_changed')
        if stage in ('plan', 'range'):
            pointer.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text('plan publication incomplete; explicit recovery required\n')
        atomic_json(output, value, before_commit=check)
        load_stage(paths, paths.reference(output), product_id=product_id, encoding_id=encoding_id)
        if stage in ('plan', 'range'):
            atomic_json(pointer, {'reference': paths.reference(output), 'body_digest': value['body_digest']},
                        before_commit=check)
            check()
            marker.unlink()
        return paths.reference(output)


def verify_evidence(paths, product_id, encoding_id, plan_ref, evidence_ref, status):
    plan = load_stage(paths, plan_ref, product_id=product_id, encoding_id=encoding_id)
    if plan['identity']['stage'] != 'plan':
        raise CacheIdentityError('checkpoint_requires_final_plan')
    pointer = current_plan_path(paths, product_id, encoding_id)
    if Path(str(pointer) + '.invalid').exists():
        raise CacheIdentityError('current_plan_publication_incomplete')
    if (not pointer.is_file() or json.loads(pointer.read_text(encoding='utf-8')) !=
            {'reference': plan_ref, 'body_digest': plan['body_digest']}):
        raise CacheIdentityError('checkpoint_plan_is_not_current')
    if status not in PASS_STAGES:
        return plan
    evidence = load_stage(paths, evidence_ref, product_id=product_id, encoding_id=encoding_id)
    stage = PASS_STAGES[status]
    body = evidence['body']
    if (evidence['identity']['stage'] != stage or not isinstance(body, dict)
            or body.get('plan_digest') != plan['body_digest']):
        raise CacheIdentityError('checkpoint_evidence_plan_mismatch')
    required = plan_ref
    if status != 'FINAL_PASS':
        previous = 'FINAL_PASS' if status == 'WRITTEN' else 'WRITTEN'
        required = body.get('previous_evidence')
        if not required:
            raise CacheIdentityError('checkpoint_evidence_chain_missing')
        verify_evidence(paths, product_id, encoding_id, plan_ref, required, previous)
    if required not in [d['path'] for d in evidence['identity']['dependencies']]:
        raise CacheIdentityError('checkpoint_evidence_dependency_missing')
    if body.get('verdict') != ('PASS' if status == 'FINAL_PASS' else 'VERIFIED'):
        raise CacheIdentityError('checkpoint_evidence_not_passed')
    if status == 'FINAL_PASS' and body.get('audit_scope') not in (
            'final_plan', 'semantic_selection_plus_local_hard_gates'):
        raise CacheIdentityError('selection_pass_is_not_final_pass')
    if (status == 'FINAL_PASS'
            and body.get('audit_scope') == 'semantic_selection_plus_local_hard_gates'
            and not body.get('semantic_review_ref')):
        raise CacheIdentityError('semantic_selection_final_evidence_missing')
    if status == 'FINAL_PASS':
        from final_quality_gate import verify_final_record
        verify_final_record(paths, plan_ref, body)
    return plan


def checkpoint_path(paths):
    return paths.project_records_dir / 'checkpoints.json'


def read_ledger(paths):
    path = checkpoint_path(paths)
    if not path.exists():
        return {'schema_version': 2, 'project_key': paths.registered_scope()[0], 'revision': 0, 'products': {}}
    ledger = json.loads(path.read_text(encoding='utf-8'))
    if (ledger.get('schema_version') != 2 or ledger.get('project_key') != paths.registered_scope()[0]
            or type(ledger.get('revision')) is not int or not isinstance(ledger.get('products'), dict)):
        raise CacheIdentityError('checkpoint_legacy_or_identity_mismatch')
    return ledger


def resume(paths, product_ids=None):
    """Read-only revalidation: stale evidence is BLOCKED, interrupted work resumes."""
    state = paths.load_state()
    checked_project(paths, state)
    ledger = read_ledger(paths)
    results = {}
    selected = state['batch']['product_ids'] if product_ids is None else list(product_ids)
    if not set(selected).issubset(state['batch']['product_ids']):
        raise CacheIdentityError('resume_product_outside_target_set')
    for pid in selected:
        row = ledger['products'].get(str(pid))
        if not row:
            results[pid] = {'status': 'PENDING'}
            continue
        row = dict(row)
        try:
            if row.get('status') not in STATUSES:
                raise CacheIdentityError('checkpoint_status_invalid')
            if row['context'] != context(paths, pid, row['encoding_id'], PASS_STAGES.get(row['status'], 'plan')):
                raise CacheIdentityError('checkpoint_context_stale')
            if row['plan_ref']:
                plan = verify_evidence(paths, pid, row['encoding_id'], row['plan_ref'], row['evidence_ref'], row['status'])
                if plan['body_digest'] != row['plan_digest']:
                    raise CacheIdentityError('checkpoint_plan_changed')
            elif row['status'] in PASS_STAGES:
                raise CacheIdentityError('checkpoint_plan_missing')
            if row['status'] == 'IN_PROGRESS':
                row['resume_required'] = True
        except (ValueError, KeyError, OSError, TypeError) as exc:
            row.update({'status': 'BLOCKED', 'reason': str(exc)})
        results[pid] = row
    return results


def update_checkpoint(paths, *, product_id, encoding_id, status, plan_ref=None, evidence_ref=None,
                      expected_revision=None, reason=None):
    if status not in STATUSES:
        raise CacheIdentityError('checkpoint_status_invalid')
    with workspace_lock(paths):
        ledger_file = checkpoint_path(paths)
        ledger_bytes = ledger_file.read_bytes() if ledger_file.exists() else None
        ledger = read_ledger(paths)
        if type(expected_revision) is not int or expected_revision != ledger['revision']:
            raise CacheIdentityError('checkpoint_revision_conflict')
        captured = context(paths, product_id, encoding_id, PASS_STAGES.get(status, 'plan'))
        if status in PASS_STAGES and (not plan_ref or not evidence_ref):
            raise CacheIdentityError('checkpoint_pass_requires_plan_and_evidence')
        plan = verify_evidence(paths, product_id, encoding_id, plan_ref, evidence_ref, status) if plan_ref else None
        old = ledger['products'].get(str(product_id))
        if old and old['encoding_id'] != encoding_id:
            raise CacheIdentityError('checkpoint_product_encoding_changed')
        row = {'status': status, 'encoding_id': encoding_id, 'plan_ref': plan_ref,
               'plan_digest': plan['body_digest'] if plan else None, 'evidence_ref': evidence_ref,
               'context': captured, 'reason': reason}
        # A retry of the same verified result is not a second completion.
        if old != row:
            ledger['products'][str(product_id)] = row
            ledger['revision'] += 1
            def check():
                if (ledger_file.read_bytes() if ledger_file.exists() else None) != ledger_bytes:
                    raise CacheIdentityError('checkpoint_changed_before_publication')
                if context(paths, product_id, encoding_id, PASS_STAGES.get(status, 'plan')) != captured:
                    raise CacheIdentityError('checkpoint_inputs_changed')
                if plan_ref:
                    verify_evidence(paths, product_id, encoding_id, plan_ref, evidence_ref, status)
            atomic_json(checkpoint_path(paths), ledger, before_commit=check)
        return ledger['revision']
