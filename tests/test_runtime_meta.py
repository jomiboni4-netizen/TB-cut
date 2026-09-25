"""Runtime identity checks use only synthetic local files."""

from __future__ import annotations

import json
import hashlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from runtime_meta import (  # noqa: E402
    CacheIdentityError,
    expected_identity,
    load_checked_json,
    metadata_path,
    validate_cache,
    write_metadata,
)
from attest_runtime_cache import main as attest_cache_main  # noqa: E402
from resolve_write_v2 import load_products  # noqa: E402


class RuntimeMetaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in ("inputs/subtitles", "inputs/videos", "config", ".cache"):
            (self.root / folder).mkdir(parents=True)
        (self.root / "inputs/title.xlsx").write_bytes(b"synthetic title")
        (self.root / "inputs/subtitles/001.srt").write_text("synthetic cue", encoding="utf-8")
        (self.root / "inputs/videos/001.mp4").write_bytes(b"synthetic video")
        (self.root / "PROJECT_RULES.md").write_text("synthetic rules", encoding="utf-8")
        (self.root / "config/content_rules.json").write_text("{}", encoding="utf-8")
        self.state = {
            "title_path": str(self.root / "inputs/title.xlsx"),
            "subtitle_paths": [str(self.root / "inputs/subtitles")],
            "video_paths": [str(self.root / "inputs/videos")],
            "resolve": {"project_name": "example", "source_timeline": "source", "target_timeline": "target"},
            "batch": {"batch_id": "batch-a"},
            "runtime": {"pipeline_version": "2.1"},
        }
        self.save_state()
        self.artifact = self.root / ".cache/product_ranges.json"
        self.artifact.write_text('{"schema_version": 1, "products": []}\n', encoding="utf-8")

    def save_state(self) -> None:
        (self.root / "PROJECT_STATE.json").write_text(json.dumps(self.state), encoding="utf-8")

    def test_current_batch_passes(self) -> None:
        write_metadata(self.root, self.artifact, "test")
        self.assertEqual(validate_cache(self.root, self.artifact)["batch_id"], "batch-a")
        self.assertEqual(load_checked_json(self.root, self.artifact, artifact_schema_version=1)["products"], [])

    def test_other_batch_fails(self) -> None:
        write_metadata(self.root, self.artifact, "test")
        self.state["batch"]["batch_id"] = "batch-b"
        self.save_state()
        with self.assertRaisesRegex(CacheIdentityError, "batch_id_mismatch"):
            validate_cache(self.root, self.artifact)

    def test_changed_input_fails(self) -> None:
        write_metadata(self.root, self.artifact, "test")
        (self.root / "inputs/subtitles/001.srt").write_text("changed synthetic cue", encoding="utf-8")
        with self.assertRaisesRegex(CacheIdentityError, "input_fingerprint_mismatch"):
            validate_cache(self.root, self.artifact)

    def test_pipeline_and_schema_fail(self) -> None:
        write_metadata(self.root, self.artifact, "test")
        self.state["runtime"]["pipeline_version"] = "2.2"
        self.save_state()
        with self.assertRaisesRegex(CacheIdentityError, "pipeline_version_mismatch"):
            validate_cache(self.root, self.artifact)
        self.state["runtime"]["pipeline_version"] = "2.1"
        self.save_state()
        with self.assertRaisesRegex(CacheIdentityError, "artifact_schema_version_mismatch"):
            load_checked_json(self.root, self.artifact, artifact_schema_version=2)
        sidecar = metadata_path(self.artifact)
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        meta["schema_version"] = 999
        sidecar.write_text(json.dumps(meta), encoding="utf-8")
        with self.assertRaisesRegex(CacheIdentityError, "schema_version_mismatch"):
            validate_cache(self.root, self.artifact)

    def test_legacy_file_is_not_current(self) -> None:
        self.assertTrue(self.artifact.exists())
        self.assertFalse(metadata_path(self.artifact).exists())
        with self.assertRaisesRegex(CacheIdentityError, "legacy_cache_missing_metadata"):
            validate_cache(self.root, self.artifact)

    def test_changed_artifact_fails(self) -> None:
        write_metadata(self.root, self.artifact, "test")
        self.artifact.write_text('{"schema_version": 1, "products": [1]}\n', encoding="utf-8")
        with self.assertRaisesRegex(CacheIdentityError, "artifact_sha256_mismatch"):
            validate_cache(self.root, self.artifact)

    def test_resolve_preflight_rejects_legacy_titles(self) -> None:
        titles = self.root / ".cache/titles.json"
        titles.write_text('{"products": []}\n', encoding="utf-8")
        with self.assertRaisesRegex(CacheIdentityError, "legacy_cache_missing_metadata"):
            load_products(self.root, [1])

    def test_response_attestation_requires_matching_request_digest(self) -> None:
        product_dir = self.root / ".cache/product_01_rough"
        product_dir.mkdir()
        request = product_dir / "mimo_request.json"
        request.write_text('{"product_index": 1, "pipeline_version": "2.1", "candidates": []}\n', encoding="utf-8")
        write_metadata(self.root, request, "test", product_id=1)
        result = product_dir / "mimo_result.json"
        result.write_text('{"output_text": "{}"}\n', encoding="utf-8")
        transport = product_dir / "mimo_result_meta.json"
        transport.write_text(json.dumps({"status": "completed", "request_digest": "wrong"}), encoding="utf-8")
        identity = expected_identity(self.root)
        argv = [
            "attest_runtime_cache.py", "--root", str(self.root), "attest-response",
            "--artifact", str(result), "--request", str(request),
            "--artifact-sha256", hashlib.sha256(result.read_bytes()).hexdigest(),
            "--batch-id", identity["batch_id"],
            "--input-fingerprint", identity["input_fingerprint"],
            "--product-id", "1",
        ]
        with patch.object(sys, "argv", argv), patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaisesRegex(CacheIdentityError, "response_request_digest_mismatch"):
                attest_cache_main()
        self.assertFalse(metadata_path(result).exists())
        transport.write_text(json.dumps({"status": "completed", "request_digest": hashlib.sha256(request.read_bytes()).hexdigest()}), encoding="utf-8")
        with patch.object(sys, "argv", argv), patch("sys.stdout", new_callable=io.StringIO):
            attest_cache_main()
        self.assertEqual(validate_cache(self.root, result, product_id=1)["producer"], "explicit_response_attestation")


if __name__ == "__main__":
    unittest.main()
