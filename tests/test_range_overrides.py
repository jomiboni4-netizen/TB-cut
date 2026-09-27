"""Authenticated overrides: synthetic inputs only, never the real workspace."""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_index_titles as fixtures
import apply_range_overrides_v2 as overrides
import runtime_publication as publication
from runtime_meta import CacheIdentityError, invalidation_path, load_checked_json, metadata_path, write_metadata


class RangeOverridesTests(unittest.TestCase):
    def setUp(self):
        fixtures.IndexTitlesTests.setUp(self)
        self.paths.cache_dir.mkdir()
        self.output = self.paths.cache_dir / 'product_ranges.json'
        self.override = self.paths.cache_dir / 'synthetic_range_overrides.json'
        self.index = {'schema_version': 1, 'generator': 'index_subtitles.py:v2', 'sources': [
            {'source_stem': 'A', 'global_start': 0, 'global_end': 200},
            {'source_stem': 'B', 'global_start': 200, 'global_end': 400}], 'cues': []}
        parsed = {'schema_version': 1, 'sheet': 'Synthetic', 'values': [self.rows[0], *[[f'id-{i}', f'Synthetic {i}'] for i in range(1,31)]]}
        self.titles = fixtures.titles.build_model(self.source, parsed, {})
        self.save('titles.json', self.titles, 'index_titles.py')
        self.save('subtitle_index.json', self.index, 'index_subtitles.py')
        self.document = {'schema_version': 1, 'products': [self.row(i) for i in range(1,21)]}
        self.save('product_ranges.json', self.document)
        self.save_overrides([self.change(1, 1, 9)])
        self.before = self.pair()

    def row(self, pid):
        start, end = (pid-1)*10, pid*10
        return {**self.titles['products'][pid-1], 'global_start': start, 'global_end': end,
                'ranges': overrides.split_range(start,end,self.index['sources'])}

    def change(self, pid, start, end):
        def point(t):
            return {'source_stem': 'A' if t<200 else 'B', 'time': t if t<200 else t-200}
        return {'product_index': pid, 'start': point(start), 'end': point(end), 'reason': 'Synthetic evidence'}

    def save(self, name, value, producer='synthetic-test'):
        path = self.paths.cache_dir / name
        path.write_text(json.dumps(value))
        write_metadata(self.paths,path,producer)

    def save_overrides(self, rows):
        self.save(self.override.name, {'products':rows})

    def pair(self):
        return self.output.read_bytes(),metadata_path(self.output).read_bytes()

    def run_override(self):
        return overrides.apply_overrides(self.paths,str(self.override))

    def checked(self):
        return load_checked_json(self.paths,self.output,artifact_schema_version=1)

    def rejected(self, rows, pattern):
        self.save_overrides(rows)
        with self.assertRaisesRegex(overrides.RangeOverrideError,pattern): self.run_override()
        self.assertEqual(self.before,self.pair())
        self.checked()

    def test_update_existing(self):
        self.run_override()
        result=self.checked()
        self.assertEqual(result['products'][0]['global_start'],1)
        self.assertEqual(result['products'][0]['encoding_id'],'id-1')
        self.assertEqual(json.loads(metadata_path(self.output).read_text())['producer'],'apply_range_overrides_v2.py')

    def test_add_missing_and_complete_thirty_sorted(self):
        self.save_overrides([self.change(i,(i-1)*10,i*10) for i in reversed(range(21,31))])
        self.run_override()
        result=self.checked()
        self.assertEqual([p['product_index'] for p in result['products']],list(range(1,31)))
        for row in result['products']:
            self.assertEqual(row['encoding_id'],self.titles['products'][row['product_index']-1]['encoding_id'])
            overrides.validate_range(row,overrides.source_map(self.index))

    def test_unknown_rejected(self): self.rejected([self.change(31,1,9)],'unknown_product_index')
    def test_duplicate_override_rejected(self): self.rejected([self.change(1,1,9)]*2,'duplicate_override')
    def test_identity_fields_cannot_be_forged(self):
        for key in ('title','encoding_id'):
            self.rejected([{**self.change(21,201,209),key:'forged'}],'identity_fields_forbidden')

    def test_invalid_start_end_rejected(self):
        for start,end in [(9,1),(1,1),(-1,9),(1,401),(True,9),(float('nan'),9),(1,float('inf'))]:
            with self.subTest(start=start,end=end):
                row=self.change(1,1,9)
                row['start']['time']=start; row['end']['time']=end
                self.rejected([row],'range_|override_time')

    def test_unknown_source_rejected(self):
        row=self.change(1,1,9); row['start']['source_stem']='missing'
        self.rejected([row],'source_unknown')

    def test_cross_source_range(self):
        self.save_overrides([self.change(20,195,205)])
        self.run_override()
        self.assertEqual(self.checked()['products'][-1]['ranges'],[
            {'media_stem':'A','start':195.0,'end':200.0},{'media_stem':'B','start':0.0,'end':5.0}])

    def test_duplicate_existing_rejected(self):
        doc=copy.deepcopy(self.document); doc['products'].append(doc['products'][0])
        self.save(self.output.name,doc); self.before=self.pair()
        self.rejected([self.change(1,1,9)],'duplicate_product_index')

    def test_unmodified_invalid_range_rejected(self):
        doc=copy.deepcopy(self.document); doc['products'][1]['ranges'][0]['media_stem']='unknown'
        self.save(self.output.name,doc); self.before=self.pair()
        self.rejected([self.change(1,1,9)],'source_unknown')

    def test_override_requires_current_identity(self):
        meta=metadata_path(self.override); value=json.loads(meta.read_text()); value['rules_fingerprint']='stale'; meta.write_text(json.dumps(value))
        with self.assertRaises(CacheIdentityError): self.run_override()
        self.assertEqual(self.before,self.pair())

    def test_titles_provenance_required(self):
        self.save('titles.json',self.titles,'legacy-attestation')
        with self.assertRaises(CacheIdentityError): self.run_override()
        self.assertEqual(self.before,self.pair())

    def test_input_state_rules_changes_fail_before_publication(self):
        paths=[self.root/'input.srt',self.root/'input.mp4',self.source,self.paths.state_path,
               self.repo/'PROJECT_RULES.md',self.repo/'config/content_rules.json']
        for path in paths:
            with self.subTest(input=path.name):
                original=path.read_bytes(); stat=path.stat(); build=overrides.build_model
                def changing(*args):
                    result=build(*args); path.write_bytes(original+b' '); return result
                try:
                    with patch.object(overrides,'build_model',side_effect=changing),self.assertRaises(CacheIdentityError): self.run_override()
                    self.assertEqual(self.before,self.pair())
                    self.assertFalse(invalidation_path(self.output).exists())
                finally:
                    path.write_bytes(original); os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))

    def test_authenticated_cache_changes_fail_before_publication(self):
        for name in ['titles.json','subtitle_index.json',self.override.name,self.output.name]:
            path=self.paths.cache_dir/name; original=path.read_bytes(); meta=metadata_path(path); original_meta=meta.read_bytes()
            build=overrides.build_model
            def changing(*args):
                result=build(*args); path.write_bytes(original+b' '); return result
            try:
                with patch.object(overrides,'build_model',side_effect=changing),self.assertRaises(CacheIdentityError): self.run_override()
                self.assertFalse(invalidation_path(self.output).exists())
                if path!=self.output: self.assertEqual(self.before,self.pair())
            finally: path.write_bytes(original); meta.write_bytes(original_meta)

    def test_second_rename_failure_restores_pair_but_invalidates(self):
        replace=os.replace
        def fail(source,target):
            if Path(target)==metadata_path(self.output): raise OSError('original publication error')
            return replace(source,target)
        with patch.object(publication.os,'replace',side_effect=fail),self.assertRaisesRegex(OSError,'original publication error'): self.run_override()
        self.assertEqual(self.before,self.pair())
        self.assertTrue(invalidation_path(self.output).exists())
        with self.assertRaises(CacheIdentityError): self.checked()

    def test_rollback_failure_preserves_original_error_and_invalidates(self):
        replace=os.replace
        def fail(source,target):
            if Path(target)==metadata_path(self.output): raise OSError('original publication error')
            if str(source).endswith('.previous'): raise OSError('rollback error')
            return replace(source,target)
        with patch.object(publication.os,'replace',side_effect=fail),self.assertRaisesRegex(OSError,'original publication error'): self.run_override()
        self.assertTrue(invalidation_path(self.output).exists())
        with self.assertRaises(CacheIdentityError): self.checked()

    def test_change_between_renames_rolls_back_and_invalidates(self):
        replace=os.replace
        def changing(source,target):
            result=replace(source,target)
            if Path(target)==self.output: (self.repo/'PROJECT_RULES.md').write_text('Changed synthetic rules')
            return result
        with patch.object(publication.os,'replace',side_effect=changing),self.assertRaises(CacheIdentityError): self.run_override()
        self.assertEqual(self.before,self.pair())
        self.assertTrue(invalidation_path(self.output).exists())

    def test_metadata_failure_leaves_original_valid(self):
        with patch.object(overrides,'write_metadata',side_effect=OSError('metadata error')),self.assertRaises(OSError): self.run_override()
        self.assertEqual(self.before,self.pair()); self.checked()

    def test_concurrent_generator_rejected(self):
        with publication.generator_lock(self.paths),self.assertRaises(BlockingIOError): self.run_override()
        self.assertEqual(self.before,self.pair()); self.checked()


if __name__=='__main__': unittest.main()
