import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_delivery_pipeline as fixtures
from delivery_assembly import prepare_asr
from delivery_runtime import Ledger
from runtime_meta import _sha256

class ASRReuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.paths=fixtures.Paths(Path(self.tmp.name));self.paths.registered_scope=lambda:('project','batch')
        media=self.paths.workspace_root/'raw.mp4';media.write_bytes(b'synthetic')
        self.run=dict(inputs=[dict(path=str(media),bytes=media.stat().st_size,mtime_ns=media.stat().st_mtime_ns,sha256=_sha256(media))])
        self.index=dict(sources=[dict(source_stem='raw',video_path=str(media),duration=100)])
        self.ledger=Ledger(self.paths.workspace_root/'delivery')
        weights=dict(name='synthetic',resolved_path='unused-offline-path',weights_digest='v1')
        identity=patch('asr_identity.model_identity',return_value=weights);self.model_identity=identity.start();self.addCleanup(identity.stop)
        transcribe=patch('registered_pipeline.transcribe_window',return_value=[dict(start=10,end=11,text='合成测试')]);self.transcribe=transcribe.start();self.addCleanup(transcribe.stop)
        load=patch('windowed_asr_v2.load_model');self.load=load.start();self.addCleanup(load.stop)
    def run_windows(self,jobs):return prepare_asr(self.paths,{},self.index,jobs,'synthetic',self.ledger,run=self.run)
    def test_exact_and_contained_windows_do_not_transcribe_or_load(self):
        self.run_windows([dict(source_file='raw',start=10.,end=30.)]);self.transcribe.reset_mock()
        result=self.run_windows([dict(source_file='raw',start=12.,end=20.)])
        self.assertEqual(result['body']['windows'][0]['start'],10.)
        self.transcribe.assert_not_called();self.load.assert_not_called()
    def test_only_new_window_computes(self):
        self.run_windows([dict(source_file='raw',start=10.,end=20.)]);self.transcribe.reset_mock()
        self.run_windows([dict(source_file='raw',start=10.,end=20.),dict(source_file='raw',start=60.,end=70.)])
        self.assertEqual(self.transcribe.call_count,1)
    def test_model_change_invalidates_only_matching_compute_identity(self):
        self.run_windows([dict(source_file='raw',start=10.,end=20.)]);self.transcribe.reset_mock()
        self.model_identity.return_value=dict(name='synthetic',resolved_path='unused-offline-path',weights_digest='v2')
        self.run_windows([dict(source_file='raw',start=10.,end=20.)]);self.assertEqual(self.transcribe.call_count,1)
    def test_corrupt_cache_blocks_instead_of_silently_recomputing(self):
        self.run_windows([dict(source_file='raw',start=10.,end=20.)]);self.transcribe.reset_mock()
        path=next((self.paths.project_records_dir/'compute_v3/asr').glob('*.json'));value=json.loads(path.read_text());value['body']['words']=[];path.write_text(json.dumps(value))
        with self.assertRaisesRegex(Exception,'unproven_or_corrupt'):
            self.run_windows([dict(source_file='raw',start=10.,end=20.)])
        self.transcribe.assert_not_called()

if __name__=='__main__':unittest.main()
