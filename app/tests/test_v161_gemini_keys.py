# LONA v1.6.1 - item 23: TWO separate Gemini keys in Settings.
#
# Before v1.6.1 there was one "gemini" key serving BOTH online voice
# (Gemini TTS) and vision (Gemini 2.5 Flash). Now there are two
# independent keys, each its own DPAPI entry / txt file by name:
#   "gemini-voice" - Gemini TTS, online voice ONLY.
#   "gemini-chat"  - Gemini 2.5 Flash, chat-brain fallback + vision.
# The chat fallback chain is Groq -> Gemini (2.5 Flash, chat key
# only) -> local Ollama. The voice key is never used for chat and
# the chat key is never used for voice.
#
# MIGRATION RULE (chosen because the old key served both purposes):
# a stored single "gemini" key is copied into BOTH new fields as two
# independent values (both keep working exactly as before), and the
# old entry is deleted only after both copies verify. A new provider
# that already has its own key is never overwritten.
#
#   python3 -m pytest app/tests/test_v161_gemini_keys.py -q

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
UI_DIR = APP_DIR / "ui"
sys.path.insert(0, str(APP_DIR))

import apikeys as apikeys_mod  # noqa: E402
import brain as brain_mod  # noqa: E402
import settings_store as settings_mod  # noqa: E402


# Every test in this file patches settings_store.KEYS_DIR to a fresh
# tmp dir and assumes that isolates the key store. On Windows that
# assumption is FALSE: the real store is Credential Manager, a shared
# OS vault keyed by fixed target names ("LONA:gemini-voice", ...),
# so a key saved by one test leaks into every later test in the same
# process - which is exactly how CI failed: a gemini-chat key saved
# by test_v160_backend's vision test made the no-key tests issue
# live network calls, and a gemini-voice key saved by
# test_existing_voice_key_never_overwritten (unittest runs methods
# alphabetically, so it runs first) made the split migration report
# 'already_set'. Force the plaintext keys/*.txt backend for the whole
# module: it IS isolated by the KEYS_DIR patch, and the split/migration
# logic under test is backend-agnostic. The real Windows vault path
# is covered by test_v160_backend
# .TestApiKeysCredentialStore.test_migration_txt_keys_matches_platform.
_cred_patcher = None


def setUpModule():
    global _cred_patcher
    _cred_patcher = mock.patch.object(
        apikeys_mod, "_cred_available", return_value=False)
    _cred_patcher.start()


def tearDownModule():
    global _cred_patcher
    if _cred_patcher is not None:
        _cred_patcher.stop()
        _cred_patcher = None


