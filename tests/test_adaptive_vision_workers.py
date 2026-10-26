import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import adaptive_vision as vision
from adaptive_vision import map_bounded


class BoundedVisionWorkerTests(unittest.TestCase):
    def test_jobs_overlap_within_bound_and_keep_input_order(self):
        lock = threading.Lock()
        active = peak = 0

        def classify(value):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(.02)
            with lock:
                active -= 1
            return value * 2

        values = list(range(12))
        self.assertEqual(map_bounded(classify, values, max_workers=4),
                         [value * 2 for value in values])
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, 4)

    def test_invalid_worker_limit_is_rejected(self):
        with self.assertRaisesRegex(Exception, 'frame_classifier_worker_limit_invalid'):
            map_bounded(lambda value: value, [1], max_workers=0)

    def test_codex_contact_sheets_overlap_within_bound_and_keep_batch_results(self):
        import tempfile

        lock = threading.Lock()
        active = peak = 0
        pending = []
        for index in range(9):
            image = Path(tempfile.gettempdir())/f'tbcut-worker-{index}.png'
            image.write_bytes(b'panel')
            pending.append({'record': {'variant_id': 'v1', 'sample_kind': 'initial',
                                        'stratum_index': index},
                            'key': ('v1', 'initial', index), 'expected': {'key': str(index)},
                            'image_path': image,
                            'apple': {'label': 'NO_PERSON', 'subject_evidence': False,
                                      'reason': 'synthetic'},
                            'codex': None})

        def classify(images, _sheet, _model):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(.03)
            with lock:
                active -= 1
            return [{'label': 'NO_PERSON', 'subject_evidence': False,
                     'reason': f'batch-{Path(image).stem}'} for image in images]

        with tempfile.TemporaryDirectory() as folder, \
             patch.object(vision, 'compose_contact_sheet',
                          side_effect=lambda _paths, _images, out: Path(out)), \
             patch.object(vision, 'classify_with_codex_batch', side_effect=classify), \
             patch('compute_store.cached', side_effect=lambda _p, _e, producer: (producer(), False)):
            values = {}
            vision.review_pending_frames(object(), pending,
                                         {'final_absence_review': {'provider': 'codex'}},
                                         folder, values)
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, vision.MAX_CONCURRENT_CODEX_SHEETS)
        self.assertEqual([pending[index]['codex']['reason'] for index in range(9)],
                         [f'batch-tbcut-worker-{index}' for index in range(9)])


if __name__ == '__main__':
    unittest.main()
