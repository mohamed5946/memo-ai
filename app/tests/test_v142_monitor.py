# Memo AI v1.4.2 - headless tests for the system monitor +
# important-notification alerts (run on Linux, no Windows, no keys).
#
#   python -m unittest discover -s app/tests -v
#   (also runnable under pytest: these are plain unittest.TestCase classes)
#
# What is proven here:
#   * RAM below threshold -> EXACTLY ONE alert; a second poll inside the
#     cooldown -> no duplicate; after the condition clears and
#     re-appears (past the cooldown) -> the alert fires again; a
#     re-appearance inside the cooldown after a clear stays suppressed
#     (a flickering condition cannot spam).
#   * A nearly-full disk -> alert fires (critical when < 2 GB free).
#   * get_status() on this real machine returns real numbers (RAM total
#     > 0, disk total > 0); temp_available is False on Linux and NO fake
#     temperature is present.
#   * Sustained CPU > 85% over ~5 min -> alert; two quick high polls
#     alone do NOT trigger it.
#   * CPU temperature: a real sensor reading alerts; no sensor -> no
#     alert, no invented temperature. Battery: discharging < 20% alerts,
#     charging does not.
#   * Notification filter: important app (WhatsApp / mail) -> alert;
#     unlisted app -> no alert; keyword match in the title -> alert;
#     title/body text is NEVER written to the log.
#   * Listener unavailable on Linux -> get_listener_status() says so
#     honestly; request_access() fails cleanly; show_toast() is False.
#
# NOT proven here (needs real Windows): the WMI temperature probe, the
# live winrt listener actually receiving a toast, the permission prompt
# on first run. Those are the field tests in the release notes.

import sys
import time
import unittest
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

import monitor as monitor_mod  # noqa: E402
import notifications as notif_mod  # noqa: E402
from monitor import SystemMonitor  # noqa: E402
from notifications import (  # noqa: E402
    NotificationWatcher,
    get_listener_status,
    request_access,
    show_toast,
)


def _reading(ram_free_mb=4000, ram_pct=50.0, disk_free_gb=100.0,
             disk_pct_free=80.0, cpu=10.0, battery=None,
             temp_c=None, temp_available=False):
    """One fake reading in exactly the shape _sample_real returns."""
    return {
        "ram": {"total_mb": 8192, "used_mb": 8192 - ram_free_mb,
                "free_mb": ram_free_mb, "percent": ram_pct},
        "disk": {"free_gb": disk_free_gb, "total_gb": 256.0,
                 "percent_free": disk_pct_free},
        "cpu_percent": cpu,
        "battery": battery,
        "temp_c": temp_c,
        "temp_available": temp_available,
    }


def _healthy(**overrides):
    kw = dict(_reading())
    kw.update(overrides)
    return kw


class TestRamAlertCooldown(unittest.TestCase):
    def test_low_ram_fires_once_then_rearms_after_clear(self):
        fired = []
        mon = SystemMonitor(on_alert=fired.append)
        low = _reading(ram_free_mb=300, ram_pct=95.0)
        t0 = 1_000_000.0

        # First appearance -> exactly one alert.
        got = mon.check_once(low, now=t0)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "ram_low")
        self.assertEqual(got[0]["level"], "warn")
        self.assertIn("title_ar", got[0])
        self.assertIn("message_ar", got[0])
        self.assertEqual(len(fired), 1)

        # Second poll inside the cooldown (and still low) -> no dup.
        self.assertEqual(mon.check_once(low, now=t0 + 60), [])
        self.assertEqual(mon.check_once(low, now=t0 + 29 * 60), [])
        self.assertEqual(len(fired), 1)

        # Condition clears...
        self.assertEqual(mon.check_once(_healthy(), now=t0 + 300), [])
        # ...re-appears inside the cooldown -> still suppressed
        # (a flickering condition must not spam).
        self.assertEqual(mon.check_once(low, now=t0 + 600), [])
        self.assertEqual(len(fired), 1)
        # ...re-appears after the 30-min cooldown -> fires again.
        got = mon.check_once(low, now=t0 + 30 * 60 + 1)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "ram_low")
        self.assertEqual(len(fired), 2)

    def test_ram_thresholds(self):
        # 84-86% is his normal idle: must NOT alert.
        mon = SystemMonitor(on_alert=lambda a: None)
        self.assertEqual(
            mon.check_once(_reading(ram_free_mb=1100, ram_pct=86.0),
                           now=2_000_000.0), [])
        # Free < 500 MB alerts even at a modest percent.
        got = mon.check_once(_reading(ram_free_mb=400, ram_pct=60.0),
                             now=2_000_001.0)
        self.assertEqual([a["kind"] for a in got], ["ram_low"])
        # Used > 92% alerts even with plenty free (small machine).
        mon2 = SystemMonitor(on_alert=lambda a: None)
        got = mon2.check_once(_reading(ram_free_mb=4000, ram_pct=93.0),
                              now=2_000_002.0)
        self.assertEqual([a["kind"] for a in got], ["ram_low"])


