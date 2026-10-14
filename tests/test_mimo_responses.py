import json
import time
import unittest
from io import BytesIO

from scripts.mimo_responses import ResponseAPIError, _event_stream, _init_metrics


def event(name, value):
    return (f"event: {name}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n").encode()


def completed(output='{"ok":true}'):
    return {"type": "response.completed", "response": {
        "status": "completed", "model": "mimo-v2.6-pro", "id": "resp_synthetic",
        "output": [{"content": [{"type": "output_text", "text": output}]}],
        "usage": {"input_tokens": 2, "output_tokens": 3}}}


class FragmentedResponse:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def read1(self, _size):
        return next(self.chunks, b"")


class DelayedResponse:
    def __init__(self, first):
        self.first = first
        self.n = 0

    def read1(self, _size):
        self.n += 1
        if self.n == 1:
            return self.first
        time.sleep(0.02)
        return b""


class ResponsesStreamTests(unittest.TestCase):
    def metrics(self):
        return _init_metrics({"model": "mimo-v2.6-pro"}, "B", "synthetic-id")

    def parse(self, stream):
        started = time.perf_counter()
        return _event_stream(stream, self.metrics(), started, started + 2)

    def test_completed_event_is_required_and_returns_text(self):
        payload = completed()
        payload["response"]["output"].insert(0, {"type": "reasoning", "content": [
            {"type": "reasoning_text", "text": "private synthetic reasoning"}]})
        result = self.parse(BytesIO(event("response.completed", payload)))
        self.assertEqual(result["output_text"], '{"ok":true}')
        self.assertNotIn("reasoning", [row.get("type") for row in result["output"]])
        self.assertNotIn("private synthetic reasoning", json.dumps(result))

    def test_reasoning_deltas_then_timeout_are_counted_not_saved(self):
        metrics = self.metrics()
        started = time.perf_counter()
        data = event("response.reasoning_text.delta", {
            "type": "response.reasoning_text.delta", "delta": "synthetic reasoning"})
        with self.assertRaisesRegex(ResponseAPIError, "request_deadline_exceeded") as raised:
            _event_stream(DelayedResponse(data), metrics, started, started + 0.01)
        self.assertEqual(raised.exception.metrics["reasoning_delta_count"], 1)
        self.assertGreater(raised.exception.metrics["reasoning_delta_bytes"], 0)
        self.assertNotIn("synthetic reasoning", json.dumps(metrics))
        self.assertFalse(metrics["terminal_event_received"])

    def test_output_delta_without_terminal_is_not_a_result(self):
        metrics = self.metrics()
        data = event("response.output_text.delta", {
            "type": "response.output_text.delta", "delta": '{"partial":true}'})
        with self.assertRaisesRegex(ResponseAPIError, "stream_ended_without_response_completed"):
            _event_stream(BytesIO(data), metrics, time.perf_counter(), time.perf_counter() + 2)
        self.assertEqual(metrics["output_delta_count"], 1)
        self.assertFalse(metrics["terminal_event_received"])

    def test_incomplete_max_tokens_records_reason(self):
        metrics = self.metrics()
        payload = {"type": "response.incomplete", "response": {
            "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}}
        with self.assertRaisesRegex(ResponseAPIError, "response_incomplete"):
            _event_stream(BytesIO(event("response.incomplete", payload)), metrics,
                          time.perf_counter(), time.perf_counter() + 2)
        self.assertEqual(metrics["incomplete_reason"], "max_output_tokens")
        self.assertTrue(metrics["terminal_event_received"])

    def test_failure_terminal_is_rejected(self):
        metrics = self.metrics()
        payload = {"type": "response.failed", "response": {"status": "failed"}}
        with self.assertRaisesRegex(ResponseAPIError, "response_failed"):
            _event_stream(BytesIO(event("response.failed", payload)), metrics,
                          time.perf_counter(), time.perf_counter() + 2)

    def test_utf8_and_sse_event_boundaries_can_split_across_chunks(self):
        # Multi-line data fields are joined with LF by SSE; JSON permits that whitespace.
        raw_json = json.dumps(completed("{\"title\":\"羊毛\"}"), ensure_ascii=False, indent=2)
        wire = ("event: response.completed\n" + "".join(
            "data: " + line + "\n" for line in raw_json.splitlines()) + "\n").encode()
        chunks = [wire[i:i + 3] for i in range(0, len(wire), 3)]
        result = self.parse(FragmentedResponse(chunks))
        self.assertEqual(result["output_text"], '{"title":"羊毛"}')

    def test_partial_event_at_eof_is_discarded(self):
        metrics = self.metrics()
        partial = b'event: response.completed\ndata: {"type":"response.completed"}'
        with self.assertRaisesRegex(ResponseAPIError, "stream_ended_without_response_completed"):
            _event_stream(BytesIO(partial), metrics, time.perf_counter(), time.perf_counter() + 2)
        self.assertTrue(metrics["discarded_partial_event"])


if __name__ == "__main__":
    unittest.main()
