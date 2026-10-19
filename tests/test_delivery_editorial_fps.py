"""Review provenance, seam coverage and rate registration regressions."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_delivery_pipeline as fixtures
from delivery_editorial import task,verify,SEAM_CHECKS
from delivery_fps import capture,load,verify_live,rate
from delivery_resolve import read
from project_registry import atomic_json
from runtime_meta import _sha256


class EarlyReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.DeliveryTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.f=self.fixture
    def test_minimal_context_includes_excluded_subject_and_next_words(self):
        t=task(self.f.paths,self.f.recipe);u=t['variants'][0]['units'][0]
        self.assertEqual(u['previous_words'][0]['text'],'前文')
        self.assertEqual(u['next_words'][0]['text'],'后文')
        self.assertEqual(len(u['retained_words']),1)
        self.assertTrue(u['source_context'])
    def test_source_context_change_stales_review_even_with_same_selected_words(self):
        p=self.f.paths.workspace_root/'index.json';index=read(p)
        index['cues'].append(dict(source_stem='raw',start=98.,end=99.,text='Subject in preceding context'))
        atomic_json(p,index);self.f.recipe['index']['sha256']=_sha256(p)
        t=task(self.f.paths,self.f.recipe)
        with self.assertRaisesRegex(ValueError,'stale'):verify(self.f.paths,self.f.recipe,t)
    def test_every_seam_requires_specific_decisions(self):
        self.f.recipe['variants'][0]['clips'].append(self.f.recipe['variants'][1]['clips'][0])
        self.f.save_recipe();t=task(self.f.paths,self.f.recipe)
        review=verify(self.f.paths,self.f.recipe,t);self.assertEqual(len(review['seams']),1)
        p=self.f.paths.workspace_root/'primary.json';review['seams']=[]
        atomic_json(p,review);self.f.recipe['assembly_review']['sha256']=_sha256(p)
        with self.assertRaisesRegex(ValueError,'coverage_missing'):verify(self.f.paths,self.f.recipe,t)
    def test_blank_reason_or_duplicate_rows_cannot_pass(self):
        p=self.f.paths.workspace_root/'primary.json';review=read(p)
        review['units'].append(copy.deepcopy(review['units'][0]))
        atomic_json(p,review);self.f.recipe['assembly_review']['sha256']=_sha256(p)
        with self.assertRaisesRegex(ValueError,'coverage_missing'):verify(self.f.paths,self.f.recipe,task(self.f.paths,self.f.recipe))
    def test_rate_context_reuse_does_not_probe_or_connect(self):
        with patch('delivery_resolve.connected') as connect,patch('delivery_fps.subprocess.check_output') as probe:
            self.assertEqual(load(self.f.paths,self.f.state,self.f.run,self.f.recipe),load(self.f.paths,self.f.state,self.f.run,self.f.recipe))
            connect.assert_not_called();probe.assert_not_called()
    def test_changed_media_stat_invalidates_cached_rates(self):
        self.f.media.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'fps_source_changed'):load(self.f.paths,self.f.state,self.f.run,self.f.recipe)
    def test_capture_records_average_disagreement_without_certifying_cfr(self):
        state=copy.deepcopy(self.f.state);state['resolve']=dict(source_timeline='source',target_timeline='target')
        from test_delivery_resolve import Project,Media
        project=Project(Media(self.f.media));resolve=SimpleNamespace(GetVersionString=lambda:'test-version')
        with patch('delivery_resolve.connected',return_value=(resolve,None,project)),patch('delivery_fps.subprocess.check_output',return_value=json.dumps(dict(streams=[dict(r_frame_rate='25/1',avg_frame_rate='24999/1000')]))):
            body=capture(self.f.paths,state,self.f.run)
        row=next(iter(body['sources'].values()))
        self.assertFalse(row['metadata_rates_agree']);self.assertEqual(row['fps'],25.)
        self.assertEqual(body['cfr_certification'],'metadata_only_full_clock_required_at_assembly')
        verify_live(body,resolve,project)
        project.timelines[0].settings['timelineFrameRate']='30'
        with self.assertRaisesRegex(ValueError,'frame_rate_mismatch'):verify_live(body,resolve,project)
    def test_registration_inherits_only_same_project_and_no_authorization(self):
        from coldstart_semantics import inherited_frame_rates
        parent=dict(runtime=dict(frame_rate_registration=self.f.recipe['frame_rates'],external_authorization=True))
        ref=inherited_frame_rates(parent,'project',self.f.paths.workspace_root/'state.json')
        self.assertTrue(Path(ref['path']).is_absolute())
        self.assertEqual(set(ref),{'path','sha256'})
        with self.assertRaisesRegex(ValueError,'project_mismatch'):
            inherited_frame_rates(parent,'other-project',self.f.paths.workspace_root/'state.json')

    def test_rational_frame_rate_and_invalid_values(self):
        self.assertAlmostEqual(rate('30000/1001'),29.97002997)
        for value in ['0','nan','1/0','oops',None]:
            with self.assertRaises(ValueError):rate(value)

if __name__=='__main__':unittest.main()
