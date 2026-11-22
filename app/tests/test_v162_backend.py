"""LONA v1.6.2 backend tests (headless, Linux: no Windows, no keys,
no network except mocked):

    python3 -m pytest app/tests/test_v162_backend.py -q -p no:cacheprovider

What is proven here (one class per work item):

  * A1 voice_chat returns pending at once; the turn lands in
    chat_result() with the heard text; ONE WhisperModel is cached
    in hear.py (never rebuilt per call); Groq Whisper has a 30 s
    timeout.
  * A2 check_updates returns {"pending": true, "id": ...} at once; the poll
    method returns the update dict when the thread finishes.
  * A3 analyze_image / understand_image return pending+id at once;
    the poll methods return the Gemini/tool result.
  * A4 test_api_key returns pending+id at once; the poll method
    returns ok/valid/detail.
  * A5 get_status() never samples live (no GPU/process/temp probe
    on the bridge); the sampler thread delivers slow fields;
    get_self_view() reads the monitor once.
  * A8 a stored verified/size/mtime fingerprint is trusted without
    a SHA recompute; a changed fingerprint recomputes on a
    background thread; check_update() has no "downloaded" key.
  * B10 one 180 s deadline for the chat cycle, max 5 tool calls
    per round.
  * B11 Deep Think 150 s, normal replies 60 s, Ollama 60 s.
  * B16 logger reads only the tail of log files; selfview uses a
    caller-supplied status instead of reading the monitor.
  * B17 is_online() reads a cache (never a socket on the caller);
    the watcher thread probes in the background.
  * B18 settings/WhatsApp saves return pending+id at once; the
    poll methods complete.
  * B19 browser-cache cleaning is capped (500 files / 10 s) and
    reports files_remaining.
  * B20 DDGS search has a real timeout (thread + join).
  * B22 TempWatch computes top processes only when hot; the temp
    probe is capped at 15 s.
  * B23 TTS timeouts are 30/30/60.
  * B24 temperature comes from ONE PowerShell spawn; the probe is
    capped at 15 s.
  * B25 edit_file has the 5 MB cap; list_dir is lazy (first 100).
  * B26 _apply_zip reports progress per file (0-50% extract,
    50-100% copy).
  * Extension re-wire: module-level send_command / is_connected
    exist; send_command translates browser_control's action shape;
    is_connected is False with no client.
  * F4 island is 220x60 (rc.2), island window transparent,
    island_radius matches the pill; main window has a NATIVE frame
    (rc.2 - was frameless in item 3; the frameless window looked
    like a browser, maximize was broken and Win+Arrow snap did
    not work) with window_minimize / window_toggle_maximize /
    window_close / window_move_to.
  * API-keys page: get_api_keys_status is cached (30 s TTL);
    _maybe_migrate runs once per process; get_api_key_value
    returns the value only on the explicit show tap, and
    get_api_keys_status stays presence-only.
  * Model choice: get/set_model_choice contract; strict routing
    (explicit model fails with its name + reason, no silent
    fallback); auto keeps the old chain; Deep Think reasoning
    rides on Groq only.
  * Watermark: _save_generated_image crops the bottom strip
    carrying the Pollinations logo (verified live 2026-11-22);
    the generate URL keeps nologo=true.
  * (4a) request_deep_file_access is callable from JS
    (pending+id, poll get_deep_file_access_result).
  * (4b) get_device_overview exposes temp_available=False +
    temp_hint (detail + LibreHardwareMonitor URL) when no
    sensor answers, instead of an empty value.

main.py cannot be imported as-is here (no pywebview on Linux),
so a minimal webview stub is installed in sys.modules first --
the same trick the app itself would need on a headless box.
"""

import base64
import hashlib
import io
import json
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

# -- minimal pywebview stub (main.py imports it at module level) -----
import types as _types


class _FakeWindow:
    """Records the window-control calls the frameless header makes."""

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

import apikeys as apikeys_mod  # noqa: E402
import brain  # noqa: E402
import hear  # noqa: E402
import logger as logger_mod  # noqa: E402
import main as main_mod  # noqa: E402
import monitor as monitor_mod  # noqa: E402
import native_host  # noqa: E402
import selfview as selfview_mod  # noqa: E402
import settings_store  # noqa: E402
import speak as speak_mod  # noqa: E402
import tempwatch as tempwatch_mod  # noqa: E402
import tools  # noqa: E402
import updater  # noqa: E402
import winshape  # noqa: E402
from monitor import SystemMonitor  # noqa: E402

MAIN_SRC = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
MONITOR_SRC = (ROOT / "app" / "monitor.py").read_text(encoding="utf-8")
TOOLS_SRC = (ROOT / "app" / "tools.py").read_text(encoding="utf-8")


