#!/usr/bin/env python3
import argparse, json, shutil
from pathlib import Path
from runtime_paths import resolve_runtime_paths

REQUIRED = ["AGENTS.md", "PROJECT_RULES.md", "config/content_rules.json", "config/vision_rules.json"]

def main():
    ap = argparse.ArgumentParser(description="Check TB Cut V2 project health")
    ap.add_argument("--root", default=".")
    ap.add_argument("--workspace")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
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
    print("TB Cut V2 doctor")
    for x in errors: print("ERROR:", x)
    for x in warnings: print("WARN :", x)
    if not errors: print("OK   : project structure is valid")
    raise SystemExit(1 if errors else 0)

if __name__ == "__main__":
    main()
