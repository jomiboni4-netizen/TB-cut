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


def item_path(item):
    try:
        mpi = item.GetMediaPoolItem()
        return (mpi.GetClipProperty() or {}).get("File Path", "")
    except Exception:
        return ""


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
                snap["items"].append({
                    "type": kind,
                    "track": track,
                    "name": item.GetName(),
                    "start": int(item.GetStart()),
                    "end": int(item.GetEnd()),
                    "duration": int(item.GetDuration()),
                    "path": item_path(item),
                })
    return snap


def ensure_tracks(timeline, video_track, audio_track):
    while timeline.GetTrackCount("video") < video_track:
        if not timeline.AddTrack("video"):
            raise RuntimeError(f"Resolve rejected AddTrack(video) for V{video_track}")
    while timeline.GetTrackCount("audio") < audio_track:
        if not timeline.AddTrack("audio"):
            raise RuntimeError(f"Resolve rejected AddTrack(audio) for A{audio_track}")


def find_media_pool_item(timeline, source_path):
    source_path = str(Path(source_path))
    for kind in ("video", "audio"):
        for track in range(1, timeline.GetTrackCount(kind) + 1):
            for item in timeline.GetItemListInTrack(kind, track) or []:
                try:
                    mpi = item.GetMediaPoolItem()
                    props = mpi.GetClipProperty() or {}
                    if props.get("File Path") == source_path:
                        return mpi
                except Exception:
                    continue
    raise RuntimeError(f"Cannot find source on current timeline: {source_path}")


def track_end(timeline, kind, track):
    items = timeline.GetItemListInTrack(kind, track) or []
    if not items:
        return int(timeline.GetStartFrame())
    return max(int(item.GetEnd()) for item in items)


def load_state(path):
    p = Path(path)
    if not p.exists():
        return {"completed_group_ids": [], "batches": []}
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(path, state):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def choose_groups(plan, only_product, only_group_id, state, force):
    done = set(state.get("completed_group_ids", []))
    groups = []
    for group in plan:
        if only_product and group.get("product") != only_product:
            continue
        if only_group_id and group.get("id") != only_group_id:
            continue
        if not force and group.get("id") in done:
            continue
        groups.append(group)
    return groups


