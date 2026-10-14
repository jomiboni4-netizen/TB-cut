"""Offline ASR subprocess, bound JSON task; no cloud or Resolve access."""
import json
from pathlib import Path
import sys
from project_registry import digest
from asr_identity import model_identity


def main():
    task = json.loads(Path(sys.argv[1]).read_text())
    payload = {k: v for k, v in task.items() if k != 'task_digest'}
    if task.get('task_digest') != digest(payload):
        raise ValueError('asr_task_digest_invalid')
    weights = model_identity(task['model'])
    result = {'task_digest': task['task_digest'], 'model': weights}
    if task['operation'] == 'transcribe':
        from review_compute_cache import LazyModel
        from windowed_asr_v2 import load_model
        from registered_pipeline import transcribe_window
        model = LazyModel(lambda: load_model(weights['resolved_path']))
        result['words'] = transcribe_window(Path(task['media']), task['start'], task['end'], model)
    elif task['operation'] != 'identity':
        raise ValueError('asr_operation_invalid')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
