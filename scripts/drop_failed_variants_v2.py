#!/usr/bin/env python3
"""Drop variants that still fail after repair limit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import load_checked_json, write_metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", required=True)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--workspace")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    selection = load_checked_json(paths, paths.runtime_argument(args.selection))
    audit = load_checked_json(paths, paths.runtime_argument(args.audit))
    if audit.get("status") != "EXHAUSTED":
        raise SystemExit("只有EXHAUSTED审计才允许减少variant数量")
    exhaustion = audit.get("exhaustion") or {}
    if exhaustion.get("full_product_range_scanned") is not True or exhaustion.get("full_candidate_pool_searched") is not True:
        raise SystemExit("缺少完整商品范围与候选池穷尽证明")
    if int(exhaustion.get("repair_attempts") or 0) < int(exhaustion.get("repair_limit") or 5):
        raise SystemExit("返修次数未达上限，禁止减少variant数量")
    failed = {str(error.get("variant_id")) for error in audit.get("errors", []) if error.get("variant_id")}
    kept = []
    for variant in selection.get("variants", []):
        if variant["variant_id"] in failed:
            continue
        kept.append({
            "variant_id": variant["variant_id"],
            "logic_outline": variant.get("logic_outline") or [],
            "closure_check": variant.get("closure_check") or {},
            "timeline": [
                {key: clip.get(key) for key in ("source", "start", "end", "role")}
                for clip in variant.get("timeline", [])
            ],
        })
    if not kept:
        raise SystemExit("没有可保留的合格方案")
    payload = {"selected_variants": kept, "duplicate_remove": [], "status": "EXHAUSTED", "insufficient_reason": "完整范围与候选池已穷尽，失败方案达到返修上限后删除"}
    paths.runtime_argument(args.output).write_text(json.dumps({"output_text": json.dumps(payload, ensure_ascii=False)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_metadata(paths, paths.runtime_argument(args.output), "drop_failed_variants_v2.py", product_id=selection.get("product_index"))
    print(f"删除失败方案={len(failed)} 保留方案={len(kept)}")


if __name__ == "__main__":
    main()
