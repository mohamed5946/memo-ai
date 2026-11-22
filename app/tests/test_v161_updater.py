# Memo AI v1.6.1 - tests for the updater setup-install fix
# (run on Linux, no Windows, no keys, no network):
#
#   python3 -m pytest app/tests/test_v161_updater.py -q
#
# What is proven here (field bug, reported 2026-11-22 after 2 failed
# attempts; the download itself worked):
#   * apply_downloaded() used to delete the setup.exe IMMEDIATELY after
#     _launch_setup() spawned run-setup.bat, but the bat waits 3s before
#     running the setup - so the file was already gone when the installer
#     should run; the app exited and nothing happened. Now the setup file
#     stays on disk after apply_downloaded() returns, and the bat deletes
#     it (and itself) only AFTER the installer finishes.
#   * The bat relaunches the app ONLY when the installer succeeded
#     (exit code 0) - the old bat relaunched unconditionally, even on a
#     failed install. The installer's [Run] section has skipifsilent,
#     so silent installs rely on this bat for the relaunch.
#   * On a failed install the setup.exe is deliberately KEPT on disk
#     (app/data/updates/) so it can be run by hand or retried; the bat
#     itself is still deleted.
#   * Zip installs are unchanged: the zip is still consumed (deleted)
#     by apply_downloaded() after a successful install.
#   * The "installing" progress state is still set during install, so
#     the UI keeps showing "installing now" and the button stays locked.
#
# NOT proven here (needs real Windows): the bat actually running under
# cmd.exe, the silent setup.exe run on a frozen install, and the app
# restart after Install now. The bat's ordering IS proven here by
# executing it in a small simulator for the exact cmd constructs it
# uses (timeout / quoted-exe run / if errorlevel / start / del / goto).

import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

import updater  # noqa: E402


def _make_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("memo-ai/app/main.py", "# new main\n")
        zf.writestr("memo-ai/app/ui/app.js", "// new ui\n")
    return buf.getvalue()


def _run_bat(bat_path: Path, *, setup_exit_code: int) -> list:
    """Minimal cmd.exe simulator for the exact constructs run-setup.bat
    uses - enough to prove the ordering (wait -> run setup -> gate the
    relaunch on the exit code -> delete setup -> delete self) on Linux,
    where cmd.exe does not exist. The fake "setup" is the quoted exe
    line: it records whether the setup file still existed at run time
    and returns the configured exit code. Returns the ordered event
    list, e.g. ["waited", "ran:LONA-Setup-v1.6.1.exe:existed=True",
    "relaunch", "deleted:LONA-Setup-v1.6.1.exe:existed=True",
    "bat_deleted"]."""
    lines = bat_path.read_text(encoding="utf-8").splitlines()
    labels = {}
    for i, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith(":") and not line.startswith("::"):
            labels[line[1:].strip().lower()] = i
    events = []
    last_code = 0
    pc = 0
    steps = 0
    while 0 <= pc < len(lines):
        steps += 1
        if steps > 100:
            raise AssertionError("bat did not terminate (loop?)")
        raw = lines[pc].strip()
        pc += 1
        if not raw or raw.lower() == "@echo off":
            continue
        if raw.startswith(":"):
            continue
        low = raw.lower()
        if low.startswith("timeout "):
            events.append("waited")
            continue
        if low.startswith("if errorlevel "):
            parts = raw.split()
            code = int(parts[2])
            target = parts[4].lstrip(":").lower()
            if last_code >= code:
                pc = labels[target]
            continue
        if low.startswith("goto "):
            pc = labels[raw.split()[1].lstrip(":").lower()]
            continue
        if low.startswith("start "):
            events.append("relaunch")
            continue
        if low.startswith("del "):
            tokens = [t for t in raw.split()[1:]
                      if t.lower() not in ("/f", "/q")]
            target = " ".join(tokens).strip().strip('"')
            if target == "%~f0":
                bat_path.unlink(missing_ok=True)
                events.append("bat_deleted")
            else:
                p = Path(target)
                existed = p.is_file()
                p.unlink(missing_ok=True)
                events.append(f"deleted:{p.name}:existed={existed}")
            continue
        if low.startswith("exit "):
            break
        if raw.startswith('"'):
            end = raw.index('"', 1)
            exe = Path(raw[1:end])
            events.append(f"ran:{exe.name}:existed={exe.is_file()}")
            last_code = setup_exit_code
            continue
        raise AssertionError(f"unhandled bat line in simulator: {raw!r}")
    return events


