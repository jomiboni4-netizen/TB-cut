#!/usr/bin/env python3
"""Run offline ASR only around approved rough cuts."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import expected_identity, load_checked_json, write_metadata

from faster_whisper import WhisperModel


def fingerprint(path: Path) -> str:
    stat = path.stat()
    return hashlib.sha256(f"{path}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()[:16]


def merge_windows(rows: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for row in sorted(rows, key=lambda item: (item["source_file"], item["start"])):
        if merged and merged[-1]["source_file"] == row["source_file"] and row["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], row["end"])
        else:
            merged.append(dict(row))
    return merged


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    parser.add_argument("--products", required=True)
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--expand", type=float, default=5.0)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    identity = expected_identity(paths)
    state = json.loads((paths.state_path).read_text(encoding="utf-8"))
    video_dirs = [Path(path) for path in state.get("video_paths") or [] if Path(path).is_dir()]
    media_by_name = {path.name: path for directory in video_dirs for path in directory.glob("*.mp4")}
    ranges = {
        int(row["product_index"]): row
        for row in load_checked_json(paths, paths.cache_dir / "product_ranges.json", expected=identity, artifact_schema_version=1)["products"]
    }
    product_ids = [int(value) for value in args.products.split(",") if value.strip()]
    jobs_by_product: dict[int, list[dict]] = {}
    for product_index in product_ids:
        product_dir = paths.cache_dir / f"product_{product_index:02d}_rough"
        audit = load_checked_json(paths, product_dir / "rough_audit.approved.json", product_id=product_index, expected=identity, artifact_schema_version=2)
        if audit.get("passed") is not True:
            raise SystemExit(f"商品{product_index}粗选未批准")
        selection = load_checked_json(paths, product_dir / "rough_selection.approved.json", product_id=product_index, expected=identity, artifact_schema_version=2)
        candidates = {}
        for request_path in (product_dir / "mimo_request.json", product_dir / "codex_expanded_request.json"):
            if request_path.exists():
                request = load_checked_json(paths, request_path, product_id=product_index, expected=identity)
                candidates.update({row["candidate_id"]: row for row in request["candidates"]})
        allowed = {f"{row['media_stem']}.mp4": (float(row["start"]), float(row["end"])) for row in ranges[product_index]["ranges"]}
        jobs: list[dict] = []
        for variant in selection["variants"]:
            for clip in variant["timeline"]:
                candidate = candidates[clip["source"]]
                source_file = candidate["source_file"]
                low, high = allowed[source_file]
                jobs.append({
                    "source_file": source_file,
                    "start": max(low, float(clip["start"]) - args.expand),
                    "end": min(high, float(clip["end"]) + args.expand),
                })
        jobs_by_product[product_index] = merge_windows(jobs)

    model = WhisperModel(args.model, device="cpu", compute_type="int8", local_files_only=True)
    cache_root = paths.cache_dir / "asr_windows"
    for product_index, jobs in jobs_by_product.items():
        output_windows: list[dict] = []
        for job in jobs:
            media = media_by_name.get(job["source_file"])
            if media is None:
                raise SystemExit(f"找不到视频：{job['source_file']}")
            key = f"{int(round(job['start'] * 1000))}_{int(round(job['end'] * 1000))}"
            cache_path = cache_root / f"{media.stem}_{fingerprint(media)}" / f"{key}.json"
            if cache_path.exists():
                cached = load_checked_json(paths, cache_path, expected=identity)
                if cached.get("model") != args.model:
                    raise SystemExit(f"局部ASR模型缓存不匹配：{cache_path}")
                output_windows.append(cached)
                continue
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(suffix=".wav") as wav:
                subprocess.run([
                    "/opt/homebrew/bin/ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-ss", str(job["start"]), "-i", str(media), "-t", str(job["end"] - job["start"]),
                    "-vn", "-ac", "1", "-ar", "16000", "-y", wav.name,
                ], check=True)
                segments_iter, _ = model.transcribe(
                    wav.name,
                    language="zh",
                    beam_size=5,
                    vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 700},
                    word_timestamps=True,
                    condition_on_previous_text=False,
                )
                segments, words = [], []
                for segment in segments_iter:
                    segments.append({
                        "start": round(job["start"] + float(segment.start), 3),
                        "end": round(job["start"] + float(segment.end), 3),
                        "text": segment.text.strip(),
                    })
                    for word in segment.words or []:
                        words.append({
                            "start": round(job["start"] + float(word.start), 3),
                            "end": round(job["start"] + float(word.end), 3),
                            "text": word.word,
                        })
            result = {
                "schema_version": 1,
                "model": args.model,
                "source_file": job["source_file"],
                "window_start": round(job["start"], 3),
                "window_end": round(job["end"], 3),
                "segments": segments,
                "words": words,
            }
            cache_path.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
            write_metadata(paths, cache_path, "windowed_asr_v2.py", identity=identity)
            output_windows.append(result)
        product_dir = paths.cache_dir / f"product_{product_index:02d}_rough"
        asr_path = product_dir / "window_asr.json"
        asr_path.write_text(
            json.dumps({"product_index": product_index, "windows": output_windows}, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        write_metadata(paths, asr_path, "windowed_asr_v2.py", product_id=product_index, identity=identity)
        print(f"商品{product_index} 局部ASR完成：{len(output_windows)} 个窗口")


if __name__ == "__main__":
    main()
