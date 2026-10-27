import sys
import json
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workflow_scheduler import (WORKFLOWS, SchedulerError, batch_product_scope,
                                diagnose, early_delivery_preflight, selected_delivery_binding,
                                selected_delivery_timeline, plan as scheduler_plan,
                                test_reuse_capability, failure_repair_card,
                                diagnose_cursor_path)
from unittest.mock import patch, Mock
from types import SimpleNamespace
from project_registry import digest


class WorkflowDeliveryCapabilityTests(unittest.TestCase):
    def test_blind_fresh_diagnose_uses_batch_cache_cursor(self):
        paths = SimpleNamespace(cache_dir=Path('/workspace/.cache/batch'),
                                project_records_dir=Path('/workspace/.cache/projects/project'))
        self.assertEqual(diagnose_cursor_path(paths, 39, 'blind_fresh'),
                         Path('/workspace/.cache/batch/cursors/39.json'))
        self.assertEqual(diagnose_cursor_path(paths, 39, 'resume_valid_evidence'),
                         Path('/workspace/.cache/projects/project/cursors/39.json'))

    def test_diagnose_reads_the_blind_fresh_cursor_not_stale_project_cursor(self):
        import workflow_scheduler as scheduler
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / 'batch'
            cache = root / '.cache' / 'batch'
            project_records = root / '.cache' / 'projects' / 'project'
            (cache / 'cursors').mkdir(parents=True)
            (project_records / 'cursors').mkdir(parents=True)
            state = {'batch': {'batch_id': 'batch-a', 'product_ids': [39]}}
            plan_body = {'project_key': 'project-key', 'batch_id': 'batch-a',
                         'mode': 'taobao_legacy', 'selection_mode': 'blind_fresh'}
            workspace.mkdir()
            (workspace / 'SCHEDULER_PLAN.json').write_text(json.dumps({
                'plan': plan_body, 'plan_digest': digest(plan_body)}))
            batch_cursor = {'candidate': 'candidate-ref', 'rough': 'rough-ref',
                            'repair_attempts': 1, 'repair_history': [{'attempt': 1}]}
            (cache / 'cursors' / '39.json').write_text(json.dumps(batch_cursor))
            (project_records / 'cursors' / '39.json').write_text(json.dumps({'plan': 'stale-plan'}))
            paths = SimpleNamespace(
                workspace_root=workspace, cache_dir=cache,
                project_records_dir=project_records,
                registered_scope=lambda: ('project-key', 'batch-a'),
                load_state=lambda: state)
            with patch.object(scheduler, 'workflow_for_state', return_value=(
                    state, 'taobao_legacy', WORKFLOWS['taobao_legacy'])), \
                 patch('product_checkpoints.resume', return_value={39: {
                     'status': 'REPAIRABLE', 'reason': 'source overlap',
                     'resume_required': True}}):
                result = scheduler._diagnose(paths)
            product = result['products']['39']
            self.assertEqual(product['selection_mode'], 'blind_fresh')
            self.assertEqual(product['repair_attempts'], 1)
            self.assertEqual(product['last_reusable_stages'], ['candidate', 'rough'])
            self.assertEqual(Path(product['cursor_path']), cache / 'cursors' / '39.json')

    def test_waiting_for_boundary_evidence_has_bounded_action_card(self):
        card = failure_repair_card({
            'status': 'FAILED', 'stage': 'batch_semantic_range_matching',
            'error': 'project_range_matching_waiting_for_evidence_product_33:'
                     'one_shot_adjacent_context_insufficient'})
        self.assertEqual(card['status'], 'WAITING_FOR_EVIDENCE')
        self.assertEqual(card['product_id'], '33')
        self.assertEqual(card['attempts_used'], 1)
        self.assertFalse(card['same_input_retry'])
        self.assertIn('前后商品', card['action_required'])
        self.assertIn('整体商品结束锚点缺失本身不应阻断', card['action_required'])

    def test_ordinary_scheduler_exception_is_failed_not_interrupted(self):
        import workflow_scheduler as scheduler
        import project_task
        paths = SimpleNamespace(load_state=lambda: {'batch': {'batch_id': 'batch'}},
                                repo_root=Path('/repo'))
        with patch.object(scheduler, 'workflow_for_state', return_value=(
                {}, 'taobao_legacy', WORKFLOWS['taobao_legacy'])), \
             patch.object(scheduler, 'require_current_plan'), \
             patch.object(scheduler, 'selected_delivery_binding',
                          return_value={'timeline_id': 'timeline'}), \
             patch.object(project_task, 'begin_run', return_value='run-id'), \
             patch.object(project_task, 'finish_run') as finish, \
             patch.object(scheduler, '_run', side_effect=RuntimeError('stage failed')):
            with self.assertRaisesRegex(RuntimeError, 'stage failed'):
                scheduler.run(paths, operation='default')
        finish.assert_called_once_with(paths, 'run-id', 1,
                                       interrupted=False, reason='RuntimeError')

    def test_keyboard_interrupt_remains_interrupted(self):
        import workflow_scheduler as scheduler
        import project_task
        paths = SimpleNamespace(load_state=lambda: {'batch': {'batch_id': 'batch'}},
                                repo_root=Path('/repo'))
        with patch.object(scheduler, 'workflow_for_state', return_value=(
                {}, 'taobao_legacy', WORKFLOWS['taobao_legacy'])), \
             patch.object(scheduler, 'require_current_plan'), \
             patch.object(scheduler, 'selected_delivery_binding',
                          return_value={'timeline_id': 'timeline'}), \
             patch.object(project_task, 'begin_run', return_value='run-id'), \
             patch.object(project_task, 'finish_run') as finish, \
             patch.object(scheduler, '_run', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                scheduler.run(paths, operation='default')
        finish.assert_called_once_with(paths, 'run-id', None,
                                       interrupted=True, reason='KeyboardInterrupt')

    def test_stage_ledger_distinguishes_failure_from_interruption(self):
        import json
        import tempfile
        import workflow_scheduler as scheduler
        import project_task
        for exception, expected in ((RuntimeError('failed'), 'FAILED'),
                                    (KeyboardInterrupt(), 'INTERRUPTED')):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                paths = SimpleNamespace(
                    load_state=lambda: {'batch': {'batch_id': 'batch'}},
                    registered_scope=lambda: ('project',),
                    workspace_root=root / 'workspace', project_records_dir=root / 'records')
                with patch.object(project_task, 'guard'), \
                     self.assertRaises(type(exception)):
                    scheduler.execute_recorded(paths, 'taobao_legacy', 'synthetic-stage',
                                               lambda: (_ for _ in ()).throw(exception))
                records = list((root / 'records' / 'scheduler_runs').glob('*.json'))
                event = json.loads(records[0].read_text())['events'][-1]
                self.assertEqual(event['status'], expected)

    def test_plan_binds_parallel_batch_to_one_delivery_product(self):
        state = {
            "batch": {"batch_id": "b1", "product_ids": [4, 9]},
            "project_identity": {"project_key": "pk", "config_digest": "cfg",
                                 "resolve_project_id": "project-id"},
            "resolve": {"project_name": "淘宝", "source_timeline_id": "source",
                        "target_timeline_id": "formal", "target_timeline": "成片"},
            "runtime": {"test_timeline_binding": {
                "project_id": "project-id", "name": "测试", "timeline_id": "test"}},
        }
        paths = SimpleNamespace(load_state=lambda: state, project_key=None)
        with patch('workflow_scheduler.workflow_for_state',
                   return_value=(state, 'taobao_legacy', WORKFLOWS['taobao_legacy'])):
            result = scheduler_plan(paths, '测试', delivery_product_id=9)
            self.assertEqual(result['products'], [4, 9])
            self.assertEqual(result['delivery_product_id'], 9)
            with self.assertRaisesRegex(SchedulerError, 'delivery_product_outside_batch'):
                scheduler_plan(paths, '测试', delivery_product_id=3)

    def test_run_internal_plan_check_keeps_delivery_product_scope(self):
        state = {"batch": {"batch_id": "b1", "product_ids": [4, 9]}}
        paths = SimpleNamespace(load_state=lambda: state, repo_root=Path('/repo'),
                                workspace_root=Path('/repo/batch'))
        with patch('workflow_scheduler.workflow_for_state',
                   return_value=(state, 'taobao_legacy', WORKFLOWS['taobao_legacy'])), \
             patch('workflow_scheduler.require_current_plan') as check, \
             patch('workflow_scheduler.selected_delivery_binding',
                   return_value={'name': '测试', 'timeline_id': 'test', 'mode': 'reusable_test_timeline'}):
            with self.assertRaisesRegex(SchedulerError, 'unsupported_taobao_operation'):
                __import__('workflow_scheduler')._run(
                    paths, target_timeline='测试', operation='synthetic-invalid',
                    delivery_product_id=9)
        check.assert_called_once_with(paths, 'taobao_legacy', '测试', False, 9)

    def test_taobao_declares_registered_default_and_selected_target_modes(self):
        workflow = WORKFLOWS["taobao_legacy"]
        self.assertIn("registered_target_append", workflow["delivery_modes"])
        self.assertIn("selected_existing_timeline_append", workflow["delivery_modes"])
        self.assertIn("reusable_test_timeline", workflow["delivery_modes"])
        self.assertTrue(test_reuse_capability("taobao_legacy"))

    def test_taobao_plan_declares_the_real_person_and_asr_stage_order(self):
        stages = WORKFLOWS["taobao_legacy"]["stages"]
        expected = ["selection_person_prescreen", "asr_nearby_cutpoint_execution", "physical_plan",
                    "final_person_audit", "resolve_delivery"]
        self.assertEqual([stage for stage in stages if stage in expected], expected)
        self.assertNotIn("selection_vision", stages)
        self.assertNotIn("asr", stages)
        self.assertNotIn("vision", stages)

    def test_delivery_pipeline_route_declares_reusable_test_timeline(self):
        workflow = WORKFLOWS["dudu_marked_range"]
        self.assertIn("reusable_test_timeline", workflow["delivery_modes"])
        self.assertTrue(test_reuse_capability("dudu_marked_range"))

    def test_unknown_mode_has_no_test_reuse_capability(self):
        self.assertFalse(test_reuse_capability("unknown"))

    def test_delivery_timeline_uses_explicit_per_run_selection(self):
        state = {"resolve": {"target_timeline": "成片"}}
        self.assertEqual(selected_delivery_timeline(state, "盲测 A"), "盲测 A")
        self.assertEqual(selected_delivery_timeline(state), "成片")

    def test_delivery_timeline_rejects_empty_selection(self):
        with self.assertRaises(SchedulerError):
            selected_delivery_timeline({"resolve": {"target_timeline": "成片"}}, " ")

    def test_registered_test_target_requires_its_own_stable_id(self):
        state = {
            "project_identity": {"resolve_project_id": "project-id"},
            "resolve": {"source_timeline_id": "source-id", "target_timeline": "成片",
                        "target_timeline_id": "formal-id"},
            "runtime": {"test_timeline_binding": {
                "project_id": "project-id", "name": "测试", "timeline_id": "test-id"}},
        }
        self.assertEqual(selected_delivery_binding(state, "测试"), {
            "name": "测试", "timeline_id": "test-id", "mode": "reusable_test_timeline"})
        state["runtime"]["test_timeline_binding"]["timeline_id"] = "formal-id"
        with self.assertRaisesRegex(SchedulerError, "cannot_alias_formal_target"):
            selected_delivery_binding(state, "测试")

    def test_semantic_range_scope_is_the_active_batch_not_full_catalog(self):
        state = {"batch": {"product_ids": [25]}}
        titles = {"products": [{"product_index": value} for value in range(1, 44)]}
        self.assertEqual(batch_product_scope(state, titles), {25})

    def test_semantic_range_scope_rejects_empty_or_unknown_batch(self):
        titles = {"products": [{"product_index": 25}]}
        with self.assertRaises(SchedulerError):
            batch_product_scope({"batch": {"product_ids": []}}, titles)
        with self.assertRaises(SchedulerError):
            batch_product_scope({"batch": {"product_ids": [26]}}, titles)

    def test_diagnose_reports_no_active_batch_without_checkpoint_access(self):
        paths = SimpleNamespace(repo_root=Path('/repo'))
        state = {"resolve": {"project_name": "Synthetic 淘宝"}}
        with patch('workflow_scheduler.workflow_for_state',
                   return_value=(state, 'taobao_legacy', WORKFLOWS['taobao_legacy'])):
            result = diagnose(SimpleNamespace(load_state=lambda: state))
        self.assertIsNone(result['batch_id'])
        self.assertIsNone(result['rollback_to'])
        self.assertIn('no_active_batch', result['notes'][0])

    def test_failed_append_blocks_normal_resume_before_checkpoint_access(self):
        import tempfile
        import json
        for status in ("FAILED_VERIFICATION", "WRITTEN", "APPEND_IN_PROGRESS", "STARTED"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                state = {"batch": {"batch_id": "batch"}}
                paths = SimpleNamespace(load_state=lambda: state,
                    registered_scope=lambda: ("project",), cache_dir=root,
                    workspace_root=root, repo_root=root, project_records_dir=root / "records")
                tx = root / "resolve_delivery" / "batch" / "resolve_append_transaction_1.json"
                tx.parent.mkdir(parents=True)
                tx.write_text(json.dumps({"status": status, "products": [1]}))
                with patch('workflow_scheduler.workflow_for_state', return_value=(
                        state, 'taobao_legacy', WORKFLOWS['taobao_legacy'])), \
                     patch('product_checkpoints.resume', side_effect=AssertionError("must not trust checkpoint")):
                    result = diagnose(paths)
                self.assertTrue(result['resume_required'])
                self.assertIsNone(result['resume_command'])
                self.assertEqual(result['pending_delivery_transactions'][0]['status'], status)

    def test_diagnose_returns_waiting_for_evidence_instead_of_generic_resume(self):
        import tempfile
        import json
        from project_registry import digest
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {'batch': {'batch_id': 'batch'}}
            paths = SimpleNamespace(load_state=lambda: state,
                registered_scope=lambda: ('project',), cache_dir=root,
                workspace_root=root, repo_root=root, project_records_dir=root / 'records')
            run_key = digest({'project': 'project', 'batch_id': 'batch',
                              'mode': 'taobao_legacy'})[:24]
            record = root / 'records' / 'scheduler_runs' / f'{run_key}.json'
            record.parent.mkdir(parents=True)
            record.write_text(json.dumps({'events': [{
                'stage': 'batch_semantic_range_matching', 'status': 'FAILED',
                'error': 'project_range_matching_waiting_for_evidence_product_33:'
                         'one_shot_adjacent_context_insufficient'}]}))
            with patch('workflow_scheduler.workflow_for_state', return_value=(
                    state, 'taobao_legacy', WORKFLOWS['taobao_legacy'])), \
                 patch('product_checkpoints.resume', return_value={}):
                result = diagnose(paths)
        self.assertIsNone(result['resume_command'])
        self.assertEqual(result['repair_plan']['status'], 'WAITING_FOR_EVIDENCE')
        self.assertEqual(result['repair_plan']['product_id'], '33')
        self.assertTrue(result['resume_required'])

    def test_preflight_checks_resolve_and_exact_selected_target_first(self):
        paths = SimpleNamespace(repo_root=Path('/repo'), workspace_root=Path('/repo/batch'))
        scripts = Path('/repo/scripts')
        with patch('workflow_scheduler.subprocess.run', return_value=Mock(returncode=0, stdout='', stderr='')) as run:
            self.assertEqual(early_delivery_preflight(paths, scripts, '测试'), 0)
        self.assertEqual(run.call_count, 2)
        first = run.call_args_list[0].args[0]
        second = run.call_args_list[1].args[0]
        self.assertIn('project_doctor.py', first[1])
        self.assertIn('resolve_connection_doctor.py', second[1])
        self.assertEqual(second[-2:], ['--target-timeline', '测试'])

    def test_preflight_stops_immediately_when_resolve_is_unavailable(self):
        paths = SimpleNamespace(repo_root=Path('/repo'), workspace_root=Path('/repo/batch'))
        scripts = Path('/repo/scripts')
        outcomes = [Mock(returncode=0, stdout='', stderr=''),
                    Mock(returncode=1, stdout='{"ready":false}', stderr='')]
        with patch('workflow_scheduler.subprocess.run', side_effect=outcomes) as run:
            self.assertEqual(early_delivery_preflight(paths, scripts, '测试'), 1)
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
