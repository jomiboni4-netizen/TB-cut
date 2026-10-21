import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from coldstart_resolve import (assert_new_name, check_readback, validate_physical_overlap,
                               infer_append_endpoint, append_endpoint, verify_source_fingerprints,
                               variant_timeline_name)
from coldstart_resolve import verify_visual_provenance
from project_registry import atomic_json


class NativeReadbackTests(unittest.TestCase):
    def setUp(self):
        self.clips=[{'source_file':'raw.mp4','source_start_frame':100,'source_end_frame':357,'record_duration_frames':257},
                    {'source_file':'raw.mp4','source_start_frame':500,'source_end_frame':750,'record_duration_frames':250}]
        self.sources={'raw.mp4':'/private/tmp/raw.mp4'}
        self.actual={'start':0,'end':507,'rows':[]}
        for kind in ('video','audio'):
            for lo,hi,left,right in [(100,357,0,257),(500,750,257,507)]:
                self.actual['rows'].append({'kind':kind,'media_path':'/private/tmp/raw.mp4','source_start':lo,'source_end_raw':hi-1,
                                           'record_start':left,'record_end':right,'duration':right-left})

    def test_exact_inclusive_source_end_readback(self):
        r=check_readback(self.actual,self.clips,self.sources,0)
        self.assertEqual(r['status'],'PASS');self.assertEqual(r['total_frames'],507)

    def test_installed_exclusive_append_protocol_keeps_last_frame(self):
        protocol=infer_append_endpoint(15854,17205,1351)
        self.assertEqual(protocol,'exclusive')
        self.assertEqual(append_endpoint(17206,protocol),17206)
        self.assertEqual(append_endpoint(17206,'inclusive'),17205)
        with self.assertRaisesRegex(ValueError,'unproven'):infer_append_endpoint(100,200,90)

    def test_black_frame_and_audio_source_mismatch_are_blocked(self):
        a=copy.deepcopy(self.actual);a['rows'][1]['record_start']=258
        with self.assertRaisesRegex(ValueError,'gap'):check_readback(a,self.clips,self.sources,0)
        a=copy.deepcopy(self.actual);a['rows'][2]['source_start']=101
        with self.assertRaisesRegex(ValueError,'source_start'):check_readback(a,self.clips,self.sources,0)

    def test_prejoined_media_cannot_be_used(self):
        a=copy.deepcopy(self.actual);a['rows'][0]['media_path']='/private/tmp/finished.mp4'
        with self.assertRaisesRegex(ValueError,'not_original_media'):check_readback(a,self.clips,self.sources,0)

    def test_no_existing_name_is_overwritten(self):
        with self.assertRaisesRegex(ValueError,'no_overwrite'):assert_new_name('existing',{'existing'})

    def test_timeline_name_uses_variant_product_not_first_batch_product(self):
        variant={'product_index':11,'version':'v1','variant_id':'p11_v1'}
        self.assertEqual(variant_timeline_name('20261015_run',variant),
                         'TBcut_20261015_run_11_v1_审核_人工听感待验')
        self.assertEqual(variant_timeline_name('20261015_run',variant,preview=True),
                         'TBcut_20261015_run_11_v1_临时试听_人物门禁待验_声学待验')
        with self.assertRaisesRegex(ValueError,'product_identity_missing'):
            variant_timeline_name('20261015_run',{'variant_id':'v1'})

    def test_cross_variant_physical_overlap_rejected(self):
        with self.assertRaisesRegex(ValueError,'physical_source_frame_overlap'):
            validate_physical_overlap([{'clips':[self.clips[0]]},{'clips':[dict(self.clips[0],source_start_frame=356)]}])
        validate_physical_overlap([{'clips':[self.clips[0]]},{'clips':[dict(self.clips[1],source_start_frame=357)]}])

    def test_source_replaced_after_run_init_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            media=Path(root)/'raw.mp4';media.write_bytes(b'original')
            stat=media.stat()
            run={'inputs':[{'path':str(media),'bytes':stat.st_size,'mtime_ns':stat.st_mtime_ns,
                            'sha256':hashlib.sha256(media.read_bytes()).hexdigest()}]}
            state={'video_paths':[str(media)]}
            verify_source_fingerprints(run,state,self.clips)
            media.write_bytes(b'replaced')
            with self.assertRaisesRegex(ValueError,'source_changed_since_run_init'):
                verify_source_fingerprints(run,state,self.clips)
            media.write_bytes(b'changed!')
            os.utime(media,ns=(stat.st_atime_ns,stat.st_mtime_ns))
            with self.assertRaisesRegex(ValueError,'source_changed_since_run_init'):
                verify_source_fingerprints(run,state,self.clips)

    def test_visual_status_must_match_sealed_audit(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'v5_person_audit.json'
            audit={'contract':'person-subject-record-strata-v5','variants':[{
                'variant_id':'v1','status':'UNCERTAIN','host_visible_ratio_lower_bound':.69,
                'host_visible_ratio_estimate':.86,'samples':[{}],'evidence_level':'ENGINEERING_SAMPLED'}]}
            atomic_json(path,audit)
            visual={'audit_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                    'contract':audit['contract'],'quality_threshold_exclusive':.70,
                    'strict_framewise_certified':False,'variant_results':[{
                        'variant_id':'v1','status':'PASS','visible_ratio_lower_bound':.81,
                        'visible_ratio_estimate':.86,'sample_count':1,'evidence_level':'ENGINEERING_SAMPLED'}]}
            paths=SimpleNamespace(cache_dir=Path(root))
            with patch('coldstart_resolve.verify_person_audit') as verify:
                with self.assertRaisesRegex(ValueError,'not_derived_from_sealed'):
                    verify_visual_provenance(paths,{},visual)
                verify.assert_called_once()
                visual['variant_results'][0].update(status='UNCERTAIN',visible_ratio_lower_bound=.69)
                verify_visual_provenance(paths,{},visual)


if __name__=='__main__':unittest.main()
