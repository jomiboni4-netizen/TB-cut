import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import test_project_resume as fixtures
from create_batch_workspace import create_batch_workspace
from runtime_paths import resolve_runtime_paths


class CreateBatchWorkspaceTests(unittest.TestCase):
    setUp = fixtures.ProjectResumeTests.setUp
    register = fixtures.ProjectResumeTests.register

    def test_creates_identity_bound_batch_without_changing_project_record(self):
        project_record = self.paths.project_directory / 'PROJECT_STATE.json'
        before = project_record.read_bytes()
        batch_workspace = create_batch_workspace(
            self.repo, self.paths.project_directory, 'synthetic-batch-1', [1])
        batch_paths = resolve_runtime_paths(self.repo, batch_workspace)
        state = batch_paths.load_state()
        self.assertEqual(state['batch']['batch_id'], 'synthetic-batch-1')
        self.assertEqual(state['batch']['product_ids'], [1])
        self.assertEqual(project_record.read_bytes(), before)
        self.assertEqual({path.name for path in batch_workspace.iterdir()},
                         {'PROJECT_REF.json', 'BATCH_STATE.json'})

    def test_existing_batch_and_invalid_product_scope_are_rejected(self):
        create_batch_workspace(self.repo, self.paths.project_directory,
                               'synthetic-batch-2', [1])
        with self.assertRaisesRegex(ValueError, 'batch_workspace_already_exists'):
            create_batch_workspace(self.repo, self.paths.project_directory,
                                   'synthetic-batch-2', [1])
        with self.assertRaisesRegex(ValueError, 'product_ids_must_be'):
            create_batch_workspace(self.repo, self.paths.project_directory,
                                   'synthetic-batch-3', [0])


if __name__ == '__main__':
    unittest.main()
