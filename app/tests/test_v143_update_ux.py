# Memo AI v1.4.3 - headless tests for the Install-now / Install-later
# choice + the Apple-style "What's new" slide (run on Linux, no
# Windows, no keys, no network except mocked):
#
#   python -m unittest app.tests.test_v143_update_ux -v
#
# What is proven here:
#   * download_update() downloads to a PERSISTENT folder, verifies the
#     SHA, records the download, sets progress state "downloaded" -
#     and installs NOTHING (no files land in BASE_DIR).
#   * "Install later": the reminder flag allows the reminder once,
#     then blocks it for the rest of the day; yesterday's flag allows
#     it again. The downloaded file stays on disk.
#   * "Install now": apply_downloaded() installs the saved zip over
#     BASE_DIR (keys/ + app/data/ preserved), then consumes the file
#     and clears the record.
#   * A saved file deleted from disk, or with a wrong SHA, is refused
#     with redownload=True (never installed); the record is cleared.
#   * A SHA mismatch at download time aborts with no state saved.
#   * parse_features(): structured manifest "features" win; the plain
#     "notes" text (e.g. the real v1.4.2 notes) falls back to one card
#     per feature, with the "vX.Y.Z:" prefix stripped.
#   * The UI: the slide container + renderUpdateSlide() + feature
#     cards + Install now / Install later exist, the old plain-text
#     confirm() install flow is gone, and the CSS entrance animation
#     classes exist. app.js parses (node --check when node exists).
#
# NOT proven here (needs real Windows + WebView2 + the user's tap):
# the slide rendering in the real window, the silent setup.exe run
# on a frozen install, and the app restart after Install now.

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

V142_NOTES = ("v1.4.2: إصلاح عدم الرد على الواتساب (تعريف المرسل LID + "
              "قارئ stdout بـ UTF-8 ميموتش + تسجيل stderr/exit code "
              "لـ sidecar + backoff) — أداة إرسال واتساب حقيقية — ذاكرة "
              "طويلة محلية + تعلم من المستخدم — بحث ويب مجاني — مراقبة "
              "صحة اللابتوب + تنبيهات — تنبيه الإشعارات المهمة — أذونات "
              "الأدوات كرسالة في الشات (سماح/رفض) مش popup — retry لجلب "
              "الـ manifest.")


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _make_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("memo-ai/app/main.py", "# new main\n")
        zf.writestr("memo-ai/app/ui/app.js", "// new ui\n")
        zf.writestr("memo-ai/README.md", "new readme\n")
    return buf.getvalue()


class _FakeResp:
    def __init__(self, *, json_data=None, content=b"", headers=None):
        self._json = json_data
        self._content = content
        self.headers = headers or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._json

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i:i + chunk_size]


