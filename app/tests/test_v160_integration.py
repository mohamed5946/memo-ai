# LONA v1.6.0 - integration tests (coordinator's merge fixes).
#
#   python -m pytest app/tests/test_v160_integration.py -q
#
# What is proven here (headless, Linux, no Windows, no network):
#   * fixer.restore_deleted: a file sent to the (local) trash comes
#     back at its original path, and the undo entry is removed.
#   * fixer.relaunch_process works from a recorded kill entry.
#   * Api contract (source inspection - main.py cannot import here,
#     webview is missing): the five bridge methods the UI calls exist
#     (list_undoable, relaunch_process, restore_deleted,
#     set_privacy_mode, analyze_image), the native-host hook is in
#     main(), VERSION is 1.6.0 and matches version.json.
#   * UI contract: app.js passes the undo INDEX (not it.id) to
#     relaunch_process / restore_deleted.
#   * Packaging: the installer ships extension/, the spec collects
#     cryptography, CI runs the security check, the release title
#     says LONA.
#   * Image understanding: with no Gemini key the vision call fails
#     honestly (no_key) without touching the network.

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

import fixer as fixer_mod  # noqa: E402
import image_understanding as image_mod  # noqa: E402
import settings_store as settings_mod  # noqa: E402
import apikeys as apikeys_mod  # noqa: E402


class TestRestoreDeleted(unittest.TestCase):
    def test_delete_then_restore_roundtrip(self):
        tmp = Path(tempfile.mkdtemp())
        data_dir = tmp / "data"
        target = tmp / "notes.txt"
        target.write_text("keep me", encoding="utf-8")
        with mock.patch.object(settings_mod, "DATA_DIR", data_dir):
            deleted = fixer_mod.delete_user_file(str(target), granted=True)
            self.assertTrue(deleted.get("ok"), deleted)
            self.assertFalse(target.exists())
            entries = fixer_mod.list_undoable()
            files = [e for e in entries if e.get("kind") == "file"]
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0]["path"], str(target))
            restored = fixer_mod.restore_deleted(files[0])
            self.assertTrue(restored.get("ok"), restored)
            self.assertEqual(target.read_text(encoding="utf-8"), "keep me")
            self.assertEqual(
                [e for e in fixer_mod.list_undoable()
                 if e.get("kind") == "file"], [])

    def test_restore_refuses_non_file_entries(self):
        entry = {"kind": "process", "name": "x", "exe": "/bin/true"}
        res = fixer_mod.restore_deleted(entry)
        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("reason"), "not_a_file")

    def test_restore_missing_file_is_honest(self):
        res = fixer_mod.restore_deleted(
            {"kind": "file", "name": "ghost.txt",
             "path": "/tmp/no-such-dir-xyz/ghost.txt"})
        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("reason"), "not_found")


class TestRelaunchRecordedKill(unittest.TestCase):
    def test_relaunch_from_record(self):
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(settings_mod, "DATA_DIR", tmp / "data"):
            if sys.platform == "win32":
                # See test_v160_backend.TestFixerTiers
                # .test_relaunch_process: os.startfile() takes no
                # arguments, so a bare python.exe would open a REPL
                # that never exits - assert the wiring instead.
                exe, name = sys.executable, "python"
            else:
                exe, name = "/bin/true", "true"
            entry = fixer_mod.record_kill(name, None, exe=exe)
            if sys.platform == "win32":
                with mock.patch.object(fixer_mod.os,
                                       "startfile") as started:
                    res = fixer_mod.relaunch_process(entry)
                started.assert_called_once_with(exe)
            else:
                res = fixer_mod.relaunch_process(entry)
            self.assertTrue(res.get("ok"), res)
            self.assertIn(entry,
                          fixer_mod.list_undoable())


class TestPrivacySettingRoundtrip(unittest.TestCase):
    def test_privacy_mode_persists(self):
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(settings_mod, "DATA_DIR", tmp), \
                mock.patch.object(settings_mod, "SETTINGS_PATH",
                                  tmp / "settings.json"):
            settings = settings_mod.load_settings()
            self.assertTrue(settings["privacy_mode"])
            settings["privacy_mode"] = False
            self.assertTrue(settings_mod.save_settings(settings))
            self.assertFalse(settings_mod.load_settings()["privacy_mode"])


