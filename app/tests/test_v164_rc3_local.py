# LONA v1.6.4 rc.3 - local-first routing for trivial turns + the
# per-reply cost line.
#
# FIELD CONTEXT (2026-11-23): the user asked for OpenJarvis-style
# local-first behaviour - simple questions answered on-device for
# zero cloud tokens - and a real cost line under every reply
# (tokens / engine / time). The Ollama path already existed as a
# fallback; rc.3 promotes it to FIRST for trivial turns only.
#
# This file proves:
#   * the classifier: greetings, the time and short factual asks
#     are local-first; Deep Think, actions, device questions, web
#     lookups, code, explanations and long messages stay on cloud;
#   * a simple ask/tool chat in auto mode answers from local Ollama
#     (15 s cap) with 0 cloud tokens and never touches Groq/Gemini;
#   * Ollama down / refusing / timing out falls back to the cloud
#     silently - the user still gets an answer, never a dead reply;
#   * a local reply records 0 cloud tokens (the Groq/Gemini token
#     bar stays untouched and truthful);
#   * the cost line data: tokens_used / engine / elapsed_sec are
#     present in the ask result dict, measured from the engines'
#     own usage fields - and tokens_used is None (UI shows "—")
#     when the engine reported no usage or nothing answered;
#   * a strict model choice never tries local.
#
#   python3 -m pytest app/tests/test_v164_rc3_local.py -q

import sys
import tempfile
import threading
import time
import types as _types
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import requests  # noqa: E402

import brain as brain_mod  # noqa: E402
import memory as memory_mod  # noqa: E402

# -- minimal pywebview stub (main.py imports it at module level) -----
# Same trick as test_v162_backend.py: the app is headless here.
_fake_webview = _types.ModuleType("webview")


class _FakeWindow:
    def minimize(self):
        pass

    def maximize(self):
        pass

    def restore(self):
        pass

    def move(self, x, y):
        pass

    def destroy(self):
        pass

    def show(self):
        pass

    def hide(self):
        pass

    def evaluate_js(self, js):
        return None


_fake_webview.create_window = lambda *a, **k: _FakeWindow()
_fake_webview.start = lambda *a, **k: None
_fake_webview.windows = []
sys.modules.setdefault("webview", _fake_webview)

import main as main_mod  # noqa: E402
import settings_store  # noqa: E402


