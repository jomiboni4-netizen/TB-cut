"""Real evidence/checkpoint consumers with synthetic decode and review boundaries."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import adaptive_vision
import codex_worker
import test_project_resume as fixtures
import test_project_scheduler as scheduler_fixtures
import project_scheduler as scheduler
import registered_pipeline as pipeline
from quality_fixtures import valid_plan, range_body, synthetic_vision, fake_outcome
from product_checkpoints import capture_stage, publish_stage, load_stage, read_ledger, update_checkpoint, resume
from project_registry import digest, atomic_json
from variant_delivery import retain_passed_variants, verify_projection
from final_quality_gate import final_task, import_final_review, approved_product
from update_batch_state import reconcile
from resolve_write_v2 import record_written_checkpoints
from runtime_meta import CacheIdentityError


class VariantDeliveryTests(unittest.TestCase):
    setUp = fixtures.ProjectResumeTests.setUp
    register = fixtures.ProjectResumeTests.register

    def publish(self, stage, body, dependencies=()):
        args = dict(product_id=1, encoding_id='001', stage=stage, version=digest(body),
                    model_version='synthetic', dependencies=dependencies)
        return publish_stage(self.paths, **args, body=body, snapshot=capture_stage(self.paths, **args))

    def original(self, all_failed=False):
        rr = self.publish('range', range_body(end=200))
        plan = {'variants': []}
        for i, text in enumerate(['轻便透气适合夏天户外活动', '袖口采用双层走线牢固耐穿', '腰部剪裁宽松弯腰活动自如']):
            v = valid_plan(offset=i * 55, text=text)['variants'][0]
            v['variant_id'] = f'variant_{i+1}'
            plan['variants'].append(v)
        pr = self.publish('plan', plan, [rr])
        detector = self.material / 'synthetic-detector'
        detector.write_text('synthetic detector')
        calls = [0]
        def label():
            calls[0] += 1
            failed = all_failed or calls[0] <= 10
            return {'label': 'DETECTOR_ERROR' if failed else 'HOST_VISIBLE',
                    'subject_evidence': not failed, 'reason': 'synthetic timeout' if failed else 'synthetic person'}
        with synthetic_vision(10, label):
            evidence = adaptive_vision.audit(self.paths, plan, {'001.mp4': self.video}, detector)
        vr = self.publish('vision', evidence, [pr])
        return pr, vr

    def test_passed_versions_approve_and_write_without_completing_product(self):
        pr, vr = self.original()
        original_bytes = self.paths.resolve_reference(pr).path.read_bytes()
        projected, vision = retain_passed_variants(self.paths, pr, vr)
        self.assertEqual(self.paths.resolve_reference(pr).path.read_bytes(), original_bytes)
        body = load_stage(self.paths, projected)['body']
        self.assertEqual([v['variant_id'] for v in body['variants']], ['variant_2', 'variant_3'])
        self.assertEqual(body['delivery_scope']['pending_variants'][0]['variant_id'], 'variant_1')
        self.assertFalse(body['delivery_scope']['product_complete'])
        with patch.object(codex_worker, 'launch', side_effect=fake_outcome):
            receipt = codex_worker.execute(self.paths, final_task(self.paths, projected))
        audit = import_final_review(self.paths, projected, vision, receipt)
        update_checkpoint(self.paths, product_id=1, encoding_id='001', status='FINAL_PASS',
                          plan_ref=projected, evidence_ref=audit, expected_revision=read_ledger(self.paths)['revision'])
        self.assertEqual(len(approved_product(self.paths, 1)['plans']), 2)
        reconcile(self.paths)
        self.assertEqual(self.paths.load_state()['batch']['completed_product_ids'], [])
        record_written_checkpoints(self.paths, [1], {}, 'synthetic-readback', 'synthetic-target')
        row = resume(self.paths)[1]
        self.assertEqual(row['variant_statuses']['variant_2'], 'WRITTEN')
        self.assertEqual(row['variant_statuses']['variant_1'], 'BLOCKED')
        self.assertFalse(row['product_complete'])
        ledger = read_ledger(self.paths)
        ledger['products']['1']['product_complete'] = True
        atomic_json(self.paths.project_records_dir / 'checkpoints.json', ledger)
        self.assertEqual(resume(self.paths)[1]['status'], 'BLOCKED')

    def test_projection_cannot_change_retained_clip_or_certify_failed_variant(self):
        pr, vr = self.original()
        projected, evidence = retain_passed_variants(self.paths, pr, vr)
        body = load_stage(self.paths, projected)['body']
        vision = load_stage(self.paths, evidence)['body']
        changed = copy.deepcopy(body)
        changed['variants'][0]['clips'][0]['source_start_frame'] += 1
        with self.assertRaisesRegex(CacheIdentityError, 'projection_content_changed'):
            verify_projection(self.paths, changed, vision)
        changed = copy.deepcopy(vision)
        changed['variants'][0]['samples'][0]['classification']['label'] = 'DETECTOR_ERROR'
        with self.assertRaisesRegex(CacheIdentityError, 'projection_content_changed'):
            verify_projection(self.paths, body, changed)

    def test_all_failed_variants_do_not_grant_delivery(self):
        pr, vr = self.original(all_failed=True)
        with self.assertRaisesRegex(CacheIdentityError, 'no_independently_passed_variants'):
            retain_passed_variants(self.paths, pr, vr)


class SchedulerVariantDeliveryTests(unittest.TestCase):
    setUp = scheduler_fixtures.SchedulerTests.setUp
    setUpBase = fixtures.ProjectResumeTests.setUp
    register = fixtures.ProjectResumeTests.register
    engine = scheduler_fixtures.SchedulerTests.engine

    def test_mixed_final_vision_delivers_two_versions_and_leaves_one_pending(self):
        manifest = {'products': [{'product_index': 1, 'decision_id': 'synthetic-extended',
                    'confirmed_by': 'synthetic reviewer', 'confirmed_at': '2026-10-26T00:00:00Z',
                    'start': {'source_stem': '001', 'time': 0},
                    'end': {'source_stem': '001', 'time': 300}}]}
        pipeline.confirm_range(self.paths, 1, manifest)
        def launch(task):
            if task['role'] != 'selection':
                return fake_outcome(task)
            candidates = task['candidates']
            source = candidates[0]
            ranges = [{'candidate_id': source['candidate_id'], 'variant_id': f'variant_{i+1}',
                       'start': i * 100, 'end': i * 100 + 50} for i in range(3)]
            result = fake_outcome(task, selected_ranges=ranges)
            result['result']['semantic_map'] = [
                {'candidate_id': c['candidate_id'], 'selling_point': 'Synthetic selling point',
                 'quote': c['asr_text']} for c in candidates]
            return result
        original = scheduler.vision_audit
        def mixed_final(*args, **kwargs):
            count = [0]
            def label():
                count[0] += 1
                failed = count[0] <= 5
                return {'label': 'DETECTOR_ERROR' if failed else 'HOST_VISIBLE',
                        'subject_evidence': not failed, 'reason': 'synthetic timeout' if failed else 'person'}
            with synthetic_vision(30, label):
                return original(*args, **kwargs)
        with self.engine(), patch.object(codex_worker, 'launch', side_effect=launch), \
                patch.object(scheduler, 'vision_audit', side_effect=mixed_final):
            result = scheduler.process_product(self.paths, {'product_id': 1, 'encoding_id': '001'},
                                               mode='codex', detector=self.detector, repair_limit=1)
            self.assertEqual(result['status'], 'FINAL_PASS', result)
            product = approved_product(self.paths, 1)
            self.assertEqual([v['variant_id'] for v in product['plans']], ['variant_2', 'variant_3'])
            self.assertFalse(product['product_complete'])
            audit = scheduler.project_audit(self.paths, product_ids=[1])
            self.assertTrue(audit['final_review_complete'])
            self.assertFalse(audit['all_variants_complete'])
            self.assertEqual(audit['pending_variants']['1'][0]['variant_id'], 'variant_1')


if __name__ == '__main__':
    unittest.main()
