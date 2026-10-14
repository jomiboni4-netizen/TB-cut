"""Read-only discovery of an installed offline ASR interpreter; no dependency installation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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


def request(payload, *, timeout=900):
    python = interpreter()
    before = _sha256(python.resolve())
    task = {**payload, 'task_digest': digest(payload)}
    env = dict(os.environ, TB_CUT_ASR_WORKER='1', PYTHONUTF8='1')
    with tempfile.TemporaryDirectory(prefix='tbcut-asr-worker-') as folder:
        source = Path(folder)/'request.json'
        source.write_text(json.dumps(task))
        child = subprocess.run([str(python), str(Path(__file__).with_name('asr_worker.py')), str(source)],
                               env=env, capture_output=True, text=True, timeout=timeout)
    if child.returncode:
        raise CacheIdentityError('offline_asr_worker_failed')
    try:
        result = json.loads(child.stdout)
        if result['task_digest'] != task['task_digest'] or _sha256(python.resolve()) != before:
            raise ValueError('identity')
        return result
    except (ValueError, KeyError) as exc:
        raise CacheIdentityError('offline_asr_worker_identity_invalid') from exc


def remote_identity(name):
    result = request({'operation': 'identity', 'model': name}, timeout=120)['model']
    python = interpreter()
    result['runtime_python'] = str(python)
    result['runtime_sha256'] = _sha256(python.resolve())
    return result
