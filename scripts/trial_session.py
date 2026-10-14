"""Bounded 3–5-product runs inherit a verified project, never an old final PASS."""
import copy
import hashlib
import hmac
import json
from pathlib import Path
import shutil
import uuid
from project_registry import atomic_json, checked_project, digest, workspace_lock
from runtime_meta import CacheIdentityError, load_checked_json, metadata_path
from runtime_paths import resolve_runtime_paths
from product_checkpoints import current_range_path, load_stage, capture_stage, publish_stage
from publish_confirmed_ranges import verify_confirmation
from codex_worker import key

CONTRACT = 'trial-session-v4'


def select_products(titles, ranges, count=4):
    if type(count) is not int or not 3 <= count <= 5:
        raise CacheIdentityError('trial_scope_requires_three_to_five_products')
    rows = sorted(titles['products'], key=lambda r: r['product_index'])
    if len(rows) < count or set(r['product_index'] for r in rows) != set(ranges):
        raise CacheIdentityError('trial_selection_requires_verified_full_catalog_and_ranges')
    for row in rows:
        body = ranges[row['product_index']]
        if body['encoding_id'] != row['encoding_id'] or not verify_confirmation(body):
            raise CacheIdentityError('trial_confirmed_range_identity_invalid')
    picked = {0, len(rows)//2, len(rows)-1}
    while len(picked) < count:
        def complexity(i):
            parts = ranges[rows[i]['product_index']]['ranges']
            return sum(p['end']-p['start'] for p in parts), len(parts), -i
        picked.add(max((i for i in range(len(rows)) if i not in picked), key=complexity))
    return [rows[i]['product_index'] for i in sorted(picked)]


def validate_trial(paths):
    state = json.loads(paths.state_path.read_text())
    claim = state.get('trial')
    if not claim:
        return None
    body = json.loads((paths.workspace_root/'TRIAL_SESSION.json').read_text())
    unsigned = {k:v for k,v in body.items() if k != 'seal'}
    secret = paths.project_records_dir/'worker_seal.key'
    if (not secret.is_file() or not isinstance(body.get('seal'), str)
            or not hmac.compare_digest(body['seal'], hmac.new(secret.read_bytes(), digest(unsigned).encode(), hashlib.sha256).hexdigest())
            or body.get('contract') != CONTRACT or claim != {'run_id': body['run_id'], 'manifest_digest': digest(body)}
            or state['batch']['product_ids'] != body['products'] or not 3 <= len(body['products']) <= 5
            or body['project_key'] != paths.registered_scope()[0]):
        raise CacheIdentityError('trial_manifest_or_scope_invalid')
    return body


def create_trial(parent, workspace, count=4):
    state = json.loads(parent.state_path.read_text())
    config = checked_project(parent, state)
    titles_path = parent.upstream_cache_dir/'titles.json'
    subtitles_path = parent.upstream_cache_dir/'subtitle_index.json'
    titles = load_checked_json(parent, titles_path)
    load_checked_json(parent, subtitles_path)
    ranges = {}
    for row in titles['products']:
        pointer = current_range_path(parent, row['product_index'], row['encoding_id'])
        record = load_stage(parent, json.loads(pointer.read_text())['reference'])
        ranges[row['product_index']] = record['body']
    products = select_products(titles, ranges, count)
    child = resolve_runtime_paths(parent.repo_root, workspace)
    if child.workspace_root.exists() and any(child.workspace_root.iterdir()):
        raise CacheIdentityError('trial_workspace_must_be_new_empty_directory')
    run_id = str(uuid.uuid4())
    new_state = copy.deepcopy(state)
    new_state['batch'] = {'batch_id': 'trial-'+run_id, 'product_ids': products,
                          'completed_product_ids': [], 'failed_product_ids': []}
    new_state['runtime']['resolve_write_enabled'] = False
    for k in ('last_successful_stage', 'last_error', 'completed', 'written', 'export_verified'):
        new_state['runtime'].pop(k, None)
    # Configuration is inherited unchanged. No new material-project identity and no legacy cache blessing.
    with workspace_lock(child):
        atomic_json(child.workspace_root/'PROJECT_CONFIG.json', config)
        atomic_json(child.state_path, new_state)
    body = {'contract': CONTRACT, 'project_key': config['project_key'], 'run_id': run_id,
            'products': products, 'parent_config_digest': digest(config), 'mode': 'bounded_trial',
            'protected_resolve': copy.deepcopy(config['inputs']['resolve'])}
    body['seal'] = hmac.new(key(child), digest(body).encode(), hashlib.sha256).hexdigest()
    new_state['trial'] = {'run_id': run_id, 'manifest_digest': digest(body)}
    with workspace_lock(child):
        atomic_json(child.workspace_root/'TRIAL_SESSION.json', body)
        atomic_json(child.state_path, new_state)
    for source in (titles_path, subtitles_path):
        # Exact verified bytes + metadata remain intact; cannot import schema-1 or unproven producers.
        destination = child.upstream_cache_dir/source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        shutil.copyfile(metadata_path(source), metadata_path(destination))
        load_checked_json(child, destination)
    for pid in products:
        row = ranges[pid]
        args = dict(product_id=pid, encoding_id=row['encoding_id'], stage='range', version=digest(row),
                    model_version='inherited-verified-human-range-v4', dependencies=[])
        publish_stage(child, **args, snapshot=capture_stage(child, **args), body=row)
    validate_trial(child)
    return child
