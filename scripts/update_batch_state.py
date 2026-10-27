#!/usr/bin/env python3
"""Resume validated product checkpoints; bare completion flags cannot grant PASS."""
import argparse
import json
from runtime_paths import resolve_runtime_paths
from project_registry import atomic_json, checked_project, workspace_lock
from product_checkpoints import read_ledger, resume, update_checkpoint
from runtime_meta import CacheIdentityError


def reconcile(paths):
    with workspace_lock(paths):
        state_bytes = paths.state_snapshot_bytes()
        state = paths.load_state()
        checked_project(paths, state)
        rows = resume(paths)
        batch = state['batch']
        batch['completed_product_ids'] = sorted(pid for pid, row in rows.items()
                                               if row['status'] in ('FINAL_PASS', 'WRITTEN', 'EXPORT_VERIFIED')
                                               and row.get('product_complete', True))
        batch['pending_variant_ids'] = {
            str(pid): [v['variant_id'] for v in row.get('delivery_scope', {}).get('pending_variants', [])]
            for pid, row in rows.items() if not row.get('product_complete', True)}
        batch['failed_product_ids'] = sorted(pid for pid, row in rows.items() if row['status'] == 'BLOCKED')
        batch['checkpoint_status_counts'] = {status: sum(r['status'] == status for r in rows.values())
                                             for status in sorted({r['status'] for r in rows.values()})}
        state.setdefault('runtime', {})['checkpoint_revision'] = read_ledger(paths)['revision']
        def check():
            if paths.state_snapshot_bytes() != state_bytes or resume(paths) != rows:
                raise CacheIdentityError('progress_inputs_changed_before_state_publication')
            if read_ledger(paths)['revision'] != state['runtime']['checkpoint_revision']:
                raise CacheIdentityError('checkpoint_revision_conflict')
        paths.write_state(state, before_commit=check)
        return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.')
    parser.add_argument('--workspace')
    for flag in ('completed', 'failed', 'recovered', 'stage'):
        parser.add_argument('--' + flag)
    parser.add_argument('--product', type=int)
    parser.add_argument('--encoding-id')
    parser.add_argument('--status')
    parser.add_argument('--plan')
    parser.add_argument('--evidence')
    parser.add_argument('--expected-revision', type=int)
    parser.add_argument('--reason')
    parser.add_argument('--inspect', action='store_true', help='Read-only restart status')
    args = parser.parse_args()
    if any(getattr(args, f) is not None for f in ('completed', 'failed', 'recovered', 'stage')):
        raise CacheIdentityError('unverified_progress_flags_blocked_use_checkpoint_evidence')
    paths = resolve_runtime_paths(args.root, args.workspace)
    if args.inspect:
        rows = resume(paths)
    else:
        if args.status:
            update_checkpoint(paths, product_id=args.product, encoding_id=args.encoding_id,
                              status=args.status, plan_ref=args.plan, evidence_ref=args.evidence,
                              expected_revision=args.expected_revision, reason=args.reason)
        rows = reconcile(paths)
    for pid, row in sorted(rows.items()):
        print(f"{pid}: {row['status']}")


if __name__ == '__main__':
    main()
