# LONA v1.6.1 - item 22: the "Groq hit its daily limit" message was
# wrong/misleading. brain.py used to turn EVERY HTTP 429 into
# "Groq وصل للحد اليومي" even though Groq has four separate free-tier
# limits for gpt-oss-120b (30 RPM, 1000 RPD, 8000 TPM, 200000 TPD)
# and the tightest is usually the 8000 tokens/min one. v1.6.1 reads
# the REAL limit from the 429 body/headers and says which limit and
# when it clears; an unclassified 429 never claims "daily". The
# local-fallback message also names the real Ollama state (not
# installed vs not running) instead of blaming the user's usage.
#
#   python3 -m pytest app/tests/test_v161_groq_limits.py -q

import sys
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import brain  # noqa: E402


def _fake_429_resp(body_text=None, headers=None, json_body=None):
    """A requests-like 429 response with a real Groq-shaped body."""
    resp = mock.Mock()
    resp.status_code = 429
    resp.text = body_text if body_text is not None else ""
    resp.headers = headers or {}
    if json_body is None:
        resp.json.return_value = {}
    else:
        resp.json.return_value = json_body
    return resp


def _groq_429_body(kind_phrase, limit=8000, used=7600, requested=950,
                   retry_s="4.1s"):
    """A real Groq 429 body for one limit kind."""
    msg = (f"Rate limit reached for model `openai/gpt-oss-120b` in "
           f"organization `org_xxx` service tier `on_demand` on "
           f"{kind_phrase}: Limit {limit}, Used {used}, "
           f"Requested {requested}. Please try again in {retry_s}. "
           "Visit https://console.groq.com/docs/rate-limits for more "
           "information.")
    return {"error": {"message": msg, "type": "tokens",
                      "code": "rate_limit_exceeded"}}


class TestClassifyGroq429(unittest.TestCase):
    """Each of the four Groq limits is read from the body."""

    def test_tpm_from_body(self):
        body = _groq_429_body("tokens per minute (TPM)")
        exc = brain._classify_groq_429(_fake_429_resp(
            headers={"retry-after": "2"}, json_body=body))
        self.assertEqual(exc.limit, "tpm")
        self.assertEqual(exc.retry_after, 2.0)
        self.assertEqual(str(exc), "groq-429")

    def test_rpm_from_body(self):
        body = _groq_429_body("requests per minute (RPM)", limit=30,
                              used=30, requested=1)
        body["error"]["type"] = "requests"
        exc = brain._classify_groq_429(_fake_429_resp(json_body=body))
        self.assertEqual(exc.limit, "rpm")
        # "try again in 4.1s" in the body is the retry source here.
        self.assertAlmostEqual(exc.retry_after, 4.1)

    def test_tpd_from_body(self):
        body = _groq_429_body("tokens per day (TPD)", limit=200000,
                              used=199000, requested=2000)
        exc = brain._classify_groq_429(_fake_429_resp(json_body=body))
        self.assertEqual(exc.limit, "tpd")

    def test_rpd_from_body(self):
        body = _groq_429_body("requests per day (RPD)", limit=1000,
                              used=1000, requested=1)
        body["error"]["type"] = "requests"
        exc = brain._classify_groq_429(_fake_429_resp(json_body=body))
        self.assertEqual(exc.limit, "rpd")

    def test_unknown_body_never_claims_daily(self):
        # Empty body, no headers: the kind is unknown, and the note
        # built from it must not contain the word "daily"/"يومي".
        exc = brain._classify_groq_429(_fake_429_resp())
        self.assertEqual(exc.limit, "unknown")
        self.assertIsNone(exc.retry_after)
        kind, _ = brain._groq_429_info(exc)
        self.assertEqual(kind, "groq-429")
        note = brain._groq_failure_note(kind, "ar")
        self.assertNotIn("يومي", note)
        self.assertNotIn("daily", brain._groq_failure_note(kind, "en"))

    def test_header_fallback_names_counter(self):
        # Body says nothing, but the token counter is exhausted.
        exc = brain._classify_groq_429(_fake_429_resp(headers={
            "x-ratelimit-remaining-tokens": "0",
            "x-ratelimit-remaining-requests": "900",
            "retry-after": "5"}))
        self.assertEqual(exc.limit, "tpm")
        self.assertEqual(exc.retry_after, 5.0)
        exc = brain._classify_groq_429(_fake_429_resp(headers={
            "x-ratelimit-remaining-tokens": "4000",
            "x-ratelimit-remaining-requests": "0"}))
        self.assertEqual(exc.limit, "rpd")

    def test_plain_runtime_error_stays_neutral(self):
        # Old code/tests raise a bare RuntimeError("groq-429") with no
        # limit info - it must map to the neutral kind, never daily.
        kind, retry_after = brain._groq_429_info(
            RuntimeError("groq-429"))
        self.assertEqual(kind, "groq-429")
        self.assertIsNone(retry_after)
        self.assertNotIn("يومي", brain._groq_failure_note(kind, "ar"))


