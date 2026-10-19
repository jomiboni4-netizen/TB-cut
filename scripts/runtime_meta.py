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
    return root if isinstance(root, RuntimePaths) else resolve_runtime_paths(root, root)


def _state(root: Path | RuntimePaths) -> dict:
    path = _paths(root).state_path
    if not path.is_file():
        raise CacheIdentityError(f"runtime_state_missing: {path}")
    state = json.loads(path.read_text(encoding="utf-8"))
    from project_registry import CONFIG_NAME, is_registered
    if (_paths(root).workspace_root / CONFIG_NAME).exists() and not is_registered(state):
        raise CacheIdentityError('registered_identity_removed_legacy_fallback_forbidden')
    if not state.get("batch", {}).get("batch_id"):
        raise CacheIdentityError("batch_id_missing: PROJECT_STATE.json")
    if not state.get("runtime", {}).get("pipeline_version"):
        raise CacheIdentityError("pipeline_version_missing: PROJECT_STATE.json")
    return state


def upstream_fingerprint(root, state=None):
    """Project inputs independent of the selected processing group."""
    import copy
    state = copy.deepcopy(state or _state(root))
    state.setdefault('batch', {}).pop('product_ids', None)
    return input_fingerprint(root, state)


def upstream_versions(paths):
    versions = {}
    for name in ('index_titles.py', 'index_subtitles.py'):
        source = paths.repo_root / 'scripts' / name
        if not source.is_file():
            source = Path(__file__).parent / name
        dependencies = [source, *[Path(__file__).parent / helper for helper in
                                 ('runtime_meta.py', 'runtime_paths.py', 'runtime_publication.py', 'project_registry.py')]]
        if name == 'index_titles.py':
            dependencies.extend(paths.repo_root / dep for dep in
                                ('scripts/inspect_title_workbook.mjs', 'scripts/check_workbook_dependencies.mjs',
                                 'package.json', 'workbook-dependencies.lock.json', 'config/workbook-runtime-v4.lock.json'))
        versions[name] = hashlib.sha256(''.join(_sha256(dep) if dep.is_file() else 'missing'
                                               for dep in dependencies).encode()).hexdigest()
    return versions


def input_fingerprint(root: Path | RuntimePaths, state: dict | None = None, *, subtitle_contents: dict | None = None) -> str:
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
    if subtitle_contents is not None and set(subtitle_contents) != set(subtitles):
        raise CacheIdentityError("subtitle_snapshot_file_set_changed")
    payload = {
        "title": [str(title), _sha256(title)],
        "subtitles": [[str(path), _sha256(path) if subtitle_contents is None else hashlib.sha256(subtitle_contents[path]).hexdigest()] for path in subtitles],
        "videos": [[str(path), path.stat().st_size, path.stat().st_mtime_ns] for path in videos],
        "resolve": state.get("resolve") or {},
        "batch_product_ids": (state.get("batch") or {}).get("product_ids") or [],
        "batch_policy": {
            "skip_blank_titles": (state.get("batch_overrides") or {}).get("skip_blank_titles", False),
            "preserve_blank_row_indices": (state.get("batch_overrides") or {}).get("preserve_blank_row_indices", False),
        },
    }
    if not isinstance(payload["batch_policy"]["skip_blank_titles"], bool):
        raise CacheIdentityError("skip_blank_titles_override_invalid")
    if not isinstance(payload["batch_policy"]["preserve_blank_row_indices"], bool):
        raise CacheIdentityError("preserve_blank_row_indices_override_invalid")
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


def expected_identity(root: Path | RuntimePaths, state: dict | None = None, *, include_ranges: bool = True) -> dict:
    paths = _paths(root)
    state = _state(paths) if state is None else state
    from project_registry import checked_project, is_registered, ProjectIdentityError
    registered = is_registered(state)
    if registered:
        try:
            config = checked_project(paths, state)
        except ProjectIdentityError as exc:
            raise CacheIdentityError(str(exc)) from exc
        if include_ranges:
            raise CacheIdentityError('registered_downstream_not_integrated: use checkpoint/upstream entry points; round 2 required')
    identity = {
        "schema_version": META_SCHEMA_VERSION,
        "pipeline_version": str(state["runtime"]["pipeline_version"]),
        "batch_id": str(state["batch"]["batch_id"]),
        "input_fingerprint": input_fingerprint(paths, state),
        "rules_fingerprint": rules_fingerprint(paths),
    }
    if registered:
        identity.update({'schema_version': 2, 'identity_contract': 'project-resume-v2',
                         'project_key': config['project_key'], 'config_digest': state['project_identity']['config_digest'],
                         'upstream_fingerprint': upstream_fingerprint(paths, state),
                         'upstream_versions': upstream_versions(paths)})
    if include_ranges:
        ranges = paths.cache_dir / "product_ranges.json"
        identity["range_snapshot"] = _sha256(ranges) if ranges.is_file() else None
    if (state.get("batch_overrides") or {}).get("preserve_blank_row_indices") is True:
        identity["preserve_blank_row_indices"] = True
    return identity


