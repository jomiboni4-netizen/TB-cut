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
        "stages": ["registration", "title_and_subtitle_index", "semantic_ranges",
                   "codex_selection", "codex_semantic_final_review", "local_asr", "physical_plan", "person_audit",
                   "final_quality_gate", "resolve_delivery"],
        "implemented_through": "resolve_delivery",
        "run_args": ["--mode", "codex"],
    },
    "dudu_marked_range": {
        "label": "都督",
        "module": "delivery_pipeline.py",
        "stages": ["registration", "resolve_preflight", "marked_ranges",
                   "codex_selection", "local_asr", "waveform_cutpoints",
                   "semantic_and_person_gates", "resolve_write_readback"],
        "implemented_through": "semantic_and_person_gates",
        "run_args": [],
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


def plan(paths):
    state, mode_name, workflow = workflow_for_state(paths)
    return {
        "schema_version": 1,
        "mode": mode_name,
        "workflow": workflow["label"],
        "project_key": (state.get("project_identity") or {}).get("project_key"),
        "registration_digest": (state.get("project_identity") or {}).get("config_digest"),
        "resolve_project_id": (state.get("project_identity") or {}).get("resolve_project_id"),
        "project_name": state["resolve"]["project_name"],
        "source_timeline_id": state["resolve"].get("source_timeline_id"),
        "target_timeline_id": state["resolve"].get("target_timeline_id"),
        "batch_id": state.get("batch", {}).get("batch_id"),
        "products": state.get("batch", {}).get("product_ids", []),
        "stages": workflow["stages"],
        "implemented_through": workflow["implemented_through"],
        "delivery_stage": workflow["stages"][-1],
        "delivery_ready": workflow["implemented_through"] == workflow["stages"][-1],
    }


def persist_plan(paths):
    body = plan(paths)
    record = {"schema_version": 1, "plan": body, "plan_digest": digest(body)}
    from project_registry import atomic_json
    atomic_json(paths.workspace_root / "SCHEDULER_PLAN.json", record)
    return record


def require_current_plan(paths, expected_mode):
    path = paths.workspace_root / "SCHEDULER_PLAN.json"
    if not path.is_file():
        raise SchedulerError("scheduler_plan_required_run_plan_first")
    record = json.loads(path.read_text(encoding="utf-8"))
    current = plan(paths)
    if (record.get("schema_version") != 1 or record.get("plan") != current
            or record.get("plan_digest") != digest(current)
            or current.get("mode") != expected_mode):
        raise SchedulerError("scheduler_plan_stale_or_mode_mismatch_rerun_plan")


def diagnose(paths):
    """Describe the latest checkpoint and a conservative resume/rollback point."""
    _, mode_name, workflow = workflow_for_state(paths)
    batch_id = str(paths.load_state().get("batch", {}).get("batch_id") or "unassigned")
    run_key = digest({"project": paths.registered_scope()[0], "batch_id": batch_id,
                      "mode": mode_name})[:24]
    record_path = paths.project_records_dir / "scheduler_runs" / f"{run_key}.json"
    events = json.loads(record_path.read_text()).get("events", []) if record_path.is_file() else []
    last = events[-1] if events else None
    result = {"mode": mode_name, "workflow": workflow["label"], "batch_id": batch_id,
              "last_event": last, "resume_command": None, "rollback_to": None,
              "preserve": [], "invalidate": [], "notes": []}
    if mode_name == "taobao_legacy":
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
            cursor_path = paths.project_records_dir / "cursors" / f"{pid}.json"
            cursor = json.loads(cursor_path.read_text()) if cursor_path.is_file() else {}
            completed = [name for name in ("candidate", "rough", "asr", "plan", "vision", "final_audit")
                         if cursor.get(name)]
            repair = cursor.get("audio_repair_request") or cursor.get("asr_edge_repair_request")
            product_diagnostics[str(pid)] = {
                "status": row["status"], "reason": row.get("reason"),
                "resume_required": row.get("resume_required", False),
                "last_reusable_stages": completed,
                "rollback_to": repair.get("rollback_to") if repair else None,
                "repair_reference": repair.get("repair_digest") if repair else None,
                "repair_attempts": cursor.get("repair_attempts", 0),
            }
        result["products"] = product_diagnostics
        result["resume_command"] = "taobao_scheduler.py run（project_scheduler 按已验证 cursor/checkpoint 续跑）"
        repairable = [row for row in product_diagnostics.values()
                      if row["status"] in ("REPAIRABLE", "BLOCKED")]
        if repairable:
            result["rollback_to"] = "各商品诊断项给出的回退阶段；普通失败从最近有效阶段恢复"
            result["preserve"] = ["仍通过 load_stage 身份与依赖核验的上游阶段"]
            result["invalidate"] = ["失败阶段及其依赖的下游阶段；由 project_scheduler 依据依赖摘要重建"]
            result["notes"].append("先按失败模块修复；不要手动删除 cursor 或重用失效审核。")
        if any(row["status"] == "WRITTEN" for row in rows.values()):
            result["notes"].append("Resolve 已写入商品必须先核对事务与回读记录，不能重放追加。")
        return result

    delivery = paths.workspace_root / "delivery"
    transaction = delivery / "RESOLVE_TRANSACTION.json"
    if transaction.is_file():
        tx = json.loads(transaction.read_text())
        status = tx.get("status")
        if status in ("APPENDED_PENDING_VERIFY", "APPEND_IN_PROGRESS", "CREATE_IN_PROGRESS") or str(status).startswith("APPEND_"):
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


def execute_recorded(paths, mode_name, stage_name, callback):
    """Write a small run ledger around one delegated stage for fault localization."""
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
    except BaseException as exc:
        event.update({"status": "FAILED", "error_type": type(exc).__name__,
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


def run(paths, *, detector=None, group_size=3, repair_limit=2, operation=None,
        recipe="delivery/RECIPE.json", approved_semantic_task=None,
        allow_test_reuse=False, allow_new_timeline=False, allow_vfr_approximate=False,
        preview_variants=None, preview_check_only=False, preview_recover_markers=False,
        preview_rough_semantic=False,
        deliver_passed_only=False):
    _, mode_name, workflow = workflow_for_state(paths)
    require_current_plan(paths, mode_name)
    scripts = paths.repo_root / "scripts"
    if mode_name == "taobao_legacy":
        detector = resolve_person_detector(paths, detector)
    if mode_name == "taobao_legacy":
        if operation == "recover-append":
            state = paths.load_state()
            directory = paths.cache_dir / "resolve_delivery" / str(state["batch"]["batch_id"])
            pending = []
            for transaction in directory.glob("resolve_append_transaction_*.json"):
                row = json.loads(transaction.read_text(encoding="utf-8"))
                if row.get("status") == "APPENDED_PENDING_VERIFY":
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
        missing = []
        for product_id in paths.load_state()["batch"]["product_ids"]:
            row = next((p for p in titles["products"]
                        if int(p["product_index"]) == int(product_id)), None)
            if row is None:
                print(json.dumps({"scheduler": "taobao", "status": "BLOCKED",
                                  "reason": "batch_product_missing_from_current_titles",
                                  "product_id": product_id}, ensure_ascii=False))
                return 2
            pointer_path = current_range_path(paths, int(product_id), row["encoding_id"])
            valid_range = False
            try:
                pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
                stage = load_stage(paths, pointer["reference"], product_id=int(product_id),
                                   encoding_id=row["encoding_id"])
                check_current_range(paths, pointer["reference"], stage)
                valid_range = True
            except (OSError, ValueError, KeyError, TypeError, CacheIdentityError):
                valid_range = False
            if not valid_range:
                missing.append(int(product_id))
        semantic_manifest = paths.workspace_root / "delivery" / "SEMANTIC_RANGES.json"
        # A changed user-authorized manifest may refine already-published
        # boundaries. Re-publish it on every scheduler run; stage publication is
        # content-addressed and downstream stages will resume from the updated
        # range identity when it changes.
        if semantic_manifest.is_file():
            command = [sys.executable, str(scripts / "publish_semantic_ranges.py"),
                       "--root", str(paths.repo_root), "--workspace", str(paths.workspace_root),
                       "--manifest", str(semantic_manifest)]

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
            for pid in missing:
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
                   *workflow["run_args"]]
        if deliver_passed_only:
            command.append("--allow-partial-delivery")
        if allow_vfr_approximate:
            command.append("--allow-vfr-approximate")
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
                "target_timeline": state["resolve"]["target_timeline"],
                "target_timeline_id": state["resolve"]["target_timeline_id"],
                "product_ids": product_ids,
                "clear_existing": False,
            },
        }
        atomic_json(authorization_path, authorization)
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
        delivery_command = [sys.executable, str(scripts / "resolve_write_v2.py"),
                            "--root", str(paths.repo_root), "--workspace", str(paths.workspace_root),
                            "--products", ",".join(str(x) for x in product_ids),
                            "--append-registered-target"]
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


