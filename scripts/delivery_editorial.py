"""Source-bound primary Codex assembly review, before audio/vision work.

This validates coverage and provenance, never infers semantic PASS from keywords.
The primary editor must actually read and judge the generated minimal task.
"""
from delivery_assembly import checked_file
from delivery_runtime import require
from project_registry import digest
from runtime_meta import _sha256

CONTRACT = 'source-bound-assembly-review-v5-causal-coherence'
UNIT_CHECKS = {'opening_complete','ending_complete','no_internal_unfinished_clause',
               'source_dependencies_resolved','removed_words_safe','disfluency_reviewed'}
SEAM_CHECKS = {'reference_resolved','causal_conditions_preserved','transition_complete','no_information_repeat'}


def task(paths, recipe):
    records = {}; variants = []
    for variant in recipe['variants']:
        units = []
        for choice in variant['clips']:
            ref = recipe['asr_records'][choice['asr']]; key = digest(ref)
            if key not in records:
                record = checked_file(ref, paths.workspace_root)
                require(digest(record['body']) == record['body_digest'], 'asr_body_digest_mismatch')
                records[key] = record['body']
            window = records[key]['windows'][choice['window']]
            words = window['words']; first,last = choice['first'],choice['last']
            require(type(first) is int and type(last) is int and 0 <= first <= last < len(words), 'invalid_editorial_word_selection')
            units.append(dict(unit_id=choice['unit_id'],source_file=window['source_file'],selection=choice,
                asr_sha256=ref['sha256'],retained_words=words[first:last+1],
                previous_words=words[max(0,first-16):first],next_words=words[last+1:last+17],
                context_may_require_expansion=True))
        require(len({u['unit_id'] for u in units}) == len(units), 'duplicate_editorial_unit_id')
        variants.append(dict(variant_id=variant['variant_id'],focus=variant['focus'],units=units,
            seams=[dict(left=a['unit_id'],right=b['unit_id']) for a,b in zip(units,units[1:])]))
    require(len({v['variant_id'] for v in variants}) == len(variants), 'duplicate_editorial_variant_id')
    index = checked_file(recipe['index'], paths.workspace_root)
    # ASR word context and independently indexed source context expose omitted
    # subjects and unfinished clauses; no complete subtitle or ASR dump.
    for v in variants:
        for u in v['units']:
            lo=u['retained_words'][0]['start']; hi=u['retained_words'][-1]['end']
            u['source_context']=[r for r in index['cues'] if r['source_stem']==u['source_file'].rsplit('.',1)[0]
                                 and r['end'] > lo-5 and r['start'] < hi+5]
    titles=checked_file(recipe['titles'],paths.workspace_root)
    product=next(p for p in titles['products'] if p['product_index']==recipe['product_id'])
    result = dict(rules_sha256=_sha256(paths.repo_root/'PROJECT_RULES.md'),review_producer_sha256=_sha256(paths.repo_root/'scripts/delivery_editorial.py'),product_title=product['title'],titles_sha256=recipe['titles']['sha256'],range_sha256=recipe['range_manifest']['sha256'],contract=CONTRACT,product_id=recipe['product_id'],encoding_id=recipe['encoding_id'],
        input_content_digest=recipe['input_content_digest'],index_sha256=recipe['index']['sha256'],
        variants=variants,unit_checks=sorted(UNIT_CHECKS),seam_checks=sorted(SEAM_CHECKS),
        instructions='主Codex逐段审读最终保留的原词（不是只看字幕概括），按普通观众听完是否容易理解判断：没有明显听感或逻辑大漏洞即可通过，不要求说话人补足所有省略前提或满足形式完整性。结合最小原文上下文检查开头、段内、结尾和接缝。重点检查因果逻辑本身：理由是否支撑结果，剪切是否删掉必要的原因/结果，是否把原本无关的话拼成因果、造成错误暗示；不能只按“因为/所以”等词判断。省略、口语化或隐含因果只要听众能自然理解就通过；仅当因果断裂、矛盾、倒置、无依据或剪辑造成明显误解时标问题。连接词本身不是拒绝理由；条件、转折、列举、指代、承诺也按是否造成实质困惑判断。小幅边界修剪确实能解决时才返修。逐段留简短理由，不要为了填满检查项制造问题。检查明显卡壳、假起句、词语/音节重复及无效填充；只在局部原词/音频证据支持时修剪，不能把语气或正常重复机械删除。跨版本检查卖点重复。声音边界风险另交本机。另输出semantic_map：列出本组商品的核心卖点，每条包含unit_id、category、selling_point、quote；quote必须逐字来自对应unit的retained_words或source_context，不能补写产品属性。')
    result['task_digest']=digest(result)
    return result


def verify(paths, recipe, expected):
    ref = recipe.get('assembly_review')
    require(isinstance(ref,dict), 'ASSEMBLY_REVIEW_REQUIRED')
    review = checked_file(ref,paths.workspace_root)
    require(review.get('contract') == CONTRACT and review.get('task_digest') == expected['task_digest']
            and review.get('reviewer') == 'primary_codex', 'assembly_review_stale_or_wrong_role')
    require(review.get('verdict') == 'PASS' and review.get('issues') == []
            and review.get('cross_variant_duplicates',{}).get('passed') is True
            and bool(review['cross_variant_duplicates'].get('reason','').strip()), 'assembly_semantic_repair_required')
    expected_rows = [(v['variant_id'],u['unit_id']) for v in expected['variants'] for u in v['units']]
    expected_seams = [(v['variant_id'],s['left'],s['right']) for v in expected['variants'] for s in v['seams']]
    def check(rows, keys, checks, wanted):
        require(isinstance(rows,list), 'assembly_review_coverage_missing')
        actual=[tuple(r.get(k) for k in keys) for r in rows]
        require(len(actual)==len(set(actual)) and set(actual)==set(wanted), 'assembly_review_coverage_missing')
        for row in rows:
            require(set(row.get('checks',{})) == checks, 'assembly_review_checks_missing')
            for value in row['checks'].values():
                require(isinstance(value,dict) and value.get('passed') is True
                        and isinstance(value.get('reason'),str) and bool(value['reason'].strip()), 'assembly_semantic_repair_required')
    check(review.get('units'),['variant_id','unit_id'],UNIT_CHECKS,expected_rows)
    check(review.get('seams'),['variant_id','left','right'],SEAM_CHECKS,expected_seams)
    units = {u['unit_id']: u for v in expected['variants'] for u in v['units']}
    semantic_map = review.get('semantic_map')
    require(isinstance(semantic_map,list) and bool(semantic_map), 'assembly_semantic_map_missing')
    require(all(isinstance(item,dict) for item in semantic_map), 'assembly_semantic_map_schema_invalid')
    for item in semantic_map:
        unit = units.get(item.get('unit_id'))
        quote = item.get('quote')
        source_text = ''.join(w.get('text','') for w in unit.get('retained_words',[])) if unit else ''
        if unit:
            source_text += ''.join(str(row.get('text') or '') for row in unit.get('source_context',[]))
        require(unit is not None and isinstance(item.get('selling_point'),str) and item['selling_point'].strip()
                and isinstance(item.get('category'),str) and item['category'].strip()
                and isinstance(quote,str) and quote.strip() and quote in source_text,
                'assembly_semantic_map_source_quote_mismatch')
    return review
