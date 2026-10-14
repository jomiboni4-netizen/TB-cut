"""Boundary safety regression tests using synthetic, isolated workspaces only."""
import copy
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_range_overrides as fixtures
import locate_link_intros as locator
import apply_range_overrides_v2 as overrides
import runtime_publication as publication
from runtime_meta import (CacheIdentityError, expected_identity, load_checked_json,
                          metadata_path, invalidation_path, write_metadata)


class AutomaticBoundaryTests(unittest.TestCase):
    def setUp(self):
        fixtures.RangeOverridesTests.setUp(self)

    # Borrow fixture helpers without inheriting/rerunning unrelated tests.
    row = fixtures.RangeOverridesTests.row
    change = fixtures.RangeOverridesTests.change
    save = fixtures.RangeOverridesTests.save
    save_overrides = fixtures.RangeOverridesTests.save_overrides
    pair = fixtures.RangeOverridesTests.pair

    def index_with_anchors(self, anchors):
        index = copy.deepcopy(self.index)
        index['cues'] = [dict(source_stem='A', start=time, end=time+1,
                             global_start=time, global_end=time+1,
                             text=f'正式开始{pid}号链接讲解') for pid, time in anchors]
        return index

    def suggest(self, anchors, count=3):
        return locator.build_suggestions(self.index_with_anchors(anchors), self.titles['products'][:count])

    def test_missing_middle_blocks_full_set(self):
        result = self.suggest([(1, 10), (3, 120)])
        self.assertEqual(result['status'], 'PRODUCT_UNCERTAIN')
        self.assertEqual(result['unresolved_products'], [1, 2, 3])
        self.assertEqual(result['range_suggestions'], [])
        self.assertEqual(result['products'][1]['boundary_status'], 'PRODUCT_UNCERTAIN')

    def test_ambiguous_anchor_keeps_diagnostics_and_blocks(self):
        result = self.suggest([(1, 10), (1, 120)], count=1)
        self.assertEqual(result['products'][0]['boundary_status'], 'PRODUCT_UNCERTAIN')
        self.assertEqual(len(result['products'][0]['link_mentions']), 2)
        self.assertEqual(result['range_suggestions'], [])

    def test_actual_live_order_and_inference_not_confirmation(self):
        result = self.suggest([(3, 10), (1, 70), (2, 140)])
        self.assertEqual(result['range_suggestions'], [])
        self.assertFalse(result['ready_for_cutting'])
        self.assertEqual(result['status'], 'PRODUCT_UNCERTAIN')
        self.assertEqual(result['authority'], 'NOT_AUTHENTICATED_NOT_FOR_CUTTING')

    def test_old_order_helper_does_not_impose_product_order(self):
        products = [{'candidates': [{'score': 10, 'global_start': time}]} for time in [130, 10]]
        self.assertEqual([r['global_start'] for r in locator.choose_ordered(products)], [130, 10])

    def test_auto_preserves_human_and_unknown_ranges_even_with_marker(self):
        for provenance in [None, {'start': 'human_confirmed', 'end': 'human_confirmed'}]:
            with self.subTest(provenance=provenance):
                doc = copy.deepcopy(self.document)
                if provenance: doc['products'][0]['boundary_provenance'] = provenance
                self.save('product_ranges.json', doc)
                marker = invalidation_path(self.output)
                marker.write_text('existing invalid marker')
                before = self.pair(), marker.read_bytes()
                result = locator.generate(self.paths)
                self.assertEqual(result['status'], 'PRODUCT_UNCERTAIN')
                self.assertEqual((self.pair(), marker.read_bytes()), before)
                self.assertFalse((self.paths.cache_dir/'link_intros.json').exists())

    def test_no_existing_ranges_does_not_promote_suggestions(self):
        self.output.unlink();metadata_path(self.output).unlink()
        locator.generate(self.paths)
        self.assertFalse(self.output.exists())

    def test_invalid_input_preserves_previous_suggestion(self):
        locator.generate(self.paths)
        output = self.paths.cache_dir/'boundary_suggestions.json'
        before = output.read_bytes(), metadata_path(output).read_bytes()
        self.save('subtitle_index.json', {**self.index, 'sources': []}, 'index_subtitles.py')
        with self.assertRaises(ValueError): locator.generate(self.paths)
        self.assertEqual((output.read_bytes(),metadata_path(output).read_bytes()),before)
        self.assertFalse(invalidation_path(output).exists())

    def test_suggestion_publication_failure_keeps_old_pair_and_marker(self):
        locator.generate(self.paths)
        output = self.paths.cache_dir/'boundary_suggestions.json'
        before = output.read_bytes(), metadata_path(output).read_bytes()
        replace = os.replace
        def fail(source, target):
            if Path(target) == metadata_path(output): raise OSError('synthetic publication failure')
            return replace(source,target)
        with patch.object(publication.os,'replace',side_effect=fail), self.assertRaises(OSError):
            locator.generate(self.paths)
        self.assertEqual((output.read_bytes(),metadata_path(output).read_bytes()),before)
        self.assertTrue(invalidation_path(output).exists())
        self.assertEqual(self.pair(),self.before)

    def test_suggestion_inputs_changing_are_rejected(self):
        build = locator.build_suggestions
        def changing(*args):
            result = build(*args)
            (self.paths.cache_dir/'subtitle_index.json').write_text('{}')
            return result
        with patch.object(locator,'build_suggestions',side_effect=changing), self.assertRaises(CacheIdentityError):
            locator.generate(self.paths)
        self.assertFalse((self.paths.cache_dir/'boundary_suggestions.json').exists())
        self.assertEqual(self.pair(),self.before)

    def test_legal_product_gap_preserved_by_explicit_override(self):
        self.save_overrides([self.change(1, 2, 7)])
        overrides.apply_overrides(self.paths, str(self.override))
        result = load_checked_json(self.paths,self.output)
        self.assertEqual((result['products'][0]['global_end'],result['products'][1]['global_start']),(7,10))
        self.assertEqual(result['products'][0]['boundary_provenance'], {'start':'explicit_override','end':'explicit_override'})

    def test_downstream_digest_rejects_previous_same_batch_artifact(self):
        derived = self.paths.cache_dir/'product_01_rough'/'mimo_request.json'
        derived.parent.mkdir();derived.write_text('{"candidates": []}')
        write_metadata(self.paths,derived,'synthetic',product_id=1)
        load_checked_json(self.paths,derived)
        original = derived.read_bytes(),metadata_path(derived).read_bytes()
        overrides.apply_overrides(self.paths,str(self.override))
        with self.assertRaisesRegex(CacheIdentityError,'range_dependency_mismatch'):
            load_checked_json(self.paths,derived)
        self.assertEqual((derived.read_bytes(),metadata_path(derived).read_bytes()),original)

    def test_downstream_legacy_metadata_cannot_be_reused(self):
        derived = self.paths.cache_dir/'person_coverage_audit.json'
        derived.write_text('{}')
        write_metadata(self.paths,derived,'synthetic')
        meta = json.loads(metadata_path(derived).read_text());del meta['range_dependency']
        metadata_path(derived).write_text(json.dumps(meta))
        with self.assertRaisesRegex(CacheIdentityError,'range_dependency_missing'):
            load_checked_json(self.paths,derived)

    def test_bound_identity_rejects_new_version_even_when_new_cache_matches(self):
        captured = expected_identity(self.paths)
        overrides.apply_overrides(self.paths,str(self.override))
        derived = self.paths.cache_dir/'person_coverage_audit.json';derived.write_text('{}')
        write_metadata(self.paths,derived,'synthetic')
        with self.assertRaisesRegex(CacheIdentityError,'ranges_changed_during_generation'):
            load_checked_json(self.paths,derived,expected=captured)

    def test_legacy_candidate_attestation_refused(self):
        import hashlib
        import sys
        import attest_runtime_cache
        derived = self.paths.cache_dir/'product_01_rough'/'mimo_request.json'
        derived.parent.mkdir();derived.write_text('{"product_index":1,"pipeline_version":"2.1","candidates":[]}')
        identity = expected_identity(self.paths)
        argv=['attest_runtime_cache.py','--root',str(self.repo),'--workspace',str(self.workspace),
              'attest-input','--artifact',str(derived),'--artifact-sha256',hashlib.sha256(derived.read_bytes()).hexdigest(),
              '--batch-id',identity['batch_id'],'--input-fingerprint',identity['input_fingerprint'],'--product-id','1']
        with patch.object(sys,'argv',argv), self.assertRaisesRegex(CacheIdentityError,'legacy_candidates_require_regeneration'):
            attest_runtime_cache.main()
        self.assertFalse(metadata_path(derived).exists())

    def test_range_change_during_generation_cannot_stamp_latest(self):
        captured = expected_identity(self.paths)
        overrides.apply_overrides(self.paths,str(self.override))
        derived = self.paths.cache_dir/'any-output.json';derived.write_text('{}')
        with self.assertRaisesRegex(CacheIdentityError,'ranges_changed_during_generation'):
            write_metadata(self.paths,derived,'audit_mimo_v2.py',identity=captured)
        self.assertFalse(metadata_path(derived).exists())

    def test_upstream_invalid_marker_blocks_downstream(self):
        derived = self.paths.cache_dir/'person_coverage_audit.json';derived.write_text('{}')
        write_metadata(self.paths,derived,'synthetic')
        invalidation_path(self.output).write_text('synthetic failure')
        with self.assertRaisesRegex(CacheIdentityError,'publication_incomplete'):
            load_checked_json(self.paths,derived)


