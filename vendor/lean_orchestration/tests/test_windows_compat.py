"""Native Windows safety regressions; all process effects are faked."""
import ctypes
import importlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from lo_core.config import load_config_file
from lo_exec import ledger, supervise

dispatch_mod = importlib.import_module("lo_exec.dispatch")


class WindowsCompatTests(unittest.TestCase):
    def test_pid_ctypes_never_signals(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 123
        def exit_code(handle, pointer):
            pointer._obj.value = 259
            return 1
        kernel.GetExitCodeProcess.side_effect = exit_code
        with patch.object(ledger, "_IS_WINDOWS", True, create=True), patch.object(ledger, "psutil", None, create=True), patch.object(ctypes, "WinDLL", return_value=kernel, create=True), patch.object(os, "kill", side_effect=AssertionError("Windows liveness must not signal")):
            self.assertTrue(ledger.pid_alive(42))
        kernel.OpenProcess.assert_called_once_with(0x1000, False, 42)
        kernel.CloseHandle.assert_called_once_with(123)

    def test_pid_psutil(self):
        fake = Mock()
        fake.pid_exists.return_value = False
        with patch.object(ledger, "_IS_WINDOWS", True, create=True), patch.object(ledger, "psutil", fake, create=True), patch.object(os, "kill", side_effect=AssertionError("Windows liveness must not signal")):
            self.assertFalse(ledger.pid_alive(42))
        fake.pid_exists.assert_called_once_with(42)

    def test_start_token_windows_psutil(self):
        fake = Mock()
        fake.Process.return_value.create_time.return_value = 1700000000.1234567
        with patch.object(ledger, "_IS_WINDOWS", True), patch.object(ledger, "psutil", fake), patch.object(subprocess, "check_output", side_effect=AssertionError("Windows must not spawn ps")):
            self.assertEqual(ledger.process_start_token(42), "1700000000.123457")
        fake.Process.assert_called_once_with(42)

    def test_start_token_windows_ctypes(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 123

        def process_times(handle, created, exited, kernel_time, user_time):
            created._obj.dwHighDateTime = 1
            created._obj.dwLowDateTime = 7
            return 1

        kernel.GetProcessTimes.side_effect = process_times
        with patch.object(ledger, "_IS_WINDOWS", True), patch.object(ledger, "psutil", None), patch.object(ctypes, "WinDLL", return_value=kernel, create=True) as windll, patch.object(subprocess, "check_output", side_effect=AssertionError("Windows must not spawn ps")):
            self.assertEqual(ledger.process_start_token(42), "4294967303")
        windll.assert_called_once_with("kernel32", use_last_error=True)
        kernel.OpenProcess.assert_called_once_with(0x1000, False, 42)
        kernel.GetProcessTimes.assert_called_once()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_start_token_windows_failures(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 123
        kernel.GetProcessTimes.return_value = 0
        fake = Mock()
        fake.Process.side_effect = PermissionError("denied")
        with patch.object(ledger, "_IS_WINDOWS", True), patch.object(subprocess, "check_output", side_effect=AssertionError("Windows must not spawn ps")):
            with patch.object(ledger, "psutil", fake):
                self.assertIsNone(ledger.process_start_token(42))
            with patch.object(ledger, "psutil", None), patch.object(ctypes, "WinDLL", return_value=kernel, create=True):
                self.assertIsNone(ledger.process_start_token(42))
                kernel.CloseHandle.assert_called_once_with(123)
                kernel.reset_mock()
                kernel.OpenProcess.return_value = 0
                self.assertIsNone(ledger.process_start_token(42))
                kernel.CloseHandle.assert_not_called()

    def test_start_token_posix_unchanged(self):
        with patch.object(ledger, "_IS_WINDOWS", False), patch.object(subprocess, "check_output", return_value=b"  Sun Nov 22 12:34:56 2026\n") as output:
            self.assertEqual(ledger.process_start_token(42), "Sun Nov 22 12:34:56 2026")
        output.assert_called_once_with(["ps", "-p", "42", "-o", "lstart="], stderr=subprocess.DEVNULL, timeout=5)

    def test_stop_psutil(self):
        parent, child = Mock(), Mock()
        parent.children.return_value = [child]
        fake = Mock()
        fake.Process.return_value = parent
        fake.wait_procs.return_value = ([parent], [child])
        fake.NoSuchProcess = ProcessLookupError
        fake.AccessDenied = PermissionError
        with patch.object(supervise, "_IS_WINDOWS", True, create=True), patch.object(supervise, "psutil", fake, create=True), patch.object(os, "killpg", side_effect=AssertionError("Windows must not killpg"), create=True):
            supervise._kill_group(42, 3)
        parent.children.assert_called_once_with(recursive=True)
        parent.terminate.assert_called_once_with()
        child.terminate.assert_called_once_with()
        fake.wait_procs.assert_called_once_with([child, parent], timeout=3)
        child.kill.assert_called_once_with()
        parent.kill.assert_not_called()

    def test_stop_taskkill(self):
        fake_process = Mock()
        fake_process.wait.side_effect = subprocess.TimeoutExpired("taskkill", 3)
        with patch.object(supervise, "_IS_WINDOWS", True, create=True), patch.object(supervise, "psutil", None, create=True), patch("shutil.which", return_value="C:/Windows/taskkill.exe"), patch.object(subprocess, "Popen", return_value=fake_process) as popen, patch.object(subprocess, "run") as run, patch.object(os, "killpg", side_effect=AssertionError("Windows must not killpg"), create=True):
            supervise._kill_group(42, 3)
        self.assertEqual(popen.call_args.args[0], ["C:/Windows/taskkill.exe", "/PID", "42", "/T"])
        fake_process.wait.assert_called_once_with(timeout=3)
        self.assertEqual(run.call_args.args[0], ["C:/Windows/taskkill.exe", "/PID", "42", "/T", "/F"])

    def test_launch_platform_kwargs(self):
        for windows in (True, False):
            with self.subTest(windows=windows), patch.object(supervise, "_IS_WINDOWS", windows, create=True), patch.object(subprocess, "Popen") as popen:
                supervise._popen(["executor"], cwd="cwd", env={}, log_fh=None)
                kwargs = popen.call_args.kwargs
                if windows:
                    self.assertEqual(kwargs.get("creationflags"), getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200))
                    self.assertNotIn("start_new_session", kwargs)
                else:
                    self.assertIs(kwargs["start_new_session"], True)

    def test_dispatch_platform_kwargs(self):
        for windows in (True, False):
            with tempfile.TemporaryDirectory() as directory, self.subTest(windows=windows), patch.object(dispatch_mod, "_IS_WINDOWS", windows, create=True), patch.object(dispatch_mod, "run_gate", return_value={"decision": "PASS", "task": "task"}), patch.object(dispatch_mod, "capture_git_base", return_value={}), patch.object(subprocess, "Popen", return_value=Mock(pid=42)) as popen:
                result = dispatch_mod.dispatch(repo=directory, brief_text="brief", workdir=directory, executor="fake", data_dir=directory, config={"executors": {"fake": {"argv": ["fake"]}}})
                self.assertTrue(result["ok"])
                kwargs = popen.call_args.kwargs
                if windows:
                    self.assertEqual(kwargs.get("creationflags"), getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200))
                    self.assertNotIn("start_new_session", kwargs)
                else:
                    self.assertIs(kwargs["start_new_session"], True)

    def test_bom_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_bytes(b'\xef\xbb\xbf{"watch_interval_s": 17}')
            self.assertEqual(load_config_file(str(path))["watch_interval_s"], 17)

    def test_bom_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            path.write_bytes(b'\xef\xbb\xbf{"event":"dispatched","id":"bom"}\n')
            self.assertEqual(ledger.read_events(str(path)), [{"event": "dispatched", "id": "bom"}])


if __name__ == "__main__":
    unittest.main()
