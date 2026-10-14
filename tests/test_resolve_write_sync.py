import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from resolve_write_v2 import validate_written_rows


class ResolveWriteSyncTests(unittest.TestCase):
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
