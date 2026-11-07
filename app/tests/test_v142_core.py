# Memo AI v1.4.2 - headless tests for the parent-built core
# (run on Linux, no Windows, no keys, no network except mocked):
#
#   python -m unittest app.tests.test_v142_core -v
#
# What is proven here:
#   * long memory: save -> new session (reload) -> ask -> the brain
#     answers from memory; delete -> ask -> it does not; wipe-all.
#   * learning: a correction persists across reload and lands in the
#     system prompt; Egyptian dialect + masculine forms are in the
#     prompt; tone learning ("اختصر" -> short); routines + preferences.
#   * guests (memory_enabled=False) never write the owner's memory.
#   * history window is 40 messages now, and old turns are summarized
#     into the memory file instead of silently dropped (rollover).
#   * web_search: the brain calls it for unknown facts and the answer
#     uses the results; a failed search is reported honestly; the
#     system prompt carries the search rule.
#   * send_whatsapp_message: default owner number works without a
#     phone; explicit phone works; a failed send is reported honestly
#     (never a false success); WhatsApp not linked -> honest error.
#   * in-chat permissions: a request appears in get_permission_state()
#     (pending + resolved), Allow runs, Deny cancels, a 120s timeout
#     resolves as "timeout"; the UI builds chat cards, not a modal.
#   * updater manifest fetch retries after a transient timeout.
#   * system_status returns real numbers from the monitor.
#
# NOT proven here (needs real Windows + live Groq + the user's phone):
# the real Groq model CHOOSING web_search/send/system_status, the real
# Baileys send, the real chat-card rendering in WebView2. Field tests
# in the release notes cover those.

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

import brain  # noqa: E402
import memory as memory_mod  # noqa: E402
import tools  # noqa: E402
import updater  # noqa: E402


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class _MemoryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = memory_mod.MemoryStore(self.tmp / "memory.json")
        brain.set_memory_store(self.store)
        brain.clear_history()

    def tearDown(self):
        brain.set_memory_store(None)
        brain.clear_history()


class TestLongMemory(_MemoryCase):
    def test_save_reload_ask_answers_from_memory(self):
        # Save a fact, simulate a restart (fresh store, same file),
        # then ask - the fact must reach the model's system prompt.
        self.store.add_fact("مشروع التخرج عن الذكاء الاصطناعي",
                            source="explicit")
        reloaded = memory_mod.MemoryStore(self.tmp / "memory.json")
        brain.set_memory_store(reloaded)
        seen = {}

        def fake_groq(messages, use_tools=False, timeout=60):
            seen["messages"] = messages
            return {"content": "مشروعك عن الذكاء الاصطناعي."}

        with mock.patch.object(brain, "is_online", return_value=True), \
                mock.patch.object(brain, "_groq_message",
                                  side_effect=fake_groq):
            result = brain.ask_with_tools(
                "مشروع التخرج عن إيه؟", "Memo", "Mohamed", "ar")
        self.assertEqual(result["engine"], "groq")
        system = seen["messages"][0]["content"]
        self.assertIn("الذكاء الاصطناعي", system)

    def test_delete_fact_then_ask_does_not_answer_with_it(self):
        fid = self.store.add_fact("مشروع التخرج عن الذكاء الاصطناعي",
                                  source="explicit")
        self.assertTrue(self.store.delete_fact(fid))
        seen = {}

        def fake_groq(messages, use_tools=False, timeout=60):
            seen["messages"] = messages
            return {"content": "معرفش."}

        with mock.patch.object(brain, "is_online", return_value=True), \
                mock.patch.object(brain, "_groq_message",
                                  side_effect=fake_groq):
            brain.ask_with_tools("مشروع التخرج عن إيه؟",
                                 "Memo", "Mohamed", "ar")
        self.assertNotIn("الذكاء الاصطناعي",
                         seen["messages"][0]["content"])

    def test_remember_command_via_brain_no_model_call(self):
        calls = []

        def fake_groq(messages, use_tools=False, timeout=60):
            calls.append(messages)
            return {"content": "should not happen"}

        with mock.patch.object(brain, "is_online", return_value=True), \
                mock.patch.object(brain, "_groq_message",
                                  side_effect=fake_groq):
            result = brain.ask_with_tools(
                "افتكر إن عندي امتحان CS 303 يوم الخميس",
                "Memo", "Mohamed", "ar")
        self.assertEqual(result["engine"], "memory")
        self.assertEqual(calls, [])  # answered locally, no model call
        self.assertEqual(len(self.store.data["facts"]), 1)

    def test_forget_one_command_does_not_wipe_all(self):
        self.store.add_fact("عندي امتحان CS 303 يوم الخميس")
        self.store.add_fact("شغلي بيبدأ الساعة 8 الصبح")
        reply = self.store.try_handle_command("امسح ذاكرتك عن الامتحان")
        self.assertIn("1", reply)
        self.assertEqual(len(self.store.data["facts"]), 1)
        self.assertIn("شغلي", self.store.data["facts"][0]["text"])

    def test_wipe_all_command(self):
        self.store.add_fact("عندي امتحان CS 303 يوم الخميس")
        reply = self.store.try_handle_command("امسح كل ذاكرتك")
        self.assertIn("مسحت كل ذاكرتي", reply)
        self.assertEqual(self.store.data["facts"], [])


