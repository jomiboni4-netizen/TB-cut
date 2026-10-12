"""Synthetic transport fixtures only; no production test mode or fake review provider."""
import uuid
from unittest.mock import patch
from project_registry import digest
from product_checkpoints import capture_stage, publish_stage
from publish_confirmed_ranges import build_confirmed_ranges
import codex_worker
from final_quality_gate import final_task, import_final_review


def valid_plan(source='001.mp4', offset=0, text='这件衣服穿起来轻便活动不受限制'):
    return {'variants': [{'variant_id': 'variant_1', 'clips': [{
        'source_file': source, 'source_start_frame': int(offset*10), 'source_end_frame': int((offset+50)*10),
        'audio_start_frame': int(offset*10), 'audio_end_frame': int((offset+50)*10), 'fps': 10,
        'words': [{'start': offset+1, 'end': offset+49, 'text': text}], 'transcript': text,
        'audio_boundary_verified': True}]}]}


def range_body(pid=1, encoding='001', start=0, end=100):
    manifest={'products': [{'product_index': pid, 'decision_id': f'synthetic-{pid}', 'confirmed_by': 'synthetic reviewer',
         'confirmed_at': '2026-10-12T00:00:00Z', 'start': {'source_stem': '001', 'time': start},
         'end': {'source_stem': '001', 'time': end}}]}
    index={'sources': [{'source_stem': '001', 'global_start': 0, 'global_end': 2000, 'duration': 2000}]}
    titles={'products': [{'product_index': pid, 'encoding_id': encoding, 'title': 'Synthetic product'}]}
    return build_confirmed_ranges(manifest,index,titles)['products'][0]


def fake_outcome(task, *, verdict='PASS', checks=None, selected_ranges=None):
    return {'thread_id': str(uuid.uuid4()), 'result': {'task_digest': task['task_digest'], 'verdict': verdict,
            'checks': checks or {k: True for k in ('opening', 'ending', 'causal_logic', 'seams', 'selling_value', 'duplicates')},
            'issues': [] if verdict=='PASS' else ['synthetic review failure'], 'selected_ranges': selected_ranges or []},
            'events_digest': digest(['synthetic transport']), 'elapsed_seconds': 0.01, 'usage': {}}


def audit_fixture(paths, plan_ref):
    from product_checkpoints import load_stage
    plan=load_stage(paths,plan_ref)
    body={'variants': [{'variant_id': v['variant_id'], 'host_identity_verified': True, 'full_plan_coverage': True,
                         'host_visible_ratio_lower_bound': 0.9} for v in plan['body']['variants']]}
    args=dict(product_id=plan['identity']['product_id'],encoding_id=plan['identity']['encoding_id'],stage='vision',
              version=digest(body),model_version='synthetic-detector',dependencies=[plan_ref])
    vision=publish_stage(paths,**args,body=body,snapshot=capture_stage(paths,**args))
    task=final_task(paths,plan_ref)
    with patch.object(codex_worker,'launch',side_effect=fake_outcome):
        receipt=codex_worker.execute(paths,task)
    return import_final_review(paths,plan_ref,vision,receipt)
