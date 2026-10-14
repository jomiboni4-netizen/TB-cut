"""Shared, read-only Resolve connection discovery. Never replay project writes."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
MOD = API + "/Modules"
LIB = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
STATUS_PATH = Path(__file__).resolve().parents[1] / ".cache/resolve_connection_status.json"


def load_api():
    os.environ.setdefault("RESOLVE_SCRIPT_API", API)
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", LIB)
    if MOD not in sys.path:
        sys.path.insert(0, MOD)
    return importlib.import_module("DaVinciResolveScript")


def routes():
    """Refresh addresses on every attempt; never reuse a saved IP or native handle."""
    result = [("local", None)]
    host = os.environ.get("RESOLVE_HOST", "").strip()
    if host:
        return result + [("RESOLVE_HOST", host)]
    result.append(("loopback", "127.0.0.1"))
    seen = {"127.0.0.1"}
    for interface in ("en0", "en1"):
        try:
            found = subprocess.run(
                ["/usr/sbin/ipconfig", "getifaddr", interface],
                capture_output=True, text=True, check=False, timeout=2,
            ).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            continue
        if found and found not in seen:
            result.append((interface, found))
            seen.add(found)
    return result


def app_running():
    try:
        return subprocess.run(
            ["/usr/bin/pgrep", "-x", "Resolve"],
            capture_output=True, check=False, timeout=2,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return None


def save_status(status, path):
    if path is None:
        return
    # Logs are disposable; a log-write failure must not mask a working connection.
    temp = None
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
    except OSError:
        if temp is not None:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass


def connect(*, attempts=4, retry_delay=1.0, status_path=STATUS_PATH):
    """Return a freshly checked handle or None; retry discovery only, never writes.

    Retry counts are bounded. Native API calls themselves have no Python timeout;
    use the separate doctor process for a hard wall-clock timeout.
    """
    if attempts < 1 or retry_delay < 0:
        raise ValueError("attempts must be positive and retry_delay nonnegative")
    status = {"schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
              "connected": False, "route": None, "attempts": [], "reason": None}
    try:
        api = load_api()
    except Exception as exc:
        status.update(reason="api_unavailable", error_type=type(exc).__name__)
        save_status(status, status_path)
        return None
    for number in range(1, attempts + 1):
        for label, address in routes():
            attempt = {"round": number, "route": label}
            try:
                resolve = api.scriptapp("Resolve", address) if address else api.scriptapp("Resolve")
                # A stale native object can exist but fail its first real method call.
                healthy = resolve is not None and resolve.GetProjectManager() is not None
            except Exception as exc:
                healthy = False
                attempt["error_type"] = type(exc).__name__
            attempt["healthy"] = healthy
            status["attempts"].append(attempt)
            if healthy:
                status.update(connected=True, route=label, reason="connected")
                save_status(status, status_path)
                return resolve
        if number < attempts:
            time.sleep(retry_delay)
    running = app_running()
    status.update(app_running=running, reason="app_not_running" if running is False else "api_not_ready_or_inaccessible")
    save_status(status, status_path)
    return None
