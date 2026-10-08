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
    cues = index["cues"]
    results: list[dict] = []
    for product in titles:
        product_index = int(product["product_index"])
        pattern = link_pattern(product_index)
        mentions: list[dict] = []
        for cue_index, cue in enumerate(cues):
            if not pattern.search(cue["text"]):
                continue
            nearby = [
                row for row in cues[max(0, cue_index - 6): cue_index + 7]
                if row["source_stem"] == cue["source_stem"]
                and abs(float(row["global_start"]) - float(cue["global_start"])) <= 22
            ]
            context = "".join(row["text"] for row in nearby)
            score, reasons = score_context(context, pattern)
            mentions.append({
                "source_stem": cue["source_stem"],
                "start": cue["start"],
                "end": cue["end"],
                "global_start": cue["global_start"],
                "score": score,
                "reasons": reasons,
            })

        clusters: list[list[dict]] = []
        for mention in mentions:
            if not clusters or mention["global_start"] - clusters[-1][-1]["global_start"] > 90:
                clusters.append([])
            clusters[-1].append(mention)
        candidates: list[dict] = []
        for cluster in clusters:
            best = max(cluster, key=lambda row: (row["score"], -row["global_start"]))
            candidates.append({
                **best,
                "cluster_start": cluster[0]["global_start"],
                "cluster_end": cluster[-1]["global_start"],
                "mention_count": len(cluster),
            })
        candidates.sort(key=lambda row: (-row["score"], row["global_start"]))
        selected = candidates[0] if candidates else None
        confidence = "high" if selected and selected["score"] >= 7 else "low"
        results.append({
            "product_index": product_index,
            "encoding_id": product.get("encoding_id"),
            "title_present": bool(product.get("title")),
            "confidence": confidence,
            "selected": selected,
            "candidates": candidates,
        })

    anchors = []
    for row in results:
        status, selected = classify_anchor(row)
        row["boundary_status"] = status
        row["ordered_selected"] = selected
        if selected is not None:
            anchors.append((row, selected))
    unresolved = [row["product_index"] for row in results if row["boundary_status"] != "confirmed"]
    # Missing positions cannot be placed on the live timeline safely. Block the
    # complete set rather than guessing which confirmed anchors they lie between.
    suggestions = []
    if not unresolved:
        anchors.sort(key=lambda pair: pair[1]["global_start"])
        for position, (row, anchor) in enumerate(anchors):
            next_anchor = anchors[position + 1][1]["global_start"] if position + 1 < len(anchors) else None
            suggested = {"product_index": row["product_index"],
                         "boundary_provenance": {"start": "automatic_anchor", "end": "inferred_next_anchor_or_stream_end"},
                         "status": "REQUIRES_CONFIRMATION"}
            try:
                start, end = complete_product_range(anchor["global_start"], next_anchor, index["sources"][-1]["global_end"])
                suggested.update(global_start=start, global_end=end, ranges=split_range(start, end, index["sources"]))
                validate_range(suggested, source_map(index))
            except ValueError as exc:
                suggested["status"] = "BLOCKED"
                suggested["blocker"] = str(exc)
            suggestions.append(suggested)
    return {"schema_version": 1, "generator": "locate_link_intros.py:suggestions-v1",
            "ready_for_cutting": False, "status": "BLOCKED" if unresolved or any(r["status"] == "BLOCKED" for r in suggestions) else "REQUIRES_CONFIRMATION",
            "unresolved_products": unresolved, "products": results, "range_suggestions": suggestions}


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
