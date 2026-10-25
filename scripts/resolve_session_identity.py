"""Confirm a Resolve project once per Resolve process lifetime."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from project_registry import atomic_json, digest
from runtime_meta import CacheIdentityError


def process_session():
    result = subprocess.run(['/usr/bin/pgrep', '-x', 'Resolve'], capture_output=True,
                            text=True, check=False, timeout=2)
    pids = [value.strip() for value in result.stdout.splitlines() if value.strip()]
    if result.returncode or len(pids) != 1:
        raise CacheIdentityError('resolve_process_missing_or_ambiguous')
    pid = pids[0]
    details = subprocess.run(['/bin/ps', '-p', pid, '-o', 'lstart='], capture_output=True,
                             text=True, check=True, timeout=2).stdout.strip()
    if not details:
        raise CacheIdentityError('resolve_process_start_time_unavailable')
    return {'pid': int(pid), 'started_at': details}


def session_record_path(paths):
    return paths.project_directory / '.cache' / 'resolve_identity_session.json'


def cached_project_key(paths):
    """Find the unique project bound to this already-confirmed Resolve process."""
    session = process_session()
    catalog = paths.repo_root / '.cache' / 'projects'
    matches = []
    if not catalog.is_dir():
        return None
    for config_path in catalog.glob('*/PROJECT_CONFIG.json'):
        try:
            config = json.loads(config_path.read_text(encoding='utf-8'))
            saved = json.loads((config_path.parent / '.cache' / 'resolve_identity_session.json')
                               .read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if (saved.get('process') == session
                and saved.get('project_key') == config.get('project_key')
                and saved.get('config_digest') == digest(config)
                and saved.get('project_id') == config.get('resolve_project_id')):
            matches.append(config)
    if len(matches) > 1:
        raise CacheIdentityError('resolve_cached_project_binding_ambiguous')
    return matches[0]['project_key'] if matches else None


def confirm_project(paths, project, *, allow_cached=True):
    """Return the registered ID; re-read Resolve's ID only for a new process session."""
    state = paths.load_state()
    expected = (state.get('project_identity') or {}).get('resolve_project_id')
    key = (state.get('project_identity') or {}).get('project_key')
    config_digest = (state.get('project_identity') or {}).get('config_digest')
    if not all(isinstance(value, str) and value for value in (expected, key, config_digest)):
        raise CacheIdentityError('registered_resolve_identity_missing')
    current_session = process_session()
    path = session_record_path(paths)
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        saved = None
    if (allow_cached and isinstance(saved, dict)
            and saved.get('process') == current_session
            and saved.get('project_key') == key
            and saved.get('config_digest') == config_digest
            and saved.get('project_id') == expected):
        if project.GetName() != state['resolve']['project_name']:
            raise CacheIdentityError('resolve_project_display_name_changed_within_session')
        return expected, True
    live = project.GetUniqueId()
    if not live or live != expected:
        raise CacheIdentityError('resolve_project_identity_mismatch')
    record = {'schema_version': 1, 'process': current_session, 'project_key': key,
              'config_digest': config_digest, 'project_id': live}
    atomic_json(path, record)
    return live, False
