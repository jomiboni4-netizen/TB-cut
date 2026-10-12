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
    path = Path(path).expanduser().resolve()
    if not path.is_dir():
        raise ProjectIdentityError('material_folder_missing')
    stat = path.stat()
    return {'path': str(path), 'device': stat.st_dev, 'inode': stat.st_ino}


def validate_config(config, *, check_inputs=True):
    if config.get('schema_version') != CONTRACT:
        raise ProjectIdentityError('project_config_schema_invalid')
    try:
        uuid.UUID(config['project_key'])
        if not isinstance(config['resolve_project_id'], str) or not config['resolve_project_id'].strip():
            raise ValueError()
        if folder_identity(config['material_folder']['path']) != config['material_folder']:
            raise ProjectIdentityError('material_folder_identity_mismatch')
        inputs = config['inputs']
        if not inputs.get('title_path') or check_inputs and not Path(inputs['title_path']).is_file():
            raise ProjectIdentityError('registered_title_missing')
        for key in ('subtitle_paths', 'video_paths'):
            if not inputs[key] or check_inputs and any(not Path(p).exists() for p in inputs[key]):
                raise ProjectIdentityError('registered_' + key + '_missing')
        if any(not inputs['resolve'].get(k) for k in ('project_name', 'source_timeline', 'target_timeline')):
            raise ProjectIdentityError('registered_resolve_fields_missing')
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ProjectIdentityError):
            raise
        raise ProjectIdentityError('project_config_invalid') from exc


def checked_project(paths, state, *, check_inputs=True):
    config_path = paths.workspace_root / CONFIG_NAME
    if (paths.workspace_root / (CONFIG_NAME + '.invalid')).exists():
        raise ProjectIdentityError('project_registration_incomplete')
    if not config_path.is_file():
        raise ProjectIdentityError('project_registration_missing')
    config = json.loads(config_path.read_text())
    validate_config(config, check_inputs=check_inputs)
    identity = state.get('project_identity') or {}
    if identity != {'contract': CONTRACT, 'project_key': config['project_key'],
                    'resolve_project_id': config['resolve_project_id'], 'config_digest': digest(config)}:
        raise ProjectIdentityError('project_binding_mismatch')
    if any(state.get(k) != v for k, v in config['inputs'].items()):
        raise ProjectIdentityError('registered_inputs_changed_without_confirmation')
    return config


def is_registered(state):
    return 'project_identity' in state or state.get('runtime', {}).get('identity_contract') == CONTRACT
