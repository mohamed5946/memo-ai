# LONA v1.6.1 - first-launch migration from the old "Memo AI" install.
#
# The program was renamed (2026-11-22): new install folder
# %LOCALAPPDATA%\Programs\LONA, new exe LONA.exe, new installer AppId.
# An upgrade from a v1.6.0 "Memo AI" install must carry its data over,
# or the app would ask for the invite code again and the user would
# lose his settings and memory:
#   * app/data/  - settings.json, activated.json (invite state),
#     memory.json, rules.json, qa.json, updates/, logs/
#   * keys/      - legacy *.txt key files (v1.6.0 keys themselves live
#     in Windows Credential Manager / DPAPI and survive the move on
#     their own; the folder still comes over so nothing is stranded)
#
# Two layers do the same job, and either one alone is enough:
#   1. The installer (installer/memo-ai.iss, ssInstall/ssPostInstall)
#      stages the old data before removing the old install - that is
#      the primary path for a setup.exe upgrade.
#   2. This module is the fallback for every other path (zip install,
#      manual copy, installer step skipped): main() calls it once at
#      startup, before settings are loaded, and only when frozen
#      (sys.frozen - a source checkout never migrates into itself).
# It is a no-op when there is no old install, and it never overwrites
# files that already exist in the new location - the new install's
# data always wins.
#
# Removal: after a successful copy the old install is removed through
# its own uninstaller (unins000.exe /VERYSILENT) when one exists, so
# its Add/Remove Programs entry goes with it; a zip-style old folder
# (no uninstaller) is deleted directly. The stale HKCU Run autostart
# value "Memo AI" is removed too - the new app registers "LONA".
# Every step is logged (diagnostics-log standing rule); any failure
# is logged, never raised, so a broken migration can never block
# startup - the worst case is the old folder staying put.

import shutil
import subprocess
import sys
from pathlib import Path

from logger import error, info, warn

OLD_APP_DIR_NAME = "Memo AI"
OLD_EXE_NAME = "Memo AI.exe"
# Old Inno Setup AppId (installer/memo-ai.iss before the rename) - used
# to find the old uninstaller in the registry. The new installer uses
# a different AppId, so the old entry is not removed by the upgrade
# itself and must be removed here / by the new installer.
OLD_APP_ID = "{7BBF7DD9-42C6-41D0-BB76-99A5E19569A1}"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _default_old_base() -> Path | None:
    """%LOCALAPPDATA%\\Programs\\Memo AI on Windows, else None."""
    if sys.platform != "win32":
        return None
    local = Path.home() / "AppData" / "Local"
    return local / "Programs" / OLD_APP_DIR_NAME


def _copy_missing_tree(src: Path, dst: Path) -> list[str]:
    """Copy src/ into dst/ recursively, only files missing at dst.

    Never overwrites: a file that already exists in the new location
    is left untouched. Returns the relative paths that were copied."""
    copied: list[str] = []
    if not src.is_dir():
        return copied
    for item in sorted(src.rglob("*")):
        if item.is_dir():
            continue
        rel = item.relative_to(src)
        target = dst / rel
        if target.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            copied.append(str(rel))
        except Exception as exc:
            warn("app", f"Migration: could not copy {rel} ({exc})")
    return copied


def _old_data_dir(old_base: Path) -> Path | None:
    """The old install's data dir (frozen layout first, zip second)."""
    for candidate in (old_base / "_internal" / "app" / "data",
                      old_base / "app" / "data"):
        if candidate.is_dir():
            return candidate
    return None