class TestGroqFailureNotes(unittest.TestCase):
    """Each limit kind gets the right message + retry guidance."""

    def test_tpm_note_minute_not_daily(self):
        note = brain._groq_failure_note("groq-429-tpm", "ar")
        self.assertIn("التوكنز في الدقيقة", note)
        self.assertIn("جرّب كمان دقيقة", note)
        self.assertNotIn("يومي", note)
        self.assertNotIn("02:00", note)

    def test_rpm_note_minute_not_daily(self):
        note = brain._groq_failure_note("groq-429-rpm", "ar")
        self.assertIn("الطلبات في الدقيقة", note)
        self.assertIn("جرّب كمان دقيقة", note)
        self.assertNotIn("يومي", note)

    def test_tpd_note_names_daily_reset_time(self):
        note = brain._groq_failure_note("groq-429-tpd", "ar")
        self.assertIn("اليومي", note)
        self.assertIn("02:00 بتوقيت القاهرة", note)

    def test_rpd_note_names_daily_reset_time(self):
        note = brain._groq_failure_note("groq-429-rpd", "ar")
        self.assertIn("اليومي", note)
        self.assertIn("02:00 بتوقيت القاهرة", note)

    def test_long_retry_after_says_two_minutes(self):
        note = brain._groq_failure_note("groq-429-tpm", "ar",
                                        retry_after=120)
        self.assertIn("دقيقتين", note)
        note_en = brain._groq_failure_note("groq-429-tpm", "en",
                                           retry_after=120)
        self.assertIn("two minutes", note_en)

    def test_english_notes_match(self):
        self.assertIn("per-minute token limit",
                      brain._groq_failure_note("groq-429-tpm", "en"))
        self.assertIn("02:00 Cairo time",
                      brain._groq_failure_note("groq-429-tpd", "en"))
        self.assertNotIn("daily",
                         brain._groq_failure_note("groq-429", "en"))


class TestAskEndToEnd(unittest.TestCase):
    """Full ask() path: mocked Groq 429 + dead Ollama -> honest reply."""

    def _run(self, exc, ollama_which=None):
        import requests
        with mock.patch.object(brain, "is_online", return_value=True), \
                mock.patch.object(brain, "_groq",
                                  side_effect=exc), \
                mock.patch.object(
                    brain, "_ollama",
                    side_effect=requests.ConnectionError("refused")), \
                mock.patch.object(brain.shutil, "which",
                                  return_value=ollama_which):
            return brain.ask("إزيك؟", "LONA", "Mohamed", "ar")

    def test_tpm_429_plus_dead_ollama(self):
        res = self._run(brain.GroqRateLimit(limit="tpm", retry_after=4.1))
        self.assertEqual(res["engine"], "")
        self.assertIn("التوكنز في الدقيقة", res["reply"])
        self.assertIn("جرّب كمان دقيقة", res["reply"])
        self.assertNotIn("يومي", res["reply"])
        # Ollama is not installed in this scenario.
        self.assertIn("مش متثبت", res["reply"])
        self.assertIn("Ollama", res["reply"])

    def test_tpd_429_plus_dead_ollama(self):
        res = self._run(brain.GroqRateLimit(limit="tpd"))
        self.assertIn("اليومي", res["reply"])
        self.assertIn("02:00 بتوقيت القاهرة", res["reply"])

    def test_unknown_429_never_says_daily(self):
        res = self._run(RuntimeError("groq-429"))
        self.assertIn("Groq", res["reply"])
        self.assertNotIn("يومي", res["reply"])
        self.assertIn("مؤقتًا", res["reply"])


class TestOllamaStateMessage(unittest.TestCase):
    """The fallback failure names the REAL Ollama state."""

    def _ask_local(self, which):
        import requests
        with mock.patch.object(
                brain, "_ollama",
                side_effect=requests.ConnectionError("refused")), \
                mock.patch.object(brain.shutil, "which",
                                  return_value=which):
            return brain._ask_local(
                [{"role": "user", "content": "hi"}], "ar")

    def test_not_installed_says_not_installed(self):
        reply, engine = self._ask_local(None)
        self.assertEqual(engine, "")
        self.assertIn("مش متثبت", reply)
        self.assertIn("ollama.com", reply)
        self.assertNotIn("ollama serve", reply)

    def test_installed_but_not_running_says_start_it(self):
        reply, engine = self._ask_local("C:\\Program Files\\Ollama\\"
                                        "ollama.exe")
        self.assertEqual(engine, "")
        self.assertIn("متثبت بس مش شغال", reply)
        self.assertIn("ollama serve", reply)

    def test_english_not_installed(self):
        import requests
        with mock.patch.object(
                brain, "_ollama",
                side_effect=requests.ConnectionError("refused")), \
                mock.patch.object(brain.shutil, "which",
                                  return_value=None):
            reply, engine = brain._ask_local(
                [{"role": "user", "content": "hi"}], "en")
        self.assertIn("isn't installed", reply)
        self.assertIn("ollama.com", reply)


class TestGroqCallRaisesClassified(unittest.TestCase):
    """The two Groq call sites raise the classified exception."""

    def _resp_429(self, phrase):
        resp = mock.Mock()
        resp.status_code = 429
        resp.text = ""
        resp.headers = {"retry-after": "3"}
        resp.json.return_value = _groq_429_body(phrase)
        return resp

    def test_groq_raises_tpm(self):
        with mock.patch.object(brain, "read_key",
                               return_value="k"), \
                mock.patch.object(brain.requests, "post",
                                  return_value=self._resp_429(
                                      "tokens per minute (TPM)")):
            with self.assertRaises(brain.GroqRateLimit) as ctx:
                brain._groq([{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.limit, "tpm")
        self.assertEqual(ctx.exception.retry_after, 3.0)

    def test_groq_message_raises_tpd(self):
        with mock.patch.object(brain, "read_key",
                               return_value="k"), \
                mock.patch.object(brain.requests, "post",
                                  return_value=self._resp_429(
                                      "tokens per day (TPD)")):
            with self.assertRaises(brain.GroqRateLimit) as ctx:
                brain._groq_message([{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.limit, "tpd")


if __name__ == "__main__":
    unittest.main()
