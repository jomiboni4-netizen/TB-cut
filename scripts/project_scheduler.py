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
from final_quality_gate import (semantic_selection_task, publish_semantic_review,
                                import_final_review, approved_product)
from codex_worker import execute
from registered_pipeline import (inputs, candidates, selection_task, rough_selection, local_asr,
                                 physical_plan, vision_audit, visual_repair_samples)
from compute_metrics import Metrics, scope, span, count
from sentence_boundaries import CONTRACT as SENTENCE_CONTRACT


def current_plan_contract(record):
    """A saved plan cannot survive a changed sentence-edge producer."""
    return (record.get('identity', {}).get('stage') == 'plan'
            and record['identity'].get('model_version') == SENTENCE_CONTRACT)


def targets(paths):
    titles, _ = inputs(paths)
    state = paths.load_state()
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


def save_product_metrics(paths, product_id, run_record):
    """Retain bounded per-invocation timing instead of replacing the prior run."""
    path = paths.project_records_dir / 'metrics' / f'{product_id}.json'
    with workspace_lock(paths):
        previous = json.loads(path.read_text()) if path.is_file() else {}
        runs = list(previous.get('runs', []))
        if not runs and previous:
            runs.append({'legacy_snapshot': True,
                         'stages': previous.get('stages', []),
                         'compute': previous.get('compute', {}),
                         'repair_attempts': previous.get('repair_attempts')})
        runs.append(run_record)
        atomic_json(path, {'product_id': product_id, 'runs': runs[-100:], 'latest': run_record,
                           'time_semantics': 'per-invocation wall clock; nested stage spans are inclusive'})


