#!/usr/bin/env python3
"""Merge failed-only MiMo repair output with locally locked variants."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import load_checked_json, write_metadata


def extract(raw: dict) -> dict:
    text = raw.get("output_text")
    if not isinstance(text, str):
        for item in raw.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    text = content.get("text")
                    break
    if not isinstance(text, str):
        raise RuntimeError("响应缺少 output_text")
    return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", required=True)
    parser.add_argument("--repair-request", required=True)
    parser.add_argument("--repair-result", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--workspace")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    selection = load_checked_json(paths, paths.runtime_argument(args.selection))
    request = load_checked_json(paths, paths.runtime_argument(args.repair_request))
    repair = extract(load_checked_json(paths, paths.runtime_argument(args.repair_result)))
    failed = set(request.get("repair_variants") or [])
    repaired = {row.get("variant_id"): row for row in repair.get("selected_variants") or []}
    if set(repaired) != failed:
        raise SystemExit(f"返修方案不匹配：期望{sorted(failed)}，实际{sorted(repaired)}")
    merged = []
    for variant in selection.get("variants", []):
        variant_id = variant["variant_id"]
        if variant_id in repaired:
            merged.append(repaired[variant_id])
        else:
            merged.append({
                "variant_id": variant_id,
                "logic_outline": variant.get("logic_outline") or [],
                "closure_check": variant.get("closure_check") or {},
                "timeline": [
                    {key: clip.get(key) for key in ("source", "start", "end", "role")}
                    for clip in variant.get("timeline", [])
                ],
            })
    payload = {"selected_variants": sorted(merged, key=lambda row: row["variant_id"]), "duplicate_remove": repair.get("duplicate_remove") or []}
    paths.runtime_argument(args.output).write_text(json.dumps({"output_text": json.dumps(payload, ensure_ascii=False)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_metadata(paths, paths.runtime_argument(args.output), "merge_repair_result_v2.py", product_id=request.get("product_index"))
    print("返修结果已与锁定方案合并")


if __name__ == "__main__":
    main()
