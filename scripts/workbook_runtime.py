"""Resolve the Node runtime pinned for workbook processing."""

from pathlib import Path
import shutil


def preferred_node() -> str:
    bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
    if bundled.is_file() and bundled.stat().st_mode & 0o111:
        return str(bundled)
    return shutil.which("node") or "node"