class TestLearning(_MemoryCase):
    def test_correction_persists_across_reload_and_reaches_prompt(self):
        with mock.patch.object(brain, "is_online", return_value=True), \
                mock.patch.object(brain, "_groq_message",
                                  return_value={"content": "تمام."}):
            brain.ask_with_tools(
                "لا، الصح إن الامتحان يوم الأربعاء مش الخميس",
                "Memo", "Mohamed", "ar")
        reloaded = memory_mod.MemoryStore(self.tmp / "memory.json")
        self.assertEqual(len(reloaded.data["rules"]), 1)
        self.assertIn("الأربعاء", reloaded.data["rules"][0]["text"])
        block = reloaded.relevant_block("الامتحان", "ar")
        self.assertIn("الأربعاء", block)
        self.assertIn("متكررش الغلطة", block)

    def test_style_block_egyptian_dialect_masculine_forms(self):
        prompt = brain.system_prompt(
            "Memo", "Mohamed", "ar", tools_available=True,
            memory_block=self.store.relevant_block("ازيك", "ar"))
        self.assertIn("مصري", prompt)
        self.assertIn("مذكر", prompt)

    def test_tone_learned_from_short_request(self):
        self.store.learn_from_turn("اختصر في الرد لو سمحت")
        self.assertEqual(self.store.data["style"]["tone"], "short")
        block = self.store.get_style_block("ar")
        self.assertIn("القصيرة", block)

    def test_tone_not_fooled_by_semester_word(self):
        # "الفصل الدراسي" must NOT flip the tone to detailed.
        self.store.learn_from_turn("الفصل الدراسي ده صعب")
        self.assertEqual(self.store.data["style"]["tone"], "auto")

    def test_routine_and_preference_learned(self):
        self.store.learn_from_turn("شغلي بيبدأ الساعة 8 كل يوم")
        self.assertEqual(len(self.store.data["routines"]), 1)
        self.store.learn_from_turn("بحب الردود القصيرة")
        self.assertTrue(any(f["source"] == "auto"
                            for f in self.store.data["facts"]))

    def test_guest_never_writes_owner_memory(self):
        with mock.patch.object(brain, "is_online", return_value=True), \
                mock.patch.object(brain, "_groq", return_value="تمام."):
            result = brain.ask(
                "لا، الصح إن الامتحان يوم الجمعة", "Memo", "Mohamed",
                "ar", memory_enabled=False)
        self.assertEqual(result["engine"], "groq")
        self.assertEqual(self.store.data["rules"], [])
        self.assertEqual(self.store.data["facts"], [])


