"""Stateful fake native bridge: write, failure, recovery, and independent readback."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_delivery_pipeline as fixtures
from delivery_resolve import inspect, perform, verify_combined, read
from coldstart_resolve import snapshot
from project_registry import atomic_json, digest
from runtime_meta import _sha256

class Media:
    def __init__(self,path):self.path=path
    def GetUniqueId(self):return 'media'
    def GetClipProperty(self,key):return str(self.path) if key=='File Path' else None

class Item:
    def __init__(self,media,lo,hi,position):self.media=media;self.lo=lo;self.hi=hi;self.position=position
    def GetStart(self):return self.position
    def GetEnd(self):return self.position+self.GetDuration()
    def GetDuration(self):return self.hi-self.lo
    def GetProperty(self):return {}
    def GetMediaPoolItem(self):return self.media
    def GetSourceStartFrame(self):return self.lo-1  # deliberately truncated integer API
    def GetSourceEndFrame(self):return self.hi
    def GetSourceStartTime(self):return self.lo/25.
    def GetSourceEndTime(self):return self.hi/25.
    def GetLeftOffset(self,precise=True):return float(self.lo)

class Timeline:
    def __init__(self,name,uid):
        self.name=name;self.uid=uid;self.items={'video':[],'audio':[]};self.markers={}
        self.settings=dict(timelineFrameRate='25',timelineResolutionWidth='750',timelineResolutionHeight='1000')
    def GetUniqueId(self):return self.uid
    def GetName(self):return self.name
    def GetStartFrame(self):return 90000
    def GetEndFrame(self):return max([i.GetEnd() for values in self.items.values() for i in values] or [90000])
    def GetTrackCount(self,kind):return int(kind in self.items)
    def GetItemListInTrack(self,kind,track):return self.items[kind]
    def GetSetting(self,key=None):return self.settings.get(key) if key else copy.deepcopy(self.settings)
    def SetSetting(self,key,value):
        self.setting_calls=getattr(self,'setting_calls',[])+[(key,value)]
        self.settings[key]=value;return True
    def GetMarkers(self):return copy.deepcopy(self.markers)
    def AddMarker(self,frame,color,name,note,duration,custom):
        self.markers[float(frame)]=dict(color=color,name=name,note=note,duration=duration,customData=custom);return True

    def DeleteClips(self,items,ripple=False):
        self.clear_calls=getattr(self,'clear_calls',0)+1
        if getattr(self,'fail_clear',False):return False
        self.items={kind:[item for item in values if item not in items] for kind,values in self.items.items()};return True
    def DeleteMarkersByColor(self,color):self.markers={};return True

class Pool:
    def __init__(self,project,media):self.project=project;self.media=media;self.append_calls=0;self.fail_append=False
    def GetRootFolder(self):return self
    def GetClipList(self):return [self.media]
    def GetSubFolderList(self):return []
    def CreateEmptyTimeline(self,name):
        target=Timeline(name,'new');self.project.timelines.append(target);return target
    def AppendToTimeline(self,rows):
        self.append_calls+=1
        for row in rows:
            for kind in ['video','audio']:
                self.project.current.items[kind].append(Item(row['mediaPoolItem'],row['startFrame'],row['endFrame'],row['recordFrame']))
            if self.fail_append:raise RuntimeError('native_partial_append')
        return [True]*len(rows)

class Project:
    def __init__(self,media):
        self.timelines=[Timeline('source','source-id'),Timeline('target','target-id')];self.current=self.timelines[0];self.pool=Pool(self,media)
    def GetUniqueId(self):return 'project'
    def GetName(self):return 'synthetic-project'
    def GetTimelineCount(self):return len(self.timelines)
    def GetTimelineByIndex(self,i):return self.timelines[i-1]
    def GetSetting(self,key):return '25'
    def GetMediaPool(self):return self.pool
    def SetCurrentTimeline(self,t):self.current=t;return True
    def GetCurrentTimeline(self):return self.current

class Manager:
    def __init__(self):self.saves=0
    def ExportProject(self,name,path):
        with zipfile.ZipFile(path,'w') as z:z.writestr('project.xml','synthetic backup')
        return True
    def SaveProject(self):self.saves+=1;return True

class ResolveDeliveryTests(unittest.TestCase):
    save_recipe=fixtures.DeliveryTests.save_recipe
    pipeline=fixtures.DeliveryTests.pipeline
    # Reuse the synthetic input fixture, not its test methods.
    def setUp(self):
        fixtures.DeliveryTests.setUp(self);self.plan,self.audio_report=self.pipeline().prepare()
        self.state['project_identity']={'resolve_project_id':'project'}
        self.state['resolve']=dict(project_name='synthetic-project',source_timeline='source',target_timeline='target')
        self.project=Project(Media(self.media));self.manager=Manager();self.resolve=SimpleNamespace(GetVersionString=lambda:'test-version')
        conn=patch('delivery_resolve.connected',return_value=(self.resolve,self.manager,self.project));conn.start();self.addCleanup(conn.stop)
        doctor=patch('delivery_resolve.subprocess.run',return_value=SimpleNamespace(returncode=0));doctor.start();self.addCleanup(doctor.stop)
        def save(name,body):
            p=self.paths.workspace_root/name;atomic_json(p,body);return dict(path=name,sha256=_sha256(p))
        self.request=dict(timeline_mode='one_new_combined_timeline',timeline_name='new-delivery',product_id=1,requested_variants=3,gap_frames=50,
            native_protocol=save('protocol.json',dict(mode='exclusive',resolve_version='test-version',fps=25.,video_and_audio_same=True)),
            native_protocol_evidence=save('protocol-proof.json',dict(requested_api_start=100,requested_api_end=200,
                actual=dict(rows=[dict(kind='video',duration=100),dict(kind='audio',duration=100)]))))
        self.quality=lambda:dict(status='PASS',plan_digest=digest(self.plan))
        inspect(self.paths,self.state)
    def perform(self,op):return perform(self.paths,self.state,self.run,self.plan,self.request,self.quality,operation=op)

    def test_native_transaction_readback_and_no_duplicate_write(self):
        protected=[snapshot(t) for t in self.project.timelines]
        result=self.perform('write');self.assertEqual(result['status'],'VERIFIED')
        self.assertEqual(self.project.pool.append_calls,1);self.assertEqual(self.manager.saves,1)
        self.assertFalse(any(k=='timelineFrameRate' for k,v in self.project.current.setting_calls))
        self.assertEqual([snapshot(t) for t in self.project.timelines[:2]],protected)
        result=self.perform('readback');self.assertEqual(result['status'],'PASS')
        self.assertEqual(self.manager.saves,1)  # readback is read-only
        with self.assertRaisesRegex(ValueError,'no_reappend'):self.perform('write')
        self.assertEqual(self.project.pool.append_calls,1)

    def test_failed_verification_recovery_only_reads_and_saves(self):
        with patch('delivery_resolve.verify_combined',side_effect=ValueError('transient_readback')):
            with self.assertRaisesRegex(ValueError,'transient'):self.perform('write')
        journal=read(self.paths.workspace_root/'delivery/RESOLVE_TRANSACTION.json')
        self.assertEqual(journal['status'],'APPENDED_PENDING_VERIFY')
        self.assertEqual(self.perform('recover')['status'],'VERIFIED')
        self.assertEqual(self.project.pool.append_calls,1)

    def test_partial_append_never_replayed(self):
        self.project.pool.fail_append=True
        with self.assertRaisesRegex(RuntimeError,'partial_append'):self.perform('write')
        with self.assertRaisesRegex(ValueError,'no_reappend'):self.perform('write')
        with self.assertRaisesRegex(ValueError,'manual_inspection'):self.perform('recover')
        self.assertEqual(self.project.pool.append_calls,1)

    def test_protected_edit_before_write_blocks_without_backup_or_append(self):
        self.project.timelines[0].settings['timelineResolutionWidth']='1'
        with self.assertRaisesRegex(ValueError,'protected_changed'):self.perform('write')
        self.assertEqual(self.project.pool.append_calls,0)
        self.assertFalse((self.paths.workspace_root/'delivery/RESOLVE_TRANSACTION.json').exists())

    def test_changed_target_source_or_marker_rejected(self):
        self.perform('write');item=self.project.current.items['audio'][0];item.lo+=1
        with self.assertRaisesRegex(ValueError,'readback_'):self.perform('readback')
        item.lo-=1
        first=next(iter(self.project.current.markers));self.project.current.markers[first]['duration']-=1
        with self.assertRaisesRegex(ValueError,'marker_mismatch'):self.perform('readback')

    def test_failed_quality_or_protocol_never_creates_target(self):
        self.quality=lambda:dict(status='REPAIRABLE',plan_digest=digest(self.plan))
        with self.assertRaisesRegex(ValueError,'quality_gate'):self.perform('write')
        self.assertEqual(self.project.GetTimelineCount(),2)
        self.quality=lambda:dict(status='PASS',plan_digest=digest(self.plan));self.resolve.GetVersionString=lambda:'changed'
        with self.assertRaisesRegex(ValueError,'frame_rate_project_changed|environment_changed'):self.perform('write')
        self.assertEqual(self.project.GetTimelineCount(),2)

    def test_changed_plan_or_backup_blocks_recovery(self):
        self.perform('write');backup=next((self.paths.workspace_root/'delivery/backups').glob('*.drp'));backup.write_bytes(b'broken')
        with self.assertRaisesRegex(ValueError,'backup_unverified'):self.perform('recover')

class ReusableTests(ResolveDeliveryTests):
    def setUp(self):
        super().setUp()
        self.test=Timeline('test-line','test-id');self.project.timelines.append(self.test)
        for kind in ['video','audio']:
            self.test.items[kind]=[Item(self.project.pool.media,10,20,90000)]
        self.test.AddMarker(0,'Red','old','old',10,'old')
        self.state['runtime']=dict(test_timeline_binding=dict(project_id='project',timeline_id='test-id',name='test-line'))
        self.request.update(timeline_mode='reusable_test_timeline',timeline_name='test-line')
        inspect(self.paths,self.state,self.request)
    def test_changed_target_rate_stops_before_clear(self):
        target=self.project.timelines[-1]
        self.plan['frame_rates']['timelines'][target.uid]=dict(name=target.name,fps=25.)
        target.settings['timelineFrameRate']='30'
        with self.assertRaisesRegex(ValueError,'frame_rate_mismatch'):self.perform('write')
        self.assertEqual(getattr(target,'clear_calls',0),0)
        self.assertEqual(self.project.pool.append_calls,0)

    def test_reuse_preserves_id_inventory_and_other_timelines(self):
        before=[snapshot(t) for t in self.project.timelines[:2]]
        result=self.perform('write')
        self.assertEqual(result['timeline_id'],'test-id');self.assertEqual(self.project.GetTimelineCount(),3)
        self.assertEqual(self.test.clear_calls,1);self.assertEqual(len(self.test.markers),3)
        self.assertEqual([snapshot(t) for t in self.project.timelines[:2]],before)
        self.assertEqual(self.perform('readback')['status'],'PASS')
    def test_bad_binding_and_source_alias_block_before_deletion(self):
        self.state['runtime']['test_timeline_binding']['timeline_id']='source-id'
        with self.assertRaisesRegex(ValueError,'identity_ambiguous'):self.perform('write')
        self.assertFalse(hasattr(self.test,'clear_calls'))
    def test_source_and_final_roles_cannot_be_cleared_as_test(self):
        for name,uid in [('source','source-id'),('target','target-id')]:
            self.state['runtime']['test_timeline_binding']=dict(project_id='project',timeline_id=uid,name=name)
            self.request['timeline_name']=name
            with self.assertRaisesRegex(ValueError,'cannot_be_protected'):
                inspect(self.paths,self.state,self.request)
        self.assertFalse(hasattr(self.test,'clear_calls'))

    def test_failed_quality_never_clears_previous_test(self):
        before=snapshot(self.test);self.quality=lambda:dict(status='REPAIRABLE',plan_digest=digest(self.plan))
        with self.assertRaisesRegex(ValueError,'quality_gate'):self.perform('write')
        self.assertEqual(snapshot(self.test),before)
    def test_clear_failure_never_replays(self):
        self.test.fail_clear=True
        with self.assertRaisesRegex(ValueError,'clear_failed'):self.perform('write')
        with self.assertRaisesRegex(ValueError,'no_reappend'):self.perform('write')
        with self.assertRaisesRegex(ValueError,'manual_inspection'):self.perform('recover')
        self.assertEqual(self.test.clear_calls,1)

# Run the base tests once; the reusable mode has its own stateful cases.
for name in ResolveDeliveryTests.__dict__:
    if name.startswith('test_'):setattr(ReusableTests,name,None)

if __name__=='__main__':unittest.main()
