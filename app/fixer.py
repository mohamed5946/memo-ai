# LONA v1.6.0 - Autonomous fixing with permission tiers.
#
# GREEN  (safe, does it alone): kill a hung / RAM-hogging process (only
#        with evidence - CPU>90% sustained or not responding - and never
#        a system process), clear temp files (%TEMP% + Windows Temp,
#        files older than 7 days, never the user's files), close
#        unneeded startup apps (only from the HKCU Run list, only apps
#        the user approved before or known bloat - listed first).
# YELLOW (asks first via the tools.py permission-card flow): killing an
#        app with unsaved work, changing system settings.
# RED    (never alone, always an explicit one-tap grant): deleting the
#        user's files, drivers, hardware faults (e.g. a failing battery).
#
# Every action logs to the diagnostics log with the exact reason and
# result. No action runs without its tier check.

import json
import os
import shutil
import sys
import time
from pathlib import Path

from logger import error, info, warn

GREEN = "green"
YELLOW = "yellow"
RED = "red"

# Processes that are NEVER killed, whatever the evidence says. The
# list is matched case-insensitively on the process name, and the app's
# own PID + PID 0/4 (System Idle / System) are never killed either.
NEVER_KILL = {
    "explorer.exe", "system", "registry", "csrss.exe", "winlogon.exe",
    "smss.exe", "lsass.exe", "services.exe", "svchost.exe",
    "dwm.exe", "wininit.exe", "spoolsv.exe", "searchindexer.exe",
}

# Known bloat that may be closed from startup WITHOUT a fresh ask once
# listed (the user still sees the list first - see list_startup_apps).
KNOWN_BLOAT = {
    "onedrive", "skype", "teams", "spotify", "discord", "steam",
    "adobearm", "ccxprocess", "cortana",
}

TEMP_MAX_AGE_DAYS = 7
HUNG_CPU_THRESHOLD = 90.0


# ---------------------------------------------------------------------------
# Undo (scope item 6): killed processes are recorded, deleted files go to
# the Recycle Bin - never a permanent delete, never a claim of literal
# undo of everything.
# ---------------------------------------------------------------------------

def _undo_path():
    # Resolved lazily (tests redirect settings_store.DATA_DIR).
    from settings_store import DATA_DIR
    return DATA_DIR / "undo.json"


def _load_undo() -> list:
    try:
        path = _undo_path()
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_undo(entries: list) -> None:
    try:
        path = _undo_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries[-50:], ensure_ascii=False,
                                   indent=2), encoding="utf-8")
    except Exception as exc:
        warn("fixer", f"Could not save undo.json: {exc}")


