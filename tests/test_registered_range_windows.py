import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from registered_pipeline import (exact_title_anchor_evidence, join_nearby_range_windows,
                                pack_semantic_range_chunks)
from project_registry import digest
from runtime_meta import CacheIdentityError


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

    def test_oversized_range_is_split_with_overlap_without_losing_cues(self):
        product = {"product_index": 27, "title": "轻薄加绒直筒裤子"}
        source_bounds = {"part-a": {"source_stem": "part-a", "global_start": 0,
                                      "global_end": 10000}}
        cue_rows = [[i, i * 2.0, i * 2.0 + 1.0, f"第{i}条字幕：轻薄加绒直筒裤子的完整上下文证据。" * 2]
                    for i in range(240)]
        windows = [{"window_id": "w1", "source_stem": "part-a", "global_start": 0,
                    "global_end": 1000, "boundary_context": "fixed_context",
                    "distinctive_terms": ["加绒"], "cue_count": len(cue_rows),
                    "cue_rows": cue_rows}]

        chunks = pack_semantic_range_chunks(product, windows, source_bounds, "project-key",
                                            target_bytes=4500, overlap_cues=5)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(json.dumps(chunk, ensure_ascii=False).encode("utf-8")) <= 4500
                            for chunk in chunks))
        core_ids = [cue_id for chunk in chunks for cue_id in chunk["core_cue_ids"]]
        self.assertEqual(sorted(core_ids), list(range(240)))
        cue_sets = [{row[0] for row in chunk["cues"]} for chunk in chunks]
        self.assertTrue(all(left & right for left, right in zip(cue_sets, cue_sets[1:])))
        self.assertEqual(chunks[0]["cues"][0][3], cue_rows[0][3])
        for chunk in chunks:
            self.assertEqual(chunk["task_digest"], digest(
                {key: value for key, value in chunk.items() if key != "task_digest"}))

    def test_oversized_single_cue_fails_closed_instead_of_truncating(self):
        product = {"product_index": 27, "title": "轻薄加绒裤子"}
        source_bounds = {"part-a": {"source_stem": "part-a", "global_start": 0,
                                      "global_end": 10000}}
        windows = [{"window_id": "w1", "source_stem": "part-a", "global_start": 0,
                    "global_end": 10, "cue_rows": [[1, 0.0, 1.0, "字幕" * 10000]]}]

        with self.assertRaisesRegex(CacheIdentityError, "range_chunk_single_cue_exceeds_budget"):
            pack_semantic_range_chunks(product, windows, source_bounds, "project-key",
                                       target_bytes=5000, overlap_cues=2)


if __name__ == "__main__":
    unittest.main()
