# LONA v1.6.1 - item 21-refined (a-d): the field freeze after denying
# a YouTube-play permission.
#
# Field report (2026-11-22 01:28): "Groq وصل للحد اليومي" -> he asked
# "ازاي" -> a browser_play_youtube permission card appeared -> he
# pressed "رفض" -> the app sat silent and dead with no typing
# indicator until a reply finally arrived (from Groq - the per-minute
# limit had cleared).
#
# Audit findings (see the report - the code, not a guess):
# (a) hideTyping() has NO hole: both call sites (sendText, voiceChat)
#     hide only in a finally block. The dead silence was sendText's
#     `if (!text || busy) return;` - while the previous turn was
#     blocked on the permission (up to 120s), "ازاي" was silently
#     DROPPED: no bubble, no dots, no feedback. Now messages sent
#     while busy are QUEUED (bubble at once, sent when the turn ends).
# (b) waitForChatResult's 150s cap returned "…" as a message - a fake
#     reply hiding the failure. It now resolves with a clear failure
#     state. The backend thread already fails fast once unblocked
#     (Groq 429 is immediate, Gemini skips without a key, Ollama
#     ConnectionError is immediate) and run_tool_chat always marks the
#     entry done, so the poll ends as soon as the thread finishes.
# (c) A permission pending from an old turn is CANCELLED the moment a
#     new message starts (bridge.chat + the queue path) - its card
#     flips to "cancelled" and never appears in the new turn.
# (d) The YouTube card was STALE (from the previous turn, not from
#     "ازاي"): the tool loop executed any tool call the model emitted,
#     and a pending permission has no turn binding. Now media tools
#     (browser_play_youtube, browser_open) run ONLY on an explicit
#     play/open request in THIS turn's message - enforced in code,
#     not just in the tool schema's wording. The deterministic voice
#     fast path (parse_voice_intent) cannot match "ازاي" either
#     (it needs an explicit "افتح يوتيوب" / "شغّل X من على اليوتيوب").
#
#   python3 -m pytest app/tests/test_v161_freeze_field.py -q
#   NODE_PATH=/tmp/jsdom-test/node_modules node app/tests/freeze_field_dom.js

import json
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
UI_DIR = APP_DIR / "ui"
sys.path.insert(0, str(APP_DIR))

import brain as brain_mod  # noqa: E402
import tools as tools_mod  # noqa: E402

JS = (UI_DIR / "app.js").read_text(encoding="utf-8")
MAIN_SRC = (APP_DIR / "main.py").read_text(encoding="utf-8")


class TestHideTypingAudit(unittest.TestCase):
    """(a) No hideTyping() executes before the final reply."""

    def test_every_hide_typing_is_in_a_finally(self):
        calls = [m.start() for m in re.finditer(r"hideTyping\(\);", JS)]
        self.assertEqual(len(calls), 2, "sendText + voiceChat only")
        for pos in calls:
            window = JS[max(0, pos - 60):pos]
            # The call must be the first statement of a finally block.
            self.assertRegex(window, r"finally\s*\{\s*$",
                             "hideTyping outside finally")

    def test_send_text_queues_while_busy(self):
        self.assertIn("_sendQueue", JS)
        self.assertIn("if (busy) {", JS)
        # The busy branch queues instead of returning silently.
        busy_branch = JS.split("if (busy) {")[1].split(
            "busy = true;")[0]
        self.assertIn("_sendQueue.push(text)", busy_branch)
        self.assertIn("addMsg(text, \"user\")", busy_branch)
        self.assertIn("cancel_pending_permissions", busy_branch)
        # The turn drains the queue when it finishes.
        self.assertIn("_sendQueue.shift()", JS)

    def test_voice_path_also_hides_in_finally_only(self):
        # voiceChat's hideTyping is the second call site, also finally.
        voice = JS.split("async function voiceChat()")[1]
        self.assertIn("hideTyping();", voice)


