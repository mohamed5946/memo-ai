# LONA v1.6.4 rc.3 - one-tap modes / presets (OpenJarvis presets).
#
# This file proves:
#   * each preset changes REAL settings the app code reads (every
#     bundle key is a settings_store DEFAULT and is read somewhere:
#     _on_system_alert / companion loop / startup);
#   * the active mode is stored (active_mode) and shown in the UI
#     (Modes section, current-mode line + selected card);
#   * switching back to normal restores the pre-mode values (and
#     chaining modes keeps the ORIGINAL pre-mode values);
#   * with no saved previous bundle, normal falls back to the
#     shipped DEFAULTS - and says so;
#   * the bridge translates the new keys, the UI has a 15th Settings
#     section (الأوضاع) with working mode buttons, and Api exposes
#     get_modes / apply_mode (no dead bridge calls);
#   * the toast gating is real: important-only mode toasts critical
#     alerts and important notifications but not routine warnings;
#     monitor_alert_toast off silences the Windows toast AND the
#     island toast (the island stays collapsed).
#
#   python3 -m pytest app/tests/test_v164_rc3_modes.py -q

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
INDEX = UI_DIR / "index.html"
CSS = UI_DIR / "assets" / "lona.css"

import bridge_v2  # noqa: E402
import companion as companion_mod  # noqa: E402
import modes as modes_mod  # noqa: E402
import settings_store  # noqa: E402

# main.py cannot be imported as-is here (no pywebview on Linux), so
# a minimal webview stub is installed in sys.modules first - the
# same trick as test_reskin_bridge.py.
_fake_webview = _types.ModuleType("webview")
_fake_webview.create_window = lambda *a, **k: None
_fake_webview.start = lambda *a, **k: None
_fake_webview.windows = []
sys.modules.setdefault("webview", _fake_webview)

import main as main_mod  # noqa: E402


def _read(path):
    return Path(path).read_text(encoding="utf-8")


class _FakeApp:
    def __init__(self, settings):
        self.settings = dict(settings)
        self._companion = None
        self._notif_watcher = None
        self._monitor = None


class _FakeApi:
    """Minimal Api stand-in: _app for the bridge's _app() seam, and
    a save_settings that applies the translated patch in memory
    (the real persistence path is covered by the reskin tests)."""

    def __init__(self, settings):
        self._app = _FakeApp(settings)

    def save_settings(self, patch):
        split = bridge_v2.translate_save_settings_patch(patch)
        for key, value in split["settings"].items():
            self._app.settings[key] = value
        return {"ok": True}


class TestModeBundlesAreRealSettings(unittest.TestCase):
    def test_every_bundle_key_is_a_default(self):
        for mode_id, spec in modes_mod.MODES.items():
            for key in spec["settings"]:
                self.assertIn(key, settings_store.DEFAULTS,
                              f"{mode_id} changes {key}, which is not "
                              "a real setting")
        self.assertIn("active_mode", settings_store.DEFAULTS)

    def test_every_bundle_key_is_read_somewhere(self):
        # Each bundle key must be read by app code - a key nothing
        # reads is a fake toggle. (notifications_enabled: main
        # _start_notifications + save live-apply; monitor_*: main
        # _on_system_alert; toasts_important_only: same;
        # study_reminders_enabled: companion loop;
        # exam_countdown_pinned: index.html hero; morning_brief_enabled:
        # companion.morning_brief.)
        readers = {
            "notifications_enabled": _read(APP_DIR / "main.py"),
            "monitor_alert_toast": _read(APP_DIR / "main.py"),
            "monitor_alert_in_app": _read(APP_DIR / "main.py"),
            "toasts_important_only": _read(APP_DIR / "main.py"),
            "study_reminders_enabled": _read(APP_DIR / "companion.py"),
            "exam_countdown_pinned": _read(INDEX),
            "morning_brief_enabled": _read(APP_DIR / "companion.py"),
        }
        for mode_id, spec in modes_mod.MODES.items():
            for key in spec["settings"]:
                self.assertIn(key, readers[key],
                              f"{mode_id}: {key} is never read")

    def test_three_modes_arabic_labels_no_emoji(self):
        modes = modes_mod.list_modes()
        self.assertEqual([m["id"] for m in modes],
                         ["exam", "work", "quiet"])
        labels = [m["label_ar"] for m in modes]
        self.assertEqual(labels, ["وضع الامتحانات", "وضع الشغل",
                                  "وضع هادي"])
        for mode in modes:
            for field in ("label_ar", "description_ar"):
                text = mode[field]
                self.assertTrue(text.strip(), field)
                # No emoji anywhere in the mode UI strings.
                self.assertFalse(any(ord(c) > 0x2500 for c in text),
                                 f"emoji in {mode['id']} {field}: {text}")


