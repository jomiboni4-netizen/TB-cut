#!/usr/bin/env python3
"""Verify expected exports, durations, and trailing black frames."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import expected_identity, load_checked_json, write_metadata


BLACK = re.compile(r"black_start:([0-9.]+) black_end:([0-9.]+) black_duration:([0-9.]+)")
YAVG = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")


def verify(row: dict, output_dir: Path) -> dict:
    path = output_dir / f"{row['name']}.mp4"
    if not path.exists():
        return {"name": row["name"], "passed": False, "error": "missing"}
    probe = subprocess.run(
        ["/opt/homebrew/bin/ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        check=True, text=True, capture_output=True,
    )
    duration = float(probe.stdout.strip())
    scan = subprocess.run(
        [
            "/opt/homebrew/bin/ffmpeg", "-v", "info", "-sseof", "-0.5", "-i", str(path),
            "-vf", "setpts=PTS-STARTPTS,blackdetect=d=0.03:pix_th=0.10,signalstats,metadata=print:key=lavfi.signalstats.YAVG",
            "-an", "-f", "null", "-",
        ],
        check=False, text=True, capture_output=True,
    )
    intervals = [(float(a), float(b), float(c)) for a, b, c in BLACK.findall(scan.stderr)]
    yavg = [float(value) for value in YAVG.findall(scan.stderr)]
    trailing_black = any(end >= 0.45 and length >= 0.03 for _, end, length in intervals)
    last_frame_black = bool(yavg and yavg[-1] < 8.0)
    return {
        "name": row["name"],
        "encoding_id": row["encoding_id"],
        "path": str(path),
        "duration": round(duration, 3),
        "expected_duration": row["duration_seconds"],
        "duration_delta": round(duration - float(row["duration_seconds"]), 3),
        "trailing_black": trailing_black,
        "last_frame_black": last_frame_black,
        "last_frame_yavg": round(yavg[-1], 3) if yavg else None,
        "passed": not trailing_black and not last_frame_black and abs(duration - float(row["duration_seconds"])) <= 0.15,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    identity = expected_identity(paths)
    manifest = load_checked_json(paths, paths.cache_dir / "export_manifest.json", expected=identity)
    output_reference = Path(manifest["output_dir"])
    output_dir = output_reference if output_reference.is_absolute() else (paths.workspace_root / output_reference).resolve()
    if output_reference.is_absolute() and root != paths.workspace_root and (output_dir == root or root in output_dir.parents):
        raise SystemExit(f"legacy_absolute_export_path_outside_workspace: {output_dir}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda row: verify(row, output_dir), manifest["variants"]))
    report = {
        "passed": all(row["passed"] for row in rows),
        "expected_count": len(manifest["variants"]),
        "actual_count": len(list(output_dir.glob("*.mp4"))),
        "trailing_black_count": sum(1 for row in rows if row.get("trailing_black") or row.get("last_frame_black")),
        "failed": [row for row in rows if not row["passed"]],
        "files": rows,
    }
    output = paths.cache_dir / "export_verification.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_metadata(paths, output, "verify_exports.py", identity=identity)
    print(json.dumps({key: report[key] for key in ("passed", "expected_count", "actual_count", "trailing_black_count", "failed")}, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
