# LONA v1.6.4 rc.3 - habit learning, first HONEST version
# (OpenJarvis learning loop, local-only).
#
# This file proves:
#   * recording stores hour + coarse category ONLY - message text
#     never reaches the file;
#   * the off switch is respected (record + suggestion both refuse
#     when habits_enabled is False) and the store is clearable;
#   * the suggestion comes ONLY from real repeated data (>= 3 events
#     in the hour window, over >= 2 different days, one category
#     clearly on top) - with no data, one day of data, or a tie it
#     returns not_enough_data and the UI shows nothing;
#   * the suggestion carries a real working action (morning brief
#     or focus session) with an Arabic label;
#   * the chat hook in bridge_v2 records a category per turn
#     (bridge layer only - brain.py untouched) and never blocks
#     the chat;
#   * the store lives under DATA_DIR (habits.json) and old events
#     are pruned.
#
#   python3 -m pytest app/tests/test_v164_rc3_habits.py -q

import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

UI_DIR = APP_DIR / "ui"
INDEX = UI_DIR / "index.html"
CSS = UI_DIR / "assets" / "lona.css"

import bridge_v2  # noqa: E402
import habits as habits_mod  # noqa: E402
import settings_store  # noqa: E402


def _read(path):
    return Path(path).read_text(encoding="utf-8")


class _HabitsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.habits_path = Path(self.tmp.name) / "habits.json"
        self.orig_path = habits_mod._HABITS_PATH
        self.patch = mock.patch.object(habits_mod, "_HABITS_PATH",
                                       self.habits_path)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.settings = {"habits_enabled": True}

    def _record(self, category, day, hour, kind="chat"):
        return habits_mod.record_event(
            kind, category, self.settings,
            datetime.fromisoformat(day) + timedelta(hours=hour))

    def _now(self, day, hour):
        return datetime.fromisoformat(day) + timedelta(hours=hour)


class TestCategorize(_HabitsCase):
    def test_arabic_study_keywords(self):
        self.assertEqual(habits_mod.categorize("امتحان CS 309 امتى؟"),
                         "study")
        self.assertEqual(habits_mod.categorize("عايز أذاكر محاضرة 3"),
                         "study")

    def test_arabic_delivery_keywords(self):
        self.assertEqual(habits_mod.categorize("عندي شيفت توصيل 8 الصبح"),
                         "delivery")
        self.assertEqual(habits_mod.categorize("طلب أمازون وصل؟"),
                         "delivery")

    def test_device_and_money_keywords(self):
        self.assertEqual(habits_mod.categorize("البطارية كام في المية؟"),
                         "device")
        self.assertEqual(habits_mod.categorize("الراتب نزل؟"), "money")

    def test_english_keywords_and_fallback(self):
        self.assertEqual(habits_mod.categorize("when is my exam?"),
                         "study")
        self.assertEqual(habits_mod.categorize("hello, how are you?"),
                         "general")
        self.assertEqual(habits_mod.categorize(""), "general")
        self.assertEqual(habits_mod.categorize("   "), "general")


class TestRecording(_HabitsCase):
    def test_record_stores_hour_and_category_never_text(self):
        secret = "امتحان الفيزيا صعب أوي يا لونا ساعديني"
        res = habits_mod.record_chat(secret, self.settings,
                                     self._now("2026-11-20", 7))
        self.assertTrue(res["ok"])
        stored = _read(self.habits_path)
        self.assertNotIn(secret, stored)
        self.assertNotIn("الفيزيا", stored)
        data = habits_mod._load()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["hour"], 7)
        self.assertEqual(data[0]["category"], "study")
        self.assertEqual(data[0]["day"], "2026-11-20")
        self.assertEqual(data[0]["kind"], "chat")

    def test_record_app_open(self):
        res = habits_mod.record_app_open(self.settings,
                                         self._now("2026-11-20", 7))
        self.assertTrue(res["ok"])
        data = habits_mod._load()
        self.assertEqual(data[0]["kind"], "open")
        self.assertEqual(data[0]["category"], "general")

    def test_off_switch_blocks_recording(self):
        off = {"habits_enabled": False}
        res = habits_mod.record_chat("امتحان امتى؟", off,
                                     self._now("2026-11-20", 7))
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "disabled")
        self.assertFalse(self.habits_path.exists())
        res = habits_mod.record_app_open(off,
                                         self._now("2026-11-20", 7))
        self.assertFalse(res["ok"])

    def test_empty_chat_not_recorded(self):
        res = habits_mod.record_chat("   ", self.settings)
        self.assertFalse(res["ok"])
        self.assertFalse(self.habits_path.exists())

    def test_old_events_pruned(self):
        old = datetime.now() - timedelta(days=40)
        habits_mod.record_event("chat", "study", self.settings, old)
        habits_mod.record_event("chat", "study", self.settings,
                                datetime.now())
        data = habits_mod._load()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["day"],
                         datetime.now().date().isoformat())

    def test_store_lives_under_data_dir(self):
        self.assertEqual(self.orig_path.name, "habits.json")
        self.assertEqual(str(self.orig_path),
                         str(settings_store.DATA_DIR / "habits.json"))


