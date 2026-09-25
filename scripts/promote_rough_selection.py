#!/usr/bin/env python3
"""Promote a passed rough selection to the canonical approved paths."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import load_checked_json, write_metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", required=True)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--workspace")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    selection_path = paths.runtime_argument(args.selection)
    audit_path = paths.runtime_argument(args.audit)
    selection = load_checked_json(paths, selection_path)
    audit = load_checked_json(paths, audit_path)
    if audit.get("passed") is not True or audit.get("status", "PASS" if audit.get("passed") else "REPAIRABLE") != "PASS":
        raise SystemExit("粗选审计未通过，禁止提升")
    destination = selection_path.parent
    approved_selection = destination / "rough_selection.approved.json"
    approved_selection.write_text(
        json.dumps(selection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    approved_audit = destination / "rough_audit.approved.json"
    approved_audit.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_metadata(paths, approved_selection, "promote_rough_selection.py", product_id=selection.get("product_index"))
    write_metadata(paths, approved_audit, "promote_rough_selection.py", product_id=selection.get("product_index"))
    print(f"商品{selection.get('product_index')} 粗选已批准：{audit.get('variant_count')} 条")


if __name__ == "__main__":
    main()
