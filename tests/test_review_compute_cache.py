import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from review_compute_cache import LazyModel, cached_probe, probe_key
from windowed_asr_v2 import merge_windows


class ReviewComputeCacheTests(unittest.TestCase):
    def test_cached_asr_never_loads_model(self):
        factory = Mock()
        LazyModel(factory)
        factory.assert_not_called()

    def test_multiple_asr_misses_load_once(self):
        factory = Mock(return_value=object())
        lazy = LazyModel(factory)
        self.assertIs(lazy.get(), lazy.get())
        factory.assert_called_once()

    def test_windows_merge_without_bridging_gaps_or_sources(self):
        rows = [dict(source_file="a", start=1, end=8),
                dict(source_file="a", start=4, end=10),
                dict(source_file="a", start=4, end=10),
                dict(source_file="a", start=11, end=12),
                dict(source_file="b", start=1, end=8)]
        self.assertEqual(merge_windows(rows), [dict(source_file="a", start=1, end=10), rows[3], rows[4]])
        self.assertEqual(rows[0]["end"], 8)

    def test_key_tracks_media_detector_ranges_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "media"
            media.write_bytes(b"one")
            key = probe_key(media, "detector1", ["1:2", "3:4"])
            self.assertNotEqual(key, probe_key(media, "detector2", ["1:2", "3:4"]))
            self.assertNotEqual(key, probe_key(media, "detector1", ["1:2.04", "3:4"]))
            self.assertNotEqual(key, probe_key(media, "detector1", ["3:4", "1:2"]))
            media.write_bytes(b"different")
            self.assertNotEqual(key, probe_key(media, "detector1", ["1:2", "3:4"]))

    def test_same_physical_ranges_reuse_computation(self):
        with tempfile.TemporaryDirectory() as tmp:
            compute = Mock(return_value={"personSamples": 8, "totalSamples": 10})
            report, hit = cached_probe(Path(tmp), "same", compute)
            self.assertFalse(hit)
            self.assertEqual(cached_probe(Path(tmp), "same", compute), (report, True))
            compute.assert_called_once()
            # Approval is calculated afresh using the current threshold.
            self.assertTrue(report["personSamples"] / report["totalSamples"] > .7)
            self.assertFalse(report["personSamples"] / report["totalSamples"] > .8)

    def test_corrupt_and_wrong_key_cache_recomputed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            compute = Mock(return_value={"personSamples": 1, "totalSamples": 2})
            for content in ["invalid", "[]", json.dumps({"key": "other", "report": compute.return_value})]:
                (cache / "same.json").write_text(content)
                self.assertFalse(cached_probe(cache, "same", compute)[1])
            self.assertEqual(compute.call_count, 3)

    def test_invalid_results_fail_closed_and_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            for report in [{"personSamples": 0, "totalSamples": 0},
                           {"personSamples": 3, "totalSamples": 2},
                           {"personSamples": -1, "totalSamples": 2},
                           {"personSamples": True, "totalSamples": 2}]:
                with self.assertRaises(ValueError):
                    cached_probe(Path(tmp), "bad", lambda: report)
            self.assertFalse((Path(tmp) / "bad.json").exists())

    def test_detector_failure_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                cached_probe(Path(tmp), "bad", Mock(side_effect=RuntimeError("detector failed")))
            self.assertFalse((Path(tmp) / "bad.json").exists())


if __name__ == "__main__":
    unittest.main()
