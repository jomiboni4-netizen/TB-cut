#!/usr/bin/env python3
"""Probe black and frozen frames only inside approved ASR windows."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import expected_identity, load_checked_json, write_metadata


def probe(media: Path, start: float, end: float, filter_text: str) -> str:
    result = subprocess.run([
        "/opt/homebrew/bin/ffmpeg", "-hide_banner", "-nostats",
        "-ss", str(start), "-i", str(media), "-t", str(end - start),
        "-vf", filter_text, "-an", "-f", "null", "-",
    ], text=True, capture_output=True, check=False)
    if result.returncode not in (0, 255):
        raise RuntimeError(result.stderr[-1200:])
    return result.stderr


def pairs(log: str, start_pattern: str, end_pattern: str, offset: float) -> list[dict]:
    starts = [float(value) + offset for value in re.findall(start_pattern, log)]
    ends = [float(value) + offset for value in re.findall(end_pattern, log)]
    return [{"start": round(start, 3), "end": round(end, 3)} for start, end in zip(starts, ends) if end > start]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    parser.add_argument("--products", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    identity = expected_identity(paths)
    state = json.loads((paths.state_path).read_text(encoding="utf-8"))
    video_dirs = [Path(path) for path in state.get("video_paths") or [] if Path(path).is_dir()]
    media_by_name = {path.name: path for directory in video_dirs for path in directory.glob("*.mp4")}
    for product_index in [int(value) for value in args.products.split(",") if value.strip()]:
        product_dir = paths.cache_dir / f"product_{product_index:02d}_rough"
        asr = load_checked_json(paths, product_dir / "window_asr.json", product_id=product_index, expected=identity)
        windows = []
        for window in asr["windows"]:
            media = media_by_name[window["source_file"]]
            start, end = float(window["window_start"]), float(window["window_end"])
            black = probe(media, start, end, "blackdetect=d=0.3:pix_th=0.98")
            frozen = probe(media, start, end, "freezedetect=n=-60dB:d=1")
            windows.append({
                "source_file": window["source_file"],
                "window_start": start,
                "window_end": end,
                "black_ranges": pairs(black, r"black_start:([0-9.]+)", r"black_end:([0-9.]+)", start),
                "freeze_ranges": pairs(frozen, r"freeze_start: ([0-9.]+)", r"freeze_end: ([0-9.]+)", start),
            })
        quality_path = product_dir / "window_quality.json"
        quality_path.write_text(
            json.dumps({"product_index": product_index, "windows": windows}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        write_metadata(paths, quality_path, "window_quality_v2.py", product_id=product_index, identity=identity)
        black_count = sum(len(row["black_ranges"]) for row in windows)
        freeze_count = sum(len(row["freeze_ranges"]) for row in windows)
        print(f"商品{product_index} 画面探测完成：黑帧{black_count} 静帧{freeze_count}")


if __name__ == "__main__":
    main()
