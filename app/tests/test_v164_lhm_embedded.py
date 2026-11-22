"""v1.6.4-rc.2 (W6) - embedded LibreHardwareMonitor CPU temperature.

What is proven here (all with a mocked subprocess.run - no Windows,
no real sensors, no built helper needed):
  * read_embedded_temp() returns the exact contract dict:
    {"celsius": float} on success, {"error": ..., "detail": str}
    on failure, with error in {not_available, needs_admin,
    helper_missing, timeout}.
  * helper missing -> helper_missing, never a raise, never a
    guessed temperature.
  * helper JSON errors (needs_admin / not_available) pass through.
  * a timeout -> timeout; a spawn failure -> not_available;
    garbage stdout / an implausible temperature / an unknown error
    value -> not_available.
  * the helper is spawned hidden on Windows (CREATE_NO_WINDOW, so
    no console flash) via hidden_spawn.
  * _find_helper() honours the LONA_LHM_HELPER override.

Run only this file:

    python -m pytest app/tests/test_v164_lhm_embedded.py -q
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


class TestReadEmbeddedTemp(unittest.TestCase):
    def setUp(self):
        # Point the helper lookup at a dummy path that never exists
        # as a file, so no test depends on a built helper; the
        # subprocess is mocked in every test anyway.
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

    def test_success_returns_float_celsius(self):
        with mock.patch.object(lhm.subprocess, "run",
                               _run_with(_proc('{"celsius": 51.5}\n'))):
            assert lhm.read_embedded_temp() == {"celsius": 51.5}

    def test_success_int_celsius_coerced_to_float(self):
        with mock.patch.object(lhm.subprocess, "run",
                               _run_with(_proc('{"celsius": 52}'))):
            assert lhm.read_embedded_temp() == {"celsius": 52.0}

    def test_helper_needs_admin_passes_through(self):
        out = ('{"error": "needs_admin", "detail": "CPU found but no '
               'temperature sensor returned a value; reading it may '
               'need administrator privileges"}')
        with mock.patch.object(lhm.subprocess, "run",
                               _run_with(_proc(out))):
            result = lhm.read_embedded_temp()
            assert result["error"] == "needs_admin"
            assert "administrator" in result["detail"]

    def test_helper_not_available_passes_through(self):
        out = '{"error": "not_available", "detail": "no CPU hardware"}'
        with mock.patch.object(lhm.subprocess, "run",
                               _run_with(_proc(out))):
            result = lhm.read_embedded_temp()
            assert result == {"error": "not_available",
                              "detail": "no CPU hardware"}

    def test_helper_missing_never_raises(self):
        with mock.patch.object(lhm, "_find_helper", return_value=None):
            result = lhm.read_embedded_temp()
            assert result["error"] == "helper_missing"
            assert isinstance(result["detail"], str)
            assert result["detail"]

    def test_timeout(self):
        with mock.patch.object(
                lhm.subprocess, "run",
                _run_with(subprocess.TimeoutExpired(
                    cmd="LhmReader.exe", timeout=10))):
            result = lhm.read_embedded_temp(timeout=10)
            assert result["error"] == "timeout"
            assert "10" in result["detail"]

    def test_spawn_failure_is_not_available(self):
        with mock.patch.object(lhm.subprocess, "run",
                               _run_with(OSError("boom"))):
            result = lhm.read_embedded_temp()
            assert result["error"] == "not_available"

    def test_garbage_stdout_is_not_available(self):
        with mock.patch.object(lhm.subprocess, "run",
                               _run_with(_proc("not json at all\n"))):
            result = lhm.read_embedded_temp()
            assert result["error"] == "not_available"

    def test_empty_stdout_is_not_available(self):
        with mock.patch.object(lhm.subprocess, "run",
                               _run_with(_proc(""))):
            result = lhm.read_embedded_temp()
            assert result["error"] == "not_available"

    def test_implausible_temperature_is_not_available(self):
        # 240 C is sensor garbage, never a temperature.
        with mock.patch.object(lhm.subprocess, "run",
                               _run_with(_proc('{"celsius": 240.0}'))):
            result = lhm.read_embedded_temp()
            assert result["error"] == "not_available"

    def test_unknown_error_value_maps_to_not_available(self):
        out = '{"error": "something_else", "detail": "???"}'
        with mock.patch.object(lhm.subprocess, "run",
                               _run_with(_proc(out))):
            result = lhm.read_embedded_temp()
            assert result["error"] == "not_available"

    def test_stray_lines_before_json_are_tolerated(self):
        out = "some warning line\n{\"celsius\": 48.0}\n"
        with mock.patch.object(lhm.subprocess, "run",
                               _run_with(_proc(out))):
            assert lhm.read_embedded_temp() == {"celsius": 48.0}

    def test_never_raises_on_anything(self):
        # Even a helper that raises a bizarre error inside run, and
        # even a run that returns a non-dict JSON value, must come
        # back as an error dict - never an exception.
        with mock.patch.object(lhm.subprocess, "run",
                               _run_with(RuntimeError("weird"))):
            assert lhm.read_embedded_temp()["error"] == "not_available"
        with mock.patch.object(lhm.subprocess, "run",
                               _run_with(_proc('[1, 2, 3]'))):
            assert lhm.read_embedded_temp()["error"] == "not_available"

    def test_spawn_is_hidden_on_windows(self):
        captured = {}

        def _capture(*args, **kwargs):
            captured.update(kwargs)
            return _proc('{"celsius": 50.0}')

        with mock.patch.object(lhm.subprocess, "run",
                               side_effect=_capture):
            lhm.read_embedded_temp()
        if sys.platform == "win32":
            assert captured.get("creationflags") == \
                hidden_spawn.CREATE_NO_WINDOW
        else:
            assert "creationflags" not in captured
        assert captured.get("timeout") == 10.0
        assert captured.get("stdin") == subprocess.DEVNULL


class TestFindHelper(unittest.TestCase):
    def test_env_override_locates_helper(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".exe",
                                         delete=False) as f:
            path = Path(f.name)
        try:
            with mock.patch.dict(os.environ,
                                 {"LONA_LHM_HELPER": str(path)}):
                assert lhm._find_helper() == path
        finally:
            path.unlink(missing_ok=True)

    def test_env_override_to_missing_file_falls_through(self):
        # A bogus override must not crash the lookup; with no built
        # helper on this machine the result is None.
        with mock.patch.dict(os.environ, {"LONA_LHM_HELPER":
                                          str(APP_DIR / "nope.exe")}):
            with mock.patch.object(
                    lhm, "_helper_candidates",
                    return_value=[APP_DIR / "nope.exe"]):
                assert lhm._find_helper() is None


if __name__ == "__main__":
    unittest.main()