class TestSuggestion(_HabitsCase):
    def _seed_study_mornings(self, days=("2026-11-20", "2026-11-21",
                                         "2026-11-22")):
        for day in days:
            self._record("study", day, 7)
            self._record("study", day, 8)

    def test_no_data_no_suggestion(self):
        res = habits_mod.get_suggestion(self.settings,
                                        self._now("2026-11-23", 7))
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "not_enough_data")

    def test_one_day_of_data_no_suggestion(self):
        self._record("study", "2026-11-22", 7)
        self._record("study", "2026-11-22", 7)
        self._record("study", "2026-11-22", 8)
        res = habits_mod.get_suggestion(self.settings,
                                        self._now("2026-11-23", 7))
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "not_enough_data")

    def test_repeated_mornings_suggest_study(self):
        self._seed_study_mornings()
        res = habits_mod.get_suggestion(self.settings,
                                        self._now("2026-11-23", 7))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["category"], "study")
        self.assertEqual(res["action"], "focus")
        self.assertEqual(res["action_label"], "ابدأ جلسة تركيز")
        self.assertIn("تذاكر", res["text"])
        self.assertGreaterEqual(res["events"], 3)
        self.assertGreaterEqual(res["days"], 2)

    def test_delivery_mornings_suggest_brief(self):
        for day in ("2026-11-20", "2026-11-21"):
            self._record("delivery", day, 7)
            self._record("delivery", day, 8)
        res = habits_mod.get_suggestion(self.settings,
                                        self._now("2026-11-22", 8))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["category"], "delivery")
        self.assertEqual(res["action"], "brief")
        self.assertEqual(res["action_label"], "افتح نشرة الصباح")

    def test_tie_is_not_a_habit(self):
        for day in ("2026-11-20", "2026-11-21"):
            self._record("study", day, 7)
            self._record("delivery", day, 7)
        res = habits_mod.get_suggestion(self.settings,
                                        self._now("2026-11-22", 7))
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "not_enough_data")

    def test_wrong_hour_no_suggestion(self):
        self._seed_study_mornings()
        res = habits_mod.get_suggestion(self.settings,
                                        self._now("2026-11-23", 20))
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "not_enough_data")

    def test_disabled_no_suggestion(self):
        self._seed_study_mornings()
        res = habits_mod.get_suggestion({"habits_enabled": False},
                                        self._now("2026-11-23", 7))
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "disabled")

    def test_events_older_than_14_days_ignored(self):
        for day in ("2026-11-01", "2026-11-02"):
            self._record("study", day, 7)
            self._record("study", day, 7)
        res = habits_mod.get_suggestion(self.settings,
                                        self._now("2026-11-23", 7))
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "not_enough_data")


