#!/usr/bin/env python3
"""Parse MiMo output and enforce V2 rough-selection hard rules locally."""

from __future__ import annotations

import argparse
import json
import re
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import load_checked_json, write_metadata


REQUIRED_CLOSURE = (
    "enumerations_resolved",
    "references_resolved",
    "independent_opening",
    "complete_ending",
    "forbidden_content_absent",
)
ALLOWED_ROLES = {"product_intro", "selling_point", "evidence", "experience", "closing"}
FORBIDDEN = re.compile(r"价格|价位|售价|到手价|成交价|优惠券|优惠|补贴|满减|立减|券后|平台券|红包|抽奖|\d+\s*(?:元|块钱|号链接)")
CONNECTOR_OPENING = re.compile(r"^(?:嗯|呃|啊|对|好|那)*(?:然后|所以|但是|而且|另外|接着|因为|同时)")
RISK_ENDING = ("然后", "所以", "因为", "但是", "而且", "如果", "比如", "这个", "那个", "我们", "它的", "第一", "第二", "的话")
ELLIPSIS = re.compile(r"(?:…{1,}|\.{3,}|。{3,})$")


def extract_response(raw: dict) -> dict:
    if isinstance(raw.get("selected_variants"), list):
        return raw
    text = raw.get("output_text")
    if not isinstance(text, str):
        for item in raw.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    text = content.get("text")
                    break
    if not isinstance(text, str):
        raise RuntimeError("响应缺少 output_text")
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)


def selected_text(candidate: dict, start: float, end: float) -> str:
    return "".join(
        str(cue.get("text") or "")
        for cue in candidate.get("subtitle", [])
        if float(cue["end"]) > start and float(cue["start"]) < end
    )


def normalized(text: str) -> str:
    return re.sub(r"[\W_]+", "", text).lower()


def semantic_duplicate(left: str, right: str) -> bool:
    left, right = normalized(left), normalized(right)
    if min(len(left), len(right)) < 12:
        return False
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    longest = matcher.find_longest_match(0, len(left), 0, len(right)).size
    return (longest >= 12 and longest / min(len(left), len(right)) >= 0.60) or (
        min(len(left), len(right)) >= 20 and matcher.ratio() >= 0.72
    )


def dependency_risks(text: str) -> list[str]:
    """Connectors request context checks; only demonstrably open dependencies flag repair."""
    clean = normalized(text)
    risks: list[str] = []
    if not clean:
        return ["EMPTY_TEXT"]
    if clean.endswith(RISK_ENDING) or ELLIPSIS.search(text.strip()):
        risks.append("TAIL_CLOSURE_RISK")
    enumeration = re.search(r"([三3])个颜色", clean)
    if enumeration:
        fulfilled = len(re.findall(r"(?:白|黑|灰|咖|绿|蓝|红|紫|黄|粉|米|棕|橙|杏|卡其|颜色)", clean[enumeration.end():]))
        if fulfilled < 3 and "三个颜色你们可以对比" not in clean:
            risks.append("ENUMERATION_UNRESOLVED")
    # Opening connectors alone never fail. They are recorded for downstream context review.
    return risks


