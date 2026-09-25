#!/usr/bin/env python3
import argparse, hashlib, json
from pathlib import Path
from runtime_paths import resolve_runtime_paths

def sha256(path: Path, chunk=1024*1024):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b: break
            h.update(b)
    return h.hexdigest()

def main():
    ap = argparse.ArgumentParser(description="Fingerprint configured title/subtitle/video inputs")
    ap.add_argument("--root", default=".")
    ap.add_argument("--workspace")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    state = json.loads((paths.state_path).read_text(encoding="utf-8"))
    input_paths = []
    if state.get("title_path"): input_paths.append(state["title_path"])
    input_paths += state.get("subtitle_paths") or []
    input_paths += state.get("video_paths") or []
    out = {}
    for raw in input_paths:
        p = Path(raw).expanduser()
        out[str(p)] = {"exists": p.exists(), "sha256": sha256(p) if p.exists() and p.is_file() else None}
    dest = paths.cache_dir / "input_fingerprints.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(dest)

if __name__ == "__main__":
    main()
