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


FORBIDDEN = re.compile(
    r"价格|价位|售价|到手价|成交价|优惠券|优惠|补贴|满减|立减|券后|平台券|红包|抽奖|"
    r"福袋|倒计时|库存|关注|点赞|\d+\s*(?:元|块钱|号链接)|几号链接|上车|拍下|"
    r"欢迎.{0,12}(?:来到|进入|宝宝|姐妹)|主播.{0,12}(?:后台|操作)|后台|流程|对焦|镜头|摄像头"
)


def build_wide_candidates(cues: list[dict], stem: str, title: str) -> tuple[list[dict], list[dict]]:
    """Keep every useful continuous run; impose no duration bound."""
    kept_runs: list[list[dict]] = []
    removed: list[dict] = []
    current: list[dict] = []
    for cue in cues:
        if FORBIDDEN.search(str(cue.get("text") or "")):
            if current:
                kept_runs.append(current)
                current = []
            removed.append({"start": cue["start"], "end": cue["end"], "text": cue["text"], "reason": "forbidden_content"})
            continue
        current.append(cue)
    if current:
        kept_runs.append(current)

    candidates: list[dict] = []
    title_terms = [term for term in re.split(r"[/\s]+", title or "") if len(term) >= 2]
    for index, run in enumerate(kept_runs, 1):
        start, end = float(run[0]["start"]), float(run[-1]["end"])
        text = "".join(str(row.get("text") or "") for row in run)
        candidates.append({
            "candidate_id": f"s{stem}_wide_{index:03d}",
            "source_file": f"{stem}.mp4",
            "time_range": {"start": round(start, 3), "end": round(end, 3)},
            "asr_text": text,
            "local_title_matches": [term for term in title_terms if term in text],
            "subtitle": [
                {"start": float(row["start"]), "end": float(row["end"]), "text": str(row["text"])}
                for row in run
            ],
            "semantic_boundary": {
                "start_reason": "product_range_start" if index == 1 else "after_forbidden_content",
                "end_reason": "product_range_end" if index == len(kept_runs) else "forbidden_content",
                "duration_sec": round(end - start, 3),
                "standalone_candidate": False,
            },
        })
    return candidates, removed


def validate_accepted_ranges(document, meta, titles, index):
    producer=meta.get("producer")
    if producer=="publish_confirmed_ranges.py":
        validate_confirmed_document(document,titles,index)
    elif producer=="apply_range_overrides_v2.py":
        rows=document.get("products")
        if not isinstance(rows,list) or {r.get("product_index") for r in rows}!={r["product_index"] for r in titles}:
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


def expanded_request(product, candidates, removed, range_digest):
    duration=round(float(product["global_end"])-float(product["global_start"]),3)
    rules={"product_title":product["title"],"product_index":product["product_index"],
           "encoding_id":product["encoding_id"],"phase":"完整商品范围宽候选池语义选择",
           "requirements":[
             "仅在已验证的当前商品区间内选择","每条成片严格大于45秒且小于60秒",
             "候选不限长度，允许从长语义块内部选择完整句群，不可截断依赖","每条开头独立、结尾完整",
             "禁止价格、优惠、抽奖、链接号和直播流程话术","不同版本源时间零重叠且口播语义不重复",
             "仅 MiMo 负责语义选择；不得修改商品范围、决定物理切点或控制 Resolve"]}
    pool_duration=round(sum(float(r["time_range"]["end"])-float(r["time_range"]["start"]) for r in candidates),3)
    return {"schema_version":1,"pipeline_version":"2.1","product_index":product["product_index"],
            "rules":rules,"model":"mimo-v2.5-pro","product_range":{"start":product["global_start"],
            "end":product["global_end"],"duration":duration,"basis":product.get("range_basis")},
            "product_range_sha256":range_digest,"candidates":candidates,"local_forbidden_cues":removed,
            "candidate_pool_summary":{"segment_count":len(candidates),"duration":pool_duration,
            "product_range_duration":duration,"coverage":round(pool_duration/duration,6) if duration else 0.0}}


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
        validate_accepted_ranges(ranges_doc,range_meta,titles_doc["products"],index)
        ranges={int(r["product_index"]):r for r in ranges_doc["products"]}
        title_by_id={int(r["product_index"]):r for r in titles_doc["products"]}
        if set(ranges)!=set(title_by_id):raise CacheIdentityError("accepted_range_product_set_incomplete")
        if not requested.issubset(ranges):raise ValueError("unknown_or_missing_product_range")
        if len(ranges)!=len(title_by_id):raise CacheIdentityError("accepted_range_product_set_incomplete")
        source_rows={r["source_stem"]:r for r in index["sources"]}

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
                    source_cues=[cue for cue in by_source.get(stem,[])
                        if float(cue["start"])>=float(source_range["start"])
                        and float(cue["end"])<=float(source_range["end"])]
                    kept,rejected=build_wide_candidates(source_cues,stem,title["title"])
                    candidates.extend(kept);removed.extend(rejected)
                request=expanded_request(product,candidates,removed,range_digest)
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
