import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from unittest.mock import patch

from registered_pipeline import (current_asr_parameters, index_asr_edges_by_selection,
                                 selected_range_identity, selection_repair_instruction,
                                 visual_repair_samples)


def test_visual_repair_keeps_semantic_worker_within_its_scope():
    instruction = selection_repair_instruction(['variant_2人物画面比例超限：无人物估算0.47'])
    assert '不要要求人物画面证据' in instruction
    assert '本机人物检测会独立复核' in instruction
    assert 'locked_selected_ranges' in instruction
    assert 'visual_repair_samples' in instruction


def test_audio_repair_keeps_boundary_instruction():
    instruction = selection_repair_instruction(['variant_1结尾建议保留完整词'])
    assert 'repair_boundary_targets' in instruction
    assert '不要要求人物画面证据' not in instruction


def test_overlap_repair_requires_semantic_reselection_and_hard_exclusion():
    instruction = selection_repair_instruction([
        'SOURCE_OVERLAP:variant_1/clip_1 与 variant_1/clip_2 重叠 23.000-27.000秒'])
    assert '重新理解当前商品' in instruction
    assert 'hard_excluded_source_ranges' in instruction
    assert '不能只改 variant 名' in instruction


def test_asr_edges_for_multiple_ranges_of_one_candidate_do_not_alias():
    first = {'candidate_id': 'same-candidate', 'variant_id': 'variant_1',
             'start': 10.0, 'end': 20.0}
    second = {'candidate_id': 'same-candidate', 'variant_id': 'variant_1',
              'start': 30.0, 'end': 40.0}
    first_id = selected_range_identity(first, 0)
    second_id = selected_range_identity(second, 1)
    edges = index_asr_edges_by_selection([
        {'candidate_id': first['candidate_id'], 'variant_id': first['variant_id'],
         'selected_range_id': first_id, 'edge': 'opening', 'resolved_time': 10.1},
        {'candidate_id': second['candidate_id'], 'variant_id': second['variant_id'],
         'selected_range_id': second_id, 'edge': 'opening', 'resolved_time': 30.1},
    ])
    assert len(edges) == 2
    assert edges[(first_id, 'opening')]['resolved_time'] == 10.1
    assert edges[(second_id, 'opening')]['resolved_time'] == 30.1


def test_final_gate_uses_the_same_multilevel_asr_parameters():
    with patch('adaptive_vision.decoder_identity', return_value={'decoder': 'synthetic'}):
        parameters = current_asr_parameters()
    assert parameters['boundary_source'] == 'codex_selected_range_start_end_with_unique_occurrence_identity'
    assert parameters['search'] == 'nearest_complete_asr_word_start_or_end_within_2_5_seconds'
    assert parameters['fallback'] == 'preserve_codex_boundary_and_mark_manual_review'


def test_visual_repair_samples_are_source_timed_and_exclude_passed_variants():
    audit = {'clocks': {'source.mp4': {'fps': 30}}, 'variants': [
        {'variant_id': 'variant_1', 'status': 'REPAIRABLE', 'samples': [
            {'source_file': 'source.mp4', 'frame': 300,
             'classification': {'label': 'NO_PERSON'}},
            {'source_file': 'source.mp4', 'frame': 330,
             'classification': {'label': 'PERSON_VISIBLE'}}]},
        {'variant_id': 'variant_2', 'status': 'PASS', 'samples': [
            {'source_file': 'source.mp4', 'frame': 360,
             'classification': {'label': 'NO_PERSON'}}]}]}
    assert visual_repair_samples(audit) == [
        {'variant_id': 'variant_1', 'source_file': 'source.mp4',
         'source_time': 10.0, 'label': 'NO_PERSON'},
        {'variant_id': 'variant_1', 'source_file': 'source.mp4',
         'source_time': 11.0, 'label': 'PERSON_VISIBLE'}]
