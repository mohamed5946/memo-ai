# LONA v1.6.4 rc.2 - W8: token efficiency + token-bar data layer.
#
# FIELD FAILURE (2026-11-22 evening): Groq hit its 8000 tokens/min
# rate limit and the user says LONA's tokens "evaporate" - every turn
# re-sent the full 40-message history plus a device snapshot, with no
# output cap and no visibility into the burn.
#
# This file proves:
#   * context trimming: a 30-message chat sends only the last ~10
#     history messages verbatim; older turns ride as a short
#     deterministic summary in the system prompt (never dropped);
#   * device context on demand: is_device_question() gates the
#     snapshot, and a plain "hello" payload carries no process list
#     and no device snapshot;
#   * output caps: Groq max_completion_tokens 600 (2000 Deep Think)
#     and Gemini maxOutputTokens 600;
#   * model routing: simple chat goes to Gemini 2.5 Flash first in
#     auto mode (higher rate limit, already configured); Groq 120b
#     stays for Deep Think / hard questions / tool use; explicit
#     model choices are untouched (strict);
#   * no re-send of unchanged context: an identical device snapshot
#     is dropped on the next turn, and a repeated system_status tool
#     result in one turn becomes a short "no change" note;
#   * per-reply token counts are recorded from the engines' usage
#     fields, Groq rate-limit headers are captured, and
#     get_token_usage() reports the tracked state (has_data False
#     before any call - never a fake 100%);
#   * the chat device answer names the REAL temp reason (hint
#     verbatim) instead of "مفيش حساس";
#   * main.py ISLAND_IDLE_W/H == 220/60 (matches island.html's pill).
#
#   python3 -m pytest app/tests/test_v164_token_efficiency.py -q

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import requests  # noqa: E402

import brain as brain_mod  # noqa: E402
import memory as memory_mod  # noqa: E402
import tools as tools_mod  # noqa: E402


def _hist(n_turns):
    """n_turns of user/assistant history: سؤال 0..n-1."""
    hist = []
    for i in range(n_turns):
        hist.append({"role": "user", "content": f"سؤال {i}"})
        hist.append({"role": "assistant", "content": f"رد {i}"})
    return hist


def _groq_resp(content="أهلاً", usage=None, headers=None):
    """A requests-like Groq 200 response with a usage field."""
    resp = mock.Mock()
    resp.status_code = 200
    body = {"choices": [{"message": {"content": content}}]}
    if usage is not None:
        body["usage"] = usage
    resp.json.return_value = body
    resp.headers = headers or {}
    return resp


def _gemini_resp(text="أهلاً، أنا Gemini", usage=None):
    """A requests-like Gemini 200 response with usageMetadata."""
    resp = mock.Mock()
    resp.status_code = 200
    body = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    if usage is not None:
        body["usageMetadata"] = usage
    resp.json.return_value = body
    resp.headers = {}
    return resp


class _StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = memory_mod.MemoryStore(self.tmp / "memory.json")
        brain_mod.set_memory_store(self.store)
        brain_mod.clear_history()
        brain_mod._reset_token_usage()
        brain_mod._LAST_DEVICE_VIEW["hash"] = None

    def tearDown(self):
        brain_mod.set_memory_store(None)
        brain_mod.clear_history()
        brain_mod._reset_token_usage()
        brain_mod._LAST_DEVICE_VIEW["hash"] = None