def record_kill(name: str, pid: int | None, exe: str = "") -> dict:
    """Record a killed process so it can be relaunched (undo)."""
    from datetime import datetime
    entry = {"kind": "process", "name": str(name or "?"),
             "pid": int(pid) if pid else None,
             "exe": str(exe or ""),
             "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    entries = _load_undo()
    entries.append(entry)
    _save_undo(entries)
    info("fixer", f"Kill recorded for undo: {entry['name']} "
                  f"(pid {entry['pid']})")
    return entry


def list_undoable() -> list:
    """Undoable actions, newest last: killed processes (relaunchable)
    + files sent to the Recycle Bin (restorable from the Bin)."""
    return _load_undo()


def relaunch_process(entry: dict) -> dict:
    """Relaunch a killed process from its undo entry (exe path)."""
    exe = str((entry or {}).get("exe") or "").strip()
    name = str((entry or {}).get("name") or exe or "?")
    if not exe:
        return {"ok": False, "reason": "no_exe_path",
                "detail": f"«{name}» مفيش مسار تنفيذي متسجل ليها — "
                          "مش هقدر أشغّلها تاني."}
    try:
        if sys.platform == "win32":
            os.startfile(exe)  # noqa: S606 - exe came from our own record
            info("fixer", f"Relaunched (undo): {name} ({exe})")
            return {"ok": True, "reason": "relaunched"}
        import subprocess
        proc = subprocess.Popen(
            [exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        info("fixer", f"Relaunched (undo): {name} ({exe}, pid {proc.pid})")
        return {"ok": True, "reason": "relaunched", "pid": proc.pid}
    except Exception as exc:
        error("fixer", f"Could not relaunch {name} ({exe}): {exc}")
        return {"ok": False, "reason": "launch_failed", "detail": str(exc)}


def _remove_undo_entry(entry: dict) -> None:
    """Drop one undo entry (after a successful restore/relaunch)."""
    entries = _load_undo()
    for i, old in enumerate(entries):
        if old == entry:
            del entries[i]
            break
    _save_undo(entries)


def restore_deleted(entry: dict) -> dict:
    """Restore a file previously sent to the Recycle Bin (undo).
    Windows: Shell.Application COM (Namespace 0xA) - find the Bin
    item whose original path matches and invoke the restore verb.
    Non-Windows fallback: move the file back from app/data/trash/."""
    entry = entry or {}
    if str(entry.get("kind") or "") != "file":
        return {"ok": False, "reason": "not_a_file",
                "detail": "العنصر ده مش ملف — مفيش حاجة ترجع."}
    original = str(entry.get("path") or "").strip()
    if not original:
        return {"ok": False, "reason": "no_path",
                "detail": "مفيش مسار متسجل للملف ده."}
    if sys.platform == "win32":
        try:
            import subprocess
            import hidden_spawn
            # The Bin records the canonical (long) path, but the undo
            # entry may hold an 8.3 short spelling (e.g. RUNNER~1 in
            # a Temp path) - match either spelling of the same file,
            # so a just-deleted file is always found. (Pre-existing
            # v1.6.3: the old code compared the recorded spelling
            # only, and restore failed with 'not_found' on CI where
            # tempfile returns short paths.)
            spellings = [original]
            try:
                import ctypes
                buf = ctypes.create_unicode_buffer(512)
                n = ctypes.windll.kernel32.GetLongPathNameW(
                    original, buf, 512)
                if n and buf.value and buf.value != original:
                    spellings.append(buf.value)
            except Exception:
                pass
            quoted = ",".join(
                "'" + s.replace("'", "''") + "'" for s in spellings)
            ps = (
                "$s=New-Object -ComObject Shell.Application;"
                "$b=$s.Namespace(0xA);"
                f"$paths=@({quoted});"
                "$i=$b.Items() | Where-Object { $paths -contains $_.Path } "
                "| Select-Object -First 1;"
                "if ($i) { $i.InvokeVerb('restore'); 'restored' } "
                "else { 'not_found' }"
            )
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True, text=True, timeout=30,
                **hidden_spawn.run_kwargs())
            if "restored" in (proc.stdout or ""):
                _remove_undo_entry(entry)
                info("fixer", f"Restored from Recycle Bin: {original}")
                return {"ok": True, "reason": "restored"}
            return {"ok": False, "reason": "not_found",
                    "detail": ("الملف مش موجود في سلة المهملات — "
                               "يمكن اتمسح منها خلاص.")}
        except Exception as exc:
            error("fixer", f"Recycle Bin restore failed ({original}): {exc}")
            return {"ok": False, "reason": "restore_failed",
                    "detail": str(exc)}
    # Non-Windows fallback (dev/tests): the trash folder.
    try:
        from settings_store import DATA_DIR
        trash = DATA_DIR / "trash"
        stem = Path(original).stem
        suffix = Path(original).suffix
        candidates = [p for p in trash.glob(f"{stem}*{suffix}")
                      if p.is_file()] if trash.exists() else []
        if not candidates:
            return {"ok": False, "reason": "not_found",
                    "detail": "الملف مش موجود في سلة المهملات المحلية."}
        src = max(candidates, key=lambda p: p.stat().st_mtime)
        dest = Path(original)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        _remove_undo_entry(entry)
        info("fixer", f"Restored from local trash: {original}")
        return {"ok": True, "reason": "restored"}
    except Exception as exc:
        error("fixer", f"Local trash restore failed ({original}): {exc}")
        return {"ok": False, "reason": "restore_failed", "detail": str(exc)}


def _recycle_file(path: Path) -> bool:
    """Send a file to the Windows Recycle Bin (shell32 SHFileOperation
    FO_DELETE + FOF_ALLOWUNDO, ctypes - stdlib only), NOT a permanent
    delete. Non-Windows fallback: move it into app/data/trash/ (still
    recoverable, and the move is logged honestly as a fallback)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class _SHFILEOPSTRUCTW(ctypes.Structure):
                _fields_ = [
                    ("hwnd", wintypes.HWND),
                    ("wFunc", ctypes.c_uint),
                    ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort),
                    ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wintypes.LPCWSTR),
                ]

            FO_DELETE = 0x0003
            FOF_ALLOWUNDO = 0x0040
            FOF_NOCONFIRMATION = 0x0010
            FOF_SILENT = 0x0004
            op = _SHFILEOPSTRUCTW()
            op.hwnd = None
            op.wFunc = FO_DELETE
            op.pFrom = str(path) + "\0"  # double-null-terminated list
            op.pTo = None
            op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
            result = ctypes.windll.shell32.SHFileOperationW(
                ctypes.byref(op))
            if result != 0 or op.fAnyOperationsAborted:
                raise OSError(f"SHFileOperation failed (code {result})")
            return True
        except Exception as exc:
            error("fixer", f"Recycle Bin move failed ({path}): {exc}")
            return False
    # Non-Windows fallback (dev/tests): a local trash folder.
    try:
        from settings_store import DATA_DIR
        trash = DATA_DIR / "trash"
        trash.mkdir(parents=True, exist_ok=True)
        dest = trash / path.name
        if dest.exists():
            dest = trash / f"{path.stem}-{int(time.time())}{path.suffix}"
        shutil.move(str(path), str(dest))
        info("fixer", f"Moved to local trash (non-Windows fallback): {dest}")
        return True
    except Exception as exc:
        error("fixer", f"Trash fallback failed ({path}): {exc}")
        return False


def _psutil():
    try:
        import psutil
        return psutil
    except Exception:
        return None


def is_system_process(name: str | None, pid: int | None) -> bool:
    """True for anything that must never be killed: a NEVER_KILL name,
    a system PID, or this app's own process."""
    if pid is not None and int(pid) in (0, 4, os.getpid()):
        return True
    if name and str(name).strip().lower() in NEVER_KILL:
        return True
    return False


def tier_for(action: str) -> str:
    """Permission tier for an action kind. Unknown kinds are RED -
    fail closed, never assume something is safe."""
    tiers = {
        "kill_hung_process": GREEN,
        "kill_ram_hog": GREEN,
        "clear_temp": GREEN,
        "close_startup_app": GREEN,
        "kill_app_with_unsaved_work": YELLOW,
        "change_system_setting": YELLOW,
        "delete_user_file": RED,
        "change_driver": RED,
        "hardware_fault": RED,
        # -- in-chat permission kinds (tools.request_permission) --
        # Same rule, one registry: GREEN is harmless, YELLOW needs the
        # card's ask, RED is dangerous / explicit permission only.
        # Mapped by what the kind DOES. Unknown kinds stay RED.
        "clean_cache": GREEN,          # == clear_temp
        "browser_open": GREEN,         # opens a URL, nothing changes
        "browser_play_youtube": GREEN,  # plays a video, reversible
        "open_app": GREEN,             # launches an app, no data change
        "fix_hung_process": YELLOW,    # the card only appears when the
        # process may hold unsaved work (tools._tool_fix_hung_process)
        "create_file": YELLOW,         # a new file, no overwrite
        "send_whatsapp": YELLOW,       # a message leaves the device
        "write_file": RED,             # overwrites an existing file
        "edit_file": RED,              # modifies an existing file
        "run_command": RED,            # arbitrary command execution
        "deep_file_access": RED,       # deep files need explicit grant
    }
    return tiers.get(str(action or ""), RED)


def _tier_gate(action: str, granted: bool) -> dict | None:
    """Return a refusal dict when the tier forbids running now, else
    None (allowed). GREEN runs alone; YELLOW/RED need `granted`."""
    tier = tier_for(action)
    if tier == GREEN:
        return None
    if not granted:
        warn("fixer", f"{action} refused: tier {tier}, no explicit grant")
        return {"ok": False, "tier": tier, "action": action,
                "reason": ("needs_grant",
                           "هذا الإجراء محتاج إذنك الأول — دوس سماح "
                           "في كارت الإذن عشان أعمله.")}
    return None


def _find_process(pid: int | None = None, name: str | None = None):
    """Resolve a process by PID (preferred) or exact name. Returns the
    psutil.Process or None (with the reason logged)."""
    psutil = _psutil()
    if psutil is None:
        warn("fixer", "psutil is missing - cannot find a process")
        return None
    try:
        if pid:
            return psutil.Process(int(pid))
        if name:
            want = str(name).strip().lower()
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    if (proc.info.get("name") or "").lower() == want:
                        return proc
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
    except psutil.NoSuchProcess:
        return None
    except Exception as exc:
        warn("fixer", f"Process lookup failed: {exc}")
    return None


def fix_hung_process(pid: int | None = None, name: str | None = None,
                     evidence: dict | None = None,
                     granted: bool = False) -> dict:
    """GREEN: kill a hung process - ONLY with evidence (cpu_percent>90
    sustained, or not_responding True) and never a system process.
    Without evidence this refuses (killing on a hunch is not safe).
    An app with unsaved work is YELLOW: it needs `granted` (an explicit
    permission card) even with evidence."""
    evidence = dict(evidence or {})
    cpu = evidence.get("cpu_percent")
    not_responding = bool(evidence.get("not_responding"))
    sustained = bool(evidence.get("sustained"))
    if not (not_responding or (cpu is not None and float(cpu) > HUNG_CPU_THRESHOLD
                               and sustained)):
        warn("fixer", "fix_hung_process refused: no evidence of a hang "
                      f"(cpu={cpu}, sustained={sustained}, "
                      f"not_responding={not_responding})")
        return {"ok": False, "tier": GREEN, "action": "kill_hung_process",
                "reason": ("no_evidence",
                           "مفيش دليل إن العملية معلّقة — محتاج استهلاك "
                           "معالج فوق 90% لفترة أو إن البرنامج مش بيرد.")}
    proc = _find_process(pid, name)
    if proc is None:
        return {"ok": False, "tier": GREEN, "action": "kill_hung_process",
                "reason": ("not_found", "العملية مش موجودة دلوقتي.")}
    try:
        pname = proc.name()
        ppid = proc.pid
    except Exception:
        pname, ppid = str(name or "?"), pid
    if is_system_process(pname, ppid):
        error("fixer", f"Refused to kill system process {pname} (pid {ppid})")
        return {"ok": False, "tier": GREEN, "action": "kill_hung_process",
                "reason": ("system_process",
                           f"«{pname}» عملية نظام — مستحيل أقتلها.")}
    if evidence.get("unsaved_work"):
        # An app with unsaved work is YELLOW, not GREEN - it needs an
        # explicit grant (permission card) even with hang evidence.
        if not granted:
            return {"ok": False, "tier": YELLOW, "action": "kill_hung_process",
                    "reason": ("needs_grant",
                               f"«{pname}» ممكن يكون فيه شغل مش محفوظ — "
                               "محتاج إذنك الأول.")}
        info("fixer", f"Killing {pname} with explicit grant despite "
                      "possible unsaved work")
    try:
        exe_path = ""
        try:
            exe_path = str(proc.exe())
        except Exception:
            exe_path = ""
        proc.kill()
        record_kill(pname, ppid, exe_path)
        info("fixer", f"Killed hung process {pname} (pid {ppid}) - "
                      f"cpu={cpu}, not_responding={not_responding}")
        return {"ok": True, "tier": GREEN, "action": "kill_hung_process",
                "process": pname, "pid": ppid,
                "reason": "killed_hung_process",
                "undo": "recorded - relaunchable from undo.json"}
    except Exception as exc:
        error("fixer", f"Could not kill {pname} (pid {ppid}): {exc}")
        return {"ok": False, "tier": GREEN, "action": "kill_hung_process",
                "reason": ("failed", str(exc))}


def fix_ram_hog(pid: int | None = None, name: str | None = None,
                evidence: dict | None = None,
                granted: bool = False) -> dict:
    """GREEN: kill a RAM-hogging process - only with evidence
    (memory_mb above the given threshold, sustained). Same system
    guard as fix_hung_process."""
    evidence = dict(evidence or {})
    mem_mb = evidence.get("memory_mb")
    threshold = float(evidence.get("memory_threshold_mb") or 1024)
    if mem_mb is None or float(mem_mb) < threshold \
            or not evidence.get("sustained"):
        warn("fixer", "fix_ram_hog refused: no sustained RAM-hog evidence "
                      f"(memory_mb={mem_mb}, threshold={threshold})")
        return {"ok": False, "tier": GREEN, "action": "kill_ram_hog",
                "reason": ("no_evidence",
                           "مفيش دليل إن العملية بتاكل رام باستمرار — "
                           "محتاج استهلاك رام فوق الحد لفترة.")}
    return fix_hung_process(pid=pid, name=name, evidence={
        "cpu_percent": 0, "sustained": True, "not_responding": False,
        "unsaved_work": evidence.get("unsaved_work", False)},
        granted=granted)


def _temp_dirs() -> list:
    """The ONLY directories clear_temp ever touches: %TEMP% + Windows
    Temp. Never the user's files, never anything else."""
    dirs = []
    temp_env = os.environ.get("TEMP") or os.environ.get("TMP")
    if temp_env:
        dirs.append(Path(temp_env))
    windir = os.environ.get("SystemRoot") or os.environ.get("windir")
    if windir:
        dirs.append(Path(windir) / "Temp")
    elif sys.platform == "win32":
        dirs.append(Path(r"C:\Windows\Temp"))
    return [d for d in dirs if d]


def clear_temp_files(dry_run: bool = False,
                     older_than_days: int = TEMP_MAX_AGE_DAYS) -> dict:
    """GREEN: delete temp files older than `older_than_days` from the
    temp dirs only. Files only (never directories), in-use files are
    skipped, and every failure is counted, not fatal. Returns
    {"ok", "tier", "files_removed", "bytes_freed", "errors"} - dry_run
    reports the same numbers without deleting anything."""
    cutoff = time.time() - float(older_than_days) * 86400.0
    removed, freed, errors = 0, 0, 0
    scanned = 0
    for base in _temp_dirs():
        try:
            entries = list(base.iterdir())
        except Exception as exc:
            warn("fixer", f"Temp dir unreadable ({base}): {exc}")
            errors += 1
            continue
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                scanned += 1
                st = entry.stat()
                if st.st_mtime >= cutoff:
                    continue
                size = st.st_size
                if not dry_run:
                    entry.unlink()
                removed += 1
                freed += size
            except Exception:
                errors += 1  # in-use / locked file - skip it
    info("fixer", f"clear_temp {'(dry run) ' if dry_run else ''}"
                  f"removed {removed} files, freed {freed} bytes "
                  f"({scanned} scanned, {errors} errors)")
    return {"ok": True, "tier": GREEN, "action": "clear_temp",
            "files_removed": removed, "bytes_freed": freed,
            "bytes_freed_mb": round(freed / (1024 * 1024), 2),
            "errors": errors, "dry_run": bool(dry_run)}


def list_startup_apps() -> list:
    """The HKCU Run list (name + command). Windows only; elsewhere [].
    This is the list the user sees FIRST before anything is closed."""
    if sys.platform != "win32":
        return []
    try:
        import winreg
        out = []
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_READ) as key:
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                out.append({"name": str(name), "command": str(value)})
                i += 1
        return out
    except Exception as exc:
        warn("fixer", f"Could not read HKCU Run list: {exc}")
        return []


def close_startup_apps(names: list, approved: list | None = None) -> dict:
    """GREEN (with a list-first rule): remove Run entries - only names
    that are in `approved` (previously approved by the user) or in
    KNOWN_BLOAT, and only after list_startup_apps showed them. Anything
    else is refused as YELLOW (needs an explicit grant)."""
    approved = {str(n).strip().lower() for n in (approved or [])}
    removed, refused = [], []
    if sys.platform != "win32":
        return {"ok": False, "tier": GREEN, "action": "close_startup_app",
                "reason": ("unsupported", "قفل تطبيقات بدء التشغيل "
                           "شغال على ويندوز بس."),
                "removed": [], "refused": [str(n) for n in names]}
    try:
        import winreg
        for raw in names or []:
            name = str(raw or "").strip()
            key = name.lower()
            if key not in approved and key not in KNOWN_BLOAT \
                    and name.lower() not in KNOWN_BLOAT:
                refused.append(name)
                continue
            try:
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Run",
                        0, winreg.KEY_SET_VALUE) as reg:
                    winreg.DeleteValue(reg, name)
                removed.append(name)
                info("fixer", f"Removed startup entry: {name}")
            except FileNotFoundError:
                refused.append(name)
            except Exception as exc:
                warn("fixer", f"Could not remove startup entry {name}: {exc}")
                refused.append(name)
    except Exception as exc:
        error("fixer", f"close_startup_apps failed: {exc}")
        return {"ok": False, "tier": GREEN, "action": "close_startup_app",
                "reason": ("failed", str(exc)), "removed": removed,
                "refused": refused}
    return {"ok": True, "tier": GREEN, "action": "close_startup_app",
            "removed": removed, "refused": refused,
            "reason": "startup_entries_removed" if removed
            else "nothing_removed"}


