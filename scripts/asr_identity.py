"""Local weights and actual inference settings; never download on identity lookup."""
from pathlib import Path
from runtime_meta import CacheIdentityError, _sha256
from project_registry import digest
from compute_metrics import span

_weights = {}

PARAMETERS = {'language': 'zh', 'beam_size': 5, 'vad_filter': True, 'word_timestamps': True,
              'condition_on_previous_text': False, 'device': 'cpu', 'compute_type': 'int8',
              'protocol': 'window-words-v3'}


def model_identity(name):
    try:
        from faster_whisper.utils import download_model
        folder = Path(name) if Path(name).is_dir() else Path(download_model(name, local_files_only=True))
        entries = [p for p in folder.iterdir() if p.is_file()]
        signature = [(str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ino) for p in sorted(entries)]
        fingerprint = digest(signature)
        with span('asr_weights_identity'):
            files = _weights.get(fingerprint)
            if files is None:
                files = {p.name: _sha256(p) for p in entries}
                after = [(str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ino) for p in sorted(entries)]
                if after != signature: raise ValueError('weights changed while hashing')
                _weights[fingerprint] = files
        if not {'model.bin', 'config.json', 'tokenizer.json'} <= set(files):
            raise ValueError('required weights missing')
        import faster_whisper, ctranslate2
        return {'name': name, 'resolved_path': str(folder.resolve()), 'weights_digest': digest(files), 'files': files,
                'libraries': {'faster_whisper': faster_whisper.__version__, 'ctranslate2': ctranslate2.__version__}}
    except Exception as exc:
        raise CacheIdentityError('local_asr_model_weights_unavailable') from exc
