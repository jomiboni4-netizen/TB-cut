#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path


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
        pass

    media_props = {}
    path = ""
    try:
        mpi = item.GetMediaPoolItem()
        media_props = mpi.GetClipProperty() or {}
        path = media_props.get("File Path", "")
    except Exception:
        pass

    def maybe_int(fn_name):
        fn = getattr(item, fn_name, None)
        if not callable(fn):
            return None
        try:
            value = fn()
            return int(value) if value is not None else None
        except Exception:
            return None

    return {
        "type": kind,
        "track": track_index,
        "name": item.GetName(),
        "start": int(item.GetStart()),
        "end": int(item.GetEnd()),
        "duration": int(item.GetDuration()),
        "sourceStart": maybe_int("GetSourceStart"),
        "sourceEnd": maybe_int("GetSourceEnd"),
        "path": path,
        "media": {
            "duration": media_props.get("Duration"),
            "fps": media_props.get("FPS"),
            "resolution": media_props.get("Resolution"),
            "audio_channels": media_props.get("Audio Ch"),
            "codec": media_props.get("Codec"),
        },
        "properties": props,
    }


def main():
    resolve = load_resolve()
    if resolve is None:
        raise RuntimeError("Cannot connect to DaVinci Resolve scripting API")
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None:
        raise RuntimeError("No current Resolve project")
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise RuntimeError("No current Resolve timeline")

    fps = project.GetSetting("timelineFrameRate") or project.GetSetting("timelinePlaybackFrameRate")
    snap = {
        "resolve_version": resolve.GetVersionString() if hasattr(resolve, "GetVersionString") else None,
        "project": project.GetName(),
        "timeline": timeline.GetName(),
        "fps": float(fps) if fps else None,
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

    print(json.dumps(snap, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
