"""v1.6.4-rc.2 (W7) - fan RPM via the embedded LibreHardwareMonitor.

What is proven here (all with a mocked subprocess.run - no Windows,
no real sensors, no built helper needed):
  * the helper JSON carries fans on success:
    {"celsius": 51.5, "fans": [{"name": "Fan #1", "rpm": 2400}]};
    read_embedded_temp() still returns EXACTLY {"celsius": ...}
    (its contract is unchanged - the fans live in the fans API).
  * read_embedded_fans() returns the real RPM list; [] when the
    helper reports no fans (unsupported chip), when the helper is
    missing, on timeout, or on garbage output - never a raise,
    never a faked RPM. Malformed fan entries are dropped one by
    one; out-of-range RPMs are sensor garbage, never a speed.
  * ONE helper invocation serves temp + fans: temp-then-fans and
    fans-then-fans each spawn the helper exactly once.
  * monitor.py carries the fans field: get_status() and
    get_device_overview() report the fans from an injected sampler
    (and [] when the reading has none); _sample_slow() carries the
    fans cached with the temperature probe; _sanitize_fans() drops
    malformed entries and never invents a speed.

NOT proven here (needs real Windows + a supported chip): the
LhmReader.exe .NET helper actually enumerating Fan sensors, and
the device tab painting real RPMs on Mohamed's Dell Latitude 5401
— the chip may report no fan sensors there, in which case the tab
simply shows nothing for fans. Those are the field tests.

Run only this file:

    python -m pytest app/tests/test_v164_lhm_fans.py -q
"""
import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import hidden_spawn  # noqa: E402
import lhm_embedded as lhm  # noqa: E402
import monitor as monitor_mod  # noqa: E402
from monitor import SystemMonitor  # noqa: E402

FANS_JSON = ('{"celsius": 51.5, "fans": [{"name": "Fan #1", "rpm": 2400}, '
             '{"name": "Fan #2", "rpm": 1850.5}]}')


def _proc(stdout, returncode=0, stderr=""):
    return types.SimpleNamespace(
        stdout=stdout, stderr=stderr, returncode=returncode)


def _run_with(proc_or_exc):
    """subprocess.run replacement returning proc_or_exc (or raising
    it when it is an exception)."""
    def _fake(*args, **kwargs):
        if isinstance(proc_or_exc, BaseException):
            raise proc_or_exc
        return proc_or_exc
    return _fake


class TestFanParsing(unittest.TestCase):
    def test_fans_present_in_helper_json(self):
        fans = lhm._extract_fans_from_stdout(FANS_JSON + "\n")
        assert fans == [{"name": "Fan #1", "rpm": 2400.0},
                        {"name": "Fan #2", "rpm": 1850.5}]

    def test_fans_absent_is_empty_list(self):
        assert lhm._extract_fans_from_stdout('{"celsius": 51.5}\n') == []
        assert lhm._extract_fans_from_stdout(
            '{"celsius": 51.5, "fans": []}\n') == []

    def test_error_json_has_no_fans(self):
        out = '{"error": "not_available", "detail": "no CPU hardware"}'
        assert lhm._extract_fans_from_stdout(out) == []

    def test_garbage_stdout_has_no_fans(self):
        assert lhm._extract_fans_from_stdout("not json at all\n") == []
        assert lhm._extract_fans_from_stdout("") == []

    def test_malformed_entries_are_dropped_one_by_one(self):
        out = ('{"celsius": 50.0, "fans": ['
               '{"name": "Fan #1", "rpm": 2400}, '
               '{"name": "Fan #2", "rpm": "fast"}, '
               '{"name": "Fan #3"}, '
               '{"rpm": 1200}, '
               '"not a dict", '
               '{"name": "Fan #4", "rpm": -5}, '
               '{"name": "Fan #5", "rpm": 99999}, '
               '{"name": "Fan #6", "rpm": true}]}')
        fans = lhm._extract_fans_from_stdout(out)
        # Only the two real readings survive; the nameless one gets
        # the plain "Fan" name. No faked RPM anywhere.
        assert fans == [{"name": "Fan #1", "rpm": 2400.0},
                        {"name": "Fan", "rpm": 1200.0}]

    def test_stray_lines_before_json_are_tolerated(self):
        out = "some warning line\n" + FANS_JSON + "\n"
        assert len(lhm._extract_fans_from_stdout(out)) == 2


