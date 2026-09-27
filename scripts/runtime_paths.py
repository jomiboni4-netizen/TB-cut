"""Resolve repository code/config and batch runtime locations."""

from __future__ import annotations

import os
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
        return self.workspace_root / ".cache"

    @property
    def reports_dir(self) -> Path:
        return self.workspace_root / "reports"

    @property
    def plans_dir(self) -> Path:
        return self.workspace_root / "plans"

    @property
    def exports_dir(self) -> Path:
        return self.workspace_root / "exports"

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
    configured = workspace if workspace is not None else os.environ.get("TB_CUT_WORKSPACE")
    root = Path(configured).expanduser().resolve() if configured else repo
    return RuntimePaths(repo, root)