class TestApplyAndRestore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prev_path = Path(self.tmp.name) / "mode_previous.json"
        # The unpatched default location, captured BEFORE patching.
        self.orig_prev = modes_mod._PREVIOUS_PATH
        self.patch = mock.patch.object(modes_mod, "_PREVIOUS_PATH",
                                       self.prev_path)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.tmp.cleanup)
        # His normal: notifications on, toasts on, reminders on,
        # countdown not pinned - the values restore must bring back.
        self.normal = dict(settings_store.DEFAULTS)
        self.normal.update({
            "notifications_enabled": True,
            "monitor_alert_toast": True,
            "monitor_alert_in_app": True,
            "toasts_important_only": False,
            "study_reminders_enabled": True,
            "exam_countdown_pinned": False,
            "morning_brief_enabled": True,
            "active_mode": "",
        })

    def test_exam_mode_patch_changes_real_settings(self):
        res = modes_mod.apply_mode(dict(self.normal), "exam")
        self.assertTrue(res["ok"])
        patch = res["patch"]
        self.assertEqual(patch["active_mode"], "exam")
        self.assertTrue(patch["toasts_important_only"])
        self.assertTrue(patch["study_reminders_enabled"])
        self.assertTrue(patch["exam_countdown_pinned"])
        self.assertTrue(patch["monitor_alert_toast"])
        self.assertTrue(patch["notifications_enabled"])

    def test_work_mode_patch(self):
        res = modes_mod.apply_mode(dict(self.normal), "work")
        self.assertTrue(res["ok"])
        patch = res["patch"]
        self.assertTrue(patch["notifications_enabled"])
        self.assertTrue(patch["monitor_alert_toast"])
        self.assertFalse(patch["toasts_important_only"])
        self.assertFalse(patch["study_reminders_enabled"])
        self.assertFalse(patch["exam_countdown_pinned"])

    def test_quiet_mode_patch(self):
        res = modes_mod.apply_mode(dict(self.normal), "quiet")
        self.assertTrue(res["ok"])
        patch = res["patch"]
        self.assertFalse(patch["notifications_enabled"])
        self.assertFalse(patch["monitor_alert_toast"])
        self.assertFalse(patch["monitor_alert_in_app"])
        self.assertFalse(patch["study_reminders_enabled"])

    def test_unknown_mode_refused(self):
        res = modes_mod.apply_mode(dict(self.normal), "party")
        self.assertFalse(res["ok"])

    def test_restore_returns_pre_mode_values(self):
        settings = dict(self.normal)
        modes_mod.apply_mode(settings, "exam")
        # Simulate the persisted state: the bundle applied.
        settings.update(modes_mod.MODES["exam"]["settings"])
        settings["active_mode"] = "exam"
        res = modes_mod.restore_normal(settings)
        self.assertTrue(res["ok"])
        self.assertEqual(res["mode"], "")
        self.assertEqual(res["restored_from"], "previous")
        for key in modes_mod.MODE_KEYS:
            self.assertEqual(res["patch"][key], self.normal[key],
                             f"restore changed {key}")

    def test_chaining_modes_keeps_original_normal(self):
        settings = dict(self.normal)
        modes_mod.apply_mode(settings, "exam")
        settings.update(modes_mod.MODES["exam"]["settings"])
        settings["active_mode"] = "exam"
        # Straight to work: the snapshot must still be the ORIGINAL
        # normal, not the exam bundle.
        modes_mod.apply_mode(settings, "work")
        settings.update(modes_mod.MODES["work"]["settings"])
        settings["active_mode"] = "work"
        res = modes_mod.restore_normal(settings)
        self.assertEqual(res["restored_from"], "previous")
        for key in modes_mod.MODE_KEYS:
            self.assertEqual(res["patch"][key], self.normal[key],
                             f"chained restore changed {key}")

    def test_restore_without_snapshot_falls_back_to_defaults(self):
        res = modes_mod.restore_normal(dict(self.normal))
        self.assertTrue(res["ok"])
        self.assertEqual(res["restored_from"], "defaults")
        for key in modes_mod.MODE_KEYS:
            self.assertEqual(res["patch"][key],
                             settings_store.DEFAULTS[key])

    def test_previous_bundle_lives_under_data_dir(self):
        self.assertEqual(self.orig_prev.name, "mode_previous.json")
        # Default (unpatched) location is under DATA_DIR.
        self.assertEqual(str(self.orig_prev),
                         str(settings_store.DATA_DIR
                             / "mode_previous.json"))


