#!/usr/bin/env python3
"""Compare current Resolve timeline snapshot with first approved cut plans."""

from __future__ import annotations

import json
import re
import argparse
from pathlib import Path
from runtime_paths import RuntimePaths, resolve_runtime_paths


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = re.compile(r"商品(\d+)")


def overlap_frames(left: list[dict], right: list[dict]) -> int:
    total = 0
    for a in left:
        for b in right:
            if a["source_file"] != b["source_file"]:
                continue
            total += max(0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    return total


def current_by_product(snapshot: dict) -> dict[int, list[dict]]:
    clips = snapshot["video_tracks"]["1"]
    markers = sorted(snapshot["markers"], key=lambda row: row["frame"])
    output: dict[int, list[dict]] = {}
    for index, marker in enumerate(markers):
        match = PRODUCT.search(str(marker.get("note") or ""))
        if not match:
            continue
        product = int(match.group(1))
        end_bound = markers[index + 1]["frame"] if index + 1 < len(markers) else 2**63 - 1
        rows = [row for row in clips if marker["frame"] <= row["record_start"] < end_bound]
        groups: list[list[dict]] = []
        for row in rows:
            if not groups or row["record_start"] != groups[-1][-1]["record_end"]:
                groups.append([])
            groups[-1].append(row)
        output[product] = [{
            "duration_frames": group[-1]["record_end"] - group[0]["record_start"],
            "clip_count": len(group),
            "clips": [
                {"source_file": row["media"], "start": row["source_start"], "end": row["source_end"]}
                for row in group
            ],
        } for group in groups]
    return output


def original_by_product(paths: RuntimePaths) -> dict[int, list[dict]]:
    output: dict[int, list[dict]] = {}
    for manifest_path in sorted(paths.cache_dir.glob("product_*_windowed/approved_plans.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        product = int(manifest["product_index"])
        variants = []
        for row in manifest.get("plans", []):
            plan = json.loads(paths.resolve_reference(row["path"]).path.read_text(encoding="utf-8"))
            clips = [{
                "source_file": clip["source_file"],
                "start": int(clip["source_start_frame"]),
                "end": int(clip["source_end_frame"]),
            } for clip in plan.get("clips", [])]
            variants.append({
                "variant_id": plan.get("variant_id"),
                "duration_frames": sum(clip["end"] - clip["start"] for clip in clips),
                "clip_count": len(clips),
                "clips": clips,
            })
        output[product] = variants
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--workspace")
    args = parser.parse_args()
    paths = resolve_runtime_paths(args.root, args.workspace)
    snapshot = json.loads((paths.cache_dir / "current_timeline_snapshot.json").read_text(encoding="utf-8"))
    fps = float(snapshot["fps"])
    current = current_by_product(snapshot)
    original = original_by_product(paths)
    products = []
    for product in sorted(set(current) | set(original)):
        current_rows = current.get(product, [])
        original_rows = original.get(product, [])
        matched = []
        available = set(range(len(original_rows)))
        for current_index, current_row in enumerate(current_rows):
            choices = [(overlap_frames(current_row["clips"], original_rows[index]["clips"]), index) for index in available]
            overlap, original_index = max(choices, default=(0, -1))
            if original_index >= 0:
                available.remove(original_index)
                original_row = original_rows[original_index]
                matched.append({
                    "current_variant": current_index + 1,
                    "original_variant": original_row.get("variant_id"),
                    "current_duration": round(current_row["duration_frames"] / fps, 3),
                    "original_duration": round(original_row["duration_frames"] / fps, 3),
                    "duration_delta": round((current_row["duration_frames"] - original_row["duration_frames"]) / fps, 3),
                    "current_clip_count": current_row["clip_count"],
                    "original_clip_count": original_row["clip_count"],
                    "shared_source_seconds": round(overlap / fps, 3),
                    "current_retained_ratio": round(overlap / current_row["duration_frames"], 4) if current_row["duration_frames"] else 0,
                })
        products.append({
            "product_index": product,
            "original_variant_count": len(original_rows),
            "current_variant_count": len(current_rows),
            "original_clip_count": sum(row["clip_count"] for row in original_rows),
            "current_clip_count": sum(row["clip_count"] for row in current_rows),
            "original_duration": round(sum(row["duration_frames"] for row in original_rows) / fps, 3),
            "current_duration": round(sum(row["duration_frames"] for row in current_rows) / fps, 3),
            "matched": matched,
        })
    transforms = [row.get("transform") or {} for row in snapshot["video_tracks"]["1"]]
    reframed = sum(1 for row in transforms if float(row.get("ZoomX", 1)) != 1 or float(row.get("ZoomY", 1)) != 1 or float(row.get("Pan", 0)) != 0 or float(row.get("Tilt", 0)) != 0)
    report = {
        "fps": fps,
        "original_variant_count": sum(row["original_variant_count"] for row in products),
        "current_variant_count": sum(row["current_variant_count"] for row in products),
        "original_clip_count": sum(row["original_clip_count"] for row in products),
        "current_clip_count": sum(row["current_clip_count"] for row in products),
        "original_duration": round(sum(row["original_duration"] for row in products), 3),
        "current_duration": round(sum(row["current_duration"] for row in products), 3),
        "reframed_clip_count": reframed,
        "transform_examples": transforms[:1],
        "products": products,
    }
    (paths.cache_dir / "timeline_difference.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
