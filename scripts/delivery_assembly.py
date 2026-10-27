"""Parameter-only sentence assembly and shared local ASR. No commodity constants."""
import copy
import json
from pathlib import Path
from project_registry import digest
from runtime_meta import _sha256
from delivery_runtime import require
from sentence_boundaries import bounded_edges, CONTRACT
from final_quality_gate import plan_errors


def checked_file(reference, workspace):
    path = Path(reference['path']).expanduser()
    path = path.resolve() if path.is_absolute() else (Path(workspace)/path).resolve()
    before = _sha256(path)
    require(before == reference['sha256'], 'delivery_input_digest_changed')
    result = json.loads(path.read_text())
    require(_sha256(path) == before, 'delivery_input_changed_during_read')
    return result


def inputs(paths, state, run, recipe):
    require(recipe['schema_version'] == 1 and recipe['run_id'] == run['run_id']
            and recipe['input_content_digest'] == run['input_content_digest'], 'delivery_recipe_run_mismatch')
    require(state['batch']['product_ids'] == [recipe['product_id']], 'delivery_requires_single_product')
    index = checked_file(recipe['index'], paths.workspace_root)
    titles = checked_file(recipe['titles'], paths.workspace_root)
    require(index['input_digest'] == run['input_content_digest'], 'delivery_index_input_mismatch')
    product = next(p for p in titles['products'] if p['product_index'] == recipe['product_id'])
    require(product['encoding_id'] == recipe['encoding_id'], 'delivery_encoding_mismatch')
    names = [Path(s['video_path']).name for s in index['sources']]
    require(len(set(names)) == len(names), 'ambiguous_source_basename')
    registered = {str(Path(p).resolve()) for p in state['video_paths']}
    require(all(str(Path(s['video_path']).resolve()) in registered for s in index['sources']), 'index_unregistered_media')
    # Validate the existing human decision instead of trusting recipe range numbers.
    from publish_confirmed_ranges import build_confirmed_ranges
    manifest = checked_file(recipe['range_manifest'], paths.workspace_root)
    selected = [p for p in manifest['products'] if p['product_index'] == recipe['product_id']]
    ranges = build_confirmed_ranges({'products': selected}, index, {'products': [product]}, [recipe['product_id']])
    review = recipe.get('pool_review')
    require(isinstance(review,dict) and review.get('reference') and review.get('sha256'), 'full_pool_editorial_record_required')
    editorial = checked_file(dict(path=review['reference'],sha256=review['sha256']),paths.workspace_root)
    require(editorial.get('coverage') and editorial.get('product',{}).get('product_index') == recipe['product_id']
            and editorial['product'].get('encoding_id') == recipe['encoding_id'], 'full_pool_editorial_identity_missing')
    return index, product, ranges['products'][0]


def validate_ranges(plan, ranges):
    for variant in plan['variants']:
        for c in variant['clips']:
            require(any(r['media_stem']+'.mp4' == c['source_file'] and
                        r['start'] <= c['source_start_frame']/c['fps'] < c['source_end_frame']/c['fps'] <= r['end']
                        for r in ranges['ranges']), 'delivery_clip_outside_confirmed_range')


