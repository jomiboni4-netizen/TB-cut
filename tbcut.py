#!/usr/bin/env python3
"""Small command wrapper for TB Cut V2 project maintenance."""
import argparse, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def run(script, extra, workspace=None):
    cmd = [sys.executable, str(ROOT / "scripts" / script), "--root", str(ROOT)] + (["--workspace", workspace] if workspace else []) + extra
    raise SystemExit(subprocess.call(cmd))

def main():
    ap = argparse.ArgumentParser(prog="tbcut", description="TB Cut V2 maintenance CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor")
    p = sub.add_parser("fingerprint")
    p = sub.add_parser("index-subtitles")
    p = sub.add_parser("locate-links")

    p = sub.add_parser("clean")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--light", action="store_true")
    mode.add_argument("--cache", action="store_true")
    mode.add_argument("--reports", action="store_true")
    mode.add_argument("--all", action="store_true")
    p.add_argument("--apply", action="store_true")

    p = sub.add_parser("init")
    p.add_argument("--title")
    p.add_argument("--subtitles", nargs="*")
    p.add_argument("--videos", nargs="*")
    p.add_argument("--resolve-project")
    p.add_argument("--source-timeline")
    p.add_argument("--target-timeline")
    p.add_argument("--force", action="store_true")

    for command in sub.choices.values():
        command.add_argument("--workspace")

    a = ap.parse_args()
    if a.cmd == "doctor": run("project_doctor.py", [], a.workspace)
    if a.cmd == "fingerprint": run("fingerprint_inputs.py", [], a.workspace)
    if a.cmd == "index-subtitles": run("index_subtitles.py", [], a.workspace)
    if a.cmd == "locate-links": run("locate_link_intros.py", [], a.workspace)
    if a.cmd == "clean":
        mode = "--light" if a.light else "--cache" if a.cache else "--reports" if a.reports else "--all"
        run("cleanup.py", [mode] + (["--apply"] if a.apply else []), a.workspace)
    if a.cmd == "init":
        extra = []
        if a.title: extra += ["--title", a.title]
        if a.subtitles is not None: extra += ["--subtitles", *a.subtitles]
        if a.videos is not None: extra += ["--videos", *a.videos]
        if a.resolve_project: extra += ["--resolve-project", a.resolve_project]
        if a.source_timeline: extra += ["--source-timeline", a.source_timeline]
        if a.target_timeline: extra += ["--target-timeline", a.target_timeline]
        if a.force: extra += ["--force"]
        run("init_project.py", extra, a.workspace)

if __name__ == "__main__":
    main()
