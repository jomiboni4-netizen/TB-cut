#!/usr/bin/env python3
"""Create local-only, two-stage semantic previews; never publish edit ranges."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from audit_mimo_v2 import dependency_risks, semantic_duplicate
from index_subtitles import expand
from index_titles import build_model, parse_workbook, workbook_guards
from mimo_review_v2 import prompt as mimo_prompt
from mimo_responses import MODEL as RESPONSES_MODEL, ResponseAPIError, create_response
from semantic_evidence import clean_cue, semantic_windows
from workbook_runtime import preferred_node


MODEL = RESPONSES_MODEL
API_TIMEOUT = 240
STAGE_A_MAX_OUTPUT_TOKENS = 12000
LINK_MENTION = re.compile(r"(?:\d+|[零一二三四五六七八九十两]+)\s*号\s*(?:商品)?\s*(?:链|连)接")


def current_inputs(root: Path, workspace: Path, product_ids: list[int]):
    state = json.loads((workspace / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    title_path = Path(state["title_path"]).expanduser().resolve()
    parsed = parse_workbook(title_path, root, preferred_node())
    overrides = state.get("batch_overrides") or {}
    title_model = build_model(title_path, parsed, workbook_guards(title_path, parsed["sheet"]),
                              skip_blank_titles=bool(overrides.get("skip_blank_titles")),
                              preserve_blank_row_indices=bool(overrides.get("preserve_blank_row_indices")))
    titles = [row for row in title_model["products"] if row["product_index"] in product_ids]
    if {row["product_index"] for row in titles} != set(product_ids):
        raise ValueError("selected_product_ids_not_in_current_workbook")

    subtitle_files = expand(state.get("subtitle_paths") or [], ".srt")
    digest = hashlib.sha256()
    for path in subtitle_files:
        digest.update(str(path).encode())
        digest.update(path.read_bytes())
    current_fingerprint = digest.hexdigest()
    index_path = workspace / ".cache" / "subtitle_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    expected = state.get("runtime", {}).get("subtitle_index_fingerprint")
    if not expected or current_fingerprint != expected or index.get("fingerprint") != expected:
        raise ValueError("subtitle_index_fingerprint_mismatch")
    return state, titles, index, current_fingerprint


def build_windows(index: dict, products: list[dict], hints: dict):
    source_rows = {str(row["source_stem"]): row for row in index.get("sources", [])}
    rough = semantic_windows(index, products)
    by_id = {int(row["product_index"]): row for row in rough["products"]}
    for product in products:
        row = by_id[int(product["product_index"])]
        windows = []
        ranked = []
        product_hints = hints.get(int(product["product_index"]), [])
        for candidate in row["windows"]:
            overlap = max((max(0.0, min(candidate["global_end"] - source_rows[str(candidate["source_stem"])]["global_start"], h["local_end"])
                                  - max(candidate["global_start"] - source_rows[str(candidate["source_stem"])]["global_start"], h["local_start"]))
                            for h in product_hints if h["source_stem"] == str(candidate["source_stem"])), default=0.0)
            distinctive = len(candidate.get("distinctive_terms", []))
            # Prior human-reviewed time windows are matched by exact workbook
            # encoding ID and used only to prioritize semantic evidence. They
            # are never copied into the output as accepted product ranges.
            ranked.append((overlap, distinctive, int(candidate.get("matched_cue_count", 0)), candidate))
        ranked.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]["source_stem"], item[3]["global_start"]))
        chosen = [item[3] for item in ranked if item[0] > 0][:3]
        if not chosen:
            chosen = [item[3] for item in ranked if item[1] >= 2][:1]
        for candidate in sorted(chosen, key=lambda c: (c["source_stem"], c["global_start"])):
            windows.append({"source_stem": str(candidate["source_stem"]),
                            "local_start": max(0.0, candidate["global_start"] - source_rows[str(candidate["source_stem"])]["global_start"]),
                            "local_end": min(float(source_rows[str(candidate["source_stem"])]["duration"]),
                                             candidate["global_end"] - source_rows[str(candidate["source_stem"])]["global_start"]),
                            "evidence": candidate})
        windows.sort(key=lambda w: (w["source_stem"], w["local_start"]))
        merged = []
        for window in windows:
            if (merged and merged[-1]["source_stem"] == window["source_stem"]
                    and window["local_start"] <= merged[-1]["local_end"] + 20):
                merged[-1]["local_end"] = max(merged[-1]["local_end"], window["local_end"])
                merged[-1]["evidence_rows"].append(window["evidence"])
            else:
                merged.append({"source_stem": window["source_stem"],
                               "local_start": window["local_start"], "local_end": window["local_end"],
                               "evidence_rows": [window["evidence"]]})
        # Time-bounded chunks preserve discourse and add overlap. This is not a
        # character-count truncation and every reference remains source-timed.
        chunks = []
        for window in merged:
            cursor = window["local_start"]
            global_offset = float(source_rows[window["source_stem"]]["global_start"])
            while cursor < window["local_end"]:
                end = min(window["local_end"], cursor + 240.0)
                chunks.append({"source_stem": window["source_stem"], "local_start": cursor,
                               "local_end": end, "global_start": global_offset + cursor,
                               "global_end": global_offset + end,
                               "evidence_rows": window["evidence_rows"]})
                if end >= window["local_end"]:
                    break
                cursor = end - 12.0
        row["windows"] = chunks
    return rough


def make_phase_a(product: dict, windows: list[dict], index: dict):
    sources = {str(row["source_stem"]): row for row in index["sources"]}
    cues = index["cues"]
    aliases = {stem: f"src{n:02d}" for n, stem in enumerate(sorted({w["source_stem"] for w in windows}), 1)}
    sent, local_refs = [], {}
    counter = 0
    for number, window in enumerate(windows, 1):
        source = sources[window["source_stem"]]
        selected = [cue for cue in cues if cue.get("source_stem") == window["source_stem"]
                    and float(cue["end"]) > window["local_start"]
                    and float(cue["start"]) < window["local_end"]]
        rows = []
        events = []
        for cue in selected:
            counter += 1
            cue_ref = f"q{counter:05d}"
            if LINK_MENTION.search(str(cue.get("text") or "")):
                events.append({"event": "link_announcement", "source": aliases[window["source_stem"]],
                               "time": round(float(cue["start"]), 3),
                               "note": "number_redacted_auxiliary_only"})
            pieces, removed = clean_cue(cue, cue_ref)
            for piece_no, piece in enumerate(pieces, 1):
                ref = cue_ref if len(pieces) == 1 else f"{cue_ref}p{piece_no}"
                alias = aliases[window["source_stem"]]
                rows.append({"ref": ref, "source": alias,
                             "start": round(float(piece["start"]), 3),
                             "end": round(float(piece["end"]), 3),
                             "text": piece["text"],
                             "needs_cutpoint_check": piece["needs_cutpoint_check"]})
                local_refs[ref] = {"source_stem": window["source_stem"],
                                   "source_alias": aliases[window["source_stem"]],
                                   "source_ref": cue_ref,
                                   "local_start": float(piece["start"]), "local_end": float(piece["end"]),
                                   "global_start": float(source["global_start"]) + float(piece["start"]),
                                   "global_end": float(source["global_start"]) + float(piece["end"]),
                                   "text": piece["text"], "needs_cutpoint_check": piece["needs_cutpoint_check"]}
            # Keep source text and excluded clauses only in the ignored local preview workspace.
            for row in removed:
                local_refs.setdefault("_removed", []).append({**row, "source_stem": window["source_stem"],
                                                                "original_text": cue.get("text")})
        sent.append({"window_id": f"w{number:02d}", "source": aliases[window["source_stem"]],
                     "start": round(window["local_start"], 3), "end": round(window["local_end"], 3),
                     "events": events, "cues": rows})
    return {"product_title": product["title"], "windows": sent}, local_refs


def parse_response(raw: dict) -> dict:
    if isinstance(raw.get("output_text"), str):
        text = raw["output_text"]
    else:
        text = next((content.get("text") for item in raw.get("output", [])
                     for content in item.get("content", []) if content.get("type") == "output_text"), None)
    if not isinstance(text, str):
        raise ValueError("mimo_output_text_missing")
    return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()))


def validate_verified_groups(groups: list[dict]) -> list[str]:
    """Validate the local source identity before compacting groups for stage B."""
    errors, seen_ids, seen_refs = [], set(), set()
    if not isinstance(groups, list):
        return ["groups_not_list"]
    for group in groups:
        if not isinstance(group, dict):
            errors.append("group_not_object")
            continue
        group_id = group.get("group_id")
        refs = group.get("actual_refs")
        if not isinstance(group_id, str) or not group_id or group_id in seen_ids:
            errors.append("group_id_missing_or_duplicate")
        seen_ids.add(group_id)
        if not isinstance(refs, list) or not refs:
            errors.append("source_refs_missing")
            continue
        text_parts = []
        local_ref_ids = set()
        for ref in refs:
            if not isinstance(ref, dict):
                errors.append("source_ref_not_object")
                continue
            ref_id, source = ref.get("ref"), ref.get("source_stem")
            start, end, text = ref.get("local_start"), ref.get("local_end"), ref.get("text")
            if not isinstance(ref_id, str) or not ref_id or ref_id in local_ref_ids:
                errors.append("source_ref_id_missing_or_duplicate")
            local_ref_ids.add(ref_id)
            if not isinstance(source, str) or not source:
                errors.append("source_identity_missing")
            if (not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                    or not math.isfinite(start) or not math.isfinite(end)
                    or not (0 <= start < end)):
                errors.append("source_interval_invalid")
            if not isinstance(text, str):
                errors.append("source_text_missing")
            else:
                text_parts.append(text)
            if ref_id in seen_refs:
                errors.append("source_ref_reused_across_groups")
            seen_refs.add(ref_id)
        if "".join(text_parts) != group.get("text"):
            errors.append("group_text_source_mismatch")
    return errors


def call_cached(stage: str, document: dict, folder: Path):
    if stage == "B" and "verified_groups" in document:
        input_errors = validate_verified_groups(document.get("verified_groups") or [])
        if input_errors:
            raise ValueError("invalid_verified_groups_for_assembly")
    text = mimo_prompt(document, "understand" if stage == "A" else "assemble")
    request_body = {"model": MODEL, "input": text,
                    "max_output_tokens": int(document.get("_mimo_output_budget") or
                                             (STAGE_A_MAX_OUTPUT_TOKENS if stage == "A" else 8000)),
                    "reasoning": {"effort": "low"}, "stream": True}
    encoded = json.dumps(request_body, ensure_ascii=False, separators=(",", ":")).encode()
    fingerprint = hashlib.sha256(encoded).hexdigest()
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"stage_{stage}_{fingerprint[:16]}"
    request_path, output_path, meta_path = (folder / f"{stem}_request.json",
                                            folder / f"{stem}_response.json",
                                            folder / f"{stem}_meta.json")
    for existing_meta_path in folder.glob(f"stage_{stage}_*_meta.json"):
        meta = json.loads(existing_meta_path.read_text())
        if meta.get("request_sha256") != fingerprint:
            continue
        existing_stem = existing_meta_path.name[:-len("_meta.json")]
        existing_output = folder / f"{existing_stem}_response.json"
        existing_request = folder / f"{existing_stem}_request.json"
        if meta.get("status") == "completed" and existing_output.is_file():
            return json.loads(existing_output.read_text()), float(meta.get("elapsed_seconds", 0)), True, existing_request
        if meta.get("status") == "in_progress":
            lock = folder / f"{existing_stem}.lock"
            started_at = meta.get("started_at")
            age = time.time() - float(started_at) if isinstance(started_at, (int, float)) \
                else time.time() - existing_meta_path.stat().st_mtime
            if age is not None and age > API_TIMEOUT + 60:
                meta.update({"status": "uncertain", "recovery": "stale_in_progress_no_automatic_resend",
                             "recovery_age_seconds": round(age, 1)})
                existing_meta_path.write_text(json.dumps(meta, indent=2) + "\n")
                raise ValueError("mimo_request_previously_attempted_no_resend")
            alive = False
            if lock.exists():
                try:
                    lock_data = json.loads(lock.read_text())
                    os.kill(int(lock_data["pid"]), 0)
                    alive = True
                except ProcessLookupError:
                    lock.unlink(missing_ok=True)
                except PermissionError:
                    alive = True
                except (OSError, ValueError, KeyError, json.JSONDecodeError):
                    # Unknown lock files may belong to an active request. Let
                    # the bounded request window expire before recovering them.
                    if time.time() - lock.stat().st_mtime <= API_TIMEOUT + 60:
                        alive = True
                    else:
                        lock.unlink(missing_ok=True)
            if not alive:
                meta.update({"status": "uncertain", "recovery": "orphaned_in_progress_no_automatic_resend"})
                existing_meta_path.write_text(json.dumps(meta, indent=2) + "\n")
            else:
                raise ValueError("mimo_request_process_still_running_no_resend")
        raise ValueError("mimo_request_previously_attempted_no_resend")
    if output_path.exists():
        raise ValueError("mimo_output_exists_no_overwrite")
    key = os.environ.get("MIMO_API_KEY")
    if not key:
        request_path.write_text(json.dumps(request_body, ensure_ascii=False, indent=2) + "\n")
        return None, 0.0, False, request_path
    lock_path = folder / f"{stem}.lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        owner_alive = None
        try:
            lock_data = json.loads(lock_path.read_text())
            pid = int(lock_data["pid"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            pass
        else:
            try:
                os.kill(pid, 0)
                owner_alive = True
            except ProcessLookupError:
                owner_alive = False
            except PermissionError:
                owner_alive = True
            except OSError:
                owner_alive = None
        if owner_alive is True:
            raise ValueError("mimo_request_process_still_running_no_resend") from exc
        if owner_alive is None and time.time() - lock_path.stat().st_mtime <= API_TIMEOUT + 60:
            raise ValueError("mimo_request_lock_unknown_no_resend") from exc
        if owner_alive is False or owner_alive is None:
            lock_path.unlink(missing_ok=True)
        # A request record is written before the HTTP call. With no matching
        # metadata above, an orphan lock cannot represent a sent request.
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.write(lock_fd, json.dumps({"pid": os.getpid(), "request_sha256": fingerprint}).encode())
    os.close(lock_fd)
    request_path.write_text(json.dumps(request_body, ensure_ascii=False, indent=2) + "\n")
    request_id = uuid.uuid4().hex
    meta = {"request_sha256": fingerprint, "request_id": request_id, "stage": stage,
            "status": "in_progress", "model": MODEL,
            "request_bytes": len(encoded), "output_budget": request_body["max_output_tokens"],
            "started_at": time.time()}
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")
    try:
        raw, metrics = create_response(request_body, timeout=API_TIMEOUT,
                                       stage=stage, request_id=request_id)
        parse_response(raw)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{stem}.", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                target.write(json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary_name, output_path)
        finally:
            Path(temporary_name).unlink(missing_ok=True)
        meta.update({"status": "completed", "response_status": raw.get("status"),
                     "elapsed_seconds": metrics.get("total_seconds", 0), **metrics,
                     "output_tokens": (raw.get("usage") or {}).get("output_tokens")})
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")
        return raw, metrics.get("response_seconds", metrics.get("total_seconds", 0)), False, request_path
    except ResponseAPIError as exc:
        if exc.code == "response_incomplete":
            status = "incomplete"
        elif exc.http_status == 200 and not (exc.metrics or {}).get("terminal_event_received"):
            status = "uncertain"
        else:
            status = "failed"
        meta.update({"status": status,
                     "error_code": exc.code, "http_status": exc.http_status,
                     **(exc.metrics or {})})
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")
        raise
    except BaseException as exc:
        meta.update({"status": "uncertain" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed",
                     "error_code": type(exc).__name__, **getattr(exc, "mimo_metrics", {})})
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")
        raise
    finally:
        lock_path.unlink(missing_ok=True)


def validate_phase_a(response: dict, local_refs: dict):
    valid = set(local_refs) - {"_removed"}
    groups, excluded = [], []
    errors = []
    identity = response.get("product_identity") or {}
    for window in response.get("windows", []):
        for group in window.get("sentence_groups", []):
            refs = group.get("refs") or []
            if not refs or any(ref not in valid for ref in refs):
                errors.append({"code": "UNKNOWN_SOURCE_REF", "group_id": group.get("group_id")})
                continue
            exact = "".join(local_refs[ref]["text"] for ref in refs)
            if str(group.get("text") or "") != exact:
                errors.append({"code": "MIMO_TEXT_NOT_EXACT_SOURCE", "group_id": group.get("group_id")})
                continue
            row = dict(group)
            row["window_id"] = window.get("window_id")
            row["actual_refs"] = [{"ref": ref, **local_refs[ref]} for ref in refs]
            row["text"] = exact
            if row.get("product_attribution") != "MATCH":
                excluded.append({"group_id": row.get("group_id"), "refs": refs,
                                 "reason": row.get("product_attribution") or "PRODUCT_UNCERTAIN"})
            else:
                groups.append(row)
    if identity.get("match") != "MATCH":
        errors.append({"code": "PRODUCT_IDENTITY_UNCERTAIN", "match": identity.get("match")})
    seen = set()
    for group in groups:
        group_id = group.get("group_id")
        if not group_id or group_id in seen:
            errors.append({"code": "DUPLICATE_OR_MISSING_GROUP_ID", "group_id": group_id})
        seen.add(group_id)
    return groups, excluded, errors


def group_duration(group: dict) -> float:
    intervals = {}
    for ref in group["actual_refs"]:
        intervals.setdefault(ref["source_stem"], []).append((ref["local_start"], ref["local_end"]))
    total = 0.0
    for rows in intervals.values():
        rows.sort()
        left, right = rows[0]
        for start, end in rows[1:]:
            if start <= right:
                right = max(right, end)
            else:
                total += right - left
                left, right = start, end
        total += right - left
    return total


def audit_assembly(response: dict, groups: list[dict]):
    group_errors = validate_verified_groups(groups)
    if group_errors:
        return [], [{"code": "INVALID_VERIFIED_GROUPS", "reasons": sorted(set(group_errors))}]
    by_id = {str(row.get("group_id")): row for row in groups}
    errors, variants = [], []
    incoming = response.get("selected_variants") or []
    if len(incoming) > 3 or (not incoming and not str(response.get("insufficient_reason") or "").strip()):
        errors.append({"code": "VARIANT_COUNT_INVALID", "count": len(incoming)})
    used_intervals, used_groups, texts = [], set(), []
    for variant in incoming:
        variant_id = str(variant.get("variant_id") or "")
        ids = variant.get("group_ids") or []
        selected = [by_id[group_id] for group_id in ids if group_id in by_id]
        if not ids or len(selected) != len(ids):
            errors.append({"variant_id": variant_id, "code": "UNKNOWN_SENTENCE_GROUP"})
            continue
        if used_groups.intersection(ids):
            errors.append({"variant_id": variant_id, "code": "GROUP_REUSED_ACROSS_VARIANTS"})
        used_groups.update(ids)
        total = sum(group_duration(row) for row in selected)
        if not 45.0 < total < 60.0:
            errors.append({"variant_id": variant_id, "code": "DURATION_INVALID", "duration": round(total, 3)})
        text = "".join(row["text"] for row in selected)
        for risk in dependency_risks(text):
            errors.append({"variant_id": variant_id, "code": risk})
        intervals = []
        for group in selected:
            for ref in group["actual_refs"]:
                interval = (ref["source_stem"], ref["local_start"], ref["local_end"],
                            variant_id, group["group_id"])
                intervals.append(interval)
        for i, (source, start, end, _, group_id) in enumerate(intervals):
            others = used_intervals + [
                row for row in intervals[:i] if row[4] != group_id
            ]
            for other_source, other_start, other_end, other_variant, _other_group in others:
                if source == other_source and min(end, other_end) - max(start, other_start) > 0:
                    errors.append({"variant_id": variant_id, "code": "SOURCE_TIME_OVERLAP",
                                   "with": other_variant or variant_id})
        used_intervals.extend(intervals)
        for old_id, old_text in texts:
            if semantic_duplicate(text, old_text):
                errors.append({"variant_id": variant_id, "code": "SEMANTIC_DUPLICATE", "with": old_id})
        texts.append((variant_id, text))
        variants.append({"variant_id": variant_id, "focus": variant.get("focus"),
                         "group_ids": ids, "spoken_order": [row.get("claim") for row in selected],
                         "duration_estimate": round(total, 3), "text": text,
                         "sentence_groups": selected,
                         "status": "REVIEW_PENDING"})
    for variant in variants:
        variant_errors = [row for row in errors if row.get("variant_id") == variant["variant_id"]]
        variant["hard_rule_errors"] = variant_errors
        if variant_errors:
            variant["status"] = "REPAIRABLE"
    return variants, errors


def markdown_preview(product: dict, rough: dict, groups: list[dict], excluded: list[dict],
                     variants: list[dict], removals: list[dict], metrics: dict, errors: list[dict]):
    lines = [f"# 商品 {product['product_index']} 文字审核预览", "",
             f"- 标题：{product['title']}",
             f"- 商品身份：{'MATCH' if not errors else 'PRODUCT_UNCERTAIN'}；依据当前工作簿物理行、编码ID与字幕语义窗口。",
             "- 窗口性质：粗略候选，未认证为 product_ranges.json，不能直接剪辑。", "",
             "## 粗略窗口", ""]
    for window in rough.get("windows", []):
        evidence = window.get("evidence_rows", [])
        terms = sorted({term for row in evidence for term in row.get("feature_terms", [])})
        status = "PRODUCT_UNCERTAIN" if not evidence or all(row.get("status") == "PRODUCT_UNCERTAIN" for row in evidence) else "ROUGH_CANDIDATE"
        lines.append(f"- {window['source_stem']} {window['global_start']:.1f}–{window['global_end']:.1f}s；"
                     f"特征={','.join(terms)}；状态={status}")
    lines += ["", "## 商品归属证据", ""]
    for number, window in enumerate(rough.get("windows", []), 1):
        evidence = window.get("evidence_rows", [])
        terms = sorted({term for row in evidence for term in row.get("feature_terms", [])})
        count = sum(int(row.get("matched_cue_count", 0)) for row in evidence)
        lines.append(f"- w{number:02d}: 命中特征 {', '.join(terms) or '无'}；相关字幕命中约 {count} 条。链接号仅作旁注，不参与定位分数。")
    lines += ["", "## 有效卖点句群（阶段A）", ""]
    if not groups:
        lines.append("- 无通过来源引用与商品归属校验的句群。")
    for group in groups:
        citations = ", ".join(f"{r['source_stem']} {r['local_start']:.1f}–{r['local_end']:.1f}s" for r in group["actual_refs"])
        flag = "；需实际切点检查" if any(r["needs_cutpoint_check"] for r in group["actual_refs"]) else ""
        lines.append(f"- **{group.get('selling_point_category','未分类')}**：{group.get('claim','')}（{citations}{flag}）\n  - 原文：{group['text']}")
        if group.get("relations"):
            lines.append(f"  - 关系：{'; '.join(map(str, group['relations']))}")
    lines += ["", "## 版本组装（阶段B）", ""]
    if not variants:
        lines.append("- 暂无可组装版本。")
    for variant in variants:
        lines.append(f"### {variant['variant_id']}：{variant.get('focus') or '卖点组合'} — 预计 {variant['duration_estimate']:.1f}s；状态 {variant['status']}")
        for group in variant["sentence_groups"]:
            lines.append(f"- {group.get('selling_point_category','')}：{group.get('claim','')} — {group['text']}")
        if variant["hard_rule_errors"]:
            lines.append(f"- 本地硬规则问题：{variant['hard_rule_errors']}")
    lines += ["", "## 排除与不确定", ""]
    lines.append(f"- 阶段A排除/不确定句群：{len(excluded)}；粗窗口归属/冲突问题：{errors or '未发现结构错误'}。")
    lines.append(f"- 禁用子句：{len(removals)} 条已在本地逐子句排除；有效同句片段保留并标记切点检查。")
    for row in excluded:
        lines.append(f"- {row.get('group_id')}: {row.get('reason')}")
    lines += ["", "## 需 ASR/实片确认", "",
              "- 字幕方案不证明音节完整、实际音视频同步或最终剪辑质量；所有 `needs_cutpoint_check` 位置须由后续局部 ASR/成片检查。",
              "- 独立 Codex 审核状态：REVIEW_PENDING（需要独立核对每条句群归属、闭合、顺序与跨版重复）。", "",
              "## 耗时与调用", "",
              f"- 阶段A：{metrics.get('stage_a_seconds',0):.2f}s，MiMo调用 {metrics.get('stage_a_calls',0)}，缓存命中 {metrics.get('stage_a_cache_hits',0)}。",
              f"- 阶段B：{metrics.get('stage_b_seconds',0):.2f}s，MiMo调用 {metrics.get('stage_b_calls',0)}，缓存命中 {metrics.get('stage_b_cache_hits',0)}。",
              f"- 模型：{MODEL}。ASR/实体剪辑：未执行。"]
    return "\n".join(lines) + "\n"


def run(root: Path, workspace: Path, product_ids: list[int], output_root: Path, *, offline: bool = False):
    state, products, index, fingerprint = current_inputs(root, workspace, product_ids)
    rough = build_windows(index, products, {})
    output_root.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "authority": "LOCAL_PREVIEW_ONLY_NOT_AUTHENTICATED_RANGES",
                "product_ids": product_ids, "subtitle_fingerprint": fingerprint, "model": MODEL,
                "miMo_data_scope": ["product title", "locally cleaned candidate subtitle", "timecodes"],
                "products": []}
    for product in products:
        pid = int(product["product_index"])
        row = next(r for r in rough["products"] if int(r["product_index"]) == pid)
        phase_a, local_refs = make_phase_a(product, row["windows"], index)
        folder = output_root / f"product_{pid:02d}"
        if offline:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "STAGE_A_LOCAL_CANDIDATES.json").write_text(
                json.dumps(phase_a, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            raw_a, a_time, a_hit, a_failure = None, 0.0, False, "OFFLINE_NO_MIMO_CALL"
        else:
            try:
                raw_a, a_time, a_hit, _ = call_cached("A", phase_a, folder)
                a_failure = None
            except Exception as exc:
                meta_candidates = list(folder.glob("stage_A_*_meta.json")) + [folder / "stage_A_meta.json"]
                failure_meta = next((json.loads(path.read_text()) for path in meta_candidates if path.exists()), {})
                raw_a, a_time, a_hit = None, float(failure_meta.get("elapsed_seconds", 0)), False
                a_failure = type(exc).__name__
        metrics = {"stage_a_seconds": a_time,
                   "stage_a_calls": int(bool(os.environ.get("MIMO_API_KEY")) and not a_hit),
                   "stage_a_cache_hits": int(a_hit), "stage_b_seconds": 0.0,
                   "stage_b_calls": 0, "stage_b_cache_hits": 0}
        groups, excluded, a_errors = ([], [], [{"code": "MIMO_STAGE_A_UNAVAILABLE", "error_type": a_failure}])
        if raw_a is not None:
            try:
                response_a = parse_response(raw_a)
                (folder / "stage_A_validated.json").write_text(json.dumps(response_a, ensure_ascii=False, indent=2) + "\n")
                groups, excluded, a_errors = validate_phase_a(response_a, local_refs)
            except Exception as exc:
                a_errors = [{"code": "MIMO_STAGE_A_SCHEMA_INVALID", "error_type": type(exc).__name__}]
        phase_b_result, variants, b_errors = None, [], []
        if groups and not any(e.get("code") == "PRODUCT_IDENTITY_UNCERTAIN" for e in a_errors):
            doc_b = {"product_title": product["title"], "verified_groups": groups}
            try:
                raw_b, b_time, b_hit, _ = call_cached("B", doc_b, folder)
                b_failure = None
            except Exception as exc:
                meta_path = folder / "stage_B_meta.json"
                failure_meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
                raw_b, b_time, b_hit = None, float(failure_meta.get("elapsed_seconds", 0)), False
                b_failure = type(exc).__name__
            metrics["stage_b_seconds"] = b_time
            metrics["stage_b_calls"] = int(bool(os.environ.get("MIMO_API_KEY")) and not b_hit)
            metrics["stage_b_cache_hits"] = int(b_hit)
            if raw_b is not None:
                try:
                    phase_b_result = parse_response(raw_b)
                    variants, b_errors = audit_assembly(phase_b_result, groups)
                    (folder / "stage_B_validated.json").write_text(json.dumps(phase_b_result, ensure_ascii=False, indent=2) + "\n")
                except Exception as exc:
                    b_errors = [{"code": "MIMO_STAGE_B_SCHEMA_INVALID", "error_type": type(exc).__name__}]
            elif b_failure:
                b_errors = [{"code": "MIMO_STAGE_B_UNAVAILABLE", "error_type": b_failure}]
        else:
            b_errors = [{"code": "STAGE_A_NOT_VERIFIED"}]
        removals = local_refs.get("_removed", [])
        preview = markdown_preview(product, row, groups, excluded, variants, removals, metrics, a_errors + b_errors)
        (folder / "PREVIEW.md").write_text(preview, encoding="utf-8")
        detail = {"product_index": pid, "encoding_id": product["encoding_id"], "title": product["title"],
                  "status": "REVIEW_PENDING", "rough_windows": row["windows"],
                  "phase_a_errors": a_errors, "phase_a_excluded": excluded,
                  "selling_point_groups": groups, "phase_b_errors": b_errors,
                  "variants": variants, "removed_clauses": removals, "metrics": metrics,
                  "source_refs": {k: v for k, v in local_refs.items() if k != "_removed"}}
        (folder / "PREVIEW.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest["products"].append({"product_index": pid, "status": detail["status"],
                                     "preview_md": str((folder / "PREVIEW.md").relative_to(output_root)),
                                     "preview_json": str((folder / "PREVIEW.json").relative_to(output_root)),
                                     "stage_a_errors": len(a_errors), "stage_b_errors": len(b_errors),
                                     "variant_count": len(variants), "metrics": metrics})
    (output_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--products", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--offline", action="store_true", help="build local clean candidate windows without contacting MiMo")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    workspace = Path(args.workspace).expanduser().resolve()
    environment = os.environ.get("TB_CUT_WORKSPACE")
    if environment and Path(environment).expanduser().resolve() != workspace:
        parser.error("workspace_identity_conflict")
    output_root = (workspace / args.output).resolve()
    if workspace not in output_root.parents:
        parser.error("output_must_be_inside_selected_workspace")
    manifest = run(root, workspace, [int(v) for v in args.products.split(",") if v.strip()],
                   output_root, offline=args.offline)
    for row in manifest["products"]:
        print(f"商品{row['product_index']}: {row['status']} variants={row['variant_count']} "
              f"preview={row['preview_md']}")


if __name__ == "__main__":
    main()
