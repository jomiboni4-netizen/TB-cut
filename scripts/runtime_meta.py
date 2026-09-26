#!/usr/bin/env python3
"""Identity checks for local runtime artifacts; no editing decisions live here."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from runtime_paths import RuntimePaths, resolve_runtime_paths


META_SCHEMA_VERSION = 1
META_SUFFIX = ".meta.json"

# Only these top-level sections are non-runtime policy. Unknown sections stay
# in the digest so adding a new hard rule fails closed by default.
NON_RUNTIME_RULE_SECTIONS = frozenset({
    "Working communication",
    "Git and public repository workflow",
    "Temporary public review report",
})


def runtime_rules_content(content: bytes) -> bytes:
    """Exclude named H2 workflow sections; preserve all other bytes."""
    lines = content.splitlines(keepends=True)
    retained = []
    paragraph = []
    excluded = False
    fence = None
    for index, line in enumerate(lines):
        text = line.decode("utf-8").rstrip("\r\n")
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", text)
        if fence is not None:
            paragraph = []
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
        elif marker:
            paragraph = []
            fence = marker[1]
        else:
            heading = re.match(r"^ {0,3}(#{1,2})[ \t]+(.+?)\s*$", text)
            if heading:
                excluded = heading[1] == "##" and heading[2] in NON_RUNTIME_RULE_SECTIONS
                paragraph = []
            elif paragraph and re.fullmatch(r" {0,3}(=+|-+)[ \t]*", text):
                # Only an unambiguous single-line H2 may start exclusion.
                # Unknown/multiline Setext headings retain the whole paragraph.
                title = lines[paragraph[0]].decode("utf-8").rstrip("\r\n")
                excluded = (
                    text.lstrip().startswith("-")
                    and len(paragraph) == 1
                    and re.fullmatch(r" {0,3}[^ \t].*", title) is not None
                    and title.strip() in NON_RUNTIME_RULE_SECTIONS
                )
                for previous in paragraph:
                    retained[previous] = not excluded
                paragraph = []
            elif text.strip():
                paragraph.append(index)
            else:
                paragraph = []
        retained.append(not excluded)
    if fence is not None:
        raise CacheIdentityError("rules_markdown_unclosed_fence")
    return b"".join(line for line, keep in zip(lines, retained) if keep)


class CacheIdentityError(ValueError):
    """A runtime file cannot be trusted for the configured batch."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _files(paths: list[str], suffix: str) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            files.extend(item for item in path.glob(f"*{suffix}") if item.is_file())
        elif path.is_file() and path.suffix.lower() == suffix:
            files.append(path)
        else:
            raise CacheIdentityError(f"input_missing_or_wrong_type: {path}")
    if not files:
        raise CacheIdentityError(f"input_missing: no {suffix} files configured")
    return sorted(set(files), key=str)


def _paths(root: Path | RuntimePaths) -> RuntimePaths:
    return root if isinstance(root, RuntimePaths) else resolve_runtime_paths(root)


def _state(root: Path | RuntimePaths) -> dict:
    path = _paths(root).state_path
    if not path.is_file():
        raise CacheIdentityError(f"runtime_state_missing: {path}")
    state = json.loads(path.read_text(encoding="utf-8"))
    if not state.get("batch", {}).get("batch_id"):
        raise CacheIdentityError("batch_id_missing: PROJECT_STATE.json")
    if not state.get("runtime", {}).get("pipeline_version"):
        raise CacheIdentityError("pipeline_version_missing: PROJECT_STATE.json")
    return state


