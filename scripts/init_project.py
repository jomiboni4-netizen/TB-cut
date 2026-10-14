#!/usr/bin/env python3
"""Register one confirmed offline Resolve identity; resume without re-entering paths."""
import argparse
import copy
import json
from pathlib import Path
import uuid
from runtime_paths import resolve_runtime_paths
from project_registry import (CONFIG_NAME, CONTRACT, ProjectIdentityError, atomic_json,
                              checked_project, digest, folder_identity, validate_config, workspace_lock)


def initialize(paths, *, material_folder=None, resolve_project_id=None, title=None,
               subtitles=None, videos=None, resolve_project=None, source_timeline=None,
               target_timeline=None, batch_id=None, products=None, force=False):
    with workspace_lock(paths):
        marker = paths.workspace_root / (CONFIG_NAME + '.invalid')
        if marker.exists():
            raise ProjectIdentityError('project_registration_incomplete_manual_recovery_required')
        config_path = paths.workspace_root / CONFIG_NAME
        existing_state = json.loads(paths.state_path.read_text()) if paths.state_path.exists() else None
        if existing_state is not None:
            config = checked_project(paths, existing_state, check_inputs=False)
        elif config_path.exists():
            raise ProjectIdentityError('registration_without_state_manual_recovery_required')
        else:
            config = None
        supplied = {'title_path': title, 'subtitle_paths': subtitles, 'video_paths': videos}
        resolve_values = {'project_name': resolve_project, 'source_timeline': source_timeline,
                          'target_timeline': target_timeline}
        if config is None:
            if not material_folder or not resolve_project_id:
                raise ProjectIdentityError('first_registration_requires_folder_and_confirmed_resolve_id')
            config = {'schema_version': CONTRACT, 'project_key': str(uuid.uuid4()),
                      'resolve_project_id': resolve_project_id,
                      'material_folder': folder_identity(material_folder), 'inputs': {}}
            state = json.loads((paths.repo_root / 'PROJECT_STATE.template.json').read_text())
        else:
            state = copy.deepcopy(existing_state)
            if resolve_project_id is not None and resolve_project_id != config['resolve_project_id']:
                raise ProjectIdentityError('resolve_identity_mismatch_use_separate_workspace')
            if material_folder and folder_identity(material_folder) != config['material_folder']:
                raise ProjectIdentityError('material_identity_mismatch_use_separate_workspace')
        new_config = copy.deepcopy(config)
        inputs = new_config['inputs']
        for key, value in supplied.items():
            if value is not None:
                inputs[key] = ([str(Path(p).expanduser().resolve()) for p in value]
                               if isinstance(value, list) else str(Path(value).expanduser().resolve()))
        resolve = inputs.setdefault('resolve', {})
        resolve.update({k: v for k, v in resolve_values.items() if v is not None})
        validate_config(new_config)
        changed = new_config != config
        if existing_state is not None and changed and not force:
            raise ProjectIdentityError('input_update_requires_explicit_force')
        state.update(copy.deepcopy(inputs))
        state['project_identity'] = {'contract': CONTRACT, 'project_key': new_config['project_key'],
                                     'resolve_project_id': new_config['resolve_project_id'],
                                     'config_digest': digest(new_config)}
        state['project_root'] = str(paths.repo_root)
        runtime = state.setdefault('runtime', {})
        runtime.setdefault('pipeline_version', '2.1')
        runtime.update({'identity_contract': CONTRACT, 'cache_semantics': 'project-resume-v2',
                        'resolve_write_enabled': False})
        batch = state.setdefault('batch', {})
        new_batch = batch_id if batch_id is not None else batch.get('batch_id')
        new_products = products if products is not None else batch.get('product_ids')
        if not isinstance(new_batch, str) or not new_batch.strip():
            raise ProjectIdentityError('batch_id_required')
        if (not isinstance(new_products, list) or not new_products
                or any(type(p) is not int or p <= 0 for p in new_products)
                or len(set(new_products)) != len(new_products)):
            raise ProjectIdentityError('explicit_unique_product_ids_required')
        if changed or new_batch != batch.get('batch_id') or new_products != batch.get('product_ids'):
            batch['completed_product_ids'] = []
            batch['failed_product_ids'] = []
            runtime['last_successful_stage'] = 'registered'
        batch.update({'batch_id': new_batch, 'product_ids': sorted(new_products)})
        if existing_state == state and new_config == config:
            return state
        # Registration is two renames. Any interrupted pair is blocked, never repaired by attestation.
        marker.write_text('registration incomplete; inspect both records before recovery\n')
        atomic_json(config_path, new_config)
        atomic_json(paths.state_path, state)
        if json.loads(config_path.read_text()) != new_config or json.loads(paths.state_path.read_text()) != state:
            raise ProjectIdentityError('registration_records_changed')
        marker.unlink()
        checked_project(paths, state)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.')
    parser.add_argument('--workspace')
    parser.add_argument('--material-folder')
    parser.add_argument('--resolve-project-id', help='Confirmed stable Resolve ID, never its display name')
    parser.add_argument('--title')
    parser.add_argument('--subtitles', nargs='*')
    parser.add_argument('--videos', nargs='*')
    parser.add_argument('--resolve-project')
    parser.add_argument('--source-timeline')
    parser.add_argument('--target-timeline')
    parser.add_argument('--batch-id')
    parser.add_argument('--products', help='Comma-separated product indexes')
    parser.add_argument('--force', action='store_true', help='Confirm changed inputs; cannot switch project identity')
    args = parser.parse_args()
    products = [int(p) for p in args.products.split(',')] if args.products is not None else None
    initialize(resolve_runtime_paths(args.root, args.workspace), material_folder=args.material_folder,
               resolve_project_id=args.resolve_project_id, title=args.title, subtitles=args.subtitles,
               videos=args.videos, resolve_project=args.resolve_project, source_timeline=args.source_timeline,
               target_timeline=args.target_timeline, batch_id=args.batch_id, products=products, force=args.force)
    print('项目登记已验证，配置可跨窗口继承')


if __name__ == '__main__':
    main()
