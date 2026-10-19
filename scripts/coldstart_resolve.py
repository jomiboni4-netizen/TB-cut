"""Cold-run native Resolve writer. Protected timelines are never write targets."""
import argparse
import json
import os
import sys
import time
import zipfile
from pathlib import Path

from coldstart_semantics import load_run
from adaptive_vision import verify as verify_person_audit
from project_registry import atomic_json, digest
from runtime_meta import _sha256
from resolve_connection import connect
from resolve_trial import project_lock, stable_id


def snapshot(timeline):
    rows=[]
    for kind in ('video','audio','subtitle'):
        for track in range(1,timeline.GetTrackCount(kind)+1):
            for item in timeline.GetItemListInTrack(kind,track) or []:
                media=item.GetMediaPoolItem()
                row={'kind':kind,'track':track,'record_start':item.GetStart(),'record_end':item.GetEnd(),
                     'duration':item.GetDuration(),'properties':item.GetProperty()}
                if media:
                    row.update(media_id=stable_id(media),media_path=media.GetClipProperty('File Path'),
                               source_start=item.GetSourceStartFrame(),source_end_raw=item.GetSourceEndFrame())
                rows.append(row)
    return {'id':stable_id(timeline),'name':timeline.GetName(),'start':timeline.GetStartFrame(),'end':timeline.GetEndFrame(),
            'settings':timeline.GetSetting(),'rows':rows,'markers':{str(k):v for k,v in (timeline.GetMarkers() or {}).items()}}


def assert_new_name(name, existing):
    if name in existing:raise ValueError('timeline_name_exists_no_overwrite')


def variant_timeline_name(run_id, variant, *, preview=False):
    product_id = variant.get('product_index')
    version = variant.get('version') or variant.get('variant_id')
    if not isinstance(product_id, int) or product_id <= 0 or not isinstance(version, str) or not version:
        raise ValueError('variant_product_identity_missing_for_timeline_name')
    kind = '临时试听_人物门禁待验_声学待验' if preview else '审核_人工听感待验'
    return f"TBcut_{run_id}_{product_id}_{version}_{kind}"


def infer_append_endpoint(start, api_end, actual_duration):
    if actual_duration==api_end-start:return 'exclusive'
    if actual_duration==api_end-start+1:return 'inclusive'
    raise ValueError('native_endpoint_protocol_unproven')


def append_endpoint(end_exclusive, protocol):
    if protocol=='exclusive':return end_exclusive
    if protocol=='inclusive':return end_exclusive-1
    raise ValueError('native_endpoint_protocol_unproven')


def validate_physical_overlap(variants):
    used=[]
    for variant in variants:
        for clip in variant['clips']:
            lo,hi=clip['source_start_frame'],clip['source_end_frame']
            if not isinstance(lo,int) or not isinstance(hi,int) or not 0<=lo<hi:
                raise ValueError('invalid_physical_frame_range')
            if any(old['source_file']==clip['source_file'] and min(hi,old['source_end_frame'])>max(lo,old['source_start_frame']) for old in used):
                raise ValueError('physical_source_frame_overlap')
            used.append(clip)


def check_readback(actual, clips, source_paths, start_frame):
    expected=[];record=start_frame
    for clip in clips:
        end=record+clip['record_duration_frames']
        expected.append((str(Path(source_paths[clip['source_file']]).resolve()),clip['source_start_frame'],
                         clip['source_end_frame'],record,end));record=end
    end_modes={};verified=[]
    for kind in ('video','audio'):
        rows=sorted([r for r in actual['rows'] if r['kind']==kind],key=lambda r:r['record_start'])
        if len(rows)!=len(expected):raise ValueError('readback_clip_count_mismatch_'+kind)
        modes=[]
        for row,(media,start,end,left,right) in zip(rows,expected):
            if str(Path(row['media_path']).resolve())!=media:raise ValueError('readback_not_original_media')
            if (row['record_start'],row['record_end'],row['duration'])!=(left,right,right-left):raise ValueError('readback_record_gap_or_wrong_duration')
            if row['source_start']!=start:raise ValueError('readback_source_start_mismatch')
            if row['source_end_raw']==end:modes.append('exclusive')
            elif row['source_end_raw']==end-1:modes.append('inclusive')
            else:raise ValueError('readback_source_end_mismatch')
            verified.append({'kind':kind,'source_start':start,'source_end_exclusive':end,'record_start':left,'record_end_exclusive':right})
        if len(set(modes))!=1:raise ValueError('inconsistent_native_source_end_protocol')
        end_modes[kind]=modes[0]
    if actual['end']!=record or actual['start']!=start_frame:raise ValueError('timeline_total_frames_mismatch')
    return {'source_end_protocol':end_modes,'verified_rows':verified,'total_frames':record-start_frame,'status':'PASS'}


