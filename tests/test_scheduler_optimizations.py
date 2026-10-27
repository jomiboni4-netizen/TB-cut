"""Synthetic scheduler, subtitle snapshots and request coordination; no services."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import adaptive_vision as vision
import project_scheduler as scheduler
from project_registry import digest
from runtime_meta import CacheIdentityError
from resource_limits import request_slot
from semantic_snapshot import assemble, verify
from stage_graph import descendants, EXECUTION_BARRIERS
from stage_supervision import heartbeat


class SnapshotTests(unittest.TestCase):
    def task(self):
        task = {'role': 'selection', 'candidate_digest': 'synthetic', 'candidates': [{
            'candidate_id': 'a', 'source_file': 'synthetic.mp4', 'subtitle': [
                {'start': 0, 'end': 1, 'text': '原先看到的前文', 'context_only': True},
                {'start': 1, 'end': 3, 'text': '原先看到的卖点'},
                {'start': 3, 'end': 4, 'text': '原先看到的后文', 'context_only': True}]}]}
        task['task_digest'] = digest(task)
        return task

    def test_snapshot_is_exact_first_selection_text_even_when_later_input_differs(self):
        task = self.task()
        selected = [{'candidate_id': 'a', 'variant_id': 'variant_1', 'start': 1, 'end': 3}]
        rough = {'selection_task': copy.deepcopy(task), 'selected_ranges': selected,
                 'semantic_snapshot': assemble(task, selected)}
        task['candidates'][0]['subtitle'][1]['text'] = '后来重新生成的字幕'
        frozen = verify(rough)
        self.assertEqual(frozen['source_candidates'][0]['subtitle'], rough['selection_task']['candidates'][0]['subtitle'])
        clip = frozen['variants'][0]['clips'][0]
        self.assertEqual(clip['source_subtitle_text'], '原先看到的卖点')
        self.assertEqual(clip['previous_source_context'], '原先看到的前文')
        self.assertEqual(clip['following_source_context'], '原先看到的后文')
        self.assertEqual(verify(rough), verify(rough))

    def test_snapshot_or_selection_tamper_and_legacy_rough_are_rejected(self):
        task = self.task()
        selected = [{'candidate_id': 'a', 'variant_id': 'variant_1', 'start': 1, 'end': 3}]
        rough = {'selection_task': task, 'selected_ranges': selected,
                 'semantic_snapshot': assemble(task, selected)}
        for change in ('snapshot', 'selection', 'task', 'legacy'):
            altered = copy.deepcopy(rough)
            if change == 'snapshot':
                altered['semantic_snapshot']['variants'][0]['clips'][0]['transcript'] = '被替换'
            elif change == 'selection':
                altered['selected_ranges'][0]['end'] = 2
            elif change == 'task':
                altered['selection_task']['candidates'][0]['subtitle'][1]['text'] = '被替换'
            else:
                del altered['semantic_snapshot']
            with self.subTest(change=change), self.assertRaises(CacheIdentityError):
                verify(altered)


class SchedulerScopeTests(unittest.TestCase):
    def test_subset_written_count_cannot_borrow_another_products_record(self):
        paths = SimpleNamespace(load_state=lambda: {'batch': {}})
        for selected_status in ('FINAL_PASS', 'WRITTEN', 'EXPORT_VERIFIED'):
            rows = {1: {'status': selected_status}, 2: {'status': 'WRITTEN'}}
            with self.subTest(status=selected_status), \
                 patch.object(scheduler, 'targets', return_value=[{'product_id': 1}]), \
                 patch.object(scheduler, 'resume', return_value=rows), \
                 patch.object(scheduler, 'approved_product', return_value={'product_index': 1, 'plans': []}):
                value = scheduler.project_audit(paths, [1])
            self.assertEqual(value['written_count'], int(selected_status != 'FINAL_PASS'))
            self.assertEqual(value['export_verified_count'], int(selected_status == 'EXPORT_VERIFIED'))
            self.assertEqual(value['delivery_status'], 'READY_FOR_DELIVERY' if selected_status == 'FINAL_PASS'
                             else 'READBACK_VERIFIED')

    def test_invalidation_graph_and_asr_execution_barriers(self):
        self.assertIn('asr', descendants('rough'))
        self.assertIn('vision', descendants('asr'))
        self.assertNotIn('rough', descendants('asr'))
        self.assertEqual(descendants('selection_vision'), ())
        self.assertEqual(EXECUTION_BARRIERS['asr'], ('semantic_review', 'selection_vision'))


class FrameIdentityTests(unittest.TestCase):
    def test_one_stratum_has_independent_frame_results_and_duplicate_work_is_shared(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = SimpleNamespace(registered_scope=lambda: ('synthetic-project', 'batch'))
            records = [dict(variant_id='v1', sample_kind='refinement', stratum_index=0,
                            source_file='synthetic', frame=frame, clip={'fps': 30}) for frame in (3, 6, 3)]
            records[2]['variant_id'] = 'v2'
            expected_labels = {3: 'NO_PERSON', 6: 'HOST_VISIBLE'}
            def classify(_media, frame, _detector, image, **_kwargs):
                Path(image).write_bytes(b'synthetic')
                label = expected_labels[frame]
                return {'label': label, 'subject_evidence': label != 'NO_PERSON', 'reason': 'synthetic'}
            with patch('compute_store.cached_value', return_value=(None, False)), \
                 patch('compute_store.cached', side_effect=lambda p, e, producer: (producer(), False)), \
                 patch.object(vision, 'recovery_controller', return_value=None), \
                 patch.object(vision, 'classify_apple_frame', side_effect=classify) as mocked, \
                 patch.object(vision, 'compose_contact_sheet', side_effect=lambda p, imgs, out: Path(out)), \
                 patch.object(vision, 'classify_with_codex_batch', return_value=[{
                     'label': 'NO_PERSON', 'subject_evidence': False, 'reason': 'synthetic'}]):
                values = {}
                pending = vision.collect_apple_frames(paths, records, 'detector', {'final_absence_review': {}},
                    {'synthetic': Path(folder)/'video'}, {'synthetic': {'sha256': 'synthetic'}}, folder, values)
                vision.review_pending_frames(paths, pending, {'final_absence_review': {}}, folder, values)
            self.assertEqual(mocked.call_count, 2)
            self.assertEqual(len(values), 3)
            self.assertEqual(values[vision.sample_result_key(records[2])][0]['label'], 'NO_PERSON')
            self.assertEqual(values[vision.sample_result_key(records[0])][0]['label'], 'NO_PERSON')
            self.assertEqual(values[vision.sample_result_key(records[1])][0]['label'], 'HOST_VISIBLE')


class CoordinationTests(unittest.TestCase):
    def test_shared_slots_limit_parallel_requests_and_release_after_error(self):
        with tempfile.TemporaryDirectory() as folder:
            mutex = threading.Lock()
            active = peak = 0
            def request(_):
                nonlocal active, peak
                with request_slot(folder, 2, limit=2):
                    with mutex:
                        active += 1
                        peak = max(peak, active)
                    time.sleep(.02)
                    with mutex:
                        active -= 1
            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(request, range(12)))
            self.assertEqual(peak, 2)
            with self.assertRaises(RuntimeError):
                with request_slot(folder, 1, limit=1):
                    raise RuntimeError('synthetic failure')
            with request_slot(folder, 1, limit=1):
                pass

    def test_queue_timeout_stays_missing_evidence(self):
        from vision_recovery import technical_failure
        with tempfile.TemporaryDirectory() as folder, request_slot(folder, 1, limit=1):
            with self.assertRaisesRegex(CacheIdentityError, 'queue_timeout'):
                with request_slot(folder, .01, limit=1):
                    self.fail('unavailable slot acquired')
        self.assertTrue(technical_failure({'label': 'DETECTOR_ERROR', 'reason': 'vision_request_queue_timeout'}))

    def test_heartbeat_updates_while_callback_runs_and_stops_on_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'heartbeat.json'
            event = {'event_id': 'synthetic-event', 'stage': 'synthetic-stage'}
            with heartbeat(path, event, interval=.005):
                initial = path.read_bytes()
                time.sleep(.03)
                self.assertNotEqual(path.read_bytes(), initial)
            final = path.read_bytes()
            time.sleep(.015)
            self.assertEqual(path.read_bytes(), final)
            self.assertEqual(json.loads(final)['event_id'], 'synthetic-event')


class SemanticOrderIntegrationTests(unittest.TestCase):
    import test_project_scheduler as scheduler_fixtures
    import test_project_resume as fixtures
    setUp = scheduler_fixtures.SchedulerTests.setUp
    setUpBase = fixtures.ProjectResumeTests.setUp
    register = fixtures.ProjectResumeTests.register
    engine = scheduler_fixtures.SchedulerTests.engine

    def test_semantic_review_immediately_follows_selection_before_prescreen_and_asr(self):
        import codex_worker
        from final_quality_gate import semantic_selection_task
        from product_checkpoints import resume, load_stage
        events, reviewed = [], []
        visual, asr = scheduler.selection_visual_audit, scheduler.local_asr
        with self.engine():
            original_launch = codex_worker.launch.side_effect
            def launch(task):
                events.append(task['role'])
                if task['role'] == 'final_review':
                    reviewed.append(copy.deepcopy(task))
                return original_launch(task)
            def prescreen(*args, **kwargs):
                events.append('prescreen')
                return visual(*args, **kwargs)
            def execute_asr(*args, **kwargs):
                events.append('asr')
                return asr(*args, **kwargs)
            with patch.object(codex_worker, 'launch', side_effect=launch), \
                 patch.object(scheduler, 'selection_visual_audit', side_effect=prescreen), \
                 patch.object(scheduler, 'local_asr', side_effect=execute_asr):
                result = scheduler.process_product(self.paths, {'product_id': 1, 'encoding_id': '001'},
                                                   mode='codex', detector=self.detector)
            self.assertEqual(result['status'], 'FINAL_PASS', result)
            self.assertEqual(events[:4], ['selection', 'final_review', 'prescreen', 'asr'])
            self.assertEqual(len(reviewed), 1)
            plan = load_stage(self.paths, resume(self.paths)[1]['plan_ref'])
            rough_ref = next(dep['path'] for dep in plan['identity']['dependencies']
                             if load_stage(self.paths, dep['path'])['identity']['stage'] == 'rough')
            self.assertEqual(semantic_selection_task(self.paths, rough_ref), reviewed[0])


class CurrentCutpointVetoTests(unittest.TestCase):
    import test_project_scheduler as scheduler_fixtures
    import test_project_resume as fixtures
    setUp = scheduler_fixtures.SchedulerTests.setUp
    setUpBase = fixtures.ProjectResumeTests.setUp
    register = fixtures.ProjectResumeTests.register
    engine = scheduler_fixtures.SchedulerTests.engine

    def test_changed_physical_cut_still_cannot_publish_a_final_pass(self):
        from product_checkpoints import resume, load_stage, capture_stage, publish_stage
        from final_quality_gate import plan_errors
        from quality_fixtures import audit_fixture
        with self.engine():
            result = scheduler.process_product(self.paths, {'product_id': 1, 'encoding_id': '001'},
                                               mode='codex', detector=self.detector)
            self.assertEqual(result['status'], 'FINAL_PASS', result)
        row = resume(self.paths)[1]
        record = load_stage(self.paths, row['plan_ref'])
        plan = copy.deepcopy(record['body'])
        first = plan['variants'][0]['clips'][0]
        second = copy.deepcopy(first)
        first.update(source_end_frame=750, audio_end_frame=750)
        second.update(source_start_frame=751, audio_start_frame=751)
        plan['variants'][0]['clips'] = [first, second]
        args = dict(product_id=1, encoding_id='001', stage='plan', version='synthetic-changed-cut',
                    model_version='synthetic', dependencies=[d['path'] for d in record['identity']['dependencies']])
        changed = publish_stage(self.paths, **args, body=plan, snapshot=capture_stage(self.paths, **args))
        self.assertIn('CUTPOINT_PROOF_MISSING_OR_STALE', plan_errors(plan))
        with self.assertRaisesRegex(CacheIdentityError, 'local_final_veto'):
            audit_fixture(self.paths, changed)
        self.assertEqual(resume(self.paths)[1]['status'], 'BLOCKED')


class PartialResumeMetadataTests(unittest.TestCase):
    def test_completed_subset_cache_does_not_hide_remaining_versions(self):
        from contextlib import nullcontext
        pending = [{'variant_id': 'variant_2', 'status': 'WAITING_EVIDENCE'}]
        row = {'status': 'WRITTEN', 'product_complete': False,
               'delivery_scope': {'pending_variants': pending}}
        paths = SimpleNamespace()
        with patch.object(scheduler, 'product_lease', return_value=nullcontext()), \
             patch.object(scheduler, 'resume', return_value={1: row}), \
             patch.object(scheduler, 'read_ledger', return_value={'products': {'1': row}}), \
             patch.object(scheduler, 'approved_product'), patch.object(scheduler, 'save_product_metrics'), \
             patch.object(scheduler, 'selection_task') as selection:
            value = scheduler.process_product(paths, {'product_id': 1, 'encoding_id': '001'})
        selection.assert_not_called()
        self.assertEqual(value['status'], 'WRITTEN')
        self.assertFalse(value['product_complete'])
        self.assertEqual(value['pending_variants'], pending)
        self.assertTrue(value['pending_recovery_requires_separate_dispatch'])


class LosslessSelectionTransportTests(unittest.TestCase):
    def test_large_task_keeps_every_verbatim_cue_and_reconstructs_removed_full_text(self):
        from semantic_snapshot import compact_selection_task
        cues = [{'start': i, 'end': i+1, 'text': '合成字幕保持原字与时间', 'context_only': False}
                for i in range(1100)]
        candidate = {'candidate_id': 'synthetic', 'source_file': 'synthetic.mp4',
                     'subtitle': cues, 'asr_text': ''.join(c['text'] for c in cues),
                     'time_range': {'start': 0, 'end': 1100}}
        task = {'role': 'selection', 'candidates': [candidate]}
        original = copy.deepcopy(task)
        compact = compact_selection_task(task)
        self.assertEqual(task, original)
        self.assertLess(len(json.dumps(compact, ensure_ascii=False).encode()), 128000)
        actual = compact['candidates'][0]
        restored = [{**cue, 'context_only': cue.get('context_only', False)} for cue in actual['subtitle']]
        self.assertEqual(restored, cues)
        self.assertEqual(''.join(c['text'] for c in actual['subtitle']), candidate['asr_text'])
        self.assertEqual(actual['time_range'], candidate['time_range'])

    def test_nonredundant_text_and_true_context_flags_are_never_discarded(self):
        from semantic_snapshot import compact_selection_task
        task = {'role': 'selection', 'candidates': [{'candidate_id': 'synthetic', 'subtitle': [
            {'start': 1, 'end': 2, 'text': '合成原文', 'context_only': True}], 'asr_text': '必须保留的不同证据'}]}
        compact = compact_selection_task(task, target_bytes=1)
        self.assertEqual(compact['candidates'][0], task['candidates'][0])
