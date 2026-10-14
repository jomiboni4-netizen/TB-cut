#!/usr/bin/env python3
"""Batch-scoped local semantic selection compatible with audit_mimo_v2.py."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import urllib.request

from runtime_meta import expected_identity, load_checked_json, write_metadata
from runtime_paths import resolve_runtime_paths


SYSTEM = """你是本地中文商品口播剪辑语义审核员。仅依据用户提供的当前商品候选字幕选择完整、独立的方案。不要虚构字幕内容或时间。先尝试输出3条，每条严格大于45秒且小于60秒。不同方案的源时间不得重叠，口播内容不得重复。每段须在候选时间范围内，优先按字幕完整句群选范围。只保留当前商品卖点、材质、版型、做工、穿着感受、上身效果、相关搭配解释。排除价格优惠、红包抽奖、链接号、寒暄、闲聊、流程话术、其他商品和空话。开头独立、结尾完整；枚举、因果、条件、转折、指代和承诺闭环。任何项目做不到时不要猜测，写入insufficient_reason。输出JSON对象，结构为：{"selected_variants":[{"variant_id":"variant_1","timeline":[{"source":"候选id","start":0.0,"end":0.0,"role":"product_intro|selling_point|evidence|experience|closing"}],"logic_outline":["商品定义","核心卖点","解释或体验","收尾"],"closure_check":{"enumerations_resolved":true,"references_resolved":true,"independent_opening":true,"complete_ending":true,"forbidden_content_absent":true}}],"duplicate_remove":[],"insufficient_reason":""}。只能输出JSON。"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--products", required=True)
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434/api/chat")
    args = parser.parse_args()
    paths = resolve_runtime_paths(Path(args.root).resolve(), args.workspace)
    identity = expected_identity(paths)

    for product_id in [int(value) for value in args.products.split(",") if value.strip()]:
        folder = paths.cache_dir / f"product_{product_id:02d}_rough"
        request_path = folder / "codex_expanded_request.json"
        output_path = folder / "local_semantic_result.json"
        if output_path.exists():
            raise SystemExit(f"local_result_exists:{product_id}")
        request = load_checked_json(paths, request_path, product_id=product_id, expected=identity)
        candidates = []
        for row in request.get("candidates", []):
            candidates.append({
                "id": row.get("candidate_id"),
                "source_file": row.get("source_file"),
                "range": row.get("time_range"),
                "boundary": row.get("semantic_boundary"),
                "subtitle": row.get("subtitle", []),
            })
        user = {
            "product_title": request.get("rules", {}).get("product_title"),
            "product_range": request.get("product_range"),
            "candidate_pool_summary": request.get("candidate_pool_summary"),
            "candidates": candidates,
        }
        payload = {
            "model": args.model,
            "stream": False,
            "format": "json",
            "think": False,
            "options": {"temperature": 0, "num_ctx": 24576},
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False, separators=(",", ":"))},
            ],
        }
        req = urllib.request.Request(
            args.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=args.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body.get("message", {}).get("content")
        if not isinstance(content, str):
            raise RuntimeError(f"local_model_response_missing_content:{product_id}")
        result = json.loads(content)
        if not isinstance(result.get("selected_variants"), list):
            variants = result.get("selected_variants")
            preview = content[:240].replace("\n", " ")
            raise RuntimeError(
                f"local_model_response_schema_invalid:{product_id}; keys={sorted(result)}; "
                f"selected_variants_type={type(variants).__name__}; preview={preview}"
            )
        result["review_mode"] = "batch_scoped_local_semantic_review"
        result["review_model"] = args.model
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_metadata(paths, output_path, "local_semantic_review_v2.py", product_id=product_id, identity=identity)
        print(f"product_{product_id:02d}: local result written; variants={len(result['selected_variants'])}")


if __name__ == "__main__":
    main()