def metadata_path(path: Path) -> Path:
    return Path(str(path) + META_SUFFIX)


def make_metadata(root: Path, artifact: Path, producer: str, *, product_id: int | None = None, identity: dict | None = None) -> dict:
    artifact = Path(artifact)
    if not artifact.is_file():
        raise CacheIdentityError(f"artifact_missing: {artifact}")
    if not producer:
        raise CacheIdentityError("producer_missing")
    captured = identity or expected_identity(root)
    from project_registry import is_registered
    if is_registered(_state(root)) and captured.get('schema_version') != 2:
        raise CacheIdentityError('registered_identity_downgrade_forbidden')
    meta = {
        **{key: value for key, value in captured.items() if key != "range_snapshot"},
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "producer": producer,
        "artifact_sha256": _sha256(artifact),
    }
    if captured.get('schema_version') == 2:
        if producer not in ('index_titles.py', 'index_subtitles.py') or product_id is not None:
            raise CacheIdentityError('registered_producer_not_integrated')
        meta.update({'scope': 'project_input', 'stage_version': captured['upstream_versions'][producer]})
    if product_id is not None:
        meta["product_id"] = int(product_id)
    if range_dependent(artifact, meta):
        digest = checked_range_digest(root, captured)
        if captured.get("range_snapshot") != digest:
            raise CacheIdentityError("ranges_changed_during_generation")
        meta["range_dependency"] = {"version": 1, "sha256": digest}
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
    keys = ("schema_version", "pipeline_version", "batch_id", "input_fingerprint", "rules_fingerprint")
    if expected.get('schema_version') == 2:
        if (meta.get('scope') != 'project_input' or meta.get('producer') not in expected['upstream_versions']
                or meta.get('product_id') is not None or product_id is not None):
            raise CacheIdentityError('registered_artifact_scope_invalid')
        keys = ('schema_version', 'pipeline_version', 'identity_contract', 'project_key', 'config_digest',
                'upstream_fingerprint', 'rules_fingerprint')
        if meta.get('stage_version') != expected['upstream_versions'][meta['producer']]:
            raise CacheIdentityError('stage_version_mismatch')
    for key in keys:
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


def invalidation_path(artifact: Path) -> Path:
    return Path(str(artifact) + ".invalid")


# All current semantic/ASR/plan/export consumers are range dependent. Path and
# product identity checks also cover legacy producers and renamed generated files.
RANGE_DEPENDENT_PRODUCERS = frozenset({
    "build_expanded_codex_request.py", "mimo_review_v2.py", "audit_mimo_v2.py",
    "build_repair_request_v2.py", "merge_repair_result_v2.py", "drop_failed_variants_v2.py",
    "enforce_v2_request_rules.py", "promote_rough_selection.py", "windowed_asr_v2.py",
    "window_quality_v2.py", "refine_plans_v2.py", "approve_final_plans.py",
    "person_coverage_audit_v2.py", "collect_post_audit_failures.py", "resolve_write_v2.py",
    "validate_resolve_write_v2.py", "export_marked_variants.py", "verify_exports.py",
    "explicit_response_attestation",
})
RANGE_DEPENDENT_NAMES = frozenset({
    "person_coverage_audit.json", "resolve_write_report.json", "resolve_write_validation.json",
    "export_manifest.json", "export_verification.json",
})


def range_dependent(artifact, meta):
    artifact = Path(artifact)
    return ("range_dependency" in meta or meta.get("product_id") is not None
            or meta.get("producer") in RANGE_DEPENDENT_PRODUCERS
            or artifact.name in RANGE_DEPENDENT_NAMES
            or any(re.fullmatch(r"product_\d+_.+", part) for part in artifact.parts)
            or "plans" in artifact.parts or "asr_windows" in artifact.parts)


def checked_range_digest(root, expected):
    path = _paths(root).cache_dir / "product_ranges.json"
    # This is an upstream artifact, so checking it does not recurse downstream.
    _, content = _checked_bytes(root, path, expected=expected)
    return hashlib.sha256(content).hexdigest()


