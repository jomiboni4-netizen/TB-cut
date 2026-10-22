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
from types import SimpleNamespace

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


def check(root=None, repo_root=None):
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
    project_root = Path(repo_root).resolve() if repo_root else Path(__file__).resolve().parents[1]
    from runtime_paths import resolve_runtime_paths
    if root is not None:
        paths = resolve_runtime_paths(project_root, root)
        state_path = paths.state_path
        state = paths.load_state() if state_path.exists() else None
    else:
        # With no batch workspace supplied, select the bound project workspace
        # from the live Resolve ID. Never consult the repository's legacy root state.
        paths = SimpleNamespace(repo_root=project_root)
        from project_registry import find_project_configs, project_directory, read_project_state
        matches = find_project_configs(paths, project.GetUniqueId())
        if len(matches) != 1:
            out.update(ready=False, reason=('project_registration_missing_for_live_resolve_id'
                                           if not matches else 'project_registration_ambiguous_for_live_resolve_id'))
            return out, 1
        config = matches[0]
        key = config['project_key']
        project_workspace = project_directory(paths, key)
        state = read_project_state(paths, key)
        state_path = project_workspace / 'PROJECT_STATE.json'
        out.update(bound_project_key=key, bound_workspace=str(project_workspace))
    if state is not None:
        expected = state["resolve"]
        if state.get('project_identity'):
            from project_registry import checked_project
            registered = checked_project(paths, state)
            if registered is None:
                expected_ids = {}
            else:
                expected_ids = registered['inputs']['resolve']
            if not expected_ids.get('source_timeline_id') or not expected_ids.get('target_timeline_id'):
                out.update(ready=False, reason='registered_timeline_stable_ids_missing')
            if registered and project.GetUniqueId() != registered['resolve_project_id']:
                out.update(ready=False, reason='project_stable_id_mismatch')
            elif expected_ids.get('source_timeline_id') and expected_ids.get('target_timeline_id'):
                timeline_by_id = {
                    project.GetTimelineByIndex(i).GetUniqueId(): project.GetTimelineByIndex(i)
                    for i in range(1, project.GetTimelineCount() + 1)
                }
                source = timeline_by_id.get(expected_ids['source_timeline_id'])
                target = timeline_by_id.get(expected_ids['target_timeline_id'])
                if (source is None or target is None or source.GetName() != expected['source_timeline']
                        or target.GetName() != expected['target_timeline']):
                    out.update(ready=False, reason='timeline_stable_id_mismatch')
        # Resolve's stable project ID authenticates the project. Its display name
        # selects a workflow mode but may change without changing project identity.
        if expected["source_timeline"] == expected["target_timeline"] or not all(
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
    parser.add_argument("--workspace", type=Path,
                        help="Optional batch workspace; otherwise select the project workspace by live Resolve ID")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument('--status-path', type=Path, help='Isolated diagnostic file for this run')
    args = parser.parse_args()
    from runtime_paths import resolve_runtime_paths
    workspace = resolve_runtime_paths(args.root, args.workspace).workspace_root if args.workspace else None
    if workspace and not workspace.joinpath('PROJECT_STATE.json').is_file() and not workspace.joinpath('BATCH_STATE.json').is_file():
        parser.error('selected_workspace_state_missing')
    STATUS_PATH = (workspace / '.cache' / 'resolve_connection_status.json' if workspace
                   else args.root.resolve() / '.cache' / 'resolve_connection_status.json')
    if args.status_path:
        STATUS_PATH = args.status_path.resolve()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    if args.worker:
        try:
            out, code = check(workspace, args.root)
        except Exception as exc:
            out, code = {"ready": False, "reason": "read_failed", "error_type": type(exc).__name__}, 1
        record_check(out)
        print(json.dumps(out, ensure_ascii=False), flush=True)
        # The native bridge may crash during interpreter cleanup, after a good read.
        os._exit(code)
    try:
        env = dict(os.environ, PYTHONUTF8="1")
        command = [sys.executable, str(Path(__file__).resolve()), "--root", str(args.root.resolve())]
        if workspace:
            command.extend(['--workspace', str(workspace)])
        command.extend(['--status-path', str(STATUS_PATH), "--worker"])
        child = subprocess.run(
            command,
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
