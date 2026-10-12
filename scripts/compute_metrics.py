"""Nested wall-clock spans; no user content. Inclusive times must not be summed."""
from contextlib import contextmanager
from contextvars import ContextVar
import time

_active = ContextVar('compute_metrics', default=None)


class Metrics:
    def __init__(self):
        self.rows = {}
        for name in ('candidate', 'selection', 'final_review', 'project_review', 'codex_request_wait',
                     'mimo_request_wait', 'asr_model_load', 'asr_transcription', 'local_asr_vad',
                     'plan', 'audio_rms_boundaries', 'audio_decode', 'video_decode', 'apple_vision',
                     'qwen_fallback', 'vision', 'frame_clock', 'frame_classification',
                     'final_audit', 'checkpoint', 'cache', 'media_signature'):
            self.add(name)

    def add(self, name, **counts):
        row = self.rows.setdefault(name, {'seconds': 0.0, 'calls': 0, 'units': 0,
                                         'hits': 0, 'errors': 0, 'retries': 0, 'invalidations': {}})
        for key, value in counts.items():
            if key == 'reason':
                row['invalidations'][value] = row['invalidations'].get(value, 0) + 1
            else:
                row[key] += value


def count(name, **values):
    if _active.get() is not None:
        _active.get().add(name, **values)


@contextmanager
def scope(metrics):
    token = _active.set(metrics)
    try:
        yield metrics
    finally:
        _active.reset(token)


@contextmanager
def span(name, units=0):
    start = time.perf_counter()
    count(name, calls=1, units=units)
    try:
        yield
    except Exception:
        count(name, errors=1)
        raise
    finally:
        count(name, seconds=time.perf_counter()-start)
