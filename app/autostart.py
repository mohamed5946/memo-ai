# Memo AI - Autostart on Windows boot (v1.3.1).
#
# Choice (recorded): HKCU Run key, not a Startup-folder shortcut.
# A single registry value is the cleanest toggleable mechanism for a
# per-user install: no file to litter the Startup folder, no shortcut
# to go stale after an uninstall, and one value to add/remove.
#   Key:   HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run
#   Value: "Memo AI" = "<exe path>" (frozen install) or
#          "<python>" "<main.py>" (source/zip run).
# The app always starts as the island only (see main.py), so autostart
# never pops the big window at boot. The setting (default ON) is the
# source of truth: on every startup the registry is brought in line
# with it, and the Settings toggle applies immediately.
#
# All registry access is guarded: on non-Windows (or any failure) the
# functions return False and log, never raise.

import sys

from logger import error, info

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "Memo AI"


def _exe_command() -> str:
    """How Windows should launch this install (quoted, one command)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    from pathlib import Path
    main_py = Path(__file__).resolve().parent / "main.py"
    return f'"{sys.executable}" "{main_py}"'


def is_enabled() -> bool:
    """Is the Run value present (whatever its command is)?"""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, _VALUE_NAME)
            return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def set_enabled(on: bool) -> bool:
    """Add (on=True) or remove (on=False) the Run value. Returns True
    when the registry now matches the request."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        if on:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ,
                                  _exe_command())
            info("app", "Autostart ON (HKCU Run value set)")
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                    winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, _VALUE_NAME)
            except FileNotFoundError:
                pass  # already gone - that is the requested state
            info("app", "Autostart OFF (HKCU Run value removed)")
        return True
    except Exception as exc:
        error("app", f"Could not change autostart ({exc})")
        return False


def apply_on_startup(want_on: bool) -> None:
    """Bring the registry in line with the setting at every startup.
    Never raises; a mismatch is logged, not fatal."""
    try:
        if bool(want_on) != is_enabled():
            set_enabled(bool(want_on))
    except Exception as exc:
        error("app", f"Autostart sync failed ({exc})")
