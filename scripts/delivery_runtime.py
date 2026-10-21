"""Scoped immutable delivery stages and explicit time accounting; no media policy."""
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
import uuid
from project_registry import atomic_json, digest
from runtime_meta import _sha256

CATEGORIES = {'compute', 'editorial', 'development', 'waiting', 'verification'}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


class Ledger:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder/'events.jsonl'

    def emit(self, **row):
        row = dict(wall=time.time(), monotonic_ns=time.monotonic_ns(), **row)
        with self.path.open('a', encoding='utf-8') as out:
            fcntl.flock(out, fcntl.LOCK_EX)
            out.write(json.dumps(row, ensure_ascii=False)+'\n')
        return row

    def begin(self, name, category, *, repair=False):
        require(category in CATEGORIES, 'unknown_time_category')
        token = uuid.uuid4().hex
        self.emit(event='START', token=token, name=name, category=category, repair=repair)
        return token

    def end(self, token, *, status='PASS', **extra):
        rows = self.rows()
        starts = [r for r in rows if r.get('token') == token and r.get('event') == 'START']
        require(len(starts) == 1 and not any(r.get('token') == token and r.get('event') == 'END' for r in rows),
                'time_span_missing_or_already_closed')
        elapsed = (time.monotonic_ns()-starts[0]['monotonic_ns'])/1e9
        self.emit(event='END', token=token, status=status, seconds=elapsed, **extra)

    @contextmanager
    def span(self, name, category='compute', *, repair=False):
        token = self.begin(name, category, repair=repair)
        try:
            yield
        except BaseException as exc:
            self.end(token, status='FAILED', error=type(exc).__name__)
            raise
        else:
            self.end(token)

    def rows(self):
        if not self.path.exists():
            return []
        with self.path.open(encoding='utf-8') as source:
            fcntl.flock(source,fcntl.LOCK_SH)
            return [json.loads(line) for line in source.read().splitlines()]

    def summary(self, now=None):
        rows = self.rows(); now = time.time() if now is None else now
        if not rows:
            return dict(total_wall_seconds=0, observed_union_seconds=0, unattributed_seconds=0, categories={}, open_spans=[])
        starts = {r['token']: r for r in rows if r.get('event') == 'START'}
        intervals = []; categories = {}; closed = set(); repairs = 0
        for row in rows:
            if row.get('event') != 'END':
                continue
            start = starts[row['token']]; closed.add(row['token'])
            # Wall intervals identify concurrency; monotonic duration measures work.
            intervals.append((start['wall'], row['wall']))
            cat = categories.setdefault(start['category'], dict(inclusive_seconds=0, calls=0, failures=0))
            cat['inclusive_seconds'] += row['seconds']; cat['calls'] += 1; cat['failures'] += row['status'] == 'FAILED'
            repairs += bool(start.get('repair'))
        merged = []
        for lo, hi in sorted(intervals):
            if merged and lo <= merged[-1][1]: merged[-1][1] = max(merged[-1][1], hi)
            else: merged.append([lo, hi])
        covered = sum(hi-lo for lo, hi in merged); total = max(0, now-rows[0]['wall'])
        return dict(total_wall_seconds=total, observed_union_seconds=covered,
                    unattributed_seconds=max(0, total-covered), categories=categories,
                    repair_spans=repairs, open_spans=sorted(set(starts)-closed),
                    cache_hits=sum(r.get('event') == 'CACHE_HIT' for r in rows),
                    note='Category times are inclusive, may overlap; gaps remain unattributed, never invented as compute or waiting.')


class Store:
    def __init__(self, paths, scope, ledger):
        self.paths = paths; self.scope = scope; self.ledger = ledger
        self.folder = paths.workspace_root/'delivery'/'stages'
        self.folder.mkdir(parents=True, exist_ok=True)

    def identity(self, stage, inputs, producers):
        return dict(contract='delivery-stage-v1', scope=self.scope, stage=stage, inputs=inputs,
                    producers={name: _sha256(self.paths.repo_root/'scripts'/name) for name in producers})

    def load(self, path, expected=None):
        record = json.loads(path.read_text()); unsigned = {k:v for k,v in record.items() if k != 'seal'}
        secret = (self.paths.project_records_dir/'worker_seal.key').read_bytes()
        require(record['body_digest'] == digest(record['body']) and path.stem == digest(record['identity'])
                and hmac.compare_digest(record.get('seal',''), hmac.new(secret,digest(unsigned).encode(),hashlib.sha256).hexdigest())
                and (expected is None or record['identity'] == expected), 'delivery_checkpoint_corrupt')
        return record['body']

    def stage(self, name, inputs, producers, compute, *, category='compute', repair=False):
        before = self.identity(name, inputs, producers); target = self.folder/(digest(before)+'.json')
        with (self.folder/(name+'.lock')).open('a') as lock:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc: raise ValueError('delivery_stage_busy:'+name) from exc
            pointer = self.folder/(name+'.current')
            if target.exists():
                result = self.load(target, before)
                self.ledger.emit(event='CACHE_HIT', name=name, identity=digest(before))
            else:
                if pointer.exists():
                    self.ledger.emit(event='INVALIDATED', name=name, previous=pointer.read_text(), current=digest(before), reason='dependency_or_stage_producer_changed')
                with self.ledger.span(name, category, repair=repair):
                    result = compute()
                    require(self.identity(name, inputs, producers) == before, 'delivery_producer_changed_during_execution')
                    record = dict(identity=before, body=result, body_digest=digest(result))
                    from codex_worker import key
                    record['seal'] = hmac.new(key(self.paths), digest(record).encode(),hashlib.sha256).hexdigest()
                    atomic_json(target, record)
            pointer.write_text(digest(before))
            return result
