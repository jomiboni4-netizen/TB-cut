#!/usr/bin/env python3
"""Append explicitly reviewed semantic-pass variants for inspection only."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import zipfile

from coldstart_resolve import snapshot as resolve_snapshot
from codex_worker import verify_receipt
from final_quality_gate import final_task, plan_errors, semantic_selection_task
from product_checkpoints import load_stage, resume
from project_registry import atomic_json, digest, workspace_lock
from resolve_connection import connect
from resolve_write_v2 import item_row, snapshot_preserved, validate_written_rows
from resolve_trial import stable_id
from runtime_meta import CacheIdentityError
from runtime_paths import resolve_runtime_paths


def parse_variants(value: str) -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    for item in value.split(","):
        product, separator, variant = item.partition(":")
        if not separator or not product.isdigit() or not variant.startswith("variant_"):
            raise ValueError("preview_variant_format_invalid")
        pid = int(product)
        result.setdefault(pid, []).append(variant)
    if not result or any(len(rows) != len(set(rows)) for rows in result.values()):
        raise ValueError("preview_variant_set_invalid")
    return result


def load_preview_products(paths, variants: dict[int, list[str]]):
    state = paths.load_state()
    if set(variants) - {int(value) for value in state["batch"]["product_ids"]}:
        raise CacheIdentityError("preview_product_outside_current_batch")
    records = paths.project_records_dir
    products = []
    evidence = []
    for pid, wanted in sorted(variants.items()):
        cursor_path = records / "cursors" / f"{pid}.json"
        if not cursor_path.is_file():
            raise CacheIdentityError(f"preview_cursor_missing:{pid}")
        cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
        plan_ref = cursor.get("plan")
        if not plan_ref:
            raise CacheIdentityError(f"preview_plan_missing:{pid}")
        plan_record = load_stage(paths, plan_ref, product_id=pid)
        plan = plan_record["body"]
        available = {row["variant_id"]: row for row in plan["variants"]}
        if not set(wanted) <= set(available):
            raise CacheIdentityError(f"preview_variant_missing:{pid}")
        subset = deepcopy(plan)
        subset["variants"] = [deepcopy(available[variant]) for variant in wanted]
        errors = plan_errors(subset)
        if errors:
            raise CacheIdentityError(f"preview_local_gate_failed:{pid}:{','.join(errors)}")

        task = final_task(paths, plan_ref)
        receipt_path = records / "worker_receipts" / f"{task['task_digest']}.json"
        if not receipt_path.is_file():
            raise CacheIdentityError(f"preview_semantic_receipt_missing:{pid}")
        receipt = verify_receipt(paths, paths.reference(receipt_path), task)
        result = receipt["result"]
        if result.get("verdict") != "PASS" or result.get("issues") or not all(
                result.get("checks", {}).values()):
            raise CacheIdentityError(f"preview_semantic_receipt_invalid:{pid}")
        products.append({"product_index": pid, "encoding_id": plan_record["identity"]["encoding_id"],
                         "plans": subset["variants"]})
        evidence.append({"product_index": pid, "plan_reference": plan_ref,
                         "plan_digest": plan_record["body_digest"],
                         "review_task_digest": task["task_digest"],
                         "review_receipt": paths.reference(receipt_path),
                         "review_verdict": result["verdict"],
                         "preview_variants": wanted,
                         "subset_local_errors": errors})
    return state, products, evidence


ASR_CUTPOINT_REASONS = (
    "sentence_", "asr_cut_", "word_alignment_", "overlapping_asr_tokens_",
    "audio_cutpoint_", "audio_boundary_", "vfr_exact_mapping_not_integrated",
)


def load_rough_preview_products(paths, variants: dict[int, list[str]], *, allow_vfr_approximate=False):
    """Build a marked manual draft from current semantic PASS, without granting ASR approval."""
    from audit_mimo_v2 import FORBIDDEN as FINAL_FORBIDDEN, semantic_duplicate
    from semantic_evidence import FORBIDDEN as SOURCE_FORBIDDEN
    from registered_pipeline import inputs, source_clock
    from project_scheduler import selection_overlap_issues

    state = paths.load_state()
    if set(variants) - {int(value) for value in state["batch"]["product_ids"]}:
        raise CacheIdentityError("preview_product_outside_current_batch")
    _, index = inputs(paths)
    sources = {Path(row["video_path"]).name: row for row in index["sources"]}
    clocks = {}
    products, evidence, occupied = [], [], []
    for pid, wanted in sorted(variants.items()):
        status = resume(paths, [pid])[pid]
        reason = str(status.get("reason") or "")
        if status.get("status") != "BLOCKED" or not reason.startswith(ASR_CUTPOINT_REASONS):
            raise CacheIdentityError(f"preview_requires_semantic_pass_asr_cutpoint_failure:{pid}:{reason}")
        cursor_path = paths.project_records_dir / "cursors" / f"{pid}.json"
        if not cursor_path.is_file():
            raise CacheIdentityError(f"preview_cursor_missing:{pid}")
        cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
        if cursor.get("plan") or not all(cursor.get(name) for name in ("rough", "semantic_review", "asr")):
            raise CacheIdentityError(f"preview_pre_asr_stage_set_invalid:{pid}")
        rough_ref, semantic_ref = cursor["rough"], cursor["semantic_review"]
        rough = load_stage(paths, rough_ref, product_id=pid)
        semantic = load_stage(paths, semantic_ref, product_id=pid,
                              encoding_id=rough["identity"]["encoding_id"])
        load_stage(paths, cursor["asr"], product_id=pid)
        task = semantic_selection_task(paths, rough_ref)
        body = semantic["body"]
        if (semantic["identity"]["stage"] != "semantic_review" or body.get("verdict") != "PASS"
                or body.get("rough_reference") != rough_ref or body.get("rough_digest") != rough["body_digest"]
                or body.get("task") != task or rough_ref not in
                {dep["path"] for dep in semantic["identity"]["dependencies"]}):
            raise CacheIdentityError(f"preview_semantic_stage_not_current:{pid}")
        review = verify_receipt(paths, body["receipt"], task)["result"]
        if (review.get("verdict") != "PASS" or review.get("issues")
                or set(review.get("checks", {})) !=
                {"opening", "ending", "causal_logic", "seams", "selling_value", "duplicates"}
                or not all(review["checks"].values())):
            raise CacheIdentityError(f"preview_semantic_receipt_not_passed:{pid}")
        available = {row["variant_id"]: row for row in task["variants"]}
        if not set(wanted) <= set(available):
            raise CacheIdentityError(f"preview_variant_missing:{pid}")
        candidate_ref = rough["identity"]["dependencies"][0]["path"]
        candidate = load_stage(paths, candidate_ref, product_id=pid)
        if selection_overlap_issues(paths, candidate_ref, rough["body"]["selected_ranges"]):
            raise CacheIdentityError(f"preview_source_overlap:{pid}")
        range_ref = candidate["identity"]["dependencies"][0]["path"]
        confirmed = load_stage(paths, range_ref, product_id=pid)["body"]["ranges"]
        by_candidate = {row["candidate_id"]: row for row in candidate["body"]["candidates"]}
        plans = []
        for variant_id in wanted:
            semantic_variant = available[variant_id]
            clips, total = [], 0.0
            for row in semantic_variant["clips"]:
                source = by_candidate.get(row["candidate_id"])
                source_name = row["source_file"]
                if not source or source["source_file"] != source_name or source_name not in sources:
                    raise CacheIdentityError(f"preview_candidate_source_mismatch:{pid}")
                start, end = float(row["start"]), float(row["end"])
                text = str(row.get("source_subtitle_text") or "")
                if (not math.isfinite(start) or not math.isfinite(end) or end <= start
                        or not text or FINAL_FORBIDDEN.search(text) or SOURCE_FORBIDDEN.search(text)):
                    raise CacheIdentityError(f"preview_bad_or_forbidden_semantic_clip:{pid}:{variant_id}")
                if not any(part["media_stem"] + ".mp4" == source_name
                           and float(part["start"]) <= start < end <= float(part["end"])
                           for part in confirmed):
                    raise CacheIdentityError(f"preview_clip_outside_confirmed_range:{pid}")
                if source_name not in clocks:
                    clocks[source_name] = source_clock(Path(sources[source_name]["video_path"]), paths,
                                                       allow_vfr_approximate=allow_vfr_approximate)
                clock = clocks[source_name]
                fps = float(clock.get("average_fps", clock["fps"]) if allow_vfr_approximate else clock["fps"])
                first, last = round(start * fps), round(end * fps)
                if first < 0 or last <= first or last > int(clock["frame_count"]):
                    raise CacheIdentityError(f"preview_source_frame_bounds_invalid:{pid}")
                occupied.append((source_name, first, last, pid, variant_id))
                clips.append({"source_file": source_name, "source_start_frame": first,
                              "source_end_frame": last, "audio_start_frame": first,
                              "audio_end_frame": last, "fps": fps,
                              "duration_sec": (last - first) / fps,
                              "asr_cutpoint_status": "FAILED_MANUAL_REPAIR_REQUIRED"})
                total += (last - first) / fps
            if not 45 < total < 60:
                raise CacheIdentityError(f"preview_duration_outside_project_rule:{pid}:{variant_id}")
            plans.append({"variant_id": variant_id, "clips": clips, "duration_seconds": total})
        for left_index, left in enumerate(wanted):
            for right in wanted[left_index + 1:]:
                left_text = "".join(str(row.get("source_subtitle_text") or "")
                                    for row in available[left]["clips"])
                right_text = "".join(str(row.get("source_subtitle_text") or "")
                                     for row in available[right]["clips"])
                if semantic_duplicate(left_text, right_text):
                    raise CacheIdentityError(f"preview_semantic_duplicate:{pid}:{left}:{right}")
        products.append({"product_index": pid, "encoding_id": rough["identity"]["encoding_id"],
                         "plans": plans})
        evidence.append({"product_index": pid, "rough_reference": rough_ref,
                         "rough_digest": rough["body_digest"], "semantic_reference": semantic_ref,
                         "semantic_receipt": body["receipt"], "semantic_task_digest": task["task_digest"],
                         "asr_reference": cursor["asr"], "asr_failure": reason,
                         "preview_variants": wanted, "manual_cutpoint_repair_required": True})
    for index, left in enumerate(occupied):
        for right in occupied[index + 1:]:
            if left[0] == right[0] and min(left[2], right[2]) > max(left[1], right[1]):
                raise CacheIdentityError("preview_cross_product_or_variant_source_overlap")
    return state, products, evidence


def execute(paths, variants: dict[int, list[str]], *, check_only=False,
            allow_vfr_approximate=False, rough_semantic=False):
    state, products, evidence = (load_rough_preview_products(
        paths, variants, allow_vfr_approximate=allow_vfr_approximate)
        if rough_semantic else load_preview_products(paths, variants))
    project_config = state["resolve"]
    resolve = connect(status_path=paths.workspace_root / "resolve_connection_status.json")
    if not resolve:
        raise SystemExit("preview_resolve_connection_failed")
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject() if manager else None
    if (not project or project.GetName() != project_config["project_name"]
            or stable_id(project) != state["project_identity"]["resolve_project_id"]):
        raise SystemExit("preview_project_identity_mismatch")
    timelines = [project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)]
    by_id = {stable_id(timeline): timeline for timeline in timelines}
    source_id = project_config.get("source_timeline_id")
    target_id = project_config.get("target_timeline_id")
    source, target = by_id.get(source_id), by_id.get(target_id)
    if (not source or not target or source_id == target_id
            or source.GetName() != project_config["source_timeline"]
            or target.GetName() != project_config["target_timeline"]):
        raise SystemExit("preview_timeline_identity_mismatch")
    if not target.GetTrackCount("video") or not target.GetTrackCount("audio"):
        raise SystemExit("preview_target_missing_audio_or_video_track")

    source_media = {}
    media_fps = {}
    for item in source.GetItemListInTrack("video", 1) or []:
        media = item.GetMediaPoolItem()
        if not media:
            continue
        name = media.GetName()
        if name in source_media and source_media[name].GetClipProperty("File Path") != media.GetClipProperty("File Path"):
            raise SystemExit("preview_ambiguous_same_name_source_media")
        source_media[name] = media
        try:
            media_fps[name] = float((media.GetClipProperty() or {}).get("FPS") or 0)
        except (TypeError, ValueError):
            media_fps[name] = 0.0

    batch_id = str(state["batch"]["batch_id"])
    output_dir = paths.cache_dir / "resolve_semantic_preview" / batch_id
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "preview_report.json"
    transaction_path = output_dir / "preview_transaction.json"
    if report_path.exists() or transaction_path.exists():
        raise SystemExit("preview_transaction_exists_inspect_before_retry")

    before = {stable_id(timeline): resolve_snapshot(timeline) for timeline in timelines}
    old_video = list(target.GetItemListInTrack("video", 1) or [])
    old_audio = list(target.GetItemListInTrack("audio", 1) or [])
    project_fps = float(project.GetSetting("timelineFrameRate") or project.GetSetting("timelinePlaybackFrameRate") or 30)
    gap_frames = round(2.0 * project_fps)
    end_positions = [int(item.GetEnd()) for kind in ("video", "audio")
                     for track in range(1, int(target.GetTrackCount(kind)) + 1)
                     for item in (target.GetItemListInTrack(kind, track) or [])]
    record = max(end_positions) + gap_frames if end_positions else int(target.GetStartFrame())
    append_infos, expected, markers = [], [], []
    selected_total = sum(len(product["plans"]) for product in products)
    for product in products:
        product_start = record
        for variant_index, variant in enumerate(product["plans"], 1):
            variant_start = record
            for clip in variant["clips"]:
                media_name = clip["source_file"]
                if media_name not in source_media:
                    raise SystemExit("preview_source_media_missing_from_source_timeline")
                clip_fps = float(clip["fps"])
                native_fps = media_fps.get(media_name) or 0.0
                if (not native_fps or abs(native_fps - project_fps) > 1e-6
                        or (abs(clip_fps - project_fps) > 1e-6 and
                            (not allow_vfr_approximate or abs(clip_fps - project_fps) > .01))):
                    raise SystemExit("preview_media_plan_timeline_fps_mismatch:"
                                     f"media={media_fps.get(media_name)};plan={clip_fps};timeline={project_fps}")
                start, end = int(clip["source_start_frame"]), int(clip["source_end_frame"])
                if end <= start:
                    raise SystemExit("preview_source_range_invalid")
                append_infos.append({"mediaPoolItem": source_media[media_name], "startFrame": start,
                                     "endFrame": end, "recordFrame": record, "trackIndex": 1})
                expected.append({"product_index": product["product_index"], "variant_id": variant["variant_id"],
                                 "media": media_name, "record_start": record, "record_end": record + end - start,
                                 "source_start": start, "source_end": end})
                record += end - start
            if variant_index > 1:
                markers.append({"frame": variant_start, "product_index": product["product_index"],
                                "encoding_id": product["encoding_id"], "variant_id": variant["variant_id"],
                                "kind": "variant"})
            record += gap_frames
        markers.append({"frame": product_start, "product_index": product["product_index"],
                        "encoding_id": product["encoding_id"], "kind": "product"})

    existing_custom = {row.get("customData", "") for row in (target.GetMarkers() or {}).values()
                       if isinstance(row, dict)}
    for marker in markers:
        marker["custom_data"] = (f"tb_cut_semantic_preview:{batch_id}:product:{marker['encoding_id']}"
                                 if marker["kind"] == "product" else
                                 f"tb_cut_semantic_preview:{batch_id}:product:{marker['encoding_id']}:{marker['variant_id']}")
        if marker["custom_data"] in existing_custom:
            raise SystemExit("preview_marker_duplicate")
    if not append_infos:
        raise SystemExit("preview_append_plan_empty")
    if check_only:
        return {"status": "PREFLIGHT_PASS", "target_timeline": target.GetName(),
                "existing_video_count": len(old_video), "existing_audio_count": len(old_audio),
                "append_variants": selected_total, "append_clip_rows": len(append_infos),
                "gap_seconds": 2, "product_markers": sum(row["kind"] == "product" for row in markers),
                "variant_markers": sum(row["kind"] == "variant" for row in markers),
                "product_ids": [row["product_index"] for row in products],
                "vfr_approximate_mapping": allow_vfr_approximate,
                "new_content_only": True}

    backup_dir = paths.cache_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{project.GetName()}_before_semantic_preview_{datetime.now():%Y%m%d_%H%M%S}.drp"
    if not manager.SaveProject() or not manager.ExportProject(project.GetName(), str(backup), False):
        raise SystemExit("preview_backup_failed")
    if not zipfile.is_zipfile(backup):
        raise SystemExit("preview_backup_invalid")
    with zipfile.ZipFile(backup) as archive:
        if not archive.namelist() or archive.testzip() is not None:
            raise SystemExit("preview_backup_corrupt")

    transaction = {"status": "STARTED", "mode": ("manual_cutpoint_preview" if rough_semantic
                                                    else "semantic_review_preview_only"),
                   "project_id": stable_id(project), "source_timeline_id": source_id,
                   "target_timeline_id": target_id, "backup": str(backup),
                   "products": evidence, "existing_video_count": len(old_video),
                   "existing_audio_count": len(old_audio), "variant_count": selected_total,
                   "expected": expected, "markers": markers, "target_before": before[target_id]}
    atomic_json(transaction_path, transaction)
    if not project.SetCurrentTimeline(target):
        raise SystemExit("preview_cannot_activate_target_timeline")
    if {stable_id(t): resolve_snapshot(t) for t in timelines} != before:
        raise SystemExit("preview_timeline_changed_before_append")
    transaction["status"] = "APPEND_IN_PROGRESS"
    atomic_json(transaction_path, transaction)
    created = project.GetMediaPool().AppendToTimeline(append_infos)
    if not created or len(created) != len(append_infos):
        transaction["status"] = "APPENDED_PENDING_INSPECTION"
        atomic_json(transaction_path, transaction)
        raise SystemExit("preview_append_incomplete_no_automatic_retry")
    transaction["status"] = "APPENDED_PENDING_VERIFY"
    atomic_json(transaction_path, transaction)
    for marker in markers:
        prefix = "ASR待修" if rough_semantic else "预审"
        name = (f"{prefix}｜商品 ID {marker['encoding_id']}｜开始" if marker["kind"] == "product"
                else f"{prefix}｜商品 ID {marker['encoding_id']}｜{marker['variant_id']}")
        if not target.AddMarker(marker["frame"] - int(target.GetStartFrame()), "Red",
                                "MANUAL_CUTPOINT" if rough_semantic else "SEMANTIC_PREVIEW",
                                name, 1, marker["custom_data"]):
            raise SystemExit("preview_marker_write_failed_no_automatic_retry")
    if not manager.SaveProject():
        raise SystemExit("preview_save_failed_no_automatic_retry")

    new_video = list(target.GetItemListInTrack("video", 1) or [])[len(old_video):]
    new_audio = list(target.GetItemListInTrack("audio", 1) or [])[len(old_audio):]
    errors = validate_written_rows(expected, [item_row(row) for row in new_video],
                                  [item_row(row) for row in new_audio])
    after = {stable_id(t): resolve_snapshot(t) for t in timelines}
    for timeline in timelines:
        tid = stable_id(timeline)
        if tid == target_id:
            if not snapshot_preserved(before[tid], after[tid]):
                errors.append("preview_target_existing_content_changed")
        elif before[tid] != after[tid]:
            errors.append("preview_non_target_timeline_changed")
    current_markers = target.GetMarkers() or {}
    for marker in markers:
        offset = marker["frame"] - int(target.GetStartFrame())
        if (current_markers.get(int(offset), {}) or {}).get("customData") != marker["custom_data"]:
            errors.append("preview_marker_readback_failed")
    report = {"schema_version": 1, "mode": transaction["mode"],
              "passed": not errors, "errors": errors, "project_id": stable_id(project),
              "target_timeline_id": target_id, "target_timeline": target.GetName(),
              "source_timeline_id": source_id, "products": evidence,
              "variant_count": selected_total, "video_rows_added": len(new_video),
              "audio_rows_added": len(new_audio), "two_second_empty_black_gaps": True,
              "vfr_approximate_mapping": allow_vfr_approximate,
              "markers": markers, "backup": str(backup),
              "target_before": before[target_id], "target_after": after[target_id]}
    if rough_semantic:
        report["manual_cutpoint_learning_status"] = "WAITING_FOR_USER_EDIT"
    atomic_json(report_path, report)
    transaction.update(status="VERIFIED" if not errors else "FAILED_VERIFICATION",
                       report=str(report_path), report_sha256=hashlib.sha256(
                           json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest())
    atomic_json(transaction_path, transaction)
    if errors:
        raise SystemExit("preview_readback_failed_no_automatic_retry")
    return {"status": "PREVIEW_WRITTEN_AND_VERIFIED", "target_timeline": target.GetName(),
            "products": [row["product_index"] for row in products],
            "variants": selected_total, "clip_rows": len(new_video), "gaps_seconds": 2,
            "preview_only": True}


def recover_manual_cutpoint_preview(paths, transaction, transaction_path, output_dir):
    """Read back an already appended manual draft; never append its clips again."""
    state = paths.load_state()
    if transaction.get("status") != "APPENDED_PENDING_VERIFY":
        raise SystemExit("manual_preview_transaction_not_safe_for_recovery")
    backup = Path(str(transaction.get("backup") or ""))
    if not backup.is_file() or not zipfile.is_zipfile(backup):
        raise SystemExit("manual_preview_recovery_backup_missing")
    resolve = connect(status_path=paths.workspace_root / "resolve_connection_status.json")
    if not resolve:
        raise SystemExit("manual_preview_recovery_resolve_unavailable")
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject() if manager else None
    if (not project or stable_id(project) != transaction.get("project_id")
            or stable_id(project) != state["project_identity"]["resolve_project_id"]):
        raise SystemExit("manual_preview_recovery_project_mismatch")
    timelines = [project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)]
    by_id = {stable_id(timeline): timeline for timeline in timelines}
    target = by_id.get(transaction.get("target_timeline_id"))
    source = by_id.get(transaction.get("source_timeline_id"))
    if (not target or not source or target.GetName() != state["resolve"]["target_timeline"]
            or source.GetName() != state["resolve"]["source_timeline"]):
        raise SystemExit("manual_preview_recovery_timeline_mismatch")
    expected, markers = transaction.get("expected"), transaction.get("markers")
    if not isinstance(expected, list) or not expected or not isinstance(markers, list) or not markers:
        raise SystemExit("manual_preview_recovery_plan_missing")
    before = transaction.get("target_before")
    if not isinstance(before, dict) or not snapshot_preserved(before, resolve_snapshot(target)):
        raise SystemExit("manual_preview_existing_content_changed")
    video = list(target.GetItemListInTrack("video", 1) or [])
    audio = list(target.GetItemListInTrack("audio", 1) or [])
    old_video, old_audio = int(transaction["existing_video_count"]), int(transaction["existing_audio_count"])
    if len(video) != old_video + len(expected) or len(audio) != old_audio + len(expected):
        raise SystemExit("manual_preview_recovery_clip_count_mismatch")
    errors = validate_written_rows(expected, [item_row(row) for row in video[old_video:]],
                                   [item_row(row) for row in audio[old_audio:]])
    if errors:
        raise SystemExit("manual_preview_recovery_clip_readback_failed:" + ",".join(errors))
    for marker in markers:
        offset = int(marker["frame"]) - int(target.GetStartFrame())
        existing = (target.GetMarkers() or {}).get(offset)
        if existing:
            if existing.get("customData") != marker["custom_data"]:
                raise SystemExit("manual_preview_recovery_marker_position_occupied")
            continue
        name = (f"ASR待修｜商品 ID {marker['encoding_id']}｜开始" if marker["kind"] == "product"
                else f"ASR待修｜商品 ID {marker['encoding_id']}｜{marker['variant_id']}")
        if not target.AddMarker(offset, "Red", "MANUAL_CUTPOINT", name, 1, marker["custom_data"]):
            raise SystemExit("manual_preview_recovery_marker_write_failed")
    if not manager.SaveProject():
        raise SystemExit("manual_preview_recovery_save_failed")
    marker_rows = target.GetMarkers() or {}
    if any((marker_rows.get(int(marker["frame"]) - int(target.GetStartFrame())) or {}).get("customData")
           != marker["custom_data"] for marker in markers):
        raise SystemExit("manual_preview_recovery_marker_readback_failed")
    after = resolve_snapshot(target)
    if not snapshot_preserved(before, after):
        raise SystemExit("manual_preview_recovery_existing_content_changed")
    report = {"schema_version": 1, "mode": "manual_cutpoint_preview", "passed": True,
              "project_id": stable_id(project), "source_timeline_id": stable_id(source),
              "target_timeline_id": stable_id(target), "target_timeline": target.GetName(),
              "products": transaction["products"], "variant_count": transaction["variant_count"],
              "video_rows_added": len(expected), "audio_rows_added": len(expected),
              "two_second_empty_black_gaps": True, "markers": markers,
              "backup": str(backup), "target_before": before, "target_after": after,
              "manual_cutpoint_learning_status": "WAITING_FOR_USER_EDIT",
              "recovered_without_reappend": True}
    report_path = output_dir / "preview_report.json"
    atomic_json(report_path, report)
    transaction.update(status="VERIFIED", report=str(report_path),
                       recovery="marker_readback_only_no_clip_reappend")
    atomic_json(transaction_path, transaction)
    return {"status": "PREVIEW_WRITTEN_AND_VERIFIED", "target_timeline": target.GetName(),
            "products": [row["product_index"] for row in transaction["products"]],
            "variants": transaction["variant_count"], "clip_rows": len(expected),
            "recovery": "readback_without_reappend"}


def review_manual_edits(paths):
    """Read the user's timeline edits against the immutable preview baseline."""
    state = paths.load_state()
    batch_id = str(state["batch"]["batch_id"])
    output_dir = paths.cache_dir / "resolve_semantic_preview" / batch_id
    transaction_path = output_dir / "preview_transaction.json"
    transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
    if transaction.get("mode") != "manual_cutpoint_preview" or transaction.get("status") != "VERIFIED":
        raise SystemExit("manual_edit_review_requires_verified_preview")
    report_path = Path(transaction["report"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (report.get("mode") != "manual_cutpoint_preview" or not report.get("passed")
            or report.get("project_id") != transaction.get("project_id")):
        raise SystemExit("manual_edit_review_baseline_invalid")
    resolve = connect(status_path=paths.workspace_root / "resolve_connection_status.json")
    if not resolve:
        raise SystemExit("manual_edit_review_resolve_unavailable")
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject() if manager else None
    if (not project or stable_id(project) != state["project_identity"]["resolve_project_id"]
            or stable_id(project) != transaction["project_id"]):
        raise SystemExit("manual_edit_review_project_mismatch")
    timelines = [project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)]
    by_id = {stable_id(timeline): timeline for timeline in timelines}
    source = by_id.get(transaction["source_timeline_id"])
    target = by_id.get(transaction["target_timeline_id"])
    if (not source or not target or source.GetName() != state["resolve"]["source_timeline"]
            or target.GetName() != state["resolve"]["target_timeline"]):
        raise SystemExit("manual_edit_review_timeline_mismatch")
    current = resolve_snapshot(target)
    old = report["target_before"]
    baseline = report["target_after"]
    protected_preserved = snapshot_preserved(old, current)
    clip_kinds = ("video", "audio")
    old_counts = {kind: sum(row["kind"] == kind for row in old["rows"]) for kind in clip_kinds}
    baseline_rows = {kind: [row for row in baseline["rows"] if row["kind"] == kind][old_counts[kind]:]
                     for kind in clip_kinds}
    current_rows = {kind: [row for row in current["rows"] if row["kind"] == kind][old_counts[kind]:]
                    for kind in clip_kinds}
    changes = {}
    for kind in clip_kinds:
        original, edited = baseline_rows[kind], current_rows[kind]
        changes[kind] = [{"row": i + 1, "media_changed": a.get("media_id") != b.get("media_id"),
                          "record_start_delta_frames": int(b["record_start"]) - int(a["record_start"]),
                          "record_end_delta_frames": int(b["record_end"]) - int(a["record_end"]),
                          "source_start_delta_frames": int(b["source_start"]) - int(a["source_start"]),
                          "source_end_delta_frames": int(b["source_end_raw"]) - int(a["source_end_raw"]),
                          "properties_changed": a.get("properties") != b.get("properties")}
                         for i, (a, b) in enumerate(zip(original, edited))]
    result = {"status": "MANUAL_EDIT_READBACK", "project_id": transaction["project_id"],
              "target_timeline_id": transaction["target_timeline_id"],
              "protected_content_preserved": protected_preserved,
              "baseline_counts": {kind: len(baseline_rows[kind]) for kind in clip_kinds},
              "current_counts": {kind: len(current_rows[kind]) for kind in clip_kinds},
              "changes": changes, "baseline_markers": transaction["markers"],
              "current_markers": current["markers"], "current_snapshot": current,
              "baseline_snapshot": baseline}
    atomic_json(output_dir / "manual_edit_readback.json", result)
    compact = {key: value for key, value in result.items()
               if key not in ("baseline_markers", "current_markers", "current_snapshot", "baseline_snapshot")}
    return compact


def normalize_manual_markers(paths, *, check_only=False):
    """Replace only this verified manual preview's red markers with blue product starts."""
    state = paths.load_state()
    batch_id = str(state["batch"]["batch_id"])
    output_dir = paths.cache_dir / "resolve_semantic_preview" / batch_id
    transaction_path = output_dir / "preview_transaction.json"
    transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
    if transaction.get("mode") != "manual_cutpoint_preview" or transaction.get("status") != "VERIFIED":
        raise SystemExit("manual_marker_normalization_requires_verified_preview")
    marker_tx_path = output_dir / "manual_marker_transaction.json"
    if marker_tx_path.is_file():
        raise SystemExit("manual_marker_transaction_exists_inspect_before_retry")
    review_manual_edits(paths)
    readback = json.loads((output_dir / "manual_edit_readback.json").read_text(encoding="utf-8"))
    if not readback["protected_content_preserved"]:
        raise SystemExit("manual_marker_protected_content_changed")
    before = readback["current_snapshot"]
    old_counts = {kind: sum(row["kind"] == kind for row in transaction["target_before"]["rows"])
                  for kind in ("video", "audio")}
    rows = {kind: [row for row in before["rows"] if row["kind"] == kind][old_counts[kind]:]
            for kind in ("video", "audio")}
    expected = transaction["expected"]
    if any(len(rows[kind]) != len(expected) for kind in rows):
        raise SystemExit("manual_marker_clip_count_changed")
    for index, (video, audio, original) in enumerate(zip(rows["video"], rows["audio"], expected)):
        identity = ("media_id", "media_path", "record_start", "record_end",
                    "source_start", "source_end_raw")
        if any(video.get(key) != audio.get(key) for key in identity):
            raise SystemExit(f"manual_marker_audio_video_mismatch:{index + 1}")
        baseline = [row for row in readback["baseline_snapshot"]["rows"]
                    if row["kind"] == "video"][old_counts["video"] + index]
        if (video.get("media_id") != baseline.get("media_id")
                or video["record_end"] <= video["record_start"]
                or video["source_end_raw"] < video["source_start"]
                or abs(video["source_start"] - baseline["source_start"]) > 60
                or abs(video["source_end_raw"] - baseline["source_end_raw"]) > 60):
            raise SystemExit(f"manual_marker_unexpected_clip_edit:{index + 1}")
    grouped = {}
    occupied = []
    for index, (original, row) in enumerate(zip(expected, rows["video"])):
        key = (original["product_index"], original["variant_id"])
        grouped.setdefault(key, []).append(row)
        for prior in occupied:
            if (prior[0] == row["media_path"]
                    and min(prior[2], row["source_end_raw"] + 1)
                    > max(prior[1], row["source_start"])):
                raise SystemExit("manual_marker_new_source_overlap")
        occupied.append((row["media_path"], row["source_start"], row["source_end_raw"] + 1))
    fps = float(before["settings"].get("timelineFrameRate") or 0)
    if fps <= 0:
        raise SystemExit("manual_marker_timeline_fps_missing")
    for key, clips in grouped.items():
        duration = sum((row["record_end"] - row["record_start"]) / fps for row in clips)
        if not 45 < duration < 60:
            raise SystemExit(f"manual_marker_variant_duration_invalid:{key[0]}:{key[1]}")
    new_markers = []
    encoding_by_product = {row["product_index"]: row["encoding_id"]
                           for row in transaction["markers"] if row["kind"] == "product"}
    for product in transaction["products"]:
        pid = product["product_index"]
        encoding_id = encoding_by_product.get(pid)
        if not encoding_id:
            raise SystemExit("manual_marker_product_encoding_missing")
        first = next((index for index, row in enumerate(expected) if row["product_index"] == pid), None)
        if first is None:
            raise SystemExit("manual_marker_product_missing")
        new_markers.append({"frame": rows["video"][first]["record_start"], "product_index": pid,
                            "encoding_id": encoding_id,
                            "custom_data": f"tb_cut_product_start:{encoding_id}"})
    if len({row["frame"] for row in new_markers}) != len(new_markers):
        raise SystemExit("manual_marker_duplicate_product_start")
    current_markers = before["markers"]
    old_markers = transaction["markers"]
    for marker in old_markers:
        current = current_markers.get(str(marker["frame"] - before["start"]))
        if (not current or current.get("customData") != marker["custom_data"]
                or current.get("color") != "Red"):
            raise SystemExit("manual_marker_preview_marker_changed")
    old_custom = {row["custom_data"] for row in old_markers}
    for marker in new_markers:
        offset = str(marker["frame"] - before["start"])
        occupied_marker = current_markers.get(offset)
        if occupied_marker and occupied_marker.get("customData") not in old_custom:
            raise SystemExit("manual_marker_product_start_occupied")
        if any(row.get("customData") == marker["custom_data"]
               for row in current_markers.values()):
            raise SystemExit("manual_marker_normal_product_marker_exists")
    summary = {"status": "PREFLIGHT_PASS" if check_only else "PENDING",
               "target_timeline": before["name"], "products": [row["product_index"] for row in new_markers],
               "remove_red_markers": len(old_markers), "add_blue_product_markers": len(new_markers),
               "product_start_frames": {str(row["product_index"]): row["frame"] for row in new_markers},
               "user_clip_edits_preserved": True}
    if check_only:
        return summary
    resolve = connect(status_path=paths.workspace_root / "resolve_connection_status.json")
    manager = resolve.GetProjectManager() if resolve else None
    project = manager.GetCurrentProject() if manager else None
    if not project or stable_id(project) != transaction["project_id"]:
        raise SystemExit("manual_marker_project_changed_before_write")
    timelines = [project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)]
    by_id = {stable_id(row): row for row in timelines}
    target, source = by_id.get(transaction["target_timeline_id"]), by_id.get(transaction["source_timeline_id"])
    if not target or not source or resolve_snapshot(target) != before:
        raise SystemExit("manual_marker_timeline_changed_before_write")
    source_before = resolve_snapshot(source)
    backup_dir = paths.cache_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{project.GetName()}_before_manual_markers_{datetime.now():%Y%m%d_%H%M%S}.drp"
    if not manager.SaveProject() or not manager.ExportProject(project.GetName(), str(backup), False):
        raise SystemExit("manual_marker_backup_failed")
    if not zipfile.is_zipfile(backup):
        raise SystemExit("manual_marker_backup_invalid")
    marker_tx = {"status": "STARTED", "project_id": transaction["project_id"],
                 "target_timeline_id": transaction["target_timeline_id"], "backup": str(backup),
                 "before": before, "source_before": source_before,
                 "remove": old_markers, "add": new_markers}
    atomic_json(marker_tx_path, marker_tx)
    marker_tx["status"] = "MUTATING"
    atomic_json(marker_tx_path, marker_tx)
    for marker in old_markers:
        if not target.DeleteMarkerByCustomData(marker["custom_data"]):
            raise SystemExit("manual_marker_delete_failed_inspect_transaction")
    for marker in new_markers:
        offset = marker["frame"] - int(target.GetStartFrame())
        if not target.AddMarker(offset, "Blue", str(marker["encoding_id"]), "", 1,
                                marker["custom_data"]):
            raise SystemExit("manual_marker_add_failed_inspect_transaction")
    if not manager.SaveProject():
        raise SystemExit("manual_marker_save_failed_inspect_transaction")
    after = resolve_snapshot(target)
    marker_map = after["markers"]
    errors = []
    if after["rows"] != before["rows"] or any(after[key] != before[key]
                                               for key in ("id", "name", "start", "end", "settings")):
        errors.append("manual_marker_clip_or_timeline_changed")
    if resolve_snapshot(source) != source_before:
        errors.append("manual_marker_source_timeline_changed")
    for marker in old_markers:
        if any(row.get("customData") == marker["custom_data"] for row in marker_map.values()):
            errors.append("manual_marker_red_remains")
    for marker in new_markers:
        value = marker_map.get(str(marker["frame"] - after["start"])) or {}
        if (value.get("color") != "Blue" or value.get("name") != str(marker["encoding_id"])
                or value.get("customData") != marker["custom_data"]):
            errors.append("manual_marker_blue_readback_failed")
    for frame, value in before["markers"].items():
        if value.get("customData") not in old_custom and marker_map.get(frame) != value:
            errors.append("manual_marker_other_marker_changed")
    marker_tx.update(status="VERIFIED" if not errors else "FAILED_VERIFICATION",
                     errors=errors, after=after)
    atomic_json(marker_tx_path, marker_tx)
    if errors:
        raise SystemExit("manual_marker_readback_failed_inspect_transaction:" + ",".join(errors))
    summary["status"] = "NORMAL_MARKERS_VERIFIED"
    return summary


