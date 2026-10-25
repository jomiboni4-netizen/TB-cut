import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from unittest.mock import patch

from registered_pipeline import (current_asr_parameters, selection_repair_instruction,
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


def test_final_gate_uses_the_same_multilevel_asr_parameters():
    with patch('adaptive_vision.decoder_identity', return_value={'decoder': 'synthetic'}):
        parameters = current_asr_parameters()
    assert parameters['fallback_ladder'] == [
        'initial_window', 'expanded_window', 'subtitle_boundary_manual_cutpoint_preview']


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