class TestContextTrimming(_StoreCase):
    """Item 1: last ~10 messages verbatim + short summary of older."""

    def test_context_history_splits_recent_and_summary(self):
        hist = _hist(15)  # 30 messages
        recent, summary = brain_mod._context_history(hist, self.store, "ar")
        self.assertEqual(len(recent), 10)
        self.assertEqual(recent[0]["content"], "سؤال 10")
        self.assertEqual(recent[-1]["content"], "رد 14")
        # Older turns (0-9) are summarized, not dropped.
        self.assertIn("سؤال 0", summary)
        self.assertIn("سؤال 9", summary)
        self.assertNotIn("سؤال 10", summary)

    def test_context_history_short_chat_sends_everything(self):
        hist = _hist(3)  # 6 messages, under the window
        recent, summary = brain_mod._context_history(hist, self.store, "ar")
        self.assertEqual(len(recent), 6)
        self.assertEqual(summary, "")

    def test_ask_sends_only_last_10_of_30(self):
        # A 30-message chat must not send the full history each time.
        hist = _hist(15)
        seen = {}

        def fake_groq(messages, timeout=60, **kw):
            seen["messages"] = messages
            return "تمام."

        with mock.patch.object(brain_mod, "is_online",
                               return_value=True), \
                mock.patch.object(brain_mod, "_ask_gemini_chat",
                                  return_value=("", "", "no-gemini-chat-key")), \
                mock.patch.object(brain_mod, "_groq",
                                  side_effect=fake_groq):
            # Long + detailed => HARD, so Groq answers (no routing).
            brain_mod.ask("اشرحلي بالتفصيل الممل الفرق بين المعالج "
                          "والرام والقرص الصلب وكيف يتعامل نظام التشغيل "
                          "مع الذاكرة الافتراضية عند امتلاء الرام، مع "
                          "أمثلة عملية من ويندوز ولينكس ومقارنة شاملة "
                          "بين إدارة الذاكرة في النظامين وشرح خوارزميات "
                          "الاستبدال المستخدمة في كل منهما بالتفصيل",
                          "LONA", "Mohamed", "ar", history=hist)
        # system + last 10 history messages + this turn = 12.
        self.assertEqual(len(seen["messages"]), 12)
        self.assertEqual(seen["messages"][1]["content"], "سؤال 10")
        # Older turns ride as a summary in the system prompt.
        system_text = seen["messages"][0]["content"]
        self.assertIn("سؤال 0", system_text)
        self.assertIn("سؤال 9", system_text)


class TestDeviceIntent(unittest.TestCase):
    """Item 2: the device snapshot belongs only in device questions."""

    def test_device_questions(self):
        for text in ("اللابتوب عامل إيه؟", "الرام قد إيه؟",
                     "البطارية كام؟", "الجهاز سخن؟",
                     "حرارة المعالج كام؟", "المساحة الفاضية قد إيه؟",
                     "how much RAM is free?",
                     "what is the CPU temperature?",
                     "is the laptop hot?"):
            self.assertTrue(brain_mod.is_device_question(text), text)

    def test_not_device_questions(self):
        for text in ("إزيك؟", "hello", "عامل إيه؟", "افتح يوتيوب",
                     "اشرحلي الدرس", "أنا في الشغل",
                     "امسح ذاكرتك عن الامتحان"):
            self.assertFalse(brain_mod.is_device_question(text), text)

    def test_hello_payload_has_no_process_list(self):
        # A plain "hello" must not include the process list (or any
        # device snapshot) in what is sent to the engine.
        hist = _hist(15)
        seen = {}

        def fake_groq(messages, timeout=60, **kw):
            seen["messages"] = messages
            return "أهلاً يا محمد."

        with mock.patch.object(brain_mod, "is_online",
                               return_value=True), \
                mock.patch.object(brain_mod, "_ask_gemini_chat",
                                  return_value=("", "", "no-gemini-chat-key")), \
                mock.patch.object(brain_mod, "_groq",
                                  side_effect=fake_groq):
            brain_mod.ask("hello", "LONA", "Mohamed", "en", history=hist)
        joined = "\n".join(str(m.get("content") or "")
                           for m in seen["messages"])
        self.assertNotIn("top_processes", joined)
        self.assertNotIn("chrome.exe", joined)
        self.assertNotIn("process list", joined.lower())


