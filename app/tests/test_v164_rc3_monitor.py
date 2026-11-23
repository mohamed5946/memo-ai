# LONA v1.6.4-rc.3 - monitoring principles, LONA-sized.
# (Micrometer -> Prometheus -> Grafana -> Loki -> Alertmanager ideas,
# implemented in the existing LONA code -- no server stack.)
#
#   python -m pytest app/tests/test_v164_rc3_monitor.py -v
#   (also runnable under unittest: plain unittest.TestCase classes)
#
# What is proven here:
#   (a) LOG REDACTION: a fake Groq key / OpenAI-style key / password /
#       Bearer token written through the REAL log path never reaches
#       the file, the returned entry, read_recent() or log_text() --
#       only *** is stored or shown. A secret planted in a file by
#       hand (pre-redaction entry) is masked on READ too.
#   (b) ALERT GROUPING + RESOLVED: sustained CPU + high temp together
#       fire ONE grouped alert naming the likely cause; RAM + disk
#       together fire ONE grouped alert; clearing a condition sends
#       exactly ONE resolved notice (through on_alert, so it reaches
#       the in-app list AND the island toast via alert_to_toast);
#       the 30-min cooldown is respected by groups and members alike.
#   (c) LOG RETENTION: trim drops entries older than 30 days by their
#       own timestamps, keeps recent + undated entries, and never
#       empties a file entirely.
#   (d) NAMED ALERT RULES: every rule has a name + severity;
#       token-limit-near fires on the MEASURED remaining (warn and
#       critical); slow-reply fires on a MEASURED long elapsed time;
#       the rules are listed in the Settings health section.
#
# NOT proven here: the live wiring in main.py (the coordinator owns
# it) -- check_token_usage needs brain.get_token_usage passed as
# token_fn, and check_reply_time needs the elapsed seconds measured
# around brain.ask_with_tools. Both integration points are named in
# the rc.3 report; the rule functions themselves are proven here.

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import logger as logger_mod  # noqa: E402
import monitor as monitor_mod  # noqa: E402
from monitor import SystemMonitor, list_alert_rules  # noqa: E402


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


def _token_usage(groq_remaining=None, groq_has_data=True,
                 gemini_remaining=None, gemini_has_data=False):
    """A brain.get_token_usage() shaped dict (measured values only)."""
    return {
        "groq": {"used": 0, "limit": 8000, "remaining": groq_remaining,
                 "reset_in_seconds": None, "estimated": False,
                 "has_data": groq_has_data},
        "gemini": {"used": 0, "limit": None,
                   "remaining": gemini_remaining,
                   "reset_in_seconds": None, "estimated": True,
                   "has_data": gemini_has_data},
    }


# ---------------------------------------------------------------------------
# (a) log redaction
# ---------------------------------------------------------------------------

