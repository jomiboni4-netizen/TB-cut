#!/usr/bin/env python3
"""Acceptance tests A–G from TB_CUT_V2_1_CODEX_UPDATE.md."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from audit_mimo_v2 import audit_status, dependency_risks  # noqa: E402
from build_expanded_codex_request import build_wide_candidates  # noqa: E402
from locate_link_intros import complete_product_range  # noqa: E402
from refine_plans_v2 import fit_duration_with_complete_segments, normalize_physical_clips  # noqa: E402


class V21AcceptanceTests(unittest.TestCase):
    def test_a_meaningless_cut_is_merged(self) -> None:
        clips = [
            {"source_file": "source.mp4", "source_start_frame": 3000, "source_end_frame": 3255, "duration_sec": 8.5, "fps": 30, "audit": {"role": "material"}},
            {"source_file": "source.mp4", "source_start_frame": 3255, "source_end_frame": 3480, "duration_sec": 7.5, "fps": 30, "audit": {"role": "fit"}},
        ]
        merged, count = normalize_physical_clips(clips)
        self.assertEqual(count, 1)
        self.assertEqual(len(merged), 1)
        self.assertEqual((merged[0]["source_start_frame"], merged[0]["source_end_frame"]), (3000, 3480))

    def test_b_result_then_because_passes(self) -> None:
        text = "这件穿起来不会贴身，因为这个面料本身有一点筋骨感。"
        self.assertEqual(dependency_risks(text), [])
        self.assertEqual(audit_status([]), "PASS")

    def test_c_truncated_because_is_repairable(self) -> None:
        errors = [{"code": code} for code in dependency_risks("因为这个面料它……")]
        self.assertIn({"code": "TAIL_CLOSURE_RISK"}, errors)
        self.assertEqual(audit_status(errors), "REPAIRABLE")

    def test_d_unfulfilled_three_colors_is_repairable(self) -> None:
        errors = [{"code": code} for code in dependency_risks("这个有三个颜色，第一个是白色……")]
        self.assertIn({"code": "ENUMERATION_UNRESOLVED"}, errors)
        self.assertEqual(audit_status(errors), "REPAIRABLE")

    def test_e_complete_tail_replaces_whole_low_value_segment(self) -> None:
        clips = [
            {"name": "low", "duration_sec": 2.1, "audit": {"complete_boundary": True, "value_score": 0.1}},
            {"name": "core", "duration_sec": 56.9, "audit": {"complete_boundary": True, "value_score": 1.0}},
            {"name": "completed_tail", "duration_sec": 2.0, "audit": {"complete_boundary": True, "value_score": 1.0}},
        ]
        kept, removed = fit_duration_with_complete_segments(clips)
        self.assertEqual([row["name"] for row in removed], ["low"])
        self.assertAlmostEqual(sum(row["duration_sec"] for row in kept), 58.9)

    def test_f_first_68_percent_failure_is_repairable(self) -> None:
        errors = [{"code": "HOST_VISIBLE_RATIO_TOO_LOW", "host_ratio": 0.68}]
        self.assertEqual(audit_status(errors), "REPAIRABLE")
        self.assertNotEqual(audit_status(errors), "EXHAUSTED")

    def test_g_anchor_expands_to_complete_product_range(self) -> None:
        start, end = complete_product_range(100.0, 400.0, 900.0)
        cues = [
            {"start": float(second), "end": float(second + 10), "text": "当前商品材质版型上身效果"}
            for second in range(100, 400, 10)
        ]
        candidates, removed = build_wide_candidates(cues, "source", "材质版型")
        self.assertEqual((start, end), (100.0, 400.0))
        self.assertEqual(removed, [])
        self.assertEqual(candidates[0]["time_range"], {"start": 100.0, "end": 400.0})
        self.assertGreater(candidates[0]["semantic_boundary"]["duration_sec"], 60.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
