#!/usr/bin/env python3
"""Send a compact, pre-cleaned semantic review request to MiMo."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.request
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import CacheIdentityError, expected_identity, load_checked_json, read_metadata, validate_cache, write_metadata


ENDPOINT = "https://api.xiaomimimo.com/v1/responses"


def compact_request(document: dict) -> dict:
    rules = dict(document.get("rules") or {})
    rules.pop("encoding_id", None)
    rules.pop("product_index", None)
    candidates = []
    for candidate in document.get("candidates", []):
        candidates.append({
            "id": candidate.get("candidate_id"),
            "range": [candidate.get("time_range", {}).get("start"), candidate.get("time_range", {}).get("end")],
            "boundary": [
                candidate.get("semantic_boundary", {}).get("start_reason"),
                candidate.get("semantic_boundary", {}).get("end_reason"),
                candidate.get("semantic_boundary", {}).get("standalone_candidate"),
            ],
            "subtitle": [
                [cue.get("start"), cue.get("end"), cue.get("text")]
                for cue in candidate.get("subtitle", [])
            ],
        })
    compact = {
        "pipeline_version": "2.1",
        "rules": rules,
        "candidates": candidates,
        "output_schema": {
            "selected_variants": [{
                "variant_id": "variant_1",
                "timeline": [{"source": "candidate_id", "start": 0.0, "end": 0.0, "role": "product_intro|selling_point|evidence|experience|closing"}],
                "logic_outline": ["商品定义", "核心卖点", "解释或体验", "收尾"],
                "closure_check": {
                    "enumerations_resolved": True,
                    "references_resolved": True,
                    "independent_opening": True,
                    "complete_ending": True,
                    "forbidden_content_absent": True,
                },
            }],
            "duplicate_remove": [],
            "insufficient_reason": "不足3条时说明原因，否则为空",
        },
    }
    if document.get("task") == "repair_failed_variants":
        candidates = document.get("remaining_candidate_pool") or candidates
        compact.update({
            "task": "repair_failed_variants",
            "repair_variants": document.get("repair_variants") or [],
            "failed_reasons": document.get("failed_reasons") or {},
            "locked_variants": document.get("locked_variants") or [],
            "previous_variants": document.get("previous_variants") or [],
            "remaining_candidate_pool": candidates,
            "repair_attempt": document.get("repair_attempt"),
            "repair_limit": document.get("repair_limit"),
        })
    return compact


def understanding_prompt(document: dict) -> str:
    compact = {
        "stage": "A_understand_and_extract",
        "product_title": document.get("product_title"),
        "windows": document.get("windows") or [],
        "required_output": {
            "product_identity": {"match": "MATCH|PRODUCT_UNCERTAIN|MISMATCH", "evidence": [], "conflicts": []},
            "windows": [{"window_id": "w01", "role": "formal_explanation|teaser|outfit_mention|return_supplement|uncertain",
                         "evidence": "", "sentence_groups": [{"group_id": "g01", "refs": ["q0001"],
                         "text": "must exactly match cited cleaned source text", "selling_point_category": "",
                         "claim": "", "relations": [], "standalone": True,
                         "needs_context": [], "product_attribution": "MATCH|OTHER_PRODUCT|PRODUCT_UNCERTAIN"}]}],
            "excluded": [{"refs": [], "reason": "other_product|low_value|duplicate|promotion"}],
            "insufficient_reason": "",
        },
    }
    return (
        "只输出JSON。你只做阶段A：理解商品归属并提取字幕句群，不组装视频、不选物理切点。"
        "Excel商品标题/编号用于标识商品；链接号只是辅助线索。按口播品类、面料、版型、颜色、结构和上下文判断。"
        "events中的link_announcement只说明该时刻有人报链接号，数字已遮蔽；不得由此认定商品或切换边界。"
        "区分提前预告、搭配提及、正式讲解和回头补充；不同商品描述相似时标PRODUCT_UNCERTAIN并写冲突。"
        "逐窗口检查独立商品特征和反证；不能因相邻、同一模特搭配或先前报链接就自动归属。"
        "每个句群必须引用输入中的原始refs，text须为这些来源的逐字拼接，不得改写或补造。"
        "提取卖点类别、具体主张、原文、引用时间、因果/转折/列举/指代关系、能否独立使用及需保留上下文。"
        "链接号不能决定范围；窗口是粗略候选，可以多个、不连续且较宽。被标记需切点检查的句子不能声称可直接剪。"
        "输入已经本地去除了促销禁用子句；不得推测被删内容。" +
        json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    )


def assembly_prompt(document: dict) -> str:
    compact_groups = []
    for group in document.get("verified_groups") or []:
        refs = group.get("actual_refs") or group.get("source_refs") or []
        by_source = {}
        for ref in refs:
            source = str(ref.get("source_alias") or ref.get("source") or ref.get("source_stem") or "")
            start = ref.get("local_start", ref.get("start"))
            end = ref.get("local_end", ref.get("end"))
            if source and isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
                by_source.setdefault(source, []).append((float(start), float(end)))
        spans = []
        for source, intervals in sorted(by_source.items()):
            intervals.sort()
            left, right = intervals[0]
            for start, end in intervals[1:]:
                if start <= right:
                    right = max(right, end)
                else:
                    spans.append([source, round(left, 3), round(right, 3)])
                    left, right = start, end
            spans.append([source, round(left, 3), round(right, 3)])
        compact_groups.append({
            "group_id": group.get("group_id"),
            "text": group.get("text", ""),
            "category": group.get("selling_point_category", group.get("category", "")),
            "claim": group.get("claim", ""),
            "relations": group.get("relations", []),
            "standalone": group.get("standalone", True),
            "needs_context": group.get("needs_context", []),
            "spans": spans,
        })
    compact = {
        "stage": "B_assemble_from_verified_sentence_groups",
        "product_title": document.get("product_title"),
        "verified_groups": compact_groups,
        "locked_variants": document.get("locked_variants") or [],
        "repair_variants": document.get("repair_variants") or [],
        "failed_reasons": document.get("failed_reasons") or {},
        "required_output": {"selected_variants": [{"variant_id": "variant_1",
                          "group_ids": ["g01", "g02"], "focus": "简短卖点组合"}],
                          "insufficient_reason": "", "duplicate_remove": []},
    }
    return (
        "只输出JSON。阶段A已核验商品、原文和来源，不要重判、复述或改写。"
        "仅用完整句群，按顺序输出group_ids及简短focus；时长按spans估算。"
        "最多三版；允许零版并给insufficient_reason，不凑数。每版严格>45s且<60s；版本间不得复用句群或重叠spans。"
        "遵守relations/needs_context中的因果、转折和指代，不机械保留‘因为’。模型自报closure不作为审核证据。"
        "不得生成字幕、时间或来源；音频切点、同步、人物比例及最终语义须本地审核。" +
        json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    )


def prompt(document: dict, stage: str = "assemble") -> str:
    if stage == "understand":
        return understanding_prompt(document)
    if stage == "assemble" and "verified_groups" in document:
        return assembly_prompt(document)
    if stage not in {"assemble", "repair"}:
        raise ValueError("unknown_mimo_semantic_stage")
    compact = compact_request(document)
    repair = document.get("task") == "repair_failed_variants"
    task_rule = (
        "只重做repair_variants列出的失败方案；locked_variants完全锁定，不得修改或复用其源时间。"
        "输出selected_variants时只包含返修方案，variant_id必须保持不变。"
        if repair else
        "首次选择必须尝试输出3条独立方案，ID依次使用variant_1、variant_2、variant_3；失败项交给返修，不得自行提前减量。"
    )
    return (
        "只输出标准JSON，不要Markdown。你是淘宝商品口播剪辑语义选择器。"
        + task_rule +
        "从完整候选池选择、删减和排序；返修时优先使用remaining_candidate_pool并保持已通过方案锁定。"
        "每条总时长必须严格大于45秒且严格小于60秒。"
        "每段start/end必须位于对应候选range内。片段数量不限，以语义完整为准。"
        "删除价格、金额、优惠、补贴、红包、抽奖、链接号、寒暄、闲聊、流程话术和无关商品。"
        "保证枚举、因果、条件、转折、指代和承诺闭环；连接词只提示依赖检查，结果在前、因为在后的正常表达可通过；开头独立，结尾完整。"
        "不同variant源时间重叠必须为零，口播语义也不得重复；每条尽量采用不同卖点。"
        "不要修改商品边界，不决定最终物理切点，不控制Resolve。"
    ) + json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--stage", choices=("understand", "assemble", "repair"), default="assemble")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--workspace")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    request_path = paths.runtime_argument(args.request)
    output_path = paths.runtime_argument(args.output)
    identity = expected_identity(paths)
    document = load_checked_json(paths, request_path, expected=identity)
    request_meta = read_metadata(request_path)
    request_bytes = request_path.read_bytes()
    request_digest = hashlib.sha256(request_bytes).hexdigest()
    meta_path = output_path.with_name(output_path.stem + "_meta.json")
    if output_path.exists():
        validate_cache(paths, output_path, product_id=request_meta.get("product_id"), expected=identity)
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        if meta.get("request_digest") == request_digest and meta.get("status") == "completed":
            print("MiMo 结果命中缓存")
            return
        raise CacheIdentityError(f"mimo_result_request_mismatch: {output_path}; choose a new output path")
    key = os.environ.get("MIMO_API_KEY")
    if not key:
        raise SystemExit("MIMO_API_KEY 未设置")
    lock_path = output_path.with_suffix(output_path.suffix + ".lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit("同一 MiMo 请求正在执行，拒绝重复发送")
    try:
        os.write(descriptor, request_digest.encode())
        os.close(descriptor)
        payload = {
            "model": "mimo-v2.6-pro",
            "input": prompt(document, args.stage),
            "temperature": 0.1,
        }
        meta_path.write_text(json.dumps({"request_digest": request_digest, "status": "in_progress"}, indent=2) + "\n", encoding="utf-8")
        request = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                response_text = response.read().decode("utf-8")
        except BaseException:
            meta_path.write_text(json.dumps({"request_digest": request_digest, "status": "uncertain"}, indent=2) + "\n", encoding="utf-8")
            raise
        output_path.write_text(response_text, encoding="utf-8")
        write_metadata(paths, output_path, "mimo_review_v2.py", product_id=request_meta.get("product_id"), identity=identity)
        meta_path.write_text(json.dumps({"request_digest": request_digest, "status": "completed"}, indent=2) + "\n", encoding="utf-8")
        print("MiMo 语义选择完成")
    finally:
        lock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