class TestReadEmbeddedFans(unittest.TestCase):
    def setUp(self):
        lhm._reset_reading_cache()
        self._env = mock.patch.dict(
            os.environ, {"LONA_LHM_HELPER": str(APP_DIR / "no-such-helper.exe")})
        self._env.start()
        self._find = mock.patch.object(
            lhm, "_find_helper",
            return_value=Path(APP_DIR / "no-such-helper.exe"))
        self._find.start()

    def tearDown(self):
        self._find.stop()
        self._env.stop()
        lhm._reset_reading_cache()

    def test_fans_returned(self):
        with mock.patch.object(lhm.subprocess, "run",
                               _run_with(_proc(FANS_JSON))):
            assert lhm.read_embedded_fans() == [
                {"name": "Fan #1", "rpm": 2400.0},
                {"name": "Fan #2", "rpm": 1850.5}]

    def test_no_fans_is_empty_list(self):
        with mock.patch.object(lhm.subprocess, "run",
                               _run_with(_proc('{"celsius": 51.5}'))):
            assert lhm.read_embedded_fans() == []

    def test_temp_contract_unchanged_when_fans_present(self):
        # read_embedded_temp() returns EXACTLY {"celsius": ...} —
        # the fans live in the fans API, not in the temp dict.
        with mock.patch.object(lhm.subprocess, "run",
                               _run_with(_proc(FANS_JSON))):
            assert lhm.read_embedded_temp() == {"celsius": 51.5}

    def test_helper_missing_is_empty_list_never_raises(self):
        with mock.patch.object(lhm, "_find_helper", return_value=None):
            assert lhm.read_embedded_fans() == []

    def test_timeout_is_empty_list_never_raises(self):
        with mock.patch.object(
                lhm.subprocess, "run",
                _run_with(subprocess.TimeoutExpired(
                    cmd="LhmReader.exe", timeout=10))):
            assert lhm.read_embedded_fans() == []

    def test_garbage_stdout_is_empty_list(self):
        with mock.patch.object(lhm.subprocess, "run",
                               _run_with(_proc("not json at all\n"))):
            assert lhm.read_embedded_fans() == []

    def test_error_json_is_empty_list(self):
        out = '{"error": "needs_admin", "detail": "needs admin"}'
        with mock.patch.object(lhm.subprocess, "run",
                               _run_with(_proc(out))):
            assert lhm.read_embedded_fans() == []

    def test_never_raises_on_anything(self):
        with mock.patch.object(lhm.subprocess, "run",
                               _run_with(RuntimeError("weird"))):
            assert lhm.read_embedded_fans() == []
        with mock.patch.object(lhm.subprocess, "run",
                               _run_with(_proc('[1, 2, 3]'))):
            assert lhm.read_embedded_fans() == []


class TestOneHelperInvocation(unittest.TestCase):
    """Temp + fans share ONE helper invocation (the 60 s cache)."""

    def setUp(self):
        lhm._reset_reading_cache()
        self._env = mock.patch.dict(
            os.environ, {"LONA_LHM_HELPER": str(APP_DIR / "no-such-helper.exe")})
        self._env.start()
        self._find = mock.patch.object(
            lhm, "_find_helper",
            return_value=Path(APP_DIR / "no-such-helper.exe"))
        self._find.start()

    def tearDown(self):
        self._find.stop()
        self._env.stop()
        lhm._reset_reading_cache()

    def _counting_run(self, stdout):
        calls = {"n": 0}

        def _fake(*args, **kwargs):
            calls["n"] += 1
            return _proc(stdout)
        return calls, _fake

    def test_temp_then_fans_spawns_once(self):
        calls, fake = self._counting_run(FANS_JSON)
        with mock.patch.object(lhm.subprocess, "run", side_effect=fake):
            assert lhm.read_embedded_temp() == {"celsius": 51.5}
            assert lhm.read_embedded_fans() == [
                {"name": "Fan #1", "rpm": 2400.0},
                {"name": "Fan #2", "rpm": 1850.5}]
        assert calls["n"] == 1

    def test_fans_then_fans_spawns_once(self):
        calls, fake = self._counting_run(FANS_JSON)
        with mock.patch.object(lhm.subprocess, "run", side_effect=fake):
            assert len(lhm.read_embedded_fans()) == 2
            assert len(lhm.read_embedded_fans()) == 2
        assert calls["n"] == 1

    def test_reading_returns_both_from_one_spawn(self):
        calls, fake = self._counting_run(FANS_JSON)
        with mock.patch.object(lhm.subprocess, "run", side_effect=fake):
            reading = lhm.read_embedded_reading()
            assert reading["temp"] == {"celsius": 51.5}
            assert reading["fans"] == [
                {"name": "Fan #1", "rpm": 2400.0},
                {"name": "Fan #2", "rpm": 1850.5}]
        assert calls["n"] == 1


