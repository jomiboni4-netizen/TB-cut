"""Workspace relocation checks use only synthetic inputs."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from runtime_meta import expected_identity, rules_fingerprint, validate_cache, write_metadata  # noqa: E402
from runtime_paths import ArtifactPathError, resolve_runtime_paths  # noqa: E402
from workspace_preflight import inspect  # noqa: E402


class RuntimePathsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "repo"
        self.workspace = self.base / "workspace"
        for folder in (self.repo / "config", self.workspace / ".cache", self.base / "inputs"):
            folder.mkdir(parents=True)
        (self.repo / "PROJECT_RULES.md").write_text("rule A", encoding="utf-8")
        (self.repo / "config/content_rules.json").write_text("{}", encoding="utf-8")
        (self.base / "inputs/title.xlsx").write_bytes(b"title")
        (self.base / "inputs/one.srt").write_text("synthetic", encoding="utf-8")
        (self.base / "inputs/one.mp4").write_bytes(b"video")
        state = {
            "title_path": str(self.base / "inputs/title.xlsx"),
            "subtitle_paths": [str(self.base / "inputs/one.srt")],
            "video_paths": [str(self.base / "inputs/one.mp4")],
            "resolve": {}, "batch": {"batch_id": "synthetic-batch"},
            "runtime": {"pipeline_version": "2.1"},
        }
        (self.workspace / "PROJECT_STATE.json").write_text(json.dumps(state), encoding="utf-8")

    def test_default_workspace_is_repo_and_cli_overrides_env(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(resolve_runtime_paths(self.repo).workspace_root, self.repo)
        with patch.dict("os.environ", {"TB_CUT_WORKSPACE": str(self.base / "other")}):
            self.assertEqual(resolve_runtime_paths(self.repo, self.workspace).workspace_root, self.workspace)

    def test_separate_rules_state_and_cache(self) -> None:
        paths = resolve_runtime_paths(self.repo, self.workspace)
        self.assertEqual(paths.state_path, self.workspace / "PROJECT_STATE.json")
        self.assertEqual(paths.cache_dir, self.workspace / ".cache")
        first = expected_identity(paths)
        self.assertEqual(first["rules_fingerprint"], rules_fingerprint(self.repo))
        artifact = paths.cache_dir / "product_ranges.json"
        artifact.write_text('{"products": []}', encoding="utf-8")
        write_metadata(paths, artifact, "synthetic")
        self.assertEqual(validate_cache(paths, artifact)["batch_id"], "synthetic-batch")
        (self.repo / "PROJECT_RULES.md").write_text("rule B", encoding="utf-8")
        self.assertNotEqual(first["rules_fingerprint"], expected_identity(paths)["rules_fingerprint"])

    def test_relative_and_legacy_plan_references(self) -> None:
        paths = resolve_runtime_paths(self.repo, self.workspace)
        plan = paths.cache_dir / "product_01_windowed/cut_plan_variant_1.json"
        plan.parent.mkdir()
        plan.write_text("{}", encoding="utf-8")
        relative = paths.reference(plan)
        self.assertFalse(paths.resolve_reference(relative).legacy_absolute)
        relocated = self.base / "relocated"
        relocated_plan = relocated / relative
        relocated_plan.parent.mkdir(parents=True)
        relocated_plan.write_text("{}", encoding="utf-8")
        moved = resolve_runtime_paths(self.repo, relocated)
        self.assertEqual(moved.resolve_reference(relative).path, relocated_plan)
        legacy = paths.resolve_reference(str(plan))
        self.assertTrue(legacy.legacy_absolute)
        with self.assertRaisesRegex(ArtifactPathError, "artifact_outside_workspace"):
            paths.resolve_reference(str(self.repo / ".cache/product_01_windowed/cut_plan_variant_1.json"))

    def test_incomplete_workspace_not_ready(self) -> None:
        reasons = inspect(resolve_runtime_paths(self.repo, self.base / "empty"))
        self.assertTrue(any(reason.startswith("workspace_state_missing") for reason in reasons))

    def test_preflight_does_not_treat_external_input_as_runtime_reference(self) -> None:
        paths = resolve_runtime_paths(self.repo, self.workspace)
        (paths.cache_dir / "person_coverage_audit.json").write_text(
            json.dumps({"video_path": str(self.repo / "external_video.mp4")}), encoding="utf-8"
        )
        reasons = inspect(paths)
        self.assertFalse(any(reason.startswith("repo_absolute_path_in_runtime_json") for reason in reasons))


if __name__ == "__main__":
    unittest.main()
