#!/usr/bin/env python3
"""Build a compact failed-variant-only MiMo repair request."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import load_checked_json, write_metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--selection", required=True)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--workspace")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    request = load_checked_json(paths, paths.runtime_argument(args.request))
    selection = load_checked_json(paths, paths.runtime_argument(args.selection))
    audit = load_checked_json(paths, paths.runtime_argument(args.audit))
    failed = sorted({str(row.get("variant_id")) for row in audit.get("errors", []) if row.get("variant_id")})
    attempt = int(request.get("repair_attempt") or 0) + 1
    limit = int(request.get("repair_limit") or 5)
    if attempt > limit:
        raise SystemExit("返修次数已达上限；必须先完成候选池穷尽证明，再标记EXHAUSTED")
    reasons = {
        variant_id: [
            {key: error.get(key) for key in ("code", "clip", "duration", "with") if error.get(key) is not None}
            for error in audit.get("errors", [])
            if error.get("variant_id") == variant_id
        ]
        for variant_id in failed
    }
    previous = []
    locked = []
    for variant in selection.get("variants", []):
        compact = {
            "variant_id": variant["variant_id"],
            "timeline": [
                {key: clip.get(key) for key in ("source", "start", "end", "role")}
                for clip in variant.get("timeline", [])
            ],
        }
        previous.append(compact)
        if variant["variant_id"] not in failed:
            locked.append(compact)
    used = {clip["source"] for variant in previous for clip in variant["timeline"] if clip.get("source")}
    remaining = [row for row in request.get("candidates", []) if row.get("candidate_id") not in used]
    request.update({
        "task": "repair_failed_variants",
        "repair_variants": failed,
        "failed_reasons": reasons,
        "locked_variants": locked,
        "previous_variants": previous,
        "remaining_candidate_pool": remaining,
        "repair_attempt": attempt,
        "repair_limit": limit,
        "repair_log": list(request.get("repair_log") or []) + [{
            "repair_attempt": attempt,
            "repair_reason": reasons,
            "old_segments": previous,
            "removed_segments": [],
            "added_segments": [],
            "remaining_candidate_pool_count": len(remaining),
            "audit_result": audit.get("status", "REPAIRABLE"),
        }],
    })
    paths.runtime_argument(args.output).write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_metadata(paths, paths.runtime_argument(args.output), "build_repair_request_v2.py", product_id=request.get("product_index"))
    print("返修请求完成：" + ",".join(failed))


if __name__ == "__main__":
    main()
