"""Exact-plan local veto, independent Codex review import, and write preflight."""
from itertools import combinations
import math
from pathlib import Path
from project_registry import digest
from runtime_meta import CacheIdentityError, _sha256
from product_checkpoints import load_stage, capture_stage, publish_stage
from codex_worker import verify_receipt
from sentence_boundaries import verify_edges, CONTRACT as SENTENCE_CONTRACT
from audit_mimo_v2 import FORBIDDEN, semantic_duplicate
from semantic_evidence import FORBIDDEN as CANDIDATE_FORBIDDEN

CONTRACT = 'final-gate-v11-semantic-before-local-audits'


def plan_errors(plan):
    errors = []
    editing_policy = plan.get('editing_policy') or {}
    duration_limits = editing_policy.get('duration_exclusive_seconds', plan.get('duration_exclusive_seconds', [45.0, 60.0]))
    if (not isinstance(duration_limits, list) or len(duration_limits) != 2
            or not all(isinstance(value, (int, float)) for value in duration_limits)
            or not 0 <= duration_limits[0] < duration_limits[1] <= 60):
        duration_limits = [45.0, 60.0]
    variants = plan.get('variants', [])
    if not 1 <= len(variants) <= 3 or len({v.get('variant_id') for v in variants}) != len(variants):
        return ['VARIANT_SET_INVALID']
    target_count = editing_policy.get('target_variant_count')
    if target_count is not None:
        allow_fewer = editing_policy.get('allow_fewer_variants_when_exhausted') is True
        if (allow_fewer and len(variants) > target_count) or (not allow_fewer and len(variants) != target_count):
            errors.append('VARIANT_COUNT_DOES_NOT_MATCH_PROJECT_MODE')
    all_clips = []
    for variant in variants:
        clips = variant.get('clips', [])
        if not clips:
            errors.append('EMPTY_VARIANT')
        duration = 0.0
        for clip in clips:
            fps = clip.get('fps')
            start, end = clip.get('source_start_frame'), clip.get('source_end_frame')
            if (not isinstance(fps, (int, float)) or not math.isfinite(fps) or fps <= 0
                    or type(start) is not int or type(end) is not int or start < 0 or end <= start):
                errors.append('INVALID_SOURCE_FRAMES')
                continue
            duration += (end - start) / fps
            if not verify_edges(clip):
                errors.append('SENTENCE_EDGE_PROOF_MISSING_OR_STALE')
            if (clip.get('audio_start_frame'), clip.get('audio_end_frame')) != (start, end):
                errors.append('AV_RANGE_MISMATCH')
            words = clip.get('words')
            if not words:
                errors.append('RETAINED_WORD_ALIGNMENT_MISSING')
                continue
            lo, hi = start / fps, end / fps
            proof = clip.get('sentence_edges') or {}
            if verify_edges(clip):
                kept = [words[proof['first_word_index']], words[proof['last_word_index']]]
                tolerance = .5/fps if proof.get('alignment_status') == 'FRAME_ROUNDED_ESTIMATE' else 0.
            else:
                kept = []
                tolerance = 0.
            if not kept or any(w['start'] < lo-tolerance-1e-9 or w['end'] > hi+tolerance+1e-9 for w in kept):
                errors.append('WORD_CUT_AT_OPENING_ENDING_OR_SEAM')
            text = str(clip.get('source_subtitle_text') or clip.get('transcript') or '')
            if not text:
                errors.append('RETAINED_SUBTITLE_TEXT_MISSING')
            if FORBIDDEN.search(text) or CANDIDATE_FORBIDDEN.search(text):
                errors.append('BANNED_CONTENT')
            if clip.get('audio_boundary_verified') is not True:
                errors.append('AUDIO_BOUNDARY_UNVERIFIED')
            all_clips.append((clip, lo, hi))
        if not duration_limits[0] < duration < duration_limits[1]:
            errors.append('DURATION_OUTSIDE_STRICT_LIMITS')
        for left, right in zip(clips, clips[1:]):
            if left['source_file'] == right['source_file'] and abs(left['source_end_frame'] - right['source_start_frame']) <= 2:
                errors.append('MEANINGLESS_PHYSICAL_SEAM')
    for (a, lo, hi), (b, other_lo, other_hi) in combinations(all_clips, 2):
        if a['source_file'] == b['source_file'] and min(hi, other_hi) > max(lo, other_lo) + 1e-9:
            errors.append('SOURCE_OVERLAP')
    for a, b in combinations(variants, 2):
        ta = ''.join(c.get('transcript', '') for c in a['clips'])
        tb = ''.join(c.get('transcript', '') for c in b['clips'])
        if semantic_duplicate(ta, tb):
            errors.append('SEMANTIC_DUPLICATION')
    return sorted(set(errors))