class TestSimpleChatRouting(_StoreCase):
    """Item 4: simple chat -> Gemini 2.5 Flash; 120b for hard ones."""

    def test_simple_classifier(self):
        self.assertTrue(brain_mod._is_simple_chat("إزيك؟"))
        self.assertTrue(brain_mod._is_simple_chat("hello"))
        self.assertTrue(brain_mod._is_simple_chat("شكراً يا لونا"))
        self.assertFalse(brain_mod._is_simple_chat("إزيك؟",
                                                   deep_think=True))
        self.assertFalse(brain_mod._is_simple_chat("افتح VS Code"))
        self.assertFalse(brain_mod._is_simple_chat("اللابتوب عامل إيه؟"))
        self.assertFalse(brain_mod._is_simple_chat("سعر الدولار كام؟"))
        self.assertFalse(brain_mod._is_simple_chat("```python\nprint(1)"))
        self.assertFalse(brain_mod._is_simple_chat("x" * 501))

    def test_simple_ask_goes_to_gemini_not_groq(self):
        with mock.patch.object(brain_mod, "is_online",
                               return_value=True), \
                mock.patch.object(brain_mod, "_ask_gemini_chat",
                                  return_value=("أهلاً يا محمد", "gemini",
                                                "")) as gem, \
                mock.patch.object(brain_mod, "_groq",
                                  side_effect=AssertionError(
                                      "Groq must not be called")):
            res = brain_mod.ask("إزيك؟", "LONA", "Mohamed", "ar",
                                history=[])
        self.assertEqual(res["engine"], "gemini")
        self.assertTrue(gem.called)

    def test_hard_ask_goes_to_groq(self):
        with mock.patch.object(brain_mod, "is_online",
                               return_value=True), \
                mock.patch.object(brain_mod, "_ask_gemini_chat",
                                  side_effect=AssertionError(
                                      "Gemini must not be tried first")), \
                mock.patch.object(brain_mod, "_groq",
                                  return_value="تمام، فتحته."):
            res = brain_mod.ask("افتح VS Code", "LONA", "Mohamed", "ar",
                                history=[])
        self.assertEqual(res["engine"], "groq")

    def test_simple_tool_chat_goes_to_gemini_text_only(self):
        with mock.patch.object(brain_mod, "is_online",
                               return_value=True), \
                mock.patch.object(brain_mod, "_ask_gemini_chat",
                                  return_value=("أهلاً يا محمد", "gemini",
                                                "")), \
                mock.patch.object(brain_mod, "_groq_message",
                                  side_effect=AssertionError(
                                      "Groq loop must not run")):
            res = brain_mod.ask_with_tools("إزيك؟", "LONA", "Mohamed",
                                           "ar", history=[])
        self.assertEqual(res["engine"], "gemini")
        self.assertEqual(res["tools_used"], 0)
        self.assertFalse(res["tools_disabled"])

    def test_explicit_groq_choice_stays_strict(self):
        # A strict Groq pick never routes a simple turn to Gemini.
        with mock.patch.object(brain_mod, "is_online",
                               return_value=True), \
                mock.patch.object(brain_mod, "_ask_gemini_chat",
                                  side_effect=AssertionError(
                                      "strict Groq must not try Gemini")), \
                mock.patch.object(brain_mod, "_groq",
                                  return_value="أهلاً."):
            res = brain_mod.ask("إزيك؟", "LONA", "Mohamed", "ar",
                                history=[], model_choice="groq")
        self.assertEqual(res["engine"], "groq")


class TestOutputCaps(unittest.TestCase):
    """Item 3: a sensible max_tokens per reply (Deep Think: more)."""

    def test_groq_max_completion_tokens(self):
        captured = []

        def fake_post(url, headers=None, **kw):
            captured.append(kw.get("json") or {})
            return _groq_resp(usage={"prompt_tokens": 10,
                                     "completion_tokens": 5,
                                     "total_tokens": 15})

        with mock.patch.object(brain_mod, "read_key",
                               return_value="gsk-test"), \
                mock.patch.object(brain_mod.requests, "post",
                                  side_effect=fake_post):
            brain_mod._groq([{"role": "user", "content": "hi"}])
            brain_mod._groq([{"role": "user", "content": "hi"}],
                            deep_think=True)
        self.assertEqual(captured[0]["max_completion_tokens"], 600)
        self.assertEqual(captured[1]["max_completion_tokens"], 2000)

    def test_gemini_max_output_tokens(self):
        captured = []

        def fake_post(url, headers=None, **kw):
            captured.append(kw.get("json") or {})
            return _gemini_resp(usage={"promptTokenCount": 12,
                                       "candidatesTokenCount": 6,
                                       "totalTokenCount": 18})

        with mock.patch.object(brain_mod, "read_key",
                               return_value="AIzaTest"), \
                mock.patch.object(brain_mod.requests, "post",
                                  side_effect=fake_post):
            brain_mod._gemini_chat([{"role": "user", "content": "hi"}])
        self.assertEqual(captured[0]["generationConfig"]
                         ["maxOutputTokens"], 600)


