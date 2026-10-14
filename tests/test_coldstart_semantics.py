import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from coldstart_semantics import validate_unit, frame_append_spec, validate_selection, stage_b_payload


class ColdStartContractTests(unittest.TestCase):
    def setUp(self):
        self.rows=[{'source_id':f'q{i}','source_stem':'raw','ordinal':i,'start':i*2.,'end':i*2.+2,'text':t}
                   for i,t in enumerate(['这件夹克用羊毛。','因为纤维细，','所以穿着柔软。'])]
        self.paragraph={'paragraph_id':'run:p01','source_alias':'src01','rows':self.rows}
        self.run={'project_key':'project-identity','run_id':'new-run'}
        self.unit={'source_ids':['q0','q1','q2'],'text':''.join(r['text'] for r in self.rows),'product_attribution':'MATCH',
                   'continuous_clip_recommended':True,'complete_semantics':'夹克材料及因果完整'}

    def test_phrase_splicing_and_reordering_are_rejected(self):
        for ids in (['q0','q2'],['q2','q1','q0']):
            with self.assertRaisesRegex(ValueError,'phrase_splice'):
                validate_unit(dict(self.unit,source_ids=ids),self.paragraph,self.run,1)

    def test_unverified_or_old_source_cannot_enter(self):
        with self.assertRaisesRegex(ValueError,'unknown'):
            validate_unit(dict(self.unit,source_ids=['old-run:q1']),self.paragraph,self.run,1)
        with self.assertRaisesRegex(ValueError,'product_not_confirmed'):
            validate_unit(dict(self.unit,product_attribution='UNCERTAIN'),self.paragraph,self.run,1)

    def test_missing_semantic_evidence_rejected(self):
        with self.assertRaisesRegex(ValueError,'complete_semantics_missing'):
            validate_unit(dict(self.unit,complete_semantics=''),self.paragraph,self.run,1)

    def test_cross_window_group_ids_do_not_collide(self):
        a=validate_unit(self.unit,self.paragraph,self.run,1)
        b=validate_unit(self.unit,self.paragraph,dict(self.run,run_id='another-run'),1)
        self.assertNotEqual(a['unit_id'],b['unit_id'])

    def test_model_complete_label_does_not_bypass_local_review(self):
        source_verified=validate_unit(self.unit,self.paragraph,self.run,1)
        self.assertEqual(source_verified['local_status'],'PENDING_LOCAL_SEMANTIC_REVIEW')
        with self.assertRaisesRegex(ValueError,'without_local_KEEP'):
            validate_selection({'selected_variants':[{'unit_ids':[source_verified['unit_id']]}]},[source_verified],self.run)

    def test_B_unknown_units_cannot_be_restored(self):
        with self.assertRaisesRegex(ValueError,'unknown_or_unverified'):
            validate_selection({'selected_variants':[{'unit_ids':['historical:unit']} ]},[],self.run)

    def test_B_joint_three_target_and_underfill_requires_reason(self):
        unit=validate_unit(self.unit,self.paragraph,self.run,1)
        unit.update(local_status='KEEP',duration=50,category='material',claim='羊毛柔软',needs_context=[],local_semantic_review='KEEP')
        payload=stage_b_payload({'title':'测试商品'},[unit],self.run)
        self.assertEqual(payload['model'],'mimo-v2.6-pro')
        self.assertIn('"target_variant_count":3',payload['input'])
        self.assertIn('不得找到第一条合格方案就停止',payload['input'])
        selection={'selected_variants':[{'unit_ids':[unit['unit_id']],'focus':'材质'}]}
        with self.assertRaisesRegex(ValueError,'missing_reason_for_underfilled'):
            validate_selection(selection,[unit],self.run)
        self.assertEqual(len(validate_selection(dict(selection,insufficient_reason='其余内容不足'),[unit],self.run)),1)

    def test_inclusive_api_end_and_exclusive_record_do_not_make_black_frame(self):
        a=frame_append_spec(0,10.28,25,0)
        b=frame_append_spec(20,30,25,a['record_end_exclusive'])
        self.assertEqual(a['api_end_inclusive'],256)
        self.assertEqual(a['record_end_exclusive'],257)
        self.assertEqual(b['record_start'],257)
        self.assertEqual(b['record_end_exclusive'],507)


if __name__=='__main__':unittest.main()
