import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from publish_confirmed_ranges import (NEIGHBOR_INFERRED_BOUNDARY_MODE,
                                      RangeOverrideError,
                                      build_semantic_inferred_ranges,
                                      validate_semantic_product_windows_nonoverlap)
from apply_range_overrides_v2 import source_map
from build_expanded_codex_request import expanded_request
import registered_pipeline as pipeline
from unittest.mock import patch
from types import SimpleNamespace
import tempfile


class NeighborInferredRangeTests(unittest.TestCase):
    def setUp(self):
        self.index = {'sources': [
            {'source_stem': 'source-a', 'global_start': 0.0, 'global_end': 10.0},
            {'source_stem': 'source-b', 'global_start': 20.0, 'global_end': 30.0}],
            'cues': [
                {'source_stem': 'source-a', 'start': 2.0, 'end': 3.0,
                 'global_start': 2.0, 'global_end': 3.0, 'text': '羽绒裤保暖卖点'},
                {'source_stem': 'source-a', 'start': 4.0, 'end': 5.0,
                 'global_start': 4.0, 'global_end': 5.0, 'text': '羽绒裤版型细节'},
                {'source_stem': 'source-b', 'start': 5.0, 'end': 6.0,
                 'global_start': 25.0, 'global_end': 26.0, 'text': '羽绒裤面料卖点'},
                {'source_stem': 'source-b', 'start': 7.0, 'end': 8.0,
                 'global_start': 27.0, 'global_end': 28.0, 'text': '羽绒裤讲解片段结尾'},
            ]}
        self.product = {'product_index': 27, 'encoding_id': '027', 'title': '羽绒裤'}

    def test_inferred_search_windows_do_not_require_contiguous_whole_product_range(self):
        start_anchor = {'source_stem': 'source-a', 'time': 2.0,
                        'quote': '羽绒裤保暖卖点'}
        end_anchor = {'source_stem': 'source-b', 'time': 8.0,
                      'quote': '羽绒裤讲解片段结尾'}
        manifest = {'schema_version': 1,
            'authority': 'user_directed_semantic_inference',
            'authorization': '用户授权当前批次语义范围推断。',
            'products': [{'product_index': 27,
                'start': {'source_stem': 'source-a', 'time': 2.0},
                'end': {'source_stem': 'source-b', 'time': 8.0},
                'start_anchor': start_anchor, 'end_anchor': end_anchor,
                'title_anchors': [start_anchor],
                'boundary_mode': NEIGHBOR_INFERRED_BOUNDARY_MODE,
                'boundary_notes': [{'previous_product_id': 26,
                                    'next_product_id': 28}],
                'candidate_windows': [
                    {'source_stem': 'source-a', 'start': 2.0, 'end': 5.0,
                     'start_anchor': start_anchor,
                     'end_anchor': {'source_stem': 'source-a', 'time': 5.0,
                                    'quote': '羽绒裤版型细节'}},
                    {'source_stem': 'source-b', 'start': 5.0, 'end': 8.0,
                     'start_anchor': {'source_stem': 'source-b', 'time': 5.0,
                                      'quote': '羽绒裤面料卖点'},
                     'end_anchor': end_anchor},
                ]}]}

        result = build_semantic_inferred_ranges(
            manifest, self.index, {'products': [self.product]}, [27],
            project_key='project-key', batch_id='batch-1')

        row = result['products'][0]
        self.assertEqual(row['boundary_mode'], NEIGHBOR_INFERRED_BOUNDARY_MODE)
        self.assertEqual([(part['media_stem'], part['start'], part['end'])
                          for part in row['ranges']],
                         [('source-a', 2.0, 5.0), ('source-b', 5.0, 8.0)])

    def test_neighbor_search_windows_still_reject_cross_product_overlap(self):
        fallback = {'product_index': 27, 'global_start': 2.0, 'global_end': 28.0,
                    'semantic_range_proof': {'decision': {
                        'boundary_mode': NEIGHBOR_INFERRED_BOUNDARY_MODE,
                        'candidate_windows': [{'source_stem': 'source-a', 'start': 2.0,
                                              'end': 5.0}]}}}
        neighbor = {'product_index': 28, 'global_start': 4.0, 'global_end': 6.0,
                    'semantic_range_proof': {'decision': {}}}
        with self.assertRaisesRegex(RangeOverrideError, 'cross_product_search_window_overlap'):
            validate_semantic_product_windows_nonoverlap(
                [fallback, neighbor], source_map(self.index))

    def test_inferred_search_request_does_not_claim_full_range_coverage(self):
        product = {**self.product, 'global_start': 2.0, 'global_end': 28.0,
                   'range_basis': 'user_directed_semantic_inference',
                   'boundary_mode': NEIGHBOR_INFERRED_BOUNDARY_MODE,
                   'boundary_notes': [{'previous_product_id': 26, 'next_product_id': 28}]}
        request = expanded_request(product, [], [], 'range-digest', 'taobao_legacy', {
            'duration_exclusive_seconds': [45.0, 60.0],
            'target_variant_count': 3, 'target_duration_seconds': 52.0,
        })

        self.assertIn('搜索包络', request['rules']['requirements'][0])
        self.assertTrue(any('不能因整体商品起点/终点不确定' in row
                            for row in request['rules']['requirements']))
        self.assertFalse(request['candidate_pool_summary']['complete_product_range_scanned'])
        self.assertIsNone(request['candidate_pool_summary']['coverage'])

    def test_selection_prompt_preserves_three_variant_goal_with_inferred_bounds(self):
        identity = {'project_key': 'project-key', 'product_id': 27,
                    'encoding_id': '027', 'rules_fingerprint': 'rules-v1',
                    'dependencies': []}
        candidate = {'identity': identity, 'body_digest': 'candidate-digest', 'body': {
            'boundary_mode': NEIGHBOR_INFERRED_BOUNDARY_MODE,
            'boundary_notes': [{'previous_product_id': 26, 'next_product_id': 28}],
            'candidate_search_scope': 'neighbor_inferred_search_windows',
            'candidates': [{'candidate_id': 'c1', 'source_file': 'source-a.mp4',
                'time_range': {'start': 10.0, 'end': 70.0}, 'asr_text': '羽绒裤卖点',
                'local_title_matches': ['羽绒裤'], 'forbidden_annotations': [],
                'semantic_boundary': {'duration_sec': 60.0},
                'subtitle': [{'start': 10.0, 'end': 11.0, 'text': '羽绒裤卖点'}]}]}}
        with tempfile.TemporaryDirectory() as directory:
            paths = SimpleNamespace(
                load_state=lambda: {'resolve': {'project_name': 'Synthetic 淘宝'}},
                workspace_root=Path(directory))
            with patch.object(pipeline, 'load_stage', return_value=candidate):
                task = pipeline.selection_task(paths, 'candidate-ref')

        self.assertEqual(task['editing_policy']['target_variant_count'], 3)
        self.assertIn('neighbor_inferred_search_windows', task['candidate_search_scope'])
        self.assertTrue(any('不得因整段起点或终点不精确而拒绝选段' in row
                            for row in task['applicable_shared_rules']))
        self.assertFalse(any('完整候选池耗尽后才允许减少数量' in row
                             for row in task['applicable_shared_rules']))


if __name__ == '__main__':
    unittest.main()
