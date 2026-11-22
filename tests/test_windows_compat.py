"""Windows portability regressions; all writes confined to disposable fixtures."""
import importlib.util
import ntpath
import os
from pathlib import Path, PureWindowsPath
import tempfile
import unittest
from unittest import mock

from support import ROOT


def load(name, relative):
    root = Path(os.environ.get("W2_BEFORE_ROOT", str(ROOT)))
    spec = importlib.util.spec_from_file_location(name, root / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WindowsCompatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = load("w2_state", "state.py")
        self.night = load("w2_night", "night.py")
        self.df = load("w2_df", "scripts/df_local_patches/__init__.py")

    def test_bom_state_load(self):
        path = self.root / "state.json"
        path.write_text('{"saved": true}', encoding="utf-8-sig")
        with mock.patch.object(self.state, "state_path", return_value=path):
            self.assertEqual(self.state.load_state(), {"saved": True})

    def test_bom_night_load(self):
        (self.root / "night.json").write_text('{"saved": true}', encoding="utf-8-sig")
        self.assertEqual(self.night.load_night_json(self.root), {"saved": True})

    def test_windows_child_environment_allowlist(self):
        keys = "PATH HOME SYSTEMROOT USERPROFILE HOMEDRIVE HOMEPATH TEMP TMP TMPDIR APPDATA LOCALAPPDATA SYSTEMDRIVE WINDIR COMSPEC PATHEXT LANG LC_ALL".split()
        expected = {key: "fixture-" + key for key in keys}
        with mock.patch.dict(os.environ, {**expected, "GH_TOKEN": "fake-token", "OPENAI_API_KEY": "fake-key", "UNRELATED": "no"}, clear=True):
            self.assertEqual(self.df._child_env(), {**expected, "PYTHONDONTWRITEBYTECODE": "1"})

    def test_appdata_refused_even_inside_allowed_session(self):
        home = self.root / "home"
        home.mkdir()
        with mock.patch.object(self.df, "_user_home", return_value=home), mock.patch.object(self.df, "_hermes_home", return_value=self.root / "hermes"), mock.patch.object(self.df, "_is_git_work_tree", return_value=True):
            for target in (home / "AppData", home / "AppData" / "Roaming" / "credentials"):
                with self.subTest(target=target):
                    self.assertIn("protected path", self.df._refuse_df_init_target(target) or "")

    def test_windows_case_insensitive_appdata_refusal(self):
        home = self.root / "home"
        home.mkdir()
        with mock.patch.object(self.df, "_user_home", return_value=home), mock.patch.object(self.df, "_hermes_home", return_value=self.root / "hermes"), mock.patch.object(self.df, "_is_git_work_tree", return_value=True), mock.patch.object(self.df.os.path, "normcase", side_effect=ntpath.normcase):
            self.assertIn("protected path", self.df._refuse_df_init_target(home / "appdata" / "local") or "")

    def test_drive_and_unc_roots_refused(self):
        for raw in ("C:\\", "\\\\server\\share\\"):
            target = PureWindowsPath(raw)
            with self.subTest(raw=raw), mock.patch.object(PureWindowsPath, "expanduser", create=True, return_value=target), mock.patch.object(PureWindowsPath, "resolve", create=True, return_value=target), mock.patch.object(self.df, "Path", PureWindowsPath):
                self.assertIn("filesystem root", self.df._refuse_df_init_target(target))

    def exercise_retry(self, module, write):
        real_replace = Path.replace
        attempts = []
        def transient(source, destination):
            attempts.append(source)
            if len(attempts) <= 2:
                raise PermissionError("fixture busy")
            return real_replace(source, destination)
        with mock.patch.object(Path, "replace", autospec=True, side_effect=transient), mock.patch("time.sleep") as sleep:
            write()
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleep.call_args_list, [mock.call(0.05), mock.call(0.1)])
        with mock.patch.object(Path, "replace", side_effect=PermissionError("fixture busy")) as replace, mock.patch("time.sleep") as sleep:
            with self.assertRaises(PermissionError):
                write()
        self.assertEqual(replace.call_count, 5)
        self.assertEqual(sleep.call_args_list, [mock.call(0.05 * n) for n in range(1, 5)])
        with mock.patch.object(Path, "replace", side_effect=OSError("not permission")) as replace:
            with self.assertRaises(OSError):
                write()
        self.assertEqual(replace.call_count, 1)

    def test_state_replace_retry(self):
        path = self.root / "state.json"
        with mock.patch.object(self.state, "state_path", return_value=path):
            self.exercise_retry(self.state, lambda: self.state.save_state({"saved": True}))
        self.assertIn('"saved": true', path.read_text(encoding="utf-8"))

    def test_replace_exhaustion_is_bounded(self):
        path = self.root / "state.json"
        with mock.patch.object(self.state, "state_path", return_value=path):
            for write in (lambda: self.state.save_state({}), lambda: self.night.atomic_write(path, "saved")):
                with self.subTest(write=write), mock.patch.object(Path, "replace", side_effect=PermissionError("fixture busy")) as replace, mock.patch("time.sleep"):
                    with self.assertRaises(PermissionError):
                        write()
                    self.assertEqual(replace.call_count, 5)

    def test_night_replace_retry(self):
        path = self.root / "night.json"
        self.exercise_retry(self.night, lambda: self.night.atomic_write(path, "saved"))
        self.assertEqual(path.read_text(encoding="utf-8"), "saved")
