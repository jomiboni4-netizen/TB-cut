"""Read-only dependency checks, with synthetic damaged/missing installations."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import project_doctor


class WorkbookDependencyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in project_doctor.REQUIRED:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}')

    def test_missing_module_and_wrong_version_or_digest_fail(self):
        node = os.environ.get('TB_CUT_TEST_NODE', 'node')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'scripts').mkdir()
            script = root / 'scripts/check_workbook_dependencies.mjs'
            shutil.copyfile(ROOT / 'scripts/check_workbook_dependencies.mjs', script)
            lock = json.loads((ROOT / 'workbook-dependencies.lock.json').read_text())
            for fault in ('module', 'node', 'artifactTool', 'treeSha256'):
                with self.subTest(fault=fault):
                    modified = dict(lock)
                    if fault != 'module':
                        link = root / 'node_modules'
                        if not link.exists():
                            link.symlink_to((ROOT / 'node_modules').resolve(), target_is_directory=True)
                        modified[fault] = 'wrong'
                    (root / 'workbook-dependencies.lock.json').write_text(json.dumps(modified))
                    result = subprocess.run([node, str(script)], capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('workbook_dependency_check_failed', result.stderr)

    def test_doctor_missing_node_is_an_error(self):
        with patch.object(sys, 'argv', ['doctor', '--root', str(self.root)]), patch.object(project_doctor.subprocess, 'run', side_effect=FileNotFoundError), patch('sys.stdout', new_callable=io.StringIO) as output:
            with self.assertRaises(SystemExit) as stopped:
                project_doctor.main()
            self.assertEqual(stopped.exception.code, 1)
            self.assertIn('workbook dependency check unavailable', output.getvalue())

    def test_doctor_dependency_failure_is_an_error(self):
        result = subprocess.CompletedProcess([], 1, '', 'synthetic dependency failure')
        with patch.object(sys, 'argv', ['doctor', '--root', str(self.root)]), patch.object(project_doctor.subprocess, 'run', return_value=result), patch('sys.stdout', new_callable=io.StringIO) as output:
            with self.assertRaises(SystemExit) as stopped:
                project_doctor.main()
            self.assertEqual(stopped.exception.code, 1)
            self.assertIn('synthetic dependency failure', output.getvalue())
