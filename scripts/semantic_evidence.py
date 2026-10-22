"""Shared semantic evidence helpers for rough product windows and MiMo stages.

These functions create review candidates only. They never publish authenticated
product ranges or authorize physical edits.
"""
from __future__ import annotations

import re
from copy import deepcopy

FORBIDDEN = re.compile(
    r"价格|价位|售价|到手价|成交价|优惠券|优惠|补贴|满减|立减|券后|平台券|红包|抽奖|"
    r"福袋|倒计时|库存|关注|点赞|"
    r"(?:\d+|[零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟]+)\s*"
    r"(?:元|毛钱?|折扣?|号链接|块钱|块(?:\d+|[零〇一二两三四五六七八九十百千万亿]+)?毛?(?=[，。！？；,;!? ]|$))|"
    r"满.{0,12}(?:减|送|赠)|买.{0,8}(?:送|赠)|几号链接|"
    r"点\s*(?:\d+|[零〇一二两三四五六七八九十百千万亿]+)\s*号?链接|"
    r"链接\s*(?:\d+|[零〇一二两三四五六七八九十百千万亿]+)\s*号?|"
    r"点链接|链接下单|下单|包邮|半价|特价|秒杀|活动价|上车|拍下|"
    r"欢迎.{0,12}(?:来到|进入|宝宝|姐妹)|主播.{0,12}(?:后台|操作)|后台|流程|对焦|镜头|摄像头"
)
CLAUSE_SPLIT = re.compile(r"(?<=[，。！？；,;!?])|(?=但是|不过|但|然而|可是|而且|然后|所以|因为|另外|同时)")

# Feature words are deliberately broad; direct title features rank windows but
# cannot by themselves certify product ownership.
FEATURES = tuple(sorted({
    "白鹅绒", "鹅绒", "羽绒", "排骨", "尼龙长丝", "超细尼龙", "小立领",
    "康赛妮", "羊绒", "羊毛", "V领", "针织开衫", "开衫", "摇粒绒", "驼绒",
    "Limonta", "麂皮", "反绒", "羊反绒", "格纹", "豆荚袖", "夹克", "皮毛一体",
    "拉贡", "LACON", "皮草", "水貂", "貂", "直毛", "翻领", "双面呢", "斗篷",
    "马甲", "半裙", "Piacenza", "羊绒羊毛", "色织", "绉纱", "抽绳", "直筒裤",
    "阔腿裤", "豹纹", "猫眼", "粗花呢", "羊驼", "长绒棉", "花边", "T恤",
    "毛衣", "大衣", "外套", "皮衣", "裤子", "裙子", "套装", "真丝", "丝绒", "收腰", "立领",
    "棉", "皮", "呢", "流浪感", "山竹裤", "大长T", "荡领", "背心", "气球裙",
    "不规则下摆", "围巾领", "BIELLA", "多纽扣", "POLO", "小A字", "宝嘉美",
    "水貂围脖", "深V领", "空气感", "香蕉奶奶裤", "大卫衣", "VBC", "百褶半裙",
    "船领", "连帽大围脖", "九分裤", "摇粒绒", "骑马靴", "袜靴", "马海毛",
    "棉服夹克", "轻油蜡", "一脚蹬", "棒球领", "苏力羊驼", "花瓣袖", "八分牛仔裤",
    "流苏大披肩", "无领西装", "银河牛仔裤", "TEXCO", "独角兽农场", "婴驼",
    "袋鼠口袋工装裤", "中古短皮衣", "浴袍", "小拉贡", "烟囱领", "白鹅绒",
    "飞机领", "赤耳红边", "十字水貂夹克", "熊猫夹克", "胎羔小湖羊", "针织醋酸",
    "大围脖", "开衩披肩", "羊皮袜靴", "中高筒", "皮草感", "短裤针织套装", "巴素兰",
    "气球", "松紧腰", "A字毛呢半裙", "深V", "撞色条纹", "八分", "窄臀", "长半裙",
    "花瓣袖", "弧形八分", "皇室英驼", "英驼", "发光", "浴袍式短大衣", "短大衣",
    "双面穿飞机领", "绳染", "白鹅绒", "羊毛棉", "T恤", "双开拉链", "短裤",
    "中筒", "骑士靴", "平底", "瘦腿"
}, key=len, reverse=True))
GENERIC = {"棉", "皮", "呢", "外套", "裤子", "裙子", "套装", "大衣", "毛衣"}
DISCOVERY_FEATURES = {
    "白鹅绒", "排骨", "尼龙长丝", "超细尼龙", "小立领", "康赛妮", "针织开衫",
    "摇粒绒", "驼绒", "Limonta", "麂皮", "反绒", "羊反绒", "豆荚袖", "皮毛一体",
    "拉贡", "LACON", "直毛", "双面呢", "斗篷", "Piacenza", "色织", "绉纱",
    "抽绳", "直筒裤", "阔腿裤", "豹纹", "猫眼", "粗花呢", "羊驼", "长绒棉", "花边",
    "真丝", "丝绒", "收腰", "立领", "流浪感", "山竹裤", "大长T", "荡领", "背心",
    "气球裙", "不规则下摆", "围巾领", "BIELLA", "多纽扣", "POLO", "小A字",
    "宝嘉美", "水貂围脖", "深V领", "空气感", "香蕉奶奶裤", "大卫衣", "VBC",
    "百褶半裙", "船领", "连帽大围脖", "九分裤", "骑马靴", "袜靴", "马海毛",
    "棉服夹克", "轻油蜡", "一脚蹬", "棒球领", "苏力羊驼", "花瓣袖", "八分牛仔裤",
    "流苏大披肩", "无领西装", "银河牛仔裤", "TEXCO", "独角兽农场", "婴驼",
    "袋鼠口袋工装裤", "中古短皮衣", "浴袍", "小拉贡", "烟囱领", "白鹅绒",
    "飞机领", "赤耳红边", "十字水貂夹克", "熊猫夹克", "胎羔小湖羊", "针织醋酸",
    "大围脖", "开衩披肩", "羊皮袜靴", "中高筒", "皮草感", "短裤针织套装", "巴素兰",
    "双开拉链", "短裤", "气球", "A字毛呢半裙", "深V", "撞色条纹", "窄臀", "花瓣袖",
    "弧形八分", "皇室英驼", "英驼", "发光", "浴袍式短大衣", "双面穿飞机领", "绳染", "T恤",
    "双开拉链", "短裤", "中筒", "骑士靴", "平底", "瘦腿"
}


