#!/usr/bin/env python3
"""Build a wide candidate pool across each complete product range."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from runtime_paths import resolve_runtime_paths

import hashlib
import tempfile
from runtime_meta import (CacheIdentityError, expected_identity, load_checked_json,
                          read_metadata, write_metadata, validate_cache)
from runtime_publication import BuildSnapshot, generator_lock, publish_pair
from runtime_meta import metadata_path
from apply_range_overrides_v2 import signature, source_map, validate_global_ranges, validate_range
from publish_confirmed_ranges import validate_confirmed_document
from semantic_evidence import clean_cue


def build_wide_candidates(cues: list[dict], stem: str, title: str,
                          permitted_range: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Keep original subtitle wording for semantic review; annotate forbidden clauses."""
    if not cues:
        return [], []
    rows, annotations = [], []
    ordered = sorted(cues, key=lambda item: (float(item["start"]), float(item["end"])))
    lower = float(permitted_range["start"]) if permitted_range else min(float(c["start"]) for c in ordered)
    upper = float(permitted_range["end"]) if permitted_range else max(float(c["end"]) for c in ordered)
    for index, cue in enumerate(ordered, 1):
        cue_ref = f"{stem}:{index}"
        _cleaned, excluded = clean_cue(cue, cue_ref)
        annotations.extend({**row, "source_stem": stem} for row in excluded)
        start, end = float(cue["start"]), float(cue["end"])
        if end <= lower or start >= upper:
            continue
        rows.append({**cue, "cue_ref": cue_ref, "source_stem": stem,
                     "context_only": start < lower or end > upper})
    if not rows:
        return [], annotations
    text = "".join(str(row.get("text") or "") for row in rows)
    title_terms = [term for term in re.split(r"[/\s]+", title or "") if len(term) >= 2]
    start = max(lower, min(float(row["start"]) for row in rows))
    end = min(upper, max(float(row["end"]) for row in rows))
    candidate = {
        "candidate_id": f"s{stem}_wide_{round(start * 1000):010d}_{round(end * 1000):010d}",
        "source_file": f"{stem}.mp4",
        "time_range": {"start": round(start, 3), "end": round(end, 3)},
        "asr_text": text,
        "local_title_matches": [term for term in title_terms if term in text],
        "forbidden_annotations": annotations,
        "subtitle": [{"start": float(row["start"]), "end": float(row["end"]),
                      "text": str(row["text"]), "cue_ref": row.get("cue_ref"),
                      "needs_cutpoint_check": bool(row.get("needs_cutpoint_check")),
                      "source_cue_ref": row.get("source_cue_ref"),
                      "context_only": bool(row.get("context_only"))} for row in rows],
        "semantic_boundary": {"start_reason": "product_range_start", "end_reason": "product_range_end",
                              "duration_sec": round(end-start, 3), "standalone_candidate": False},
    }
    return [candidate], annotations


def validate_accepted_ranges(document, meta, titles, index, product_ids):
    producer=meta.get("producer")
    if producer=="publish_confirmed_ranges.py":
        validate_confirmed_document(document,titles,index,product_ids)
    elif producer=="apply_range_overrides_v2.py":
        rows=document.get("products")
        if not isinstance(rows,list) or {r.get("product_index") for r in rows}!=set(product_ids):
            raise CacheIdentityError("range_authority_unverified")
        sources=source_map(index)
        for row in rows:
            provenance=row.get("boundary_provenance") or {}
            if all(provenance.get(side)=={"kind":"explicit_override"} for side in ("start","end")):
                validate_range(row,sources)
                continue
            # Mixed provenance is not produced by the range writer and cannot
            # be inferred from its confidence label.
            if not all(provenance.get(side,{}).get("kind")=="human_confirmed" for side in ("start","end")):
                raise CacheIdentityError("range_authority_unverified")
            validate_confirmed_document({"products":[row]},[next(t for t in titles if t["product_index"]==row["product_index"])],index)
        validate_global_ranges(rows)
    else:
        raise CacheIdentityError("range_authority_unverified: publish confirmed ranges first")
    return document


