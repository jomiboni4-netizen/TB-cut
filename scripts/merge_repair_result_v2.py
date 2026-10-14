#!/usr/bin/env python3
"""Merge failed-only MiMo repair output with locally locked variants."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import expected_identity, load_checked_json, write_metadata
from audit_mimo_v2 import semantic_duplicate


def validate_repair_variants(variants, allowed_pool, locked_intervals):
    """Validate repair clips against residual time ranges and locked plans."""
    allowed = {row.get("candidate_id"): row for row in allowed_pool}
    locked = [(row.get("source_file"), float(row["start"]), float(row["end"]),
               str(row.get("variant_id", "locked"))) for row in locked_intervals]
    used = []
    selected_texts = []
    for variant in variants:
        text_parts = []
        for clip in variant.get("timeline", []):
            candidate = allowed.get(clip.get("source"))
            if candidate is None:
                raise ValueError("repair_candidate_not_in_residual_pool")
            start, end = float(clip["start"]), float(clip["end"])
            bounds = candidate.get("time_range") or {}
            if start < float(bounds["start"]) - 0.02 or end > float(bounds["end"]) + 0.02 or end <= start:
                raise ValueError("repair_clip_outside_residual_range")
            for source_file, left, right, _ in locked + used:
                if candidate.get("source_file") == source_file and min(end, right) - max(start, left) > 0:
                    raise ValueError("repair_reuses_locked_or_repaired_source_time")
            used.append((candidate.get("source_file"), start, end, str(variant.get("variant_id"))))
            text_parts.append("".join(str(c.get("text") or "") for c in candidate.get("subtitle", [])
                                       if float(c["end"]) > start and float(c["start"]) < end))
        combined = "".join(text_parts)
        if any(semantic_duplicate(combined, previous) for previous in selected_texts):
            raise ValueError("repair_semantic_duplicate")
        selected_texts.append(combined)


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
    identity = expected_identity(paths)
    selection = load_checked_json(paths, paths.runtime_argument(args.selection), expected=identity)
    request = load_checked_json(paths, paths.runtime_argument(args.repair_request), expected=identity)
    repair = extract(load_checked_json(paths, paths.runtime_argument(args.repair_result), expected=identity))
    failed = set(request.get("repair_variants") or [])
    repaired = {row.get("variant_id"): row for row in repair.get("selected_variants") or []}
    if set(repaired) != failed:
        raise SystemExit(f"返修方案不匹配：期望{sorted(failed)}，实际{sorted(repaired)}")
    try:
        validate_repair_variants(list(repaired.values()), request.get("remaining_candidate_pool", []),
                                 request.get("locked_source_intervals", []))
    except (TypeError, ValueError, KeyError) as exc:
        raise SystemExit(f"返修范围验证失败：{exc}") from exc
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
    write_metadata(paths, paths.runtime_argument(args.output), "merge_repair_result_v2.py", product_id=request.get("product_index"), identity=identity)
    print("返修结果已与锁定方案合并")


if __name__ == "__main__":
    main()
