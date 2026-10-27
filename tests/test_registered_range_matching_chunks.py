import sys
import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import registered_pipeline as pipeline


class RegisteredRangeChunkFlowTests(unittest.TestCase):
    def test_one_shot_extension_adds_exact_neighboring_source_cues(self):
        payload = {'role': 'semantic_range_matching', 'instructions': ['review']}
        index = {'sources': [
            {'source_stem': 'source-a', 'global_start': 0.0, 'global_end': 100.0},
            {'source_stem': 'source-b', 'global_start': 100.0, 'global_end': 200.0}],
            'cues': [
                {'source_stem': 'source-a', 'start': 80.0, 'end': 81.0,
                 'global_start': 80.0, 'global_end': 81.0, 'text': '候选范围起点商品标题'},
                {'source_stem': 'source-a', 'start': 98.0, 'end': 99.0,
                 'global_start': 98.0, 'global_end': 99.0, 'text': '当前商品讲解继续'},
                {'source_stem': 'source-b', 'start': 0.0, 'end': 1.0,
                 'global_start': 100.0, 'global_end': 101.0, 'text': '相邻段边界线索'},
                {'source_stem': 'source-b', 'start': 12.0, 'end': 13.0,
                 'global_start': 112.0, 'global_end': 113.0, 'text': '下一商品开始'},
            ]}
        windows = [{'window_id': 'w1', 'source_stem': 'source-a',
                    'global_start': 80.0, 'global_end': 100.0}]

        task, metrics = pipeline.extend_range_payload_with_adjacent_context(
            payload, windows, index)

        self.assertIsNotNone(task)
        self.assertEqual(metrics['reason'], None)
        self.assertEqual(task['boundary_context_repair']['attempt'], 1)
        contexts = task['boundary_context_repair']['evidence']
        adjacent = [row for row in contexts if row['source_stem'] == 'source-b']
        self.assertEqual(len(adjacent), 1)
        self.assertEqual([cue[3] for cue in adjacent[0]['cues']],
                         ['相邻段边界线索', '下一商品开始'])
        self.assertLessEqual(pipeline._json_utf8_bytes(task), pipeline.RANGE_TASK_MAX_BYTES)

    def test_range_matching_uses_one_context_repair_then_accepts_exact_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            product = {'product_index': 27, 'encoding_id': '027', 'title': '羽绒裤商品'}
            cues = [
                {'source_stem': 'source-a', 'start': 90.0, 'end': 91.0,
                 'global_start': 90.0, 'global_end': 91.0, 'text': '这款羽绒裤开始介绍'},
                {'source_stem': 'source-a', 'start': 99.0, 'end': 100.0,
                 'global_start': 99.0, 'global_end': 100.0, 'text': '当前商品版型说明继续'},
                {'source_stem': 'source-b', 'start': 0.0, 'end': 1.0,
                 'global_start': 100.0, 'global_end': 101.0, 'text': '这一段补充当前商品收尾'},
            ]
            titles = {'products': [product]}
            index = {'sources': [
                {'source_stem': 'source-a', 'global_start': 0.0, 'global_end': 100.0},
                {'source_stem': 'source-b', 'global_start': 100.0, 'global_end': 200.0}],
                'cues': cues}
            rough = {'products': [{'product_index': 27, 'windows': [{
                'window_id': 'w1', 'source_stem': 'source-a', 'global_start': 80.0,
                'global_end': 100.0, 'evidence_start': 90.0, 'evidence_end': 100.0,
                'distinctive_terms': ['羽绒裤'], 'feature_terms': ['羽绒裤'],
                'matched_cue_count': 2}]}]}
            paths = SimpleNamespace(
                load_state=lambda: {'batch': {'batch_id': 'batch-boundary', 'product_ids': [27]}},
                registered_scope=lambda: ('project-key', 'resolve-id'),
                workspace_root=root, project_records_dir=root / 'records')
            calls = []

            def execute_range_worker(_paths, task):
                calls.append(task)
                call = {'task_digest': task['task_digest'], 'payload_bytes': 1,
                        'cache_hit': len(calls) == 1, 'elapsed_seconds': 0.01,
                        'worker_elapsed_seconds': 0.01, 'receipt_ref': 'synthetic'}
                if len(calls) == 1:
                    return {'verdict': 'BLOCKED', 'issues': ['缺少后续字幕的边界上下文'],
                            'range_decision': None}, call
                self.assertIn('boundary_context_repair', task)
                start = {'source_stem': 'source-a', 'time': 90.0,
                         'quote': '这款羽绒裤开始介绍'}
                end = {'source_stem': 'source-b', 'time': 0.0,
                       'quote': '这一段补充当前商品收尾'}
                return {'verdict': 'PASS', 'issues': [], 'range_decision': {
                    'start': start, 'end': end, 'title_anchors': [start]}}, call

            with patch.object(pipeline, 'inputs', return_value=(titles, index)), \
                 patch('semantic_evidence.semantic_windows', return_value=rough), \
                 patch.object(pipeline, '_execute_range_worker', side_effect=execute_range_worker), \
                 patch.object(pipeline, 'expected_identity', return_value={'stable': True}):
                manifest = pipeline.generate_batch_range_manifest(paths, [27])

            self.assertEqual(len(calls), 2)
            self.assertEqual(manifest['products'][0]['end']['source_stem'], 'source-b')
            self.assertEqual(manifest['boundary_context_repair_protocol'],
                             pipeline.RANGE_BOUNDARY_REPAIR_PROTOCOL)
            self.assertEqual(calls[1]['boundary_context_repair']['protocol'],
                             pipeline.RANGE_BOUNDARY_REPAIR_PROTOCOL)
            metric_file = next(root.glob('RANGE_MATCHING_METRICS_*.json'))
            product_metrics = json.loads(metric_file.read_text(encoding='utf-8'))['products'][0]
            repair = product_metrics['boundary_context_repair']
            self.assertTrue(product_metrics['calls'][0]['cache_hit'])
            self.assertEqual(repair['trigger'], 'blocked_missing_boundary_context')
            self.assertTrue(repair['attempted'])
            self.assertEqual(repair['status'], 'PASS')

    def test_unrelated_blocked_response_does_not_trigger_context_retry(self):
        self.assertIsNone(pipeline.range_boundary_context_trigger({
            'verdict': 'BLOCKED', 'range_decision': None,
            'issues': ['商品身份冲突，候选归属不唯一']}))
        self.assertEqual(pipeline.range_boundary_context_trigger({
            'verdict': 'REPAIRABLE', 'range_decision': None,
            'issues': ['需要相邻段边界']}), 'repairable_verdict')

    def test_neighbor_boundaries_create_search_windows_without_exact_product_end(self):
        product = {'product_index': 27, 'title': '羽绒裤商品'}
        windows = [{'window_id': 'w1', 'source_stem': 'source-a',
                    'global_start': 80.0, 'global_end': 260.0,
                    'evidence_start': 120.0, 'evidence_end': 180.0,
                    'distinctive_terms': ['羽绒裤']}]
        catalog = [
            {'product_index': 26, 'windows': [{'window_id': 'prev',
             'source_stem': 'source-a', 'global_start': 70.0, 'global_end': 100.0,
             'evidence_start': 80.0, 'evidence_end': 100.0,
             'distinctive_terms': ['大衣'], 'status': 'ROUGH_CANDIDATE'}]},
            {'product_index': 27, 'windows': windows},
            {'product_index': 28, 'windows': [{'window_id': 'next',
             'source_stem': 'source-a', 'global_start': 220.0, 'global_end': 250.0,
             'evidence_start': 230.0, 'evidence_end': 240.0,
             'distinctive_terms': ['皮衣'], 'status': 'ROUGH_CANDIDATE'}]},
        ]
        cues = [
            {'source_stem': 'source-a', 'start': 99.0, 'end': 100.0,
             'global_start': 99.0, 'global_end': 100.0, 'text': '上一款大衣介绍结束'},
            {'source_stem': 'source-a', 'start': 120.0, 'end': 121.0,
             'global_start': 120.0, 'global_end': 121.0, 'text': '这条羽绒裤面料很好'},
            {'source_stem': 'source-a', 'start': 145.0, 'end': 146.0,
             'global_start': 145.0, 'global_end': 146.0, 'text': '羽绒裤版型很轻便'},
            {'source_stem': 'source-a', 'start': 179.0, 'end': 180.0,
             'global_start': 179.0, 'global_end': 180.0, 'text': '羽绒裤收尾介绍'},
            {'source_stem': 'source-a', 'start': 230.0, 'end': 231.0,
             'global_start': 230.0, 'global_end': 231.0, 'text': '下一款皮衣开始介绍'},
        ]
        index = {'sources': [{'source_stem': 'source-a', 'global_start': 0.0,
                              'global_end': 300.0}], 'cues': cues}

        neighbors = pipeline.adjacent_product_boundary_evidence(27, windows, catalog, index)
        decision = pipeline.neighbor_inferred_search_decision(product, windows, neighbors, index)

        self.assertEqual({row['edge'] for row in neighbors}, {'previous_end', 'next_start'})
        self.assertIsNotNone(decision)
        self.assertEqual(decision['boundary_mode'], pipeline.NEIGHBOR_INFERRED_BOUNDARY_MODE)
        self.assertEqual(len(decision['candidate_windows']), 1)
        candidate = decision['candidate_windows'][0]
        self.assertGreaterEqual(candidate['start'], 100.0)
        self.assertLessEqual(candidate['end'], 180.0)
        self.assertTrue(any('羽绒裤' in row['quote'] for row in decision['title_anchors']))

    def test_insufficient_whole_product_end_falls_back_and_continues_range_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            product = {'product_index': 27, 'encoding_id': '027', 'title': '羽绒裤商品'}
            cues = [
                {'source_stem': 'source-a', 'start': 99.0, 'end': 100.0,
                 'global_start': 99.0, 'global_end': 100.0, 'text': '上一款大衣介绍结束'},
                {'source_stem': 'source-a', 'start': 120.0, 'end': 121.0,
                 'global_start': 120.0, 'global_end': 121.0, 'text': '这条羽绒裤面料很好'},
                {'source_stem': 'source-a', 'start': 145.0, 'end': 146.0,
                 'global_start': 145.0, 'global_end': 146.0, 'text': '羽绒裤版型很轻便'},
                {'source_stem': 'source-a', 'start': 179.0, 'end': 180.0,
                 'global_start': 179.0, 'global_end': 180.0, 'text': '羽绒裤收尾介绍'},
                {'source_stem': 'source-b', 'start': 20.0, 'end': 21.0,
                 'global_start': 220.0, 'global_end': 221.0, 'text': '下一款皮衣开始介绍'},
            ]
            target_window = {'window_id': 'w1', 'source_stem': 'source-a',
                'global_start': 80.0, 'global_end': 200.0,
                'evidence_start': 120.0, 'evidence_end': 180.0,
                'distinctive_terms': ['羽绒裤'], 'feature_terms': ['羽绒裤'],
                'matched_cue_count': 2}
            catalog = [
                {'product_index': 26, 'windows': [{'window_id': 'prev',
                 'source_stem': 'source-a', 'global_start': 70.0, 'global_end': 100.0,
                 'evidence_start': 80.0, 'evidence_end': 100.0,
                 'distinctive_terms': ['大衣'], 'status': 'ROUGH_CANDIDATE'}]},
                {'product_index': 27, 'windows': [target_window]},
                {'product_index': 28, 'windows': [{'window_id': 'next',
                 'source_stem': 'source-b', 'global_start': 210.0, 'global_end': 250.0,
                 'evidence_start': 220.0, 'evidence_end': 230.0,
                 'distinctive_terms': ['皮衣'], 'status': 'ROUGH_CANDIDATE'}]},
            ]
            titles = {'products': [product]}
            index = {'sources': [
                {'source_stem': 'source-a', 'global_start': 0.0, 'global_end': 200.0},
                {'source_stem': 'source-b', 'global_start': 200.0, 'global_end': 300.0}],
                'cues': cues}
            rough = {'products': catalog}
            paths = SimpleNamespace(
                load_state=lambda: {'batch': {'batch_id': 'batch-boundary', 'product_ids': [27]}},
                registered_scope=lambda: ('project-key', 'resolve-id'),
                workspace_root=root, project_records_dir=root / 'records')
            calls = []

            def execute_range_worker(_paths, task):
                calls.append(task)
                call = {'task_digest': task['task_digest'], 'payload_bytes': 100,
                        'cache_hit': len(calls) == 1, 'elapsed_seconds': 0.01,
                        'worker_elapsed_seconds': 0.01, 'receipt_ref': 'synthetic'}
                self.assertTrue(task.get('neighbor_product_boundaries'))
                if len(calls) == 1:
                    return {'verdict': 'BLOCKED', 'issues': ['缺少商品完整结束边界上下文'],
                            'range_decision': None}, call
                self.assertIn('boundary_context_repair', task)
                self.assertTrue(task.get('neighbor_product_boundaries'))
                return {'verdict': 'REPAIRABLE', 'issues': ['整体结束锚点仍不完整'],
                        'range_decision': None}, call

            with patch.object(pipeline, 'inputs', return_value=(titles, index)), \
                 patch('semantic_evidence.semantic_windows', return_value=rough), \
                 patch.object(pipeline, '_execute_range_worker', side_effect=execute_range_worker), \
                 patch('publish_confirmed_ranges.build_semantic_inferred_ranges',
                       return_value={'products': []}), \
                 patch.object(pipeline, 'expected_identity', return_value={'stable': True}):
                manifest = pipeline.generate_batch_range_manifest(paths, [27])

            self.assertEqual(len(calls), 2)
            row = manifest['products'][0]
            self.assertEqual(row['boundary_mode'], pipeline.NEIGHBOR_INFERRED_BOUNDARY_MODE)
            self.assertEqual(len(row['candidate_windows']), 1)
            self.assertEqual(row['start']['source_stem'], 'source-a')
            metric_file = next(root.glob('RANGE_MATCHING_METRICS_*.json'))
            metrics = json.loads(metric_file.read_text(encoding='utf-8'))['products'][0]
            self.assertEqual(metrics['status'], 'PASS_BEST_EFFORT')
            self.assertEqual(metrics['boundary_search_resolution']['exact_whole_product_end_required'], False)

    def test_small_product_keeps_legacy_task_and_large_product_uses_cached_chunks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            product_a = {"product_index": 22, "encoding_id": "022", "title": "高腰直筒裤牛仔裤"}
            product_b = {"product_index": 27, "encoding_id": "027", "title": "商品乙羽绒裤"}
            cues = [
                {"source_stem": "source-a", "start": 1.0, "end": 2.0,
                 "global_start": 1.0, "global_end": 2.0, "text": "高腰直筒裤牛仔裤开始讲解。"},
                {"source_stem": "source-a", "start": 9.0, "end": 10.0,
                 "global_start": 9.0, "global_end": 10.0, "text": "高腰直筒裤牛仔裤讲解结束。"},
            ]
            for offset in range(1200):
                local_start = float(offset + 1)
                if offset == 0:
                    text = "商品乙羽绒裤开始讲解。" + "面料和细节说明。" * 12
                elif offset == 1199:
                    text = "商品乙羽绒裤讲解收尾。" + "面料和细节说明。" * 12
                else:
                    text = f"第{offset}句讲解羽绒裤面料和穿着细节。" + "商品说明。" * 12
                cues.append({"source_stem": "source-b", "start": local_start,
                             "end": local_start + 0.5, "global_start": 10000 + local_start,
                             "global_end": 10000 + local_start + 0.5, "text": text})
            titles = {"products": [product_a, product_b]}
            index = {"sources": [
                {"source_stem": "source-a", "global_start": 0.0, "global_end": 30.0},
                {"source_stem": "source-b", "global_start": 10000.0, "global_end": 13000.0}],
                "cues": cues}
            semantic_index = {"products": [
                {"product_index": 22, "windows": [{"window_id": "w22", "source_stem": "source-a",
                    "global_start": 0.0, "global_end": 20.0, "evidence_start": 1.0,
                    "evidence_end": 10.0, "distinctive_terms": ["直筒裤"]}]},
                {"product_index": 27, "windows": [{"window_id": "w27", "source_stem": "source-b",
                    "global_start": 10000.0, "global_end": 11200.5, "evidence_start": 10001.0,
                    "evidence_end": 11200.5, "distinctive_terms": ["商品乙羽绒裤"]}]}]}
            paths = SimpleNamespace(
                load_state=lambda: {"batch": {"batch_id": "batch-test", "product_ids": [22, 27]}},
                registered_scope=lambda: ("project-key", "resolve-id"),
                workspace_root=root, project_records_dir=root / "records")
            calls = []
            cue_by_id = {i: row for i, row in enumerate(index["cues"])}

            def execute_range_worker(_paths, task):
                calls.append(task)
                if task["role"] == "semantic_range_chunk":
                    cue_id = task["core_cue_ids"][0]
                    return ({"verdict": "PASS", "summary": "审阅到商品讲解或相邻边界线索。",
                             "issues": [], "signals": [{"cue_id": cue_id,
                                 "kind": "product_mention", "confidence": "high"}]},
                            {"task_digest": task["task_digest"], "payload_bytes": 1,
                             "cache_hit": False, "elapsed_seconds": 0.01,
                             "worker_elapsed_seconds": 0.01, "receipt_ref": "synthetic"})
                pid = int(task["product"]["product_index"])
                if pid == 22:
                    start_id, end_id = 0, 1
                else:
                    start_id, end_id = 2, len(index["cues"]) - 1
                start_cue, end_cue = cue_by_id[start_id], cue_by_id[end_id]
                anchor = {"source_stem": start_cue["source_stem"],
                          "time": start_cue["start"], "quote": start_cue["text"]}
                end_anchor = {"source_stem": end_cue["source_stem"],
                              "time": end_cue["start"], "quote": end_cue["text"]}
                return ({"verdict": "PASS", "issues": [], "range_decision": {
                    "start": anchor, "end": end_anchor,
                    "title_anchors": [anchor]}},
                    {"task_digest": task["task_digest"], "payload_bytes": 1,
                     "cache_hit": False, "elapsed_seconds": 0.01,
                     "worker_elapsed_seconds": 0.01, "receipt_ref": "synthetic"})

            def fake_range_index(_index, _catalog, **_kwargs):
                return semantic_index

            with patch.object(pipeline, "inputs", return_value=(titles, index)), \
                 patch("semantic_evidence.semantic_windows", side_effect=fake_range_index), \
                 patch.object(pipeline, "_execute_range_worker", side_effect=execute_range_worker), \
                 patch("publish_confirmed_ranges.build_semantic_inferred_ranges", return_value={}), \
                 patch.object(pipeline, "expected_identity", return_value={"stable": True}):
                manifest = pipeline.generate_batch_range_manifest(paths, [22, 27])

            legacy = [task for task in calls if task["role"] == "semantic_range_matching"
                      and task["range_matching_protocol"] == pipeline.RANGE_MATCHING_PROTOCOL]
            chunks = [task for task in calls if task["role"] == "semantic_range_chunk"]
            synthesis = [task for task in calls if task["role"] == "semantic_range_matching"
                         and task["range_matching_protocol"] == pipeline.RANGE_MATCHING_CHUNK_PROTOCOL]
            self.assertEqual(len(legacy), 1)
            self.assertGreater(len(chunks), 1)
            self.assertEqual(len(synthesis), 1)
            self.assertTrue(all(pipeline._json_utf8_bytes(task) <= pipeline.RANGE_CHUNK_TASK_MAX_BYTES
                                for task in chunks))
            product_b_core_ids = [cue_id for task in chunks for cue_id in task["core_cue_ids"]]
            self.assertEqual(sorted(product_b_core_ids), list(range(2, len(cues))))
            self.assertEqual(manifest["range_matching_protocols"], {
                "22": pipeline.RANGE_MATCHING_PROTOCOL,
                "27": pipeline.RANGE_MATCHING_CHUNK_PROTOCOL})


if __name__ == "__main__":
    unittest.main()
