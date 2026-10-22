#!/usr/bin/env python3
"""Validate approved V2 plans, back up Resolve, append, and verify."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import re
import zipfile
from datetime import datetime
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
from resolve_connection import connect
from resolve_trial import stable_id
from runtime_paths import resolve_runtime_paths
from project_registry import atomic_json

from runtime_meta import expected_identity, load_checked_json


FORBIDDEN = re.compile(r"(优惠券|红包|抽奖|福袋|补贴|到手价|链接|上车|拍下|库存|倒计时|关注|点赞)")
CLEAN = re.compile(r"[\W_]+", re.UNICODE)
INTER_VARIANT_GAP_SECONDS = 2.0


def timeline_block_start(timeline_start: int, occupied_ends: list[int], gap_frames: int) -> int:
    """Start the first block at the timeline origin; gap only after existing content."""
    return max(occupied_ends) + gap_frames if occupied_ends else int(timeline_start)


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_timeline(project, name: str):
    for index in range(1, project.GetTimelineCount() + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline.GetName() == name:
            return timeline
    raise SystemExit(f"找不到时间线：{name}")


def semantic_duplicate(left: str, right: str) -> bool:
    left, right = CLEAN.sub("", left).lower(), CLEAN.sub("", right).lower()
    if min(len(left), len(right)) < 12:
        return False
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    longest = matcher.find_longest_match(0, len(left), 0, len(right)).size
    return (longest >= 12 and longest / min(len(left), len(right)) >= 0.60) or (
        min(len(left), len(right)) >= 20 and matcher.ratio() >= 0.72
    )


def validate_uniqueness(product_index: int, plans: list[dict]) -> None:
    for plan in plans:
        clips = plan.get("clips", [])
        for a, b in combinations(clips, 2):
            if a["source_file"] == b["source_file"]:
                overlap = min(int(a["source_end_frame"]), int(b["source_end_frame"])) - max(
                    int(a["source_start_frame"]), int(b["source_start_frame"])
                )
                if overlap > 0:
                    raise SystemExit(f"商品{product_index}同一版本源画面重复，禁止写入")
    for left, right in combinations(plans, 2):
        for a in left.get("clips", []):
            for b in right.get("clips", []):
                if a["source_file"] == b["source_file"]:
                    overlap = min(int(a["source_end_frame"]), int(b["source_end_frame"])) - max(
                        int(a["source_start_frame"]), int(b["source_start_frame"])
                    )
                    if overlap > 0:
                        raise SystemExit(f"商品{product_index}跨版本源画面重叠，禁止写入")
                ta = str((a.get("audit") or {}).get("transcript") or "")
                tb = str((b.get("audit") or {}).get("transcript") or "")
                if semantic_duplicate(ta, tb):
                    raise SystemExit(f"商品{product_index}跨版本语义重复，禁止写入")


def load_products(root: Path, wanted: list[int], identity: dict | None = None, workspace: Path | None = None) -> list[dict]:
    paths = resolve_runtime_paths(root, workspace)
    if paths.registered_scope():
        from final_quality_gate import approved_product
        return [approved_product(paths, pid) for pid in wanted]
    raise SystemExit("legacy_unregistered_delivery_disabled: use the registered fixed delivery workflow and current person-presence audit")

def item_row(item: object) -> dict:
    media = item.GetMediaPoolItem()
    return {
        "media": media.GetName() if media else None,
        "record_start": int(item.GetStart()),
        "record_end": int(item.GetEnd()),
        "source_start": int(item.GetSourceStartFrame()),
        "source_end": int(item.GetSourceEndFrame()),
    }


def validate_written_rows(expected: list[dict], video: list[dict], audio: list[dict]) -> list[str]:
    errors = []
    if len(video) != len(expected) or len(audio) != len(expected):
        errors.append(f"新增音视频数量不符：video={len(video)} audio={len(audio)} expected={len(expected)}")
    for index, row in enumerate(expected):
        if index >= len(video) or index >= len(audio):
            break
        seen = video[index]
        if (any(seen.get(key) != row.get(key) for key in ("media", "record_start", "record_end", "source_start"))
                or seen.get("source_end") not in (row["source_end"], row["source_end"] - 1)):
            errors.append(f"新增素材段{index + 1}不匹配")
        if audio[index] != seen:
            errors.append(f"新增音频段{index + 1}来源或时间不同步")
    return errors


def record_written_checkpoints(paths, wanted: list[int], report: dict, report_digest: str,
                              target_id: str) -> None:
    from product_checkpoints import capture_stage, publish_stage, read_ledger, resume, update_checkpoint
    ledger_rows = resume(paths)
    for pid in wanted:
        approved = ledger_rows[pid]
        if approved.get('status') not in ('FINAL_PASS', 'WRITTEN', 'EXPORT_VERIFIED'):
            raise SystemExit('resolve_write_verified_but_product_checkpoint_not_passed:'
                             + str(approved.get('status')) + ':' + str(approved.get('reason'))
                             + '; do_not_replay')
        cursor_path = paths.project_records_dir / 'cursors' / f'{pid}.json'
        cursor = json.loads(cursor_path.read_text(encoding='utf-8'))
        plan_ref, audit_ref = approved.get('plan_ref'), approved.get('evidence_ref')
        if not plan_ref or not audit_ref:
            raise SystemExit('resolve_write_verified_but_checkpoint_evidence_missing; do_not_replay')
        version = 'resolve-write-readback-v1'
        body = {'plan_digest': approved['plan_digest'], 'previous_evidence': audit_ref,
                'verdict': 'VERIFIED', 'delivery_receipt': report_digest,
                'target_timeline_id': target_id, 'product_ids': wanted}
        snapshot = capture_stage(paths, product_id=pid, encoding_id=approved['encoding_id'],
                                 stage='written', version=version, model_version='resolve_api',
                                 dependencies=[plan_ref, audit_ref])
        evidence_ref = publish_stage(paths, product_id=pid, encoding_id=approved['encoding_id'],
                                     stage='written', version=version, model_version='resolve_api',
                                     body=body, dependencies=[plan_ref, audit_ref], snapshot=snapshot)
        update_checkpoint(paths, product_id=pid, encoding_id=approved['encoding_id'], status='WRITTEN',
                          plan_ref=plan_ref, evidence_ref=evidence_ref,
                          expected_revision=read_ledger(paths)['revision'])


def recover_registered_append(paths, wanted: list[int]) -> None:
    """Finish verification metadata for an append that already changed Resolve; never reappend."""
    state = paths.load_state()
    batch_id = str(state['batch']['batch_id'])
    scope_key = '-'.join(str(pid) for pid in wanted)
    directory = paths.cache_dir / 'resolve_delivery' / batch_id
    tx_path = directory / f'resolve_append_transaction_{scope_key}.json'
    report_path = directory / f'resolve_write_report_{scope_key}.json'
    if not tx_path.is_file() or not report_path.is_file():
        raise SystemExit('append_recovery_artifacts_missing; do_not_replay')
    tx, report = read(tx_path), read(report_path)
    if (tx.get('status') != 'APPENDED_PENDING_VERIFY'
            or tx.get('products') != wanted
            or report.get('passed') is not True or report.get('errors')
            or report.get('product_ids') != wanted
            or report.get('project_id') != state['project_identity']['resolve_project_id']
            or report.get('target_timeline_id') != state['resolve']['target_timeline_id']):
        raise SystemExit('append_recovery_scope_or_report_invalid; do_not_replay')
    backup = Path(tx.get('backup') or '')
    if not backup.is_file() or not zipfile.is_zipfile(backup):
        raise SystemExit('append_recovery_backup_missing_or_invalid; do_not_replay')
    resolve = connect(status_path=paths.workspace_root/'resolve_connection_status.json')
    if not resolve:
        raise SystemExit('append_recovery_resolve_unavailable; do_not_replay')
    project = resolve.GetProjectManager().GetCurrentProject()
    if (not project or stable_id(project) != report['project_id']
            or project.GetName() != report['project']):
        raise SystemExit('append_recovery_project_identity_mismatch; do_not_replay')
    from coldstart_resolve import snapshot as resolve_snapshot
    timeline = next((project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount()+1)
                     if stable_id(project.GetTimelineByIndex(i)) == report['target_timeline_id']), None)
    if not timeline or timeline.GetName() != report['target_timeline']:
        raise SystemExit('append_recovery_target_identity_mismatch; do_not_replay')
    if resolve_snapshot(timeline) != report.get('target_after'):
        raise SystemExit('append_recovery_live_timeline_differs_from_verified_report; do_not_replay')
    old_video = int(tx['existing_video_count'])
    old_audio = int(tx['existing_audio_count'])
    video = list(timeline.GetItemListInTrack('video', 1) or [])
    audio = list(timeline.GetItemListInTrack('audio', 1) or [])
    expected = report.get('expected', [])
    errors = validate_written_rows(expected, [item_row(x) for x in video[old_video:]],
                                  [item_row(x) for x in audio[old_audio:]])
    marker_rows = timeline.GetMarkers() or {}
    for marker in report.get('markers', []):
        offset = int(marker['frame']) - int(timeline.GetStartFrame())
        if (marker_rows.get(offset, {}).get('customData')
                != f"tb_cut_product_start:{marker['encoding_id']}"):
            errors.append('append_recovery_product_marker_missing')
    if errors:
        raise SystemExit('append_recovery_readback_failed:' + ','.join(errors) + '; do_not_replay')
    report_digest = hashlib.sha256(json.dumps(report, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()
    record_written_checkpoints(paths, wanted, report, report_digest, report['target_timeline_id'])
    atomic_json(tx_path, {'status': 'VERIFIED', 'report': str(report_path),
                          'report_sha256': report_digest, 'backup': str(backup),
                          'products': wanted, 'recovered_without_reappend': True})
    print(f'追加事务恢复并回读通过：商品{len(wanted)}款；未重复追加。')


def snapshot_preserved(before: dict, after: dict) -> bool:
    """Check append-only preservation while allowing rows, markers and end to grow."""
    for key in ("id", "name", "start", "settings"):
        if before.get(key) != after.get(key):
            return False
    encode = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    old_rows = Counter(encode(row) for row in before.get("rows", []))
    new_rows = Counter(encode(row) for row in after.get("rows", []))
    if any(count > new_rows[key] for key, count in old_rows.items()):
        return False
    old_markers = before.get("markers", {})
    new_markers = after.get("markers", {})
    return all(new_markers.get(key) == value for key, value in old_markers.items())


def registered_append_authorization(paths, state, target_id: str, product_ids: list[int]) -> dict:
    path = paths.workspace_root / "delivery" / "USER_OVERRIDE.json"
    if not path.is_file():
        raise SystemExit("existing_target_append_user_authorization_missing")
    override = read(path)
    authorization = override.get("resolve_write_authorization", {})
    if (override.get("scope") != "this_batch_only" or override.get("confirmed_by_user") is not True
            or authorization.get("action") != "append_existing_target"
            or authorization.get("target_timeline") != state["resolve"]["target_timeline"]
            or authorization.get("target_timeline_id") != target_id
            or authorization.get("project_id") != state["project_identity"]["resolve_project_id"]
            or authorization.get("product_ids") != product_ids
            or authorization.get("clear_existing") is not False):
        raise SystemExit("existing_target_append_authorization_scope_mismatch")
    return authorization


def repair_registered_timeline_layout(paths, wanted: list[int]) -> None:
    """Remove only recorded review-preview clips and re-lay approved variants with 2s gaps."""
    state = paths.load_state()
    batch_id = str(state['batch']['batch_id'])
    if wanted != [int(pid) for pid in state['batch']['product_ids']]:
        raise SystemExit('layout_repair_must_cover_the_exact_batch')
    if not paths.registered_scope():
        raise SystemExit('layout_repair_requires_registered_project')
    directory = paths.cache_dir / 'resolve_delivery' / batch_id
    write_report_path = directory / f"resolve_write_report_{'-'.join(map(str, wanted))}.json"
    tx_path = directory / 'resolve_layout_repair_transaction.json'
    repair_report_path = directory / 'resolve_layout_repair_report.json'
    preview_directory = paths.cache_dir / 'resolve_semantic_preview' / batch_id
    preview_report_path = preview_directory / 'preview_report.json'
    preview_tx_path = preview_directory / 'preview_transaction.json'
    if tx_path.exists() or repair_report_path.exists():
        raise SystemExit('layout_repair_transaction_exists_inspect_before_retry')
    if not all(path.is_file() for path in (write_report_path, preview_report_path, preview_tx_path)):
        raise SystemExit('layout_repair_evidence_missing')
    write_report, preview_report, preview_tx = map(read, (write_report_path, preview_report_path, preview_tx_path))
    project_id = state['project_identity']['resolve_project_id']
    target_id = state['resolve']['target_timeline_id']
    if (write_report.get('passed') is not True or write_report.get('product_ids') != wanted
            or write_report.get('project_id') != project_id
            or write_report.get('target_timeline_id') != target_id
            or preview_report.get('passed') is not True
            or preview_report.get('target_timeline_id') != target_id
            or preview_tx.get('status') != 'VERIFIED'
            or preview_tx.get('mode') != 'semantic_review_preview_only'
            or preview_tx.get('project_id') != project_id
            or preview_tx.get('target_timeline_id') != target_id):
        raise SystemExit('layout_repair_report_identity_or_status_mismatch')

    expected_old = write_report.get('expected', [])
    preview_snapshot_rows = [row for row in write_report.get('target_before', {}).get('rows', [])
                             if row.get('track') == 1 and row.get('kind') in ('video', 'audio')]
    preview_video_rows = [row for row in preview_snapshot_rows if row['kind'] == 'video']
    preview_audio_rows = [row for row in preview_snapshot_rows if row['kind'] == 'audio']
    if (not expected_old or not preview_video_rows or len(preview_video_rows) != len(preview_audio_rows)
            or sorted({int(row['product_index']) for row in expected_old}) != wanted):
        raise SystemExit('layout_repair_recorded_timeline_scope_invalid')
    preview_variant_keys = [(int(row['product_index']), str(variant_id))
                            for row in preview_report.get('products', [])
                            for variant_id in row.get('preview_variants', [])]
    if not preview_variant_keys:
        raise SystemExit('layout_repair_preview_scope_empty')
    expected_group_keys = list(dict.fromkeys((int(row['product_index']), int(row['variant_index']))
                                             for row in expected_old))
    groups_by_product = {pid: [] for pid in wanted}
    for pid, variant_index in expected_group_keys:
        rows = [row for row in expected_old
                if int(row['product_index']) == pid and int(row['variant_index']) == variant_index]
        if not rows:
            raise SystemExit('layout_repair_variant_group_empty')
        groups_by_product[pid].append(rows)
    encoding_by_product = {int(row['product_index']): str(row['encoding_id'])
                           for row in write_report.get('markers', [])}
    if set(encoding_by_product) != set(wanted):
        raise SystemExit('layout_repair_product_marker_scope_invalid')

    resolve = connect(status_path=paths.workspace_root / 'resolve_connection_status.json')
    if not resolve:
        raise SystemExit('layout_repair_resolve_unavailable')
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject() if manager else None
    if (not project or project.GetName() != state['resolve']['project_name']
            or stable_id(project) != project_id):
        raise SystemExit('layout_repair_project_identity_mismatch')
    from coldstart_resolve import snapshot as resolve_snapshot
    timelines = [project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)]
    timeline_by_id = {stable_id(timeline): timeline for timeline in timelines}
    target = timeline_by_id.get(target_id)
    source = timeline_by_id.get(state['resolve']['source_timeline_id'])
    if (not target or not source or target.GetName() != state['resolve']['target_timeline']
            or source.GetName() != state['resolve']['source_timeline'] or target is source):
        raise SystemExit('layout_repair_timeline_identity_mismatch')
    before = {stable_id(timeline): resolve_snapshot(timeline) for timeline in timelines}
    if before[target_id] != write_report.get('target_after'):
        raise SystemExit('layout_repair_live_timeline_changed_since_verified_write')
    for kind in ('video', 'audio'):
        for track in range(2, int(target.GetTrackCount(kind)) + 1):
            if target.GetItemListInTrack(kind, track):
                raise SystemExit('layout_repair_unrecognized_additional_track_content')
    for track in range(1, int(target.GetTrackCount('subtitle')) + 1):
        if target.GetItemListInTrack('subtitle', track):
            raise SystemExit('layout_repair_unrecognized_subtitle_content')

    current_video = sorted(list(target.GetItemListInTrack('video', 1) or []), key=lambda item: int(item.GetStart()))
    current_audio = sorted(list(target.GetItemListInTrack('audio', 1) or []), key=lambda item: int(item.GetStart()))
    if (len(current_video) != len(expected_old) + len(preview_video_rows)
            or len(current_audio) != len(expected_old) + len(preview_audio_rows)):
        raise SystemExit('layout_repair_live_clip_count_mismatch')

    def item_snapshot_signature(item, kind):
        media = item.GetMediaPoolItem()
        if not media:
            return None
        return (kind, 1, int(item.GetStart()), int(item.GetEnd()), int(item.GetDuration()),
                stable_id(media), media.GetClipProperty('File Path'),
                int(item.GetSourceStartFrame()), int(item.GetSourceEndFrame()))

    def snapshot_signature(row):
        return (row['kind'], int(row['track']), int(row['record_start']), int(row['record_end']),
                int(row['duration']), row.get('media_id'), row.get('media_path'),
                int(row['source_start']), int(row['source_end_raw']))

    preview_signatures = {kind: Counter(snapshot_signature(row) for row in preview_snapshot_rows
                                        if row['kind'] == kind)
                          for kind in ('video', 'audio')}

    def split_preview(items, kind):
        pending, formal = [], []
        remaining = preview_signatures[kind].copy()
        for item in items:
            signature = item_snapshot_signature(item, kind)
            if signature in remaining and remaining[signature] > 0:
                pending.append(item)
                remaining[signature] -= 1
            else:
                formal.append(item)
        if any(remaining.values()):
            raise SystemExit('layout_repair_preview_snapshot_item_missing')
        return pending, formal

    pending_video, formal_video = split_preview(current_video, 'video')
    pending_audio, formal_audio = split_preview(current_audio, 'audio')
    if len(pending_video) != len(preview_video_rows) or len(pending_audio) != len(preview_audio_rows):
        raise SystemExit('layout_repair_preview_clip_count_mismatch')

    def matches_expected(item, expected):
        actual = item_row(item)
        return (actual['media'] == expected['media']
                and actual['record_start'] == int(expected['record_start'])
                and actual['record_end'] == int(expected['record_end'])
                and actual['source_start'] == int(expected['source_start'])
                and actual['source_end'] in (int(expected['source_end']), int(expected['source_end']) - 1))

    # Approved rows must still match the immutable write report exactly.
    for index, expected in enumerate(expected_old):
        if not matches_expected(formal_video[index], expected) or not matches_expected(formal_audio[index], expected):
            raise SystemExit('layout_repair_formal_clip_readback_mismatch')
    markers = target.GetMarkers() or {}
    preview_custom = set()
    for marker in preview_report.get('markers', []):
        encoding_id = str(marker['encoding_id'])
        custom = (f"tb_cut_semantic_preview:{batch_id}:product:{encoding_id}"
                  if marker['kind'] == 'product' else
                  f"tb_cut_semantic_preview:{batch_id}:product:{encoding_id}:{marker['variant_id']}")
        preview_custom.add(custom)
    formal_custom = {f"tb_cut_product_start:{encoding_by_product[pid]}" for pid in wanted}
    marker_by_custom = {row.get('customData'): (int(offset), row) for offset, row in markers.items()
                        if isinstance(row, dict) and row.get('customData')}
    if not preview_custom.issubset(marker_by_custom) or not formal_custom.issubset(marker_by_custom):
        raise SystemExit('layout_repair_expected_marker_missing')
    if sum(custom.startswith(f'tb_cut_semantic_preview:{batch_id}:') for custom in marker_by_custom) != len(preview_custom):
        raise SystemExit('layout_repair_unrecognized_preview_marker')

    fps = float(project.GetSetting('timelineFrameRate') or project.GetSetting('timelinePlaybackFrameRate') or 30)
    gap = round(2.0 * fps)
    # A layout repair rebuilds the registered batch from the timeline origin.
    # Reusing its former marker frame preserves any stale leading offset.
    first_frame = int(target.GetStartFrame())
    expected_new, new_product_markers = [], []
    record = first_frame
    variant_boundaries = []
    total_variants = len(expected_group_keys)
    variant_number = 0
    for pid in wanted:
        product_start = record
        for variant_index, group_rows in enumerate(groups_by_product[pid], 1):
            start_of_variant = record
            for clip in group_rows:
                begin, end = int(clip['source_start']), int(clip['source_end'])
                duration = int(clip['record_end']) - int(clip['record_start'])
                if duration <= 0 or duration != end - begin:
                    raise SystemExit('layout_repair_invalid_source_duration')
                expected_new.append({'product_index': pid, 'variant_index': variant_index,
                                     'media': clip['media'], 'record_start': record,
                                     'record_end': record + duration, 'source_start': begin,
                                     'source_end': end})
                record += duration
            variant_boundaries.append({'product_index': pid, 'variant_index': variant_index,
                                       'start': start_of_variant, 'end': record})
            variant_number += 1
            if variant_number < total_variants:
                record += gap
        new_product_markers.append({'product_id': pid, 'frame': product_start,
                                    'encoding_id': encoding_by_product[pid],
                                    'old_marker': marker_by_custom[f"tb_cut_product_start:{encoding_by_product[pid]}"][1]})
    for left, right in zip(variant_boundaries, variant_boundaries[1:]):
        if right['start'] - left['end'] != gap:
            raise SystemExit('layout_repair_gap_plan_invalid')

    source_media = {}
    for item in source.GetItemListInTrack('video', 1) or []:
        media = item.GetMediaPoolItem()
        if media:
            name = media.GetName()
            if name in source_media and source_media[name].GetClipProperty('File Path') != media.GetClipProperty('File Path'):
                raise SystemExit('layout_repair_ambiguous_source_media')
            source_media[name] = media
    append_infos = []
    for row in expected_new:
        media = source_media.get(row['media'])
        if media is None:
            raise SystemExit('layout_repair_source_media_missing')
        append_infos.append({'mediaPoolItem': media, 'startFrame': row['source_start'],
                             'endFrame': row['source_end'], 'recordFrame': row['record_start'],
                             'trackIndex': 1})

    backup_dir = paths.cache_dir / 'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{project.GetName()}_before_layout_repair_{datetime.now():%Y%m%d_%H%M%S}.drp"
    if not manager.SaveProject() or not manager.ExportProject(project.GetName(), str(backup), False):
        raise SystemExit('layout_repair_backup_export_failed')
    if not zipfile.is_zipfile(backup):
        raise SystemExit('layout_repair_backup_invalid')
    with zipfile.ZipFile(backup) as archive:
        if not archive.namelist() or archive.testzip() is not None:
            raise SystemExit('layout_repair_backup_corrupt')
    atomic_json(tx_path, {'status': 'STARTED', 'mode': 'remove_preview_and_insert_variant_gaps',
                          'project_id': project_id, 'target_timeline_id': target_id,
                          'batch_id': batch_id, 'product_ids': wanted,
                          'before_snapshot': before[target_id], 'backup': str(backup),
                          'preview_clip_count_per_kind': len(preview_video_rows),
                          'formal_clip_count_per_kind': len(expected_old),
                          'expected_new': expected_new, 'variant_boundaries': variant_boundaries,
                          'preview_custom_data': sorted(preview_custom),
                          'formal_custom_data': sorted(formal_custom)})
    if not project.SetCurrentTimeline(target):
        raise SystemExit('layout_repair_cannot_activate_target')
    if {stable_id(t): resolve_snapshot(t) for t in timelines} != before:
        raise SystemExit('layout_repair_timeline_changed_before_mutation')
    tx = read(tx_path)
    tx['status'] = 'MUTATION_IN_PROGRESS'
    atomic_json(tx_path, tx)
    all_items = pending_video + formal_video + pending_audio + formal_audio
    if not target.DeleteClips(all_items, False):
        tx['status'] = 'DELETE_FAILED_NO_RETRY'
        atomic_json(tx_path, tx)
        raise SystemExit('layout_repair_delete_failed_do_not_retry')
    for custom_data in preview_custom | formal_custom:
        target.DeleteMarkerByCustomData(custom_data)
        if any(isinstance(row, dict) and row.get('customData') == custom_data
               for row in (target.GetMarkers() or {}).values()):
            tx['status'] = 'MARKER_DELETE_FAILED_NO_RETRY'
            atomic_json(tx_path, tx)
            raise SystemExit('layout_repair_marker_delete_failed_do_not_retry')
    if target.GetItemListInTrack('video', 1) or target.GetItemListInTrack('audio', 1):
        tx['status'] = 'DELETE_READBACK_FAILED_NO_RETRY'
        atomic_json(tx_path, tx)
        raise SystemExit('layout_repair_clip_delete_readback_failed_do_not_retry')
    tx['status'] = 'CLEARED_PENDING_REAPPEND'
    atomic_json(tx_path, tx)
    created = project.GetMediaPool().AppendToTimeline(append_infos)
    if not created or len(created) != len(append_infos):
        tx['status'] = 'REAPPEND_INCOMPLETE_NO_RETRY'
        atomic_json(tx_path, tx)
        raise SystemExit('layout_repair_reappend_incomplete_do_not_retry')
    tx['status'] = 'REAPPENDED_PENDING_VERIFY'
    atomic_json(tx_path, tx)
    for marker in new_product_markers:
        old = marker['old_marker']
        if not target.AddMarker(marker['frame'] - int(target.GetStartFrame()),
                                old.get('color', 'Blue'), old.get('name', marker['encoding_id']),
                                old.get('note', ''), int(old.get('duration', 1) or 1),
                                f"tb_cut_product_start:{marker['encoding_id']}"):
            tx['status'] = 'PRODUCT_MARKER_ADD_FAILED_NO_RETRY'
            atomic_json(tx_path, tx)
            raise SystemExit('layout_repair_product_marker_add_failed_do_not_retry')
    if not manager.SaveProject():
        tx['status'] = 'SAVE_FAILED_NO_RETRY'
        atomic_json(tx_path, tx)
        raise SystemExit('layout_repair_save_failed_do_not_retry')

    video = sorted(list(target.GetItemListInTrack('video', 1) or []), key=lambda item: int(item.GetStart()))
    audio = sorted(list(target.GetItemListInTrack('audio', 1) or []), key=lambda item: int(item.GetStart()))
    errors = validate_written_rows(expected_new, [item_row(item) for item in video],
                                  [item_row(item) for item in audio])
    readback_boundaries = []
    for marker in new_product_markers:
        expected_custom = f"tb_cut_product_start:{marker['encoding_id']}"
        marker_rows = target.GetMarkers() or {}
        offset = marker['frame'] - int(target.GetStartFrame())
        if (marker_rows.get(offset, {}) or {}).get('customData') != expected_custom:
            errors.append('layout_repair_product_marker_readback_failed')
    for custom_data in preview_custom:
        if any(isinstance(row, dict) and row.get('customData') == custom_data
               for row in (target.GetMarkers() or {}).values()):
            errors.append('layout_repair_preview_marker_remains')
    for boundary in variant_boundaries:
        rows = [row for row in expected_new if row['product_index'] == boundary['product_index']
                and row['variant_index'] == boundary['variant_index']]
        actual_start = rows[0]['record_start']
        actual_end = rows[-1]['record_end']
        readback_boundaries.append({**boundary, 'readback_start': actual_start, 'readback_end': actual_end})
    after = {stable_id(t): resolve_snapshot(t) for t in timelines}
    for timeline in timelines:
        uid = stable_id(timeline)
        if uid == target_id:
            if after[uid]['id'] != before[uid]['id'] or after[uid]['settings'] != before[uid]['settings']:
                errors.append('layout_repair_target_identity_or_settings_changed')
        elif after[uid] != before[uid]:
            errors.append('layout_repair_non_target_timeline_changed')
    report = {'schema_version': 1, 'passed': not errors, 'errors': errors,
              'mode': 'remove_preview_and_insert_variant_gaps', 'project_id': project_id,
              'target_timeline_id': target_id, 'product_ids': wanted,
              'removed_preview_clip_rows_per_kind': len(preview_video_rows),
              'rewritten_approved_clip_rows_per_kind': len(expected_new),
              'inserted_gap_frames': gap, 'inserted_gap_seconds': gap / fps,
              'variant_boundaries': readback_boundaries, 'markers': new_product_markers,
              'backup': str(backup), 'target_before': before[target_id], 'target_after': after[target_id]}
    atomic_json(repair_report_path, report)
    tx.update(status='VERIFIED' if not errors else 'FAILED_VERIFICATION',
              report=str(repair_report_path), report_sha256=hashlib.sha256(
                  json.dumps(report, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':')).encode()).hexdigest())
    atomic_json(tx_path, tx)
    if errors:
        raise SystemExit('layout_repair_readback_failed:' + ','.join(errors) + '; do_not_retry')
    print(json.dumps({'status': 'VERIFIED', 'removed_preview_variants': len(preview_variant_keys),
                      'removed_preview_clip_rows': len(preview_video_rows),
                      'retained_approved_variants': total_variants,
                      'rewritten_clip_rows': len(expected_new), 'gap_frames': gap,
                      'product_start_markers': len(new_product_markers), 'backup': str(backup)},
                     ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    parser.add_argument("--products")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--recover-append", action="store_true",
                        help="Read back and finalize an already appended transaction; never append media")
    parser.add_argument("--repair-layout", action="store_true",
                        help="Remove this batch's semantic-review preview and add two-second gaps between approved variants")
    parser.add_argument('--trial-write', action='store_true', help='Dedicated bounded trial only; formal timeline never writable')
    parser.add_argument('--append-registered-target', action='store_true',
                        help='Append only to the user-authorized, registered final timeline after all quality gates pass')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    if args.repair_layout:
        state = paths.load_state()
        wanted = ([int(v) for v in args.products.split(',') if v.strip()] if args.products
                  else [int(v) for v in state['batch']['product_ids']])
        repair_registered_timeline_layout(paths, wanted)
        return
    if args.recover_append:
        state = paths.load_state()
        wanted = ([int(v) for v in args.products.split(',') if v.strip()] if args.products
                  else [int(v) for v in state['batch']['product_ids']])
        recover_registered_append(paths, wanted)
        return
    if paths.registered_scope():
        from project_scheduler import project_audit
        state = paths.load_state()
        failed = {int(value) for value in state.get('batch', {}).get('failed_product_ids', [])}
        wanted = ([int(v) for v in args.products.split(',') if v.strip()] if args.products
                  else [int(v) for v in state['batch']['product_ids'] if int(v) not in failed])
        if (not wanted or len(wanted) != len(set(wanted))
                or any(pid not in state['batch']['product_ids'] or pid in failed for pid in wanted)):
            raise SystemExit('append_product_scope_invalid_or_failed')
        result = project_audit(paths, product_ids=wanted)
        if not result['final_review_complete']:
            raise SystemExit('project_quality_preflight_failed:' + ','.join(result['errors']))
        if args.check_only:
            if args.append_registered_target:
                resolve = connect(status_path=paths.workspace_root/'resolve_connection_status.json')
                if not resolve: raise SystemExit('未连接达芬奇')
                project = resolve.GetProjectManager().GetCurrentProject()
                if (not project or project.GetName() != state['resolve']['project_name']
                        or stable_id(project) != state['project_identity']['resolve_project_id']):
                    raise SystemExit('当前达芬奇项目身份不匹配')
                from coldstart_resolve import snapshot as resolve_snapshot
                live = [project.GetTimelineByIndex(i) for i in range(1,project.GetTimelineCount()+1)]
                inventory = {stable_id(t):resolve_snapshot(t) for t in live}
                target_id = state['resolve'].get('target_timeline_id')
                source_id = state['resolve'].get('source_timeline_id')
                target = next((t for t in live if stable_id(t)==target_id),None)
                if (not target or not source_id or source_id == target_id
                        or target.GetName() != state['resolve']['target_timeline']):
                    raise SystemExit('registered_target_identity_missing_or_mismatched')
                preflight = {'project_id': stable_id(project), 'protected': inventory}
                if not target or target.GetName()==state['resolve']['source_timeline']:
                    raise SystemExit('登记目标与源时间线冲突')
                if not target.GetTrackCount('video') or not target.GetTrackCount('audio'):
                    raise SystemExit('登记目标缺少音视频轨，拒绝自动改动时间线结构')
                registered_append_authorization(paths,state,target_id,wanted)
                from registered_pipeline import inputs
                titles,_ = inputs(paths)
                product_by_id = {int(row['product_index']):row for row in titles['products']}
                existing_custom = {row.get('customData','') for row in (target.GetMarkers() or {}).values()
                                   if isinstance(row,dict)}
                if any(f"tb_cut_product_start:{product_by_id[pid]['encoding_id']}" in existing_custom
                       for pid in wanted):
                    raise SystemExit('目标时间线已含本批商品标记，拒绝重复追加')
                result['append_target_preflight'] = {'status':'PASS','timeline_id':target_id,
                    'video_track_count':target.GetTrackCount('video'),'audio_track_count':target.GetTrackCount('audio'),
                    'existing_video_count':sum(len(target.GetItemListInTrack('video',i) or [])
                        for i in range(1,target.GetTrackCount('video')+1)),
                    'existing_audio_count':sum(len(target.GetItemListInTrack('audio',i) or [])
                        for i in range(1,target.GetTrackCount('audio')+1))}
            print(json.dumps(result, ensure_ascii=False))
            return
        if args.trial_write:
            from resolve_trial import project_preflight, prepare_target, write_product
            from trial_session import validate_trial
            project_preflight(paths)
            trial = validate_trial(paths)
            wanted = [int(v) for v in args.products.split(',')] if args.products else trial['products']
            if sorted(wanted) != trial['products']:
                raise SystemExit('trial_write_scope_must_match_frozen_three_to_five_products')
            resolve = connect(status_path=paths.workspace_root/'resolve_connection_status.json')
            if not resolve: raise SystemExit('trial_resolve_unavailable')
            binding = prepare_target(paths,resolve)
            result = [write_product(paths,resolve,binding,pid) for pid in wanted]
            print(json.dumps(result))
            return
        if not args.append_registered_target:
            raise SystemExit('registered_resolve_write_requires_explicit_append_registered_target')
        identity = expected_identity(paths, include_ranges=False)
        products = load_products(root, wanted, identity, paths.workspace_root)
        from registered_pipeline import inputs
        title_index, _ = inputs(paths)
        title_by_id = {int(row['product_index']): row['title'] for row in title_index['products']}
        for product in products:
            product['title'] = title_by_id[product['product_index']]
        config = state['resolve']
        resolve = connect(status_path=paths.workspace_root/'resolve_connection_status.json')
        if not resolve:
            raise SystemExit('未连接达芬奇')
        manager = resolve.GetProjectManager()
        project = manager.GetCurrentProject() if manager else None
        if (not project or project.GetName() != config['project_name']
                or stable_id(project) != state['project_identity']['resolve_project_id']):
            raise SystemExit('当前达芬奇项目身份不匹配')
        from coldstart_resolve import snapshot as resolve_snapshot
        timelines = [project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount()+1)]
        target_id = config.get('target_timeline_id')
        source_id = config.get('source_timeline_id')
        before_inventory = {stable_id(t): resolve_snapshot(t) for t in timelines}
        if not target_id or not source_id or target_id == source_id:
            raise SystemExit('registered_source_target_ids_missing_or_conflicting')
        preflight = {'project_id': stable_id(project), 'protected': before_inventory}
        source = next((t for t in timelines if stable_id(t) == source_id), None)
        target = next((t for t in timelines if stable_id(t) == target_id), None)
        if (not source or not target or source.GetName() != config['source_timeline']
                or target.GetName() != config['target_timeline']):
            raise SystemExit('源或目标时间线身份不匹配')
        registered_append_authorization(paths, state, target_id, wanted)
        batch_key = str(state['batch']['batch_id'])
        scope_key = '-'.join(str(pid) for pid in wanted)
        report_path = paths.cache_dir / 'resolve_delivery' / batch_key / f'resolve_write_report_{scope_key}.json'
        transaction_path = paths.cache_dir / 'resolve_delivery' / batch_key / f'resolve_append_transaction_{scope_key}.json'
        transaction_path.parent.mkdir(parents=True, exist_ok=True)
        if report_path.exists() or transaction_path.exists():
            raise SystemExit('append_transaction_exists_inspect_before_retry_no_replay')
        project_fps = float(project.GetSetting('timelineFrameRate') or project.GetSetting('timelinePlaybackFrameRate') or 30)
        media_items = {}
        media_fps = {}
        for item in source.GetItemListInTrack('video', 1) or []:
            media = item.GetMediaPoolItem()
            if not media:
                continue
            media_name = media.GetName()
            if media_name in media_items and media_items[media_name].GetClipProperty('File Path') != media.GetClipProperty('File Path'):
                raise SystemExit(f'素材同名但来源不同，禁止写入：{media_name}')
            media_items[media_name] = media
            try:
                media_fps[media_name] = float((media.GetClipProperty() or {}).get('FPS') or 0)
            except (TypeError, ValueError):
                media_fps[media_name] = 0.0
        old_video = list(target.GetItemListInTrack('video', 1) or [])
        old_audio = list(target.GetItemListInTrack('audio', 1) or [])
        old_custom = {row.get('customData', '') for row in (target.GetMarkers() or {}).values() if isinstance(row, dict)}
        for product in products:
            if f"tb_cut_product_start:{product['encoding_id']}" in old_custom:
                raise SystemExit(f"商品{product['product_index']}已经写入，拒绝重复")
        gap = round(INTER_VARIANT_GAP_SECONDS * project_fps)
        end_positions = [int(item.GetEnd()) for kind in ('video','audio')
                         for track in range(1, int(target.GetTrackCount(kind))+1)
                         for item in (target.GetItemListInTrack(kind,track) or [])]
        record = timeline_block_start(int(target.GetStartFrame()), end_positions, gap)
        append_infos, expected, markers = [], [], []
        total_variants = sum(len(product['plans']) for product in products)
        variant_position = 0
        for product in products:
            product_start = None
            for variant_index, plan in enumerate(product['plans'], 1):
                if product_start is None:
                    product_start = record
                for clip in plan['clips']:
                    media_name = clip['source_file']
                    if media_name not in media_items:
                        raise SystemExit(f'素材时间线找不到：{media_name}')
                    plan_fps = float(plan.get('fps') or project_fps)
                    source_fps = media_fps.get(media_name)
                    if not source_fps or abs(source_fps-plan_fps)>1e-6 or abs(plan_fps-project_fps)>1e-6:
                        raise SystemExit(f'素材帧率与方案/时间线不一致：{media_name}')
                    start, end = int(clip['source_start_frame']), int(clip['source_end_frame'])
                    if end <= start:
                        raise SystemExit(f'素材片段无效：{media_name} {start}-{end}')
                    append_infos.append({'mediaPoolItem':media_items[media_name],'startFrame':start,'endFrame':end,
                                         'recordFrame':record,'trackIndex':1})
                    expected.append({'product_index':product['product_index'],'variant_index':variant_index,
                                     'media':media_name,'record_start':record,'record_end':record+end-start,
                                     'source_start':start,'source_end':end})
                    record += end-start
                variant_position += 1
                if variant_position < total_variants:
                    record += gap
            markers.append({'frame':product_start,'encoding_id':product['encoding_id'],
                            'product_index':product['product_index'],'title':product['title']})
        print(f'预检通过：追加商品{len(products)}款，方案{sum(len(p["plans"]) for p in products)}条，素材段{len(expected)}个')
        if not append_infos:
            raise SystemExit('append_plan_empty')
        if not target.GetTrackCount('video') or not target.GetTrackCount('audio'):
            raise SystemExit('登记目标缺少音视频轨，拒绝自动改动时间线结构')
        backup_dir = paths.cache_dir / 'backups'; backup_dir.mkdir(parents=True,exist_ok=True)
        backup = backup_dir / f"{project.GetName()}_before_append_{datetime.now():%Y%m%d_%H%M%S}.drp"
        if not manager.SaveProject() or not manager.ExportProject(project.GetName(),str(backup),False):
            raise SystemExit('写入前保存或备份失败')
        if not zipfile.is_zipfile(backup):
            raise SystemExit('写入前项目备份校验失败')
        with zipfile.ZipFile(backup) as archive:
            if not archive.namelist() or archive.testzip() is not None:
                raise SystemExit('写入前项目备份损坏')
        atomic_json(transaction_path,{'status':'STARTED','project_id':stable_id(project),'target_timeline_id':target_id,
                    'products':wanted,'existing_video_count':len(old_video),'existing_audio_count':len(old_audio),
                    'backup':str(backup),'preflight_snapshot':preflight})
        if not project.SetCurrentTimeline(target):
            raise SystemExit('无法激活登记目标时间线')
        after_tracks = {stable_id(t): resolve_snapshot(t) for t in timelines}
        if after_tracks != preflight['protected']:
            raise SystemExit('写入前时间线已变化，拒绝追加')
        atomic_json(transaction_path,{'status':'APPEND_IN_PROGRESS','project_id':stable_id(project),
                    'target_timeline_id':target_id,'products':wanted,'existing_video_count':len(old_video),
                    'existing_audio_count':len(old_audio),'backup':str(backup),'preflight_snapshot':preflight})
        created = project.GetMediaPool().AppendToTimeline(append_infos)
        if not created or len(created) != len(append_infos):
            raise SystemExit(f'追加结果不完整；禁止自动重试；备份：{backup}')
        atomic_json(transaction_path,{'status':'APPENDED_PENDING_VERIFY','project_id':stable_id(project),
                    'target_timeline_id':target_id,'products':wanted,'existing_video_count':len(old_video),
                    'existing_audio_count':len(old_audio),'backup':str(backup),'preflight_snapshot':preflight})
        for marker in markers:
            if not target.AddMarker(marker['frame']-int(target.GetStartFrame()),'Blue',marker['encoding_id'],
                                    f"商品{marker['product_index']}｜{marker['title']}",1,
                                    f"tb_cut_product_start:{marker['encoding_id']}"):
                raise SystemExit(f"商品{marker['product_index']}标记添加失败；禁止自动重试；备份：{backup}")
        if not manager.SaveProject():
            raise SystemExit(f'写入后保存失败；禁止自动重试；备份：{backup}')
        new_video = list(target.GetItemListInTrack('video',1) or [])[len(old_video):]
        new_audio = list(target.GetItemListInTrack('audio',1) or [])[len(old_audio):]
        errors = validate_written_rows(expected,[item_row(x) for x in new_video],[item_row(x) for x in new_audio])
        after_inventory = {stable_id(t): resolve_snapshot(t) for t in timelines}
        for timeline in timelines:
            uid=stable_id(timeline)
            before=preflight['protected'][uid]; after=after_inventory[uid]
            if uid==target_id:
                if not snapshot_preserved(before,after): errors.append('目标时间线原有内容或设置发生变化')
            elif before!=after:
                errors.append('非目标时间线发生变化')
        current_markers = target.GetMarkers() or {}
        for marker in markers:
            row = current_markers.get(int(marker['frame']-int(target.GetStartFrame())),{})
            if row.get('customData') != f"tb_cut_product_start:{marker['encoding_id']}":
                errors.append(f"商品{marker['product_index']}标记验收失败")
        report = {'schema_version':2,'pipeline_version':'2.1','passed':not errors,'errors':errors,
            'backup_project':str(backup),'project':project.GetName(),'project_id':stable_id(project),
            'source_timeline':source.GetName(),'target_timeline':target.GetName(),'target_timeline_id':target_id,
            'product_ids':wanted,'variant_count':sum(len(p['plans']) for p in products),
            'existing_video_count':len(old_video),'existing_audio_count':len(old_audio),
            'expected':expected,'actual':[item_row(x) for x in new_video],'markers':markers,
            'target_before':preflight['protected'][target_id],'target_after':after_inventory[target_id],
            'protected_before':preflight['protected'],'protected_after':after_inventory,
            'final_variant_metrics':[{'product_index':p['product_index'],'variant_id':v['variant_id'],
                'duration':v.get('duration'),'status':v.get('status')} for p in products for v in p['plans']]}
        for marker in report['markers']:
            marker['timeline_frame_offset'] = marker['frame'] - int(target.GetStartFrame())
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        report_digest = hashlib.sha256(json.dumps(report,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        record_written_checkpoints(paths, wanted, report, report_digest, target_id)
        atomic_json(transaction_path,{'status':'VERIFIED' if not errors else 'FAILED_VERIFICATION',
            'report':str(report_path),'report_sha256':report_digest,'backup':str(backup),'products':wanted})
        if errors:
            raise SystemExit(f'追加后复验失败；禁止重试；备份：{backup}')
        print(f'追加并复验通过：新增{len(new_video)}段，备份已保存。')
        return
    raise SystemExit('legacy_resolve_write_not_migrated: independent Codex quality evidence required')
    identity = expected_identity(paths)
    state = paths.load_state()
    failed = {int(value) for value in state.get("batch", {}).get("failed_product_ids", [])}
    if args.products:
        wanted = [int(value) for value in args.products.split(",") if value.strip()]
    else:
        wanted = [int(value) for value in state["batch"]["product_ids"] if int(value) not in failed]
    products = load_products(root, wanted, identity, paths.workspace_root)

    resolve = connect()
    if not resolve:
        raise SystemExit("未连接达芬奇")
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject() if manager else None
    config = state["resolve"]
    if not project or project.GetName() != config["project_name"]:
        raise SystemExit("当前达芬奇项目不匹配")
    project_fps = float(
        project.GetSetting("timelineFrameRate")
        or project.GetSetting("timelinePlaybackFrameRate")
        or 30
    )
    source = get_timeline(project, config["source_timeline"])
    target = get_timeline(project, config["target_timeline"])
    if source.GetName() == target.GetName():
        raise SystemExit("素材与成片时间线不能相同")
    media_items = {}
    media_fps = {}
    for item in source.GetItemListInTrack("video", 1) or []:
        media = item.GetMediaPoolItem()
        if media:
            media_name = media.GetName()
            if (media_name in media_items and
                    media_items[media_name].GetClipProperty('File Path') != media.GetClipProperty('File Path')):
                raise SystemExit(f"素材同名但来源不同，禁止写入：{media_name}")
            media_items[media_name] = media
            try:
                media_fps[media_name] = float((media.GetClipProperty() or {}).get("FPS") or 0)
            except (TypeError, ValueError):
                media_fps[media_name] = 0.0

    old_video = list(target.GetItemListInTrack("video", 1) or [])
    old_audio = list(target.GetItemListInTrack("audio", 1) or [])
    old_markers = target.GetMarkers() or {}
    old_custom = {row.get("customData", "") for row in old_markers.values() if isinstance(row, dict)}
    for product in products:
        if f"tb_cut_product_start:{product['encoding_id']}" in old_custom:
            raise SystemExit(f"商品{product['product_index']}已经写入，拒绝重复")

    gap = round(INTER_VARIANT_GAP_SECONDS * project_fps)
    occupied_ends = [int(item.GetEnd()) for item in old_video + old_audio]
    record = timeline_block_start(int(target.GetStartFrame()), occupied_ends, gap) - gap
    append_infos, expected, markers = [], [], []
    for product in products:
        product_start = None
        for variant_index, plan in enumerate(product["plans"], 1):
            record += gap
            if product_start is None:
                product_start = record
            for clip in plan["clips"]:
                media_name = clip["source_file"]
                if media_name not in media_items:
                    raise SystemExit(f"素材时间线找不到：{media_name}")
                plan_fps = float(plan.get("fps") or project_fps)
                source_fps = media_fps.get(media_name)
                if not source_fps or abs(source_fps - plan_fps) > 1e-6 or abs(plan_fps - project_fps) > 1e-6:
                    raise SystemExit(f"素材帧率与方案/时间线不一致，未经精确映射禁止写入：{media_name}")
                start, end = int(clip["source_start_frame"]), int(clip["source_end_frame"])
                timeline_duration = end - start
                if end <= start:
                    raise SystemExit(f"素材帧率换算后片段无效：{media_name} {start}-{end}")
                append_infos.append({"mediaPoolItem": media_items[media_name], "startFrame": start, "endFrame": end, "recordFrame": record, "trackIndex": 1})
                expected.append({
                    "product_index": product["product_index"], "variant_index": variant_index,
                    "media": media_name, "record_start": record, "record_end": record + timeline_duration,
                    "source_start": start, "source_end": end,
                })
                record += timeline_duration
        markers.append({"frame": product_start, "encoding_id": product["encoding_id"], "product_index": product["product_index"], "title": product["title"]})

    print(f"预检通过：商品{len(products)}款，版本{sum(len(p['plans']) for p in products)}条，素材段{len(expected)}个")
    if args.check_only:
        return

    backup_dir = paths.cache_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{project.GetName()}_before_v2_{datetime.now():%Y%m%d_%H%M%S}.drp"
    if not manager.SaveProject() or not manager.ExportProject(project.GetName(), str(backup), False):
        raise SystemExit("写入前保存或备份失败")
    project.SetCurrentTimeline(target)
    created = project.GetMediaPool().AppendToTimeline(append_infos)
    if not created or len(created) != len(append_infos):
        raise SystemExit(f"批量追加失败；可用备份恢复：{backup}")
    for marker in markers:
        if not target.AddMarker(marker["frame"], "Blue", marker["encoding_id"], f"商品{marker['product_index']}｜{marker['title']}", 1, f"tb_cut_product_start:{marker['encoding_id']}"):
            raise SystemExit(f"商品{marker['product_index']}标记添加失败；可用备份恢复：{backup}")
    if not manager.SaveProject():
        raise SystemExit(f"写入后保存失败；可用备份恢复：{backup}")

    new_video = list(target.GetItemListInTrack("video", 1) or [])[len(old_video):]
    new_audio = list(target.GetItemListInTrack("audio", 1) or [])[len(old_audio):]
    actual = [item_row(item) for item in new_video]
    actual_audio = [item_row(item) for item in new_audio]
    errors = validate_written_rows(expected, actual, actual_audio)
    current_markers = target.GetMarkers() or {}
    for marker in markers:
        row = current_markers.get(int(marker["frame"]), {})
        if row.get("customData") != f"tb_cut_product_start:{marker['encoding_id']}":
            errors.append(f"商品{marker['product_index']}标记验收失败")
    report = {
        "schema_version": 2, "pipeline_version": "2.1",
        "passed": not errors, "errors": errors, "backup_project": str(backup),
        "project": project.GetName(), "source_timeline": source.GetName(), "target_timeline": target.GetName(),
        "product_ids": wanted, "variant_count": sum(len(p["plans"]) for p in products),
        "existing_video_count": len(old_video), "existing_audio_count": len(old_audio),
        "expected": expected, "actual": actual, "markers": markers,
        "final_variant_metrics": [
            {
                "product_index": product["product_index"],
                "variant_id": plan["variant_id"],
                "duration": plan.get("duration"),
                "semantic_closure": plan.get("semantic_closure"),
                "physical_overlap": plan.get("physical_overlap", 0),
                "semantic_duplicate": plan.get("semantic_duplicate", False),
                "meaningless_cut_count": plan.get("meaningless_cut_count", 0),
                "seam_tail_risk_count": plan.get("seam_tail_risk_count", 0),
                "candidate_pool_duration": plan.get("candidate_pool_duration"),
                "candidate_pool_segment_count": plan.get("candidate_pool_segment_count"),
                "product_range_start": plan.get("product_range_start"),
                "product_range_end": plan.get("product_range_end"),
                "repair_attempts": plan.get("repair_attempts", 0),
                "status": plan.get("status"),
            }
            for product in products for plan in product["plans"]
        ],
    }
    report_path = paths.cache_dir / "resolve_write_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_metadata(paths, report_path, "resolve_write_v2.py", identity=identity)
    if errors:
        raise SystemExit(f"达芬奇写入后验收失败；可用备份恢复：{backup}")
    print(f"达芬奇写入并验收通过：新增{len(new_video)}段，备份={backup}")


if __name__ == "__main__":
    main()
