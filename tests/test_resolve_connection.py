"""Synthetic lifecycle tests: no live Resolve or project writes."""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import resolve_connection as connection
import resolve_connection_doctor as doctor


def healthy():
    return SimpleNamespace(GetProjectManager=lambda: object())


class ResolveConnectionTests(unittest.TestCase):
    def call(self, api, route_list, **kwargs):
        with patch.object(connection, "load_api", return_value=api), patch.object(connection, "routes", return_value=route_list), patch.object(connection, "app_running", return_value=False):
            return connection.connect(retry_delay=0, status_path=None, **kwargs)

    def test_local_failure_tries_both_interfaces(self):
        handle = healthy()
        api = SimpleNamespace(scriptapp=Mock(side_effect=[None, None, None, handle]))
        self.assertIs(self.call(api, [("local", None), ("loopback", "127.0.0.1"), ("en0", "192.0.2.1"), ("en1", "192.0.2.2")]), handle)
        self.assertEqual(api.scriptapp.call_args.args, ("Resolve", "192.0.2.2"))

    def test_startup_retries_and_refreshes_addresses(self):
        handle = healthy()
        api = SimpleNamespace(scriptapp=Mock(side_effect=[None, handle]))
        with patch.object(connection, "load_api", return_value=api), patch.object(connection, "routes", side_effect=[[("en0", "192.0.2.1")], [("en0", "192.0.2.9")]]) as discovery:
            self.assertIs(connection.connect(attempts=2, retry_delay=0, status_path=None), handle)
        self.assertEqual(discovery.call_count, 2)
        self.assertEqual(api.scriptapp.call_args.args, ("Resolve", "192.0.2.9"))

    def test_stale_handle_must_pass_real_read(self):
        stale = SimpleNamespace(GetProjectManager=Mock(side_effect=RuntimeError("stale")))
        handle = healthy()
        api = SimpleNamespace(scriptapp=Mock(side_effect=[stale, handle]))
        self.assertIs(self.call(api, [("local", None), ("loopback", "127.0.0.1")]), handle)

    def test_close_then_reopen_does_not_cache_failed_or_old_handle(self):
        handle = healthy()
        api = SimpleNamespace(scriptapp=Mock(side_effect=[None, handle]))
        self.assertIsNone(self.call(api, [("local", None)], attempts=1))
        self.assertIs(self.call(api, [("local", None)], attempts=1), handle)
        self.assertEqual(api.scriptapp.call_count, 2)

    def test_explicit_host_does_not_silently_switch_to_another_machine(self):
        with patch.dict(connection.os.environ, {"RESOLVE_HOST": "192.0.2.7"}), patch.object(connection.subprocess, "run") as run:
            self.assertEqual(connection.routes(), [("local", None), ("RESOLVE_HOST", "192.0.2.7")])
        run.assert_not_called()

    def test_missing_interface_does_not_prevent_second_interface(self):
        with patch.dict(connection.os.environ, {"RESOLVE_HOST": ""}), patch.object(connection.subprocess, "run", side_effect=[OSError(), SimpleNamespace(stdout="192.0.2.8\n")]):
            self.assertEqual(connection.routes()[-1], ("en1", "192.0.2.8"))

    def test_bounded_failure_records_route_without_address(self):
        api = SimpleNamespace(scriptapp=Mock(return_value=None))
        with tempfile.TemporaryDirectory() as tmp, patch.object(connection, "load_api", return_value=api), patch.object(connection, "routes", return_value=[("en0", "192.0.2.1")]), patch.object(connection, "app_running", return_value=False):
            path = Path(tmp) / "status.json"
            self.assertIsNone(connection.connect(attempts=3, retry_delay=0, status_path=path))
            report = json.loads(path.read_text())
            self.assertEqual(report["reason"], "app_not_running")
            self.assertEqual(len(report["attempts"]), 3)
            self.assertNotIn("192.0.2.1", path.read_text())

    def test_missing_api_returns_clear_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(connection, "load_api", side_effect=ImportError()):
            path = Path(tmp) / "status.json"
            self.assertIsNone(connection.connect(status_path=path))
            self.assertEqual(json.loads(path.read_text())["reason"], "api_unavailable")

    def test_no_project_is_connected_but_not_ready(self):
        handle = SimpleNamespace(GetProjectManager=lambda: SimpleNamespace(GetCurrentProject=lambda: None))
        with patch.object(doctor, "connect", return_value=handle):
            report, code = doctor.check(Path("unused"))
        self.assertTrue(report["connected"])
        self.assertFalse(report["ready"])
        self.assertEqual(code, 1)

    def test_wrong_project_fails_read_only_preflight(self):
        project = SimpleNamespace(GetName=lambda: "Other", GetCurrentTimeline=lambda: None, GetTimelineCount=lambda: 0)
        handle = SimpleNamespace(GetProjectManager=lambda: SimpleNamespace(GetCurrentProject=lambda: project))
        with tempfile.TemporaryDirectory() as tmp, patch.object(doctor, "connect", return_value=handle):
            root = Path(tmp)
            (root / "PROJECT_STATE.json").write_text(json.dumps({"resolve": {"project_name": "Expected", "source_timeline": "Input", "target_timeline": "Output"}}))
            report, code = doctor.check(root)
        self.assertEqual((report["reason"], code), ("project_mismatch", 1))

    def test_native_hang_times_out_in_separate_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'PROJECT_STATE.json').write_text('{}')
            with patch.object(sys, "argv", ["doctor", "--workspace", tmp, "--timeout", "1"]), patch.object(doctor.subprocess, "run", side_effect=subprocess.TimeoutExpired("worker", 1)), patch.object(doctor, "record_check"), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(doctor.main(), 1)
        self.assertEqual(json.loads(output.getvalue())["reason"], "connection_check_timed_out")

    def test_timeout_does_not_leave_old_success_in_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text(json.dumps({"connected": True, "route": "en0", "reason": "connected"}))
            with patch.object(doctor, "STATUS_PATH", path):
                doctor.record_check({"ready": False, "reason": "connection_check_timed_out"})
            status = json.loads(path.read_text())
        self.assertIsNone(status["connected"])
        self.assertIsNone(status["route"])
        self.assertFalse(status["check_ready"])

    def test_worker_crash_is_reported_as_failure(self):
        child = SimpleNamespace(stdout="", returncode=-11)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'PROJECT_STATE.json').write_text('{}')
            with patch.object(sys, "argv", ["doctor", "--workspace", tmp]), patch.object(doctor.subprocess, "run", return_value=child), patch.object(doctor, "record_check"), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(doctor.main(), -11)
        self.assertEqual(json.loads(output.getvalue())["reason"], "connection_worker_failed")

    def test_all_project_entry_points_share_connector(self):
        import resolve_write_v2
        import validate_resolve_write_v2
        import export_marked_variants
        import inspect_timeline_for_export
        for module in (resolve_write_v2, validate_resolve_write_v2, export_marked_variants, inspect_timeline_for_export):
            self.assertIs(module.connect, connection.connect)


if __name__ == "__main__":
    unittest.main()