class TestBridgeModes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prev_path = Path(self.tmp.name) / "mode_previous.json"
        self.patch = mock.patch.object(modes_mod, "_PREVIOUS_PATH",
                                       self.prev_path)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def _api(self, **overrides):
        settings = dict(settings_store.DEFAULTS)
        settings.update(overrides)
        return _FakeApi(settings)

    def test_get_modes_lists_three_and_active(self):
        api = self._api(active_mode="work")
        res = bridge_v2.get_modes(api)
        self.assertTrue(res["ok"])
        self.assertEqual(res["active"], "work")
        self.assertEqual([m["id"] for m in res["modes"]],
                         ["exam", "work", "quiet"])

    def test_apply_mode_through_bridge_persists_and_marks_active(self):
        api = self._api()
        res = bridge_v2.apply_mode(api, "exam")
        self.assertTrue(res["ok"])
        self.assertEqual(res["mode"], "exam")
        self.assertTrue(api._app.settings["toasts_important_only"])
        self.assertTrue(api._app.settings["exam_countdown_pinned"])
        self.assertEqual(api._app.settings["active_mode"], "exam")

    def test_apply_normal_through_bridge_restores(self):
        api = self._api()
        bridge_v2.apply_mode(api, "quiet")
        self.assertEqual(api._app.settings["active_mode"], "quiet")
        self.assertFalse(api._app.settings["monitor_alert_toast"])
        res = bridge_v2.apply_mode(api, "normal")
        self.assertTrue(res["ok"])
        self.assertEqual(res["mode"], "")
        self.assertEqual(res["restored_from"], "previous")
        self.assertTrue(api._app.settings["monitor_alert_toast"])
        self.assertEqual(api._app.settings["active_mode"], "")

    def test_apply_unknown_mode_fails_without_touching_settings(self):
        api = self._api()
        before = dict(api._app.settings)
        res = bridge_v2.apply_mode(api, "party")
        self.assertFalse(res["ok"])
        self.assertEqual(api._app.settings, before)

    def test_translate_accepts_new_keys_and_validates_active_mode(self):
        split = bridge_v2.translate_save_settings_patch({
            "habits_enabled": 0, "toasts_important_only": 1,
            "study_reminders_enabled": 1, "exam_countdown_pinned": 1,
            "active_mode": "exam"})
        self.assertEqual(split["settings"]["habits_enabled"], False)
        self.assertEqual(split["settings"]["toasts_important_only"], True)
        self.assertEqual(split["settings"]["study_reminders_enabled"],
                         True)
        self.assertEqual(split["settings"]["exam_countdown_pinned"], True)
        self.assertEqual(split["settings"]["active_mode"], "exam")
        split = bridge_v2.translate_save_settings_patch(
            {"active_mode": "party"})
        self.assertNotIn("active_mode", split["settings"])
        self.assertIn("active_mode", split["dropped"])

    def test_get_settings_carries_habits_alias(self):
        settings = dict(settings_store.DEFAULTS)
        settings["habits_enabled"] = False
        aliased = bridge_v2.with_v2_aliases(settings)
        self.assertEqual(aliased["habits"], 0)
        self.assertEqual(aliased["habits_enabled"], False)
        self.assertEqual(aliased["active_mode"], "")


