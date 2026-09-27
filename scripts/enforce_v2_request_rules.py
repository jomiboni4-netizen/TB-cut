#!/usr/bin/env python3
"""Replace legacy MiMo request constraints with current V2.1 hard rules."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import load_checked_json, write_metadata


V2_REQUIREMENTS = [
    "只选择当前商品内容",
    "目标输出3个互相独立且侧重点不同的成片方案；首次失败必须返修，只有EXHAUSTED才允许减少条数",
    "每条总时长必须严格大于45秒且严格小于60秒",
    "片段数量不设硬上限，以语义完整和切点干净为准",
    "start/end必须在候选范围内，本地随后使用局部ASR、VAD和帧边界细化切点",
    "保留完整卖点逻辑，删除寒暄、重复和流程话术",
    "禁止价格、优惠券、补贴、金额、红包、抽奖、几号链接等交易或促销话术",
    "出现颜色数量、特点数量、第一、另一组等枚举或承诺时，必须在同一方案完整兑现",
    "因果、条件、转折和指代必须闭环；首段独立可懂，末段完整收尾",
    "各方案之间源时间重叠必须为零，不得复用任何源片段",
    "各方案之间不得重复相同或高度相似口播；优先形成不同卖点侧重",
    "候选只是可用语义段，不受最终45至60秒时长限制",
    "你只做语义选择，不决定最终物理切点，不控制Resolve",
    "因为、所以、但是、然后等连接词只触发依赖检查，不得按关键词直接失败",
    "候选池覆盖完整商品范围；标题或链接命中只是商品起点锚点",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("requests", nargs="+")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--workspace")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    for raw in args.requests:
        path = paths.runtime_argument(raw)
        document = load_checked_json(paths, path)
        rules = document.setdefault("rules", {})
        document["pipeline_version"] = "2.1"
        document["rule_version"] = "v2.1"
        rules["variant_min_duration_sec"] = 45.0
        rules["variant_max_duration_sec"] = 60.0
        rules["variant_count"] = 3
        rules["variant_statuses"] = ["PASS", "REPAIRABLE", "EXHAUSTED"]
        rules["repair_limit"] = 5
        rules["physical_overlap_sec"] = 0.0
        rules["requirements"] = V2_REQUIREMENTS
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_metadata(paths, path, "enforce_v2_request_rules.py", product_id=document.get("product_index"))
        print(f"V2.1 请求规则已应用：{path.parent.name}")


if __name__ == "__main__":
    main()
