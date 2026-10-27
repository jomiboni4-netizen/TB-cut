#!/usr/bin/env python3
import importlib.util
import json
import os
import sys
import time
import traceback
from pathlib import Path


RUN_DIR = Path("/Users/keigan/Documents/Codex/workspace/runs/taobao-live-slicer-v2-20260701")
PLAN_PATH = Path(os.environ.get("CLIP_PLAN_PATH", RUN_DIR / "plan" / "clip-plan.json"))
RESOLVE_DIR = Path(os.environ.get("RESOLVE_REPORT_DIR", RUN_DIR / "resolve"))
RESOLVE_DIR.mkdir(parents=True, exist_ok=True)

FPS = 30
BLACK_GAP_FRAMES = 5 * FPS
VIDEO_PATH = "/Users/keigan/Desktop/工程/20260701-淘宝切片/视频/30.mp4"


def load_resolve():
    candidates = [
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules/DaVinciResolveScript.py",
        "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so",
    ]
    for path in candidates:
        p = Path(path)
        if not p.exists():
            continue
        if p.suffix == ".py":
            spec = importlib.util.spec_from_file_location("DaVinciResolveScript", str(p))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if hasattr(module, "scriptapp"):
                return module.scriptapp("Resolve")
            if hasattr(module, "script_module"):
                return module.script_module.scriptapp("Resolve")
    try:
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")
    except Exception:
        return None


def item_info(item, kind, track_index):
    props = {}
    try:
        props = item.GetProperty() or {}
    except Exception:
        props = {}
    media_path = ""
    try:
        mpi = item.GetMediaPoolItem()
        media_path = (mpi.GetClipProperty() or {}).get("File Path", "")
    except Exception:
        pass
    get_source_start = getattr(item, "GetSourceStart", None)
    get_source_end = getattr(item, "GetSourceEnd", None)
    source_start = int(get_source_start()) if callable(get_source_start) else None
    source_end = int(get_source_end()) if callable(get_source_end) else None
    return {
        "type": kind,
        "track": track_index,
        "name": item.GetName(),
        "start": int(item.GetStart()),
        "end": int(item.GetEnd()),
        "duration": int(item.GetDuration()),
        "sourceStart": source_start,
        "sourceEnd": source_end,
        "path": media_path,
        "properties": props,
    }


def timeline_snapshot(project, timeline):
    snap = {
        "project": project.GetName(),
        "timeline": timeline.GetName(),
        "start": int(timeline.GetStartFrame()),
        "end": int(timeline.GetEndFrame()),
        "tracks": {
            "video": int(timeline.GetTrackCount("video")),
            "audio": int(timeline.GetTrackCount("audio")),
            "subtitle": int(timeline.GetTrackCount("subtitle")),
        },
        "items": [],
    }
    for kind in ("video", "audio", "subtitle"):
        for track in range(1, snap["tracks"][kind] + 1):
            for item in timeline.GetItemListInTrack(kind, track) or []:
                snap["items"].append(item_info(item, kind, track))
    return snap


def find_media_pool_item(timeline):
    for kind in ("video", "audio"):
        for track in range(1, timeline.GetTrackCount(kind) + 1):
            for item in timeline.GetItemListInTrack(kind, track) or []:
                try:
                    mpi = item.GetMediaPoolItem()
                    props = mpi.GetClipProperty() or {}
                    if props.get("File Path") == VIDEO_PATH or item.GetName() == "30.mp4":
                        return mpi
                except Exception:
                    continue
    raise RuntimeError("Cannot find 30.mp4 media pool item on current timeline")


def remove_current_v1_a1(timeline):
    to_delete = []
    for kind in ("video", "audio"):
        if timeline.GetTrackCount(kind) >= 1:
            to_delete.extend(timeline.GetItemListInTrack(kind, 1) or [])
    if to_delete:
        ok = timeline.DeleteClips(to_delete, False)
        if not ok:
            raise RuntimeError("Resolve rejected DeleteClips on V1/A1")
    time.sleep(0.5)


def build_clip_infos(plan, media_pool_item):
    clip_infos = []
    groups = []
    record = 0
    for group in plan:
        group_start = record
        group_spans = []
        for span in group["spans"]:
            src_start = int(round(float(span["start"]) * FPS))
            src_end = int(round(float(span["end"]) * FPS))
            if src_end <= src_start:
                continue
            clip_infos.append({
                "mediaPoolItem": media_pool_item,
                "startFrame": src_start,
                "endFrame": src_end,
                "recordFrame": record,
            })
            group_spans.append({
                "source_start_frame": src_start,
                "source_end_frame": src_end,
                "record_start_frame": record,
                "record_end_frame": record + (src_end - src_start),
                "text": span.get("text", ""),
            })
            record += src_end - src_start
        group_end = record
        groups.append({
            "id": group["id"],
            "product": group["product"],
            "title": group["title"],
            "record_start_frame": group_start,
            "record_end_frame": group_end,
            "duration_seconds": round((group_end - group_start) / FPS, 2),
            "span_count": len(group_spans),
            "spans": group_spans,
        })
        record += BLACK_GAP_FRAMES
    return clip_infos, groups