class TestDiskAlert(unittest.TestCase):
    def test_nearly_full_disk_fires(self):
        fired = []
        mon = SystemMonitor(on_alert=fired.append)
        got = mon.check_once(
            _reading(disk_free_gb=5.0, disk_pct_free=4.0), now=3_000_000.0)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "disk_low")
        self.assertEqual(got[0]["level"], "critical")
        self.assertEqual(len(fired), 1)

    def test_disk_thresholds(self):
        mon = SystemMonitor(on_alert=lambda a: None)
        # 9 GB free but 40% free -> alerts on the GB rule.
        got = mon.check_once(_reading(disk_free_gb=9.0, disk_pct_free=40.0),
                             now=3_000_100.0)
        self.assertEqual([a["kind"] for a in got], ["disk_low"])
        # Plenty free on both rules -> silent.
        mon2 = SystemMonitor(on_alert=lambda a: None)
        self.assertEqual(
            mon2.check_once(_reading(disk_free_gb=50.0, disk_pct_free=20.0),
                            now=3_000_200.0), [])


class TestRealStatus(unittest.TestCase):
    def test_get_status_returns_real_numbers(self):
        mon = SystemMonitor()
        status = mon.get_status()
        for key in ("ram", "disk", "cpu", "battery",
                    "temp_c", "temp_available", "available"):
            self.assertIn(key, status)
        self.assertTrue(status["available"], "psutil must be present")
        self.assertGreater(status["ram"]["total_mb"], 0)
        self.assertGreater(status["ram"]["free_mb"], 0)
        self.assertGreaterEqual(status["ram"]["percent"], 0)
        self.assertLessEqual(status["ram"]["percent"], 100)
        self.assertGreater(status["disk"]["total_gb"], 0)
        self.assertGreaterEqual(status["disk"]["free_gb"], 0)
        self.assertGreaterEqual(status["cpu"]["percent"], 0)
        self.assertIs(status["cpu"]["sustained_high"], False)
        # Battery is None on this sandbox (no battery) - or a real dict.
        self.assertTrue(status["battery"] is None
                        or isinstance(status["battery"], dict))

    def test_no_fake_temperature_on_linux(self):
        # The honest path: no sensor here, so temp is None and the
        # module says so - it never invents a number.
        self.assertEqual(monitor_mod.read_cpu_temp(), (None, False))
        status = SystemMonitor().get_status()
        self.assertIs(status["temp_c"], None)
        self.assertIs(status["temp_available"], False)
        # ...and a missing temperature never produces a temp alert.
        mon = SystemMonitor(on_alert=lambda a: None)
        got = mon.check_once(_reading(temp_c=None, temp_available=False),
                             now=4_000_000.0)
        self.assertEqual([a["kind"] for a in got], [])

    def test_temp_sensor_reading_alerts(self):
        mon = SystemMonitor(on_alert=lambda a: None)
        got = mon.check_once(_reading(temp_c=95.0, temp_available=True),
                             now=4_000_100.0)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "temp_high")
        self.assertEqual(got[0]["level"], "critical")
        mon2 = SystemMonitor(on_alert=lambda a: None)
        got = mon2.check_once(_reading(temp_c=87.0, temp_available=True),
                              now=4_000_200.0)
        self.assertEqual([(a["kind"], a["level"]) for a in got],
                         [("temp_high", "warn")])


class TestSustainedCpuAndBattery(unittest.TestCase):
    def test_sustained_cpu_needs_a_full_window(self):
        mon = SystemMonitor(on_alert=lambda a: None)
        t0 = 5_000_000.0
        # Two quick high polls -> NOT sustained (the window is 5 min).
        self.assertEqual(
            mon.check_once(_reading(cpu=95.0), now=t0), [])
        self.assertEqual(
            mon.check_once(_reading(cpu=95.0), now=t0 + 60), [])
        # Keep it high until the window is covered -> alert fires.
        all_fired = []
        for step in (120, 180, 240, 300):
            all_fired += mon.check_once(_reading(cpu=95.0), now=t0 + step)
        self.assertEqual([a["kind"] for a in all_fired], ["cpu_high"])

    def test_battery_only_while_discharging(self):
        mon = SystemMonitor(on_alert=lambda a: None)
        low_discharging = {"percent": 15, "charging": False}
        got = mon.check_once(_reading(battery=low_discharging),
                             now=6_000_000.0)
        self.assertEqual([(a["kind"], a["level"]) for a in got],
                         [("battery_low", "warn")])
        # Same level, but charging -> silent.
        mon2 = SystemMonitor(on_alert=lambda a: None)
        self.assertEqual(mon2.check_once(
            _reading(battery={"percent": 15, "charging": True}),
            now=6_000_100.0), [])
        # Critical below 10% while discharging.
        mon3 = SystemMonitor(on_alert=lambda a: None)
        got = mon3.check_once(
            _reading(battery={"percent": 5, "charging": False}),
            now=6_000_200.0)
        self.assertEqual([(a["kind"], a["level"]) for a in got],
                         [("battery_low", "critical")])


