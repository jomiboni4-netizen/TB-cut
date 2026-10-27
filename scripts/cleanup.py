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
    "AGENTS.md", "PROJECT_RULES.md", "PROJECT_STATE.json", "PROJECT_STATE.template.json", "CHANGELOG.md",
    "PROJECT_CONFIG.json", "RUN.json", "PROJECT_TASKS.json",
    "PROJECT.md", "ACCEPTANCE.md", "PLAN.md", "STATUS.md", "DECISIONS.md",
}

BUILD_DIRS = {'swift-module-cache', 'swift_module_cache', 'swift_modules',
              'ModuleCache.noindex', '__pycache__'}


def rebuildable_targets(workspace: Path):
    """Narrow allowlist: generated compiler modules and extracted scratch media.

    Preserve analysis JSON, plans, screenshots, models, backups and exports.
    Never infer that an arbitrary media file or historical workspace is waste.
    """
    base = workspace / '.cache'
    targets = []
    if base.is_dir():
        for p in sorted(base.rglob('*')):
            if p.is_symlink() or any(parent in targets for parent in p.parents):
                continue
            if p.is_dir() and p.name in BUILD_DIRS:
                targets.append(p)
            elif p.is_file():
                parts = p.relative_to(base).parts
                scratch_audio = (p.suffix == '.wav' and
                    (parts[0] == 'workspaces' or
                     parts[0] == 'diagnostics' and any('listen_gate' in part for part in parts)))
                preview = (p.suffix == '.mp4' and parts[0] == 'diagnostics'
                           and any('preview' in part for part in parts[:-1])
                           and ('preview' in p.name or p.name.startswith('variant_') and '_refined_' in p.name))
                diagnostic_render = (p.suffix == '.mp4' and parts[0] == 'diagnostics'
                                     and p.name.startswith('variant') and '_resolve_audit_' in p.name)
                if scratch_audio or preview or diagnostic_render:
                    targets.append(p)
    for folder in ('scripts', 'tests'):
        if (workspace / folder).is_dir():
            targets.extend(p for p in (workspace / folder).rglob('__pycache__')
                           if p.is_dir() and not p.is_symlink())
    if (workspace / '__pycache__').is_dir():
        targets.append(workspace / '__pycache__')
    return [str(p.relative_to(workspace)) for p in targets]

def load_protected_paths(root: Path, workspace: Path | None = None):
    protected = {root / x for x in PROTECTED_NAMES}
    states = {root / 'PROJECT_STATE.json', (workspace or root) / 'PROJECT_STATE.json'}
    project_catalog = root / '.cache' / 'projects'
    for name in ('PROJECT_TASKS.json', 'PROJECT.md', 'ACCEPTANCE.md', 'PLAN.md', 'STATUS.md', 'DECISIONS.md'):
        protected.update(project_catalog.glob('*/' + name))
    cache = (workspace or root) / '.cache'
    if cache.is_dir():
        states.update(cache.rglob('PROJECT_STATE.json'))
    for state in states:
        protected.add(state)
        if not state.exists():
            continue
        try:
            s = json.loads(state.read_text(encoding="utf-8"))
            vals = []
            vals.append(s.get("title_path"))
            vals.append((s.get('current_export') or {}).get('output_dir'))
            for field in ('subtitle_paths', 'video_paths'):
                entries = s.get(field) or []
                if not isinstance(entries, list):
                    raise ValueError(f'invalid_{field}')
                vals.extend(entries)
            for v in vals:
                if v:
                    protected.add(Path(v).expanduser().resolve())
        except Exception as exc:
            raise RuntimeError(f"Cannot establish cleanup protection from {state}") from exc
    return protected

def safe_target(root: Path, rel: str, protected):
    root = root.resolve()
    p = (root / rel).resolve()
    if root not in p.parents and p != root:
        raise RuntimeError(f"Unsafe target outside root: {p}")
    if any(p == q or p in q.parents or q in p.parents for q in (Path(item).resolve() for item in protected)):
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
    g.add_argument('--rebuildable', action='store_true', help='compiler caches and generated scratch media only')
    action = ap.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true", help="actually delete; default is dry-run")
    action.add_argument('--dry-run', action='store_true', help='show exact targets without deleting')
    args = ap.parse_args()

    root = Path(args.root).resolve()
    workspace = resolve_runtime_paths(root, args.workspace).workspace_root
    protected = load_protected_paths(root, workspace)
    rels = (rebuildable_targets(workspace) if args.rebuildable else
            LIGHT if args.light else CACHE if args.cache else REPORTS if args.reports else
            sorted(set(CACHE + REPORTS + ["exports/tmp"])))

    # Validate the complete plan before deleting any member of it.
    targets = [(rel, safe_target(workspace, rel, protected)) for rel in rels]

    print("APPLY" if args.apply else "DRY-RUN", "cleanup in", workspace)
    total = 0
    for rel, p in targets:
        if not p.exists():
            print("skip   ", rel, "(missing)")
            continue
        print("delete ", rel)
        total += sum(f.stat().st_size for f in p.rglob('*') if f.is_file() and not f.is_symlink()) if p.is_dir() else p.stat().st_size
        if args.apply:
            if p.is_dir(): shutil.rmtree(p)
            else: p.unlink()
            if not args.rebuildable and p.suffix == "": p.mkdir(parents=True, exist_ok=True)
    print('bytes:', total)
    if not args.apply:
        print("Nothing deleted. Re-run with --apply to execute.")

if __name__ == "__main__":
    main()
