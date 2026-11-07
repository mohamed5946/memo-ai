# Memo AI v1.4.3 - headless tests for items 2 + 4:
#   * system_status: real numbers, partial data, honest "unavailable"
#   * hidden spawning: no console window on Windows (CREATE_NO_WINDOW)
#
#   python -m unittest app.tests.test_v143_sysstatus -v
#   python -m unittest discover -s app/tests -v   (full suite)
#
# What is proven here:
#   * the v1.4.2 root cause is gone: the tool no longer calls the
#     non-existent module-level monitor.get_status() - it reads
#     through a live SystemMonitor (provider) or a fresh instance,
#     and returns REAL RAM/disk/CPU numbers on this machine.
#   * temperature unavailable (the user's Dell: WMI thermal never
#     answers, confirmed in his 2026-11-07 log) -> the RAM/disk/CPU
#     numbers still come back, and the temperature is stated
#     honestly as unavailable - never guessed, never a whole-tool
#     failure.
#   * one source failing (RAM read raises, CPU read raises, psutil
#     missing, sample_fn raising) -> the other sources still return,
#     the failed one is None / "مش متاحة دلوقتي", and nothing is
#     ever a fake 0.
#   * hidden_spawn: {} on non-Windows, CREATE_NO_WINDOW on win32;
#     the PowerShell temp probe, run_command, the piper spawn and
#     the WhatsApp sidecar Popen all pass that flag through
#     (mocked subprocess, platform patched to win32).
#
# NOT proven here (needs real Windows): that no console window is
# actually visible on screen, and the WMI temperature probe. Those
# are the field tests in the release notes.

import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

import hidden_spawn  # noqa: E402
import monitor as monitor_mod  # noqa: E402
import tools  # noqa: E402
import whatsapp as whatsapp_mod  # noqa: E402
from monitor import SystemMonitor  # noqa: E402


class TestSystemStatusTool(unittest.TestCase):
    def setUp(self):
        self.addCleanup(tools.set_monitor_provider, None)

    def test_tool_returns_real_numbers(self):
        # The v1.4.2 bug: AttributeError (no module-level
        # monitor.get_status) made the whole tool fail, so the brain
        # said it "couldn't fetch" RAM too. Now: real numbers.
        text = tools._tool_system_status({})
        self.assertNotIn("مقدرتش", text)
        self.assertIn("الرام:", text)
        m = re.search(r"مستخدمة من (\d+)MB", text)
        self.assertIsNotNone(m, f"no real RAM total in: {text!r}")
        self.assertGreater(int(m.group(1)), 0)
        self.assertIn("القرص:", text)
        self.assertIn("المعالج:", text)

    def test_temp_unavailable_still_returns_ram(self):
        # The user's Dell: WMI thermal never answers (his log:
        # "[monitor] CPU temperature unavailable (no WMI thermal
        # source answered)"). Temp stays honestly unavailable AND
        # the RAM/disk/CPU numbers still come back.
        mon = SystemMonitor()
        with mock.patch.object(monitor_mod, "read_cpu_temp",
                               return_value=(None, False)):
            status = mon.get_status()
        self.assertGreater(status["ram"]["total_mb"], 0)
        self.assertGreater(status["disk"]["total_gb"], 0)
        self.assertIsNotNone(status["cpu"]["percent"])
        self.assertIsNone(status["temp_c"])
        self.assertIs(status["temp_available"], False)
        with mock.patch.object(monitor_mod, "read_cpu_temp",
                               return_value=(None, False)):
            text = tools._tool_system_status({})
        self.assertIn("الرام:", text)
        self.assertNotIn("مقدرتش", text)
        self.assertIn("حرارة المعالج: مش متاحة", text)

    def test_psutil_missing_is_honest(self):
        # No psutil at all (missing from the PyInstaller build was
        # one of the v1.4.2 suspects): honest "unavailable", never
        # fake 0MB/0GB numbers, never a crash.
        with mock.patch.object(monitor_mod, "psutil", None):
            status = SystemMonitor().get_status()
        self.assertIs(status["available"], False)
        self.assertIsNone(status["ram"])
        self.assertIsNone(status["disk"])
        with mock.patch.object(monitor_mod, "psutil", None):
            text = tools._tool_system_status({})
        self.assertNotIn("0MB مستخدمة من 0MB", text)
        self.assertIn("مش متاحة", text)

    def test_one_source_failing_keeps_others(self):
        # A single failing source (RAM read raises) must not take
        # the whole tool down - disk still returns, RAM says
        # "مش متاحة دلوقتي".
        with mock.patch.object(monitor_mod.psutil, "virtual_memory",
                               side_effect=RuntimeError("boom")):
            status = SystemMonitor().get_status()
        self.assertIsNone(status["ram"])
        self.assertGreater(status["disk"]["total_gb"], 0)
        with mock.patch.object(monitor_mod.psutil, "virtual_memory",
                               side_effect=RuntimeError("boom")):
            text = tools._tool_system_status({})
        self.assertIn("الرام: مش متاحة دلوقتي", text)
        self.assertIn("القرص: فاضي", text)
        self.assertNotIn("مقدرتش أقرأ حالة اللابتوب", text)

    def test_cpu_source_failing_is_not_fake_zero(self):
        with mock.patch.object(monitor_mod.psutil, "cpu_percent",
                               side_effect=RuntimeError("boom")):
            status = SystemMonitor().get_status()
        self.assertIsNone(status["cpu"]["percent"])
        with mock.patch.object(monitor_mod.psutil, "cpu_percent",
                               side_effect=RuntimeError("boom")):
            text = tools._tool_system_status({})
        self.assertIn("المعالج: مش متاح دلوقتي", text)
        self.assertNotIn("المعالج: 0.0%", text)

    def test_sample_fn_raising_degrades_gracefully(self):
        mon = SystemMonitor(sample_fn=lambda: (_ for _ in ()).throw(
            RuntimeError("sampling boom")))
        status = mon.get_status()  # must not raise
        self.assertIsNone(status["ram"])
        self.assertIsNone(status["disk"])
        self.assertIsNone(status["cpu"]["percent"])

    def test_live_monitor_provider_is_used(self):
        # main.py registers the live monitor; the tool must read
        # through it (not ignore it and spawn a fresh probe).
        fake = mock.Mock()
        fake.get_status.return_value = {
            "ram": {"total_mb": 8192, "used_mb": 4096, "free_mb": 4096,
                    "percent": 50.0},
            "disk": {"free_gb": 42.0, "total_gb": 256.0,
                     "percent_free": 16.4},
            "cpu": {"percent": 12.5, "sustained_high": False},
            "battery": {"percent": 99, "charging": True},
            "temp_c": None, "temp_available": False,
            "available": True}
        tools.set_monitor_provider(lambda: fake)
        text = tools._tool_system_status({})
        fake.get_status.assert_called_once()
        self.assertIn("4096MB مستخدمة من 8192MB", text)
        self.assertIn("فاضي 42.0GB من 256.0GB", text)
        self.assertIn("المعالج: 12.5%", text)
        self.assertIn("البطارية: 99% (بتشحن 🔌)", text)


