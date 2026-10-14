import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cleanup import load_protected_paths, safe_target


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


if __name__ == '__main__':
    unittest.main()
