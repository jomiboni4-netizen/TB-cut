import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from resolve_write_v2 import snapshot_preserved, validate_written_rows


class ResolveWriteSyncTests(unittest.TestCase):
    def test_append_preserves_old_rows_markers_and_settings(self):
        before = {'id':'target-id','name':'成片','start':0,'end':100,
                  'settings':{'timelineFrameRate':'25'},
                  'rows':[{'kind':'video','record_start':0,'record_end':100}],
                  'markers':{'0':{'name':'old','customData':'old'}}}
        after = {**before,'end':250,
                 'rows':before['rows']+[{'kind':'video','record_start':150,'record_end':250}],
                 'markers':{**before['markers'],'150':{'name':'new','customData':'new'}}}
        self.assertTrue(snapshot_preserved(before,after))

    def test_append_rejects_changed_existing_row_marker_or_identity(self):
        before = {'id':'target-id','name':'成片','start':0,'end':100,
                  'settings':{'timelineFrameRate':'25'},
                  'rows':[{'kind':'video','record_start':0,'record_end':100}],
                  'markers':{'0':{'name':'old','customData':'old'}}}
        changed_row={**before,'rows':[{'kind':'video','record_start':1,'record_end':100}]}
        changed_marker={**before,'markers':{'0':{'name':'changed','customData':'old'}}}
        changed_identity={**before,'id':'other-target'}
        self.assertFalse(snapshot_preserved(before,changed_row))
        self.assertFalse(snapshot_preserved(before,changed_marker))
        self.assertFalse(snapshot_preserved(before,changed_identity))

    def test_audio_with_matching_record_but_wrong_source_is_rejected(self):
        expected = [{'media': 'raw.mp4', 'record_start': 0, 'record_end': 100,
                     'source_start': 250, 'source_end': 350}]
        video = [{'media': 'raw.mp4', 'record_start': 0, 'record_end': 100,
                  'source_start': 250, 'source_end': 349}]
        self.assertEqual(validate_written_rows(expected, video, [video[0].copy()]), [])
        wrong_audio = [{**video[0], 'source_start': 251}]
        self.assertTrue(any('音频' in error for error in
                            validate_written_rows(expected, video, wrong_audio)))


if __name__ == '__main__':
    unittest.main()
