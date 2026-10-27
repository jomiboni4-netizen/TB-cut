"""Fixed parameter-only delivery entry. Expensive checks are dependency-scoped.

Normal execution never generates/edits Python. It stops at unresolved evidence,
requires exact external-text authorization, and writes only an explicitly allowed
target timeline. Legacy workspaces remain untouched and are not re-attested.
"""
import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from coldstart_semantics import load_run
from delivery_runtime import Ledger, Store, require
from delivery_assembly import inputs, assemble, validate_ranges, prepare_asr, checked_file
from final_quality_gate import plan_errors, semantic_review_variants
from project_registry import atomic_json, digest
from runtime_meta import _sha256

PRODUCERS = {
    'assembly': ['delivery_editorial.py','delivery_fps.py','delivery_assembly.py','sentence_boundaries.py','refine_plans_v2.py'],
    'audio': ['owned_audio_refinement.py','audio_boundary_evidence.py','refine_plans_v2.py','sentence_boundaries.py','final_quality_gate.py'],
    'semantic': ['delivery_pipeline.py','delivery_editorial.py','codex_worker.py','final_quality_gate.py','sentence_boundaries.py'],
    'vision': ['adaptive_vision.py','vision_record_score_v5.py','apple_frame_probe.swift','vision_contact_sheet.swift'],
}
CHECKS = {'opening','ending','causal_logic','seams','selling_value','duplicates'}


def semantic_task(plan):
    from sentence_boundaries import SELECTION_RULE
    task = dict(role='final_review',review_scope='semantic_text_and_source_context',
        product_title=plan['product']['title'],plan_digest=digest(plan),rules_fingerprint=plan.get('rules_fingerprint'),
        review_producer_digest=digest({name:_sha256(Path(__file__).with_name(name)) for name in PRODUCERS['semantic']}),
        variants=semantic_review_variants(plan),
        semantic_basis={'selling_points':plan.get('semantic_map',[]),
                        'digest':plan.get('semantic_map_digest'),
                        'instruction':'核对成片表达是否有对应原文证据，原意是否完整；发现无依据卖点或上下文依赖时列为问题。'},
        instructions=SELECTION_RULE+'独立检查每条完整开头、结尾及所有接缝，原源上下文依赖、因果条件转折列举指代承诺闭合、明显卡壳/假起句/词音节重复/无效填充、禁止内容、卖点与跨版重复。'
        '只做语义审核；声音、人物和帧定位由本机检查，不要求真人试听或音素精度。承接词和半帧估计不自动失败。'
        '每条严格大于45秒、小于60秒，不以填充或高度重复凑三条。checks六项必须分别判断；issues列具体阻断问题；selected_ranges为空。')
    task['task_digest'] = digest(task)
    return task


def verify_semantic(paths, plan, body):
    from codex_worker import verify_receipt
    task = semantic_task(plan)
    require(body['plan_digest'] == digest(plan) and body['task'] == task, 'semantic_plan_or_task_changed')
    result = verify_receipt(paths,body['receipt_ref'],task)['result']
    require(result['verdict'] == 'PASS' and set(result['checks']) == CHECKS and all(v is True for v in result['checks'].values())
            and not result['issues'] and not result['selected_ranges'], 'semantic_review_not_passed')


