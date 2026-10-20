#!/usr/bin/env python3
import argparse, json, shutil, subprocess
from pathlib import Path
from runtime_paths import resolve_runtime_paths
from workbook_runtime import preferred_node

REQUIRED = ["AGENTS.md", "PROJECT_RULES.md", "config/content_rules.json", "config/vision_rules.json",
            "config/project_editing_modes.json"]

def main():
    ap = argparse.ArgumentParser(description="Check TB Cut V2 project health")
    ap.add_argument("--root", default=".")
    ap.add_argument("--workspace")
    ap.add_argument("--node", default=None)
    args = ap.parse_args()
    root = Path(args.root).resolve()
    # Without an explicit workspace this is a repository-structure check only.
    # In particular, never treat the root PROJECT_STATE.json as the active batch.
    paths = resolve_runtime_paths(root, args.workspace) if args.workspace else None
    errors, warnings = [], []
    for rel in REQUIRED:
        if not (root / rel).exists(): errors.append(f"missing: {rel}")
    state_path = paths.state_path if paths else None
    if paths is None:
        warnings.append("no --workspace supplied; active project/batch state was not checked (root PROJECT_STATE.json is legacy only)")
    elif not state_path.exists():
        warnings.append("workspace PROJECT_STATE.json not initialized yet")
    else:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("project_identity") or state.get("project_root"):
                warnings.append("legacy batch-scoped project registration detected; canonical per-Resolve-project state migration is not implemented")
            for key in ("title_path", "subtitle_paths", "video_paths"):
                if not state.get(key): warnings.append(f"state not configured: {key}")
            r = state.get("resolve", {})
            for key in ("project_name", "source_timeline", "target_timeline"):
                if not r.get(key): warnings.append(f"resolve not configured: {key}")
        except Exception as e:
            errors.append(f"invalid PROJECT_STATE.json: {e}")
    for cmd in ("python3", "ffmpeg", "ffprobe"):
        if shutil.which(cmd) is None: warnings.append(f"command not found: {cmd}")
    try:
        result = subprocess.run([args.node or preferred_node(), str(root / "scripts/check_workbook_dependencies.mjs")],
                                capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            print("OK   : Codex workbook package identity and required APIs are available")
        elif result.returncode in (2, 3):
            detail = result.stdout.strip() or result.stderr.strip()
            try:
                check = json.loads(detail)
                status = check.get("status", "unverified")
                reason = check.get("reason", "workbook_runtime_not_verified")
            except (json.JSONDecodeError, AttributeError):
                status, reason = "unverified", detail or "workbook_runtime_not_verified"
            warnings.append(f"workbook operations {status}: {reason}")
        else:
            errors.append("workbook compatibility checker failed: " + result.stderr.strip())
    except (OSError, subprocess.TimeoutExpired) as exc:
        warnings.append("workbook operations unverified: " + type(exc).__name__)
    print("TB Cut V2 doctor")
    print("Scope: required paths, workspace state/configured fields when --workspace is supplied, command availability, workbook dependencies; no runtime cache/media/Resolve validation")
    for x in errors: print("ERROR:", x)
    for x in warnings: print("WARN :", x)
    if not errors: print("OK   : project structure is valid")
    raise SystemExit(1 if errors else 0)

if __name__ == "__main__":
    main()