def append_with_rollback(media_pool, media_pool_item, clip_infos):
    appended = media_pool.AppendToTimeline(clip_infos)
    if not appended:
        raise RuntimeError("Resolve AppendToTimeline returned no items")
    if len(appended) < len(clip_infos):
        raise RuntimeError(f"Resolve appended only {len(appended)} of {len(clip_infos)} requested items")
    return appended


def verify(timeline, groups):
    video_items = sorted(timeline.GetItemListInTrack("video", 1) or [], key=lambda x: x.GetStart())
    audio_items = sorted(timeline.GetItemListInTrack("audio", 1) or [], key=lambda x: x.GetStart())
    offline = []
    for item in video_items + audio_items:
        try:
            if item.GetClipEnabled() is False:
                offline.append(item.GetName())
        except Exception:
            pass

    group_checks = []
    for i, group in enumerate(groups):
        g_start = group["record_start_frame"]
        g_end = group["record_end_frame"]
        v_in_group = [it for it in video_items if int(it.GetStart()) >= g_start and int(it.GetEnd()) <= g_end]
        a_in_group = [it for it in audio_items if int(it.GetStart()) >= g_start and int(it.GetEnd()) <= g_end]
        gaps = []
        for prev, cur in zip(v_in_group, v_in_group[1:]):
            gap = int(cur.GetStart()) - int(prev.GetEnd())
            if gap != 0:
                gaps.append(gap)
        next_gap = None
        if i + 1 < len(groups):
            next_gap = groups[i + 1]["record_start_frame"] - g_end
        group_checks.append({
            "id": group["id"],
            "duration_seconds": round((g_end - g_start) / FPS, 2),
            "video_items": len(v_in_group),
            "audio_items": len(a_in_group),
            "internal_video_gaps_frames": gaps,
            "gap_to_next_frames": next_gap,
            "duration_ok": 50 * FPS < (g_end - g_start) < 60 * FPS,
            "black_gap_ok": next_gap in (None, BLACK_GAP_FRAMES),
        })

    return {
        "video_item_count": len(video_items),
        "audio_item_count": len(audio_items),
        "offline_or_disabled": offline,
        "group_checks": group_checks,
    }


def main():
    resolve = load_resolve()
    if resolve is None:
        raise RuntimeError("Cannot connect to DaVinci Resolve scripting API")
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    timeline = project.GetCurrentTimeline()
    if project.GetName() != "20260701-淘宝切片" or timeline.GetName() != "测试":
        raise RuntimeError(f"Unexpected active timeline: {project.GetName()} / {timeline.GetName()}")

    pre = timeline_snapshot(project, timeline)
    (RESOLVE_DIR / "pre-write-snapshot.json").write_text(json.dumps(pre, ensure_ascii=False, indent=2), encoding="utf-8")

    media_pool_item = find_media_pool_item(timeline)
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    clip_infos, groups = build_clip_infos(plan, media_pool_item)
    expected_spans = sum(len(g["spans"]) for g in plan)
    if len(clip_infos) != expected_spans:
        raise RuntimeError("Clip info count does not match selected spans")

    remove_current_v1_a1(timeline)
    media_pool = project.GetMediaPool()
    append_with_rollback(media_pool, media_pool_item, clip_infos)
    time.sleep(1.0)

    post = timeline_snapshot(project, timeline)
    checks = verify(timeline, groups)
    saved = pm.SaveProject()
    report = {
        "project": project.GetName(),
        "timeline": timeline.GetName(),
        "fps": FPS,
        "source": VIDEO_PATH,
        "plan": str(PLAN_PATH),
        "expected_span_count": expected_spans,
        "groups": groups,
        "verification": checks,
        "project_saved": bool(saved),
        "post_snapshot": post,
    }
    (RESOLVE_DIR / "write-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "project": report["project"],
        "timeline": report["timeline"],
        "expected_span_count": expected_spans,
        "video_item_count": checks["video_item_count"],
        "audio_item_count": checks["audio_item_count"],
        "groups": checks["group_checks"],
        "project_saved": report["project_saved"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
