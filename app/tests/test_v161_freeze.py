"""Item 21 (v1.6.1): the app must not freeze.

Root cause (code-verified, not guessed): every 5 s poll (live cards +
mood, two callers) ran a FRESH synchronous monitor sample on the
pywebview bridge thread, and every sample ran read_gpu_usage() -- a
PowerShell subprocess with a 10 s timeout -- plus a full process
iteration. While that sample runs (1-3 s typically, up to 10 s+ when
the machine is busy, 30 s for the 10-minute temperature probe), the
whole window is unresponsive: invite boxes stop accepting input,
clicks do nothing, check_invite waits. That is the freeze Mohamed
reported on the invite screen (type -> delete -> retype).

Fix:
  * monitor.py: a background sampler thread (fast psutil fields every
    5 s, slow PowerShell/GPU/storage fields every 60 s) feeds a cache;
    get_status()/get_device_overview() serve the cache and never wait
    on a subprocess. The UI thread only ever runs psutil reads.
    Injected sample_fn (tests) still samples synchronously, as before.
  * main.py: every bridge call taking >1 s is logged to the
    diagnostics log (@_log_slow); the get_device_overview /
    get_monitor_status fallbacks share one monitor with a sampler
    thread instead of constructing + synchronously sampling a
    throwaway monitor on every poll.
  * app.js wireInvite: Backspace keydown preventDefault()s (the
    browser default used to land on the newly focused box), a
    reentrancy guard on input, and the activate call locks the button
    + times out after 10 s instead of hanging the gate.
  * jsdom could NOT reproduce a freeze (see invite_gate_dom.js header):
    under jsdom's event model the old code's values still came out
    right. The hardening removes the hazard class anyway, and the
    monitor cache removes the proven UI-thread block.

Tests: monitor cache behaviour (real, importable here), main.py/app.js
source inspection (main.py cannot be imported: no pywebview), and the
jsdom harness (invite_gate_dom.js, scenarios 5/7/8/9).
"""

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "app"
sys.path.insert(0, str(APP_DIR))

import monitor as monitor_mod  # noqa: E402
from monitor import SystemMonitor  # noqa: E402

