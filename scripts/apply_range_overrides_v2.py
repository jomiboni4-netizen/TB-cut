#!/usr/bin/env python3
"""Apply explicit local source-time overrides to derived product ranges."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import load_checked_json, write_metadata


def split_range(start: float, end: float, sources: list[dict]) -> list[dict]:
    rows = []
    for source in sources:
        left = max(start, float(source["global_start"]))
        right = min(end, float(source["global_end"]))
        if right > left:
            rows.append({
                "media_stem": source["source_stem"],
                "start": round(left - float(source["global_start"]), 3),
                "end": round(right - float(source["global_start"]), 3),
            })
    return rows


def global_time(point: dict, sources: dict[str, dict]) -> float:
    source = sources[str(point["source_stem"])]
    return float(source["global_start"]) + float(point["time"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    parser.add_argument("--overrides", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    ranges_path = paths.cache_dir / "product_ranges.json"
    document = load_checked_json(paths, ranges_path, artifact_schema_version=1)
    index = load_checked_json(paths, paths.cache_dir / "subtitle_index.json", artifact_schema_version=1)
    sources = {row["source_stem"]: row for row in index["sources"]}
    products = {int(row["product_index"]): row for row in document["products"]}
    overrides = load_checked_json(paths, Path(args.overrides))["products"]
    for override in overrides:
        product = products[int(override["product_index"])]
        start = global_time(override["start"], sources)
        end = global_time(override["end"], sources)
        if end <= start:
            raise SystemExit(f"商品{override['product_index']}覆盖范围无效")
        product["global_start"] = round(start, 3)
        product["global_end"] = round(end, 3)
        product["product_range_duration"] = round(end - start, 3)
        product["ranges"] = split_range(start, end, index["sources"])
        product["confidence"] = "locally_verified_override"
        product["override_reason"] = override.get("reason")
        print(f"商品{override['product_index']} 边界覆盖已应用")
    ranges_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_metadata(paths, ranges_path, "apply_range_overrides_v2.py")


if __name__ == "__main__":
    main()
