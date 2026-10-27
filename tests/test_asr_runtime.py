"""Synthetic checks for ASR warmup and persistent worker reuse."""
import json
from pathlib import Path
import queue
import sys
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import asr_runtime
import asr_worker
from project_registry import digest
from runtime_meta import CacheIdentityError


class _FakeStdout:
    def __init__(self):
        self.lines = queue.Queue()

    def __iter__(self):
        return self

    def __next__(self):
        line = self.lines.get()
        if line is None:
            raise StopIteration
        return line


class _FakeStdin:
    def __init__(self, stdout):
        self.stdout = stdout
        self.closed = False

    def write(self, line):
        task = json.loads(line)
        self.stdout.lines.put(json.dumps({'task_digest': task['task_digest'], 'status': 'ok'}) + '\n')

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(self):
        self.stdout = _FakeStdout()
        self.stdin = _FakeStdin(self.stdout)
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0
        self.stdout.lines.put(None)

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        return self.returncode or 0


class PersistentAsrWorkerTests(unittest.TestCase):
    def test_multiple_requests_share_one_process_and_keep_task_identity(self):
        process = _FakeProcess()
        with patch.object(asr_runtime.subprocess, 'Popen', return_value=process) as popen, \
                patch.object(asr_runtime, '_sha256', return_value='python-digest'):
            worker = asr_runtime.PersistentWorker(Path('/synthetic/python'))
            first = worker.request({'operation': 'identity', 'model': 'synthetic'})
            second = worker.request({'operation': 'preload', 'model': 'synthetic'})
            self.assertEqual(first['status'], 'ok')
            self.assertEqual(second['status'], 'ok')
            popen.assert_called_once()
            worker.close()

    def test_worker_failure_is_actionable_and_process_can_be_closed(self):
        process = _FakeProcess()
        process.stdin.write = lambda _line: process.stdout.lines.put(
            json.dumps({'task_digest': '', 'error': 'offline_asr_worker_failed'}) + '\n')
        with patch.object(asr_runtime.subprocess, 'Popen', return_value=process), \
                patch.object(asr_runtime, '_sha256', return_value='python-digest'):
            worker = asr_runtime.PersistentWorker(Path('/synthetic/python'))
            with self.assertRaisesRegex(CacheIdentityError, 'offline_asr_worker_failed'):
                worker.request({'operation': 'transcribe', 'model': 'synthetic'})
            worker.close()


class AsrModelReuseTests(unittest.TestCase):
    def setUp(self):
        asr_worker._model.cache_clear()
        asr_runtime._prewarm_futures.clear()
        asr_runtime._prewarm_counted.clear()
        self.addCleanup(asr_worker._model.cache_clear)
        self.addCleanup(asr_runtime._prewarm_futures.clear)
        self.addCleanup(asr_runtime._prewarm_counted.clear)

    def test_persistent_worker_loads_a_model_once_for_repeated_preloads(self):
        weights = {'name': 'synthetic', 'resolved_path': '/synthetic/model',
                   'weights_digest': 'same-weights'}
        with patch.object(asr_worker, 'model_identity', return_value=weights), \
                patch('windowed_asr_v2.load_model', return_value=object()) as load_model:
            for _ in range(2):
                payload = {'operation': 'preload', 'model': 'synthetic'}
                result = asr_worker.process({**payload, 'task_digest': digest(payload)})
                self.assertEqual(result['status'], 'PRELOADED')
            load_model.assert_called_once_with('/synthetic/model', 'same-weights')

    def test_direct_model_preload_is_reused_by_later_asr_calls(self):
        weights = {'name': 'synthetic', 'resolved_path': '/synthetic/model',
                   'weights_digest': 'same-weights'}
        model = object()
        with patch('asr_identity.model_identity', return_value=weights), \
                patch('windowed_asr_v2.load_model', return_value=model) as load_model:
            first = asr_runtime.begin_prewarm('synthetic')
            second = asr_runtime.begin_prewarm('synthetic')
            self.assertIs(first, second)
            self.assertEqual(asr_runtime.wait_for_prewarm('synthetic')['weights_digest'], 'same-weights')
            load_model.assert_called_once_with('/synthetic/model', 'same-weights')

    def test_worker_model_cache_is_bound_to_weight_digest(self):
        weights = {'name': 'synthetic', 'resolved_path': '/synthetic/model',
                   'weights_digest': 'old-weights'}
        with patch.object(asr_worker, 'model_identity', return_value=weights), \
                patch('windowed_asr_v2.load_model', side_effect=[object(), object()]) as load_model:
            for model_digest in ('old-weights', 'new-weights'):
                current = {**weights, 'weights_digest': model_digest}
                payload = {'operation': 'preload', 'model': 'synthetic'}
                with patch.object(asr_worker, 'model_identity', return_value=current):
                    asr_worker.process({**payload, 'task_digest': digest(payload)})
            self.assertEqual(load_model.call_count, 2)

    def test_direct_loader_keeps_primary_and_fallback_models_warm(self):
        from windowed_asr_v2 import load_model
        load_model.cache_clear()
        whisper = Mock()
        fake_module = types.SimpleNamespace(WhisperModel=whisper)
        with patch.dict(sys.modules, {'faster_whisper': fake_module}):
            first = load_model('/synthetic/primary', 'primary-digest')
            self.assertIs(first, load_model('/synthetic/primary', 'primary-digest'))
            second = load_model('/synthetic/fallback', 'fallback-digest')
            self.assertIs(second, load_model('/synthetic/fallback', 'fallback-digest'))
            self.assertEqual(whisper.call_count, 2)
        load_model.cache_clear()


if __name__ == '__main__':
    unittest.main()