def _poll_until(fn, timeout=5.0, interval=0.05):
    """Call fn() until it returns a truthy value (or timeout)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = fn()
        if res:
            return res
        time.sleep(interval)
    return fn()


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


def _tiny_png_bytes(width=16, height=16, color=(255, 0, 0)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "PNG")
    return buf.getvalue()


def _data_url_png(width=16, height=16):
    raw = _tiny_png_bytes(width, height)
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


# ---------------------------------------------------------------------------
# A1 -- voice_chat on a background thread, cached Whisper model
# ---------------------------------------------------------------------------

class TestVoiceChatAsync(_ApiCase):
    def test_returns_pending_immediately_while_listen_blocks(self):
        started = threading.Event()

        def slow_listen(language="ar"):
            started.set()
            time.sleep(2.0)
            return {"text": "hello", "engine": "groq"}

        with mock.patch.object(hear, "listen", side_effect=slow_listen):
            with mock.patch.object(main_mod.Api, "chat", return_value={
                    "reply": "hi there", "engine": "groq"}):
                t0 = time.monotonic()
                res = self.api.voice_chat()
                dt = time.monotonic() - t0
        self.assertTrue(res.get("pending"))
        self.assertTrue(res.get("chat_id"))
        self.assertLess(dt, 0.5, "voice_chat blocked the bridge")
        self.assertTrue(started.wait(timeout=2))

    def test_result_readable_via_chat_result_with_heard(self):
        with mock.patch.object(
                hear, "listen",
                return_value={"text": "hello", "engine": "groq"}):
            with mock.patch.object(main_mod.Api, "chat", return_value={
                    "reply": "hi there", "engine": "groq"}):
                res = self.api.voice_chat()
                chat_id = res["chat_id"]
                done = _poll_until(
                    lambda: self.api.chat_result(chat_id).get("done"),
                    timeout=5.0)
        self.assertTrue(done, "voice turn never completed")
        final = self.api.chat_result(chat_id)
        self.assertEqual(final.get("reply"), "hi there")
        self.assertEqual(final.get("heard"), "hello")

    def test_empty_heard_reports_honestly(self):
        with mock.patch.object(
                hear, "listen",
                return_value={"text": "", "engine": "",
                              "error": "مقدرتش أسمعك."}):
            res = self.api.voice_chat()
            done = _poll_until(
                lambda: self.api.chat_result(res["chat_id"]).get("done"),
                timeout=5.0)
        self.assertTrue(done)
        final = self.api.chat_result(res["chat_id"])
        self.assertEqual(final.get("reply"), "مقدرتش أسمعك.")
        self.assertEqual(final.get("heard"), "")


class TestHearModelCache(unittest.TestCase):
    def test_whisper_model_built_once(self):
        built = []

        class FakeModel:
            def __init__(self, *a, **k):
                built.append((a, k))

        fake_mod = _types.ModuleType("faster_whisper")
        fake_mod.WhisperModel = FakeModel
        orig = hear._LOCAL_MODEL
        hear._LOCAL_MODEL = None
        try:
            with mock.patch.dict(sys.modules,
                                 {"faster_whisper": fake_mod}):
                first = hear._get_local_model()
                second = hear._get_local_model()
            self.assertIs(first, second)
            self.assertEqual(len(built), 1, "model rebuilt per call")
        finally:
            hear._LOCAL_MODEL = orig

    def test_groq_whisper_timeout_is_30s(self):
        self.assertEqual(hear._GROQ_TIMEOUT_SEC, 30.0)


# ---------------------------------------------------------------------------
# A2 -- check_updates on a background thread + poll
# ---------------------------------------------------------------------------

class TestCheckUpdatesAsync(_ApiCase):
    def test_returns_checking_immediately_then_poll_result(self):
        started = threading.Event()

        def slow_check():
            started.set()
            time.sleep(1.0)
            return {"status": "update_available", "version": "9.9.9",
                    "current": "1.6.2"}

        with mock.patch.object(updater, "check_update",
                               side_effect=slow_check):
            t0 = time.monotonic()
            res = self.api.check_updates()
            dt = time.monotonic() - t0
        self.assertTrue(res.get("pending"))
        self.assertTrue(res.get("id"))
        self.assertLess(dt, 0.5, "check_updates blocked the bridge")
        self.assertTrue(started.wait(timeout=2))
        final = _poll_until(
            lambda: (not self.api.get_check_updates_result()["checking"]
                     and self.api.get_check_updates_result()),
            timeout=5.0)
        self.assertFalse(final["checking"])
        self.assertEqual(final["update"]["version"], "9.9.9")
        self.assertIsNone(final["error"])

    def test_second_call_while_checking_returns_same_pending_id(self):
        with mock.patch.object(
                updater, "check_update",
                side_effect=lambda: (time.sleep(1.0), {"status": "ok"})[1]):
            first = self.api.check_updates()
            res = self.api.check_updates()
        self.assertTrue(res.get("pending"))
        self.assertTrue(first.get("id"))
        self.assertEqual(res.get("id"), first.get("id"))


# ---------------------------------------------------------------------------
# A3 -- analyze_image / understand_image on background threads
# ---------------------------------------------------------------------------

class TestImageUnderstandAsync(_ApiCase):
    def test_analyze_image_pending_then_poll(self):
        fake_img = _types.ModuleType("image_understanding")
        fake_img.send_image_to_gemini = mock.Mock(
            return_value={"ok": True, "text": "a red cat"})
        with mock.patch.dict(sys.modules,
                             {"image_understanding": fake_img}):
            t0 = time.monotonic()
            res = self.api.analyze_image(_data_url_png())
            dt = time.monotonic() - t0
            self.assertTrue(res.get("pending"))
            self.assertTrue(res.get("id"))
            self.assertLess(dt, 0.5, "analyze_image blocked the bridge")
            final = _poll_until(
                lambda: (self.api.get_analyze_image_result(res["id"])
                         .get("done")
                         and self.api.get_analyze_image_result(res["id"])),
                timeout=5.0)
        self.assertTrue(final["done"])
        self.assertTrue(final["ok"])
        self.assertEqual(final["reply"], "a red cat")

    def test_analyze_image_invalid_data_url(self):
        res = self.api.analyze_image("not-a-data-url")
        self.assertTrue(res.get("pending"))
        final = self.api.get_analyze_image_result(res["id"])
        self.assertTrue(final["done"])
        self.assertFalse(final["ok"])

    def test_understand_image_pending_then_poll(self):
        with mock.patch.object(tools, "execute_tool",
                               return_value="a red car") as exec_mock:
            res = self.api.understand_image("/tmp/x.png", "what is this?",
                                            confirmed=True)
            self.assertTrue(res.get("pending"))
            self.assertTrue(res.get("id"))
            final = _poll_until(
                lambda: (self.api.get_understand_image_result(res["id"])
                         .get("done")
                         and self.api.get_understand_image_result(res["id"])),
                timeout=5.0)
        self.assertTrue(final["done"])
        self.assertTrue(final["ok"])
        self.assertEqual(final["text"], "a red car")
        exec_mock.assert_called_once()


# ---------------------------------------------------------------------------
# A4 -- test_api_key on a background thread + poll
# ---------------------------------------------------------------------------

class TestApiKeyAsync(_ApiCase):
    def test_pending_then_poll(self):
        fake_keys = _types.SimpleNamespace(
            test_key=mock.Mock(return_value={
                "ok": True, "valid": True, "detail": "ok"}))
        with mock.patch.object(main_mod, "apikeys_mod", fake_keys):
            t0 = time.monotonic()
            res = self.api.test_api_key("groq")
            dt = time.monotonic() - t0
            self.assertEqual(res.get("pending"), True)
            self.assertTrue(res.get("id"))
            self.assertLess(dt, 0.5, "test_api_key blocked the bridge")
            final = _poll_until(
                lambda: (not self.api.get_test_api_key_result(
                    res["id"]).get("pending")
                    and self.api.get_test_api_key_result(res["id"])),
                timeout=5.0)
        self.assertTrue(final["ok"])
        self.assertTrue(final["valid"])
        self.assertEqual(final["detail"], "ok")
        fake_keys.test_key.assert_called_once_with("groq")

    def test_unknown_poll_id(self):
        res = self.api.get_test_api_key_result("nope1234")
        self.assertFalse(res["pending"])
        self.assertFalse(res["ok"])


# ---------------------------------------------------------------------------
# A5 -- monitor cold-cache: no live fallbacks on the bridge
# ---------------------------------------------------------------------------

class TestMonitorNoLiveFallbacks(unittest.TestCase):
    def test_get_status_never_samples_live(self):
        # Any live probe must raise if called: get_status() must
        # serve the cache / honest "not available yet" instead.
        with mock.patch.object(monitor_mod, "read_gpu_usage",
                               side_effect=AssertionError("gpu probe")):
            with mock.patch.object(monitor_mod, "_read_top_processes",
                                   side_effect=AssertionError("proc scan")):
                with mock.patch.object(monitor_mod, "read_cpu_temp",
                                       side_effect=AssertionError("temp")):
                    mon = SystemMonitor({})
                    status = mon.get_status()
        self.assertEqual(status["gpu"],
                         {"available": False, "percent": None, "detail": ""})
        self.assertEqual(status["top_processes"], [])
        self.assertEqual(status["storage"], [])
        self.assertIsNone(status["temp_c"])
        self.assertFalse(status["temp_available"])

    def test_fast_sample_has_no_slow_fields(self):
        mon = SystemMonitor({})
        fast = mon._sample_fast()
        # v1.6.2 fix: the top_processes KEY is absent from the fast
        # sample on purpose -- a fast partial carrying [] would wipe
        # the slow-delivered list out of the merged cache on every
        # 5 s sample (dict.update clobbers it).
        self.assertNotIn("top_processes", fast)
        self.assertNotIn("gpu", fast)
        self.assertNotIn("temp_c", fast)

    def test_sampler_thread_delivers_slow_fields(self):
        mon = SystemMonitor({})
        try:
            self.assertTrue(mon.start_sampler())
            delivered = _poll_until(
                lambda: mon.get_status()["top_processes"],
                timeout=10.0)
            self.assertTrue(delivered,
                            "sampler never delivered top_processes")
        finally:
            mon.stop_sampler()
            mon.stop()


class TestSelfViewSingleRead(_ApiCase):
    def test_get_self_view_reads_monitor_once(self):
        mon = mock.Mock()
        overview = {"ram": {"percent": 10}, "cpu": {"percent": 5},
                    "battery": {"percent": 80}, "temp_c": None,
                    "temp_available": False, "top_processes": [],
                    "mood": "صاحي", "mood_reason": ""}
        mon.get_device_overview = mock.Mock(return_value=overview)
        self.app._monitor = mon
        with mock.patch.object(
                selfview_mod, "get_self_view",
                return_value=[{"label": "x", "value": "y"}]) as sv:
            res = self.api.get_self_view()
        self.assertTrue(res.get("ok"))
        mon.get_device_overview.assert_called_once()
        # The overview is passed down as status -- selfview must
        # NOT read the monitor a second time. v1.6.4 reskin: the
        # overview now comes through bridge_v2's v2 translation,
        # which keeps every v1 key and adds the v2 temp alias.
        expected = dict(overview)
        expected["temp"] = {}
        self.assertEqual(sv.call_args.kwargs.get("status"), expected)


# ---------------------------------------------------------------------------
# A8 -- downloaded-update fingerprint: size/mtime, async SHA
# ---------------------------------------------------------------------------

class TestUpdaterFingerprint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.file = self.tmp / "memo-ai-v9.9.9.zip"
        self.file.write_bytes(b"fake zip content " * 100)
        st = self.file.stat()
        self.sha = hashlib.sha256(self.file.read_bytes()).hexdigest()
        self.state_path = self.tmp / "update_downloaded.json"
        self.state = {"version": "9.9.9", "kind": "zip",
                      "path": str(self.file), "size": st.st_size,
                      "mtime": st.st_mtime, "sha256": self.sha,
                      "verified": True, "features": ["x"]}
        self.state_path.write_text(json.dumps(self.state),
                                   encoding="utf-8")
        self._patch = mock.patch.object(
            updater, "DOWNLOADED_STATE_PATH", self.state_path)
        self._patch.start()
        updater._SHA_CACHE.update({"path": "", "size": None,
                                   "mtime": None, "sha": None})
        updater._SHA_RECOMPUTE_RUNNING.clear()

    def tearDown(self):
        self._patch.stop()
        updater._SHA_CACHE.update({"path": "", "size": None,
                                   "mtime": None, "sha": None})
        updater._SHA_RECOMPUTE_RUNNING.clear()

    def test_stored_fingerprint_trusted_without_sha(self):
        with mock.patch.object(
                updater, "_sha256_file",
                side_effect=AssertionError("SHA recomputed")):
            res = updater.get_downloaded_update()
        self.assertTrue(res["downloaded"])
        self.assertTrue(res["valid"])
        self.assertEqual(res["version"], "9.9.9")

    def test_changed_mtime_recomputes_async_then_valid(self):
        # Same bytes, new mtime: fingerprint changed, so the first
        # read reports not-valid and a background recompute starts;
        # the next poll sees the matching SHA and valid=True.
        time.sleep(0.01)
        self.file.touch()
        res = updater.get_downloaded_update()
        self.assertTrue(res["downloaded"])
        self.assertFalse(res["valid"])
        final = _poll_until(
            lambda: updater.get_downloaded_update()["valid"],
            timeout=5.0)
        self.assertTrue(final, "async SHA recompute never landed")

    def test_changed_bytes_stay_invalid(self):
        # Tamper with DIFFERENT-SIZE bytes (and a fresh mtime) so
        # the fingerprint really changed: the first read reports
        # not-valid and the background recompute never flips it
        # back (the SHA no longer matches the recorded one).
        time.sleep(0.02)
        self.file.write_bytes(b"tampered!" * 100)
        res = updater.get_downloaded_update()
        self.assertFalse(res["valid"])
        time.sleep(1.0)
        self.assertFalse(updater.get_downloaded_update()["valid"])

    def test_check_update_has_no_downloaded_key(self):
        with mock.patch.object(
                updater, "_fetch_manifest",
                return_value={"version": "9.9.9", "notes": "",
                              "url": "", "sha256": "",
                              "setup_url": "", "setup_sha256": ""}):
            res = updater.check_update()
        self.assertNotIn("downloaded", res)
        self.assertEqual(res["status"], "update_available")
        self.assertEqual(res["version"], "9.9.9")


# ---------------------------------------------------------------------------
# B10 -- one 180 s deadline, max 5 tool calls per round
# ---------------------------------------------------------------------------

class TestBrainDeadlineAndCap(unittest.TestCase):
    def setUp(self):
        brain.clear_history()
        brain.set_memory_store(None)

    def tearDown(self):
        brain.clear_history()
        brain.set_memory_store(None)

    def _run_tools(self, groq_message_fn):
        with mock.patch.object(brain, "is_online", return_value=True):
            with mock.patch.object(brain, "_groq_message",
                                   side_effect=groq_message_fn):
                with mock.patch.object(tools, "execute_tool",
                                       return_value="ok") as exec_mock:
                    res = brain.ask_with_tools(
                        "hello", "LONA", "Mohamed", "en",
                        history=[], memory_enabled=False)
        return res, exec_mock

    def test_max_five_tool_calls_per_round(self):
        calls = []

        def seven_calls(messages, use_tools=False, **kwargs):
            calls.append(kwargs.get("timeout"))
            if len(calls) == 1:
                return {"content": "",
                        "tool_calls": [
                            {"id": f"c{i}", "function": {
                                "name": "system_status",
                                "arguments": "{}"}}
                            for i in range(7)]}
            return {"content": "done", "tool_calls": []}

        res, exec_mock = self._run_tools(seven_calls)
        self.assertEqual(res["reply"], "done")
        self.assertEqual(res["engine"], "groq")
        # 7 asked, 5 executed -- the cap, not the model's count.
        self.assertEqual(exec_mock.call_count, 5)
        self.assertEqual(res["tools_used"], 5)

    def test_deadline_constant_is_180(self):
        self.assertEqual(brain._CHAT_DEADLINE_SEC, 180)

    def test_groq_timeout_passed_is_capped_by_deadline(self):
        seen = []

        def capture(messages, use_tools=False, **kwargs):
            seen.append(kwargs.get("timeout"))
            return {"content": "done", "tool_calls": []}

        res, _ = self._run_tools(capture)
        self.assertEqual(res["reply"], "done")
        self.assertTrue(seen)
        self.assertLessEqual(seen[0], 60)
        self.assertGreaterEqual(seen[0], 5)


# ---------------------------------------------------------------------------
# B11 -- timeouts: Deep Think 150, normal 60, Ollama 60
# ---------------------------------------------------------------------------

class TestBrainTimeouts(unittest.TestCase):
    def setUp(self):
        brain.clear_history()
        brain.set_memory_store(None)

    def tearDown(self):
        brain.clear_history()
        brain.set_memory_store(None)

    def _ask(self, deep_think):
        seen = {}

        def fake_groq(messages, **kwargs):
            seen.update(kwargs)
            return "hello back"

        with mock.patch.object(brain, "is_online", return_value=True):
            with mock.patch.object(brain, "_groq",
                                   side_effect=fake_groq):
                res = brain.ask("hello there", "LONA", "Mohamed", "en",
                                history=[], memory_enabled=False,
                                deep_think=deep_think)
        return res, seen

    def test_deep_think_timeout_150(self):
        res, seen = self._ask(deep_think=True)
        self.assertEqual(res["engine"], "groq")
        self.assertEqual(seen.get("timeout"), 150)
        self.assertTrue(seen.get("deep_think"))

    def test_normal_timeout_60(self):
        res, seen = self._ask(deep_think=False)
        self.assertEqual(res["engine"], "groq")
        self.assertEqual(seen.get("timeout"), 60)
        self.assertNotIn("deep_think", seen)

    def test_ollama_timeout_60(self):
        self.assertEqual(brain._OLLAMA_TIMEOUT_SEC, 60)
        seen = {}

        def fake_post(url, **kwargs):
            seen.update(kwargs)
            resp = mock.Mock()
            resp.json.return_value = {"message": {"content": "local hi"}}
            resp.raise_for_status.return_value = None
            return resp

        with mock.patch.object(brain.requests, "post",
                               side_effect=fake_post):
            out = brain._ollama([{"role": "user", "content": "hi"}])
        self.assertEqual(out, "local hi")
        self.assertEqual(seen.get("timeout"), 60)


# ---------------------------------------------------------------------------
# B16 -- logger tail-read; selfview status param
# ---------------------------------------------------------------------------

class TestLoggerTailRead(unittest.TestCase):
    def test_tail_lines_returns_last_n_only(self):
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "big.log"
        with open(path, "w", encoding="utf-8") as f:
            for i in range(5000):
                f.write(f"line {i}\n")
        out = logger_mod._tail_lines(path, 5)
        self.assertEqual(out, [f"line {i}" for i in range(4995, 5000)])

    def test_read_recent_reads_tails_across_files(self):
        tmp = Path(tempfile.mkdtemp())
        for day in ("2026-11-21", "2026-11-22"):
            path = tmp / f"memo-ai-{day}.log"
            with open(path, "w", encoding="utf-8") as f:
                for i in range(300):
                    f.write(json.dumps(
                        {"time": f"{day}T00:{i % 60:02d}:00",
                         "level": "info", "category": "app",
                         "message": f"{day} msg {i}"}) + "\n")
        with mock.patch.object(logger_mod, "_log_dir", return_value=tmp):
            entries = logger_mod.read_recent(10)
        self.assertEqual(len(entries), 10)
        self.assertEqual(entries[-1]["message"], "2026-11-22 msg 299")
        self.assertEqual(entries[0]["message"], "2026-11-22 msg 290")

    def test_selfview_uses_supplied_status(self):
        status = {"ram": {"percent": 42}, "cpu": {"percent": 7},
                  "battery": {"percent": 90}, "temp_c": None,
                  "temp_available": False, "top_processes": [],
                  "storage": [], "gpu": {"available": False},
                  "battery_drain": {}}
        monitor = mock.Mock()
        monitor.get_status = mock.Mock(
            side_effect=AssertionError("must not read monitor"))
        rows = selfview_mod.get_self_view(mood="صاحي", monitor=monitor,
                                          settings={}, status=status)
        self.assertIsInstance(rows, list)
        self.assertTrue(rows)
        for row in rows:
            self.assertIn("label", row)
            self.assertIn("value", row)


# ---------------------------------------------------------------------------
# B17 -- connectivity cache
# ---------------------------------------------------------------------------

class TestConnectivityCache(unittest.TestCase):
    def test_is_online_reads_cache_never_socket(self):
        brain.start_online_watcher()
        with brain._ONLINE_LOCK:
            brain._ONLINE_CACHE.update({"at": time.time(),
                                        "value": True})
        with mock.patch.object(
                brain.socket, "create_connection",
                side_effect=AssertionError("socket on caller thread")):
            self.assertTrue(brain.is_online())
        with brain._ONLINE_LOCK:
            brain._ONLINE_CACHE.update({"at": time.time(),
                                        "value": False})
        with mock.patch.object(
                brain.socket, "create_connection",
                side_effect=AssertionError("socket on caller thread")):
            self.assertFalse(brain.is_online())

    def test_watcher_is_daemon_and_idempotent(self):
        first = brain.start_online_watcher()
        second = brain.start_online_watcher()
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertIsNotNone(brain._ONLINE_WATCHER)
        self.assertTrue(brain._ONLINE_WATCHER.daemon)
        self.assertTrue(brain._ONLINE_WATCHER.is_alive())


# ---------------------------------------------------------------------------
# B18 -- settings / WhatsApp saves on background threads + poll
# ---------------------------------------------------------------------------

class TestSettingsSavesAsync(_ApiCase):
    def test_save_monitor_settings_pending_then_poll(self):
        self.app._start_monitor = mock.Mock()
        with mock.patch.object(main_mod, "save_settings",
                               return_value=True):
            res = self.api.save_monitor_settings(
                {"monitor_ram_free_mb": 250})
            self.assertTrue(res.get("pending"))
            self.assertTrue(res.get("id"))
            final = _poll_until(
                lambda: (self.api.get_settings_save_result(res["id"])
                         .get("done")
                         and self.api.get_settings_save_result(res["id"])),
                timeout=5.0)
        self.assertTrue(final["done"])
        self.assertTrue(final["ok"])
        self.assertEqual(self.app.settings["monitor_ram_free_mb"], 250)
        self.app._start_monitor.assert_called_once()

    def test_save_notification_settings_pending_then_poll(self):
        self.app._start_notifications = mock.Mock()
        with mock.patch.object(main_mod, "save_settings",
                               return_value=True):
            res = self.api.save_notification_settings(
                {"enabled": False, "apps": ["WhatsApp"],
                 "keywords": ["urgent"]})
            self.assertTrue(res.get("pending"))
            final = _poll_until(
                lambda: (self.api.get_settings_save_result(res["id"])
                         .get("done")
                         and self.api.get_settings_save_result(res["id"])),
                timeout=5.0)
        self.assertTrue(final["done"])
        self.assertTrue(final["ok"])
        self.assertFalse(self.app.settings["notifications_enabled"])
        self.assertEqual(
            self.app.settings["notifications_important_apps"],
            ["WhatsApp"])
        self.app._start_notifications.assert_called_once()


class TestWhatsAppAsync(_ApiCase):
    def _fake_whatsapp(self):
        svc = mock.Mock()
        svc.start.return_value = True
        svc.logout = mock.Mock()
        fake = _types.SimpleNamespace(
            normalize_number=lambda n: n.strip(),
            ensure_started=mock.Mock(return_value=svc),
            get_service=mock.Mock(return_value=svc),
            get_state=mock.Mock(return_value={"state": "connected"}),
            stop_service=mock.Mock())
        return fake, svc

    def test_whatsapp_save_pending_then_poll(self):
        fake, svc = self._fake_whatsapp()
        with mock.patch.object(main_mod, "whatsapp_mod", fake):
            res = self.api.whatsapp_save(
                {"enabled": True, "owner_phone": "+201234567890"})
            self.assertTrue(res.get("pending"))
            self.assertTrue(res.get("id"))
            final = _poll_until(
                lambda: (self.api.get_whatsapp_save_result(res["id"])
                         .get("done")
                         and self.api.get_whatsapp_save_result(res["id"])),
                timeout=5.0)
        self.assertTrue(final["done"])
        self.assertTrue(final["ok"])
        self.assertEqual(final["state"], {"state": "connected"})
        svc.start.assert_called_once()

    def test_whatsapp_save_disable_stops_service(self):
        fake, svc = self._fake_whatsapp()
        with mock.patch.object(main_mod, "whatsapp_mod", fake):
            res = self.api.whatsapp_save({"enabled": False})
            final = _poll_until(
                lambda: (self.api.get_whatsapp_save_result(res["id"])
                         .get("done")
                         and self.api.get_whatsapp_save_result(res["id"])),
                timeout=5.0)
        self.assertTrue(final["done"])
        fake.stop_service.assert_called_once()

    def test_whatsapp_disconnect_pending_then_poll(self):
        fake, svc = self._fake_whatsapp()
        with mock.patch.object(main_mod, "whatsapp_mod", fake):
            res = self.api.whatsapp_disconnect()
            self.assertTrue(res.get("pending"))
            final = _poll_until(
                lambda: (self.api.get_whatsapp_action_result(res["id"])
                         .get("done")
                         and self.api.get_whatsapp_action_result(res["id"])),
                timeout=5.0)
        self.assertTrue(final["done"])
        self.assertTrue(final["ok"])
        svc.logout.assert_called_once()

    def test_whatsapp_relink_pending_then_poll(self):
        fake, svc = self._fake_whatsapp()
        self.app.settings["whatsapp_enabled"] = True
        with mock.patch.object(main_mod, "whatsapp_mod", fake):
            res = self.api.whatsapp_relink()
            self.assertTrue(res.get("pending"))
            final = _poll_until(
                lambda: (self.api.get_whatsapp_action_result(res["id"])
                         .get("done")
                         and self.api.get_whatsapp_action_result(res["id"])),
                timeout=5.0)
        self.assertTrue(final["done"])
        self.assertTrue(final["ok"])
        svc.logout.assert_called_once()
        fake.ensure_started.assert_called_once()


# ---------------------------------------------------------------------------
# B19 -- browser-cache cap (500 files / 10 s) + files_remaining
# ---------------------------------------------------------------------------

class TestBrowserCacheCap(unittest.TestCase):
    def test_cap_500_reports_remaining(self):
        tmp = Path(tempfile.mkdtemp())
        cache = (tmp / "Google" / "Chrome" / "User Data"
                 / "Default" / "Cache")
        cache.mkdir(parents=True)
        for i in range(600):
            (cache / f"f{i:04d}.bin").write_bytes(b"x" * 10)
        with mock.patch.object(sys, "platform", "win32"):
            with mock.patch.dict("os.environ",
                                 {"LOCALAPPDATA": str(tmp)}):
                res = tools._clear_browser_caches()
        self.assertEqual(res["files_removed"], 500)
        self.assertEqual(res["files_remaining"], 100)
        self.assertEqual(len(list(cache.iterdir())), 100)

    def test_small_cache_fully_cleared(self):
        tmp = Path(tempfile.mkdtemp())
        cache = (tmp / "Microsoft" / "Edge" / "User Data"
                 / "Default" / "Cache")
        cache.mkdir(parents=True)
        for i in range(3):
            (cache / f"e{i}.bin").write_bytes(b"x" * 10)
        with mock.patch.object(sys, "platform", "win32"):
            with mock.patch.dict("os.environ",
                                 {"LOCALAPPDATA": str(tmp)}):
                res = tools._clear_browser_caches()
        self.assertEqual(res["files_removed"], 3)
        self.assertEqual(res["files_remaining"], 0)


# ---------------------------------------------------------------------------
# B20 -- DDGS search timeout
# ---------------------------------------------------------------------------

class TestDdgsTimeout(unittest.TestCase):
    def _fake_ddgs_module(self, sleep=None, results=None):
        class FakeDDGS:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def text(self, query, max_results=10):
                if sleep is not None:
                    time.sleep(sleep)
                return list(results or [])

        mod = _types.ModuleType("ddgs")
        mod.DDGS = FakeDDGS
        return mod

    def test_hung_search_raises_timeout_fast(self):
        fake = self._fake_ddgs_module(sleep=10)
        with mock.patch.dict(sys.modules, {"ddgs": fake}):
            t0 = time.monotonic()
            with self.assertRaises(TimeoutError):
                tools._ddgs_text("hello", 3, timeout=0.5)
            dt = time.monotonic() - t0
        self.assertLess(dt, 3.0, "search hung past its timeout")

    def test_successful_search_returns_results(self):
        fake = self._fake_ddgs_module(results=[
            {"title": "t", "href": "u", "body": "b"}])
        with mock.patch.dict(sys.modules, {"ddgs": fake}):
            out = tools._ddgs_text("hello", 3, timeout=5)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "t")


# ---------------------------------------------------------------------------
# B22 -- TempWatch: top processes only when hot; capped temp probe
# ---------------------------------------------------------------------------

class TestTempWatch(unittest.TestCase):
    def test_cool_temp_never_toasts_or_ranks(self):
        with mock.patch.object(
                tempwatch_mod, "_show_toast",
                side_effect=AssertionError("toast when cool")):
            res = tempwatch_mod.check_once(temp_c=50.0)
        self.assertFalse(res["alert"])
        self.assertEqual(res["causes"], [])

    def test_hot_sustained_alert_ranks_causes(self):
        procs = [{"name": "chrome", "cpu_percent": 40.0},
                 {"name": "game", "cpu_percent": 25.0}]
        with mock.patch.object(tempwatch_mod, "_show_toast",
                               return_value=True):
            with mock.patch.object(tempwatch_mod, "_append_log"):
                res = tempwatch_mod.check_once(
                    temp_c=92.0, consecutive_hot=1,
                    top_processes=procs, windows_update_running=True)
        self.assertTrue(res["alert"])
        self.assertEqual(res["level"], "critical")
        self.assertIn("chrome", res["causes"][0])

    def test_temp_probe_capped(self):
        def slow_probe():
            time.sleep(10)
            return (99.0, True)

        with mock.patch.object(monitor_mod, "read_cpu_temp",
                               side_effect=slow_probe):
            t0 = time.monotonic()
            value = tempwatch_mod._read_temp_capped(timeout=0.5)
            dt = time.monotonic() - t0
        self.assertEqual(value, (None, False))
        self.assertLess(dt, 3.0, "temp probe hung past its cap")


# ---------------------------------------------------------------------------
# B23 -- TTS timeouts 30/30/60
# ---------------------------------------------------------------------------

class TestSpeakTimeouts(unittest.TestCase):
    def test_gemini_tts_timeout_30(self):
        seen = {}

        def fake_post(url, **kwargs):
            seen.update(kwargs)
            resp = mock.Mock()
            resp.json.return_value = {"candidates": [
                {"content": {"parts": [
                    {"inlineData": {"data": base64.b64encode(
                        b"\x00\x00" * 100).decode("ascii")}}
                ]}}]}
            resp.raise_for_status.return_value = None
            return resp

        with mock.patch.object(speak_mod.requests, "post",
                               side_effect=fake_post):
            with mock.patch.object(speak_mod, "read_key",
                                   return_value="k"):
                out = speak_mod._gemini_tts("hello", "girl_warm")
        self.assertEqual(seen.get("timeout"), 30)
        self.assertTrue(Path(out).exists())

    def test_azure_tts_timeout_30(self):
        seen = {}

        def fake_post(url, **kwargs):
            seen.update(kwargs)
            resp = mock.Mock()
            resp.content = b"RIFF...."
            resp.raise_for_status.return_value = None
            return resp

        with mock.patch.object(speak_mod.requests, "post",
                               side_effect=fake_post):
            with mock.patch.object(speak_mod, "read_azure",
                                   return_value=("k", "eastus")):
                out = speak_mod._azure_tts("hello", "girl_warm")
        self.assertEqual(seen.get("timeout"), 30)
        self.assertTrue(Path(out).exists())

    def test_piper_timeout_60(self):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen.update(kwargs)
            return mock.Mock(returncode=0, stderr=b"")

        out_path = Path(tempfile.gettempdir()) / "memo-ai-piper.wav"
        out_path.write_bytes(b"fake-wav")
        with mock.patch.object(speak_mod.shutil, "which",
                               return_value="/usr/bin/piper"):
            with mock.patch.object(speak_mod, "_find_piper_model",
                                   return_value=Path("/tmp/model.onnx")):
                with mock.patch.object(speak_mod.subprocess, "run",
                                       side_effect=fake_run):
                    out = speak_mod._piper_tts("hello")
        self.assertEqual(seen.get("timeout"), 60)
        self.assertEqual(out, out_path)


# ---------------------------------------------------------------------------
# B24 -- temperature from ONE PowerShell spawn; capped probe
# ---------------------------------------------------------------------------

class TestTempProbes(unittest.TestCase):
    def test_powershell_temps_is_one_spawn(self):
        proc = mock.Mock()
        proc.stdout = "55.0\n60.5\nnot-a-temp\n"
        # _powershell_temps does `import subprocess` INSIDE the
        # function, so the real subprocess module is patched here.
        with mock.patch("subprocess.run",
                        return_value=proc) as run_mock:
            temps = monitor_mod._powershell_temps()
        run_mock.assert_called_once()
        cmd = run_mock.call_args.args[0]
        self.assertEqual(cmd.count("-Command"), 1)
        self.assertEqual(temps, [55.0, 60.5])

    def test_cpu_temp_probe_capped(self):
        def slow_probe():
            time.sleep(10)
            return (99.0, True)

        with mock.patch.object(monitor_mod, "read_cpu_temp",
                               side_effect=slow_probe):
            t0 = time.monotonic()
            value = monitor_mod._read_cpu_temp_capped(timeout=0.5)
            dt = time.monotonic() - t0
        self.assertEqual(value, (None, False))
        self.assertLess(dt, 3.0, "temp probe hung past its cap")


# ---------------------------------------------------------------------------
# B25 -- edit 5 MB cap; list_dir lazy first 100
# ---------------------------------------------------------------------------

class TestEditListCaps(unittest.TestCase):
    def test_edit_file_5mb_cap(self):
        tmp = Path(tempfile.mkdtemp())
        big = tmp / "big.txt"
        big.write_bytes(b"a" * (6 * 1024 * 1024))
        res = tools._tool_edit_file({
            "path": str(big), "old_text": "a", "new_text": "b"})
        self.assertIn("5MB", res)

    def test_edit_small_file_still_works(self):
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "small.txt"
        path.write_text("hello world", encoding="utf-8")
        # edit_file asks permission first: the trusted-caller
        # context auto-allows it in tests (as the owner would).
        with tools.trusted_caller():
            res = tools._tool_edit_file({
                "path": str(path), "old_text": "world",
                "new_text": "LONA"})
        self.assertIn("LONA", path.read_text(encoding="utf-8"))

    def test_list_dir_lazy_first_100(self):
        tmp = Path(tempfile.mkdtemp())
        for i in range(150):
            (tmp / f"file{i:03d}.txt").write_text("x", encoding="utf-8")
        res = tools._tool_list_dir({"path": str(tmp)})
        self.assertIn("وفي عناصر كمان", res)
        listed = [line for line in res.splitlines()
                  if line.strip().startswith("file")]
        self.assertEqual(len(listed), 100)


# ---------------------------------------------------------------------------
# B26 -- _apply_zip progress callback (0-50% extract, 50-100% copy)
# ---------------------------------------------------------------------------

class TestApplyZipProgress(unittest.TestCase):
    def _make_zip(self, tmp):
        zip_path = tmp / "update.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("memo-ai/app/main.py", "# main\n")
            zf.writestr("memo-ai/app/other.py", "# other\n")
            zf.writestr("memo-ai/readme.txt", "notes\n")
        return zip_path

    def test_progress_moves_per_file_to_100(self):
        tmp = Path(tempfile.mkdtemp())
        dest = tmp / "install"
        dest.mkdir()
        zip_path = self._make_zip(tmp)
        seen = []
        with mock.patch.object(updater, "BASE_DIR", dest):
            ok = updater._apply_zip(zip_path, "9.9.9",
                                    on_progress=seen.append)
        self.assertTrue(ok)
        self.assertTrue(seen, "no progress reported")
        self.assertEqual(seen[-1], 100)
        self.assertEqual(seen, sorted(seen), "progress went backwards")
        self.assertTrue((dest / "app" / "main.py").exists())
        self.assertTrue((dest / "app" / "other.py").exists())
        self.assertTrue((dest / "readme.txt").exists())

    def test_bad_zip_aborts_without_copy(self):
        tmp = Path(tempfile.mkdtemp())
        dest = tmp / "install"
        dest.mkdir()
        zip_path = tmp / "bad.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("memo-ai/readme.txt", "no main.py here\n")
        with mock.patch.object(updater, "BASE_DIR", dest):
            ok = updater._apply_zip(zip_path, "9.9.9",
                                    on_progress=lambda p: None)
        self.assertFalse(ok)
        self.assertFalse((dest / "readme.txt").exists())


# ---------------------------------------------------------------------------
# Chrome extension re-wire
# ---------------------------------------------------------------------------

class TestExtensionRewire(unittest.TestCase):
    def test_module_level_functions_exist(self):
        self.assertTrue(callable(getattr(native_host, "send_command",
                                         None)))
        self.assertTrue(callable(getattr(native_host, "is_connected",
                                         None)))
        self.assertTrue(callable(getattr(native_host, "set_shared_client",
                                         None)))
        self.assertTrue(callable(getattr(native_host, "get_shared_client",
                                         None)))

    def test_is_connected_false_with_no_client(self):
        orig = native_host.get_shared_client()
        try:
            native_host.set_shared_client(None)
            self.assertFalse(native_host.is_connected())
        finally:
            native_host.set_shared_client(orig)

    def test_send_command_translates_action_to_cmd(self):
        received = {}

        class FakeClient:
            def is_connected(self):
                return True

            def send(self, cmd, timeout=15.0):
                received.update(cmd)
                return {"ok": True, "cmd": cmd.get("cmd")}

            def close(self):
                pass

        orig = native_host.get_shared_client()
        try:
            native_host.set_shared_client(FakeClient())
            res = native_host.send_command(
                {"action": "play_first_youtube_result",
                 "query": "El-Daheeh"})
            self.assertTrue(res.get("ok"))
            self.assertEqual(received.get("cmd"), "play_first_youtube")
            self.assertEqual(received.get("query"), "El-Daheeh")
            self.assertNotIn("action", received)
        finally:
            native_host.set_shared_client(orig)

    def test_selfview_probe_finds_is_connected(self):
        # selfview.py does getattr(native_host, "is_connected") --
        # in v1.6.1 that attribute did not exist at module level.
        probe = getattr(native_host, "is_connected", None)
        self.assertTrue(callable(probe))

    def test_browser_control_uses_module_send_command(self):
        # End to end: browser_control._extension_play_first() does
        # getattr(native_host, "send_command") (the v1.6.1 dead
        # path) -- with a shared client registered, the translated
        # command must reach the extension.
        import browser_control
        received = {}

        class FakeClient:
            def is_connected(self):
                return True

            def send(self, cmd, timeout=15.0):
                received.update(cmd)
                return {"ok": True, "cmd": cmd.get("cmd")}

            def close(self):
                pass

        orig = native_host.get_shared_client()
        try:
            native_host.set_shared_client(FakeClient())
            res = browser_control._extension_play_first("El-Daheeh")
        finally:
            native_host.set_shared_client(orig)
        self.assertTrue(res.get("ok"))
        self.assertEqual(received.get("cmd"), "play_first_youtube")
        self.assertEqual(received.get("query"), "El-Daheeh")


# ---------------------------------------------------------------------------
# F4 -- island size/transparency + native-frame main window (rc.2)
# ---------------------------------------------------------------------------

class TestIslandAndFrameless(unittest.TestCase):
    def test_island_idle_size_220x60(self):
        # rc.2 W8: 380 -> 220 to match island.html's 220x60 pill.
        self.assertEqual(main_mod.ISLAND_IDLE_W, 220)
        self.assertEqual(main_mod.ISLAND_IDLE_H, 60)

    def test_island_radius_matches_pill(self):
        self.assertEqual(winshape.island_radius(380, 60), 30)
        self.assertEqual(winshape.island_radius(380, 56), 28)
        self.assertEqual(winshape.island_radius(600, 200), 22)

    def test_island_window_transparent_no_bg(self):
        import re
        m = re.search(r'create_window\(\s*"LONA", str\(ui_dir / '
                      r'"island\.html"\).*?js_api=self\.api\)',
                      MAIN_SRC, re.S)
        self.assertIsNotNone(m, "island create_window not found")
        block = m.group(0)
        self.assertIn("transparent=True", block)
        self.assertNotIn("background_color", block)
        # The CI check greps for the CALL (with paren) -- the name
        # also appears in main.py's header COMMENTS explaining why
        # the chroma-key API is forbidden, which is not a call.
        self.assertNotIn("SetLayeredWindowAttributes(", MAIN_SRC)
        self.assertNotIn("#FF00FF", MAIN_SRC)

    def test_main_window_default_size_bigger(self):
        # Was 960x720; v1.6.2 frameless item grows it for chat room.
        self.assertEqual(main_mod.MAIN_W, 1280)
        self.assertEqual(main_mod.MAIN_H, 860)

    def test_main_window_native_frame_resizable(self):
        # rc.2: the main window has a NATIVE Windows frame (standard
        # title bar, working maximize, Win+Arrow snap). It was
        # frameless since v1.6.2 item 3; the frameless window looked
        # like a browser and maximize/snap were broken. The island
        # stays frameless.
        import re
        m = re.search(r'create_window\(\s*"LONA", str\(ui_dir / '
                      r'"index\.html"\).*?js_api=self\.api\)',
                      MAIN_SRC, re.S)
        self.assertIsNotNone(m, "main create_window not found")
        block = m.group(0)
        # No frameless kwarg (the word may still appear in the rc.2
        # comment inside the call explaining why it was removed).
        self.assertNotIn("frameless=True", block)
        self.assertNotIn("frameless =", block)
        self.assertIn("resizable=True", block)
        self.assertNotIn("transparent", block)


class TestWindowControls(_ApiCase):
    def setUp(self):
        super().setUp()
        self.win = _FakeWindow()
        self.app.main_window = self.win

    def test_minimize(self):
        self.assertEqual(self.api.window_minimize(), {"ok": True})
        self.assertEqual(self.win.minimized, 1)

    def test_toggle_maximize(self):
        first = self.api.window_toggle_maximize()
        self.assertEqual(first, {"maximized": True})
        self.assertEqual(self.win.maximized_calls, 1)
        second = self.api.window_toggle_maximize()
        self.assertEqual(second, {"maximized": False})
        self.assertEqual(self.win.restored_calls, 1)

    def test_move_to(self):
        res = self.api.window_move_to(120, 240)
        self.assertEqual(res, {"ok": True, "x": 120, "y": 240})
        self.assertEqual(self.win.moved_to, [(120, 240)])

    def test_close_quits_like_quit_app(self):
        called = threading.Event()
        self.app.quit = lambda: called.set()
        self.assertEqual(self.api.window_close(), {"ok": True})
        self.assertTrue(called.wait(timeout=2),
                        "window_close did not quit")

    def test_controls_never_touch_island(self):
        island = _FakeWindow()
        self.app.island_window = island
        self.api.window_minimize()
        self.api.window_toggle_maximize()
        self.api.window_move_to(10, 10)
        self.assertEqual(island.minimized, 0)
        self.assertEqual(island.maximized_calls, 0)
        self.assertEqual(island.moved_to, [])


# ---------------------------------------------------------------------------
# API-keys page: cached status, once-per-process migration, show value
# ---------------------------------------------------------------------------

class TestApiKeysCacheAndShow(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.keys_dir = self.tmp / "keys"
        self.keys_dir.mkdir()
        self._patch = mock.patch.object(settings_store, "KEYS_DIR",
                                        self.keys_dir)
        self._patch.start()
        apikeys_mod._STATUS_CACHE.clear()
        apikeys_mod._migration_done = False

    def tearDown(self):
        self._patch.stop()
        apikeys_mod._STATUS_CACHE.clear()
        apikeys_mod._migration_done = False

    def test_status_cached_second_call_no_vault_read(self):
        apikeys_mod.save_key("groq", "sk-test-123")
        apikeys_mod._STATUS_CACHE.clear()
        with mock.patch.object(apikeys_mod, "read_key_value",
                               wraps=apikeys_mod.read_key_value) as spy:
            first = apikeys_mod.get_api_keys_status()
            first_calls = spy.call_count
            second = apikeys_mod.get_api_keys_status()
            second_calls = spy.call_count
        self.assertTrue(first.get("groq"))
        self.assertEqual(first, second)
        self.assertGreater(first_calls, 0)
        self.assertEqual(second_calls, first_calls,
                         "second status call re-read the vault")

    def test_status_is_presence_only(self):
        apikeys_mod.save_key("groq", "sk-test-123")
        status = apikeys_mod.get_api_keys_status()
        self.assertEqual(status.get("groq"), True)
        self.assertNotIn("sk-test-123", json.dumps(status))

    def test_maybe_migrate_runs_once_per_process(self):
        # _maybe_migrate is CALLED on every read, but the flag makes
        # the migration WORK run once: count the work, not the call.
        with mock.patch.object(
                apikeys_mod, "migrate_gemini_split",
                wraps=apikeys_mod.migrate_gemini_split) as spy:
            apikeys_mod.read_key_value("groq")
            apikeys_mod.read_key_value("groq")
            apikeys_mod.read_key_value("gemini-chat")
        self.assertEqual(spy.call_count, 1)
        self.assertTrue(apikeys_mod._migration_done)


class TestGetApiKeyValue(_ApiCase):
    def test_returns_value_for_known_provider(self):
        fake = _types.SimpleNamespace(
            PROVIDER_FILES={"groq": "groq-key.txt",
                            "gemini-chat": "gemini-chat-key.txt"},
            read_key_value=lambda p: "sk-test-123" if p == "groq" else "")
        with mock.patch.object(main_mod, "apikeys_mod", fake):
            self.assertEqual(self.api.get_api_key_value("groq"),
                             {"ok": True, "value": "sk-test-123"})
            self.assertEqual(self.api.get_api_key_value("gemini-chat"),
                             {"ok": True, "value": None})

    def test_unknown_provider_refused(self):
        fake = _types.SimpleNamespace(
            PROVIDER_FILES={"groq": "groq-key.txt"},
            read_key_value=lambda p: "x")
        with mock.patch.object(main_mod, "apikeys_mod", fake):
            self.assertEqual(self.api.get_api_key_value("nope"),
                             {"ok": False, "value": None})

    def test_value_never_in_status(self):
        # get_api_keys_status stays presence-only even though the
        # show-value method exists alongside it.
        self.assertNotIn("get_api_key_value",
                         apikeys_mod.get_api_keys_status.__code__.co_names)


# ---------------------------------------------------------------------------
# Model choice
# ---------------------------------------------------------------------------

class TestModelChoice(_ApiCase):
    def _options(self, keys, ollama=False):
        with mock.patch.object(main_mod, "keys_status",
                               return_value=keys):
            with mock.patch.object(brain, "ollama_available",
                                   return_value=ollama):
                return self.api.get_model_options()

    def test_options_contract_nothing_available(self):
        res = self._options({"groq": False, "gemini-chat": False})
        self.assertEqual(res["choice"], "auto")
        by_id = {o["id"]: o for o in res["options"]}
        self.assertEqual(
            [o["id"] for o in res["options"]],
            ["auto", "groq", "gemini", "local"])
        self.assertTrue(by_id["auto"]["available"])
        self.assertEqual(by_id["auto"]["label"], "تلقائي")
        self.assertFalse(by_id["groq"]["available"])
        self.assertEqual(by_id["groq"]["reason"], "محتاج مفتاح Groq")
        self.assertFalse(by_id["gemini"]["available"])
        self.assertEqual(by_id["gemini"]["reason"], "محتاج مفتاح Gemini")
        self.assertFalse(by_id["local"]["available"])
        self.assertEqual(by_id["local"]["reason"], "Ollama مش متثبت")

    def test_options_contract_all_available(self):
        res = self._options({"groq": True, "gemini-chat": True},
                            ollama=True)
        by_id = {o["id"]: o for o in res["options"]}
        self.assertTrue(by_id["groq"]["available"])
        self.assertEqual(by_id["groq"]["reason"], "")
        self.assertTrue(by_id["gemini"]["available"])
        self.assertTrue(by_id["local"]["available"])

    def test_set_choice_persists(self):
        res = self.api.set_model_choice("groq")
        self.assertEqual(res, {"ok": True, "choice": "groq"})
        self.assertEqual(self.app.settings["model_choice"], "groq")
        saved = json.loads(
            (self.tmp / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(saved.get("model_choice"), "groq")
        res = self.api.set_model_choice("auto")
        self.assertEqual(res, {"ok": True, "choice": "auto"})

    def test_set_invalid_choice_refused(self):
        res = self.api.set_model_choice("gpt-5")
        self.assertFalse(res["ok"])
        self.assertEqual(self.app.settings.get("model_choice", "auto"),
                         "auto")


class TestModelChoiceStrict(unittest.TestCase):
    def setUp(self):
        brain.clear_history()
        brain.set_memory_store(None)

    def tearDown(self):
        brain.clear_history()
        brain.set_memory_store(None)

    def test_strict_groq_429_names_groq_no_fallback(self):
        def boom(messages, use_tools=False, **kwargs):
            raise brain.GroqRateLimit(limit="rpm", retry_after=30)

        with mock.patch.object(brain, "is_online", return_value=True):
            with mock.patch.object(brain, "_groq_message",
                                   side_effect=boom):
                with mock.patch.object(
                        brain, "_ask_fallback_chain",
                        side_effect=AssertionError("fallback ran")):
                    res = brain.ask_with_tools(
                        "hello", "LONA", "Mohamed", "en",
                        history=[], memory_enabled=False,
                        model_choice="groq")
        self.assertEqual(res["engine"], "")
        self.assertIn("Groq", res["reply"])
        self.assertTrue(res["tools_disabled"])

    def test_strict_gemini_failure_names_gemini(self):
        with mock.patch.object(
                brain, "_ask_gemini_chat",
                return_value=("", "", "gemini-failed")):
            res = brain.ask_with_tools(
                "hello", "LONA", "Mohamed", "en",
                history=[], memory_enabled=False, model_choice="gemini")
        self.assertEqual(res["engine"], "")
        self.assertIn("Gemini", res["reply"])
        self.assertTrue(res["tools_disabled"])

    def test_strict_gemini_success_is_text_only(self):
        with mock.patch.object(
                brain, "_ask_gemini_chat",
                return_value=("gemini says hi", "gemini", "")):
            res = brain.ask_with_tools(
                "hello", "LONA", "Mohamed", "en",
                history=[], memory_enabled=False, model_choice="gemini")
        self.assertEqual(res["engine"], "gemini")
        self.assertEqual(res["reply"], "gemini says hi")
        self.assertTrue(res["tools_disabled"])
        self.assertEqual(res["tools_used"], 0)

    def test_strict_local_down_names_ollama(self):
        with mock.patch.object(brain, "_ask_local",
                               return_value=("", "")):
            res = brain.ask_with_tools(
                "hello", "LONA", "Mohamed", "en",
                history=[], memory_enabled=False, model_choice="local")
        self.assertEqual(res["engine"], "")
        self.assertIn("Ollama", res["reply"])

    def test_auto_still_falls_back_after_groq_429(self):
        def boom(messages, use_tools=False, **kwargs):
            raise brain.GroqRateLimit(limit="rpm", retry_after=30)

        with mock.patch.object(brain, "is_online", return_value=True):
            with mock.patch.object(brain, "_groq_message",
                                   side_effect=boom):
                with mock.patch.object(
                        brain, "_ask_fallback_chain",
                        return_value=("gemini fallback", "gemini")):
                    res = brain.ask_with_tools(
                        "hello", "LONA", "Mohamed", "en",
                        history=[], memory_enabled=False,
                        model_choice="auto")
        self.assertEqual(res["engine"], "gemini")
        self.assertEqual(res["reply"], "gemini fallback")

    def test_deep_think_reasoning_only_on_groq(self):
        seen = {}

        def capture(messages, use_tools=False, **kwargs):
            seen.update(kwargs)
            return {"content": "deep answer", "tool_calls": []}

        with mock.patch.object(brain, "is_online", return_value=True):
            with mock.patch.object(brain, "_groq_message",
                                   side_effect=capture):
                res = brain.ask_with_tools(
                    "hello", "LONA", "Mohamed", "en",
                    history=[], memory_enabled=False,
                    deep_think=True, model_choice="groq")
        self.assertEqual(res["engine"], "groq")
        self.assertTrue(seen.get("deep_think"))
        self.assertEqual(seen.get("timeout"), 150)


# ---------------------------------------------------------------------------
# Watermark: bottom-strip crop at save time
# ---------------------------------------------------------------------------

class TestWatermarkCrop(unittest.TestCase):
    def test_save_crops_bottom_strip_with_watermark(self):
        from PIL import Image
        tmp = Path(tempfile.mkdtemp())
        # 100x100: white picture, red "watermark" strip in the
        # bottom 5 rows (where Pollinations stamps its logo).
        im = Image.new("RGB", (100, 100), (255, 255, 255))
        for y in range(95, 100):
            for x in range(100):
                im.putpixel((x, y), (255, 0, 0))
        buf = io.BytesIO()
        im.save(buf, "JPEG")
        name, path = tools._save_generated_image(buf.getvalue(), tmp)
        self.assertIsNotNone(name)
        saved = Image.open(path)
        # 7% of 100 rows cropped: height 93, red strip gone.
        self.assertEqual(saved.size, (100, 93))
        bottom = [saved.getpixel((x, 92)) for x in (0, 50, 99)]
        self.assertTrue(all(p != (255, 0, 0) for p in bottom),
                        "watermark strip survived the crop")

    def test_real_pollinations_image_loses_logo(self):
        # The live image downloaded 2026-11-22 (768x768, logo in the
        # bottom-right corner) -- saved through the real code path.
        src = Path("/tmp/pollinations_test.jpg")
        if not src.exists():
            self.skipTest("live Pollinations sample not downloaded")
        tmp = Path(tempfile.mkdtemp())
        name, path = tools._save_generated_image(src.read_bytes(), tmp)
        self.assertIsNotNone(name)
        from PIL import Image
        saved = Image.open(path)
        self.assertEqual(saved.size[0], 768)
        self.assertLess(saved.size[1], 768)

    def test_generate_url_keeps_nologo(self):
        self.assertIn("nologo=true", TOOLS_SRC)


# ---------------------------------------------------------------------------
# (4a) deep-file access callable from JS -- pending/id + poll
# ---------------------------------------------------------------------------

class TestDeepFileAccess(_ApiCase):
    def test_request_returns_pending_and_poll_completes(self):
        self.assertTrue(callable(getattr(self.api,
                                         "request_deep_file_access",
                                         None)))
        with mock.patch.object(tools, "execute_tool",
                               return_value="listed 3 files") as exec_mock:
            res = self.api.request_deep_file_access("/tmp")
            self.assertTrue(res.get("pending"))
            self.assertTrue(res.get("id"))
            final = _poll_until(
                lambda: (self.api.get_deep_file_access_result(res["id"])
                         .get("done")
                         and self.api.get_deep_file_access_result(
                             res["id"])),
                timeout=5.0)
        self.assertTrue(final["done"])
        self.assertEqual(final["result"], "listed 3 files")
        exec_mock.assert_called_once()

    def test_poll_unknown_id_is_not_done(self):
        res = self.api.get_deep_file_access_result("nope1234")
        self.assertFalse(res["done"])


# ---------------------------------------------------------------------------
# (4b) temperature-unavailable state reaches the UI explicitly
# ---------------------------------------------------------------------------

class TestTempUnavailableState(unittest.TestCase):
    def test_overview_says_unavailable_with_hint(self):
        mon = SystemMonitor({})
        try:
            overview = mon.get_device_overview()
        finally:
            mon.stop_sampler()
            mon.stop()
        self.assertFalse(overview["temp_available"])
        self.assertIsNone(overview["temp_c"])
        hint = overview["temp_hint"]
        self.assertIsInstance(hint, dict)
        self.assertIn("الحرارة مش متاحة", hint["text"])
        self.assertEqual(
            hint["url"],
            "https://github.com/LibreHardwareMonitor/"
            "LibreHardwareMonitor/releases")

    def test_overview_hint_is_none_when_available(self):
        # A monitor whose temp probe answers: hint disappears.
        reading = {"ram": {"total_mb": 8192, "used_mb": 100,
                           "free_mb": 8092, "percent": 1},
                   "disk": {"free_gb": 100, "total_gb": 200,
                            "percent_free": 50},
                   "cpu_percent": 5.0, "battery": {"percent": 90,
                                                   "charging": False},
                   "temp_c": 55.0, "temp_available": True,
                   "top_processes": [], "gpu": {"available": False},
                   "storage": [], "battery_drain": {}}
        mon = SystemMonitor({}, sample_fn=lambda: dict(reading))
        try:
            overview = mon.get_device_overview()
        finally:
            mon.stop_sampler()
            mon.stop()
        self.assertTrue(overview["temp_available"])
        self.assertEqual(overview["temp_c"], 55.0)
        self.assertIsNone(overview["temp_hint"])
