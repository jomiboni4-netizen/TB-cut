#!/usr/bin/env python3
"""Refine approved rough cuts to word/frame boundaries and build final plans."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import expected_identity, load_checked_json, write_metadata


RISK_ENDING = ("然后", "所以", "因为", "但是", "而且", "如果", "比如", "这个", "那个", "我们", "它的", "第一", "第二")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize(text: str) -> str:
    return re.sub(r"[\W_]+", "", text).lower()


def overlaps(start: float, end: float, ranges: list[dict]) -> bool:
    return any(float(row["end"]) > start and float(row["start"]) < end for row in ranges)


def normalize_physical_clips(clips: list[dict], tolerance_frames: int = 2) -> tuple[list[dict], int]:
    """Merge source-contiguous adjacent clips; roles remain metadata."""
    if not clips:
        return [], 0
    merged = [dict(clips[0])]
    merge_count = 0
    for raw in clips[1:]:
        clip = dict(raw)
        previous = merged[-1]
        contiguous = (
            previous.get("source_file") == clip.get("source_file")
            and abs(int(clip["source_start_frame"]) - int(previous["source_end_frame"])) <= tolerance_frames
            and not (previous.get("audit") or {}).get("intentional_source_deletion_after")
        )
        if not contiguous:
            merged.append(clip)
            continue
        previous["source_end_frame"] = max(int(previous["source_end_frame"]), int(clip["source_end_frame"]))
        fps = float(previous.get("fps") or clip.get("fps") or 30)
        previous["duration_sec"] = round((int(previous["source_end_frame"]) - int(previous["source_start_frame"])) / fps, 3)
        audit = previous.setdefault("audit", {})
        other = clip.get("audit") or {}
        roles = audit.setdefault("roles", [audit.get("role")] if audit.get("role") else [])
        for role in other.get("roles", [other.get("role")] if other.get("role") else []):
            if role and role not in roles:
                roles.append(role)
        audit["candidate_ids"] = list(dict.fromkeys(
            audit.get("candidate_ids", [audit.get("candidate_id")]) + other.get("candidate_ids", [other.get("candidate_id")])
        ))
        audit["transcript"] = str(audit.get("transcript") or "") + str(other.get("transcript") or "")
        audit["alignment_transcript"] = str(audit.get("alignment_transcript") or "") + str(other.get("alignment_transcript") or "")
        merge_count += 1
    return merged, merge_count


def fit_duration_with_complete_segments(clips: list[dict], minimum: float = 45.0, maximum: float = 60.0) -> tuple[list[dict], list[dict]]:
    """Remove low-value whole clips when tail completion pushes total over limit."""
    kept = list(clips)
    removed: list[dict] = []
    total = sum(float(row["duration_sec"]) for row in kept)
    if total < maximum:
        return kept, removed
    candidates = sorted(
        [row for row in kept[:-1] if (row.get("audit") or {}).get("complete_boundary", True)],
        key=lambda row: (float((row.get("audit") or {}).get("value_score", 0.5)), float(row["duration_sec"])),
    )
    for row in candidates:
        proposed = total - float(row["duration_sec"])
        if proposed > minimum:
            kept.remove(row)
            removed.append(row)
            total = proposed
            if total < maximum:
                break
    return kept, removed


def containing_window(windows: list[dict], source_file: str, start: float, end: float) -> dict:
    choices = [row for row in windows if row["source_file"] == source_file and float(row["window_end"]) > start and float(row["window_start"]) < end]
    if not choices:
        raise RuntimeError(f"找不到局部ASR窗口：{source_file} {start}-{end}")
    return min(choices, key=lambda row: float(row["window_end"]) - float(row["window_start"]))


def refine_words(window: dict, rough_start: float, rough_end: float) -> tuple[float, float, str, dict]:
    words = window.get("words") or []
    overlap_indices = [index for index, word in enumerate(words) if float(word["end"]) > rough_start and float(word["start"]) < rough_end]
    if not overlap_indices:
        raise RuntimeError(f"粗剪范围无识别文字：{rough_start}-{rough_end}")
    start_choices = [index for index, word in enumerate(words) if rough_start - 1.5 <= float(word["start"]) <= rough_start + 1.5 and float(word["start"]) < rough_end]
    end_choices = [index for index, word in enumerate(words) if rough_end - 1.5 <= float(word["end"]) <= rough_end + 3.0 and float(word["end"]) > rough_start]
    if not start_choices:
        start_choices = [overlap_indices[0]]
    if not end_choices:
        end_choices = [overlap_indices[-1]]

    def start_score(index: int) -> float:
        start = float(words[index]["start"])
        previous_end = float(words[index - 1]["end"]) if index else start
        preview = normalize("".join(str(row["text"]) for row in words[index:index + 10]))
        return max(0.0, start - previous_end) * 12 - abs(start - rough_start) * 1.5

    first = max(start_choices, key=start_score)
    valid_end_choices = [index for index in end_choices if index >= first] or [overlap_indices[-1]]

    def end_score(index: int) -> float:
        end = float(words[index]["end"])
        next_start = float(words[index + 1]["start"]) if index + 1 < len(words) else end
        tail = normalize("".join(str(row["text"]) for row in words[max(first, index - 12):index + 1]))
        return max(0.0, next_start - end) * 12 - abs(end - rough_end) * 1.5 - (100 if tail.endswith(RISK_ENDING) else 0)

    last = max(valid_end_choices, key=end_score)
    selected = words[first:last + 1]
    start, end = float(selected[0]["start"]), float(selected[-1]["end"])
    text = "".join(str(row["text"]) for row in selected)
    previous_end = float(words[first - 1]["end"]) if first else start
    next_start = float(words[last + 1]["start"]) if last + 1 < len(words) else end
    meta = {
        "method": "local_asr_word_and_pause_boundary",
        "start_gap_sec": round(max(0.0, start - previous_end), 3),
        "end_gap_sec": round(max(0.0, next_start - end), 3),
        "start_shift_sec": round(start - rough_start, 3),
        "end_shift_sec": round(end - rough_end, 3),
        "clean_start": bool(normalize(text)),
        "clean_end": not normalize(text).endswith(RISK_ENDING),
        "dependency_check_required": bool(re.match(r"^(?:嗯|呃|啊|对|好|那)*(?:然后|所以|但是|而且|另外|接着|因为|同时)", normalize(text))),
        "tail_closure_risk": normalize(text).endswith(RISK_ENDING),
    }
    return start, end, text, meta


def semantic_duplicate(left: str, right: str) -> bool:
    left, right = normalize(left), normalize(right)
    if min(len(left), len(right)) < 12:
        return False
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    longest = matcher.find_longest_match(0, len(left), 0, len(right)).size
    return (longest >= 12 and longest / min(len(left), len(right)) >= 0.60) or (min(len(left), len(right)) >= 20 and matcher.ratio() >= 0.72)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    parser.add_argument("--products", required=True)
    parser.add_argument("--fps", type=int, default=30)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    identity = expected_identity(paths)
    state = json.loads((paths.state_path).read_text(encoding="utf-8"))
    target = state["resolve"]["target_timeline"]
    source_timeline = state["resolve"]["source_timeline"]
    for product_index in [int(value) for value in args.products.split(",") if value.strip()]:
        product_dir = paths.cache_dir / f"product_{product_index:02d}_rough"
        output_dir = paths.cache_dir / f"product_{product_index:02d}_windowed"
        output_dir.mkdir(parents=True, exist_ok=True)
        selection = load_checked_json(paths, product_dir / "rough_selection.approved.json", product_id=product_index, expected=identity, artifact_schema_version=2)
        request_path = product_dir / "codex_expanded_request.json"
        if not request_path.exists():
            request_path = product_dir / "mimo_request.json"
        request = load_checked_json(paths, request_path, product_id=product_index, expected=identity)
        candidates = {row["candidate_id"]: row for row in request["candidates"]}
        asr = load_checked_json(paths, product_dir / "window_asr.json", product_id=product_index, expected=identity)
        quality = load_checked_json(paths, product_dir / "window_quality.json", product_id=product_index, expected=identity)
        unsafe_by_source: dict[str, list[dict]] = {}
        for window in quality["windows"]:
            long_freezes = [
                row for row in window.get("freeze_ranges", [])
                if float(row["end"]) - float(row["start"]) > 2.0
            ]
            unsafe_by_source.setdefault(window["source_file"], []).extend(window.get("black_ranges", []) + long_freezes)
        plans, errors = [], []
        for ordinal, variant in enumerate(selection["variants"], 1):
            clips = []
            for clip_index, rough in enumerate(variant["timeline"], 1):
                candidate = candidates[rough["source"]]
                window = containing_window(asr["windows"], candidate["source_file"], float(rough["start"]), float(rough["end"]))
                start, end, alignment_text, boundary = refine_words(window, float(rough["start"]), float(rough["end"]))
                start_frame, end_frame = round(start * args.fps), round(end * args.fps)
                start, end = start_frame / args.fps, end_frame / args.fps
                if end <= start:
                    errors.append({"variant_id": variant["variant_id"], "code": "INVALID_FRAME_RANGE", "clip": clip_index})
                    continue
                if not boundary["clean_start"] or not boundary["clean_end"]:
                    errors.append({"variant_id": variant["variant_id"], "code": "UNCLEAN_BOUNDARY", "clip": clip_index})
                if overlaps(start, end, unsafe_by_source.get(candidate["source_file"], [])):
                    errors.append({"variant_id": variant["variant_id"], "code": "UNSAFE_VISUAL_RANGE", "clip": clip_index})
                clips.append({
                    "name": f"第{product_index}品正片{ordinal}-{clip_index}",
                    "variant_id": variant["variant_id"],
                    "source_file": candidate["source_file"],
                    "source_start_frame": start_frame,
                    "source_end_frame": end_frame,
                    "duration_sec": round(end - start, 3),
                    "fps": args.fps,
                    "audit": {
                        "candidate_id": rough["source"],
                        "transcript": rough.get("text") or "",
                        "alignment_transcript": alignment_text,
                        "role": rough.get("role"),
                        "roles": [rough.get("role")] if rough.get("role") else [],
                        "complete_boundary": boundary["clean_start"] and boundary["clean_end"],
                        "boundary": boundary,
                    },
                })
            clips, meaningless_cut_count = normalize_physical_clips(clips, tolerance_frames=2)
            clips, duration_removed = fit_duration_with_complete_segments(clips)
            duration = round(sum(float(clip["duration_sec"]) for clip in clips), 3)
            if not 45.0 < duration < 60.0:
                errors.append({"variant_id": variant["variant_id"], "code": "FINAL_DURATION_INVALID", "duration": duration})
            plans.append({
                "schema_version": 2,
                "pipeline_version": "2.1",
                "status": "PASS",
                "writes_davinci": True,
                "product_index": product_index,
                "product_title": selection.get("product_title"),
                "variant_id": variant["variant_id"],
                "fps": args.fps,
                "gap_frames": args.fps * 5,
                "source_timeline": source_timeline,
                "target_timeline": target,
                "clips": clips,
                "duration": duration,
                "semantic_closure": not any(row.get("variant_id") == variant["variant_id"] and row["code"] == "UNCLEAN_BOUNDARY" for row in errors),
                "physical_overlap": 0,
                "semantic_duplicate": False,
                "meaningless_cut_count": 0,
                "normalized_merge_count": meaningless_cut_count,
                "seam_tail_risk_count": sum(1 for clip in clips if (clip.get("audit") or {}).get("boundary", {}).get("tail_closure_risk")),
                "duration_repair_removed_segments": [row.get("name") for row in duration_removed],
                "candidate_pool_duration": (request.get("candidate_pool_summary") or {}).get("duration"),
                "candidate_pool_segment_count": (request.get("candidate_pool_summary") or {}).get("segment_count"),
                "product_range_start": (request.get("product_range") or {}).get("start"),
                "product_range_end": (request.get("product_range") or {}).get("end"),
                "repair_attempts": int(request.get("repair_attempt") or 0),
                "request_digest": digest(request_path),
            })

        for left, right in combinations(plans, 2):
            for left_clip in left["clips"]:
                for right_clip in right["clips"]:
                    if left_clip["source_file"] == right_clip["source_file"]:
                        overlap_frames = min(left_clip["source_end_frame"], right_clip["source_end_frame"]) - max(left_clip["source_start_frame"], right_clip["source_start_frame"])
                        if overlap_frames > 0:
                            errors.append({"variant_id": right["variant_id"], "code": "SOURCE_TIME_OVERLAP", "frames": overlap_frames})
                    if semantic_duplicate(left_clip["audit"]["transcript"], right_clip["audit"]["transcript"]):
                        errors.append({"variant_id": right["variant_id"], "code": "SEMANTIC_DUPLICATE", "with": left["variant_id"]})
        passed = not errors
        for ordinal, plan in enumerate(plans, 1):
            variant_errors = [row for row in errors if row.get("variant_id") == plan["variant_id"]]
            plan["status"] = "PASS" if not variant_errors else "REPAIRABLE"
            plan_path = output_dir / f"cut_plan_variant_{ordinal}.json"
            plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            write_metadata(paths, plan_path, "refine_plans_v2.py", product_id=product_index, identity=identity)
        generated_path = output_dir / "generated_plans.json"
        generated_path.write_text(
            json.dumps({
                "schema_version": 2,
                "pipeline_version": "2.1",
                "product_index": product_index,
                "plans": [paths.reference(output_dir / f"cut_plan_variant_{ordinal}.json") for ordinal in range(1, len(plans) + 1)],
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        write_metadata(paths, generated_path, "refine_plans_v2.py", product_id=product_index, identity=identity)
        audit = {
            "schema_version": 2,
            "pipeline_version": "2.1",
            "product_index": product_index,
            "passed": passed,
            "status": "PASS" if passed else "REPAIRABLE",
            "variant_statuses": {plan["variant_id"]: plan["status"] for plan in plans},
            "variant_count": len(plans),
            "durations": [round(sum(clip["duration_sec"] for clip in plan["clips"]), 3) for plan in plans],
            "errors": errors,
            "product_summary": {
                "target_variant_count": 3,
                "final_variant_count": sum(1 for plan in plans if plan["status"] == "PASS"),
                "product_range_duration": (request.get("product_range") or {}).get("duration"),
                "candidate_pool_duration": (request.get("candidate_pool_summary") or {}).get("duration"),
                "candidate_pool_coverage": (request.get("candidate_pool_summary") or {}).get("coverage"),
                "exhausted": False,
                "exhausted_reason": None,
            },
        }
        final_audit_path = output_dir / "final_plan_audit.json"
        final_audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_metadata(paths, final_audit_path, "refine_plans_v2.py", product_id=product_index, identity=identity)
        print(f"商品{product_index} 切点细化={'通过' if passed else '失败'} 时长={audit['durations']} 错误={len(errors)}")
        if not passed:
            continue


if __name__ == "__main__":
    main()
