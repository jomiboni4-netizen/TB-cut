"""Synthetic regression coverage for semantic windows, MiMo stages and repairs."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from semantic_evidence import semantic_windows, clean_cue, subtract_used_intervals
from build_expanded_codex_request import build_wide_candidates
from mimo_review_v2 import understanding_prompt, assembly_prompt
from product_semantic_preview import (validate_phase_a, audit_assembly,
                                      validate_verified_groups, call_cached)
from audit_mimo_v2 import dependency_risks, semantic_duplicate


def cue(t, text, stem="A"):
    return {"source_stem": stem, "start": t, "end": t + 2, "global_start": t,
            "global_end": t + 2, "text": text}


class SemanticPipelineTests(unittest.TestCase):
    def test_no_link_number_uses_product_features(self):
        index = {"sources": [{"source_stem": "A", "global_start": 0, "global_end": 500}],
                 "cues": [cue(100, "这件摇粒绒里面充阿拉善驼绒，很轻暖") ]}
        result = semantic_windows(index, [{"product_index": 26,
            "title": "Limonta摇粒绒夹阿拉善驼绒外套"}])
        self.assertEqual(result["products"][0]["status"], "ROUGH_CANDIDATE")
        self.assertTrue(result["products"][0]["windows"])

    def test_early_next_link_does_not_switch_product(self):
        index = {"sources": [{"source_stem": "A", "global_start": 0, "global_end": 500}],
                 "cues": [cue(10, "下一款先上26号链接"), cue(220, "这件排骨羽绒服用的是白鹅绒") ]}
        result = semantic_windows(index, [{"product_index": 28,
            "title": "白鹅绒排骨羽绒服"}])
        window = result["products"][0]["windows"][0]
        self.assertLess(window["global_start"], 220)
        self.assertGreater(window["global_end"], 220)
        self.assertEqual(window["evidence_start"], 220)

    def test_late_link_does_not_erase_prior_selling_point(self):
        index = {"sources": [{"source_stem": "A", "global_start": 0, "global_end": 500}],
                 "cues": [cue(100, "羊反绒细腻轻薄，撞色格纹很特别"), cue(180, "现在报一下29号链接") ]}
        result = semantic_windows(index, [{"product_index": 29,
            "title": "羊反绒撞色格纹夹克"}])
        self.assertEqual(result["products"][0]["windows"][0]["evidence_start"], 100)

    def test_link_number_conflict_is_annotation_only(self):
        index = {"sources": [{"source_stem": "A", "global_start": 0, "global_end": 500}],
                 "cues": [cue(20, "现在讲29号链接"), cue(200, "这件直毛皮毛一体很有质感") ]}
        products = [{"product_index": 29, "title": "直毛皮毛一体皮草外套"}]
        from locate_link_intros import build_suggestions
        result = build_suggestions(index, products)["products"][0]
        self.assertEqual(result["windows"][0]["evidence_start"], 200)
        self.assertEqual(result["link_mentions"][0]["role"], "AUXILIARY_ONLY")
        self.assertEqual(result["boundary_status"], "ROUGH_CANDIDATE")

    def test_set_parts_can_be_one_product_but_separate_titles_are_not_merged(self):
        index = {"sources": [{"source_stem": "A", "global_start": 0, "global_end": 500}],
                 "cues": [cue(100, "这套双面呢斗篷、马甲和半裙是馆藏套装") ]}
        set_result = semantic_windows(index, [{"product_index": 31,
            "title": "Piacenza双面呢斗篷马甲半裙套装"},
            {"product_index": 32, "title": "羊毛半裙"}])
        self.assertEqual(set_result["products"][0]["status"], "ROUGH_CANDIDATE")
        self.assertEqual(set_result["products"][1]["status"], "PRODUCT_UNCERTAIN")

    def test_mixed_forbidden_and_selling_clauses_keep_valid_part(self):
        kept, removed = clean_cue(cue(1, "面料柔软不扎，价格今天很优惠"), "q1")
        self.assertIn("面料柔软不扎", "".join(row["text"] for row in kept))
        self.assertNotIn("价格", "".join(row["text"] for row in kept))
        self.assertEqual(len(removed), 1)
        candidates, logs = build_wide_candidates([cue(1, "面料柔软不扎，价格今天很优惠")], "A", "羊绒开衫")
        self.assertIn("面料柔软不扎", candidates[0]["asr_text"])
        self.assertTrue(candidates[0]["subtitle"][0]["needs_cutpoint_check"])
        self.assertEqual(logs[0]["reason"], "forbidden_clause")

    def test_long_candidate_residual_is_available_after_partial_use(self):
        pool = [{"candidate_id": "long", "source_file": "A.mp4",
                 "time_range": {"start": 0, "end": 100}, "subtitle": [
                     {"start": 0, "end": 100, "text": "all"}]}]
        remaining = subtract_used_intervals(pool, [{"source_file": "A.mp4", "start": 40, "end": 55}])
        self.assertEqual([x["time_range"] for x in remaining],
                         [{"start": 0, "end": 40.0}, {"start": 55.0, "end": 100.0}])

    def test_redundant_because_can_leave_complete_conclusion(self):
        self.assertEqual(dependency_risks("这件面料柔软，穿着完全不扎"), [])
        self.assertEqual(dependency_risks("因为它特别舒服所以"), ["TAIL_CLOSURE_RISK"])

    def test_mimo_closure_claim_is_not_requested_as_proof(self):
        self.assertNotIn("closure_check", understanding_prompt({"product_title": "测试商品", "windows": []}))
        prompt = assembly_prompt({"product_title": "测试商品", "verified_groups": []})
        self.assertIn("closure不作为审核证据", prompt)
        self.assertNotIn("closure_check", prompt)

    def test_phase_a_requires_exact_sources_and_clear_product_match(self):
        source = {"q1": {"text": "面料柔软", "source_stem": "A", "local_start": 0,
                         "local_end": 2, "global_start": 0, "global_end": 2,
                         "source_alias": "src01", "source_ref": "q1", "needs_cutpoint_check": False}}
        response = {"product_identity": {"match": "MATCH"}, "windows": [{"window_id": "w1",
                    "sentence_groups": [{"group_id": "g1", "refs": ["q1"], "text": "面料柔软",
                    "product_attribution": "MATCH"}]}]}
        groups, _, errors = validate_phase_a(response, source)
        self.assertEqual(len(groups), 1)
        self.assertEqual(errors, [])
        response["windows"][0]["sentence_groups"][0]["text"] = "面料很柔软"
        _, _, errors = validate_phase_a(response, source)
        self.assertEqual(errors[0]["code"], "MIMO_TEXT_NOT_EXACT_SOURCE")
        response["windows"][0]["sentence_groups"][0]["text"] = "面料，柔软"
        _, _, errors = validate_phase_a(response, source)
        self.assertEqual(errors[0]["code"], "MIMO_TEXT_NOT_EXACT_SOURCE")

    def test_assembly_rejects_duration_and_duplicate_versions_without_self_check(self):
        group = {"group_id": "g1", "text": "这是一个用于合成的完整卖点描述" * 3,
                 "claim": "synthetic", "actual_refs": [{"source_stem": "A", "local_start": 1,
                 "local_end": 20, "text": "这是一个用于合成的完整卖点描述" * 3,
                 "needs_cutpoint_check": False, "ref": "q1"}]}
        response = {"selected_variants": [
            {"variant_id": "variant_1", "group_ids": ["g1"], "closure_check": {"complete_ending": True}},
            {"variant_id": "variant_2", "group_ids": ["g1"], "closure_check": {"complete_ending": True}}]}
        variants, errors = audit_assembly(response, [group])
        codes = {row["code"] for row in errors}
        self.assertIn("DURATION_INVALID", codes)
        self.assertIn("GROUP_REUSED_ACROSS_VARIANTS", codes)
        self.assertNotIn("CLOSURE_FAILED", codes)

    @staticmethod
    def verified_group(group_id="g1", ref="q1", text="完整的合成卖点原文"):
        return {"group_id": group_id, "text": text, "claim": "卖点",
                "selling_point_category": "面料", "relations": ["先因后果"],
                "standalone": False, "needs_context": ["保留前句"],
                "actual_refs": [{"ref": ref, "source_stem": "source-A", "source_alias": "src01",
                                 "local_start": 10.0, "local_end": 20.0, "text": text}]}

    def test_stage_b_prompt_compacts_but_keeps_source_context_and_not_duplicate_refs(self):
        group = self.verified_group()
        prompt = assembly_prompt({"product_title": "合成商品", "verified_groups": [group]})
        self.assertEqual(prompt.count("完整的合成卖点原文"), 1)
        self.assertIn('"group_id":"g1"', prompt)
        self.assertIn('["src01",10.0,20.0]', prompt)
        self.assertIn('"relations":["先因后果"]', prompt)
        self.assertIn('"needs_context":["保留前句"]', prompt)
        self.assertNotIn("source_stem", prompt)
        self.assertNotIn("source_spans", prompt)
        self.assertNotIn('"ref":"q1"', prompt)

    def test_verified_group_source_mismatch_duplicate_interval_and_bad_id_rejected(self):
        group = self.verified_group()
        self.assertEqual(validate_verified_groups([group]), [])
        bad_text = {**group, "text": "不同原文"}
        self.assertIn("group_text_source_mismatch", validate_verified_groups([bad_text]))
        duplicate = self.verified_group("g2")
        self.assertIn("source_ref_reused_across_groups", validate_verified_groups([group, duplicate]))
        duplicate_interval = self.verified_group("g2", "q2")
        duplicate_interval["actual_refs"][0]["local_start"] = 15.0
        _, overlap_errors = audit_assembly(
            {"selected_variants": [{"variant_id": "v1", "group_ids": ["g1", "g2"]}]},
            [group, duplicate_interval])
        self.assertIn("SOURCE_TIME_OVERLAP", {row["code"] for row in overlap_errors})
        self.assertIn("group_id_missing_or_duplicate", validate_verified_groups([group, {**group}]))

    def test_zero_versions_allowed_only_with_insufficient_reason(self):
        groups = [self.verified_group()]
        variants, errors = audit_assembly({"selected_variants": [], "insufficient_reason": "可用内容不足"}, groups)
        self.assertEqual(variants, [])
        self.assertEqual(errors, [])
        _, errors = audit_assembly({"selected_variants": []}, groups)
        self.assertIn("VARIANT_COUNT_INVALID", {row["code"] for row in errors})

    def test_same_atomic_group_overlapping_cue_spans_are_not_version_overlap(self):
        text = "同一句群中的合成短句"
        group = {"group_id": "g1", "text": text + text, "actual_refs": [
            {"ref": "q1", "source_stem": "A", "local_start": 1, "local_end": 20, "text": text},
            {"ref": "q2", "source_stem": "A", "local_start": 1, "local_end": 20, "text": text}],
            "claim": "合成"}
        _, errors = audit_assembly({"selected_variants": [
            {"variant_id": "v1", "group_ids": ["g1"]}]}, [group])
        self.assertNotIn("SOURCE_TIME_OVERLAP", {row["code"] for row in errors})

    def test_uncertain_matching_request_is_never_resent(self):
        import hashlib
        import json
        import tempfile
        from unittest.mock import patch
        from mimo_review_v2 import prompt
        from product_semantic_preview import MODEL

        document = {"product_title": "合成商品", "verified_groups": [self.verified_group()]}
        body = {"model": MODEL, "input": prompt(document, "assemble"), "max_output_tokens": 8000,
                "reasoning": {"effort": "low"}, "stream": True}
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / f"stage_B_{digest[:16]}_meta.json").write_text(json.dumps({
                "request_sha256": digest, "status": "uncertain"}))
            with patch.dict("os.environ", {"MIMO_API_KEY": "synthetic-not-a-real-key"}), \
                    patch("scripts.product_semantic_preview.create_response", side_effect=AssertionError("must not call")):
                with self.assertRaisesRegex(ValueError, "previously_attempted_no_resend"):
                    call_cached("B", document, folder)

    def test_similar_versions_are_detected(self):
        self.assertTrue(semantic_duplicate("这是一段很长很长的商品卖点描述内容用于比较重复", "这是一段很长很长的商品卖点描述内容用于比较重复"))

    def test_rough_window_is_never_an_authenticated_range(self):
        index = {"sources": [{"source_stem": "A", "global_start": 0, "global_end": 100}],
                 "cues": [cue(40, "这件双面呢斗篷很轻盈") ]}
        result = semantic_windows(index, [{"product_index": 31, "title": "双面呢斗篷"}])
        self.assertEqual(result["authority"], "NOT_AUTHENTICATED_NOT_FOR_CUTTING")
        self.assertFalse(result["products"][0]["windows"][0]["authenticated"])

    def test_similar_other_product_cue_can_remain_uncertain(self):
        index = {"sources": [{"source_stem": "A", "global_start": 0, "global_end": 100}],
                 "cues": [cue(40, "羊毛半裙做了豹纹提花") ]}
        products = [{"product_index": 33, "title": "羊毛豹纹提花半裙"},
                    {"product_index": 34, "title": "羊毛豹纹提花大衣"}]
        result = semantic_windows(index, products)
        self.assertTrue(any(row["status"] == "PRODUCT_UNCERTAIN" for row in result["products"]))


if __name__ == "__main__":
    unittest.main()
