#!/usr/bin/env python3
"""Mode-aware top-level scheduler for the two editing workflows.

The scheduler owns routing and stage boundaries. Existing stage implementations
remain in their focused modules; this file never implements editorial logic.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from project_editing_modes import resolve_editing_mode
from project_registry import atomic_json, digest, workspace_lock
from runtime_paths import resolve_runtime_paths


class SchedulerError(RuntimeError):
    pass


PERSON_DETECTOR_PROTOCOL = "apple-frame-v6-person-presence-closeup-pose-fallback"


def resolve_person_detector(paths, requested=None):
    """Use or build the Resolve-project-bound Apple Vision any-person detector."""
    if requested:
        detector = Path(requested).expanduser().resolve()
    else:
        detector = paths.project_directory / "dependencies" / "apple_frame_probe"
    source = paths.repo_root / "scripts" / "apple_frame_probe.swift"
    if not source.is_file():
        raise SchedulerError("person_detector_source_missing")
    detector.parent.mkdir(parents=True, exist_ok=True)
    if not detector.is_file() or detector.stat().st_mtime_ns < source.stat().st_mtime_ns:
        if requested:
            raise SchedulerError("person_detector_stale_rebuild_in_batch_cache")
        compiler = shutil.which("swiftc")
        if not compiler:
            raise SchedulerError("person_detector_build_blocked_swiftc_missing")
        module_cache = detector.parent / ".swift_module_cache"
        module_cache.mkdir(parents=True, exist_ok=True)
        built = subprocess.run([compiler, "-module-cache-path", str(module_cache), str(source),
                                "-o", str(detector)], cwd=paths.repo_root, text=True,
                               capture_output=True)
        if built.returncode:
            raise SchedulerError("person_detector_build_failed_" + str(built.returncode))
    try:
        version = subprocess.run([str(detector), "--version"], text=True,
                                 capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SchedulerError("person_detector_start_failed_" + type(exc).__name__) from exc
    if version.returncode or version.stdout.strip() != PERSON_DETECTOR_PROTOCOL:
        raise SchedulerError("person_detector_protocol_mismatch")
    from adaptive_vision import classifier_identity
    classifier_identity(detector)
    return str(detector)


WORKFLOWS = {
    "taobao_legacy": {
        "label": "淘宝",
        "module": "project_scheduler.py",
        "stages": ["registration", "resolve_preflight", "title_and_subtitle_index", "semantic_ranges",
                   "codex_selection", "codex_semantic_final_review", "selection_person_prescreen",
                   "asr_nearby_cutpoint_execution", "physical_plan", "final_person_audit",
                   "final_quality_gate", "resolve_delivery"],
        "implemented_through": "resolve_delivery",
        "run_args": ["--mode", "codex"],
        "delivery_adapter": "resolve_write_v2.append_selected_or_registered_target",
        "delivery_modes": ["registered_target_append", "selected_existing_timeline_append",
                           "reusable_test_timeline"],
    },
    "dudu_marked_range": {
        "label": "都督",
        "module": "delivery_pipeline.py",
        "stages": ["registration", "resolve_preflight", "marked_ranges",
                   "codex_selection", "local_asr", "waveform_cutpoints",
                   "semantic_and_person_gates", "resolve_write_readback"],
        "implemented_through": "semantic_and_person_gates",
        "run_args": [],
        "delivery_adapter": "delivery_pipeline.delivery_resolve",
        "delivery_modes": ["one_new_combined_timeline", "reusable_test_timeline"],
    },
}


def workflow_for_state(paths):
    state = paths.load_state()
    resolve = state.get("resolve", {})
    name = resolve.get("project_name")
    mode_name, _ = resolve_editing_mode(name)
    workflow = WORKFLOWS.get(mode_name)
    if workflow is None:
        raise SchedulerError("scheduler_route_missing_for_project_mode")
    if workflow["module"] not in {p.name for p in (paths.repo_root / "scripts").glob("*.py")}:
        raise SchedulerError("scheduler_stage_module_missing")
    return state, mode_name, workflow


def test_reuse_capability(mode_name):
    workflow = WORKFLOWS.get(mode_name)
    return bool(workflow and "reusable_test_timeline" in workflow.get("delivery_modes", []))


def selected_delivery_timeline(state, requested=None):
    name = requested if requested is not None else state.get("resolve", {}).get("target_timeline")
    if not isinstance(name, str) or not name.strip():
        raise SchedulerError("delivery_target_timeline_missing")
    return name.strip()


def selected_delivery_binding(state, requested=None):
    """Resolve the configured stable ID for the per-run target without live guessing."""
    name = selected_delivery_timeline(state, requested)
    resolve = state.get("resolve", {})
    if name == resolve.get("target_timeline"):
        timeline_id = resolve.get("target_timeline_id")
        mode = "registered_target_append"
    else:
        binding = state.get("runtime", {}).get("test_timeline_binding", {})
        if (binding.get("project_id") != (state.get("project_identity") or {}).get("resolve_project_id")
                or binding.get("name") != name or not binding.get("timeline_id")):
            raise SchedulerError("selected_target_not_registered_test_timeline")
        timeline_id = binding["timeline_id"]
        mode = "reusable_test_timeline"
    if not timeline_id or timeline_id == resolve.get("source_timeline_id"):
        raise SchedulerError("delivery_target_stable_id_missing_or_conflicting")
    if mode == "reusable_test_timeline" and timeline_id == resolve.get("target_timeline_id"):
        raise SchedulerError("registered_test_timeline_cannot_alias_formal_target")
    return {"name": name, "timeline_id": timeline_id, "mode": mode}


def batch_product_scope(state, titles):
    selected = {int(pid) for pid in state.get("batch", {}).get("product_ids", [])}
    available = {int(row["product_index"]) for row in titles.get("products", [])}
    if not selected or not selected.issubset(available):
        raise SchedulerError("batch_product_missing_from_current_titles")
    return selected


def plan(paths, target_timeline=None, blind_fresh=False, delivery_product_id=None):
    state, mode_name, workflow = workflow_for_state(paths)
    selected_target = selected_delivery_binding(state, target_timeline)
    product_ids = state.get("batch", {}).get("product_ids", [])
    if delivery_product_id is not None and delivery_product_id not in product_ids:
        raise SchedulerError("delivery_product_outside_batch")
    return {
        "schema_version": 1,
        "project_task_identity": __import__("project_task").plan_identity(paths),
        "mode": mode_name,
        "workflow": workflow["label"],
        "project_key": (state.get("project_identity") or {}).get("project_key"),
        "registration_digest": (state.get("project_identity") or {}).get("config_digest"),
        "resolve_project_id": (state.get("project_identity") or {}).get("resolve_project_id"),
        "project_name": state["resolve"]["project_name"],
        "source_timeline_id": state["resolve"].get("source_timeline_id"),
        "registered_target_timeline_id": state["resolve"].get("target_timeline_id"),
        "delivery_target_timeline": selected_target["name"],
        "delivery_target_timeline_id": selected_target["timeline_id"],
        "delivery_target_mode": selected_target["mode"],
        "selection_mode": "blind_fresh" if blind_fresh else "resume_valid_evidence",
        "technical_recovery_policy": __import__('vision_recovery').POLICY,
        "batch_id": state.get("batch", {}).get("batch_id"),
        "products": state.get("batch", {}).get("product_ids", []),
        "delivery_product_id": delivery_product_id,
        "stages": workflow["stages"],
        "implemented_through": workflow["implemented_through"],
        "delivery_stage": workflow["stages"][-1],
        "delivery_ready": workflow["implemented_through"] == workflow["stages"][-1],
        "delivery_adapter": workflow.get("delivery_adapter"),
        "delivery_modes": (workflow.get("delivery_modes", []) if target_timeline
                           else [mode for mode in workflow.get("delivery_modes", [])
                                 if mode != "selected_existing_timeline_append"]),
        "test_timeline_reuse_ready": test_reuse_capability(mode_name),
    }


def persist_plan(paths, target_timeline=None, blind_fresh=False, delivery_product_id=None):
    body = plan(paths, target_timeline, blind_fresh, delivery_product_id)
    record = {"schema_version": 1, "plan": body, "plan_digest": digest(body)}
    from project_registry import atomic_json
    atomic_json(paths.workspace_root / "SCHEDULER_PLAN.json", record)
    from project_task import load, refresh
    if load(paths) is not None:
        refresh(paths)
    return record


def require_current_plan(paths, expected_mode, target_timeline=None, blind_fresh=False,
                         delivery_product_id=None):
    path = paths.workspace_root / "SCHEDULER_PLAN.json"
    if not path.is_file():
        raise SchedulerError("scheduler_plan_required_run_plan_first")
    record = json.loads(path.read_text(encoding="utf-8"))
    current = plan(paths, target_timeline, blind_fresh, delivery_product_id)
    if (record.get("schema_version") != 1 or record.get("plan") != current
            or record.get("plan_digest") != digest(current)
            or current.get("mode") != expected_mode):
        raise SchedulerError("scheduler_plan_stale_or_mode_mismatch_rerun_plan")


def failure_repair_card(event):
    """Map a recorded scheduler failure to a bounded, evidence-based next step."""
    if not event or event.get('status') != 'FAILED':
        return None
    stage = str(event.get('stage') or '')
    error = str(event.get('error') or '')
    if stage == 'batch_semantic_range_matching':
        marker = 'project_range_matching_waiting_for_evidence_product_'
        if marker in error:
            product_id = error.split(marker, 1)[1].split(':', 1)[0].split('_', 1)[0]
            return {
                'failure_class': 'incomplete_boundary_evidence',
                'status': 'WAITING_FOR_EVIDENCE',
                'stage': stage,
                'product_id': product_id,
                'attempts_used': 1,
                'attempts_allowed': 1,
                'action_required': ('先对照前后商品的已知起止 cue 推断候选搜索包络；整体商品结束锚点缺失本身不应阻断卖点筛选。'
                                    '仅当没有当前商品标题/卖点证据或归属冲突时，才补证或人工确认后从范围匹配继续。'),
                'preserve': ['已认证项目身份', '标题与字幕索引', '所有已验证上游阶段'],
                'invalidate': ['范围匹配输出及其下游阶段'],
                'automatic_resume': False,
                'same_input_retry': False,
            }

        return {
            'failure_class': 'range_matching_failed',
            'status': 'FAILED_NEEDS_DIAGNOSIS',
            'stage': stage,
            'action_required': ('检查本次范围匹配事件与对应 metrics；先分类具体错误，不要对相同输入盲目重跑。'),
            'preserve': ['已认证项目身份', '标题与字幕索引', '所有已验证上游阶段'],
            'invalidate': ['范围匹配输出及其下游阶段'],
            'automatic_resume': False,
            'same_input_retry': False,
        }
    if stage:
        return {
            'failure_class': 'stage_failed', 'status': 'FAILED_NEEDS_DIAGNOSIS',
            'stage': stage,
            'action_required': '检查该阶段保存的失败事件和有效检查点，再选择有界恢复操作。',
            'automatic_resume': False,
        }
    return None


def diagnose_cursor_path(paths, product_id, selection_mode):
    """Use the cursor scope selected by the persisted scheduler plan."""
    base = (paths.cache_dir if selection_mode == 'blind_fresh'
            else paths.project_records_dir)
    return base / 'cursors' / f'{product_id}.json'


def _diagnose(paths):
    """Describe the latest checkpoint and a conservative resume/rollback point."""
    _, mode_name, workflow = workflow_for_state(paths)
    state = paths.load_state()
    batch_id = state.get("batch", {}).get("batch_id")
    if not batch_id:
        return {"mode": mode_name, "workflow": workflow["label"], "batch_id": None,
                "last_event": None, "resume_command": None, "rollback_to": None,
                "preserve": [], "invalidate": [],
                "notes": ["no_active_batch; create_or_select_a_batch_before_run"]}
    batch_id = str(batch_id)
    selection_mode = 'resume_valid_evidence'
    plan_path = paths.workspace_root / 'SCHEDULER_PLAN.json'
    if plan_path.is_file():
        try:
            saved_plan = json.loads(plan_path.read_text(encoding='utf-8'))
            plan_body = saved_plan.get('plan') or {}
            if (saved_plan.get('plan_digest') == digest(plan_body)
                    and plan_body.get('batch_id') == batch_id
                    and plan_body.get('mode') == mode_name
                    and plan_body.get('project_key') == paths.registered_scope()[0]
                    and plan_body.get('selection_mode') in ('blind_fresh', 'resume_valid_evidence')):
                selection_mode = plan_body['selection_mode']
        except (OSError, ValueError, TypeError):
            pass
    run_key = digest({"project": paths.registered_scope()[0], "batch_id": batch_id,
                      "mode": mode_name})[:24]
    record_path = paths.project_records_dir / "scheduler_runs" / f"{run_key}.json"
    events = json.loads(record_path.read_text()).get("events", []) if record_path.is_file() else []
    last = events[-1] if events else None
    result = {"mode": mode_name, "workflow": workflow["label"], "batch_id": batch_id,
              "last_event": last, "resume_command": None, "rollback_to": None,
              "preserve": [], "invalidate": [], "notes": []}
    failure_card = failure_repair_card(last)
    if mode_name == "taobao_legacy":
        # An append may have changed Resolve even when its checkpoint says WRITTEN.
        # Never recommend a normal run while readback remains unresolved.
        delivery_dir = paths.cache_dir / "resolve_delivery" / batch_id
        pending = []
        for tx_path in sorted(delivery_dir.glob("resolve_append_transaction_*.json")):
            transaction = json.loads(tx_path.read_text(encoding="utf-8"))
            if transaction.get("status") != "READBACK_VERIFIED":
                pending.append({"transaction": str(tx_path),
                                "status": transaction.get("status"),
                                "products": transaction.get("products", [])})
        if pending:
            result.update(resume_required=True, resume_command=None,
                          rollback_to="Resolve 追加事务只读核对与回读恢复",
                          preserve=["已追加音视频", "项目备份", "事务与审核证据"],
                          invalidate=["禁止重复追加或清空目标时间线"],
                          pending_delivery_transactions=pending)
            result["notes"].append("unverified_append_transaction; normal run is not a recovery command")
            return result
        prepare_tx = (paths.cache_dir / "resolve_delivery" / batch_id
                      / "reusable_test_prepare_transaction.json")
        if prepare_tx.is_file():
            transaction = json.loads(prepare_tx.read_text(encoding="utf-8"))
            if transaction.get("status") == "CLEAR_IN_PROGRESS":
                result.update(resume_command=None,
                              rollback_to="复用测试时间线清理事务只读诊断",
                              preserve=["清理前项目备份", "清理事务", "非目标时间线"],
                              invalidate=["禁止重复清理或追加"])
                result["notes"].append(
                    "reusable_test_prepare_interrupted; inspect the registered target by stable ID before retry")
                return result
        preview_tx = (paths.cache_dir / "resolve_semantic_preview" / batch_id
                      / "preview_transaction.json")
        if preview_tx.is_file():
            transaction = json.loads(preview_tx.read_text(encoding="utf-8"))
            if transaction.get("mode") == "manual_cutpoint_preview":
                status = transaction.get("status")
                if status == "APPENDED_PENDING_VERIFY":
                    result.update(resume_command=("taobao_scheduler.py run --operation "
                                                  "semantic-review-preview --preview-recover-markers"),
                                  rollback_to="人工修边预审事务回读/标记恢复",
                                  preserve=["已有项目备份", "预审事务", "已追加片段"],
                                  invalidate=["禁止重复追加片段"])
                    return result
                if status in ("STARTED", "APPEND_IN_PROGRESS", "APPENDED_PENDING_INSPECTION",
                              "FAILED_VERIFICATION"):
                    result.update(resume_command=None, rollback_to="先只读核对目标时间线与事务",
                                  preserve=["已有项目备份", "预审事务"],
                                  invalidate=["禁止自动重放预审追加"])
                    return result
                if status == "VERIFIED":
                    result["notes"].append("人工修边预审已回读通过；等待用户修改后只读对比基线。")
        from product_checkpoints import resume
        rows = resume(paths)
        product_diagnostics = {}
        for pid, row in rows.items():
            cursor_path = diagnose_cursor_path(paths, pid, selection_mode)
            cursor = json.loads(cursor_path.read_text()) if cursor_path.is_file() else {}
            completed = [name for name in ("candidate", "rough", "semantic_review", "selection_vision",
                                            "asr", "plan", "vision", "final_audit")
                         if cursor.get(name)]
            repair = cursor.get("audio_repair_request") or cursor.get("asr_edge_repair_request")
            product_diagnostics[str(pid)] = {
                "status": row["status"], "reason": row.get("reason"),
                "resume_required": row.get("resume_required", False),
                "last_reusable_stages": completed,
                "cursor_path": str(cursor_path),
                "selection_mode": selection_mode,
                "rollback_to": repair.get("rollback_to") if repair else None,
                "repair_reference": repair.get("repair_digest") if repair else None,
                "repair_attempts": cursor.get("repair_attempts", len(cursor.get('repair_history', []))),
                "evidence_recovery": cursor.get('evidence_recovery_request'),
                "pending_variant_recovery": cursor.get('pending_variant_recovery'),
            }
        result["products"] = product_diagnostics
        result["resume_command"] = "taobao_scheduler.py run（project_scheduler 按已验证 cursor/checkpoint 续跑）"
        repairable = [row for row in product_diagnostics.values()
                      if row["status"] in ("REPAIRABLE", "BLOCKED", "WAITING_EVIDENCE")]
        if repairable:
            result["rollback_to"] = "各商品诊断项给出的回退阶段；普通失败从最近有效阶段恢复"
            result["preserve"] = ["仍通过 load_stage 身份与依赖核验的上游阶段"]
            result["invalidate"] = ["失败阶段及其依赖的下游阶段；由 project_scheduler 依据依赖摘要重建"]
            result["notes"].append("先按失败模块修复；不要手动删除 cursor 或重用失效审核。")
        if any(row["status"] == "WRITTEN" for row in rows.values()):
            result["notes"].append("Resolve 已写入商品必须先核对事务与回读记录，不能重放追加。")
        if failure_card:
            result['repair_plan'] = failure_card
            if failure_card['status'] == 'WAITING_FOR_EVIDENCE':
                result.update(resume_required=True, resume_command=None,
                              rollback_to='补证后仅从范围匹配阶段继续',
                              preserve=failure_card['preserve'],
                              invalidate=failure_card['invalidate'])
                result['notes'].append('边界补证的一次性尝试已耗尽；相同输入不自动重试。')
        waiting = {pid: row['evidence_recovery'] for pid, row in product_diagnostics.items()
                   if row['status'] == 'WAITING_EVIDENCE' and row.get('evidence_recovery')}
        if waiting:
            result.update(resume_command=None, repair_plan={
                'failure_class': 'technical_evidence_incomplete', 'status': 'WAITING_FOR_EVIDENCE',
                'automatic_resume': False, 'products': waiting,
                'action_required': '当前预算内补证已执行；保留原图与成功证据，等待新证据或明确的新恢复预算，不重组选段、不重放写入。'})
        return result

    delivery = paths.workspace_root / "delivery"
    transaction = delivery / "RESOLVE_TRANSACTION.json"
    if transaction.is_file():
        tx = json.loads(transaction.read_text())
        status = tx.get("status")
        if status in ("WRITTEN", "APPEND_IN_PROGRESS", "CREATE_IN_PROGRESS") or str(status).startswith("APPEND_"):
            result.update(resume_command="dudu_scheduler.py run --operation recover",
                          rollback_to="Resolve 事务核对/回读", preserve=["已有事务日志", "项目备份"],
                          invalidate=["不自动重放任何写入操作"])
            return result
    if last and last.get("status") == "FAILED" and str(last.get("stage", "")).startswith("delivery:"):
        operation = last["stage"].split(":", 1)[1]
        safe_repeat = {"project-read", "assembly-task", "asr", "prepare", "check", "preflight", "status"}
        if operation in safe_repeat:
            result.update(resume_command=f"dudu_scheduler.py run --operation {operation}",
                          rollback_to=operation, preserve=["通过摘要和依赖核验的上游派生产物"],
                          invalidate=["该阶段失败输出及依赖它的后续审核/方案"])
        else:
            result.update(rollback_to="先只读检查 Resolve 事务和目标时间线",
                          preserve=["项目备份", "Resolve 事务日志"],
                          invalidate=["不自动重放写入；确认现场后再决定恢复操作"])
        return result
    result["notes"].append("没有可自动判定的失败阶段；需先查看 delivery 状态文件，再从最近有效模块继续。")
    return result


def diagnose(paths):
    result = _diagnose(paths)
    from project_task import load, snapshot
    if getattr(paths, "project_key", None) and load(paths) is not None:
        control = snapshot(paths)
        result["project_task"] = control
        if control["blockers"]:
            result["resume_command"] = None
            result["notes"].append("project_task_restrictions_or_unclosed_transactions; inspect STATUS.md")
    return result


def execute_recorded(paths, mode_name, stage_name, callback):
    """Write a small run ledger around one delegated stage for fault localization."""
    from project_task import guard
    guard(paths, "write" if "resolve" in stage_name or "delivery" in stage_name else "run")
    state = paths.load_state()
    batch_id = str(state.get("batch", {}).get("batch_id") or "unassigned")
    run_key = digest({"project": paths.registered_scope()[0], "batch_id": batch_id,
                      "mode": mode_name})[:24]
    record_path = paths.project_records_dir / "scheduler_runs" / f"{run_key}.json"
    with workspace_lock(paths):
        record = json.loads(record_path.read_text()) if record_path.is_file() else {
            "schema_version": 1, "run_key": run_key, "mode": mode_name,
            "batch_id": batch_id, "events": []}
        event = {"event_id": str(uuid.uuid4()), "stage": stage_name, "status": "RUNNING",
                 "started_at": datetime.now(timezone.utc).isoformat()}
        record["events"].append(event)
        atomic_json(record_path, record)
    started = time.perf_counter()
    try:
        return_code = callback()
        event.update({"status": "PASS" if return_code == 0 else "FAILED",
                      "return_code": return_code,
                      "elapsed_seconds": round(time.perf_counter() - started, 3),
                      "finished_at": datetime.now(timezone.utc).isoformat()})
        return return_code
    except (KeyboardInterrupt, SystemExit) as exc:
        event.update({"status": "INTERRUPTED", "error_type": type(exc).__name__,
                      "error": str(exc)[:500],
                      "elapsed_seconds": round(time.perf_counter() - started, 3),
                      "finished_at": datetime.now(timezone.utc).isoformat()})
        raise
    except Exception as exc:
        event.update({"status": "FAILED", "error_type": type(exc).__name__,
                      "error": str(exc)[:500],
                      "elapsed_seconds": round(time.perf_counter() - started, 3),
                      "finished_at": datetime.now(timezone.utc).isoformat()})
        raise
    except BaseException as exc:
        event.update({"status": "INTERRUPTED", "error_type": type(exc).__name__,
                      "error": str(exc)[:500],
                      "elapsed_seconds": round(time.perf_counter() - started, 3),
                      "finished_at": datetime.now(timezone.utc).isoformat()})
        raise
    finally:
        with workspace_lock(paths):
            record = json.loads(record_path.read_text())
            for index in range(len(record["events"]) - 1, -1, -1):
                if record["events"][index].get("event_id") == event["event_id"]:
                    record["events"][index] = event
                    break
            atomic_json(record_path, record)


def early_delivery_preflight(paths, scripts, target_timeline=None):
    """Fail before indexing/media work when project identity or write target is unavailable."""
    for doctor_name in ("project_doctor.py", "resolve_connection_doctor.py"):
        command = [sys.executable, str(scripts / doctor_name), "--root",
                   str(paths.repo_root), "--workspace", str(paths.workspace_root)]
        if doctor_name == "resolve_connection_doctor.py" and target_timeline:
            command.extend(["--target-timeline", target_timeline])
        try:
            child = subprocess.run(command, cwd=paths.repo_root, text=True, capture_output=True,
                                   timeout=30)
        except subprocess.TimeoutExpired:
            print(json.dumps({"scheduler": "taobao", "status": "BLOCKED",
                              "stage": "early_delivery_preflight",
                              "reason": doctor_name + "_timed_out"}, ensure_ascii=False))
            return 2
        if child.stdout:
            print(child.stdout, end="")
        if child.stderr:
            print(child.stderr, file=sys.stderr, end="")
        if child.returncode:
            print(json.dumps({"scheduler": "taobao", "status": "BLOCKED",
                              "stage": "early_delivery_preflight",
                              "reason": doctor_name + "_failed"}, ensure_ascii=False))
            return child.returncode
    return 0


def manual_cutpoint_preview_scope(paths, product_ids):
    """Return only products whose current stop is an ASR edge preview handoff."""
    from product_checkpoints import load_stage, resume
    rows = resume(paths)
    scope = []
    for pid in product_ids:
        row = rows[int(pid)]
        if (row.get("status") != "REPAIRABLE"
                or not str(row.get("reason") or "").startswith(
                    "asr_cutpoint_requires_manual_review:")):
            continue
        cursor_path = paths.project_records_dir / "cursors" / f"{pid}.json"
        if not cursor_path.is_file():
            raise SchedulerError("manual_cutpoint_preview_cursor_missing")
        cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
        rough = load_stage(paths, cursor.get("rough"), product_id=int(pid))
        variants = sorted({str(item["variant_id"])
                           for item in rough["body"].get("selected_ranges", [])})
        if not variants or not cursor.get("manual_cutpoint_preview_request"):
            raise SchedulerError("manual_cutpoint_preview_scope_invalid")
        scope.extend(f"{pid}:{variant}" for variant in variants)
    return scope


def _run(paths, *, detector=None, group_size=3, repair_limit=2, operation=None,
        recipe="delivery/RECIPE.json", approved_semantic_task=None,
        allow_test_reuse=False, allow_new_timeline=False, allow_vfr_approximate=False,
        target_timeline=None,
        blind_fresh=False,
        preview_variants=None, preview_check_only=False, preview_recover_markers=False,
        preview_rough_semantic=False,
        deliver_passed_only=True, delivery_product_id=None):
    _, mode_name, workflow = workflow_for_state(paths)
    require_current_plan(paths, mode_name, target_timeline, blind_fresh,
                         delivery_product_id)
    state = paths.load_state()
    delivery_binding = selected_delivery_binding(state, target_timeline)
    if target_timeline and mode_name != "taobao_legacy":
        raise SchedulerError("explicit_target_timeline_not_supported_for_project_mode:" + mode_name)
    if blind_fresh and mode_name != "taobao_legacy":
        raise SchedulerError("blind_fresh_selection_not_supported_for_project_mode:" + mode_name)
    if allow_test_reuse and not test_reuse_capability(mode_name):
        raise SchedulerError("test_timeline_delivery_not_integrated_for_project_mode:" + mode_name)
    scripts = paths.repo_root / "scripts"
    if mode_name == "taobao_legacy":
        if operation == "recover-append":
            state = paths.load_state()
            directory = paths.cache_dir / "resolve_delivery" / str(state["batch"]["batch_id"])
            pending = []
            for transaction in directory.glob("resolve_append_transaction_*.json"):
                row = json.loads(transaction.read_text(encoding="utf-8"))
                if row.get("status") == "WRITTEN":
                    scope = row.get("products")
                    if (not isinstance(scope, list) or not scope
                            or any(type(pid) is not int or pid not in state["batch"]["product_ids"]
                                   for pid in scope)):
                        raise SchedulerError("pending_append_scope_invalid")
                    pending.append(scope)
            if len(pending) != 1:
                raise SchedulerError("expected_one_pending_append_transaction")
            for doctor_name in ("project_doctor.py", "resolve_connection_doctor.py"):
                child = subprocess.run([sys.executable, str(scripts / doctor_name), "--root",
                                        str(paths.repo_root), "--workspace", str(paths.workspace_root)],
                                       cwd=paths.repo_root, text=True, capture_output=True)
                if child.stdout:
                    print(child.stdout, end="")
                if child.stderr:
                    print(child.stderr, file=sys.stderr, end="")
                if child.returncode:
                    return child.returncode
            command = [sys.executable, str(scripts / "resolve_write_v2.py"), "--root",
                       str(paths.repo_root), "--workspace", str(paths.workspace_root),
                       "--products", ",".join(map(str, pending[0])), "--recover-append"]
            def invoke_recovery():
                child = subprocess.run(command, cwd=paths.repo_root, text=True, capture_output=True)
                if child.stdout:
                    print(child.stdout, end="")
                if child.stderr:
                    print(child.stderr, file=sys.stderr, end="")
                return child.returncode
            return execute_recorded(paths, mode_name, "resolve_delivery_recovery", invoke_recovery)
        if operation == "repair-layout":
            product_ids = [int(pid) for pid in paths.load_state()["batch"]["product_ids"]]
            command = [sys.executable, str(scripts / "resolve_write_v2.py"),
                       "--root", str(paths.repo_root), "--workspace", str(paths.workspace_root),
                       "--products", ",".join(map(str, product_ids)), "--repair-layout"]
            for doctor_name in ("project_doctor.py", "resolve_connection_doctor.py"):
                doctor_command = [sys.executable, str(scripts / doctor_name), "--root",
                                  str(paths.repo_root), "--workspace", str(paths.workspace_root)]
                child = subprocess.run(doctor_command, cwd=paths.repo_root, text=True,
                                       capture_output=True)
                if child.stdout:
                    print(child.stdout, end="")
                if child.stderr:
                    print(child.stderr, file=sys.stderr, end="")
                if child.returncode:
                    print(json.dumps({"scheduler": "taobao", "status": "BLOCKED",
                                      "reason": doctor_name + "_failed"}, ensure_ascii=False))
                    return child.returncode

            def invoke_layout_repair():
                child = subprocess.run(command, cwd=paths.repo_root, text=True, capture_output=True)
                if child.stdout:
                    print(child.stdout, end="")
                if child.stderr:
                    print(child.stderr, file=sys.stderr, end="")
                return child.returncode

            return execute_recorded(paths, mode_name, "resolve_delivery_layout_repair", invoke_layout_repair)
        if operation is not None:
            if (operation not in ("semantic-review-preview", "manual-preview-review",
                                  "manual-preview-normalize-markers", "manual-preview-label-markers")
                    or (operation == "semantic-review-preview"
                        and not preview_variants and not preview_recover_markers)):
                raise SchedulerError("unsupported_taobao_operation_or_preview_scope_missing")
            for doctor_name in ("project_doctor.py", "resolve_connection_doctor.py"):
                doctor_command = [sys.executable, str(scripts / doctor_name), "--root",
                                  str(paths.repo_root), "--workspace", str(paths.workspace_root)]
                child = subprocess.run(doctor_command, cwd=paths.repo_root, text=True,
                                       capture_output=True)
                if child.stdout:
                    print(child.stdout, end="")
                if child.stderr:
                    print(child.stderr, file=sys.stderr, end="")
                if child.returncode:
                    print(json.dumps({"scheduler": "taobao", "status": "BLOCKED",
                                      "reason": doctor_name + "_failed"}, ensure_ascii=False))
                    return child.returncode
            command = [sys.executable, str(scripts / "resolve_semantic_preview.py"),
                       "--root", str(paths.repo_root), "--workspace", str(paths.workspace_root)]
            if operation == "manual-preview-normalize-markers":
                command.append("--normalize-manual-markers")
                if preview_check_only:
                    command.append("--check-only")
            elif operation == "manual-preview-review":
                command.append("--review-manual-edits")
            elif operation == "manual-preview-label-markers":
                command.append("--label-product-markers")
                if preview_check_only:
                    command.append("--check-only")
            elif preview_recover_markers:
                command.append("--recover-markers")
            else:
                command += ["--variants", preview_variants]
                if preview_rough_semantic:
                    command.append("--rough-semantic")
                if allow_vfr_approximate:
                    command.append("--allow-vfr-approximate")
                if preview_check_only:
                    command.append("--check-only")

            def invoke_preview():
                child = subprocess.run(command, cwd=paths.repo_root, text=True, capture_output=True)
                if child.stdout:
                    print(child.stdout, end="")
                if child.stderr:
                    print(child.stderr, file=sys.stderr, end="")
                return child.returncode

            return execute_recorded(paths, mode_name, "resolve_semantic_review_preview", invoke_preview)
        # Resolve project/target readiness is a prerequisite for spending time on
        # title matching, semantic selection, ASR, or vision. A closed app or
        # invalid per-run target must fail here, not after product analysis.
        if mode_name == "taobao_legacy":
            preflight_code = execute_recorded(
                paths, mode_name, "resolve_preflight",
                lambda: early_delivery_preflight(paths, scripts, target_timeline))
            if preflight_code:
                return preflight_code
            detector = resolve_person_detector(paths, detector)
            if delivery_binding["mode"] == "reusable_test_timeline":
                prepare_command = [sys.executable, str(scripts / "reusable_test_delivery.py"),
                                   "prepare", "--root", str(paths.repo_root),
                                   "--workspace", str(paths.workspace_root),
                                   "--target-timeline", delivery_binding["name"],
                                   "--target-timeline-id", delivery_binding["timeline_id"]]
                def invoke_prepare_before_analysis():
                    child = subprocess.run(prepare_command, cwd=paths.repo_root, text=True,
                                           capture_output=True)
                    if child.stdout:
                        print(child.stdout, end="")
                    if child.stderr:
                        print(child.stderr, file=sys.stderr, end="")
                    return child.returncode
                if execute_recorded(paths, mode_name,
                                    "resolve_delivery:test_timeline_prepare",
                                    invoke_prepare_before_analysis):
                    return 2
                from project_task import supersede_transactions_after_clear
                clear_tx = (paths.cache_dir / "resolve_delivery" /
                            str(paths.load_state()["batch"]["batch_id"]) /
                            "reusable_test_prepare_transaction.json")
                if clear_tx.is_file():
                    clear_record = json.loads(clear_tx.read_text(encoding="utf-8"))
                    # An already-empty timeline has a read-only readiness receipt,
                    # not a clear. It must never supersede earlier failures.
                    if clear_record.get("status") == "READBACK_VERIFIED":
                        supersede_transactions_after_clear(paths, delivery_binding["timeline_id"], clear_tx)

        # The scheduler owns input-index initialization.  These generators are
        # identity-bound and idempotent, so a retry validates or rebuilds the
        # current registered inputs before any product worker can run.
        for stage_name, script_name in (
            ("title_index", "index_titles.py"),
            ("subtitle_index", "index_subtitles.py"),
        ):
            command = [sys.executable, str(scripts / script_name),
                       "--root", str(paths.repo_root), "--workspace", str(paths.workspace_root)]

            def invoke_index(command=command):
                child = subprocess.run(command, cwd=paths.repo_root, text=True, capture_output=True)
                if child.stdout:
                    print(child.stdout, end="")
                if child.stderr:
                    print(child.stderr, file=sys.stderr, end="")
                return child.returncode

            if execute_recorded(paths, mode_name, stage_name, invoke_index):
                return 2

        # Publish an explicitly user-authorized semantic range manifest through
        # the registered range checkpoint API before selection.
        from registered_pipeline import inputs
        from product_checkpoints import current_range_path, load_stage, check_current_range
        from runtime_meta import CacheIdentityError
        titles, _ = inputs(paths)
        try:
            batch_product_ids = batch_product_scope(paths.load_state(), titles)
        except SchedulerError:
            print(json.dumps({"scheduler": "taobao", "status": "BLOCKED",
                              "reason": "batch_product_missing_from_current_titles",
                              "product_ids": paths.load_state().get('batch', {}).get('product_ids', [])},
                             ensure_ascii=False))
            return 2
        batch_manifest = paths.workspace_root / 'delivery' / 'SEMANTIC_RANGES.json'
        semantic_manifest = batch_manifest
        manifest_body = (json.loads(semantic_manifest.read_text(encoding='utf-8'))
                         if semantic_manifest.is_file() else None)
        declared_ids = set()
        if manifest_body is not None:
            declared_ids = {int(row['product_index']) for row in manifest_body.get('products', [])
                            if isinstance(row, dict) and type(row.get('product_index')) is int}
            declared_ids.update(int(row['product_index']) for row in manifest_body.get('confirmed_products', [])
                                if isinstance(row, dict) and type(row.get('product_index')) is int)
            if (manifest_body.get('batch_id') != paths.load_state()['batch']['batch_id']
                    or manifest_body.get('product_ids') != sorted(batch_product_ids)):
                declared_ids = set()
            if manifest_body.get('manifest_source') == 'codex_batch_match_v1':
                from runtime_meta import expected_identity
                if manifest_body.get('input_identity_digest') != digest(
                        expected_identity(paths, include_ranges=False)):
                    declared_ids = set()
        if declared_ids != batch_product_ids:
            def generate_range_manifest():
                from registered_pipeline import generate_batch_range_manifest
                manifest = generate_batch_range_manifest(paths, sorted(batch_product_ids))
                batch_manifest.parent.mkdir(parents=True, exist_ok=True)
                atomic_json(batch_manifest, manifest)
                print(json.dumps({'status': 'GENERATED', 'batch_product_count': len(batch_product_ids)},
                                 ensure_ascii=False))
                return 0
            if execute_recorded(paths, mode_name, 'batch_semantic_range_matching',
                                generate_range_manifest):
                return 2
            manifest_body = json.loads(batch_manifest.read_text(encoding='utf-8'))
        command = [sys.executable, str(scripts / "publish_semantic_ranges.py"),
                   "--root", str(paths.repo_root), "--workspace", str(paths.workspace_root),
                   "--manifest", str(semantic_manifest),
                   "--product-ids", ",".join(map(str, sorted(batch_product_ids)))]

        def invoke_semantic_ranges():
            child = subprocess.run(command, cwd=paths.repo_root, text=True, capture_output=True)
            if child.stdout:
                print(child.stdout, end="")
            if child.stderr:
                print(child.stderr, file=sys.stderr, end="")
            return child.returncode

        if execute_recorded(paths, mode_name, "semantic_range_publication", invoke_semantic_ranges):
            return 2
        remaining = []
        for pid in batch_product_ids:
            encoding_id = next(p["encoding_id"] for p in titles["products"]
                               if int(p["product_index"]) == pid)
            try:
                pointer_path = current_range_path(paths, pid, encoding_id)
                pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
                stage = load_stage(paths, pointer["reference"], product_id=pid, encoding_id=encoding_id)
                check_current_range(paths, pointer["reference"], stage)
            except (OSError, ValueError, KeyError, TypeError, CacheIdentityError):
                remaining.append(pid)
        missing = remaining
        if missing:
            print(json.dumps({"scheduler": "taobao", "status": "BLOCKED",
                              "reason": "semantic_range_manifest_missing_or_incomplete",
                              "product_ids": missing}, ensure_ascii=False))
            return 2
        command = [sys.executable, str(scripts / workflow["module"]),
                   "--root", str(paths.repo_root), "--workspace", str(paths.workspace_root),
                   "--group-size", str(group_size), "--repair-limit", str(repair_limit),
                   "--product-workers", "2",
                   *workflow["run_args"]]
        if deliver_passed_only:
            command.append("--allow-partial-delivery")
        if allow_vfr_approximate:
            command.append("--allow-vfr-approximate")
        if blind_fresh:
            command.append("--blind-fresh")
        if detector:
            command += ["--detector", detector]
        def invoke():
            child = subprocess.run(command, cwd=paths.repo_root, text=True, capture_output=True)
            if child.stdout:
                print(child.stdout, end="")
            if child.stderr:
                print(child.stderr, file=sys.stderr, end="")
            return child.returncode
        code = execute_recorded(paths, mode_name, "project_analysis_and_quality", invoke)
        if code:
            return code
        state = paths.load_state()
        product_ids = list(state["batch"]["product_ids"])
        if delivery_product_id is not None:
            if delivery_product_id not in product_ids:
                raise SchedulerError("delivery_product_outside_batch")
            product_ids = [delivery_product_id]
        if deliver_passed_only:
            from product_checkpoints import resume
            ledger = resume(paths)
            product_ids = [int(pid) for pid in product_ids
                           if ledger[int(pid)]["status"] == "FINAL_PASS"]
            if not product_ids:
                print(json.dumps({"scheduler": "taobao", "status": "BLOCKED",
                                  "reason": "no_final_pass_products_to_deliver"}, ensure_ascii=False))
                return 2
        authorization_path = paths.workspace_root / "delivery" / "USER_OVERRIDE.json"
        authorization_path.parent.mkdir(parents=True, exist_ok=True)
        authorization = {
            "scope": "this_batch_only", "confirmed_by_user": True,
            "resolve_write_authorization": {
                "action": "append_existing_target",
                "project_id": state["project_identity"]["resolve_project_id"],
                "target_timeline": delivery_binding["name"],
                "target_timeline_id": delivery_binding["timeline_id"],
                "product_ids": product_ids,
                "clear_existing": False,
            },
        }
        atomic_json(authorization_path, authorization)
        for doctor_name in ("project_doctor.py", "resolve_connection_doctor.py"):
            doctor_command = [sys.executable, str(scripts / doctor_name), "--root",
                              str(paths.repo_root), "--workspace", str(paths.workspace_root)]
            if doctor_name == "resolve_connection_doctor.py" and target_timeline:
                doctor_command += ["--target-timeline", delivery_binding["name"]]
            child = subprocess.run(doctor_command, cwd=paths.repo_root, text=True,
                                   capture_output=True)
            if child.stdout:
                print(child.stdout, end="")
            if child.stderr:
                print(child.stderr, file=sys.stderr, end="")
            if child.returncode:
                print(json.dumps({"scheduler": "taobao", "status": "BLOCKED",
                                  "reason": doctor_name + "_failed"}, ensure_ascii=False))
                return child.returncode
        delivery_command = [sys.executable, str(scripts / "resolve_write_v2.py"),
                            "--root", str(paths.repo_root), "--workspace", str(paths.workspace_root),
                            "--products", ",".join(str(x) for x in product_ids),
                            "--append-registered-target"]
        if target_timeline:
            delivery_command += ["--target-timeline", target_timeline]
        def invoke_delivery():
            child = subprocess.run(delivery_command, cwd=paths.repo_root, text=True, capture_output=True)
            if child.stdout:
                print(child.stdout, end="")
            if child.stderr:
                print(child.stderr, file=sys.stderr, end="")
            return child.returncode
        return execute_recorded(paths, mode_name, "resolve_delivery", invoke_delivery)

    if not operation:
        raise SchedulerError("dudu_operation_required_until_multi_product_adapter_is_complete")
    if operation not in {"project-read", "assembly-task", "asr", "prepare", "check", "preflight",
                         "deliver", "write", "recover", "readback", "status"}:
        raise SchedulerError("unsupported_dudu_delivery_operation")
    command = [sys.executable, str(scripts / workflow["module"]), operation,
               "--root", str(paths.repo_root), "--workspace", str(paths.workspace_root),
               "--recipe", recipe]
    if detector:
        command += ["--detector", detector]
    if approved_semantic_task:
        command += ["--approved-semantic-task", approved_semantic_task]
    if allow_test_reuse:
        command.append("--allow-test-reuse")
    if allow_new_timeline:
        command.append("--allow-new-timeline")
    def invoke():
        child = subprocess.run(command, cwd=paths.repo_root, text=True, capture_output=True)
        if child.stdout:
            print(child.stdout, end="")
        if child.stderr:
            print(child.stderr, file=sys.stderr, end="")
        return child.returncode
    return execute_recorded(paths, mode_name, "delivery:" + operation, invoke)


def run(paths, **kwargs):
    from project_task import begin_run, finish_run, refresh, load
    _, mode_name, _ = workflow_for_state(paths)
    delivery_product_id = kwargs.get("delivery_product_id")
    require_current_plan(paths, mode_name, kwargs.get("target_timeline"),
                         kwargs.get("blind_fresh", False), delivery_product_id)
    target_id = selected_delivery_binding(paths.load_state(), kwargs.get("target_timeline"))["timeline_id"]
    run_id = begin_run(paths, kwargs.get("operation") or "default", kwargs.get("repair_limit", 2),
                       target_timeline_id=target_id)
    try:
        result = _run(paths, **kwargs)
    except (KeyboardInterrupt, SystemExit) as exc:
        finish_run(paths, run_id, None, interrupted=True, reason=type(exc).__name__)
        raise
    except Exception as exc:
        finish_run(paths, run_id, 1, interrupted=False, reason=type(exc).__name__)
        raise
    except BaseException as exc:
        finish_run(paths, run_id, None, interrupted=True, reason=type(exc).__name__)
        raise
    else:
        finish_run(paths, run_id, result)
        return result
    finally:
        if run_id is None and load(paths) is not None:
            refresh(paths)


def main(expected_mode=None):
    # Resolve's launched Python can inherit an ASCII locale; child workers and
    # receipt readers must decode Chinese subtitle evidence as UTF-8.
    os.environ.setdefault("PYTHONUTF8", "1")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run", "diagnose"))
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--target-timeline",
                        help="本次明确指定的 Resolve 目标时间线名称；必须与 plan 阶段一致")
    parser.add_argument("--blind-fresh", action="store_true",
                        help="忽略该商品的项目级历史游标，在此批次重新执行语义选择")
    parser.add_argument("--detector")
    parser.add_argument("--group-size", type=int, default=3)
    parser.add_argument("--repair-limit", type=int, default=2)
    parser.add_argument("--operation")
    parser.add_argument("--preview-variants",
                        help="Review-only append scope, e.g. 1:variant_1,3:variant_2,3:variant_3")
    parser.add_argument("--preview-check-only", action="store_true",
                        help="Read-only Resolve preflight for a semantic review preview")
    parser.add_argument("--preview-recover-markers", action="store_true",
                        help="Recover markers/readback from a pending preview append; never reappend clips")
    parser.add_argument("--preview-rough-semantic", action="store_true",
                        help="Mark semantic PASS variants for manual cutpoint repair after local ASR failure")
    parser.add_argument("--recipe", default="delivery/RECIPE.json")
    parser.add_argument("--approved-semantic-task")
    parser.add_argument("--allow-test-reuse", action="store_true")
    parser.add_argument("--allow-new-timeline", action="store_true")
    parser.add_argument("--allow-vfr-approximate", action="store_true", default=True,
                        help="Use the project's default average-rate source frame mapping for VFR media")
    parser.add_argument("--deliver-passed-only", action="store_true", default=True,
                        help="Audit and append only products already passing final review; keep blocked products pending")
    parser.add_argument("--delivery-product-id", type=int,
                        help="并行处理整批商品，但仅将指定商品交付到目标时间线")
    args = parser.parse_args()
    paths = resolve_runtime_paths(args.root, args.workspace)
    try:
        _, mode_name, workflow = workflow_for_state(paths)
        if expected_mode and mode_name != expected_mode:
            raise SchedulerError("scheduler_project_mode_mismatch:" + expected_mode)
        if args.command == "plan":
            print(json.dumps(persist_plan(paths, args.target_timeline, args.blind_fresh,
                                          args.delivery_product_id),
                             ensure_ascii=False, indent=2))
            return 0
        if args.command == "diagnose":
            print(json.dumps(diagnose(paths), ensure_ascii=False, indent=2))
            return 0
        try:
            return run(paths, detector=args.detector, group_size=args.group_size,
                       repair_limit=args.repair_limit, operation=args.operation, recipe=args.recipe,
                       approved_semantic_task=args.approved_semantic_task,
                       allow_test_reuse=args.allow_test_reuse,
                       allow_new_timeline=args.allow_new_timeline,
                       target_timeline=args.target_timeline,
                       blind_fresh=args.blind_fresh,
                       allow_vfr_approximate=args.allow_vfr_approximate,
                       preview_variants=args.preview_variants,
                       preview_check_only=args.preview_check_only,
                       preview_recover_markers=args.preview_recover_markers,
                       preview_rough_semantic=args.preview_rough_semantic,
                       deliver_passed_only=args.deliver_passed_only,
                       delivery_product_id=args.delivery_product_id)
        finally:
            from asr_runtime import close_worker
            close_worker()
    except (SchedulerError, ValueError, OSError) as exc:
        print(json.dumps({"scheduler_error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
