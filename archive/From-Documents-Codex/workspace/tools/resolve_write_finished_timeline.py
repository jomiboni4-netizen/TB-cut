#!/usr/bin/env python3
import argparse
import importlib.util
import json
import sys
import time
import traceback
from pathlib import Path


BLACK_GAP_SECONDS = 5.0


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


def get_timeline(project, name):
    for i in range(1, int(project.GetTimelineCount()) + 1):
        timeline = project.GetTimelineByIndex(i)
        if timeline.GetName() == name:
            return timeline
    raise RuntimeError(f"Timeline not found: {name}")


def item_path(item):
    try:
        mpi = item.GetMediaPoolItem()
        return (mpi.GetClipProperty() or {}).get("File Path", "")
    except Exception:
        return ""


def snapshot(project, timeline):
    data = {
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
        for track in range(1, data["tracks"][kind] + 1):
            for item in timeline.GetItemListInTrack(kind, track) or []:
                data["items"].append({
                    "type": kind,
                    "track": track,
                    "name": item.GetName(),
                    "start": int(item.GetStart()),
                    "end": int(item.GetEnd()),
                    "duration": int(item.GetDuration()),
                    "path": item_path(item),
                })
    return data


def find_media_pool_item(source_timeline, source_path):
    source_path = str(Path(source_path))
    for kind in ("video", "audio"):
        for track in range(1, source_timeline.GetTrackCount(kind) + 1):
            for item in source_timeline.GetItemListInTrack(kind, track) or []:
                try:
                    mpi = item.GetMediaPoolItem()
                    props = mpi.GetClipProperty() or {}
                    if props.get("File Path") == source_path:
                        return mpi
                except Exception:
                    continue
    raise RuntimeError(f"Cannot find source on source timeline: {source_path}")


def clear_v1_a1(timeline):
    delete = []
    if timeline.GetTrackCount("video") >= 1:
        delete.extend(timeline.GetItemListInTrack("video", 1) or [])
    if timeline.GetTrackCount("audio") >= 1:
        delete.extend(timeline.GetItemListInTrack("audio", 1) or [])
    if delete and not timeline.DeleteClips(delete, False):
        raise RuntimeError("Resolve rejected DeleteClips on target V1/A1")
    time.sleep(0.5)


def build_clip_infos(plan, source_items, fps):
    media_by_path = {item["path"]: item["mediaPoolItem"] for item in source_items}
    clip_infos = []
    groups = []
    record = 0
    black_gap_frames = int(round(BLACK_GAP_SECONDS * fps))
    for group in plan:
        source_path = group["source_path"]
        media_pool_item = media_by_path[source_path]
        group_start = record
        group_spans = []
        for span in group["spans"]:
            src_start = int(round(float(span["start"]) * fps))
            src_end = int(round(float(span["end"]) * fps))
            if src_end <= src_start:
                continue
            duration = src_end - src_start
            clip_infos.append({
                "mediaPoolItem": media_pool_item,
                "startFrame": src_start,
                "endFrame": src_end,
                "recordFrame": record,
            })
            group_spans.append({
                "source_path": source_path,
                "source_start_frame": src_start,
                "source_end_frame": src_end,
                "record_start_frame": record,
                "record_end_frame": record + duration,
                "text": span.get("text", ""),
            })
            record += duration
        group_end = record
        groups.append({
            "id": group["id"],
            "product": group.get("product", ""),
            "title": group.get("title", ""),
            "record_start_frame": group_start,
            "record_end_frame": group_end,
            "duration_seconds": round((group_end - group_start) / fps, 2),
            "span_count": len(group_spans),
            "spans": group_spans,
        })
        record += black_gap_frames
    return clip_infos, groups


def verify(timeline, groups, fps):
    video_items = sorted(timeline.GetItemListInTrack("video", 1) or [], key=lambda x: x.GetStart())
    audio_items = sorted(timeline.GetItemListInTrack("audio", 1) or [], key=lambda x: x.GetStart())
    offline = []
    for item in video_items + audio_items:
        try:
            if item.GetClipEnabled() is False:
                offline.append(item.GetName())
        except Exception:
            pass
    checks = []
    for index, group in enumerate(groups):
        g_start = group["record_start_frame"]
        g_end = group["record_end_frame"]
        v = [it for it in video_items if int(it.GetStart()) >= g_start and int(it.GetEnd()) <= g_end]
        a = [it for it in audio_items if int(it.GetStart()) >= g_start and int(it.GetEnd()) <= g_end]
        av_mismatches = []
        for vi, ai in zip(v, a):
            if int(vi.GetStart()) != int(ai.GetStart()) or int(vi.GetEnd()) != int(ai.GetEnd()):
                av_mismatches.append([int(vi.GetStart()), int(vi.GetEnd()), int(ai.GetStart()), int(ai.GetEnd())])
        next_gap = None
        if index + 1 < len(groups):
            next_gap = groups[index + 1]["record_start_frame"] - g_end
        checks.append({
            "id": group["id"],
            "duration_seconds": round((g_end - g_start) / fps, 2),
            "video_items": len(v),
            "audio_items": len(a),
            "av_mismatches": av_mismatches,
            "duration_ok": 50 * fps < (g_end - g_start) < 60 * fps,
            "black_gap_ok": next_gap in (None, int(round(BLACK_GAP_SECONDS * fps))),
        })
    return {
        "video_item_count": len(video_items),
        "audio_item_count": len(audio_items),
        "offline_or_disabled": offline,
        "group_checks": checks,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--source-timeline", required=True)
    parser.add_argument("--target-timeline", required=True)
    parser.add_argument("--replace-target", action="store_true")
    args = parser.parse_args()

    resolve = load_resolve()
    if resolve is None:
        raise RuntimeError("Cannot connect to DaVinci Resolve scripting API")
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if project.GetName() != args.project:
        raise RuntimeError(f"Unexpected project: {project.GetName()}")
    source_timeline = get_timeline(project, args.source_timeline)
    target_timeline = get_timeline(project, args.target_timeline)
    fps = float(project.GetSetting("timelineFrameRate") or project.GetSetting("timelinePlaybackFrameRate") or 30)

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    pre = {
        "source": snapshot(project, source_timeline),
        "target": snapshot(project, target_timeline),
    }
    (report_dir / "pre-write-snapshot.json").write_text(json.dumps(pre, ensure_ascii=False, indent=2), encoding="utf-8")

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    source_paths = sorted({group["source_path"] for group in plan})
    source_items = [{"path": path, "mediaPoolItem": find_media_pool_item(source_timeline, path)} for path in source_paths]
    clip_infos, groups = build_clip_infos(plan, source_items, fps)
    if args.replace_target:
        clear_v1_a1(target_timeline)

    project.SetCurrentTimeline(target_timeline)
    appended = project.GetMediaPool().AppendToTimeline(clip_infos)
    if not appended or len(appended) < len(clip_infos):
        raise RuntimeError(f"Resolve appended {len(appended) if appended else 0} of {len(clip_infos)} requested items")
    time.sleep(1.0)

    checks = verify(target_timeline, groups, fps)
    post = {
        "source": snapshot(project, source_timeline),
        "target": snapshot(project, target_timeline),
    }
    saved = pm.SaveProject()
    report = {
        "project": project.GetName(),
        "source_timeline": args.source_timeline,
        "target_timeline": args.target_timeline,
        "fps": fps,
        "plan": args.plan,
        "source_paths": source_paths,
        "groups": groups,
        "verification": checks,
        "project_saved": bool(saved),
        "post_snapshot": post,
    }
    (report_dir / "write-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "project": project.GetName(),
        "target_timeline": args.target_timeline,
        "appended_item_count": len(appended),
        "video_item_count": checks["video_item_count"],
        "audio_item_count": checks["audio_item_count"],
        "offline_or_disabled": checks["offline_or_disabled"],
        "groups": checks["group_checks"],
        "project_saved": bool(saved),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