def recover_markers(paths):
    """Complete marker/readback for an already appended transaction; never append clips."""
    state = paths.load_state()
    batch_id = str(state["batch"]["batch_id"])
    output_dir = paths.cache_dir / "resolve_semantic_preview" / batch_id
    transaction_path = output_dir / "preview_transaction.json"
    if not transaction_path.is_file():
        raise SystemExit("preview_recovery_transaction_missing")
    transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
    if transaction.get("mode") == "manual_cutpoint_preview":
        return recover_manual_cutpoint_preview(paths, transaction, transaction_path, output_dir)
    if (transaction.get("status") != "APPENDED_PENDING_VERIFY"
            or transaction.get("mode") != "semantic_review_preview_only"):
        raise SystemExit("preview_recovery_transaction_not_pending_verify")
    if transaction.get("existing_video_count") != 0 or transaction.get("existing_audio_count") != 0:
        raise SystemExit("preview_recovery_unexpected_existing_target_content")
    if not Path(transaction.get("backup", "")).is_file():
        raise SystemExit("preview_recovery_backup_missing")

    resolve = connect(status_path=paths.workspace_root / "resolve_connection_status.json")
    if not resolve:
        raise SystemExit("preview_recovery_resolve_connection_failed")
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject() if manager else None
    if (not project or stable_id(project) != transaction.get("project_id")
            or stable_id(project) != state["project_identity"]["resolve_project_id"]):
        raise SystemExit("preview_recovery_project_identity_mismatch")
    timelines = [project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)]
    target = next((timeline for timeline in timelines
                   if stable_id(timeline) == transaction.get("target_timeline_id")), None)
    source = next((timeline for timeline in timelines
                   if stable_id(timeline) == transaction.get("source_timeline_id")), None)
    if (not target or not source or target.GetName() != state["resolve"]["target_timeline"]
            or source.GetName() != state["resolve"]["source_timeline"]):
        raise SystemExit("preview_recovery_timeline_identity_mismatch")

    fps = float(project.GetSetting("timelineFrameRate") or project.GetSetting("timelinePlaybackFrameRate") or 30)
    gap = round(2.0 * fps)
    record = int(target.GetStartFrame())
    expected, marker_specs = [], []
    for product in transaction.get("products", []):
        variants = product.get("preview_variants", [])
        product_start = record
        plan_path = paths.resolve_reference(product["plan_reference"]).path
        plan_record = json.loads(plan_path.read_text(encoding="utf-8"))
        plan_body = plan_record.get("body")
        if (plan_record.get("body_digest") != product["plan_digest"]
                or digest(plan_body) != product["plan_digest"]
                or plan_record.get("identity", {}).get("product_id") != product["product_index"]):
            raise SystemExit("preview_recovery_plan_digest_mismatch")
        encoding_id = plan_record["identity"]["encoding_id"]
        available = {row["variant_id"]: row for row in plan_body["variants"]}
        for variant_index, variant_id in enumerate(variants):
            variant = available.get(variant_id)
            if not variant:
                raise SystemExit("preview_recovery_variant_missing")
            variant_start = record
            for clip in variant["clips"]:
                start, end = int(clip["source_start_frame"]), int(clip["source_end_frame"])
                expected.append({"product_index": product["product_index"], "variant_id": variant_id,
                                 "media": clip["source_file"], "record_start": record,
                                 "record_end": record + end - start,
                                 "source_start": start, "source_end": end})
                record += end - start
            if variant_index == 0:
                marker_specs.append({"frame": product_start, "product_index": product["product_index"],
                                     "encoding_id": encoding_id, "variant_id": variant_id,
                                     "kind": "product"})
            else:
                marker_specs.append({"frame": variant_start, "product_index": product["product_index"],
                                     "encoding_id": encoding_id, "variant_id": variant_id,
                                     "kind": "variant"})
            record += gap

    video = list(target.GetItemListInTrack("video", 1) or [])
    audio = list(target.GetItemListInTrack("audio", 1) or [])
    if len(video) != len(expected) or len(audio) != len(expected):
        raise SystemExit("preview_recovery_clip_count_mismatch_no_reappend")
    errors = validate_written_rows(expected, [item_row(row) for row in video],
                                  [item_row(row) for row in audio])
    if errors:
        raise SystemExit("preview_recovery_clip_readback_mismatch:" + ",".join(errors))

    marker_map = target.GetMarkers() or {}
    old_custom = [row.get("customData") for row in marker_map.values() if isinstance(row, dict)
                  and str(row.get("customData", "")).startswith(f"tb_cut_semantic_preview:{batch_id}:")]
    specs_by_offset = {spec["frame"] - int(target.GetStartFrame()): spec for spec in marker_specs}
    for offset, row in list(marker_map.items()):
        spec = specs_by_offset.get(int(offset))
        if (spec and isinstance(row, dict)
                and row.get("customData") != (
                    f"tb_cut_semantic_preview:{batch_id}:product:{spec['encoding_id']}"
                    if spec["kind"] == "product" else
                    f"tb_cut_semantic_preview:{batch_id}:product:{spec['encoding_id']}:{spec['variant_id']}")):
            custom_data = row.get("customData")
            if custom_data not in old_custom:
                raise SystemExit("preview_recovery_stale_marker_delete_failed")
            target.DeleteMarkerByCustomData(custom_data)
            if any(isinstance(current, dict) and current.get("customData") == custom_data
                   for current in (target.GetMarkers() or {}).values()):
                raise SystemExit("preview_recovery_stale_marker_delete_failed")

    marker_map = target.GetMarkers() or {}
    for spec in marker_specs:
        custom_data = (f"tb_cut_semantic_preview:{batch_id}:product:{spec['encoding_id']}"
                       if spec["kind"] == "product" else
                       f"tb_cut_semantic_preview:{batch_id}:product:{spec['encoding_id']}:{spec['variant_id']}")
        offset = spec["frame"] - int(target.GetStartFrame())
        existing = marker_map.get(int(offset))
        if isinstance(existing, dict) and existing.get("customData") == custom_data:
            continue
        if existing:
            raise SystemExit("preview_recovery_marker_frame_occupied")
        name = (f"商品 ID {spec['encoding_id']}｜预审起点｜{spec['variant_id']}"
                if spec["kind"] == "product" else
                f"商品 ID {spec['encoding_id']}｜{spec['variant_id']} 预审")
        note = "仅供语义预审；商品整体审核未通过。"
        if not target.AddMarker(offset, "Red", name, note, 1, custom_data):
            raise SystemExit("preview_recovery_marker_add_failed_no_reappend")
        marker_map = target.GetMarkers() or {}

    if not manager.SaveProject():
        raise SystemExit("preview_recovery_save_failed_no_reappend")
    marker_map = target.GetMarkers() or {}
    for spec in marker_specs:
        custom_data = (f"tb_cut_semantic_preview:{batch_id}:product:{spec['encoding_id']}"
                       if spec["kind"] == "product" else
                       f"tb_cut_semantic_preview:{batch_id}:product:{spec['encoding_id']}:{spec['variant_id']}")
        offset = spec["frame"] - int(target.GetStartFrame())
        if (marker_map.get(int(offset), {}) or {}).get("customData") != custom_data:
            raise SystemExit("preview_recovery_marker_readback_failed_no_reappend")
    report = {"schema_version": 1, "mode": "semantic_review_preview_only", "passed": True,
              "target_timeline": target.GetName(), "target_timeline_id": stable_id(target),
              "source_timeline_id": stable_id(source), "products": transaction["products"],
              "variant_count": transaction["variant_count"], "video_rows_added": len(video),
              "audio_rows_added": len(audio), "two_second_empty_black_gaps": True,
              "vfr_approximate_mapping": True, "markers": marker_specs,
              "backup": transaction["backup"]}
    report_path = output_dir / "preview_report.json"
    atomic_json(report_path, report)
    transaction.update(status="VERIFIED", report=str(report_path),
                       recovery="markers_and_readback_only_no_clip_reappend")
    atomic_json(transaction_path, transaction)
    return {"status": "PREVIEW_WRITTEN_AND_VERIFIED", "target_timeline": target.GetName(),
            "products": sorted({row["product_index"] for row in transaction["products"]}),
            "variants": transaction["variant_count"], "clip_rows": len(video),
            "markers": len(marker_specs), "recovery": "markers_and_readback_only"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--variants", help="Comma-separated product_id:variant_id list")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--allow-vfr-approximate", action="store_true")
    parser.add_argument("--recover-markers", action="store_true",
                        help="Complete markers/readback for an appended transaction without replaying clips")
    parser.add_argument("--rough-semantic", action="store_true",
                        help="Preview current semantic PASS when ASR cutpoint alignment failed")
    parser.add_argument("--review-manual-edits", action="store_true",
                        help="Read back the user's manual cutpoint edits without changing Resolve")
    parser.add_argument("--normalize-manual-markers", action="store_true",
                        help="Replace this batch's red manual preview markers with normal blue product markers")
    args = parser.parse_args()
    paths = resolve_runtime_paths(args.root, args.workspace)
    if args.normalize_manual_markers:
        result = normalize_manual_markers(paths, check_only=args.check_only)
    elif args.review_manual_edits:
        result = review_manual_edits(paths)
    elif args.recover_markers:
        result = recover_markers(paths)
    else:
        if not args.variants:
            parser.error("--variants is required unless --recover-markers is set")
        result = execute(paths, parse_variants(args.variants), check_only=args.check_only,
                         allow_vfr_approximate=args.allow_vfr_approximate,
                         rough_semantic=args.rough_semantic)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
