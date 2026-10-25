import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from registered_pipeline import exact_title_anchor_evidence, join_nearby_range_windows


class RangeWindowContextTests(unittest.TestCase):
    def test_nearby_hits_keep_the_full_bridge_context(self):
        rows = [
            {"window_id": "w2", "source_stem": "part-a", "global_start": 300,
             "global_end": 420, "evidence_end": 350, "matched_cue_count": 3,
             "distinctive_terms": ["直筒"]},
            {"window_id": "w1", "source_stem": "part-a", "global_start": 100,
             "global_end": 250, "evidence_end": 220, "matched_cue_count": 2,
             "distinctive_terms": ["加绒"]},
            {"window_id": "w3", "source_stem": "part-a", "global_start": 800,
             "global_end": 900, "evidence_end": 850, "matched_cue_count": 1,
             "distinctive_terms": ["弧形"]},
        ]
        merged = join_nearby_range_windows(rows)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["window_id"], "w1+w2")
        self.assertEqual(merged[0]["global_start"], 100)
        self.assertEqual(merged[0]["global_end"], 420)
        self.assertEqual(merged[0]["matched_cue_count"], 5)
        self.assertEqual(merged[0]["distinctive_terms"], ["加绒", "直筒"])

    def test_windows_from_different_source_files_do_not_merge(self):
        rows = [
            {"window_id": "w1", "source_stem": "part-a", "global_start": 0, "global_end": 100},
            {"window_id": "w2", "source_stem": "part-b", "global_start": 101, "global_end": 200},
        ]
        self.assertEqual(len(join_nearby_range_windows(rows)), 2)

    def test_title_anchor_uses_exact_current_cue_inside_selected_range(self):
        index = {"cues": [
            {"source_stem": "part-a", "start": 3.0, "end": 3.5,
             "global_start": 13.0, "global_end": 13.5, "text": "这条是八分牛仔裤"},
            {"source_stem": "part-a", "start": 50.0, "end": 51.0,
             "global_start": 60.0, "global_end": 61.0, "text": "裤子"},
        ]}
        anchors = exact_title_anchor_evidence("加绒八分牛仔裤", index, 10.0, 20.0)
        self.assertEqual(anchors, [{"source_stem": "part-a", "time": 3.0,
                                    "quote": "这条是八分牛仔裤"}])


if __name__ == "__main__":
    unittest.main()
