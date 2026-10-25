import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workflow_scheduler import (WORKFLOWS, SchedulerError, batch_product_scope,
                                diagnose, early_delivery_preflight, selected_delivery_binding,
                                selected_delivery_timeline,
                                test_reuse_capability)
from unittest.mock import patch, Mock
from types import SimpleNamespace


class WorkflowDeliveryCapabilityTests(unittest.TestCase):
    def test_taobao_declares_registered_default_and_selected_target_modes(self):
        workflow = WORKFLOWS["taobao_legacy"]
        self.assertIn("registered_target_append", workflow["delivery_modes"])
        self.assertIn("selected_existing_timeline_append", workflow["delivery_modes"])
        self.assertIn("reusable_test_timeline", workflow["delivery_modes"])
        self.assertTrue(test_reuse_capability("taobao_legacy"))

    def test_taobao_plan_declares_the_real_person_and_asr_stage_order(self):
        stages = WORKFLOWS["taobao_legacy"]["stages"]
        expected = ["selection_person_prescreen", "local_asr", "physical_plan",
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
        state = {"resolve": {"project_name": "20261021-淘宝"}}
        with patch('workflow_scheduler.workflow_for_state',
                   return_value=(state, 'taobao_legacy', WORKFLOWS['taobao_legacy'])):
            result = diagnose(SimpleNamespace(load_state=lambda: state))
        self.assertIsNone(result['batch_id'])
        self.assertIsNone(result['rollback_to'])
        self.assertIn('no_active_batch', result['notes'][0])

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
