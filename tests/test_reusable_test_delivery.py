import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from reusable_test_delivery import (assert_authorized_target_clear, expected_frames,
                                    prepare_registered_test_timeline, verify_user_ranges)


class ReusableTestDeliveryTests(unittest.TestCase):
    def test_average_rate_mapping_uses_floor_start_and_ceil_end(self):
        clip = {"source_average_fps": 25.0, "source_start_seconds": 1.1,
                "source_end_seconds": 2.2}
        self.assertEqual(expected_frames(clip, 25.0), (27, 55, 28))
        self.assertEqual(expected_frames(clip, 30.0), (27, 55, 34))

    def test_requires_explicit_configured_target_replacement_authorization(self):
        body = {"id": "test-id", "name": "测试"}
        plan = {"products": [{"product_id": pid} for pid in (4, 7, 9)]}
        override = {"resolve_write_authorization": {
            "confirmed_by_user": True, "action": "clear_configured_target_and_append",
            "clear_existing": True, "product_ids": [4, 7, 9], "target_timeline_id": "test-id",
            "project_id": "project"}}
        state = {"project_identity": {"resolve_project_id": "project"},
                 "resolve": {"source_timeline_id": "source-id", "source_timeline": "素材",
                             "target_timeline_id": "formal-target-id", "target_timeline": "成片"},
                 "runtime": {"test_timeline_binding": {
                     "project_id": "project", "timeline_id": "test-id", "name": "测试"}}}
        assert_authorized_target_clear(body, plan, override, state)
        override["resolve_write_authorization"]["action"] = "replace_previous_test_results_and_append"
        with self.assertRaises(ValueError):
            assert_authorized_target_clear(body, plan, override, state)
        override["resolve_write_authorization"]["action"] = "clear_configured_target_and_append"
        state["runtime"]["test_timeline_binding"]["timeline_id"] = "formal-target-id"
        with self.assertRaises(ValueError):
            assert_authorized_target_clear(body, plan, override, state)

    def test_clip_must_stay_inside_user_confirmed_source_range(self):
        state = {"video_paths": ["/media/a.mp4", "/media/b.mp4", "/media/c.mp4"]}
        plan = {"products": [{"product_id": 4, "clips": [{
            "source_file": "b.mp4", "source_start_seconds": 11.0, "source_end_seconds": 12.0}]}]}
        ranges = {"products": [{"product_index": 4,
            "start": {"source_stem": "b", "time": 10.0},
            "end": {"source_stem": "c", "time": 20.0}}]}
        verify_user_ranges(state, plan, ranges)
        plan["products"][0]["clips"][0]["source_start_seconds"] = 9.0
        with self.assertRaises(ValueError):
            verify_user_ranges(state, plan, ranges)

    def test_empty_test_timeline_gets_read_only_ready_receipt(self):
        timeline = MagicMock()
        timeline.GetName.return_value = "测试"
        timeline.GetUniqueId.return_value = "test-id"
        project = MagicMock()
        project.GetTimelineCount.return_value = 1
        project.GetTimelineByIndex.return_value = timeline
        manager = MagicMock()
        manager.GetCurrentProject.return_value = project
        resolve = MagicMock()
        resolve.GetProjectManager.return_value = manager
        workspace = Path(tempfile.mkdtemp(prefix="test-empty-ready-"))
        state = {"project_identity": {"resolve_project_id": "project-id"},
                 "runtime": {"test_timeline_binding": {
                     "project_id": "project-id", "timeline_id": "test-id", "name": "测试"}},
                 "resolve": {"source_timeline_id": "source-id", "target_timeline_id": "final-id",
                             "source_timeline": "素材", "target_timeline": "成片"},
                 "batch": {"batch_id": "batch-empty"}}
        paths = SimpleNamespace(load_state=lambda: state,
                                cache_dir=workspace,
                                workspace_root=workspace)
        before = {"id": "test-id", "name": "测试", "rows": [], "markers": []}
        with patch("resolve_connection.connect", return_value=resolve), \
             patch("resolve_session_identity.confirm_project", return_value=("project-id", {})), \
             patch("reusable_test_delivery.stable_id", return_value="test-id"), \
             patch("reusable_test_delivery.snapshot", return_value=before), \
             patch("reusable_test_delivery.verified_backup") as backup, \
             patch("reusable_test_delivery.atomic_json") as write_receipt:
            result = prepare_registered_test_timeline(paths, "测试", "test-id")
        self.assertEqual(result["status"], "ALREADY_EMPTY_VERIFIED")
        self.assertFalse(result["mutation_performed"])
        backup.assert_not_called()
        manager.SaveProject.assert_not_called()
        manager.ExportProject.assert_not_called()
        project.SetCurrentTimeline.assert_not_called()
        write_receipt.assert_called_once()
        self.assertEqual(write_receipt.call_args.args[1]["before"], before)
        self.assertFalse(write_receipt.call_args.args[1]["mutation_performed"])


if __name__ == "__main__":
    unittest.main()
