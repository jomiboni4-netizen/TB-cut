"""Render bounded, sealed source-frame evidence for human review. Never promote a verdict."""
import hashlib
import hmac
import json
from pathlib import Path
import subprocess
from project_registry import atomic_json, digest, workspace_lock
from runtime_meta import CacheIdentityError, _sha256
from product_checkpoints import load_stage, current_plan_path
from trial_session import validate_trial
from adaptive_vision import CONTRACT, POLICY, classifier_identity
from compute_store import check_media


def export_frame(media, frame, output):
    subprocess.run(['/opt/homebrew/bin/ffmpeg','-v','error','-i',str(media),'-vf',f'select=eq(n\\,{frame})',
                    '-frames:v','1','-vsync','0',str(output)],capture_output=True,check=True)
    if not output.is_file(): raise CacheIdentityError('human_review_frame_decode_missing')


def render_packet(paths, plan_ref, vision_ref):
    trial = validate_trial(paths)
    plan = load_stage(paths,plan_ref)
    pid = plan['identity']['product_id']
    if not trial or pid not in trial['products']:
        raise CacheIdentityError('human_review_requires_bounded_trial')
    pointer = json.loads(current_plan_path(paths,pid,plan['identity']['encoding_id']).read_text())
    if pointer['reference'] != plan_ref: raise CacheIdentityError('human_review_plan_not_current')
    record = load_stage(paths,vision_ref,product_id=pid,encoding_id=plan['identity']['encoding_id'])
    body = record['body']
    secret = paths.project_records_dir/'worker_seal.key'
    unsigned = {k:v for k,v in body.items() if k != 'seal'}
    if (record['identity']['stage'] != 'vision' or plan_ref not in [d['path'] for d in record['identity']['dependencies']]
            or body.get('plan_digest') != plan['body_digest'] or body.get('contract') != CONTRACT or body.get('policy') != POLICY
            or not secret.is_file() or not isinstance(body.get('seal'),str)
            or not hmac.compare_digest(body['seal'],hmac.new(secret.read_bytes(),digest(unsigned).encode(),hashlib.sha256).hexdigest())
            or classifier_identity(body['classifier']['detector_path']) != body['classifier']):
        raise CacheIdentityError('human_review_visual_provenance_invalid')
    folder = paths.workspace_root/'host_review_packets'/str(pid)/plan['body_digest']
    with workspace_lock(paths):
        folder.mkdir(parents=True,exist_ok=True)
        rows = []
        for variant in body['variants']:
            for sample in variant['samples']:
                signature = body['media'][sample['source_file']]
                check_media(signature)
                image = folder/(digest([sample['source_file'],sample['frame']])+'.png')
                if not image.exists(): export_frame(signature['path'],sample['frame'],image)
                fps = body['clocks'][sample['source_file']]['fps']
                rows.append({'variant_id':variant['variant_id'],'source_file':sample['source_file'],
                             'source_frame':sample['frame'],'source_seconds':sample['frame']/fps,
                             'classification':sample['classification'],'image':image.name,'image_sha256':_sha256(image)})
        packet = {'status':'REVIEW_PENDING','run_id':trial['run_id'],'product_id':pid,
                  'plan_digest':plan['body_digest'],'vision_digest':record['body_digest'],'samples':rows,
                  'authority':'human review materials only; cannot create HOST_VISIBLE or FINAL_PASS'}
        atomic_json(folder/'manifest.json',packet)
    return folder/'manifest.json'
