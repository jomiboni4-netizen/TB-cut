"""Cooperative generator lock and fail-closed, non-transactional publication."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import shutil

from runtime_meta import CacheIdentityError, expected_identity, invalidation_path, metadata_path


@contextmanager
def generator_lock(paths):
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(paths.cache_dir, os.O_RDONLY)
    try:
        # Shared by title/subtitle generators; a competing invocation fails fast.
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def policy_snapshot(paths):
    files = [paths.repo_root / 'PROJECT_RULES.md', *sorted((paths.repo_root / 'config').glob('*.json'))]
    return [(str(p), hashlib.sha256(p.read_bytes()).digest()) for p in files]


class BuildSnapshot:
    def __init__(self, paths):
        self.paths = paths
        self.state_bytes = paths.state_path.read_bytes()
        self.state = json.loads(self.state_bytes)
        self.policy = policy_snapshot(paths)
        self.identity = expected_identity(paths, self.state)
        self.check()

    def check(self):
        if (self.paths.state_path.read_bytes() != self.state_bytes
                or policy_snapshot(self.paths) != self.policy
                or expected_identity(self.paths) != self.identity):
            raise CacheIdentityError('inputs_changed_during_generation')


def publish_pair(staged, staged_meta, output, *, before_commit=lambda: None, state_update=None):
    """Two independent renames, not a cross-file or power-loss transaction.

    A marker invalidates even byte-identical leftovers if rollback fails. It is
    retained on every caught publication failure and removed only after success.
    Readers must honor it. Recovery is regeneration, never marker-only removal.
    """
    sidecar = metadata_path(output)
    pairs = [(staged, output), (staged_meta, sidecar)]
    if state_update is not None:
        pairs.append(state_update)
    backups = {}
    for _, target in pairs:
        if target.is_symlink() or target.exists() and not target.is_file():
            raise CacheIdentityError('output_must_be_regular_file')
        if target.exists():
            backup = staged.parent / (target.name + '.previous')
            shutil.copyfile(target, backup)
            backups[target] = backup
    before_commit()
    marker = invalidation_path(output)
    marker.write_text('publication incomplete; regenerate from inputs\n')
    replaced = []
    try:
        for source, target in pairs:
            before_commit()
            os.replace(source, target)
            replaced.append(target)
        if state_update is None:
            before_commit()
        marker.unlink()
    except BaseException as publication_error:
        for target in reversed(replaced):
            try:
                if target in backups:
                    os.replace(backups[target], target)
                else:
                    target.unlink()
            except BaseException as rollback_error:
                if hasattr(publication_error, 'add_note'):
                    publication_error.add_note(f'rollback failed: {type(rollback_error).__name__}')
        raise
