"""Run-scoped cold-start adapter; uses existing paths, publication and transport.

Never imports historical ranges/semantic results or creates local edited video.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import time
import uuid
from pathlib import Path

from index_subtitles import expand, parse_srt, media_duration
from index_titles import parse_workbook, workbook_guards, build_model
from project_registry import atomic_json, digest, folder_identity, workspace_lock
from runtime_paths import resolve_runtime_paths
from runtime_meta import _sha256
from semantic_evidence import FORBIDDEN, clean_cue, semantic_windows
from workbook_runtime import preferred_node
from mimo_responses import create_response, ResponseAPIError, MODEL
from product_semantic_preview import parse_response


VERSION = 'cold-continuous-v2-preclean'
MAX_A_REQUESTS = 16
MAX_A_SECONDS = 1800


def load_run(repo, workspace):
    paths = resolve_runtime_paths(repo, workspace)
    state = json.loads(paths.state_path.read_text())
    run = json.loads((paths.workspace_root / 'RUN.json').read_text())
    if state['batch']['batch_id'] != run['run_id'] or run['pipeline'] != VERSION:
        raise ValueError('cold_run_identity_mismatch')
    return paths, state, run


def init(repo, project_id):
    root = Path(repo).resolve()
    parent = json.loads((root / 'PROJECT_STATE.json').read_text())
    # Deliberate allowlist: no historical runtime/batch/results enter the run.
    inputs = {k: parent[k] for k in ('title_path', 'subtitle_paths', 'video_paths', 'resolve')}
    run_id = time.strftime('%Y%m%d') + '_' + uuid.uuid4().hex[:10]
    paths = resolve_runtime_paths(root, root / '.cache/workspaces' / ('coldstart_' + run_id))
    paths.workspace_root.mkdir(parents=True, exist_ok=False)
    status = subprocess.check_output(['git', 'status', '--porcelain=v1', '-uall'], cwd=root, text=True)
    protected = {line[3:]: _sha256(root / line[3:]) for line in status.splitlines()
                 if (root / line[3:]).is_file()}
    atomic_json(paths.workspace_root / 'GIT_BASELINE.json', {'status': status, 'files': protected})
    (paths.workspace_root / 'LATEST_CODEX_REPORT.before.md').write_bytes((root / 'tmp/review_reports/LATEST_CODEX_REPORT.md').read_bytes())
    project_key = str(uuid.uuid5(uuid.NAMESPACE_URL, project_id + str(Path(inputs['title_path']).parent)))
    config = {'schema_version': 2, 'project_key': project_key, 'resolve_project_id': project_id,
              'material_folder': folder_identity(Path(inputs['title_path']).parent), 'inputs': inputs}
    state = {'schema_version': 1, **inputs, 'project_root': str(root),
             'project_identity': {'contract': 2, 'project_key': project_key,
                                  'resolve_project_id': project_id, 'config_digest': digest(config)},
             'batch': {'batch_id': run_id, 'product_ids': [], 'completed_product_ids': [], 'failed_product_ids': []},
             'batch_overrides': {'skip_blank_titles': True, 'preserve_blank_row_indices': True},
             'runtime': {'pipeline_version': VERSION, 'rule_version': VERSION, 'resolve_write_enabled': False}}
    atomic_json(paths.workspace_root / 'PROJECT_CONFIG.json', config)
    atomic_json(paths.state_path, state)
    manifest = []
    for path in [Path(inputs['title_path']), *expand(inputs['subtitle_paths'], '.srt'), *expand(inputs['video_paths'], '.mp4')]:
        before = path.stat()
        fingerprint = _sha256(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('source_changed_while_hashing')
        manifest.append({'path': str(path), 'sha256': fingerprint, 'bytes': before.st_size, 'mtime_ns': before.st_mtime_ns})
    run = {'run_id': run_id, 'project_key': project_key, 'pipeline': VERSION,
           'input_content_digest': digest(manifest), 'inputs': manifest, 'historical_semantic_inputs': [],
           'budgets': {'max_A_requests': MAX_A_REQUESTS, 'max_A_seconds': MAX_A_SECONDS,
                       'A_output_tokens': 12000, 'B_output_tokens': 8000, 'request_timeout': 240},
           'status': 'INPUTS_FINGERPRINTED'}
    atomic_json(paths.workspace_root / 'RUN.json', run)
    print(str(paths.workspace_root), flush=True)
    return paths


def index_and_locate(paths):
    start = time.perf_counter()
    state = json.loads(paths.state_path.read_text()); run = json.loads((paths.workspace_root / 'RUN.json').read_text())
    parsed = parse_workbook(Path(state['title_path']), paths.repo_root, preferred_node())
    titles = build_model(Path(state['title_path']), parsed, workbook_guards(Path(state['title_path']), parsed['sheet']),
                         skip_blank_titles=True, preserve_blank_row_indices=True)
    sources, cues, offset = [], [], 0.0
    videos = {p.stem: p for p in expand(state['video_paths'], '.mp4')}
    for subtitle in expand(state['subtitle_paths'], '.srt'):
        media = videos[subtitle.stem]; duration = media_duration(media)
        rows = parse_srt(subtitle, offset)
        # Restore original SRT numbers without semantic filtering or keyword deletion.
        blocks = re.split(r'\n\s*\n', subtitle.read_text(encoding='utf-8-sig').replace('\r\n', '\n').strip())
        raw_ids = [b.splitlines()[0].strip() for b in blocks if len(b.splitlines()) >= 3 and '-->' in b.splitlines()[1]]
        if len(rows) != len(raw_ids):
            raise ValueError('source_subtitle_id_recovery_mismatch')
        for i, row in enumerate(rows):
            # MiMo sees this anonymous cue ordinal, never a project/run identifier.
            row.update({'original_subtitle_id': raw_ids[i], 'source_id': f'c{len(cues)+i+1:07d}', 'ordinal': i})
        if len({r['source_id'] for r in rows}) != len(rows):
            raise ValueError('duplicate_original_subtitle_ids')
        sources.append({'source_stem': subtitle.stem, 'video_path': str(media), 'duration': duration,
                        'global_start': offset, 'global_end': offset + duration})
        cues.extend(rows); offset += duration
    index = {'run_id': run['run_id'], 'input_digest': run['input_content_digest'], 'sources': sources, 'cues': cues}
    atomic_json(paths.cache_dir / 'titles.json', titles)
    atomic_json(paths.cache_dir / 'subtitle_index.json', index)
    rough = semantic_windows(index, titles['products'], before=35, after=65, cluster_gap=60)
    scored = []
    for product in rough['products']:
        windows = product['windows']; seconds = sum(w['global_end']-w['global_start'] for w in windows)
        terms = set(t for w in windows for t in w['distinctive_terms'])
        matched = sum(w['matched_cue_count'] for w in windows)
        if 90 <= seconds <= MAX_A_SECONDS and 1 <= len(windows) <= MAX_A_REQUESTS and len(terms) >= 2:
            score = matched * (1-max((w['conflict_ratio'] for w in windows), default=1)) * min(4, len(terms))
            if score > 0:
                scored.append({'product_index': product['product_index'], 'title': product['title'], 'score': score,
                               'seconds': seconds, 'matched_cues': matched, 'terms': sorted(terms), 'windows': windows})
    scored.sort(key=lambda r: (-r['score'], r['seconds'], r['product_index']))
    atomic_json(paths.cache_dir / 'fresh_locator.json', {'all_products': rough['products'], 'shortlist': scored,
                                                        'elapsed_seconds': time.perf_counter()-start})
    print(json.dumps([dict(r, windows=[{k:w[k] for k in ('source_stem','global_start','global_end','conflict_ratio')} for w in r['windows']]) for r in scored[:6]], ensure_ascii=False), flush=True)


def paragraph_chunks(rows, target=130, maximum=210, overlap=8):
    """Use pauses/topic starts; never terminate at a fixed subtitle count."""
    chunks = []; begin = 0
    while begin < len(rows):
        if rows[-1]['end']-rows[begin]['start'] <= maximum:
            chunks.append(rows[begin:]); break
        choices = []
        for i in range(begin+1, len(rows)):
            elapsed = rows[i-1]['end']-rows[begin]['start']
            if elapsed < 70: continue
            if elapsed > maximum: break
            gap = rows[i]['start']-rows[i-1]['end']
            text = rows[i-1]['text']; next_text = rows[i]['text']
            if gap >= .55 or text.endswith(('。','！','？','啊','的','好','OK')):
                if text.endswith(('因为','所以','但是','如果','这个','我们','它的')): continue
                if next_text.startswith(('所以','因为','但是','而且')) and gap < 1.2: continue
                choices.append((abs(elapsed-target)-min(gap,3)*8, i))
        if not choices:
            raise ValueError('no_natural_paragraph_boundary_within_budget')
        stop = min(choices)[1]; chunks.append(rows[begin:stop])
        next_begin = stop
        while next_begin > begin and rows[stop]['start']-rows[next_begin-1]['start'] < overlap:
            next_begin -= 1
        begin = max(begin+1, next_begin)
    return chunks


def select(paths, product_id):
    state = json.loads(paths.state_path.read_text()); run = json.loads((paths.workspace_root/'RUN.json').read_text())
    data = json.loads((paths.cache_dir/'fresh_locator.json').read_text())
    chosen = next(r for r in data['all_products'] if r['product_index']==product_id)
    titles=json.loads((paths.cache_dir/'titles.json').read_text()); product=next(p for p in titles['products'] if p['product_index']==product_id)
    index=json.loads((paths.cache_dir/'subtitle_index.json').read_text()); sources={s['source_stem']:s for s in index['sources']}
    paragraphs=[];coverage=[]
    for w in chosen['windows']:
        source=sources[w['source_stem']]; lo=w['global_start']-source['global_start'];hi=w['global_end']-source['global_start']
        rows=[r for r in index['cues'] if r['source_stem']==w['source_stem'] and r['end']>lo and r['start']<hi]
        for chunk in paragraph_chunks(rows):
            paragraphs.append({'paragraph_id':f"{run['run_id']}:p{len(paragraphs)+1:02d}", 'source_alias':f"src{list(sources).index(w['source_stem'])+1:02d}",
                               'source_stem':w['source_stem'], 'rows':chunk})
        coverage.append({'source_stem':w['source_stem'],'start':rows[0]['start'],'end':rows[-1]['end'],'cue_count':len(rows)})
    if len(paragraphs)>MAX_A_REQUESTS:raise ValueError('all_candidate_coverage_exceeds_A_budget')
    locator_bytes=(paths.cache_dir/'fresh_locator.json').read_bytes()
    state['batch']['product_ids']=[product_id];state['runtime']['selected_product']=product
    atomic_json(paths.state_path,state)
    # Selection changes the existing batch scope. Carry only freshly built inputs.
    atomic_json(paths.cache_dir/'titles.json',titles)
    atomic_json(paths.cache_dir/'subtitle_index.json',index)
    (paths.cache_dir/'fresh_locator.json').write_bytes(locator_bytes)
    unique_rows={r['source_id']:r for paragraph in paragraphs for r in paragraph['rows']}
    cleaning={uid:clean_cue(row,uid) for uid,row in unique_rows.items()}
    fully_removed=sum(not kept for kept,_ in cleaning.values())
    partial_review=sum(bool(kept) and ''.join(part['text'] for part in kept)!=unique_rows[uid]['text']
                       for uid,(kept,_) in cleaning.items())
    atomic_json(paths.cache_dir/'candidate_paragraphs.json',{'project_key':run['project_key'],'run_id':run['run_id'],'product':product,
        'paragraphs':paragraphs,'coverage':coverage,'all_fresh_locator_windows_included':True,
        'presemantic_removed_cues':fully_removed,'mixed_cues_needing_local_cutpoint_review':partial_review,
        'outbound_policy':VERSION})
    print(json.dumps({'product':product,'paragraph_count':len(paragraphs),'coverage':coverage},ensure_ascii=False))


def stage_a_payload(paragraph, product, run):
    if FORBIDDEN.search(product['title']):
        raise ValueError('stage_A_product_title_needs_local_cleaning')
    cues=[]
    for row in paragraph['rows']:
        kept,_=clean_cue(row,row['source_id'])
        cleaned=''.join(part['text'] for part in kept)
        if not cleaned:
            continue
        if FORBIDDEN.search(cleaned):
            raise ValueError('stage_A_outbound_banned_content')
        cues.append({'id':row['source_id'],'source':paragraph['source_alias'],
                     'start':row['start'],'end':row['end'],'text':cleaned,
                     'local_cutpoint_review_required':cleaned!=row['text']})
    if not cues:
        return None
    document={'product_title':product['title'],'paragraph_id':paragraph['paragraph_id'].split(':')[-1],
              'cues':cues,
              'output_schema':{'product_identity':{'match':'MATCH|UNCERTAIN|OTHER_PRODUCT','evidence':[]},
                  'units':[{'unit_id':'u01','source_ids':['contiguous source subtitle IDs, every ID between endpoints'],
                            'text':'exact concatenation in original order','product_attribution':'MATCH|UNCERTAIN|OTHER_PRODUCT',
                            'category':'','claim':'','complete_semantics':'','needs_context':[], 'visual_dependency':False,
                            'repetition_or_boundary_risks':[],'continuous_clip_recommended':True}], 'excluded':[{'source_ids':[],'reason':''}]}}
    instructions=('只输出JSON。先理解整段与商品归属，再提取真实连续完整原声句群。商品编号和链接数字均不决定归属。'
        '先理解价格/库存/链接/演示等混合语境，再区分卖点、背景、画面依赖、促销流程、他品与重复。'
        '只定位来源，不润色。每个unit必须来自同一source连续字幕，source_ids严格按原顺序且包含端点间全部字幕。'
        '标为local_cutpoint_review_required的字幕只供理解，不能进入自动可用unit；其有效子句保留在本地待物理切点复核。'
        '不得抽取词语/形容词/短语或半句话跨位置拼成新句。保留完整因果前提和结果；问题及依赖回答需一起保留。'
        '尽量提取连续几句话、语义完整且有自然音频边界的单元；必要时长段保留供LOCAL_SPLIT_REVIEW，不机械碎切。'
        '邻段重叠只作理解，来源仍用原ID。不要为45秒限制截断理解；不组装版本。不确定归属明确UNCERTAIN。')
    return {'model':MODEL,'input':instructions+json.dumps(document,ensure_ascii=False,separators=(',',':')),
            'reasoning':{'effort':'low'},'max_output_tokens':12000,'stream':True}


def request_once(paths, stage, payload, ordinal):
    run=json.loads((paths.workspace_root/'RUN.json').read_text())
    encoded=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode(); fingerprint=hashlib.sha256(encoded).hexdigest()
    folder=paths.cache_dir/'mimo'/f'{stage}_{ordinal:02d}_{fingerprint[:16]}';folder.mkdir(parents=True,exist_ok=True)
    meta_file=folder/'meta.json';response_file=folder/'response.json'
    if meta_file.exists():
        meta=json.loads(meta_file.read_text())
        if meta.get('status')=='completed' and meta.get('terminal_event_type')=='response.completed' and meta.get('request_sha256')==fingerprint:
            if meta.get('response_sha256')!=_sha256(response_file):raise ValueError('cached_response_digest_mismatch')
            raw=json.loads(response_file.read_text())
            if raw.get('model')!=MODEL or raw.get('status')!='completed':raise ValueError('cached_terminal_response_invalid')
            return parse_response(raw)
        raise ValueError('attempted_or_uncertain_request_no_resend')
    atomic_json(folder/'dry_run.json',{'run_id':run['run_id'],'stage':stage,'request_sha256':fingerprint,'bytes':len(encoded),
                                      'input_characters':len(payload['input']),'max_output_tokens':payload['max_output_tokens'],'deadline':240})
    atomic_json(folder/'request.json',payload)
    meta={'run_id':run['run_id'],'stage':stage,'request_sha256':fingerprint,'status':'in_progress'}
    atomic_json(meta_file,meta)
    try:
        raw,metrics=create_response(payload,240,stage=stage)
        parsed=parse_response(raw);atomic_json(response_file,raw)
        meta.update(metrics,status='completed',response_sha256=_sha256(response_file));atomic_json(meta_file,meta)
        return parsed
    except BaseException as exc:
        meta.update(getattr(exc,'metrics',{}))
        status='incomplete' if getattr(exc,'code',None)=='response_incomplete' else 'uncertain'
        meta.update(status=status,error_code=getattr(exc,'code',type(exc).__name__))
        atomic_json(meta_file,meta);raise


def validate_unit(unit, paragraph, run, number):
    by_id={r['source_id']:r for r in paragraph['rows']};ids=unit.get('source_ids') or []
    if not ids or len(ids)!=len(set(ids)) or any(i not in by_id for i in ids):raise ValueError('unknown_or_reused_source_id')
    rows=[by_id[i] for i in ids]
    if len({r['source_stem'] for r in rows})!=1:raise ValueError('multi_source_fake_continuity')
    ordinals=[r['ordinal'] for r in rows]
    if ordinals!=list(range(ordinals[0],ordinals[-1]+1)):raise ValueError('phrase_splice_or_reordered_source')
    if ''.join(r['text'] for r in rows)!=unit.get('text'):raise ValueError('source_text_not_exact')
    for row in rows:
        kept,_=clean_cue(row,row['source_id'])
        if ''.join(part['text'] for part in kept)!=row['text'] or FORBIDDEN.search(row['text']):
            raise ValueError('cue_requires_local_cutpoint_review')
    if unit.get('product_attribution')!='MATCH':raise ValueError('product_not_confirmed')
    if unit.get('continuous_clip_recommended') is not True:raise ValueError('continuous_unit_not_recommended')
    if not str(unit.get('complete_semantics') or '').strip():raise ValueError('complete_semantics_missing')
    return {**unit,'unit_id':run['project_key'][:8]+':'+run['run_id']+f':u{number:03d}',
            'source_stem':rows[0]['source_stem'],'source_alias':paragraph['source_alias'],
            'start':rows[0]['start'],'end':rows[-1]['end'],'duration':rows[-1]['end']-rows[0]['start'],
            'rows':rows,'paragraph_id':paragraph['paragraph_id'],'local_status':'PENDING_LOCAL_SEMANTIC_REVIEW'}


def frame_append_spec(start, end, fps, record):
    if not (math.isfinite(fps) and fps>0 and 0<=start<end):raise ValueError('invalid_source_clock')
    left=math.floor(start*fps+1e-8);right=math.ceil(end*fps-1e-8)
    return {'source_start':left,'source_end_exclusive':right,'api_end_inclusive':right-1,
            'record_start':record,'record_end_exclusive':record+right-left}


def validate_selection(response, units, run):
    by_id={g['unit_id']:g for g in units};variants=[];used=[]
    selected=response.get('selected_variants') or []
    if len(selected)>3:raise ValueError('too_many_variants')
    for n,variant in enumerate(selected,1):
        ids=variant.get('unit_ids') or []
        if not ids or len(ids)!=len(set(ids)) or any(i not in by_id for i in ids):raise ValueError('B_unknown_or_unverified_unit')
        rows=[by_id[i] for i in ids]
        for row in rows:
            if row.get('local_status')!='KEEP':raise ValueError('B_unit_without_local_KEEP')
            if any(row['source_stem']==old['source_stem'] and min(row['end'],old['end'])>max(row['start'],old['start']) for old in used):
                raise ValueError('B_reused_source_interval')
            used.append(row)
        duration=sum(g['duration'] for g in rows)
        if not 45<duration<60:raise ValueError('B_subtitle_feasibility_outside_duration')
        variants.append({'variant_id':f'v{n}','plan_id':run['project_key'][:8]+':'+run['run_id']+f':v{n}',
                         'focus':variant.get('focus'),'units':rows,'duration_estimate':duration,'status':'SEMANTIC_REVIEW_PENDING'})
    if len(selected)<3 and not str(response.get('insufficient_reason') or '').strip():
        raise ValueError('B_missing_reason_for_underfilled_three_variant_goal')
    return variants


def stage_b_payload(product, units, run, proposal=None):
    aliases={g['unit_id']:f'u{i:03d}' for i,g in enumerate(units,1)}
    for g in units:
        if FORBIDDEN.search(g['text']):raise ValueError('stage_B_outbound_banned_content')
    def public_proposal(rows):
        result=[]
        for variant in rows or []:
            public={k:v for k,v in variant.items() if k in ('focus','variant_id','estimated_duration','estimate_seconds')}
            if any(uid not in aliases for uid in variant.get('unit_ids',[])):
                raise ValueError('stage_B_proposal_unknown_unit')
            public['unit_ids']=[aliases[uid] for uid in variant.get('unit_ids',[])]
            result.append(public)
        return result
    body={'product_title':product['title'],
          'target_variant_count':3,
          'global_nonoverlap_starting_point': public_proposal(proposal),
          'units':[{'unit_id':aliases[g['unit_id']],'text':g['text'],'source':g['source_alias'],'start':g['start'],'end':g['end'],
                    'category':g['category'] if not FORBIDDEN.search(str(g['category'])) else '',
                    'claim':g['claim'] if not FORBIDDEN.search(str(g['claim'])) else '',
                    'needs_context':g['needs_context'] if not FORBIDDEN.search(json.dumps(g['needs_context'],ensure_ascii=False)) else [],
                    'visual_dependency':g.get('visual_dependency'),'local_review':'KEEP'} for g in units],
          'output_schema':{'selected_variants':[{'unit_ids':[],'focus':''}],'insufficient_reason':''}}
    if FORBIDDEN.search(json.dumps(body,ensure_ascii=False)):
        raise ValueError('stage_B_outbound_metadata_needs_local_cleaning')
    instructions=('只输出精简JSON，不输出分析。审核给定的全局三版无重叠起点，并参考完整候选池；不得找到第一条合格方案就停止。'
                  '可以调整单元顺序/分配或删减至2、1、0版；先保证每版45–60秒、完整自然、不同卖点、零源时间重叠。'
                  '只引用完整unit_id，不改写/拆词/补话。不要用促销或弱话术凑时长。'
                  '若少于3版，在insufficient_reason写明逐版具体原因；不可将不合格候选输出为合格。')
    return {'model':MODEL,'input':instructions+json.dumps(body,ensure_ascii=False,separators=(',',':')),
            'reasoning':{'effort':'low'},'max_output_tokens':6000,'stream':True}


def local_clean_source_runs(unit):
    runs=[];current=[]
    for row in unit.get('rows',[]):
        cleaned=''.join(part['text'] for part in clean_cue(row,row['source_id'])[0])
        if cleaned==row['text'] and not FORBIDDEN.search(row['text']):
            current.append(row)
        elif current:
            runs.append(current);current=[]
    if current:runs.append(current)
    return [{'source_ids':[r['source_id'] for r in rows], 'source_stem':rows[0]['source_stem'],
             'start':rows[0]['start'],'end':rows[-1]['end'],
             'text':''.join(r['text'] for r in rows),
             'status':'LOCAL_SEMANTIC_AND_CUTPOINT_REVIEW_REQUIRED'} for rows in runs]


def eligible_for_stage_b(units):
    kept=[];withheld=[]
    for g in units:
        if FORBIDDEN.search(g['text']):
            withheld.append({'unit_id':g['unit_id'],'reason':'raw_unit_contains_forbidden_content',
                             'original_source_ids':g['source_ids'],'original_text':g['text'],
                             'original_claim':g.get('claim'),'original_product_attribution':g.get('product_attribution'),
                             'clean_full_cue_runs':local_clean_source_runs(g),
                             'edit_status':'PENDING_LOCAL_SEMANTIC_AND_AUDIO_REVIEW'})
        else:kept.append(g)
    return kept,withheld


def require_complete_stage_b_pool(withheld):
    if any(row['clean_full_cue_runs'] for row in withheld):
        raise ValueError('stage_B_mixed_unit_local_review_required_before_global_allocation')


def restore_b_aliases(response, units):
    aliases={f'u{i:03d}':g['unit_id'] for i,g in enumerate(units,1)}
    restored=json.loads(json.dumps(response))
    for variant in restored.get('selected_variants',[]):
        variant['unit_ids']=[aliases.get(uid,uid) for uid in variant.get('unit_ids',[])]
    return restored


def require_product_match(result):
    if (result.get('product_identity') or {}).get('match')!='MATCH':
        raise ValueError('stage_A_product_identity_not_MATCH')


def main():
    p=argparse.ArgumentParser();p.add_argument('operation',choices=['init','index','select','A','B']);p.add_argument('--root',default='.');p.add_argument('--workspace');p.add_argument('--project-id');p.add_argument('--product',type=int)
    args=p.parse_args()
    if args.operation=='init':init(args.root,args.project_id);return
    paths,state,run=load_run(args.root,args.workspace)
    if args.operation=='index':index_and_locate(paths)
    elif args.operation=='select':select(paths,args.product)
    elif args.operation=='A':
        document=json.loads((paths.cache_dir/'candidate_paragraphs.json').read_text());accepted=[];rejected=[];seen=set()
        for n,paragraph in enumerate(document['paragraphs'],1):
            payload=stage_a_payload(paragraph,document['product'],run)
            result=request_once(paths,'A',payload,n) if payload else {'product_identity':{'match':'NO_CLEAN_CUES'},'units':[]}
            atomic_json(paths.cache_dir/f'A_paragraph_{n:02d}.json',result)
            if payload:require_product_match(result)
            for unit in result.get('units',[]):
                try:
                    row=validate_unit(unit,paragraph,run,len(accepted)+1);key=tuple(row['source_ids'])
                    if key in seen:continue
                    # Overlap-context units may not silently overlap previously retained units.
                    if any(row['source_stem']==g['source_stem'] and min(row['end'],g['end'])>max(row['start'],g['start']) for g in accepted):
                        raise ValueError('overlap_context_unit_needs_local_review')
                    seen.add(key);accepted.append(row)
                except ValueError as exc:rejected.append({'paragraph':paragraph['paragraph_id'],'unit':unit,'reason':str(exc)})
            atomic_json(paths.cache_dir/'stage_A_source_verified.json',{'run_id':run['run_id'],'units':accepted,'source_rejected':rejected,
                    'paragraphs_completed':n,'paragraphs_total':len(document['paragraphs']),'all_coverage_complete':n==len(document['paragraphs'])})
            print('A paragraph',n,'completed; verified units',len(accepted),'source rejects',len(rejected),flush=True)
    elif args.operation=='B':
        document=json.loads((paths.cache_dir/'candidate_paragraphs.json').read_text())
        local=json.loads((paths.cache_dir/'local_A_review.json').read_text())
        if local['run_id']!=run['run_id']:raise ValueError('local_A_wrong_run')
        eligible,withheld=eligible_for_stage_b(local['kept_units'])
        atomic_json(paths.cache_dir/'stage_B_outbound_withheld_local_review.json',{'run_id':run['run_id'],
                    'withheld_from_external_request':withheld,'editing_candidates_retained_locally':True})
        require_complete_stage_b_pool(withheld)
        if not eligible:raise ValueError('stage_B_no_precleaned_units')
        proposal_file=paths.cache_dir/'stage_B_candidate_allocation.json'
        proposal=json.loads(proposal_file.read_text())['variants'] if proposal_file.exists() and not withheld else None
        response=restore_b_aliases(request_once(paths,'B',stage_b_payload(document['product'],eligible,run,proposal),1),eligible)
        atomic_json(paths.cache_dir/'stage_B_selection.json',response)
        variants=validate_selection(response,eligible,run)
        atomic_json(paths.cache_dir/'stage_B_restored.json',{'run_id':run['run_id'],'product':document['product'],'variants':variants})
        print('B complete; variants',len(variants),flush=True)


if __name__=='__main__':main()