def selection_overlap_issues(paths, candidate_ref, selected_ranges):
    """Reject same-source reuse before ASR and plan work are scheduled."""
    candidate = load_stage(paths, candidate_ref)
    by_id = {row['candidate_id']: row for row in candidate['body']['candidates']}
    intervals = []
    for row in selected_ranges:
        source = by_id.get(row.get('candidate_id'))
        if source is None:
            continue  # rough_selection reports the authoritative invalid candidate error.
        try:
            start, end = float(row['start']), float(row['end'])
        except (KeyError, TypeError, ValueError):
            continue  # let rough_selection validate malformed timestamps.
        intervals.append((source['source_file'], start, end, row.get('variant_id')))
    errors = []
    for index, left in enumerate(intervals):
        for right in intervals[index + 1:]:
            if left[0] == right[0] and min(left[2], right[2]) > max(left[1], right[1]) + 1e-9:
                errors.append(f"SOURCE_RANGE_REUSE:{left[3]}:{right[3]}:{max(left[1], right[1]):.3f}-{min(left[2], right[2]):.3f}")
    return errors


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
    from codex_worker import verify_receipt
    pid, encoding = product['product_id'], product['encoding_id']
    with product_lease(paths, pid):
        resumed_at = time.perf_counter()
        run_started_at = time.time()
        existing = resume(paths, [pid])[pid]
        if existing.get('status') == 'BLOCKED' and existing.get('reason') == 'checkpoint_context_stale':
            # A temporary implementation edit can stale only the checkpoint
            # envelope while its immutable plan and audit remain valid. Rebind
            # that exact passing evidence through the normal checkpoint API;
            # never rerun selection or alter the approved plan.
            cursor_path = paths.project_records_dir / 'cursors' / f'{pid}.json'
            try:
                recovery_cursor = json.loads(cursor_path.read_text())
                plan_ref = recovery_cursor.get('plan')
                evidence_ref = recovery_cursor.get('final_audit')
                if not plan_ref or not evidence_ref:
                    raise CacheIdentityError('checkpoint_recovery_evidence_missing')
                load_stage(paths, plan_ref, product_id=pid, encoding_id=encoding)
                audit_record = load_stage(paths, evidence_ref, product_id=pid, encoding_id=encoding)
                from final_quality_gate import verify_final_record
                verify_final_record(paths, plan_ref, audit_record['body'])
                checkpoint(paths, pid, encoding, 'FINAL_PASS', plan_ref, evidence_ref)
                existing = resume(paths, [pid])[pid]
            except (CacheIdentityError, ValueError, OSError, KeyError, TypeError):
                pass
        if existing['status'] in ('FINAL_PASS', 'WRITTEN', 'EXPORT_VERIFIED'):
            approved_product(paths, pid)
            detailed = Metrics()
            detailed.add('checkpoint', calls=1, hits=1, seconds=time.perf_counter()-resumed_at)
            save_product_metrics(paths, pid, {'started_at_epoch': run_started_at,
                'elapsed_seconds': round(time.perf_counter()-resumed_at, 3), 'stages': [],
                'compute': detailed.rows, 'resumed_valid': True, 'repair_attempts': 0})
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
                    parents = {'rough': ['candidate'], 'semantic_review': ['rough'],
                               'asr': ['rough'], 'plan': ['rough', 'asr'],
                               'vision': ['plan'], 'final_audit': ['plan', 'vision']}.get(name, [])
                    dependencies = {d['path'] for d in record['identity']['dependencies']}
                    if any(cursor.get(parent) not in dependencies for parent in parents):
                        raise CacheIdentityError('cursor_dependency_changed')
                    if name == 'vision':
                        from registered_pipeline import vision_model_version
                        if record['identity']['model_version'] != vision_model_version(
                                detector, allow_vfr_approximate=allow_vfr_approximate):
                            raise CacheIdentityError('vision_classifier_or_policy_changed')
                    if name == 'plan' and not current_plan_contract(record):
                        raise CacheIdentityError('sentence_edge_contract_changed')
                    if name == 'asr':
                        from registered_pipeline import asr_model_identity, current_asr_parameters
                        body = record['body']
                        if body.get('parameters') != current_asr_parameters() or body.get('model') != asr_model_identity(body['model']['name']):
                            raise CacheIdentityError('asr_model_or_parameters_changed')
                    if name == 'final_audit':
                        from final_quality_gate import verify_final_record
                        verify_final_record(paths, cursor['plan'], record['body'])
                except (CacheIdentityError, ValueError, OSError, KeyError) as exc:
                    invalidation_reason = str(exc)[:100]
                    count('cache', reason=type(exc).__name__)
                    ref = None
            hit = ref is not None
            previous_ref = cursor.get(name)
            if not ref:
                with span(name):
                    ref = function()
                cursor[name] = ref
                descendants = {
                    'candidate': ('rough', 'asr', 'plan', 'vision', 'final_audit'),
                    'rough': ('semantic_review', 'asr', 'plan', 'vision', 'final_audit'),
                    'semantic_review': ('asr', 'plan', 'vision', 'final_audit'),
                    'asr': ('plan', 'vision', 'final_audit'),
                    'plan': ('vision', 'final_audit'),
                    'vision': ('final_audit',),
                }.get(name, ())
                if previous_ref != ref:
                    for child in descendants:
                        cursor.pop(child, None)
                    if name in ('candidate', 'rough'):
                        cursor.pop('semantic_map', None)
                        cursor.pop('audio_repair_request', None)
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
            if not detector:
                raise CacheIdentityError('verified_person_detector_required_before_selection')
            pointer = current_range_path(paths, pid, encoding)
            if not pointer.exists():
                raise CacheIdentityError('confirmed_product_range_missing')
            range_ref = json.loads(pointer.read_text())['reference']
            load_stage(paths, range_ref)
            candidate_ref = stage('candidate', lambda: candidates(
                paths, range_ref, include_cross_boundary_cues=include_cross_boundary_cues))
            from final_quality_gate import plan_errors
            repair_history = cursor.get('repair_history', [])
            # A repair request is meaningful only while its prior selection is
            # identity-valid. Otherwise discard the stale local repair context
            # and make one fresh selection from the complete candidate pool.
            prior_rough_is_current = False
            if cursor.get('rough'):
                try:
                    load_stage(paths, cursor['rough'], product_id=pid, encoding_id=encoding)
                    prior_rough_is_current = True
                except (CacheIdentityError, ValueError, OSError, KeyError):
                    pass
            if cursor.get('audio_repair_request') or cursor.get('asr_edge_repair_request'):
                semantic_current = False
                if prior_rough_is_current and cursor.get('semantic_review'):
                    try:
                        load_stage(paths, cursor['semantic_review'], product_id=pid,
                                   encoding_id=encoding)
                        semantic_current = True
                    except (CacheIdentityError, ValueError, OSError, KeyError):
                        pass
                if not semantic_current:
                    cursor.pop('audio_repair_request', None)
                    cursor.pop('asr_edge_repair_request', None)
                    with workspace_lock(paths):
                        atomic_json(cursor_path, cursor)
            latest_repair = repair_history[-1] if repair_history else {}
            latest_repair_current = (
                (latest_repair.get('from_stage') == 'selection'
                 and latest_repair.get('candidate_ref') == candidate_ref)
                or (latest_repair.get('from_stage') == 'semantic_review' and prior_rough_is_current
                    and latest_repair.get('rough_ref') == cursor.get('rough'))
                or (latest_repair.get('from_stage') not in ('selection', 'semantic_review')
                    and prior_rough_is_current
                    and latest_repair.get('selection_digest') == digest(
                        load_stage(paths, cursor['rough'], product_id=pid,
                                   encoding_id=encoding)['body'].get('selected_ranges', []))))
            if latest_repair_current and latest_repair.get('plan_ref'):
                try:
                    prior_repair_plan = load_stage(paths, latest_repair['plan_ref'],
                                                   product_id=pid, encoding_id=encoding)
                    latest_repair_current = current_plan_contract(prior_repair_plan)
                except (CacheIdentityError, ValueError, OSError, KeyError):
                    latest_repair_current = False
            repair_issues = latest_repair.get('issues', []) if latest_repair_current else []
            repair_attempts = int(cursor.get('repair_attempts', 0))
            repair_attempts_by_key = cursor.setdefault('repair_attempts_by_key', {})
            last_repair_key = latest_repair.get('repair_key') if latest_repair_current else None
            repair_rounds = 0
            if repair_limit < 1 or repair_attempts < 0:
                raise CacheIdentityError('repair_attempt_state_invalid')
            total_repair_limit = repair_limit * 3
            if repair_attempts >= total_repair_limit:
                checkpoint(paths, pid, encoding, 'REPAIRABLE', cursor.get('plan'),
                           reason='total_repair_limit_reached')
                return {'product_id': pid, 'status': 'REPAIRABLE',
                        'reason': 'total_repair_limit_reached',
                        'repair_attempts': repair_attempts,
                        'total_repair_limit': total_repair_limit}
            if (last_repair_key and repair_issues
                    and int(repair_attempts_by_key.get(last_repair_key, 0)) >= repair_limit):
                checkpoint(paths, pid, encoding, 'REPAIRABLE', cursor.get('plan'),
                           reason='same_failure_repair_limit_reached')
                return {'product_id': pid, 'status': 'REPAIRABLE',
                        'reason': 'same_failure_repair_limit_reached',
                        'repair_attempts': repair_attempts,
                        'repair_attempts_for_failure': int(repair_attempts_by_key[last_repair_key]),
                        'repair_limit': repair_limit}

            def record_repair_failure(failure_stage, issues):
                nonlocal repair_attempts, repair_rounds
                selected_ranges = []
                if cursor.get('rough'):
                    try:
                        selected_ranges = load_stage(paths, cursor['rough'], product_id=pid,
                                                     encoding_id=encoding)['body'].get('selected_ranges', [])
                    except (CacheIdentityError, ValueError, OSError, KeyError):
                        selected_ranges = []
                selection_digest = digest(selected_ranges)
                repair_key = digest({'stage': failure_stage, 'issues': issues,
                                     'candidate': candidate_ref,
                                     'selected_ranges': selection_digest})
                repair_attempts += 1
                repair_rounds += 1
                cursor['repair_attempts'] = repair_attempts
                repair_attempts_by_key[repair_key] = int(repair_attempts_by_key.get(repair_key, 0)) + 1
                cursor.setdefault('repair_history', []).append({'attempt': repair_attempts,
                    'issues': issues[:20], 'from_stage': failure_stage, 'repair_key': repair_key,
                    'candidate_ref': candidate_ref,
                    'rough_ref': cursor.get('rough') if failure_stage == 'semantic_review' else None,
                    'selection_digest': selection_digest,
                    'selected_ranges': selected_ranges,
                    'plan_ref': cursor.get('plan') if failure_stage not in (
                        'selection', 'semantic_review') else None})
                with workspace_lock(paths):
                    atomic_json(cursor_path, cursor)
                checkpoint(paths, pid, encoding, 'REPAIRABLE', cursor.get('plan'),
                           reason=','.join(issues)[:180])
                if (repair_rounds >= repair_limit or repair_attempts >= total_repair_limit
                        or repair_attempts_by_key[repair_key] >= repair_limit):
                    return {'product_id': pid, 'status': 'REPAIRABLE', 'errors': issues,
                            'repair_attempts': repair_attempts, 'repair_limit_reached': True,
                            'same_failure_limit_reached': repair_attempts_by_key[repair_key] >= repair_limit,
                            'total_repair_limit_reached': repair_attempts >= total_repair_limit}
                return None
            while True:
                current_repair_attempt = repair_attempts
                failure_stage = 'selection'
                semantic_map = cursor.get('semantic_map', [])
                audio_retry = bool(cursor.get('audio_repair_request'))
                asr_edge_retry = bool(cursor.get('asr_edge_repair_request'))
                local_retry = audio_retry or asr_edge_retry
                if local_retry:
                    rough_record = load_stage(paths, cursor.get('rough'), product_id=pid, encoding_id=encoding)
                    # Older local-edge cursors can lack this cached map even though
                    # the immutable, reviewed rough selection contains it.
                    if not semantic_map:
                        semantic_map = rough_record['body'].get('semantic_map', [])
                        if semantic_map:
                            cursor['semantic_map'] = semantic_map
                            with workspace_lock(paths):
                                atomic_json(cursor_path, cursor)
                    if audio_retry and cursor.get('asr') is None:
                        raise CacheIdentityError('audio_retry_missing_preserved_asr')
                    if audio_retry:
                        load_stage(paths, cursor['asr'], product_id=pid, encoding_id=encoding)
                    if cursor.get('semantic_review') is None:
                        raise CacheIdentityError('local_retry_missing_preserved_semantic_review')
                    load_stage(paths, cursor['semantic_review'], product_id=pid, encoding_id=encoding)
                    response = {'verdict': 'PASS', 'selected_ranges': rough_record['body'].get('selected_ranges', []),
                                'semantic_map': semantic_map}
                    repair_issues = []
                else:
                    previous_selected_ranges = cursor.get('selection_repair_context', {}).get(
                        'previous_selected_ranges', [])
                    if cursor.get('rough'):
                        try:
                            prior_rough = load_stage(paths, cursor['rough'], product_id=pid,
                                                     encoding_id=encoding)
                            previous_selected_ranges = prior_rough['body'].get(
                                'selected_ranges', previous_selected_ranges)
                        except (CacheIdentityError, ValueError, OSError, KeyError):
                            pass
                    if not previous_selected_ranges and cursor.get('repair_history'):
                        repair_files = sorted(
                            (paths.project_records_dir / 'tasks').glob(f'asr_edge_repair_{pid}_*.json'),
                            key=lambda path: path.stat().st_mtime, reverse=True)
                        for repair_file in repair_files:
                            try:
                                request = json.loads(repair_file.read_text())
                                prior_rough = load_stage(paths, request['rough_reference'],
                                                         product_id=pid, encoding_id=encoding)
                                previous_selected_ranges = prior_rough['body'].get('selected_ranges', [])
                            except (CacheIdentityError, ValueError, OSError, KeyError, TypeError):
                                continue
                            if previous_selected_ranges:
                                break
                    visual_samples = []
                    if repair_issues and all('人物画面比例超限' in str(issue) for issue in repair_issues) and cursor.get('vision'):
                        prior_vision = load_stage(paths, cursor['vision'], product_id=pid,
                                                  encoding_id=encoding)
                        visual_samples = visual_repair_samples(prior_vision['body'])
                    if repair_issues and all('人物画面比例超限' in str(issue) for issue in repair_issues) and not visual_samples:
                        raise CacheIdentityError('visual_repair_samples_missing')
                    task = selection_task(paths, candidate_ref, issues=repair_issues,
                                          repair_attempt=current_repair_attempt, semantic_map=semantic_map,
                                          previous_selected_ranges=previous_selected_ranges,
                                          visual_samples=visual_samples)
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
                semantic_map_error = None
                if response.get('verdict') == 'PASS' and response.get('selected_ranges'):
                    conflicts = selection_overlap_issues(paths, candidate_ref, response['selected_ranges'])
                    if conflicts:
                        response = {**response, 'verdict': 'REPAIRABLE', 'issues': conflicts}
                if response.get('semantic_map'):
                    try:
                        from registered_pipeline import _validate_semantic_map
                        candidate_record = load_stage(paths, candidate_ref)
                        _validate_semantic_map(response['semantic_map'], candidate_record['body']['candidates'])
                        cursor['semantic_map'] = response['semantic_map']
                        with workspace_lock(paths):
                            atomic_json(cursor_path, cursor)
                    except CacheIdentityError as exc:
                        semantic_map_error = str(exc)
                elif response['verdict'] == 'PASS':
                    semantic_map_error = 'selection_semantic_map_empty'
                if semantic_map_error:
                    repair_issues = [semantic_map_error]
                    failure_stage = 'selection'
                elif response['verdict'] != 'PASS' or not response.get('selected_ranges'):
                    if response['verdict'] == 'BLOCKED':
                        raise CacheIdentityError('selection_worker_blocked')
                    repair_issues = response.get('issues') or ['selection_returned_no_usable_ranges']
                    failure_stage = 'selection'
                else:
                    # A changed selection invalidates every downstream stage. Keep candidate/range evidence.
                    if (repair_rounds or repair_issues) and not local_retry:
                        for name in ('rough', 'semantic_review', 'asr', 'plan', 'vision', 'final_audit'):
                            cursor.pop(name, None)
                    if local_retry:
                        rough_ref = cursor['rough']
                    else:
                        rough_ref = stage('rough', lambda: rough_selection(
                            paths, candidate_ref, receipt, issues=repair_issues,
                            repair_attempt=current_repair_attempt, semantic_map=semantic_map,
                            previous_selected_ranges=previous_selected_ranges,
                            visual_samples=visual_samples))
                        if cursor.pop('selection_repair_context', None) is not None:
                            with workspace_lock(paths):
                                atomic_json(cursor_path, cursor)
                        semantic_task = semantic_selection_task(paths, rough_ref)
                        semantic_receipt = worker(semantic_task)
                        semantic_result = verify_receipt(paths, semantic_receipt, semantic_task)['result']
                        semantic_checks = semantic_result.get('checks', {})
                        expected_semantic_checks = {'opening', 'ending', 'causal_logic', 'seams',
                                                    'selling_value', 'duplicates'}
                        if (semantic_result.get('verdict') == 'PASS'
                                and set(semantic_checks) == expected_semantic_checks
                                and all(semantic_checks.values()) and not semantic_result.get('issues')):
                            semantic_review_ref = publish_semantic_review(
                                paths, rough_ref, semantic_task, semantic_receipt)
                            cursor['semantic_review'] = semantic_review_ref
                            with workspace_lock(paths):
                                atomic_json(cursor_path, cursor)
                        else:
                            if semantic_result.get('verdict') == 'BLOCKED':
                                raise CacheIdentityError('semantic_selection_review_blocked')
                            repair_issues = semantic_result.get('issues') or [
                                'semantic_selection_review_checks_incomplete_or_failed']
                            failure = record_repair_failure('semantic_review', repair_issues)
                            if failure:
                                return failure
                            continue
                    asr_ref = stage('asr', lambda: local_asr(paths, rough_ref))
                    try:
                        plan_ref = stage('plan', lambda: physical_plan(
                            paths, rough_ref, asr_ref, fps=preferred_fps,
                            allow_vfr_approximate=allow_vfr_approximate))
                    except ValueError as exc:
                        reason = str(exc)
                        if not (reason.startswith('sentence_local_text_')
                                or reason.startswith('sentence_edge_inside_asr_token')
                                or reason.startswith('sentence_alignment_')
                                or reason in ('asr_cut_neighborhood_order_invalid',
                                              'overlapping_asr_tokens_no_safe_sentence_edge',
                                              'word_alignment_uncertain_needs_local_audio_review',
                                              'word_alignment_interval_too_short')):
                            raise
                        repair_body = {'schema_version': 1, 'failed_stage': 'asr_text_edge',
                            'reason': reason, 'rollback_to': 'local_asr_edge_review',
                            'invalidate': ['plan', 'vision', 'final_audit'],
                            'preserve': ['range', 'candidate', 'rough', 'semantic_review',
                                         'semantic_map', 'asr'],
                            'rough_reference': rough_ref, 'asr_reference': asr_ref,
                            'semantic_rerun': False}
                        repair_body['repair_digest'] = digest(repair_body)
                        prior = cursor.get('asr_edge_repair_request') or {}
                        repeated = prior.get('repair_digest') == repair_body['repair_digest']
                        repair_path = paths.project_records_dir / 'tasks' / (
                            f"asr_edge_repair_{pid}_{repair_body['repair_digest'][:12]}.json")
                        if not repair_path.exists():
                            with workspace_lock(paths):
                                atomic_json(repair_path, repair_body)
                        if repeated:
                            # ASR alignment is a cutpoint-only concern. Keep
                            # the reviewed selection and expose the cutpoint
                            # for local/manual review; never request semantic
                            # reselection because an edge could not be found.
                            checkpoint(paths, pid, encoding, 'REPAIRABLE', cursor.get('plan'),
                                       reason='asr_cutpoint_requires_manual_review:' + reason)
                            return {'product_id': pid, 'status': 'REPAIRABLE_ASR_CUTPOINT',
                                    'reason': reason, 'semantic_rerun': False,
                                    'preserved_selection': True}
                        cursor['asr_edge_repair_request'] = repair_body
                        for name in ('plan', 'vision', 'final_audit'):
                            cursor.pop(name, None)
                        with workspace_lock(paths):
                            atomic_json(cursor_path, cursor)
                        checkpoint(paths, pid, encoding, 'REPAIRABLE',
                                   reason='asr_text_edge_requires_local_review')
                        return {'product_id': pid, 'status': 'REPAIRABLE_ASR_EDGE',
                                'repair_reference': paths.reference(repair_path),
                                'rollback_to': 'local_asr_edge_review',
                                'semantic_rerun': False, 'repeated_same_failure': repeated}
                    plan_body = load_stage(paths, plan_ref)['body']
                    # Person visibility is independent of ASR edge matching.
                    # Run it against the provisional cue-span plan too, while
                    # keeping that plan ineligible for final approval.
                    vision_ref = stage('vision', lambda: vision_audit(
                        paths, plan_ref, detector,
                        allow_vfr_approximate=allow_vfr_approximate))
                    vision_body = load_stage(paths, vision_ref, product_id=pid,
                                             encoding_id=encoding)['body']
                    visual_failures = [row for row in vision_body.get('variants', [])
                                       if row.get('status') == 'REPAIRABLE']
                    if visual_failures:
                        repair_issues = [
                            f"{row['variant_id']}人物画面比例超限：无人物估算"
                            f"{row.get('no_person_ratio_estimate')}，近景估算"
                            f"{row.get('closeup_duration_ratio_estimate')}；重组选段后复查。"
                            for row in visual_failures]
                        failure = record_repair_failure('vision', repair_issues)
                        if failure:
                            return failure
                        continue
                    if any(row.get('status') != 'PASS'
                           for row in vision_body.get('variants', [])):
                        raise CacheIdentityError('vision_uncertain_or_blocked_needs_evidence_repair')
                    unresolved_cutpoints = [clip.get('cutpoint_review') for variant in plan_body.get('variants', [])
                                            for clip in variant.get('clips', [])
                                            if clip.get('cutpoint_review', {}).get('status') == 'REPAIRABLE']
                    if unresolved_cutpoints:
                        repair_body = {'schema_version': 1, 'failed_stage': 'asr_cutpoint',
                            'issues': unresolved_cutpoints, 'rollback_to': 'local_audio_cutpoint_review',
                            'invalidate': ['plan', 'final_audit'],
                            'preserve': ['range', 'candidate', 'rough', 'semantic_review', 'asr', 'vision'],
                            'rough_reference': rough_ref, 'asr_reference': asr_ref,
                            'plan_reference': plan_ref, 'vision_reference': vision_ref,
                            'semantic_rerun': False}
                        repair_body['repair_digest'] = digest(repair_body)
                        repair_path = paths.project_records_dir / 'tasks' / (
                            f"asr_edge_repair_{pid}_{repair_body['repair_digest'][:12]}.json")
                        if not repair_path.exists():
                            with workspace_lock(paths):
                                atomic_json(repair_path, repair_body)
                        cursor['asr_edge_repair_request'] = repair_body
                        with workspace_lock(paths):
                            atomic_json(cursor_path, cursor)
                        checkpoint(paths, pid, encoding, 'REPAIRABLE', plan_ref,
                                   reason='asr_cutpoint_requires_manual_review')
                        return {'product_id': pid, 'status': 'REPAIRABLE_ASR_CUTPOINT',
                                'repair_reference': paths.reference(repair_path),
                                'rollback_to': 'local_audio_cutpoint_review',
                                'vision_audited': True, 'semantic_rerun': False}
                    errors = plan_errors(plan_body)
                    if errors == ['AUDIO_BOUNDARY_UNVERIFIED']:
                        plan_body = load_stage(paths, plan_ref)['body']
                        affected = [{'variant_id': variant['variant_id'], 'unit_id': clip.get('unit_id'),
                                     'edges': {name: value for name, value in
                                               (clip.get('owned_audio_review', {}).get('edges') or {}).items()
                                               if value.get('status') == 'REPAIRABLE'}}
                                    for variant in plan_body.get('variants', []) for clip in variant.get('clips', [])
                                    if not clip.get('audio_boundary_verified')]
                        repair_body = {'schema_version': 1, 'failed_stage': 'audio_cutpoint',
                            'rollback_to': 'audio_boundary_refinement',
                            'invalidate': ['plan', 'vision', 'final_audit'],
                            'preserve': ['range', 'candidate', 'rough_selection', 'asr', 'semantic_map'],
                            'plan_reference': plan_ref, 'affected_edges': affected,
                            'semantic_rerun': False}
                        repair_body['repair_digest'] = digest(repair_body)
                        prior_audio = cursor.get('audio_repair_request') or {}
                        repair_path = paths.project_records_dir / 'tasks' / (
                            f"audio_repair_{pid}_{repair_body['repair_digest'][:12]}.json")
                        if not repair_path.exists():
                            with workspace_lock(paths):
                                atomic_json(repair_path, repair_body)
                        repeated = prior_audio.get('repair_digest') == repair_body['repair_digest']
                        if not repeated:
                            cursor['audio_repair_request'] = repair_body
                            cursor.pop('plan', None)
                            cursor.pop('vision', None)
                            cursor.pop('final_audit', None)
                            with workspace_lock(paths):
                                atomic_json(cursor_path, cursor)
                        checkpoint(paths, pid, encoding, 'REPAIRABLE', plan_ref,
                                   reason='audio_cutpoint_requires_local_retry')
                        return {'product_id': pid, 'status': 'REPAIRABLE_AUDIO_CUTPOINT',
                                'repair_reference': paths.reference(repair_path),
                                'rollback_to': 'audio_boundary_refinement',
                                'semantic_rerun': False, 'repeated_same_failure': repeated}
                    if not errors:
                        cleared_edge = cursor.pop('asr_edge_repair_request', None)
                        cleared_audio = cursor.pop('audio_repair_request', None)
                        if cleared_edge is not None or cleared_audio is not None:
                            with workspace_lock(paths):
                                atomic_json(cursor_path, cursor)
                        break
                    else:
                        repair_issues = errors
                        failure_stage = 'hard_gate'
                failure = record_repair_failure(failure_stage, repair_issues)
                if failure:
                    return failure
            vision_ref = stage('vision', lambda: vision_audit(
                paths, plan_ref, detector, allow_vfr_approximate=allow_vfr_approximate))
            semantic_review_ref = cursor.get('semantic_review')
            if not semantic_review_ref:
                raise CacheIdentityError('semantic_selection_review_missing_after_local_audits')
            evidence = stage('final_audit', lambda: import_final_review(
                paths, plan_ref, vision_ref, semantic_review_ref=semantic_review_ref))
            checkpoint(paths, pid, encoding, 'FINAL_PASS', plan_ref, evidence)
            return {'product_id': pid, 'status': 'FINAL_PASS'}
        except Exception as exc:
            reason = str(exc)[:180]
            checkpoint(paths, pid, encoding, 'BLOCKED', reason=reason)
            return {'product_id': pid, 'status': 'BLOCKED', 'reason': reason}
        finally:
            # Product-only timing; no candidate text, transcript or chat events in controller summaries.
            save_product_metrics(paths, pid, {'started_at_epoch': run_started_at,
                'elapsed_seconds': round(time.perf_counter()-resumed_at, 3), 'stages': metrics,
                'compute': detailed.rows, 'repair_attempts': cursor.get('repair_attempts', 0)})
            active_scope.__exit__(None, None, None)


