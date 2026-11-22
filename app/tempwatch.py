# LONA v1.6.0 - Lightweight standalone background temperature monitor.
#
# Runs EVEN WHEN THE APP IS CLOSED: a tiny script (pythonw, psutil only,
# no webview) registered at logon as "LONA TempWatch" - via the HKCU Run
# key OR a per-user logon Scheduled Task (no admin either way). NEVER a
# Windows Service: a Service needs admin, and the install is
# per-user/no-admin. Toggleable in settings (tempwatch_enabled, default
# ON). Polls CPU temp every 60s (reuse monitor.read_cpu_temp); on
# overheat (warn >=85C, critical >=90C, sustained 2 consecutive polls)
# it shows a Windows toast with the TOP-3 causes ranked (top CPU
# processes by % + whether Windows Update is running + fan/power-saving
# state if readable) and appends to app/data/logs/tempwatch.log.
#
# Must be tiny and never crash the boot: ALL exceptions are swallowed
# and logged, the loop can never die on a bad reading, and the script
# exits quietly when tempwatch is disabled in settings.
#
# Run standalone:  pythonw app/tempwatch.py --watch

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

if not getattr(sys, "frozen", False):
    _HERE = Path(__file__).resolve().parent
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))

try:
    from logger import error, info, warn
except Exception:
    def info(*a, **k):
        return None

    def warn(*a, **k):
        return None

    def error(*a, **k):
        return None

TEMPWATCH_NAME = "LONA TempWatch"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_POLL_SEC = 60
_WARN_C = 85.0
_CRITICAL_C = 90.0
_SUSTAINED_POLLS = 2


def _log_path() -> Path:
    try:
        from settings_store import DATA_DIR
        return DATA_DIR / "logs" / "tempwatch.log"
    except Exception:
        return Path(__file__).resolve().parent / "data" / "logs" / "tempwatch.log"


def _append_log(line: str) -> None:
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {line}\n")
    except Exception:
        pass


def rank_overheat_causes(top_processes: list | None,
                         windows_update_running: bool = False,
                         power_saving: bool | None = None) -> list:
    """Rank the likely causes of an overheat, top-3, as short Arabic
    lines. 1) the top CPU processes by %, 2) Windows Update running,
    3) power-saving state when readable. Pure function - testable with
    fake readings."""
    causes = []
    procs = sorted(list(top_processes or []),
                   key=lambda p: float(p.get("cpu_percent") or 0.0),
                   reverse=True)
    for p in procs[:2]:
        name = str(p.get("name") or "?")
        cpu = float(p.get("cpu_percent") or 0.0)
        causes.append(f"1- {name} ساحب {cpu:.0f}% من المعالج"
                      if len(causes) == 0 else
                      f"{len(causes) + 1}- {name} ساحب {cpu:.0f}% من المعالج")
    if windows_update_running:
        causes.append(f"{len(causes) + 1}- Windows Update شغال دلوقتي "
                      "وبيحمّل/يثبّت تحديثات")
    if power_saving is False:
        causes.append(f"{len(causes) + 1}- وضع توفير الطاقة مطفي — "
                      "الجهاز شغال بأقصى أداء")
    if not causes:
        causes.append("1- مفيش عملية واضحة ساحبة المعالج دلوقتي — "
                      "ممكن تكون حرارة متراكمة أو تهوية مسدودة")
    return causes[:3]


def _windows_update_running() -> bool:
    """True when a Windows Update worker process is alive (best effort,
    guarded - False on any failure or off Windows)."""
    try:
        import psutil
        names = {"wuauclt.exe", "usoclient.exe", "tiworker.exe",
                 "setuphost.exe", "moshost.exe"}
        for proc in psutil.process_iter(["name"]):
            try:
                if (proc.info.get("name") or "").lower() in names:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _top_cpu_processes(limit: int = 5) -> list:
    try:
        import psutil
        out = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent"]):
            try:
                out.append({"pid": proc.info.get("pid"),
                            "name": proc.info.get("name") or "?",
                            "cpu_percent": float(
                                proc.info.get("cpu_percent") or 0.0)})
            except Exception:
                continue
        out.sort(key=lambda p: p["cpu_percent"], reverse=True)
        return out[:limit]
    except Exception:
        return []