class TestLogRedaction(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._patch = mock.patch.object(
            logger_mod, "_log_dir", return_value=self.tmp)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _log_file_text(self):
        text = ""
        for path in sorted(self.tmp.glob("memo-ai-*.log")):
            text += path.read_text(encoding="utf-8")
        return text

    def test_fake_key_token_password_redacted_on_write(self):
        fake_groq = "gsk_FAKEKEY1234567890abcdef"
        fake_openai = "sk-FAKEOPENAIKEY1234567890"
        fake_password = "Hunter2FAKE99"
        fake_bearer = "abcdefgh12345678FAKE"
        entry = logger_mod.info(
            "keys",
            f"groq key api_key={fake_groq} and openai {fake_openai} "
            f"and password={fake_password} "
            f"and Authorization: Bearer {fake_bearer}")
        stored = self._log_file_text()
        for secret in (fake_groq, fake_openai, fake_password, fake_bearer):
            self.assertNotIn(secret, stored)
            self.assertNotIn(secret, entry["message"])
            self.assertNotIn(secret, logger_mod.log_text(10))
        for returned in logger_mod.read_recent(10):
            self.assertNotIn(fake_groq, str(returned))
        self.assertIn("***", stored)
        self.assertIn("***", logger_mod.log_text(10))

    def test_json_shaped_password_and_google_key_redacted(self):
        # Exactly AIza + 35 chars: a real Google API key shape.
        fake_google = "AIzaFAKEGOOGLEKEY1234567890abcde1234567"
        entry = logger_mod.info(
            "keys", f'{{"password": "{fake_google}-pw", '
                    f'"key": "{fake_google}"}}')
        stored = self._log_file_text()
        self.assertNotIn(fake_google, stored)
        self.assertNotIn(fake_google, entry["message"])
        # The line stays valid JSON with the value masked.
        line = stored.strip().splitlines()[-1]
        parsed = json.loads(line)
        self.assertIn("***", parsed["message"])

    def test_secret_planted_in_file_is_masked_on_read(self):
        # An entry written BEFORE redaction existed (or by another
        # writer) must still never reach the in-app viewer.
        fake = "gsk_PLANTEDFAKEKEY1234567890"
        path = self.tmp / "memo-ai-2026-11-23.log"
        path.write_text(json.dumps(
            {"time": "2026-11-23 10:00:00", "level": "info",
             "category": "keys",
             "message": f"leaked key {fake}"}) + "\n",
            encoding="utf-8")
        self.assertNotIn(fake, logger_mod.log_text(10))
        entries = logger_mod.read_recent(10)
        self.assertTrue(entries)
        self.assertNotIn(fake, str(entries))
        self.assertIn("***", logger_mod.log_text(10))

    def test_normal_log_text_is_not_mangled(self):
        entry = logger_mod.info(
            "monitor", "RAM reading failed: timeout after 5s")
        self.assertEqual(entry["message"],
                         "RAM reading failed: timeout after 5s")
        entry2 = logger_mod.info("app", "password check passed")
        self.assertEqual(entry2["message"], "password check passed")


# ---------------------------------------------------------------------------
# (b) alert grouping + resolved
# ---------------------------------------------------------------------------

class TestAlertGrouping(unittest.TestCase):
    def test_cpu_plus_temp_fire_one_grouped_alert(self):
        fired = []
        mon = SystemMonitor(on_alert=fired.append)
        t0 = 1_000_000.0
        # CPU was already sustained (history seeded, no alert fired
        # yet -- the temperature sensor had not answered), then the
        # temp jumps high in the same poll: ONE grouped alert.
        for step in (0, 60, 120, 180, 240, 300):
            mon._record_cpu(95.0, now=t0 + step)
        got = mon.check_once(
            _reading(cpu=95.0, temp_c=88.0, temp_available=True),
            now=t0 + 300)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "cpu_temp")
        self.assertEqual(got[0]["level"], "warn")
        self.assertEqual(got[0]["grouped_from"],
                         ["cpu_high", "temp_high"])
        # The cause is named as LIKELY, never as a certainty.
        self.assertIn("الغالب", got[0]["message_ar"])
        self.assertEqual(len(fired), 1)
        self.assertEqual([a["kind"] for a in got], ["cpu_temp"])
        # The grouped alert also flows to the island toast.
        toast = monitor_mod.alert_to_toast(got[0])
        self.assertTrue(toast["title"])
        self.assertTrue(toast["body"])

    def test_grouped_alert_does_not_repeat_while_holding(self):
        fired = []
        mon = SystemMonitor(on_alert=fired.append)
        t0 = 2_000_000.0
        for step in (0, 60, 120, 180, 240, 300):
            mon._record_cpu(95.0, now=t0 + step)
        mon.check_once(_reading(cpu=95.0, temp_c=88.0,
                                temp_available=True), now=t0 + 300)
        self.assertEqual(len(fired), 1)
        # Same conditions, next poll: no duplicate, no individual
        # cpu_high / temp_high leaking out either.
        got = mon.check_once(_reading(cpu=95.0, temp_c=88.0,
                                      temp_available=True), now=t0 + 360)
        self.assertEqual(got, [])
        self.assertEqual(len(fired), 1)

    def test_group_resolved_once_on_clear_then_cooldown(self):
        fired = []
        mon = SystemMonitor(on_alert=fired.append)
        t0 = 3_000_000.0
        for step in (0, 60, 120, 180, 240, 300):
            mon._record_cpu(95.0, now=t0 + step)
        mon.check_once(_reading(cpu=95.0, temp_c=88.0,
                                temp_available=True), now=t0 + 300)
        self.assertEqual(len(fired), 1)
        # Conditions clear: exactly ONE resolved notice, through
        # on_alert (in-app + toast), NOT in the check_once return.
        got = mon.check_once(_healthy(cpu=10.0, temp_c=70.0,
                                      temp_available=True), now=t0 + 360)
        self.assertEqual(got, [])
        self.assertEqual(len(fired), 2)
        resolved = fired[1]
        self.assertEqual(resolved["kind"], "cpu_temp")
        self.assertTrue(resolved.get("resolved"))
        self.assertEqual(resolved["level"], "info")
        toast = monitor_mod.alert_to_toast(resolved)
        self.assertTrue(toast["title"])
        self.assertTrue(toast["body"])
        # A second healthy poll: the resolved notice does NOT repeat.
        mon.check_once(_healthy(cpu=10.0, temp_c=70.0,
                                temp_available=True), now=t0 + 420)
        self.assertEqual(len(fired), 2)
        # Re-appears inside the 30-min cooldown: still suppressed --
        # and the members cannot bypass the cooldown individually.
        for step in (600, 660, 720, 780, 840, 900):
            mon._record_cpu(95.0, now=t0 + step)
        got = mon.check_once(_reading(cpu=95.0, temp_c=88.0,
                                      temp_available=True), now=t0 + 900)
        self.assertEqual(got, [])
        self.assertEqual(len(fired), 2)
        # Re-appears after the cooldown: fires again, once.
        for step in (1801, 1861, 1921, 1981, 2041, 2101):
            mon._record_cpu(95.0, now=t0 + step)
        got = mon.check_once(_reading(cpu=95.0, temp_c=88.0,
                                      temp_available=True), now=t0 + 2101)
        self.assertEqual([a["kind"] for a in got], ["cpu_temp"])
        self.assertEqual(len(fired), 3)

    def test_ram_plus_disk_fire_one_grouped_alert(self):
        fired = []
        mon = SystemMonitor(on_alert=fired.append)
        got = mon.check_once(
            _reading(ram_free_mb=300, ram_pct=95.0,
                     disk_free_gb=5.0, disk_pct_free=4.0),
            now=4_000_000.0)
        self.assertEqual([a["kind"] for a in got], ["ram_disk"])
        self.assertEqual(got[0]["grouped_from"],
                         ["ram_low", "disk_low"])
        self.assertEqual(len(fired), 1)
        # Clearing both sends exactly one resolved notice.
        got = mon.check_once(_healthy(), now=4_000_060.0)
        self.assertEqual(got, [])
        self.assertEqual(len(fired), 2)
        self.assertEqual(fired[1]["kind"], "ram_disk")
        self.assertTrue(fired[1].get("resolved"))

    def test_single_conditions_still_fire_individually(self):
        # Grouping only kicks in when BOTH members are bad together.
        mon = SystemMonitor(on_alert=lambda a: None)
        got = mon.check_once(_reading(ram_free_mb=300, ram_pct=95.0),
                             now=5_000_000.0)
        self.assertEqual([a["kind"] for a in got], ["ram_low"])
        mon2 = SystemMonitor(on_alert=lambda a: None)
        got = mon2.check_once(
            _reading(temp_c=87.0, temp_available=True),
            now=5_000_100.0)
        self.assertEqual([a["kind"] for a in got], ["temp_high"])


