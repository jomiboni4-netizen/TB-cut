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


def main():
    resolve = load_resolve()
    if resolve is None:
        raise RuntimeError("Cannot connect to DaVinci Resolve scripting API")
    project = resolve.GetProjectManager().GetCurrentProject()
    current = project.GetCurrentTimeline()
    timelines = []
    for i in range(1, int(project.GetTimelineCount()) + 1):
        timeline = project.GetTimelineByIndex(i)
        timelines.append({
            "index": i,
            "name": timeline.GetName(),
            "start": int(timeline.GetStartFrame()),
            "end": int(timeline.GetEndFrame()),
            "video_tracks": int(timeline.GetTrackCount("video")),
            "audio_tracks": int(timeline.GetTrackCount("audio")),
            "is_current": timeline.GetName() == current.GetName() if current else False,
        })
    print(json.dumps({
        "project": project.GetName(),
        "current_timeline": current.GetName() if current else None,
        "timelines": timelines,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
