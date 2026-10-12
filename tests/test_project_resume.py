"""Round 1 synthetic integration: real registration, generators, progress and consumers."""
import copy
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from init_project import initialize
from project_registry import CONFIG_NAME, ProjectIdentityError, atomic_json, workspace_lock
from runtime_paths import resolve_runtime_paths
from runtime_meta import CacheIdentityError, expected_identity, load_checked_json, metadata_path, validate_cache
from product_checkpoints import (capture_stage, context, load_stage, publish_stage as stage_publisher, read_ledger, resume,
                                 update_checkpoint)
from update_batch_state import reconcile
import index_subtitles
import index_titles
import runtime_publication
from test_index_titles import synthetic_xlsx
from refine_plans_v2 import main as refine_main
from resolve_write_v2 import load_products


def publish_stage(paths, **kwargs):
    snapshot = capture_stage(paths, **{k: v for k, v in kwargs.items() if k != 'body'})
    return stage_publisher(paths, **kwargs, snapshot=snapshot)


def competing_stage(repo, workspace, queue):
    try:
        paths = resolve_runtime_paths(repo, workspace)
        publish_stage(paths, product_id=1, encoding_id='001', stage='plan', version='v1',
                      model_version='synthetic', body={'clips': ['competing']})
        queue.put('unexpected_success')
    except (ProjectIdentityError, CacheIdentityError) as exc:
        queue.put(str(exc))


class ProjectResumeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        (self.repo / 'config').mkdir()
        (self.repo / 'PROJECT_RULES.md').write_text('Synthetic hard rules')
        (self.repo / 'config/rules.json').write_text('{}')
        (self.repo / 'PROJECT_STATE.template.json').write_text(json.dumps({
            'runtime': {'pipeline_version': '2.1'}, 'batch': {}, 'batch_overrides': {}}))
        self.material = self.base / 'material'
        self.material.mkdir()
        self.title = self.material / 'title.xlsx'
        self.rows = [['编码 ID', '标题'], ['001', 'Synthetic product']]
        synthetic_xlsx(self.title, self.rows)
        self.srt = self.material / '001.srt'
        self.srt.write_text('1\n00:00:00,000 --> 00:00:01,000\nSynthetic cue\n')
        self.video = self.material / '001.mp4'
        self.video.write_bytes(b'synthetic')
        self.paths = resolve_runtime_paths(self.repo, self.base / 'workspace')
        self.register(self.paths, 'resolve-uuid-a')

    def register(self, paths, identity):
        return initialize(paths, material_folder=self.material, resolve_project_id=identity,
                          title=self.title, subtitles=[str(self.srt)], videos=[str(self.video)],
                          resolve_project='same display name', source_timeline='source', target_timeline='target',
                          batch_id='batch-a', products=[1])

    def plan(self, paths=None, version='v1', body=None):
        paths = paths or self.paths
        source_range = publish_stage(paths, product_id=1, encoding_id='001', stage='range', version='range-v1',
                                     model_version='synthetic-boundary', body={'start': 0, 'end': 2})
        return publish_stage(paths, product_id=1, encoding_id='001', stage='plan',
                             version=version, model_version='synthetic-model-v1',
                             body=body or {'clips': [{'source': '001', 'start': 0, 'end': 1}]}, dependencies=[source_range])

    def audit(self, plan, paths=None, version='v1', scope='final_plan'):
        paths = paths or self.paths
        data = load_stage(paths, plan)
        return publish_stage(paths, product_id=1, encoding_id='001', stage='final_audit',
                             version=version, model_version='synthetic-auditor-v1',
                             body={'verdict': 'PASS', 'audit_scope': scope, 'plan_digest': data['body_digest']},
                             dependencies=[plan])

    def complete(self, plan, audit, status='FINAL_PASS'):
        return update_checkpoint(self.paths, product_id=1, encoding_id='001', status=status,
                                 plan_ref=plan, evidence_ref=audit, expected_revision=read_ledger(self.paths)['revision'])

    def test_registration_resume_real_cli_without_paths(self):
        before = self.paths.state_path.read_bytes()
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/init_project.py'),
                                 '--root', str(self.repo), '--workspace', str(self.paths.workspace_root)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, self.paths.state_path.read_bytes())
        with self.assertRaisesRegex(ProjectIdentityError, 'resolve_identity_mismatch'):
            initialize(self.paths, resolve_project_id='another-id', force=True)
        self.title.unlink()
        with self.assertRaisesRegex(ProjectIdentityError, 'registered_title_missing'):
            initialize(self.paths)

    def test_two_projects_same_input_display_name_product_one_cannot_share(self):
        other = resolve_runtime_paths(self.repo, self.base / 'other')
        self.register(other, 'resolve-uuid-b')
        self.assertNotEqual(self.paths.cache_dir, other.cache_dir)
        self.assertNotEqual(self.paths.upstream_cache_dir, other.upstream_cache_dir)
        ref = self.plan()
        copied = other.workspace_root / ref
        copied.parent.mkdir(parents=True)
        copied.write_bytes(self.paths.resolve_reference(ref).path.read_bytes())
        with self.assertRaisesRegex(CacheIdentityError, 'outside_project'):
            load_stage(other, ref)
        with patch.object(index_subtitles, 'media_duration', return_value=2):
            output = index_subtitles.generate(self.paths)
        with self.assertRaisesRegex(CacheIdentityError, 'project_key_mismatch'):
            validate_cache(other, output, expected=expected_identity(other, include_ranges=False))

    def test_batch_change_reuses_real_upstream_generators_but_not_audit(self):
        with patch.object(index_subtitles, 'media_duration', return_value=2):
            subtitle_output = index_subtitles.generate(self.paths)
        parsed = {'schema_version': 1, 'sheet': '当前商品', 'values': self.rows}
        with patch.object(index_titles, 'parse_workbook', return_value=parsed):
            title_output = index_titles.generate(self.paths, 'unused')
        old_cache = self.paths.cache_dir
        plan = self.plan()
        audit = self.audit(plan)
        self.complete(plan, audit)
        initialize(self.paths, batch_id='batch-b', products=[1, 2])
        self.assertNotEqual(old_cache, self.paths.cache_dir)
        with patch.object(index_subtitles, 'media_duration', side_effect=AssertionError('upstream recomputed')):
            self.assertEqual(index_subtitles.generate(self.paths), subtitle_output)
        with patch.object(index_titles, 'parse_workbook', side_effect=AssertionError('title recomputed')):
            self.assertEqual(index_titles.generate(self.paths, 'unused'), title_output)
        load_checked_json(self.paths, title_output)
        load_stage(self.paths, plan)
        with self.assertRaisesRegex(CacheIdentityError, 'stage_context_stale'):
            load_stage(self.paths, audit)
        self.assertEqual(resume(self.paths)[1]['status'], 'BLOCKED')
        self.assertEqual(resume(self.paths)[2]['status'], 'PENDING')

    def test_plan_change_cannot_reuse_old_pass_or_selection_pass(self):
        plan = self.plan()
        audit = self.audit(plan)
        self.complete(plan, audit)
        changed = self.plan(version='v2', body={'clips': [{'start': 0.1, 'end': 1}]})
        self.assertEqual(resume(self.paths)[1]['status'], 'BLOCKED')
        with self.assertRaisesRegex(CacheIdentityError, 'evidence_plan_mismatch'):
            self.complete(changed, audit)
        candidate_audit = self.audit(changed, version='selection', scope='selection')
        with self.assertRaisesRegex(CacheIdentityError, 'selection_pass'):
            self.complete(changed, candidate_audit)
        update_checkpoint(self.paths, product_id=1, encoding_id='001', status='IN_PROGRESS', plan_ref=changed,
                          expected_revision=read_ledger(self.paths)['revision'])
        self.assertEqual(resume(self.paths)[1]['status'], 'IN_PROGRESS')
        self.assertTrue(resume(self.paths)[1]['resume_required'])
        self.assertEqual(reconcile(self.paths)[1]['status'], 'IN_PROGRESS')
        self.assertEqual(json.loads(self.paths.state_path.read_text())['batch']['completed_product_ids'], [])

    def test_concurrent_writer_and_immutable_conflict(self):
        ctx = multiprocessing.get_context('spawn')
        queue = ctx.Queue()
        with workspace_lock(self.paths):
            worker = ctx.Process(target=competing_stage, args=(str(self.repo), str(self.paths.workspace_root), queue))
            worker.start()
            worker.join(15)
            self.assertEqual(worker.exitcode, 0)
            self.assertEqual(queue.get(timeout=2), 'workspace_busy')
        plan = self.plan()
        before = self.paths.resolve_reference(plan).path.read_bytes()
        with self.assertRaisesRegex(CacheIdentityError, 'immutable_stage_conflict'):
            self.plan(body={'clips': ['different']})
        self.assertEqual(before, self.paths.resolve_reference(plan).path.read_bytes())
        self.assertEqual(self.plan(), plan)

    def test_interruption_retry_counts_only_once_and_revision_blocks_old_worker(self):
        plan = self.plan()
        audit = self.audit(plan)
        with patch('product_checkpoints.atomic_json', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):
                self.complete(plan, audit)
        self.assertEqual(resume(self.paths)[1]['status'], 'PENDING')
        self.complete(plan, audit)
        revision = read_ledger(self.paths)['revision']
        # Simulate failure after ledger publication but before state reconciliation.
        self.assertEqual(json.loads(self.paths.state_path.read_text())['batch']['completed_product_ids'], [])
        reconcile(self.paths)
        self.complete(plan, audit)
        reconcile(self.paths)
        self.assertEqual(read_ledger(self.paths)['revision'], revision)
        self.assertEqual(json.loads(self.paths.state_path.read_text())['batch']['completed_product_ids'], [1])
        with self.assertRaisesRegex(CacheIdentityError, 'revision_conflict'):
            update_checkpoint(self.paths, product_id=1, encoding_id='001', status='BLOCKED', expected_revision=0)

    def test_legacy_coexistence_downstream_actual_consumers_block_before_io(self):
        legacy = self.paths.workspace_root / '.cache/product_01_windowed'
        legacy.mkdir(parents=True)
        old = legacy / 'approved_plans.json'
        old.write_text('{"plans": []}')
        plan = self.plan()
        load_stage(self.paths, plan)
        with self.assertRaises(CacheIdentityError):
            load_stage(self.paths, self.paths.reference(old))
        with self.assertRaisesRegex(CacheIdentityError, 'registered_downstream_not_integrated'):
            load_products(self.repo, [1], workspace=self.paths.workspace_root)
        with patch.object(sys, 'argv', ['refine', '--root', str(self.repo), '--workspace', str(self.paths.workspace_root), '--products', '1']):
            with self.assertRaisesRegex(CacheIdentityError, 'registered_downstream_not_integrated'):
                refine_main()
        self.assertEqual(old.read_text(), '{"plans": []}')
        with patch.object(index_subtitles, 'media_duration', return_value=2):
            output = index_subtitles.generate(self.paths)
        metadata = json.loads(metadata_path(output).read_text())
        metadata['schema_version'] = 1
        metadata_path(output).write_text(json.dumps(metadata))
        state = json.loads(self.paths.state_path.read_text())
        state['runtime'].pop('subtitle_index_fingerprint', None)
        atomic_json(self.paths.state_path, state)
        with self.assertRaisesRegex(CacheIdentityError, 'schema_version_mismatch'):
            index_subtitles.generate(self.paths)

    def test_source_rules_model_dependency_changes_fail_closed(self):
        plan = self.plan()
        audit = self.audit(plan)
        self.complete(plan, audit)
        state = json.loads(self.paths.state_path.read_text())
        state['runtime']['model_versions'] = {'final_audit': 'new-model'}
        atomic_json(self.paths.state_path, state)
        load_stage(self.paths, plan)  # Audit model changes do not invalidate the plan computation.
        self.assertEqual(resume(self.paths)[1]['status'], 'BLOCKED')
        with self.assertRaisesRegex(CacheIdentityError, 'configured_stage_or_model_version_mismatch'):
            publish_stage(self.paths, product_id=1, encoding_id='001', stage='final_audit',
                          version='v2', model_version='old-model', body={})
        state['runtime'].pop('model_versions')
        atomic_json(self.paths.state_path, state)
        self.srt.write_text(self.srt.read_text() + '\nChanged input')
        self.assertEqual(resume(self.paths)[1]['status'], 'BLOCKED')

    def test_pass_chain_written_export_verified_and_progress_cli(self):
        plan = self.plan()
        audit = self.audit(plan)
        self.complete(plan, audit)
        previous = audit
        for status, stage in [('WRITTEN', 'written'), ('EXPORT_VERIFIED', 'export_verified')]:
            evidence = publish_stage(self.paths, product_id=1, encoding_id='001', stage=stage,
                                     version='v1', model_version='synthetic-verifier',
                                     body={'verdict': 'VERIFIED', 'plan_digest': load_stage(self.paths, plan)['body_digest'],
                                           'previous_evidence': previous}, dependencies=[previous])
            self.complete(plan, evidence, status)
            self.assertEqual(resume(self.paths)[1]['status'], status)
            previous = evidence
        command = [sys.executable, '-B', str(ROOT / 'scripts/update_batch_state.py'),
                   '--root', str(self.repo), '--workspace', str(self.paths.workspace_root)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        refused = subprocess.run(command + ['--completed', '1'], capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('unverified_progress_flags_blocked', refused.stderr)

    def test_snapshot_failure_and_plan_publication_failure_leave_old_pass_blocked(self):
        plan = self.plan()
        audit = self.audit(plan)
        self.complete(plan, audit)
        args = dict(product_id=1, encoding_id='001', stage='plan', version='v2', model_version='synthetic')
        args['dependencies'] = [d['path'] for d in load_stage(self.paths, plan)['identity']['dependencies']]
        snapshot = capture_stage(self.paths, **args)
        self.srt.write_text(self.srt.read_text() + '\nNew cue')
        with self.assertRaisesRegex(CacheIdentityError, 'changed_since_computation|stage_context_stale'):
            stage_publisher(self.paths, **args, snapshot=snapshot, body={'clips': []})
        self.srt.write_text(self.srt.read_text().replace('\nNew cue', ''))
        snapshot = capture_stage(self.paths, **args)
        with patch('product_checkpoints.atomic_json', side_effect=OSError('synthetic failed rename')):
            with self.assertRaises(OSError):
                stage_publisher(self.paths, **args, snapshot=snapshot, body={'clips': []})
        self.assertEqual(resume(self.paths)[1]['status'], 'BLOCKED')
        with self.assertRaisesRegex(CacheIdentityError, 'publication_incomplete'):
            self.plan(version='v3')

    def test_repairable_blocked_and_precise_missing_input_update(self):
        update_checkpoint(self.paths, product_id=1, encoding_id='001', status='REPAIRABLE', expected_revision=0,
                          reason='synthetic repair required')
        self.assertEqual(resume(self.paths)[1]['status'], 'REPAIRABLE')
        update_checkpoint(self.paths, product_id=1, encoding_id='001', status='BLOCKED', expected_revision=1,
                          reason='synthetic environment fault')
        self.assertEqual(resume(self.paths)[1]['status'], 'BLOCKED')
        replacement = self.material / 'replacement.xlsx'
        self.title.rename(replacement)
        initialize(self.paths, title=replacement, force=True)
        self.assertEqual(json.loads(self.paths.state_path.read_text())['title_path'], str(replacement.resolve()))
        state = json.loads(self.paths.state_path.read_text())
        state.pop('project_identity')
        state['runtime'].pop('identity_contract')
        atomic_json(self.paths.state_path, state)
        with self.assertRaisesRegex((ProjectIdentityError, CacheIdentityError), 'legacy_fallback_forbidden'):
            expected_identity(self.paths, include_ranges=False)

    def test_rules_and_dependency_edit_invalidate_current_pass(self):
        plan = self.plan()
        audit = self.audit(plan)
        self.complete(plan, audit)
        rules = self.repo / 'PROJECT_RULES.md'
        original = rules.read_text()
        rules.write_text(original + '\nNew synthetic policy')
        self.assertEqual(resume(self.paths)[1]['status'], 'BLOCKED')
        rules.write_text(original)
        artifact = self.paths.resolve_reference(plan).path
        value = json.loads(artifact.read_text())
        value['body']['clips'][0]['start'] = 0.2
        artifact.write_text(json.dumps(value))
        self.assertEqual(resume(self.paths)[1]['status'], 'BLOCKED')

    def test_new_range_invalidates_old_plan_and_audit_without_overwriting(self):
        plan = self.plan()
        audit = self.audit(plan)
        self.complete(plan, audit)
        publish_stage(self.paths, product_id=1, encoding_id='001', stage='range', version='range-v2',
                      model_version='synthetic-boundary', body={'start': 0.2, 'end': 2})
        with self.assertRaisesRegex(CacheIdentityError, 'range_dependency_not_current'):
            load_stage(self.paths, plan)
        self.assertEqual(resume(self.paths)[1]['status'], 'BLOCKED')
        self.assertTrue(self.paths.resolve_reference(plan).path.exists())

    def test_auxiliary_entry_points_block_before_external_operations(self):
        import inspect_timeline_for_export as timeline_snapshot
        import compare_timeline_versions as comparison
        import fingerprint_inputs as fingerprints
        for module in (timeline_snapshot, comparison, fingerprints):
            argv = ['synthetic', '--root', str(self.repo), '--workspace', str(self.paths.workspace_root)]
            with self.subTest(module=module.__name__), patch.object(sys, 'argv', argv):
                with self.assertRaisesRegex(CacheIdentityError, 'registered_downstream_not_integrated'):
                    module.main()
        with patch.object(timeline_snapshot, 'connect') as connect, patch.object(sys, 'argv', argv):
            with self.assertRaises(CacheIdentityError):
                timeline_snapshot.main()
            connect.assert_not_called()

    def test_registration_partial_failure_and_changed_config_blocked(self):
        other = resolve_runtime_paths(self.repo, self.base / 'broken')
        with patch('init_project.atomic_json', side_effect=OSError('synthetic registration failure')):
            with self.assertRaises(OSError):
                self.register(other, 'broken-id')
        with self.assertRaisesRegex(ProjectIdentityError, 'incomplete'):
            self.register(other, 'broken-id')
        state = json.loads(self.paths.state_path.read_text())
        state['resolve']['project_name'] = 'changed'
        atomic_json(self.paths.state_path, state)
        with self.assertRaises(ProjectIdentityError):
            initialize(self.paths)


if __name__ == '__main__':
    unittest.main()