class TestSplitMigration(unittest.TestCase):
    """The old single key is copied to BOTH new providers."""

    def _keys_dir_with_old(self, tmp, old_value="AIzaOldSingleKey"):
        keys_dir = tmp / "keys"
        keys_dir.mkdir(parents=True, exist_ok=True)
        (keys_dir / "gemini-key.txt").write_text(old_value + "\n")
        return keys_dir

    def test_old_key_copied_to_both_and_legacy_removed(self):
        tmp = Path(tempfile.mkdtemp())
        keys_dir = self._keys_dir_with_old(tmp)
        with mock.patch.object(settings_mod, "KEYS_DIR", keys_dir):
            apikeys_mod.reset_migration_flag()
            results = apikeys_mod.migrate_gemini_split()
            self.assertEqual(results["gemini-voice"], "copied")
            self.assertEqual(results["gemini-chat"], "copied")
            self.assertEqual(results["legacy"], "removed")
            self.assertEqual(
                apikeys_mod.read_key_value("gemini-voice"),
                "AIzaOldSingleKey")
            self.assertEqual(
                apikeys_mod.read_key_value("gemini-chat"),
                "AIzaOldSingleKey")
            self.assertEqual(apikeys_mod.read_key_value("gemini"), "")
        self.assertFalse((keys_dir / "gemini-key.txt").exists())

    def test_existing_voice_key_never_overwritten(self):
        tmp = Path(tempfile.mkdtemp())
        keys_dir = self._keys_dir_with_old(tmp)
        with mock.patch.object(settings_mod, "KEYS_DIR", keys_dir):
            apikeys_mod.reset_migration_flag()
            # Saving the voice key triggers _maybe_migrate -> the
            # split runs right here: chat gets the old key, the voice
            # key keeps its own value, the legacy entry is removed.
            apikeys_mod.save_key("gemini-voice", "AIzaVoiceOwnKey")
            self.assertEqual(apikeys_mod.read_key_value("gemini-voice"),
                             "AIzaVoiceOwnKey")
            self.assertEqual(apikeys_mod.read_key_value("gemini-chat"),
                             "AIzaOldSingleKey")
        self.assertFalse((keys_dir / "gemini-key.txt").exists())
        # A second explicit split call now finds nothing to do.
        with mock.patch.object(settings_mod, "KEYS_DIR", keys_dir):
            results = apikeys_mod.migrate_gemini_split()
        self.assertEqual(results["legacy"], "absent")

    def test_no_old_key_is_a_noop(self):
        tmp = Path(tempfile.mkdtemp())
        keys_dir = tmp / "keys"
        keys_dir.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(settings_mod, "KEYS_DIR", keys_dir):
            results = apikeys_mod.migrate_gemini_split()
        self.assertEqual(results["legacy"], "absent")
        self.assertEqual(results["gemini-voice"], "absent")

    def test_migration_runs_on_first_read(self):
        tmp = Path(tempfile.mkdtemp())
        keys_dir = self._keys_dir_with_old(tmp)
        with mock.patch.object(settings_mod, "KEYS_DIR", keys_dir):
            apikeys_mod.reset_migration_flag()
            # A plain read triggers _maybe_migrate -> split.
            self.assertEqual(
                apikeys_mod.read_key_value("gemini-chat"),
                "AIzaOldSingleKey")
            self.assertFalse((keys_dir / "gemini-key.txt").exists())


class TestKeysIndependent(unittest.TestCase):
    """Each key is its own entry; changing one never touches the other."""

    def test_save_and_read_independently(self):
        tmp = Path(tempfile.mkdtemp())
        keys_dir = tmp / "keys"
        with mock.patch.object(settings_mod, "KEYS_DIR", keys_dir):
            apikeys_mod.reset_migration_flag()
            self.assertTrue(
                apikeys_mod.save_key("gemini-voice", "AIzaVoice1")["ok"])
            self.assertTrue(
                apikeys_mod.save_key("gemini-chat", "AIzaChat1")["ok"])
            self.assertEqual(apikeys_mod.read_key_value("gemini-voice"),
                             "AIzaVoice1")
            self.assertEqual(apikeys_mod.read_key_value("gemini-chat"),
                             "AIzaChat1")
            # Rotate the chat key alone - the voice key is untouched.
            apikeys_mod.save_key("gemini-chat", "AIzaChat2")
            self.assertEqual(apikeys_mod.read_key_value("gemini-voice"),
                             "AIzaVoice1")
            self.assertEqual(apikeys_mod.read_key_value("gemini-chat"),
                             "AIzaChat2")
            status = apikeys_mod.get_api_keys_status()
            self.assertTrue(status["gemini-voice"])
            self.assertTrue(status["gemini-chat"])
            self.assertFalse(status["groq"])

    def test_old_single_provider_is_rejected(self):
        self.assertFalse(apikeys_mod.save_key("gemini", "x")["ok"])
        self.assertEqual(apikeys_mod.read_key_value("gemini"), "")
        self.assertFalse(apikeys_mod.test_key("gemini")["valid"])

    def test_keys_status_splits(self):
        tmp = Path(tempfile.mkdtemp())
        keys_dir = tmp / "keys"
        with mock.patch.object(settings_mod, "KEYS_DIR", keys_dir):
            apikeys_mod.reset_migration_flag()
            apikeys_mod.save_key("gemini-chat", "AIzaChat1")
            status = settings_mod.keys_status()
        self.assertFalse(status["gemini-voice"])
        self.assertTrue(status["gemini-chat"])
        self.assertNotIn("gemini", status)


