#!/usr/bin/env python3
"""Convert final duration/visual failures into repair error codes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import load_checked_json, write_metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", type=int, required=True)
    parser.add_argument("--final-audit", required=True)
    parser.add_argument("--visual-audit", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--workspace")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    final_audit = load_checked_json(paths, paths.runtime_argument(args.final_audit), product_id=args.product)
    visual_audit = load_checked_json(paths, paths.runtime_argument(args.visual_audit))
    errors = [
        error for error in final_audit.get("errors", [])
        if error.get("variant_id")
    ]
    for row in visual_audit.get("plans", []):
        if int(row.get("product_index", -1)) == args.product and row.get("passed") is False:
            errors.append({
                "variant_id": row["variant_id"],
                "code": "HOST_VISIBLE_RATIO_TOO_LOW",
                "duration": row.get("host_visible_ratio"),
            })
    paths.runtime_argument(args.output).write_text(
        json.dumps({
            "schema_version": 2,
            "pipeline_version": "2.1",
            "product_index": args.product,
            "passed": not errors,
            "status": "PASS" if not errors else "REPAIRABLE",
            "errors": errors,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_metadata(paths, paths.runtime_argument(args.output), "collect_post_audit_failures.py", product_id=args.product)
    print(f"商品{args.product} 终检返修错误={len(errors)}")


if __name__ == "__main__":
    main()
