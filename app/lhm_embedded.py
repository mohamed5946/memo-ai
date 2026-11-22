"""Embedded LibreHardwareMonitor CPU temperature (LONA v1.6.4-rc.2).

Reads the CPU temperature straight from the bundled LhmReader.exe
helper (tools/LhmReader/, built from LibreHardwareMonitorLib), so
LONA needs NO separate LibreHardwareMonitor app installed and never
has an "is LHM running?" failure mode. The existing WMI path
(root\\LibreHardwareMonitor while LHM runs, then thermal zones)
stays as the fallback in app/monitor.py — this module only provides
the embedded read, which monitor.py tries first.

Contract (consumed by monitor.py — do not change the shape):

    read_embedded_temp() -> {"celsius": float} on success, or
    {"error": "not_available"|"needs_admin"|"helper_missing"|"timeout",
     "detail": str} on failure.

    read_embedded_fans() -> [{"name": str, "rpm": float}] — the
    Fan-type sensors from the same helper run, [] when the chip
    reports no fan sensors (unsupported laptop EC), the helper is
    missing, or anything fails. Never a faked RPM.

    Temp + fans share ONE helper invocation: the helper's result is
    cached for 60 s, so a fans read right after a temp read (the
    monitor's sampler does exactly this) spawns nothing new.
    read_embedded_temp() itself always spawns fresh — its contract
    (and its tests) never see a stale reading.

    - not_available: no CPU / no sensor answered / helper output
      unusable (never a guessed temperature).
    - needs_admin: the CPU is there but its temperature needs
      administrator privileges; the caller surfaces the honest hint
      + a relaunch-as-admin option. This module never prompts for
      elevation itself.
    - helper_missing: LhmReader.exe was not built/bundled (e.g. a
      dev machine without `dotnet build tools/LhmReader`).
    - timeout: the helper did not answer in time.

The function NEVER raises and NEVER blocks the UI: it runs the
helper once with a short timeout (~10 s) and a hidden spawn (no
console flash on Windows), and is called from the monitor sampler
thread, not the UI thread. A reading outside 0-125 C is sensor
garbage and is reported as not_available, never returned.
"""

import json
import math
import os
import subprocess
import time
from pathlib import Path

import hidden_spawn

_HELPER_NAME = "LhmReader.exe"
_DEFAULT_TIMEOUT_SEC = 10.0

# A temperature outside this range is sensor garbage, not a real
# temperature — same range as monitor._TEMP_PLAUSIBLE_C.
_TEMP_PLAUSIBLE_C = (0.0, 125.0)

# A fan speed outside this range is sensor garbage, not a real RPM
# — same range as the helper (tools/LhmReader/Program.cs).
_FAN_PLAUSIBLE_RPM = (0.0, 20000.0)

_ERRORS = ("not_available", "needs_admin", "helper_missing", "timeout")

# One helper invocation serves both the temperature and the fans:
# the monitor's sampler reads the temp first and the fans right
# after, and the second read must not spawn the helper again. The
# cache only ever holds a result younger than this (60 s); temp
# reads always spawn fresh, so no caller ever sees a stale
# temperature through read_embedded_temp().
_READING_CACHE_TTL_SEC = 60.0
_READING_CACHE = {"at": 0.0, "temp": None, "fans": []}


def _helper_candidates():
    """Where LhmReader.exe can live, in lookup order."""
    # Tests / debugging can point at a specific helper.
    env = os.environ.get("LONA_LHM_HELPER")
    if env:
        yield Path(env)
    # Frozen install (PyInstaller one-folder): the spec bundles the
    # helper under app/lhm/, i.e. <exe dir>/_internal/app/lhm/.
    try:
        from settings_store import APP_DIR
        yield APP_DIR / "lhm" / _HELPER_NAME
    except Exception:
        pass
    # Source checkout: build output of `dotnet build tools/LhmReader`.
    here = Path(__file__).resolve().parent
    yield (here.parent / "tools" / "LhmReader" / "bin" / "Release"
           / "net472" / _HELPER_NAME)


