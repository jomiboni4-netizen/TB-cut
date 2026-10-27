"""Bounded local ASR runtime with a persistent, identity-checked worker."""
import json
import atexit
import queue
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from runtime_meta import CacheIdentityError, _sha256
from project_registry import digest


def interpreter():
    explicit = os.environ.get('TB_CUT_ASR_PYTHON')
    candidate = Path(explicit) if explicit else Path.home()/'Documents/Codex/.venvs/faster-whisper/bin/python'
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise CacheIdentityError('installed_asr_interpreter_missing')
    # A venv deliberately shares the base executable symlink but has different import paths.
    if candidate.absolute() == Path(sys.executable).absolute():
        raise CacheIdentityError('asr_worker_recursion_blocked')
    return candidate


class PersistentWorker:
    """One verified offline interpreter/model process shared by this scheduler run."""

    def __init__(self, python):
        self.python = python
        self.process = None
        self.responses = queue.Queue()
        self.lock = threading.Lock()

    def _start(self):
        if self.process is not None and self.process.poll() is None:
            return
        self._stop()
        self.responses = queue.Queue()
        env = dict(os.environ, TB_CUT_ASR_WORKER='1', PYTHONUTF8='1')
        command = [str(self.python), str(Path(__file__).with_name('asr_worker.py')), '--stdio']
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                                            bufsize=1, env=env)
        except OSError as exc:
            raise CacheIdentityError('offline_asr_worker_start_failed') from exc
        threading.Thread(target=self._read_responses, daemon=True,
                         name='tbcut-asr-worker-reader').start()

    def _read_responses(self):
        process = self.process
        try:
            for line in process.stdout:
                try:
                    self.responses.put(json.loads(line))
                except ValueError:
                    self.responses.put({'error': 'offline_asr_worker_response_invalid'})
        finally:
            self.responses.put(None)

    def _stop(self):
        process, self.process = self.process, None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
            process.terminate()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def close(self):
        with self.lock:
            self._stop()

    def request(self, payload, *, timeout=900):
        task = {**payload, 'task_digest': digest(payload)}
        before = _sha256(self.python.resolve())
        with self.lock:
            self._start()
            try:
                self.process.stdin.write(json.dumps(task, ensure_ascii=False) + '\n')
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._stop()
                raise CacheIdentityError('offline_asr_worker_pipe_failed') from exc
            try:
                result = self.responses.get(timeout=timeout)
            except queue.Empty as exc:
                self._stop()
                raise CacheIdentityError('offline_asr_worker_timed_out') from exc
            if result is None:
                self._stop()
                raise CacheIdentityError('offline_asr_worker_exited')
            if result.get('error'):
                if result.get('task_digest') not in ('', task['task_digest']):
                    raise CacheIdentityError('offline_asr_worker_identity_invalid')
                raise CacheIdentityError(result['error'])
            if result.get('task_digest') != task['task_digest'] or _sha256(self.python.resolve()) != before:
                raise CacheIdentityError('offline_asr_worker_identity_invalid')
            return result


_worker_lock = threading.Lock()
_worker = None
_prewarm_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='tbcut-asr-prewarm')
_prewarm_futures = {}
_prewarm_counted = set()


def _persistent_worker():
    global _worker
    python = interpreter()
    with _worker_lock:
        if _worker is None or _worker.python != python:
            if _worker is not None:
                _worker.close()
            _worker = PersistentWorker(python)
            atexit.register(_worker.close)
        return _worker


def request(payload, *, timeout=900):
    return _persistent_worker().request(payload, timeout=timeout)


def _preload(model):
    started = time.perf_counter()
    from asr_identity import model_identity
    weights = model_identity(model)
    if weights.get('runtime_python'):
        response = request({'operation': 'preload', 'model': model}, timeout=900)
        if response.get('status') != 'PRELOADED':
            raise CacheIdentityError('offline_asr_prewarm_failed')
    else:
        from windowed_asr_v2 import load_model
        load_model(weights['resolved_path'], weights['weights_digest'])
    return {'model': model, 'weights_digest': weights['weights_digest'],
            'elapsed_seconds': time.perf_counter() - started}


def begin_prewarm(model='large-v3-turbo'):
    """Start model loading in the background; the first ASR stage joins it."""
    with _worker_lock:
        future = _prewarm_futures.get(model)
        if future is None:
            future = _prewarm_executor.submit(_preload, model)
            _prewarm_futures[model] = future
        return future


def wait_for_prewarm(model='large-v3-turbo'):
    with _worker_lock:
        future = _prewarm_futures.get(model)
    if future is None:
        return None
    try:
        result = future.result()
    except Exception:
        # Prewarming is an optimization. The normal authenticated ASR path
        # remains responsible for reporting a real model/runtime failure.
        return None
    with _worker_lock:
        if model in _prewarm_counted:
            return result
        _prewarm_counted.add(model)
    from compute_metrics import count
    count('asr_model_prewarm', calls=1, seconds=result['elapsed_seconds'])
    return result


def close_worker():
    global _worker
    with _worker_lock:
        if _worker is not None:
            _worker.close()
            _worker = None


def remote_identity(name):
    result = request({'operation': 'identity', 'model': name}, timeout=120)['model']
    python = interpreter()
    result['runtime_python'] = str(python)
    result['runtime_sha256'] = _sha256(python.resolve())
    return result