class TestTokenUsageTracking(_StoreCase):
    """Items 6 + token bar: usage fields, headers, get_token_usage."""

    def test_no_data_before_any_call(self):
        usage = brain_mod.get_token_usage()
        for engine in ("groq", "gemini"):
            self.assertFalse(usage[engine]["has_data"])
            self.assertIsNone(usage[engine]["used"])
            self.assertIsNone(usage[engine]["remaining"])
            self.assertIsNone(usage[engine]["reset_in_seconds"])
        self.assertEqual(usage["groq"]["limit"], 8000)
        self.assertIsNone(usage["gemini"]["limit"])

    def test_groq_usage_accumulates_and_estimates(self):
        brain_mod._record_engine_usage(
            "groq", {"prompt_tokens": 1000, "completion_tokens": 200})
        usage = brain_mod.get_token_usage()["groq"]
        self.assertTrue(usage["has_data"])
        self.assertEqual(usage["used"], 1200)
        # No headers yet: remaining is the estimate (limit - used).
        self.assertEqual(usage["remaining"], 6800)
        self.assertTrue(usage["estimated"])
        self.assertIsNotNone(usage["reset_in_seconds"])

    def test_groq_header_is_true_remaining(self):
        headers = {"x-ratelimit-remaining-tokens": "6500",
                   "x-ratelimit-reset-tokens": "30s"}
        brain_mod._record_engine_usage(
            "groq", {"prompt_tokens": 1000, "completion_tokens": 200},
            headers)
        usage = brain_mod.get_token_usage()["groq"]
        self.assertEqual(usage["remaining"], 6500)
        self.assertFalse(usage["estimated"])
        self.assertEqual(usage["reset_in_seconds"], 30)
        # Spending more after the header ages the true remaining.
        brain_mod._record_engine_usage(
            "groq", {"prompt_tokens": 400, "completion_tokens": 100})
        usage = brain_mod.get_token_usage()["groq"]
        self.assertEqual(usage["used"], 1700)
        self.assertEqual(usage["remaining"], 6000)

    def test_gemini_usage_tracked_without_limit(self):
        brain_mod._record_engine_usage(
            "gemini", {"promptTokenCount": 300,
                       "candidatesTokenCount": 100})
        usage = brain_mod.get_token_usage()["gemini"]
        self.assertTrue(usage["has_data"])
        self.assertEqual(usage["used"], 400)
        self.assertIsNone(usage["limit"])
        self.assertIsNone(usage["remaining"])
        self.assertTrue(usage["estimated"])

    def test_groq_call_records_usage_and_headers(self):
        def fake_post(url, headers=None, **kw):
            return _groq_resp(
                usage={"prompt_tokens": 500, "completion_tokens": 50,
                       "total_tokens": 550},
                headers={"x-ratelimit-remaining-tokens": "7000",
                         "x-ratelimit-reset-tokens": "7.66s"})

        with mock.patch.object(brain_mod, "read_key",
                               return_value="gsk-test"), \
                mock.patch.object(brain_mod.requests, "post",
                                  side_effect=fake_post):
            brain_mod._groq([{"role": "user", "content": "hi"}])
        usage = brain_mod.get_token_usage()["groq"]
        self.assertEqual(usage["used"], 550)
        self.assertEqual(usage["remaining"], 7000)
        self.assertFalse(usage["estimated"])

    def test_gemini_call_records_usage(self):
        def fake_post(url, headers=None, **kw):
            return _gemini_resp(usage={"promptTokenCount": 200,
                                       "candidatesTokenCount": 40,
                                       "totalTokenCount": 240})

        with mock.patch.object(brain_mod, "read_key",
                               return_value="AIzaTest"), \
                mock.patch.object(brain_mod.requests, "post",
                                  side_effect=fake_post):
            brain_mod._gemini_chat([{"role": "user", "content": "hi"}])
        self.assertEqual(
            brain_mod.get_token_usage()["gemini"]["used"], 240)

    def test_reset_seconds_parsing(self):
        self.assertEqual(brain_mod._parse_reset_seconds("7.66s"), 7.66)
        self.assertAlmostEqual(
            brain_mod._parse_reset_seconds("2m59.56s"), 179.56)
        self.assertEqual(brain_mod._parse_reset_seconds("30"), 30.0)
        self.assertIsNone(brain_mod._parse_reset_seconds("nonsense"))
        self.assertIsNone(brain_mod._parse_reset_seconds(None))