def _remove_stale_autostart() -> bool:
    """Delete the old HKCU Run value "Memo AI" (Windows only)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, OLD_APP_DIR_NAME)
        info("app", "Migration: removed stale autostart value "
                    f"({OLD_APP_DIR_NAME})")
        return True
    except FileNotFoundError:
        return False
    except Exception as exc:
        warn("app", f"Migration: could not remove old autostart value ({exc})")
        return False


def _run_old_uninstaller(old_base: Path) -> bool:
    """Run the old install's own uninstaller silently (Windows only).

    Returns True when an uninstaller was found and launched. The
    uninstaller removes the old folder AND its Add/Remove Programs
    entry; a failure to launch it is logged, never raised."""
    if sys.platform != "win32":
        return False
    uninst = None
    try:
        import winreg
        subkey = (r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
                  + "\\" + OLD_APP_ID + "_is1")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0,
                            winreg.KEY_READ) as key:
            raw, _ = winreg.QueryValueEx(key, "UninstallString")
        candidate = Path(str(raw).strip().strip('"'))
        if candidate.is_file():
            uninst = candidate
    except Exception:
        uninst = None
    if uninst is None:
        candidate = old_base / "unins000.exe"
        if candidate.is_file():
            uninst = candidate
    if uninst is None:
        return False
    try:
        subprocess.Popen(
            [str(uninst), "/VERYSILENT", "/NORESTART",
             "/SUPPRESSMSGBOXES"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        info("app", f"Migration: old uninstaller launched ({uninst})")
        return True
    except Exception as exc:
        warn("app", f"Migration: could not launch old uninstaller ({exc})")
        return False


def migrate_from_old_install(new_base_dir, new_data_dir=None,
                             old_base_dir=None,
                             remove_old: bool = True) -> dict:
    """Migrate data from an old "Memo AI" install into this LONA one.

    new_base_dir: the new install folder (settings_store.BASE_DIR).
    new_data_dir: the new data dir (settings_store.DATA_DIR); derived
      from new_base_dir when omitted.
    old_base_dir: the old install folder; defaults to
      %LOCALAPPDATA%\\Programs\\Memo AI on Windows.
    Returns {"migrated", "copied", "skipped_existing", "old_removed",
    "reason"} - always a dict, never raises."""
    new_base = Path(new_base_dir)
    if new_data_dir is None:
        frozen_data = new_base / "_internal" / "app" / "data"
        new_data = frozen_data if frozen_data.is_dir() else \
            new_base / "app" / "data"
    else:
        new_data = Path(new_data_dir)
    old_base = Path(old_base_dir) if old_base_dir is not None \
        else _default_old_base()
    result = {"migrated": False, "copied": [], "skipped_existing": 0,
              "old_removed": False, "reason": ""}
    if old_base is None:
        result["reason"] = "no old install location on this platform"
        return result
    # Safety: the old folder must really be the old program folder,
    # and never the same folder as the new install.
    if old_base.name != OLD_APP_DIR_NAME:
        result["reason"] = f"old folder is not {OLD_APP_DIR_NAME!r}: {old_base}"
        return result
    try:
        if old_base.resolve() == new_base.resolve():
            result["reason"] = "old and new install folders are the same"
            return result
    except Exception:
        pass
    if not old_base.is_dir():
        result["reason"] = f"old install not found: {old_base}"
        return result

    copied: list[str] = []
    old_data = _old_data_dir(old_base)
    # Count the files the new install already has BEFORE copying -
    # afterwards the just-copied files would be counted as skipped.
    skipped = 0
    if old_data is not None:
        for item in old_data.rglob("*"):
            if item.is_file() and (new_data / item.relative_to(old_data)).exists():
                skipped += 1
    if old_data is not None:
        before = len(copied)
        copied += _copy_missing_tree(old_data, new_data)
        data_copied = len(copied) - before
    else:
        data_copied = 0
    old_keys = old_base / "keys"
    if old_keys.is_dir():
        new_keys = new_base / "keys"
        new_keys.mkdir(parents=True, exist_ok=True)
        for item in sorted(old_keys.glob("*.txt")):
            if item.name.lower() == "readme.txt":
                continue
            target = new_keys / item.name
            if target.exists():
                continue
            try:
                shutil.copy2(item, target)
                copied.append(f"keys/{item.name}")
            except Exception as exc:
                warn("app", f"Migration: could not copy key {item.name} ({exc})")
    # Files that were already in the new location were deliberately
    # left alone (new data wins) - counted above, before the copy.
    result["copied"] = copied
    result["skipped_existing"] = skipped
    result["migrated"] = True
    info("app", f"Migration: moved {len(copied)} file(s) from the old "
                f"{OLD_APP_DIR_NAME} install "
                f"({data_copied} from app/data, "
                f"{len(copied) - data_copied} from keys) - "
                f"{skipped} already present, kept the new copy")
    if not copied and skipped == 0:
        info("app", "Migration: old install had no data files to move")
    if remove_old:
        if _run_old_uninstaller(old_base):
            result["old_removed"] = True
        elif not (old_base / "unins000.exe").is_file():
            # Zip-style old install: no uninstaller, no registry entry
            # to clean - the folder itself is the whole install.
            try:
                shutil.rmtree(old_base)
                result["old_removed"] = True
                info("app", f"Migration: removed old install folder {old_base}")
            except Exception as exc:
                error("app", f"Migration: could not remove old folder "
                             f"{old_base} ({exc})")
        else:
            warn("app", "Migration: old uninstaller could not be launched; "
                        "the old folder was left in place")
        _remove_stale_autostart()
    return result
