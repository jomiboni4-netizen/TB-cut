import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from coldstart_resolve import assert_new_name, check_readback, validate_physical_overlap, infer_append_endpoint, append_endpoint


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

    def test_cross_variant_physical_overlap_rejected(self):
        with self.assertRaisesRegex(ValueError,'physical_source_frame_overlap'):
            validate_physical_overlap([{'clips':[self.clips[0]]},{'clips':[dict(self.clips[0],source_start_frame=356)]}])
        validate_physical_overlap([{'clips':[self.clips[0]]},{'clips':[dict(self.clips[1],source_start_frame=357)]}])


if __name__=='__main__':unittest.main()