MAIN = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
JS = (ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")
MONITOR_SRC = (ROOT / "app" / "monitor.py").read_text(encoding="utf-8")


def _reading(**overrides):
    base = {"ram": {"total_mb": 8192, "used_mb": 4096, "free_mb": 4096,
                    "percent": 50.0},
            "disk": {"free_gb": 100.0, "total_gb": 256.0,
                     "percent_free": 80.0},
            "cpu_percent": 10.0, "battery": None,
            "temp_c": None, "temp_available": False}
    base.update(overrides)
    return base


class TestMonitorCache(unittest.TestCase):
    def test_get_status_serves_cache_without_subprocess(self):
        """get_status on the real sampler never spawns PowerShell: the
        GPU/temp probes are background-only."""
        mon = SystemMonitor()
        with mock.patch.object(
                monitor_mod, "read_gpu_usage",
                side_effect=AssertionError("GPU probe ran")):
            with mock.patch.object(
                    monitor_mod, "read_cpu_temp",
                    side_effect=AssertionError("temp probe ran")):
                status = mon.get_status()
        self.assertIsNotNone(status["ram"])
        self.assertGreater(status["ram"]["total_mb"], 0)
        self.assertIs(status["temp_c"], None)
        self.assertIs(status["temp_available"], False)

    def test_fast_sample_never_spawns_subprocess(self):
        mon = SystemMonitor()
        with mock.patch("subprocess.run",
                        side_effect=AssertionError("subprocess spawned")):
            fast = mon._sample_fast()
        self.assertIsNotNone(fast["ram"])
        # v1.6.2: the top_processes KEY is absent from the fast
        # sample on purpose -- a fast partial carrying [] would wipe
        # the slow-delivered list out of the merged cache on every
        # 5 s sample (the process list flickered to empty forever).
        # get_status() still reports [] until the sampler delivers.
        self.assertNotIn("top_processes", fast)
        self.assertNotIn("gpu", fast)
        self.assertNotIn("temp_c", fast)

    def test_sampler_thread_fills_slow_fields(self):
        mon = SystemMonitor()
        try:
            self.assertTrue(mon.start_sampler())
            deadline = time.time() + 10
            while time.time() < deadline:
                with mon._cache_lock:
                    cached = mon._cached_reading
                if cached is not None and "gpu" in cached \
                        and "storage" in cached:
                    break
                time.sleep(0.1)
            self.assertIsNotNone(cached, "sampler never seeded the cache")
            self.assertIn("gpu", cached)
            self.assertIn("storage", cached)
            self.assertIn("ram", cached)
            # A cached status now answers without any fresh sampling.
            with mock.patch.object(mon, "_sample_fast",
                                  side_effect=AssertionError("re-sampled")):
                status = mon.get_status()
            self.assertIsNotNone(status["ram"])
        finally:
            mon.stop_sampler()
        self.assertIsNone(mon._sampler_thread)

    def test_injected_sampler_still_samples_synchronously(self):
        """Tests inject sample_fn and mutate its readings between
        calls -- that behaviour is unchanged."""
        readings = [_reading(cpu_percent=10.0), _reading(cpu_percent=95.0)]
        mon = SystemMonitor(sample_fn=lambda: readings.pop(0))
        self.assertEqual(mon.get_status()["cpu"]["percent"], 10.0)
        self.assertEqual(mon.get_status()["cpu"]["percent"], 95.0)

    def test_check_once_with_injected_sampler_still_fresh(self):
        mon = SystemMonitor(on_alert=lambda a: None,
                            sample_fn=lambda: _reading(temp_c=95.0,
                                                      temp_available=True))
        got = mon.check_once(now=7_000_000.0)
        self.assertEqual([a["kind"] for a in got], ["temp_high"])

    def test_start_starts_sampler_and_stop_stops_it(self):
        mon = SystemMonitor(sample_fn=lambda: _reading())
        try:
            self.assertTrue(mon.start())
            self.assertIsNotNone(mon._sampler_thread)
            self.assertTrue(mon._sampler_thread.is_alive())
        finally:
            mon.stop()
        self.assertFalse(mon.running)
        self.assertIsNone(mon._sampler_thread)

    def test_disabled_monitor_starts_neither_thread(self):
        mon = SystemMonitor(settings={"monitor_enabled": False},
                            sample_fn=lambda: _reading())
        self.assertFalse(mon.start())
        self.assertIsNone(mon._sampler_thread)

    def test_slow_sample_duration_is_logged(self):
        self.assertIn("Background sample took", MONITOR_SRC)
        self.assertIn("UI unaffected", MONITOR_SRC)


class TestMainSlowCallLogging(unittest.TestCase):
    SLOW_METHODS = ("chat_result", "get_pending_permission",
                    "get_device_overview", "get_mood", "get_self_view",
                    "get_api_keys_status", "test_api_key", "check_invite",
                    "get_proactive_feed", "get_exam_countdown",
                    "analyze_image", "understand_image", "check_updates",
                    "get_update_progress", "get_monitor_status")

    def test_log_slow_decorator_defined(self):
        self.assertIn("def _log_slow(fn):", MAIN)
        self.assertIn("if dt > 1.0:", MAIN)
        self.assertIn('warn("app", f"Slow bridge call', MAIN)

    def test_slow_methods_decorated(self):
        for name in self.SLOW_METHODS:
            self.assertIn(f"    @_log_slow\n    def {name}(", MAIN, name)

    def test_fallback_monitor_is_shared_not_per_call(self):
        self.assertIn("def _fallback_monitor():", MAIN)
        self.assertIn("_FALLBACK_MONITOR", MAIN)
        self.assertIn("start_sampler()", MAIN)
        # get_monitor_status still calls the fallback directly in main.py.
        block = MAIN.split("def get_monitor_status")[1].split(
            "    def ", 1)[0]
        self.assertIn("_fallback_monitor()", block, "get_monitor_status")
        self.assertNotIn("SystemMonitor(", block, "get_monitor_status")
        # v1.6.4 reskin: get_device_overview delegates to
        # bridge_v2.get_device_overview_v2, which must use the same
        # cached fallback monitor (never construct SystemMonitor).
        overview = MAIN.split("def get_device_overview")[1].split(
            "    def ", 1)[0]
        self.assertIn("bridge_v2.get_device_overview_v2", overview)
        bridge = (ROOT / "app" / "bridge_v2.py").read_text(
            encoding="utf-8")
        fn = bridge.split("def get_device_overview_v2")[1].split(
            "\ndef ", 1)[0]
        self.assertIn("_fallback_monitor()", fn)
        self.assertNotIn("SystemMonitor(", fn)


class TestInviteHardening(unittest.TestCase):
    def test_backspace_prevents_default_after_focus_move(self):
        block = JS.split("function wireInvite()")[1].split(
            "// ---------- UPDATER")[0]
        self.assertIn("e.preventDefault();", block)
        # The preventDefault sits in the Backspace keydown handler,
        # before the focus move -- not in the paste handler only.
        keydown = block.split('addEventListener("keydown"')[1].split(
            'addEventListener("paste"')[0]
        self.assertIn("e.preventDefault();", keydown)
        self.assertIn("boxes[i - 1].focus();", keydown)

    def test_reentrancy_guard_present(self):
        block = JS.split("function wireInvite()")[1].split(
            "// ---------- UPDATER")[0]
        self.assertIn("let inviteHandling = false;", block)
        self.assertIn("if (inviteHandling) return;", block)

    def test_activate_times_out_and_locks(self):
        block = JS.split("function wireInvite()")[1].split(
            "// ---------- UPDATER")[0]
        self.assertIn("Promise.race([", block)
        self.assertIn("b.check_invite(code),", block)
        self.assertIn("timeout: true", block)
        self.assertIn('"التحقق اتأخر — جرّب تاني"', block)
        self.assertIn("activateBtn.disabled = true;", block)
        self.assertIn('"بيتحقق…"', block)
        self.assertIn("window.__inviteCheckTimeoutMs", block)


class TestInviteGateDomHarness(unittest.TestCase):
    def test_dom_harness_passes(self):
        node_path = "/tmp/jsdom-test/node_modules"
        try:
            probe = subprocess.run(
                ["node", "-e", "require.resolve('jsdom')"],
                capture_output=True, text=True, timeout=30,
                env={**os.environ, "NODE_PATH": node_path})
        except Exception:
            probe = None
        if probe is None or probe.returncode != 0:
            self.skipTest(
                "jsdom not installed; run "
                "npm install jsdom --prefix /tmp/jsdom-test")
        run = subprocess.run(
            ["node", str(ROOT / "app" / "tests" / "invite_gate_dom.js")],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "NODE_PATH": node_path})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        results = json.loads(run.stdout)
        failed = [k for k, v in results.items() if not v["pass"]]
        self.assertEqual(failed, [], f"harness failures: {failed}")
        for scenario in ("scenario_typed_all", "scenario_deleted_all",
                         "scenario_retyped_all", "rapid_retyped_all",
                         "paste_delete_retyped", "timeout_message",
                         "timeout_button_reenabled"):
            self.assertIn(scenario, results, scenario)
            self.assertTrue(results[scenario]["pass"], scenario)


if __name__ == "__main__":
    unittest.main()
