#!/usr/bin/env python3
"""Read current Resolve timeline structure for export and comparison."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from runtime_paths import resolve_runtime_paths


API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
MOD = API + "/Modules"
LIB = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"


def connect():
    os.environ.setdefault("RESOLVE_SCRIPT_API", API)
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", LIB)
    sys.path.insert(0, MOD)
    import DaVinciResolveScript as dvr
    resolve = dvr.scriptapp("Resolve")
    if resolve is not None:
        return resolve
    address = os.environ.get("RESOLVE_HOST")
    if not address:
        for interface in ("en0", "en1"):
            result = subprocess.run(["ipconfig", "getifaddr", interface], capture_output=True, text=True, check=False)
            address = result.stdout.strip()
            if address:
                break
    return dvr.scriptapp("Resolve", address) if address else None


def item_row(item) -> dict:
    media = item.GetMediaPoolItem()
    properties = item.GetProperty() or {}
    transform_keys = (
        "ZoomX", "ZoomY", "PositionX", "PositionY", "Pan", "Tilt", "RotationAngle",
        "AnchorPointX", "AnchorPointY", "Pitch", "Yaw", "FlipH", "FlipV",
        "CropLeft", "CropRight", "CropTop", "CropBottom",
    )
    return {
        "name": item.GetName(),
        "media": media.GetName() if media else None,
        "record_start": int(item.GetStart()),
        "record_end": int(item.GetEnd()),
        "duration_frames": int(item.GetEnd()) - int(item.GetStart()),
        "source_start": int(item.GetSourceStartFrame()),
        "source_end": int(item.GetSourceEndFrame()),
        "transform": {key: properties[key] for key in transform_keys if key in properties},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    state = json.loads((paths.state_path).read_text(encoding="utf-8"))
    resolve = connect()
    if not resolve:
        raise SystemExit("未连接达芬奇")
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        raise SystemExit("没有当前项目")
    target_name = state["resolve"]["target_timeline"]
    timeline = next(
        (project.GetTimelineByIndex(index) for index in range(1, project.GetTimelineCount() + 1)
         if project.GetTimelineByIndex(index).GetName() == target_name),
        None,
    )
    if not timeline:
        raise SystemExit(f"找不到时间线：{target_name}")
    fps = float(timeline.GetSetting("timelineFrameRate") or project.GetSetting("timelineFrameRate") or 30)
    markers = []
    for frame, row in sorted((timeline.GetMarkers() or {}).items(), key=lambda pair: int(pair[0])):
        markers.append({
            "frame": int(frame),
            "name": row.get("name"),
            "note": row.get("note"),
            "color": row.get("color"),
            "duration": row.get("duration"),
            "customData": row.get("customData"),
        })
    document = {
        "project": project.GetName(),
        "timeline": timeline.GetName(),
        "fps": fps,
        "start_frame": int(timeline.GetStartFrame()),
        "end_frame": int(timeline.GetEndFrame()),
        "markers": markers,
        "video_tracks": {
            str(track): [item_row(item) for item in timeline.GetItemListInTrack("video", track) or []]
            for track in range(1, int(timeline.GetTrackCount("video")) + 1)
        },
        "audio_tracks": {
            str(track): [item_row(item) for item in timeline.GetItemListInTrack("audio", track) or []]
            for track in range(1, int(timeline.GetTrackCount("audio")) + 1)
        },
    }
    output = paths.cache_dir / "current_timeline_snapshot.json"
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "project": document["project"],
        "timeline": document["timeline"],
        "fps": fps,
        "markers": markers,
        "video_track_counts": {key: len(value) for key, value in document["video_tracks"].items()},
        "audio_track_counts": {key: len(value) for key, value in document["audio_tracks"].items()},
        "output": str(output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