class TestClearAndStatus(_HabitsCase):
    def test_clear_wipes_store(self):
        self._record("study", "2026-11-22", 7)
        self.assertTrue(self.habits_path.exists())
        res = habits_mod.clear()
        self.assertTrue(res["ok"])
        self.assertFalse(self.habits_path.exists())
        self.assertEqual(habits_mod._load(), [])
        # Clearing twice is fine.
        self.assertTrue(habits_mod.clear()["ok"])

    def test_status_counts_events_and_days(self):
        self._record("study", "2026-11-20", 7)
        self._record("delivery", "2026-11-21", 8)
        self._record("study", "2026-11-21", 9)
        st = habits_mod.status(self.settings)
        self.assertTrue(st["ok"])
        self.assertTrue(st["enabled"])
        self.assertEqual(st["events"], 3)
        self.assertEqual(st["days"], 2)

    def test_status_disabled(self):
        st = habits_mod.status({"habits_enabled": False})
        self.assertFalse(st["enabled"])
        self.assertEqual(st["events"], 0)


class TestBridgeChatHook(_HabitsCase):
    def _fake_api(self, settings):
        class FakeApp:
            def __init__(self):
                self.settings = dict(settings)
        api = mock.Mock()
        api._app = FakeApp()
        api._chat_raw = mock.Mock(
            return_value={"reply": "ok", "engine": "groq",
                          "chat_id": "abc"})
        # _main_module falls back to api._app when the module has no
        # _APP; Mock's class module is unittest.mock, which has none.
        return api

    def test_chat_records_category_not_text(self):
        api = self._fake_api(self.settings)
        secret = "امتحان CS 309 امتى يا لونا؟"
        res = bridge_v2.chat(api, secret)
        self.assertEqual(res["reply"], "ok")
        api._chat_raw.assert_called_once_with(secret)
        data = habits_mod._load()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["category"], "study")
        self.assertNotIn(secret, _read(self.habits_path))

    def test_chat_hook_respects_off_switch(self):
        api = self._fake_api({"habits_enabled": False})
        bridge_v2.chat(api, "امتحان امتى؟")
        self.assertFalse(self.habits_path.exists())

    def test_chat_hook_never_blocks_chat(self):
        api = self._fake_api(self.settings)
        with mock.patch.object(habits_mod, "record_chat",
                               side_effect=RuntimeError("boom")):
            res = bridge_v2.chat(api, "امتحان امتى؟")
        self.assertEqual(res["reply"], "ok")

    def test_bridge_suggestion_and_clear(self):
        api = self._fake_api(self.settings)
        res = bridge_v2.get_habit_suggestion(api)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "not_enough_data")
        for day in ("2026-11-20", "2026-11-21"):
            self._record("delivery", day, 7)
            self._record("delivery", day, 8)
        # Suggestion at 7-8 in the morning window.
        import datetime as _dt
        now = _dt.datetime(2026, 11, 22, 7, 30)
        res = habits_mod.get_suggestion(self.settings, now)
        self.assertTrue(res["ok"])
        res = bridge_v2.clear_habits(api)
        self.assertTrue(res["ok"])
        self.assertFalse(self.habits_path.exists())
        st = bridge_v2.get_habits_status(api)
        self.assertEqual(st["events"], 0)


class TestHabitUI(unittest.TestCase):
    def test_habit_card_hidden_by_default_and_wired(self):
        html = _read(INDEX)
        self.assertIn('id="habitCard" hidden', html)
        self.assertIn('id="habitText"', html)
        self.assertIn('id="habitAction"', html)
        self.assertIn('id="habitDismiss"', html)
        self.assertIn("B.call('get_habit_suggestion')", html)
        self.assertIn("refreshHabitSuggestion()", html)
        self.assertIn("B.call('focus_start')", html)

    def test_no_fake_suggestion_text_in_ui(self):
        html = _read(INDEX)
        # The card is filled ONLY from the backend response; there
        # is no hardcoded suggestion string in the UI.
        self.assertNotIn("عادةً بتذاكر", html)
        self.assertNotIn("عادةً بتبدأ شغلك", html)

    def test_habit_card_css(self):
        css = _read(CSS)
        self.assertIn(".habit-card", css)
        self.assertIn(".habit-card .habit-text", css)

    def test_suggestion_actions_are_real_bridge_calls(self):
        html = _read(INDEX)
        # focus action -> companion focus session; brief action ->
        # the same morning-brief button the hero already uses.
        self.assertIn("habitActionKind === 'focus'", html)
        self.assertIn("document.getElementById('briefBtn').click()",
                      html)


if __name__ == "__main__":
    unittest.main()