def expanded_request(product, candidates, removed, range_digest, editing_mode, editing_policy):
    duration=round(float(product["global_end"])-float(product["global_start"]),3)
    inferred_search = product.get("boundary_mode") == "neighbor_inferred_search_envelope"
    rules={"product_title":product["title"],"product_index":product["product_index"],
           "encoding_id":product["encoding_id"],
           "phase":"相邻商品推断搜索窗口内的卖点语义选择" if inferred_search else "完整商品范围宽候选池语义选择",
           "requirements":[
             ("候选窗口是依据当前商品标题/卖点及前后商品边界推断的搜索包络，不是本商品整体精确起止；只选明确属于当前商品的完整卖点。"
              if inferred_search else "仅在已验证的当前商品区间内选择"),
             f"每条成片严格大于{editing_policy['duration_exclusive_seconds'][0]}秒且小于{editing_policy['duration_exclusive_seconds'][1]}秒",
             "候选不限长度，允许从长语义块内部选择完整句群，不可截断依赖","每条开头独立、结尾完整",
             f"目标时长约{editing_policy.get('target_duration_seconds', editing_policy['duration_exclusive_seconds'][1])}秒；完整自然语义优先于贴近目标时长",
             f"目标版本数为{editing_policy['target_variant_count']}；仅按项目模式允许时，且完整候选池耗尽后才可减少",
             "保留完整字幕语境并标注禁用话术；理解后避开禁用片段，不得把它剪入成片",
             "不同版本源时间零重叠且口播语义不重复",
             "输出卖点清单及其候选编号、逐字原文证据；不得修改商品范围、决定物理切点或控制 Resolve"]}
    if inferred_search:
        rules["requirements"] = [row for row in rules["requirements"]
                                 if "完整候选池耗尽后才可减少" not in row]
        rules["requirements"].append(
            "目标是三条不同卖点成片；不能因整体商品起点/终点不确定而阻断或减少数量，邻商品交界处归属不清的内容跳过并从其它候选窗口继续找。")
    pool_duration=round(sum(float(r["time_range"]["end"])-float(r["time_range"]["start"]) for r in candidates),3)
    rules["editing_mode"] = editing_mode
    rules["editing_policy"] = editing_policy
    pool_summary={"segment_count":len(candidates),"duration":pool_duration,
            "product_range_duration":duration,
            "coverage":None if inferred_search else round(pool_duration/duration,6) if duration else 0.0,
            "search_scope":"neighbor_inferred_search_windows" if inferred_search else "confirmed_product_range",
            "complete_product_range_scanned":not inferred_search}
    return {"schema_version":2,"pipeline_version":"2.2","product_index":product["product_index"],
            "rules":rules,"model":"codex","product_range":{"start":product["global_start"],
            "end":product["global_end"],"duration":duration,"basis":product.get("range_basis"),
            "boundary_mode":product.get("boundary_mode"),
            "boundary_notes":product.get("boundary_notes",[])},
            "product_range_sha256":range_digest,"candidates":candidates,"local_forbidden_cues":removed,
            "candidate_pool_summary":pool_summary}


