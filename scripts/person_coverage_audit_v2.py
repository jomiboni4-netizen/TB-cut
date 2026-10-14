#!/usr/bin/env python3
"""Audit final plans for strict greater-than-70-percent host visibility."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import expected_identity, load_checked_json, write_metadata
from review_compute_cache import cached_probe, probe_key


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def registered_vision(plan, sources, detector, *, paths):
    from adaptive_vision import audit
    return audit(paths, plan, sources, detector)


def main() -> None:
    raise SystemExit('legacy_person_counts_not_person_subject_evidence: use registered scheduler exact-plan audit')
    started = time.perf_counter()
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
    video_inputs = [Path(path) for path in state.get("video_paths") or []]
    media_by_name = {
        media.name: media
        for item in video_inputs
        for media in (item.glob("*.mp4") if item.is_dir() else [item])
        if media.is_file() and media.suffix.lower() == ".mp4"
    }
    detector = Path(args.detector) if args.detector else paths.cache_dir / "person_coverage_probe"
    if args.detector is None:
        source = root / "scripts" / "person_coverage_probe.swift"
        if not source.is_file():
            raise SystemExit(f"人物检测器源码不存在：{source}")
        if not detector.is_file() or detector.stat().st_mtime_ns < source.stat().st_mtime_ns:
            detector.parent.mkdir(parents=True, exist_ok=True)
            module_cache = paths.cache_dir / "swift_module_cache"
            module_cache.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["swiftc", "-module-cache-path", str(module_cache), "-O", "-o", str(detector), str(source)],
                check=True,
            )
            print("已从当前 Swift 源码重建本地 Apple Vision 人物检测器")
    if not detector.exists():
        raise SystemExit(f"人物检测器不存在：{detector}")
    detector_digest = digest(detector)
    cache_hits = computed = 0
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
                try:
                    media = media_by_name[source_file]
                    def compute():
                        result = subprocess.run(
                            [str(detector), str(media), *ranges, "--summary"],
                            check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        )
                        return json.loads(result.stdout)
                    report, hit = cached_probe(
                        paths.cache_dir / "person_probe_cache_v1",
                        probe_key(media, detector_digest, ranges), compute,
                    )
                    cache_hits += int(hit)
                    computed += int(not hit)
                except subprocess.CalledProcessError as exc:
                    detail = (exc.stderr or "").strip()
                    raise SystemExit(
                        "Apple Vision 人物检测器执行失败；这不是‘无人’结果。"
                        + (f"诊断：{detail}" if detail else f"退出码：{exc.returncode}")
                        + "。请在沙盒外重试；身份识别或额外模型不是人物出镜门槛。"
                    ) from exc
                person_samples += int(report["personSamples"])
                total_samples += int(report["totalSamples"])
            if total_samples == 0:
                raise SystemExit(f"商品{product_index}的方案没有得到任何人物检测样本；不能视为审核通过")
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
        "compute_cache_version": 1,
        "cache_hits": cache_hits,
        "computed_groups": computed,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    output = paths.cache_dir / "person_coverage_audit.json"
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_metadata(paths, output, "person_coverage_audit_v2.py", identity=identity)
    print(f"人物检测缓存复用={cache_hits}，新计算={computed}")
    if not document["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
