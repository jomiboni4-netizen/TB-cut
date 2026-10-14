"""Synthetic path-only migration tests; no media analysis or external services."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from migrate_runtime_layout import migration  # noqa: E402
from runtime_paths import resolve_runtime_paths  # noqa: E402


class RuntimeMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.cache = self.root / ".cache"
        self.plan_dir = self.cache / "product_01_windowed"
        self.plan_dir.mkdir(parents=True)
        self.external = self.root.parent / "synthetic_external_video.mp4"
        media = self.root / "media"
        media.mkdir()
        (media / "clip.mp4").write_bytes(b"synthetic")
        state = {
            "video_paths": [str(media)],
            "batch": {"product_ids": [1]},
            "resolve": {"project_name": "example", "target_timeline": "target"},
        }
        (self.root / "PROJECT_STATE.json").write_text(json.dumps(state), encoding="utf-8")
        self.plan = self.plan_dir / "cut_plan_variant_1.json"
        self.plan.write_text(json.dumps({
            "schema_version": 2, "pipeline_version": "2.1", "product_index": 1,
            "variant_id": "variant_1", "status": "PASS",
            "clips": [{"source_file": "clip.mp4", "source_start_frame": 1, "source_end_frame": 100}],
            "external_video_path": str(self.external),
        }), encoding="utf-8")
        plan_digest = hashlib.sha256(self.plan.read_bytes()).hexdigest()
        self.coverage_path = self.cache / "person_coverage_audit.json"
        self.coverage_path.write_text(json.dumps({
            "schema_version": 2, "pipeline_version": "2.1",
            "plans": [{"product_index": 1, "variant_id": "variant_1", "plan": str(self.plan), "plan_digest": plan_digest}],
        }), encoding="utf-8")
        self.generated = self.plan_dir / "generated_plans.json"
        self.generated.write_text(json.dumps({"schema_version": 2, "pipeline_version": "2.1", "product_index": 1, "plans": [str(self.plan)]}), encoding="utf-8")
        self.approved = self.plan_dir / "approved_plans.json"
        self.approved.write_text(json.dumps({"schema_version": 2, "pipeline_version": "2.1", "product_index": 1, "plans": [{"variant_id": "variant_1", "path": str(self.plan)}]}), encoding="utf-8")
        self.backup = self.cache / "backups/project.drp"
        self.backup.parent.mkdir()
        self.backup.write_bytes(b"backup")
        self.resolve_report = self.cache / "resolve_write_report.json"
        self.resolve_report.write_text(json.dumps({"project": "example", "target_timeline": "target", "backup_project": str(self.backup)}), encoding="utf-8")
        self.unknown = self.cache / "product_01_rough/mimo_request.json"
        self.unknown.parent.mkdir()
        self.unknown.write_text('{"candidates": []}', encoding="utf-8")
        self.paths = resolve_runtime_paths(self.root)

    def test_dry_run_and_apply_are_idempotent(self) -> None:
        original = {p: p.read_bytes() for p in (self.plan, self.generated, self.approved, self.coverage_path, self.resolve_report, self.unknown)}
        dry = migration(self.paths)
        self.assertEqual(dry["files_updated"], 0)
        self.assertEqual(dry["paths_converted"], 4)
        self.assertEqual({p: p.read_bytes() for p in original}, original)
        self.assertFalse((self.root / "reports/runtime_migration_report.json").exists())
        applied = migration(self.paths, apply=True)
        self.assertEqual(applied["files_updated"], 4)
        self.assertEqual(json.loads(self.generated.read_text())["plans"], [self.paths.reference(self.plan)])
        self.assertEqual(json.loads(self.approved.read_text())["plans"][0]["path"], self.paths.reference(self.plan))
        self.assertEqual(json.loads(self.coverage_path.read_text())["plans"][0]["plan"], self.paths.reference(self.plan))
        self.assertEqual(json.loads(self.resolve_report.read_text())["backup_project"], self.paths.reference(self.backup))
        self.assertEqual(json.loads(self.plan.read_text())["external_video_path"], str(self.external))
        self.assertEqual(self.plan.read_bytes(), original[self.plan])
        self.assertEqual(self.unknown.read_bytes(), original[self.unknown])
        self.assertFalse(Path(str(self.unknown) + ".meta.json").exists())
        after_first = {p: p.read_bytes() for p in original}
        again = migration(self.paths, apply=True)
        self.assertEqual(again["files_updated"], 0)
        self.assertEqual(again["paths_converted"], 0)
        self.assertEqual({p: p.read_bytes() for p in original}, after_first)

    def test_unproven_plan_is_stale_and_unchanged(self) -> None:
        self.plan.write_text(self.plan.read_text().replace("clip.mp4", "other.mp4"), encoding="utf-8")
        before = self.generated.read_bytes()
        report = migration(self.paths, apply=True)
        self.assertEqual(self.generated.read_bytes(), before)
        self.assertTrue(any(item["file"].endswith("generated_plans.json") and item["status"] == "STALE" for item in report["items"]))


if __name__ == "__main__":
    unittest.main()