def semantic_review_variants(plan):
    """Expose semantic evidence without asking a text reviewer to certify audio."""
    variants = []
    for variant in plan.get('variants', []):
        clips = []
        for clip in variant.get('clips', []):
            lo = clip['source_start_frame']/clip['fps']; hi = clip['source_end_frame']/clip['fps']
            view = {k: value for k, value in clip.items() if k in (
                'unit_id','source_file','source_start_frame','source_end_frame','fps','transcript',
                'source_subtitle_text','previous_source_context')}
            view['words_and_source_context'] = [w for w in clip.get('words', [])
                if w['end'] > lo-4 and w['start'] < hi+4]
            clips.append(view)
        variants.append({k: v for k, v in variant.items() if k != 'clips'} | {'clips': clips})
    return variants


def final_task(paths, plan_ref):
    record = load_stage(paths, plan_ref)
    if record['identity']['stage'] != 'plan':
        raise CacheIdentityError('final_review_requires_plan')
    plan = record['body']
    payload = {'schema_version': 1, 'role': 'final_review', 'review_scope': 'semantic_text_and_source_context', 'project_key': record['identity']['project_key'],
               'batch_scope': paths.registered_scope()[1],
               'config_reference': 'PROJECT_CONFIG.json', 'product_id': record['identity']['product_id'],
               'encoding_id': record['identity']['encoding_id'], 'rules_fingerprint': record['identity']['rules_fingerprint'],
               'plan_reference': plan_ref, 'plan_digest': record['body_digest'],
               'variants': semantic_review_variants(plan),
               'semantic_basis': {'selling_points': plan.get('semantic_map', []),
                                  'digest': plan.get('semantic_map_digest'),
                                  'instruction': 'Check whether every variant expresses supported points from this shared map, and whether the quoted source meaning remains intact. Flag unsupported or context-dependent claims.'},
               'local_errors': [e for e in plan_errors(plan) if e != 'AUDIO_BOUNDARY_UNVERIFIED'],
               'hard_rules': {'duration_exclusive': (plan.get('editing_policy') or {}).get('duration_exclusive_seconds', plan.get('duration_exclusive_seconds', [45, 60])),
                              'person_audit': {'no_person_ratio_max_inclusive': 0.10,
                                               'closeup_duration_ratio_max_inclusive': (plan.get('editing_policy') or {}).get('closeup_duration_ratio_max_inclusive', 0.30),
                                               'sampling_contract': 'person-presence-two-stage-record-v7'},
                              'overlap_allowed': False, 'semantic_duplicates_allowed': False,
                              'content_scope': '只保留当前商品卖点/材质/版型/工艺/穿着感受；禁止价格、促销、优惠、红包、抽奖、链接、欢迎、闲聊、后台、拍摄、无关商品和空话'},
               'review_questions': ['按普通观众听完是否容易理解判断：没有明显听感或逻辑大漏洞即可通过，不要求形式化完整；结合最小原源上下文检查开头、结尾和接缝，本机声学与人物门禁单独负责物理风险',
                   '检查因果逻辑本身：理由是否支撑结果、剪辑是否删去必要前提或造成错误因果暗示；按实际语义判断，不依赖“因为/所以”等字面连接词。口语省略但关系清楚即可通过',
                   '条件、转折、列举、指代、承诺是否造成实质困惑；连接词不是自动失败条件',
                   '卖点是否自然有价值；不能用粗选或 closure_check 当终审']}
    payload['task_digest'] = digest(payload)
    return payload


