"""Resolve repository code/config and batch runtime locations."""

from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path


class ArtifactPathError(ValueError):
    """A stored artifact reference cannot be used in this workspace."""


@dataclass(frozen=True)
class ArtifactReference:
    path: Path
    legacy_absolute: bool


@dataclass(frozen=True)
class RuntimePaths:
    repo_root: Path
    workspace_root: Path

    @property
    def state_path(self) -> Path:
        return self.workspace_root / "PROJECT_STATE.json"

    @property
    def cache_dir(self) -> Path:
        scope = self.registered_scope()
        return (self.workspace_root / '.cache' / 'projects' / scope[0] / 'batches' / scope[1]
                if scope else self.workspace_root / '.cache')

    def registered_scope(self):
        from project_registry import CONFIG_NAME, checked_project, digest, is_registered, ProjectIdentityError
        if not self.state_path.is_file():
            if any((self.workspace_root / name).exists() for name in (CONFIG_NAME, CONFIG_NAME + '.invalid')):
                raise ProjectIdentityError('project_state_missing_legacy_fallback_forbidden')
            return None
        state = json.loads(self.state_path.read_text())
        if not is_registered(state):
            if (self.workspace_root / CONFIG_NAME).exists():
                raise ProjectIdentityError('registered_identity_removed_legacy_fallback_forbidden')
            return None
        config = checked_project(self, state)
        return config['project_key'], digest({'batch': {k: state['batch'].get(k) for k in ('batch_id', 'product_ids')},
                                              'overrides': state.get('batch_overrides', {})})

    @property
    def upstream_cache_dir(self):
        scope = self.registered_scope()
        if not scope:
            return self.cache_dir
        from runtime_meta import upstream_fingerprint, upstream_versions, rules_fingerprint
        from project_registry import digest
        key = digest({'inputs': upstream_fingerprint(self), 'rules': rules_fingerprint(self),
                      'generators': upstream_versions(self)})
        return self.workspace_root / '.cache' / 'projects' / scope[0] / 'inputs' / key

    @property
    def project_records_dir(self):
        scope = self.registered_scope()
        if not scope:
            raise ArtifactPathError('registered_project_required')
        return self.workspace_root / '.cache' / 'projects' / scope[0] / 'records'

    @property
    def reports_dir(self) -> Path:
        return self.scoped_directory('reports')

    @property
    def plans_dir(self) -> Path:
        return self.scoped_directory('plans')

    @property
    def exports_dir(self) -> Path:
        return self.scoped_directory('exports')

    def scoped_directory(self, name):
        scope = self.registered_scope()
        return (self.workspace_root / name / scope[0] / scope[1]
                if scope else self.workspace_root / name)

    @property
    def backups_dir(self) -> Path:
        return self.cache_dir / "backups"

    def reference(self, artifact: Path) -> str:
        path = Path(artifact).resolve()
        try:
            return path.relative_to(self.workspace_root).as_posix()
        except ValueError as exc:
            raise ArtifactPathError(f"artifact_outside_workspace: {path}") from exc

    def runtime_argument(self, value: Path | str) -> Path:
        """Resolve a CLI runtime path; absolute paths keep their original meaning."""
        raw = Path(value).expanduser()
        return raw.resolve() if raw.is_absolute() else (self.workspace_root / raw).resolve()

    def resolve_reference(self, reference: str, *, must_exist: bool = True) -> ArtifactReference:
        raw = Path(reference)
        path = raw.resolve() if raw.is_absolute() else (self.workspace_root / raw).resolve()
        if path != self.workspace_root and self.workspace_root not in path.parents:
            raise ArtifactPathError(f"artifact_outside_workspace: {reference}")
        if must_exist and not path.is_file():
            raise ArtifactPathError(f"artifact_missing: {path}")
        return ArtifactReference(path, legacy_absolute=raw.is_absolute())


def resolve_runtime_paths(repo_root: Path | str, workspace: Path | str | None = None) -> RuntimePaths:
    repo = Path(repo_root).expanduser().resolve()
    environment = os.environ.get("TB_CUT_WORKSPACE")
    if workspace is not None and environment and Path(workspace).expanduser().resolve() != Path(environment).expanduser().resolve():
        raise ArtifactPathError("workspace_identity_conflict: --workspace differs from TB_CUT_WORKSPACE")
    if workspace is None or not str(workspace).strip():
        raise ArtifactPathError("explicit_workspace_required: pass --workspace; TB_CUT_WORKSPACE only checks conflicts")
    root = Path(workspace).expanduser().resolve()
    return RuntimePaths(repo, root)