class _V161UpdaterCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.base = self.tmp / "base"
        self.base.mkdir()
        (self.base / "app").mkdir()
        (self.base / "app" / "main.py").write_text("# old main\n",
                                                   encoding="utf-8")
        self.download_dir = self.tmp / "updates"
        self.download_dir.mkdir()
        self.state_path = self.tmp / "update_downloaded.json"
        self.exe_bytes = b"MZ fake setup"
        self.exe_sha = hashlib.sha256(self.exe_bytes).hexdigest()
        self.setup_path = (self.download_dir
                           / "LONA-Setup-v1.6.1.exe")
        self.setup_path.write_bytes(self.exe_bytes)
        self.fake_main = mock.Mock()
        self.fake_main.VERSION = "1.6.0"
        self._patchers = [
            mock.patch.object(updater, "BASE_DIR", self.base),
            mock.patch.object(updater, "DOWNLOAD_DIR", self.download_dir),
            mock.patch.object(updater, "DOWNLOADED_STATE_PATH",
                              self.state_path),
            mock.patch.dict(sys.modules, {"main": self.fake_main}),
        ]
        for p in self._patchers:
            p.start()
        updater._set_progress(state="idle", downloaded=0, total=None,
                              percent=None, version="")

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        updater._set_progress(state="idle", downloaded=0, total=None,
                              percent=None, version="")

    def _save_state(self, kind, path, sha):
        self.state_path.write_text(json.dumps({
            "version": "1.6.1", "kind": kind, "path": str(path),
            "sha256": sha, "size": path.stat().st_size,
            "downloaded_at": "2026-11-22T00:00:00",
            "features": [], "reminder_shown_date": None}),
            encoding="utf-8")

    def _save_setup_state(self):
        self._save_state("setup", self.setup_path, self.exe_sha)

    def test_apply_downloaded_setup_keeps_setup_file(self):
        # THE field bug: apply_downloaded() deleted the setup.exe right
        # after spawning the bat, 3s before the bat ran it. Now the
        # file must still be on disk after apply returns ok.
        self._save_setup_state()
        with mock.patch.object(updater.subprocess, "Popen") as popen:
            result = updater.apply_downloaded()
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("status"), "installed")
        popen.assert_called_once()
        self.assertTrue(self.setup_path.is_file(),
                        "setup.exe must still exist - run-setup.bat "
                        "deletes it after the installer finishes")
        # The state record IS cleared (the UI stops offering it).
        self.assertEqual(updater.load_downloaded(), {})
        # The "installing" state ran and finished as "done".
        self.assertEqual(updater.get_progress()["state"], "done")
        self.assertEqual(updater.get_progress()["version"], "1.6.1")

    def test_apply_downloaded_setup_spawn_failure_keeps_file(self):
        # If the bat cannot even be spawned, nothing is consumed.
        self._save_setup_state()
        with mock.patch.object(updater.subprocess, "Popen",
                               side_effect=OSError("no cmd")):
            result = updater.apply_downloaded()
        self.assertFalse(result.get("ok"))
        self.assertTrue(self.setup_path.is_file())
        self.assertEqual(updater.load_downloaded()["version"], "1.6.1")
        self.assertEqual(updater.get_progress()["state"], "error")

    def test_apply_downloaded_zip_still_consumes_file(self):
        # Zip installs are unchanged: the zip is deleted by
        # apply_downloaded() after a successful install.
        zip_bytes = _make_zip_bytes()
        zip_path = self.download_dir / "memo-ai-v1.6.1.zip"
        zip_path.write_bytes(zip_bytes)
        self._save_state("zip", zip_path,
                         hashlib.sha256(zip_bytes).hexdigest())
        result = updater.apply_downloaded()
        self.assertTrue(result.get("ok"), result)
        self.assertFalse(zip_path.exists())
        self.assertEqual(updater.load_downloaded(), {})
        self.assertEqual((self.base / "app" / "main.py").read_text(
            encoding="utf-8"), "# new main\n")

    def test_launch_setup_bat_ordering(self):
        # Static ordering proof on the generated bat text: the 3s wait
        # comes first, the setup runs next, the relaunch sits AFTER an
        # exit-code gate, and the setup file is deleted only after the
        # setup line (never before it).
        with mock.patch.object(updater.subprocess, "Popen"):
            self.assertTrue(updater._launch_setup(self.setup_path))
        bat = self.download_dir / "run-setup.bat"
        text = bat.read_text(encoding="utf-8")
        wait = text.index("timeout /t 3")
        run = text.index(str(self.setup_path))
        gate = text.index("if errorlevel 1")
        relaunch = text.index('start ""')
        delete_setup = text.index(f'del /f /q "{self.setup_path}"')
        delete_self = text.index('del /f /q "%~f0"')
        self.assertLess(wait, run)
        self.assertLess(run, gate)
        self.assertLess(gate, relaunch)
        self.assertLess(run, delete_setup)
        self.assertLess(relaunch, delete_setup)
        self.assertLess(delete_setup, delete_self)
        self.assertIn("/VERYSILENT /NORESTART", text)

    def test_bat_simulated_run_success(self):
        # Launch -> (3s wait) -> setup runs WHILE the file still
        # exists -> app relaunches -> setup deleted -> bat deleted.
        with mock.patch.object(updater.subprocess, "Popen"):
            updater._launch_setup(self.setup_path)
        bat = self.download_dir / "run-setup.bat"
        events = _run_bat(bat, setup_exit_code=0)
        self.assertEqual(events, [
            "waited",
            "ran:LONA-Setup-v1.6.1.exe:existed=True",
            "relaunch",
            "deleted:LONA-Setup-v1.6.1.exe:existed=True",
            "bat_deleted",
        ])
        self.assertFalse(self.setup_path.exists())
        self.assertFalse(bat.exists())

    def test_bat_simulated_run_failure_no_relaunch_keeps_setup(self):
        # A failed install must NOT relaunch the app, and the setup.exe
        # is kept on disk so it can be run by hand or retried; only the
        # one-shot bat deletes itself.
        with mock.patch.object(updater.subprocess, "Popen"):
            updater._launch_setup(self.setup_path)
        bat = self.download_dir / "run-setup.bat"
        events = _run_bat(bat, setup_exit_code=1)
        self.assertEqual(events, [
            "waited",
            "ran:LONA-Setup-v1.6.1.exe:existed=True",
            "bat_deleted",
        ])
        self.assertNotIn("relaunch", events)
        self.assertTrue(self.setup_path.is_file(),
                        "failed install keeps the setup.exe for a manual retry")
        self.assertFalse(bat.exists())


if __name__ == "__main__":
    unittest.main()
