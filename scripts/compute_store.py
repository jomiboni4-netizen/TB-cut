"""Project-scoped immutable physical computations, never approvals or legacy imports."""
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
from project_registry import atomic_json, digest
from runtime_meta import CacheIdentityError, _sha256
from compute_metrics import count, span

CONTRACT = 'physical-compute-v3'


def media_signature(media):
    media = Path(media).resolve()
    with span('media_signature', media.stat().st_size):
        before = media.stat()
        value = {'path': str(media), 'device': before.st_dev, 'inode': before.st_ino,
                 'size': before.st_size, 'mtime_ns': before.st_mtime_ns, 'sha256': _sha256(media)}
        check_media(value)
        return value


def check_media(signature):
    st = Path(signature['path']).stat()
    if (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns) != tuple(signature[k] for k in
            ('device', 'inode', 'size', 'mtime_ns')):
        raise CacheIdentityError('media_changed_during_compute')


def identity(paths, kind, media, model, parameters, bounds):
    if not paths.registered_scope() or not model:
        raise CacheIdentityError('physical_compute_identity_missing')
    return {'contract': CONTRACT, 'project_key': paths.registered_scope()[0], 'kind': kind,
            'media': media, 'model': model, 'parameters': parameters, 'bounds': bounds}


def directory(paths, kind):
    return paths.project_records_dir / 'compute_v3' / kind


def read(paths, path, expected=None):
    try:
        value = json.loads(path.read_text())
        actual = value['identity']
        unsigned = {k: v for k, v in value.items() if k != 'seal'}
        secret_path = paths.project_records_dir/'worker_seal.key'
        if (actual.get('contract') != CONTRACT or path.stem != digest(actual)
                or not secret_path.is_file() or not isinstance(value.get('seal'), str)
                or not hmac.compare_digest(value['seal'], hmac.new(secret_path.read_bytes(), digest(unsigned).encode(), hashlib.sha256).hexdigest())
                or value['body_digest'] != digest(value['body']) or (expected is not None and actual != expected)):
            raise ValueError('identity/body')
        return value
    except (OSError, ValueError, KeyError, TypeError) as exc:
        count('cache', reason='unproven_or_corrupt')
        raise CacheIdentityError('physical_cache_unproven_or_corrupt') from exc


def cached(paths, expected, compute):
    """Single writer per immutable key. Corrupt/unknown records block, never re-certify."""
    folder = directory(paths, expected['kind'])
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (digest(expected)+'.json')
    descriptor = os.open(target.with_suffix('.lock'), os.O_CREAT | os.O_RDWR, 0o600)
    with span('cache'):
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                count('cache', reason='writer_busy')
                raise CacheIdentityError('physical_compute_writer_busy') from exc
            check_media(expected['media'])
            if target.exists():
                value = read(paths, target, expected)
                count('cache', hits=1)
                return value['body'], True
            count('cache', reason='identity_not_found')
            body = compute()
            check_media(expected['media'])
            # A computation exception leaves no result; atomic_json fsyncs and replaces a unique temp.
            value = {'identity': expected, 'body': body, 'body_digest': digest(body)}
            from codex_worker import key
            value['seal'] = hmac.new(key(paths), digest(value).encode(), hashlib.sha256).hexdigest()
            atomic_json(target, value)
            return body, False
        finally:
            os.close(descriptor)


def cached_value(paths, expected):
    """Read a proven cache row without running its producer; used before batched inference."""
    folder = directory(paths, expected['kind'])
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (digest(expected)+'.json')
    descriptor = os.open(target.with_suffix('.lock'), os.O_CREAT | os.O_RDWR, 0o600)
    with span('cache'):
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                count('cache', reason='writer_busy')
                raise CacheIdentityError('physical_compute_writer_busy') from exc
            check_media(expected['media'])
            if not target.exists():
                count('cache', reason='identity_not_found')
                return None, False
            value = read(paths, target, expected)
            check_media(expected['media'])
            count('cache', hits=1)
            return value['body'], True
        finally:
            os.close(descriptor)


def containing_asr(paths, expected):
    """Reuse a proven larger window with identical media, weights and inference parameters."""
    base = {k: v for k, v in expected.items() if k != 'bounds'}
    candidates = []
    with span('cache'):
        for path in directory(paths, 'asr').glob('*.json'):
            value = read(paths, path)
            actual = value['identity']
            if ({k: v for k, v in actual.items() if k != 'bounds'} == base
                    and actual['bounds'][0] <= expected['bounds'][0]
                    and actual['bounds'][1] >= expected['bounds'][1]):
                candidates.append(value)
        if candidates:
            value = min(candidates, key=lambda v: v['identity']['bounds'][1]-v['identity']['bounds'][0])
            check_media(expected['media'])
            count('cache', hits=1)
            return value['body']
    return None