class TestNoResend(_StoreCase):
    """Item 5: an unchanged snapshot is not re-sent."""

    def test_identical_self_view_dropped_second_turn(self):
        view = "حالتك دلوقتي: مودك صاحي، حرارة الجهاز 55°م."
        self.assertEqual(
            brain_mod._dedup_self_view(view, True), view)
        self.assertEqual(brain_mod._dedup_self_view(view, True), "")
        # A changed snapshot is sent again.
        changed = "حالتك دلوقتي: مودك صاحي، حرارة الجهاز 56°م."
        self.assertEqual(
            brain_mod._dedup_self_view(changed, True), changed)

    def test_explicit_history_never_deduped(self):
        view = "حالتك دلوقتي: مودك صاحي."
        self.assertEqual(
            brain_mod._dedup_self_view(view, False), view)
        self.assertEqual(
            brain_mod._dedup_self_view(view, False), view)

    def test_repeated_system_status_in_one_turn_is_shortened(self):
        # The model asks for the device status twice in one turn with
        # nothing changing in between: the second result must be a
        # short "no change" note, not the full numbers again.
        calls = {"n": 0}
        seen_messages = []
        status_text = ("حالة اللابتوب دلوقتي:\n• الرام: 100MB مستخدمة "
                       "من 8000MB")

        def fake_groq(messages, use_tools=False, timeout=60, **kw):
            seen_messages.append(list(messages))
            n = calls["n"]
            calls["n"] += 1
            if n < 2:
                return {"content": "",
                        "tool_calls": [{"id": f"c{n + 1}",
                                        "type": "function",
                                        "function": {
                                            "name": "system_status",
                                            "arguments": "{}"}}]}
            return {"content": "اللابتوب تمام.", "tool_calls": []}

        with mock.patch.object(brain_mod, "is_online",
                               return_value=True), \
                mock.patch.object(brain_mod, "_groq_message",
                                  side_effect=fake_groq), \
                mock.patch.object(tools_mod, "execute_tool",
                                  return_value=status_text):
            # A hard question (device) so the Groq loop runs.
            res = brain_mod.ask_with_tools(
                "اللابتوب عامل إيه؟ قولي بالتفصيل عن الرام والمعالج "
                "والبطارية والقرص والحرارة وكل حاجة بالتفصيل الممل",
                "LONA", "Mohamed", "ar", history=[])
        self.assertEqual(res["engine"], "groq")
        # The third Groq call carries BOTH tool results: the first in
        # full, the second as the short unchanged note.
        tool_msgs = [m for m in seen_messages[2]
                     if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 2)
        self.assertIn("الرام", tool_msgs[0]["content"])
        self.assertIn("مفيش تغيير", tool_msgs[1]["content"])


