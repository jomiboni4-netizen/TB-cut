#!/usr/bin/env python3
"""Build a fingerprinted local subtitle index without printing raw subtitles."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import CacheIdentityError, validate_cache, write_metadata


TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s+-->\s+"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def seconds(parts: tuple[str, ...]) -> float:
    hour, minute, second, millis = map(int, parts)
    return hour * 3600 + minute * 60 + second + millis / 1000


def digest_files(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path).encode())
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def numeric_key(path: Path) -> tuple[int, str]:
    return (int(path.stem), path.name) if path.stem.isdigit() else (10**9, path.name)


def expand(paths: list[str], suffix: str) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            files.extend(path.glob(f"*{suffix}"))
        elif path.suffix.lower() == suffix:
            files.append(path)
    return sorted(set(files), key=numeric_key)


def media_duration(path: Path) -> float:
    command = [
        "/opt/homebrew/bin/ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    return float(subprocess.check_output(command, text=True).strip())


def parse_srt(path: Path, global_offset: float) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[dict] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3:
            continue
        match = TIME_RE.search(lines[1])
        if not match:
            continue
        start = seconds(match.groups()[:4])
        end = seconds(match.groups()[4:])
        body = re.sub(r"\s+", "", "".join(lines[2:]))
        if body:
            cues.append({
                "source_stem": path.stem,
                "start": round(start, 3),
                "end": round(end, 3),
                "global_start": round(global_offset + start, 3),
                "global_end": round(global_offset + end, 3),
                "text": body,
            })
    return cues


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    state_path = paths.state_path
    state = json.loads(state_path.read_text(encoding="utf-8"))
    subtitles = expand(state.get("subtitle_paths") or [], ".srt")
    videos = expand(state.get("video_paths") or [], ".mp4")
    if not subtitles:
        raise SystemExit("未找到 SRT 字幕")
    video_by_stem = {path.stem: path for path in videos}
    missing_video = [path.stem for path in subtitles if path.stem not in video_by_stem]
    if missing_video:
        raise SystemExit("字幕缺少同名视频：" + ",".join(missing_video))

    fingerprint = digest_files(subtitles)
    output = paths.cache_dir / "subtitle_index.json"
    if output.exists() and state.get("runtime", {}).get("subtitle_index_fingerprint") == fingerprint:
        try:
            validate_cache(paths, output)
        except CacheIdentityError as exc:
            print(f"字幕索引身份无效，将从原始输入重建：{exc}")
        else:
            print(f"字幕索引命中缓存：{len(subtitles)} 个文件")
            return

    cues: list[dict] = []
    sources: list[dict] = []
    global_offset = 0.0
    for subtitle in subtitles:
        video = video_by_stem[subtitle.stem]
        duration = media_duration(video)
        local_cues = parse_srt(subtitle, global_offset)
        cues.extend(local_cues)
        sources.append({
            "source_stem": subtitle.stem,
            "subtitle_path": str(subtitle),
            "video_path": str(video),
            "global_start": round(global_offset, 3),
            "global_end": round(global_offset + duration, 3),
            "duration": round(duration, 3),
            "cue_count": len(local_cues),
        })
        global_offset += duration

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "schema_version": 1,
        "fingerprint": fingerprint,
        "sources": sources,
        "cues": cues,
    }, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    write_metadata(paths, output, "index_subtitles.py")
    state.setdefault("runtime", {})["subtitle_index_fingerprint"] = fingerprint
    state["runtime"]["last_successful_stage"] = "subtitle_indexed"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"字幕索引完成：{len(subtitles)} 个文件，{len(cues)} 条字幕")


if __name__ == "__main__":
    main()
