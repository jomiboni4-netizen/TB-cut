"""Read project/timeline/source rates once; never change an existing rate."""
import json
import math
from fractions import Fraction
from pathlib import Path
import subprocess
from delivery_runtime import require
from project_registry import atomic_json
from resolve_trial import stable_id
from delivery_assembly import checked_file

CONTRACT = 'read-once-project-rates-v1'
FFPROBE = '/opt/homebrew/bin/ffprobe'


def rate(value):
    try: result=float(Fraction(str(value)))
    except (ValueError,ZeroDivisionError,TypeError): raise ValueError('invalid_frame_rate')
    require(math.isfinite(result) and result > 0, 'invalid_frame_rate')
    return result


def equal(a,b):
    require(abs(rate(a)-rate(b)) < 1e-6, 'frame_rate_mismatch_no_automatic_conversion')


def media_stat(path):
    s=Path(path).stat()
    return dict(size=s.st_size,mtime_ns=s.st_mtime_ns)


def capture(paths,state,run):
    from delivery_resolve import connected
    resolve,_,project=connected(paths,state)
    timelines={}
    for i in range(1,project.GetTimelineCount()+1):
        t=project.GetTimelineByIndex(i)
        timelines[stable_id(t)]=dict(name=t.GetName(),fps=rate(t.GetSetting('timelineFrameRate')))
    require(all(any(t['name']==state['resolve'][k] for t in timelines.values())
                for k in ('source_timeline','target_timeline')), 'configured_timelines_missing')
    binding=state.get('runtime',{}).get('test_timeline_binding')
    if binding:
        require(binding['project_id']==stable_id(project) and binding['timeline_id'] in timelines
                and timelines[binding['timeline_id']]['name']==binding['name'], 'fps_target_binding_changed')
    sources={}
    for row in run['inputs']:
        if row['path'] not in state['video_paths']: continue
        require(media_stat(row['path']) == dict(size=row['bytes'],mtime_ns=row['mtime_ns']), 'fps_source_changed')
        data=json.loads(subprocess.check_output([FFPROBE,'-v','error','-select_streams','v:0','-show_entries',
            'stream=r_frame_rate,avg_frame_rate','-of','json',row['path']],text=True))
        stream=data['streams'][0]; nominal=rate(stream['r_frame_rate']); average=rate(stream['avg_frame_rate'])
        # Container averages can include timestamp/duration rounding. They are
        # recorded, not mistaken for decoded CFR proof; assembly checks every PTS.
        sources[str(Path(row['path']).resolve())]=dict(fps=nominal,average_fps=average,metadata_rates_agree=abs(nominal-average)<1e-6,sha256=row['sha256'],stat=media_stat(row['path']))
    require(len(sources)==len(state['video_paths']), 'fps_source_inventory_incomplete')
    return dict(contract=CONTRACT,project_id=stable_id(project),resolve_version=resolve.GetVersionString(),
        project_fps=rate(project.GetSetting('timelineFrameRate')),timelines=timelines,sources=sources,
        input_content_digest=run['input_content_digest'],cfr_certification='metadata_only_full_clock_required_at_assembly')


def load(paths,state,run,recipe):
    require(isinstance(recipe.get('frame_rates'),dict), 'PROJECT_FRAME_RATES_REQUIRED_RUN_PROJECT_READ_FIRST')
    body=checked_file(recipe['frame_rates'],paths.workspace_root)
    require(body['contract']==CONTRACT and body['project_id']==state['project_identity']['resolve_project_id']
            and body['input_content_digest']==run['input_content_digest'], 'frame_rate_registration_stale')
    equal(body['project_fps'],recipe['timeline_fps'])
    request=recipe.get('delivery_request',{})
    if request.get('timeline_mode')=='reusable_test_timeline':
        binding=state['runtime']['test_timeline_binding']; t=body['timelines'].get(binding['timeline_id'],{})
        require(binding['project_id']==body['project_id'] and t.get('name')==binding['name']==request['timeline_name'], 'fps_target_binding_changed')
        equal(t['fps'],recipe['timeline_fps'])
    registered={str(Path(p).resolve()) for p in state['video_paths']}
    require(set(body['sources'])==registered, 'fps_source_inventory_changed')
    manifest={str(Path(r['path']).resolve()):r for r in run['inputs']}
    for name,row in body['sources'].items():
        require(name in manifest and row['sha256']==manifest[name]['sha256'], 'fps_source_manifest_changed')
        require(media_stat(name)==row['stat'], 'fps_source_changed')
    return body


def verify_live(body,resolve,project):
    require(body['project_id']==stable_id(project) and body['resolve_version']==resolve.GetVersionString(), 'frame_rate_project_changed')
    equal(body['project_fps'],project.GetSetting('timelineFrameRate'))
    current={stable_id(project.GetTimelineByIndex(i)):project.GetTimelineByIndex(i) for i in range(1,project.GetTimelineCount()+1)}
    for key,row in body['timelines'].items():
        require(key in current and current[key].GetName()==row['name'], 'frame_rate_timeline_changed')
        equal(row['fps'],current[key].GetSetting('timelineFrameRate'))
