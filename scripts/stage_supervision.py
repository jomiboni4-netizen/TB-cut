"""Observe long-running stages without interrupting or replaying write effects."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import threading
from project_registry import atomic_json
from runtime_meta import CacheIdentityError

HEARTBEAT_SECONDS = 10.


def computation_timeout(paths, stage):
    value = paths.load_state().get('runtime', {}).get('stage_timeout_seconds', {}).get(stage, 7200)
    if type(value) not in (int, float) or not 1 <= value <= 86400:
        raise CacheIdentityError('computation_stage_timeout_invalid')
    return value


@contextmanager
def heartbeat(path, event, *, interval=HEARTBEAT_SECONDS):
    """Separate file avoids contending with product publication workspace locks."""
    stopped = threading.Event()
    def publish():
        atomic_json(Path(path), {'event_id': event['event_id'], 'stage': event['stage'],
                                'observed_at': datetime.now(timezone.utc).isoformat()})
    def worker():
        while not stopped.wait(interval):
            try:
                publish()
            except Exception as exc:
                event['heartbeat_error'] = type(exc).__name__
                return
    publish()
    thread = threading.Thread(target=worker, name='stage-heartbeat', daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=1.)
