#!/usr/bin/env python3
"""Read-only check for moving a batch workspace away from its repository."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime_meta import CacheIdentityError, expected_identity, validate_cache
from runtime_paths import ArtifactPathError, RuntimePaths, resolve_runtime_paths


REPO_FILES = (
    "AGENTS.md", "PROJECT_RULES.md", "PROJECT_STATE.template.json",
    "config/content_rules.json", "config/vision_rules.json",
    "scripts/runtime_meta.py", "scripts/runtime_paths.py",
)
MAIN_SCRIPTS = (
    "init_project.py", "fingerprint_inputs.py", "index_subtitles.py", "locate_link_intros.py",
    "apply_range_overrides_v2.py", "build_expanded_codex_request.py", "mimo_review_v2.py",
    "audit_mimo_v2.py", "build_repair_request_v2.py", "merge_repair_result_v2.py",
    "windowed_asr_v2.py", "refine_plans_v2.py", "person_coverage_audit_v2.py",
    "approve_final_plans.py", "resolve_write_v2.py", "validate_resolve_write_v2.py",
    "export_marked_variants.py", "verify_exports.py",
)


def inspect(paths: RuntimePaths) -> list[str]:
    reasons: list[str] = []
    active: set[int] = set()
    for rel in REPO_FILES:
        if not (paths.repo_root / rel).is_file():
            reasons.append(f"repo_file_missing: {rel}")
    if not paths.state_path.is_file():
        reasons.append(f"workspace_state_missing: {paths.state_path}")
    else:
        try:
            state = json.loads(paths.state_path.read_text(encoding="utf-8"))
            active = {int(value) for value in state.get("batch", {}).get("product_ids") or []}
            expected = expected_identity(paths)
        except (CacheIdentityError, OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError) as exc:
            reasons.append(f"workspace_identity_invalid: {exc}")
            expected = None
            active = set()
        if expected:
            key_artifacts = [
                paths.cache_dir / "subtitle_index.json",
                paths.cache_dir / "product_ranges.json",
                paths.cache_dir / "titles.json",
                paths.cache_dir / "person_coverage_audit.json",
                *(paths.cache_dir / f"product_{pid:02d}_rough" / "mimo_request.json" for pid in sorted(active)),
                *(paths.cache_dir / f"product_{pid:02d}_windowed" / "approved_plans.json" for pid in sorted(active)),
            ]
            for artifact in key_artifacts:
                if not artifact.is_file():
                    continue
                try:
                    validate_cache(paths, artifact, expected=expected)
                except (CacheIdentityError, OSError) as exc:
                    reasons.append(f"cache_identity_invalid: {artifact.relative_to(paths.workspace_root)}: {exc}")
    for name in MAIN_SCRIPTS:
        script = paths.repo_root / "scripts" / name
        if not script.is_file():
            reasons.append(f"main_script_missing: {name}")
        elif 'root / ".cache"' in script.read_text(encoding="utf-8") or 'root / "PROJECT_STATE.json"' in script.read_text(encoding="utf-8"):
            reasons.append(f"repo_runtime_path_dependency: {name}")
    if not paths.cache_dir.is_dir():
        reasons.append(f"workspace_cache_missing: {paths.cache_dir}")
    for pid in sorted(active):
        for manifest in (paths.cache_dir / f"product_{pid:02d}_windowed" / "generated_plans.json", paths.cache_dir / f"product_{pid:02d}_windowed" / "approved_plans.json"):
            if not manifest.is_file():
                continue
            try:
                document = json.loads(manifest.read_text(encoding="utf-8"))
                for row in document.get("plans", []):
                    reference = row["path"] if isinstance(row, dict) else row
                    resolved = paths.resolve_reference(reference)
                    if resolved.legacy_absolute:
                        reasons.append(f"legacy_absolute_plan_path: {manifest.relative_to(paths.workspace_root)}")
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, ArtifactPathError) as exc:
                reasons.append(f"plan_reference_invalid: {manifest.relative_to(paths.workspace_root)}: {exc}")
    # Only runtime directory references are relocatable. External media paths and
    # the state project's repo_root are intentional absolute paths.
    runtime_roots = tuple(base / name for base in (paths.workspace_root, paths.repo_root) for name in (".cache", "reports", "plans", "exports"))
    def has_repo_runtime_path(value: object) -> bool:
        if isinstance(value, dict):
            return any(has_repo_runtime_path(item) for item in value.values())
        if isinstance(value, list):
            return any(has_repo_runtime_path(item) for item in value)
        if not isinstance(value, str):
            return False
        raw = Path(value)
        return raw.is_absolute() and any(raw == base or base in raw.parents for base in runtime_roots)
    active_artifacts = [paths.cache_dir / "person_coverage_audit.json", paths.cache_dir / "resolve_write_report.json"]
    for pid in active:
        active_artifacts.extend((paths.cache_dir / f"product_{pid:02d}_windowed" / "generated_plans.json", paths.cache_dir / f"product_{pid:02d}_windowed" / "approved_plans.json"))
    for artifact in active_artifacts:
        if not artifact.is_file():
            continue
        try:
            if has_repo_runtime_path(json.loads(artifact.read_text(encoding="utf-8"))):
                reasons.append(f"repo_absolute_path_in_runtime_json: {artifact.relative_to(paths.workspace_root)}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            reasons.append(f"runtime_json_unreadable: {artifact.relative_to(paths.workspace_root)}: {exc}")
    return list(dict.fromkeys(reasons))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--workspace")
    args = parser.parse_args()
    paths = resolve_runtime_paths(args.repo, args.workspace)
    reasons = inspect(paths)
    print("NOT_READY" if reasons else "READY")
    for reason in reasons:
        print(f"- {reason}")
    raise SystemExit(1 if reasons else 0)


if __name__ == "__main__":
    main()
