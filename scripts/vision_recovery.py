"""Durable, bounded technical recovery. Never a semantic verdict or write retry."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import hmac
import json
from pathlib import Path
import shutil
import threading
import time
import tempfile

from project_registry import atomic_json, digest
from runtime_meta import CacheIdentityError, _sha256
from compute_store import check_media

POLICY_PATH = Path(__file__).resolve().parents[1] / 'config/vision_recovery.json'
POLICY = json.loads(POLICY_PATH.read_text(encoding='utf-8'))
CONTRACT = 'vision-recovery-v1'
if (set(POLICY) != {'contract', 'initial_request_seconds', 'recovery_request_seconds',
                    'max_recovery_requests_per_product', 'max_recovery_seconds_per_product',
                    'max_recovery_attempts_per_frame'} or POLICY.get('contract') != CONTRACT
        or any(type(v) is not int or v < 1
        for k, v in POLICY.items() if k != 'contract')):
    raise CacheIdentityError('vision_recovery_policy_invalid')
_active = ContextVar('vision_recovery', default=None)
_timeout = ContextVar('vision_request_timeout', default=None)
_deadline = ContextVar('vision_request_deadline', default=None)


def current():
    return _active.get()


def request_timeout():
    timeout = _timeout.get() or POLICY['initial_request_seconds']
    if _deadline.get() is not None:
        timeout = min(timeout, _deadline.get()-time.monotonic())
        if timeout <= 0:
            raise CacheIdentityError('vision_recovery_deadline_exceeded')
    return timeout


def technical_failure(value):
    if value.get('label') not in ('DETECTOR_ERROR', 'UNCERTAIN'):
        return False
    reason = str(value.get('reason', ''))
    return any(code in reason for code in ('TimeoutExpired', 'ConnectionError',
        'ConnectionResetError', 'BrokenPipeError', 'OSError', 'response_invalid',
        'response_ambiguous', 'batch_count_mismatch', 'codex_visual_review_failed',
        'execution_interrupted', 'recovery_deadline_exceeded', 'recovery_budget_exhausted'))


@contextmanager
def recovery_scope(paths, product_id):
    controller = Controller(paths, product_id)
    token = _active.set(controller)
    try:
        yield controller
    finally:
        _active.reset(token)


class Controller:
    def __init__(self, paths, product_id):
        self.paths = paths
        self.lock = threading.RLock()
        self.serial = threading.Lock()
        from codex_worker import key
        self.secret = key(paths)
        control_path = paths.project_directory / 'PROJECT_TASKS.json'
        control = json.loads(control_path.read_text(encoding='utf-8')) if control_path.exists() else {}
        self.identity = {'contract': CONTRACT, 'project_key': paths.registered_scope()[0],
                         'batch_scope': paths.registered_scope()[1], 'product_id': product_id,
                         'task_id': control.get('active_task_id'), 'policy': POLICY}
        self.directory = paths.project_records_dir / 'vision_recovery'
        budget_key = {'project_key': self.identity['project_key'], 'product_id': product_id,
                      'task_scope': control.get('active_task_id') or self.identity['batch_scope']}
        self.path = self.directory / (digest(budget_key) + '.json')
        self.jobs = paths.project_records_dir / 'vision_pending_frames'
        self.state = self._read(self.path) if self.path.exists() else {
            'identity': self.identity, 'requests': 0, 'reserved_seconds': 0., 'frames': {}, 'attempts': []}
        if self.state['identity'] != self.identity:
            raise CacheIdentityError('vision_recovery_identity_changed')

    def _read(self, path):
        if path.is_symlink():
            raise CacheIdentityError('vision_recovery_symlink_forbidden')
        value = json.loads(path.read_text(encoding='utf-8'))
        seal = value.pop('seal', None)
        if not isinstance(seal, str) or not hmac.compare_digest(seal,
                hmac.new(self.secret, digest(value).encode(), hashlib.sha256).hexdigest()):
            raise CacheIdentityError('vision_recovery_record_unproven')
        return value

    def _write(self, path, body):
        if path.is_symlink():
            raise CacheIdentityError('vision_recovery_symlink_forbidden')
        value = dict(body)
        value['seal'] = hmac.new(self.secret, digest(value).encode(), hashlib.sha256).hexdigest()
        atomic_json(path, value)

    def persist(self):
        self._write(self.path, self.state)

    def prepare(self, item):
        """Keep authenticated Apple evidence and original image across interruptions."""
        if not item['image_path'].is_file() or item['apple'].get('label') == 'DETECTOR_ERROR':
            return
        ident = item['expected']; job = digest(ident)
        check_media(ident['media'])
        with self.lock:
            self.jobs.mkdir(parents=True, exist_ok=True)
            image = self.jobs / (job + '.png')
            if image.is_symlink():
                raise CacheIdentityError('vision_recovery_symlink_forbidden')
            if not image.exists():
                with tempfile.NamedTemporaryFile(dir=self.jobs, suffix='.png', delete=False) as stream:
                    temporary = Path(stream.name)
                try:
                    shutil.copyfile(item['image_path'], temporary)
                    temporary.replace(image)
                finally:
                    temporary.unlink(missing_ok=True)
            body = {'identity': ident, 'apple': item['apple'], 'image_sha256': _sha256(image)}
            record = self.jobs / (job + '.json')
            if record.exists():
                if self._read(record) != body:
                    raise CacheIdentityError('vision_pending_frame_changed')
            else:
                self._write(record, body)
            self.state['frames'].setdefault(job, {'attempts': 0, 'status': 'PENDING'})
            self.persist()

    def restore(self, item):
        job = digest(item['expected']); record = self.jobs / (job + '.json')
        if not record.exists():
            return False
        body = self._read(record); image = self.jobs / (job + '.png')
        check_media(item['expected']['media'])
        if body['identity'] != item['expected'] or not image.is_file() or _sha256(image) != body['image_sha256']:
            raise CacheIdentityError('vision_pending_frame_changed')
        shutil.copyfile(image, item['image_path'])
        item['apple'], item['codex'] = body['apple'], None
        with self.lock:
            self.state['frames'].setdefault(job, {'attempts': 0, 'status': 'PENDING'})
            self.persist()
        return True

    def finished(self, item, value):
        job = digest(item['expected'])
        with self.lock:
            row = self.state['frames'].setdefault(job, {'attempts': 0})
            row.update(status=('WAITING_EVIDENCE' if value.get('label') in ('DETECTOR_ERROR', 'UNCERTAIN')
                               else 'DONE'), reason=value.get('reason'),
                       last_value=value.get('codex', value.get('apple_vision', value)))
            self.persist()

    def initial(self, group, request):
        from project_task import guard
        guard(self.paths, 'run')
        if json.loads(POLICY_PATH.read_text(encoding='utf-8')) != POLICY:
            raise CacheIdentityError('vision_recovery_policy_changed_during_run')
        values, fresh = [], []
        with self.lock:
            exhausted = (self.state['requests'] >= POLICY['max_recovery_requests_per_product']
                         or self.state['reserved_seconds'] >= POLICY['max_recovery_seconds_per_product'])
            circuit_open = exhausted and any(
                technical_failure(row.get('last_value', {})) and row.get('status') != 'DONE'
                for row in self.state['frames'].values())
            for index, item in enumerate(group):
                row = self.state['frames'].setdefault(digest(item['expected']), {'attempts': 0})
                values.append(row.get('last_value') or {'label': 'DETECTOR_ERROR',
                    'subject_evidence': False, 'reason': 'vision_execution_interrupted'})
                if circuit_open:
                    values[-1] = {'label': 'DETECTOR_ERROR', 'subject_evidence': False,
                                  'reason': 'vision_recovery_budget_exhausted'}
                elif not row.get('initial_attempted'):
                    fresh.append(index)
                    row.update(initial_attempted=True, status='RUNNING')
            self.persist()
        if fresh:
            result = request([group[index] for index in fresh])
            for index, value in zip(fresh, result):
                values[index] = value
        return values

    def _reserve(self, items):
        from project_task import guard
        guard(self.paths, 'run')
        if json.loads(POLICY_PATH.read_text(encoding='utf-8')) != POLICY:
            raise CacheIdentityError('vision_recovery_policy_changed_during_run')
        jobs = [digest(item['expected']) for item in items]
        with self.lock:
            remaining = POLICY['max_recovery_seconds_per_product'] - self.state['reserved_seconds']
            if (remaining <= 0 or self.state['requests'] >= POLICY['max_recovery_requests_per_product']
                    or any(self.state['frames'].get(j, {}).get('attempts', 0)
                           >= POLICY['max_recovery_attempts_per_frame'] for j in jobs)):
                return None
            timeout = min(POLICY['recovery_request_seconds'], remaining)
            self.state['requests'] += 1
            self.state['reserved_seconds'] += timeout
            for job in jobs:
                row = self.state['frames'].setdefault(job, {'attempts': 0})
                row['attempts'] += 1
                row['status'] = 'RUNNING'
            attempt = {'frames': jobs, 'timeout_seconds': timeout, 'status': 'RUNNING'}
            self.state['attempts'].append(attempt)
            self.persist()  # Interrupted requests keep their reserved budget.
            return attempt

    def recover(self, group, verdicts, request):
        """Retry only technical failures, serially: pairs then individual images."""
        values = list(verdicts)
        with self.serial:
            for size in (2, 1):
                failed = [i for i, v in enumerate(values) if technical_failure(v)]
                for offset in range(0, len(failed), size):
                    positions = failed[offset:offset+size]
                    items = [group[i] for i in positions]
                    attempt = self._reserve(items)
                    if attempt is None:
                        continue
                    started = time.monotonic(); token = _timeout.set(attempt['timeout_seconds'])
                    deadline_token = _deadline.set(started+attempt['timeout_seconds'])
                    try:
                        result = request(items)
                        if len(result) != len(items):
                            raise CacheIdentityError('codex_batch_count_mismatch')
                        for index, value in zip(positions, result):
                            values[index] = value
                        attempt['status'] = ('WAITING_EVIDENCE' if any(technical_failure(v) for v in result)
                                             else 'DONE')
                    except Exception as exc:
                        attempt.update(status='WAITING_EVIDENCE', reason=type(exc).__name__)
                    finally:
                        _timeout.reset(token)
                        _deadline.reset(deadline_token)
                        elapsed = max(0., time.monotonic()-started)
                        with self.lock:
                            self.state['reserved_seconds'] -= attempt['timeout_seconds']-elapsed
                            attempt['elapsed_seconds'] = elapsed
                            self.persist()
        return values

    def summary(self):
        return {'contract': CONTRACT, 'requests_used': self.state['requests'],
                'recovery_seconds_used': round(self.state['reserved_seconds'], 3),
                'waiting_frames': sum(r.get('status') != 'DONE' for r in self.state['frames'].values()),
                'max_requests': POLICY['max_recovery_requests_per_product'],
                'max_seconds': POLICY['max_recovery_seconds_per_product']}