class TestVoiceUsesVoiceKeyOnly(unittest.TestCase):
    """Gemini TTS sends the VOICE key - never the chat key."""

    def _set_keys(self, keys_dir, voice=None, chat=None):
        if voice is not None:
            apikeys_mod.save_key("gemini-voice", voice)
        if chat is not None:
            apikeys_mod.save_key("gemini-chat", chat)

    def test_tts_sends_voice_key(self):
        import speak as speak_mod
        tmp = Path(tempfile.mkdtemp())
        keys_dir = tmp / "keys"
        with mock.patch.object(settings_mod, "KEYS_DIR", keys_dir):
            apikeys_mod.reset_migration_flag()
            self._set_keys(keys_dir, voice="AIzaVoiceKey",
                           chat="AIzaChatKey")
            seen = {}

            class _Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

                def json(self):
                    import base64
                    return {"candidates": [{"content": {"parts": [
                        {"inlineData": {"data": base64.b64encode(
                            b"\x00\x00" * 100).decode()}}]}}]}

            def fake_post(url, headers=None, **kw):
                seen["url"] = url
                seen["key"] = (headers or {}).get("x-goog-api-key")
                return _Resp()

            with mock.patch.object(speak_mod.requests, "post",
                                   side_effect=fake_post):
                speak_mod._gemini_tts("أهلاً", "girl_warm")
        self.assertIn("gemini-2.5-flash-preview-tts", seen["url"])
        self.assertEqual(seen["key"], "AIzaVoiceKey")

    def test_tts_fails_without_voice_key_even_with_chat_key(self):
        import speak as speak_mod
        tmp = Path(tempfile.mkdtemp())
        keys_dir = tmp / "keys"
        with mock.patch.object(settings_mod, "KEYS_DIR", keys_dir):
            apikeys_mod.reset_migration_flag()
            self._set_keys(keys_dir, chat="AIzaChatKey")
            with self.assertRaises(RuntimeError) as ctx:
                speak_mod._gemini_tts("أهلاً", "girl_warm")
        self.assertEqual(str(ctx.exception), "no-gemini-key")

    def test_speak_never_calls_gemini_without_voice_key(self):
        import speak as speak_mod
        tmp = Path(tempfile.mkdtemp())
        keys_dir = tmp / "keys"
        with mock.patch.object(settings_mod, "KEYS_DIR", keys_dir):
            apikeys_mod.reset_migration_flag()
            self._set_keys(keys_dir, chat="AIzaChatKey")
            with mock.patch.object(speak_mod.requests, "post",
                                   side_effect=AssertionError(
                                       "Gemini must not be called")):
                result = speak_mod.speak("أهلاً", online=True)
        # Azure has no key, Piper is not installed in the sandbox.
        self.assertEqual(result["engine"], "none")


class TestVisionUsesChatKeyOnly(unittest.TestCase):
    """Image understanding sends the CHAT key - never the voice key."""

    def test_vision_sends_chat_key(self):
        import image_understanding as img_mod
        tmp = Path(tempfile.mkdtemp())
        keys_dir = tmp / "keys"
        with mock.patch.object(settings_mod, "KEYS_DIR", keys_dir):
            apikeys_mod.reset_migration_flag()
            apikeys_mod.save_key("gemini-voice", "AIzaVoiceKey")
            apikeys_mod.save_key("gemini-chat", "AIzaChatKey")
            seen = {}

            class _Resp:
                status_code = 200

                def json(self):
                    return {"candidates": [{"content": {"parts": [
                        {"text": "الصورة فيها قطة"}]}}]}

            def fake_post(url, params=None, **kw):
                seen["url"] = url
                seen["key"] = (params or {}).get("key")
                return _Resp()

            with mock.patch("requests.post", side_effect=fake_post):
                result = img_mod.send_image_to_gemini(
                    b"\x89PNG\r\n\x1a\nxxxx", "اشرح الصورة", confirmed=True)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(seen["key"], "AIzaChatKey")
        self.assertIn("gemini-2.5-flash", seen["url"])

    def test_vision_fails_without_chat_key_even_with_voice_key(self):
        import image_understanding as img_mod
        tmp = Path(tempfile.mkdtemp())
        keys_dir = tmp / "keys"
        with mock.patch.object(settings_mod, "KEYS_DIR", keys_dir):
            apikeys_mod.reset_migration_flag()
            apikeys_mod.save_key("gemini-voice", "AIzaVoiceKey")
            result = img_mod.send_image_to_gemini(
                b"\x89PNG\r\n\x1a\nxxxx", "اشرح", confirmed=True)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason"), "no_key")
        self.assertIn("للمحادثة", result.get("detail", ""))


