"""Synthetic project/task integration; no Resolve, media or model execution."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import test_project_resume as fixtures
import project_task as tasks
from project_registry import atomic_json
from runtime_paths import resolve_runtime_paths
import workflow_scheduler as scheduler


class ProjectTaskTests(unittest.TestCase):
    setUp = fixtures.ProjectResumeTests.setUp
    register = fixtures.ProjectResumeTests.register

    def init(self):
        return tasks.initialize(self.paths, '完成当前项目约定的交付，不改动其他项目')

    def task(self, **overrides):
        return {'id': 'T1', 'goal': '指定商品单次流程验收', 'acceptance_ids': ['A1', 'A2'],
                'batch_id': 'batch-a', 'product_ids': [1], 'operations': ['default'],
                'max_runs': 1, 'max_repairs_per_run': 1, **overrides}

    def write_tx(self, status='FAILED_VERIFICATION'):
        file = self.paths.cache_dir / 'resolve_delivery' / 'batch-a' / 'resolve_append_transaction_1.json'
        atomic_json(file, {'status': status, 'products': [1]})
        return file

    def test_five_docs_and_control_only_in_canonical_project(self):
        self.init()
        directory = self.paths.project_directory
        self.assertTrue(all((directory / name).is_file() for name in (*tasks.DOCS, tasks.RECORD)))
        self.assertFalse(any((self.paths.workspace_root / name).exists() for name in tasks.DOCS))
        self.assertFalse(any((self.repo / name).exists() for name in tasks.DOCS))
        self.assertEqual(tasks.load(self.paths)['identity']['resolve_project_id'], 'resolve-uuid-a')
        self.assertEqual(tasks.snapshot(self.paths)['project_completion'], 'NOT_EVALUATED')

    def test_refresh_preserves_authored_docs_and_initialization_refuses_overwrite(self):
        self.init()
        file = self.paths.project_directory / 'PROJECT.md'
        file.write_text(file.read_text() + '\n用户补充目标。\n')
        before = file.read_bytes()
        tasks.refresh(self.paths)
        self.assertEqual(file.read_bytes(), before)
        with self.assertRaisesRegex(tasks.TaskError, 'refuse_overwrite'):
            self.init()

    def test_new_batch_inherits_hold_without_copying_docs(self):
        self.init()
        tasks.record_hold(self.paths, ['run', 'recover', 'write'], '用户要求暂不恢复')
        from create_batch_workspace import create_batch_workspace
        destination = create_batch_workspace(self.repo, self.paths.project_directory, 'batch-b', [1])
        other_batch = resolve_runtime_paths(self.repo, destination)
        with self.assertRaisesRegex(tasks.TaskError, 'project_task_paused'):
            tasks.guard(other_batch, 'recover')
        self.assertFalse((destination / 'PROJECT.md').exists())
        tasks.record_hold(other_batch, ['recover'], '用户明确解除恢复限制', release=True)
        # Releasing one action never implicitly releases a project-wide run pause.
        with self.assertRaises(tasks.TaskError):
            tasks.guard(other_batch, 'recover')

    def test_other_project_cannot_inherit_identity_or_controls(self):
        self.init()
        original = tasks.load(self.paths)
        other = resolve_runtime_paths(self.repo, self.base / 'workspace-b')
        self.register(other, 'resolve-uuid-b')
        self.assertIsNone(tasks.load(other))
        other.project_directory.joinpath(tasks.RECORD).write_text(json.dumps(original))
        with self.assertRaisesRegex(tasks.TaskError, 'identity_invalid'):
            tasks.load(other)

    def test_deleted_or_incomplete_contract_fails_closed(self):
        self.init()
        (self.paths.project_directory / tasks.RECORD).unlink()
        with self.assertRaisesRegex(tasks.TaskError, 'initialization_incomplete'):
            tasks.load(self.paths)

    def test_doc_edits_stale_plan_but_generated_status_does_not(self):
        self.init()
        before = tasks.plan_identity(self.paths)
        tasks.refresh(self.paths)
        self.assertEqual(tasks.plan_identity(self.paths), before)
        file = self.paths.project_directory / 'ACCEPTANCE.md'
        file.write_text(file.read_text() + '\n新标准。\n')
        self.assertNotEqual(tasks.plan_identity(self.paths), before)

    def test_failed_transaction_and_written_checkpoint_never_become_complete(self):
        self.init()
        file = self.write_tx()
        before = file.read_bytes()
        atomic_json(self.paths.project_directory / 'records/checkpoints.json', {
            'schema_version': 2, 'project_key': self.paths.project_key,
            'products': {'1': {'status': 'WRITTEN'}}})
        body = tasks.refresh(self.paths)
        self.assertEqual(body['project_completion'], 'BLOCKED')
        self.assertEqual(body['products']['1']['recorded_status'], 'WRITTEN')
        self.assertFalse(body['products']['1']['evidence_revalidated'])
        self.assertEqual(file.read_bytes(), before)
        tasks.add_task(self.paths, self.task())
        with self.assertRaisesRegex(tasks.TaskError, 'unverified_transaction'):
            tasks.begin_run(self.paths, 'default', 1)

    def test_explicit_test_timeline_clear_closes_only_same_target_history(self):
        self.init()
        tasks.add_task(self.paths, self.task())
        test_tx = self.write_tx()
        test_report = test_tx.parent / 'write_report.json'
        atomic_json(test_report, {'target_timeline_id': 'test-id'})
        atomic_json(test_tx, {'status': 'FAILED_VERIFICATION', 'products': [1],
                              'report': str(test_report)})
        final_dir = self.paths.project_directory / 'batches' / 'old-final' / 'resolve_delivery'
        final_dir.mkdir(parents=True)
        final_tx = final_dir / 'resolve_append_transaction_34.json'
        final_report = final_dir / 'write_report.json'
        atomic_json(final_report, {'target_timeline_id': 'formal-id'})
        atomic_json(final_tx, {'status': 'FAILED_VERIFICATION', 'products': [34],
                               'report': str(final_report)})
        override = self.paths.workspace_root / 'delivery' / 'USER_OVERRIDE.json'
        atomic_json(override, {'confirmed_by_user': True, 'resolve_write_authorization': {
            'action': 'clear_configured_target_and_append', 'clear_existing': True,
            'project_id': 'resolve-uuid-a', 'target_timeline_id': 'test-id',
            'product_ids': [1]}})
        run_id = tasks.begin_run(self.paths, 'default', 1, target_timeline_id='test-id')
        clear_tx = self.paths.project_directory / 'batches' / 'batch-a' / 'resolve_delivery' / 'reusable_test_prepare_transaction.json'
        atomic_json(clear_tx, {'status': 'READBACK_VERIFIED', 'target_timeline_id': 'test-id'})
        closed = tasks.supersede_transactions_after_clear(self.paths, 'test-id', clear_tx)
        self.assertEqual([row['transaction_path'] for row in closed],
                         [str(test_tx.relative_to(self.paths.project_directory))])
        self.assertEqual(json.loads(test_tx.read_text())['status'], 'FAILED_VERIFICATION')
        blockers = tasks.snapshot(self.paths)['blockers']
        self.assertEqual([row['path'] for row in blockers if row['kind'] == 'transaction'],
                         [str(final_tx.relative_to(self.paths.project_directory))])
        with self.assertRaisesRegex(tasks.TaskError, 'unverified_transaction'):
            tasks.begin_run(self.paths, 'default', 1, target_timeline_id='formal-id')
        tasks.finish_run(self.paths, run_id, 0)

    def test_live_no_mutation_disposition_preserves_prepare_and_formal_failure_states(self):
        self.init()
        target_snapshot = {'id': 'test-id', 'name': '测试', 'rows': [], 'markers': []}
        test_tx = self.paths.project_directory / 'batches' / 'batch-a' / 'resolve_delivery' / 'reusable_test_prepare_transaction.json'
        atomic_json(test_tx, {'status': 'CLEAR_IN_PROGRESS', 'project_id': 'resolve-uuid-a',
                              'target_timeline_id': 'test-id', 'backup_sha256': 'backup-digest',
                              'before': target_snapshot})
        original_test_tx = test_tx.read_bytes()
        proof_path = test_tx.with_name('reusable_test_prepare_no_mutation_verification.json')
        atomic_json(proof_path, {
            'schema_version': 1, 'status': 'NO_MUTATION_VERIFIED',
            'transaction_path': str(test_tx.relative_to(self.paths.project_directory)),
            'transaction_sha256': __import__('hashlib').sha256(original_test_tx).hexdigest(),
            'original_status': 'CLEAR_IN_PROGRESS', 'project_id': 'resolve-uuid-a',
            'target_timeline_id': 'test-id', 'before': target_snapshot,
            'live_snapshot': target_snapshot,
            'before_sha256': tasks.digest(target_snapshot),
            'live_snapshot_sha256': tasks.digest(target_snapshot),
            'backup_sha256': 'backup-digest', 'backup_verified': True,
        })
        formal_dir = self.paths.project_directory / 'batches' / 'old-final' / 'resolve_delivery'
        formal_tx = formal_dir / 'resolve_append_transaction_34.json'
        atomic_json(formal_tx, {'status': 'FAILED_VERIFICATION', 'target_timeline_id': 'formal-id',
                                'products': [34]})
        original_formal_tx = formal_tx.read_bytes()

        disposition = tasks.record_no_mutation_disposition(self.paths, proof_path)
        self.assertEqual(disposition['resolution_type'], 'no_mutation_verified')
        self.assertEqual(test_tx.read_bytes(), original_test_tx)
        self.assertEqual(formal_tx.read_bytes(), original_formal_tx)
        rows = tasks.transactions(self.paths)
        test_row = next(row for row in rows if row['path'] == str(test_tx.relative_to(self.paths.project_directory)))
        formal_row = next(row for row in rows if row['path'] == str(formal_tx.relative_to(self.paths.project_directory)))
        self.assertEqual(test_row['status'], 'CLEAR_IN_PROGRESS')
        self.assertIn('no_mutation_verified', test_row)
        self.assertEqual(formal_row['status'], 'FAILED_VERIFICATION')
        self.assertNotIn('no_mutation_verified', formal_row)

        tasks.add_task(self.paths, self.task())
        run_id = tasks.begin_run(self.paths, 'default', 1, target_timeline_id='test-id')
        tasks.finish_run(self.paths, run_id, 2)
        with self.assertRaisesRegex(tasks.TaskError, 'unverified_transaction'):
            tasks.begin_run(self.paths, 'default', 1, target_timeline_id='formal-id')

    def test_budget_is_project_wide_and_persists_after_success(self):
        self.init()
        tasks.add_task(self.paths, self.task())
        token = tasks.begin_run(self.paths, 'default', 1)
        with self.assertRaisesRegex(tasks.TaskError, 'in_progress_or_interrupted'):
            tasks.begin_run(self.paths, 'default', 1)
        tasks.finish_run(self.paths, token, 0)
        self.assertEqual(tasks.snapshot(self.paths)['project_completion'], 'NOT_EVALUATED')
        with self.assertRaisesRegex(tasks.TaskError, 'budget_exhausted'):
            tasks.begin_run(self.paths, 'default', 1)
        self.assertEqual(tasks.load(self.paths)['runs'][0]['status'], 'PASS')

    def test_failed_and_interrupted_runs_consume_budget(self):
        self.init()
        tasks.add_task(self.paths, self.task(max_runs=2))
        token = tasks.begin_run(self.paths, 'default', 1)
        tasks.finish_run(self.paths, token, 2)
        token = tasks.begin_run(self.paths, 'default', 1)
        tasks.finish_run(self.paths, token, None, interrupted=True, reason='test')
        with self.assertRaisesRegex(tasks.TaskError, 'budget_exhausted'):
            tasks.begin_run(self.paths, 'default', 1)

    def test_scope_operation_and_repair_limit_checked_before_execution(self):
        self.init()
        tasks.add_task(self.paths, self.task())
        for operation, limit, error in [('recover-append', 1, 'scope_or_operation'),
                                        ('default', 2, 'repair_budget')]:
            with self.assertRaisesRegex(tasks.TaskError, error):
                tasks.begin_run(self.paths, operation, limit)
        self.assertEqual(tasks.load(self.paths)['runs'], [])

    def test_invalid_task_never_changes_record(self):
        self.init()
        before = (self.paths.project_directory / tasks.RECORD).read_bytes()
        for overrides in ({'max_runs': 0}, {'batch_id': '../escape'}, {'acceptance_ids': ['unknown']}):
            with self.assertRaises(tasks.TaskError):
                tasks.add_task(self.paths, self.task(**overrides))
            self.assertEqual((self.paths.project_directory / tasks.RECORD).read_bytes(), before)

    def test_holds_block_scheduler_before_any_delegated_work(self):
        self.init()
        tasks.record_hold(self.paths, ['run'], '暂停真实流程')
        with patch.object(scheduler, 'workflow_for_state', return_value=({}, 'taobao_legacy', {})), \
             patch.object(scheduler, 'require_current_plan'), \
             patch.object(scheduler, 'selected_delivery_binding', return_value={'timeline_id': None}), \
             patch.object(scheduler, '_run', side_effect=AssertionError('must not start')):
            with self.assertRaisesRegex(tasks.TaskError, 'project_task_paused'):
                scheduler.run(self.paths)

    def test_scheduler_full_mock_run_records_failure_and_handoff(self):
        self.init()
        tasks.add_task(self.paths, self.task())
        with patch.object(scheduler, 'workflow_for_state', return_value=({}, 'taobao_legacy', {})), \
             patch.object(scheduler, 'require_current_plan'), \
             patch.object(scheduler, 'selected_delivery_binding', return_value={'timeline_id': None}), \
             patch.object(scheduler, '_run', return_value=2) as worker:
            self.assertEqual(scheduler.run(self.paths, repair_limit=1), 2)
            worker.assert_called_once()
        self.assertEqual(tasks.load(self.paths)['runs'][0]['status'], 'FAILED')
        self.assertIn('FAILED', (self.paths.project_directory / 'STATUS.md').read_text())

    def test_scheduler_exception_is_saved_as_failure_without_replay(self):
        self.init()
        tasks.add_task(self.paths, self.task())
        with patch.object(scheduler, 'workflow_for_state', return_value=({}, 'taobao_legacy', {})), \
             patch.object(scheduler, 'require_current_plan'), \
             patch.object(scheduler, 'selected_delivery_binding', return_value={'timeline_id': None}), \
             patch.object(scheduler, '_run', side_effect=RuntimeError('test interruption')):
            with self.assertRaises(RuntimeError):
                scheduler.run(self.paths, repair_limit=1)
        self.assertEqual(tasks.load(self.paths)['runs'][0]['status'], 'FAILED')

    def test_plan_fingerprint_stays_stable_across_run_accounting(self):
        self.init()
        tasks.add_task(self.paths, self.task())
        identity = tasks.plan_identity(self.paths)
        token = tasks.begin_run(self.paths, 'default', 1)
        tasks.finish_run(self.paths, token, 2)
        self.assertEqual(tasks.plan_identity(self.paths), identity)

    def test_status_is_not_a_source_of_truth(self):
        self.init()
        (self.paths.project_directory / 'STATUS.md').write_text('全部完成，允许恢复')
        tasks.record_hold(self.paths, ['recover'], '暂停恢复')
        with self.assertRaises(tasks.TaskError):
            tasks.guard(self.paths, 'recover')
        self.assertNotIn('全部完成', (self.paths.project_directory / 'STATUS.md').read_text())

    def test_document_change_during_run_blocks_next_stage(self):
        self.init()
        tasks.add_task(self.paths, self.task())
        token = tasks.begin_run(self.paths, 'default', 1)
        tasks.guard(self.paths, 'write')
        file = self.paths.project_directory / 'PLAN.md'
        file.write_text(file.read_text() + '\n改变目标。\n')
        with self.assertRaisesRegex(tasks.TaskError, 'plan_changed_during_run'):
            tasks.guard(self.paths, 'write')
        tasks.finish_run(self.paths, token, None, interrupted=True)

    def test_recovery_hold_cannot_be_bypassed_by_direct_writer(self):
        self.init()
        tasks.record_hold(self.paths, ['recover'], '暂不恢复')
        import resolve_write_v2
        import delivery_resolve
        argv = ['writer', '--root', str(self.repo), '--workspace', str(self.paths.workspace_root), '--recover-append']
        with patch('sys.argv', argv):
            with self.assertRaisesRegex(tasks.TaskError, 'project_task_paused'):
                resolve_write_v2.main()
        with self.assertRaisesRegex(tasks.TaskError, 'project_task_paused'):
            delivery_resolve.perform(self.paths, {}, {}, {}, {}, None, operation='recover')

    def test_project_lock_rejects_second_writer(self):
        self.init()
        with tasks.locked(self.paths):
            with self.assertRaisesRegex(tasks.TaskError, 'task_record_busy'):
                tasks.record_hold(self.paths, ['run'], 'must not race')

    def test_symlink_document_is_rejected_without_writing_external_file(self):
        self.init()
        file = self.paths.project_directory / 'STATUS.md'
        other = self.base / 'outside.md'
        other.write_text('untouched')
        file.unlink()
        file.symlink_to(other)
        with self.assertRaisesRegex(tasks.TaskError, 'symlink'):
            tasks.refresh(self.paths)
        self.assertEqual(other.read_text(), 'untouched')

    def test_direct_delivery_entrypoints_honor_hold(self):
        self.init()
        tasks.record_hold(self.paths, ['write'], '不写入')
        import resolve_write_v2
        import resolve_semantic_preview
        import reusable_test_delivery
        import delivery_resolve
        for module, extra in ((resolve_write_v2, []), (resolve_semantic_preview, []),
                              (reusable_test_delivery, ['prepare'])):
            argv = [module.__name__, *extra, '--root', str(self.repo),
                    '--workspace', str(self.paths.workspace_root)]
            with self.subTest(module=module.__name__), patch('sys.argv', argv):
                with self.assertRaisesRegex(tasks.TaskError, 'project_task_paused'):
                    module.main()
        with self.assertRaisesRegex(tasks.TaskError, 'project_task_paused'):
            delivery_resolve.perform(self.paths, {}, {}, {}, {}, None, operation='write')
        import resolve_trial
        with self.assertRaisesRegex(tasks.TaskError, 'project_task_paused'):
            resolve_trial.write_product(self.paths, None, {}, 1)

    def test_cleanup_protects_long_task_records_and_parent(self):
        self.init()
        from cleanup import load_protected_paths, safe_target
        protected = load_protected_paths(self.repo)
        for name in (*tasks.DOCS, tasks.RECORD):
            rel = str((self.paths.project_directory / name).relative_to(self.repo.resolve()))
            with self.assertRaisesRegex(RuntimeError, 'Protected path'):
                safe_target(self.repo, rel, protected)
        with self.assertRaisesRegex(RuntimeError, 'Protected path'):
            safe_target(self.repo, '.cache/projects', protected)

    def test_live_identity_must_match_explicit_workspace(self):
        response = type('Response', (), {'stdout': '{"ready":true,"project_id":"other-project"}', 'returncode': 0})()
        with patch.object(tasks.subprocess, 'run', return_value=response):
            with self.assertRaisesRegex(tasks.TaskError, 'live_project_mismatch'):
                tasks.live_paths(self.repo, self.paths.workspace_root)

    def test_fresh_live_identity_failure_does_not_choose_old_workspace(self):
        response = type('Response', (), {'stdout': '{"ready":false,"reason":"app_not_running"}', 'returncode': 1})()
        with patch.object(tasks.subprocess, 'run', return_value=response) as call:
            with self.assertRaisesRegex(tasks.TaskError, 'live_identity_unavailable'):
                tasks.live_paths(self.repo)
            self.assertIn('--fresh-identity', call.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