def title_features(title: str) -> list[str]:
    return [term for term in FEATURES if term in title]


def semantic_windows(index: dict, products: list[dict], *, before: float = 75.0,
                     after: float = 105.0, cluster_gap: float = 105.0) -> dict:
    """Find wide lexical windows from the complete indexed stream.

    Link mentions are retained as annotations but have zero ranking weight.
    Product order is never inferred from workbook row or link number.
    """
    cues = index.get("cues") or []
    sources = {row["source_stem"]: row for row in index.get("sources") or []}
    prepared = []
    for product in products:
        features = title_features(str(product.get("title") or ""))
        matches = []
        for cue_index, cue in enumerate(cues):
            text = str(cue.get("text") or "")
            found = [term for term in features if term.casefold() in text.casefold()]
            discovery = [term for term in found if term in DISCOVERY_FEATURES]
            strong = [term for term in found if term not in GENERIC and len(term) > 1]
            score = sum(1 if term in GENERIC else 3 if len(term) >= 3 else 2 for term in found)
            if discovery:
                matches.append({"cue_index": cue_index, "cue": cue, "terms": found,
                                "discovery_terms": discovery,
                                "strong_terms": strong, "score": score})
        clusters = []
        for row in matches:
            at = float(row["cue"].get("global_start", 0))
            if not clusters or at - float(clusters[-1][-1]["cue"].get("global_start", 0)) > cluster_gap:
                clusters.append([])
            clusters[-1].append(row)
        prepared.append({"product": product, "features": features, "matches": matches,
                         "clusters": clusters})

    results = []
    for entry in prepared:
        product = entry["product"]
        windows = []
        for number, cluster in enumerate(entry["clusters"], 1):
            start = float(cluster[0]["cue"]["global_start"])
            end = float(cluster[-1]["cue"].get("global_end", cluster[-1]["cue"]["global_start"]))
            source = sources.get(cluster[0]["cue"].get("source_stem"), {})
            global_start = max(float(source.get("global_start", 0)), start - before)
            global_end = min(float(source.get("global_end", end)), end + after)
            other_scores = []
            conflict_count = 0
            for hit in cluster:
                scores = []
                for other in prepared:
                    if other["product"].get("product_index") == product.get("product_index"):
                        continue
                    same = next((m["score"] for m in other["matches"]
                                 if m["cue_index"] == hit["cue_index"]), 0)
                    scores.append(same)
                rival = max(scores, default=0)
                other_scores.append(rival)
                if rival >= hit["score"] and rival > 0:
                    conflict_count += 1
            unique_terms = sorted({term for row in cluster for term in row["discovery_terms"]})
            conflict_ratio = conflict_count / max(1, len(cluster))
            confident = bool(unique_terms) and conflict_ratio < 0.35
            windows.append({
                "window_id": f"w{number:02d}", "source_stem": cluster[0]["cue"].get("source_stem"),
                "global_start": round(global_start, 3), "global_end": round(global_end, 3),
                "evidence_start": round(start, 3), "evidence_end": round(end, 3),
                "feature_terms": sorted({term for row in cluster for term in row["terms"]}),
                "distinctive_terms": unique_terms, "matched_cue_count": len(cluster),
                "conflict_ratio": round(conflict_ratio, 3),
                "status": "ROUGH_CANDIDATE" if confident else "PRODUCT_UNCERTAIN",
                "authenticated": False,
            })
        results.append({
            "product_index": product.get("product_index"),
            "encoding_id": product.get("encoding_id"),
            "title": product.get("title"), "features": entry["features"],
            "status": "ROUGH_CANDIDATE" if any(w["status"] == "ROUGH_CANDIDATE" for w in windows)
                     else "PRODUCT_UNCERTAIN",
            "windows": windows,
        })
    return {"schema_version": 1, "kind": "rough_semantic_windows",
            "authority": "NOT_AUTHENTICATED_NOT_FOR_CUTTING", "products": results}


