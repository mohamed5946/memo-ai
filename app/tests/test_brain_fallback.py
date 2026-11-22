# LONA v1.6.4 rc.2 - W5: the brain fallback chain must actually work.
#
# FIELD FAILURE (2026-11-22 evening): Groq hit its 8000 tokens/min
# rate limit AND Gemini failed at the same moment, leaving NO working
# brain (Ollama not installed) - the user got an error instead of an
# answer from the next engine. The user-facing Gemini note was the
# bare "Gemini فشل" with no reason.
#
# ROOT CAUSE (in brain.py): Google answers an invalid Gemini API key
# with HTTP 400 ("API key not valid", error.status INVALID_ARGUMENT,
# details[].reason API_KEY_INVALID) - NOT 401/403. The old
# _gemini_chat only treated 401/403 as key-rejected, so every 400
# fell through raise_for_status() into the generic "gemini-failed"
# kind. The key was the actual problem and the code hid it.
#
# This file proves:
#   * a Groq 429 (any limit, incl. the 8000 TPM one) falls back to
#     Gemini, and the engine badge names Gemini;
#   * a Groq failure of ANY kind (429, 5xx, transport error) fires
#     the chain - never surfaces as the final error;
#   * every Gemini HTTP error is classified from the response body
#     (400 API_KEY_INVALID -> key-rejected, 400 FAILED_PRECONDITION
#     -> api-not-enabled, 404 -> model-not-found, 5xx -> unavailable);
#   * when ALL engines are down, the reply names each engine and why
#     (no bare "failed" with no reason), in both ask() and the
#     ask_with_tools() tool loop.
#
#   python3 -m pytest app/tests/test_brain_fallback.py -q

import sys
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import requests  # noqa: E402

import brain as brain_mod  # noqa: E402


def _gemini_resp(status=200, json_body=None):
    """A requests-like Gemini response."""
    resp = mock.Mock()
    resp.status_code = status
    resp.json.return_value = json_body if json_body is not None else {}
    return resp


def _gemini_ok(text="أهلاً، أنا Gemini"):
    return _gemini_resp(200, {"candidates": [{"content": {"parts": [
        {"text": text}]}}]})


def _gemini_error(status, err_status, reason, message):
    """A real-shaped Gemini error body, as Google sends it."""
    return _gemini_resp(status, {"error": {
        "code": status, "message": message, "status": err_status,
        "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                     "reason": reason, "domain": "googleapis.com",
                     "metadata": {"service":
                                  "generativelanguage.googleapis.com"}}]}})


def _http_error(status):
    """A requests.HTTPError carrying a response with this status."""
    resp = mock.Mock()
    resp.status_code = status
    err = requests.HTTPError(f"{status} Client Error")
    err.response = resp
    return err