class TestHistoryWindow(_MemoryCase):
    def test_window_is_40_messages(self):
        hist = []
        for i in range(25):
            hist.append({"role": "user", "content": f"سؤال {i}"})
            hist.append({"role": "assistant", "content": f"رد {i}"})
        seen = {}

        def fake_groq(messages, use_tools=False, timeout=60):
            seen["messages"] = messages
            return {"content": "تمام."}

        with mock.patch.object(brain, "is_online", return_value=True), \
                mock.patch.object(brain, "_groq_message",
                                  side_effect=fake_groq):
            brain.ask_with_tools("سؤال جديد", "Memo", "Mohamed", "ar",
                                 history=hist)
        # system + last 40 history messages + this turn = 42.
        self.assertEqual(len(seen["messages"]), 42)
        self.assertEqual(seen["messages"][1]["content"], "سؤال 5")

    def test_rollover_summarizes_old_turns_instead_of_dropping(self):
        hist = []
        for i in range(35):
            hist.append({"role": "user", "content": f"سؤال {i}"})
            hist.append({"role": "assistant", "content": f"رد {i}"})
        brain._record_turn("سؤال جديد", "رد جديد", "groq", hist,
                           learn=True, language="ar")
        self.assertEqual(len(hist), 52)  # 72 - 20 rolled over
        self.assertIn("سؤال 0", self.store.data["summary"])
        self.assertIn("سؤال 9", self.store.data["summary"])


class TestWebSearch(_MemoryCase):
    def test_brain_calls_search_and_answer_uses_results(self):
        calls = []

        class FakeDDGS:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def text(self, query, max_results=5):
                calls.append(query)
                return [{"title": "الدولار اليوم",
                         "body": "سعر الدولار 48 جنيه في البنوك.",
                         "href": "https://example.com/usd"}]

        script = [
            {"content": "", "tool_calls": [
                {"id": "call_1", "function": {
                    "name": "web_search",
                    "arguments": json.dumps({"query": "سعر الدولار"})}}]},
            {"content": "الدولار بـ 48 جنيه حسب موقع example.com."},
        ]

        def fake_groq(messages, use_tools=False, timeout=60):
            return script.pop(0)

        with mock.patch.object(brain, "is_online", return_value=True), \
                mock.patch.object(brain, "_groq_message",
                                  side_effect=fake_groq), \
                mock.patch("ddgs.DDGS", FakeDDGS):
            result = brain.ask_with_tools(
                "سعر الدولار كام النهاردة؟", "Memo", "Mohamed", "ar")
        self.assertEqual(calls, ["سعر الدولار"])
        self.assertIn("48", result["reply"])

    def test_search_result_format_names_source(self):
        class FakeDDGS:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def text(self, query, max_results=5):
                return [{"title": "T", "body": "B",
                         "href": "https://example.com"}]

        with mock.patch("ddgs.DDGS", FakeDDGS):
            out = tools.execute_tool("web_search", {"query": "test"})
        self.assertIn("المصدر: https://example.com", out)

    def test_search_failure_is_honest(self):
        class FailingDDGS:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def text(self, query, max_results=5):
                raise RuntimeError("network down")

        with mock.patch("ddgs.DDGS", FailingDDGS):
            out = tools.execute_tool("web_search", {"query": "test"})
        self.assertIn("مقدرتش", out)

    def test_system_prompt_carries_search_rule(self):
        prompt = brain.system_prompt("Memo", "Mohamed", "ar",
                                     tools_available=True)
        self.assertIn("web_search", prompt)
        self.assertIn("تخمن", prompt)


