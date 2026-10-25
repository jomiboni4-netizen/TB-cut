"""Replace prior marked test clips and append one Codex test product at a time.

This is a narrowly scoped runtime path for an explicitly authorized test batch.
It is not a finished-delivery writer and always labels results pending review.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import zipfile

from coldstart_resolve import media_items, snapshot, verify_source_fingerprints
from delivery_runtime import require
from project_registry import atomic_json, digest
from resolve_trial import project_lock, stable_id
from runtime_meta import _sha256
from runtime_paths import resolve_runtime_paths


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def item_rows(target):
    return [item for kind in ("video", "audio", "subtitle")
            for track in range(1, target.GetTrackCount(kind) + 1)
            for item in target.GetItemListInTrack(kind, track) or []]


def product_clips(plan, product_id):
    product = next((row for row in plan["products"] if row["product_id"] == product_id), None)
    require(product is not None, "test_product_not_in_plan")
    return product


def expected_frames(clip, timeline_fps):
    fps = float(clip["source_average_fps"])
    start = float(clip["source_start_seconds"])
    end = float(clip["source_end_seconds"])
    require(math.isfinite(fps) and fps > 0 and 0 <= start < end, "invalid_test_clip_clock")
    lo = math.floor(start * fps + 1e-8)
    hi = math.ceil(end * fps - 1e-8)
    duration = round((hi - lo) / fps * timeline_fps)
    return lo, hi, duration


def assert_authorized_target_clear(snapshot_body, plan, override, state):
    auth = override.get("resolve_write_authorization", {})
    wanted = [row["product_id"] for row in plan["products"]]
    require(auth.get("confirmed_by_user") is True
            and auth.get("action") == "clear_configured_target_and_append"
            and auth.get("clear_existing") is True
            and auth.get("product_ids") == wanted
            and auth.get("target_timeline_id") == snapshot_body["id"]
            and auth.get("project_id") == state["project_identity"]["resolve_project_id"],
            "current_target_replacement_authorization_missing")
    binding = state.get("runtime", {}).get("test_timeline_binding", {})
    require(binding.get("project_id") == state["project_identity"]["resolve_project_id"]
            and snapshot_body["id"] == binding.get("timeline_id")
            and snapshot_body["id"] not in {state["resolve"].get("source_timeline_id"),
                                             state["resolve"].get("target_timeline_id")}
            and snapshot_body["name"] == binding.get("name"),
            "clear_target_is_not_registered_test_timeline")


def verified_backup(path):
    require(path.is_file() and zipfile.is_zipfile(path), "resolve_backup_unverified")
    with zipfile.ZipFile(path) as archive:
        require(bool(archive.namelist()) and archive.testzip() is None, "resolve_backup_corrupt")
    return _sha256(path)


def connect_project(paths, state, override):
    from resolve_connection import connect
    resolve = connect(status_path=paths.workspace_root / "resolve_connection_status.json")
    require(resolve is not None, "resolve_not_connected")
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject()
    require(project is not None, "resolve_project_identity_mismatch")
    from resolve_session_identity import confirm_project
    confirmed, _ = confirm_project(paths, project)
    require(confirmed == state["project_identity"]["resolve_project_id"]
            and project.GetName() == state["resolve"]["project_name"], "resolve_project_identity_mismatch")
    timelines = [project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)]
    source_matches = [t for t in timelines if t.GetName() == state["resolve"]["source_timeline"]]
    binding = state.get("runtime", {}).get("test_timeline_binding", {})
    test_matches = [t for t in timelines if stable_id(t) == binding.get("timeline_id")
                    and t.GetName() == binding.get("name")]
    require(len(source_matches) == len(test_matches) == 1
            and binding.get("project_id") == state["project_identity"]["resolve_project_id"],
            "resolve_timeline_name_missing_or_ambiguous")
    source, target = source_matches[0], test_matches[0]
    require(stable_id(target) not in {stable_id(source), state["resolve"].get("target_timeline_id")}
            and target.GetName() not in {state["resolve"]["source_timeline"], state["resolve"]["target_timeline"]},
            "test_timeline_is_protected_source_or_final")
    require(stable_id(source) == override["resolve_write_authorization"]["source_timeline_id"],
            "resolve_source_identity_mismatch")
    require(stable_id(target) == override["resolve_write_authorization"]["target_timeline_id"],
            "resolve_target_identity_mismatch")
    require(stable_id(source) != stable_id(target), "resolve_source_equals_target")
    return resolve, manager, project, timelines, source, target


def prepare_registered_test_timeline(paths, target_name, target_id):
    """Back up and clear only the stable-ID registered reusable test timeline."""
    state = paths.load_state()
    project_id = state["project_identity"]["resolve_project_id"]
    binding = state.get("runtime", {}).get("test_timeline_binding", {})
    require(binding.get("project_id") == project_id
            and binding.get("timeline_id") == target_id
            and binding.get("name") == target_name,
            "selected_target_is_not_registered_test_timeline")
    require(target_id not in {state["resolve"].get("source_timeline_id"),
                              state["resolve"].get("target_timeline_id")}
            and target_name not in {state["resolve"].get("source_timeline"),
                                    state["resolve"].get("target_timeline")},
            "test_timeline_is_protected_source_or_final")
    from resolve_connection import connect
    resolve = connect(status_path=paths.workspace_root / "resolve_connection_status.json")
    require(resolve is not None, "resolve_not_connected")
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject() if manager else None
    require(project is not None, "resolve_project_identity_mismatch")
    from resolve_session_identity import confirm_project
    confirmed, _ = confirm_project(paths, project)
    require(confirmed == project_id, "resolve_project_identity_mismatch")
    timelines = [project.GetTimelineByIndex(index)
                 for index in range(1, project.GetTimelineCount() + 1)]
    matches = [timeline for timeline in timelines
               if timeline.GetName() == target_name and stable_id(timeline) == target_id]
    require(len(matches) == 1, "registered_test_timeline_missing_or_ambiguous")
    target = matches[0]
    transaction = (paths.cache_dir / "resolve_delivery" /
                   str(state["batch"]["batch_id"]) / "reusable_test_prepare_transaction.json")
    transaction.parent.mkdir(parents=True, exist_ok=True)
    before = {stable_id(timeline): snapshot(timeline) for timeline in timelines}
    if transaction.is_file():
        prior = read(transaction)
        require(prior.get("status") == "READBACK_VERIFIED"
                and prior.get("project_id") == project_id
                and prior.get("target_timeline_id") == target_id,
                "reusable_test_prepare_transaction_pending_diagnose_first")
        current = snapshot(target)
        require(not current["rows"] and not current["markers"],
                "reusable_test_timeline_changed_after_prepare")
        return {"status": "READBACK_VERIFIED", "reused_transaction": True,
                "target_timeline": target_name, "target_timeline_id": target_id}
    backup = (paths.cache_dir / "backups" /
              f"{project.GetName()}_before_reusable_test_{datetime.now():%Y%m%d_%H%M%S}.drp")
    backup.parent.mkdir(parents=True, exist_ok=True)
    require(manager.SaveProject() and manager.ExportProject(project.GetName(), str(backup), False),
            "reusable_test_backup_failed")
    backup_sha = verified_backup(backup)
    atomic_json(transaction, {"status": "CLEAR_IN_PROGRESS", "project_id": project_id,
                              "target_timeline_id": target_id, "target_timeline": target_name,
                              "backup": str(backup), "backup_sha256": backup_sha,
                              "before": before[target_id]})
    with project_lock(paths, project_id):
        require(project.SetCurrentTimeline(target), "reusable_test_target_activation_failed")
        require({stable_id(timeline): snapshot(timeline) for timeline in timelines} == before,
                "resolve_timeline_changed_before_reusable_test_clear")
        rows = item_rows(target)
        if rows:
            require(target.DeleteClips(rows, False), "reusable_test_clip_clear_failed")
        if target.GetMarkers():
            require(target.DeleteMarkersByColor("All"), "reusable_test_marker_clear_failed")
        require(manager.SaveProject(), "reusable_test_save_after_clear_failed")
    after = {stable_id(timeline): snapshot(timeline) for timeline in timelines}
    require(not after[target_id]["rows"] and not after[target_id]["markers"],
            "reusable_test_clear_readback_failed")
    require(all(before[timeline_id] == body for timeline_id, body in after.items()
                if timeline_id != target_id), "protected_timeline_changed_during_test_clear")
    body = read(transaction)
    body.update(status="READBACK_VERIFIED", after=after[target_id],
                removed_item_count=len(rows),
                removed_marker_count=len(before[target_id]["markers"]))
    atomic_json(transaction, body)
    return {"status": "READBACK_VERIFIED", "reused_transaction": False,
            "target_timeline": target_name, "target_timeline_id": target_id,
            "removed_item_count": len(rows),
            "removed_marker_count": len(before[target_id]["markers"])}


def verify_source_media(paths, state, run, plan, *, full_hash, target_fps):
    clips = [clip for product in plan["products"] for clip in product["clips"]]
    if full_hash:
        verify_source_fingerprints(run, state, clips)
    else:
        recorded = {row["path"]: row for row in run["inputs"]}
        for clip in clips:
            path = next(Path(p) for p in state["video_paths"] if Path(p).name == clip["source_file"])
            row = recorded.get(str(path))
            stat = path.stat()
            require(row is not None and (stat.st_size, stat.st_mtime_ns) ==
                    (row["bytes"], row["mtime_ns"]), "test_source_stat_changed_since_batch_hash")
    sources = {Path(path).name: path for path in state["video_paths"]}
    for clip in clips:
        path = Path(sources[clip["source_file"]])
        require(path.is_file(), "test_source_missing")
        require(clip["width"] == 1080 and clip["height"] == 1920, "test_source_aspect_not_verified")
        require(float(clip["timeline_fps"]) == float(target_fps), "test_timeline_rate_mismatch")
        require(clip["source_end_seconds"] <= clip["audio_duration_seconds"],
                "selected_audio_time_outside_source_audio_stream")
    return sources


def verify_user_ranges(state, plan, range_doc):
    source_order = {Path(path).stem: i for i, path in enumerate(state["video_paths"])}
    confirmed = {row["product_index"]: row for row in range_doc["products"]}
    for product in plan["products"]:
        row = confirmed.get(product["product_id"])
        require(row is not None, "confirmed_product_range_missing")
        start, end = row["start"], row["end"]
        require(start["source_stem"] in source_order and end["source_stem"] in source_order
                and source_order[start["source_stem"]] <= source_order[end["source_stem"]],
                "confirmed_product_range_order_invalid")
        for clip in product["clips"]:
            stem = Path(clip["source_file"]).stem
            require(stem in source_order
                    and source_order[start["source_stem"]] <= source_order[stem] <= source_order[end["source_stem"]],
                    "test_clip_outside_confirmed_source_range")
            if stem == start["source_stem"]:
                require(clip["source_start_seconds"] >= start["time"], "test_clip_before_confirmed_range")
            if stem == end["source_stem"]:
                require(clip["source_end_seconds"] <= end["time"], "test_clip_after_confirmed_range")


def expected_product_rows(plan, product_id, sources, start_frame):
    product = product_clips(plan, product_id)
    position = start_frame
    expected = []
    total = 0
    seams = []
    for index, clip in enumerate(product["clips"]):
        lo, hi, duration = expected_frames(clip, clip["timeline_fps"])
        require(0 < duration and duration / clip["timeline_fps"] < 60,
                "physical_test_segment_duration_invalid")
        expected.append({"product_id": product_id, "source_file": clip["source_file"], "path": str(Path(sources[clip["source_file"]]).resolve()),
                         "source_start": lo, "source_end_exclusive": hi,
                         "record_start": position, "record_end": position + duration,
                         "duration": duration, "average_fps": clip["source_average_fps"]})
        position += duration
        total += duration
        if index < len(product["clips"]) - 1:
            seams.append(position)
    require(total / product["timeline_fps"] < 60, "test_product_duration_not_under_60")
    return product, expected, seams, total


def validate_product_gap_layout(expected, gap_frames):
    for previous, current in zip(expected, expected[1:]):
        gap = current["record_start"] - previous["record_end"]
        wanted = gap_frames if current["product_id"] != previous["product_id"] else 0
        require(gap == wanted, "test_inter_product_black_gap_layout_mismatch")


def validate_readback(target, expected, product_id, plan_digest, *, require_marker=True, gap_frames=25):
    validate_product_gap_layout(expected, gap_frames)
    actual = snapshot(target)
    video = sorted([r for r in actual["rows"] if r["kind"] == "video"], key=lambda r: r["record_start"])
    audio = sorted([r for r in actual["rows"] if r["kind"] == "audio"], key=lambda r: r["record_start"])
    require(len(video) == len(audio) == len(expected), "test_readback_av_clip_count_mismatch")
    for v, a, exp in zip(video, audio, expected):
        require(str(Path(v["media_path"]).resolve()) == exp["path"]
                and str(Path(a["media_path"]).resolve()) == exp["path"], "test_readback_source_mismatch")
        require(v["record_start"] == exp["record_start"] and v["record_end"] == exp["record_end"]
                and (a["record_start"], a["record_end"]) == (v["record_start"], v["record_end"]),
                "test_readback_av_record_sync_mismatch")
        require(abs(v["source_start"] - exp["source_start"]) <= 1,
                "test_readback_video_source_start_mismatch")
        require(v["source_end_raw"] in (exp["source_end_exclusive"], exp["source_end_exclusive"] - 1),
                "test_readback_video_source_end_mismatch")
    require(float(actual["settings"]["timelineFrameRate"]) == float(target.GetSetting("timelineFrameRate"))
            and actual["settings"]["timelineResolutionWidth"] == "1080"
            and actual["settings"]["timelineResolutionHeight"] == "1920", "test_target_settings_changed")
    markers = [m for m in actual["markers"].values() if m.get("customData", "").startswith(
        f"CODEX_TEST:{plan_digest}:{product_id}:")]
    if require_marker:
        require(len(markers) >= 1, "test_product_marker_missing")
    return actual


def reconcile_expected_rows(expected, actual):
    video = sorted([r for r in actual["rows"] if r["kind"] == "video"], key=lambda r: r["record_start"])
    require(len(video) == len(expected), "test_readback_video_count_mismatch")
    reconciled = []
    for exp, row in zip(expected, video):
        require(row["record_start"] == exp["record_start"] and row["record_end"] > row["record_start"],
                "test_readback_record_bounds_invalid")
        item = dict(exp, record_end=row["record_end"], duration=row["record_end"] - row["record_start"])
        reconciled.append(item)
    return reconciled


def add_product_markers(target, product_id, plan_sha, product, expected, start_frame):
    begin = expected[0]["record_start"]
    end = expected[-1]["record_end"]
    duration = end - begin
    timeline_fps = float(target.GetSetting("timelineFrameRate"))
    require(0 < duration / timeline_fps < 60, "test_readback_product_duration_not_under_60")
    require(target.AddMarker(begin - start_frame, "Blue", f"Codex测试｜商品{product_id}｜待审核",
                             f"字幕选段；音频首尾/音画同步人工复核待完成；时长约{duration/timeline_fps:.2f}秒",
                             duration, f"CODEX_TEST:{plan_sha}:{product_id}:start"),
            "test_product_marker_add_failed_no_replay")
    for n, row in enumerate(expected[1:], 1):
        require(target.AddMarker(row["record_start"] - start_frame, "Red",
                                 f"问题点｜商品{product_id}接缝{n}",
                                 "Codex测试待审核：此处跳过了非保留字幕；请复听接缝并核对音画",
                                 1, f"CODEX_TEST:{plan_sha}:{product_id}:seam:{n}"),
                "test_seam_marker_add_failed_no_replay")
    if len(expected) == 1:
        require(target.AddMarker(end - 1 - start_frame, "Yellow", f"待审核｜商品{product_id}结尾",
                                 "字幕边界选点；音频结尾待人工复核", 1,
                                 f"CODEX_TEST:{plan_sha}:{product_id}:ending"),
                "test_ending_marker_add_failed_no_replay")


def append_product_sequential(target, pool, media_by_name, product_id, product, expected,
                              prior_expected, plan_sha, gap_frames):
    """Append each physical segment at the previous segment's actual record end."""
    all_expected = list(prior_expected)
    position = snapshot(target)["end"] + (gap_frames if prior_expected else 0)
    require(not expected or position == expected[0]["record_start"],
            "test_append_product_gap_start_mismatch")
    for clip, planned in zip(product["clips"], expected):
        row = dict(planned, record_start=position, record_end=position + planned["duration"])
        app = {"mediaPoolItem": media_by_name[clip["source_file"]],
               "startFrame": row["source_start"], "endFrame": row["source_end_exclusive"],
               "recordFrame": position, "trackIndex": 1}
        require(pool.AppendToTimeline([app]), "test_append_failed_no_replay")
        all_expected.append(row)
        actual = validate_readback(target, all_expected, product_id, plan_sha,
                                   require_marker=False, gap_frames=gap_frames)
        all_expected = reconcile_expected_rows(all_expected, actual)
        position = all_expected[-1]["record_end"]
    return all_expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "clear", "append", "repair", "recover", "readback"))
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--product", type=int)
    parser.add_argument("--target-timeline")
    parser.add_argument("--target-timeline-id")
    parser.add_argument("--plan", default="delivery/CODEX_TEST_PLAN.json")
    args = parser.parse_args()
    paths = resolve_runtime_paths(Path(args.root).resolve(), args.workspace)
    if args.operation == "prepare":
        require(bool(args.target_timeline and args.target_timeline_id),
                "reusable_test_prepare_target_identity_required")
        print(json.dumps(prepare_registered_test_timeline(
            paths, args.target_timeline, args.target_timeline_id), ensure_ascii=False))
        return
    state = paths.load_state(); run = read(paths.workspace_root / "RUN.json")
    plan_path = paths.runtime_argument(args.plan); plan = read(plan_path)
    override = read(paths.workspace_root / "delivery" / "USER_OVERRIDE.json")
    require(run["run_id"] == state["batch"]["batch_id"] == plan["batch_id"], "test_batch_identity_mismatch")
    wanted = [row["product_id"] for row in plan["products"]]
    require(1 <= len(wanted) <= 3 and len(wanted) == len(set(wanted)) and wanted == sorted(wanted)
            and state["batch"]["product_ids"] == wanted, "test_product_scope_mismatch")
    gap_frames = plan.get("inter_product_gap_frames", 25)
    require(type(gap_frames) is int and 1 <= gap_frames <= 250, "test_black_gap_frames_invalid")
    plan_sha = _sha256(plan_path)
    verify_user_ranges(state, plan, read(paths.workspace_root / "delivery" / "human_ranges.json"))
    journal = paths.workspace_root / "resolve_transactions" / "CODEX_TEST_REUSE.json"
    journal.parent.mkdir(exist_ok=True)
    resolve, manager, project, timelines, source, target = connect_project(paths, state, override)
    require(target.GetUniqueId() == override["resolve_write_authorization"]["target_timeline_id"],
            "authorized_target_changed")
    for clip in [c for p in plan["products"] for c in p["clips"]]:
        require(clip["source_file"] in {Path(p).name for p in state["video_paths"]}, "test_source_outside_registration")
    timeline_fps = float(target.GetSetting("timelineFrameRate"))
    sources = verify_source_media(paths, state, run, plan, full_hash=args.operation == "clear",
                                  target_fps=timeline_fps)
    pool = project.GetMediaPool(); available = media_items(pool.GetRootFolder())
    media_by_name = {name: available.get(str(Path(path).resolve())) for name, path in sources.items()}
    require(all(media_by_name[c["source_file"]] is not None for p in plan["products"] for c in p["clips"]),
            "test_source_missing_from_resolve_pool")
    with project_lock(paths, stable_id(project)):
        require(float(target.GetSetting("timelineFrameRate")) == timeline_fps
                and target.GetSetting("timelineResolutionWidth") == "1080"
                and target.GetSetting("timelineResolutionHeight") == "1920", "resolve_target_format_mismatch")
        if args.operation == "clear":
            require(not journal.exists(), "test_transaction_exists_inspect_before_restart")
            before = {stable_id(t): snapshot(t) for t in timelines}
            target_before = before[stable_id(target)]
            assert_authorized_target_clear(target_before, plan, override, state)
            backup = paths.workspace_root / "backups" / (run["run_id"] + "_before_test_reuse.drp")
            backup.parent.mkdir(exist_ok=True)
            require(not backup.exists(), "test_backup_exists_inspect_before_retry")
            require(manager.SaveProject() and manager.ExportProject(project.GetName(), str(backup)),
                    "test_backup_export_failed")
            backup_sha = verified_backup(backup)
            atomic_json(journal, {"batch_id": run["run_id"], "plan_sha256": plan_sha,
                                  "project_id": stable_id(project), "target_timeline_id": stable_id(target),
                                  "backup": str(backup), "backup_sha256": backup_sha,
                                  "status": "CLEAR_IN_PROGRESS", "previous_snapshot": target_before})
            old_items = item_rows(target)
            if old_items:
                require(target.DeleteClips(old_items, False), "test_prior_clip_clear_failed")
            if target.GetMarkers():
                require(target.DeleteMarkersByColor("All"), "test_prior_marker_clear_failed")
            empty = snapshot(target)
            require(not empty["rows"] and not empty["markers"], "test_target_not_empty_after_clear")
            require(all(snapshot(project.GetTimelineByIndex(i)) == before[stable_id(project.GetTimelineByIndex(i))]
                        for i in range(1, project.GetTimelineCount() + 1)
                        if stable_id(project.GetTimelineByIndex(i)) != stable_id(target)),
                    "protected_timeline_changed_during_clear")
            require(manager.SaveProject(), "test_project_save_after_clear_failed")
            body = read(journal); body.update(status="CLEARED", empty_snapshot=empty,
                                               latest_snapshot=empty)
            atomic_json(journal, body)
            print(json.dumps({"status": "CLEARED", "backup_sha256": backup_sha,
                              "removed_video_audio_items": len(old_items), "removed_markers": len(target_before["markers"])}))
            return

        require(journal.is_file(), "test_transaction_missing_clear_first")
        tx = read(journal)
        require(tx["batch_id"] == run["run_id"] and tx["plan_sha256"] == plan_sha
                and tx["project_id"] == stable_id(project) and tx["target_timeline_id"] == stable_id(target),
                "test_transaction_identity_changed")
        require(verified_backup(Path(tx["backup"])) == tx["backup_sha256"], "test_backup_changed")
        if args.operation == "append":
            require(args.product in wanted, "test_product_outside_batch")
            preceding = wanted[:wanted.index(args.product)]
            require(tx["status"] == (f"READBACK_{preceding[-1]}" if preceding else "CLEARED"),
                    "previous_product_readback_required")
            current = snapshot(target)
            require(current == tx["latest_snapshot"], "timeline_changed_since_last_readback")
            require(project.SetCurrentTimeline(target), "test_target_activation_failed")
            record_start = current["end"] + (gap_frames if tx.get("expected_rows") else 0)
            product, expected, _, _ = expected_product_rows(plan, args.product, sources, record_start)
            tx["status"] = f"APPEND_IN_PROGRESS_{args.product}"
            tx["planned_rows"] = expected
            atomic_json(journal, tx)
            prior_expected = tx.get("expected_rows", [])
            all_expected = append_product_sequential(target, pool, media_by_name, args.product,
                                                     product, expected, prior_expected, plan_sha, gap_frames)
            expected = [row for row in all_expected if row["product_id"] == args.product]
            add_product_markers(target, args.product, plan_sha, product, expected, target.GetStartFrame())
            require(manager.SaveProject(), "test_save_after_product_failed_no_replay")
            new_snapshot = snapshot(target)
            tx["expected_rows"] = all_expected
            tx["latest_snapshot"] = new_snapshot
            tx["status"] = f"APPENDED_{args.product}_PENDING_READBACK"
            tx["last_product"] = args.product
            atomic_json(journal, tx)
            print(json.dumps({"status": tx["status"], "product_id": args.product,
                              "source_clips": len(expected), "duration_seconds_approx": sum(r["duration"] for r in expected)/timeline_fps,
                              "record_start": expected[0]["record_start"], "record_end": expected[-1]["record_end"]}))
            return

        if args.operation == "repair":
            product_id = args.product
            append_status = f"APPEND_IN_PROGRESS_{product_id}"
            repairing_status = f"REPAIR_{product_id}_IN_PROGRESS"
            cleared_status = f"REPAIR_{product_id}_CLEARED"
            require(product_id in wanted and tx["status"] in (append_status, repairing_status, cleared_status),
                    "test_repair_state_or_product_mismatch")
            current = snapshot(target)
            prior_expected = tx.get("expected_rows", [])
            record_start = (prior_expected[-1]["record_end"] + gap_frames if prior_expected
                            else tx["empty_snapshot"]["start"])
            product, planned, _, _ = expected_product_rows(plan, product_id, sources, record_start)
            if tx["status"] == append_status:
                # First establish what the failed attempt did; never delete an unknown partial write.
                product_markers = [marker for marker in (target.GetMarkers() or {}).values()
                                   if str(marker.get("customData", "")).startswith(
                                       f"CODEX_TEST:{plan_sha}:{product_id}:")]
                require(not product_markers, "repair_product_marker_present_manual_recovery_required")
                repair_backup = paths.workspace_root / "backups" / (run["run_id"] + f"_product_{product_id}_repair.drp")
                require(not repair_backup.exists(), "repair_backup_exists_inspect_before_retry")
                require(manager.SaveProject() and manager.ExportProject(project.GetName(), str(repair_backup)),
                        "repair_backup_export_failed")
                repair_backup_sha = verified_backup(repair_backup)
                first, last = planned[0]["record_start"], planned[-1]["record_end"]
                product_paths = {str(Path(sources[c["source_file"]]).resolve()) for c in product["clips"]}
                repair_items = [item for item in item_rows(target)
                                if first - 2 <= item.GetStart() < last + 2]
                require(len(repair_items) == 2 * len(product["clips"])
                        and all(item.GetMediaPoolItem() and str(Path(item.GetMediaPoolItem().GetClipProperty(
                            "File Path")).resolve()) in product_paths for item in repair_items),
                        "repair_target_has_unrecognized_product_items")
                tx["status"] = repairing_status
                tx.setdefault("repairs", []).append({"product_id": product_id, "round": 1,
                                                       "reason": "local timeline placement retry",
                                                       "backup": str(repair_backup),
                                                       "backup_sha256": repair_backup_sha})
                atomic_json(journal, tx)
                require(target.DeleteClips(repair_items, False), "repair_product_delete_failed_no_replay")
                remaining = snapshot(target)
                validate_readback(target, prior_expected, product_id, plan_sha,
                                  require_marker=False, gap_frames=gap_frames)
                require(len(remaining["rows"]) == 2 * len(prior_expected), "repair_left_unexpected_timeline_items")
                tx["status"] = cleared_status
                tx["repair_clear_snapshot"] = remaining
                atomic_json(journal, tx)
            else:
                repair_record = tx.get("repairs", [])[-1]
                repair_backup_sha = repair_record["backup_sha256"]
                require(verified_backup(Path(repair_record["backup"])) == repair_record["backup_sha256"],
                        "repair_backup_changed")
                remaining = current
                validate_readback(target, prior_expected, product_id, plan_sha,
                                  require_marker=False, gap_frames=gap_frames)
                require(len(remaining["rows"]) == 2 * len(prior_expected), "repair_clear_snapshot_invalid")
                if tx["status"] == repairing_status:
                    tx["status"] = cleared_status
                    tx["repair_clear_snapshot"] = remaining
                    atomic_json(journal, tx)
                else:
                    require(current == tx.get("repair_clear_snapshot"), "repair_clear_state_changed")
            require(project.SetCurrentTimeline(target), "repair_target_activation_failed")
            repaired_expected = append_product_sequential(target, pool, media_by_name, product_id, product,
                                                           planned, prior_expected, plan_sha, gap_frames)
            expected = [row for row in repaired_expected if row["product_id"] == product_id]
            add_product_markers(target, product_id, plan_sha, product, expected, target.GetStartFrame())
            require(manager.SaveProject(), "repair_product_save_failed_no_replay")
            tx["expected_rows"] = repaired_expected
            tx["latest_snapshot"] = snapshot(target)
            tx["status"] = f"APPENDED_{product_id}_PENDING_READBACK"
            atomic_json(journal, tx)
            print(json.dumps({"status": tx["status"], "product_id": product_id, "repair_round": 1,
                              "backup_sha256": repair_backup_sha,
                              "duration_seconds_approx": sum(r["duration"] for r in expected)/timeline_fps,
                              "seam_frame": expected[1]["record_start"] if len(expected) > 1 else None}))
            return

        if args.operation == "recover":
            require(args.product in wanted and tx["status"] == f"APPEND_IN_PROGRESS_{args.product}",
                    "test_recovery_state_mismatch_no_replay")
            prior_expected = tx.get("expected_rows", [])
            record_start = (prior_expected[-1]["record_end"] + gap_frames if prior_expected
                            else tx["empty_snapshot"]["start"])
            _, planned, _, _ = expected_product_rows(plan, args.product, sources, record_start)
            all_expected = tx.get("expected_rows", []) + planned
            actual = validate_readback(target, all_expected, args.product, plan_sha,
                                       require_marker=False, gap_frames=gap_frames)
            all_expected = reconcile_expected_rows(all_expected, actual)
            expected = [row for row in all_expected if row["product_id"] == args.product]
            add_product_markers(target, args.product, plan_sha, product_clips(plan, args.product),
                                expected, target.GetStartFrame())
            require(manager.SaveProject(), "test_recovery_save_failed_no_replay")
            tx["expected_rows"] = all_expected
            tx["latest_snapshot"] = snapshot(target)
            tx["status"] = f"APPENDED_{args.product}_PENDING_READBACK"
            tx["recovery_note"] = "Recovered the already appended item from readback; no append replay."
            atomic_json(journal, tx)
            print(json.dumps({"status": tx["status"], "product_id": args.product,
                              "recovered_existing_append": True,
                              "duration_seconds_approx": sum(r["duration"] for r in expected)/timeline_fps}))
            return

        require(args.product in wanted and tx["status"] == f"APPENDED_{args.product}_PENDING_READBACK",
                "product_readback_not_pending")
        all_expected = tx.get("expected_rows", [])
        expected = [row for row in all_expected if row.get("product_id") == args.product]
        actual = validate_readback(target, all_expected, args.product, plan_sha, gap_frames=gap_frames)
        require(actual == tx["latest_snapshot"], "independent_test_readback_snapshot_mismatch")
        previous = wanted[:wanted.index(args.product)]
        require(tx["status"] == f"APPENDED_{args.product}_PENDING_READBACK", "readback_status_changed")
        tx["status"] = f"READBACK_{args.product}"
        tx.setdefault("readbacks", []).append({"product_id": args.product, "status": "PASS",
                                                "snapshot_sha256": digest(actual), "source_clip_count": len(expected)})
        atomic_json(journal, tx)
        print(json.dumps({"status": "PASS", "product_id": args.product,
                          "timeline_start": expected[0]["record_start"],
                          "timeline_end": expected[-1]["record_end"],
                          "duration_seconds_approx": sum(x["duration"] for x in expected)/timeline_fps,
                          "readback_snapshot_sha256": digest(actual)}))


if __name__ == "__main__":
    main()