def build_requests(paths, product_ids):
    requested={int(value) for value in product_ids}
    if not requested or any(pid<1 for pid in requested): raise ValueError("products_must_be_nonempty_positive_ids")
    outputs=[]
    with generator_lock(paths):
        snapshot=BuildSnapshot(paths);identity=expected_identity(paths)
        range_path=paths.cache_dir/"product_ranges.json"
        index_path=paths.cache_dir/"subtitle_index.json";titles_path=paths.cache_dir/"titles.json"
        ranges_doc=load_checked_json(paths,range_path,expected=identity,artifact_schema_version=1)
        range_meta=read_metadata(range_path)
        titles_doc=load_checked_json(paths,titles_path,expected=identity)
        index=load_checked_json(paths,index_path,expected=identity,artifact_schema_version=1)
        batch_ids=snapshot.state.get("batch",{}).get("product_ids") or []
        validate_accepted_ranges(ranges_doc,range_meta,titles_doc["products"],index,batch_ids)
        ranges={int(r["product_index"]):r for r in ranges_doc["products"]}
        title_by_id={int(r["product_index"]):r for r in titles_doc["products"]}
        if set(ranges)!=set(batch_ids):raise CacheIdentityError("accepted_range_product_set_incomplete")
        if not requested.issubset(ranges):raise ValueError("unknown_or_missing_product_range")
        source_rows={r["source_stem"]:r for r in index["sources"]}
        from project_editing_modes import resolve_editing_mode
        editing_mode, editing_policy = resolve_editing_mode(
            (snapshot.state.get("resolve") or {}).get("project_name"))

        cues=index["cues"];by_source={}
        for cue in cues:by_source.setdefault(str(cue["source_stem"]),[]).append(cue)
        range_digest=hashlib.sha256(range_path.read_bytes()).hexdigest()
        source_stamp=signature(range_path);index_stamp=signature(index_path);titles_stamp=signature(titles_path)
        staged=[]
        with tempfile.TemporaryDirectory(prefix=".expanded-requests-",dir=paths.cache_dir) as temporary:
            temp=Path(temporary)
            for pid in sorted(requested):
                product=ranges[pid];title=title_by_id[pid]
                candidates=[];removed=[]
                for source_range in product["ranges"]:
                    stem=str(source_range["media_stem"])
                    if stem not in source_rows:raise CacheIdentityError("accepted_range_source_unknown")
                    approximate = editing_mode == "dudu_marked_range"
                    source_cues=[cue for cue in by_source.get(stem,[])
                        if float(cue["end"])>float(source_range["start"])
                        and float(cue["start"])<float(source_range["end"])
                        and (approximate or (float(cue["start"])>=float(source_range["start"])
                        and float(cue["end"])<=float(source_range["end"])))]
                    kept,rejected=build_wide_candidates(source_cues,stem,title["title"],
                        permitted_range=source_range if approximate else None)
                    candidates.extend(kept);removed.extend(rejected)
                request=expanded_request(product,candidates,removed,range_digest,editing_mode,editing_policy)
                output=paths.cache_dir/f"product_{pid:02d}_rough"/"codex_expanded_request.json"
                output.parent.mkdir(parents=True,exist_ok=True)
                target=temp/f"product_{pid:02d}_codex_expanded_request.json"
                target.write_text(json.dumps(request,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
                meta=write_metadata(paths,target,"build_expanded_codex_request.py",product_id=pid,identity=identity)
                validate_cache(paths,target,product_id=pid,expected=identity)
                staged.append((target,metadata_path(target),output))
            def check_inputs():
                snapshot.check()
                if signature(range_path)!=source_stamp or signature(index_path)!=index_stamp or signature(titles_path)!=titles_stamp:
                    raise CacheIdentityError("artifact_changed_during_generation")
                if hashlib.sha256(range_path.read_bytes()).hexdigest()!=range_digest:
                    raise CacheIdentityError("range_dependency_mismatch")
            for data,meta,output in staged:
                publish_pair(data,meta,output,before_commit=check_inputs)
                outputs.append(output)
    return outputs


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",default=".");parser.add_argument("--workspace");parser.add_argument("--products",required=True)
    parser.add_argument("--max-block",type=float,help="已弃用；V2.1 candidate pool 不限长度")
    args=parser.parse_args();paths=resolve_runtime_paths(Path(args.root).resolve(),args.workspace)
    outputs=build_requests(paths,[int(value) for value in args.products.split(",") if value.strip()])
    for path in outputs:print(f"生成范围绑定候选请求：{path.parent.name}")


if __name__ == "__main__":
    main()
