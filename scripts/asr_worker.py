"""Offline ASR worker with one-shot and persistent JSON-lines protocols."""
import argparse
from functools import lru_cache
import json
from pathlib import Path
import sys
from project_registry import digest
from asr_identity import model_identity


@lru_cache(maxsize=2)
def _model(weights_digest, resolved_path):
    from windowed_asr_v2 import load_model
    return load_model(resolved_path, weights_digest)


def process(task):
    payload = {k: v for k, v in task.items() if k != 'task_digest'}
    if task.get('task_digest') != digest(payload):
        raise ValueError('asr_task_digest_invalid')
    weights = model_identity(task['model'])
    result = {'task_digest': task['task_digest'], 'model': weights}
    if task['operation'] in ('preload', 'transcribe'):
        model = _model(weights['weights_digest'], weights['resolved_path'])
        if task['operation'] == 'preload':
            result['status'] = 'PRELOADED'
            return result
        from registered_pipeline import transcribe_window
        from review_compute_cache import LazyModel
        result['words'] = transcribe_window(Path(task['media']), task['start'], task['end'],
                                             LazyModel(lambda: model))
    elif task['operation'] != 'identity':
        raise ValueError('asr_operation_invalid')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('request_file', nargs='?')
    parser.add_argument('--stdio', action='store_true')
    args = parser.parse_args()
    if args.stdio:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                result = process(json.loads(line))
            except Exception as exc:
                # Keep the worker alive for independent later requests while
                # returning a bounded, non-content-bearing failure.
                result = {'task_digest': '', 'error': 'offline_asr_worker_failed',
                          'error_type': type(exc).__name__}
            print(json.dumps(result, ensure_ascii=False), flush=True)
        return
    if not args.request_file:
        parser.error('request_file_required_without_stdio')
    task = json.loads(Path(args.request_file).read_text())
    print(json.dumps(process(task), ensure_ascii=False))


if __name__ == '__main__':
    main()
