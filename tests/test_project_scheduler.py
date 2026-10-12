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
from quality_fixtures import fake_outcome, audit_fixture, valid_plan
import codex_worker
import index_titles
import index_subtitles
import registered_pipeline as pipeline
import project_scheduler as scheduler
from init_project import initialize
from product_checkpoints import load_stage, resume, read_ledger, update_checkpoint, capture_stage, publish_stage
from final_quality_gate import final_task, import_final_review, plan_errors, approved_product
from project_registry import digest, atomic_json
from runtime_meta import CacheIdentityError
from resolve_write_v2 import load_products


class SchedulerTests(unittest.TestCase):
    setUpBase=resume_fixtures.ProjectResumeTests.setUp
    register=resume_fixtures.ProjectResumeTests.register
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
        initialize(self.paths,products=list(range(1,13)))
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

    def engine(self):
        stack=ExitStack()
        def launch(task):
            self.calls.append({'pid':task['product_id'],'role':task['role'],'digest':task['task_digest']})
            if task['role']=='selection':
                c=task['candidates'][0]
                ranges=[{'candidate_id':c['candidate_id'],'variant_id':'variant_1',**c['time_range']}]
                return fake_outcome(task,selected_ranges=ranges)
            return fake_outcome(task,verdict='REPAIRABLE' if task['product_id']==self.fail_pid else 'PASS')
        stack.enter_context(patch.object(codex_worker,'launch',side_effect=launch))
        def transcribe(media,start,end,model):
            pid=int((start+2)//100)+1
            return [{'start':(pid-1)*100+0.5,'end':(pid-1)*100+49.5,'text':self.texts[pid]}]
        stack.enter_context(patch.object(pipeline,'transcribe_window',side_effect=transcribe))
        stack.enter_context(patch.object(pipeline,'source_fps',return_value=30))
        stack.enter_context(patch.object(pipeline,'audit_audio_start',side_effect=lambda media,start:(start,{'risk':False})))
        stack.enter_context(patch.object(pipeline,'refine_audio_end',side_effect=lambda media,start,end,duration:(end,{'risk':False})))
        def vision(plan,sources,detector):
            return {'variants':[{'variant_id':v['variant_id'],'host_identity_verified':True,'full_plan_coverage':True,
                                  'host_visible_ratio_lower_bound':0.9} for v in plan['variants']]}
        stack.enter_context(patch('person_coverage_audit_v2.registered_vision',side_effect=vision))
        return stack

    def full(self):
        with self.engine(): return scheduler.run_project(self.paths,mode='codex',detector=self.detector)

    def test_twelve_products_four_groups_independent_bounded_inputs_and_final_gate(self):
        result=self.full()
        self.assertTrue(result['final_review_complete'],result)
        self.assertFalse(result['delivery_complete'])
        self.assertEqual(result['written_count'],0)
        self.assertEqual([g['products'] for g in result['groups_processed']],[[1,2,3],[4,5,6],[7,8,9],[10,11,12]])
        self.assertEqual(len(self.calls),25)
        self.assertEqual(len({c['digest'] for c in self.calls}),25)
        for path in (self.paths.project_records_dir/'tasks').glob('*.json'):
            task=json.loads(path.read_text())['task']
            self.assertNotIn('chat_history',task)
            self.assertNotIn('logs',task)
            self.assertLess(path.stat().st_size,20000)
        self.assertEqual(len(load_products(self.repo,[1,2],workspace=self.paths.workspace_root)),2)

    def test_group_two_failure_stops_with_full_pending_target_set(self):
        self.fail_pid=5
        result=self.full()
        self.assertEqual(len(result['groups_processed']),2)
        self.assertFalse(result['final_review_complete'])
        self.assertEqual(resume(self.paths)[5]['status'],'REPAIRABLE')
        self.assertEqual(resume(self.paths)[7]['status'],'PENDING')

    def test_restart_completed_products_not_recomputed_or_counted_twice(self):
        first=self.full(); count=len(self.calls)
        second=self.full()
        self.assertEqual(len(self.calls),count)
        self.assertEqual(first['final_pass_count'],second['final_pass_count'])
        self.assertEqual(scheduler.targets(self.paths)[0]['encoding_id'],'001')
        initialize(self.paths)
        self.assertEqual(scheduler.project_audit(self.paths)['final_pass_count'],12)

    def test_missing_true_review_and_package_mode_never_grant_final_pass(self):
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
            import_final_review(self.paths,ref,load_stage(self.paths,row['evidence_ref'])['body']['vision_ref'],
                                load_stage(self.paths,row['evidence_ref'])['body']['codex_receipt'])

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
        path=self.paths.resolve_reference(body['codex_receipt']).path
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
        with patch.object(codex_worker.shutil, 'which', return_value=None):
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

    def test_whole_project_restores_full_targets_without_registering_paths_again(self):
        before=json.loads(self.paths.state_path.read_text())
        upstream=self.paths.upstream_cache_dir
        initialize(self.paths,products=[1,2,3])
        result=scheduler.schedule(self.paths)
        after=json.loads(self.paths.state_path.read_text())
        self.assertEqual(len(result['targets']),12)
        self.assertEqual(result['groups'],[[1,2,3],[4,5,6],[7,8,9],[10,11,12]])
        self.assertEqual(upstream,self.paths.upstream_cache_dir)
        for field in ('title_path','subtitle_paths','video_paths','resolve','project_identity'):
            self.assertEqual(before[field],after[field])

if __name__=='__main__': unittest.main()