def _checked_bytes(root, artifact, *, product_id=None, expected=None):
    artifact = Path(artifact)
    from project_registry import is_registered
    if expected is not None and is_registered(_state(root)) and expected.get('schema_version') != 2:
        raise CacheIdentityError('registered_identity_downgrade_forbidden')
    if invalidation_path(artifact).exists():
        raise CacheIdentityError(f"publication_incomplete: {artifact}")
    if not artifact.is_file():
        raise CacheIdentityError(f"artifact_missing: {artifact}")
    meta = read_metadata(artifact)
    expected = expected or expected_identity(root, include_ranges=artifact.name not in ('titles.json', 'subtitle_index.json'))
    validate_metadata(meta, expected, product_id=product_id)
    content = artifact.read_bytes()
    if meta["artifact_sha256"] != hashlib.sha256(content).hexdigest():
        raise CacheIdentityError(f"artifact_sha256_mismatch: {artifact}")
    if invalidation_path(artifact).exists():
        raise CacheIdentityError(f"publication_incomplete: {artifact}")
    if artifact.resolve() == (_paths(root).cache_dir / "product_ranges.json").resolve() and range_dependent(artifact, meta):
        raise CacheIdentityError("range_dependency_cycle")
    if range_dependent(artifact, meta):
        dependency = meta.get("range_dependency")
        if not isinstance(dependency, dict) or dependency.get("version") != 1:
            raise CacheIdentityError("range_dependency_missing: regenerate from verified ranges")
        digest = checked_range_digest(root, expected)
        if dependency.get("sha256") != digest:
            raise CacheIdentityError("range_dependency_mismatch: regenerate from verified ranges")
        if "range_snapshot" in expected and expected["range_snapshot"] != digest:
            raise CacheIdentityError("ranges_changed_during_generation")
    if artifact.name == "titles.json":
        validate_titles(json.loads(content), meta)
    if artifact.name == "subtitle_index.json":
        value = json.loads(content)
        if (meta.get('producer') != 'index_subtitles.py' or not isinstance(value, dict)
                or value.get('generator') != 'index_subtitles.py:v2' or value.get('schema_version') != 1):
            raise CacheIdentityError('subtitle_generator_provenance_invalid')
    return meta, content


def validate_titles(value, meta):
    """Generator provenance/schema gate; not a signature against malicious edits."""
    if meta.get("producer") != "index_titles.py":
        raise CacheIdentityError("titles_producer_invalid")
    preserve_rows = meta.get("preserve_blank_row_indices") is True
    expected_generator = "index_titles.py:v3" if preserve_rows else "index_titles.py:v2"
    if not isinstance(value, dict) or value.get("schema_version") != 2 or value.get("generator") != expected_generator:
        raise CacheIdentityError("titles_schema_or_provenance_invalid")
    if any(not isinstance(value.get(key), str) or not value[key].strip() for key in ("source", "sheet")):
        raise CacheIdentityError("titles_source_or_sheet_invalid")
    products = value.get("products")
    if not isinstance(products, list) or not products:
        raise CacheIdentityError("titles_products_invalid")
    seen = set()
    previous_index = 0
    for row in products:
        index = row.get("product_index") if isinstance(row, dict) else None
        if type(index) is not int or index <= previous_index or (not preserve_rows and index != previous_index + 1):
            raise CacheIdentityError("titles_product_index_invalid")
        previous_index = index
        encoding, title = row.get("encoding_id"), row.get("title")
        if not isinstance(encoding, str) or not encoding or encoding.strip() != encoding or encoding in seen:
            raise CacheIdentityError("titles_encoding_id_invalid")
        if not isinstance(title, str) or not title.strip():
            raise CacheIdentityError("titles_title_invalid")
        seen.add(encoding)


def validate_cache(root: Path, artifact: Path, *, product_id: int | None = None, expected: dict | None = None) -> dict:
    return _checked_bytes(root, artifact, product_id=product_id, expected=expected)[0]


def load_checked_json(
    root: Path,
    artifact: Path,
    *,
    product_id: int | None = None,
    expected: dict | None = None,
    artifact_schema_version: int | None = None,
) -> dict:
    expected = expected or expected_identity(root, include_ranges=Path(artifact).name not in ('titles.json', 'subtitle_index.json'))
    _, content = _checked_bytes(root, artifact, product_id=product_id, expected=expected)
    value = json.loads(content)
    if not isinstance(value, dict):
        raise CacheIdentityError(f"artifact_invalid_type: {artifact}")
    if value.get("pipeline_version") is not None and str(value["pipeline_version"]) != expected["pipeline_version"]:
        raise CacheIdentityError(f"artifact_pipeline_version_mismatch: {artifact}")
    if artifact_schema_version is not None and value.get("schema_version") != artifact_schema_version:
        raise CacheIdentityError(f"artifact_schema_version_mismatch: {artifact}")
    return value