class TestSendWhatsApp(unittest.TestCase):
    def setUp(self):
        self.sent = []
        fake = mock.Mock()
        fake.normalize_number.side_effect = lambda raw: "201557887719"
        fake.mask_number.side_effect = lambda d: "…19"
        fake.get_state.return_value = {"status": "linked"}
        fake.send_message.side_effect = (
            lambda to, text: (self.sent.append((to, text)),
                              {"ok": True, "detail": "sent"})[1])
        self.fake = fake
        self.patch = mock.patch.dict(sys.modules, {"whatsapp": fake})
        self.patch.start()
        self.settings_patch = mock.patch(
            "settings_store.load_settings",
            return_value={"whatsapp_owner_phone": "01557887719"})
        self.settings_patch.start()

    def tearDown(self):
        self.patch.stop()
        self.settings_patch.stop()

    def test_send_to_default_owner_number(self):
        with tools.trusted_caller():
            out = tools.execute_tool(
                "send_whatsapp_message", {"text": "هاي، دي رسالة تجريبية"})
        self.assertEqual(self.sent, [("201557887719",
                                      "هاي، دي رسالة تجريبية")])
        self.assertIn("✅", out)

    def test_send_to_explicit_number(self):
        with tools.trusted_caller():
            tools.execute_tool(
                "send_whatsapp_message",
                {"phone": "01000000000", "text": "هاي"})
        # Explicit phone is passed through as given (normalization to
        # JID happens inside whatsapp.send_message, not here).
        self.assertEqual(self.sent[0][0], "01000000000")
        self.assertEqual(self.sent[0][1], "هاي")

    def test_failed_send_is_reported_honestly(self):
        self.fake.send_message.side_effect = (
            lambda to, text: {"ok": False, "detail": "sidecar down"})
        with tools.trusted_caller():
            out = tools.execute_tool(
                "send_whatsapp_message", {"text": "هاي"})
        self.assertIn("مقدرتش", out)
        self.assertNotIn("✅", out)

    def test_not_linked_is_honest(self):
        self.fake.get_state.return_value = {"status": "waiting_qr"}
        with tools.trusted_caller():
            out = tools.execute_tool(
                "send_whatsapp_message", {"text": "هاي"})
        self.assertIn("مش مربوط", out)
        self.assertEqual(self.sent, [])

    def test_permission_denied_cancels_send(self):
        with mock.patch.object(tools, "request_permission",
                               return_value=False):
            out = tools.execute_tool(
                "send_whatsapp_message", {"text": "هاي"})
        self.assertIn("رفض", out)
        self.assertEqual(self.sent, [])


class TestInChatPermissions(unittest.TestCase):
    def test_pending_appears_then_allow_runs(self):
        result = {}

        def ask():
            result["ok"] = tools.request_permission(
                "open_app", "فتح برنامج: VS Code", "detail")

        t = threading.Thread(target=ask, daemon=True)
        t.start()
        deadline = time.time() + 5
        pending = None
        while time.time() < deadline:
            state = tools.get_permission_state()
            pending = state.get("pending")
            if pending:
                break
            time.sleep(0.05)
        self.assertIsNotNone(pending)
        self.assertEqual(pending["kind"], "open_app")
        self.assertIn("VS Code", pending["summary"])
        self.assertTrue(tools.resolve_permission(pending["id"], True))
        t.join(timeout=5)
        self.assertTrue(result["ok"])
        resolved = [r for r in tools.get_permission_state()["resolved"]
                    if r["id"] == pending["id"]]
        self.assertEqual(resolved[0]["status"], "allowed")

    def test_deny_cancels(self):
        result = {}

        def ask():
            result["ok"] = tools.request_permission(
                "run_command", "تشغيل أمر: dir", "")

        t = threading.Thread(target=ask, daemon=True)
        t.start()
        deadline = time.time() + 5
        pending = None
        while time.time() < deadline:
            pending = tools.get_permission_state().get("pending")
            if pending:
                break
            time.sleep(0.05)
        self.assertIsNotNone(pending)
        self.assertTrue(tools.resolve_permission(pending["id"], False))
        t.join(timeout=5)
        self.assertFalse(result["ok"])
        resolved = [r for r in tools.get_permission_state()["resolved"]
                    if r["id"] == pending["id"]]
        self.assertEqual(resolved[0]["status"], "denied")

    def test_timeout_resolves_as_timeout(self):
        result = {}

        def ask():
            result["ok"] = tools.request_permission(
                "open_app", "فتح برنامج: X", "")

        with mock.patch.object(tools, "_PERMISSION_TIMEOUT", 0.2):
            t = threading.Thread(target=ask, daemon=True)
            t.start()
            t.join(timeout=5)
        self.assertFalse(result["ok"])
        state = tools.get_permission_state()
        self.assertIsNone(state.get("pending"))
        self.assertTrue(any(r["status"] == "timeout"
                            for r in state["resolved"]))

    def test_ui_builds_chat_cards_not_a_modal(self):
        js = _read("app/ui/app.js")
        html = _read("app/ui/index.html")
        css = _read("app/ui/styles.css")
        self.assertIn("addPermCard", js)
        self.assertIn("perm-btns", js)
        self.assertIn("finalizePermCard", js)
        self.assertNotIn("perm-modal", js)
        self.assertNotIn('id="perm-modal"', html)
        self.assertNotIn('id="perm-allow"', html)
        self.assertNotIn('id="perm-deny"', html)
        self.assertIn(".msg.perm", css)