def _find_helper():
    """Path of the first LhmReader.exe that exists, or None."""
    for candidate in _helper_candidates():
        try:
            if candidate.is_file():
                return candidate
        except Exception:
            continue
    return None


def _parse_helper_output(stdout):
    """One stdout string from the helper -> the contract dict."""
    text = (stdout or "").strip()
    if not text:
        return {"error": "not_available",
                "detail": "LHM helper printed nothing"}
    data = None
    try:
        data = json.loads(text)
    except ValueError:
        # Tolerate stray lines (warnings, blank lines): the JSON
        # line is the last parseable one.
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                data = json.loads(line)
                break
            except ValueError:
                continue
    if not isinstance(data, dict):
        return {"error": "not_available",
                "detail": "LHM helper output was not JSON: "
                          + text[:200]}
    if "celsius" in data:
        try:
            celsius = float(data["celsius"])
        except (TypeError, ValueError):
            return {"error": "not_available",
                    "detail": "LHM helper returned a non-numeric "
                              "temperature"}
        if isinstance(data["celsius"], bool) or not math.isfinite(celsius):
            return {"error": "not_available",
                    "detail": "LHM helper returned a non-numeric "
                              "temperature"}
        if not (_TEMP_PLAUSIBLE_C[0] <= celsius <= _TEMP_PLAUSIBLE_C[1]):
            return {"error": "not_available",
                    "detail": "LHM helper returned an implausible "
                              f"temperature ({celsius} C)"}
        return {"celsius": celsius}
    error = data.get("error")
    if error not in _ERRORS:
        return {"error": "not_available",
                "detail": "LHM helper returned an unknown error: "
                          + str(error)[:100]}
    detail = data.get("detail") or ""
    if not isinstance(detail, str):
        detail = str(detail)
    return {"error": error, "detail": detail}


def _scan_helper_json(stdout):
    """The helper's JSON object from one stdout string (whole text
    first, then the last JSON-looking line — same tolerance for
    stray warning lines as _parse_helper_output), or None."""
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _extract_fans(data):
    """Fan list from one parsed helper JSON dict: [{"name", "rpm"}].
    [] when the helper reports no fans, an error, or garbage.
    Malformed entries are dropped one by one — a fan speed is never
    guessed, and a dropped entry simply does not show."""
    fans = []
    if not isinstance(data, dict):
        return fans
    raw = data.get("fans")
    if not isinstance(raw, list):
        return fans
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        rpm = entry.get("rpm")
        if isinstance(rpm, bool) or not isinstance(rpm, (int, float)):
            continue
        rpm = float(rpm)
        if not math.isfinite(rpm):
            continue
        if not (_FAN_PLAUSIBLE_RPM[0] <= rpm <= _FAN_PLAUSIBLE_RPM[1]):
            continue
        name = entry.get("name")
        fans.append({"name": str(name) if name else "Fan",
                     "rpm": rpm})
    return fans


def _extract_fans_from_stdout(stdout):
    """Fan list from one helper stdout string (see _extract_fans)."""
    return _extract_fans(_scan_helper_json(stdout))


def _reset_reading_cache():
    """Tests only: forget the cached helper result."""
    _READING_CACHE["at"] = 0.0
    _READING_CACHE["temp"] = None
    _READING_CACHE["fans"] = []


def read_embedded_temp(timeout: float = _DEFAULT_TIMEOUT_SEC) -> dict:
    """CPU temperature via the embedded LHM helper.

    Never raises; never blocks longer than `timeout` seconds.
    See the module docstring for the exact return contract.
    """
    try:
        temp, _fans = _invoke_helper(timeout)
        return temp
    except Exception as exc:  # never raise, whatever went wrong
        return {"error": "not_available",
                "detail": "unexpected error reading embedded "
                          f"temperature: {exc.__class__.__name__}: {exc}"}