class SourceCoverageTests(unittest.TestCase):
    def sources(self, gap=0):
        return overrides.source_map({'sources':[
            {'source_stem':'X','global_start':100,'global_end':110,'duration':10},
            {'source_stem':'Y','global_start':110+gap,'global_end':120+gap,'duration':10}]})

    def test_local_time_cannot_exceed_source_even_by_fraction_ms(self):
        for value in [-0.0001,10.0001,110]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError,'outside_source'):
                overrides.global_time({'source_stem':'X','time':value},self.sources())
        self.assertEqual(overrides.global_time({'source_stem':'X','time':1.234},self.sources()),101.234)

    def test_stored_local_split_cannot_escape_source(self):
        for left, right in [(-0.0001, 10), (0, 10.0001)]:
            row = {'global_start':100,'global_end':110,
                   'ranges':[{'media_stem':'X','start':left,'end':right}]}
            with self.subTest(left=left,right=right), self.assertRaisesRegex(ValueError,'local_time_outside_source'):
                overrides.validate_range(row,self.sources())

    def test_recorded_duration_also_limits_local_time(self):
        sources=self.sources();sources['X']['duration']=9
        with self.assertRaisesRegex(ValueError,'outside_source'):
            overrides.global_time({'source_stem':'X','time':9.5},sources)

    def test_split_cross_source_uses_local_coordinates(self):
        sources=self.sources();parts=overrides.split_range(108.123,112.345,list(sources.values()))
        self.assertEqual(parts,[{'media_stem':'X','start':8.123,'end':10.0},{'media_stem':'Y','start':0.0,'end':2.345}])
        overrides.validate_range({'global_start':108.123,'global_end':112.345,'ranges':parts},sources)

    def test_real_source_hole_not_absorbed_by_rounding_budget(self):
        for gap in [0.0001,0.001,0.003,1]:
            sources=self.sources(gap)
            with self.subTest(gap=gap), self.assertRaisesRegex(ValueError,'crosses_gap'):
                overrides.validate_range({'global_start':108,'global_end':115,
                    'ranges':overrides.split_range(108,115,list(sources.values()))},sources)

    def test_absolute_overlap_tolerance_and_furthest_predecessor(self):
        self.assertEqual(overrides.OVERLAP_TOLERANCE_SECONDS,1e-9)
        for base in [0,10000]:
            overrides.validate_global_ranges([{'product_index':5,'global_start':base,'global_end':base+10},
                {'product_index':1,'global_start':base+10-1e-10,'global_end':base+20}])
            with self.assertRaisesRegex(ValueError,'cross_product_range_overlap'):
                overrides.validate_global_ranges([{'product_index':5,'global_start':base,'global_end':base+10},
                    {'product_index':1,'global_start':base+10-2e-9,'global_end':base+20}])


