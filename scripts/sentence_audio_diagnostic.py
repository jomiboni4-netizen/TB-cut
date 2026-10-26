"""Local, resumable edge diagnostics. Activity is never proof of a clipped word.

Keeps plans, original ASR and semantic approvals immutable. New contextual ASR
is evidence for a bounded repair, not an automatic audio PASS or write permit.
"""
import argparse
import json
from pathlib import Path
import time

from compute_metrics import Metrics, scope, span
from compute_store import cached, identity, media_signature
from project_registry import atomic_json, digest, workspace_lock
from runtime_meta import _sha256
from audio_boundary_evidence import activity_at_cut

CONTRACT = 'sentence-audio-diagnostic-v1'


def producer_digest():
    return digest({name: _sha256(Path(__file__).with_name(name)) for name in
                   ('sentence_audio_diagnostic.py', 'audio_boundary_evidence.py',
                    'refine_plans_v2.py', 'registered_pipeline.py', 'windowed_asr_v2.py')})


def run(trial, plan_path, asr_path, model_name='large-v3-turbo'):
    from asr_identity import model_identity, snap_interval
    from review_compute_cache import LazyModel
    from registered_pipeline import transcribe_window, current_asr_parameters
    from sentence_boundaries import verify_edges
    from refine_plans_v2 import audio_level_frames
    from windowed_asr_v2 import load_model
    trial.paths.registered_scope()
    producer = producer_digest()
    state_digest = digest(trial.paths.load_state())
    plan = json.loads(plan_path.read_text())
    parent = json.loads(asr_path.read_text())
    if (parent['body_digest'] != digest(parent['body'])
            or plan.get('repair_parent_asr') != _sha256(asr_path)):
        raise ValueError('audio_diagnostic_asr_parent_mismatch')
    sources = {Path(p).name: Path(p) for p in trial.state['video_paths']}
    used = {c['source_file'] for v in plan['variants'] for c in v['clips']}
    registered = {str(Path(row['path']).resolve()): row for row in trial.run['inputs']}
    signatures = {}
    for name in used:
        signature = media_signature(sources[name])
        if signature['sha256'] != registered[signature['path']]['sha256']:
            raise ValueError('audio_diagnostic_source_changed')
        signatures[name] = signature
    if model_identity(parent['body']['model']['name']) != parent['body']['model']:
        raise ValueError('audio_diagnostic_model_changed')
    model = model_identity(model_name)
    parameters = current_asr_parameters()
    def load():
        with span('asr_model_load', 1):
            return load_model(model['resolved_path'])
    worker = LazyModel(load)
    rows = []
    for variant in plan['variants']:
        for clip in variant['clips']:
            if not verify_edges(clip):
                raise ValueError('audio_diagnostic_sentence_edges_stale')
            for edge, field in [('opening', 'source_start_frame'), ('ending', 'source_end_frame')]:
                cut = clip[field]/clip['fps']
                old = clip['audio_audit'][edge]
                # Exactly aligned bins around the cut; no later-speech lookahead.
                with span('audio_rms_boundaries', .4):
                    levels = audio_level_frames(sources[clip['source_file']], max(0., cut-.2), cut+.2)
                observation = activity_at_cut(levels, cut)
                row = dict(variant=variant['variant_id'], unit=clip['unit_id'], edge=edge,
                           cut=cut, prior_risk=old['risk'], observation=observation)
                if old['risk']:
                    lo, hi = snap_interval(max(0., cut-2.5), cut+2.5, cut+2.5)
                    expected = identity(trial.paths, 'asr', signatures[clip['source_file']],
                                        model, parameters, [lo, hi])
                    with span('edge_context_asr', hi-lo):
                        words, hit = cached(trial.paths, expected,
                            lambda: transcribe_window(sources[clip['source_file']], lo, hi, worker))
                    row.update(context_words=words, context_cache_hit=hit, bounds=[lo, hi])
                rows.append(row)
                print(json.dumps(dict(unit=row['unit'], edge=edge, prior_risk=old['risk'],
                    observation=observation['status'], context_asr='context_words' in row)), flush=True)
    for signature in signatures.values():
        from compute_store import check_media
        check_media(signature)
    if model_identity(model_name) != model:
        raise ValueError('audio_diagnostic_model_changed_during_audit')
    if json.loads(plan_path.read_text()) != plan or _sha256(asr_path) != plan['repair_parent_asr']:
        raise ValueError('audio_diagnostic_parent_changed_during_audit')
    if producer_digest() != producer or digest(trial.paths.load_state()) != state_digest:
        raise ValueError('audio_diagnostic_producer_or_state_changed_during_audit')
    trial.paths.registered_scope()
    return dict(contract=CONTRACT, plan_digest=digest(plan), parent_asr_sha256=_sha256(asr_path),
                producer_sha256=producer, state_digest=state_digest,
                model=model, media=signatures,
                rows=rows, status='DIAGNOSTIC_ONLY', audio_gate='UNVERIFIED',
                resolve_write=False, original_plan_modified=False)


def main():
    from sentence_trial import Trial
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.')
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--product', type=int, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--asr', type=Path, required=True)
    parser.add_argument('--model', choices=['large-v3-turbo', 'medium'], default='large-v3-turbo')
    args = parser.parse_args()
    trial = Trial(args.root, args.workspace, args.product)
    for path in (args.plan, args.asr):
        if path.resolve().parent != trial.folder.resolve():
            raise ValueError('audio_diagnostic_parent_outside_current_run')
    metrics = Metrics(); started = time.time()
    trial.event('audio_diagnostic', 'START')
    try:
        with workspace_lock(trial.paths), scope(metrics):
            result = run(trial, args.plan, args.asr, args.model)
            result['metrics'] = metrics.rows
            result['seconds'] = time.time()-started
            target = trial.folder/('audio_diagnostic_'+digest(result)[:12]+'.json')
            atomic_json(target, result)
            print(json.dumps(dict(status=result['status'], artifact=target.name,
                                  seconds=result['seconds']), ensure_ascii=False), flush=True)
    except BaseException as exc:
        trial.event('audio_diagnostic', 'FAILED', seconds=time.time()-started,
                    error=type(exc).__name__, metrics=metrics.rows)
        raise
    else:
        trial.event('audio_diagnostic', 'END', seconds=time.time()-started,
                    metrics=metrics.rows, artifact=target.name)


if __name__ == '__main__':
    main()
