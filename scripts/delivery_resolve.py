"""New combined timeline transaction, strict recovery and fresh-process readback.

Existing timelines are always protected. No edits/deletes/re-appends during
recovery. This adapter does not unlock the legacy registered writer.
"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import zipfile
from coldstart_resolve import (snapshot, media_items, verify_source_fingerprints,
                              append_endpoint, infer_append_endpoint, check_readback,
                              validate_physical_overlap)
from delivery_assembly import checked_file
from delivery_runtime import require
from project_registry import atomic_json, digest
from runtime_meta import _sha256
from resolve_trial import project_lock, stable_id


def read(path):
    return json.loads(Path(path).read_text())


def connected(paths, state):
    from resolve_connection import connect
    resolve = connect(status_path=paths.workspace_root/'delivery'/'resolve_connection.json')
    require(resolve is not None, 'resolve_not_connected')
    manager = resolve.GetProjectManager(); project = manager.GetCurrentProject()
    require(project is not None and stable_id(project) == state['project_identity']['resolve_project_id']
            and project.GetName() == state['resolve']['project_name'], 'resolve_project_identity_mismatch')
    return resolve, manager, project


def inventory(project):
    return {stable_id(t):snapshot(t) for t in
            [project.GetTimelineByIndex(i) for i in range(1,project.GetTimelineCount()+1)]}


def reusable_target(state, request, current):
    binding = state.get('runtime',{}).get('test_timeline_binding',{})
    require(binding.get('project_id') == state['project_identity']['resolve_project_id']
            and binding.get('name') == request['timeline_name'] and binding.get('timeline_id') in current,
            'test_timeline_binding_missing_or_changed')
    target_id = binding['timeline_id']; target = current[target_id]
    require(target['name'] == binding['name'] and sum(t['name']==binding['name'] for t in current.values()) == 1,
            'test_timeline_identity_ambiguous')
    require(target['name'] not in {state['resolve']['source_timeline'],state['resolve']['target_timeline']},
            'test_timeline_cannot_be_protected_source_or_final')
    return target_id


def inspect(paths, state, request=None):
    resolve, _, project = connected(paths, state)
    body = dict(project_id=stable_id(project), project_name=project.GetName(), protected=inventory(project),
                resolve_version=resolve.GetVersionString())
    names = {v['name'] for v in body['protected'].values()}
    require({state['resolve']['source_timeline'],state['resolve']['target_timeline']}.issubset(names), 'configured_timelines_missing')
    if request and request['timeline_mode'] == 'reusable_test_timeline':
        target_id = reusable_target(state,request,body['protected'])
        body['reusable_target'] = body['protected'].pop(target_id)
    atomic_json(paths.workspace_root/'delivery'/'RESOLVE_PREFLIGHT.json', body)
    return body


def layout(plan, start, request):
    require(request['timeline_mode'] in ('one_new_combined_timeline','reusable_test_timeline'), 'unsupported_delivery_timeline_mode')
    require(isinstance(request['timeline_name'], str) and request['timeline_name'].strip(), 'new_timeline_name_required')
    require(type(request['gap_frames']) is int and request['gap_frames'] >= 0, 'invalid_group_gap')
    require(len(plan['variants']) == request['requested_variants'] and 1 <= request['requested_variants'] <= 3, 'delivery_variant_count_mismatch')
    position = start; groups = []
    validate_physical_overlap(plan['variants'])
    for v in plan['variants']:
        require(v['product_index'] == request['product_id'], 'delivery_product_mismatch')
        total = sum(c['record_duration_frames'] for c in v['clips'])
        require(45 < total/plan['timeline_fps'] < 60, 'delivery_duration_outside_gate')
        for c in v['clips']:
            require(c['fps'] == plan['timeline_fps'] and c['record_duration_frames'] == c['source_end_frame']-c['source_start_frame']
                    and (c['audio_start_frame'],c['audio_end_frame']) == (c['source_start_frame'],c['source_end_frame']), 'delivery_av_frame_plan_mismatch')
        groups.append(dict(variant_id=v['variant_id'], start=position,end=position+total,duration_seconds=total/plan['timeline_fps']))
        position += total+request['gap_frames']
    return groups


def precision_rows(target):
    return [dict(kind=kind, record_start=item.GetStart(), source_start_time=item.GetSourceStartTime(),
                 source_left_offset=item.GetLeftOffset(True),source_end_time=item.GetSourceEndTime())
            for kind in ('video','audio') for track in range(1,target.GetTrackCount(kind)+1)
            for item in target.GetItemListInTrack(kind,track) or []]


def verify_combined(actual, precision, plan, sources, request):
    groups = layout(plan, actual['start'], request)
    require(actual['name'] == request['timeline_name'] and actual['end'] == groups[-1]['end'], 'combined_timeline_identity_or_end')
    require(len(actual['rows']) == 2*sum(len(v['clips']) for v in plan['variants']), 'combined_clip_inventory')
    require(len(actual['markers']) == len(groups), 'combined_marker_count')
    for key, value in [('timelineFrameRate',plan['timeline_fps']),('timelineResolutionWidth',plan['width']),('timelineResolutionHeight',plan['height'])]:
        require(float(actual['settings'][key]) == value, 'combined_settings_changed')
    for v, g in zip(plan['variants'], groups):
        sub = copy.deepcopy(actual); sub['start'] = g['start']; sub['end'] = g['end']
        sub['rows'] = [row for row in sub['rows'] if g['start'] <= row['record_start'] < g['end']]
        for row in sub['rows']:
            require(row['track'] == 1, 'combined_unexpected_track')
            matches = [x for x in precision if x['kind'] == row['kind'] and x['record_start'] == row['record_start']]
            require(len(matches) == 1, 'source_precision_coverage_missing')
            row.update(matches[0])
        g['readback'] = check_readback(sub, v['clips'], sources, g['start'])
        markers = [m for frame,m in actual['markers'].items() if float(frame) == g['start']-actual['start']]
        require(len(markers) == 1, 'combined_marker_missing')
        m = markers[0]
        require(m['duration'] == g['end']-g['start'] and m['customData'] == digest(plan)+':'+v['variant_id']
                and m['name'] == f"商品{request['product_id']}_{v['variant_id']}_成品", 'combined_marker_mismatch')
    return groups


def protocol_mode(paths, request, resolve, plan):
    protocol = checked_file(request['native_protocol'], paths.workspace_root)
    evidence = checked_file(request['native_protocol_evidence'], paths.workspace_root)
    require(protocol['resolve_version'] == resolve.GetVersionString() and protocol['fps'] == plan['timeline_fps']
            and protocol['video_and_audio_same'] is True, 'native_protocol_environment_changed')
    rows = evidence['actual']['rows']
    require({r['kind'] for r in rows} == {'video','audio'} and len(rows) == 2
            and all(infer_append_endpoint(evidence['requested_api_start'],evidence['requested_api_end'],r['duration']) == protocol['mode']
                    for r in rows), 'native_protocol_unproven')
    return protocol['mode']


def validate_backup(path, expected=None):
    require(path.is_file() and zipfile.is_zipfile(path) and (expected is None or _sha256(path) == expected), 'backup_unverified')
    with zipfile.ZipFile(path) as z:
        require(bool(z.namelist()) and z.testzip() is None, 'backup_corrupt')
    return _sha256(path)


def perform(paths, state, run, plan, request, quality_gate, *, operation):
    """Write once; recover only by verification. Caller supplies current exact quality gate."""
    from project_task import guard
    if operation != "readback":
        guard(paths, "write")
        if operation == "recover":
            guard(paths, "recover")
    require(operation in ('write','recover','readback'), 'invalid_resolve_operation')
    folder = paths.workspace_root/'delivery'; journal = folder/'RESOLVE_TRANSACTION.json'
    # Quality and source identity are mandatory even when resuming.
    quality = quality_gate(); require(quality['plan_digest'] == digest(plan) and quality['status'] == 'PASS', 'quality_gate_not_current')
    clips = [c for v in plan['variants'] for c in v['clips']]
    verify_source_fingerprints(run, state, clips)
    sources = {Path(p).name:p for p in state['video_paths']}
    require(len(sources) == len(state['video_paths']), 'ambiguous_source_basename')
    pre = read(folder/'RESOLVE_PREFLIGHT.json')
    if operation == 'write':
        doctor = subprocess.run([sys.executable,str(paths.repo_root/'scripts/project_doctor.py'),'--root',str(paths.repo_root),'--workspace',str(paths.workspace_root)],capture_output=True,text=True)
        require(doctor.returncode == 0, 'project_doctor_failed')
        require(not journal.exists(), 'transaction_exists_use_readback_or_recover_no_reappend')
    else:
        require(journal.is_file(), 'transaction_missing')
    resolve, manager, project = connected(paths, state)
    require(pre['project_id'] == stable_id(project) and pre['project_name'] == project.GetName(), 'preflight_project_changed')
    require(operation != 'write' or 'frame_rates' in plan, 'project_frame_rates_required_before_write')
    if 'frame_rates' in plan:
        from delivery_fps import verify_live
        verify_live(plan['frame_rates'],resolve,project)
    with project_lock(paths, stable_id(project)):
        reuse = request['timeline_mode'] == 'reusable_test_timeline'
        if reuse:
            require(reusable_target(state,request,inventory(project)) == pre.get('reusable_target',{}).get('id'),
                    'test_timeline_preflight_binding_changed')
        if operation == 'write':
            before = inventory(project)
            expected_before = dict(pre['protected'])
            if reuse: expected_before[pre['reusable_target']['id']] = pre['reusable_target']
            require(before == expected_before, 'protected_changed_since_preflight')
            layout(plan, 0, request)
            if reuse:
                require(float(pre['reusable_target']['settings']['timelineFrameRate']) == plan['timeline_fps'], 'existing_target_fps_changed_no_reset')
            if not reuse: require(request['timeline_name'] not in {t['name'] for t in before.values()}, 'new_timeline_name_exists')
            mode = protocol_mode(paths, request, resolve, plan)
            pool = project.GetMediaPool(); media = media_items(pool.GetRootFolder())
            require(all(str(Path(sources[c['source_file']]).resolve()) in media for c in clips), 'source_media_not_in_pool')
            backup = folder/'backups'/(run['run_id']+'_pre_delivery.drp'); backup.parent.mkdir(parents=True,exist_ok=True)
            require(not backup.exists(), 'backup_exists_inspect_before_retry')
            require(manager.ExportProject(project.GetName(),str(backup)), 'backup_export_failed')
            base = dict(plan_digest=digest(plan),request_digest=digest(request),backup_path=paths.reference(backup),backup_sha256=validate_backup(backup))
            atomic_json(journal,dict(base,status='STARTED'))
            if reuse:
                target = next(project.GetTimelineByIndex(i) for i in range(1,project.GetTimelineCount()+1)
                              if stable_id(project.GetTimelineByIndex(i)) == pre['reusable_target']['id'])
                base['timeline_id'] = stable_id(target)
                atomic_json(journal,dict(base,status='CLEAR_IN_PROGRESS',previous_test_snapshot=pre['reusable_target']))
                old_items = [item for kind in ('video','audio','subtitle') for track in range(1,target.GetTrackCount(kind)+1)
                             for item in target.GetItemListInTrack(kind,track) or []]
                if old_items: require(target.DeleteClips(old_items,False), 'test_timeline_clear_failed_no_replay')
                if target.GetMarkers(): require(target.DeleteMarkersByColor('All'), 'test_marker_clear_failed')
                empty = snapshot(target)
                require(not empty['rows'] and not empty['markers'], 'test_timeline_not_empty_after_clear')
            else:
                target = pool.CreateEmptyTimeline(request['timeline_name'])
                require(target is not None and stable_id(target) not in before, 'new_target_creation_failed')
            base['timeline_id'] = stable_id(target)
            atomic_json(journal,dict(base,status='CREATED_PENDING_APPEND'))
            require(target.SetSetting('useCustomSettings','1'), 'custom_settings_failed')
            # Existing timelines keep their frame rate. New timelines inherit the project rate.
            require(float(target.GetSetting('timelineFrameRate')) == plan['timeline_fps'], 'target_fps_mismatch_no_reset')
            for k,v in [('timelineResolutionWidth',str(plan['width'])),('timelineResolutionHeight',str(plan['height']))]:
                require(target.SetSetting(k,v), 'target_setting_failed:'+k)
            require(project.SetCurrentTimeline(target), 'activate_failed')
            if target.GetTrackCount('video') < 1: require(target.AddTrack('video'), 'video_track_failed')
            if target.GetTrackCount('audio') < 1: require(target.AddTrack('audio','stereo'), 'audio_track_failed')
            appends = []; groups = layout(plan,target.GetStartFrame(),request)
            for v,g in zip(plan['variants'],groups):
                position = g['start']
                for c in v['clips']:
                    appends.append(dict(mediaPoolItem=media[str(Path(sources[c['source_file']]).resolve())],startFrame=c['source_start_frame'],endFrame=append_endpoint(c['source_end_frame'],mode),recordFrame=position,trackIndex=1))
                    position += c['record_duration_frames']
            require(all(snapshot(project.GetTimelineByIndex(i)) == pre['protected'][stable_id(project.GetTimelineByIndex(i))]
                        for i in range(1,project.GetTimelineCount()+1) if stable_id(project.GetTimelineByIndex(i)) in pre['protected']), 'protected_changed_before_append')
            require(stable_id(project.GetCurrentTimeline()) == base['timeline_id'], 'append_target_changed')
            # The journal forbids replay even if the native call partially succeeds.
            atomic_json(journal,dict(base,status='APPEND_IN_PROGRESS'))
            require(pool.AppendToTimeline(appends), 'append_failed_partial_state_preserved')
            atomic_json(journal,dict(base,status='APPENDED_PENDING_VERIFY'))
            for v,g in zip(plan['variants'],groups):
                require(target.AddMarker(g['start']-target.GetStartFrame(),'Green',f"商品{request['product_id']}_{v['variant_id']}_成品",'成品分组',g['end']-g['start'],digest(plan)+':'+v['variant_id']), 'marker_failed_no_auto_replay')
        else:
            base = read(journal)
            require(base['plan_digest'] == digest(plan) and base['request_digest'] == digest(request), 'resume_plan_or_request_changed')
            require(base['status'] in ('APPENDED_PENDING_VERIFY','VERIFIED','FINAL_VERIFIED'), 'partial_transaction_requires_manual_inspection_no_reappend')
            validate_backup(paths.resolve_reference(base['backup_path']).path, base['backup_sha256'])
            target = next((project.GetTimelineByIndex(i) for i in range(1,project.GetTimelineCount()+1)
                           if stable_id(project.GetTimelineByIndex(i)) == base['timeline_id']),None)
            require(target is not None, 'delivery_target_missing')
        actual = snapshot(target); precision = precision_rows(target)
        groups = verify_combined(actual,precision,plan,sources,request)
        current = inventory(project)
        require(set(current) == set(pre['protected'])|{base['timeline_id']}
                and all(current[k] == v for k,v in pre['protected'].items()), 'protected_timelines_changed')
        verify_source_fingerprints(run,state,clips)
        if operation == 'readback':
            require(base['status'] in ('VERIFIED','FINAL_VERIFIED') and actual == base['actual'], 'saved_target_changed')
            output = dict(status='PASS',plan_digest=digest(plan),actual_digest=digest(actual),protected_unchanged=True)
            atomic_json(folder/'INDEPENDENT_READBACK.json',output)
            return output
        require(manager.SaveProject(), 'save_project_failed')
        result = dict(base,status='VERIFIED',quality_status='PENDING_INDEPENDENT_READBACK',name=target.GetName(),actual=actual,
                      precision_readback=precision,variants=groups,protected_unchanged=True,quality_digest=digest(quality))
        atomic_json(journal,result)
        return result
