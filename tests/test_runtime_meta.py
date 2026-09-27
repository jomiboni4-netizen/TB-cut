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
    rules_fingerprint,
    runtime_rules_content,
    validate_cache,
    write_metadata,
)
import runtime_meta
from workspace_preflight import inspect
from runtime_paths import resolve_runtime_paths
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

    def check_workflow_edit(self, heading: str) -> None:
        rules = self.root / "PROJECT_RULES.md"
        before = f"# Rules\n\n## {heading}\nOld workflow.\n\n## Final deliverables\nDuration >45 and <60.\n"
        rules.write_text(before)
        write_metadata(self.root, self.artifact, "test")
        fingerprint = rules_fingerprint(self.root)
        metadata = metadata_path(self.artifact).read_bytes()
        rules.write_text(before.replace("Old workflow.", "New workflow with different reporting instructions."))
        self.assertEqual(rules_fingerprint(self.root), fingerprint)
        validate_cache(self.root, self.artifact)
        self.assertEqual(metadata_path(self.artifact).read_bytes(), metadata)

    def test_git_workflow_does_not_invalidate_cache(self) -> None:
        self.check_workflow_edit("Git and public repository workflow")

    def test_review_report_does_not_invalidate_cache(self) -> None:
        self.check_workflow_edit("Temporary public review report")

    def test_communication_does_not_invalidate_cache(self) -> None:
        self.check_workflow_edit("Working communication")

    def test_all_hard_rule_sections_invalidate_cache(self) -> None:
        for heading in (
            "Final deliverables", "Candidate pool definition", "Allowed content",
            "Banned content", "Semantic closure", "Cross-variant uniqueness",
            "Visual/person rule", "Audio/cut-point rule", "Repair and exhaustion",
            "Physical clip normalization", "Resolve", "New unknown hard rule",
        ):
            with self.subTest(heading=heading):
                path = self.root / "PROJECT_RULES.md"
                text = f"# Rules\n## Git and public repository workflow\nWorkflow.\n## {heading}\nOriginal constraint.\n"
                path.write_text(text)
                write_metadata(self.root, self.artifact, "test")
                fingerprint = rules_fingerprint(self.root)
                path.write_text(text.replace("Original constraint.", "Changed constraint."))
                self.assertNotEqual(rules_fingerprint(self.root), fingerprint)
                with self.assertRaisesRegex(CacheIdentityError, "rules_fingerprint_mismatch"):
                    validate_cache(self.root, self.artifact)

    def test_config_edit_add_and_remove_invalidate_cache(self) -> None:
        for operation in ("edit", "add", "remove"):
            with self.subTest(operation=operation):
                path = self.root / "config/content_rules.json"
                path.write_text('{}')
                write_metadata(self.root, self.artifact, "test")
                fingerprint = rules_fingerprint(self.root)
                if operation == "edit":
                    path.write_text('{"duration": 50}')
                elif operation == "add":
                    (self.root / "config/extra.json").write_text('{}')
                else:
                    path.unlink()
                self.assertNotEqual(rules_fingerprint(self.root), fingerprint)
                with self.assertRaisesRegex(CacheIdentityError, "rules_fingerprint_mismatch"):
                    validate_cache(self.root, self.artifact)

    def test_fenced_heading_is_not_a_workflow_section(self) -> None:
        for fence in (b"```", b"~~~~"):
            text = b"# Rules\n" + fence + b"\n## Working communication\nHard rule example\n" + fence + b"\n"
            self.assertEqual(runtime_rules_content(text), text)
        with self.assertRaisesRegex(CacheIdentityError, "rules_markdown_unclosed_fence"):
            runtime_rules_content(b"# Rules\n```\nunfinished")

    def test_unknown_heading_ends_workflow_exclusion(self) -> None:
        text = b"# Rules\n## Working communication\nWorkflow\n# Extra rules\nHard rule\n"
        self.assertEqual(runtime_rules_content(text), b"# Rules\n# Extra rules\nHard rule\n")

    def test_setext_heading_ends_exclusion(self) -> None:
        for underline in (b"===", b"---", b"=", b"-", b"   ---\t"):
            with self.subTest(underline=underline):
                section = b"Extra rules\n" + underline + b"\nHard rule\n"
                text = b"# Rules\n## Working communication\nWorkflow\n\n" + section
                self.assertEqual(runtime_rules_content(text), b"# Rules\n" + section)

    def test_setext_hard_and_unknown_rules_invalidate_cache(self) -> None:
        for underline in ("===", "---"):
            for heading in ("Final deliverables", "New unknown hard rule"):
                with self.subTest(underline=underline, heading=heading):
                    path = self.root / "PROJECT_RULES.md"
                    text = f"# Rules\n## Working communication\nWorkflow\n\n{heading}\n{underline}\nOriginal constraint.\n"
                    path.write_text(text)
                    write_metadata(self.root, self.artifact, "test")
                    fingerprint = rules_fingerprint(self.root)
                    path.write_text(text.replace("Original constraint.", "Changed constraint."))
                    self.assertNotEqual(rules_fingerprint(self.root), fingerprint)
                    with self.assertRaisesRegex(CacheIdentityError, "rules_fingerprint_mismatch"):
                        validate_cache(self.root, self.artifact)

    def test_setext_workflow_h2_is_excluded_but_h1_is_retained(self) -> None:
        for heading in ("Working communication", "Git and public repository workflow", "Temporary public review report"):
            for underline in ("---", "==="):
                with self.subTest(heading=heading, underline=underline):
                    text = f"# Rules\n\n{heading}\n{underline}\nOld workflow.\n\n## Final deliverables\nHard rule\n".encode()
                    changed = text.replace(b"Old workflow.", b"New workflow.")
                    if underline == "---":
                        self.assertEqual(runtime_rules_content(text), runtime_rules_content(changed))
                    else:
                        self.assertNotEqual(runtime_rules_content(text), runtime_rules_content(changed))

    def test_multiline_setext_unknown_heading_is_retained(self) -> None:
        for underline in (b"===", b"---"):
            section = b"Unknown hard rules\nWorking communication\n" + underline + b"\nHard rule\n"
            text = b"# Rules\n## Working communication\nWorkflow\n\n" + section
            self.assertEqual(runtime_rules_content(text), b"# Rules\n" + section)
            self.assertNotEqual(runtime_rules_content(text), runtime_rules_content(text.replace(b"Unknown hard rules", b"Changed hard rules")))

    def test_setext_inside_fences_does_not_change_exclusion(self) -> None:
        for fence in (b"```", b"~~~~"):
            for underline in (b"===", b"---"):
                block = fence + b"\nWorking communication\n" + underline + b"\nExample\n" + fence + b"\n"
                self.assertEqual(runtime_rules_content(b"# Rules\n" + block), b"# Rules\n" + block)
                text = b"# Rules\n## Working communication\n" + block + b"Workflow\n## Hard rules\nKeep\n"
                self.assertEqual(runtime_rules_content(text), b"# Rules\n## Hard rules\nKeep\n")

    def test_legacy_fingerprint_is_not_silently_accepted(self) -> None:
        legacy = hashlib.sha256()
        for path in [self.root / "PROJECT_RULES.md", *sorted((self.root / "config").glob("*.json"))]:
            legacy.update(str(path.relative_to(self.root)).encode())
            legacy.update(hashlib.sha256(path.read_bytes()).hexdigest().encode())
        write_metadata(self.root, self.artifact, "test")
        sidecar = metadata_path(self.artifact)
        document = json.loads(sidecar.read_text())
        document["rules_fingerprint"] = legacy.hexdigest()
        sidecar.write_text(json.dumps(document))
        with self.assertRaisesRegex(CacheIdentityError, "rules_fingerprint_mismatch"):
            validate_cache(self.root, self.artifact)

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

    def test_legacy_titles_cannot_be_attested_even_with_valid_rows(self):
        titles = self.root / '.cache/titles.json'
        titles.write_text(json.dumps({'source': 'synthetic.xlsx', 'sheet': 'Sheet1',
                                     'products': [{'product_index': 1, 'encoding_id': '001', 'title': 'Synthetic'}]}))
        identity = expected_identity(self.root)
        argv = ['attest_runtime_cache.py', '--root', str(self.root), 'attest-input',
                '--artifact', str(titles), '--artifact-sha256', hashlib.sha256(titles.read_bytes()).hexdigest(),
                '--batch-id', identity['batch_id'], '--input-fingerprint', identity['input_fingerprint']]
        with patch.object(sys, 'argv', argv), self.assertRaisesRegex(CacheIdentityError, 'attestation_not_allowed'):
            attest_cache_main()
        self.assertFalse(metadata_path(titles).exists())

    def test_titles_generic_identity_does_not_bypass_provenance_or_schema(self):
        titles = self.root / '.cache/titles.json'
        titles.write_text(json.dumps({'source': 'synthetic.xlsx', 'sheet': 'Sheet1',
                                     'products': [{'product_index': 1, 'encoding_id': '001', 'title': 'Synthetic'}]}))
        for producer in ('explicit_human_attestation', 'index_titles.py'):
            write_metadata(self.root, titles, producer)
            with self.assertRaisesRegex(CacheIdentityError, 'titles_'):
                load_checked_json(self.root, titles)
            with self.assertRaisesRegex(CacheIdentityError, 'titles_'):
                load_products(self.root, [1])
            self.assertTrue(any('titles_' in reason for reason in inspect(resolve_runtime_paths(self.root))))

    def test_parse_uses_exact_authenticated_bytes_after_path_replacement(self):
        write_metadata(self.root, self.artifact, 'test')
        checked = runtime_meta._checked_bytes
        def replace_after_check(*args, **kwargs):
            result = checked(*args, **kwargs)
            replacement = self.artifact.with_suffix('.replacement')
            replacement.write_text('{"products": ["unauthenticated"]}')
            replacement.replace(self.artifact)
            return result
        with patch.object(runtime_meta, '_checked_bytes', side_effect=replace_after_check):
            self.assertEqual(load_checked_json(self.root, self.artifact)['products'], [])
        with self.assertRaisesRegex(CacheIdentityError, 'artifact_sha256_mismatch'):
            load_checked_json(self.root, self.artifact)

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
