#!/usr/bin/env python3
"""Privacy-safe Xiaomi MiMo Responses API transport with bounded SSE diagnostics."""
from __future__ import annotations

import http.client
import json
import os
import socket
import ssl
import time
import uuid
from urllib.parse import urlsplit

ENDPOINT = "https://api.xiaomimimo.com/v1/responses"
MODEL = "mimo-v2.6-pro"


class ResponseAPIError(RuntimeError):
    def __init__(self, code: str, metrics: dict, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.metrics = metrics
        self.http_status = status


def _init_metrics(payload: dict, stage: str, request_id: str) -> dict:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {
        "request_id": request_id,
        "model": payload.get("model"),
        "stage": stage,
        "request_bytes": len(encoded),
        "http_status": None,
        "connect_seconds": None,
        "first_byte_seconds": None,
        "last_byte_after_seconds": None,
        "last_event_after_seconds": None,
        "response_seconds": None,
        "total_seconds": None,
        "stream_bytes": 0,
        "event_counts": {},
        "reasoning_delta_count": 0,
        "reasoning_delta_bytes": 0,
        "output_delta_count": 0,
        "output_delta_bytes": 0,
        "output_done_count": 0,
        "terminal_event_received": False,
        "terminal_event_type": None,
        "response_status": None,
        "response_id": None,
        "usage": None,
        "incomplete_reason": None,
        "error_type": None,
        "error_cause": None,
        "exception_phase": None,
        "discarded_partial_event": False,
    }


def _read_chunk(response, size: int = 4096) -> bytes:
    read1 = getattr(response, "read1", None)
    return read1(size) if callable(read1) else response.read(size)


def _event_stream(response, metrics: dict, request_started: float, deadline: float, sock=None):
    """Parse SSE by complete lines; never dispatch an event without its blank line."""
    buffer = bytearray()
    event_name = None
    data_lines: list[bytes] = []
    completed = None

    def process_line(raw_line: bytes):
        nonlocal event_name, data_lines, completed
        line = raw_line[:-1] if raw_line.endswith(b"\r") else raw_line
        if not line:
            if data_lines:
                kind, body = _dispatch_event(event_name, data_lines, metrics, request_started)
                event_name, data_lines = None, []
                if kind == "response.completed":
                    completed = body
                elif kind == "response.incomplete":
                    raise ResponseAPIError("response_incomplete", metrics, metrics["http_status"])
                elif kind in {"response.failed", "error"}:
                    raise ResponseAPIError("response_failed", metrics, metrics["http_status"])
            else:
                event_name = None
            return
        if line.startswith(b":"):
            return
        field, separator, value = line.partition(b":")
        if not separator:
            value = b""
        elif value.startswith(b" "):
            value = value[1:]
        if field == b"event":
            event_name = value.decode("utf-8", errors="strict")
        elif field == b"data":
            data_lines.append(value)

    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise ResponseAPIError("request_deadline_exceeded", metrics, metrics["http_status"])
        if sock is not None:
            sock.settimeout(remaining)
        chunk = _read_chunk(response)
        now = time.perf_counter()
        if chunk:
            if metrics["first_byte_seconds"] is None:
                metrics["first_byte_seconds"] = round(now - request_started, 3)
            metrics["last_byte_after_seconds"] = round(now - request_started, 3)
            metrics["stream_bytes"] += len(chunk)
            buffer.extend(chunk)
            while True:
                newline = buffer.find(b"\n")
                if newline < 0:
                    break
                process_line(bytes(buffer[:newline]))
                del buffer[:newline + 1]
            if completed is not None:
                # A complete terminal event is sufficient; do not wait for
                # the server to close the SSE connection after completion.
                break
        if time.perf_counter() >= deadline:
            raise ResponseAPIError("request_deadline_exceeded", metrics, metrics["http_status"])
        if not chunk:
            metrics["discarded_partial_event"] = bool(buffer or data_lines or event_name)
            break

    if completed is None:
        raise ResponseAPIError("stream_ended_without_response_completed", metrics, metrics["http_status"])
    response_obj = completed.get("response", completed)
    metrics["response_status"] = response_obj.get("status")
    metrics["response_id"] = response_obj.get("id")
    metrics["usage"] = response_obj.get("usage")
    details = response_obj.get("incomplete_details") or {}
    metrics["incomplete_reason"] = details.get("reason")
    if response_obj.get("model") != MODEL:
        raise ResponseAPIError("response_model_mismatch", metrics, metrics["http_status"])
    if response_obj.get("status") != "completed":
        raise ResponseAPIError("completed_event_has_noncompleted_status", metrics, metrics["http_status"])
    if not isinstance(response_obj.get("output_text"), str):
        response_obj["output_text"] = "".join(
            content.get("text", "")
            for item in response_obj.get("output", [])
            for content in item.get("content", [])
            if content.get("type") == "output_text" and isinstance(content.get("text"), str)
        )
    if not response_obj["output_text"].strip():
        raise ResponseAPIError("completed_response_missing_output_text", metrics, metrics["http_status"])
    # The transport may include reasoning items in the completed response
    # object. Keep only final answer content in any persisted response artifact.
    if isinstance(response_obj.get("output"), list):
        response_obj["output"] = [
            {**item, "content": [part for part in item.get("content", [])
                                 if part.get("type") != "reasoning_text"]}
            for item in response_obj["output"]
            if item.get("type") != "reasoning"
        ]
    return response_obj


def _dispatch_event(name: str | None, data_lines: list[bytes], metrics: dict,
                    request_started: float):
    if not data_lines:
        return None, None
    raw = b"\n".join(data_lines).decode("utf-8", errors="strict")
    if raw.strip() == "[DONE]":
        kind, body = "stream.done_marker", {}
    else:
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            metrics["error_type"] = type(exc).__name__
            metrics["error_cause"] = "invalid_sse_json"
            raise ResponseAPIError("invalid_sse_json", metrics, metrics["http_status"]) from exc
        kind = body.get("type") or name or "unknown"
    counts = metrics["event_counts"]
    counts[kind] = counts.get(kind, 0) + 1
    metrics["last_event_after_seconds"] = round(time.perf_counter() - request_started, 3)
    if kind == "response.reasoning_text.delta":
        metrics["reasoning_delta_count"] += 1
        metrics["reasoning_delta_bytes"] += len(str(body.get("delta") or "").encode("utf-8"))
    elif kind == "response.output_text.delta":
        metrics["output_delta_count"] += 1
        metrics["output_delta_bytes"] += len(str(body.get("delta") or "").encode("utf-8"))
    elif kind == "response.output_text.done":
        metrics["output_done_count"] += 1
    if kind in {"response.completed", "response.incomplete", "response.failed", "error"}:
        metrics["terminal_event_received"] = True
        metrics["terminal_event_type"] = kind
        response_obj = body.get("response") or body
        metrics["response_status"] = response_obj.get("status")
        metrics["response_id"] = response_obj.get("id")
        metrics["usage"] = response_obj.get("usage")
        details = response_obj.get("incomplete_details") or {}
        metrics["incomplete_reason"] = details.get("reason")
        if kind in {"response.failed", "error"}:
            error = body.get("error") or response_obj.get("error") or {}
            metrics["error_cause"] = error.get("code") or error.get("type") or "api_terminal_error"
    return kind, body


def create_response(payload: dict, timeout: int = 240, *, stage: str = "unknown",
                    request_id: str | None = None):
    key = os.environ.get("MIMO_API_KEY")
    if not key:
        raise ResponseAPIError("MIMO_API_KEY_missing", {}, None)
    request_id = request_id or uuid.uuid4().hex
    metrics = _init_metrics(payload, stage, request_id)
    parsed = urlsplit(ENDPOINT)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    started = time.perf_counter()
    phase = "connect"
    request_started = None
    conn = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout,
                                       context=ssl.create_default_context())
    try:
        connect_started = time.perf_counter()
        conn.connect()
        metrics["connect_seconds"] = round(time.perf_counter() - connect_started, 3)
        conn.sock.settimeout(timeout)
        phase = "request_write"
        conn.request("POST", parsed.path, body=body,
                     headers={"api-key": key, "Content-Type": "application/json",
                              "Accept": "text/event-stream"})
        request_started = time.perf_counter()
        deadline = request_started + timeout
        phase = "response_read"
        response = conn.getresponse()
        metrics["http_status"] = response.status
        if response.status < 200 or response.status >= 300:
            metrics["terminal_event_type"] = "http_error"
            response.read(4096)
            raise ResponseAPIError("http_error", metrics, response.status)
        result = _event_stream(response, metrics, request_started, deadline, conn.sock)
        now = time.perf_counter()
        metrics["response_seconds"] = round(now - request_started, 3)
        metrics["total_seconds"] = round(now - started, 3)
        return result, metrics
    except ResponseAPIError as exc:
        now = time.perf_counter()
        metrics["total_seconds"] = round(now - started, 3)
        metrics["response_seconds"] = round(now - request_started, 3) if request_started else None
        if exc.code == "request_deadline_exceeded":
            metrics.update({"error_type": "ResponseDeadlineExceeded", "error_cause": "total_response_deadline",
                            "exception_phase": "response_read"})
        elif exc.code == "stream_ended_without_response_completed":
            metrics.update({"error_type": "IncompleteSSEStream",
                            "error_cause": "partial_event_at_eof" if metrics.get("discarded_partial_event")
                            else "eof_without_terminal_event", "exception_phase": "response_read"})
        elif exc.code == "response_incomplete":
            metrics.update({"error_type": "TerminalResponseIncomplete",
                            "error_cause": metrics.get("incomplete_reason") or "incomplete_terminal_event",
                            "exception_phase": "response_read"})
        elif exc.code == "response_failed":
            metrics.update({"error_type": "TerminalResponseFailed", "exception_phase": "response_read"})
        elif exc.code == "http_error":
            metrics.update({"error_type": "HTTPStatusError", "exception_phase": "http_status"})
        if not exc.metrics:
            exc.metrics = metrics
        else:
            exc.metrics.update(metrics)
        raise
    except Exception as exc:
        metrics["error_type"] = type(exc).__name__
        metrics["exception_phase"] = phase
        metrics["error_cause"] = "socket_idle_timeout" if isinstance(exc, (socket.timeout, TimeoutError)) else phase
        now = time.perf_counter()
        metrics["total_seconds"] = round(now - started, 3)
        metrics["response_seconds"] = round(now - request_started, 3) if request_started else None
        raise ResponseAPIError(type(exc).__name__, metrics, metrics["http_status"]) from exc
    except BaseException as exc:
        metrics["error_type"] = type(exc).__name__
        metrics["exception_phase"] = phase
        metrics["error_cause"] = "interrupted_before_terminal_event"
        now = time.perf_counter()
        metrics["total_seconds"] = round(now - started, 3)
        metrics["response_seconds"] = round(now - request_started, 3) if request_started else None
        try:
            exc.mimo_metrics = metrics
        except Exception:
            pass
        raise
    finally:
        conn.close()
