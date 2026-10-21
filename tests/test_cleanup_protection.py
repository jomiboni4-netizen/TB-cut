import json
import sys
import tempfile
import unittest
import contextlib
import io
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cleanup import load_protected_paths, safe_target, rebuildable_targets, main


class CleanupProtectionTests(unittest.TestCase):
    def test_registered_input_nested_in_cache_blocks_parent_deletion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / '.cache/subtitles/private.srt'
            source.parent.mkdir(parents=True)
            source.write_text('source', encoding='utf-8')
            (root / 'PROJECT_STATE.json').write_text(json.dumps({
                'title_path': None, 'subtitle_paths': [str(source)], 'video_paths': []}), encoding='utf-8')
            protected = load_protected_paths(root)
            with self.assertRaisesRegex(RuntimeError, 'Protected path'):
                safe_target(root, '.cache/subtitles', protected)
            self.assertTrue(source.exists())

    def test_invalid_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / 'PROJECT_STATE.json').write_text('{broken', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'Cannot establish cleanup protection'):
                load_protected_paths(root)
            (root / 'PROJECT_STATE.json').write_text(json.dumps({'subtitle_paths': 'private.srt'}), encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'Cannot establish cleanup protection'):
                load_protected_paths(root)

    def test_rebuildable_plan_preserves_core_and_sources_and_final_exports(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            names = ['.cache/swift_modules/Swift.swiftmodule',
                     '.cache/workspaces/run/sentence_trial/context_0.wav',
                     '.cache/diagnostics/full_preview/variant_preview.mp4',
                     '.cache/diagnostics/refined_preview/variant_2_refined_v1.mp4',
                     '.cache/diagnostics/resolve_export/variant2_resolve_audit_v1.mp4',
                     '.cache/diagnostics/resolve_export/final.mp4',
                     '.cache/workspaces/run/PROJECT_STATE.json',
                     '.cache/workspaces/run/backup.drp',
                     '.cache/workspaces/run/plan.json',
                     '.cache/models/weights.bin', 'video/source.mp4']
            for name in names:
                p = root/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('{}')
            targets = rebuildable_targets(root)
            self.assertEqual(set(targets), {'.cache/swift_modules',
                '.cache/workspaces/run/sentence_trial/context_0.wav',
                '.cache/diagnostics/full_preview/variant_preview.mp4',
                '.cache/diagnostics/refined_preview/variant_2_refined_v1.mp4',
                '.cache/diagnostics/resolve_export/variant2_resolve_audit_v1.mp4'})

    def test_nested_workspace_input_and_protected_directory_contents_cannot_be_deleted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); folder = root/'.cache/workspaces/run'
            folder.mkdir(parents=True); source = folder/'input.wav'; source.write_bytes(b'input')
            (folder/'PROJECT_STATE.json').write_text(json.dumps({'video_paths':[str(source)],
                'subtitle_paths':[], 'title_path':None}))
            protected = load_protected_paths(root)
            with self.assertRaisesRegex(RuntimeError, 'Protected path'):
                safe_target(root, str(source.relative_to(root)), protected)
            with self.assertRaisesRegex(RuntimeError, 'Protected path'):
                safe_target(root, '.cache', protected)
            with self.assertRaisesRegex(RuntimeError, 'Protected path'):
                safe_target(root, '.cache/workspaces/run/input.wav/child', {source})

    def test_cli_dry_run_then_apply_only_removes_generated_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); scratch = root/'.cache/workspaces/run/context_0.wav'
            scratch.parent.mkdir(parents=True); scratch.write_bytes(b'scratch')
            core = scratch.with_name('plan.json'); core.write_text('{"keep":true}')
            args = ['cleanup', '--root', str(root), '--workspace', str(root), '--rebuildable']
            with patch.object(sys, 'argv', args+['--dry-run']), contextlib.redirect_stdout(io.StringIO()):
                main()
            self.assertTrue(scratch.exists())
            with patch.object(sys, 'argv', args+['--apply']), contextlib.redirect_stdout(io.StringIO()):
                main()
            self.assertFalse(scratch.exists())
            self.assertEqual(core.read_text(), '{"keep":true}')

    def test_protected_candidate_aborts_entire_apply_before_first_deletion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); cache = root/'.cache/swift_modules'
            cache.mkdir(parents=True); (cache/'module.pcm').write_bytes(b'cache')
            source = root/'.cache/workspaces/run/input.wav'; source.parent.mkdir(parents=True)
            source.write_bytes(b'source')
            (root/'PROJECT_STATE.json').write_text(json.dumps({'video_paths':[str(source)]}))
            args = ['cleanup', '--root', str(root), '--workspace', str(root), '--rebuildable', '--apply']
            with patch.object(sys, 'argv', args), self.assertRaisesRegex(RuntimeError, 'Protected path'):
                main()
            self.assertTrue((cache/'module.pcm').exists())
            self.assertEqual(source.read_bytes(), b'source')


if __name__ == '__main__':
    unittest.main()
