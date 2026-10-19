"""Synthetic execution tests: real assembly/cache/audio policy, no service/media calls."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from delivery_pipeline import Pipeline, PRODUCERS, semantic_task, verify_semantic
from delivery_assembly import checked_file, validate_ranges, prepare_asr
from delivery_runtime import Ledger, Store
from project_registry import atomic_json, digest
from runtime_meta import _sha256

ROOT = Path(__file__).resolve().parents[1]


class Paths:
    def __init__(self, root):
        self.repo_root = root/'repo';self.workspace_root = root/'work';self.project_records_dir = self.workspace_root/'records'
        for p in [self.repo_root/'scripts',self.repo_root/'config',self.workspace_root,self.project_records_dir]:p.mkdir(parents=True,exist_ok=True)
        for name in set(sum(PRODUCERS.values(),[])+['delivery_runtime.py','delivery_resolve.py']):
            (self.repo_root/'scripts'/name).write_bytes((ROOT/'scripts'/name).read_bytes())
        (self.repo_root/'PROJECT_RULES.md').write_text('synthetic rules')
        (self.repo_root/'config'/'vision_rules.json').write_bytes((ROOT/'config'/'vision_rules.json').read_bytes())
        (self.project_records_dir/'worker_seal.key').write_bytes(b'k'*32)
    def runtime_argument(self, ref):return self.workspace_root/ref
    def reference(self, path):return str(path.relative_to(self.workspace_root))
    def resolve_reference(self, ref):return SimpleNamespace(path=self.workspace_root/ref)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.paths = Paths(Path(self.tmp.name));w=self.paths.workspace_root
        self.media=w/'raw.mp4';self.media.write_bytes(b'synthetic media identity, never decoded')
        self.state=dict(batch=dict(product_ids=[1]),video_paths=[str(self.media)],project_identity=dict(resolve_project_id='project'))
        stat=self.media.stat();self.run=dict(run_id='synthetic',project_key='synthetic',input_content_digest='inputs',
            inputs=[dict(path=str(self.media),bytes=stat.st_size,mtime_ns=stat.st_mtime_ns,sha256=_sha256(self.media))])
        self.product=dict(product_index=1,encoding_id='00001',title='合成测试服装')
        source=dict(source_stem='raw',video_path=str(self.media),duration=1000.,global_start=0.,global_end=1000.)
        index=dict(input_digest='inputs',sources=[source],cues=[])
        windows=[];variants=[]
        texts=['柔软棉布透气亲肤，细腻纹理穿着舒适。','修身剪裁肩线自然，下摆比例利落显高。','蓝色条纹清爽明亮，冷水轻洗悬挂晾干。']
        for i,text in enumerate(texts):
            start=100*(i+1)
            words=[dict(start=start-1,end=start-.5,text='前文'),dict(start=start,end=start+46.5,text=text),dict(start=start+47,end=start+48,text='后文')]
            windows.append(dict(source_file='raw',start=start-2,end=start+49,words=words))
            index['cues'].append(dict(source_stem='raw',start=start,end=start+46.5,text=text))
            variants.append(dict(variant_id=f'v{i+1}',focus=text,clips=[dict(unit_id=f'u{i}',asr='base',window=i,first=1,last=1)]))
        self.body=dict(windows=windows,model={'name':'synthetic'})
        def save(name, body):
            path=w/name;atomic_json(path,body);return dict(path=name,sha256=_sha256(path))
        self.recipe=dict(schema_version=1,run_id='synthetic',input_content_digest='inputs',product_id=1,encoding_id='00001',
            index=save('index.json',index),titles=save('titles.json',dict(products=[self.product])),
            range_manifest=save('ranges.json',dict(products=[dict(product_index=1,start=dict(source_stem='raw',time=0.),
                end=dict(source_stem='raw',time=1000.),decision_id='synthetic-human',confirmed_by='test',confirmed_at='2026-01-01T00:00:00+00:00')])),
            asr_records=dict(base=save('asr.json',dict(body=self.body,body_digest=digest(self.body)))),
            variants=variants,pool_review={},timeline_fps=25.,width=750,height=1000)
        editorial=save('editorial.json',dict(product=self.product,coverage=[dict(window='whole',decision='reviewed')]))
        self.recipe['pool_review']=dict(reference=editorial['path'],sha256=editorial['sha256'])
        from delivery_fps import CONTRACT, media_stat
        self.recipe['frame_rates']=save('fps.json',dict(contract=CONTRACT,project_id='project',resolve_version='test-version',input_content_digest='inputs',project_fps=25.,timelines={},sources={str(self.media.resolve()):dict(fps=25.,stat=media_stat(self.media),sha256=_sha256(self.media))}))
        self.save_recipe()
        self.load=patch('delivery_pipeline.load_run',return_value=(self.paths,self.state,self.run));self.load.start();self.addCleanup(self.load.stop)
        clock_patch=patch('adaptive_vision.proven_clock',return_value=dict(fps=25.,frame_count=25000));self.clock=clock_patch.start();self.addCleanup(clock_patch.stop)
        def quiet(media,lo,hi):return [(n/50,-45.) for n in range(round(lo*50)-2,round(hi*50)+3)]
        audio_patch=patch('owned_audio_refinement.audio_level_frames',side_effect=quiet);self.audio=audio_patch.start();self.addCleanup(audio_patch.stop)
    def save_recipe(self):
        # Synthetic primary-editor decisions; production never generates these.
        from delivery_editorial import task, CONTRACT, UNIT_CHECKS, SEAM_CHECKS
        t=task(self.paths,self.recipe)
        review=dict(contract=CONTRACT,task_digest=t['task_digest'],reviewer='primary_codex',verdict='PASS',issues=[],
            cross_variant_duplicates=dict(passed=True,reason='Distinct synthetic material, cut and color claims.'),units=[],seams=[])
        for v in t['variants']:
            for u in v['units']:
                review['units'].append(dict(variant_id=v['variant_id'],unit_id=u['unit_id'],
                    checks={k:dict(passed=True,reason='Complete synthetic sentence with explicit object.') for k in UNIT_CHECKS}))
            for seam in v['seams']:
                review['seams'].append(dict(variant_id=v['variant_id'],**seam,
                    checks={k:dict(passed=True,reason='Independent synthetic sentences, no omitted condition.') for k in SEAM_CHECKS}))
        path=self.paths.workspace_root/'primary.json';atomic_json(path,review)
        self.recipe['assembly_review']=dict(path='primary.json',sha256=_sha256(path))
        atomic_json(self.paths.workspace_root/'recipe.json',self.recipe)
    def pipeline(self):return Pipeline(self.paths.repo_root,self.paths.workspace_root,'recipe.json')

    def test_full_prepare_then_zero_recompute(self):
        p,a=self.pipeline().prepare();self.assertEqual(a['status'],'PASS');self.assertEqual(len(p['variants']),3)
        self.assertEqual(self.audio.call_count,6);self.assertEqual(self.clock.call_count,1)
        original=copy.deepcopy(self.body)
        self.audio.reset_mock();self.clock.reset_mock()
        again,a=self.pipeline().prepare()
        self.assertEqual(p,again);self.assertEqual(self.audio.call_count,0);self.assertEqual(self.clock.call_count,0)
        self.assertEqual(self.body,original)

    def test_one_changed_window_only_rechecks_its_edges(self):
        self.pipeline().prepare();self.audio.reset_mock();self.clock.reset_mock()
        self.body['windows'][0]['words'][1]['end']-=.2
        path=self.paths.workspace_root/'asr.json';atomic_json(path,dict(body=self.body,body_digest=digest(self.body)))
        self.recipe['asr_records']['base']['sha256']=_sha256(path);self.save_recipe()
        p,a=self.pipeline().prepare()
        self.assertEqual(a['status'],'PASS');self.assertEqual(self.audio.call_count,2)
        self.assertEqual(p['variants'][1]['clips'][0]['asr_reference_sha256'],_sha256(path))
        rows=Ledger(self.paths.workspace_root/'delivery').rows()
        self.assertTrue(any(r['event']=='INVALIDATED' for r in rows))

    def test_writer_code_change_does_not_recompute_assembly_or_audio(self):
        self.pipeline().prepare();self.audio.reset_mock();self.clock.reset_mock()
        path=self.paths.repo_root/'scripts/delivery_resolve.py';path.write_text(path.read_text()+'\n# synthetic writer update\n')
        self.pipeline().prepare()
        self.assertEqual(self.audio.call_count,0);self.assertEqual(self.clock.call_count,0)

    def test_changed_audio_producer_rechecks_audio_only(self):
        self.pipeline().prepare();self.audio.reset_mock();self.clock.reset_mock()
        path=self.paths.repo_root/'scripts/owned_audio_refinement.py';path.write_text(path.read_text()+'\n# test\n')
        self.pipeline().prepare()
        self.assertEqual(self.audio.call_count,6);self.assertEqual(self.clock.call_count,0)

    def test_tampered_checkpoint_does_not_recompute_or_attest(self):
        pipeline=self.pipeline();pipeline.prepare();self.audio.reset_mock()
        path=next(pipeline.store.folder.glob('*.json'));record=json.loads(path.read_text());record['body_digest']='wrong';atomic_json(path,record)
        with self.assertRaisesRegex(ValueError,'checkpoint_corrupt'):self.pipeline().prepare()
        self.assertEqual(self.audio.call_count,0)

    def test_no_external_call_without_exact_task_authorization(self):
        pipeline=self.pipeline();plan,_=pipeline.prepare()
        with patch('codex_worker.execute') as call:
            with self.assertRaisesRegex(ValueError,'AUTHORIZATION_REQUIRED'):pipeline.semantic(plan)
            with self.assertRaisesRegex(ValueError,'AUTHORIZATION_REQUIRED'):pipeline.semantic(plan,'old-product-task')
            call.assert_not_called()

    def test_outside_human_range_and_input_changes_block(self):
        pipeline=self.pipeline();plan,_=pipeline.prepare()
        altered=copy.deepcopy(plan);altered['variants'][0]['clips'][0]['source_start_frame']=0
        with self.assertRaisesRegex(ValueError,'outside_confirmed'):validate_ranges(altered,dict(ranges=[dict(media_stem='raw',start=99.,end=500.)]))
        (self.paths.workspace_root/'asr.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'digest_changed'):pipeline.unchanged()

    def test_code_change_during_normal_execution_stops(self):
        pipeline=self.pipeline();path=self.paths.repo_root/'scripts/delivery_resolve.py';path.write_text('changed')
        with self.assertRaisesRegex(ValueError,'code_changed'):pipeline.prepare()

    def test_vision_policy_config_change_during_execution_stops(self):
        pipeline=self.pipeline();path=self.paths.repo_root/'config'/'vision_rules.json';path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'vision_rules_changed_during_execution'):pipeline.unchanged()

    def test_reordered_variants_reuse_all_audio_and_refresh_plan(self):
        before,_=self.pipeline().prepare();self.audio.reset_mock()
        self.recipe['variants'].reverse();self.save_recipe();after,_=self.pipeline().prepare()
        self.assertNotEqual(digest(before),digest(after));self.assertEqual(self.audio.call_count,0)

    def test_reallocated_clips_keep_physical_audio_cache(self):
        self.pipeline().prepare();self.audio.reset_mock()
        left,right=self.recipe['variants'][:2]
        left['clips'],right['clips']=right['clips'],left['clips']
        self.save_recipe();self.pipeline().prepare()
        self.assertEqual(self.audio.call_count,0)

    def test_semantic_cannot_be_bound_to_different_plan(self):
        p,_=self.pipeline().prepare()
        with self.assertRaisesRegex(ValueError,'semantic_plan_or_task_changed'):
            verify_semantic(self.paths,p,dict(plan_digest='old',task=semantic_task(p)))


    def test_exact_authorized_review_is_sealed_and_never_resent(self):
        pipeline=self.pipeline();plan,_=pipeline.prepare();task=semantic_task(plan)
        def reviewer(request):
            return dict(thread_id='independent-synthetic-context',events_digest='synthetic',elapsed_seconds=.1,usage={},
                result=dict(task_digest=request['task_digest'],verdict='PASS',
                    checks={k:True for k in ['opening','ending','causal_logic','seams','selling_value','duplicates']},issues=[],selected_ranges=[]))
        with patch('codex_worker.launch',side_effect=reviewer) as call:
            first=pipeline.semantic(plan,task['task_digest']);verify_semantic(self.paths,plan,first)
            self.assertEqual(pipeline.semantic(plan),first);self.assertEqual(call.call_count,1)
            changed=copy.deepcopy(plan);changed['variants'][0]['focus']='changed emphasis'
            with self.assertRaisesRegex(ValueError,'AUTHORIZATION_REQUIRED'):pipeline.semantic(changed,task['task_digest'])
            self.assertEqual(call.call_count,1)

    def test_final_checks_run_independently_after_plan_freeze(self):
        from threading import Barrier
        pipeline=self.pipeline();plan,_=pipeline.prepare();barrier=Barrier(2)
        def branch(*args):barrier.wait(timeout=3);return dict(plan_digest=digest(plan),status='PASS')
        with patch.object(pipeline,'semantic',side_effect=branch),patch.object(pipeline,'vision',side_effect=branch):
            self.assertEqual(pipeline.checks('synthetic-detector')['status'],'PASS')

    def test_failed_local_gate_stops_both_final_branches(self):
        pipeline=self.pipeline();plan,a=pipeline.prepare();a['status']='REPAIRABLE'
        with patch.object(pipeline,'semantic') as sem,patch.object(pipeline,'vision') as vision:
            with self.assertRaisesRegex(ValueError,'LOCAL_GATE'):pipeline.checks('synthetic-detector')
            sem.assert_not_called();vision.assert_not_called()


    def test_missing_primary_review_stops_before_clock_audio(self):
        self.recipe.pop('assembly_review')
        atomic_json(self.paths.workspace_root/'recipe.json',self.recipe)
        with self.assertRaisesRegex(ValueError,'ASSEMBLY_REVIEW_REQUIRED'):self.pipeline().prepare()
        self.audio.assert_not_called();self.clock.assert_not_called()

    def test_omitted_subject_or_changed_context_invalidates_early_review(self):
        self.recipe['variants'][0]['clips'][0]['first']=0
        atomic_json(self.paths.workspace_root/'recipe.json',self.recipe)
        with self.assertRaisesRegex(ValueError,'assembly_review_stale'):self.pipeline().prepare()
        self.audio.assert_not_called();self.clock.assert_not_called()

    def test_unfinished_clause_decision_blocks_before_expensive_work(self):
        path=self.paths.workspace_root/'primary.json';review=json.loads(path.read_text())
        review['units'][0]['checks']['no_internal_unfinished_clause']=dict(passed=False,reason='Action object omitted before transition.')
        atomic_json(path,review);self.recipe['assembly_review']['sha256']=_sha256(path)
        atomic_json(self.paths.workspace_root/'recipe.json',self.recipe)
        with self.assertRaisesRegex(ValueError,'assembly_semantic_repair_required'):self.pipeline().prepare()
        self.audio.assert_not_called();self.clock.assert_not_called()

    def test_frame_registration_required_before_clock_audio(self):
        self.recipe.pop('frame_rates');self.save_recipe()
        with self.assertRaisesRegex(ValueError,'PROJECT_FRAME_RATES_REQUIRED'):self.pipeline().prepare()
        self.audio.assert_not_called();self.clock.assert_not_called()

    def test_recipe_cannot_override_registered_fps(self):
        self.recipe['timeline_fps']=30.;self.save_recipe()
        with self.assertRaisesRegex(ValueError,'frame_rate_mismatch'):self.pipeline().prepare()
        self.audio.assert_not_called();self.clock.assert_not_called()


class LedgerTests(unittest.TestCase):
    def test_concurrency_failure_wait_repair_and_unattributed_are_separate(self):
        with tempfile.TemporaryDirectory() as root:
            ledger=Ledger(root)
            rows=[dict(event='START',token='a',wall=10,monotonic_ns=0,name='decode',category='compute',repair=True),
                  dict(event='START',token='b',wall=12,monotonic_ns=0,name='authorization',category='waiting'),
                  dict(event='END',token='a',wall=15,seconds=5,status='FAILED'),
                  dict(event='END',token='b',wall=18,seconds=6,status='PASS')]
            ledger.path.write_text('\n'.join(json.dumps(r) for r in rows))
            summary=ledger.summary(now=20)
            self.assertEqual(summary['observed_union_seconds'],8)
            self.assertEqual(summary['unattributed_seconds'],2)
            self.assertEqual(summary['categories']['compute']['failures'],1)
            self.assertEqual(summary['categories']['waiting']['inclusive_seconds'],6)
            self.assertEqual(summary['repair_spans'],1)
    def test_open_and_double_closed_span(self):
        with tempfile.TemporaryDirectory() as root:
            ledger=Ledger(root);token=ledger.begin('editing','editorial')
            self.assertEqual(ledger.summary()['open_spans'],[token]);ledger.end(token)
            with self.assertRaisesRegex(ValueError,'already_closed'):ledger.end(token)


if __name__=='__main__':unittest.main()
