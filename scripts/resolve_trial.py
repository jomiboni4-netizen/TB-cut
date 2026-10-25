"""Trial-only Resolve transaction. Exact readback; partial writes block, never replay clips."""
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import zipfile
from project_registry import atomic_json, checked_project, digest
from runtime_meta import CacheIdentityError, _sha256
from trial_session import validate_trial
from final_quality_gate import approved_product
from compute_metrics import span
from codex_worker import key


def stable_id(obj):
    value = obj.GetUniqueId()
    if not isinstance(value, str) or not value.strip():
        raise CacheIdentityError('resolve_stable_identity_missing')
    return value


def timeline_by_name(project, name):
    rows = [project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount()+1)]
    found = [t for t in rows if t.GetName() == name]
    if len(found) != 1:
        raise CacheIdentityError('resolve_timeline_name_missing_or_ambiguous')
    return found[0]


def snapshot(timeline, end_mode='exclusive'):
    rows = []
    for kind in ('video', 'audio'):
        for track in range(1, timeline.GetTrackCount(kind)+1):
            for item in timeline.GetItemListInTrack(kind, track) or []:
                media = item.GetMediaPoolItem()
                if media is None:
                    raise CacheIdentityError('resolve_item_source_unproven')
                start, end = item.GetSourceStartFrame(), item.GetSourceEndFrame()
                rows.append({'kind': kind, 'track': track, 'media_id': stable_id(media),
                             'source_start': start, 'source_end': end+(end_mode == 'inclusive'),
                             'record_start': item.GetStart(), 'record_end': item.GetEnd()})
    return {'timeline_id': stable_id(timeline), 'start_frame': timeline.GetStartFrame(),
            'fps': float(timeline.GetSetting('timelineFrameRate')), 'rows': rows,
            'markers': {str(k): v for k,v in (timeline.GetMarkers() or {}).items()}}


def protected_snapshot(timeline):
    if timeline.GetTrackCount('subtitle'):
        raise CacheIdentityError('protected_subtitle_snapshot_not_integrated')
    body = snapshot(timeline)
    body['settings'] = timeline.GetSetting()
    body['properties'] = [item.GetProperty() for kind in ('video','audio')
                          for track in range(1,timeline.GetTrackCount(kind)+1)
                          for item in timeline.GetItemListInTrack(kind,track) or []]
    digest(body)
    return body