def assemble(paths, state, run, recipe, index, product, ranges):
    from adaptive_vision import proven_clock
    from refine_plans_v2 import normalize_physical_clips
    from delivery_fps import load
    rates = load(paths,state,run,recipe)
    sources = {s['source_stem']: s for s in index['sources']}
    records = {}; clocks = {}; variants = []
    for v in recipe['variants']:
        clips = []
        for choice in v['clips']:
            reference = recipe['asr_records'][choice['asr']]
            tag = digest(reference)
            if tag not in records:
                record = checked_file(reference, paths.workspace_root)
                require(digest(record['body']) == record['body_digest'], 'asr_body_digest_mismatch')
                records[tag] = record['body']
            window = records[tag]['windows'][choice['window']]
            stem = Path(window['source_file']).stem; src = sources[stem]
            if stem not in clocks: clocks[stem] = proven_clock(paths, Path(src['video_path']))
            require(abs(clocks[stem]['fps']-recipe['timeline_fps']) < 1e-6, 'cross_fps_not_certified')
            fps = recipe['timeline_fps']
            words = window['words']; first, last = choice['first'], choice['last']
            lo, hi, proof = bounded_edges(words, first, last, fps)
            rows = [r for r in index['cues'] if r['source_stem'] == stem and r['end'] > words[first]['start'] and r['start'] < words[last]['end']]
            text = ''.join(w['text'] for w in words[first:last+1])
            clips.append(dict(unit_id=choice['unit_id'],source_file=Path(src['video_path']).name,
                source_start_frame=lo,source_end_frame=hi,audio_start_frame=lo,audio_end_frame=hi,
                fps=fps,duration_sec=(hi-lo)/fps,record_duration_frames=hi-lo,words=copy.deepcopy(words),
                transcript=text,sentence_edges=proof,audio_boundary_verified=False,
                source_subtitle_text=''.join(r['text'] for r in rows),
                previous_source_context=[r for r in index['cues'] if r['source_stem']==stem and lo/fps-4<r['end']<=lo/fps],
                asr_reference_sha256=reference['sha256'],audit=dict(transcript=text)))
        clips, merges = normalize_physical_clips(clips)
        variants.append(dict(variant_id=v['variant_id'],product_index=recipe['product_id'],focus=v['focus'],
                             clips=clips,duration_seconds=sum(c['duration_sec'] for c in clips),normalized_merges=merges))
    plan = dict(product=product,variants=variants,sentence_contract=CONTRACT,clock_evidence=clocks,
                alignment_errors=[],frame_rates=rates,timeline_fps=recipe['timeline_fps'],width=recipe['width'],height=recipe['height'],
                run_id=run['run_id'],confirmed_ranges_digest=digest(ranges),editorial_choices_digest=digest(recipe['variants']))
    validate_ranges(plan, ranges)
    # Audio is a subsequent local stage; do not mislabel an assembled plan PASS.
    plan['local_errors'] = plan_errors(plan)
    return plan


def prepare_asr(paths, state, index, jobs, model_name, ledger, *, run):
    from asr_identity import snap_interval, model_identity
    from registered_pipeline import current_asr_parameters, transcribe_window
    from compute_store import media_signature, identity, cached, containing_asr
    from review_compute_cache import LazyModel
    from windowed_asr_v2 import load_model, merge_windows
    sources = {s['source_stem']:s for s in index['sources']}
    weights = model_identity(model_name); parameters = current_asr_parameters()
    def load():
        with ledger.span('asr_model_load','compute'):
            return load_model(weights['resolved_path'])
    model = LazyModel(load)
    signatures = {}; windows = []
    snapped = []
    for j in jobs:
        src = sources[j['source_file']]
        require(0 <= j['start'] < j['end'] <= src['duration'], 'asr_window_outside_source')
        lo, hi = snap_interval(j['start'], j['end'], src['duration'])
        snapped.append(dict(source_file=j['source_file'], start=lo, end=hi))
    for job in merge_windows(snapped):
        name = job['source_file']; media = sources[name]['video_path']
        if name not in signatures:
            signatures[name] = media_signature(media)
            signature = signatures[name]
            row = next((r for r in run['inputs'] if str(Path(r['path']).resolve()) == signature['path']),None)
            require(row is not None and (row['bytes'],row['mtime_ns'],row['sha256']) ==
                    (signature['size'],signature['mtime_ns'],signature['sha256']), 'asr_source_changed_since_init')
        expected = identity(paths, 'asr', signatures[name], weights, parameters, [job['start'],job['end']])
        body = containing_asr(paths, expected)
        if body is None:
            def compute():
                with ledger.span('asr_transcription', 'compute'):
                    if weights.get('runtime_python'):
                        from asr_runtime import request
                        value = request(dict(operation='transcribe',model=model_name,media=media,start=job['start'],end=job['end']))
                        require(value['model'] == {k:v for k,v in weights.items() if k not in ('runtime_python','runtime_sha256')}, 'asr_model_changed')
                        words = value['words']
                    else: words = transcribe_window(Path(media), job['start'], job['end'], model)
                    return dict(job, words=words)
            body, hit = cached(paths, expected, compute)
        else: hit = True
        ledger.emit(event='CACHE_HIT' if hit else 'COMPUTED',name='asr_window',bounds=expected['bounds'])
        windows.append(body)
    require(model_identity(model_name) == weights, 'asr_model_changed_during_execution')
    body = dict(model=weights,parameters=parameters,windows=windows)
    return dict(body=body,body_digest=digest(body))