class _UpdaterCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.base = self.tmp / "base"
        self.base.mkdir()
        (self.base / "app").mkdir()
        (self.base / "app" / "main.py").write_text("# old main\n", encoding="utf-8")
        self.download_dir = self.tmp / "updates"
        self.state_path = self.tmp / "update_downloaded.json"
        self.zip_bytes = _make_zip_bytes()
        self.zip_sha = hashlib.sha256(self.zip_bytes).hexdigest()
        self.exe_bytes = b"MZ fake setup"
        self.exe_sha = hashlib.sha256(self.exe_bytes).hexdigest()
        self.manifest = {
            "version": "1.4.3",
            "url": "https://example.invalid/memo-ai-v1.4.3.zip",
            "sha256": self.zip_sha,
            "notes": V142_NOTES,
            "setup_url": "https://example.invalid/MemoAI-Setup-1.4.3.exe",
            "setup_sha256": self.exe_sha,
            "features": [
                {"icon": "💬", "title": "إصلاح الرد على الواتساب",
                 "desc": "تعريف المرسل LID"},
                {"icon": "🧠", "title": "ذاكرة طويلة",
                 "desc": "البرنامج بيفتكر اللي اتفقتوا عليه"},
            ],
        }
        self.fake_main = mock.Mock()
        self.fake_main.VERSION = "1.4.2"
        self._patchers = [
            mock.patch.object(updater, "load_config",
                              return_value={"manifest_url":
                                            "https://example.invalid/v.json"}),
            mock.patch.object(updater, "BASE_DIR", self.base),
            mock.patch.object(updater, "DOWNLOAD_DIR", self.download_dir),
            mock.patch.object(updater, "DOWNLOADED_STATE_PATH", self.state_path),
            mock.patch.object(updater.time, "sleep", return_value=None),
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
        if hasattr(sys, "frozen"):
            del sys.frozen

    def _fake_get(self, url, timeout=None, stream=False):
        if url.startswith("https://example.invalid/v.json"):
            return _FakeResp(json_data=self.manifest)
        if url.endswith(".zip"):
            return _FakeResp(content=self.zip_bytes,
                             headers={"Content-Length": str(len(self.zip_bytes))})
        if url.endswith(".exe"):
            return _FakeResp(content=self.exe_bytes,
                             headers={"Content-Length": str(len(self.exe_bytes))})
        raise AssertionError(f"unexpected url {url}")

    def _patch_get(self):
        return mock.patch.object(updater.requests, "get",
                                 side_effect=self._fake_get)


class TestParseFeatures(_UpdaterCase):
    def test_structured_features_win(self):
        feats = updater.parse_features(self.manifest)
        self.assertEqual(len(feats), 2)
        self.assertEqual(feats[0]["icon"], "💬")
        self.assertEqual(feats[0]["title"], "إصلاح الرد على الواتساب")
        self.assertEqual(feats[0]["desc"], "تعريف المرسل LID")
        self.assertEqual(feats[1]["title"], "ذاكرة طويلة")

    def test_string_items_and_missing_icon_get_defaults(self):
        feats = updater.parse_features(
            {"features": ["إصلاح سريع", {"title": "بلا أيقونة"}]})
        self.assertEqual(feats[0]["title"], "إصلاح سريع")
        self.assertTrue(feats[0]["icon"])
        self.assertTrue(feats[1]["icon"])

    def test_fallback_splits_plain_notes(self):
        feats = updater.parse_features({"notes": V142_NOTES})
        self.assertGreaterEqual(len(feats), 5)
        for f in feats:
            self.assertTrue(f["title"])
            self.assertTrue(f["icon"])
        self.assertNotIn("v1.4.2", feats[0]["title"])

    def test_empty_manifest_gives_empty_list(self):
        self.assertEqual(updater.parse_features({}), [])
        self.assertEqual(updater.parse_features({"notes": ""}), [])


class TestDownloadOnly(_UpdaterCase):
    def test_download_saves_persistent_file_and_installs_nothing(self):
        with self._patch_get():
            result = updater.download_update()
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("status"), "downloaded")
        self.assertEqual(result.get("version"), "1.4.3")
        self.assertEqual(result.get("kind"), "zip")
        saved = Path(result["path"])
        self.assertTrue(saved.is_file())
        self.assertEqual(saved.parent, self.download_dir)
        self.assertEqual(saved.read_bytes(), self.zip_bytes)
        # NOTHING was installed: BASE_DIR still has only the old files.
        self.assertEqual((self.base / "app" / "main.py").read_text(),
                         "# old main\n")
        self.assertFalse((self.base / "README.md").exists())
        # Progress ends in the new "downloaded" state, not "done".
        self.assertEqual(updater.get_progress()["state"], "downloaded")
        # The record is saved, with features for the slide.
        state = updater.load_downloaded()
        self.assertEqual(state["version"], "1.4.3")
        self.assertEqual(state["sha256"], self.zip_sha)
        self.assertEqual(len(state["features"]), 2)
        self.assertIsNone(state["reminder_shown_date"])

    def test_download_sha_mismatch_aborts_with_no_state(self):
        self.manifest["sha256"] = "00" * 32
        with self._patch_get():
            result = updater.download_update()
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "sha_mismatch")
        self.assertEqual(updater.load_downloaded(), {})
        self.assertEqual(list(self.download_dir.iterdir()), [])
        self.assertEqual(updater.get_progress()["state"], "error")

    def test_frozen_download_picks_setup(self):
        sys.frozen = True
        with self._patch_get():
            result = updater.download_update()
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("kind"), "setup")
        self.assertTrue(Path(result["path"]).name.endswith(".exe"))

    def test_check_update_returns_features_and_downloaded(self):
        with self._patch_get():
            result = updater.check_update()
        self.assertEqual(result["status"], "update_available")
        self.assertEqual(len(result["features"]), 2)
        self.assertEqual(result["features"][0]["title"],
                         "إصلاح الرد على الواتساب")
        self.assertIn("downloaded", result)
        self.assertFalse(result["downloaded"]["downloaded"])


class TestInstallLaterReminder(_UpdaterCase):
    def test_reminder_once_per_day_then_blocked(self):
        with self._patch_get():
            self.assertTrue(updater.download_update()["ok"])
        info = updater.get_downloaded_update()
        self.assertTrue(info["downloaded"])
        self.assertTrue(info["valid"])
        self.assertTrue(info["remind"])  # not shown yet today
        updater.mark_reminder_shown()
        self.assertFalse(updater.get_downloaded_update()["remind"])
        # The file itself is untouched by "Install later".
        self.assertTrue(Path(info["path"]).is_file())
        # ... but tomorrow it may remind again.
        updater.mark_reminder_shown("2000-01-01")
        self.assertTrue(updater.get_downloaded_update()["remind"])
        self.assertTrue(updater.should_remind())


