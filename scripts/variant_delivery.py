"""Project independently passing variants without changing retained clips or audits."""
import copy
import hashlib
import hmac

from project_registry import digest
from runtime_meta import CacheIdentityError
from product_checkpoints import load_stage, capture_stage, publish_stage

CONTRACT = 'passed-variant-projection-v1'


def projected_bodies(plan, vision, plan_ref, vision_ref):
    from final_quality_gate import plan_errors
    if plan.get('delivery_scope'):
        raise CacheIdentityError('variant_projection_already_scoped')
    rows = {row['variant_id']: row for row in vision['variants']}
    retained, pending = [], []
    for variant in plan['variants']:
        candidate = dict(plan, variants=[variant])
        errors = plan_errors(candidate)
        audit = rows[variant['variant_id']]
        if audit.get('status') == 'PASS' and not errors:
            retained.append(variant)
        else:
            pending.append({'variant_id': variant['variant_id'],
                            'status': audit.get('status') if not errors else 'REPAIRABLE',
                            'recovery_route': ('semantic_reselection' if errors or audit.get('status') == 'REPAIRABLE'
                                               else 'waiting_evidence'),
                            'errors': errors})
    if not retained:
        raise CacheIdentityError('no_independently_passed_variants')
    projected = copy.deepcopy(plan)
    projected['variants'] = copy.deepcopy(retained)
    projected['delivery_scope'] = {
        'contract': CONTRACT, 'source_plan_ref': plan_ref, 'source_vision_ref': vision_ref,
        'original_variant_ids': [v['variant_id'] for v in plan['variants']],
        'approved_variant_ids': [v['variant_id'] for v in retained],
        'pending_variants': pending, 'product_complete': not pending}
    errors = plan_errors(projected)
    if errors:
        raise CacheIdentityError('projected_variant_hard_veto:' + ','.join(errors))
    evidence = copy.deepcopy(vision)
    evidence.pop('seal', None)
    evidence['variants'] = [copy.deepcopy(rows[v['variant_id']]) for v in retained]
    evidence['plan_digest'] = digest(projected)
    evidence['projection'] = copy.deepcopy(projected['delivery_scope'])
    return projected, evidence


def verify_projection(paths, plan, evidence):
    scope = plan.get('delivery_scope')
    if not scope:
        if evidence.get('projection'):
            raise CacheIdentityError('vision_projection_without_plan_scope')
        return
    if scope.get('contract') != CONTRACT:
        raise CacheIdentityError('variant_projection_contract_invalid')
    source = load_stage(paths, scope['source_plan_ref'])
    original = load_stage(paths, scope['source_vision_ref'])
    if scope['source_plan_ref'] not in [d['path'] for d in original['identity']['dependencies']]:
        raise CacheIdentityError('variant_projection_source_not_bound')
    from adaptive_vision import verify
    verify(paths, source['body'], original['body'], require_pass=False)
    expected_plan, expected_vision = projected_bodies(
        source['body'], original['body'], scope['source_plan_ref'], scope['source_vision_ref'])
    if plan != expected_plan or {k: v for k, v in evidence.items() if k != 'seal'} != expected_vision:
        raise CacheIdentityError('variant_projection_content_changed')


def retain_passed_variants(paths, plan_ref, vision_ref):
    plan = load_stage(paths, plan_ref)
    vision = load_stage(paths, vision_ref)
    if plan_ref not in [d['path'] for d in vision['identity']['dependencies']]:
        raise CacheIdentityError('variant_projection_source_not_bound')
    from adaptive_vision import verify
    verify(paths, plan['body'], vision['body'], require_pass=False)
    if all(row.get('status') == 'PASS' for row in vision['body']['variants']):
        return plan_ref, vision_ref
    body, evidence = projected_bodies(plan['body'], vision['body'], plan_ref, vision_ref)
    shared = dict(product_id=plan['identity']['product_id'], encoding_id=plan['identity']['encoding_id'])
    args = dict(**shared, stage='plan', version=digest([CONTRACT, plan_ref, vision_ref]),
                model_version=plan['identity']['model_version'],
                dependencies=list(dict.fromkeys([d['path'] for d in plan['identity']['dependencies']]
                                                + [plan_ref, vision_ref])))
    projected_ref = publish_stage(paths, **args, body=body, snapshot=capture_stage(paths, **args))
    # Preserve and verify the original seal; this new seal attests only an exact
    # subset projection, whose original records remain immutable dependencies.
    from codex_worker import key
    evidence['seal'] = hmac.new(key(paths), digest(evidence).encode(), hashlib.sha256).hexdigest()
    verify_projection(paths, body, evidence)
    verify(paths, body, evidence)
    args = dict(**shared, stage='vision', version=digest([CONTRACT, projected_ref, vision_ref]),
                model_version=vision['identity']['model_version'],
                dependencies=[projected_ref, vision_ref])
    projected_vision = publish_stage(paths, **args, body=evidence, snapshot=capture_stage(paths, **args))
    return projected_ref, projected_vision
