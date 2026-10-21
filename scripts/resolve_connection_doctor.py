#!/usr/bin/env python3
"""Bounded, isolated, read-only Resolve check using the shared connector."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from resolve_connection import STATUS_PATH, connect, save_status


def record_check(out):
    try:
        status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        status = {"schema_version": 1}
    status.update(check_ready=out.get("ready", False), check_reason=out["reason"],
                  check_at=datetime.now(timezone.utc).isoformat())
    if out["reason"] in ("connection_check_timed_out", "connection_worker_failed"):
        # Do not leave a previous successful connection as the apparent latest result.
        status.update(connected=None, route=None, reason=out["reason"], attempts=[])
    save_status(status, STATUS_PATH)


def check(root):
    resolve = connect(status_path=STATUS_PATH)
    if resolve is None:
        try:
            reason = json.loads(STATUS_PATH.read_text(encoding="utf-8"))["reason"]
        except (OSError, ValueError, KeyError):
            reason = "connection_failed"
        return {"connected": False, "ready": False, "reason": reason}, 1
    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        return {"connected": True, "ready": False, "reason": "no_project_open"}, 1
    timeline = project.GetCurrentTimeline()
    names = [project.GetTimelineByIndex(i).GetName() for i in range(1, project.GetTimelineCount() + 1)]
    out = {"connected": True, "project": project.GetName(),
           "current_timeline": timeline.GetName() if timeline else None,
           "timelines": names, "AI cut": "AI cut" in names, "成品": "成品" in names,
           "ready": True, "reason": "ready"}
    try:
        from project_editing_modes import resolve_editing_mode
        mode_id, mode_policy = resolve_editing_mode(project.GetName())
        out.update(editing_mode=mode_id, editing_policy=mode_policy)
    except Exception as exc:
        out.update(ready=False, reason=str(exc) if str(exc).startswith('resolve_project_mode_')
                   else 'project_editing_mode_invalid')
    state_path = root / "PROJECT_STATE.json"
    if state_path.exists():
        expected = json.loads(state_path.read_text(encoding="utf-8"))["resolve"]
        if project.GetName() != expected["project_name"]:
            out.update(ready=False, reason="project_mismatch")
        elif expected["source_timeline"] == expected["target_timeline"] or not all(
            expected[key] in names for key in ("source_timeline", "target_timeline")
        ):
            out.update(ready=False, reason="timeline_configuration_mismatch")
    if out['ready']:
        identity = project.GetUniqueId()
        timeline_ids = [project.GetTimelineByIndex(i).GetUniqueId() for i in range(1, project.GetTimelineCount()+1)]
        if not identity or any(not value for value in timeline_ids) or len(set(timeline_ids)) != len(timeline_ids):
            out.update(ready=False, reason='stable_resolve_identity_unavailable')
        else:
            out.update(project_id=identity, timeline_ids=timeline_ids)
    return out, 0 if out["ready"] else 1


def main():
    global STATUS_PATH
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument('--status-path', type=Path, help='Isolated diagnostic file for this run')
    args = parser.parse_args()
    from runtime_paths import resolve_runtime_paths
    workspace = resolve_runtime_paths(args.root, args.workspace).workspace_root
    if not (workspace / 'PROJECT_STATE.json').is_file():
        parser.error('selected_workspace_state_missing')
    STATUS_PATH = workspace / '.cache' / 'resolve_connection_status.json'
    if args.status_path:
        STATUS_PATH = args.status_path.resolve()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    if args.worker:
        try:
            out, code = check(workspace)
        except Exception as exc:
            out, code = {"ready": False, "reason": "read_failed", "error_type": type(exc).__name__}, 1
        record_check(out)
        print(json.dumps(out, ensure_ascii=False), flush=True)
        # The native bridge may crash during interpreter cleanup, after a good read.
        os._exit(code)
    try:
        env = dict(os.environ, PYTHONUTF8="1")
        child = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--root", str(args.root.resolve()),
             '--workspace', str(workspace),
             '--status-path', str(STATUS_PATH), "--worker"],
            capture_output=True, text=True, encoding="utf-8", timeout=args.timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        out = {"ready": False, "reason": "connection_check_timed_out"}
        record_check(out)
        print(json.dumps(out))
        return 1
    if child.stdout:
        print(child.stdout, end="")
    else:
        out = {"ready": False, "reason": "connection_worker_failed", "exit_code": child.returncode}
        record_check(out)
        print(json.dumps(out))
    return child.returncode


if __name__ == "__main__":
    raise SystemExit(main())
