import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import project_scheduler as scheduler
import codex_worker
import test_project_scheduler as fixtures
import test_project_resume as resume_fixtures
from quality_fixtures import synthetic_vision


class RecoverySchedulerTests(unittest.TestCase):
    setUp = fixtures.SchedulerTests.setUp
    setUpBase = resume_fixtures.ProjectResumeTests.setUp
    register = fixtures.SchedulerTests.register
    engine = fixtures.SchedulerTests.engine

    def absent(self):
        return {'label': 'NO_PERSON', 'subject_evidence': False, 'reason': 'synthetic absence'}

    def test_timeout_waits_for_evidence_without_semantic_reselection_or_asr(self):
        import adaptive_vision
        with self.engine(), synthetic_vision(30, self.absent), \
                patch.object(adaptive_vision, 'classify_with_codex_batch',
                             side_effect=subprocess.TimeoutExpired('synthetic', 60)), \
                patch.object(scheduler, 'local_asr', side_effect=AssertionError('ASR must be preserved')):
            result = scheduler.process_product(self.paths, {'product_id': 1, 'encoding_id': '001'},
                                               mode='codex', detector=self.detector, repair_limit=1)
        self.assertEqual(result['status'], 'WAITING_EVIDENCE', result)
        self.assertLessEqual(result['recovery']['requests_used'], 6)
        self.assertEqual(len([c for c in self.calls if c['role'] == 'selection']), 1)
        cursor = json.loads((self.paths.project_records_dir/'cursors/1.json').read_text())
        self.assertEqual(cursor.get('repair_attempts', 0), 0)
        self.assertEqual(cursor['evidence_recovery_request']['stage'], 'selection_vision')

    def test_confirmed_absence_sends_precise_ranges_to_semantic_repair(self):
        tasks = []
        with self.engine(), synthetic_vision(30, self.absent):
            original = codex_worker.launch.side_effect
            def launch(task):
                if task['role'] == 'selection':
                    tasks.append(task)
                return original(task)
            with patch.object(codex_worker, 'launch', side_effect=launch), \
                    patch.object(scheduler, 'local_asr', side_effect=AssertionError('prescreen failed')):
                result = scheduler.process_product(self.paths, {'product_id': 1, 'encoding_id': '001'},
                                                   mode='codex', detector=self.detector, repair_limit=1)
        self.assertEqual(len(tasks), 2, result)
        self.assertEqual(tasks[1]['repairable_variant_ids'], ['variant_1'])
        self.assertTrue(tasks[1]['visual_exclusion_ranges'])
        self.assertTrue(tasks[1]['visual_repair_samples'])
        self.assertNotEqual(result['status'], 'WAITING_EVIDENCE')

    def test_waiting_version_does_not_stop_other_authorized_products(self):
        called = []
        def process(_paths, product, **_kwargs):
            called.append(product['product_id'])
            return {'product_id': product['product_id'],
                    'status': 'WAITING_EVIDENCE' if product['product_id'] == 1 else 'FINAL_PASS'}
        rows = {i: {'status': 'WAITING_EVIDENCE' if i == 1 else 'FINAL_PASS'} for i in range(1, 13)}
        with patch.object(scheduler, 'process_product', side_effect=process), \
                patch.object(scheduler, 'resume', return_value=rows), \
                patch.object(scheduler, 'project_audit', return_value={
                    'final_review_complete': True, 'all_variants_complete': True, 'errors': []}):
            result = scheduler.run_project(self.paths, mode='codex', product_workers=1)
        self.assertEqual(called, list(range(1, 13)))
        self.assertEqual(result['pending_product_ids'], [1])
        self.assertFalse(result['all_variants_complete'])


if __name__ == '__main__':
    unittest.main()