def _show_toast(title: str, message: str) -> bool:
    """Windows toast via the existing notifications helper (guarded -
    False without winrt, never raises)."""
    try:
        import notifications as notif_mod
        return bool(notif_mod.show_toast(title, message))
    except Exception:
        return False


def check_once(temp_c: float | None = None, temp_available: bool = True,
               consecutive_hot: int = 0, top_processes: list | None = None,
               windows_update_running: bool = False,
               power_saving: bool | None = None) -> dict:
    """One TempWatch evaluation (pure-ish, testable). Returns
    {"alert": bool, "level": "", "consecutive_hot": int, "causes": []}.
    An alert fires only when the temperature is at/above the warn
    threshold for 2 consecutive polls - a single hot spike never toasts
    the user."""
    if temp_c is None or not temp_available:
        return {"alert": False, "level": "", "consecutive_hot": 0,
                "causes": []}
    hot = temp_c >= _WARN_C
    consecutive = consecutive_hot + 1 if hot else 0
    if not hot or consecutive < _SUSTAINED_POLLS:
        return {"alert": False, "level": "",
                "consecutive_hot": consecutive, "causes": []}
    level = "critical" if temp_c >= _CRITICAL_C else "warn"
    causes = rank_overheat_causes(top_processes, windows_update_running,
                                  power_saving)
    title = ("حرارة اللابتوب عالية جداً" if level == "critical"
             else "حرارة اللابتوب عالية")
    message = (f"حرارة المعالج {temp_c:.0f}°م\nأسباب محتملة:\n"
               + "\n".join(causes))
    _show_toast(title, message)
    _append_log(f"OVERHEAT {level} temp={temp_c:.0f}C causes: "
                f"{' | '.join(causes)}")
    try:
        info("tempwatch", f"Overheat alert ({level}, {temp_c:.0f}C)")
    except Exception:
        pass
    return {"alert": True, "level": level, "consecutive_hot": consecutive,
            "causes": causes}


def _read_temp_capped(timeout: float = 15.0) -> tuple:
    """read_cpu_temp() in a worker thread (v1.6.2 B22): a stuck WMI /
    PowerShell probe can never block the watcher past `timeout` --
    on timeout this is (None, False), never a hang."""
    import threading

    result: dict = {}

    def _run():
        try:
            import monitor as monitor_mod
            result["value"] = monitor_mod.read_cpu_temp()
        except Exception as exc:
            _append_log(f"temp probe failed: {exc}")
            result["value"] = (None, False)

    thread = threading.Thread(target=_run, daemon=True,
                              name="tempwatch-temp-probe")
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        _append_log(f"temp probe timed out ({timeout:.0f}s)")
        return None, False
    return result.get("value", (None, False))


def _watch_loop() -> None:
    """The background loop: poll every 60s, never die. Reads the
    setting each loop so toggling tempwatch off stops it promptly."""
    consecutive_hot = 0
    while True:
        try:
            try:
                from settings_store import load_settings
                if not load_settings().get("tempwatch_enabled", True):
                    _append_log("TempWatch disabled in settings - exiting")
                    return
            except Exception:
                pass
            temp_c, available = _read_temp_capped()
            # v1.6.2 (B22): the top-process scan runs ONLY when the
            # device is actually hot -- a cool poll needs no scan.
            hot = (temp_c is not None and available
                   and temp_c >= _WARN_C)
            result = check_once(
                temp_c=temp_c, temp_available=available,
                consecutive_hot=consecutive_hot,
                top_processes=_top_cpu_processes() if hot else [],
                windows_update_running=_windows_update_running()
                if hot else False)
            consecutive_hot = int(result.get("consecutive_hot") or 0)
        except Exception as exc:
            # The watcher must NEVER crash the boot / the session.
            _append_log(f"watch loop error (swallowed): {exc}")
            try:
                warn("tempwatch", f"Watch loop error: {exc}")
            except Exception:
                pass
            consecutive_hot = 0
        time.sleep(_POLL_SEC)


