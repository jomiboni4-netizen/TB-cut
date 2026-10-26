#!/usr/bin/env python3
"""Register one confirmed offline Resolve identity; resume without re-entering paths."""
import argparse
import copy
import json
from pathlib import Path
from runtime_paths import resolve_runtime_paths
from project_registry import (CONFIG_NAME, CONTRACT, ProjectIdentityError, atomic_json,
                              checked_project, digest, folder_identity, material_folder_matches, project_directory,
                              find_project_configs, project_state_path, read_project_state, validate_config,
                              project_record_lock, workspace_lock, _project_key_for)


def initialize(paths, *, material_folder=None, resolve_project_id=None, title=None,
               subtitles=None, videos=None, resolve_project=None, source_timeline=None,
               target_timeline=None, source_timeline_id=None, target_timeline_id=None,
               batch_id=None, products=None, force=False):
    with workspace_lock(paths):
        if paths.project_ref_path.is_file():
            workspace_state = paths.load_state()
        else:
            workspace_state = json.loads(paths.state_path.read_text()) if paths.state_path.exists() else None
        if workspace_state is not None:
            identity = workspace_state.get('project_identity') or {}
            if not identity.get('project_key'):
                raise ProjectIdentityError('legacy_workspace_state_requires_explicit_migration')
            config = checked_project(paths, workspace_state, check_inputs=False)
            key = config['project_key']
            state = read_project_state(paths, key)
            for key_name in ('batch', 'batch_overrides'):
                if key_name in workspace_state:
                    state[key_name] = copy.deepcopy(workspace_state[key_name])
        else:
            if not material_folder and resolve_project_id:
                matches = find_project_configs(paths, resolve_project_id)
                if len(matches) > 1:
                    raise ProjectIdentityError('resolve_project_id_has_ambiguous_project_registrations')
                if matches:
                    config = matches[0]
                    key = config['project_key']
                    state = read_project_state(paths, key)
                else:
                    raise ProjectIdentityError('project_registration_not_found_for_resolve_id')
            elif not material_folder or not resolve_project_id:
                reference = paths.workspace_root / 'PROJECT_REF.json'
                if not reference.is_file():
                    raise ProjectIdentityError('first_registration_requires_folder_and_confirmed_resolve_id')
                ref = json.loads(reference.read_text())
                key = ref.get('project_key')
                state = read_project_state(paths, key)
                config = json.loads((project_directory(paths, key) / CONFIG_NAME).read_text())
                if ref.get('config_digest') != digest(config):
                    raise ProjectIdentityError('project_reference_stale_reinitialize')
            else:
                key = _project_key_for(resolve_project_id, material_folder)
                matches = find_project_configs(paths, resolve_project_id)
                if len(matches) > 1:
                    raise ProjectIdentityError('resolve_project_id_has_ambiguous_project_registrations')
                if matches:
                    config = matches[0]
                    key = config['project_key']
                directory = project_directory(paths, key)
                config_path = directory / CONFIG_NAME
                state_path = project_state_path(paths, key)
                if config_path.exists() or state_path.exists():
                    if not config_path.is_file() or not state_path.is_file():
                        raise ProjectIdentityError('project_registration_incomplete_manual_recovery_required')
                    config = json.loads(config_path.read_text())
                    state = json.loads(state_path.read_text())
                    if config.get('resolve_project_id') != resolve_project_id:
                        raise ProjectIdentityError('resolve_identity_mismatch_use_separate_project')
                else:
                    config = {'schema_version': CONTRACT, 'project_key': key,
                              'resolve_project_id': resolve_project_id,
                              'material_folder': folder_identity(material_folder), 'inputs': {}}
                    state = json.loads((paths.repo_root / 'PROJECT_STATE.template.json').read_text())
        supplied = {'title_path': title, 'subtitle_paths': subtitles, 'video_paths': videos}
        resolve_values = {'project_name': resolve_project, 'source_timeline': source_timeline,
                          'target_timeline': target_timeline,
                          'source_timeline_id': source_timeline_id,
                          'target_timeline_id': target_timeline_id}
        state = copy.deepcopy(state)
        if resolve_project_id is not None and resolve_project_id != config['resolve_project_id']:
            raise ProjectIdentityError('resolve_identity_mismatch_use_separate_project')
        material_folder_changed = (material_folder is not None and
                                   not material_folder_matches(config.get('material_folder', {}), material_folder))
        if material_folder_changed and not force:
            raise ProjectIdentityError('material_folder_update_requires_explicit_force')
        new_config = copy.deepcopy(config)
        if material_folder_changed:
            new_config['material_folder'] = folder_identity(material_folder)
        inputs = new_config['inputs']
        for key, value in supplied.items():
            if value is not None:
                inputs[key] = ([str(Path(p).expanduser().resolve()) for p in value]
                               if isinstance(value, list) else str(Path(value).expanduser().resolve()))
        resolve = inputs.setdefault('resolve', {})
        resolve.update({k: v for k, v in resolve_values.items() if v is not None})
        validate_config(new_config)
        changed = new_config != config
        if (config.get('inputs') or config.get('material_folder')) and changed and not force:
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
        state.setdefault('batch_overrides', {'skip_blank_titles': True,
                                             'preserve_blank_row_indices': True})
        batch = state.setdefault('batch', {})
        new_batch = batch_id if batch_id is not None else batch.get('batch_id') or 'registration'
        new_products = products if products is not None else batch.get('product_ids', [])
        if not isinstance(new_batch, str) or not new_batch.strip():
            raise ProjectIdentityError('batch_id_invalid')
        if (not isinstance(new_products, list)
                or any(type(p) is not int or p <= 0 for p in new_products)
                or len(set(new_products)) != len(new_products)):
            raise ProjectIdentityError('product_ids_must_be_unique_positive_integers')
        if changed or new_batch != batch.get('batch_id') or new_products != batch.get('product_ids'):
            batch['completed_product_ids'] = []
            batch['failed_product_ids'] = []
            runtime['last_successful_stage'] = 'registered'
        batch.update({'batch_id': new_batch, 'product_ids': sorted(new_products)})
        # One authoritative project record is stored by stable Resolve identity.
        # A batch workspace contains a reference and a compatibility state view;
        # new runs always load their inputs from this project-scoped record.
        directory = project_directory(paths, new_config['project_key'])
        directory.mkdir(parents=True, exist_ok=True)
        config_path = directory / CONFIG_NAME
        project_state_path_value = directory / 'PROJECT_STATE.json'
        marker = directory / (CONFIG_NAME + '.invalid')
        project_state = copy.deepcopy(state)
        project_state.pop('batch', None)
        project_state.pop('batch_overrides', None)
        project_state['project_root'] = str(paths.repo_root)
        project_state['project_identity'] = state['project_identity']
        batch_runtime_keys = {'last_successful_stage', 'checkpoint_revision', 'last_error',
                              'completed', 'written', 'export_verified'}
        runtime_values = project_state.get('runtime', {})
        batch_runtime = {key: value for key, value in runtime_values.items()
                         if key in batch_runtime_keys}
        project_state['runtime'] = {key: value for key, value in runtime_values.items()
                                    if key not in batch_runtime_keys}
        batch_state = {key: state[key] for key in ('batch', 'batch_overrides') if key in state}
        batch_state.update({'schema_version': 1, 'project_key': new_config['project_key'],
                            'config_digest': digest(new_config)})
        if batch_runtime:
            batch_state['runtime'] = batch_runtime
        # The workspace contains only a project reference and batch state.
        with project_record_lock(paths, new_config['project_key']):
            marker.write_text('project registration incomplete; inspect records before recovery\n')
            atomic_json(config_path, new_config)
            atomic_json(project_state_path_value, project_state)
            atomic_json(paths.workspace_root / 'PROJECT_REF.json',
                        {'schema_version': 1, 'project_key': new_config['project_key'],
                         'config_digest': digest(new_config)})
            atomic_json(paths.state_path, batch_state)
            if (json.loads(config_path.read_text()) != new_config
                    or json.loads(project_state_path_value.read_text()) != project_state
                    or json.loads(paths.state_path.read_text()) != batch_state):
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
    parser.add_argument('--source-timeline-id')
    parser.add_argument('--target-timeline-id')
    parser.add_argument('--batch-id')
    parser.add_argument('--products', help='Comma-separated product indexes')
    parser.add_argument('--force', action='store_true', help='Confirm changed inputs; cannot switch project identity')
    args = parser.parse_args()
    products = [int(p) for p in args.products.split(',')] if args.products is not None else None
    initialize(resolve_runtime_paths(args.root, args.workspace), material_folder=args.material_folder,
               resolve_project_id=args.resolve_project_id, title=args.title, subtitles=args.subtitles,
               videos=args.videos, resolve_project=args.resolve_project, source_timeline=args.source_timeline,
               target_timeline=args.target_timeline, source_timeline_id=args.source_timeline_id,
               target_timeline_id=args.target_timeline_id, batch_id=args.batch_id, products=products,
               force=args.force)
    print('项目登记已验证，配置可跨窗口继承')


if __name__ == '__main__':
    main()