def semantic_selection_task(paths, rough_ref):
    """Review assembled subtitle meaning before any audio or visual computation."""
    rough = load_stage(paths, rough_ref)
    if rough['identity']['stage'] != 'rough':
        raise CacheIdentityError('semantic_review_requires_rough_selection')
    candidate_ref = next((dep['path'] for dep in rough['identity']['dependencies']
                          if load_stage(paths, dep['path'])['identity']['stage'] == 'candidate'), None)
    if not candidate_ref:
        raise CacheIdentityError('semantic_review_candidate_missing')
    candidate = load_stage(paths, candidate_ref)
    by_id = {row['candidate_id']: row for row in candidate['body']['candidates']}
    grouped = {}
    for selected in rough['body']['selected_ranges']:
        source = by_id.get(selected['candidate_id'])
        if not source:
            raise CacheIdentityError('semantic_review_selected_candidate_missing')
        lo, hi = float(selected['start']), float(selected['end'])
        cues = sorted(source.get('subtitle', []), key=lambda row: (float(row['start']), float(row['end'])))
        chosen = [(index, cue) for index, cue in enumerate(cues)
                  if not cue.get('context_only') and float(cue['end']) > lo
                  and float(cue['start']) < hi]
        if not chosen:
            raise CacheIdentityError('semantic_review_selected_subtitles_missing')
        first, last = chosen[0][0], chosen[-1][0]
        text = ''.join(str(cue.get('text') or '') for _, cue in chosen)
        context_before = ''.join(str(cue.get('text') or '') for cue in cues[max(0, first-2):first])
        context_after = ''.join(str(cue.get('text') or '') for cue in cues[last+1:last+3])
        grouped.setdefault(selected['variant_id'], []).append({
            'unit_id': f"{selected['variant_id']}_{len(grouped.get(selected['variant_id'], []))+1}",
            'candidate_id': selected['candidate_id'], 'source_file': source['source_file'],
            'start': lo, 'end': hi, 'transcript': text, 'source_subtitle_text': text,
            'previous_source_context': context_before, 'following_source_context': context_after})
    variants = [{'variant_id': key, 'clips': rows} for key, rows in sorted(grouped.items())]
    payload = {'schema_version': 1, 'role': 'final_review',
               'review_scope': 'semantic_selection_before_audio',
               'project_key': rough['identity']['project_key'],
               'batch_scope': paths.registered_scope()[1], 'config_reference': 'PROJECT_CONFIG.json',
               'product_id': rough['identity']['product_id'], 'encoding_id': rough['identity']['encoding_id'],
               'rules_fingerprint': rough['identity']['rules_fingerprint'],
               'rough_reference': rough_ref, 'rough_digest': rough['body_digest'],
               'variants': variants,
               'semantic_basis': {'selling_points': rough['body'].get('semantic_map', []),
                                  'digest': rough['body'].get('semantic_map_digest'),
                                  'instruction': 'Review the complete assembled subtitle meaning and source context.'},
               'review_questions': [
                   '按普通观众听感判断：整体容易理解且没有大的逻辑漏洞即可通过，不要求形式化完整。',
                   '核对因果、条件、转折、列举、指代和承接关系，判断是否存在实质误解。',
                   '检查卖点是否来自当前商品字幕、是否重复、无关或包含禁用内容。',
                   '这是语义组合终审；不审核 ASR 切点或画面人物，语义通过后由后续本地阶段检查。'],
               'hard_rules': {'semantic_threshold': 'ordinary_listener_no_major_logical_gap',
                              'must_not_claim_audio_or_visual_audit': True}}
    payload['task_digest'] = digest(payload)
    return payload


def publish_semantic_review(paths, rough_ref, task, receipt_ref):
    rough = load_stage(paths, rough_ref)
    receipt = verify_receipt(paths, receipt_ref, task)
    result = receipt['result']
    expected_checks = {'opening', 'ending', 'causal_logic', 'seams', 'selling_value', 'duplicates'}
    if (result.get('verdict') != 'PASS' or set(result.get('checks', {})) != expected_checks
            or not all(result['checks'].values()) or result.get('issues')):
        raise CacheIdentityError('semantic_selection_review_not_passed')
    body = {'verdict': 'PASS', 'rough_reference': rough_ref, 'rough_digest': rough['body_digest'],
            'task': task, 'receipt': receipt_ref}
    args = dict(product_id=rough['identity']['product_id'], encoding_id=rough['identity']['encoding_id'],
                stage='semantic_review', version=digest([rough['body_digest'], task['task_digest']]),
                model_version='codex-semantic-selection-review-v1', dependencies=[rough_ref])
    return publish_stage(paths, **args, body=body, snapshot=capture_stage(paths, **args))


