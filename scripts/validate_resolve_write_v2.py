#!/usr/bin/env python3
"""Independently validate the current Resolve timeline against a V2 write report."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import expected_identity, load_checked_json, write_metadata


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


def timeline_by_name(project, name: str):
    for index in range(1, project.GetTimelineCount() + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline.GetName() == name:
            return timeline
    raise SystemExit(f"找不到时间线：{name}")


def item_row(item) -> dict:
    media = item.GetMediaPoolItem()
    return {
        "media": media.GetName() if media else None,
        "record_start": int(item.GetStart()),
        "record_end": int(item.GetEnd()),
        "source_start": int(item.GetSourceStartFrame()),
        "source_end": int(item.GetSourceEndFrame()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    parser.add_argument("--report", default=".cache/resolve_write_report.json")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    identity = expected_identity(paths)
    report_path = paths.runtime_argument(args.report)
    report = load_checked_json(paths, report_path, expected=identity, artifact_schema_version=2)
    if report.get("pipeline_version") != "2.1":
        raise SystemExit("写入报告不是V2.1，拒绝按新版验收")
    resolve = connect()
    if not resolve:
        raise SystemExit("未连接达芬奇")
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project or project.GetName() != report["project"]:
        raise SystemExit("当前达芬奇项目不匹配")
    target = timeline_by_name(project, report["target_timeline"])
    video = list(target.GetItemListInTrack("video", 1) or [])
    audio = list(target.GetItemListInTrack("audio", 1) or [])
    expected = report["expected"]
    old_v, old_a = int(report["existing_video_count"]), int(report["existing_audio_count"])
    new_video, new_audio = video[old_v:], audio[old_a:]
    errors = []
    if len(new_video) != len(expected) or len(new_audio) != len(expected):
        errors.append(f"新增数量不符 video={len(new_video)} audio={len(new_audio)} expected={len(expected)}")
    for index, row in enumerate(expected):
        if index >= len(new_video):
            break
        actual = item_row(new_video[index])
        if any(actual[key] != row[key] for key in ("media", "record_start", "record_end")):
            errors.append(f"素材段{index + 1}记录位置不匹配")
        if any(abs(actual[key] - int(row[key])) > 1 for key in ("source_start", "source_end")):
            errors.append(f"素材段{index + 1}源入出点超过1帧容差")
        if index >= len(new_audio) or int(new_audio[index].GetStart()) != int(row["record_start"]) or int(new_audio[index].GetEnd()) != int(row["record_end"]):
            errors.append(f"素材段{index + 1}音频不同步")
    markers = target.GetMarkers() or {}
    for marker in report["markers"]:
        actual = markers.get(int(marker["frame"]), {})
        if actual.get("name") != marker["encoding_id"] or actual.get("customData") != f"tb_cut_product_start:{marker['encoding_id']}":
            errors.append(f"商品{marker['product_index']}标记不匹配")
    result = {
        "schema_version": 2,
        "pipeline_version": "2.1",
        "passed": not errors,
        "errors": errors,
        "video_count": len(video),
        "audio_count": len(audio),
        "validated_segments": min(len(new_video), len(expected)),
        "source_frame_tolerance": 1,
    }
    output = paths.cache_dir / "resolve_write_validation.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_metadata(paths, output, "validate_resolve_write_v2.py", identity=identity)
    print(f"复验：视频{len(video)} 音频{len(audio)} 素材段{len(expected)}")
    print("复验通过" if not errors else f"复验失败：{len(errors)}项")
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