# ---------------------------------------------------------------------------
# (c) log retention
# ---------------------------------------------------------------------------

class TestLogRetention(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._patch = mock.patch.object(
            logger_mod, "_log_dir", return_value=self.tmp)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.now = datetime(2026, 11, 23, 12, 0, 0)

    def _entry(self, when, message):
        return json.dumps({"time": when, "level": "info",
                           "category": "app", "message": message},
                          ensure_ascii=False)

    def test_trim_drops_old_keeps_recent_and_undated(self):
        old = (self.now - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
        recent = self.now.strftime("%Y-%m-%d %H:%M:%S")
        path = self.tmp / "memo-ai-2026-10-01.log"
        path.write_text(
            self._entry(old, "OLDMSG") + "\n"
            + self._entry(recent, "NEWMSG") + "\n"
            + "not json at all UNDATEDMSG\n",
            encoding="utf-8")
        result = logger_mod.trim_old_logs(now=self.now)
        self.assertEqual(result["entries_dropped"], 1)
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("OLDMSG", text)
        self.assertIn("NEWMSG", text)
        self.assertIn("UNDATEDMSG", text)

    def test_trim_never_empties_a_file(self):
        old = (self.now - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
        path = self.tmp / "memo-ai-2026-10-02.log"
        path.write_text(
            self._entry(old, "ONLYOLD1") + "\n"
            + self._entry(old, "ONLYOLD2") + "\n",
            encoding="utf-8")
        result = logger_mod.trim_old_logs(now=self.now)
        self.assertEqual(result["files_trimmed"], 1)
        lines = [ln for ln in path.read_text(
            encoding="utf-8").splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("ONLYOLD2", lines[0])

    def test_trim_leaves_fresh_files_untouched(self):
        recent = self.now.strftime("%Y-%m-%d %H:%M:%S")
        path = self.tmp / "memo-ai-2026-11-23.log"
        path.write_text(self._entry(recent, "FRESHMSG") + "\n",
                        encoding="utf-8")
        before = path.read_text(encoding="utf-8")
        result = logger_mod.trim_old_logs(now=self.now)
        self.assertEqual(result, {"files_trimmed": 0,
                                  "entries_dropped": 0})
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_log_write_trims_once_per_process(self):
        old = (self.now - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
        path = self.tmp / "memo-ai-2026-10-03.log"
        path.write_text(self._entry(old, "OLDMSG") + "\n"
                        + self._entry(self.now.strftime("%Y-%m-%d %H:%M:%S"),
                                      "NEWMSG") + "\n",
                        encoding="utf-8")
        logger_mod._trim_done = False
        logger_mod.info("app", "a fresh write triggers the trim")
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("OLDMSG", text)
        self.assertIn("NEWMSG", text)


# ---------------------------------------------------------------------------
# (d) named alert rules with severity
# ---------------------------------------------------------------------------

class TestNamedAlertRules(unittest.TestCase):
    def test_every_rule_has_name_and_severity(self):
        rules = list_alert_rules()
        self.assertGreaterEqual(len(rules), 9)
        names = set()
        for rule in rules:
            self.assertTrue(rule["name"], "rule needs a machine name")
            self.assertTrue(rule["name_ar"], "rule needs an Arabic label")
            self.assertIn(rule["severity"], ("warning", "critical"))
            self.assertIn(rule["severity_ar"], ("تحذير", "حرج"))
            self.assertTrue(rule["threshold"])
            self.assertTrue(rule["threshold_ar"])
            self.assertIn("critical_when", rule)
            self.assertIn("grouped_from", rule)
            names.add(rule["name"])
        self.assertEqual(len(names), len(rules), "rule names must be unique")
        for expected in ("ram-low", "disk-low", "temp-high", "cpu-high",
                         "battery-low", "cpu-temp", "ram-disk",
                         "token-limit-near", "slow-reply"):
            self.assertIn(expected, names)

    def test_rules_reflect_live_thresholds(self):
        rules = list_alert_rules(
            {"monitor_token_warn_remaining": 500,
             "monitor_slow_reply_sec": 45})
        by_name = {r["name"]: r for r in rules}
        self.assertIn("500", by_name["token-limit-near"]["threshold_ar"])
        self.assertIn("45", by_name["slow-reply"]["threshold_ar"])

    def test_token_limit_near_fires_on_measured_remaining(self):
        fired = []
        mon = SystemMonitor(on_alert=fired.append)
        # Groq: 800 left of the 8000/min window -> warning.
        got = mon.check_token_usage(
            _token_usage(groq_remaining=800), now=6_000_000.0)
        self.assertEqual([(a["kind"], a["level"]) for a in got],
                         [("token_low_groq", "warn")])
        self.assertEqual(len(fired), 1)
        # Still low: no duplicate while the condition holds.
        got = mon.check_token_usage(
            _token_usage(groq_remaining=700), now=6_000_060.0)
        self.assertEqual(got, [])
        self.assertEqual(len(fired), 1)
        # Recovered: exactly one resolved notice.
        got = mon.check_token_usage(
            _token_usage(groq_remaining=5000), now=6_000_120.0)
        self.assertEqual(got, [])
        self.assertEqual(len(fired), 2)
        self.assertTrue(fired[1].get("resolved"))
        self.assertEqual(fired[1]["kind"], "token_low_groq")

    def test_token_limit_near_critical_at_lower_threshold(self):
        mon = SystemMonitor(on_alert=lambda a: None)
        got = mon.check_token_usage(
            _token_usage(groq_remaining=150), now=6_100_000.0)
        self.assertEqual([(a["kind"], a["level"]) for a in got],
                         [("token_low_groq", "critical")])

    def test_token_rule_never_fires_without_measured_remaining(self):
        # Gemini has no per-minute limit in the tracker: remaining
        # None / has_data False must stay silent, never a guess.
        mon = SystemMonitor(on_alert=lambda a: None)
        self.assertEqual(mon.check_token_usage(
            _token_usage(groq_remaining=None, groq_has_data=False),
            now=6_200_000.0), [])
        self.assertEqual(mon.check_token_usage(
            _token_usage(groq_remaining=5000,
                         gemini_remaining=None, gemini_has_data=True),
            now=6_200_060.0), [])
        # ...but if a tracker ever DOES report a Gemini remaining,
        # the same rule fires for it (the rule is generic). 500 left
        # is under the 1000 warn threshold but above the 200
        # critical one, so this fires as a warning.
        got = mon.check_token_usage(
            _token_usage(groq_remaining=5000,
                         gemini_remaining=500, gemini_has_data=True),
            now=6_200_120.0)
        self.assertEqual([(a["kind"], a["level"]) for a in got],
                         [("token_low_gemini", "warn")])

    def test_token_rule_via_check_once_token_fn(self):
        # The polling hook: a monitor built with token_fn evaluates
        # the rule on every poll, on the measured remaining.
        fired = []
        mon = SystemMonitor(
            on_alert=fired.append,
            token_fn=lambda: _token_usage(groq_remaining=800))
        got = mon.check_once(_healthy(), now=6_300_000.0)
        self.assertEqual([a["kind"] for a in got], ["token_low_groq"])
        self.assertEqual(len(fired), 1)

    def test_slow_reply_fires_on_long_measured_elapsed(self):
        fired = []
        mon = SystemMonitor(on_alert=fired.append)
        got = mon.check_reply_time(45.0, now=7_000_000.0)
        self.assertEqual([(a["kind"], a["level"]) for a in got],
                         [("slow_reply", "warn")])
        self.assertIn("45", got[0]["message_ar"])
        self.assertEqual(len(fired), 1)
        # Another slow reply while still active: no duplicate.
        self.assertEqual(mon.check_reply_time(60.0, now=7_000_060.0), [])
        self.assertEqual(len(fired), 1)
        # A fast reply after it: exactly one resolved notice.
        self.assertEqual(mon.check_reply_time(4.0, now=7_000_120.0), [])
        self.assertEqual(len(fired), 2)
        self.assertTrue(fired[1].get("resolved"))
        self.assertEqual(fired[1]["kind"], "slow_reply")
        # ...and it does not repeat.
        self.assertEqual(mon.check_reply_time(3.0, now=7_000_180.0), [])
        self.assertEqual(len(fired), 2)

    def test_slow_reply_threshold_boundary(self):
        # Exactly at the 30 s threshold: not slow (strictly greater).
        mon = SystemMonitor(on_alert=lambda a: None)
        self.assertEqual(mon.check_reply_time(30.0, now=7_100_000.0), [])
        self.assertEqual(
            [a["kind"] for a in
             mon.check_reply_time(30.5, now=7_100_060.0)],
            ["slow_reply"])

    def test_rules_listed_in_health_section(self):
        # The Settings -> health section renders every rule with its
        # Arabic name + severity + threshold (no emoji anywhere).
        html = (APP_DIR / "ui" / "index.html").read_text(encoding="utf-8")
        self.assertIn("قواعد التنبيه", html)
        for rule in list_alert_rules():
            self.assertIn(rule["name_ar"], html,
                          f"rule {rule['name']} missing from health section")
        self.assertIn("تحذير", html)
        self.assertIn("توكنات المحرك قربت تخلص", html)
        self.assertIn("الرد بطيء", html)


if __name__ == "__main__":
    unittest.main()
