import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from coldstart_resolve import source_start_matches, source_end_mode
class Precision(unittest.TestCase):
 def test_exact(self):self.assertTrue(source_start_matches({'source_start':100},100,25))
 def test_truncated_float_with_two_agreeing_observations(self):
  self.assertTrue(source_start_matches(dict(source_start=12844,source_start_time=513.8,source_left_offset=12845.),12845,25))
 def test_actual_frame_error(self):
  self.assertFalse(source_start_matches(dict(source_start=99,source_start_time=3.96,source_left_offset=99.),100,25))
 def test_missing_or_conflicting_observation(self):
  self.assertFalse(source_start_matches(dict(source_start=99,source_start_time=4.),100,25))
  self.assertFalse(source_start_matches(dict(source_start=99,source_start_time=4.,source_left_offset=99.),100,25))
 def test_large_shift(self):
  self.assertFalse(source_start_matches(dict(source_start=98,source_start_time=4.,source_left_offset=100.),100,25))
 def test_end_float_truncation_requires_two_agreeing_observations(self):
  row=dict(source_end_raw=10000,source_end_time=10001.000000000002/25,source_left_offset=9000.,duration=1001)
  self.assertEqual(source_end_mode(row,10001,25),'exclusive')
 def test_end_actual_error_is_not_relabelled_as_inclusive(self):
  row=dict(source_end_raw=10000,source_end_time=10000/25,source_left_offset=9000.,duration=1001)
  with self.assertRaisesRegex(ValueError,'precision_mismatch'):source_end_mode(row,10001,25)
 def test_end_incomplete_precision_proof_blocks(self):
  with self.assertRaisesRegex(ValueError,'precision_mismatch'):
   source_end_mode(dict(source_end_raw=99,source_end_time=4.,duration=50),100,25)
if __name__=='__main__':unittest.main()
