"""Resolve-name routing for the two user-defined editorial workflows."""
from __future__ import annotations

import json
from pathlib import Path


class EditingModeError(ValueError):
    pass


def available_modes() -> dict:
    path = Path(__file__).resolve().parents[1] / 'config' / 'project_editing_modes.json'
    body = json.loads(path.read_text(encoding='utf-8'))
    if body.get('schema_version') != 1 or not isinstance(body.get('modes'), dict):
        raise EditingModeError('project_editing_modes_config_invalid')
    return body['modes']


def resolve_editing_mode(project_name: str) -> tuple[str, dict]:
    if not isinstance(project_name, str) or not project_name.strip():
        raise EditingModeError('resolve_project_name_missing')
    matches = [(name, mode) for name, mode in available_modes().items()
               if isinstance(mode.get('resolve_name_keyword'), str)
               and mode['resolve_name_keyword'] in project_name]
    if len(matches) != 1:
        reason = 'resolve_project_mode_ambiguous' if matches else 'resolve_project_mode_unmatched'
        raise EditingModeError(reason)
    name, mode = matches[0]
    limits = mode.get('duration_exclusive_seconds')
    if (not isinstance(limits, list) or len(limits) != 2
            or not all(isinstance(value, (int, float)) for value in limits)
            or not 0 <= limits[0] < limits[1] <= 60
            or not 0 <= mode.get('closeup_duration_ratio_max_inclusive', -1) <= 1
            or type(mode.get('target_variant_count')) is not int or mode['target_variant_count'] < 1):
        raise EditingModeError('project_editing_mode_policy_invalid')
    return name, dict(mode)