def build_clip_infos(groups, media_pool_item, fps, record_start, video_track, audio_track):
    clip_infos = []
    planned_groups = []
    record = int(record_start)
    black_gap_frames = int(round(BLACK_GAP_SECONDS * fps))
    for group in groups:
        group_start = record
        group_spans = []
        for span in group["spans"]:
            src_start = int(round(float(span["start"]) * fps))
            src_end = int(round(float(span["end"]) * fps))
            if src_end <= src_start:
                continue
            duration = src_end - src_start
            base = {
                "mediaPoolItem": media_pool_item,
                "startFrame": src_start,
                "endFrame": src_end,
                "recordFrame": record,
            }
            video_clip = dict(base)
            video_clip.update({"mediaType": 1, "trackIndex": video_track})
            audio_clip = dict(base)
            audio_clip.update({"mediaType": 2, "trackIndex": audio_track})
            clip_infos.extend([video_clip, audio_clip])
            group_spans.append({
                "source_start_frame": src_start,
                "source_end_frame": src_end,
                "record_start_frame": record,
                "record_end_frame": record + duration,
                "text": span.get("text", ""),
            })
            record += duration
        group_end = record
        planned_groups.append({
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
    return clip_infos, planned_groups, record


def verify_groups(timeline, groups, fps, video_track, audio_track):
    video_items = sorted(timeline.GetItemListInTrack("video", video_track) or [], key=lambda x: x.GetStart())
    audio_items = sorted(timeline.GetItemListInTrack("audio", audio_track) or [], key=lambda x: x.GetStart())
    checks = []
    offline = []
    for item in video_items + audio_items:
        try:
            if item.GetClipEnabled() is False:
                offline.append(item.GetName())
        except Exception:
            pass

    for i, group in enumerate(groups):
        g_start = group["record_start_frame"]
        g_end = group["record_end_frame"]
        v = [it for it in video_items if int(it.GetStart()) >= g_start and int(it.GetEnd()) <= g_end]
        a = [it for it in audio_items if int(it.GetStart()) >= g_start and int(it.GetEnd()) <= g_end]
        av_mismatches = []
        for vi, ai in zip(v, a):
            if int(vi.GetStart()) != int(ai.GetStart()) or int(vi.GetEnd()) != int(ai.GetEnd()):
                av_mismatches.append([int(vi.GetStart()), int(vi.GetEnd()), int(ai.GetStart()), int(ai.GetEnd())])
        next_gap = None
        if i + 1 < len(groups):
            next_gap = groups[i + 1]["record_start_frame"] - g_end
        checks.append({
            "id": group["id"],
            "duration_seconds": round((g_end - g_start) / fps, 2),
            "video_items": len(v),
            "audio_items": len(a),
            "av_mismatches": av_mismatches,
            "duration_ok": 50 * fps < (g_end - g_start) < 60 * fps,
            "black_gap_to_next_ok": next_gap in (None, int(round(BLACK_GAP_SECONDS * fps))),
        })
    return {"offline_or_disabled": offline, "group_checks": checks}


def main():
    parser = argparse.ArgumentParser(description="Append finished live-shopping product clips without touching source tracks.")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--project")
    parser.add_argument("--timeline")
    parser.add_argument("--product")
    parser.add_argument("--group-id")
    parser.add_argument("--video-track", type=int, default=2)
    parser.add_argument("--audio-track", type=int, default=2)
    parser.add_argument("--append-gap-seconds", type=float, default=5.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    global BLACK_GAP_SECONDS
    BLACK_GAP_SECONDS = args.append_gap_seconds

    resolve = load_resolve()
    if resolve is None:
        raise RuntimeError("Cannot connect to DaVinci Resolve scripting API")
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    timeline = project.GetCurrentTimeline()
    if args.project and project.GetName() != args.project:
        raise RuntimeError(f"Unexpected project: {project.GetName()}")
    if args.timeline and timeline.GetName() != args.timeline:
        raise RuntimeError(f"Unexpected timeline: {timeline.GetName()}")

    fps = float(project.GetSetting("timelineFrameRate") or project.GetSetting("timelinePlaybackFrameRate") or 30)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    pre = timeline_snapshot(project, timeline)
    (report_dir / "pre-append-snapshot.json").write_text(json.dumps(pre, ensure_ascii=False, indent=2), encoding="utf-8")

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    state = load_state(args.state)
    groups = choose_groups(plan, args.product, args.group_id, state, args.force)
    if not groups:
        print(json.dumps({"status": "nothing_to_append", "state": args.state}, ensure_ascii=False, indent=2))
        return

    media_pool_item = find_media_pool_item(timeline, args.source)
    if args.dry_run:
        video_track_exists = timeline.GetTrackCount("video") >= args.video_track
        audio_track_exists = timeline.GetTrackCount("audio") >= args.audio_track
        record_start = max(
            track_end(timeline, "video", args.video_track) if video_track_exists else int(timeline.GetStartFrame()),
            track_end(timeline, "audio", args.audio_track) if audio_track_exists else int(timeline.GetStartFrame()),
        )
    else:
        ensure_tracks(timeline, args.video_track, args.audio_track)
        record_start = max(track_end(timeline, "video", args.video_track), track_end(timeline, "audio", args.audio_track))
    if record_start > int(timeline.GetStartFrame()):
        record_start += int(round(args.append_gap_seconds * fps))
    clip_infos, planned_groups, record_end = build_clip_infos(
        groups, media_pool_item, fps, record_start, args.video_track, args.audio_track
    )

    blueprint = {
        "project": project.GetName(),
        "timeline": timeline.GetName(),
        "fps": fps,
        "source": args.source,
        "destination_tracks": {"video": args.video_track, "audio": args.audio_track},
        "record_start_frame": record_start,
        "record_end_frame": record_end,
        "groups": planned_groups,
        "clip_info_count": len(clip_infos),
        "dry_run": args.dry_run,
    }
    (report_dir / "append-blueprint.json").write_text(json.dumps(blueprint, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.dry_run:
        print(json.dumps(blueprint, ensure_ascii=False, indent=2))
        return

    appended = project.GetMediaPool().AppendToTimeline(clip_infos)
    if not appended or len(appended) < len(clip_infos):
        raise RuntimeError(f"Resolve appended {len(appended) if appended else 0} of {len(clip_infos)} requested items")
    time.sleep(1.0)

    post = timeline_snapshot(project, timeline)
    checks = verify_groups(timeline, planned_groups, fps, args.video_track, args.audio_track)
    completed = set(state.get("completed_group_ids", []))
    for group in planned_groups:
        completed.add(group["id"])
    state["completed_group_ids"] = sorted(completed)
    state.setdefault("batches", []).append({
        "project": project.GetName(),
        "timeline": timeline.GetName(),
        "source": args.source,
        "group_ids": [g["id"] for g in planned_groups],
        "record_start_frame": record_start,
        "record_end_frame": record_end,
        "destination_tracks": {"video": args.video_track, "audio": args.audio_track},
        "report_dir": str(report_dir),
    })
    save_state(args.state, state)

    saved = pm.SaveProject()
    report = {
        **blueprint,
        "dry_run": False,
        "appended_item_count": len(appended),
        "verification": checks,
        "project_saved": bool(saved),
        "post_snapshot": post,
        "state": state,
    }
    (report_dir / "append-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "project": project.GetName(),
        "timeline": timeline.GetName(),
        "appended_item_count": len(appended),
        "groups": checks["group_checks"],
        "offline_or_disabled": checks["offline_or_disabled"],
        "project_saved": bool(saved),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
