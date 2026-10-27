#!/usr/bin/env python3
"""Create an isolated batch workspace from a registered project record."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from project_registry import atomic_json, checked_project, project_record_lock
from runtime_paths import resolve_runtime_paths


_BATCH_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def create_batch_workspace(repo_root, project_workspace, batch_id, product_ids):
    root = Path(repo_root).resolve()
    project_paths = resolve_runtime_paths(root, project_workspace)
    if project_paths.project_ref_path.is_file():
        raise ValueError("project_workspace_must_be_bound_project_record")
    state = project_paths.load_state()
    config = checked_project(project_paths, state, check_inputs=True)
    identity = state.get("project_identity") or {}
    key = config.get("project_key")
    if (not key or identity.get("project_key") != key
            or identity.get("config_digest") is None):
        raise ValueError("registered_project_identity_incomplete")
    if not isinstance(batch_id, str) or not _BATCH_ID.fullmatch(batch_id):
        raise ValueError("batch_id_invalid")
    if (not isinstance(product_ids, list) or not product_ids or len(product_ids) > 3
            or any(type(value) is not int or value <= 0 for value in product_ids)
            or len(set(product_ids)) != len(product_ids)):
        raise ValueError("product_ids_must_be_one_to_three_unique_positive_integers")

    destination = project_paths.project_directory / "batches" / batch_id
    with project_record_lock(project_paths, key):
        if destination.exists():
            raise ValueError("batch_workspace_already_exists")
        destination.mkdir(parents=True)
        reference = {"schema_version": 1, "project_key": key,
                     "config_digest": identity["config_digest"]}
        batch_state = {
            "schema_version": 1, "project_key": key,
            "config_digest": identity["config_digest"],
            "batch": {"batch_id": batch_id, "product_ids": sorted(product_ids),
                      "completed_product_ids": [], "failed_product_ids": []},
            "batch_overrides": state.get("batch_overrides") or {
                "skip_blank_titles": True, "preserve_blank_row_indices": True},
        }
        try:
            atomic_json(destination / "PROJECT_REF.json", reference)
            atomic_json(destination / "BATCH_STATE.json", batch_state)
            batch_paths = resolve_runtime_paths(root, destination)
            loaded = batch_paths.load_state()
            if (loaded.get("batch", {}).get("batch_id") != batch_id
                    or loaded.get("batch", {}).get("product_ids") != sorted(product_ids)):
                raise ValueError("created_batch_workspace_readback_mismatch")
        except BaseException:
            for name in ("PROJECT_REF.json", "BATCH_STATE.json"):
                (destination / name).unlink(missing_ok=True)
            destination.rmdir()
            raise
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--project-workspace", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--products", required=True, help="Comma-separated product indices; maximum three")
    args = parser.parse_args()
    try:
        products = [int(value.strip()) for value in args.products.split(",") if value.strip()]
        workspace = create_batch_workspace(args.root, args.project_workspace,
                                           args.batch_id, products)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f"batch_workspace_created={workspace}")


if __name__ == "__main__":
    main()
