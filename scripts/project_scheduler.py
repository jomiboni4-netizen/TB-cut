#!/usr/bin/env python3
"""Bounded project scheduler: no chat history; product leases and exact-evidence gates."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import time
from itertools import combinations
from project_registry import atomic_json, digest, workspace_lock
from runtime_paths import resolve_runtime_paths
from runtime_meta import CacheIdentityError
from product_checkpoints import (current_range_path, load_stage, read_ledger, resume, update_checkpoint)
from final_quality_gate import final_task, import_final_review, approved_product
from codex_worker import execute
from registered_pipeline import (inputs, candidates, selection_task, rough_selection, local_asr, physical_plan, vision_audit)
from audit_mimo_v2 import semantic_duplicate
from compute_metrics import Metrics, scope, span, count


def targets(paths):
    titles, _ = inputs(paths)
    state = json.loads(paths.state_path.read_text())
    by_id = {int(p['product_index']): p for p in titles['products']}
    product_ids = [int(pid) for pid in state['batch']['product_ids']]
    if not product_ids or len(product_ids) != len(set(product_ids)):
        raise CacheIdentityError('registered_batch_product_set_invalid')
    missing = [pid for pid in product_ids if pid not in by_id]
    if missing:
        raise CacheIdentityError('registered_batch_product_missing_from_catalog')
    values = [{'product_id': pid, 'encoding_id': by_id[pid]['encoding_id']} for pid in product_ids]
    if state.get('trial'):
        from trial_session import validate_trial
        selected = set(validate_trial(paths)['products'])
        values = [v for v in values if v['product_id'] in selected]
    return values


def groups(values, size=3):
    if type(size) is not int or not 2 <= size <= 3:
        raise CacheIdentityError('group_size_must_be_2_or_3')
    return [values[i:i+size] for i in range(0, len(values), size)]


def schedule(paths, size=3):
    # The registered batch is the explicit work scope. Do not silently widen a
    # three-product task to the entire workbook when preparing the schedule.
    values = targets(paths)
    result = {'schema_version': 3, 'project_key': paths.registered_scope()[0], 'targets': values,
              'groups': [[p['product_id'] for p in g] for g in groups(values, size)], 'group_size': size}
    with workspace_lock(paths):
        atomic_json(paths.project_records_dir / 'schedule_v3.json', result)
    return result


@contextmanager
def product_lease(paths, pid):
    directory = paths.project_records_dir / 'leases'
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(directory / f'product_{pid}.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CacheIdentityError('product_worker_busy') from exc
        yield
    finally:
        os.close(descriptor)


def checkpoint(paths, pid, encoding, status, plan=None, evidence=None, reason=None):
    for _ in range(3):
        try:
            with span('checkpoint'):
                return update_checkpoint(paths, product_id=pid, encoding_id=encoding, status=status, plan_ref=plan,
                                     evidence_ref=evidence, expected_revision=read_ledger(paths)['revision'], reason=reason)
        except CacheIdentityError as exc:
            if 'checkpoint_revision_conflict' not in str(exc):
                raise
            count('checkpoint', retries=1)
    raise CacheIdentityError('checkpoint_revision_contention')


def process_product(paths, product, *, mode='package', detector=None, include_cross_boundary_cues=False,
                    allow_vfr_approximate=False, preferred_fps=None, repair_limit=2):
    pid, encoding = product['product_id'], product['encoding_id']
    with product_lease(paths, pid):
        resumed_at = time.perf_counter()
        existing = resume(paths, [pid])[pid]
        if existing['status'] in ('FINAL_PASS', 'WRITTEN', 'EXPORT_VERIFIED'):
            approved_product(paths, pid)
            detailed = Metrics()
            detailed.add('checkpoint', calls=1, hits=1, seconds=time.perf_counter()-resumed_at)
            with workspace_lock(paths):
                atomic_json(paths.project_records_dir/'metrics'/f'{pid}.json', {
                    'product_id': pid, 'stages': [], 'compute': detailed.rows, 'resumed_valid': True,
                    'time_semantics': 'inclusive nested wall-clock; do not sum spans'})
            return {'product_id': pid, 'status': existing['status'], 'cache_hit': True}
        cursor_path = paths.project_records_dir / 'cursors' / f'{pid}.json'
        cursor = json.loads(cursor_path.read_text()) if cursor_path.exists() else {}
        metrics = []
        detailed = Metrics()
        active_scope = scope(detailed)
        active_scope.__enter__()
        def stage(name, function):
            started = time.perf_counter()
            ref = cursor.get(name)
            invalidation_reason = None
            if ref:
                try:
                    record = load_stage(paths, ref, product_id=pid, encoding_id=encoding)
                    parents = {'rough': ['candidate'], 'asr': ['rough'], 'plan': ['rough', 'asr'],
                               'vision': ['plan'], 'final_audit': ['plan', 'vision']}.get(name, [])
                    dependencies = {d['path'] for d in record['identity']['dependencies']}
                    if any(cursor.get(parent) not in dependencies for parent in parents):
                        raise CacheIdentityError('cursor_dependency_changed')
                    if name == 'vision':
                        from registered_pipeline import vision_model_version
                        if record['identity']['model_version'] != vision_model_version(
                                detector, allow_vfr_approximate=allow_vfr_approximate):
                            raise CacheIdentityError('vision_classifier_or_policy_changed')
                    if name == 'asr':
                        from registered_pipeline import asr_model_identity, current_asr_parameters
                        body = record['body']
                        if body.get('parameters') != current_asr_parameters() or body.get('model') != asr_model_identity(body['model']['name']):
                            raise CacheIdentityError('asr_model_or_parameters_changed')
                    if name == 'final_audit':
                        from final_quality_gate import verify_final_record
                        verify_final_record(paths, cursor['plan'], record['body'])
                except (ValueError, OSError, KeyError) as exc:
                    invalidation_reason = str(exc)[:100]
                    count('cache', reason=type(exc).__name__)
                    ref = None
            hit = ref is not None
            if not ref:
                with span(name):
                    ref = function()
                cursor[name] = ref
                with workspace_lock(paths):
                    atomic_json(cursor_path, cursor)
            metrics.append({'stage': name, 'elapsed_seconds': time.perf_counter()-started, 'cache_hit': hit,
                            'invalidation_reason': invalidation_reason})
            if hit:
                count(name, hits=1)
            return ref
        def worker(task):
            started = time.perf_counter()
            cached = (paths.project_records_dir / 'worker_receipts' / (task['task_digest']+'.json')).exists()
            try:
                with span(task['role']), span('codex_request_wait'):
                    result = execute(paths, task)
                if cached:
                    count('codex_request_wait', hits=1)
                return result
            finally:
                metrics.append({'stage': task['role'], 'elapsed_seconds': time.perf_counter()-started, 'cache_hit': cached})
        try:
            checkpoint(paths, pid, encoding, 'IN_PROGRESS')
            pointer = current_range_path(paths, pid, encoding)
            if not pointer.exists():
                raise CacheIdentityError('confirmed_product_range_missing')
            range_ref = json.loads(pointer.read_text())['reference']
            load_stage(paths, range_ref)
            candidate_ref = stage('candidate', lambda: candidates(
                paths, range_ref, include_cross_boundary_cues=include_cross_boundary_cues))
            from final_quality_gate import plan_errors
            from codex_worker import verify_receipt

            repair_history = cursor.get('repair_history', [])
            repair_issues = (repair_history[-1].get('issues', []) if repair_history else [])
            repair_attempts = int(cursor.get('repair_attempts', 0))
            repair_rounds = 0
            if repair_limit < 1 or repair_attempts < 0:
                raise CacheIdentityError('repair_attempt_state_invalid')
            while True:
                current_repair_attempt = repair_attempts + repair_rounds
                task = selection_task(paths, candidate_ref, issues=repair_issues,
                                      repair_attempt=current_repair_attempt)
                task_path = paths.project_records_dir / 'tasks' / f'{task["task_digest"]}.json'
                with workspace_lock(paths):
                    atomic_json(task_path, {'status': 'REVIEW_PENDING', 'task': task})
                if mode == 'package':
                    checkpoint(paths, pid, encoding, 'REPAIRABLE' if repair_attempts else 'IN_PROGRESS',
                               reason='selection_task_package_pending')
                    return {'product_id': pid, 'status': 'REPAIRABLE' if repair_attempts else 'IN_PROGRESS',
                            'reason': 'selection_task_package_pending', 'repair_attempts': repair_attempts}
                receipt = worker(task)
                response = verify_receipt(paths, receipt, task)['result']
                if response['verdict'] != 'PASS' or not response.get('selected_ranges'):
                    if response['verdict'] == 'BLOCKED':
                        raise CacheIdentityError('selection_worker_blocked')
                    repair_issues = response.get('issues') or ['selection_returned_no_usable_ranges']
                else:
                    # A changed selection invalidates every downstream stage. Keep candidate/range evidence.
                    if repair_rounds or repair_issues:
                        for name in ('rough', 'asr', 'plan', 'vision', 'final_audit'):
                            cursor.pop(name, None)
                    rough_ref = stage('rough', lambda: rough_selection(
                        paths, candidate_ref, receipt, issues=repair_issues,
                        repair_attempt=current_repair_attempt))
                    asr_ref = stage('asr', lambda: local_asr(paths, rough_ref))
                    plan_ref = stage('plan', lambda: physical_plan(
                        paths, rough_ref, asr_ref, fps=preferred_fps,
                        allow_vfr_approximate=allow_vfr_approximate))
                    errors = plan_errors(load_stage(paths, plan_ref)['body'])
                    if not errors:
                        task = final_task(paths, plan_ref)
                        with workspace_lock(paths):
                            atomic_json(paths.project_records_dir / 'tasks' / f'{task["task_digest"]}.json',
                                        {'status': 'REVIEW_PENDING', 'task': task})
                        final_receipt = worker(task)
                        final_result = verify_receipt(paths, final_receipt, task)['result']
                        expected_checks = {'opening', 'ending', 'causal_logic', 'seams', 'selling_value', 'duplicates'}
                        checks = final_result.get('checks', {})
                        if (final_result['verdict'] == 'PASS' and set(checks) == expected_checks
                                and all(checks.values()) and not final_result.get('issues')):
                            break
                        if final_result['verdict'] == 'BLOCKED':
                            raise CacheIdentityError('independent_final_review_blocked')
                        repair_issues = final_result.get('issues') or ['final_review_checks_incomplete_or_failed']
                    else:
                        repair_issues = errors
                repair_attempts += 1
                repair_rounds += 1
                cursor['repair_attempts'] = repair_attempts
                cursor.setdefault('repair_history', []).append({'attempt': repair_attempts,
                    'issues': repair_issues[:20], 'from_stage': 'selection' if repair_rounds == 1 else 'final_review'})
                with workspace_lock(paths):
                    atomic_json(cursor_path, cursor)
                checkpoint(paths, pid, encoding, 'REPAIRABLE', cursor.get('plan'),
                           reason=','.join(repair_issues)[:180])
                if repair_rounds >= repair_limit:
                    return {'product_id': pid, 'status': 'REPAIRABLE', 'errors': repair_issues,
                            'repair_attempts': repair_attempts, 'repair_limit_reached': True}
            if not detector:
                raise CacheIdentityError('verified_host_detector_unavailable')
            vision_ref = stage('vision', lambda: vision_audit(
                paths, plan_ref, detector, allow_vfr_approximate=allow_vfr_approximate))
            evidence = stage('final_audit', lambda: import_final_review(paths, plan_ref, vision_ref, final_receipt))
            checkpoint(paths, pid, encoding, 'FINAL_PASS', plan_ref, evidence)
            return {'product_id': pid, 'status': 'FINAL_PASS'}
        except Exception as exc:
            reason = str(exc)[:180]
            checkpoint(paths, pid, encoding, 'BLOCKED', reason=reason)
            return {'product_id': pid, 'status': 'BLOCKED', 'reason': reason}
        finally:
            # Product-only timing; no candidate text, transcript or chat events in controller summaries.
            with workspace_lock(paths):
                atomic_json(paths.project_records_dir / 'metrics' / f'{pid}.json',
                            {'product_id': pid, 'stages': metrics, 'compute': detailed.rows,
                             'time_semantics': 'inclusive nested wall-clock; do not sum spans',
                             'repair_attempts': cursor.get('repair_attempts', 0)})
            active_scope.__exit__(None, None, None)


def subprocess_error_types():
    import subprocess
    return subprocess.SubprocessError


def project_audit(paths):
    state = json.loads(paths.state_path.read_text())
    skipped = {int(value) for value in state.get('batch', {}).get('failed_product_ids', [])}
    values = [value for value in targets(paths) if value['product_id'] not in skipped]
    rows = resume(paths)
    errors, passed = [], []
    for value in values:
        pid = value['product_id']
        row = rows[pid]
        if row['status'] not in ('FINAL_PASS', 'WRITTEN', 'EXPORT_VERIFIED'):
            errors.append(f'PRODUCT_{pid}_{row["status"]}')
        else:
            passed.append(approved_product(paths, pid, row))
    clips = [(p['product_index'], c) for p in passed for v in p['plans'] for c in v['clips']]
    for (pa, a), (pb, b) in combinations(clips, 2):
        if pa == pb:
            continue
        if (a['source_file'] == b['source_file'] and min(a['source_end_frame']/a['fps'], b['source_end_frame']/b['fps'])
                > max(a['source_start_frame']/a['fps'], b['source_start_frame']/b['fps'])+1e-9):
            errors.append('CROSS_GROUP_SOURCE_OVERLAP')
        if semantic_duplicate(a['transcript'], b['transcript']):
            errors.append('CROSS_PRODUCT_SEMANTIC_DUPLICATION')
    if not errors:
        task = project_review_task(paths, passed, rows)
        receipt = paths.project_records_dir / 'worker_receipts' / (task['task_digest']+'.json')
        try:
            from codex_worker import verify_receipt
            result = verify_receipt(paths, paths.reference(receipt), task)['result']
            if (result.get('verdict') != 'PASS' or set(result.get('checks', {})) !=
                    {'opening', 'ending', 'causal_logic', 'seams', 'selling_value', 'duplicates'}
                    or result.get('checks', {}).get('duplicates') is not True
                    or any(v is not True for v in result.get('checks', {}).values()) or result.get('issues')):
                errors.append('PROJECT_CODEX_REVIEW_NOT_PASSED')
        except (ValueError, OSError, KeyError):
            errors.append('PROJECT_CODEX_REVIEW_PENDING')
    return {'schema_version': 3, 'target_count': len(values), 'final_pass_count': len(passed),
            'final_review_complete': not errors, 'written_count': sum(r['status'] in ('WRITTEN', 'EXPORT_VERIFIED') for r in rows.values()),
            'export_verified_count': sum(r['status'] == 'EXPORT_VERIFIED' for r in rows.values()),
            'delivery_complete': False, 'delivery_gate': 'BLOCKED_UNMIGRATED_RESOLVE_EXPORT_VERIFIERS',
            'errors': sorted(set(errors))}


def project_review_task(paths, products, rows):
    task = {'schema_version': 1, 'role': 'project_review', 'product_id': 0,
            'project_key': paths.registered_scope()[0], 'config_reference': 'PROJECT_CONFIG.json',
            'products': [{'product_id': p['product_index'], 'plan_ref': rows[p['product_index']]['plan_ref'],
                          'audit_ref': rows[p['product_index']]['evidence_ref'],
                          'variants': [{'variant_id': v['variant_id'], 'text': ''.join(c['transcript'] for c in v['clips'])}
                                       for v in p['plans']]} for p in products],
            'question': '独立检查全目标覆盖与跨商品/跨组语义重复；不要仅凭文字匹配或单商品 PASS 宣布整项目通过。没有必要的证据则 BLOCKED。'}
    task['task_digest'] = digest(task)
    return task


def _run_project(paths, *, group_size=3, mode='package', detector=None, include_cross_boundary_cues=False,
                 allow_vfr_approximate=False, preferred_fps=None, repair_limit=2):
    manifest = schedule(paths, group_size)
    by_id = {v['product_id']: v for v in manifest['targets']}
    processed = []
    for group in manifest['groups']:
        results = [process_product(paths, by_id[pid], mode=mode, detector=detector,
                                   include_cross_boundary_cues=include_cross_boundary_cues,
                                   allow_vfr_approximate=allow_vfr_approximate,
                                   preferred_fps=preferred_fps, repair_limit=repair_limit) for pid in group]
        processed.append({'products': group, 'statuses': [r['status'] for r in results]})
    with span('project_quality_gate'):
        preliminary = project_audit(paths)
    if preliminary['errors'] == ['PROJECT_CODEX_REVIEW_PENDING'] and mode == 'codex':
        rows = resume(paths)
        products = [approved_product(paths, p['product_id'], rows[p['product_id']]) for p in manifest['targets']]
        task = project_review_task(paths, products, rows)
        with workspace_lock(paths):
            atomic_json(paths.project_records_dir / 'tasks' / (task['task_digest']+'.json'),
                        {'status': 'REVIEW_PENDING', 'task': task})
        try:
            with span('project_review'), span('codex_request_wait'):
                execute(paths, task)
        except (ValueError, OSError, subprocess_error_types()):
            pass  # Remain explicitly pending; no global approval without the receipt.
    with span('project_quality_gate'):
        result = {**project_audit(paths), 'groups_processed': processed}
    with workspace_lock(paths):
        atomic_json(paths.project_records_dir / 'project_audit_v3.json', result)
    return result


def run_project(paths, *, group_size=3, mode='package', detector=None, include_cross_boundary_cues=False,
                allow_vfr_approximate=False, preferred_fps=None, repair_limit=2):
    metrics = Metrics()
    with scope(metrics):
        try:
            with span('project_total'):
                return _run_project(paths, group_size=group_size, mode=mode, detector=detector,
                                    include_cross_boundary_cues=include_cross_boundary_cues,
                                    allow_vfr_approximate=allow_vfr_approximate,
                                    preferred_fps=preferred_fps, repair_limit=repair_limit)
        finally:
            with workspace_lock(paths):
                atomic_json(paths.project_records_dir/'metrics'/'project.json', {
                    'compute': metrics.rows, 'product_metrics': 'metrics/<product_id>.json',
                    'time_semantics': 'inclusive nested wall-clock; do not sum spans'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.')
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--group-size', type=int, default=3)
    parser.add_argument('--repair-limit', type=int, default=2,
                        help='Maximum repair reruns per product per invocation; default 2')
    parser.add_argument('--mode', choices=('package', 'codex'), default='package')
    parser.add_argument('--detector')
    parser.add_argument('--audit-only', action='store_true')
    parser.add_argument('--product', type=int, help='Standalone single-product entry; never resumes another chat')
    parser.add_argument('--include-cross-boundary-cues', action='store_true',
                        help='Explicit batch policy: include complete subtitle cues intersecting approximate range edges')
    parser.add_argument('--allow-vfr-approximate', action='store_true',
                        help='Explicit batch override: use whole-file average rate for VFR source mapping')
    parser.add_argument('--preferred-fps', type=float,
                        help='Verified project/timeline rate used only when the approved mean-rate approximation agrees within tolerance')
    args = parser.parse_args()
    paths = resolve_runtime_paths(args.root, args.workspace)
    if args.product is not None:
        product = next((p for p in targets(paths) if p['product_id'] == args.product), None)
        if product is None:
            raise CacheIdentityError('unknown_project_product')
        print(json.dumps(process_product(paths, product, mode=args.mode, detector=args.detector,
                                         include_cross_boundary_cues=args.include_cross_boundary_cues,
                                         allow_vfr_approximate=args.allow_vfr_approximate,
                                         preferred_fps=args.preferred_fps,
                                         repair_limit=args.repair_limit), ensure_ascii=False))
        return
    result = project_audit(paths) if args.audit_only else run_project(
        paths, group_size=args.group_size, mode=args.mode, detector=args.detector,
        include_cross_boundary_cues=args.include_cross_boundary_cues,
        allow_vfr_approximate=args.allow_vfr_approximate,
        preferred_fps=args.preferred_fps, repair_limit=args.repair_limit)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['final_review_complete'] else 2)


if __name__ == '__main__':
    main()