class TestUpdaterRetry(unittest.TestCase):
    def test_manifest_timeout_then_success(self):
        class Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"version": "9.9.9", "url": "https://x/y.zip",
                        "sha256": "ab" * 32}

        import requests as requests_mod
        calls = {"n": 0}

        def fake_get(url, timeout=15):
            calls["n"] += 1
            if calls["n"] == 1:
                raise requests_mod.exceptions.ReadTimeout("timed out")
            return Resp()

        fake_main = mock.Mock()
        fake_main.VERSION = "1.4.1"
        with mock.patch.object(updater, "load_config",
                              return_value={"manifest_url":
                                            "https://example.invalid/v.json"}), \
                mock.patch.object(updater.requests, "get",
                                  side_effect=fake_get), \
                mock.patch.object(updater.time, "sleep",
                                  return_value=None), \
                mock.patch.dict(sys.modules, {"main": fake_main}):
            result = updater.check_update()
        self.assertEqual(calls["n"], 2)
        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["version"], "9.9.9")


class TestSystemStatus(unittest.TestCase):
    def test_status_reports_real_numbers(self):
        fake_monitor = mock.Mock()
        fake_monitor.get_status.return_value = {
            "ram": {"total_mb": 8000, "used_mb": 6800,
                    "free_mb": 1200, "percent": 85},
            "disk": {"free_gb": 45.5, "total_gb": 238,
                     "percent_free": 19},
            "cpu": {"percent": 12, "sustained_high": False},
            "battery": {"percent": 87, "charging": True},
            "temp_c": None, "temp_available": False,
        }
        # v1.4.3: the tool calls SystemMonitor().get_status() (an
        # instance method) - the old module-level monitor.get_status()
        # never existed and made every call fail.
        fake_module = mock.Mock()
        fake_module.SystemMonitor.return_value = fake_monitor
        with mock.patch.dict(sys.modules, {"monitor": fake_module}):
            out = tools.execute_tool("system_status", {})
        self.assertIn("6800", out)
        self.assertIn("1200", out)
        self.assertIn("45.5", out)
        self.assertIn("87", out)
        self.assertIn("مش متاحة", out)  # temp honestly unavailable

    def test_status_with_temp_shows_it(self):
        fake_monitor = mock.Mock()
        fake_monitor.get_status.return_value = {
            "ram": {"total_mb": 8000, "used_mb": 4000,
                    "free_mb": 4000, "percent": 50},
            "disk": {"free_gb": 100, "total_gb": 238,
                     "percent_free": 42},
            "cpu": {"percent": 5, "sustained_high": False},
            "battery": None,
            "temp_c": 61, "temp_available": True,
        }
        # v1.4.3: instance method, see test_status_reports_real_numbers.
        fake_module = mock.Mock()
        fake_module.SystemMonitor.return_value = fake_monitor
        with mock.patch.dict(sys.modules, {"monitor": fake_module}):
            out = tools.execute_tool("system_status", {})
        self.assertIn("61", out)
        self.assertNotIn("مش متاحة", out)