def input_fingerprint(root: Path | RuntimePaths, state: dict | None = None) -> str:
    """Hash title/SRT content and video identity without reading large video bytes."""
    state = state or _state(root)
    title_raw = state.get("title_path")
    if not title_raw:
        raise CacheIdentityError("title_path_missing: PROJECT_STATE.json")
    title = Path(title_raw).expanduser().resolve()
    if not title.is_file():
        raise CacheIdentityError(f"title_input_missing: {title}")
    subtitles = _files(state.get("subtitle_paths") or [], ".srt")
    videos = _files(state.get("video_paths") or [], ".mp4")
    payload = {
        "title": [str(title), _sha256(title)],
        "subtitles": [[str(path), _sha256(path)] for path in subtitles],
        "videos": [[str(path), path.stat().st_size, path.stat().st_mtime_ns] for path in videos],
        "resolve": state.get("resolve") or {},
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def rules_fingerprint(root: Path | RuntimePaths) -> str:
    root = _paths(root).repo_root
    paths = [root / "PROJECT_RULES.md", *sorted((root / "config").glob("*.json"))]
    if not paths or any(not path.is_file() for path in paths):
        raise CacheIdentityError("rules_input_missing: PROJECT_RULES.md or config JSON")
    digest = hashlib.sha256(b"runtime-rules-v2\0")
    for path in paths:
        digest.update(str(path.relative_to(root)).encode())
        content = path.read_bytes()
        if path.name == "PROJECT_RULES.md":
            content = runtime_rules_content(content)
        digest.update(hashlib.sha256(content).hexdigest().encode())
    return digest.hexdigest()


def expected_identity(root: Path | RuntimePaths) -> dict:
    paths = _paths(root)
    state = _state(paths)
    return {
        "schema_version": META_SCHEMA_VERSION,
        "pipeline_version": str(state["runtime"]["pipeline_version"]),
        "batch_id": str(state["batch"]["batch_id"]),
        "input_fingerprint": input_fingerprint(paths, state),
        "rules_fingerprint": rules_fingerprint(paths),
    }


def metadata_path(path: Path) -> Path:
    return Path(str(path) + META_SUFFIX)


def make_metadata(root: Path, artifact: Path, producer: str, *, product_id: int | None = None, identity: dict | None = None) -> dict:
    artifact = Path(artifact)
    if not artifact.is_file():
        raise CacheIdentityError(f"artifact_missing: {artifact}")
    if not producer:
        raise CacheIdentityError("producer_missing")
    meta = {
        **(identity or expected_identity(root)),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "producer": producer,
        "artifact_sha256": _sha256(artifact),
    }
    if product_id is not None:
        meta["product_id"] = int(product_id)
    return meta


def write_metadata(root: Path, artifact: Path, producer: str, *, product_id: int | None = None, identity: dict | None = None) -> Path:
    artifact = Path(artifact)
    sidecar = metadata_path(artifact)
    meta = make_metadata(root, artifact, producer, product_id=product_id, identity=identity)
    temporary = Path(str(sidecar) + ".tmp")
    temporary.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(sidecar)
    return sidecar


def read_metadata(artifact: Path) -> dict:
    sidecar = metadata_path(artifact)
    if not sidecar.is_file():
        raise CacheIdentityError(f"legacy_cache_missing_metadata: {artifact}; refresh from validated inputs")
    try:
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CacheIdentityError(f"metadata_unreadable: {sidecar}: {exc}") from exc
    if not isinstance(meta, dict):
        raise CacheIdentityError(f"metadata_invalid_type: {sidecar}")
    return meta


def validate_metadata(meta: dict, expected: dict, *, product_id: int | None = None) -> None:
    for key in ("schema_version", "pipeline_version", "batch_id", "input_fingerprint", "rules_fingerprint"):
        if meta.get(key) != expected[key]:
            raise CacheIdentityError(f"{key}_mismatch: cached={meta.get(key)!r}, current={expected[key]!r}")
    if not meta.get("producer") or not meta.get("created_at") or not meta.get("artifact_sha256"):
        raise CacheIdentityError("metadata_required_field_missing: producer/created_at/artifact_sha256")
    if product_id is not None and meta.get("product_id") != int(product_id):
        raise CacheIdentityError(f"product_id_mismatch: cached={meta.get('product_id')!r}, current={product_id!r}")


def find_root(artifact: Path) -> Path:
    for parent in (Path(artifact).resolve().parent, *Path(artifact).resolve().parents):
        if (parent / "PROJECT_STATE.json").is_file():
            return parent
    raise CacheIdentityError(f"runtime_root_missing_for: {artifact}")


def validate_cache(root: Path, artifact: Path, *, product_id: int | None = None, expected: dict | None = None) -> dict:
    artifact = Path(artifact)
    if not artifact.is_file():
        raise CacheIdentityError(f"artifact_missing: {artifact}")
    meta = read_metadata(artifact)
    validate_metadata(meta, expected or expected_identity(root), product_id=product_id)
    if meta["artifact_sha256"] != _sha256(artifact):
        raise CacheIdentityError(f"artifact_sha256_mismatch: {artifact}")
    return meta


def load_checked_json(
    root: Path,
    artifact: Path,
    *,
    product_id: int | None = None,
    expected: dict | None = None,
    artifact_schema_version: int | None = None,
) -> dict:
    expected = expected or expected_identity(root)
    validate_cache(root, artifact, product_id=product_id, expected=expected)
    value = json.loads(Path(artifact).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CacheIdentityError(f"artifact_invalid_type: {artifact}")
    if value.get("pipeline_version") is not None and str(value["pipeline_version"]) != expected["pipeline_version"]:
        raise CacheIdentityError(f"artifact_pipeline_version_mismatch: {artifact}")
    if artifact_schema_version is not None and value.get("schema_version") != artifact_schema_version:
        raise CacheIdentityError(f"artifact_schema_version_mismatch: {artifact}")
    return value