@contextmanager
def project_lock(paths, project_id):
    folder = paths.repo_root/'.cache'/'resolve_trial_locks'
    folder.mkdir(parents=True, exist_ok=True)
    fd = os.open(folder/(digest(project_id)+'.lock'), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc: raise CacheIdentityError('resolve_trial_project_busy') from exc
        yield
    finally: os.close(fd)


def project_preflight(paths):
    if not validate_trial(paths):
        raise CacheIdentityError('resolve_write_requires_bounded_trial')
    from project_scheduler import project_audit
    audit = project_audit(paths)
    if not audit['final_review_complete']:
        raise CacheIdentityError('trial_final_quality_or_project_review_not_passed')
    # Full dependency proof remains mandatory, not a Boolean supplied by the caller.
    import subprocess
    from workbook_runtime import preferred_node
    child = subprocess.run([preferred_node(), str(paths.repo_root/'scripts/check_workbook_dependencies.mjs')],
                           capture_output=True, text=True, timeout=65)
    if child.returncode:
        raise CacheIdentityError('trial_workbook_dependency_blocked')


def current_project(paths, resolve):
    config = checked_project(paths, paths.load_state())
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject()
    from resolve_session_identity import confirm_project
    if not project:
        raise CacheIdentityError('resolve_project_identity_mismatch')
    confirmed, _ = confirm_project(paths, project)
    if confirmed != config['resolve_project_id']:
        raise CacheIdentityError('resolve_project_identity_mismatch')
    return manager, project, config


def native_write_preflight(paths, resolve):
    # Real read-only inventory shows mixed source-end/span behavior. A fake bridge's
    # regression cannot certify the installed native bridge or an audio frame domain.
    raise CacheIdentityError('native_resolve_roundtrip_and_audio_frame_protocol_not_certified')


def verify_backup(paths, binding):
    file = paths.workspace_root/binding['backup']['path']
    if not file.resolve().is_relative_to(paths.workspace_root.resolve()):
        raise CacheIdentityError('resolve_backup_outside_trial_workspace')
    if (not file.is_file() or file.is_symlink() or _sha256(file) != binding['backup']['sha256']
            or not zipfile.is_zipfile(file)):
        raise CacheIdentityError('resolve_trial_backup_unverified')
    with zipfile.ZipFile(file) as archive:
        if not archive.namelist() or archive.testzip() is not None:
            raise CacheIdentityError('resolve_trial_backup_corrupt')


def prepare_target(paths, resolve):
    """Called only after all real quality gates. No automatic mutation during diagnostics."""
    project_preflight(paths)
    native_write_preflight(paths, resolve)
    manager, project, config = current_project(paths, resolve)
    trial = validate_trial(paths)
    output = paths.workspace_root/'RESOLVE_TRIAL_BINDING.json'
    if output.exists():
        body = json.loads(output.read_text())
        verify_binding(paths, resolve, body)
        return body
    with project_lock(paths, stable_id(project)):
        source = timeline_by_name(project, config['inputs']['resolve']['source_timeline'])
        formal = timeline_by_name(project, config['inputs']['resolve']['target_timeline'])
        if stable_id(source) == stable_id(formal):
            raise CacheIdentityError('resolve_source_equals_formal_target')
        protected = {stable_id(t): protected_snapshot(t) for t in (source, formal)}
        backup = paths.workspace_root/'backups'/(trial['run_id']+'.drp')
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists() or not manager.ExportProject(project.GetName(), str(backup)):
            raise CacheIdentityError('resolve_trial_backup_failed_or_existing')
        body = {'contract': 'resolve-trial-v4', 'run_id': trial['run_id'], 'project_id': stable_id(project),
                'protected': protected, 'backup': {'path': str(backup.relative_to(paths.workspace_root)), 'sha256': _sha256(backup)}}
        verify_backup(paths, body)
        if {stable_id(t): protected_snapshot(t) for t in (source, formal)} != protected:
            raise CacheIdentityError('formal_timeline_changed_during_backup')
        # A dedicated empty timeline is the minimum safe fallback; project-copy/restore is not automatic.
        target = project.GetMediaPool().CreateEmptyTimeline('TB-cut-test-'+trial['run_id'])
        if not target or stable_id(target) in protected or snapshot(target)['rows']:
            raise CacheIdentityError('resolve_test_timeline_creation_unverified')
        body.update(target_id=stable_id(target), target_name=target.GetName(), target_initial=snapshot(target))
        body['seal'] = hmac.new(key(paths), digest(body).encode(), hashlib.sha256).hexdigest()
        atomic_json(output, body)
        verify_binding(paths, resolve, body)
        return body


def verify_binding(paths, resolve, binding):
    _, project, config = current_project(paths, resolve)
    trial = validate_trial(paths)
    unsigned = {k:v for k,v in binding.items() if k != 'seal'}
    secret = paths.project_records_dir/'worker_seal.key'
    if not secret.is_file() or not isinstance(binding.get('seal'),str) or not hmac.compare_digest(
            binding['seal'], hmac.new(secret.read_bytes(),digest(unsigned).encode(),hashlib.sha256).hexdigest()):
        raise CacheIdentityError('resolve_trial_binding_seal_invalid')
    required_protected = {stable_id(timeline_by_name(project,config['inputs']['resolve'][k]))
                          for k in ('source_timeline','target_timeline')}
    if (binding.get('contract') != 'resolve-trial-v4' or binding.get('project_id') != stable_id(project)
            or binding.get('run_id') != trial['run_id'] or set(binding.get('protected',{})) != required_protected
            or binding.get('target_id') in required_protected
            or binding.get('target_name') != 'TB-cut-test-'+trial['run_id']):
        raise CacheIdentityError('resolve_trial_binding_invalid')
    verify_backup(paths, binding)
    by_id = {stable_id(project.GetTimelineByIndex(i)): project.GetTimelineByIndex(i) for i in range(1,project.GetTimelineCount()+1)}
    if any(uid not in by_id or protected_snapshot(by_id[uid]) != old for uid,old in binding['protected'].items()):
        raise CacheIdentityError('protected_formal_timeline_changed')
    target = by_id.get(binding['target_id'])
    if not target or target.GetName() != binding['target_name']:
        raise CacheIdentityError('resolve_test_timeline_identity_changed')
    return project, target


def write_product(paths, resolve, binding, product_id):
    trial = validate_trial(paths)
    if not trial or product_id not in trial['products']:
        raise CacheIdentityError('product_outside_trial_scope')
    project_preflight(paths)
    native_write_preflight(paths, resolve)
    # This reader independently verifies Codex, host, plan, ASR and current dependencies.
    product = approved_product(paths, product_id)
    from product_checkpoints import resume
    state = resume(paths, [product_id])[product_id]
    operation = digest([trial['run_id'], product_id, state['plan_ref'], state['evidence_ref'], binding['target_id']])
    folder = paths.workspace_root/'resolve_transactions'
    journal_path = folder/(str(product_id)+'.json')
    with project_lock(paths, binding['project_id']), span('resolve_write_readback'):
        project, target = verify_binding(paths, resolve, binding)
        before = snapshot(target)
        if journal_path.exists():
            journal = json.loads(journal_path.read_text())
            if journal['operation'] != operation or journal.get('state') == 'BLOCKED':
                raise CacheIdentityError('resolve_changed_plan_or_partial_write_requires_repair')
            if before == journal.get('verified_snapshot'):
                return {'status': 'WRITTEN_VERIFIED', 'cache_hit': True, 'operation': operation}
            if before['rows'] != journal['expected_rows']:
                raise CacheIdentityError('resolve_interrupted_write_not_exact_never_replay')
            # Exact clip set is present: finish only the marker, never replay AppendToTimeline.
        else:
            source = timeline_by_name(project, validate_trial(paths)['protected_resolve']['source_timeline'])
            media = {}
            for kind in ('video', 'audio'):
                for track in range(1,source.GetTrackCount(kind)+1):
                    for item in source.GetItemListInTrack(kind, track) or []:
                        pool = item.GetMediaPoolItem()
                        path = pool.GetClipProperty('File Path')
                        if not path: raise CacheIdentityError('resolve_media_file_identity_missing')
                        name = Path(path).name
                        if name in media and stable_id(media[name]) != stable_id(pool):
                            raise CacheIdentityError('resolve_same_named_media_ambiguous')
                        media[name] = pool
            from product_checkpoints import load_stage
            review = load_stage(paths,state['evidence_ref'])['body']
            visual = load_stage(paths,review['vision_ref'])['body']
            cursor = max([before['start_frame'], *[r['record_end'] for r in before['rows']]])
            expected, commands = list(before['rows']), []
            for variant in product['plans']:
                for c in variant['clips']:
                    pool = media.get(c['source_file'])
                    if not pool or str(Path(pool.GetClipProperty('File Path')).resolve()) != visual['media'][c['source_file']]['path']:
                        raise CacheIdentityError('resolve_media_pool_source_mismatch')
                    if abs(c['fps']-before['fps']) > 1e-6:
                        raise CacheIdentityError('resolve_mixed_frame_rate_mapping_not_verified')
                    lo, hi = c['source_start_frame'], c['source_end_frame']
                    for kind, media_type in (('video',1),('audio',2)):
                        commands.append({'mediaPoolItem': pool, 'startFrame': lo, 'endFrame': hi-1,
                                         'mediaType': media_type, 'trackIndex': 1, 'recordFrame': cursor})
                        expected.append({'kind':kind,'track':1,'media_id':stable_id(pool),'source_start':lo,'source_end':hi,
                                         'record_start':cursor,'record_end':cursor+hi-lo})
                    cursor += hi-lo
            # Snapshot iteration groups video/audio by track, so preserve that readback order.
            expected.sort(key=lambda r:(r['kind'] != 'video',r['track'],r['record_start']))
            journal = {'contract':'resolve-transaction-v4','operation':operation,'state':'PREPARED',
                       'before':before,'expected_rows':expected,'marker_frame':cursor-before['start_frame'],
                       'binding_digest':digest(binding)}
            atomic_json(journal_path,journal)
            old_current = project.GetCurrentTimeline()
            try:
                if not project.SetCurrentTimeline(target) or stable_id(project.GetCurrentTimeline()) != binding['target_id']:
                    raise CacheIdentityError('resolve_current_target_not_verified')
                # Current bridge readback convention has not been proven in a real write this round.
                # API success is ignored; every video/audio range must match independently.
                project.GetMediaPool().AppendToTimeline(commands)
            finally:
                if old_current is not None: project.SetCurrentTimeline(old_current)
            after = snapshot(target)
            if after['rows'] != expected or after['markers'] != before['markers']:
                journal['state']='BLOCKED'; atomic_json(journal_path,journal)
                raise CacheIdentityError('resolve_video_audio_source_or_record_readback_mismatch')
        project, target = verify_binding(paths, resolve, binding)
        marker = 'tbcut-trial:'+operation
        current = snapshot(target)
        if current['rows'] != journal['expected_rows']:
            raise CacheIdentityError('resolve_readback_changed_before_marker')
        existing = [v for v in current['markers'].values() if v.get('customData') == marker]
        if not existing and not target.AddMarker(journal['marker_frame'], 'Blue', 'TB-cut trial', '', 1, marker):
            raise CacheIdentityError('resolve_trial_marker_failed')
        verified = snapshot(target)
        if verified['rows'] != journal['expected_rows'] or len([v for v in verified['markers'].values() if v.get('customData')==marker]) != 1:
            raise CacheIdentityError('resolve_marker_or_clips_not_verified')
        verify_binding(paths,resolve,binding)
        journal.update(state='VERIFIED', verified_snapshot=verified)
        atomic_json(journal_path,journal)
        # Writer verification is not an export or legacy checkpoint promotion.
        return {'status':'WRITTEN_VERIFIED','cache_hit':False,'operation':operation}
