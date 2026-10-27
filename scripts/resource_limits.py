"""Project-wide bounded request slots, including across worker processes."""
from contextlib import contextmanager
import fcntl
import os
import math
from pathlib import Path
import time
from runtime_meta import CacheIdentityError

VISION_REQUEST_LIMIT = 4


@contextmanager
def request_slot(directory, timeout, *, limit=VISION_REQUEST_LIMIT):
    if (type(limit) is not int or not 1 <= limit <= VISION_REQUEST_LIMIT
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
        raise CacheIdentityError('vision_request_slot_parameters_invalid')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    acquired = None
    try:
        while acquired is None:
            for index in range(limit):
                descriptor = os.open(directory / f'{index}.lock', os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(descriptor)
                else:
                    acquired = descriptor
                    break
            if acquired is None:
                if time.monotonic() >= deadline:
                    raise CacheIdentityError('vision_request_queue_timeout')
                time.sleep(min(.02, max(0., deadline-time.monotonic())))
        yield
    finally:
        if acquired is not None:
            os.close(acquired)
