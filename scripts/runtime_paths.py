"""Resolve repository code/config and batch runtime locations."""

from __future__ import annotations

import os
import json
import uuid
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
        return (self.workspace_root / 'BATCH_STATE.json' if self.project_ref_path.is_file()
                else self.workspace_root / 'PROJECT_STATE.json')

    @property
    def project_ref_path(self) -> Path:
        return self.workspace_root / 'PROJECT_REF.json'

    @property
    def project_key(self):
        if self.project_ref_path.is_file():
            reference = json.loads(self.project_ref_path.read_text(encoding='utf-8'))
            return reference.get('project_key')
        if self.state_path.is_file():
            state = json.loads(self.state_path.read_text(encoding='utf-8'))
            return (state.get('project_identity') or {}).get('project_key')
        return None

    @property
    def project_directory(self) -> Path:
        key = self.project_key
        if not key:
            raise ArtifactPathError('registered_project_required')
        try:
            uuid.UUID(key)
            project = (self.repo_root / '.cache' / 'projects' / key).resolve()
            project.relative_to((self.repo_root / '.cache' / 'projects').resolve())
        except (ValueError, OSError) as exc:
            raise ArtifactPathError('project_reference_invalid') from exc
        return project

    def load_state(self) -> dict:
        """Combine authoritative project settings with this workspace's batch state."""
        if not self.project_ref_path.is_file():
            if not self.state_path.is_file():
                raise ArtifactPathError('workspace_state_missing')
            return json.loads(self.state_path.read_text(encoding='utf-8'))
        reference = json.loads(self.project_ref_path.read_text(encoding='utf-8'))
        if reference.get('schema_version') != 1:
            raise ArtifactPathError('project_reference_schema_invalid')
        project_path = self.project_directory / 'PROJECT_STATE.json'
        if not project_path.is_file() or not self.state_path.is_file():
            raise ArtifactPathError('registered_project_or_batch_state_missing')
        if ((self.project_directory / 'PROJECT_STATE.json.invalid').exists()
                or (self.project_directory / 'PROJECT_CONFIG.json.invalid').exists()):
            raise ArtifactPathError('project_state_update_incomplete')
        from project_registry import digest, validate_config
        config_path = self.project_directory / 'PROJECT_CONFIG.json'
        if not config_path.is_file():
            raise ArtifactPathError('project_config_missing')
        config = json.loads(config_path.read_text(encoding='utf-8'))
        validate_config(config)
        if digest(config) != reference.get('config_digest'):
            raise ArtifactPathError('project_config_digest_mismatch')
        project = json.loads(project_path.read_text(encoding='utf-8'))
        batch = json.loads(self.state_path.read_text(encoding='utf-8'))
        if (reference.get('project_key') != project.get('project_identity', {}).get('project_key')
                or reference.get('config_digest') != project.get('project_identity', {}).get('config_digest')
                or batch.get('project_key') != reference.get('project_key')
                or batch.get('config_digest') != reference.get('config_digest')
                or batch.get('schema_version') != 1):
            raise ArtifactPathError('project_or_batch_reference_mismatch')
        if any(project.get(key) != value for key, value in config.get('inputs', {}).items()):
            raise ArtifactPathError('project_state_config_mismatch')
        if batch.get('batch', {}).get('batch_id') is None:
            raise ArtifactPathError('batch_id_missing')
        state = dict(project)
        state['batch'] = batch.get('batch', {})
        if 'batch_overrides' in batch:
            state['batch_overrides'] = batch['batch_overrides']
        if 'trial' in batch:
            state['trial'] = batch['trial']
        if batch.get('runtime'):
            state['runtime'] = {**state.get('runtime', {}), **batch['runtime']}
        return state

    def state_snapshot_bytes(self) -> bytes:
        if not self.project_ref_path.is_file():
            return self.state_path.read_bytes()
        return json.dumps(self.load_state(), ensure_ascii=False, sort_keys=True,
                          separators=(',', ':')).encode('utf-8')

    def write_state(self, state: dict, *, before_commit=lambda: None) -> None:
        """Persist mutable project metadata and batch progress in their own records."""
        if not self.project_ref_path.is_file():
            from project_registry import atomic_json
            atomic_json(self.state_path, state, before_commit=before_commit)
            return
        from project_registry import atomic_json, project_record_lock
        current = self.load_state()
        identity = current.get('project_identity') or {}
        if state.get('project_identity') != identity:
            raise ArtifactPathError('project_identity_change_requires_reregistration')
        project = {key: value for key, value in state.items()
                   if key not in {'batch', 'batch_overrides', 'trial'}}
        batch_runtime_keys = {'last_successful_stage', 'checkpoint_revision', 'last_error',
                              'completed', 'written', 'export_verified'}
        project_runtime = dict(project.get('runtime', {}))
        batch_runtime = {key: value for key, value in project_runtime.items()
                         if key in batch_runtime_keys}
        project['runtime'] = {key: value for key, value in project_runtime.items()
                              if key not in batch_runtime_keys}
        batch = {'schema_version': 1, 'project_key': identity['project_key'],
                 'config_digest': identity['config_digest'],
                 'batch': state.get('batch', {}),
                 'batch_overrides': state.get('batch_overrides', {})}
        if 'trial' in state:
            batch['trial'] = state['trial']
        if batch_runtime:
            batch['runtime'] = batch_runtime
        project_path = self.project_directory / 'PROJECT_STATE.json'
        marker = self.project_directory / 'PROJECT_STATE.json.invalid'
        with project_record_lock(self, identity['project_key']):
            before_commit()
            marker.write_text('state update incomplete; inspect project and batch records\n', encoding='utf-8')
            try:
                atomic_json(project_path, project)
                atomic_json(self.state_path, batch)
                marker.unlink()
            except BaseException:
                # Keep the marker on all failures. Readers fail closed until explicit recovery.
                raise

    @property
    def cache_dir(self) -> Path:
        scope = self.registered_scope()
        return (self.project_directory / 'batches' / scope[1]
                if scope else self.workspace_root / '.cache')

    def registered_scope(self):
        from project_registry import CONFIG_NAME, checked_project, digest, is_registered, ProjectIdentityError
        if self.project_ref_path.is_file():
            reference = json.loads(self.project_ref_path.read_text(encoding='utf-8'))
            key = reference.get('project_key')
            if not key:
                raise ProjectIdentityError('project_reference_invalid')
            try:
                state = self.load_state()
            except (OSError, ValueError, ArtifactPathError) as exc:
                raise ProjectIdentityError(str(exc)) from exc
            config = checked_project(self, state, check_inputs=True)
            from project_registry import digest
            if reference.get('config_digest') != digest(config):
                raise ProjectIdentityError('project_reference_stale_reinitialize')
            batch = state.get('batch', {})
            return key, digest({'batch': {k: batch.get(k) for k in ('batch_id', 'product_ids')},
                                'overrides': state.get('batch_overrides', {})})
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
        return self.repo_root / '.cache' / 'projects' / scope[0] / 'inputs' / key

    @property
    def project_records_dir(self):
        scope = self.registered_scope()
        if not scope:
            raise ArtifactPathError('registered_project_required')
        return self.repo_root / '.cache' / 'projects' / scope[0] / 'records'

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
            try:
                return '@project/' + path.relative_to(self.project_directory).as_posix()
            except (ArtifactPathError, ValueError):
                raise ArtifactPathError(f"artifact_outside_workspace: {path}") from exc

    def runtime_argument(self, value: Path | str) -> Path:
        """Resolve a CLI runtime path; absolute paths keep their original meaning."""
        raw = Path(value).expanduser()
        return raw.resolve() if raw.is_absolute() else (self.workspace_root / raw).resolve()

    def resolve_reference(self, reference: str, *, must_exist: bool = True) -> ArtifactReference:
        if reference.startswith('@project/'):
            raw_relative = Path(reference[len('@project/'):])
            if raw_relative.is_absolute() or '..' in raw_relative.parts:
                raise ArtifactPathError(f"artifact_outside_project: {reference}")
            path = (self.project_directory / raw_relative).resolve()
            try:
                path.relative_to(self.project_directory.resolve())
            except ValueError as exc:
                raise ArtifactPathError(f"artifact_outside_project: {reference}") from exc
            if must_exist and not path.is_file():
                raise ArtifactPathError(f"artifact_missing: {reference}")
            return ArtifactReference(path, legacy_absolute=False)
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