class TestChatFallbackChain(unittest.TestCase):
    """Groq -> Gemini (chat key) -> Ollama, with no key mixing."""

    def _run_ask(self, groq_exc, gemini_text=None, gemini_status=200,
                 ollama_reply=None, chat_key="AIzaChatKey"):
        """Run brain.ask with a mocked Groq failure. The REAL
        _gemini_chat runs (mocked HTTP only), so the key actually
        read is observable in `reads`."""
        import requests
        reads = []

        def fake_read_key(name, quiet=False):
            reads.append(name)
            if name == "gemini-chat-key.txt":
                return chat_key
            return ""

        def fake_post(url, headers=None, **kw):
            if "generativelanguage.googleapis.com" not in url:
                raise AssertionError(f"unexpected POST to {url}")
            resp = mock.Mock()
            resp.status_code = gemini_status
            resp.json.return_value = {"candidates": [{"content": {"parts": [
                {"text": gemini_text or ""}]}}]}
            return resp

        with mock.patch.object(brain_mod, "is_online",
                               return_value=True), \
                mock.patch.object(brain_mod, "_groq",
                                  side_effect=groq_exc), \
                mock.patch.object(brain_mod, "read_key",
                                  side_effect=fake_read_key), \
                mock.patch.object(brain_mod.requests, "post",
                                  side_effect=fake_post), \
                mock.patch.object(brain_mod, "_ollama") as oll:
            if ollama_reply is not None:
                oll.return_value = ollama_reply
            else:
                oll.side_effect = requests.ConnectionError("refused")
            with mock.patch.object(brain_mod.shutil, "which",
                                   return_value=None):
                res = brain_mod.ask("إزيك؟", "LONA", "Mohamed", "ar")
        return res, reads

    def test_groq_429_falls_back_to_gemini(self):
        res, reads = self._run_ask(
            brain_mod.GroqRateLimit(limit="tpm", retry_after=4.1),
            gemini_text="أهلاً، أنا Gemini")
        self.assertEqual(res["engine"], "gemini")
        self.assertIn("Gemini", res["reply"])
        # The chat path read the CHAT key file, never the voice one.
        self.assertIn("gemini-chat-key.txt", reads)
        self.assertNotIn("gemini-voice-key.txt", reads)

    def test_no_chat_key_chain_is_groq_to_ollama(self):
        res, reads = self._run_ask(
            brain_mod.GroqRateLimit(limit="tpm"),
            ollama_reply="أنا المخ المحلي، أهلاً", chat_key="")
        self.assertEqual(res["engine"], "ollama")
        self.assertIn("المخ المحلي", res["reply"])

    def test_all_down_names_every_engine(self):
        res, _ = self._run_ask(
            brain_mod.GroqRateLimit(limit="tpm", retry_after=4.1),
            chat_key="")
        self.assertEqual(res["engine"], "")
        # Groq: the real per-minute limit, never "daily".
        self.assertIn("التوكنز في الدقيقة", res["reply"])
        self.assertNotIn("يومي", res["reply"])
        # Gemini: the reason it did not answer.
        self.assertIn("مفتاح Gemini للمحادثة مش متسجل", res["reply"])
        # Ollama: not installed (shutil.which mocked to None).
        self.assertIn("مش متثبت", res["reply"])

    def test_gemini_429_named_in_all_down_message(self):
        res, _ = self._run_ask(
            brain_mod.GroqRateLimit(limit="tpd"),
            gemini_status=429, chat_key="AIzaChatKey")
        self.assertEqual(res["engine"], "")
        self.assertIn("02:00 بتوقيت القاهرة", res["reply"])
        self.assertIn("Gemini وصل للحد", res["reply"])


