#!/usr/bin/env python3
"""Publish user-authorized semantic ranges into registered product checkpoints."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime_paths import resolve_runtime_paths
from registered_pipeline import publish_semantic_ranges


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    paths = resolve_runtime_paths(Path(args.root).resolve(), args.workspace)
    manifest_path = paths.runtime_argument(args.manifest)
    if not manifest_path.is_file():
        raise SystemExit("semantic_range_manifest_missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    references = publish_semantic_ranges(paths, manifest)
    print(json.dumps({"status": "PUBLISHED", "range_authority": "user_directed_semantic_inference",
                      "product_ids": sorted(references)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
