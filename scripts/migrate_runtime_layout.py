#!/usr/bin/env python3
"""Conservatively migrate proven runtime path references; dry-run by default."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from runtime_meta import CacheIdentityError, metadata_path, read_metadata, validate_cache, write_metadata
from runtime_paths import ArtifactPathError, RuntimePaths, resolve_runtime_paths
from workspace_preflight import inspect as inspect_workspace


PRODUCT_DIR = re.compile(r"product_(\d+)_")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_names(state: dict) -> set[str]:
    result: set[str] = set()
    for raw in state.get("video_paths") or []:
        path = Path(raw).expanduser()
        result.update(item.name for item in path.glob("*.mp4") if item.is_file()) if path.is_dir() else result.add(path.name)
    return result


def plan_proven(path: Path, product_id: int, coverage: dict, sources: set[str]) -> bool:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
        if (plan.get("schema_version"), str(plan.get("pipeline_version")), plan.get("product_index")) != (2, "2.1", product_id):
            return False
        if not plan.get("clips") or not all(row.get("source_file") in sources for row in plan["clips"]):
            return False
        return any(
            row.get("product_index") == product_id
            and row.get("variant_id") == plan.get("variant_id")
            and row.get("plan_digest") == digest(path)
            for row in coverage.get("plans", [])
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False


def migration(paths: RuntimePaths, *, apply: bool = False, baseline_preflight: Path | None = None) -> dict:
    state = json.loads(paths.state_path.read_text(encoding="utf-8"))
    active = {int(value) for value in state.get("batch", {}).get("product_ids") or []}
    sources = source_names(state)
    coverage_path = paths.cache_dir / "person_coverage_audit.json"
    coverage = json.loads(coverage_path.read_text(encoding="utf-8")) if coverage_path.is_file() else {}
    report = {
        "schema_version": 1,
        "mode": "apply" if apply else "dry-run",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files_scanned": 0, "files_updated": 0, "files_stale": 0,
        "files_unverifiable": 0, "paths_converted": 0,
        "metadata_rebuilt": 0, "inputs_regenerated": 0,
        "regenerated_inputs": [], "current_identity_unverifiable": [],
        "baseline_findings": [], "baseline_classification_counts": {},
        "items": [],
    }

    index_path = paths.cache_dir / "subtitle_index.json"
    if index_path.is_file() and metadata_path(index_path).is_file():
        try:
            validate_cache(paths, index_path)
            if read_metadata(index_path).get("producer") == "index_subtitles.py":
                report["regenerated_inputs"].append(paths.reference(index_path))
                report["inputs_regenerated"] = 1
        except CacheIdentityError:
            pass

    if baseline_preflight is not None:
        counts: Counter[str] = Counter()
        for line in baseline_preflight.read_text(encoding="utf-8").splitlines():
            if not line.startswith("- "):
                continue
            issue, _, detail = line[2:].partition(": ")
            file = detail.split(": ", 1)[0]
            active_file = any(f"product_{pid:02d}_" in file for pid in active)
            inactive_product = PRODUCT_DIR.search(file) is not None and not active_file
            if inactive_product or "before_" in file or "prior_state_" in file:
                category = "C"
            elif "subtitle_index.json" in file or "product_ranges.json" in file:
                category = "A"
            elif "titles.json" in file or "mimo_request.json" in file:
                category = "B"
            elif issue == "cache_identity_invalid":
                category = "C"
            else:
                category = "B"
            counts[category] += 1
            report["baseline_findings"].append({"file": file, "issue": issue, "category": category})
        report["baseline_classification_counts"] = dict(sorted(counts.items()))

    def record(path: Path, status: str, reason: str, conversions: int = 0) -> None:
        report["items"].append({"file": paths.reference(path), "status": status, "reason": reason, "path_conversions": conversions})
        if status == "STALE":
            report["files_stale"] += 1
        if status == "ERROR":
            report["files_unverifiable"] += 1

    def save(path: Path, document: dict, conversions: int) -> None:
        old_bytes = path.read_bytes()
        if not conversions:
            record(path, "SKIPPED", "already_portable")
            return
        had_meta = metadata_path(path).is_file()
        if had_meta:
            try:
                validate_cache(paths, path)
            except CacheIdentityError as exc:
                record(path, "STALE", f"metadata_invalid: {exc}")
                return
        if not apply:
            record(path, "WOULD_UPDATE", "verified_runtime_paths", conversions)
            report["paths_converted"] += conversions
            return
        backup = paths.reports_dir / "runtime_migration_backups" / path.relative_to(paths.workspace_root)
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            backup.write_bytes(old_bytes)
        temporary = path.with_name(path.name + ".migration.tmp")
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        if had_meta:
            write_metadata(paths, path, "migrate_runtime_layout.py")
            report["metadata_rebuilt"] += 1
        report["files_updated"] += 1
        report["paths_converted"] += conversions
        record(path, "UPDATED", "verified_runtime_paths", conversions)

    documents = sorted(
        path for directory in (paths.cache_dir, paths.reports_dir, paths.plans_dir, paths.exports_dir)
        if directory.is_dir() for path in directory.rglob("*.json")
        if not path.name.endswith(".meta.json")
        and path != paths.reports_dir / "runtime_migration_report.json"
        and paths.reports_dir / "runtime_migration_backups" not in path.parents
    )
    report["files_scanned"] = len(documents)
    for path in documents:
        relative = path.relative_to(paths.workspace_root).as_posix()
        match = PRODUCT_DIR.search(relative)
        product_id = int(match.group(1)) if match else None
        if product_id is not None and product_id not in active:
            record(path, "STALE", "outside_current_batch")
            continue
        if path.name in {"generated_plans.json", "approved_plans.json"} and product_id is not None:
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                if (doc.get("schema_version"), str(doc.get("pipeline_version")), doc.get("product_index")) != (2, "2.1", product_id):
                    raise ValueError("manifest_identity_unproven")
                generated = None
                if path.name == "approved_plans.json":
                    generated_path = path.with_name("generated_plans.json")
                    generated = json.loads(generated_path.read_text(encoding="utf-8"))
                    generated_set = {paths.resolve_reference(value).path for value in generated.get("plans", [])}
                converted = 0
                new_rows = []
                for row in doc.get("plans", []):
                    value = row["path"] if isinstance(row, dict) else row
                    resolved = paths.resolve_reference(value)
                    if not plan_proven(resolved.path, product_id, coverage, sources):
                        raise ValueError("plan_source_or_digest_unproven")
                    if generated is not None and resolved.path not in generated_set:
                        raise ValueError("approved_plan_missing_from_generated")
                    if resolved.legacy_absolute:
                        converted += 1
                    new_value = paths.reference(resolved.path)
                    new_rows.append({**row, "path": new_value} if isinstance(row, dict) else new_value)
                if not new_rows:
                    raise ValueError("empty_plan_manifest")
                doc["plans"] = new_rows
                save(path, doc, converted)
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, ArtifactPathError) as exc:
                record(path, "STALE", f"manifest_source_unproven: {exc}")
            continue
        if path == coverage_path:
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                if (doc.get("schema_version"), str(doc.get("pipeline_version"))) != (2, "2.1"):
                    raise ValueError("coverage_version_unproven")
                converted = 0
                for row in doc.get("plans", []):
                    pid = int(row.get("product_index"))
                    if pid not in active:
                        raise ValueError("coverage_contains_other_batch")
                    resolved = paths.resolve_reference(row["plan"])
                    if not plan_proven(resolved.path, pid, coverage, sources):
                        raise ValueError("coverage_plan_digest_unproven")
                    converted += int(resolved.legacy_absolute)
                    row["plan"] = paths.reference(resolved.path)
                save(path, doc, converted)
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, ArtifactPathError) as exc:
                record(path, "STALE", f"coverage_source_unproven: {exc}")
            continue
        if path.name == "resolve_write_report.json":
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                if doc.get("project") != state.get("resolve", {}).get("project_name") or doc.get("target_timeline") != state.get("resolve", {}).get("target_timeline"):
                    raise ValueError("resolve_report_project_mismatch")
                resolved = paths.resolve_reference(doc["backup_project"])
                doc["backup_project"] = paths.reference(resolved.path)
                save(path, doc, int(resolved.legacy_absolute))
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, ArtifactPathError) as exc:
                record(path, "STALE", f"resolve_report_source_unproven: {exc}")
            continue
        if path.name in {"titles.json", "mimo_request.json"}:
            record(path, "STALE", "requires_explicit_human_attestation")
        elif path.name in {"subtitle_index.json", "product_ranges.json"}:
            record(path, "STALE", "regenerate_from_validated_inputs_when_prerequisites_pass")
        elif path.name == "prior_state_20260909.json" or "before_" in path.name:
            record(path, "STALE", "historical_snapshot")
        else:
            record(path, "SKIPPED", "no_supported_path_conversion")

    report["current_identity_unverifiable"] = [
        line.split(": ", 2)[1] for line in inspect_workspace(paths)
        if line.startswith("cache_identity_invalid: ")
    ]
    report["files_unverifiable"] = len(report["current_identity_unverifiable"])
    if apply:
        destination = paths.reports_dir / "runtime_migration_report.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        persisted = report
        if destination.is_file() and not report["files_updated"]:
            previous = json.loads(destination.read_text(encoding="utf-8"))
            previous.update({key: report[key] for key in (
                "regenerated_inputs", "inputs_regenerated", "current_identity_unverifiable",
                "files_unverifiable", "baseline_findings", "baseline_classification_counts",
            )})
            previous["latest_idempotent_check"] = report["created_at"]
            persisted = previous
        destination.write_text(json.dumps(persisted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--workspace")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--baseline-preflight", type=Path)
    args = parser.parse_args()
    report = migration(resolve_runtime_paths(args.repo, args.workspace), apply=args.apply, baseline_preflight=args.baseline_preflight)
    for item in report["items"]:
        if item["status"] != "SKIPPED":
            print(f"{item['status']} {item['file']}: {item['reason']}")
    print(json.dumps({key: report[key] for key in ("mode", "files_scanned", "files_updated", "files_stale", "files_unverifiable", "paths_converted", "metadata_rebuilt", "inputs_regenerated")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