def audit_status(errors: list[dict], *, full_pool_scanned: bool = False, repair_attempts: int = 0, repair_limit: int = 5) -> str:
    if not errors:
        return "PASS"
    if full_pool_scanned and repair_attempts >= repair_limit:
        return "EXHAUSTED"
    return "REPAIRABLE"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--selection", required=True)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--workspace")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    request = load_checked_json(paths, paths.runtime_argument(args.request))
    response = extract_response(load_checked_json(paths, paths.runtime_argument(args.result)))
    variants = response.get("selected_variants")
    errors: list[dict] = []
    if not isinstance(variants, list) or not 1 <= len(variants) <= 3:
        raise SystemExit("MiMo 方案数量必须为 1 至 3")
    ids = [str(row.get("variant_id")) for row in variants]
    if len(ids) != len(set(ids)) or any(not re.fullmatch(r"variant_[123]", value) for value in ids):
        raise SystemExit("MiMo 方案 ID 无效或重复")
    candidates = {row["candidate_id"]: row for row in request.get("candidates", [])}
    normalized_variants: list[dict] = []
    for variant in variants:
        variant_id = str(variant["variant_id"])
        closure = variant.get("closure_check") or {}
        for key in REQUIRED_CLOSURE:
            if closure.get(key) is not True:
                errors.append({"variant_id": variant_id, "code": "CLOSURE_FAILED", "field": key})
        timeline = variant.get("timeline")
        if not isinstance(timeline, list) or not timeline:
            errors.append({"variant_id": variant_id, "code": "EMPTY_TIMELINE"})
            timeline = []
        clips: list[dict] = []
        total = 0.0
        for clip_index, row in enumerate(timeline, 1):
            source_id = str(row.get("source") or "")
            candidate = candidates.get(source_id)
            if not candidate:
                errors.append({"variant_id": variant_id, "code": "UNKNOWN_CANDIDATE", "clip": clip_index})
                continue
            try:
                start, end = float(row["start"]), float(row["end"])
            except (KeyError, TypeError, ValueError):
                errors.append({"variant_id": variant_id, "code": "INVALID_TIME", "clip": clip_index})
                continue
            bounds = candidate["time_range"]
            if end <= start or start < float(bounds["start"]) - 0.02 or end > float(bounds["end"]) + 0.02:
                errors.append({"variant_id": variant_id, "code": "OUT_OF_BOUNDS", "clip": clip_index})
                continue
            role_labels = [label.strip() for label in str(row.get("role") or "").split("|") if label.strip()]
            role = role_labels[0] if role_labels else ""
            if not role_labels or any(label not in ALLOWED_ROLES for label in role_labels):
                errors.append({"variant_id": variant_id, "code": "INVALID_ROLE", "clip": clip_index})
            text = selected_text(candidate, start, end)
            if FORBIDDEN.search(text):
                errors.append({"variant_id": variant_id, "code": "FORBIDDEN_CONTENT", "clip": clip_index})
            dependency_check_required = bool(CONNECTOR_OPENING.search(normalized(text)))
            for risk in dependency_risks(text):
                errors.append({"variant_id": variant_id, "code": risk, "clip": clip_index})
            total += end - start
            clips.append({
                "source": source_id,
                "source_file": candidate["source_file"],
                "start": start,
                "end": end,
                "role": role,
                "roles": role_labels,
                "text": text,
                "dependency_check_required": dependency_check_required,
            })
        if not 45.0 < total < 60.0:
            errors.append({"variant_id": variant_id, "code": "DURATION_INVALID", "duration": round(total, 3)})
        normalized_variants.append({
            "variant_id": variant_id,
            "logic_outline": variant.get("logic_outline") or [],
            "closure_check": closure,
            "duration": round(total, 3),
            "timeline": clips,
        })

    for left, right in combinations(normalized_variants, 2):
        for left_clip in left["timeline"]:
            for right_clip in right["timeline"]:
                if left_clip["source_file"] == right_clip["source_file"]:
                    overlap = min(left_clip["end"], right_clip["end"]) - max(left_clip["start"], right_clip["start"])
                    if overlap > 0:
                        errors.append({"variant_id": right["variant_id"], "code": "SOURCE_TIME_OVERLAP", "with": left["variant_id"], "duration": round(overlap, 3)})
                if semantic_duplicate(left_clip["text"], right_clip["text"]):
                    errors.append({"variant_id": right["variant_id"], "code": "SEMANTIC_DUPLICATE", "with": left["variant_id"]})

    by_variant = {row["variant_id"]: [error for error in errors if error.get("variant_id") == row["variant_id"]] for row in normalized_variants}
    for row in normalized_variants:
        row["status"] = audit_status(by_variant[row["variant_id"]])
    selection = {
        "schema_version": 2,
        "pipeline_version": "2.1",
        "product_index": request.get("product_index"),
        "product_title": request.get("rules", {}).get("product_title"),
        "variants": normalized_variants,
    }
    paths.runtime_argument(args.selection).write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_metadata(paths, paths.runtime_argument(args.selection), "audit_mimo_v2.py", product_id=request.get("product_index"))
    audit = {
        "schema_version": 2,
        "pipeline_version": "2.1",
        "product_index": request.get("product_index"),
        "passed": not errors,
        "status": audit_status(errors),
        "variant_statuses": {row["variant_id"]: row["status"] for row in normalized_variants},
        "variant_count": len(normalized_variants),
        "durations": [row["duration"] for row in normalized_variants],
        "errors": errors,
    }
    paths.runtime_argument(args.audit).write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_metadata(paths, paths.runtime_argument(args.audit), "audit_mimo_v2.py", product_id=request.get("product_index"))
    print(f"商品{request.get('product_index')} 粗审={'通过' if not errors else '失败'} 方案={len(normalized_variants)} 错误={len(errors)}")
    if errors:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
