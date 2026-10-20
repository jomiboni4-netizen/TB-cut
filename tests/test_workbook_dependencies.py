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

    def test_current_runtime_is_compatible_but_historical_lock_stays_strict(self):
        node = os.environ.get('TB_CUT_TEST_NODE', project_doctor.preferred_node())
        script = ROOT / 'scripts/check_workbook_dependencies.mjs'
        current = subprocess.run([node, str(script)], capture_output=True, text=True)
        self.assertEqual(current.returncode, 0, current.stderr)
        self.assertEqual(json.loads(current.stdout)['package'], '@oai/artifact-tool')
        historical = subprocess.run([node, str(script), '--mode', 'strict-history'], capture_output=True, text=True)
        self.assertEqual(historical.returncode, 3)
        self.assertEqual(json.loads(historical.stdout)['reason'], 'historical_artifact_tool_version_mismatch')
        old_lock = json.loads((ROOT / 'workbook-dependencies.lock.json').read_text())
        self.assertEqual(old_lock['artifactTool'], '2.8.93')

    def test_doctor_missing_node_warns_only_for_workbook_operations(self):
        with patch.object(sys, 'argv', ['doctor', '--root', str(self.root)]), patch.object(project_doctor.subprocess, 'run', side_effect=FileNotFoundError), patch('sys.stdout', new_callable=io.StringIO) as output:
            with self.assertRaises(SystemExit) as stopped:
                project_doctor.main()
            self.assertEqual(stopped.exception.code, 0)
            self.assertIn('workbook operations unverified', output.getvalue())

    def test_doctor_reports_excel_incompatibility_without_failing_other_checks(self):
        result = subprocess.CompletedProcess([], 3, '{"status":"incompatible","reason":"required_api_missing"}', '')
        with patch.object(sys, 'argv', ['doctor', '--root', str(self.root)]), patch.object(project_doctor.subprocess, 'run', return_value=result), patch('sys.stdout', new_callable=io.StringIO) as output:
            with self.assertRaises(SystemExit) as stopped:
                project_doctor.main()
            self.assertEqual(stopped.exception.code, 0)
            self.assertIn('workbook operations incompatible: required_api_missing', output.getvalue())
