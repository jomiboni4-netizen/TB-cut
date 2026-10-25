import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from resolve_connection_doctor import selected_target_readiness


class FakeTimeline:
    def __init__(self, name, unique_id, video=1, audio=1):
        self.name, self.unique_id = name, unique_id
        self.video, self.audio = video, audio

    def GetName(self): return self.name
    def GetUniqueId(self): return self.unique_id
    def GetTrackCount(self, kind): return self.video if kind == 'video' else self.audio


class FakeProject:
    def __init__(self, timelines): self.timelines = timelines
    def GetTimelineCount(self): return len(self.timelines)
    def GetTimelineByIndex(self, index): return self.timelines[index - 1]


class SelectedTargetReadinessTests(unittest.TestCase):
    def setUp(self):
        self.source = FakeTimeline('素材', 'source-id')
        self.target = FakeTimeline('测试', 'test-id')
        self.project = FakeProject([self.source, self.target])
        self.expected = {'target_timeline': '成片'}

    def test_exact_explicit_target_is_validated_without_rebinding_project(self):
        result = selected_target_readiness(self.project, self.expected, 'source-id', '测试')
        self.assertTrue(result['ready'])
        self.assertEqual(result['target_timeline_id'], 'test-id')
        self.assertEqual(self.expected['target_timeline'], '成片')

    def test_source_timeline_cannot_be_selected_as_target(self):
        result = selected_target_readiness(self.project, self.expected, 'source-id', '素材')
        self.assertEqual(result['reason'], 'selected_target_conflicts_with_source')

    def test_duplicate_or_missing_target_name_blocks(self):
        duplicate = FakeProject([self.source, self.target, FakeTimeline('测试', 'other-id')])
        self.assertEqual(selected_target_readiness(duplicate, self.expected, 'source-id', '测试')['reason'],
                         'selected_target_timeline_missing_or_ambiguous')
        self.assertEqual(selected_target_readiness(self.project, self.expected, 'source-id', '缺失')['reason'],
                         'selected_target_timeline_missing_or_ambiguous')

    def test_target_without_both_track_types_blocks(self):
        project = FakeProject([self.source, FakeTimeline('测试', 'test-id', video=1, audio=0)])
        self.assertEqual(selected_target_readiness(project, self.expected, 'source-id', '测试')['reason'],
                         'selected_target_missing_audio_or_video_tracks')


if __name__ == '__main__':
    unittest.main()
