"""Source-bound primary Codex assembly review, before audio/vision work.

This validates coverage and provenance, never infers semantic PASS from keywords.
The primary editor must actually read and judge the generated minimal task.
"""
from delivery_assembly import checked_file
from delivery_runtime import require
from project_registry import digest
from runtime_meta import _sha256

CONTRACT = 'source-bound-assembly-review-v1'
UNIT_CHECKS = {'opening_complete','ending_complete','no_internal_unfinished_clause','source_dependencies_resolved','removed_words_safe'}
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
        instructions='主Codex逐段审读最终保留的原词（不是只看字幕概括）：句首主语/对象是否完整，段内是否有未完动作、条件或列举，删首尾后是否仍独立。逐接缝核对指代、因果、转折、承诺与信息重复，并跨版本检查卖点重复。上下文不足时扩大最小原文窗口，未解决前不能PASS；不能按连接词、口误或半帧误差机械拒绝。记录每项判断的具体理由，声音风险另交本机。')
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
    return review
