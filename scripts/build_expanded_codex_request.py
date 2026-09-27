#!/usr/bin/env python3
"""Build a wide candidate pool across each complete product range."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import expected_identity, load_checked_json, write_metadata


FORBIDDEN = re.compile(
    r"价格|价位|售价|到手价|成交价|优惠券|优惠|补贴|满减|立减|券后|平台券|红包|抽奖|"
    r"福袋|倒计时|库存|关注|点赞|\d+\s*(?:元|块钱|号链接)|几号链接|上车|拍下|"
    r"欢迎.{0,12}(?:来到|进入|宝宝|姐妹)|主播.{0,12}(?:后台|操作)|后台|流程|对焦|镜头|摄像头"
)


def build_wide_candidates(cues: list[dict], stem: str, title: str) -> tuple[list[dict], list[dict]]:
    """Keep every useful continuous run; impose no duration bound."""
    kept_runs: list[list[dict]] = []
    removed: list[dict] = []
    current: list[dict] = []
    for cue in cues:
        if FORBIDDEN.search(str(cue.get("text") or "")):
            if current:
                kept_runs.append(current)
                current = []
            removed.append({"start": cue["start"], "end": cue["end"], "text": cue["text"], "reason": "forbidden_content"})
            continue
        current.append(cue)
    if current:
        kept_runs.append(current)

    candidates: list[dict] = []
    title_terms = [term for term in re.split(r"[/\s]+", title or "") if len(term) >= 2]
    for index, run in enumerate(kept_runs, 1):
        start, end = float(run[0]["start"]), float(run[-1]["end"])
        text = "".join(str(row.get("text") or "") for row in run)
        candidates.append({
            "candidate_id": f"s{stem}_wide_{index:03d}",
            "source_file": f"{stem}.mp4",
            "time_range": {"start": round(start, 3), "end": round(end, 3)},
            "asr_text": text,
            "local_title_matches": [term for term in title_terms if term in text],
            "subtitle": [
                {"start": float(row["start"]), "end": float(row["end"]), "text": str(row["text"])}
                for row in run
            ],
            "semantic_boundary": {
                "start_reason": "product_range_start" if index == 1 else "after_forbidden_content",
                "end_reason": "product_range_end" if index == len(kept_runs) else "forbidden_content",
                "duration_sec": round(end - start, 3),
                "standalone_candidate": False,
            },
        })
    return candidates, removed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    parser.add_argument("--products", required=True)
    parser.add_argument("--max-block", type=float, help="已弃用；V2.1 candidate pool 不限长度")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    identity = expected_identity(paths)
    wanted = {int(value) for value in args.products.split(",") if value.strip()}
    ranges = {int(row["product_index"]): row for row in load_checked_json(paths, paths.cache_dir / "product_ranges.json", expected=identity, artifact_schema_version=1)["products"]}
    cues = load_checked_json(paths, paths.cache_dir / "subtitle_index.json", expected=identity, artifact_schema_version=1)["cues"]
    by_source: dict[str, list[dict]] = {}
    for cue in cues:
        by_source.setdefault(str(cue["source_stem"]), []).append(cue)
    for product_index in sorted(wanted):
        product = ranges[product_index]
        candidates, removed = [], []
        for source_range in product["ranges"]:
            stem = str(source_range["media_stem"])
            source_cues = [
                cue
                for cue in by_source.get(stem, [])
                if float(cue["end"]) > float(source_range["start"]) and float(cue["start"]) < float(source_range["end"])
            ]
            rows, rejected = build_wide_candidates(source_cues, stem, product["title"])
            candidates.extend(rows)
            removed.extend(rejected)
        old = load_checked_json(paths, paths.cache_dir / f"product_{product_index:02d}_rough" / "mimo_request.json", product_id=product_index, expected=identity)
        old["model"] = "mimo-v2.5-pro"
        old["pipeline_version"] = "2.1"
        old["product_range"] = {
            "start": product["global_start"],
            "end": product["global_end"],
            "duration": product.get("product_range_duration", round(float(product["global_end"]) - float(product["global_start"]), 3)),
            "basis": product.get("range_basis", "product_start_anchor_to_next_real_product_start"),
        }
        old["rules"]["phase"] = "完整商品范围宽候选池语义选择"
        old["rules"]["requirements"] = [
            "仅在已验证的当前商品区间内选择",
            "每条成片严格大于45秒且小于60秒",
            "候选不限长度，允许从长语义块内部截取完整句群，不能截断因果、转折、枚举或指代",
            "每条开头必须独立，结尾必须闭合",
            "禁止价格、优惠、抽奖、链接号和直播流程话术",
            "不同版本源时间零重叠、语义不重复；首次失败必须返修，只有EXHAUSTED才可减少条数",
            "因为、所以、但是、然后等连接词只触发依赖检查，不按关键词直接淘汰",
        ]
        old["candidates"] = candidates
        old["local_forbidden_cues"] = removed
        output = paths.cache_dir / f"product_{product_index:02d}_rough" / "codex_expanded_request.json"
        pool_duration = round(sum(float(row["time_range"]["end"]) - float(row["time_range"]["start"]) for row in candidates), 3)
        old["candidate_pool_summary"] = {
            "segment_count": len(candidates),
            "duration": pool_duration,
            "product_range_duration": old["product_range"]["duration"],
            "coverage": round(pool_duration / old["product_range"]["duration"], 6) if old["product_range"]["duration"] else 0.0,
        }
        output.write_text(json.dumps(old, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_metadata(paths, output, "build_expanded_codex_request.py", product_id=product_index, identity=identity)
        print(f"商品{product_index} 宽候选={len(candidates)} 候选时长={pool_duration:g}秒")


if __name__ == "__main__":
    main()
