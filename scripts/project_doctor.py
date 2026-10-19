#!/usr/bin/env python3
import argparse, json, shutil, subprocess
from pathlib import Path
from runtime_paths import resolve_runtime_paths
from workbook_runtime import preferred_node

REQUIRED = ["AGENTS.md", "PROJECT_RULES.md", "config/content_rules.json", "config/vision_rules.json"]

def main():
    ap = argparse.ArgumentParser(description="Check TB Cut V2 project health")
    ap.add_argument("--root", default=".")
    ap.add_argument("--workspace")
    ap.add_argument("--node", default=None)
    args = ap.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace or root)
    errors, warnings = [], []
    for rel in REQUIRED:
        if not (root / rel).exists(): errors.append(f"missing: {rel}")
    state_path = paths.state_path
    if not state_path.exists():
        warnings.append("PROJECT_STATE.json not initialized yet")
    else:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
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
            print("OK   : workbook runtime matches a reviewed compatibility record")
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
    print("Scope: required paths, state JSON/configured fields, command availability, workbook dependencies; no runtime cache/media/Resolve validation")
    for x in errors: print("ERROR:", x)
    for x in warnings: print("WARN :", x)
    if not errors: print("OK   : project structure is valid")
    raise SystemExit(1 if errors else 0)

if __name__ == "__main__":
    main()
