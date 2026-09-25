#!/usr/bin/env python3
import argparse, json, shutil
from pathlib import Path
from runtime_paths import resolve_runtime_paths


def main():
    ap = argparse.ArgumentParser(description="Initialize PROJECT_STATE.json for TB Cut V2")
    ap.add_argument("--root", default=".")
    ap.add_argument("--workspace")
    ap.add_argument("--title")
    ap.add_argument("--subtitles", nargs="*")
    ap.add_argument("--videos", nargs="*")
    ap.add_argument("--resolve-project")
    ap.add_argument("--source-timeline")
    ap.add_argument("--target-timeline")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    tpl = root / "PROJECT_STATE.template.json"
    out = paths.state_path
    if out.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite {out}; use --force if intentional")
    if not tpl.exists():
        raise SystemExit(f"Missing template: {tpl}")

    state = json.loads(tpl.read_text(encoding="utf-8"))
    state["project_root"] = str(root)
    if args.title: state["title_path"] = str(Path(args.title).expanduser())
    if args.subtitles is not None: state["subtitle_paths"] = [str(Path(x).expanduser()) for x in args.subtitles]
    if args.videos is not None: state["video_paths"] = [str(Path(x).expanduser()) for x in args.videos]
    if args.resolve_project: state["resolve"]["project_name"] = args.resolve_project
    if args.source_timeline: state["resolve"]["source_timeline"] = args.source_timeline
    if args.target_timeline: state["resolve"]["target_timeline"] = args.target_timeline
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Initialized: {out}")

if __name__ == "__main__":
    main()