class _ChainHarness:
    """Run brain.ask / brain.ask_with_tools with Groq failing and a
    scripted Gemini HTTP response. No live network, no real keys,
    no real Ollama, no real memory store."""

    def __init__(self, test, groq_exc, gemini_resp=None,
                 gemini_exc=None, ollama_reply=None, chat_key="AIzaChatKey"):
        self.test = test
        self.groq_exc = groq_exc
        self.gemini_resp = gemini_resp
        self.gemini_exc = gemini_exc
        self.ollama_reply = ollama_reply
        self.chat_key = chat_key
        self.posts = []

    def _fake_read_key(self, name, quiet=False):
        if name == "gemini-chat-key.txt":
            return self.chat_key
        return ""

    def _fake_post(self, url, headers=None, **kw):
        self.posts.append(url)
        if "generativelanguage.googleapis.com" not in url:
            raise AssertionError(f"unexpected POST to {url}")
        if self.gemini_exc is not None:
            raise self.gemini_exc
        return self.gemini_resp

    def _patchers(self):
        test = self.test
        patchers = [
            mock.patch.object(brain_mod, "is_online", return_value=True),
            mock.patch.object(brain_mod, "read_key",
                              side_effect=self._fake_read_key),
            mock.patch.object(brain_mod.requests, "post",
                              side_effect=self._fake_post),
            mock.patch.object(brain_mod, "get_memory_store",
                              return_value=None),
            mock.patch.object(brain_mod.shutil, "which", return_value=None),
        ]
        if self.ollama_reply is not None:
            patchers.append(mock.patch.object(
                brain_mod, "_ollama", return_value=self.ollama_reply))
        else:
            patchers.append(mock.patch.object(
                brain_mod, "_ollama",
                side_effect=requests.ConnectionError("refused")))
        return patchers

    def ask(self, tools=False, text="إزيك؟"):
        patchers = self._patchers()
        if tools:
            patchers.append(mock.patch.object(
                brain_mod, "_groq_message", side_effect=self.groq_exc))
        else:
            patchers.append(mock.patch.object(
                brain_mod, "_groq", side_effect=self.groq_exc))
        for p in patchers:
            p.start()
        try:
            if tools:
                return brain_mod.ask_with_tools(
                    text, "LONA", "Mohamed", "ar", history=[])
            return brain_mod.ask(text, "LONA", "Mohamed", "ar",
                                 history=[])
        finally:
            for p in reversed(patchers):
                p.stop()


def _groq_429_tpm():
    return brain_mod.GroqRateLimit(limit="tpm", retry_after=4.1)


class TestGroqRateLimitFallsBackToGemini(unittest.TestCase):
    """A Groq 429 MUST trigger the fallback to Gemini, never surface
    as the final error."""

    def test_tpm_429_gemini_answers(self):
        res = _ChainHarness(self, _groq_429_tpm(),
                            gemini_resp=_gemini_ok()).ask()
        self.assertEqual(res["engine"], "gemini")
        self.assertIn("Gemini", res["reply"])
        # The 429 text never leaks into the answer.
        self.assertNotIn("8000", res["reply"])
        self.assertNotIn("وصل للحد", res["reply"])

    def test_tpm_429_gemini_answers_in_tool_loop(self):
        # rc.2 W8: a simple greeting now routes to Gemini FIRST in
        # auto mode, so the 429-fallback path needs a HARD question
        # (an action) that actually reaches the Groq tool loop.
        res = _ChainHarness(self, _groq_429_tpm(),
                            gemini_resp=_gemini_ok()).ask(
                                tools=True, text="افتح برنامج الرسام")
        self.assertEqual(res["engine"], "gemini")
        self.assertIn("Gemini", res["reply"])
        # The tool loop fell back with tools disabled, honestly.
        self.assertTrue(res["tools_disabled"])

    def test_groq_transport_error_fires_chain(self):
        """A non-RuntimeError Groq failure (connection cut) also
        falls back - the chain is not 429-only."""
        res = _ChainHarness(
            self, requests.ConnectionError("groq unreachable"),
            gemini_resp=_gemini_ok()).ask()
        self.assertEqual(res["engine"], "gemini")

    def test_groq_500_fires_chain(self):
        res = _ChainHarness(self, _http_error(500),
                            gemini_resp=_gemini_ok()).ask()
        self.assertEqual(res["engine"], "gemini")

    def test_groq_401_fires_chain(self):
        """Even a rejected Groq key falls back - the user still gets
        an answer from Gemini instead of a Groq error."""
        res = _ChainHarness(self, _http_error(401),
                            gemini_resp=_gemini_ok()).ask()
        self.assertEqual(res["engine"], "gemini")