class TestCapNeverReturnsDots(unittest.TestCase):
    """(b) The 150s cap never returns "…" as a message."""

    def test_cap_resolves_with_a_failure_message(self):
        fn = JS.split("function waitForChatResult(chatId)")[1].split(
            "function startPermPolling")[0]
        self.assertNotIn('reply: "…"', fn)
        self.assertIn("timed_out: true", fn)
        self.assertIn("الرد اتأخر أوي", fn)
        self.assertIn("__chatResultCapMs", fn)

    def test_run_tool_chat_always_marks_done(self):
        # Even when the brain raises, the chat entry is marked done -
        # the poll can never wait forever on a dead thread.
        fn = MAIN_SRC.split("def run_tool_chat")[1].split(
            "\n    def ")[0]
        self.assertIn("except Exception as exc:", fn)
        self.assertIn('entry.update({"done": True', fn)


class TestCancelPending(unittest.TestCase):
    """(c) A new message cancels stale permissions from an old turn."""

    def test_cancel_wakes_blocked_thread_fast(self):
        results = {}

        def waiter():
            results["ok"] = tools_mod.request_permission(
                "browser_play_youtube", "تشغيل من يوتيوب: الدحيح")

        t = threading.Thread(target=waiter, daemon=True)
        t.start()
        time.sleep(0.2)
        self.assertIsNotNone(tools_mod.get_pending())
        started = time.monotonic()
        count = tools_mod.cancel_pending()
        t.join(timeout=5)
        elapsed = time.monotonic() - started
        self.assertEqual(count, 1)
        self.assertFalse(t.is_alive(), "thread still blocked")
        self.assertFalse(results["ok"], "cancel must deny")
        self.assertLess(elapsed, 5, "cancel must be fast, not 120s")
        self.assertIsNone(tools_mod.get_pending())
        state = tools_mod.get_permission_state()
        self.assertEqual(state["resolved"][-1]["status"], "cancelled")

    def test_cancel_is_a_noop_when_nothing_pending(self):
        self.assertEqual(tools_mod.cancel_pending(), 0)

    def test_main_chat_cancels_on_new_turn(self):
        fn = MAIN_SRC.split("def chat(self, text):")[1].split(
            "def unlock_owner")[0]
        self.assertIn("tools.cancel_pending()", fn)

    def test_bridge_method_exists(self):
        self.assertIn("def cancel_pending_permissions(self):", MAIN_SRC)

    def test_cancelled_card_state_in_js(self):
        self.assertIn('perm_cancelled: "اتلغى — سؤال جديد بدأ"', JS)
        self.assertIn('perm_cancelled: "Cancelled — a new question started"',
                      JS)
        fn = JS.split("function finalizePermCard(id, status)")[1].split(
            "function startPermPolling")[0]
        self.assertIn('status === "cancelled"', fn)


class TestMediaIntentGate(unittest.TestCase):
    """(d) Media tools run only on an explicit request THIS turn."""

    def test_intent_cases(self):
        self.assertFalse(brain_mod._media_intent("ازاي وصل للحد"))
        self.assertFalse(brain_mod._media_intent("إيه هو يوتيوب؟"))
        self.assertFalse(brain_mod._media_intent("أنا في الشغل"))
        self.assertFalse(brain_mod._media_intent("قولي إيه اللي حصل"))
        self.assertTrue(brain_mod._media_intent("شغّل أغنية حلوة"))
        self.assertTrue(brain_mod._media_intent("شغل الفيديو ده"))
        self.assertTrue(brain_mod._media_intent("افتح يوتيوب"))
        self.assertTrue(brain_mod._media_intent("play something on youtube"))
        self.assertTrue(brain_mod._media_intent("open youtube"))

    def _run_tool_loop(self, user_text, script):
        calls = {"n": 0}

        def fake_groq(messages, use_tools=False, timeout=60, **kw):
            msg = script[min(calls["n"], len(script) - 1)]
            calls["n"] += 1
            return msg

        executed = []
        with mock.patch.object(brain_mod, "is_online",
                               return_value=True), \
                mock.patch.object(brain_mod, "_groq_message",
                                  side_effect=fake_groq), \
                mock.patch.object(tools_mod, "execute_tool",
                                  side_effect=lambda n, a: (
                                      executed.append((n, a)),
                                      "done")[1]):
            return brain_mod.ask_with_tools(
                user_text, "LONA", "Mohamed", "ar"), executed

    def test_youtube_call_blocked_for_azay(self):
        script = [
            {"content": "", "tool_calls": [{"id": "c1", "type": "function",
                                            "function": {
                                                "name": "browser_play_youtube",
                                                "arguments": json.dumps(
                                                    {"query": "الدحيح"})}}]},
            {"content": "الحد ده حد الدقيقة.", "tool_calls": []},
        ]
        res, executed = self._run_tool_loop("ازاي وصل للحد", script)
        self.assertEqual(executed, [], "no tool must execute")
        self.assertEqual(res["engine"], "groq")
        self.assertEqual(res["tools_used"], 0)
        self.assertIn("حد الدقيقة", res["reply"])

    def test_youtube_call_allowed_on_explicit_request(self):
        script = [
            {"content": "", "tool_calls": [{"id": "c1", "type": "function",
                                            "function": {
                                                "name": "browser_play_youtube",
                                                "arguments": json.dumps(
                                                    {"query": "الدحيح"})}}]},
            {"content": "شغّلت الفيديو.", "tool_calls": []},
        ]
        res, executed = self._run_tool_loop("شغّل الفيديو ده", script)
        self.assertEqual(len(executed), 1)
        self.assertEqual(executed[0][0], "browser_play_youtube")
        self.assertEqual(res["tools_used"], 1)

    def test_browser_open_blocked_without_open_intent(self):
        script = [
            {"content": "", "tool_calls": [{"id": "c1", "type": "function",
                                            "function": {
                                                "name": "browser_open",
                                                "arguments": json.dumps(
                                                    {"url": "https://example.com"})}}]},
            {"content": "تمام.", "tool_calls": []},
        ]
        res, executed = self._run_tool_loop("إيه رأيك في الموقع ده؟",
                                            script)
        self.assertEqual(executed, [])
        self.assertEqual(res["tools_used"], 0)


