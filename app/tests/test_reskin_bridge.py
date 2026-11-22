"""LONA v1.6.4 reskin bridge tests (headless, Linux: no Windows, no keys,
no network except mocked):

    python3 -m pytest app/tests/test_reskin_bridge.py -q -p no:cacheprovider

What is proven here:

  * (a) the ported bridge.js polls window.pywebview.api FIRST
    (pywebview 6.2.1 has no js_api - the v2.0.0-rebuild.4/.5 dead-bridge
    bug was exactly this name); js_api appears only as a fallback.
  * (b) every B.call('...') method in the ported UI (index/island/
    invite) exists on Api - no dead bridge calls.
  * (c) save_settings flat v2 patch -> canonical v1 keys round-trips
    through get_settings (aliases included).
  * (d) request_deep_file_access() with no arg does not raise
    TypeError (v1.6.3 F-C) and returns a pending id + message.
  * (e) get_themes_v2 includes the Liquid Glass 'lg' theme.

main.py cannot be imported as-is here (no pywebview on Linux),
so a minimal webview stub is installed in sys.modules first --
the same trick as test_v162_backend.py.
"""

import json
import re
import sys
import tempfile
import types as _types
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

UI_DIR = APP_DIR / "ui"


class _FakeWindow:
    def __init__(self):
        self.minimized = 0
        self.maximized_calls = 0
        self.restored_calls = 0
        self.moved_to = []
        self.destroyed = False

    def minimize(self):
        self.minimized += 1

    def maximize(self):
        self.maximized_calls += 1

    def restore(self):
        self.restored_calls += 1

    def move(self, x, y):
        self.moved_to.append((x, y))

    def destroy(self):
        self.destroyed = True

    def show(self):
        pass

    def hide(self):
        pass

    def evaluate_js(self, js):
        return None


_fake_webview = _types.ModuleType("webview")
_fake_webview.create_window = lambda *a, **k: _FakeWindow()
_fake_webview.start = lambda *a, **k: None
_fake_webview.windows = []
sys.modules.setdefault("webview", _fake_webview)

import main as main_mod  # noqa: E402
import settings_store  # noqa: E402


