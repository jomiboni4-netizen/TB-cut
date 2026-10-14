#!/usr/bin/env python3
"""Preflight or explicitly publish human-confirmed ranges for the active batch."""
from __future__ import annotations
import argparse
import hashlib
import json
import tempfile
from datetime import datetime
from pathlib import Path
from runtime_paths import resolve_runtime_paths
from runtime_meta import (CacheIdentityError, expected_identity, load_checked_json, invalidation_path,
                          metadata_path, read_metadata, validate_cache, validate_metadata, write_metadata)
from runtime_publication import BuildSnapshot, generator_lock, publish_pair
from apply_range_overrides_v2 import (RangeOverrideError, global_time, source_map,
                                      split_range, validate_global_ranges, validate_range)


def canonical_hash(value: dict) -> str:
    body=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",", ":"),allow_nan=False)
    return hashlib.sha256(body.encode()).hexdigest()


def verify_confirmation(row: dict) -> bool:
    proof=row.get("human_confirmation")
    if not isinstance(proof,dict) or proof.get("schema_version")!=1 or proof.get("source")!="explicit_human_manifest":
        return False
    payload=proof.get("decision")
    if not isinstance(payload,dict) or canonical_hash(payload)!=proof.get("decision_sha256"):
        return False
    if not all(isinstance(payload.get(k),str) and payload[k].strip() for k in ("decision_id","confirmed_by","confirmed_at")):
        return False
    try:
        confirmed_time=datetime.fromisoformat(payload["confirmed_at"].replace("Z","+00:00"))
        if confirmed_time.tzinfo is None or confirmed_time.utcoffset() is None:return False
    except ValueError:
        return False
    for side in ("start","end"):
        point=payload.get(side)
        if not isinstance(point,dict) or set(point)!={"source_stem","time"}:
            return False
        if point.get("source_stem")!=row.get("boundary_provenance",{}).get(side,{}).get("source_stem"):
            return False
        try:
            if type(point["time"]) not in (int,float) or not __import__("math").isfinite(point["time"]):return False
            if round(float(point["time"]),3)!=point["time"] or round(float(point["time"]),3)!=row[f"{side}_source_local"]:
                return False
        except (TypeError,ValueError,KeyError):
            return False
    return row.get("boundary_provenance",{}).get("start",{}).get("kind")=="human_confirmed" and row.get("boundary_provenance",{}).get("end",{}).get("kind")=="human_confirmed"


def validate_confirmed_document(document: dict, titles: list[dict], index: dict, product_ids=None) -> None:
    rows=document.get("products")
    title_by_id={r["product_index"]:r for r in titles}
    expected_ids=set(title_by_id) if product_ids is None else set(product_ids)
    if not expected_ids or not expected_ids.issubset(title_by_id):
        raise RangeOverrideError("human_range_product_ids_invalid")
    if not isinstance(rows,list) or len(rows)!=len(expected_ids):
        raise RangeOverrideError("human_ranges_incomplete_product_set")
    indexes=[r.get("product_index") for r in rows]
    if len(set(indexes))!=len(indexes) or set(indexes)!=expected_ids:
        raise RangeOverrideError("human_ranges_product_set_mismatch")
    if any(not verify_confirmation(row) for row in rows):
        raise RangeOverrideError("human_confirmation_proof_missing_or_invalid")
    sources=source_map(index)
    for row in rows:
        if any(row.get(k)!=title_by_id[row["product_index"]].get(k) for k in ("encoding_id","title")):
            raise RangeOverrideError("human_range_title_identity_mismatch")
        payload=row["human_confirmation"]["decision"]
        for side in ("start","end"):
            if row["boundary_provenance"][side]!={"kind":"human_confirmed","source_stem":payload[side]["source_stem"]}:
                raise RangeOverrideError("human_confirmation_endpoint_provenance_mismatch")
            if row[f"global_{side}"]!=global_time(payload[side],sources):
                raise RangeOverrideError("human_confirmation_global_time_mismatch")
        validate_range(row,sources)
    validate_global_ranges(rows)


