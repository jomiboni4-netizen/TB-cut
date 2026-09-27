#!/usr/bin/env python3
"""Explicitly attest reviewed legacy inputs or matching MiMo responses."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import CacheIdentityError, expected_identity, load_checked_json, metadata_path, write_metadata


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect or explicitly attest runtime cache identity")
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("identity")
    for name in ("attest-input", "attest-response"):
        command = commands.add_parser(name)
        command.add_argument("--artifact", required=True)
        command.add_argument("--artifact-sha256", required=True)
        command.add_argument("--batch-id", required=True)
        command.add_argument("--input-fingerprint", required=True)
        command.add_argument("--product-id", type=int, required=name == "attest-response")
        if name == "attest-response":
            command.add_argument("--request", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    identity = expected_identity(paths)
    if args.command == "identity":
        print(json.dumps(identity, ensure_ascii=False, indent=2))
        return

    artifact = paths.runtime_argument(args.artifact)
    if metadata_path(artifact).exists():
        raise CacheIdentityError(f"metadata_already_exists: {artifact}")
    if args.batch_id != identity["batch_id"] or args.input_fingerprint != identity["input_fingerprint"]:
        raise CacheIdentityError("attestation_identity_mismatch: current state differs")
    if not artifact.is_file() or sha256(artifact) != args.artifact_sha256:
        raise CacheIdentityError("attestation_artifact_sha256_mismatch")

    if args.command == "attest-response":
        request_path = paths.runtime_argument(args.request)
        if artifact.parent != request_path.parent or artifact.parent.parent != paths.cache_dir:
            raise CacheIdentityError("response_location_mismatch")
        if not re.fullmatch(r"product_\d+_rough", artifact.parent.name) or not artifact.name.startswith("mimo_"):
            raise CacheIdentityError("response_attestation_not_allowed")
        folder_id = int(artifact.parent.name.split("_")[1])
        if args.product_id != folder_id:
            raise CacheIdentityError("response_product_id_mismatch")
        request = load_checked_json(paths, request_path, product_id=folder_id, expected=identity)
        if request.get("product_index") != folder_id:
            raise CacheIdentityError("request_product_id_mismatch")
        transport_meta_path = artifact.with_name(artifact.stem + "_meta.json")
        transport_meta = json.loads(transport_meta_path.read_text(encoding="utf-8"))
        if transport_meta.get("status") != "completed" or transport_meta.get("request_digest") != sha256(request_path):
            raise CacheIdentityError("response_request_digest_mismatch")
        result = json.loads(artifact.read_text(encoding="utf-8"))
        if not isinstance(result, dict) or not ("output" in result or "output_text" in result):
            raise CacheIdentityError("response_format_invalid")
        write_metadata(paths, artifact, "explicit_response_attestation", product_id=folder_id, identity=identity)
        print(f"attested: {artifact}")
        return

    override_artifact = artifact.parent == paths.cache_dir and artifact.name.endswith("range_overrides.json")
    if not override_artifact and not (
        artifact.parent.parent == paths.cache_dir
        and re.fullmatch(r"product_\d+_rough", artifact.parent.name)
        and artifact.name == "mimo_request.json"
    ):
        raise CacheIdentityError("attestation_not_allowed: only range overrides or base mimo_request.json")
    document = json.loads(artifact.read_text(encoding="utf-8"))
    if override_artifact:
        if args.product_id is not None or not isinstance(document.get("products"), list):
            raise CacheIdentityError("products_attestation_invalid")
    else:
        folder_id = int(artifact.parent.name.split("_")[1])
        if args.product_id != folder_id or document.get("product_index") != folder_id:
            raise CacheIdentityError("request_product_id_mismatch")
        if str(document.get("pipeline_version")) != identity["pipeline_version"] or not isinstance(document.get("candidates"), list):
            raise CacheIdentityError("request_pipeline_or_candidates_invalid")
    write_metadata(paths, artifact, "explicit_human_attestation", product_id=args.product_id, identity=identity)
    print(f"attested: {artifact}")


if __name__ == "__main__":
    main()
