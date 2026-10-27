"""Reallocate complete speech on proven frame-safe edges in current-run ASR.

Never retime ASR, widen a cut into an excluded word, or import old product plans.
This is a bounded batch repair stage, not per-product hand-written correction.
"""
import argparse
import json
from pathlib import Path
import time

from asr_identity import model_identity
from codex_worker import launch
from final_quality_gate import plan_errors
from project_registry import atomic_json, digest
from runtime_meta import _sha256
from sentence_boundaries import CONTRACT, SELECTION_RULE, bounded_edges
from sentence_trial import Trial, obj, STRING


INT = {'type': 'integer'}
SCHEMA = obj({'variants': {'type': 'array', 'items': obj({'variant_id': STRING, 'focus': STRING,
    'clips': {'type': 'array', 'items': obj({'window': INT, 'first': INT, 'last': INT,
                                          'opening_reason': STRING, 'ending_reason': STRING})}})},
    'coverage': STRING, 'insufficient_reason': STRING})


def read_body(path):
    value = json.loads(path.read_text())
    if value['body_digest'] != digest(value['body']): raise ValueError('trial_parent_corrupt')
    return value['body']


def run(trial, asr_path, selection_path):
    from adaptive_vision import proven_clock
    from refine_plans_v2 import audit_audio_start, refine_audio_end, normalize_physical_clips
    for path in (asr_path, selection_path):
        if path.resolve().parent != trial.folder.resolve(): raise ValueError('repair_parent_outside_current_run')
    for row in trial.run['inputs']:
        if _sha256(Path(row['path'])) != row['sha256']: raise ValueError('source_changed_since_trial')
    asr, selection = read_body(asr_path), read_body(selection_path)
    if asr['model'] != model_identity('large-v3-turbo'): raise ValueError('repair_asr_model_changed')
    sources = {Path(p).stem: Path(p) for p in trial.state['video_paths']}
    clocks, windows = {}, []
    for i, w in enumerate(asr['windows']):
        media = sources[w['source_file']]
        if w['source_file'] not in clocks: clocks[w['source_file']] = proven_clock(trial.paths, media)
        fps = clocks[w['source_file']]['fps']; words = w['words']; starts = []; ends = []
        # Test the two edges independently, without modifying a token's time.
        for n in range(len(words)):
            try: bounded_edges(words, n, len(words)-1, fps); starts.append(n)
            except ValueError: pass
            try: bounded_edges(words, 0, n, fps); ends.append(n)
            except ValueError: pass
        windows.append(dict(window=i, source=w['source_file'], fps=fps, safe_first=starts,
            safe_last=ends, words=[[n, x['start'], x['end'], x['text']] for n, x in enumerate(words)]))
    task = dict(role='selection', product_title=selection['product']['title'], windows=windows,
        prior_variants=selection['variants'], instructions=SELECTION_RULE+
        '这是本轮物理修复：从所有提供的真实ASR上下文联合重组3条，严格>45且<60秒，目标48–56秒，'
        '物理时间零重叠、卖点不高度重复。每段first必须来自safe_first，last必须来自safe_last，first<=last。'
        '这些只是物理可切位置，不证明语义独立；必须结合前文选择完整句子。不要选字幕残尾、承接半句或无关前言。'
        '可保留自然口语，不拼词，不为强删填充词制造额外切口；去价格促销链接寒暄后台他品空话。'
        '每段需写opening_reason及ending_reason；所有windows全部搜索。若边界不安全，换完整句群，'
        '不修改ASR时间戳。至少尽力完成三条；不足须说明完整池内实际限制，不能凑数。')
    identity = digest(dict(task=task, parents=[_sha256(asr_path), _sha256(selection_path)],
                           code=_sha256(Path(__file__)), contract=CONTRACT))
    receipt_path = trial.folder/('frame_safe_selection_'+identity[:12]+'.json')
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if receipt['task_digest'] != digest(task): raise ValueError('repair_task_changed')
    else:
        receipt = launch(task, SCHEMA, timeout=360)
        atomic_json(receipt_path, dict(task_digest=digest(task), **receipt))
    outcome = receipt['result']; variants = []; used = []
    for v in outcome['variants']:
        clips = []
        for n, choice in enumerate(v['clips']):
            i, first, last = choice['window'], choice['first'], choice['last']; w = asr['windows'][i]
            if first not in windows[i]['safe_first'] or last not in windows[i]['safe_last']:
                raise ValueError('model_selected_unproven_frame_edge')
            fps = windows[i]['fps']; lo, hi, proof = bounded_edges(w['words'], first, last, fps)
            source = w['source_file']; media = sources[source]
            if any(source == s and min(hi, b) > max(lo, a) for s, a, b in used):
                raise ValueError('repaired_source_overlap')
            used.append((source, lo, hi))
            _, head = audit_audio_start(media, lo/fps)
            _, tail = refine_audio_end(media, lo/fps, hi/fps, clocks[source]['frame_count']/fps)
            tail['risk'] = tail['risk'] or tail.get('end_extension_sec', 0) > 0
            text = ''.join(x['text'] for x in w['words'][first:last+1])
            clips.append(dict(unit_id=f'{v["variant_id"]}_c{n+1}', source_file=media.name,
                source_start_frame=lo, source_end_frame=hi, audio_start_frame=lo, audio_end_frame=hi,
                fps=fps, duration_sec=(hi-lo)/fps, record_duration_frames=hi-lo, words=w['words'],
                transcript=text, sentence_edges=proof, audio_boundary_verified=not head['risk'] and not tail['risk'],
                audio_audit=dict(opening=head, ending=tail), audit=dict(transcript=text),
                source_subtitle_text=text, previous_source_context=[],
                semantic_edge_reasons=dict(opening=choice['opening_reason'], ending=choice['ending_reason'])))
        clips, _ = normalize_physical_clips(clips)
        variants.append(dict(variant_id=v['variant_id'], product_index=trial.product, focus=v['focus'],
            clips=clips, duration_seconds=sum(c['duration_sec'] for c in clips)))
    plan = dict(product=selection['product'], variants=variants, sentence_contract=CONTRACT,
        alignment_errors=[], clock_evidence=clocks, repair_parent_asr=_sha256(asr_path),
        repair_parent_selection=_sha256(selection_path), complete_pool_notes=outcome['coverage'],
        insufficient_reason=outcome['insufficient_reason'])
    plan['local_errors'] = plan_errors(plan)
    atomic_json(trial.folder/'FRAME_SAFE_PLAN.json', plan)
    review = trial.review(plan)
    atomic_json(trial.folder/'FRAME_SAFE_REVIEW.json', review)
    status = 'SEMANTIC_PASS' if (len(variants) == 3 and not plan['local_errors'] and review['verdict'] == 'PASS'
        and all(review['checks'].values()) and not review['issues']) else 'REPAIRABLE'
    result = dict(status=status, durations=[v['duration_seconds'] for v in variants], review=review,
        local_errors=plan['local_errors'], person_audit='NOT_RUN', resolve_write=False,
        delivery_status='NOT_FINAL', requested_variant_count=3, candidate_variant_count=len(variants), same_run_asr_reused=True, old_semantic_cache_hits=0)
    atomic_json(trial.folder/'FRAME_SAFE_RESULT.json', result)
    print(json.dumps({k: result[k] for k in ('status', 'durations', 'local_errors')}, ensure_ascii=False), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='.')
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--product', type=int, required=True)
    parser.add_argument('--asr', type=Path, required=True)
    parser.add_argument('--selection', type=Path, required=True)
    args = parser.parse_args(); trial = Trial(args.root, args.workspace, args.product)
    started = time.time(); trial.event('frame_safe_repair', 'START')
    try: run(trial, args.asr, args.selection)
    finally: trial.event('frame_safe_repair', 'END', seconds=time.time()-started)


if __name__ == '__main__':
    main()