class TestInstallNow(_UpdaterCase):
    def test_apply_downloaded_installs_and_consumes_file(self):
        with self._patch_get():
            self.assertTrue(updater.download_update()["ok"])
        result = updater.apply_downloaded()
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("status"), "installed")
        self.assertEqual((self.base / "app" / "main.py").read_text(),
                         "# new main\n")
        self.assertTrue((self.base / "README.md").exists())
        # Consumed: file + record are gone after a successful install.
        self.assertEqual(updater.load_downloaded(), {})
        self.assertEqual(list(self.download_dir.iterdir()), [])
        self.assertEqual(updater.get_progress()["state"], "done")

    def test_apply_with_no_download(self):
        result = updater.apply_downloaded()
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "no_download")
        self.assertFalse(result.get("redownload"))

    def test_apply_missing_file_redownloads_instead_of_installing(self):
        with self._patch_get():
            self.assertTrue(updater.download_update()["ok"])
        saved = Path(updater.load_downloaded()["path"])
        saved.unlink()
        result = updater.apply_downloaded()
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "missing")
        self.assertTrue(result.get("redownload"))
        self.assertEqual(updater.load_downloaded(), {})
        # Nothing was installed from the (missing) file.
        self.assertEqual((self.base / "app" / "main.py").read_text(),
                         "# old main\n")

    def test_apply_corrupt_file_redownloads_instead_of_installing(self):
        with self._patch_get():
            self.assertTrue(updater.download_update()["ok"])
        saved = Path(updater.load_downloaded()["path"])
        saved.write_bytes(b"corrupted!")
        result = updater.apply_downloaded()
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "corrupt")
        self.assertTrue(result.get("redownload"))
        self.assertEqual(updater.load_downloaded(), {})

    def test_apply_stale_version_is_discarded(self):
        with self._patch_get():
            self.assertTrue(updater.download_update()["ok"])
        self.fake_main.VERSION = "1.4.3"  # no longer newer
        result = updater.apply_downloaded()
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "stale")
        self.assertFalse(result.get("redownload"))

    def test_frozen_apply_launches_saved_setup(self):
        sys.frozen = True
        with self._patch_get(), \
                mock.patch.object(updater.subprocess, "Popen",
                                  return_value=mock.Mock()) as popen:
            self.assertTrue(updater.download_update()["ok"])
            result = updater.apply_downloaded()
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("kind"), "setup")
        popen.assert_called_once()
        # The bat the setup launcher writes lives next to the setup.
        self.assertTrue((self.download_dir / "run-setup.bat").exists())

    def test_legacy_install_update_still_downloads_and_installs(self):
        with self._patch_get():
            self.assertTrue(updater.install_update())
        self.assertEqual((self.base / "app" / "main.py").read_text(),
                         "# new main\n")

    def test_legacy_install_setup_refuses_non_frozen(self):
        self.assertFalse(updater.install_setup())


class TestUpdateSlideUI(unittest.TestCase):
    def test_slide_markup_and_handlers(self):
        js = _read("app/ui/app.js")
        html = _read("app/ui/index.html")
        css = _read("app/ui/styles.css")
        # The slide container lives in the Updates section.
        self.assertIn('id="update-slide"', html)
        self.assertIn('id="install-downloaded-btn"', html)
        # New flow: render + choice handlers, no old install button/flow.
        self.assertIn("renderUpdateSlide", js)
        self.assertIn("parseFeaturesFromNotes", js)
        self.assertIn("refreshDownloadedState", js)
        self.assertIn("installDownloadedNow", js)
        self.assertIn("installDownloadedLater", js)
        self.assertIn("download_update", js)
        self.assertIn("dismiss_update_reminder", js)
        self.assertIn("update_install_now", js)
        self.assertIn("update_install_later", js)
        self.assertIn("update-slide-progress-fill", js)
        self.assertNotIn("install-update-btn", js)
        self.assertNotIn("install-update-btn", html)
        self.assertNotIn('confirm(t("confirm_install"))', js)
        self.assertNotIn("confirm_install", js)
        # Entrance animation + feature cards (Apple-style, no pop-in).
        self.assertIn(".update-slide", css)
        self.assertIn(".update-slide-in", css)
        self.assertIn(".update-feature-card", css)
        self.assertIn("@keyframes updateSlideIn", css)
        self.assertIn("@keyframes updateCardIn", css)
        self.assertIn("cubic-bezier(0.22, 1, 0.36, 1)", css)

    def test_api_exposes_download_install_choice(self):
        main_src = _read("app/main.py")
        self.assertIn("def download_update(self)", main_src)
        self.assertIn("def get_downloaded_update(self)", main_src)
        self.assertIn("def dismiss_update_reminder(self)", main_src)
        self.assertIn("def install_update(self)", main_src)
        self.assertIn("apply_downloaded()", main_src)

    def test_js_parses(self):
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        proc = subprocess.run([node, "--check", str(ROOT / "app/ui/app.js")],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
