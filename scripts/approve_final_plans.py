#!/usr/bin/env python3
"""Approve passing final plans while retaining blocked files for audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import expected_identity, load_checked_json, write_metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    parser.add_argument("--products", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    identity = expected_identity(paths)
    for product_index in [int(value) for value in args.products.split(",") if value.strip()]:
        directory = paths.cache_dir / f"product_{product_index:02d}_windowed"
        audit = load_checked_json(paths, directory / "final_plan_audit.json", product_id=product_index, expected=identity, artifact_schema_version=2)
        if audit.get("status") not in {"PASS", "EXHAUSTED"}:
            raise SystemExit(f"商品{product_index}仍可返修，禁止减少条数后批准")
        failed = {str(row.get("variant_id")) for row in audit.get("errors", []) if row.get("variant_id")}
        generated_path = directory / "generated_plans.json"
        if not generated_path.exists():
            raise SystemExit(f"商品{product_index}缺少本次生成清单")
        generated = load_checked_json(paths, generated_path, product_id=product_index, expected=identity, artifact_schema_version=2)
        approved = []
        for reference in generated.get("plans", []):
            resolved = paths.resolve_reference(reference)
            if resolved.legacy_absolute:
                print(f"WARN legacy_absolute_plan_path: {reference}")
            path = resolved.path
            plan = load_checked_json(paths, path, product_id=product_index, expected=identity, artifact_schema_version=2)
            duration = round(sum(float(clip["duration_sec"]) for clip in plan.get("clips", [])), 3)
            if plan["variant_id"] in failed or not 45.0 < duration < 60.0:
                continue
            approved.append({"variant_id": plan["variant_id"], "path": paths.reference(path), "duration": duration})
        if not approved:
            raise SystemExit(f"商品{product_index}没有可批准成片")
        approved_path = directory / "approved_plans.json"
        approved_path.write_text(
            json.dumps({"schema_version": 2, "pipeline_version": "2.1", "product_index": product_index, "plans": approved}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        write_metadata(paths, approved_path, "approve_final_plans.py", product_id=product_index, identity=identity)
        print(f"商品{product_index} 最终方案批准：{len(approved)} 条")


if __name__ == "__main__":
    main()