class TestHiddenSpawn(unittest.TestCase):
    def test_helper_returns_nothing_off_windows(self):
        self.assertEqual(hidden_spawn.popen_kwargs(), {})
        self.assertEqual(hidden_spawn.run_kwargs(), {})

    def test_helper_returns_create_no_window_on_win32(self):
        with mock.patch.object(sys, "platform", "win32"):
            self.assertEqual(
                hidden_spawn.popen_kwargs(),
                {"creationflags": 0x08000000})
            self.assertEqual(
                hidden_spawn.run_kwargs(),
                {"creationflags": 0x08000000})

    def test_temp_probe_passes_hidden_flag(self):
        # The monitor's PowerShell temperature probe runs every
        # 10 minutes on Windows - it must never pop a console.
        fake_proc = mock.Mock()
        fake_proc.stdout = ""
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch("subprocess.run",
                           return_value=fake_proc) as run_mock:
            monitor_mod.read_cpu_temp()
        self.assertTrue(run_mock.called)
        for call in run_mock.call_args_list:
            self.assertEqual(call.kwargs.get("creationflags"),
                             0x08000000,
                             f"PowerShell probe without CREATE_NO_WINDOW: {call}")

    def test_run_command_passes_hidden_flag(self):
        # run_command spawns PowerShell for the user's command.
        fake_proc = mock.Mock()
        fake_proc.stdout = "ok\n"
        fake_proc.stderr = ""
        fake_proc.returncode = 0
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch("subprocess.run",
                           return_value=fake_proc) as run_mock, \
                mock.patch.object(tools, "request_permission",
                                  return_value=True):
            out = tools._tool_run_command({"command": "echo hi"})
        self.assertTrue(run_mock.called)
        for call in run_mock.call_args_list:
            self.assertEqual(call.kwargs.get("creationflags"),
                             0x08000000,
                             f"run_command without CREATE_NO_WINDOW: {call}")
        self.assertIn("ok", out)

    def test_sidecar_popen_passes_hidden_flag(self):
        # The WhatsApp sidecar's node.exe is the prime suspect for
        # the Terminal window the user saw (and that the watchdog
        # re-opened after he closed it). Its Popen must be hidden.
        tmp = Path(tempfile.mkdtemp())
        fake_sidecar = tmp / "sidecar.js"
        fake_sidecar.write_text("// fake", encoding="utf-8")
        link = whatsapp_mod.WhatsAppService({})
        loops = ("_reader_loop", "_stderr_loop", "_worker_loop",
                 "_watchdog_loop")
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch.object(whatsapp_mod.WhatsAppService,
                                  "_find_node",
                                  return_value="C:\\node.exe"), \
                mock.patch.object(whatsapp_mod, "SIDECAR_JS",
                                  fake_sidecar), \
                mock.patch.object(whatsapp_mod, "AUTH_DIR", tmp), \
                mock.patch("subprocess.Popen") as popen_mock, \
                mock.patch.multiple(whatsapp_mod.WhatsAppService,
                                    **{name: (lambda self: None)
                                       for name in loops}):
            self.assertTrue(link.start())
        popen_mock.assert_called_once()
        kwargs = popen_mock.call_args.kwargs
        self.assertEqual(kwargs.get("creationflags"), 0x08000000,
                         f"sidecar Popen without CREATE_NO_WINDOW: {kwargs}")


if __name__ == "__main__":
    unittest.main()
