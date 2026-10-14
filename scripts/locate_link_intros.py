#!/usr/bin/env python3
"""Locate anchors and publish non-authoritative range suggestions only."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from runtime_paths import resolve_runtime_paths

import tempfile
from runtime_meta import CacheIdentityError, load_checked_json, write_metadata, validate_cache
from runtime_publication import BuildSnapshot, generator_lock, publish_pair
from apply_range_overrides_v2 import number, source_map, split_range, validate_range, signature
from semantic_evidence import semantic_windows


DIGITS = "零一二三四五六七八九"


def chinese_number(value: int) -> str:
    if value < 10:
        return DIGITS[value]
    if value < 20:
        return "十" + (DIGITS[value % 10] if value % 10 else "")
    return DIGITS[value // 10] + "十" + (DIGITS[value % 10] if value % 10 else "")


def link_pattern(value: int) -> re.Pattern[str]:
    # A single Chinese digit must not match the tail of 十一/十二/二十一.
    return re.compile(
        rf"(?<!\d)(?:0?{value}|(?<![零一二三四五六七八九十百千万]){chinese_number(value)})\s*号\s*(?:链|连)?接"
    )


def score_context(context: str, pattern: re.Pattern[str]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    expression = pattern.pattern
    tests = [
        (rf"(?:接下来|进入|开始|正式|准备|来到|轮到).{{0,35}}{expression}", 7, "前导转场"),
        (rf"{expression}.{{0,35}}(?:讲解|开始|来了|登场|正式)", 7, "后接讲解"),
        (r"正式.{0,12}讲解|开始.{0,12}讲解|进入.{0,12}讲解", 3, "讲解语义"),
    ]
    for expression_text, points, reason in tests:
        if re.search(expression_text, context):
            score += points
            reasons.append(reason)
    negatives = [
        (r"搭配|内搭|外搭|上面搭|里面搭", -6, "搭配引用"),
        (r"等会|稍后|晚点|先不|还没|没有到|回放", -5, "延后引用"),
        (r"讲完|结束|过完|抄完", -5, "结束语义"),
    ]
    for expression_text, points, reason in negatives:
        if re.search(expression_text, context):
            score += points
            reasons.append(reason)
    return score, reasons


def choose_ordered(products: list[dict]) -> list[dict | None]:
    """Compatibility helper: select independently, never infer live order from IDs."""
    return [classify_anchor(row)[1] for row in products]


def classify_anchor(row):
    ranked = sorted(row["candidates"], key=lambda item: (-item["score"], item["global_start"]))
    selected = ranked[0] if ranked else None
    if selected is None or selected["score"] < 7 or not row.get("title_present", True):
        return "missing_or_low_confidence", None
    if len(ranked) > 1 and selected["score"] - ranked[1]["score"] < 2:
        return "ambiguous", None
    return "confirmed", selected


def complete_product_range(start_anchor: float, next_anchor: float | None, stream_end: float) -> tuple[float, float]:
    """Infer a suggestion only. This is not evidence of a confirmed product end."""
    start = number(start_anchor)
    if start < 0:
        raise ValueError("negative_start_anchor")
    end = number(next_anchor) if next_anchor is not None else number(stream_end)
    if end <= start:
        raise ValueError("下一商品边界必须晚于当前商品起点")
    return round(start, 3), round(end, 3)


def build_suggestions(index, titles):
    source_map(index)
    result = semantic_windows(index, titles)
    cues = index["cues"]
    for row, product in zip(result["products"], titles):
        pattern = link_pattern(int(product["product_index"]))
        # Link-number mentions are diagnostics only. They never rank, create,
        # start, or end a product window.
        row["link_mentions"] = [
            {"source_stem": cue["source_stem"], "start": cue["start"],
             "global_start": cue["global_start"], "role": "AUXILIARY_ONLY"}
            for cue in cues if pattern.search(str(cue.get("text") or ""))
        ]
        row["boundary_status"] = row["status"]
    unresolved = [row["product_index"] for row in result["products"]
                  if row["status"] == "PRODUCT_UNCERTAIN"]
    result.update({
        "generator": "locate_link_intros.py:semantic-windows-v2",
        "ready_for_cutting": False,
        "status": "PRODUCT_UNCERTAIN" if unresolved else "ROUGH_CANDIDATES_REVIEW_REQUIRED",
        "unresolved_products": unresolved,
        "range_suggestions": [],
    })
    return result


def generate(paths):
    """Publish only diagnostics/suggestions; never open product_ranges for writing.

    Existing confirmed OR unknown-provenance ranges are protected identically.
    Removing them does not promote automatic suggestions to accepted ranges.
    """
    with generator_lock(paths):
        snapshot = BuildSnapshot(paths)
        inputs = [paths.cache_dir / name for name in ("subtitle_index.json", "titles.json")]
        signatures = {path: signature(path) for path in inputs}
        index, titles = [load_checked_json(paths, path, expected=snapshot.identity) for path in inputs]
        model = build_suggestions(index, titles["products"])
        def check_inputs():
            snapshot.check()
            if any(signature(path) != stamp for path, stamp in signatures.items()):
                raise CacheIdentityError("artifact_changed_during_generation")
        output = paths.cache_dir / "boundary_suggestions.json"
        with tempfile.TemporaryDirectory(prefix=".boundary-suggestions-", dir=paths.cache_dir) as temporary:
            staged = Path(temporary) / output.name
            staged.write_text(json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
            meta = write_metadata(paths, staged, "locate_link_intros.py", identity=snapshot.identity)
            validate_cache(paths, staged, expected=snapshot.identity)
            publish_pair(staged, meta, output, before_commit=check_inputs)
        return model


def main():
    parser = argparse.ArgumentParser(description="Generate boundary suggestions, never accepted ranges")
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    args = parser.parse_args()
    result = generate(resolve_runtime_paths(Path(args.root).resolve(), args.workspace))
    print(result["status"] + ": automatic suggestions are not accepted product ranges")
    raise SystemExit(2)


if __name__ == "__main__":
    main()