class TestToastGatingIsReal(unittest.TestCase):
    """_on_system_alert with a bare MemoApp (no __init__): the toast
    channels must actually follow the settings."""

    def _memo_app(self, settings):
        app = object.__new__(main_mod.MemoApp)
        app.settings = dict(settings)
        app._alerts = []
        app._alerts_lock = __import__("threading").Lock()
        app._alert_seq = 0
        app.island_window = None
        return app

    def test_important_only_suppresses_routine_warn(self):
        app = self._memo_app({"monitor_alert_in_app": True,
                              "monitor_alert_toast": True,
                              "toasts_important_only": True,
                              "island_enabled": False})
        with mock.patch("notifications.show_toast") as toast:
            app._on_system_alert({"kind": "routine_suggestion",
                                  "level": "info",
                                  "title_ar": "لونا",
                                  "message_ar": "معاد المذاكرة",
                                  "at": "2026-11-23 07:00:00"})
        toast.assert_not_called()

    def test_important_only_allows_critical(self):
        app = self._memo_app({"monitor_alert_in_app": True,
                              "monitor_alert_toast": True,
                              "toasts_important_only": True,
                              "island_enabled": False})
        with mock.patch("notifications.show_toast") as toast:
            app._on_system_alert({"kind": "ram_low",
                                  "level": "critical",
                                  "title_ar": "الرام",
                                  "message_ar": "الرام خلصت",
                                  "at": "2026-11-23 07:00:00"})
        self.assertEqual(toast.call_count, 1)

    def test_important_only_allows_important_notification(self):
        app = self._memo_app({"monitor_alert_in_app": True,
                              "monitor_alert_toast": True,
                              "toasts_important_only": True,
                              "island_enabled": False})
        with mock.patch("notifications.show_toast") as toast:
            app._on_system_alert({"kind": "notification",
                                  "level": "warn",
                                  "title_ar": "إشعار مهم",
                                  "message_ar": "وصل إشعار مهم من واتساب",
                                  "at": "2026-11-23 07:00:00"})
        self.assertEqual(toast.call_count, 1)

    def test_toast_off_silences_everything(self):
        app = self._memo_app({"monitor_alert_in_app": True,
                              "monitor_alert_toast": False,
                              "toasts_important_only": False,
                              "island_enabled": True})
        island = mock.Mock()
        app.island_window = island
        with mock.patch("notifications.show_toast") as toast:
            app._on_system_alert({"kind": "ram_low",
                                  "level": "critical",
                                  "title_ar": "الرام",
                                  "message_ar": "الرام خلصت",
                                  "at": "2026-11-23 07:00:00"})
        toast.assert_not_called()
        island.evaluate_js.assert_not_called()

    def test_toast_on_expands_island(self):
        app = self._memo_app({"monitor_alert_in_app": True,
                              "monitor_alert_toast": True,
                              "toasts_important_only": False,
                              "island_enabled": True})
        island = mock.Mock()
        app.island_window = island
        with mock.patch("notifications.show_toast"):
            app._on_system_alert({"kind": "ram_low",
                                  "level": "warn",
                                  "title_ar": "الرام",
                                  "message_ar": "الرام قليلة",
                                  "at": "2026-11-23 07:00:00"})
        self.assertEqual(island.evaluate_js.call_count, 1)
        self.assertIn("showIslandToast",
                      island.evaluate_js.call_args[0][0])