class TestImageUnderstandingNoKey(unittest.TestCase):
    def test_no_key_fails_without_network(self):
        # A tiny PNG header + confirmed: the key check fires first,
        # so no HTTP call is attempted in this sandbox.
        # The key store is forced to an EMPTY txt fallback: on
        # Windows the real store is the shared Credential Manager,
        # and a key saved by an earlier test in the same process
        # would otherwise make this a live network call (which is
        # exactly how it failed on CI: 'http_error' instead of
        # 'no_key'). requests.post is also guarded so a leaked key
        # can never silently turn this into a live call.
        tmp = Path(tempfile.mkdtemp())
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        with mock.patch.object(settings_mod, "KEYS_DIR", tmp / "keys"), \
                mock.patch.object(apikeys_mod, "_cred_available",
                                  return_value=False), \
                mock.patch("requests.post",
                           side_effect=AssertionError(
                               "no HTTP call with no key")):
            apikeys_mod.reset_migration_flag()
            res = image_mod.send_image_to_gemini(png, "x", confirmed=True)
        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("reason"), "no_key")

    def test_refused_without_confirmation(self):
        res = image_mod.send_image_to_gemini(b"\x89PNG\r\n\x1a\n",
                                             "x", confirmed=False)
        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("reason"), "needs_confirmation")


class TestApiContract(unittest.TestCase):
    def _main_source(self):
        return (APP_DIR / "main.py").read_text(encoding="utf-8")

    def test_bridge_methods_exist(self):
        src = self._main_source()
        for method in ("def list_undoable(self):",
                       "def relaunch_process(self, index):",
                       "def restore_deleted(self, index):",
                       "def set_privacy_mode(self, enabled):",
                       "def analyze_image(self, data_url):"):
            with self.subTest(method=method):
                self.assertIn(method, src)

    def test_native_host_hook_in_main(self):
        src = self._main_source()
        self.assertIn('"--native-host" in sys.argv', src)
        self.assertIn('chrome-extension://', src)
        self.assertIn('from native_host import run_host_main', src)

    def test_version_is_1_6_2_everywhere(self):
        # v1.6.4 reskin: the version is 1.6.4-rc.2 everywhere; the
        # manifest version matches it while its URLs still point at
        # the v1.6.3 release (the RC tag is gated out of the updater
        # manifest). Test name kept for continuity with the v1 suite.
        src = self._main_source()
        self.assertIn('VERSION = "1.6.4-rc.2"', src)
        manifest = json.loads((ROOT / "version.json").read_text(
            encoding="utf-8"))
        self.assertEqual(manifest["version"], "1.6.4-rc.2")
        self.assertTrue(manifest.get("features"))


class TestUiContract(unittest.TestCase):
    def test_undo_passes_index_not_it_id(self):
        src = (APP_DIR / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("relaunch_process(idx)", src)
        self.assertIn("restore_deleted(idx)", src)
        self.assertNotIn("relaunch_process(it.id)", src)
        self.assertNotIn("restore_deleted(it.id)", src)

    def test_no_memo_ai_in_ui_files(self):
        for name in ("index.html", "island.html", "app.js", "pet.html"):
            src = (APP_DIR / "ui" / name).read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertNotIn("Memo AI", src)
                self.assertNotIn("يا ميمو", src)


class TestPackaging(unittest.TestCase):
    def test_installer_ships_extension(self):
        iss = (ROOT / "installer" / "memo-ai.iss").read_text(
            encoding="utf-8")
        self.assertIn('..\\extension\\*"; DestDir: "{app}\\extension"',
                      iss)
        self.assertIn('native-host.json"; DestDir: "{app}"', iss)

    def test_spec_collects_cryptography(self):
        spec = (ROOT / "installer" / "memo-ai.spec").read_text(
            encoding="utf-8")
        self.assertIn('"cryptography"', spec)

    def test_ci_runs_security_check(self):
        wf = (ROOT / ".github" / "workflows" / "build-installer.yml"
              ).read_text(encoding="utf-8")
        self.assertIn("scripts/security_check.py", wf)
        self.assertIn('"LONA $tag"', wf)


if __name__ == "__main__":
    unittest.main()