class TestGeminiChatCall(unittest.TestCase):
    """The raw Gemini chat call: payload shape + key actually sent."""

    def _resp_ok(self, text="رد Gemini"):
        resp = mock.Mock()
        resp.status_code = 200
        resp.json.return_value = {"candidates": [{"content": {"parts": [
            {"text": text}]}}]}
        return resp

    def test_sends_chat_key_in_header(self):
        tmp = Path(tempfile.mkdtemp())
        keys_dir = tmp / "keys"
        with mock.patch.object(settings_mod, "KEYS_DIR", keys_dir):
            apikeys_mod.reset_migration_flag()
            apikeys_mod.save_key("gemini-voice", "AIzaVoiceKey")
            apikeys_mod.save_key("gemini-chat", "AIzaChatKey")
            seen = {}
            resp = self._resp_ok()

            def fake_post(url, headers=None, **kw):
                seen["url"] = url
                seen["key"] = (headers or {}).get("x-goog-api-key")
                seen["payload"] = kw.get("json") or {}
                return resp

            with mock.patch.object(brain_mod.requests, "post",
                                   side_effect=fake_post):
                out = brain_mod._gemini_chat(
                    [{"role": "system", "content": "أنت لونا"},
                     {"role": "user", "content": "إزيك؟"}])
        self.assertEqual(out, "رد Gemini")
        self.assertEqual(seen["key"], "AIzaChatKey")
        self.assertIn("gemini-2.5-flash", seen["url"])
        payload = seen["payload"]
        self.assertEqual(payload["systemInstruction"]["parts"][0]["text"],
                         "أنت لونا")
        self.assertEqual(payload["contents"][0]["role"], "user")

    def test_messages_for_gemini_drops_tools_and_merges(self):
        system, contents = brain_mod._messages_for_gemini([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello",
             "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "content": "tool output",
             "tool_call_id": "c1"},
            {"role": "user", "content": "again"},
            {"role": "user", "content": "and again"},
        ])
        self.assertEqual(system, "sys")
        roles = [c["role"] for c in contents]
        self.assertEqual(roles, ["user", "model", "user"])
        # The two consecutive user turns merged into one content.
        self.assertIn("again", contents[2]["parts"][0]["text"])
        self.assertIn("and again", contents[2]["parts"][0]["text"])

    def test_gemini_429_and_rejected_kinds(self):
        for status, kind in ((429, "gemini-429"), (401, "gemini-key-rejected"),
                             (403, "gemini-key-rejected")):
            resp = mock.Mock()
            resp.status_code = status
            resp.json.return_value = {}
            with mock.patch.object(
                    brain_mod, "read_key",
                    return_value="AIzaChatKey"), \
                    mock.patch.object(brain_mod.requests, "post",
                                      return_value=resp):
                with self.assertRaises(RuntimeError) as ctx:
                    brain_mod._gemini_chat(
                        [{"role": "user", "content": "hi"}])
            self.assertEqual(str(ctx.exception), kind)


class TestApiKeysPage(unittest.TestCase):
    """Settings shows THREE clearly separate key fields (reskin).

    v1.6.4 reskin: the API keys live in the Settings "api" section,
    rendered by the Bsec.api builder in index.html — Gemini voice,
    Gemini chat, AND Groq, each with save + test + eye.
    """

    def test_two_fields_with_exact_labels(self):
        html = (UI_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("[['gemini-voice', 'Gemini صوت'], "
                      "['gemini-chat', 'Gemini شات'], ['groq', 'Groq']]",
                      html)
        self.assertIn("Gemini صوت", html)
        self.assertIn("Gemini شات", html)
        self.assertIn("Groq", html)
        self.assertIn("data-ksave='", html)
        self.assertIn("data-ktest='", html)
        self.assertNotIn('data-key="gemini"', html)

    def test_js_wires_both_providers(self):
        html = (UI_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("[['gemini-voice', 'Gemini صوت'], "
                      "['gemini-chat', 'Gemini شات'], ['groq', 'Groq']]",
                      html)
        # The save/test/eye handlers take the provider from the row
        # (data-ksave / data-ktest / data-key), never a hardcoded one.
        self.assertIn("data-ksave", html)
        self.assertIn("data-ktest", html)
        self.assertIn("get_api_key_value", html)
        self.assertNotIn('["gemini", "groq"]', html)


if __name__ == "__main__":
    unittest.main()