# ---------------------------------------------------------------------------
# Registration: HKCU Run OR per-user logon Scheduled Task (never a Service)
# ---------------------------------------------------------------------------

def _watch_command() -> str:
    """How Windows should launch the watcher (quoted, one command)."""
    if getattr(sys, "frozen", False):
        exe = sys.executable
        return f'"{exe}"'
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    launcher = str(pythonw) if pythonw.exists() else sys.executable
    script = Path(__file__).resolve()
    return f'"{launcher}" "{script}" --watch'


def register_tempwatch_run() -> bool:
    """HKCU Run value "LONA TempWatch". Per-user, no admin. Returns
    True when the registry now matches. Guarded: False off Windows."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, TEMPWATCH_NAME, 0, winreg.REG_SZ,
                              _watch_command())
        info("tempwatch", "TempWatch registered in HKCU Run")
        return True
    except Exception as exc:
        error("tempwatch", f"Could not register TempWatch (Run): {exc}")
        return False


def register_tempwatch_task() -> bool:
    """Per-user logon Scheduled Task "LONA TempWatch" (schtasks, /RL
    LIMITED - no admin). Alternative to the Run key. Returns True when
    schtasks reports success."""
    if sys.platform != "win32":
        return False
    try:
        import hidden_spawn
        proc = subprocess.run(
            ["schtasks", "/Create", "/TN", TEMPWATCH_NAME,
             "/TR", _watch_command(), "/SC", "ONLOGON",
             "/RL", "LIMITED", "/F"],
            capture_output=True, text=True, timeout=30,
            **hidden_spawn.run_kwargs())
        if proc.returncode == 0:
            info("tempwatch", "TempWatch registered as a logon task")
            return True
        warn("tempwatch", "schtasks failed: "
                          f"{(proc.stderr or proc.stdout or '').strip()[:200]}")
        return False
    except Exception as exc:
        error("tempwatch", f"Could not register TempWatch (task): {exc}")
        return False


def register_tempwatch(prefer: str = "run") -> bool:
    """Register the watcher. Default: HKCU Run (same mechanism as
    autostart); pass prefer="task" for the logon Scheduled Task. Falls
    back to the other mechanism when the preferred one fails. NEVER a
    Windows Service (needs admin; the install is per-user/no-admin)."""
    if prefer == "task":
        return register_tempwatch_task() or register_tempwatch_run()
    return register_tempwatch_run() or register_tempwatch_task()


def unregister_tempwatch() -> bool:
    """Remove BOTH the Run value and the Scheduled Task (whichever
    exists). Returns True when neither remains."""
    ok = True
    if sys.platform == "win32":
        try:
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                    winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, TEMPWATCH_NAME)
            except FileNotFoundError:
                pass
        except Exception as exc:
            warn("tempwatch", f"Could not remove TempWatch Run value: {exc}")
            ok = False
        try:
            import hidden_spawn
            subprocess.run(
                ["schtasks", "/Delete", "/TN", TEMPWATCH_NAME, "/F"],
                capture_output=True, text=True, timeout=30,
                **hidden_spawn.run_kwargs())
        except Exception:
            pass
    info("tempwatch", "TempWatch unregistered")
    return ok


def is_tempwatch_registered() -> bool:
    """True when the Run value or the Scheduled Task exists."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, TEMPWATCH_NAME)
            return True
    except Exception:
        pass
    try:
        import hidden_spawn
        proc = subprocess.run(
            ["schtasks", "/Query", "/TN", TEMPWATCH_NAME],
            capture_output=True, text=True, timeout=15,
            **hidden_spawn.run_kwargs())
        return proc.returncode == 0
    except Exception:
        return False


def apply_tempwatch_setting(enabled: bool) -> bool:
    """Bring registration in line with the setting (called at startup).
    Returns True when the registration now matches the request."""
    if enabled:
        return register_tempwatch()
    return unregister_tempwatch()


def main() -> int:
    if "--watch" in sys.argv:
        _append_log("TempWatch started")
        _watch_loop()
        return 0
    print("LONA TempWatch: run with --watch to start monitoring, "
          "or use register_tempwatch() from the app.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