class TestTempReasonInChat(unittest.TestCase):
    """Item 8: the chat device answer names the real temp reason."""

    def _status(self, reason, hint):
        return {"available": True,
                "ram": {"total_mb": 8000, "used_mb": 100,
                        "free_mb": 7900, "percent": 2},
                "disk": {"free_gb": 100, "total_gb": 200,
                         "percent_free": 50},
                "cpu": {"percent": 5.0, "sustained_high": False},
                "battery": {"percent": 88, "charging": False},
                "temp_c": None, "temp_available": False,
                "temp_status": {"available": False, "celsius": None,
                                "reason": reason, "hint": hint}}

    def test_lhm_not_running_hint_verbatim(self):
        hint = ("LibreHardwareMonitor متثبت بس مش شغال — افتحه "
                "وسيبه شغال في الخلفية عشان الحرارة تظهر")
        mon = mock.Mock()
        mon.get_status.return_value = self._status(
            "lhm_not_running", hint)
        tools_mod.set_monitor_provider(lambda: mon)
        try:
            out = tools_mod._tool_system_status({})
        finally:
            tools_mod.set_monitor_provider(None)
        self.assertIn(hint, out)
        self.assertNotIn("مفيش حساس مقروء", out)
        self.assertIn("متخمنش رقم", out)

    def test_no_sensor_hint_verbatim(self):
        hint = ("مفيش حساس حرارة للمعالج على الجهاز ده — "
                "الحرارة مش متاحة")
        mon = mock.Mock()
        mon.get_status.return_value = self._status("no_sensor", hint)
        tools_mod.set_monitor_provider(lambda: mon)
        try:
            out = tools_mod._tool_system_status({})
        finally:
            tools_mod.set_monitor_provider(None)
        self.assertIn(hint, out)

    def test_no_status_dict_keeps_old_generic_line(self):
        st = self._status("not_installed", "")
        del st["temp_status"]
        mon = mock.Mock()
        mon.get_status.return_value = st
        tools_mod.set_monitor_provider(lambda: mon)
        try:
            out = tools_mod._tool_system_status({})
        finally:
            tools_mod.set_monitor_provider(None)
        self.assertIn("مفيش حساس مقروء", out)


class TestPillConstantAndBridge(unittest.TestCase):
    """Item 7 + token-bar bridge: 220x60 pill, get_token_usage."""

    def test_main_idle_pill_is_220x60(self):
        src = (APP_DIR / "main.py").read_text(encoding="utf-8")
        import re
        m = re.search(r"ISLAND_IDLE_W, ISLAND_IDLE_H = (\d+), (\d+)",
                      src)
        self.assertIsNotNone(m, "pill constants missing from main.py")
        self.assertEqual((int(m.group(1)), int(m.group(2))), (220, 60))

    def test_get_token_usage_bridge_is_a_pure_read(self):
        # Import main the same way test_v163_island.py does (minimal
        # webview stub) and call the bridge method on a dummy self -
        # it must return the tracked state without probing anything.
        import types as _types
        if str(APP_DIR) not in sys.path:
            sys.path.insert(0, str(APP_DIR))
        if "webview" not in sys.modules:
            fake = _types.ModuleType("webview")
            fake.create_window = lambda *a, **k: None
            fake.start = lambda *a, **k: None
            fake.windows = []
            sys.modules["webview"] = fake
        import main as main_mod
        brain_mod._reset_token_usage()
        try:
            usage = main_mod.Api.get_token_usage(object())
        finally:
            brain_mod._reset_token_usage()
        self.assertFalse(usage["groq"]["has_data"])
        self.assertFalse(usage["gemini"]["has_data"])
        self.assertIsNone(usage["groq"]["used"])
        brain_mod._record_engine_usage(
            "groq", {"prompt_tokens": 100, "completion_tokens": 10})
        try:
            usage = main_mod.Api.get_token_usage(object())
        finally:
            brain_mod._reset_token_usage()
        self.assertTrue(usage["groq"]["has_data"])
        self.assertEqual(usage["groq"]["used"], 110)
        self.assertEqual(usage["groq"]["remaining"], 7890)


if __name__ == "__main__":
    unittest.main()
