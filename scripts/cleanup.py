#!/usr/bin/env python3
import argparse, json, shutil
from pathlib import Path
from runtime_paths import resolve_runtime_paths

LIGHT = [
    ".cache/tmp",
    "exports/tmp"
]
CACHE = [
    ".cache/subtitles",
    ".cache/products",
    ".cache/asr",
    ".cache/vision",
    ".cache/mimo",
    ".cache/tmp"
]
REPORTS = ["reports"]

PROTECTED_NAMES = {
    "AGENTS.md", "PROJECT_RULES.md", "PROJECT_STATE.json", "PROJECT_STATE.template.json", "CHANGELOG.md"
}

def load_protected_paths(root: Path, workspace: Path | None = None):
    protected = {root / x for x in PROTECTED_NAMES}
    state = (workspace or root) / "PROJECT_STATE.json"
    protected.add(state)
    if state.exists():
        try:
            s = json.loads(state.read_text(encoding="utf-8"))
            vals = []
            vals.append(s.get("title_path"))
            vals.extend(s.get("subtitle_paths") or [])
            vals.extend(s.get("video_paths") or [])
            for v in vals:
                if v:
                    protected.add(Path(v).expanduser().resolve())
        except Exception:
            pass
    return protected

def safe_target(root: Path, rel: str, protected):
    p = (root / rel).resolve()
    if root not in p.parents and p != root:
        raise RuntimeError(f"Unsafe target outside root: {p}")
    if p in protected:
        raise RuntimeError(f"Protected path: {p}")
    return p

def main():
    ap = argparse.ArgumentParser(description="Safely clean reproducible TB Cut V2 files")
    ap.add_argument("--root", default=".")
    ap.add_argument("--workspace")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--light", action="store_true", help="temporary files only")
    g.add_argument("--cache", action="store_true", help="all reproducible caches")
    g.add_argument("--reports", action="store_true", help="reports only")
    g.add_argument("--all", action="store_true", help="cache + reports + temporary exports")
    ap.add_argument("--apply", action="store_true", help="actually delete; default is dry-run")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    workspace = resolve_runtime_paths(root, args.workspace).workspace_root
    protected = load_protected_paths(root, workspace)
    rels = LIGHT if args.light else CACHE if args.cache else REPORTS if args.reports else sorted(set(CACHE + REPORTS + ["exports/tmp"]))

    print("APPLY" if args.apply else "DRY-RUN", "cleanup in", workspace)
    for rel in rels:
        p = safe_target(workspace, rel, protected)
        if not p.exists():
            print("skip   ", rel, "(missing)")
            continue
        print("delete ", rel)
        if args.apply:
            if p.is_dir(): shutil.rmtree(p)
            else: p.unlink()
            p.mkdir(parents=True, exist_ok=True) if p.suffix == "" else None
    if not args.apply:
        print("Nothing deleted. Re-run with --apply to execute.")

if __name__ == "__main__":
    main()