def build_confirmed_ranges(manifest: dict,index: dict,titles_doc: dict,product_ids=None) -> dict:
    titles=titles_doc.get("products")
    if not isinstance(titles,list) or not titles:
        raise RangeOverrideError("authenticated_titles_missing")
    sources=source_map(index);title_by_id={r["product_index"]:r for r in titles}
    expected_ids=set(title_by_id) if product_ids is None else set(product_ids)
    if not expected_ids or not expected_ids.issubset(title_by_id):
        raise RangeOverrideError("human_range_product_ids_invalid")
    decisions=manifest.get("products")
    if not isinstance(decisions,list):
        raise RangeOverrideError("human_manifest_products_must_be_list")
    if len(decisions)!=len(expected_ids) or {d.get("product_index") for d in decisions}!=expected_ids:
        raise RangeOverrideError("human_ranges_incomplete_product_set")
    rows=[];seen_decisions=set()
    for decision in decisions:
        pid=decision.get("product_index")
        if type(pid) is not int or pid not in title_by_id or pid in {r["product_index"] for r in rows}:
            raise RangeOverrideError("human_range_product_index_invalid_or_duplicate")
        for side in ("start","end"):
            item=decision.get(side)
            if not isinstance(item,dict) or set(item)!={"source_stem","time"}:
                raise RangeOverrideError("human_endpoint_must_be_source_local")
            if not isinstance(item["source_stem"],str) or item["source_stem"] not in sources:
                raise RangeOverrideError("override_source_unknown")
        decision_id=decision.get("decision_id");confirmed_by=decision.get("confirmed_by");confirmed_at=decision.get("confirmed_at")
        if not all(isinstance(x,str) and x.strip() for x in (decision_id,confirmed_by,confirmed_at)) or decision_id in seen_decisions:
            raise RangeOverrideError("human_decision_identity_missing_or_duplicate")
        seen_decisions.add(decision_id)
        payload={"decision_id":decision_id,"confirmed_by":confirmed_by,"confirmed_at":confirmed_at,
                 "start":decision["start"],"end":decision["end"]}
        proof={"schema_version":1,"source":"explicit_human_manifest","decision":payload,"decision_sha256":canonical_hash(payload)}
        start=global_time(decision["start"],sources);end=global_time(decision["end"],sources)
        row={**title_by_id[pid],"global_start":start,"global_end":end,
             "product_range_duration":round(end-start,3),"range_basis":"explicit_human_confirmation",
             "product_start_anchor":start,"ranges":split_range(start,end,list(sources.values())),
             "start_source_local":round(float(decision["start"]["time"]),3),
             "end_source_local":round(float(decision["end"]["time"]),3),
             "boundary_provenance":{"start":{"kind":"human_confirmed","source_stem":decision["start"]["source_stem"]},
                                    "end":{"kind":"human_confirmed","source_stem":decision["end"]["source_stem"]}},
             "human_confirmation":proof}
        validate_range(row,sources);rows.append(row)
    validate_global_ranges(rows)
    result={"schema_version":1,"products":sorted(rows,key=lambda r:r["product_index"]),
            "human_confirmation_manifest_sha256":canonical_hash(manifest)}
    validate_confirmed_document(result,titles,index,expected_ids)
    return result


