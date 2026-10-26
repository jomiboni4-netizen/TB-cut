"""Real round-2 chain; mock only Codex transport, ASR/audio, detector and private parser."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from contextlib import ExitStack
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import test_project_resume as resume_fixtures
from test_index_titles import synthetic_xlsx
from quality_fixtures import fake_outcome, audit_fixture, valid_plan, synthetic_vision
import codex_worker
import index_titles
import index_subtitles
import registered_pipeline as pipeline
import owned_audio_refinement
import project_scheduler as scheduler
import project_editing_modes
import final_quality_gate
from init_project import initialize
from product_checkpoints import load_stage, resume, read_ledger, update_checkpoint, capture_stage, publish_stage
from final_quality_gate import (final_task, import_final_review, plan_errors,
                                source_overlap_details, approved_product)
from project_registry import digest, atomic_json
from runtime_meta import CacheIdentityError
from resolve_write_v2 import load_products
from types import SimpleNamespace
from sentence_boundaries import CUTPOINT_CONTRACT


class PlanContractTests(unittest.TestCase):
    def test_physical_overlap_diagnostics_name_both_clips_and_exact_intersection(self):
        plan = valid_plan(source='same-source.mp4')
        duplicate = json.loads(json.dumps(plan['variants'][0]['clips'][0]))
        duplicate.update(source_start_frame=250, audio_start_frame=250,
                         source_end_frame=500, audio_end_frame=500)
        plan['variants'][0]['clips'].append(duplicate)
        details = source_overlap_details(plan)
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0]['left']['unit_id'], 'clip_1')
        self.assertEqual(details[0]['right']['unit_id'], 'clip_2')
        self.assertEqual(details[0]['overlap_start_seconds'], 25.0)
        self.assertEqual(details[0]['overlap_end_seconds'], 50.0)
        self.assertIn('SOURCE_OVERLAP', plan_errors(plan))

    def test_old_sentence_edge_plan_cannot_drive_cache_or_repair(self):
        self.assertFalse(scheduler.current_plan_contract({'identity': {
            'stage': 'plan', 'model_version': 'sentence-owned-edges-v2-frame-estimate'}}))
        self.assertTrue(scheduler.current_plan_contract({'identity': {
            'stage': 'plan', 'model_version': CUTPOINT_CONTRACT}}))

    def test_worker_identity_retry_never_accepts_wrong_task(self):
        task = {'task_digest': 'expected'}
        wrong = {'result': {'task_digest': 'other', 'verdict': 'PASS'}}
        correct = {'result': {'task_digest': 'expected', 'verdict': 'PASS'}}
        with patch.object(codex_worker, 'launch', side_effect=[wrong, correct]) as launch:
            self.assertIs(codex_worker.matching_outcome(task), correct)
            self.assertEqual(launch.call_count, 2)
        with patch.object(codex_worker, 'launch', return_value=wrong):
            with self.assertRaisesRegex(CacheIdentityError, 'worker_response_identity_mismatch'):
                codex_worker.matching_outcome(task)

    def test_project_audit_reports_delivery_readiness_without_legacy_placeholder(self):
        paths = SimpleNamespace(load_state=lambda: {'batch': {'failed_product_ids': []}})
        target = {'product_id': 1, 'encoding_id': '001'}
        with patch.object(scheduler, 'targets', return_value=[target]), \
             patch.object(scheduler, 'resume', return_value={1: {
                 'status': 'FINAL_PASS', 'encoding_id': '001'}}), \
             patch.object(scheduler, 'approved_product', return_value={
                 'product_index': 1, 'plans': []}):
            audit = scheduler.project_audit(paths)
        self.assertEqual(audit['delivery_status'], 'READY_FOR_DELIVERY')
        self.assertNotIn('BLOCKED_UNMIGRATED_RESOLVE_EXPORT_VERIFIERS', audit['errors'])

    def test_memory_pressure_reduces_product_workers_to_one(self):
        outcomes = [SimpleNamespace(stdout='4096\n'), SimpleNamespace(stdout=(
            'Mach Virtual Memory Statistics: (page size of 4096 bytes)\n'
            'Pages free: 1000.\nPages inactive: 1000.\nPages speculative: 0.\n'))]
        with patch.object(scheduler.subprocess, 'run', side_effect=outcomes):
            self.assertEqual(scheduler.controlled_product_workers(2), 1)


class SchedulerTests(unittest.TestCase):
    setUpBase=resume_fixtures.ProjectResumeTests.setUp
    def register(self,paths,identity):
        return initialize(paths,material_folder=self.material,resolve_project_id=identity,
            title=self.title,subtitles=[str(self.srt)],videos=[str(self.video)],
            resolve_project='same display name',source_timeline='source',target_timeline='target',
            batch_id='batch-a',products=[1],force=True)
    def setUp(self):
        self.setUpBase()
        self.rows=[['编码 ID','标题']]+[[f'{i:03d}',f'Synthetic {i}'] for i in range(1,13)]
        synthetic_xlsx(self.title,self.rows)
        # Distinct texts avoid meaningless cross-product text reuse in synthetic fixtures.
        self.texts={i:''.join(chr(0x4e00+i*40+j) for j in range(18)) for i in range(1,13)}
        srt=[]
        for i in range(1,13):
            start=(i-1)*100
            def stamp(t): return f'{t//3600:02d}:{t//60%60:02d}:{t%60:02d},000'
            srt.append(f'{i}\n{stamp(start)} --> {stamp(start+50)}\n{self.texts[i]}\n')
        self.srt.write_text('\n'.join(srt))
        initialize(self.paths,products=list(range(1,13)),force=True)
        with patch.object(index_subtitles,'media_duration',return_value=2000): index_subtitles.generate(self.paths)
        parsed={'schema_version':1,'sheet':'当前商品','values':self.rows}
        with patch.object(index_titles,'parse_workbook',return_value=parsed): index_titles.generate(self.paths,'unused')
        for i in range(1,13):
            manifest={'products':[{'product_index':i,'decision_id':f'synthetic-{i}', 'confirmed_by':'synthetic reviewer',
                'confirmed_at':'2026-10-12T00:00:00Z','start':{'source_stem':'001','time':(i-1)*100},
                'end':{'source_stem':'001','time':i*100}}]}
            pipeline.confirm_range(self.paths,i,manifest)
        self.calls=[]
        self.fail_pid=None
        self.detector=self.material/'synthetic-detector'
        self.detector.write_text('synthetic')
        weights = patch.object(pipeline, 'asr_model_identity', return_value={
            'name': 'large-v3-turbo', 'resolved_path': 'synthetic', 'weights_digest': digest('synthetic weights'), 'files': {}})
        weights.start()
        self.addCleanup(weights.stop)

    def engine(self):
        stack=ExitStack()
        def launch(task):
            self.calls.append({'pid':task['product_id'],'role':task['role'],'digest':task['task_digest']})
            if task['role']=='selection':
                c=task['candidates'][0]
                ranges=[{'candidate_id':c['candidate_id'],'variant_id':'variant_1',**c['time_range']}]
                outcome=fake_outcome(task,selected_ranges=ranges)
                outcome['result']['semantic_map']=[{'candidate_id':c['candidate_id'],
                    'selling_point':'Synthetic selling point','quote':c['asr_text']}]
                return outcome
            return fake_outcome(task,verdict='REPAIRABLE' if task['product_id']==self.fail_pid else 'PASS')
        stack.enter_context(patch.object(codex_worker,'launch',side_effect=launch))
        def transcribe(media,start,end,model):
            pid=int((start+2.5)//100)+1
            return [{'start':(pid-1)*100+0.5,'end':(pid-1)*100+49.5,'text':self.texts[pid]}]
        stack.enter_context(patch.object(pipeline,'transcribe_window',side_effect=transcribe))
        stack.enter_context(patch.object(pipeline,'asr_model_identity',return_value={
            'name': 'large-v3-turbo', 'resolved_path': 'synthetic', 'weights_digest': digest('synthetic weights'), 'files': {}}))
        stack.enter_context(patch.object(pipeline,'source_clock',return_value={
            'fps':30,'average_fps':30,'mapping':'decoded-cfr-v3','frame_count':100000,
            'pts_digest':digest(['synthetic pts',30]),'max_deviation_seconds':0}))
        stack.enter_context(patch.object(owned_audio_refinement,'audio_waveform_frames',
            side_effect=lambda media,start,end:[(start+i*.02,-40) for i in range(max(1,int((end-start)/.02)))]))
        stack.enter_context(synthetic_vision(30))
        stack.enter_context(patch.object(project_editing_modes,'resolve_editing_mode',return_value=(
            'taobao_legacy',{'duration_exclusive_seconds':[45,60],'target_variant_count':3,
                'allow_fewer_variants_when_exhausted':True,'closeup_duration_ratio_max_inclusive':.3})))
        return stack

    def full(self):
        with self.engine():
            return scheduler.run_project(self.paths,mode='codex',detector=self.detector,
                                         product_workers=1)

    def test_twelve_products_four_groups_independent_bounded_inputs_and_final_gate(self):
        result=self.full()
        self.assertTrue(result['final_review_complete'],result)
        self.assertFalse(result['delivery_complete'])
        self.assertEqual(result['written_count'],0)
        self.assertEqual([g['products'] for g in result['groups_processed']],[[1,2,3],[4,5,6],[7,8,9],[10,11,12]])
        self.assertEqual(len(self.calls),24)
        self.assertEqual(len({c['digest'] for c in self.calls}),24)
        for path in (self.paths.project_records_dir/'tasks').glob('*.json'):
            task=json.loads(path.read_text())['task']
            self.assertNotIn('chat_history',task)
            self.assertNotIn('logs',task)
            self.assertLess(path.stat().st_size,20000)
        self.assertEqual(len(load_products(self.repo,[1,2],workspace=self.paths.workspace_root)),2)

    def test_group_two_failure_stops_with_full_pending_target_set(self):
        self.fail_pid=5
        result=self.full()
        self.assertEqual(len(result['groups_processed']),4)
        self.assertTrue(result['final_review_complete'])
        self.assertFalse(result['all_variants_complete'])
        self.assertIn(5, result['pending_product_ids'])
        self.assertEqual(resume(self.paths)[5]['status'],'REPAIRABLE')
        self.assertEqual(resume(self.paths)[7]['status'],'FINAL_PASS')

    def test_post_asr_overlap_returns_to_semantic_selection_with_exact_exclusion_once(self):
        left = {'variant_id': 'variant_1', 'unit_id': 'variant_1_1',
                'source_file': self.video.name, 'start_frame': 600, 'end_frame': 900,
                'fps': 30.0, 'start_seconds': 20.0, 'end_seconds': 30.0}
        right = {'variant_id': 'variant_1', 'unit_id': 'variant_1_2',
                 'source_file': self.video.name, 'start_frame': 690, 'end_frame': 810,
                 'fps': 30.0, 'start_seconds': 23.0, 'end_seconds': 27.0}
        overlap = {'source_file': self.video.name, 'left': left, 'right': right,
                   'overlap_start_seconds': 23.0, 'overlap_end_seconds': 27.0,
                   'overlap_seconds': 4.0}
        tasks = []
        gate_calls = [0]

        def plan_errors(_plan):
            gate_calls[0] += 1
            return ['SOURCE_OVERLAP'] if gate_calls[0] == 1 else []

        def launch(task):
            tasks.append(task)
            if task['role'] == 'selection':
                candidate = task['candidates'][0]
                if task.get('issues'):
                    selected = [
                        {'candidate_id': candidate['candidate_id'], 'variant_id': 'variant_1',
                         'start': 0.0, 'end': 23.0},
                        {'candidate_id': candidate['candidate_id'], 'variant_id': 'variant_1',
                         'start': 27.0, 'end': 50.0},
                    ]
                else:
                    selected = [{'candidate_id': candidate['candidate_id'],
                                 'variant_id': 'variant_1', **candidate['time_range']}]
                outcome = fake_outcome(task, selected_ranges=selected)
                outcome['result']['semantic_map'] = [{'candidate_id': candidate['candidate_id'],
                    'selling_point': 'Synthetic selling point', 'quote': candidate['asr_text']}]
                return outcome
            return fake_outcome(task)

        with self.engine(), patch.object(codex_worker, 'launch', side_effect=launch), \
             patch.object(final_quality_gate, 'plan_errors', side_effect=plan_errors), \
             patch.object(final_quality_gate, 'source_overlap_details', return_value=[overlap]), \
             patch.object(scheduler, 'vision_audit', wraps=scheduler.vision_audit) as vision_call:
            result = scheduler.process_product(
                self.paths, {'product_id': 1, 'encoding_id': '001'}, mode='codex',
                detector=self.detector, repair_limit=1)

        self.assertEqual(result['status'], 'FINAL_PASS', result)
        selection_tasks = [task for task in tasks if task['role'] == 'selection']
        self.assertEqual(len(selection_tasks), 2)
        self.assertEqual(vision_call.call_count, 1,
                         'the plan overlap must be rejected before final person sampling')
        repaired = selection_tasks[1]
        self.assertEqual(repaired['repair_route'], 'semantic_reselection')
        self.assertEqual(repaired['hard_excluded_source_ranges'], [{
            'source_file': self.video.name, 'start': 23.0, 'end': 27.0,
            'variant_ids': ['variant_1'], 'reason': 'post_asr_source_overlap'}])
        self.assertIn('重新理解当前商品', repaired['repair_instruction'])
        cursor = json.loads((self.paths.project_records_dir / 'cursors' / '1.json').read_text())
        self.assertEqual(cursor['repair_attempts'], 1)
        self.assertEqual(cursor['repair_history'][-1]['repair_route'], 'semantic_reselection')

    def test_restart_completed_products_not_recomputed_or_counted_twice(self):
        first=self.full(); count=len(self.calls)
        second=self.full()
        self.assertEqual(len(self.calls),count)
        self.assertEqual(first['final_pass_count'],second['final_pass_count'])
        self.assertEqual(scheduler.targets(self.paths)[0]['encoding_id'],'001')
        initialize(self.paths)
        self.assertEqual(scheduler.project_audit(self.paths)['final_pass_count'],12)

    def test_missing_true_review_and_package_mode_never_grant_final_pass(self):
        with patch.object(project_editing_modes,'resolve_editing_mode',return_value=(
                'taobao_legacy',{'duration_exclusive_seconds':[45,60],'target_variant_count':3,
                    'allow_fewer_variants_when_exhausted':True,
                    'closeup_duration_ratio_max_inclusive':.3})):
            result=scheduler.run_project(self.paths,mode='package')
        self.assertFalse(result['final_review_complete'])
        self.assertEqual(self.calls,[])
        task_paths=list((self.paths.project_records_dir/'tasks').glob('*.json'))
        self.assertTrue(task_paths)
        self.assertEqual(json.loads(task_paths[0].read_text())['status'],'REVIEW_PENDING')
        with self.assertRaises(CacheIdentityError): approved_product(self.paths,1)
        args=dict(product_id=1,encoding_id='001',stage='final_audit',version='spoof',model_version='fake',dependencies=[])
        fake=publish_stage(self.paths,**args,body={'verdict':'PASS'},snapshot=capture_stage(self.paths,**args))
        with self.assertRaises(CacheIdentityError):
            update_checkpoint(self.paths,product_id=1,encoding_id='001',status='FINAL_PASS',evidence_ref=fake,
                              expected_revision=read_ledger(self.paths)['revision'])

    def test_middle_word_cut_is_final_veto_despite_rough_pass(self):
        with self.engine():
            scheduler.process_product(self.paths, {'product_id':1,'encoding_id':'001'}, mode='codex',detector=self.detector)
        row=resume(self.paths)[1]
        record=load_stage(self.paths,row['plan_ref'])
        plan=json.loads(json.dumps(record['body']))
        first=plan['variants'][0]['clips'][0]
        second=json.loads(json.dumps(first))
        first['source_end_frame']=750;first['audio_end_frame']=750
        second['source_start_frame']=751;second['audio_start_frame']=751
        plan['variants'][0]['clips']=[first,second]
        args=dict(product_id=1,encoding_id='001',stage='plan',version='word-cut',model_version='word-frame-audio-v3',
                  dependencies=[d['path'] for d in record['identity']['dependencies']])
        ref=publish_stage(self.paths,**args,body=plan,snapshot=capture_stage(self.paths,**args))
        self.assertIn('WORD_CUT_AT_OPENING_ENDING_OR_SEAM',plan_errors(plan))
        with self.assertRaisesRegex(CacheIdentityError,'local_final_veto'):
            audit_fixture(self.paths,ref)
        self.assertEqual(resume(self.paths)[1]['status'],'BLOCKED')

    def test_asr_edge_miss_keeps_codex_boundary_and_still_passes_product(self):
        with self.engine():
            with patch.object(pipeline,'transcribe_window',return_value=[
                    {'start':0.5,'end':1.0,'text':'unrelated synthetic speech'}]):
                result=scheduler.process_product(self.paths,{'product_id':1,'encoding_id':'001'},
                    mode='codex',detector=self.detector)
        self.assertEqual(result['status'],'FINAL_PASS',result)
        row=resume(self.paths)[1]
        plan=load_stage(self.paths,row['plan_ref'])['body']
        clip=plan['variants'][0]['clips'][0]
        self.assertEqual(clip['cutpoint_review']['status'],'WAITING_FOR_MANUAL_EDGE_REVIEW')
        self.assertEqual(clip['cutpoint_review']['pending_edges'],['ending'])
        self.assertEqual(clip['cutpoint_proof']['ending']['status'],'CODEX_FALLBACK')
        self.assertEqual(clip['source_end_frame'],round(50*30))
        self.assertFalse(plan['cutpoint_execution']['asr_is_quality_gate'])
        cursor=json.loads((self.paths.project_records_dir/'cursors/1.json').read_text())
        self.assertTrue(cursor.get('rough'))
        self.assertTrue(cursor.get('semantic_review'))
        self.assertTrue(cursor.get('selection_vision'), cursor)
        self.assertTrue(cursor.get('vision'))
        self.assertNotIn('manual_cutpoint_preview_request',cursor)
        self.assertNotIn('selection_repair_context',cursor)
        self.assertEqual([row['role'] for row in self.calls].count('selection'),1)
        self.assertEqual([row['role'] for row in self.calls].count('final_review'),1)

    def test_causal_conclusion_not_keyword_rejected(self):
        self.assertEqual(plan_errors(valid_plan(text='穿着轻便活动自在')),[])
        self.assertEqual(plan_errors(valid_plan(text='穿着轻便因为面料柔软')),[])

    def test_cut_change_invalidates_review_and_resume(self):
        self.full()
        row=resume(self.paths)[1];record=load_stage(self.paths,row['plan_ref'])
        changed=json.loads(json.dumps(record['body']))
        changed['variants'][0]['clips'][0]['source_start_frame']+=1
        changed['variants'][0]['clips'][0]['audio_start_frame']+=1
        args=dict(product_id=1,encoding_id='001',stage='plan',version='new-cut',model_version='word-frame-audio-v3',
                  dependencies=[d['path'] for d in record['identity']['dependencies']])
        ref=publish_stage(self.paths,**args,body=changed,snapshot=capture_stage(self.paths,**args))
        self.assertEqual(resume(self.paths)[1]['status'],'BLOCKED')
        with self.assertRaises(CacheIdentityError):
            final_body=load_stage(self.paths,row['evidence_ref'])['body']
            import_final_review(self.paths,ref,final_body['vision_ref'],
                                semantic_review_ref=final_body['semantic_review_ref'])

    def test_cross_group_source_overlap_fails_global_gate(self):
        self.full()
        # Feed verified-product reader fixtures with an actual overlapping final source range.
        products={i:approved_product(self.paths,i) for i in range(1,13)}
        products[4]['plans'][0]['clips'][0]['source_start_frame']=0
        products[4]['plans'][0]['clips'][0]['source_end_frame']=500
        with patch.object(scheduler,'approved_product',side_effect=lambda paths,pid,row=None:products[pid]):
            self.assertIn('CROSS_GROUP_SOURCE_OVERLAP',scheduler.project_audit(self.paths)['errors'])

    def test_seal_tamper_rejected_and_product_lease_denies_concurrent_worker(self):
        self.full()
        row=resume(self.paths)[1];body=load_stage(self.paths,row['evidence_ref'])['body']
        semantic=load_stage(self.paths,body['semantic_review_ref'])
        path=self.paths.resolve_reference(semantic['body']['receipt']).path
        value=json.loads(path.read_text());value['result']['checks']['seams']=False
        atomic_json(path,value)
        self.assertEqual(resume(self.paths)[1]['status'],'BLOCKED')
        with scheduler.product_lease(self.paths,2):
            with self.assertRaisesRegex(CacheIdentityError,'product_worker_busy'):
                scheduler.process_product(self.paths,{'product_id':2,'encoding_id':'002'})

    def test_unmigrated_consumer_remains_blocked(self):
        from runtime_meta import expected_identity
        with self.assertRaisesRegex(CacheIdentityError,'registered_downstream_not_integrated'):expected_identity(self.paths)

    def test_unavailable_cli_keeps_task_package_blocked(self):
        with patch.object(codex_worker.shutil, 'which', return_value=None), \
             patch.object(project_editing_modes,'resolve_editing_mode',return_value=(
                 'taobao_legacy',{'duration_exclusive_seconds':[45,60],'target_variant_count':3,
                     'allow_fewer_variants_when_exhausted':True,
                     'closeup_duration_ratio_max_inclusive':.3})):
            result=scheduler.process_product(self.paths,{'product_id':1,'encoding_id':'001'},mode='codex')
        self.assertEqual(result['status'],'BLOCKED')
        self.assertIn('independent_codex_cli_unavailable',result['reason'])
        self.assertTrue(list((self.paths.project_records_dir/'tasks').glob('*.json')))

    def test_legacy_approval_and_resolve_commands_cannot_bypass_new_gate(self):
        import approve_final_plans
        import resolve_write_v2
        empty=self.base/'legacy-empty'
        empty.mkdir()
        for module, extra in ((approve_final_plans,['--products','1']), (resolve_write_v2,['--check-only'])):
            args=['synthetic','--root',str(self.repo),'--workspace',str(empty),*extra]
            with patch.object(sys,'argv',args), self.assertRaisesRegex(SystemExit,'not_migrated'):
                module.main()

    def test_batch_schedule_keeps_the_explicit_product_scope_without_registering_paths_again(self):
        before=self.paths.load_state()
        upstream=self.paths.upstream_cache_dir
        initialize(self.paths,products=[1,2,3])
        result=scheduler.schedule(self.paths)
        after=self.paths.load_state()
        self.assertEqual(len(result['targets']),3)
        self.assertEqual(result['groups'],[[1,2,3]])
        self.assertEqual(upstream,self.paths.upstream_cache_dir)
        for field in ('title_path','subtitle_paths','video_paths','resolve','project_identity'):
            self.assertEqual(before[field],after[field])

if __name__=='__main__': unittest.main()