class _ApiCase(unittest.TestCase):
    """An Api bound to a MemoApp with a TEMP settings file."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.orig_settings = settings_store.SETTINGS_PATH
        settings_store.SETTINGS_PATH = self.tmp / "settings.json"
        self.app = main_mod.MemoApp()
        self.api = self.app.api

    def tearDown(self):
        try:
            if self.app._monitor is not None:
                self.app._monitor.stop()
        except Exception:
            pass
        settings_store.SETTINGS_PATH = self.orig_settings


# ---------------------------------------------------------------------------
# (a) bridge.js polls window.pywebview.api first
# ---------------------------------------------------------------------------

class TestBridgeJsApiName(unittest.TestCase):
    def test_api_first_js_api_only_as_fallback(self):
        src = (UI_DIR / "assets" / "bridge.js").read_text(encoding="utf-8")
        # The 6.x property name must be the primary poll target.
        self.assertRegex(
            src, r"(?:global\.)?pywebview\.api\s*\|\|\s*(?:global\.)?pywebview\.js_api",
            "bridge.js must poll window.pywebview.api first "
            "(js_api only as fallback)")
        # The timeout error must name the real property, not the dead one.
        self.assertIn("pywebview.api", src)
        self.assertNotIn("pywebview.js_api لم يظهر", src)

    def test_no_standalone_js_api_poll(self):
        src = (UI_DIR / "assets" / "bridge.js").read_text(encoding="utf-8")
        # Strip comments: the header documents the old name, but the
        # CODE must mention js_api exactly once - the || fallback.
        code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        code = re.sub(r"//[^\n]*", "", code)
        hits = list(re.finditer(r"js_api", code))
        self.assertEqual(len(hits), 1,
                         "js_api must appear exactly once in code "
                         "(the || fallback)")
        before = code[max(0, hits[0].start() - 40):hits[0].start()]
        self.assertIn("||", before,
                      "js_api used outside the || fallback")


# ---------------------------------------------------------------------------
# (b) every UI bridge call exists on Api
# ---------------------------------------------------------------------------

class TestUiBridgeInventory(unittest.TestCase):
    def test_every_b_call_method_exists_on_api(self):
        called = set()
        for name in ("index.html", "island.html", "invite.html"):
            html = (UI_DIR / name).read_text(encoding="utf-8")
            called.update(re.findall(r"B\.call\('([^']+)'", html))
        # bridge.js calls api.log_error directly, not via B.call.
        bridge = (UI_DIR / "assets" / "bridge.js").read_text(
            encoding="utf-8")
        called.update(re.findall(r"api\.(\w+)\(", bridge))
        self.assertTrue(called, "no bridge calls found in the UI")
        missing = sorted(n for n in called
                         if not hasattr(main_mod.Api, n))
        self.assertEqual(missing, [],
                         "UI calls bridge methods missing on Api: %s"
                         % missing)

    def test_critical_reskin_methods_present(self):
        for name in ("get_device_overview", "save_settings", "chat",
                     "job_result", "get_update_status", "clean_cache",
                     "get_clean_cache_result_v2", "island_expand",
                     "island_collapse", "log_error",
                     "request_deep_file_access", "get_tasks",
                     "check_owner_code", "export_backup",
                     "get_morning_brief", "set_muted", "get_themes_v2"):
            self.assertTrue(hasattr(main_mod.Api, name),
                            "Api is missing %s" % name)


# ---------------------------------------------------------------------------
# (c) save_settings flat -> canonical round-trip
# ---------------------------------------------------------------------------

class TestSaveSettingsRoundTrip(_ApiCase):
    def test_flat_patch_saves_canonical_and_reads_back_aliased(self):
        with mock.patch.object(main_mod.MemoApp, "_start_monitor"):
            res = self.api.save_settings({
                "name": "لونا", "user": "Mohamed", "lang": "en",
                "greet": 1, "r1": 400, "vc": "m1", "theme": "lg",
                "muted": 1, "lite": 0,
            })
        self.assertTrue(res.get("ok"))
        st = self.app.settings
        self.assertEqual(st.get("ai_name"), "لونا")
        self.assertEqual(st.get("user_name"), "Mohamed")
        self.assertEqual(st.get("language"), "en")
        self.assertEqual(st.get("monitor_ram_free_mb"), 400)
        self.assertEqual(st.get("voice"), "boy_calm")
        self.assertEqual(st.get("theme"), "lg")
        self.assertTrue(st.get("muted"))
        back = self.api.get_settings()
        self.assertEqual(back.get("name"), "لونا")
        self.assertEqual(back.get("r1"), 400)
        self.assertEqual(back.get("vc"), "m1")
        self.assertEqual(back.get("theme"), "lg")
        # The canonical keys really persisted to the temp settings file.
        saved = json.loads((self.tmp / "settings.json").read_text(
            encoding="utf-8"))
        self.assertEqual(saved.get("ai_name"), "لونا")
        self.assertEqual(saved.get("monitor_ram_free_mb"), 400)


# ---------------------------------------------------------------------------
# (d) request_deep_file_access() with no arg
# ---------------------------------------------------------------------------

class TestDeepFileAccessNoArg(_ApiCase):
    def test_no_arg_returns_pending_id_and_message(self):
        res = self.api.request_deep_file_access()
        self.assertTrue(res.get("pending"))
        self.assertTrue(res.get("id"))
        self.assertTrue(res.get("message"))

    def test_raw_method_accepts_none(self):
        res = self.api._request_deep_file_access_raw(None)
        self.assertTrue(res.get("pending"))
        self.assertTrue(res.get("id"))


# ---------------------------------------------------------------------------
# (e) get_themes_v2 includes Liquid Glass 'lg'
# ---------------------------------------------------------------------------

class TestThemesV2(_ApiCase):
    def test_five_themes_including_lg(self):
        res = self.api.get_themes_v2()
        ids = [t["id"] for t in res.get("themes", [])]
        self.assertEqual(ids, ["lg", "night", "graphite", "calm", "old"])
        self.assertIn(res.get("current"), ids)

    def test_set_theme_v2_lg_round_trips(self):
        res = self.api.set_theme_v2("lg")
        self.assertTrue(res.get("ok"))
        self.assertEqual(res.get("theme"), "lg")
        # Stored through v1's set_theme (translated id).
        self.assertEqual(self.app.settings.get("theme"), "night-glass")
        self.assertEqual(self.api.get_themes_v2().get("current"), "lg")


if __name__ == "__main__":
    unittest.main()