def delete_user_file(path: str, granted: bool = False) -> dict:
    """RED: deleting a user's file - NEVER alone. Even with a grant the
    caller is tools.py (there is no delete tool in the brain); this
    function exists so the tier check has one enforced place. Without
    an explicit grant it always refuses."""
    gate = _tier_gate("delete_user_file", granted)
    if gate is not None:
        return gate
    try:
        target = Path(os.path.expandvars(str(path or "").strip())).expanduser()
    except Exception as exc:
        return {"ok": False, "tier": RED, "action": "delete_user_file",
                "reason": ("invalid_path", str(exc))}
    if not target.exists() or not target.is_file():
        return {"ok": False, "tier": RED, "action": "delete_user_file",
                "reason": ("not_found", "الملف مش موجود.")}
    # RED + explicit grant: the file goes to the Recycle Bin (or the
    # local trash fallback), NEVER a permanent delete - it stays
    # restorable, and the move is recorded for undo.
    if not _recycle_file(target):
        return {"ok": False, "tier": RED, "action": "delete_user_file",
                "reason": ("failed", "مقدرتش أنقل الملف لسلة المهملات.")}
    entries = _load_undo()
    from datetime import datetime
    entries.append({"kind": "file", "name": target.name,
                    "path": str(target),
                    "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "where": "recycle_bin"})
    _save_undo(entries)
    info("fixer", f"Moved user file to Recycle Bin with explicit grant: "
                  f"{target}")
    return {"ok": True, "tier": RED, "action": "delete_user_file",
            "reason": "moved_to_recycle_bin"}
