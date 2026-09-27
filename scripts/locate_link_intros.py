#!/usr/bin/env python3
"""Locate product-start anchors, then derive complete product ranges."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import load_checked_json, write_metadata


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
    """Choose one monotonically increasing candidate per product."""
    target_gap = 980.0
    minimum_gap = 180.0
    maximum_gap = 3200.0
    states: list[dict[int, tuple[float, int | None]]] = []
    for product_pos, product in enumerate(products):
        current: dict[int, tuple[float, int | None]] = {}
        for candidate_pos, candidate in enumerate(product["candidates"]):
            base = max(-6.0, min(12.0, float(candidate["score"])))
            if product_pos == 0:
                current[candidate_pos] = (base - abs(candidate["global_start"] - 400.0) / 300.0, None)
                continue
            best: tuple[float, int | None] | None = None
            for previous_pos, (previous_score, _) in states[-1].items():
                previous = products[product_pos - 1]["candidates"][previous_pos]
                gap = float(candidate["global_start"]) - float(previous["global_start"])
                if not minimum_gap <= gap <= maximum_gap:
                    continue
                transition = base - abs(gap - target_gap) / 260.0
                proposed = (previous_score + transition, previous_pos)
                if best is None or proposed[0] > best[0]:
                    best = proposed
            if best is not None:
                current[candidate_pos] = best
        states.append(current)
    if not states or not states[-1]:
        return [None] * len(products)
    chosen_positions = [0] * len(products)
    chosen_positions[-1] = max(states[-1], key=lambda pos: states[-1][pos][0])
    for product_pos in range(len(products) - 1, 0, -1):
        previous_pos = states[product_pos][chosen_positions[product_pos]][1]
        if previous_pos is None:
            return [None] * len(products)
        chosen_positions[product_pos - 1] = previous_pos
    return [
        products[index]["candidates"][candidate_pos]
        for index, candidate_pos in enumerate(chosen_positions)
    ]


def split_range(start: float, end: float, sources: list[dict]) -> list[dict]:
    ranges: list[dict] = []
    for source in sources:
        left = max(start, float(source["global_start"]))
        right = min(end, float(source["global_end"]))
        if right > left:
            ranges.append({
                "media_stem": source["source_stem"],
                "start": round(left - float(source["global_start"]), 3),
                "end": round(right - float(source["global_start"]), 3),
            })
    return ranges


def complete_product_range(start_anchor: float, next_anchor: float | None, stream_end: float) -> tuple[float, float]:
    """Treat title/link hits as anchors; range continues to next real start."""
    start = max(0.0, float(start_anchor))
    end = float(next_anchor) if next_anchor is not None else float(stream_end)
    if end <= start:
        raise ValueError("下一商品边界必须晚于当前商品起点")
    return round(start, 3), round(end, 3)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    index = load_checked_json(paths, paths.cache_dir / "subtitle_index.json", artifact_schema_version=1)
    titles = load_checked_json(paths, paths.cache_dir / "titles.json")["products"]
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

    # Select anchors independently, then sort by live time. Title-sheet order is not a boundary rule.
    anchors: list[tuple[dict, dict]] = []
    for row in results:
        ranked = sorted(row["candidates"], key=lambda candidate: (-candidate["score"], candidate["global_start"]))
        selected = ranked[0] if ranked else None
        ambiguous = bool(selected and len(ranked) > 1 and float(selected["score"]) - float(ranked[1]["score"]) < 2)
        row["ordered_selected"] = selected
        row["boundary_status"] = "ambiguous" if ambiguous else "confirmed" if selected and selected["score"] >= 7 else "missing_or_low_confidence"
        if selected and selected["score"] >= 7 and not ambiguous and row["title_present"]:
            anchors.append((row, selected))
    anchors.sort(key=lambda pair: float(pair[1]["global_start"]))
    title_by_product = {int(row["product_index"]): row for row in titles}
    range_products: list[dict] = []
    for position, (row, selected) in enumerate(anchors):
        next_selected = anchors[position + 1][1] if position + 1 < len(anchors) else None
        start, end = complete_product_range(
            float(selected["global_start"]),
            float(next_selected["global_start"]) if next_selected else None,
            float(index["sources"][-1]["global_end"]),
        )
        range_products.append({
            "product_index": row["product_index"],
            "encoding_id": row["encoding_id"],
            "title": title_by_product[row["product_index"]].get("title"),
            "confidence": "high",
            "range_basis": "product_start_anchor_to_next_real_product_start",
            "product_start_anchor": round(float(selected["global_start"]), 3),
            "global_start": round(start, 3),
            "global_end": round(end, 3),
            "product_range_duration": round(end - start, 3),
            "ranges": split_range(start, end, index["sources"]),
        })

    output = paths.cache_dir / "link_intros.json"
    output.write_text(json.dumps({"schema_version": 1, "products": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_metadata(paths, output, "locate_link_intros.py")
    ranges_path = paths.cache_dir / "product_ranges.json"
    ranges_path.write_text(
        json.dumps({"schema_version": 1, "products": range_products}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_metadata(paths, ranges_path, "locate_link_intros.py")
    for row in results:
        selected = row.get("ordered_selected") or {}
        confidence = row.get("boundary_status")
        print(
            f"商品{row['product_index']:02d} {confidence} "
            f"候选={len(row['candidates'])} 来源={selected.get('source_stem', '-')} "
            f"时间={selected.get('start', '-')} 分数={selected.get('score', '-')}"
        )


if __name__ == "__main__":
    main()