def read_embedded_fans(timeout: float = _DEFAULT_TIMEOUT_SEC) -> list:
    """Fan speeds via the embedded LHM helper.

    [{"name": str, "rpm": float}] with real RPM values only; []
    when the chip reports no fan sensors, the helper is missing,
    or anything fails. Never raises; never blocks longer than
    `timeout` seconds. Shares ONE helper invocation with a recent
    read_embedded_temp() call (see _READING_CACHE): the monitor's
    temperature probe populates it, so the fans read right after
    spawns nothing new.
    """
    try:
        return list(read_embedded_reading(timeout)["fans"])
    except Exception:
        return []


def read_embedded_reading(timeout: float = _DEFAULT_TIMEOUT_SEC) -> dict:
    """One helper invocation, both values at once:
    {"temp": <the read_embedded_temp() contract dict>,
     "fans": [{"name", "rpm"}]}. A result younger than
    _READING_CACHE_TTL_SEC is served from the cache, so temp + fans
    in one sample cost a single helper spawn. Never raises."""
    try:
        cached = _READING_CACHE
        if cached["temp"] is not None and \
                time.monotonic() - cached["at"] < _READING_CACHE_TTL_SEC:
            return {"temp": dict(cached["temp"]),
                    "fans": [dict(f) for f in cached["fans"]]}
        temp, fans = _invoke_helper(timeout)
        return {"temp": temp, "fans": list(fans)}
    except Exception as exc:  # never raise, whatever went wrong
        return {"temp": {"error": "not_available",
                         "detail": "unexpected error reading embedded "
                                   f"temperature: {exc.__class__.__name__}: "
                                   f"{exc}"},
                "fans": []}


def _invoke_helper(timeout: float):
    """Run LhmReader.exe ONCE; return (temp_dict, fans_list).

    temp_dict is the exact read_embedded_temp() contract dict;
    fans_list is [] unless the helper's JSON carried real fan
    sensors. The pair is stored in _READING_CACHE, so a fans read
    right after a temp read spawns nothing new. Spawn errors are
    reported in temp_dict (never raised here) — except a truly
    unexpected failure inside subprocess.run, which propagates to
    the caller's never-raises wrapper, exactly as before.
    """
    helper = _find_helper()
    if helper is None:
        result = {"error": "helper_missing",
                  "detail": "LhmReader.exe not found (run "
                            "`dotnet build tools/LhmReader -c Release` "
                            "to build it)"}
        fans = []
    else:
        try:
            proc = subprocess.run(
                [str(helper)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                **hidden_spawn.run_kwargs(),
            )
        except subprocess.TimeoutExpired:
            result = {"error": "timeout",
                      "detail": f"LHM helper did not answer within "
                                f"{timeout:g} s"}
            fans = []
        except FileNotFoundError:
            result = {"error": "helper_missing",
                      "detail": f"LhmReader.exe disappeared: {helper}"}
            fans = []
        except OSError as exc:
            result = {"error": "not_available",
                      "detail": f"could not start LHM helper: {exc}"}
            fans = []
        else:
            result = _parse_helper_output(proc.stdout)
            fans = _extract_fans_from_stdout(proc.stdout)
            if "celsius" not in result:
                # The helper always exits 0 with a JSON line; a
                # non-zero exit with no usable JSON means the helper
                # itself broke — say so honestly instead of
                # inventing a temperature.
                if proc.returncode != 0 \
                        and result.get("error") == "not_available":
                    stderr = (proc.stderr or "").strip()
                    if stderr:
                        result = {"error": "not_available",
                                  "detail": "LHM helper failed (exit "
                                            f"{proc.returncode}): "
                                            f"{stderr[:200]}"}
    _READING_CACHE.update({"at": time.monotonic(),
                           "temp": result, "fans": fans})
    return result, fans
