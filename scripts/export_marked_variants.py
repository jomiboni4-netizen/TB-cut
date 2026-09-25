#!/usr/bin/env python3
"""Export contiguous variants under product markers without trailing gap frames."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import expected_identity, write_metadata


API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
MOD = API + "/Modules"
LIB = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
PRODUCT = re.compile(r"商品(\d+)")


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


def build_manifest(timeline) -> dict:
    fps = float(timeline.GetSetting("timelineFrameRate") or 30)
    clips = sorted(list(timeline.GetItemListInTrack("video", 1) or []), key=lambda item: int(item.GetStart()))
    audio = sorted(list(timeline.GetItemListInTrack("audio", 1) or []), key=lambda item: int(item.GetStart()))
    markers = sorted((timeline.GetMarkers() or {}).items(), key=lambda pair: int(pair[0]))
    variants: list[dict] = []
    for marker_index, (frame, marker) in enumerate(markers):
        start_bound = int(frame)
        end_bound = int(markers[marker_index + 1][0]) if marker_index + 1 < len(markers) else 2**63 - 1
        product_clips = [item for item in clips if start_bound <= int(item.GetStart()) < end_bound]
        if not product_clips:
            continue
        groups: list[list] = []
        for item in product_clips:
            if not groups or int(item.GetStart()) != int(groups[-1][-1].GetEnd()):
                groups.append([])
            groups[-1].append(item)
        product_match = PRODUCT.search(str(marker.get("note") or ""))
        product_index = int(product_match.group(1)) if product_match else None
        encoding_id = str(marker.get("name") or "").strip()
        if not encoding_id:
            raise SystemExit(f"{frame} 帧标记缺少编码ID")
        for ordinal, group in enumerate(groups, 1):
            mark_in = int(group[0].GetStart())
            end_exclusive = int(group[-1].GetEnd())
            matching_audio = [item for item in audio if int(item.GetStart()) < end_exclusive and int(item.GetEnd()) > mark_in]
            audio_end = max((int(item.GetEnd()) for item in matching_audio), default=mark_in)
            safe_end_exclusive = min(end_exclusive, audio_end)
            if safe_end_exclusive <= mark_in:
                raise SystemExit(f"{encoding_id}_{ordinal} 缺少有效音频范围")
            variants.append({
                "product_index": product_index,
                "encoding_id": encoding_id,
                "variant_number": ordinal,
                "name": f"{encoding_id}_{ordinal}",
                "mark_in": mark_in,
                "mark_out": safe_end_exclusive - 1,
                "end_exclusive": safe_end_exclusive,
                "duration_frames": safe_end_exclusive - mark_in,
                "duration_seconds": round((safe_end_exclusive - mark_in) / fps, 3),
                "clip_count": len(group),
            })
    return {"fps": fps, "variant_count": len(variants), "variants": variants}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    identity = expected_identity(paths)
    output_dir = paths.runtime_argument(args.output_dir)
    state = json.loads((paths.state_path).read_text(encoding="utf-8"))
    resolve = connect()
    if not resolve:
        raise SystemExit("未连接达芬奇")
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project or project.GetName() != state["resolve"]["project_name"]:
        raise SystemExit("当前达芬奇项目不匹配")
    timeline = timeline_by_name(project, state["resolve"]["target_timeline"])
    project.SetCurrentTimeline(timeline)
    manifest = build_manifest(timeline)
    manifest.update({
        "project": project.GetName(),
        "timeline": timeline.GetName(),
        "output_dir": paths.reference(output_dir) if output_dir.resolve() == paths.workspace_root or paths.workspace_root in output_dir.resolve().parents else str(output_dir),
        "format_codec_before": project.GetCurrentRenderFormatAndCodec(),
    })
    manifest_path = paths.cache_dir / "export_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_metadata(paths, manifest_path, "export_marked_variants.py", identity=identity)
    counts: dict[str, int] = {}
    for row in manifest["variants"]:
        counts[row["encoding_id"]] = counts.get(row["encoding_id"], 0) + 1
    print(json.dumps({"variant_count": manifest["variant_count"], "counts": counts, "durations": [row["duration_seconds"] for row in manifest["variants"]]}, ensure_ascii=False))
    if not args.render:
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    if not project.SetCurrentRenderFormatAndCodec("mp4", "H264"):
        raise SystemExit("无法设置 MP4/H.264 导出格式")
    job_ids = []
    for row in manifest["variants"]:
        settings = {
            "TargetDir": str(output_dir),
            "CustomName": row["name"],
            "SelectAllFrames": False,
            "MarkIn": row["mark_in"],
            "MarkOut": row["mark_out"],
            "ExportVideo": True,
            "ExportAudio": True,
        }
        if not project.SetRenderSettings(settings):
            raise SystemExit(f"无法设置导出范围：{row['name']}")
        job_id = project.AddRenderJob()
        if not job_id:
            raise SystemExit(f"无法加入导出队列：{row['name']}")
        row["job_id"] = job_id
        job_ids.append(job_id)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_metadata(paths, manifest_path, "export_marked_variants.py", identity=identity)
    if not project.StartRendering(job_ids, False):
        raise SystemExit("导出任务启动失败")
    while project.IsRenderingInProgress():
        statuses = [project.GetRenderJobStatus(job_id) or {} for job_id in job_ids]
        complete = sum(1 for status in statuses if status.get("JobStatus") in {"Complete", "完成"})
        progress = max((float(status.get("CompletionPercentage") or 0) for status in statuses), default=0)
        print(f"导出进度：{complete}/{len(job_ids)}，当前{progress:.1f}%", flush=True)
        time.sleep(10)
    failures = []
    for row in manifest["variants"]:
        status = project.GetRenderJobStatus(row["job_id"]) or {}
        row["render_status"] = status
        if status.get("JobStatus") not in {"Complete", "完成"}:
            failures.append({"name": row["name"], "status": status})
    manifest["passed"] = not failures
    manifest["failures"] = failures
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_metadata(paths, manifest_path, "export_marked_variants.py", identity=identity)
    if failures:
        raise SystemExit(f"导出失败：{len(failures)}条")
    print(f"导出完成：{len(job_ids)}条")


if __name__ == "__main__":
    main()
