"""Read-only verification reuse, bounded to one call and checked before return."""
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps

from runtime_meta import CacheIdentityError, _sha256

_active = ContextVar('evidence_operation', default=None)


class Operation:
    def __init__(self):
        self.records = {}
        self.files = {}
        self.contexts = {}
        self.runtime_inputs = {}
        self.code_digests = {}

    def inputs(self, paths):
        key = str(paths.workspace_root)
        if key not in self.runtime_inputs:
            from project_registry import checked_project
            from runtime_meta import expected_identity
            state = paths.load_state()
            checked_project(paths, state)
            self.runtime_inputs[key] = (state, expected_identity(paths, state=state, include_ranges=False))
        return self.runtime_inputs[key]

    def context(self, paths, product_id, encoding_id, stage):
        key = (str(paths.workspace_root), product_id, encoding_id, stage)
        if key not in self.contexts:
            from product_checkpoints import _context
            from compute_metrics import span
            with span('stage_identity'):
                self.contexts[key] = _context(paths, product_id, encoding_id, stage)
        return deepcopy(self.contexts[key])

    def lookup(self, paths, artifact, product_id, encoding_id):
        row = self.records.get((str(paths.workspace_root), artifact))
        if row is None:
            return None
        _, _, value = row
        self.check(paths, artifact, row)
        identity = value['identity']
        if product_id is not None and identity['product_id'] != product_id:
            raise CacheIdentityError('stage_product_mismatch')
        if encoding_id is not None and identity['encoding_id'] != encoding_id:
            raise CacheIdentityError('stage_encoding_mismatch')
        return deepcopy(value)

    def remember(self, paths, artifact, raw_digest, expected, value):
        row = (raw_digest, expected, deepcopy(value))
        self.check(paths, artifact, row, check_context=False)
        self.records[(str(paths.workspace_root), artifact)] = row

    @staticmethod
    def check(paths, artifact, row, *, check_context=True):
        from product_checkpoints import context
        raw_digest, expected, value = row
        identity = value['identity']
        if _sha256(artifact) != raw_digest:
            raise CacheIdentityError('stage_changed_during_verification')
        if check_context and context(paths, identity['product_id'], identity['encoding_id'], identity['stage']) != expected:
            raise CacheIdentityError('stage_context_changed_during_verification')

    def watch(self, path):
        value = _sha256(path) if path.is_file() else None
        if path in self.files and self.files[path] != value:
            raise CacheIdentityError('stage_pointer_changed_during_verification')
        self.files[path] = value

    def check_files(self):
        for path, expected in self.files.items():
            if (_sha256(path) if path.is_file() else None) != expected:
                raise CacheIdentityError('stage_pointer_changed_during_verification')


def current_operation():
    active = _active.get()
    return active[0] if active is not None else None


def verified_operation(function):
    @wraps(function)
    def wrapped(paths, *args, **kwargs):
        active = _active.get()
        if active is not None:
            active[1][str(paths.workspace_root)] = paths
            return function(paths, *args, **kwargs)
        operation = Operation()
        path_objects = {str(paths.workspace_root): paths}
        token = _active.set((operation, path_objects))
        try:
            result = function(paths, *args, **kwargs)
            # A NEW snapshot after the read phase rehashes input/rules/code once;
            # the read-phase memo cannot validate itself or survive this boundary.
            fresh_token = _active.set((Operation(), path_objects))
            try:
                for (workspace, product_id, encoding_id, stage), expected in operation.contexts.items():
                    current = current_operation().context(path_objects[workspace], product_id, encoding_id, stage)
                    if current != expected:
                        raise CacheIdentityError('stage_context_changed_during_verification')
                for (workspace, artifact), row in operation.records.items():
                    operation.check(path_objects[workspace], artifact, row)
                operation.check_files()
            finally:
                _active.reset(fresh_token)
            return result
        finally:
            _active.reset(token)
    return wrapped
