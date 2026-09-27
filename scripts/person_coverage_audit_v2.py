#!/usr/bin/env python3
"""Audit final plans for strict greater-than-70-percent host visibility."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import expected_identity, load_checked_json, write_metadata


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    parser.add_argument("--products", required=True)
    parser.add_argument("--minimum", type=float, default=0.70)
    parser.add_argument("--detector")
    parser.add_argument("--generated", action="store_true", help="审计本批次新生成的方案，忽略旧批准清单")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    identity = expected_identity(paths)
    state = json.loads((paths.state_path).read_text(encoding="utf-8"))
    video_dirs = [Path(path) for path in state.get("video_paths") or [] if Path(path).is_dir()]
    media_by_name = {path.name: path for directory in video_dirs for path in directory.glob("*.mp4")}
    detector = Path(args.detector) if args.detector else paths.cache_dir / "person_coverage_probe"
    if not detector.exists():
        raise SystemExit(f"人物检测器不存在：{detector}")
    rows = []
    for product_index in [int(value) for value in args.products.split(",") if value.strip()]:
        output_dir = paths.cache_dir / f"product_{product_index:02d}_windowed"
        manifest_path = output_dir / "approved_plans.json"
        if manifest_path.exists() and not args.generated:
            manifest = load_checked_json(paths, manifest_path, product_id=product_index, expected=identity, artifact_schema_version=2)
            plan_refs = [row["path"] for row in manifest.get("plans", [])]
        else:
            generated_path = output_dir / "generated_plans.json"
            if not generated_path.exists():
                raise SystemExit(f"商品{product_index}缺少本批次生成清单")
            generated = load_checked_json(paths, generated_path, product_id=product_index, expected=identity, artifact_schema_version=2)
            plan_refs = generated.get("plans", [])
        plan_paths = []
        for reference in plan_refs:
            resolved = paths.resolve_reference(reference)
            if resolved.legacy_absolute:
                print(f"WARN legacy_absolute_plan_path: {reference}")
            plan_paths.append(resolved.path)
        for plan_path in plan_paths:
            plan = load_checked_json(paths, plan_path, product_id=product_index, expected=identity, artifact_schema_version=2)
            fps = int(plan.get("fps") or 30)
            grouped: dict[str, list[str]] = {}
            for clip in plan["clips"]:
                grouped.setdefault(clip["source_file"], []).append(
                    f"{int(clip['source_start_frame']) / fps:.6f}:{int(clip['source_end_frame']) / fps:.6f}"
                )
            person_samples = total_samples = 0
            for source_file, ranges in grouped.items():
                result = subprocess.run(
                    [str(detector), str(media_by_name[source_file]), *ranges, "--summary"],
                    check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                report = json.loads(result.stdout)
                person_samples += int(report["personSamples"])
                total_samples += int(report["totalSamples"])
            ratio = person_samples / total_samples if total_samples else 0.0
            rows.append({
                "product_index": product_index,
                "variant_id": plan["variant_id"],
                "plan": paths.reference(plan_path),
                "plan_digest": digest(plan_path),
                "host_visible_ratio": round(ratio, 6),
                "person_samples": person_samples,
                "total_samples": total_samples,
                "passed": ratio > args.minimum,
                "status": "PASS" if ratio > args.minimum else "REPAIRABLE",
            })
            print(f"商品{product_index} {plan['variant_id']} 人物可见={ratio:.1%} {'通过' if ratio > args.minimum else '失败'}")
    document = {
        "schema_version": 2,
        "pipeline_version": "2.1",
        "minimum_exclusive": args.minimum,
        "passed": all(row["passed"] for row in rows),
        "plans": rows,
    }
    output = paths.cache_dir / "person_coverage_audit.json"
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_metadata(paths, output, "person_coverage_audit_v2.py", identity=identity)
    if not document["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