if __name__=='__main__': unittest.main()

class ConfirmedRangeAndRequestTests(unittest.TestCase):
    row = fixtures.RangeOverridesTests.row
    change = fixtures.RangeOverridesTests.change
    save = fixtures.RangeOverridesTests.save
    save_overrides = fixtures.RangeOverridesTests.save_overrides
    pair = fixtures.RangeOverridesTests.pair

    def setUp(self):
        fixtures.RangeOverridesTests.setUp(self)
        from publish_confirmed_ranges import publish_or_preflight
        from build_expanded_codex_request import build_requests
        self.publish_or_preflight=publish_or_preflight
        self.build_requests=build_requests
        self.manifest_path=self.root/'manual_confirmations.json'
        self.write_manifest()

    def point(self, seconds, sources=None):
        sources=sources or self.index['sources']
        for source in sources:
            lo=source['global_start'];hi=source['global_end']
            if lo<=seconds<=hi:
                return {'source_stem':source['source_stem'],'time':round(seconds-lo,3)}
        raise ValueError('synthetic time outside sources')

    def make_manifest(self):
        products=[]
        for pos,title in enumerate(self.titles['products']):
            start=pos*2.0;end=start+0.5
            products.append({'product_index':title['product_index'],'decision_id':f'synthetic-{pos+1}',
              'confirmed_by':'synthetic reviewer','confirmed_at':'2026-10-08T00:00:00Z',
              'start':self.point(start),'end':self.point(end)})
        return {'schema_version':1,'products':products}

    def write_manifest(self,manifest=None):
        self.manifest_path.write_text(json.dumps(manifest or self.make_manifest(),ensure_ascii=False))

    def first_publish(self):
        self.output.unlink(missing_ok=True);metadata_path(self.output).unlink(missing_ok=True)
        return self.publish_or_preflight(self.paths,self.manifest_path)

    def test_first_creation_defaults_to_read_only_preflight(self):
        result=self.first_publish()
        self.assertEqual(result['status'],'PREFLIGHT_PASS');self.assertTrue(result['will_create'])
        self.assertFalse(self.output.exists())

    def test_first_creation_publish_emits_verifiable_confirmation_proofs(self):
        self.first_publish();result=self.publish_or_preflight(self.paths,self.manifest_path,publish=True)
        self.assertEqual(result['status'],'PUBLISHED')
        doc=load_checked_json(self.paths,self.output,artifact_schema_version=1)
        from publish_confirmed_ranges import validate_confirmed_document
        validate_confirmed_document(doc,self.titles['products'],self.index)
        self.assertEqual(len(doc['products']),30)
        self.assertEqual(doc['products'][0]['boundary_provenance']['start']['kind'],'human_confirmed')

    def test_manual_manifest_order_does_not_impose_live_order(self):
        manifest=self.make_manifest()
        for pos,row in enumerate(manifest['products']):
            global_start=(len(manifest['products'])-pos)*2.0
            row['start']=self.point(global_start);row['end']=self.point(global_start+0.5)
        self.write_manifest(manifest)
        self.first_publish();result=self.publish_or_preflight(self.paths,self.manifest_path,publish=True)
        self.assertEqual(result['status'],'PUBLISHED')
        doc=load_checked_json(self.paths,self.output)
        self.assertGreater(doc['products'][0]['global_start'],doc['products'][-1]['global_start'])

    def test_existing_confirmed_ranges_require_explicit_replacement(self):
        self.first_publish();self.publish_or_preflight(self.paths,self.manifest_path,publish=True)
        with self.assertRaisesRegex(ValueError,'explicit_replacement'):
            self.publish_or_preflight(self.paths,self.manifest_path,publish=True)
        result=self.publish_or_preflight(self.paths,self.manifest_path,publish=True,replace_confirmed=True)
        self.assertEqual(result['status'],'PUBLISHED')

    def test_unconfirmed_or_missing_product_blocks_first_creation(self):
        manifest=self.make_manifest();manifest['products'].pop()
        self.write_manifest(manifest)
        with self.assertRaisesRegex(ValueError,'incomplete_product_set'):
            self.first_publish()
        self.assertFalse(self.output.exists())

    def test_invalid_source_local_time_is_not_clamped(self):
        manifest=self.make_manifest();manifest['products'][0]['start']['time']=-0.001
        self.write_manifest(manifest)
        with self.assertRaisesRegex(ValueError,'outside_source'):
            self.first_publish()
        self.assertFalse(self.output.exists())

    def test_full_collection_overlap_rejected_without_adjustment(self):
        manifest=self.make_manifest();manifest['products'][1]['start']['time']=0.4
        self.write_manifest(manifest)
        with self.assertRaisesRegex(ValueError,'cross_product_range_overlap'):
            self.first_publish()
        self.assertFalse(self.output.exists())

    def test_source_gap_cannot_be_spanned(self):
        self.index['sources']=[{'source_stem':'A','global_start':0,'global_end':30,'duration':30},
                               {'source_stem':'B','global_start':30.1,'global_end':400,'duration':369.9}]
        self.save('subtitle_index.json',self.index,'index_subtitles.py')
        manifest=self.make_manifest()
        manifest['products'][14]['start']=self.point(29.5,self.index['sources'])
        manifest['products'][14]['end']=self.point(30.2,self.index['sources'])
        self.write_manifest(manifest)
        with self.assertRaisesRegex(ValueError,'crosses_gap'):
            self.first_publish()

    def test_missing_or_untrusted_accepted_ranges_refuse_downstream_build(self):
        self.output.unlink(missing_ok=True);metadata_path(self.output).unlink(missing_ok=True)
        with self.assertRaisesRegex(CacheIdentityError,'artifact_missing'):
            self.build_requests(self.paths,[1])
        self.save('product_ranges.json',self.document,'synthetic-test')
        with self.assertRaisesRegex(CacheIdentityError,'range_authority_unverified'):
            self.build_requests(self.paths,[1])

    def test_rebuild_without_old_base_request_and_bind_range_digest(self):
        self.first_publish();self.publish_or_preflight(self.paths,self.manifest_path,publish=True)
        self.save('subtitle_index.json',{**self.index,'cues':[
           {'source_stem':'A','start':0.1,'end':0.4,'global_start':0.1,'global_end':0.4,'text':'Synthetic fabric feels soft'}]},'index_subtitles.py')
        old=self.paths.cache_dir/'product_01_rough'/'mimo_request.json'
        self.assertFalse(old.exists())
        [output]=self.build_requests(self.paths,[1])
        request=load_checked_json(self.paths,output,product_id=1)
        self.assertEqual(request['rules']['product_title'],self.titles['products'][0]['title'])
        self.assertEqual(request['product_range_sha256'],hashlib.sha256(self.output.read_bytes()).hexdigest())
        self.assertEqual(request['candidates'][0]['time_range'],{'start':0.1,'end':0.4})
        self.assertTrue(metadata_path(output).exists())

    def test_changed_range_invalidates_rebuilt_request(self):
        self.first_publish();self.publish_or_preflight(self.paths,self.manifest_path,publish=True)
        [output]=self.build_requests(self.paths,[1]);old_bytes=output.read_bytes()
        self.save_overrides([self.change(1,0.1,0.4)])
        overrides.apply_overrides(self.paths,str(self.override))
        with self.assertRaisesRegex(CacheIdentityError,'range_dependency_mismatch'):
            load_checked_json(self.paths,output,product_id=1)
        self.assertEqual(output.read_bytes(),old_bytes)

    def test_unversioned_historical_candidate_is_not_consulted(self):
        self.first_publish();self.publish_or_preflight(self.paths,self.manifest_path,publish=True)
        old=self.paths.cache_dir/'product_01_rough'/'mimo_request.json';old.parent.mkdir(parents=True);old.write_text('{"candidates":["stale"]}')
        self.save('subtitle_index.json',{**self.index,'cues':[
          {'source_stem':'A','start':0.1,'end':0.4,'global_start':0.1,'global_end':0.4,'text':'Synthetic useful material'}]},'index_subtitles.py')
        [output]=self.build_requests(self.paths,[1])
        request=load_checked_json(self.paths,output,product_id=1)
        self.assertNotIn('stale',json.dumps(request))

    def test_replacement_failure_restores_old_pair_and_keeps_marker(self):
        self.first_publish();self.publish_or_preflight(self.paths,self.manifest_path,publish=True)
        before=self.pair();manifest=self.make_manifest();manifest['products'][0]['end']['time']=0.6;self.write_manifest(manifest)
        replace=os.replace
        def fail(source,target):
            if Path(target)==metadata_path(self.output):raise OSError('synthetic replacement failure')
            return replace(source,target)
        with patch.object(publication.os,'replace',side_effect=fail),self.assertRaises(OSError):
            self.publish_or_preflight(self.paths,self.manifest_path,publish=True,replace_confirmed=True)
        self.assertEqual(self.pair(),before);self.assertTrue(invalidation_path(self.output).exists())
        with self.assertRaisesRegex(CacheIdentityError,'publication_incomplete'):
            load_checked_json(self.paths,self.output)

    def test_request_candidates_never_include_cues_outside_confirmed_range(self):
        self.first_publish();self.publish_or_preflight(self.paths,self.manifest_path,publish=True)
        self.save('subtitle_index.json',{**self.index,'cues':[
          {'source_stem':'A','start':-0.1,'end':0.05,'global_start':-0.1,'global_end':0.05,'text':'outside before'},
          {'source_stem':'A','start':0.01,'end':0.08,'global_start':0.01,'global_end':0.08,'text':'欢迎来到直播间'},
          {'source_stem':'A','start':0.1,'end':0.4,'global_start':0.1,'global_end':0.4,'text':'inside fabric'},
          {'source_stem':'A','start':0.45,'end':0.6,'global_start':0.45,'global_end':0.6,'text':'outside after'}]},'index_subtitles.py')
        [output]=self.build_requests(self.paths,[1]);request=load_checked_json(self.paths,output,product_id=1)
        self.assertEqual(request['product_range']['start'],0.0)
        self.assertEqual([cue['text'] for cue in request['candidates'][0]['subtitle']],['inside fabric'])
        self.assertEqual([row['text'] for row in request['local_forbidden_cues']],['欢迎来到直播间'])

    def test_failed_initial_publication_leaves_invalidation_marker(self):
        self.first_publish();replace=os.replace
        def fail(source,target):
            if Path(target)==metadata_path(self.output):raise OSError('synthetic publication failure')
            return replace(source,target)
        with patch.object(publication.os,'replace',side_effect=fail),self.assertRaises(OSError):
            self.publish_or_preflight(self.paths,self.manifest_path,publish=True)
        self.assertTrue(invalidation_path(self.output).exists())
        with self.assertRaisesRegex(CacheIdentityError,'publication_incomplete'):
            load_checked_json(self.paths,self.output)


if __name__=='__main__':unittest.main()