def media_items(folder):
    result={}
    for item in folder.GetClipList() or []:
        path=item.GetClipProperty('File Path')
        if path:result[str(Path(path).resolve())]=item
    for sub in folder.GetSubFolderList() or []:result.update(media_items(sub))
    return result


def verify_source_fingerprints(run, state, clips):
    recorded={str(Path(row['path']).resolve()):row for row in run['inputs']}
    paths=[str(Path(path).resolve()) for path in state['video_paths']]
    if len({Path(path).name for path in paths})!=len(paths):
        raise ValueError('ambiguous_source_basename')
    selected={clip['source_file'] for clip in clips}
    if not selected.issubset({Path(path).name for path in paths}):
        raise ValueError('source_not_in_registered_inputs')
    for path in paths:
        if Path(path).name not in selected:continue
        row=recorded.get(path)
        if not row:raise ValueError('source_not_in_run_fingerprint')
        before=Path(path).stat()
        if before.st_size!=row['bytes'] or before.st_mtime_ns!=row['mtime_ns'] or _sha256(Path(path))!=row['sha256']:
            raise ValueError('source_changed_since_run_init')
        after=Path(path).stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
            raise ValueError('source_changed_while_hashing')


def verify_visual_provenance(paths, plan, visual):
    audit_file=paths.cache_dir/'v5_person_audit.json'
    if visual.get('audit_sha256')!=_sha256(audit_file):
        raise ValueError('person_audit_digest_mismatch')
    audit=json.loads(audit_file.read_text())
    verify_person_audit(paths,plan,audit,require_pass=False)
    if visual.get('contract')!=audit['contract'] or visual.get('quality_threshold_exclusive')!=.70 or visual.get('strict_framewise_certified') is not False:
        raise ValueError('person_gate_policy_mismatch')
    rows={row['variant_id']:row for row in audit['variants']}
    assessed=visual.get('variant_results',[])
    if len(assessed)!=len(rows) or {r['variant_id'] for r in assessed}!=set(rows):
        raise ValueError('person_gate_variant_coverage_mismatch')
    for row in assessed:
        sealed=rows[row['variant_id']]
        if (row.get('status')!=sealed['status'] or
                row.get('visible_ratio_lower_bound')!=sealed['host_visible_ratio_lower_bound'] or
                row.get('visible_ratio_estimate')!=sealed['host_visible_ratio_estimate'] or
                row.get('sample_count')!=len(sealed['samples']) or
                row.get('evidence_level')!=sealed['evidence_level']):
            raise ValueError('person_gate_not_derived_from_sealed_audit')


