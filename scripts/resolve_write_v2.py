#!/usr/bin/env python3
"""Validate approved V2 plans, back up Resolve, append, and verify."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
from runtime_paths import resolve_runtime_paths

from runtime_meta import expected_identity, load_checked_json, write_metadata


API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
MOD = API + "/Modules"
LIB = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
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


def connect():
    os.environ.setdefault("RESOLVE_SCRIPT_API", API)
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", LIB)
    sys.path.insert(0, MOD)
    import DaVinciResolveScript as dvr
    resolve = dvr.scriptapp("Resolve")
    if resolve is not None:
        return resolve
    address = os.environ.get("RESOLVE_HOST")
    if not address:
        for interface in ("en0", "en1"):
            result = subprocess.run(["ipconfig", "getifaddr", interface], capture_output=True, text=True, check=False)
            address = result.stdout.strip()
            if address:
                break
    return dvr.scriptapp("Resolve", address) if address else None


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
    identity = identity or expected_identity(paths)
    titles = {int(row["product_index"]): row for row in load_checked_json(paths, paths.cache_dir / "titles.json", expected=identity)["products"]}
    visual = load_checked_json(paths, paths.cache_dir / "person_coverage_audit.json", expected=identity, artifact_schema_version=2)
    if visual.get("passed") is not True:
        raise SystemExit("全量人物可见比例审计未通过")
    visual_rows = {(int(row["product_index"]), str(row["plan_digest"])): row for row in visual.get("plans", [])}
    products = []
    for product_index in wanted:
        directory = paths.cache_dir / f"product_{product_index:02d}_windowed"
        manifest = load_checked_json(paths, directory / "approved_plans.json", product_id=product_index, expected=identity, artifact_schema_version=2)
        rough = load_checked_json(paths, paths.cache_dir / f"product_{product_index:02d}_rough" / "rough_audit.approved.json", product_id=product_index, expected=identity, artifact_schema_version=2)
        final = load_checked_json(paths, directory / "final_plan_audit.json", product_id=product_index, expected=identity, artifact_schema_version=2)
        if rough.get("pipeline_version") != "2.1" or final.get("pipeline_version") != "2.1" or manifest.get("pipeline_version") != "2.1":
            raise SystemExit(f"商品{product_index}仍是旧版派生缓存，必须按V2.1重新生成")
        if rough.get("passed") is not True:
            raise SystemExit(f"商品{product_index}语义审计状态不通过")
        failed_variants = {str(row.get("variant_id")) for row in final.get("errors", []) if row.get("variant_id")}
        rows = manifest.get("plans", [])
        if not 1 <= len(rows) <= 3:
            raise SystemExit(f"商品{product_index}合格版本数无效")
        plans = []
        for row in rows:
            resolved = paths.resolve_reference(row["path"])
            if resolved.legacy_absolute:
                print(f"WARN legacy_absolute_plan_path: {row['path']}")
            path = resolved.path
            plan = load_checked_json(paths, path, product_id=product_index, expected=identity, artifact_schema_version=2)
            if plan.get("pipeline_version") != "2.1" or plan.get("status") != "PASS":
                raise SystemExit(f"商品{product_index}方案不是V2.1 PASS状态")
            if str(plan.get("variant_id")) in failed_variants:
                raise SystemExit(f"商品{product_index}批准清单包含终审失败版本")
            duration = sum((int(c["source_end_frame"]) - int(c["source_start_frame"])) / float(plan.get("fps") or 30) for c in plan.get("clips", []))
            if not 45.0 < duration < 60.0:
                raise SystemExit(f"商品{product_index}存在时长不合格版本")
            check = visual_rows.get((product_index, digest(path)))
            if not check or check.get("passed") is not True or not float(check.get("host_visible_ratio", 0)) > 0.70:
                raise SystemExit(f"商品{product_index}人物可见比例审计缺失或不通过")
            for clip in plan.get("clips", []):
                if int(clip["source_end_frame"]) <= int(clip["source_start_frame"]):
                    raise SystemExit(f"商品{product_index}存在无效素材范围")
                boundary = (clip.get("audit") or {}).get("boundary") or {}
                if boundary.get("clean_start") is not True or boundary.get("clean_end") is not True:
                    raise SystemExit(f"商品{product_index}存在未通过的切点")
                if FORBIDDEN.search(str((clip.get("audit") or {}).get("transcript") or "")):
                    raise SystemExit(f"商品{product_index}成片仍含禁用内容")
            for left, right in zip(plan.get("clips", []), plan.get("clips", [])[1:]):
                if left["source_file"] == right["source_file"] and abs(int(right["source_start_frame"]) - int(left["source_end_frame"])) <= 2:
                    raise SystemExit(f"商品{product_index}仍有无意义物理剪辑点")
            if int(plan.get("meaningless_cut_count", -1)) != 0 or int(plan.get("seam_tail_risk_count", 0)) != 0:
                raise SystemExit(f"商品{product_index}物理剪辑或句尾风险未清零")
            plans.append(plan)
        validate_uniqueness(product_index, plans)
        title = titles[product_index]
        products.append({
            "product_index": product_index,
            "encoding_id": str(title["encoding_id"]),
            "title": title["title"],
            "plans": plans,
        })
    return products


def item_row(item: object) -> dict:
    media = item.GetMediaPoolItem()
    return {
        "media": media.GetName() if media else None,
        "record_start": int(item.GetStart()),
        "record_end": int(item.GetEnd()),
        "source_start": int(item.GetSourceStartFrame()),
        "source_end": int(item.GetSourceEndFrame()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    parser.add_argument("--products")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
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
    source = get_timeline(project, config["source_timeline"])
    target = get_timeline(project, config["target_timeline"])
    if source.GetName() == target.GetName():
        raise SystemExit("素材与成片时间线不能相同")
    media_items = {}
    for item in source.GetItemListInTrack("video", 1) or []:
        media = item.GetMediaPoolItem()
        if media:
            media_items[media.GetName()] = media

    old_video = list(target.GetItemListInTrack("video", 1) or [])
    old_audio = list(target.GetItemListInTrack("audio", 1) or [])
    old_markers = target.GetMarkers() or {}
    old_custom = {row.get("customData", "") for row in old_markers.values() if isinstance(row, dict)}
    for product in products:
        if f"tb_cut_product_start:{product['encoding_id']}" in old_custom:
            raise SystemExit(f"商品{product['product_index']}已经写入，拒绝重复")

    gap = 150
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
                start, end = int(clip["source_start_frame"]), int(clip["source_end_frame"])
                append_infos.append({"mediaPoolItem": media_items[media_name], "startFrame": start, "endFrame": end, "recordFrame": record, "trackIndex": 1})
                expected.append({
                    "product_index": product["product_index"], "variant_index": variant_index,
                    "media": media_name, "record_start": record, "record_end": record + end - start,
                    "source_start": start, "source_end": end,
                })
                record += end - start
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
    errors = []
    if len(new_video) != len(expected) or len(new_audio) != len(expected):
        errors.append(f"新增音视频数量不符：video={len(new_video)} audio={len(new_audio)} expected={len(expected)}")
    actual = [item_row(item) for item in new_video]
    for index, row in enumerate(expected):
        if index >= len(actual):
            break
        exact_mismatch = any(actual[index].get(key) != row.get(key) for key in ("media", "record_start", "record_end"))
        source_mismatch = any(abs(int(actual[index][key]) - int(row[key])) > 1 for key in ("source_start", "source_end"))
        if exact_mismatch or source_mismatch:
            errors.append(f"新增素材段{index + 1}不匹配")
        if index >= len(new_audio) or int(new_audio[index].GetStart()) != row["record_start"] or int(new_audio[index].GetEnd()) != row["record_end"]:
            errors.append(f"新增音频段{index + 1}不同步")
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