def validate_vision(plan, vision, paths):
    from adaptive_vision import verify
    verify(paths, plan, vision)


def verify_final_record(paths, plan_ref, body):
    if body.get('quality_contract') != CONTRACT:
        raise CacheIdentityError('legacy_final_verdict_not_quality_evidence')
    if not isinstance(body.get('vision_ref'), str) or not body['vision_ref']:
        raise CacheIdentityError('vision_evidence_missing')
    plan = load_stage(paths, plan_ref)
    semantic_ref = body.get('semantic_review_ref')
    if semantic_ref:
        semantic = load_stage(paths, semantic_ref, product_id=plan['identity']['product_id'],
                              encoding_id=plan['identity']['encoding_id'])
        if (semantic['identity']['stage'] != 'semantic_review'
                or semantic['body'].get('verdict') != 'PASS'
                or semantic['body'].get('rough_digest') != load_stage(
                    paths, semantic['body']['rough_reference'])['body_digest']):
            raise CacheIdentityError('semantic_selection_review_evidence_stale')
        rough_ref = semantic['body']['rough_reference']
        if rough_ref not in [dep['path'] for dep in plan['identity']['dependencies']]:
            raise CacheIdentityError('semantic_review_does_not_match_final_plan_selection')
        task = semantic['body']['task']
        receipt = verify_receipt(paths, semantic['body']['receipt'], task)
        if receipt['result'].get('verdict') != 'PASS':
            raise CacheIdentityError('semantic_selection_review_not_passed')
    else:
        if not isinstance(body.get('codex_receipt'), str) or not body['codex_receipt']:
            raise CacheIdentityError('independent_final_review_evidence_missing')
        task = final_task(paths, plan_ref)
        if body.get('review_task') != task:
            raise CacheIdentityError('final_review_input_changed')
        receipt = verify_receipt(paths, body['codex_receipt'], task)
    response = receipt['result']
    if plan['body'].get('selection_receipt'):
        selection = verify_receipt(paths, plan['body']['selection_receipt'], plan['body']['selection_task'])
        if selection['thread_id'] == receipt['thread_id']:
            raise CacheIdentityError('final_review_context_not_independent')
        semantic_map = selection['result'].get('semantic_map')
        if (semantic_map != plan['body'].get('semantic_map')
                or digest(semantic_map) != plan['body'].get('semantic_map_digest')):
            raise CacheIdentityError('final_semantic_basis_not_bound_to_selection_receipt')
    if (receipt['role'] != 'final_review' or response.get('verdict') != 'PASS'
            or set(response.get('checks', {})) != {'opening', 'ending', 'causal_logic', 'seams', 'selling_value', 'duplicates'}
            or any(v is not True for v in response['checks'].values()) or response.get('issues')):
        raise CacheIdentityError('independent_codex_final_review_not_passed')
    errors = plan_errors(plan['body'])
    if errors:
        raise CacheIdentityError('local_final_veto: ' + ','.join(errors))
    for dependency in plan['identity']['dependencies']:
        parent = load_stage(paths, dependency['path'])
        if parent['identity']['stage'] == 'asr':
            from registered_pipeline import asr_model_identity, current_asr_parameters
            model = parent['body'].get('model')
            if (not model or parent['body'].get('parameters') != current_asr_parameters()
                    or asr_model_identity(model['name']) != model):
                raise CacheIdentityError('final_asr_model_identity_changed')
    from publish_confirmed_ranges import verify_confirmation
    range_records = [load_stage(paths, d['path']) for d in plan['identity']['dependencies']
                     if load_stage(paths, d['path'])['identity']['stage'] == 'range']
    if len(range_records) != 1 or not verify_confirmation(range_records[0]['body']):
        raise CacheIdentityError('final_plan_confirmed_range_proof_missing')
    ranges = range_records[0]['body']['ranges']
    for variant in plan['body']['variants']:
        for clip in variant['clips']:
            if not any(r['media_stem']+'.mp4' == clip['source_file']
                       and r['start'] <= clip['source_start_frame']/clip['fps']
                       < clip['source_end_frame']/clip['fps'] <= r['end'] for r in ranges):
                raise CacheIdentityError('final_plan_outside_confirmed_range')
    vision = load_stage(paths, body['vision_ref'], product_id=plan['identity']['product_id'], encoding_id=plan['identity']['encoding_id'])
    if vision['identity']['stage'] != 'vision' or plan_ref not in [d['path'] for d in vision['identity']['dependencies']]:
        raise CacheIdentityError('vision_not_bound_to_exact_plan')
    validate_vision(plan['body'], vision['body'], paths)
    return plan