class TestStudyRemindersGate(unittest.TestCase):
    """The companion loop only suggests routines when
    study_reminders_enabled is on."""

    def _companion(self, reminders_on):
        settings = {"companion_enabled": True,
                    "study_reminders_enabled": reminders_on,
                    "quiet_hours_enabled": False}
        store = mock.Mock()
        store.data = {"routines": [{"text": "study at "
                                             f"{__import__('datetime').datetime.now().hour}:00"}]}
        comp = companion_mod.Companion(
            settings, on_alert=None, monitor_fn=None,
            memory_store=store, idle_fn=lambda: None,
            fullscreen_fn=lambda: False)
        return comp

    def _run_one_loop_pass(self, comp):
        import threading
        thread = threading.Thread(target=comp._loop, daemon=True)
        thread.start()
        import time
        time.sleep(0.5)
        comp._stop.set()
        thread.join(timeout=3)

    def test_reminders_off_pushes_nothing(self):
        comp = self._companion(False)
        self._run_one_loop_pass(comp)
        kinds = [i["kind"] for i in comp.get_feed()]
        self.assertNotIn("routine_suggestion", kinds)

    def test_reminders_on_suggests_at_usual_hour(self):
        comp = self._companion(True)
        self._run_one_loop_pass(comp)
        kinds = [i["kind"] for i in comp.get_feed()]
        self.assertIn("routine_suggestion", kinds)


class TestModesUI(unittest.TestCase):
    def test_fifteen_sections_modes_second(self):
        html = _read(INDEX)
        sec = re.search(r"var SEC = \[(.*?)\];", html, re.S)
        self.assertIsNotNone(sec, "SEC list missing")
        sec_ids = re.findall(r"\['([a-z]+)',", sec.group(1))
        self.assertEqual(len(sec_ids), 15,
                         f"expected 15 sections, got {sec_ids}")
        self.assertEqual(sec_ids[1], "modes")

    def test_modes_section_builder_and_icon(self):
        html = _read(INDEX)
        self.assertIn("modes: function ()", html)
        self.assertIn("modes:", html.split("var ICON = {")[1]
                      .split("};")[0])
        self.assertIn("الوضع الحالي", html)
        self.assertIn("رجوع للوضع العادي", html)
        # The mode names/descriptions come from the backend
        # (get_modes), not hardcoded in the HTML - see
        # TestModeBundlesAreRealSettings for the Arabic labels.

    def test_mode_buttons_call_apply_mode(self):
        html = _read(INDEX)
        self.assertIn("data-mode='", html)
        self.assertIn("B.call('apply_mode', d.mode)", html)
        self.assertIn("B.call('get_modes')", html)
        self.assertIn("B.call('get_habits_status')", html)

    def test_habits_toggle_and_clear_in_modes_section(self):
        html = _read(INDEX)
        self.assertIn("tg('habits', 'لونا تتعلم من عاداتك')", html)
        self.assertIn("data-hclear", html)
        self.assertIn("B.call('clear_habits')", html)
        self.assertIn("habits_enabled: S.habits", html)

    def test_exam_hero_pinned_by_setting(self):
        html = _read(INDEX)
        self.assertIn('id="examHero"', html)
        self.assertIn("S.exam_countdown_pinned", html)
        css = _read(CSS)
        self.assertIn(".exam-hero", css)

    def test_api_exposes_mode_calls(self):
        for name in ("get_modes", "apply_mode", "get_habit_suggestion",
                     "clear_habits", "get_habits_status"):
            self.assertTrue(callable(getattr(main_mod.Api, name, None)),
                            f"Api.{name} missing - dead bridge call")


if __name__ == "__main__":
    unittest.main()