def clean_cue(cue: dict, cue_ref: str) -> tuple[list[dict], list[dict]]:
    """Retain allowed clauses of a mixed cue and preserve source/time citations."""
    text = str(cue.get("text") or "")
    parts = [part for part in CLAUSE_SPLIT.split(text) if part]
    kept, removed = [], []
    mixed = any(FORBIDDEN.search(part) for part in parts) and any(not FORBIDDEN.search(part) for part in parts)
    for part in parts:
        match = FORBIDDEN.search(part)
        if match:
            # If a ban phrase is embedded in an otherwise useful cue without
            # punctuation, retain only the verifiable prefix. Never send the
            # banned clause or guess an audio boundary.
            prefix = part[:match.start()].strip(" ，。！？；,;!? ")
            if prefix and not FORBIDDEN.search(prefix):
                kept.append({"cue_ref": cue_ref, "start": float(cue["start"]), "end": float(cue["end"]),
                             "text": prefix, "needs_cutpoint_check": True,
                             "source_cue_ref": cue_ref})
            removed.append({"cue_ref": cue_ref, "start": cue.get("start"), "end": cue.get("end"),
                            "reason": "forbidden_clause", "source_retained_locally": True})
            continue
        if not part.strip("，。！？；,;!? "):
            continue
        kept.append({"cue_ref": cue_ref, "start": float(cue["start"]), "end": float(cue["end"]),
                     "text": part, "needs_cutpoint_check": len(parts) > 1 or mixed,
                     "source_cue_ref": cue_ref})
    if not kept and not removed:
        kept.append({"cue_ref": cue_ref, "start": float(cue["start"]), "end": float(cue["end"]),
                     "text": text, "needs_cutpoint_check": False,
                     "source_cue_ref": cue_ref})
    return kept, removed


def subtract_used_intervals(candidates: list[dict], used: list[dict]) -> list[dict]:
    """Subtract physically used intervals; keep the unused remainder of long candidates."""
    result = []
    for candidate in candidates:
        bounds = candidate.get("time_range") or {}
        start, end = float(bounds.get("start", 0)), float(bounds.get("end", 0))
        source = candidate.get("source_file")
        cuts = sorted((max(start, float(row["start"])), min(end, float(row["end"])))
                      for row in used if row.get("source_file", source) == source
                      and float(row["end"]) > start and float(row["start"]) < end)
        pieces, cursor = [], start
        for left, right in cuts:
            if left > cursor:
                pieces.append((cursor, left))
            cursor = max(cursor, right)
        if cursor < end:
            pieces.append((cursor, end))
        for index, (left, right) in enumerate(pieces, 1):
            if right - left <= 0.02:
                continue
            row = deepcopy(candidate)
            row["candidate_id"] = f"{candidate.get('candidate_id')}_residual_{index}"
            row["time_range"] = {"start": round(left, 3), "end": round(right, 3)}
            row["subtitle"] = [cue for cue in candidate.get("subtitle", [])
                               if float(cue["end"]) > left and float(cue["start"]) < right]
            row["asr_text"] = "".join(cue.get("text", "") for cue in row["subtitle"])
            row["residual_of"] = candidate.get("candidate_id")
            result.append(row)
    return result


def dependency_risks(text: str) -> list[str]:
    """Conservative text-only flags; never reject a connector without an open dependency."""
    clean = re.sub(r"[\W_]", "", text).lower()
    if not clean:
        return ["EMPTY_TEXT"]
    risks = []
    if clean.endswith(("然后", "所以", "因为", "但是", "而且", "如果", "比如", "这个", "那个", "它的", "第一", "第二")):
        risks.append("TAIL_CLOSURE_RISK")
    # A final conclusion can stand alone even if its redundant introductory
    # clause began with 因为; only explicit dangling references are flagged.
    if clean.startswith(("这个", "那个", "它的", "他们的")) and len(clean) < 18:
        risks.append("UNRESOLVED_REFERENCE")
    return risks