def import_final_review(paths, plan_ref, vision_ref, receipt_ref=None, *, semantic_review_ref=None):
    plan = load_stage(paths, plan_ref)
    if semantic_review_ref:
        semantic = load_stage(paths, semantic_review_ref, product_id=plan['identity']['product_id'],
                              encoding_id=plan['identity']['encoding_id'])
        if semantic['identity']['stage'] != 'semantic_review':
            raise CacheIdentityError('semantic_review_stage_required')
        body = {'verdict': 'PASS', 'audit_scope': 'semantic_selection_plus_local_hard_gates',
                'quality_contract': CONTRACT, 'plan_digest': plan['body_digest'],
                'semantic_review_ref': semantic_review_ref, 'vision_ref': vision_ref}
    else:
        task = final_task(paths, plan_ref)
        body = {'verdict': 'PASS', 'audit_scope': 'final_plan', 'quality_contract': CONTRACT,
                'plan_digest': plan['body_digest'], 'review_task': task,
                'codex_receipt': receipt_ref, 'vision_ref': vision_ref}
    verify_final_record(paths, plan_ref, body)
    args = dict(product_id=plan['identity']['product_id'], encoding_id=plan['identity']['encoding_id'],
                stage='final_audit', version=digest(body), model_version=(
                    'codex-semantic-selection-review-v1' if semantic_review_ref else 'codex-independent-review-v1'),
                dependencies=[plan_ref, vision_ref])
    return publish_stage(paths, **args, body=body, snapshot=capture_stage(paths, **args))


def approved_product(paths, product_id, row=None):
    from product_checkpoints import resume
    row = row if row is not None else resume(paths, [product_id]).get(product_id, {})
    if row.get('status') not in ('FINAL_PASS', 'WRITTEN', 'EXPORT_VERIFIED'):
        raise CacheIdentityError('product_not_final_quality_passed')
    from product_checkpoints import verify_evidence
    plan = verify_evidence(paths, product_id, row['encoding_id'], row['plan_ref'], row['evidence_ref'], row['status'])
    return {'product_index': product_id, 'encoding_id': row['encoding_id'], 'plans': plan['body']['variants']}


def main():
    import argparse
    from runtime_paths import resolve_runtime_paths
    from project_registry import atomic_json, workspace_lock
    parser = argparse.ArgumentParser(description='Independent final Codex task/import; no Resolve connection')
    parser.add_argument('--root', default='.')
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--vision')
    parser.add_argument('--receipt')
    args = parser.parse_args()
    paths = resolve_runtime_paths(args.root, args.workspace)
    task = final_task(paths, args.plan)
    if not args.receipt:
        with workspace_lock(paths):
            output = paths.project_records_dir / 'tasks' / (task['task_digest']+'.json')
            atomic_json(output, {'status': 'REVIEW_PENDING', 'task': task})
        print('REVIEW_PENDING: ' + paths.reference(output))
        raise SystemExit(2)
    if not args.vision:
        raise CacheIdentityError('exact_plan_vision_evidence_required')
    evidence = import_final_review(paths, args.plan, args.vision, args.receipt)
    from product_checkpoints import update_checkpoint, read_ledger
    update_checkpoint(paths, product_id=task['product_id'], encoding_id=task['encoding_id'], status='FINAL_PASS',
                      plan_ref=args.plan, evidence_ref=evidence, expected_revision=read_ledger(paths)['revision'])
    print('FINAL_PASS: ' + evidence)


if __name__ == '__main__':
    main()