def publish_or_preflight(paths,manifest_path:Path,*,publish=False,replace_confirmed=False):
    with generator_lock(paths):
        snapshot=BuildSnapshot(paths);identity=expected_identity(paths)
        output=paths.cache_dir/"product_ranges.json"
        if invalidation_path(output).exists():
            raise CacheIdentityError("publication_incomplete: existing range output is blocked")
        manifest_path=Path(manifest_path).resolve()
        if manifest_path in {output.resolve(),(paths.cache_dir/"subtitle_index.json").resolve(),(paths.cache_dir/"titles.json").resolve()}:
            raise RangeOverrideError("manifest_aliases_pipeline_artifact")
        input_paths=[manifest_path,paths.cache_dir/"subtitle_index.json",paths.cache_dir/"titles.json"]
        def file_signature(p):return (hashlib.sha256(p.read_bytes()).digest(),metadata_path(p).exists(),metadata_path(p).read_bytes() if metadata_path(p).is_file() else None)
        stamps={p:file_signature(p) for p in input_paths}
        manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
        index=load_checked_json(paths,input_paths[1],expected=identity,artifact_schema_version=1)
        titles_doc=load_checked_json(paths,input_paths[2],expected=identity)
        selected_ids=snapshot.state.get("batch",{}).get("product_ids") or []
        model=build_confirmed_ranges(manifest,index,titles_doc,selected_ids)
        existing=None
        replace_stale=False
        if output.exists() or metadata_path(output).exists():
            if not output.is_file() or not metadata_path(output).is_file():
                raise CacheIdentityError("existing_product_ranges_pair_incomplete")
            old_meta=read_metadata(output)
            if old_meta.get("producer")!="publish_confirmed_ranges.py":
                raise RangeOverrideError("existing_ranges_provenance_unverified")
            old_bytes=output.read_bytes()
            if old_meta.get("artifact_sha256")!=hashlib.sha256(old_bytes).hexdigest():
                raise CacheIdentityError("existing_ranges_sha256_mismatch")
            try:
                existing=json.loads(old_bytes)
            except json.JSONDecodeError as exc:
                raise CacheIdentityError("existing_ranges_json_invalid") from exc
            if not isinstance(existing,dict) or existing.get("schema_version")!=1:
                raise CacheIdentityError("existing_ranges_schema_invalid")
            try:
                validate_metadata(old_meta,identity)
            except CacheIdentityError:
                # A verified previous-batch publication is replaceable only with
                # the explicit --replace-confirmed authorization below.
                replace_stale=True
            if not replace_stale:
                validate_confirmed_document(existing,titles_doc["products"],index,selected_ids)
            if not replace_confirmed:
                raise RangeOverrideError("existing_human_ranges_require_explicit_replacement")
        if not publish:
            return {"status":"PREFLIGHT_PASS","product_count":len(model["products"]),
                    "will_create":existing is None,"would_replace":existing is not None,
                    "replacing_stale_batch":replace_stale}
        def check_inputs():
            snapshot.check()
            if not invalidation_path(output).exists() and expected_identity(paths).get("range_snapshot")!=identity.get("range_snapshot"):
                raise CacheIdentityError("ranges_changed_during_generation")
            if any(file_signature(p)!=stamp for p,stamp in stamps.items()):
                raise CacheIdentityError("artifact_changed_during_generation")
        with tempfile.TemporaryDirectory(prefix=".confirmed-ranges-",dir=paths.cache_dir) as tmp:
            staged=Path(tmp)/output.name
            staged.write_text(json.dumps(model,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
            meta=write_metadata(paths,staged,"publish_confirmed_ranges.py",identity=identity)
            validate_cache(paths,staged,expected=identity)
            publish_pair(staged,metadata_path(staged),output,before_commit=check_inputs)
        return {"status":"PUBLISHED","product_count":len(model["products"]),"created":existing is None}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--root",default=".");parser.add_argument("--workspace")
    parser.add_argument("--manifest",required=True);parser.add_argument("--publish",action="store_true");parser.add_argument("--replace-confirmed",action="store_true")
    args=parser.parse_args();paths=resolve_runtime_paths(Path(args.root).resolve(),args.workspace)
    result=publish_or_preflight(paths,paths.runtime_argument(args.manifest),publish=args.publish,replace_confirmed=args.replace_confirmed)
    print(json.dumps(result,ensure_ascii=False))

if __name__=="__main__":main()