class TestFallbackChainFastFail(unittest.TestCase):
    """(b) Groq 429 + dead Ollama fails FAST with an honest message."""

    def test_all_engines_down_returns_fast(self):
        import requests
        started = time.monotonic()
        with mock.patch.object(brain_mod, "_gemini_chat",
                               side_effect=RuntimeError(
                                   "no-gemini-chat-key")), \
                mock.patch.object(
                    brain_mod, "_ollama",
                    side_effect=requests.ConnectionError("refused")), \
                mock.patch.object(brain_mod.shutil, "which",
                                  return_value=None):
            reply, engine = brain_mod._ask_fallback_chain(
                [{"role": "user", "content": "ازاي؟"}], "ar",
                "groq-429-tpm", 4.1)
        elapsed = time.monotonic() - started
        self.assertEqual(engine, "")
        self.assertLess(elapsed, 5, "must fail fast, not after 150s")
        self.assertIn("التوكنز في الدقيقة", reply)
        self.assertIn("مفتاح Gemini للمحادثة مش متسجل", reply)
        self.assertIn("مش متثبت", reply)
        self.assertNotIn("يومي", reply)
        self.assertNotIn("…", reply)


class TestFieldDomHarness(unittest.TestCase):
    """Run the jsdom field scenario (queue + cancel + cap + card)."""

    @classmethod
    def _run_harness(cls):
        node_modules = Path("/tmp/jsdom-test/node_modules")
        if not node_modules.exists():
            return None
        proc = subprocess.run(
            ["node", str(APP_DIR / "tests" / "freeze_field_dom.js")],
            capture_output=True, text=True, timeout=120,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin",
                 "NODE_PATH": str(node_modules)})
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_field_scenarios(self):
        data = self._run_harness()
        if data is None:
            self.skipTest("jsdom not installed at /tmp/jsdom-test")
        if "harness_error" in data:
            self.fail(f"jsdom harness crashed: {data['harness_error']}")
        failed = {k: v for k, v in data.items() if not v.get("pass")}
        self.assertEqual(failed, {}, f"failed jsdom checks: {failed}")
        self.assertTrue(data["field_queued_bubble_shown_at_once"]["pass"])
        self.assertTrue(data["field_stale_perm_cancelled_on_new_message"]["pass"])
        self.assertTrue(data["field_dots_still_visible_while_queued"]["pass"])
        self.assertTrue(data["field_queued_message_sent_after_first"]["pass"])
        self.assertTrue(data["field_dots_hidden_after_final_reply"]["pass"])
        self.assertTrue(data["cap_error_message_shown_not_dots"]["pass"])
        self.assertTrue(data["cancel_card_text"]["pass"])
        self.assertTrue(data["no_stale_card_in_new_turn"]["pass"])


if __name__ == "__main__":
    unittest.main()
