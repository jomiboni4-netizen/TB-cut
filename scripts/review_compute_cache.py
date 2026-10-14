"""Reuse expensive computations; never reuse a semantic approval from this cache."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


class LazyModel:
    def __init__(self, factory):
        self.factory = factory
        self.model = None

    def get(self):
        if self.model is None:
            self.model = self.factory()
        return self.model


def probe_key(media: Path, detector_digest: str, ranges: list[str]) -> str:
    stat = media.stat()
    payload = {
        "version": 1, "media": str(media.resolve()),
        "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
        "detector": detector_digest, "ranges": ranges, "options": ["--summary"],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def valid_probe(report) -> bool:
    return (isinstance(report, dict)
            and type(report.get("totalSamples")) is int
            and type(report.get("personSamples")) is int
            and report["totalSamples"] > 0
            and 0 <= report["personSamples"] <= report["totalSamples"])


def cached_probe(cache_dir: Path, key: str, compute):
    path = cache_dir / f"{key}.json"
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("key") == key and valid_probe(cached.get("report")):
            return cached["report"], True
    except (OSError, ValueError, AttributeError):
        pass
    report = compute()
    if not valid_probe(report):
        raise ValueError("人物检测结果缺失或样本数无效；不能缓存或视为通过")
    cache_dir.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"key": key, "report": {
        "personSamples": report["personSamples"], "totalSamples": report["totalSamples"],
    }}), encoding="utf-8")
    temporary.replace(path)
    return report, False