def _wait_chat(api, chat_id, timeout=5.0):
    """Poll chat_result until the turn lands (or timeout)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = api.chat_result(chat_id)
        if res.get("done"):
            return res
        time.sleep(0.05)
    return api.chat_result(chat_id)


def _groq_resp(content="تمام.", usage=None):
    """A requests-like Groq 200 response with an optional usage field."""
    resp = mock.Mock()
    resp.status_code = 200
    body = {"choices": [{"message": {"content": content}}]}
    if usage is not None:
        body["usage"] = usage
    resp.json.return_value = body
    resp.headers = {}
    return resp


def _gemini_resp(text="أهلاً من Gemini", usage=None):
    """A requests-like Gemini 200 response with optional usageMetadata."""
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


class _AskCase(_StoreCase):
    """ask()/ask_with_tools() with the network fully scripted."""

    def _patches(self, ollama_up=True, ollama_reply="أهلاً يا محمد",
                 ollama_exc=None, ollama_timeout_seen=None,
                 gemini_reply="أهلاً من Gemini",
                 groq_reply="تمام."):
        """Patchers for one ask call. _ollama is the real seam:
        the local-first attempt runs through the real _ask_local
        (timeout + failure mapping), only the HTTP call is faked.
        Groq/Gemini answer through the real _groq/_gemini_chat over
        a scripted requests.post when their reply is given; pass
        None to make that engine raise instead."""
        seen = ollama_timeout_seen if ollama_timeout_seen is not None \
            else {}

        def fake_ollama(messages, timeout=None):
            seen["timeout"] = timeout
            if ollama_exc is not None:
                raise ollama_exc
            return ollama_reply

        def fake_read_key(name, quiet=False):
            if name == "gemini-chat-key.txt":
                return "AIzaChatKey"
            if name == "groq-key.txt":
                return "gsk-test"
            return ""

        def fake_post(url, headers=None, **kw):
            if "generativelanguage.googleapis.com" in url:
                if gemini_reply is None:
                    raise AssertionError("Gemini must not be called")
                return _gemini_resp(
                    gemini_reply,
                    usage={"promptTokenCount": 40,
                           "candidatesTokenCount": 12,
                           "totalTokenCount": 52})
            if "api.groq.com" in url:
                if groq_reply is None:
                    raise AssertionError("Groq must not be called")
                return _groq_resp(
                    groq_reply,
                    usage={"prompt_tokens": 100,
                           "completion_tokens": 23,
                           "total_tokens": 123})
            raise AssertionError(f"unexpected POST to {url}")

        return [
            mock.patch.object(brain_mod, "is_online", return_value=True),
            mock.patch.object(brain_mod, "read_key",
                              side_effect=fake_read_key),
            mock.patch.object(brain_mod.requests, "post",
                              side_effect=fake_post),
            mock.patch.object(brain_mod, "get_memory_store",
                              return_value=None),
            mock.patch.object(brain_mod, "ollama_available",
                              return_value=ollama_up),
            mock.patch.object(brain_mod, "_ollama",
                              side_effect=fake_ollama),
        ]

    def _ask(self, text="إزيك؟", tools=False, deep_think=False,
             model_choice="auto", **kw):
        patchers = self._patches(**kw)
        for p in patchers:
            p.start()
        try:
            if tools:
                return brain_mod.ask_with_tools(
                    text, "LONA", "Mohamed", "ar", history=[],
                    deep_think=deep_think, model_choice=model_choice)
            return brain_mod.ask(text, "LONA", "Mohamed", "ar",
                                 history=[], deep_think=deep_think,
                                 model_choice=model_choice)
        finally:
            for p in reversed(patchers):
                p.stop()


class TestLocalFirstClassifier(_StoreCase):
    """The classifier is the gate: only trivial turns go local."""

    def test_trivial_turns_are_local_first(self):
        for text in ("إزيك؟", "hello", "الساعة كام؟",
                     "صباح الخير", "شكراً يا لونا",
                     "عاصمة مصر إيه؟", "تمام"):
            self.assertTrue(brain_mod._is_local_first(text), text)

    def test_deep_think_never_local(self):
        self.assertFalse(brain_mod._is_local_first("إزيك؟",
                                                   deep_think=True))

    def test_actions_stay_on_cloud(self):
        # A tool-requiring ask must never go to the chat-only 3B
        # model - it cannot call tools, so a local "done" would be
        # a lie (nothing would actually open).
        self.assertFalse(brain_mod._is_local_first("افتح VS Code"))
        self.assertFalse(brain_mod._is_local_first("افتح برنامج الرسام"))
        self.assertFalse(brain_mod._is_local_first("شغّل أغنية"))

    def test_device_web_code_stay_on_cloud(self):
        self.assertFalse(brain_mod._is_local_first("اللابتوب عامل إيه؟"))
        self.assertFalse(brain_mod._is_local_first("سعر الدولار كام؟"))
        self.assertFalse(brain_mod._is_local_first("```python\nprint(1)"))

    def test_explanations_stay_on_cloud(self):
        # The small model guesses at explanations - keep those on
        # the cloud even when short.
        self.assertFalse(brain_mod._is_local_first("اشرحلي النحو"))
        self.assertFalse(brain_mod._is_local_first("ليه السماء زرقا؟"))
        self.assertFalse(brain_mod._is_local_first("ازاي أثبت بايثون؟"))

    def test_long_turns_stay_on_cloud(self):
        self.assertFalse(brain_mod._is_local_first("x" * 121))
        self.assertTrue(brain_mod._is_local_first("x" * 120))

    def test_now_line_is_real_and_does_not_mutate(self):
        msgs = [{"role": "system", "content": "base"},
                {"role": "user", "content": "الساعة كام؟"}]
        out = brain_mod._with_now_line(msgs, "ar")
        self.assertEqual(msgs[0]["content"], "base")
        self.assertIn("الوقت الحالي على جهاز صاحبك:", out[0]["content"])
        self.assertEqual(out[1], msgs[1])


class TestLocalFirstRouting(_AskCase):
    """Simple turns answer locally first; anything else stays cloud."""

    def test_simple_ask_goes_local_first(self):
        seen = {}
        res = self._ask(ollama_timeout_seen=seen)
        self.assertEqual(res["engine"], "ollama")
        self.assertEqual(res["reply"], "أهلاً يا محمد")
        # The slow-laptop cap: 15 s, not the old 60 s.
        self.assertEqual(seen.get("timeout"),
                         brain_mod._LOCAL_FIRST_TIMEOUT_SEC)
        self.assertEqual(brain_mod._LOCAL_FIRST_TIMEOUT_SEC, 15)
        # Cost line: 0 cloud tokens, real elapsed time.
        self.assertEqual(res["tokens_used"], 0)
        self.assertIsInstance(res["elapsed_sec"], float)
        self.assertGreaterEqual(res["elapsed_sec"], 0.0)

    def test_simple_tool_chat_goes_local_first(self):
        res = self._ask(tools=True)
        self.assertEqual(res["engine"], "ollama")
        self.assertEqual(res["tools_used"], 0)
        self.assertFalse(res["tools_disabled"])
        self.assertEqual(res["tokens_used"], 0)

    def test_deep_think_stays_on_cloud(self):
        # Deep Think never takes the simple route at all: not local,
        # not Gemini - it goes to Groq 120b with reasoning effort.
        res = self._ask(deep_think=True)
        self.assertEqual(res["engine"], "groq")
        self.assertEqual(res["reply"], "تمام.")

    def test_action_stays_on_cloud(self):
        res = self._ask(tools=True, text="افتح برنامج الرسام",
                        ollama_exc=AssertionError(
                            "local must not be tried for actions"))
        self.assertEqual(res["engine"], "groq")
        self.assertEqual(res["reply"], "تمام.")

    def test_hard_question_stays_on_cloud(self):
        # A 200-char plain turn is too long for local, but still
        # "simple" for rc.2 - so it goes to Gemini, never to the
        # local model (the AssertionError proves no local attempt).
        res = self._ask(text="x" * 200,
                        ollama_exc=AssertionError(
                            "local must not be tried for long turns"))
        self.assertEqual(res["engine"], "gemini")

    def test_strict_groq_choice_never_tries_local(self):
        res = self._ask(model_choice="groq",
                        ollama_exc=AssertionError(
                            "strict Groq must not try local"))
        self.assertEqual(res["engine"], "groq")

    def test_strict_gemini_choice_never_tries_local(self):
        res = self._ask(model_choice="gemini",
                        ollama_exc=AssertionError(
                            "strict Gemini must not try local"))
        self.assertEqual(res["engine"], "gemini")


class TestLocalFallback(_AskCase):
    """Ollama down / slow must fall back to the cloud silently."""

    def test_ollama_down_falls_back_to_cloud(self):
        res = self._ask(ollama_up=False,
                        ollama_exc=AssertionError(
                            "no local attempt when Ollama is down"))
        self.assertEqual(res["engine"], "gemini")
        self.assertIn("Gemini", res["reply"])

    def test_ollama_refused_falls_back_to_cloud(self):
        res = self._ask(
            ollama_exc=requests.ConnectionError("refused"))
        self.assertEqual(res["engine"], "gemini")
        self.assertIn("Gemini", res["reply"])

    def test_local_timeout_falls_back_to_cloud(self):
        seen = {}
        res = self._ask(ollama_exc=requests.Timeout("slow"),
                        ollama_timeout_seen=seen)
        self.assertEqual(res["engine"], "gemini")
        self.assertIn("Gemini", res["reply"])
        # Even the timed-out attempt was capped at 15 s.
        self.assertEqual(seen.get("timeout"), 15)

    def test_local_empty_reply_falls_back_to_cloud(self):
        res = self._ask(ollama_exc=RuntimeError("ollama-empty-reply"))
        self.assertEqual(res["engine"], "gemini")

    def test_local_tool_chat_falls_back_to_cloud(self):
        res = self._ask(tools=True,
                        ollama_exc=requests.ConnectionError("refused"))
        self.assertEqual(res["engine"], "gemini")
        self.assertFalse(res["tools_disabled"])


class TestLocalZeroTokens(_AskCase):
    """A local reply costs 0 cloud tokens - and records it."""

    def test_local_reply_leaves_token_bar_untouched(self):
        res = self._ask()
        self.assertEqual(res["engine"], "ollama")
        self.assertEqual(res["tokens_used"], 0)
        usage = brain_mod.get_token_usage()
        for engine in ("groq", "gemini"):
            self.assertFalse(usage[engine]["has_data"])
            self.assertIsNone(usage[engine]["used"])


class TestCostLineData(_AskCase):
    """tokens_used / engine / elapsed_sec in the ask result dict."""

    def test_groq_tokens_measured_from_usage(self):
        res = self._ask(text="افتح VS Code")
        self.assertEqual(res["engine"], "groq")
        self.assertEqual(res["tokens_used"], 123)
        self.assertIsInstance(res["elapsed_sec"], float)
        self.assertGreaterEqual(res["elapsed_sec"], 0.0)

    def test_gemini_tokens_measured_from_usage(self):
        res = self._ask(ollama_up=False)
        self.assertEqual(res["engine"], "gemini")
        self.assertEqual(res["tokens_used"], 52)
        self.assertIsInstance(res["elapsed_sec"], float)

    def test_tool_loop_tokens_sum_all_rounds(self):
        # Two Groq rounds in one turn: the cost line shows the
        # turn's total cloud tokens, not just the last round.
        calls = {"n": 0}

        def groq_message(messages, use_tools=False, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"content": "",
                        "tool_calls": [{"id": "c1", "function": {
                            "name": "system_status",
                            "arguments": "{}"}}]}
            return {"content": "تمام.", "tool_calls": []}

        # _groq_message is mocked, so record usage the way the real
        # one would: 10 tokens per round.
        def groq_message_recording(*args, **kwargs):
            brain_mod._record_engine_usage(
                "groq", {"prompt_tokens": 7, "completion_tokens": 3})
            return groq_message(*args, **kwargs)

        with mock.patch.object(brain_mod, "is_online",
                               return_value=True), \
                mock.patch.object(brain_mod, "get_memory_store",
                                  return_value=None), \
                mock.patch.object(brain_mod, "ollama_available",
                                  return_value=False), \
                mock.patch.object(brain_mod, "_groq_message",
                                  side_effect=groq_message_recording), \
                mock.patch.object(brain_mod.tools, "execute_tool",
                                  return_value="CPU 5%"):
            res = brain_mod.ask_with_tools(
                "افتح برنامج الرسام", "LONA", "Mohamed", "ar",
                history=[])
        self.assertEqual(res["engine"], "groq")
        self.assertEqual(res["tokens_used"], 20)

    def test_no_usage_field_is_none_not_zero(self):
        # Groq answered but reported no usage: the cost is
        # genuinely unknown - None (the UI shows "—"), never a
        # fake 0.
        def fake_post(url, headers=None, **kw):
            return _groq_resp("تمام.", usage=None)

        with mock.patch.object(brain_mod, "is_online",
                               return_value=True), \
                mock.patch.object(brain_mod, "read_key",
                                  return_value="gsk-test"), \
                mock.patch.object(brain_mod.requests, "post",
                                  side_effect=fake_post), \
                mock.patch.object(brain_mod, "get_memory_store",
                                  return_value=None), \
                mock.patch.object(brain_mod, "ollama_available",
                                  return_value=False):
            res = brain_mod.ask("افتح VS Code", "LONA", "Mohamed",
                                "ar", history=[])
        self.assertEqual(res["engine"], "groq")
        self.assertIsNone(res["tokens_used"])
        self.assertIsInstance(res["elapsed_sec"], float)

    def test_all_down_tokens_is_none(self):
        # Nothing answered: tokens unknown, elapsed still measured.
        with mock.patch.object(brain_mod, "is_online",
                               return_value=True), \
                mock.patch.object(brain_mod, "read_key",
                                  return_value=""), \
                mock.patch.object(brain_mod, "get_memory_store",
                                  return_value=None), \
                mock.patch.object(brain_mod, "ollama_available",
                                  return_value=False), \
                mock.patch.object(
                    brain_mod, "_groq",
                    side_effect=brain_mod.GroqRateLimit(limit="tpm")), \
                mock.patch.object(
                    brain_mod, "_ollama",
                    side_effect=requests.ConnectionError("refused")), \
                mock.patch.object(brain_mod.shutil, "which",
                                  return_value=None):
            res = brain_mod.ask("إزيك؟", "LONA", "Mohamed", "ar",
                                history=[])
        self.assertEqual(res["engine"], "")
        self.assertIsNone(res["tokens_used"])
        self.assertIsInstance(res["elapsed_sec"], float)

    def test_turn_tokens_for(self):
        self.assertEqual(brain_mod._turn_tokens_for("ollama"), 0)
        self.assertEqual(brain_mod._turn_tokens_for("memory"), 0)
        self.assertEqual(brain_mod._turn_tokens_for("browser"), 0)
        self.assertIsNone(brain_mod._turn_tokens_for("groq"))
        self.assertIsNone(brain_mod._turn_tokens_for("gemini"))
        self.assertIsNone(brain_mod._turn_tokens_for(""))
        self.assertIsNone(brain_mod._turn_tokens_for("unknown"))


class TestCostLinePlumbing(unittest.TestCase):
    """The measured cost survives the bridge: brain result ->
    _chats entry -> chat_result() -> the UI's cost line."""

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
        main_mod._APP = None

    def _run_chat(self, fake_result):
        def fake_ask(*args, **kwargs):
            return dict(fake_result)

        with mock.patch.object(brain_mod, "ask_with_tools",
                               side_effect=fake_ask), \
                mock.patch.object(self.app, "speak_async",
                                  lambda text: None):
            res = self.api.chat("عاصمة مصر إيه؟")
            self.assertTrue(res.get("pending"),
                            "rules must not intercept this question")
            return _wait_chat(self.api, res["chat_id"])

    def test_chat_result_carries_measured_cost(self):
        final = self._run_chat({"reply": "القاهرة.", "engine": "groq",
                                "tools_used": 0, "tools_disabled": False,
                                "tokens_used": 123, "elapsed_sec": 2.4})
        self.assertTrue(final.get("done"))
        self.assertEqual(final.get("reply"), "القاهرة.")
        self.assertEqual(final.get("engine"), "groq")
        self.assertEqual(final.get("tokens_used"), 123)
        self.assertEqual(final.get("elapsed_sec"), 2.4)

    def test_chat_result_cost_unknown_is_none(self):
        # A brain result without cost keys (older caller / failure
        # path) still polls fine - the UI shows "—", never a number.
        final = self._run_chat({"reply": "تمام.", "engine": "ollama",
                                "tools_used": 0, "tools_disabled": False})
        self.assertTrue(final.get("done"))
        self.assertIsNone(final.get("tokens_used"))
        self.assertIsNone(final.get("elapsed_sec"))


if __name__ == "__main__":
    unittest.main()
