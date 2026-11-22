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
from pathlib import Path

import hidden_spawn

_HELPER_NAME = "LhmReader.exe"
_DEFAULT_TIMEOUT_SEC = 10.0

# A temperature outside this range is sensor garbage, not a real
# temperature — same range as monitor._TEMP_PLAUSIBLE_C.
_TEMP_PLAUSIBLE_C = (0.0, 125.0)

_ERRORS = ("not_available", "needs_admin", "helper_missing", "timeout")


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


def read_embedded_temp(timeout: float = _DEFAULT_TIMEOUT_SEC) -> dict:
    """CPU temperature via the embedded LHM helper.

    Never raises; never blocks longer than `timeout` seconds.
    See the module docstring for the exact return contract.
    """
    try:
        return _read_embedded_temp(timeout)
    except Exception as exc:  # never raise, whatever went wrong
        return {"error": "not_available",
                "detail": "unexpected error reading embedded "
                          f"temperature: {exc.__class__.__name__}: {exc}"}


def _read_embedded_temp(timeout: float) -> dict:
    helper = _find_helper()
    if helper is None:
        return {"error": "helper_missing",
                "detail": "LhmReader.exe not found (run "
                          "`dotnet build tools/LhmReader -c Release` "
                          "to build it)"}
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
        return {"error": "timeout",
                "detail": f"LHM helper did not answer within "
                          f"{timeout:g} s"}
    except FileNotFoundError:
        return {"error": "helper_missing",
                "detail": f"LhmReader.exe disappeared: {helper}"}
    except OSError as exc:
        return {"error": "not_available",
                "detail": f"could not start LHM helper: {exc}"}
    result = _parse_helper_output(proc.stdout)
    if "celsius" in result:
        return result
    # The helper always exits 0 with a JSON line; a non-zero exit
    # with no usable JSON means the helper itself broke — say so
    # honestly instead of inventing a temperature.
    if proc.returncode != 0 and result.get("error") == "not_available":
        stderr = (proc.stderr or "").strip()
        if stderr:
            result = {"error": "not_available",
                      "detail": "LHM helper failed (exit "
                                f"{proc.returncode}): {stderr[:200]}"}
    return result
