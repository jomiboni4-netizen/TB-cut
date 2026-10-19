"""One resumable, timed fresh-product trial; no Resolve mutation or draft waiver.

Uses the explicit cold workspace's current index, never historical selections.
Stages are immutable and bound to input/code digests. All stage failures remain
visible; a failed stage is never silently retried or re-attested as PASS.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time

from coldstart_semantics import load_run
from codex_worker import launch
from project_registry import atomic_json, digest, workspace_lock
from runtime_meta import _sha256
from sentence_boundaries import CONTRACT, SELECTION_RULE, anchored_words, bounded_edges


def obj(props):
    return dict(type='object', additionalProperties=False, properties=props, required=list(props))


STRING = {'type': 'string'}
BOOL = {'type': 'boolean'}
STRINGS = {'type': 'array', 'items': STRING}
SELECTION_SCHEMA = obj({
    'coverage': {'type': 'array', 'items': obj({'window': STRING, 'decision': STRING, 'reason': STRING})},
    'units': {'type': 'array', 'items': obj({'unit_id': STRING, 'source_ids': STRINGS,
        'opening_independent': BOOL, 'opening_reason': STRING, 'ending_complete': BOOL})},
    'variants': {'type': 'array', 'items': obj({'variant_id': STRING, 'focus': STRING, 'unit_ids': STRINGS})},
    'insufficient_reason': STRING})


class Trial:
    def __init__(self, root, workspace, product):
        self.paths, self.state, self.run = load_run(root, workspace)
        self.product = product
        if self.state['batch']['product_ids'] != [product]:
            raise ValueError('trial_requires_explicit_single_product_batch')
        self.folder = self.paths.workspace_root / 'sentence_trial'
        self.folder.mkdir(exist_ok=True)
        self.log = self.folder / 'events.jsonl'
        self.stage_paths = {}
        self.base = dict(contract=CONTRACT, run=self.run['run_id'], product=product,
            inputs=self.run['input_content_digest'],
            producer=digest({n: _sha256(Path(__file__).with_name(n)) for n in
                ('sentence_trial.py', 'sentence_boundaries.py', 'final_quality_gate.py', 'refine_plans_v2.py')}),
            rules=_sha256(self.paths.repo_root/'PROJECT_RULES.md'))

    def event(self, stage, status, **extra):
        with self.log.open('a', encoding='utf-8') as out:
            out.write(json.dumps(dict(stage=stage, status=status, wall=time.time(),
                                     monotonic_ns=time.monotonic_ns(), **extra), ensure_ascii=False)+'\n')

    def stage(self, name, inputs, fn):
        identity = digest([self.base, inputs]); path = self.folder/(name+'_'+identity[:12]+'.json')
        self.stage_paths[name] = path
        if path.exists():
            prior = json.loads(path.read_text())
            if prior['identity'] != identity or prior['body_digest'] != digest(prior['body']):
                raise ValueError('trial_checkpoint_stale:'+name)
            self.event(name, 'CACHE_HIT_THIS_RUN')
            return prior['body']
        self.event(name, 'START', identity=identity); start = time.perf_counter()
        try:
            result = fn()
            atomic_json(path, dict(identity=identity, body=result, body_digest=digest(result)))
        except BaseException as exc:
            self.event(name, 'FAILED', seconds=time.perf_counter()-start, error=str(exc)[:500])
            raise
        self.event(name, 'END', seconds=time.perf_counter()-start)
        print(json.dumps(dict(stage=name, seconds=round(time.perf_counter()-start, 2)), ensure_ascii=False), flush=True)
        return result

    def select(self, index, product, locator, repair=None):
        sources = {s['source_stem']: s for s in index['sources']}
        windows = []
        # Merge overlapping locator ranges, adding context on both sides. All
        # resulting windows go to the same selector, not separate chat turns.
        for source, src in sources.items():
            spans = sorted((max(0., w['global_start']-src['global_start']-35),
                            min(src['duration'], w['global_end']-src['global_start']+35))
                           for w in locator['windows'] if w['source_stem'] == source)
            merged = []
            for lo, hi in spans:
                if merged and lo <= merged[-1][1]: merged[-1][1] = max(hi, merged[-1][1])
                else: merged.append([lo, hi])
            for lo, hi in merged:
                cues = [c for c in index['cues'] if c['source_stem'] == source and c['end'] > lo and c['start'] < hi]
                windows.append(dict(window=f'w{len(windows)+1}', source=source,
                    cues=[[c['source_id'], c['start'], c['end'], c['text']] for c in cues]))
        task = dict(role='selection', product=product['title'], windows=windows,
            instructions=SELECTION_RULE+'完整扫描每个窗口，按商品材质版型结构辨别归属，链接号无归属权威。'
            '给出全部有价值的连续完整句群（含备选）并联合分配三版；各版目标48–56秒、严格45–60秒、'
            '源时间零重叠、卖点无高度重复。每个unit仅同源连续source_ids，必须包含中间全部ID，不能拼词。'
            '宁可保留自然连续的完整表达，优先每版3–5个较长句群，非硬性数量限制。'
            '去价格促销链接闲聊后台他品和短时间同义重复，保留有效细节；不能用这些内容凑时长。'
            '每个unit检查其前一句，opening_independent必须真实判断，ending_complete判断完整结束；'
            '每个window都有coverage，不能只找第一条；不足3版须说明完整池搜索后缺哪条。')
        if repair:
            task['repair'] = repair
            task['instructions'] += '这是有记录的一次整池修复：已指出有依赖的片段不得继续选中；在完整备选池重新联合分配，不进行声音或人物重算。'
        receipt_path = self.folder/('selection_repair_receipt.json' if repair else 'selection_receipt.json')
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            if receipt['task_digest'] != digest(task): raise ValueError('selection_receipt_task_changed')
            self.event('selection_transport', 'CACHE_HIT_THIS_RUN', repair=bool(repair))
        else:
            receipt = launch(task, SELECTION_SCHEMA, timeout=360)
            atomic_json(receipt_path, dict(task_digest=digest(task), **receipt))
        result = receipt['result']
        visible = {row[0] for window in windows for row in window['cues']}
        by_id = {c['source_id']: c for c in index['cues'] if c['source_id'] in visible}
        if sorted(c['window'] for c in result['coverage']) != sorted(w['window'] for w in windows):
            raise ValueError('selection_did_not_cover_full_locator_pool')
        units = {}
        for u in result['units']:
            if not u['source_ids'] or len(set(u['source_ids'])) != len(u['source_ids']):
                raise ValueError('empty_or_repeated_source_ids')
            rows = [by_id[i] for i in u['source_ids']]
            if (len({r['source_stem'] for r in rows}) != 1 or
                    [r['ordinal'] for r in rows] != list(range(rows[0]['ordinal'], rows[-1]['ordinal']+1))):
                raise ValueError('noncontiguous_sentence')
            if u['unit_id'] in units: raise ValueError('duplicate_unit_id')
            before = [r for r in index['cues'] if r['source_stem'] == rows[0]['source_stem']
                      and rows[0]['start']-5 < r['end'] <= rows[0]['start']]
            units[u['unit_id']] = dict(u, source=rows[0]['source_stem'], start=rows[0]['start'],
                end=rows[-1]['end'], text=''.join(r['text'] for r in rows), previous_context=before)
        used = []
        for v in result['variants']:
            for unit_index, uid in enumerate(v['unit_ids']):
                u = units[uid]
                if (unit_index == 0 and not u['opening_independent']) or not u['ending_complete'] or not u['opening_reason']:
                    if repair:
                        raise ValueError('selected_unit_not_independently_complete_after_repair:'+uid)
                    return self.select(index, product, locator, repair=dict(previous_result=result,
                        reason='selected_unit_not_independently_complete:'+uid))
                if any(u['source'] == x['source'] and min(u['end'], x['end']) > max(u['start'], x['start']) for x in used):
                    raise ValueError('selection_source_overlap')
                used.append(u)
        if not result['variants'] or len(result['variants']) > 3:
            raise ValueError('invalid_variant_count')
        if len(result['variants']) < 3 and not result['insufficient_reason']:
            raise ValueError('underfill_without_full_pool_reason')
        return dict(product=product, units=list(units.values()), variants=result['variants'],
                    coverage=result['coverage'], insufficient_reason=result['insufficient_reason'])

    def asr(self, selection, index):
        from faster_whisper import WhisperModel
        from windowed_asr_v2 import merge_windows
        from asr_identity import model_identity
        sources = {s['source_stem']: s for s in index['sources']}
        used = {uid for v in selection['variants'] for uid in v['unit_ids']}
        jobs = merge_windows([dict(source_file=u['source'], start=max(0., u['start']-5),
                    end=min(sources[u['source']]['duration'], u['end']+5))
                for u in selection['units'] if u['unit_id'] in used])
        # Decode real audio from a 20ms grid before recognition. Never shift
        # old ASR timestamps onto video frames after recognition.
        for job in jobs:
            from asr_identity import snap_interval
            job['start'], job['end'] = snap_interval(job['start'], job['end'], sources[job['source_file']]['duration'])
        ident = model_identity('large-v3-turbo'); model = WhisperModel(ident['resolved_path'],
            device='cpu', compute_type='int8', local_files_only=True)
        windows = []
        for i, j in enumerate(jobs):
            wav = self.folder/f'context_{i}.wav'
            subprocess.run(['/opt/homebrew/bin/ffmpeg', '-v', 'error', '-y', '-ss', str(j['start']),
                '-i', sources[j['source_file']]['video_path'], '-t', str(j['end']-j['start']),
                '-vn', '-ac', '1', '-ar', '16000', str(wav)], capture_output=True, check=True)
            segments, _ = model.transcribe(str(wav), language='zh', beam_size=5, vad_filter=True,
                word_timestamps=True, condition_on_previous_text=False)
            words = [dict(start=j['start']+w.start, end=j['start']+w.end, text=w.word,
                          probability=w.probability) for seg in segments for w in seg.words or []]
            windows.append(dict(j, words=words))
        return dict(model=ident, parameters={'audio_origin_grid_seconds': .02, 'protocol': 'window-words-v4-grid-origin'}, windows=windows, audio_seconds=sum(j['end']-j['start'] for j in jobs))

    def build(self, selection, asr, index):
        from adaptive_vision import proven_clock
        from refine_plans_v2 import audit_audio_start, refine_audio_end, normalize_physical_clips
        from final_quality_gate import plan_errors
        sources = {s['source_stem']: s for s in index['sources']}
        units = {u['unit_id']: u for u in selection['units']}; clocks = {}; variants = []; errors = []
        for v in selection['variants']:
            clips = []
            for unit_index, uid in enumerate(v['unit_ids']):
                u = units[uid]; src = sources[u['source']]; media = Path(src['video_path'])
                if u['source'] not in clocks: clocks[u['source']] = proven_clock(self.paths, media)
                fps = clocks[u['source']]['fps']
                w = next(w for w in asr['windows'] if w['source_file'] == u['source']
                         and w['start'] <= u['start'] and w['end'] >= u['end'])
                try:
                    first, last, similarity = anchored_words(w['words'], u['text'], u['start'], u['end'])
                    lo, hi, proof = bounded_edges(w['words'], first, last, fps)
                except ValueError as exc:
                    errors.append(dict(variant=v['variant_id'], unit=uid, reason=str(exc)))
                    continue
                _, opening = audit_audio_start(media, lo/fps)
                _, ending = refine_audio_end(media, lo/fps, hi/fps, src['duration'])
                ending['risk'] = ending['risk'] or ending.get('end_extension_sec', 0) > 0
                text = ''.join(x['text'] for x in w['words'][first:last+1])
                clips.append(dict(unit_id=uid, source_file=media.name, source_start_frame=lo,
                    source_end_frame=hi, audio_start_frame=lo, audio_end_frame=hi, fps=fps,
                    duration_sec=(hi-lo)/fps, record_duration_frames=hi-lo, words=w['words'],
                    transcript=text, sentence_edges=proof, source_subtitle_text=u['text'],
                    previous_source_context=u['previous_context'], alignment_similarity=similarity,
                    audio_boundary_verified=not opening['risk'] and not ending['risk'],
                    audio_audit=dict(opening=opening, ending=ending), audit=dict(transcript=text)))
            clips, _ = normalize_physical_clips(clips)
            variants.append(dict(variant_id=v['variant_id'], product_index=self.product, focus=v['focus'],
                clips=clips, duration_seconds=sum(c['duration_sec'] for c in clips)))
        plan = dict(product=selection['product'], variants=variants, sentence_contract=CONTRACT,
                    clock_evidence=clocks, alignment_errors=errors, historical_semantic_inputs=[])
        plan['local_errors'] = plan_errors(plan)
        return plan

    def review(self, plan):
        from final_quality_gate import semantic_review_variants
        variants = semantic_review_variants(plan)
        task = dict(role='final_review', review_scope='semantic_text_and_source_context',
            product_title=plan['product']['title'], variants=variants,
            local_errors=[x for x in plan['local_errors'] if x != 'AUDIO_BOUNDARY_UNVERIFIED'], alignment_errors=plan['alignment_errors'],
            instructions=SELECTION_RULE+'独立检查实际首尾与所有接缝，专查上句残尾、吞掉首字、'
            '依赖前文、短时重复、因果列举闭合、跨版重复、禁止内容。词时戳及音量不是人耳证明。'
            '本次只审核语义与原源上下文依赖，声学风险和人物画面由独立本机步骤处理；不要因未提供视频或真人试听而拒绝语义充分的句子。'
            'checks的opening/ending/seams仅判断语义。duplicates检查跨版本高度重复及同版重复整段信息，短词口误或少量自然重复不是硬失败；只有能安全整句提炼且明显冗余的信息才要求修复。issues只列语义阻断问题。'
            '半帧量化误差不是语义缺陷，不擅自认定吞字；明确区分真实内容问题与声学待查。每条严格45–60秒。selected_ranges为空；具体指出variant和unit问题。')
        task['task_digest'] = digest(task)
        receipt = launch(task, timeout=300)
        if receipt['result']['task_digest'] != task['task_digest']: raise ValueError('review_digest_mismatch')
        atomic_json(self.folder/'final_review_receipt.json', dict(task=task, **receipt))
        return dict(plan_digest=digest(plan), **receipt['result'])

    def run_trial(self):
        start = time.time(); self.event('trial', 'START')
        try:
            def read_inputs():
                for item in self.run['inputs']:
                    if _sha256(Path(item['path'])) != item['sha256']: raise ValueError('trial_inputs_changed')
                index = json.loads((self.paths.cache_dir/'subtitle_index.json').read_text())
                if index['run_id'] != self.run['run_id'] or index['input_digest'] != self.run['input_content_digest']:
                    raise ValueError('index_not_from_this_fresh_run')
                return dict(index=index, titles=json.loads((self.paths.cache_dir/'titles.json').read_text()),
                    locator=json.loads((self.paths.cache_dir/'fresh_locator.json').read_text()))
            data = self.stage('inputs', {}, read_inputs)
            product = next(p for p in data['titles']['products'] if p['product_index'] == self.product)
            locator = next(p for p in data['locator']['all_products'] if p['product_index'] == self.product)
            selection = self.stage('selection', digest([data, product]), lambda: self.select(data['index'], product, locator))
            asr = self.stage('asr', digest(selection), lambda: self.asr(selection, data['index']))
            plan = self.stage('plan', digest([selection, asr]), lambda: self.build(selection, asr, data['index']))
            if plan['local_errors'] or plan['alignment_errors']:
                from sentence_trial_repair import run as repair_edges
                repaired = self.stage('frame_safe_repair', digest([plan, asr, selection]),
                    lambda: repair_edges(self, self.stage_paths['asr'], self.stage_paths['selection']))
                atomic_json(self.folder/'RESULT.json', dict(repaired, trial_seconds=time.time()-start,
                    automatic_repair_count=1, code_development_seconds=None))
            else:
                review = self.stage('review', digest(plan), lambda: self.review(plan))
                status = 'SEMANTIC_PASS' if (len(plan['variants']) == 3 and review['verdict'] == 'PASS' and not review['issues']
                    and all(review['checks'].values())) else 'REPAIRABLE'
                atomic_json(self.folder/'RESULT.json', dict(status=status, plan_digest=digest(plan), review=review,
                    durations=[v['duration_seconds'] for v in plan['variants']],
                    person_audit='NOT_RUN', resolve_write=False, trial_seconds=time.time()-start,
                    delivery_status='NOT_FINAL', code_development_seconds=None))
        finally:
            self.event('trial', 'END', seconds=time.time()-start)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='.')
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--product', required=True, type=int)
    args = parser.parse_args()
    trial = Trial(args.root, args.workspace, args.product)
    from codex_worker import key
    key(trial.paths)  # Initialize before taking the long-lived workspace lease.
    with workspace_lock(trial.paths): trial.run_trial()


if __name__ == '__main__':
    main()