class TestGeminiErrorClassification(unittest.TestCase):
    """Every Gemini HTTP error is classified from the body."""

    def _kind(self, resp):
        with self.assertRaises(RuntimeError) as ctx:
            with mock.patch.object(brain_mod, "read_key",
                                   return_value="AIzaChatKey"), \
                    mock.patch.object(brain_mod.requests, "post",
                                      return_value=resp):
                brain_mod._gemini_chat([{"role": "user", "content": "hi"}])
        return str(ctx.exception)

    def test_400_api_key_invalid_is_key_rejected(self):
        # THE field bug: Google sends 400, not 401, for a bad key.
        resp = _gemini_error(400, "INVALID_ARGUMENT", "API_KEY_INVALID",
                             "API key not valid. Please pass a valid API key.")
        self.assertEqual(self._kind(resp), "gemini-key-rejected")

    def test_401_and_403_still_key_rejected(self):
        for status in (401, 403):
            resp = _gemini_error(status, "UNAUTHENTICATED", "", "")
            self.assertEqual(self._kind(resp), "gemini-key-rejected")

    def test_400_failed_precondition_is_api_not_enabled(self):
        resp = _gemini_error(
            400, "FAILED_PRECONDITION", "SERVICE_DISABLED",
            "Generative Language API has not been used in project "
            "123 before or it is disabled.")
        self.assertEqual(self._kind(resp), "gemini-api-not-enabled")

    def test_404_is_model_not_found(self):
        resp = _gemini_error(404, "NOT_FOUND", "",
                             "models/gemini-2.5-flash is not found.")
        self.assertEqual(self._kind(resp), "gemini-model-not-found")

    def test_503_is_unavailable(self):
        resp = _gemini_error(503, "UNAVAILABLE", "",
                             "The service is currently unavailable.")
        self.assertEqual(self._kind(resp), "gemini-unavailable")

    def test_429_is_gemini_429(self):
        resp = _gemini_error(429, "RESOURCE_EXHAUSTED", "",
                             "Resource has been exhausted.")
        self.assertEqual(self._kind(resp), "gemini-429")

    def test_other_400_is_bad_request_not_bare_failed(self):
        resp = _gemini_error(400, "INVALID_ARGUMENT", "",
                             "Request contains an invalid argument.")
        self.assertEqual(self._kind(resp), "gemini-bad-request")

    def test_safety_block_is_blocked_not_empty_reply(self):
        resp = _gemini_resp(200, {"candidates": [],
                                  "promptFeedback": {"blockReason": "SAFETY"}})
        self.assertEqual(self._kind(resp), "gemini-blocked")

    def test_no_chat_key_is_no_key_kind(self):
        with mock.patch.object(brain_mod, "read_key", return_value=""):
            reply, engine, kind = brain_mod._ask_gemini_chat(
                [{"role": "user", "content": "hi"}], "ar")
        self.assertEqual(kind, "no-gemini-chat-key")
        self.assertEqual(engine, "")

    def test_transport_error_is_unreachable(self):
        with mock.patch.object(brain_mod, "read_key",
                               return_value="AIzaChatKey"), \
                mock.patch.object(brain_mod.requests, "post",
                                  side_effect=requests.ConnectionError(
                                      "generativelanguage unreachable")):
            reply, engine, kind = brain_mod._ask_gemini_chat(
                [{"role": "user", "content": "hi"}], "ar")
        self.assertEqual(kind, "gemini-unreachable")
        self.assertEqual(engine, "")