class Pipeline:
    def __init__(self, root, workspace, recipe_path):
        self.paths,self.state,self.run = load_run(root,workspace)
        self.folder = self.paths.workspace_root/'delivery'; self.folder.mkdir(exist_ok=True)
        self.ledger = Ledger(self.folder)
        self.recipe_path = self.paths.runtime_argument(recipe_path)
        self.recipe = json.loads(self.recipe_path.read_text())
        self.recipe_sha = _sha256(self.recipe_path)
        if 'frame_rates' not in self.recipe and self.state.get('runtime',{}).get('frame_rate_registration'):
            self.recipe['frame_rates']=self.state['runtime']['frame_rate_registration']
        self.index,self.product,self.ranges = inputs(self.paths,self.state,self.run,self.recipe)
        self.sources = {Path(x).name:x for x in self.state['video_paths']}
        require(len(self.sources) == len(self.state['video_paths']), 'ambiguous_source_basename')
        self.rules = _sha256(self.paths.repo_root/'PROJECT_RULES.md')
        self.vision_rules_path = self.paths.repo_root/'config'/'vision_rules.json'
        self.vision_rules_sha = _sha256(self.vision_rules_path)
        self.store = Store(self.paths,dict(project=self.run['project_key'],run=self.run['run_id'],
            inputs=self.run['input_content_digest'],product=self.recipe['product_id'],encoding=self.recipe['encoding_id']),self.ledger)
        self.code = {n:_sha256(self.paths.repo_root/'scripts'/n) for n in
                     set(sum(PRODUCERS.values(),[])+['delivery_runtime.py','delivery_resolve.py'])}
        self._prepared = None
        atomic_json(self.folder/'PIPELINE_MANIFEST.json',dict(contract='fixed-delivery-v3-two-stage-person-audit-disfluency',
            producer_snapshot=self.code,stage_producers=PRODUCERS,recipe_sha256=self.recipe_sha,
            frozen_cutpoint_contract='owned-gap-local-audio-v3-clean-pause-else-owned-edge',rules_sha256=self.rules,
            vision_rules_sha256=self.vision_rules_sha,
            normal_execution='parameters_only_no_generated_code',legacy_artifacts='read_only_not_automatically_attested',
            older_automatic_plans='stale_for_new_execution_requires_source_bound_editorial_and_rate_registration'))

    def unchanged(self):
        require(_sha256(self.recipe_path) == self.recipe_sha and _sha256(self.paths.repo_root/'PROJECT_RULES.md') == self.rules,
                'recipe_or_rules_changed_during_execution')
        require(_sha256(self.vision_rules_path) == self.vision_rules_sha,
                'vision_rules_changed_during_execution')
        require(all(_sha256(self.paths.repo_root/'scripts'/n) == sha for n,sha in self.code.items()),
                'pipeline_code_changed_during_execution_stop_and_revalidate')
        for key in ('index','titles','range_manifest','frame_rates','assembly_review'):
            if key in self.recipe: checked_file(self.recipe[key],self.paths.workspace_root)
        for ref in self.recipe['asr_records'].values(): checked_file(ref,self.paths.workspace_root)
        review = self.recipe['pool_review']
        checked_file(dict(path=review['reference'],sha256=review['sha256']),self.paths.workspace_root)

    def prepare(self):
        if self._prepared is not None: return self._prepared
        from delivery_editorial import task, verify
        from delivery_fps import load
        # Cheap text review first: never spend audio/clock/vision on an unreviewed combination.
        early = task(self.paths,self.recipe)
        atomic_json(self.folder/'ASSEMBLY_TASK.json',early)
        with self.ledger.span('primary_assembly_review_validation','verification'):
            assembly_review = verify(self.paths,self.recipe,early)
            rates = load(self.paths,self.state,self.run,self.recipe)
        from compute_store import media_signature
        records = {name:checked_file(ref,self.paths.workspace_root)['body'] for name,ref in self.recipe['asr_records'].items()}
        used = {Path(records[c['asr']]['windows'][c['window']]['source_file']).stem
                for v in self.recipe['variants'] for c in v['clips']}
        chosen = [Path(s['video_path']).name for s in self.index['sources'] if s['source_stem'] in used]
        with self.ledger.span('selected_source_identity','verification'):
            signatures = {name:media_signature(self.sources[name]) for name in chosen}
            recorded = {str(Path(row['path']).resolve()):row for row in self.run['inputs']}
            for signature in signatures.values():
                row = recorded.get(signature['path'])
                require(row is not None and (row['bytes'],row['mtime_ns'],row['sha256']) ==
                        (signature['size'],signature['mtime_ns'],signature['sha256']), 'selected_source_changed_since_init')
        raw = self.store.stage('assembly',dict(recipe=self.recipe,media=signatures),PRODUCERS['assembly'],
            lambda: assemble(self.paths,self.state,self.run,self.recipe,self.index,self.product,self.ranges))
        plan = copy.deepcopy(raw); plan['rules_fingerprint'] = self.rules
        plan['semantic_map'] = assembly_review['semantic_map']
        plan['semantic_map_digest'] = digest(plan['semantic_map'])
        audit_rows = []
        from owned_audio_refinement import refine
        for variant in plan['variants']:
            refined_clips = []
            for clip in variant['clips']:
                name = 'audio_'+digest([clip['source_file'],clip['unit_id']])[:16]
                # Reordering or changing another clip does not recompute this edge.
                audio_input = {k:clip[k] for k in ('unit_id','source_file','source_start_frame','source_end_frame',
                    'audio_start_frame','audio_end_frame','fps','duration_sec','record_duration_frames','words',
                    'transcript','sentence_edges','audio_boundary_verified')}
                def compute(c=clip):
                    local = dict(variants=[dict(variant_id='edge',clips=[c])])
                    updated,audit = refine(local,self.sources)
                    return dict(clip=updated['variants'][0]['clips'][0],audit=audit['edges'][0])
                result = self.store.stage(name,dict(clip=audio_input,media=signatures[clip['source_file']]),PRODUCERS['audio'],compute)
                refined = copy.deepcopy(clip)
                for key in ('source_start_frame','source_end_frame','audio_start_frame','audio_end_frame','duration_sec',
                            'record_duration_frames','sentence_edges','audio_boundary_verified','owned_audio_review'):
                    refined[key] = result['clip'][key]
                refined_clips.append(refined)
                audit_rows.append(dict(result['audit'],variant_id=variant['variant_id']))
            variant['clips'] = refined_clips; variant['duration_seconds'] = sum(c['duration_sec'] for c in refined_clips)
        plan['local_errors'] = plan_errors(plan)
        validate_ranges(plan,self.ranges)
        audit = dict(status='PASS' if not plan['local_errors'] else 'REPAIRABLE',plan_digest=digest(plan),edges=audit_rows,
                     local_errors=plan['local_errors'],phoneme_accuracy_certified=False)
        atomic_json(self.folder/'DELIVERY_PLAN.json',plan); atomic_json(self.folder/'AUDIO_REFINEMENT.json',audit)
        task = semantic_task(plan); atomic_json(self.folder/'SEMANTIC_TASK.json',task)
        from compute_store import check_media
        for signature in signatures.values(): check_media(signature)
        self.unchanged(); self._prepared = plan,audit
        return self._prepared

    def semantic(self, plan, approved_task=None):
        from codex_worker import execute, verify_receipt
        task = semantic_task(plan)
        def compute():
            # No implicit reuse of last product's one-shot authorization.
            receipt = self.paths.project_records_dir/'worker_receipts'/(task['task_digest']+'.json')
            if not receipt.exists():
                require(approved_task == task['task_digest'], 'SEMANTIC_AUTHORIZATION_REQUIRED:'+task['task_digest'])
                self.ledger.emit(event='EXTERNAL_TEXT_AUTHORIZED',task_digest=approved_task,
                                 scope='title_selected_text_timecodes_necessary_context_only')
            ref = execute(self.paths,task)
            verify_receipt(self.paths,ref,task)
            return dict(plan_digest=digest(plan),task=task,receipt_ref=ref)
        body = self.store.stage('semantic',dict(task=task,rules=self.rules),PRODUCERS['semantic'],compute,category='waiting')
        verify_semantic(self.paths,plan,body)
        return body

    def vision(self, plan, detector):
        from adaptive_vision import audit, verify, classifier_identity, POLICY
        detector = Path(detector).resolve()
        require(detector.is_file() and detector.stat().st_mtime_ns >= (self.paths.repo_root/'scripts/apple_frame_probe.swift').stat().st_mtime_ns,
                'detector_missing_or_rebuild_required')
        from compute_store import media_signature
        media = {name:media_signature(self.sources[name]) for name in {c['source_file'] for v in plan['variants'] for c in v['clips']}}
        body = self.store.stage('vision',dict(plan=digest(plan),classifier=classifier_identity(detector),
            policy=POLICY,vision_rules_sha256=self.vision_rules_sha,media=media),
            PRODUCERS['vision'],lambda:audit(self.paths,plan,self.sources,detector))
        verify(self.paths,plan,body,require_pass=True)
        return body

    def checks(self, detector, approved_task=None):
        plan,audio = self.prepare()
        require(audio['status'] == 'PASS' and not plan_errors(plan), 'LOCAL_GATE_REPAIRABLE')
        # Both are independent once the exact physical plan is frozen.
        results = {}; errors = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = dict(semantic=pool.submit(self.semantic,plan,approved_task),vision=pool.submit(self.vision,plan,detector))
            for name,future in jobs.items():
                try: results[name] = future.result()
                except Exception as exc: errors[name] = str(exc)
        self.unchanged()
        if errors:
            atomic_json(self.folder/'CHECK_STATUS.json',dict(status='BLOCKED',errors=errors,plan_digest=digest(plan)))
            raise ValueError('delivery_checks_pending:'+','.join(errors))
        result = dict(status='PASS',plan_digest=digest(plan),audio_digest=digest(audio),
                      semantic_digest=digest(results['semantic']),vision_digest=digest(results['vision']))
        atomic_json(self.folder/'CHECK_STATUS.json',result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['project-read','assembly-task','asr','prepare','check','deliver','preflight','write','recover','readback','status','time-start','time-end'])
    parser.add_argument('--root',default='.')
    parser.add_argument('--workspace',required=True)
    parser.add_argument('--recipe',default='delivery/RECIPE.json')
    parser.add_argument('--detector')
    parser.add_argument('--approved-semantic-task',help='Exact task digest, only after human authorizes this external text payload')
    parser.add_argument('--allow-new-timeline',action='store_true',help='Only after user explicitly requested a new timeline')
    parser.add_argument('--allow-test-reuse',action='store_true',help='Only for the user-authorized stable test timeline; backup before clearing')
    parser.add_argument('--name');parser.add_argument('--category',choices=sorted(__import__('delivery_runtime').CATEGORIES))
    parser.add_argument('--token');parser.add_argument('--repair',action='store_true')
    args = parser.parse_args()
    from runtime_paths import resolve_runtime_paths
    paths = resolve_runtime_paths(args.root,args.workspace); ledger = Ledger(paths.workspace_root/'delivery')
    if args.operation in ('status','time-start','time-end'):
        if args.operation == 'time-start':
            require(args.name and args.category,'time_name_and_category_required');print(ledger.begin(args.name,args.category,repair=args.repair))
        elif args.operation == 'time-end': ledger.end(args.token)
        else: print(json.dumps(ledger.summary(),ensure_ascii=False))
        return
    if args.operation in ('project-read','write','recover','readback','preflight','deliver') and not sys.flags.utf8_mode:
        os.execve(sys.executable,[sys.executable,*sys.argv],dict(os.environ,PYTHONUTF8='1'))
    pipeline = Pipeline(args.root,args.workspace,args.recipe)
    try:
        with ledger.span(args.operation,'verification' if args.operation in ('readback','preflight') else 'compute',repair=args.repair):
            if args.operation == 'project-read':
                from delivery_fps import capture
                destination = pipeline.folder/'FRAME_RATES.json'
                if pipeline.recipe.get('frame_rates'):
                    from delivery_fps import load
                    load(paths,pipeline.state,pipeline.run,pipeline.recipe)
                    result=dict(status='PROJECT_RATES_REUSED',reference=pipeline.recipe['frame_rates'])
                else:
                    require(not destination.exists(),'frame_rates_already_read_reuse_reference_or_explicitly_start_new_registration')
                    atomic_json(destination,capture(paths,pipeline.state,pipeline.run))
                    ref=dict(path=str(destination.resolve()),sha256=_sha256(destination))
                    pipeline.state.setdefault('runtime',{})['frame_rate_registration']=ref
                    paths.write_state(pipeline.state)
                    result = dict(status='PROJECT_RATES_REGISTERED',reference=ref)
            elif args.operation == 'assembly-task':
                from delivery_editorial import task
                output=task(paths,pipeline.recipe);atomic_json(pipeline.folder/'ASSEMBLY_TASK.json',output)
                result=dict(status='PRIMARY_EDITORIAL_REVIEW_PENDING',task_digest=output['task_digest'])
            elif args.operation == 'asr':
                from delivery_fps import load
                load(paths,pipeline.state,pipeline.run,pipeline.recipe)
                r = pipeline.recipe
                output = prepare_asr(paths,pipeline.state,pipeline.index,r['asr_jobs'],r.get('asr_model','large-v3-turbo'),ledger,run=pipeline.run)
                destination = pipeline.folder/('asr_'+digest(output)+'.json'); atomic_json(destination,output)
                result = dict(status='ASR_READY',reference=dict(path=paths.reference(destination),sha256=_sha256(destination)))
            elif args.operation == 'prepare':
                plan,audio = pipeline.prepare();result = dict(status='LOCAL_PASS_REVIEW_PENDING' if audio['status']=='PASS' else audio['status'],plan_digest=digest(plan),task_digest=semantic_task(plan)['task_digest'])
            elif args.operation == 'preflight':
                from delivery_resolve import inspect
                result = inspect(paths,pipeline.state,pipeline.recipe.get('delivery_request'))
                result = dict(status='PREFLIGHT_PASS',timeline_count=len(result['protected']))
            else:
                require(args.detector,'detector_required')
                gate = lambda:pipeline.checks(args.detector,args.approved_semantic_task)
                if args.operation == 'check': result = gate()
                else:
                    from delivery_resolve import perform, inspect, read
                    plan,_ = pipeline.prepare(); request = pipeline.recipe['delivery_request']
                    reuse = request['timeline_mode'] == 'reusable_test_timeline'
                    require(args.allow_test_reuse if reuse else args.allow_new_timeline,'explicit_test_reuse_authorization_required' if reuse else 'explicit_new_timeline_authorization_required')
                    require(request['product_id'] == pipeline.recipe['product_id'],'delivery_request_product_mismatch')
                    if args.operation == 'deliver':
                        # New invocations never need a generated writer or recovery script.
                        journal = pipeline.folder/'RESOLVE_TRANSACTION.json'
                        if not journal.exists():
                            gate(); inspect(paths,pipeline.state,request)
                            perform(paths,pipeline.state,pipeline.run,plan,request,gate,operation='write')
                        # A fresh OS process performs the final readback.
                        command = [sys.executable,str(Path(__file__).resolve()),'readback','--root',str(paths.repo_root),
                                   '--workspace',str(paths.workspace_root),'--recipe',str(pipeline.recipe_path),
                                   '--detector',args.detector,'--allow-test-reuse' if reuse else '--allow-new-timeline']
                        child = subprocess.run(command,capture_output=True,text=True)
                        atomic_json(pipeline.folder/'READBACK_PROCESS.json',dict(returncode=child.returncode,
                            stdout=child.stdout[-20000:],stderr=child.stderr[-20000:]))
                        require(child.returncode == 0,'independent_readback_failed_see_transaction')
                        result = read(journal); independent = read(pipeline.folder/'INDEPENDENT_READBACK.json')
                        require(independent['status'] == 'PASS' and independent['plan_digest'] == digest(plan)
                                and independent['actual_digest'] == digest(result['actual']) and independent['protected_unchanged'] is True,
                                'independent_readback_missing_or_stale')
                        result['status'] = 'FINAL_VERIFIED';result['quality_status'] = 'FINAL_VERIFIED';atomic_json(journal,result)
                    else: result = perform(paths,pipeline.state,pipeline.run,plan,request,gate,operation=args.operation)
        pipeline.unchanged()
        print(json.dumps({k:v for k,v in result.items() if k in ('status','plan_digest','task_digest','reference','timeline_count','name')},ensure_ascii=False),flush=True)
    finally:
        atomic_json(pipeline.folder/'TIMING.json',ledger.summary())
    if args.operation in ('project-read','write','recover','readback','preflight','deliver'):
        # Avoid native bridge teardown after all records and timing have flushed.
        os._exit(0)


if __name__ == '__main__':main()
