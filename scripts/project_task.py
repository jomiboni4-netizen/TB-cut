#!/usr/bin/env python3
"""Project-bound long-task plans, persistent holds and evidence-derived handoff.

This layer never edits delivery transactions or promotes product checkpoints.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

from project_registry import atomic_json, checked_project, digest
from runtime_paths import resolve_runtime_paths

CONTRACT = 'project-task-v1'
RECORD = 'PROJECT_TASKS.json'
DOCS = ('PROJECT.md', 'ACCEPTANCE.md', 'PLAN.md', 'STATUS.md', 'DECISIONS.md')
ACTIONS = {'run', 'recover', 'write'}


class TaskError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def binding(paths):
    state = paths.load_state()
    config = checked_project(paths, state)
    if not config:
        raise TaskError('task_registered_project_required')
    return {key: config[key] for key in ('project_key', 'resolve_project_id')}


def location(paths):
    directory = paths.repo_root / '.cache' / 'projects' / paths.project_key
    if any(path.is_symlink() for path in (directory, directory.parent, directory.parent.parent)):
        raise TaskError('task_project_directory_symlink')
    if directory.resolve() != paths.project_directory:
        raise TaskError('task_project_directory_mismatch')
    # Even legacy workspaces must use the one canonical project directory.
    if directory.is_symlink():
        raise TaskError('task_project_directory_symlink')
    return directory


@contextmanager
def locked(paths):
    directory = location(paths)
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / '.task.lock'
    if lock.is_symlink():
        raise TaskError('task_lock_symlink')
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise TaskError('task_record_busy') from exc
        yield
    finally:
        os.close(descriptor)


def text_file(path, text, *, create=False):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise TaskError('task_document_must_be_regular_file')
    if create:
        with path.open('x', encoding='utf-8') as stream:
            stream.write(text)
        return
    descriptor, temporary = tempfile.mkstemp(prefix='.task-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate(value, expected):
    if not isinstance(value, dict):
        raise TaskError('task_contract_or_identity_invalid')
    if (value.get('contract') != CONTRACT or value.get('identity') != expected
            or type(value.get('revision')) is not int or value['revision'] < 1
            or not isinstance(value.get('goal'), str) or not value['goal'].strip()
            or not isinstance(value.get('holds'), dict)
            or set(value['holds']) - ACTIONS
            or any(not isinstance(reason, str) or not reason.strip() for reason in value['holds'].values())
            or not isinstance(value.get('tasks'), list)
            or not isinstance(value.get('runs'), list)
            or not isinstance(value.get('decisions'), list)):
        raise TaskError('task_contract_or_identity_invalid')
    ids = set()
    for task in value['tasks']:
        if (not isinstance(task, dict) or not isinstance(task.get('id'), str)
                or not task['id'].strip() or task['id'] in ids
                or not isinstance(task.get('goal'), str) or not task['goal'].strip()
                or not isinstance(task.get('acceptance_ids'), list) or not task['acceptance_ids']
                or not set(task['acceptance_ids']).issubset({'A1', 'A2', 'A3', 'A4'})
                or type(task.get('max_runs')) is not int or task['max_runs'] < 1
                or type(task.get('max_repairs_per_run')) is not int or task['max_repairs_per_run'] < 0
                or not isinstance(task.get('product_ids'), list) or not task['product_ids']
                or any(type(pid) is not int or pid < 1 for pid in task['product_ids'])
                or len(set(task['product_ids'])) != len(task['product_ids'])
                or not isinstance(task.get('batch_id'), str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', task['batch_id'])
                or not isinstance(task.get('operations'), list) or not task['operations']
                or any(not isinstance(op, str) or not op for op in task['operations'])):
            raise TaskError('task_definition_invalid')
        ids.add(task['id'])
    if value.get('active_task_id') is not None and value['active_task_id'] not in ids:
        raise TaskError('task_active_id_invalid')
    for run in value['runs']:
        if (not isinstance(run, dict) or run.get('task_id') not in ids
                or not run.get('id') or run.get('status') not in ('RUNNING', 'PASS', 'FAILED', 'INTERRUPTED')):
            raise TaskError('task_run_invalid')
    return value


def load(paths, *, required=False):
    if not paths.project_key:
        if required:
            raise TaskError('task_registered_project_required')
        return None
    file = location(paths) / RECORD
    if file.is_symlink():
        raise TaskError('task_contract_symlink')
    if not file.exists():
        # Existing projects remain compatible until explicitly enrolled.
        if required or any((location(paths) / name).exists() or (location(paths) / name).is_symlink() for name in DOCS):
            raise TaskError('task_contract_missing_or_initialization_incomplete')
        return None
    if file.is_symlink():
        raise TaskError('task_contract_symlink')
    value = validate(json.loads(file.read_text(encoding='utf-8')), binding(paths))
    for name in DOCS:
        path = location(paths) / name
        if not path.is_file() or path.is_symlink():
            raise TaskError('task_document_missing_or_symlink:' + name)
    return value


def save(paths, value):
    value['revision'] += 1
    validate(value, binding(paths))
    atomic_json(location(paths) / RECORD, value)


def initialize(paths, goal):
    expected = binding(paths)
    with locked(paths):
        directory = location(paths)
        if any((directory / name).exists() or (directory / name).is_symlink() for name in (*DOCS, RECORD)):
            raise TaskError('task_files_exist_refuse_overwrite')
        value = {'contract': CONTRACT, 'identity': expected, 'revision': 1, 'goal': goal,
                 'active_task_id': None, 'holds': {}, 'tasks': [], 'runs': [],
                 'decisions': [{'at': now(), 'kind': 'initialize', 'reason': goal}]}
        validate(value, expected)
        header = f"项目稳定 ID：`{expected['resolve_project_id']}`\n\n"
        bodies = {
            'PROJECT.md': '# 项目目标\n\n' + header + goal + '\n\n## 范围\n\n仅本项目；具体商品范围由当前任务与批次绑定。工具代码优化不等于项目交付完成。\n\n## 完成标准\n\n所有约定商品、版本均满足 ACCEPTANCE.md，且没有未关闭事务；项目汇总验收仍需单独执行。\n',
            'ACCEPTANCE.md': '# 验收标准\n\n' + header + '''版本：1。修改标准后旧调度计划失效，旧证据不能仅凭文字更新为通过。

- A1 质量：当前方案对应的语义与全部本地硬门禁通过；以现有商品检查点验证链为准。
- A2 交付：稳定项目/目标 ID、逐段源范围、音画同步、标记与受保护时间线校验通过；必须有有效回读事务及报告。WRITTEN 不等于回读通过。
- A3 汇总：覆盖约定完整商品范围，跨组重叠、版本及缺少版本的理由均有审核证据；单批 PASS 不能证明项目完成。
- A4 导出：仅在用户明确要求时必需；导出文件与方案、音画和规格验证通过。

状态摘要只列证据和冲突，不封存新审核，不自动宣布整项目完成。无证据为待验收，冲突为阻塞。
''',
            'PLAN.md': '# 执行计划\n\n' + header + '''任务通过 project_task.py add-task 登记到 PROJECT_TASKS.json；每项写明目标、商品/批次范围、验收编号、允许操作、最多运行次数和单次返修上限。

## 阶段

1. 认证实时项目，读取本目录目标、验收、状态和决定。
2. 核对失败/中断事务，优先处理阻塞；暂停决定未撤销时不恢复。
3. 选择范围匹配的任务，经调度器 plan 后 run。
4. 记录运行结果与证据；PASS 仅代表本次命令成功，仍需验收。
5. 失败先 diagnose；预算耗尽、事务不一致或用户暂停时停止。禁止换批次绕过预算。

任务依赖由前置条件和未关闭事务约束；不自动将命令成功当作需求验收成功。
''',
            'STATUS.md': '# 当前状态\n\n尚未生成。运行 refresh。\n',
            'DECISIONS.md': '# 决策与用户限制\n\n' + header + '结构化决定保存在 PROJECT_TASKS.json；此文件同步显示决定与理由，可追加人工备注。\n',
        }
        # Publish the contract last. Interrupted initialization fails closed.
        for name, text in bodies.items():
            text_file(directory / name, text, create=True)
        atomic_json(directory / RECORD, value)
    return refresh(paths)


def record_hold(paths, actions, reason, *, release=False):
    if not actions or set(actions) - ACTIONS or not reason.strip():
        raise TaskError('task_hold_action_or_reason_invalid')
    with locked(paths):
        value = load(paths, required=True)
        for action in actions:
            if release:
                value['holds'].pop(action, None)
            else:
                value['holds'][action] = reason
        value['decisions'].append({'at': now(), 'kind': 'release' if release else 'hold',
                                   'actions': actions, 'reason': reason})
        save(paths, value)
        # Append, never erase user notes or prior decisions.
        file = location(paths) / 'DECISIONS.md'
        text_file(file, file.read_text(encoding='utf-8') +
                  f"\n- {now()} {'解除' if release else '暂停'} {', '.join(actions)}：{reason}\n")
    return refresh(paths)


def add_task(paths, task):
    with locked(paths):
        value = load(paths, required=True)
        value['tasks'].append(task)
        value['active_task_id'] = task['id']
        save(paths, value)
    return refresh(paths)


def select_task(paths, task_id):
    with locked(paths):
        value = load(paths, required=True)
        if task_id not in {row['id'] for row in value['tasks']}:
            raise TaskError('task_id_not_found')
        value['active_task_id'] = task_id
        save(paths, value)
    return refresh(paths)


def transactions(paths):
    """Only inspect transaction summaries, never media/subtitles or model logs."""
    control = load(paths, required=True)
    dispositions = {row.get('transaction_path'): row
                    for row in control.get('closed_transactions', [])
                    if isinstance(row, dict)}
    result = []
    directory = location(paths)
    patterns = ('batches/**/resolve_append_transaction_*.json',
                'batches/**/reusable_test_prepare_transaction.json',
                'batches/**/preview_transaction.json',
                'batches/**/RESOLVE_TRANSACTION.json')
    seen = set()
    for pattern in patterns:
        for file in sorted(directory.glob(pattern)):
            if file in seen:
                continue
            seen.add(file)
            if file.is_symlink() or not file.resolve().is_relative_to(directory.resolve()):
                raise TaskError('task_evidence_path_escape')
            row = json.loads(file.read_text(encoding='utf-8'))
            state = row.get('status')
            relative = str(file.relative_to(directory))
            report = row.get('report')
            target_id = row.get('target_timeline_id')
            if report and not target_id:
                report_path = Path(report)
                if report_path.is_file() and report_path.resolve().is_relative_to(directory.resolve()):
                    target_id = json.loads(report_path.read_text(encoding='utf-8')).get('target_timeline_id')
            empty_ready = (state == 'ALREADY_EMPTY_VERIFIED'
                           and row.get('mutation_performed') is False
                           and row.get('project_id') == control['identity']['resolve_project_id']
                           and row.get('target_timeline_id') == target_id
                           and isinstance(row.get('before'), dict)
                           and row.get('before') == row.get('after')
                           and not row['before'].get('rows')
                           and not row['before'].get('markers'))
            item = {'path': relative, 'status': state,
                    'verified': state in ('READBACK_VERIFIED', 'VERIFIED', 'EXPORT_VERIFIED') or empty_ready,
                           'products': row.get('products', row.get('product_ids', [])),
                           'target_timeline_id': target_id, 'evidence_revalidated': False}
            disposition = dispositions.get(relative)
            if disposition:
                transaction_sha = hashlib.sha256(file.read_bytes()).hexdigest()
                valid_base = (transaction_sha == disposition.get('transaction_sha256')
                              and disposition.get('status_preserved') == state
                              and target_id == disposition.get('target_timeline_id'))
                if disposition.get('resolution_type') == 'no_mutation_verified':
                    proof_path = directory / str(disposition.get('resolution_path', ''))
                    valid_disposition = (valid_base and proof_path.is_file()
                                         and not proof_path.is_symlink()
                                         and proof_path.resolve().is_relative_to(directory.resolve())
                                         and hashlib.sha256(proof_path.read_bytes()).hexdigest()
                                         == disposition.get('resolution_sha256'))
                    if valid_disposition:
                        proof = json.loads(proof_path.read_text(encoding='utf-8'))
                        valid_disposition = (
                            proof.get('schema_version') == 1
                            and proof.get('status') == 'NO_MUTATION_VERIFIED'
                            and proof.get('transaction_path') == relative
                            and proof.get('transaction_sha256') == transaction_sha
                            and proof.get('original_status') == state
                            and proof.get('project_id') == control['identity']['resolve_project_id']
                            and proof.get('target_timeline_id') == target_id
                            and proof.get('before') == proof.get('live_snapshot')
                            and proof.get('before_sha256') == digest(proof.get('before'))
                            and proof.get('live_snapshot_sha256') == digest(proof.get('live_snapshot'))
                            and not proof.get('live_snapshot', {}).get('rows')
                            and not proof.get('live_snapshot', {}).get('markers')
                            and proof.get('backup_sha256') == row.get('backup_sha256')
                            and proof.get('backup_verified') is True)
                    if valid_disposition:
                        item['no_mutation_verified'] = disposition
                else:
                    clear_path = directory / str(disposition.get('clear_transaction_path', ''))
                    valid_disposition = (valid_base and clear_path.is_file()
                                         and not clear_path.is_symlink()
                                         and clear_path.resolve().is_relative_to(directory.resolve())
                                         and hashlib.sha256(clear_path.read_bytes()).hexdigest()
                                         == disposition.get('clear_transaction_sha256'))
                    if valid_disposition:
                        clear_record = json.loads(clear_path.read_text(encoding='utf-8'))
                        valid_disposition = (clear_record.get('status') == 'READBACK_VERIFIED'
                                             and clear_record.get('target_timeline_id') == target_id)
                    if valid_disposition:
                        item['superseded_by_authorized_clear'] = disposition
            result.append(item)
    return result


def snapshot(paths):
    value = load(paths, required=True)
    txs = transactions(paths)
    blockers = [{'kind': 'hold', 'action': action, 'reason': reason}
                for action, reason in value['holds'].items()]
    blockers += [{'kind': 'transaction', **row} for row in txs
                 if not row['verified'] and 'superseded_by_authorized_clear' not in row
                 and 'no_mutation_verified' not in row]
    blockers += [{'kind': 'interrupted_or_running', 'run_id': row['id']}
                 for row in value['runs'] if row['status'] == 'RUNNING']
    # Checkpoints are observations, not new approvals. Never call resume here:
    # rendering project notes must not load models or media.
    ledger_file = location(paths) / 'records' / 'checkpoints.json'
    rows = {}
    if ledger_file.exists():
        ledger = json.loads(ledger_file.read_text(encoding='utf-8'))
        if ledger.get('schema_version') != 2 or ledger.get('project_key') != value['identity']['project_key']:
            raise TaskError('task_checkpoint_identity_invalid')
        rows = {pid: {'recorded_status': row.get('status'), 'evidence_revalidated': False}
                for pid, row in ledger.get('products', {}).items()}
    return {'contract': CONTRACT, 'identity': value['identity'], 'revision': value['revision'],
            'generated_at': now(), 'active_task_id': value['active_task_id'],
            'holds': value['holds'], 'blockers': blockers, 'transactions': txs,
            'products': rows, 'runs': value['runs'], 'tasks': value['tasks'],
            'project_completion': 'BLOCKED' if blockers else 'NOT_EVALUATED',
            'next_action': '保留现场，先核对限制和未关闭事务；不得自动续跑。' if blockers
                           else '选择任务并核验前置条件，再运行匹配调度器 plan/run。'}


def refresh(paths):
    with locked(paths):
        body = snapshot(paths)
        value = load(paths, required=True)
        lines = ['# 当前状态（自动生成，请勿手改）', '',
                 f"项目稳定 ID：`{body['identity']['resolve_project_id']}`", '',
                 f"生成时间：{body['generated_at']}；控制版本：{body['revision']}", '',
                 f"项目完成状态：{body['project_completion']}（不替代最终验收）", '',
                 '## 当前任务', '', body['active_task_id'] or '尚未选择任务。', '',
                 '## 阻塞与用户限制', '']
        lines += [f"- {json.dumps(row, ensure_ascii=False)}" for row in body['blockers']] or ['无已记录阻塞；不代表已验收。']
        lines += ['', '## 事务记录（仅记录状态，不是新验收）', '']
        lines += [f"- {row['path']}：{row['status']}；" +
                  ("已由只读回读证实未发生改动，原事务状态保留。" if 'no_mutation_verified' in row
                   else "已由本时间线经验证清理取代，原失败状态保留。" if 'superseded_by_authorized_clear' in row
                   else '证据待重新核验。') for row in body['transactions']] or ['暂无事务。']
        lines += ['', '## 商品记录（未重新验证证据）', '']
        lines += [f"- 商品 {pid}：{row['recorded_status']}；证据待重新核验。" for pid, row in body['products'].items()] or ['暂无商品检查点。']
        lines += ['', '## 任务及预算', '']
        for task in value['tasks']:
            attempts = [row for row in body['runs'] if row['task_id'] == task['id']]
            lines.append(f"- {task['id']}：{task['goal']}；已运行 {len(attempts)}/{task['max_runs']}；单次返修上限 {task['max_repairs_per_run']}；验收 {', '.join(task['acceptance_ids'])}。")
        lines += ['', '## 最近执行', '']
        lines += [f"- {row['id']}：{row['status']}；任务 {row['task_id']}；命令成功仍待验收。" for row in body['runs'][-5:]] or ['无执行记录。']
        lines += ['', '## 下一步', '', body['next_action'], '']
        text_file(location(paths) / 'STATUS.md', '\n'.join(lines))
    return body


def plan_identity(paths):
    value = load(paths)
    if value is None:
        return None
    directory = location(paths)
    return digest({'contract': {key: val for key, val in value.items()
                                if key not in ('runs', 'revision', 'closed_transactions')},
                   'documents': {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                   for name in DOCS if name != 'STATUS.md'}})


def guard(paths, action='run'):
    # Delivery adapters may supply a small path facade; resolve its real workspace.
    if not hasattr(paths, "project_key"):
        paths = resolve_runtime_paths(paths.repo_root, paths.workspace_root)
    value = load(paths)
    if value is None:
        return
    if value['holds'].get(action) or value['holds'].get('run'):
        raise TaskError('project_task_paused:' + (value['holds'].get(action) or value['holds']['run']))
    for row in value['runs']:
        if row['status'] == 'RUNNING' and row.get('plan_identity') != plan_identity(paths):
            raise TaskError('project_task_plan_changed_during_run')


def authorized_clear_for_run(paths, target_timeline_id):
    state = paths.load_state().get('batch', {})
    override_path = paths.workspace_root / 'delivery' / 'USER_OVERRIDE.json'
    if not override_path.is_file() or override_path.is_symlink():
        return False
    try:
        auth = json.loads(override_path.read_text(encoding='utf-8'))
        write = auth.get('resolve_write_authorization', {})
        return (auth.get('confirmed_by_user') is True
                and write.get('action') == 'clear_configured_target_and_append'
                and write.get('clear_existing') is True
                and write.get('project_id') == paths.load_state()['project_identity']['resolve_project_id']
                and write.get('target_timeline_id') == target_timeline_id
                and sorted(write.get('product_ids', [])) == sorted(state.get('product_ids', [])))
    except (OSError, ValueError, TypeError):
        return False


def supersede_transactions_after_clear(paths, target_timeline_id, clear_transaction_path):
    """Keep failed receipts immutable while closing only history on the cleared target."""
    directory = location(paths).resolve()
    clear_path = Path(clear_transaction_path)
    if clear_path.is_symlink() or not clear_path.resolve().is_relative_to(directory):
        raise TaskError('clear_transaction_path_invalid')
    clear = json.loads(clear_path.read_text(encoding='utf-8'))
    if clear.get('status') != 'READBACK_VERIFIED' or clear.get('target_timeline_id') != target_timeline_id:
        raise TaskError('clear_transaction_not_verified_for_target')
    value = load(paths, required=True)
    existing = {row.get('transaction_path') for row in value.get('closed_transactions', [])}
    added = []
    for transaction in transactions(paths):
        relative = transaction['path']
        if (transaction['verified'] or 'no_mutation_verified' in transaction
                or transaction.get('target_timeline_id') != target_timeline_id or relative in existing):
            continue
        path = directory / relative
        report_path = directory / json.loads(path.read_text(encoding='utf-8')).get('report', '')
        if not report_path.is_file() or not report_path.resolve().is_relative_to(directory):
            continue
        row = {'transaction_path': relative, 'transaction_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
               'status_preserved': transaction['status'],
               'clear_transaction_path': str(clear_path.relative_to(directory)),
               'clear_transaction_sha256': hashlib.sha256(clear_path.read_bytes()).hexdigest(),
               'target_timeline_id': target_timeline_id, 'closed_at': now(),
               'reason': 'user_authorized_test_timeline_clear_superseded_old_contents'}
        value.setdefault('closed_transactions', []).append(row)
        added.append(row)
    if added:
        with locked(paths):
            current = load(paths, required=True)
            known = {row.get('transaction_path') for row in current.get('closed_transactions', [])}
            current.setdefault('closed_transactions', []).extend(
                row for row in added if row['transaction_path'] not in known)
            save(paths, current)
        refresh(paths)
    return added


def record_no_mutation_disposition(paths, proof_path):
    """Close only a prepare transaction proven unchanged by a live readback."""
    directory = location(paths).resolve()
    proof_file = Path(proof_path)
    if (proof_file.is_symlink() or not proof_file.is_file()
            or not proof_file.resolve().is_relative_to(directory)):
        raise TaskError('no_mutation_proof_path_invalid')
    proof = json.loads(proof_file.read_text(encoding='utf-8'))
    relative = proof.get('transaction_path')
    transaction_file = directory / str(relative or '')
    if (transaction_file.is_symlink() or not transaction_file.is_file()
            or not transaction_file.resolve().is_relative_to(directory)):
        raise TaskError('no_mutation_transaction_path_invalid')
    transaction = json.loads(transaction_file.read_text(encoding='utf-8'))
    transaction_sha = hashlib.sha256(transaction_file.read_bytes()).hexdigest()
    if (proof.get('schema_version') != 1 or proof.get('status') != 'NO_MUTATION_VERIFIED'
            or proof.get('transaction_sha256') != transaction_sha
            or proof.get('original_status') != transaction.get('status')
            or transaction.get('status') != 'CLEAR_IN_PROGRESS'
            or proof.get('project_id') != load(paths, required=True)['identity']['resolve_project_id']
            or proof.get('target_timeline_id') != transaction.get('target_timeline_id')
            or proof.get('before') != proof.get('live_snapshot')
            or proof.get('before_sha256') != digest(proof.get('before'))
            or proof.get('live_snapshot_sha256') != digest(proof.get('live_snapshot'))
            or proof.get('live_snapshot', {}).get('id') != proof.get('target_timeline_id')
            or proof.get('live_snapshot', {}).get('rows')
            or proof.get('live_snapshot', {}).get('markers')
            or proof.get('backup_sha256') != transaction.get('backup_sha256')
            or proof.get('backup_verified') is not True):
        raise TaskError('no_mutation_proof_invalid')
    row = {'transaction_path': str(transaction_file.relative_to(directory)),
           'transaction_sha256': transaction_sha, 'status_preserved': transaction['status'],
           'target_timeline_id': transaction['target_timeline_id'],
           'resolution_type': 'no_mutation_verified',
           'resolution_path': str(proof_file.relative_to(directory)),
           'resolution_sha256': hashlib.sha256(proof_file.read_bytes()).hexdigest(),
           'closed_at': now(), 'reason': 'live_readback_matches_preparation_snapshot_no_mutation'}
    with locked(paths):
        value = load(paths, required=True)
        existing = {item.get('transaction_path') for item in value.get('closed_transactions', [])}
        if row['transaction_path'] not in existing:
            value.setdefault('closed_transactions', []).append(row)
            save(paths, value)
    refresh(paths)
    return row


def begin_run(paths, operation, repair_limit, target_timeline_id=None):
    """Reserve a project-wide run before any media/model/Resolve stage starts."""
    with locked(paths):
        value = load(paths)
        if value is None:
            return None
        action = 'recover' if 'recover' in operation or 'repair-layout' == operation else 'run'
        guard(paths, action)
        guard(paths, 'write')  # all scheduler runs may reach delivery
        pending = [row for row in transactions(paths)
                   if not row['verified'] and 'superseded_by_authorized_clear' not in row
                   and 'no_mutation_verified' not in row
                   and (target_timeline_id is None or row.get('target_timeline_id') in (None, target_timeline_id))]
        clear_authorized = target_timeline_id and authorized_clear_for_run(paths, target_timeline_id)
        if action != 'recover' and pending and not clear_authorized:
            raise TaskError('project_task_unverified_transaction_no_automatic_run')
        if any(row['status'] == 'RUNNING' for row in value['runs']):
            raise TaskError('project_task_run_in_progress_or_interrupted_diagnose_first')
        task = next((row for row in value['tasks'] if row['id'] == value['active_task_id']), None)
        if task is None:
            raise TaskError('project_task_select_task_first')
        batch = paths.load_state().get('batch', {})
        if (batch.get('batch_id') != task['batch_id']
                or sorted(batch.get('product_ids', [])) != sorted(task['product_ids'])
                or operation not in task['operations']):
            raise TaskError('project_task_scope_or_operation_mismatch')
        if (type(repair_limit) is not int or repair_limit < 0
                or repair_limit > task['max_repairs_per_run']):
            raise TaskError('project_task_repair_budget_exceeded')
        if sum(row['task_id'] == task['id'] for row in value['runs']) >= task['max_runs']:
            raise TaskError('project_task_run_budget_exhausted')
        run_id = str(uuid.uuid4())
        value['runs'].append({'id': run_id, 'task_id': task['id'], 'operation': operation,
                              'status': 'RUNNING', 'started_at': now(),
                              'plan_identity': plan_identity(paths)})
        save(paths, value)
    return run_id


def finish_run(paths, run_id, code, *, interrupted=False, reason=None):
    if run_id is None:
        return
    with locked(paths):
        value = load(paths, required=True)
        row = next((row for row in value['runs'] if row['id'] == run_id), None)
        if row is None or row['status'] != 'RUNNING':
            raise TaskError('project_task_run_not_open')
        row.update(status='INTERRUPTED' if interrupted else 'PASS' if code == 0 else 'FAILED',
                   finished_at=now(), return_code=code, reason=reason)
        save(paths, value)
    refresh(paths)


def live_paths(root, workspace=None):
    """Use the existing bounded read-only doctor, forcing fresh stable-ID authentication."""
    root = Path(root).resolve()
    command = [sys.executable, str(root / 'scripts/resolve_connection_doctor.py'), '--root', str(root),
               '--fresh-identity']
    if workspace:
        command += ['--workspace', str(Path(workspace).resolve())]
    child = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    try:
        result = json.loads(child.stdout)
    except ValueError as exc:
        raise TaskError('task_live_identity_check_failed') from exc
    if child.returncode or not result.get('ready') or not result.get('project_id'):
        raise TaskError('task_live_identity_unavailable:' + result.get('reason', 'unknown'))
    selected = workspace or result.get('bound_workspace')
    if not selected:
        raise TaskError('task_live_project_workspace_missing')
    paths = resolve_runtime_paths(root, selected)
    if binding(paths)['resolve_project_id'] != result['project_id']:
        raise TaskError('task_live_project_mismatch')
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('init', 'refresh', 'check', 'hold', 'release', 'add-task', 'select-task', 'close-interrupted'))
    parser.add_argument('--root', default='.')
    parser.add_argument('--workspace', help='Optional explicit workspace; still verified against live Resolve ID')
    parser.add_argument('--goal')
    parser.add_argument('--actions', help='Comma separated run,recover,write')
    parser.add_argument('--reason')
    parser.add_argument('--task-file', type=Path)
    parser.add_argument('--run-id')
    parser.add_argument('--task-id')
    args = parser.parse_args()
    try:
        paths = live_paths(args.root, args.workspace)
        if args.command == 'init':
            if not args.goal:
                raise TaskError('task_goal_required')
            result = initialize(paths, args.goal)
        elif args.command in ('hold', 'release'):
            if not args.actions or not args.reason:
                raise TaskError('task_actions_and_reason_required')
            result = record_hold(paths, args.actions.split(','), args.reason, release=args.command == 'release')
        elif args.command == 'add-task':
            if not args.task_file:
                raise TaskError('task_file_required')
            result = add_task(paths, json.loads(args.task_file.read_text(encoding='utf-8')))
        elif args.command == 'select-task':
            if not args.task_id:
                raise TaskError('task_id_required')
            result = select_task(paths, args.task_id)
        elif args.command == 'close-interrupted':
            if not args.run_id or not args.reason:
                raise TaskError('task_run_id_and_reason_required')
            finish_run(paths, args.run_id, None, interrupted=True, reason=args.reason)
            result = snapshot(paths)
        else:
            result = refresh(paths) if args.command == 'refresh' else snapshot(paths)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'task_error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