class TestAllDownNamesEveryEngine(unittest.TestCase):
    """Only when ALL engines fail may an error be shown, and it must
    say which engines were tried and why each failed."""

    def test_field_case_groq_tpm_gemini_bad_key_ollama_absent(self):
        """The exact field night: Groq 8000 TPM + Gemini key rejected
        (400 API_KEY_INVALID) + Ollama not installed."""
        resp = _gemini_error(400, "INVALID_ARGUMENT", "API_KEY_INVALID",
                             "API key not valid. Please pass a valid API key.")
        res = _ChainHarness(self, _groq_429_tpm(),
                            gemini_resp=resp).ask()
        self.assertEqual(res["engine"], "")
        # Groq: the real per-minute limit, never "daily".
        self.assertIn("التوكنز في الدقيقة", res["reply"])
        self.assertNotIn("يومي", res["reply"])
        # Gemini: the key is the reason now - NOT the bare "Gemini فشل".
        self.assertIn("مفتاح Gemini", res["reply"])
        self.assertIn("مرفوض", res["reply"])
        self.assertNotIn("Gemini فشل", res["reply"])
        # Ollama: not installed.
        self.assertIn("مش متثبت", res["reply"])

    def test_all_down_gemini_unavailable(self):
        res = _ChainHarness(
            self, _groq_429_tpm(),
            gemini_resp=_gemini_error(503, "UNAVAILABLE", "",
                                      "The service is currently unavailable.")
        ).ask()
        self.assertEqual(res["engine"], "")
        self.assertIn("التوكنز في الدقيقة", res["reply"])
        self.assertIn("سيرفرات Gemini مش متاحة", res["reply"])
        self.assertIn("مش متثبت", res["reply"])

    def test_all_down_gemini_unreachable_groq_unreachable(self):
        res = _ChainHarness(
            self, requests.ConnectionError("groq unreachable"),
            gemini_exc=requests.ConnectionError("google unreachable")
        ).ask()
        self.assertEqual(res["engine"], "")
        self.assertIn("مش قادر أوصل لـ Groq", res["reply"])
        self.assertIn("مش قادر أوصل لـ Gemini", res["reply"])
        self.assertIn("مش متثبت", res["reply"])

    def test_all_down_in_tool_loop(self):
        resp = _gemini_error(400, "INVALID_ARGUMENT", "API_KEY_INVALID",
                             "API key not valid. Please pass a valid API key.")
        res = _ChainHarness(self, _groq_429_tpm(),
                            gemini_resp=resp).ask(tools=True)
        self.assertEqual(res["engine"], "")
        self.assertIn("التوكنز في الدقيقة", res["reply"])
        self.assertIn("مفتاح Gemini", res["reply"])
        self.assertIn("مرفوض", res["reply"])

    def test_groq_500_named_in_all_down(self):
        """A Groq 500 is named as Groq down, not a bare 'Groq فشل'."""
        res = _ChainHarness(
            self, _http_error(500),
            gemini_resp=_gemini_error(500, "UNAVAILABLE", "",
                                      "The service is currently unavailable.")
        ).ask()
        self.assertEqual(res["engine"], "")
        self.assertIn("سيرفرات Groq مش متاحة", res["reply"])
        self.assertIn("سيرفرات Gemini مش متاحة", res["reply"])
        self.assertNotIn("Groq فشل", res["reply"])


class TestGroqFailureKinds(unittest.TestCase):
    """_groq_failure_kind keeps the real reason for the all-down note."""

    def test_known_runtime_kinds_kept(self):
        self.assertEqual(
            brain_mod._groq_failure_kind(RuntimeError("no-groq-key")),
            "no-groq-key")
        self.assertEqual(
            brain_mod._groq_failure_kind(RuntimeError("groq-empty-reply")),
            "groq-empty-reply")

    def test_http_statuses(self):
        self.assertEqual(brain_mod._groq_failure_kind(_http_error(401)),
                         "groq-key-rejected")
        self.assertEqual(brain_mod._groq_failure_kind(_http_error(400)),
                         "groq-bad-request")
        self.assertEqual(brain_mod._groq_failure_kind(_http_error(503)),
                         "groq-unavailable")

    def test_transport_is_unreachable(self):
        self.assertEqual(
            brain_mod._groq_failure_kind(
                requests.ConnectionError("cut")),
            "groq-unreachable")
        self.assertEqual(
            brain_mod._groq_failure_kind(requests.Timeout("slow")),
            "groq-unreachable")

    def test_unknown_stays_failed(self):
        self.assertEqual(
            brain_mod._groq_failure_kind(ValueError("weird")), "failed")


if __name__ == "__main__":
    unittest.main()