def main():
    # Resolve's native bridge can switch the process locale to C/ASCII.
    if not sys.flags.utf8_mode:
        os.execve(sys.executable,[sys.executable,*sys.argv],dict(os.environ,PYTHONUTF8='1'))
    parser=argparse.ArgumentParser();parser.add_argument('operation',choices=['inspect','write']);parser.add_argument('--workspace',required=True);parser.add_argument('--root',default='.')
    parser.add_argument('--allow-user-requested-preview',action='store_true',help='Use only when the current user explicitly requested an UNCERTAIN temporary preview')
    args=parser.parse_args();paths,state,run=load_run(args.root,args.workspace)
    resolve=connect(status_path=paths.workspace_root/'resolve_connection_status.json')
    if not resolve:raise ValueError('resolve_not_connected')
    pm=resolve.GetProjectManager();project=pm.GetCurrentProject()
    if stable_id(project)!=state['project_identity']['resolve_project_id'] or project.GetName()!=state['resolve']['project_name']:
        raise ValueError('resolve_project_identity_mismatch')
    timelines=[project.GetTimelineByIndex(i) for i in range(1,project.GetTimelineCount()+1)]
    pool=project.GetMediaPool();sources={Path(v).name:v for v in state['video_paths']};items=media_items(pool.GetRootFolder())
    if len(sources)!=len(state['video_paths']):raise ValueError('ambiguous_source_basename')
    if args.operation=='inspect':
        current={'project_id':stable_id(project),'project_name':project.GetName(),'project_settings':project.GetSetting(),
                 'protected':{stable_id(t):snapshot(t) for t in timelines},
                 'source_media_present':{name:str(Path(path).resolve()) in items for name,path in sources.items()}}
        atomic_json(paths.workspace_root/'RESOLVE_PREFLIGHT.json',current)
        print(json.dumps({'project':project.GetName(),'project_settings':{k:project.GetSetting(k) for k in ('timelineFrameRate','timelineResolutionWidth','timelineResolutionHeight')},
                          'timelines':[{'name':t.GetName(),'fps':t.GetSetting('timelineFrameRate'),'width':t.GetSetting('timelineResolutionWidth'),'height':t.GetSetting('timelineResolutionHeight')} for t in timelines]},ensure_ascii=False),flush=True)
        os._exit(0)
    pre=json.loads((paths.workspace_root/'RESOLVE_PREFLIGHT.json').read_text())
    plan=json.loads((paths.cache_dir/'physical_plan.json').read_text())
    review=json.loads((paths.cache_dir/'physical_semantic_review.json').read_text())
    if review['plan_sha256']!=_sha256(paths.cache_dir/'physical_plan.json') or review['status']!='SEMANTIC_PASS':raise ValueError('exact_physical_plan_semantics_not_approved')
    visual=json.loads((paths.cache_dir/'visual_gate.json').read_text())
    if visual['plan_sha256']!=_sha256(paths.cache_dir/'physical_plan.json') or visual['status'] not in ('PASS','PARTIAL_PASS','PARTIAL_PASS_WITH_USER_REQUESTED_PREVIEW'):
        raise ValueError('person_gate_not_passed')
    if plan['run_id']!=run['run_id'] or len(plan['variants'])>3 or not plan['variants']:raise ValueError('invalid_run_plan_scope')
    verify_visual_provenance(paths,plan,visual)
    validate_physical_overlap(plan['variants'])
    eligible=set(visual.get('eligible_variant_ids') or [v['variant_id'] for v in plan['variants']])
    preview=set(visual.get('user_requested_preview_variant_ids') or [])
    if preview and (not args.allow_user_requested_preview or not visual.get('preview_authorization')):
        raise ValueError('temporary_preview_requires_explicit_current_invocation_authorization')
    assessments={v['variant_id']:v for v in visual.get('variant_results',[])}
    if (not eligible or not eligible.issubset({v['variant_id'] for v in plan['variants']})
            or not preview.issubset(eligible)
            or (preview and visual['status']!='PARTIAL_PASS_WITH_USER_REQUESTED_PREVIEW')
            or any(not ((assessments.get(uid,{}).get('status')=='PASS' and
                         assessments[uid].get('visible_ratio_lower_bound',0)>.70) or
                        (uid in preview and assessments.get(uid,{}).get('status')=='UNCERTAIN'))
                   for uid in eligible)):
        raise ValueError('person_gate_selected_variant_not_passed')
    folder=paths.workspace_root/'resolve_transactions';folder.mkdir(exist_ok=True)
    result=[]
    with project_lock(paths,stable_id(project)):
        timelines=[project.GetTimelineByIndex(i) for i in range(1,project.GetTimelineCount()+1)]
        before={stable_id(t):snapshot(t) for t in timelines}
        if before!=pre['protected']:
            raise ValueError('protected_timeline_changed_since_preflight')
        names={t.GetName() for t in timelines}
        verify_source_fingerprints(run,state,[c for v in plan['variants'] if v['variant_id'] in eligible for c in v['clips']])
        backup=paths.workspace_root/'backups'/f"{run['run_id']}.drp";backup.parent.mkdir(exist_ok=True)
        if not backup.exists():
            if not pm.ExportProject(project.GetName(),str(backup)):raise ValueError('project_backup_failed')
        if not zipfile.is_zipfile(backup):raise ValueError('project_backup_unverified')
        with zipfile.ZipFile(backup) as archive:
            if not archive.namelist() or archive.testzip() is not None:raise ValueError('project_backup_corrupt')
        for variant in plan['variants']:
            if variant['variant_id'] not in eligible:continue
            if variant.get('product_index') not in state['batch']['product_ids']:
                raise ValueError('variant_product_outside_run_batch')
            name=variant_timeline_name(run['run_id'],variant,preview=variant['variant_id'] in preview)
            record_file=folder/(variant['variant_id']+'.json')
            if record_file.exists():
                prior=json.loads(record_file.read_text())
                if prior.get('status')!='VERIFIED' or prior.get('plan_digest')!=digest(variant):raise ValueError('partial_native_write_no_replay')
                target=next((t for t in timelines if stable_id(t)==prior['timeline_id']),None)
                if not target or snapshot(target)!=prior['actual']:raise ValueError('existing_run_timeline_changed')
                result.append(prior);continue
            assert_new_name(name,{project.GetTimelineByIndex(i).GetName() for i in range(1,project.GetTimelineCount()+1)})
            fps=plan['timeline_fps'];clips=variant['clips'];total=sum(c['record_duration_frames'] for c in clips)
            if not 45<total/fps<60:raise ValueError('actual_frame_plan_duration_outside_gate')
            if any(abs(c['fps']-fps)>1e-6 for c in clips):raise ValueError('native_cross_fps_mapping_not_certified')
            protocol_file=paths.workspace_root/'NATIVE_ENDPOINT_PROTOCOL.json'
            if not protocol_file.is_file():raise ValueError('native_endpoint_protocol_requires_certified_run_evidence')
            protocol=json.loads(protocol_file.read_text())
            if protocol['resolve_version']!=resolve.GetVersionString() or protocol['fps']!=fps:
                raise ValueError('native_endpoint_protocol_context_mismatch')
            if any(str(Path(sources[c['source_file']]).resolve()) not in items for c in clips):
                raise ValueError('original_media_not_in_pool')
            # Finish expensive I/O before activating a target for native append.
            verify_source_fingerprints(run,state,clips)
            atomic_json(record_file,{'status':'STARTED','name':name,'plan_digest':digest(variant)})
            started=time.perf_counter();target=pool.CreateEmptyTimeline(name)
            if not target or stable_id(target) in before:raise ValueError('new_target_creation_failed')
            names.add(name)
            if not target.SetSetting('useCustomSettings','1'):raise ValueError('custom_settings_failed')
            for setting,value in [('timelineFrameRate',format(fps,'g')),('timelineResolutionWidth',str(plan['width'])),('timelineResolutionHeight',str(plan['height']))]:
                if not target.SetSetting(setting,value):raise ValueError('new_target_setting_failed_'+setting)
            if not project.SetCurrentTimeline(target):raise ValueError('new_target_activation_failed')
            if target.GetTrackCount('video')<1:target.AddTrack('video')
            if target.GetTrackCount('audio')<1:target.AddTrack('audio','stereo')
            position=target.GetStartFrame();append=[]
            for clip in clips:
                path=str(Path(sources[clip['source_file']]).resolve());media=items.get(path)
                if not media:raise ValueError('original_media_not_in_pool')
                append.append({'mediaPoolItem':media,'startFrame':clip['source_start_frame'],'endFrame':append_endpoint(clip['source_end_frame'],protocol['mode']),'recordFrame':position,'trackIndex':1})
                position+=clip['record_duration_frames']
            if {stable_id(t):snapshot(t) for t in timelines}!=before:
                raise ValueError('protected_timelines_changed_before_append')
            if not project.SetCurrentTimeline(target) or stable_id(project.GetCurrentTimeline())!=stable_id(target):
                raise ValueError('native_append_target_changed')
            created=pool.AppendToTimeline(append)
            if not created:raise ValueError('native_append_failed')
            actual=snapshot(target)
            atomic_json(record_file,{'status':'APPENDED_PENDING_VERIFY','name':name,'timeline_id':stable_id(target),'plan_digest':digest(variant),'actual':actual})
            checked=check_readback(actual,clips,sources,target.GetStartFrame())
            if float(target.GetSetting('timelineFrameRate'))!=fps or int(target.GetSetting('timelineResolutionWidth'))!=plan['width'] or int(target.GetSetting('timelineResolutionHeight'))!=plan['height']:
                raise ValueError('readback_settings_mismatch')
            if {stable_id(t):snapshot(t) for t in timelines}!=before:raise ValueError('protected_timelines_changed')
            record={'status':'VERIFIED','timeline_id':stable_id(target),'name':name,'plan_digest':digest(variant),'actual':actual,
                    'readback':checked,'duration_seconds':checked['total_frames']/fps,'write_seconds':time.perf_counter()-started,
                    'backup_sha256':_sha256(backup),'user_full_review_status':'PENDING',
                    'quality_status':'PERSON_GATE_UNCERTAIN_USER_REQUESTED_PREVIEW' if variant['variant_id'] in preview else 'AUDIO_QA_PENDING'}
            atomic_json(record_file,record);result.append(record)
    if not pm.SaveProject():raise ValueError('save_project_failed')
    atomic_json(paths.workspace_root/'RESOLVE_RESULT.json',{'run_id':run['run_id'],'variants':result,'protected_unchanged':True})
    print(json.dumps([{'name':r['name'],'duration':r['duration_seconds'],'readback':r['readback']['status']} for r in result],ensure_ascii=False),flush=True)
    os._exit(0)


if __name__=='__main__':main()