def project_audit(paths, product_ids=None):
    state = paths.load_state()
    skipped = {int(value) for value in state.get('batch', {}).get('failed_product_ids', [])}
    values = [value for value in targets(paths) if value['product_id'] not in skipped]
    if product_ids is not None:
        requested = {int(pid) for pid in product_ids}
        allowed = {value['product_id'] for value in values}
        if not requested or not requested.issubset(allowed):
            raise CacheIdentityError('project_audit_product_scope_invalid')
        values = [value for value in values if value['product_id'] in requested]
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
    return {'schema_version': 3, 'target_count': len(values), 'final_pass_count': len(passed),
            'final_review_complete': not errors, 'written_count': sum(r['status'] in ('WRITTEN', 'EXPORT_VERIFIED') for r in rows.values()),
            'export_verified_count': sum(r['status'] == 'EXPORT_VERIFIED' for r in rows.values()),
            'delivery_complete': False, 'delivery_gate': 'BLOCKED_UNMIGRATED_RESOLVE_EXPORT_VERIFIERS',
                'errors': sorted(set(errors))}


def _run_project(paths, *, group_size=3, mode='package', detector=None, include_cross_boundary_cues=False,
                 allow_vfr_approximate=False, preferred_fps=None, repair_limit=2,
                 allow_partial_delivery=False):
    manifest = schedule(paths, group_size)
    by_id = {v['product_id']: v for v in manifest['targets']}
    processed = []
    for group in manifest['groups']:
        results = [process_product(paths, by_id[pid], mode=mode, detector=detector,
                                   include_cross_boundary_cues=include_cross_boundary_cues,
                                   allow_vfr_approximate=allow_vfr_approximate,
                                   preferred_fps=preferred_fps, repair_limit=repair_limit) for pid in group]
        processed.append({'products': group, 'statuses': [r['status'] for r in results]})
    delivery_ids = [row['product_id'] for row in manifest['targets']]
    if allow_partial_delivery:
        ledger_rows = resume(paths)
        delivery_ids = [pid for pid in delivery_ids
                        if ledger_rows[pid]['status'] in ('FINAL_PASS', 'WRITTEN', 'EXPORT_VERIFIED')]
    with span('project_quality_gate'):
        result = {**(project_audit(paths, product_ids=delivery_ids) if delivery_ids else {
                      'final_review_complete': False, 'errors': ['NO_FINAL_PASS_PRODUCTS'],
                      'target_count': 0, 'final_pass_count': 0}),
                  'groups_processed': processed,
                  'batch_target_count': len(manifest['targets']),
                  'pending_product_ids': [row['product_id'] for row in manifest['targets']
                                          if row['product_id'] not in delivery_ids]}
    with workspace_lock(paths):
        atomic_json(paths.project_records_dir / 'project_audit_v3.json', result)
    return result


def run_project(paths, *, group_size=3, mode='package', detector=None, include_cross_boundary_cues=False,
                allow_vfr_approximate=False, preferred_fps=None, repair_limit=2,
                allow_partial_delivery=False):
    metrics = Metrics()
    with scope(metrics):
        try:
            with span('project_total'):
                return _run_project(paths, group_size=group_size, mode=mode, detector=detector,
                                    include_cross_boundary_cues=include_cross_boundary_cues,
                                    allow_vfr_approximate=allow_vfr_approximate,
                                    preferred_fps=preferred_fps, repair_limit=repair_limit,
                                    allow_partial_delivery=allow_partial_delivery)
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
    parser.add_argument('--allow-partial-delivery', action='store_true',
                        help='Audit and deliver only products already at FINAL_PASS; leave blocked products pending')
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
        preferred_fps=args.preferred_fps, repair_limit=args.repair_limit,
        allow_partial_delivery=args.allow_partial_delivery)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['final_review_complete'] else 2)


if __name__ == '__main__':
    main()
