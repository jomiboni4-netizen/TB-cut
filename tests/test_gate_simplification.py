"""Isolated evidence graphs and mocked frame producers; no real services."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import test_project_resume as fixtures
import product_checkpoints as checkpoints
import stage_dependencies
from evidence_operation import verified_operation
from runtime_meta import CacheIdentityError


class GateSimplificationTests(unittest.TestCase):
    setUp = fixtures.ProjectResumeTests.setUp
    register = fixtures.ProjectResumeTests.register
    plan = fixtures.ProjectResumeTests.plan

    def test_unrelated_code_preserves_plan_related_code_invalidates_descendants(self):
        plan = self.plan()
        original = stage_dependencies._sha256
        def changed(path):
            return 'changed' if path.name == 'vision_record_score_v5.py' else original(path)
        with patch.object(stage_dependencies, '_sha256', side_effect=changed):
            checkpoints.load_stage(self.paths, plan)
        def changed_range(path):
            return 'changed' if path.name == 'publish_confirmed_ranges.py' else original(path)
        with patch.object(stage_dependencies, '_sha256', side_effect=changed_range):
            with self.assertRaisesRegex(CacheIdentityError, 'stage_context_stale'):
                checkpoints.load_stage(self.paths, plan)

    def test_old_namespace_is_not_recertified(self):
        plan = self.plan()
        path = self.paths.resolve_reference(plan).path
        value = json.loads(path.read_text())
        del value['identity']['store_contract']
        path.write_text(json.dumps(value))
        with self.assertRaises(CacheIdentityError):
            checkpoints.load_stage(self.paths, plan)

    def test_one_operation_reuses_parse_but_separate_operations_do_not(self):
        plan = self.plan()
        original = checkpoints.json.loads
        reads = []
        def parse(text, *args, **kwargs):
            value = original(text, *args, **kwargs)
            if isinstance(value, dict) and value.get('identity', {}).get('stage') == 'plan':
                reads.append(1)
            return value
        @verified_operation
        def read_twice(paths):
            first = checkpoints.load_stage(paths, plan)
            first['body']['variants'] = []
            self.assertTrue(checkpoints.load_stage(paths, plan)['body']['variants'])
        with patch.object(checkpoints.json, 'loads', side_effect=parse):
            read_twice(self.paths)
            self.assertEqual(len(reads), 1)
            read_twice(self.paths)
            self.assertEqual(len(reads), 2)

    def test_dependency_tamper_after_cached_child_is_rejected(self):
        plan = self.plan()
        @verified_operation
        def read_then_tamper(paths):
            value = checkpoints.load_stage(paths, plan)
            dep = paths.resolve_reference(value['identity']['dependencies'][0]['path']).path
            dep.write_text(dep.read_text() + ' ')
            checkpoints.load_stage(paths, plan)
        with self.assertRaisesRegex(CacheIdentityError, 'changed_during_verification'):
            read_then_tamper(self.paths)

    def test_rule_change_after_cached_read_is_rejected(self):
        plan = self.plan()
        @verified_operation
        def change(paths):
            checkpoints.load_stage(paths, plan)
            (self.repo / 'PROJECT_RULES.md').write_text('Changed synthetic hard rule')
            checkpoints.load_stage(paths, plan)
        with self.assertRaisesRegex(CacheIdentityError, 'context_changed'):
            change(self.paths)


    def test_identity_snapshot_is_shared_and_fully_rehashed_at_operation_exit(self):
        import runtime_meta
        plan = self.plan()
        @verified_operation
        def read_twice(paths):
            checkpoints.load_stage(paths, plan)
            checkpoints.load_stage(paths, plan)
        with patch.object(runtime_meta, 'expected_identity', wraps=runtime_meta.expected_identity) as identity:
            read_twice(self.paths)
            self.assertEqual(identity.call_count, 2)  # read snapshot + fresh exit snapshot
            read_twice(self.paths)
            self.assertEqual(identity.call_count, 4)  # no cross-operation reuse

    def test_same_size_same_timestamp_input_tamper_cannot_reuse_identity_snapshot(self):
        import os
        plan = self.plan()
        @verified_operation
        def tamper(paths):
            checkpoints.load_stage(paths, plan)
            before = self.srt.stat()
            raw = self.srt.read_bytes()
            self.srt.write_bytes(raw.replace(b'Synthetic', b'Tampered!'))
            os.utime(self.srt, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertEqual(self.srt.stat().st_size, before.st_size)
            checkpoints.load_stage(paths, plan)
        with self.assertRaisesRegex(CacheIdentityError, 'context_changed'):
            tamper(self.paths)


class AsrHistoryTests(unittest.TestCase):
    import test_project_scheduler as scheduler_fixtures
    setUp = scheduler_fixtures.SchedulerTests.setUp
    setUpBase = fixtures.ProjectResumeTests.setUp
    register = fixtures.ProjectResumeTests.register
    engine = scheduler_fixtures.SchedulerTests.engine

    def test_weight_change_preserves_written_fact_and_blocks_unwritten_approval(self):
        import registered_pipeline as pipeline
        import project_scheduler as scheduler
        from final_quality_gate import approved_product
        from resolve_write_v2 import record_written_checkpoints
        with self.engine():
            result = scheduler.process_product(self.paths, {'product_id': 1, 'encoding_id': '001'},
                                               mode='codex', detector=self.detector)
            self.assertEqual(result['status'], 'FINAL_PASS', result)
            row = checkpoints.resume(self.paths)[1]
            weights = pipeline.asr_model_identity('large-v3-turbo')
            weights = {**weights, 'weights_digest': 'changed-synthetic-weights'}
            with patch.object(pipeline, 'asr_model_identity', return_value=weights):
                self.assertEqual(checkpoints.resume(self.paths)[1]['status'], 'BLOCKED')
                with self.assertRaises(CacheIdentityError):
                    approved_product(self.paths, 1)
            record_written_checkpoints(self.paths, [1], {}, 'synthetic-readback', 'synthetic-target')
            ledger_before = checkpoints.checkpoint_path(self.paths).read_bytes()
            with patch.object(pipeline, 'asr_model_identity', return_value=weights):
                self.assertEqual(checkpoints.resume(self.paths)[1]['status'], 'WRITTEN')
                self.assertEqual(len(approved_product(self.paths, 1)['plans']), 1)
            self.assertEqual(checkpoints.checkpoint_path(self.paths).read_bytes(), ledger_before)
            with patch.object(pipeline, 'asr_model_identity', side_effect=RuntimeError('unavailable')):
                checkpoints.verify_evidence(self.paths, 1, '001', row['plan_ref'],
                                            row['evidence_ref'], 'FINAL_PASS')


if __name__ == '__main__':
    unittest.main()