class TestMonitorFans(unittest.TestCase):
    def _reading(self, fans=None):
        reading = {
            "ram": {"total_mb": 8192, "used_mb": 4096,
                    "free_mb": 4096, "percent": 50.0},
            "disk": {"free_gb": 100.0, "total_gb": 256.0,
                     "percent_free": 80.0},
            "cpu_percent": 10.0,
            "battery": None,
            "temp_c": 51.5,
            "temp_available": True,
        }
        if fans is not None:
            reading["fans"] = fans
        return reading

    def test_get_status_carries_fans(self):
        fans = [{"name": "Fan #1", "rpm": 2400.0}]
        mon = SystemMonitor(sample_fn=lambda: self._reading(fans))
        assert mon.get_status()["fans"] == fans

    def test_get_device_overview_carries_fans(self):
        fans = [{"name": "Fan #1", "rpm": 2400.0},
                {"name": "Fan #2", "rpm": 1850.0}]
        mon = SystemMonitor(sample_fn=lambda: self._reading(fans))
        assert mon.get_device_overview()["fans"] == fans

    def test_no_fans_in_reading_is_empty_list(self):
        mon = SystemMonitor(sample_fn=lambda: self._reading())
        assert mon.get_status()["fans"] == []
        assert mon.get_device_overview()["fans"] == []

    def test_get_status_sanitizes_fans(self):
        # A malformed entry from an injected sampler never reaches
        # the card as a fake RPM.
        fans = [{"name": "Fan #1", "rpm": 2400},
                {"name": "Fan #2", "rpm": "fast"},
                {"name": "Fan #3", "rpm": 99999}]
        mon = SystemMonitor(sample_fn=lambda: self._reading(fans))
        assert mon.get_status()["fans"] == [
            {"name": "Fan #1", "rpm": 2400.0}]

    def test_sanitize_fans_unit(self):
        assert monitor_mod._sanitize_fans(None) == []
        assert monitor_mod._sanitize_fans("nope") == []
        assert monitor_mod._sanitize_fans(
            [{"name": "Fan #1", "rpm": 0}]) == [
                {"name": "Fan #1", "rpm": 0.0}]
        # A stopped fan (0 RPM) is a real value and is kept.
        assert monitor_mod._sanitize_fans(
            [{"rpm": float("nan")}]) == []
        assert monitor_mod._sanitize_fans(
            [{"rpm": float("inf")}]) == []

    def test_slow_sample_carries_fans_cached_with_temp(self):
        # The fans cached with the temperature probe ride the slow
        # sample; no probe runs here (the cache is already fresh).
        now = [1_000_000.0]
        mon = SystemMonitor(clock=lambda: now[0])
        fans = [{"name": "Fan #1", "rpm": 2400.0}]
        mon._temp_cache.update(
            {"at": now[0], "temp_c": 51.5, "available": True,
             "status": monitor_mod._temp_status(True, 51.5, "ok", ""),
             "fans": fans})
        slow = mon._sample_slow()
        assert slow["fans"] == fans
        assert slow["temp_c"] == 51.5

    def test_temp_probe_refreshes_fans_from_embedded_reader(self):
        # One probe refreshes temp AND fans together; a second call
        # inside the probe interval re-probes neither.
        now = [1_000_000.0]
        mon = SystemMonitor(clock=lambda: now[0])
        fans = [{"name": "Fan #1", "rpm": 2400.0}]
        probe_calls = {"temp": 0, "fans": 0}
        status = monitor_mod._temp_status(True, 51.5, "ok", "")

        def _fake_status_capped():
            probe_calls["temp"] += 1
            return dict(status)

        def _fake_fans():
            probe_calls["fans"] += 1
            return list(fans)

        with mock.patch.object(monitor_mod, "_read_temp_status_capped",
                               side_effect=_fake_status_capped), \
                mock.patch.object(monitor_mod, "_read_embedded_fans",
                                  side_effect=_fake_fans):
            temp_c, available = mon._read_temp_cached()
            assert (temp_c, available) == (51.5, True)
            assert mon._temp_cache["fans"] == fans
            # Second call inside the 600 s probe interval: cached.
            mon._read_temp_cached()
        assert probe_calls == {"temp": 1, "fans": 1}


if __name__ == "__main__":
    unittest.main()
