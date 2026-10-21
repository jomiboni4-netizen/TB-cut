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

from runtime_meta import expected_identity, load_checked_json, write_metadata


FORBIDDEN = re.compile(r"(优惠券|红包|抽奖|福袋|补贴|到手价|链接|上车|拍下|库存|倒计时|关注|点赞)")
CLEAN = re.compile(r"[\W_]+", re.UNICODE)


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


def registered_append_authorization(paths, state, target_id: str) -> dict:
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
            or authorization.get("product_ids") != state["batch"]["product_ids"]
            or authorization.get("clear_existing") is not False):
        raise SystemExit("existing_target_append_authorization_scope_mismatch")
    return authorization


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    parser.add_argument("--products")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument('--trial-write', action='store_true', help='Dedicated bounded trial only; formal timeline never writable')
    parser.add_argument('--append-registered-target', action='store_true',
                        help='Append only to the user-authorized, registered final timeline after all quality gates pass')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    if paths.registered_scope():
        from project_scheduler import project_audit
        result = project_audit(paths)
        if not result['final_review_complete']:
            raise SystemExit('project_quality_preflight_failed:' + ','.join(result['errors']))
        if args.check_only:
            if args.append_registered_target:
                state = read(paths.state_path)
                preflight_path = paths.workspace_root / 'RESOLVE_PREFLIGHT.json'
                if not preflight_path.is_file():
                    raise SystemExit('registered_resolve_preflight_snapshot_missing')
                preflight = read(preflight_path)
                target_ids = [key for key, value in preflight.get('protected', {}).items()
                              if value.get('name') == state['resolve']['target_timeline']]
                if (len(target_ids) != 1
                        or preflight.get('project_id') != state['project_identity']['resolve_project_id']):
                    raise SystemExit('registered_target_identity_missing_or_mismatched')
                target_id = target_ids[0]
                registered_append_authorization(paths,state,target_id)
                resolve = connect(status_path=paths.workspace_root/'resolve_connection_status.json')
                if not resolve: raise SystemExit('未连接达芬奇')
                project = resolve.GetProjectManager().GetCurrentProject()
                if (not project or project.GetName() != state['resolve']['project_name']
                        or stable_id(project) != state['project_identity']['resolve_project_id']):
                    raise SystemExit('当前达芬奇项目身份不匹配')
                from coldstart_resolve import snapshot as resolve_snapshot
                live = [project.GetTimelineByIndex(i) for i in range(1,project.GetTimelineCount()+1)]
                inventory = {stable_id(t):resolve_snapshot(t) for t in live}
                if inventory != preflight['protected']:
                    raise SystemExit('Resolve时间线与只读预检快照不一致')
                target = next((t for t in live if stable_id(t)==target_id),None)
                if not target or target.GetName()==state['resolve']['source_timeline']:
                    raise SystemExit('登记目标与源时间线冲突')
                if not target.GetTrackCount('video') or not target.GetTrackCount('audio'):
                    raise SystemExit('登记目标缺少音视频轨，拒绝自动改动时间线结构')
                failed = {int(value) for value in state.get('batch',{}).get('failed_product_ids',[])}
                wanted = ([int(v) for v in args.products.split(',') if v.strip()] if args.products
                          else [int(v) for v in state['batch']['product_ids'] if int(v) not in failed])
                if (not wanted or len(wanted)!=len(set(wanted))
                        or any(pid not in state['batch']['product_ids'] or pid in failed for pid in wanted)):
                    raise SystemExit('append_product_scope_invalid_or_failed')
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
        state = read(paths.state_path)
        failed = {int(value) for value in state.get('batch', {}).get('failed_product_ids', [])}
        if args.products:
            wanted = [int(value) for value in args.products.split(',') if value.strip()]
        else:
            wanted = [int(value) for value in state['batch']['product_ids'] if int(value) not in failed]
        if not wanted or len(wanted) != len(set(wanted)) or any(pid not in state['batch']['product_ids'] or pid in failed for pid in wanted):
            raise SystemExit('append_product_scope_invalid_or_failed')
        products = load_products(root, wanted, identity, paths.workspace_root)
        config = state['resolve']
        preflight_path = paths.workspace_root / 'RESOLVE_PREFLIGHT.json'
        if not preflight_path.is_file():
            raise SystemExit('registered_resolve_preflight_snapshot_missing')
        preflight = read(preflight_path)
        target_id = next((key for key, value in preflight.get('protected', {}).items()
                          if value.get('name') == config['target_timeline']), None)
        if not target_id or preflight.get('project_id') != state['project_identity']['resolve_project_id']:
            raise SystemExit('registered_target_identity_missing_or_mismatched')
        registered_append_authorization(paths, state, target_id)
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
        before_inventory = {stable_id(t): resolve_snapshot(t) for t in timelines}
        if before_inventory != preflight.get('protected'):
            raise SystemExit('Resolve时间线与只读预检快照不一致，拒绝追加')
        source = next((t for t in timelines if t.GetName() == config['source_timeline']), None)
        target = next((t for t in timelines if stable_id(t) == target_id), None)
        if not source or not target or source.GetName() == target.GetName():
            raise SystemExit('源或目标时间线身份不匹配')
        report_path = paths.cache_dir / 'resolve_write_report.json'
        transaction_path = paths.cache_dir / 'resolve_append_transaction.json'
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
        gap = round(5.0 * project_fps)
        end_positions = [int(item.GetEnd()) for kind in ('video','audio')
                         for track in range(1, int(target.GetTrackCount(kind))+1)
                         for item in (target.GetItemListInTrack(kind,track) or [])]
        record = max(end_positions, default=int(target.GetStartFrame())) + gap
        append_infos, expected, markers = [], [], []
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
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        write_metadata(paths,report_path,'resolve_write_v2.py',identity=identity)
        report_digest = hashlib.sha256(json.dumps(report,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        atomic_json(transaction_path,{'status':'VERIFIED' if not errors else 'FAILED_VERIFICATION',
            'report':str(report_path),'report_sha256':report_digest,'backup':str(backup),'products':wanted})
        if errors:
            raise SystemExit(f'追加后复验失败；禁止重试；备份：{backup}')
        print(f'追加并复验通过：新增{len(new_video)}段，备份已保存。')
        return
    raise SystemExit('legacy_resolve_write_not_migrated: independent Codex quality evidence required')
    identity = expected_identity(paths)
    state = read(paths.state_path)
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

    gap = round(5.0 * project_fps)
    record = max((int(item.GetEnd()) for item in old_video), default=-gap)
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