class TestMonitorThread(unittest.TestCase):
    def test_start_stop(self):
        mon = SystemMonitor(
            settings={"monitor_interval_sec": 60},
            sample_fn=lambda: _reading())
        self.assertTrue(mon.start())
        self.assertTrue(mon.running)
        time.sleep(0.1)
        mon.stop()
        self.assertFalse(mon.running)

    def test_disabled_monitor_does_not_start(self):
        mon = SystemMonitor(settings={"monitor_enabled": False},
                            sample_fn=lambda: _reading())
        self.assertFalse(mon.start())
        self.assertFalse(mon.running)


class TestNotificationFilter(unittest.TestCase):
    def test_important_app_alerts(self):
        seen = []
        watcher = NotificationWatcher(on_important=seen.append)
        alert = watcher.on_notification("WhatsApp", "أحمد", "هلا")
        self.assertIsNotNone(alert)
        self.assertEqual(alert["kind"], "notification")
        self.assertEqual(alert["level"], "warn")
        self.assertEqual(alert["app"], "WhatsApp")
        self.assertEqual(len(seen), 1)
        # Mail app, English title.
        alert = watcher.on_notification("Microsoft Outlook",
                                        "Meeting at 3", "")
        self.assertIsNotNone(alert)
        self.assertEqual(len(seen), 2)

    def test_unlisted_app_is_ignored(self):
        seen = []
        watcher = NotificationWatcher(on_important=seen.append)
        self.assertIsNone(watcher.on_notification(
            "Candy Crush", "New level!", "Come play"))
        self.assertIsNone(watcher.on_notification(
            "Spotify", "Your Daily Mix is ready", ""))
        self.assertEqual(seen, [])

    def test_keyword_match_in_title_alerts(self):
        seen = []
        watcher = NotificationWatcher(on_important=seen.append)
        # Arabic keyword from an otherwise unlisted app.
        alert = watcher.on_notification("MyBank", "تنبيه عاجل",
                                        "تم خصم مبلغ من حسابك")
        self.assertIsNotNone(alert)
        self.assertEqual(len(seen), 1)
        # English keyword in the title.
        alert = watcher.on_notification("Random App",
                                        "URGENT: verify your password", "")
        self.assertIsNotNone(alert)
        self.assertEqual(len(seen), 2)

    def test_case_insensitive_matching(self):
        watcher = NotificationWatcher()
        self.assertIsNotNone(watcher.on_notification(
            "whatsapp", "hi", ""))
        self.assertIsNotNone(watcher.on_notification(
            "Some App", "SECURITY alert", ""))

    def test_custom_lists_via_settings(self):
        watcher = NotificationWatcher(settings={
            "notifications_important_apps": ["Telegram"],
            "notifications_keywords": ["invoice"]})
        self.assertIsNotNone(watcher.on_notification(
            "Telegram", "hi", ""))
        self.assertIsNone(watcher.on_notification(
            "WhatsApp", "hi", ""))
        self.assertIsNotNone(watcher.on_notification(
            "Other", "your invoice is ready", ""))

    def test_title_body_never_logged(self):
        # The privacy rule: only the app name + verdict may be logged.
        marker_title = "SECRET_MARKER_XYZ123 title"
        marker_body = "SECRET_BODY_ABC789 body"
        watcher = NotificationWatcher()
        watcher.on_notification("RandomApp", marker_title, marker_body)
        watcher.on_notification("WhatsApp", marker_title, marker_body)
        from settings_store import APP_DIR as _APP_DIR
        log_path = (_APP_DIR / "data" / "logs" /
                    f"memo-ai-{datetime.now():%Y-%m-%d}.log")
        if log_path.exists():
            text = log_path.read_text(encoding="utf-8")
            self.assertNotIn("SECRET_MARKER_XYZ123", text)
            self.assertNotIn("SECRET_BODY_ABC789", text)


class TestListenerUnavailableOnLinux(unittest.TestCase):
    def test_status_says_unavailable_honestly(self):
        status = get_listener_status()
        self.assertIs(status["available"], False)
        self.assertEqual(status["access"], "unavailable")
        self.assertTrue(status["detail"], "must explain why")

    def test_request_access_fails_cleanly(self):
        result = request_access()
        self.assertIs(result["ok"], False)
        self.assertEqual(result["access"], "unavailable")

    def test_show_toast_is_false_without_winrt(self):
        self.assertIs(show_toast("عنوان", "رسالة"), False)

    def test_watcher_start_refuses_without_winrt(self):
        watcher = NotificationWatcher()
        self.assertFalse(watcher.start())
        self.assertFalse(watcher.running)


if __name__ == "__main__":
    unittest.main()
