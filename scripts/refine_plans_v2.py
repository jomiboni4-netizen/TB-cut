#!/usr/bin/env python3
"""Refine approved rough cuts to word/frame boundaries and build final plans."""

from __future__ import annotations

import argparse
import array
import hashlib
import json
import math
import re
import subprocess
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import expected_identity, load_checked_json, write_metadata
from audio_boundary_evidence import activity_at_cut


RISK_ENDING = ("然后", "所以", "因为", "但是", "而且", "如果", "比如", "这个", "那个", "我们", "它的", "第一", "第二")
AUDIO_SILENCE_DBFS = -23.0
AUDIO_FRAME_SEC = 0.02
AUDIO_SILENCE_HOLD_SEC = 0.12
AUDIO_END_LOOKAHEAD_SEC = 1.6


def _audio_pcm_frames(media: Path, start: float, end: float) -> list[dict]:
    """Decode 20 ms mono PCM windows with RMS and waveform extrema."""
    if end <= start:
        return []
    result = subprocess.run([
        "/opt/homebrew/bin/ffmpeg", "-v", "error", "-ss", f"{start:.6f}",
        "-i", str(media), "-t", f"{end - start:.6f}", "-vn", "-ac", "1",
        "-ar", "16000", "-f", "s16le", "-acodec", "pcm_s16le", "-",
    ], capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"音频切点电平分析失败：{media.name} {result.stderr[-300:].decode(errors='replace')}")
    samples = array.array("h")
    samples.frombytes(result.stdout[:len(result.stdout) // 2 * 2])
    samples_per_frame = round(16000 * AUDIO_FRAME_SEC)
    frames = []
    for offset in range(0, len(samples) - samples_per_frame + 1, samples_per_frame):
        chunk = samples[offset:offset + samples_per_frame]
        rms = math.sqrt(sum(value * value for value in chunk) / len(chunk))
        dbfs = 20 * math.log10(max(rms / 32768, 1e-8))
        peak = max(abs(value) for value in chunk)
        frames.append({"start": start + offset / 16000, "end": start + (offset + samples_per_frame) / 16000,
                       "rms_dbfs": dbfs,
                       "peak_dbfs": 20 * math.log10(max(peak / 32768, 1e-8)),
                       "waveform_min": min(chunk), "waveform_max": max(chunk)})
    return frames


def audio_level_frames(media: Path, start: float, end: float) -> list[tuple[float, float]]:
    """Return (frame_start_seconds, RMS dBFS) for 20 ms mono PCM windows."""
    return [(row["start"], row["rms_dbfs"]) for row in _audio_pcm_frames(media, start, end)]


def audio_waveform_frames(media: Path, start: float, end: float) -> list[dict]:
    """Return a 20 ms waveform envelope used to choose local cut positions."""
    return _audio_pcm_frames(media, start, end)


def refine_audio_end(media: Path, start: float, end: float, source_duration: float) -> tuple[float, dict]:
    """Propose an extension for activity across the cut, not later nearby speech.

    The proposal still needs sentence ownership checks before application.
    This signal audit never confirms a truncated word.
    """
    lookback = max(start, end - 0.24)
    lookahead = min(source_duration, end + AUDIO_END_LOOKAHEAD_SEC)
    frames = audio_level_frames(media, lookback, lookahead)
    before = [level for at, level in frames if end - 0.20 <= at < end-1e-7]
    after = [(at, level) for at, level in frames if end-1e-7 <= at < lookahead]
    signal = activity_at_cut(frames, end, AUDIO_SILENCE_DBFS, AUDIO_FRAME_SEC)
    continued = signal['activity_crosses_cut'] is True
    extension = 0.0
    quiet_run = 0
    quiet_start = None
    if continued:
        for at, level in after:
            if level <= AUDIO_SILENCE_DBFS:
                if quiet_run == 0:
                    quiet_start = at
                quiet_run += 1
                if quiet_run * AUDIO_FRAME_SEC >= AUDIO_SILENCE_HOLD_SEC:
                    candidate_end = (quiet_start or at) + 0.04
                    extension = max(0.0, min(candidate_end, end + AUDIO_END_LOOKAHEAD_SEC) - end)
                    break
            else:
                quiet_run, quiet_start = 0, None
    adjusted_end = end + extension
    return adjusted_end, {
        "method": "rms_dbfs_20ms",
        "silence_threshold_dbfs": AUDIO_SILENCE_DBFS,
        "end_level_before_dbfs": round(before[-1], 1) if before else None,
        "speech_continues_after_end": continued,
        "quiet_run_found": extension > 0,
        "end_extension_sec": round(extension, 3),
        "risk": signal['activity_crosses_cut'] is None or bool(continued and extension == 0),
        "signal_observation": signal,
        "risk_scope": "signal_continuity_not_word_truncation",
    }


def audit_audio_start(media: Path, start: float, *, lower_bound: float | None = None) -> tuple[float, dict]:
    frames = audio_level_frames(media, max(0.0, start - 0.80), start + 0.10)
    onset = None
    quiet_frames = round(AUDIO_SILENCE_HOLD_SEC / AUDIO_FRAME_SEC)
    for index, (at, level) in enumerate(frames):
        if at < start - 0.40 or at > start + AUDIO_FRAME_SEC or level <= AUDIO_SILENCE_DBFS:
            continue
        prior = frames[max(0, index - quiet_frames):index]
        if len(prior) == quiet_frames and all(value <= AUDIO_SILENCE_DBFS for _, value in prior):
            onset = at
            break
    # Without an explicit sentence-owned envelope the audit must not move it.
    adjusted_start = max(lower_bound, min(start, onset)) if onset is not None and lower_bound is not None else start
    before = [level for at, level in frames if at < adjusted_start-1e-7]
    active_before = bool(before and before[-1] > AUDIO_SILENCE_DBFS)
    signal = activity_at_cut(frames, adjusted_start, AUDIO_SILENCE_DBFS, AUDIO_FRAME_SEC)
    return adjusted_start, {
        "method": "rms_dbfs_20ms",
        "silence_threshold_dbfs": AUDIO_SILENCE_DBFS,
        "speech_before_start": active_before,
        "start_onset_found": onset is not None,
        "start_extension_sec": round(start - adjusted_start, 3),
        "risk": signal['activity_crosses_cut'] is not False,
        "signal_observation": signal,
        "risk_scope": "signal_continuity_not_word_truncation",
    }


def media_duration(media: Path) -> float:
    result = subprocess.run([
        "/opt/homebrew/bin/ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(media),
    ], text=True, capture_output=True, check=True)
    return float(result.stdout.strip())


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
        from sentence_boundaries import verify_edges
        left_proof = dict(previous.get('sentence_edges') or {})
        proofs_mergeable = verify_edges(previous) and verify_edges(clip)
        if not proofs_mergeable:
            merged.append(clip)
            continue
        previous["source_end_frame"] = max(int(previous["source_end_frame"]), int(clip["source_end_frame"]))
        fps = float(previous.get("fps") or clip.get("fps") or 30)
        if "record_duration_frames" in previous:
            previous["record_duration_frames"] = previous["source_end_frame"] - previous["source_start_frame"]
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
        right_proof = clip['sentence_edges']
        left_first = previous['words'][left_proof['first_word_index']]
        right_last = clip['words'][right_proof['last_word_index']]
        edge_words = {(float(word['start']), float(word['end']), str(word['text'])): dict(word)
                      for word in previous['words'] + clip['words']}
        previous['words'] = sorted(edge_words.values(), key=lambda word: (word['start'], word['end']))
        proof = dict(left_proof)
        left_key = (float(left_first['start']), float(left_first['end']), str(left_first['text']))
        proof['first_word_index'] = next(i for i, word in enumerate(previous['words'])
            if (float(word['start']), float(word['end']), str(word['text'])) == left_key)
        right_key = (float(right_last['start']), float(right_last['end']), str(right_last['text']))
        proof['last_word_index'] = next(i for i, word in enumerate(previous['words'])
            if (float(word['start']), float(word['end']), str(word['text'])) == right_key)
        for key in ('last_word_end', 'next_word_start', 'last_text', 'source_end_frame'):
            proof[key] = right_proof[key]
        proof['word_context_digest'] = digest(previous['words'])
        if right_proof.get('alignment_status') == 'FRAME_ROUNDED_ESTIMATE':
            proof['alignment_status'] = 'FRAME_ROUNDED_ESTIMATE'
        previous['sentence_edges'] = proof
        previous['audio_end_frame'] = previous['source_end_frame']
        previous['audio_boundary_verified'] = previous.get('audio_boundary_verified') is True and clip.get('audio_boundary_verified') is True
        previous['source_subtitle_text'] = (str(previous.get('source_subtitle_text') or '')
                                            + str(clip.get('source_subtitle_text') or ''))
        previous['transcript'] = previous['source_subtitle_text']
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


def refine_words(window: dict, rough_start: float, rough_end: float, selected_text: str | None = None) -> tuple[float, float, str, dict]:
    words = window.get("words") or []
    overlap_indices = [index for index, word in enumerate(words) if float(word["end"]) > rough_start and float(word["start"]) < rough_end]
    if not overlap_indices:
        raise RuntimeError(f"粗剪范围无识别文字：{rough_start}-{rough_end}")
    # A rough upper bound must not pull in a word that starts before the bound
    # and ends after it. Keep only complete ASR words contained in the selection.
    contained_indices = [index for index in overlap_indices
                         if float(words[index]["start"]) >= rough_start - 1e-7
                         and float(words[index]["end"]) <= rough_end + 1e-7]
    if not contained_indices:
        raise RuntimeError(f"粗剪范围不包含完整词：{rough_start}-{rough_end}")
    if selected_text:
        from sentence_boundaries import anchored_words
        first, last, _ = anchored_words(words, selected_text, rough_start, rough_end)
        if first not in contained_indices or last not in contained_indices:
            raise RuntimeError("选中文字边界超出粗剪范围")
    else:
        first, last = contained_indices[0], contained_indices[-1]
    selected = words[first:last + 1]
    start, end = float(selected[0]["start"]), float(selected[-1]["end"])
    text = "".join(str(row["text"]) for row in selected)
    previous_end = float(words[first - 1]["end"]) if first else start
    next_start = float(words[last + 1]["start"]) if last + 1 < len(words) else end
    meta = {
        "method": "sentence-owned-complete-word-edges-v2",
        "first_word_index": first, "last_word_index": last,
        "source_context_review_required": True,
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
    state = paths.load_state()
    media_by_name = {
        media.name: media
        for item in state.get("video_paths") or []
        for media in (Path(item).glob("*.mp4") if Path(item).is_dir() else [Path(item)])
        if media.is_file() and media.suffix.lower() == ".mp4"
    }
    durations = {name: media_duration(media) for name, media in media_by_name.items()}
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
                media = media_by_name[candidate["source_file"]]
                window = containing_window(asr["windows"], candidate["source_file"], float(rough["start"]), float(rough["end"]))
                start, end, alignment_text, boundary = refine_words(window, float(rough["start"]), float(rough["end"]), rough.get("text"))
                from sentence_boundaries import bounded_edges
                start_frame, end_frame, sentence_edges = bounded_edges(window['words'], boundary['first_word_index'], boundary['last_word_index'], args.fps)
                start, end = start_frame / args.fps, end_frame / args.fps
                _, audio_start = audit_audio_start(media, start)
                _, audio_end = refine_audio_end(media, start, end, durations[candidate['source_file']])
                audio_end['risk'] = audio_end['risk'] or audio_end['end_extension_sec'] > 0
                if end <= start:
                    errors.append({"variant_id": variant["variant_id"], "code": "INVALID_FRAME_RANGE", "clip": clip_index})
                    continue
                if not boundary["clean_start"] or not boundary["clean_end"]:
                    errors.append({"variant_id": variant["variant_id"], "code": "UNCLEAN_BOUNDARY", "clip": clip_index})
                if audio_end["risk"]:
                    errors.append({"variant_id": variant["variant_id"], "code": "AUDIO_TAIL_CONTINUES_BEYOND_SCAN", "clip": clip_index})
                if audio_start["risk"]:
                    errors.append({"variant_id": variant["variant_id"], "code": "AUDIO_HEAD_CONTINUES_BEFORE_SCAN", "clip": clip_index})
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
                    "sentence_edges": sentence_edges,
                    "words": window["words"],
                    "audit": {
                        "candidate_id": rough["source"],
                        "transcript": rough.get("text") or "",
                        "alignment_transcript": alignment_text,
                        "role": rough.get("role"),
                        "roles": [rough.get("role")] if rough.get("role") else [],
                        "complete_boundary": boundary["clean_start"] and boundary["clean_end"],
                        "boundary": boundary,
                        "audio_cutpoint": {"start": audio_start, "end": audio_end},
                    },
                })
            clips, meaningless_cut_count = normalize_physical_clips(clips, tolerance_frames=2)
            clips, duration_removed = fit_duration_with_complete_segments(clips)
            for left_clip, right_clip in combinations(clips, 2):
                if left_clip["source_file"] != right_clip["source_file"]:
                    continue
                overlap_frames = min(
                    int(left_clip["source_end_frame"]), int(right_clip["source_end_frame"])
                ) - max(
                    int(left_clip["source_start_frame"]), int(right_clip["source_start_frame"])
                )
                if overlap_frames > 0:
                    errors.append({
                        "variant_id": variant["variant_id"],
                        "code": "WITHIN_VARIANT_SOURCE_OVERLAP",
                        "frames": overlap_frames,
                    })
            duration = round(sum(float(clip["duration_sec"]) for clip in clips), 3)
            if not 45.0 < duration < 60.0:
                errors.append({"variant_id": variant["variant_id"], "code": "FINAL_DURATION_INVALID", "duration": duration})
            plans.append({
                "schema_version": 2,
                "pipeline_version": "2.1",
                "cutpoint_audio_audit_version": "sentence-owned-edges-v1",
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
                "cutpoint_audio_audit_version": "sentence-owned-edges-v1",
                "audio_silence_threshold_dbfs": AUDIO_SILENCE_DBFS,
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
