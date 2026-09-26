#!/usr/bin/env python3
"""Build a fingerprinted local subtitle index without printing raw subtitles."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import CacheIdentityError, input_fingerprint, validate_cache, write_metadata
from runtime_publication import BuildSnapshot, generator_lock, publish_pair


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


def parse_srt(path: Path, global_offset: float, content: bytes | None = None) -> list[dict]:
    text = (path.read_bytes() if content is None else content).decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
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


def generate(paths):
    with generator_lock(paths):
        return _generate_locked(paths)


def _generate_locked(paths):
    snapshot = BuildSnapshot(paths)
    state = snapshot.state
    subtitles = expand(state.get("subtitle_paths") or [], ".srt")
    videos = expand(state.get("video_paths") or [], ".mp4")
    if not subtitles:
        raise SystemExit("未找到 SRT 字幕")
    video_by_stem = {path.stem: path for path in videos}
    missing_video = [path.stem for path in subtitles if path.stem not in video_by_stem]
    if missing_video:
        raise SystemExit("字幕缺少同名视频：" + ",".join(missing_video))

    subtitle_bytes = {p: p.read_bytes() for p in subtitles}
    digest = hashlib.sha256()
    for path, content in subtitle_bytes.items():
        digest.update(str(path).encode())
        digest.update(content)
    fingerprint = digest.hexdigest()
    buffered = {p.resolve(): content for p, content in subtitle_bytes.items()}
    if input_fingerprint(paths, state, subtitle_contents=buffered) != snapshot.identity['input_fingerprint']:
        raise CacheIdentityError('inputs_changed_during_generation')
    snapshot.check()
    output = paths.cache_dir / "subtitle_index.json"
    if output.exists() and state.get("runtime", {}).get("subtitle_index_fingerprint") == fingerprint:
        try:
            validate_cache(paths, output, expected=snapshot.identity)
        except CacheIdentityError as exc:
            print(f"字幕索引身份无效，将从原始输入重建：{exc}")
        else:
            snapshot.check()
            return output

    cues: list[dict] = []
    sources: list[dict] = []
    global_offset = 0.0
    for subtitle in subtitles:
        video = video_by_stem[subtitle.stem]
        duration = media_duration(video)
        local_cues = parse_srt(subtitle, global_offset, subtitle_bytes[subtitle])
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

    with tempfile.TemporaryDirectory(prefix=".subtitles-", dir=paths.cache_dir) as temporary:
        staged = Path(temporary) / output.name
        staged.write_text(json.dumps({
            "schema_version": 1, "generator": "index_subtitles.py:v2", "fingerprint": fingerprint,
            "sources": sources, "cues": cues,
        }, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        staged_meta = write_metadata(paths, staged, "index_subtitles.py", identity=snapshot.identity)
        validate_cache(paths, staged, expected=snapshot.identity)
        # The state rename is separate too; never advance it before both files.
        updated_state = json.loads(snapshot.state_bytes)
        updated_state.setdefault("runtime", {})["subtitle_index_fingerprint"] = fingerprint
        updated_state["runtime"]["last_successful_stage"] = "subtitle_indexed"
        with tempfile.TemporaryDirectory(prefix=".subtitle-state-", dir=paths.state_path.parent) as state_temp:
            staged_state = Path(state_temp) / "state.json"
            staged_state.write_text(json.dumps(updated_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            publish_pair(staged, staged_meta, output, before_commit=snapshot.check,
                         state_update=(staged_state, paths.state_path))
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    args = parser.parse_args()
    generate(resolve_runtime_paths(Path(args.root).resolve(), args.workspace))
    print("subtitle index generated or validated")


if __name__ == "__main__":
    main()