def main(expected_mode=None):
    # Resolve's launched Python can inherit an ASCII locale; child workers and
    # receipt readers must decode Chinese subtitle evidence as UTF-8.
    os.environ.setdefault("PYTHONUTF8", "1")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run", "diagnose"))
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace", required=True)
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
    parser.add_argument("--deliver-passed-only", action="store_true",
                        help="Audit and append only products already passing final review; keep blocked products pending")
    args = parser.parse_args()
    paths = resolve_runtime_paths(args.root, args.workspace)
    try:
        _, mode_name, workflow = workflow_for_state(paths)
        if expected_mode and mode_name != expected_mode:
            raise SchedulerError("scheduler_project_mode_mismatch:" + expected_mode)
        if args.command == "plan":
            print(json.dumps(persist_plan(paths), ensure_ascii=False, indent=2))
            return 0
        if args.command == "diagnose":
            print(json.dumps(diagnose(paths), ensure_ascii=False, indent=2))
            return 0
        return run(paths, detector=args.detector, group_size=args.group_size,
                   repair_limit=args.repair_limit, operation=args.operation, recipe=args.recipe,
                   approved_semantic_task=args.approved_semantic_task,
                   allow_test_reuse=args.allow_test_reuse,
                   allow_new_timeline=args.allow_new_timeline,
                   allow_vfr_approximate=args.allow_vfr_approximate,
                   preview_variants=args.preview_variants,
                   preview_check_only=args.preview_check_only,
                   preview_recover_markers=args.preview_recover_markers,
                   preview_rough_semantic=args.preview_rough_semantic,
                   deliver_passed_only=args.deliver_passed_only)
    except (SchedulerError, ValueError, OSError) as exc:
        print(json.dumps({"scheduler_error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
