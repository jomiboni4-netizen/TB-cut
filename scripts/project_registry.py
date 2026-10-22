"""Offline project registration and fail-closed atomic local records."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

CONTRACT = 2
CONFIG_NAME = 'PROJECT_CONFIG.json'


class ProjectIdentityError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@contextmanager
def workspace_lock(paths):
    paths.workspace_root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(paths.workspace_root, os.O_RDONLY)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProjectIdentityError('workspace_busy') from exc
        yield
    finally:
        os.close(descriptor)


def atomic_json(path, value, *, before_commit=lambda: None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.exists() and not path.is_file():
        raise ProjectIdentityError('record_must_be_regular_file')
    descriptor, temporary = tempfile.mkstemp(prefix='.record-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        before_commit()
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def folder_identity(path):
    """Return the registered location; filesystem inode is not project identity."""
    path = Path(path).expanduser().resolve()
    if not path.is_dir():
        raise ProjectIdentityError('material_folder_missing')
    return {'path': str(path)}


def material_folder_matches(record, path):
    """Compare configured locations without treating filesystem replacement as identity."""
    try:
        return (Path(record['path']).expanduser().resolve()
                == Path(path).expanduser().resolve())
    except (KeyError, TypeError, OSError):
        return False


def validate_config(config, *, check_inputs=True):
    if config.get('schema_version') != CONTRACT:
        raise ProjectIdentityError('project_config_schema_invalid')
    try:
        uuid.UUID(config['project_key'])
        if not isinstance(config['resolve_project_id'], str) or not config['resolve_project_id'].strip():
            raise ValueError()
        material_folder = config['material_folder']
        if (not isinstance(material_folder, dict) or not material_folder.get('path')
                or not Path(material_folder['path']).expanduser().is_dir()):
            raise ProjectIdentityError('material_folder_missing')
        inputs = config['inputs']
        if not inputs.get('title_path') or check_inputs and not Path(inputs['title_path']).is_file():
            raise ProjectIdentityError('registered_title_missing')
        for key in ('subtitle_paths', 'video_paths'):
            if not inputs[key] or check_inputs and any(not Path(p).exists() for p in inputs[key]):
                raise ProjectIdentityError('registered_' + key + '_missing')
        if any(not inputs['resolve'].get(k) for k in ('project_name', 'source_timeline', 'target_timeline')):
            raise ProjectIdentityError('registered_resolve_fields_missing')
        source_id = inputs['resolve'].get('source_timeline_id')
        target_id = inputs['resolve'].get('target_timeline_id')
        if ((source_id is None) != (target_id is None) or
                source_id is not None and (not isinstance(source_id, str) or not source_id.strip() or
                                           not isinstance(target_id, str) or not target_id.strip() or
                                           source_id == target_id)):
            raise ProjectIdentityError('registered_timeline_ids_invalid')
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ProjectIdentityError):
            raise
        raise ProjectIdentityError('project_config_invalid') from exc


def project_directory(paths, project_key):
    """Return the sole persistent registration directory for a Resolve project."""
    try:
        uuid.UUID(project_key)
    except (TypeError, ValueError) as exc:
        raise ProjectIdentityError('project_key_invalid') from exc
    return paths.repo_root / '.cache' / 'projects' / project_key


def project_config_path(paths, project_key):
    return project_directory(paths, project_key) / CONFIG_NAME


def project_state_path(paths, project_key):
    return project_directory(paths, project_key) / 'PROJECT_STATE.json'


@contextmanager
def project_record_lock(paths, project_key):
    directory = project_directory(paths, project_key)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / '.registration.lock'
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProjectIdentityError('project_registration_busy') from exc
        yield
    finally:
        os.close(descriptor)


def find_project_configs(paths, resolve_project_id):
    catalog = paths.repo_root / '.cache' / 'projects'
    found = []
    if catalog.is_dir():
        for candidate in catalog.glob('*/PROJECT_CONFIG.json'):
            try:
                config = json.loads(candidate.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if config.get('resolve_project_id') == resolve_project_id:
                found.append(config)
    return found


def _project_key_for(resolve_project_id, material=None):
    """Derive the sole project key from the stable Resolve project ID."""
    if not isinstance(resolve_project_id, str) or not resolve_project_id.strip():
        raise ProjectIdentityError('resolve_project_id_missing')
    if material is not None:
        folder_identity(material)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'tb-cut-v2:{resolve_project_id}'))


def checked_project(paths, state, *, check_inputs=True):
    identity = state.get('project_identity') or {}
    project_key = identity.get('project_key')
    if not project_key:
        raise ProjectIdentityError('project_binding_missing')
    directory = project_directory(paths, project_key)
    config_path = directory / CONFIG_NAME
    state_path = directory / 'PROJECT_STATE.json'
    if ((directory / (CONFIG_NAME + '.invalid')).exists()
            or (directory / 'PROJECT_STATE.json.invalid').exists()):
        raise ProjectIdentityError('project_registration_incomplete')
    if not config_path.is_file() or not state_path.is_file():
        raise ProjectIdentityError('project_registration_missing')
    config = json.loads(config_path.read_text(encoding='utf-8'))
    registered_state = json.loads(state_path.read_text(encoding='utf-8'))
    validate_config(config, check_inputs=check_inputs)
    expected_identity = {'contract': CONTRACT, 'project_key': config['project_key'],
                         'resolve_project_id': config['resolve_project_id'], 'config_digest': digest(config)}
    if identity != expected_identity or registered_state.get('project_identity') != expected_identity:
        raise ProjectIdentityError('project_binding_mismatch')
    if any(registered_state.get(k) != v for k, v in config['inputs'].items()):
        raise ProjectIdentityError('registered_inputs_changed_without_confirmation')
    # Workspace snapshots are compatibility views only. The project-scoped
    # record is authoritative and stale snapshots fail closed.
    if any(state.get(k) != v for k, v in config['inputs'].items()):
        raise ProjectIdentityError('workspace_project_snapshot_stale_reinitialize')
    return config


def read_project_state(paths, project_key):
    path = project_state_path(paths, project_key)
    if not path.is_file():
        raise ProjectIdentityError('project_state_missing')
    return json.loads(path.read_text(encoding='utf-8'))


def is_registered(state):
    return 'project_identity' in state or state.get('runtime', {}).get('identity_contract') == CONTRACT
