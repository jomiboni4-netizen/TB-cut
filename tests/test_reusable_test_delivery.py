import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from reusable_test_delivery import assert_authorized_target_clear, expected_frames, verify_user_ranges


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


if __name__ == "__main__":
    unittest.main()