class TestMonitorNotificationApi(unittest.TestCase):
    """Api-level wiring for the monitor + notification Settings
    sections (headless, Linux: real psutil numbers, temp honestly
    unavailable, winrt listener honestly unavailable). Settings are
    written to a TEMP settings file, never the real one."""

    def setUp(self):
        import settings_store
        self.tmp = Path(tempfile.mkdtemp())
        self.orig_path = settings_store.SETTINGS_PATH
        settings_store.SETTINGS_PATH = self.tmp / "settings.json"
        import main
        self.app = main.MemoApp()
        self.api = self.app.api

    def tearDown(self):
        import settings_store
        settings_store.SETTINGS_PATH = self.orig_path
        try:
            if self.app._monitor is not None:
                self.app._monitor.stop()
        except Exception:
            pass

    def test_get_monitor_status_real_numbers_temp_unavailable(self):
        res = self.api.get_monitor_status()
        self.assertTrue(res["ok"])
        st = res["status"]
        self.assertGreater(st["ram"]["total_mb"], 0)
        self.assertGreater(st["disk"]["total_gb"], 0)
        self.assertFalse(res["temp_available"])
        self.assertIsNone(st["temp_c"])
        self.assertEqual(res["settings"]["monitor_ram_free_mb"], 500)
        self.assertEqual(res["settings"]["monitor_ram_percent"], 92)

    def test_save_monitor_settings_roundtrip(self):
        res = self.api.save_monitor_settings(
            json.dumps({"monitor_ram_free_mb": 400,
                        "monitor_cooldown_min": 15}))
        self.assertTrue(res["ok"])
        after = self.api.get_monitor_status()
        self.assertEqual(after["settings"]["monitor_ram_free_mb"], 400)
        self.assertEqual(after["settings"]["monitor_cooldown_min"], 15)

    def test_get_notification_status_honest_on_linux(self):
        res = self.api.get_notification_status()
        self.assertTrue(res["ok"])
        self.assertFalse(res["listener"]["available"])
        self.assertEqual(res["listener"]["access"], "unavailable")
        self.assertIn("WhatsApp", res["apps"])
        self.assertIn("عاجل", res["keywords"])

    def test_save_notification_settings_roundtrip(self):
        res = self.api.save_notification_settings(
            json.dumps({"apps": ["WhatsApp", "Mail"],
                        "keywords": ["عاجل", "مهم"]}))
        self.assertTrue(res["ok"])
        after = self.api.get_notification_status()
        self.assertEqual(after["apps"], ["WhatsApp", "Mail"])
        self.assertEqual(after["keywords"], ["عاجل", "مهم"])

    def test_alert_delivery_to_in_app_feed(self):
        self.assertEqual(self.api.get_alerts(), {"alerts": []})
        self.app._on_system_alert({
            "kind": "ram", "level": "warn",
            "title_ar": "الرام قربت تتملي",
            "message_ar": "فاضي 300MB بس", "at": "2026-11-07 12:00:00"})
        alerts = self.api.get_alerts()["alerts"]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["title"], "الرام قربت تتملي")
        self.assertEqual(alerts[0]["level"], "warn")

    def test_ui_has_system_and_notification_sections(self):
        html = _read("app/ui/index.html")
        js = _read("app/ui/app.js")
        css = _read("app/ui/styles.css")
        for section in ("system", "notifications"):
            self.assertIn(f'data-section="{section}"', html)
            self.assertIn(f'id="section-{section}"', html)
        self.assertIn("refreshMonitor", js)
        self.assertIn("refreshNotifications", js)
        self.assertIn("pollAlerts", js)
        self.assertIn('id="alerts"', html)
        self.assertIn(".alert-banner", css)


if __name__ == "__main__":
    unittest.main()
