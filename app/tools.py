# Memo AI v1.3.0 - Tools ("hands"): what the brain may DO on the laptop.
#
# The brain (Groq gpt-oss-120b) plans; this module executes. No model ever
# touches the laptop by itself - every action runs here, in Python.
#
# SAFETY MODEL (strict, v1.3.0):
#   * Every write / run / open action asks the user FIRST through an
#     in-app Allow/Deny dialog that shows the EXACT action (app name,
#     file path, full command, content preview). Deny = nothing happens.
#     There is NO "always allow" in v1.3.0 - every single action asks.
#   * Reads (read_file / list_dir) need no dialog, but are logged.
#   * There is NO delete tool in v1.3.0 at all. Deletion requests are
#     refused with an explanation.
#   * run_command has a blocklist of destructive commands (format, del /s,
#     rm -rf, shutdown, registry edits, ...). Blocklisted commands are
#     refused with a reason and NEVER run - not even with permission.
#   * Path rules: keys/ is never readable by tools (API keys stay secret).
#     Writes are refused inside the Windows / Program Files / ProgramData
#     folders and inside Memo AI's own install folder. Writes live under
#     the user's own profile (Desktop, Documents, Downloads, ...).
#   * Every tool call - request, permission decision, execution, result -
#     is written to the diagnostics log (category "tools"), viewable
#     from Settings. The log never contains key values or full file
#     contents (previews only, first lines).
#
# PERMISSION FLOW (no deadlock by design):
#   The brain runs in a background thread (see Api.chat in main.py).
#   request_permission() blocks THAT thread on a threading.Event while
#   the UI (main window) polls get_permission_state() and shows the
#   request as an IN-CHAT message with Allow/Deny buttons (v1.4.2 -
#   not a popup/modal anymore). resolve_permission() is called from the
#   JS bridge and wakes the waiting thread. A 120s timeout denies.
#   When a permission is requested, main.py brings the main window
#   forward (via the on_permission_needed hook) so the chat card is
#   seen even if the user was talking through the island only.

import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import hidden_spawn  # v1.4.3: run_command must not pop a console window

from logger import error, info, warn
from settings_store import APP_DIR, BASE_DIR, KEYS_DIR

# ---------------------------------------------------------------------------
# Permission queue
# ---------------------------------------------------------------------------

_PERMISSION_TIMEOUT = 120.0  # seconds; timeout = deny

_pending: list = []          # list of pending request dicts (FIFO)
_pending_lock = threading.Lock()

# v1.4.0: verified-owner channel (WhatsApp owner number). The owner
# number IS the authentication - a message can only arrive from that
# SIM - and the owner is remote by definition, so an Allow/Deny dialog
# on the laptop would never be seen. request_permission() auto-allows
# in such a thread (logged as AUTO-ALLOWED). The destructive-command
# blocklist still runs BEFORE any permission check, so it applies here
# too, and there is still NO delete tool. Thread-local: the in-app
# chat is never affected.
_trusted = threading.local()


def set_trusted_caller(flag: bool) -> None:
    _trusted.flag = bool(flag)


class trusted_caller:
    """Context manager: run a block as a verified-owner channel."""
    def __init__(self, flag: bool = True):
        self.flag = flag
        self.prev = False

    def __enter__(self):
        self.prev = getattr(_trusted, "flag", False)
        set_trusted_caller(self.flag)
        return self

    def __exit__(self, *exc):
        set_trusted_caller(self.prev)
        return False

# Hook set by main.py at startup: called (best effort) when a permission
# request is created, so the app can bring the main window forward.
on_permission_needed = None


def _log_request(req: dict, decision: str) -> None:
    info("tools", f"Permission {decision}: {req['kind']} - {req['summary']}")


def request_permission(kind: str, summary: str, detail: str = "") -> bool:
    """Ask the user (Allow/Deny). Blocks the calling (brain) thread until
    the UI resolves it or the timeout hits. Returns True only on Allow.

    v1.4.0: in a verified-owner channel (WhatsApp owner number) the
    permission is auto-allowed - the number IS the authentication and
    the owner is remote, so no dialog could ever be answered."""
    if getattr(_trusted, "flag", False):
        info("tools", f"Permission AUTO-ALLOWED (verified owner channel): "
                      f"{kind} - {summary}")
        return True
    req = {
        "id": uuid.uuid4().hex[:8],
        "kind": kind,
        "summary": summary,
        "detail": detail,
        "status": "pending",
        "created": time.strftime("%H:%M:%S"),
        "event": threading.Event(),
    }
    with _pending_lock:
        _pending.append(req)
    info("tools", f"Permission requested: {kind} - {summary}")
    if on_permission_needed is not None:
        try:
            on_permission_needed()
        except Exception as exc:
            warn("tools", f"on_permission_needed hook failed: {exc}")
    ok = req["event"].wait(timeout=_PERMISSION_TIMEOUT)
    with _pending_lock:
        if req in _pending:
            _pending.remove(req)
    if not ok:
        req["status"] = "timeout"
        # v1.4.2: a timeout is a resolution too - the in-chat card flips
        # to "انتهت المهلة — تم الرفض" instead of hanging forever.
        _note_resolved(req, "timeout")
        warn("tools", f"Permission TIMEOUT (120s, denied): {kind} - {summary}")
        return False
    allowed = req.get("allowed", False)
    req["status"] = "allowed" if allowed else "denied"
    _note_resolved(req, req["status"])
    _log_request(req, req["status"])
    return allowed


# v1.4.2: resolved-permission record. The in-chat permission UI
# (app.js) polls get_permission_state(): the oldest pending request
# plus recently resolved ones, so a chat card can flip to its final
# state ("تم السماح" / "تم الرفض" / "انتهت المهلة") and never be
# clickable twice. The 120s timeout resolves as "timeout" (denied).
_resolved: dict = {}
_resolved_lock = threading.Lock()


def _note_resolved(req: dict, status: str) -> None:
    with _resolved_lock:
        _resolved[req["id"]] = {
            "id": req["id"], "kind": req["kind"],
            "summary": req["summary"], "status": status,
            "decided_at": time.time(),
        }
        if len(_resolved) > 10:
            for old_id in sorted(
                    _resolved,
                    key=lambda i: _resolved[i]["decided_at"])[:-10]:
                _resolved.pop(old_id, None)
        for rid in [r for r, v in _resolved.items()
                    if time.time() - v["decided_at"] > 600]:
            _resolved.pop(rid, None)


def get_permission_state() -> dict:
    """Oldest pending request (or None) + recently resolved ones.
    Polled by the UI while a tool chat runs."""
    pending = get_pending()
    with _resolved_lock:
        resolved = [dict(v) for v in sorted(
            _resolved.values(), key=lambda v: v["decided_at"])]
    return {"pending": pending, "resolved": resolved}


def get_pending() -> dict | None:
    """Oldest unresolved permission request (for the UI to poll), or None.
    The threading.Event is never exposed to the UI."""
    with _pending_lock:
        for req in _pending:
            if req["status"] == "pending":
                return {k: v for k, v in req.items() if k != "event"}
    return None


def resolve_permission(req_id: str, allowed: bool) -> bool:
    """Called from the JS bridge when the user taps Allow/Deny."""
    with _pending_lock:
        for req in _pending:
            if req["id"] == req_id and req["status"] == "pending":
                req["allowed"] = bool(allowed)
                req["event"].set()
                return True
    return False


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

# Write-blocked roots (Windows paths; compared case-insensitively).
_WRITE_BLOCKED_NAMES = {"windows", "program files", "program files (x86)",
                        "programdata", "system volume information",
                        "$recycle.bin"}

MAX_READ_CHARS = 8000
MAX_READ_BYTES = 5 * 1024 * 1024


def _resolve_path(raw: str) -> Path:
    """Absolute, normalized path. Relative paths resolve under the user's
    home folder (never under the app folder)."""
    p = Path(os.path.expandvars(str(raw or "").strip())).expanduser()
    if not p.is_absolute():
        p = Path.home() / p
    return Path(os.path.normpath(str(p)))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _check_read_path(path: Path) -> str | None:
    """Return an error string, or None when the path may be read."""
    if _is_within(path, KEYS_DIR):
        return "ممنوع قراءة ملفات المفاتيح (keys/) — دي أسرار."
    return None


def _check_write_path(path: Path) -> str | None:
    """Return an error string, or None when the path may be written."""
    if _is_within(path, KEYS_DIR):
        return "ممنوع الكتابة في مجلد المفاتيح (keys/)."
    if _is_within(path, BASE_DIR):
        return "ممنوع الكتابة في مجلد تثبيت Memo AI نفسه."
    if sys.platform == "win32":
        parts = [p.lower() for p in path.parts]
        if len(parts) >= 2 and parts[1] in _WRITE_BLOCKED_NAMES:
            return (f"ممنوع الكتابة في مجلد النظام '{path.parts[1]}' — "
                    "اختار مكان في ملفاتك (سطح المكتب، المستندات، التنزيلات).")
    return None


def _preview(text: str, limit: int = 400) -> str:
    text = (text or "").strip()
    if len(text) > limit:
        return text[:limit] + f"\n… ({len(text)} حرف إجمالاً)"
    return text


# ---------------------------------------------------------------------------
# open_app
# ---------------------------------------------------------------------------

# Known apps: match keywords (Arabic + English) -> how to find the exe.
# Custom exe paths are allowed too, but only with explicit permission.
ALLOWED_APPS = {
    "vscode": {
        "label": "VS Code",
        "match": ["vs code", "vscode", "code", "فيجوال ستوديو كود", "فيجوال ستوديو"],
        "which": ["code"],
        "paths": [r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
                  r"%ProgramFiles%\Microsoft VS Code\Code.exe"],
    },
    "notepad": {
        "label": "Notepad",
        "match": ["notepad", "notepad++", "المفكرة", "نوت باد"],
        "which": ["notepad.exe"],
        "paths": [],
    },
    "explorer": {
        "label": "File Explorer",
        "match": ["explorer", "file explorer", "مستكشف الملفات", "الملفات"],
        "which": ["explorer.exe"],
        "paths": [],
    },
    "terminal": {
        "label": "Terminal",
        "match": ["terminal", "cmd", "powershell", "الطرفية", "موجه الأوامر"],
        "which": ["wt.exe", "powershell.exe"],
        "paths": [],
    },
    "chrome": {
        "label": "Google Chrome",
        "match": ["chrome", "كروم"],
        "which": ["chrome.exe"],
        "paths": [r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
                  r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"],
    },
    "edge": {
        "label": "Microsoft Edge",
        "match": ["edge", "إيدج"],
        "which": ["msedge.exe"],
        "paths": [r"%ProgramFiles (x86)%\Microsoft\Edge\Application\msedge.exe",
                  r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"],
    },
}


def _find_app_exe(app_id: str) -> str | None:
    spec = ALLOWED_APPS.get(app_id)
    if not spec:
        return None
    for name in spec["which"]:
        found = shutil.which(name)
        if found:
            return found
    for raw in spec["paths"]:
        cand = Path(os.path.expandvars(raw))
        if cand.exists():
            return str(cand)
    return None


def match_app(query: str) -> str | None:
    """Map what the user typed (Arabic/English) to a known app id."""
    q = (query or "").strip().lower()
    for app_id, spec in ALLOWED_APPS.items():
        if app_id == q:
            return app_id
        for kw in spec["match"]:
            if kw in q:
                return app_id
    return None


def _launch(exe_path: str) -> None:
    if sys.platform == "win32":
        os.startfile(exe_path)  # noqa: S606 - path was resolved + permitted
    else:
        subprocess.Popen([exe_path], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


def _tool_open_app(args: dict) -> str:
    app_id = str(args.get("app", "") or "").strip()
    custom = str(args.get("path", "") or "").strip()
    target = None
    label = ""
    if custom:
        target = _resolve_path(custom)
        if not target.exists():
            return f"المسار مش موجود: {custom}"
        if target.suffix.lower() not in (".exe", ".lnk", ".bat", ".cmd"):
            return "المسار المخصوص لازم يكون برنامج (.exe) أو اختصار (.lnk)."
        err = _check_write_path(target)  # same system-dir guard as writes
        if err:
            return err
        label = target.name
    else:
        resolved = match_app(app_id) if app_id else None
        if not resolved:
            known = ", ".join(s["label"] for s in ALLOWED_APPS.values())
            return (f"معرفتش البرنامج '{app_id}'. البرامج المعروفة: {known}. "
                    "لو برنامج تاني، قولي المسار الكامل بتاعه.")
        exe = _find_app_exe(resolved)
        if not exe:
            return (f"البرنامج {ALLOWED_APPS[resolved]['label']} مش لاقيه "
                    "متثبت على الجهاز.")
        target = Path(exe)
        label = ALLOWED_APPS[resolved]["label"]
    summary = f"فتح برنامج: {label}"
    detail = f"البرنامج: {label}\nالمسار: {target}"
    if not request_permission("open_app", summary, detail):
        return f"المستخدم رفض فتح {label}."
    try:
        _launch(str(target))
    except Exception as exc:
        error("tools", f"open_app failed ({label}): {exc}")
        return f"مقدرتش أفتح {label} — حصل خطأ: {exc}"
    info("tools", f"open_app OK: {label} ({target})")
    return f"فتحت {label} ✅"


# ---------------------------------------------------------------------------
# File tools
# ---------------------------------------------------------------------------

def _tool_create_file(args: dict, overwrite: bool) -> str:
    raw = str(args.get("path", "") or "").strip()
    content = str(args.get("content", "") or "")
    if not raw:
        return "فين المسار؟ قولي اسم الملف ومكانه."
    path = _resolve_path(raw)
    err = _check_write_path(path)
    if err:
        return err
    if path.exists() and not overwrite:
        return (f"الملف موجود أصلاً: {path}\n"
                "لو عايز تعدّله استخدم edit_file، ولو عايز تستبدله قولي.")
    action = "استبدال ملف" if (path.exists() and overwrite) else "إنشاء ملف"
    summary = f"{action}: {path.name}"
    detail = (f"المسار: {path}\n"
              f"المحتوى:\n{_preview(content)}")
    if not request_permission("write_file" if overwrite else "create_file",
                              summary, detail):
        return f"المستخدم رفض {action.lower()} {path.name}."
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except Exception as exc:
        error("tools", f"write_file failed ({path}): {exc}")
        return f"مقدرتش أكتب الملف — حصل خطأ: {exc}"
    info("tools", f"write_file OK: {path} ({len(content)} chars)")
    return f"تم {action.lower()} {path.name} ✅\nالمسار: {path}"


def _tool_edit_file(args: dict) -> str:
    raw = str(args.get("path", "") or "").strip()
    old = str(args.get("old_text", "") or "")
    new = str(args.get("new_text", "") or "")
    if not raw or not old:
        return "محتاج مسار الملف والنص القديم اللي هيتغير."
    path = _resolve_path(raw)
    err = _check_write_path(path)
    if err:
        return err
    if not path.exists():
        return f"الملف مش موجود: {path}"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        return f"مقدرتش أقرأ الملف: {exc}"
    count = text.count(old)
    if count == 0:
        return "النص القديم مش موجود في الملف — اتأكد منه."
    if count > 1:
        return (f"النص القديم موجود {count} مرات في الملف — "
                "حدد نص أطول وأدق عشان أعرف أنهي واحد.")
    summary = f"تعديل ملف: {path.name}"
    detail = (f"المسار: {path}\n"
              f"النص القديم:\n{_preview(old, 300)}\n\n"
              f"النص الجديد:\n{_preview(new, 300)}")
    if not request_permission("edit_file", summary, detail):
        return f"المستخدم رفض تعديل {path.name}."
    try:
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    except Exception as exc:
        error("tools", f"edit_file failed ({path}): {exc}")
        return f"مقدرتش أعدّل الملف — حصل خطأ: {exc}"
    info("tools", f"edit_file OK: {path}")
    return f"تم تعديل {path.name} ✅"


def _tool_read_file(args: dict) -> str:
    raw = str(args.get("path", "") or "").strip()
    if not raw:
        return "فين المسار؟ قولي اسم الملف ومكانه."
    path = _resolve_path(raw)
    err = _check_read_path(path)
    if err:
        return err
    if not path.exists():
        return f"الملف مش موجود: {path}"
    if path.stat().st_size > MAX_READ_BYTES:
        return "الملف كبير أوي (أكتر من 5MB) — مش هقدر أقرأه."
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"مقدرتش أقرأ الملف: {exc}"
    info("tools", f"read_file: {path} ({len(text)} chars, read-only)")
    if len(text) > MAX_READ_CHARS:
        return text[:MAX_READ_CHARS] + f"\n… (الملف {len(text)} حرف — دي أول {MAX_READ_CHARS})"
    return text


def _tool_list_dir(args: dict) -> str:
    raw = str(args.get("path", "") or "").strip() or str(Path.home())
    path = _resolve_path(raw)
    err = _check_read_path(path)
    if err:
        return err
    if not path.exists():
        return f"المجلد مش موجود: {path}"
    if not path.is_dir():
        return f"ده ملف مش مجلد: {path}"
    try:
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except Exception as exc:
        return f"مقدرتش أفتح المجلد: {exc}"
    info("tools", f"list_dir: {path} (read-only)")
    lines = [f"📁 {path}"]
    for entry in entries[:100]:
        try:
            if entry.is_dir():
                lines.append(f"  [مجلد] {entry.name}")
            else:
                lines.append(f"  {entry.name} ({entry.stat().st_size} بايت)")
        except Exception:
            lines.append(f"  {entry.name}")
    if len(entries) > 100:
        lines.append(f"  … وفي {len(entries) - 100} عنصر كمان")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# run_command
# ---------------------------------------------------------------------------

# Destructive commands: refused with a reason, NEVER run - not even with
# permission. Patterns are matched against the command (case-insensitive).
_BLOCKLIST = [
    (re.compile(r"\bformat\s+[a-z]:", re.I), "أمر format بيمسح القرص كله."),
    (re.compile(r"\bdel\s+.*/[sq]\b", re.I), "أمر del مع /s أو /q بيمسح ملفات كتير."),
    (re.compile(r"\b(rd|rmdir)\s+.*/[sq]\b", re.I), "أمر حذف مجلد كامل (rd /s)."),
    (re.compile(r"\brm\s+.*-rf\b", re.I), "أمر rm -rf بيمسح كل حاجة."),
    (re.compile(r"\bRemove-Item\b", re.I), "أمر Remove-Item (حذف) ممنوع في v1.3.0."),
    (re.compile(r"\bshutdown\b", re.I), "أمر إطفاء الجهاز."),
    (re.compile(r"\b(Restart|Stop)-Computer\b", re.I), "أمر إعادة تشغيل/إيقاف الجهاز."),
    (re.compile(r"\breg\s+(add|delete)\b", re.I), "تعديل الريجستري ممنوع."),
    (re.compile(r"\bdiskpart\b", re.I), "أمر diskpart (أقراص) ممنوع."),
    (re.compile(r"\bnet\s+user\b", re.I), "تعديل المستخدمين ممنوع."),
    (re.compile(r"\bcipher\s+/w", re.I), "أمر cipher /w بيمسح البيانات نهائياً."),
    (re.compile(r"\bbcdedit\b", re.I), "تعديل إعدادات الإقلاع ممنوع."),
    (re.compile(r"\btaskkill\b", re.I), "أمر taskkill (قتل عمليات) ممنوع."),
    (re.compile(r"\bInvoke-Expression\b", re.I), "أمر Invoke-Expression ممنوع (خطر أمني)."),
    (re.compile(r"\|\s*iex\b", re.I), "تنفيذ كود من النت (| iex) ممنوع."),
]


def _blocked_reason(command: str) -> str | None:
    for pattern, reason in _BLOCKLIST:
        if pattern.search(command):
            return reason
    return None


def _tool_run_command(args: dict) -> str:
    command = str(args.get("command", "") or "").strip()
    if not command:
        return "قولي الأمر اللي عايزني أشغّله."
    reason = _blocked_reason(command)
    if reason:
        warn("tools", f"run_command BLOCKED by policy: {command[:120]} ({reason})")
        return f"مقدرتش أنفذ الأمر ده: {reason}"
    summary = "تشغيل أمر: " + (command if len(command) <= 100 else command[:100] + "…")
    detail = f"الأمر كامل:\n{command}"
    if not request_permission("run_command", summary, detail):
        return "المستخدم رفض تشغيل الأمر."
    if sys.platform != "win32":
        return "تشغيل الأوامر شغال على ويندوز بس."
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=60,
            **hidden_spawn.run_kwargs(),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        out = out.strip()
        if len(out) > 4000:
            out = out[:4000] + f"\n… (المخرجات {len(out)} حرف — دي أول 4000)"
        info("tools", f"run_command OK (exit {proc.returncode}): {command[:120]}")
        result = f"الأمر خلص (exit code: {proc.returncode})."
        if out:
            result += f"\nالمخرجات:\n{out}"
        return result
    except subprocess.TimeoutExpired:
        error("tools", f"run_command TIMEOUT (60s): {command[:120]}")
        return "الأمر خد أكتر من 60 ثانية فوقفناه."
    except Exception as exc:
        error("tools", f"run_command failed: {exc}")
        return f"مقدرتش أشغّل الأمر — حصل خطأ: {exc}"


# ---------------------------------------------------------------------------
# send_whatsapp_message (v1.4.2) + web_search (v1.4.2) + system_status (v1.4.2)
# ---------------------------------------------------------------------------

def _tool_send_whatsapp(args: dict) -> str:
    """Send a WhatsApp TEXT message from the linked third number.
    Default recipient = the owner's main number (Settings); an explicit
    phone overrides it. Same permission model as every other action
    (Allow/Deny in desktop chat, auto-allow in the trusted owner
    thread; the destructive blocklist is N/A here). The result reflects
    REALITY: success ONLY after Baileys confirms the send
    (whatsapp.send_message waits for the sidecar's 'sent' event) -
    never claim a send that did not happen. (Diagnosis: the original 7
    tools had NO send tool at all, so the brain could only claim it
    sent a message - it never did.)"""
    text = str(args.get("text", "") or "").strip()
    if not text:
        return "قولي نص الرسالة اللي عايزني أبعتها."
    if len(text) > 4000:
        return "الرسالة طويلة أوي (أكتر من 4000 حرف)."
    phone = str(args.get("phone", "") or "").strip()
    try:
        import whatsapp as whatsapp_mod
    except Exception:
        return "الواتساب مش متاح في النسخة دي."
    if not phone:
        try:
            from settings_store import load_settings
            phone = whatsapp_mod.normalize_number(
                load_settings().get("whatsapp_owner_phone", ""))
        except Exception:
            phone = ""
    if not phone:
        return ("مفيش رقم مالك متسجل — اكتب رقمك الأساسي في إعدادات "
                "الواتساب الأول، أو قولي الرقم اللي أبعت له.")
    masked = whatsapp_mod.mask_number(phone)
    state = whatsapp_mod.get_state()
    if state.get("status") != "linked":
        return (f"الواتساب مش مربوط دلوقتي (الحالة: {state.get('status')}) — "
                "اربطه من الإعدادات الأول.")
    summary = f"إرسال رسالة واتساب إلى {masked}"
    detail = f"الرقم: {masked}\nالرسالة:\n{text[:500]}"
    if not request_permission("send_whatsapp", summary, detail):
        return "المستخدم رفض إرسال رسالة الواتساب."
    try:
        result = whatsapp_mod.send_message(phone, text)
    except Exception as exc:
        error("tools", f"send_whatsapp failed: {exc}")
        return f"مقدرتش أبعت رسالة الواتساب — حصل خطأ: {exc}"
    if isinstance(result, dict) and result.get("ok"):
        info("tools", f"send_whatsapp OK to {masked}")
        return f"بعت رسالة الواتساب إلى {masked} ✅"
    detail_err = ""
    if isinstance(result, dict):
        detail_err = str(result.get("detail", "") or "")
    error("tools", f"send_whatsapp failed to {masked}: {detail_err}")
    return (f"مقدرتش أبعت رسالة الواتساب إلى {masked} — "
            f"{detail_err or 'السبب في السجل'}.")


def _tool_web_search(args: dict) -> str:
    """Free web search (DuckDuckGo via the ddgs library - no API key, no
    account; chosen because it is a maintained pure-Python client that
    worked in a live sandbox test). Read-only: no permission dialog,
    like read_file. Returns titles + snippets + source URLs so the
    brain can answer from the results and name the source. On failure
    it says so honestly - the brain must never present a failed search
    as an answer."""
    query = str(args.get("query", "") or "").strip()
    if not query:
        return "قولي اللي عايزني أدور عليه."
    count = args.get("count", 5)
    try:
        count = max(1, min(10, int(count)))
    except Exception:
        count = 5
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=count))
    except Exception as exc:
        error("tools", f"web_search failed ({query[:80]}): {exc}")
        return (f"مقدرتش أدور على النت دلوقتي ({exc}) — "
                "قولها بصراحة للمستخدم ومتخمنش إجابة.")
    if not results:
        return (f"دورت على «{query}» وملقيتش نتايج — "
                "قولها بصراحة ومتخمنش.")
    lines = [f"نتايج البحث عن «{query}»:"]
    for i, r in enumerate(results[:count], 1):
        title = (r.get("title") or "").strip()
        body = (r.get("body") or "").strip()
        href = (r.get("href") or "").strip()
        lines.append(f"{i}. {title}\n   {body[:300]}"
                     + (f"\n   المصدر: {href}" if href else ""))
    info("tools", f"web_search OK: '{query[:80]}' -> {len(results)} results")
    return "\n".join(lines)


# v1.4.3: the live SystemMonitor instance, registered by main.py
# (set_monitor_provider). The tool prefers it - warm CPU history,
# shared temperature cache - and falls back to a fresh SystemMonitor
# when the app has none (tests, standalone calls).
_monitor_provider = None


def set_monitor_provider(fn) -> None:
    """Register a callable returning the live SystemMonitor (or None)."""
    global _monitor_provider
    _monitor_provider = fn


def _tool_system_status(args: dict) -> str:
    """Real laptop numbers (RAM, disk, CPU, battery, temp when
    available) from the background monitor. Read-only, no permission
    dialog. The brain calls this when the user asks "اللابتوب عامل
    إيه؟" / "الرام قد إيه؟" / "الجهاز سخن؟" - and answers from these
    numbers, never from memory. An unavailable temperature is stated
    honestly, never guessed.

    v1.4.3 ROOT-CAUSE FIX: this used to call
    ``monitor_mod.get_status()`` - a module-level function that does
    NOT exist (get_status is a SystemMonitor *method*). Every call
    raised AttributeError, so the tool failed as a whole and the
    brain told the user it "couldn't fetch" RAM too. The call now
    goes through a live monitor instance (or a fresh one), and every
    section is rendered on its own - a missing temperature can never
    take the RAM/disk/CPU numbers down with it.
    """
    try:
        import monitor as monitor_mod
    except Exception:
        return "مراقبة النظام مش متاحة في النسخة دي."
    st = None
    provider = _monitor_provider
    if provider is not None:
        try:
            mon = provider()
        except Exception:
            mon = None
        if mon is not None:
            try:
                st = mon.get_status()
            except Exception as exc:
                error("tools", f"system_status (live monitor) failed: {exc}")
    if st is None:
        try:
            st = monitor_mod.SystemMonitor().get_status()
        except Exception as exc:
            error("tools", f"system_status failed: {exc}")
            return f"مقدرتش أقرأ حالة اللابتوب — حصل خطأ: {exc}"
    if not st.get("available", True):
        return ("مراقبة النظام مش متاحة على الجهاز ده دلوقتي "
                "(مكتبة قراءة حالة الجهاز مش موجودة).")
    ram = st.get("ram") or {}
    disk = st.get("disk") or {}
    cpu = st.get("cpu") or {}
    battery = st.get("battery")
    temp = st.get("temp_c")
    lines = ["حالة اللابتوب دلوقتي:"]
    if ram:
        lines.append(f"• الرام: {ram.get('used_mb', '?')}MB مستخدمة من "
                     f"{ram.get('total_mb', '?')}MB "
                     f"({ram.get('percent', '?')}% — فاضي {ram.get('free_mb', '?')}MB)")
    else:
        lines.append("• الرام: مش متاحة دلوقتي (تعذر قراءتها).")
    if disk:
        lines.append(f"• القرص: فاضي {disk.get('free_gb', '?')}GB من "
                     f"{disk.get('total_gb', '?')}GB "
                     f"({disk.get('percent_free', '?')}% فاضي)")
    else:
        lines.append("• القرص: مش متاح دلوقتي (تعذر قراءته).")
    cpu_pct = cpu.get("percent")
    if cpu_pct is not None:
        lines.append(f"• المعالج: {cpu_pct}% دلوقتي"
                     + (" (حمل مرتفع مستمر ⚠️)" if cpu.get("sustained_high") else ""))
    else:
        lines.append("• المعالج: مش متاح دلوقتي (تعذر قراءته).")
    if temp is not None:
        lines.append(f"• حرارة المعالج: {temp}°م")
    else:
        lines.append("• حرارة المعالج: مش متاحة على الجهاز ده "
                     "(مفيش حساس مقروء) — متخمنش رقم.")
    if battery:
        lines.append(f"• البطارية: {battery.get('percent', '?')}%"
                     + (" (بتشحن 🔌)" if battery.get("charging")
                        else " (مش بتشحن)"))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# generate_image (v1.4.3) - free image generation, no API key
# ---------------------------------------------------------------------------

# Pollinations.ai: free, no key, no account - a plain HTTP GET returns
# the rendered image. Verified live 2026-11-07 (HTTP 200, real JPEG
# bytes, 512x512 in ~6s). Model "flux" is the quality model; nologo
# drops the watermark. The prompt is sent as-is (the brain translates
# the user's Arabic description to English when calling the tool -
# the image model understands English prompts far better).
_IMAGE_GEN_URL = "https://image.pollinations.ai/prompt/{prompt}"
_IMAGE_GEN_TIMEOUT = 90.0  # seconds; generation is slow, be patient
_IMAGE_GEN_MAX_DIM = 1280
_IMAGE_GEN_MIN_DIM = 256
_IMAGE_GEN_MAX_PROMPT = 500
_IMAGE_DIRNAME = "images"

# Images generated during this process, in order (filenames only).
# run_tool_chat() consumes them (take_generated_images) and attaches
# them to the chat result, so the UI shows the image even if the model
# forgets to echo the [[IMAGE:...]] marker in its reply text.
_generated_images: list = []
_generated_lock = threading.Lock()

_IMAGE_MARKER_RE = re.compile(r"\[\[IMAGE:([A-Za-z0-9._-]+)\]\]")


def extract_image_markers(text: str) -> tuple:
    """Strip [[IMAGE:<filename>]] markers from a reply. Returns
    (clean_text, [filenames]). The marker is a machine token for the
    chat UI, never user-facing text (WhatsApp replies are stripped
    with this too - WhatsApp gets text only)."""
    names = _IMAGE_MARKER_RE.findall(text or "")
    clean = _IMAGE_MARKER_RE.sub("", text or "")
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, names


def take_generated_images() -> list:
    """Filenames generated since the last take (drains the list)."""
    with _generated_lock:
        names = list(_generated_images)
        _generated_images.clear()
    return names


def _parse_image_size(raw: str) -> tuple:
    """'1024x1024' -> (1024, 1024), clamped to 256-1280 per side.
    Anything unparsable falls back to the 1024x1024 default."""
    try:
        w_s, h_s = str(raw or "").strip().lower().replace(" ", "").split("x")
        w = max(_IMAGE_GEN_MIN_DIM,
                min(_IMAGE_GEN_MAX_DIM, int(w_s)))
        h = max(_IMAGE_GEN_MIN_DIM,
                min(_IMAGE_GEN_MAX_DIM, int(h_s)))
        return w, h
    except Exception:
        return 1024, 1024


def _save_generated_image(data: bytes, out_dir: Path) -> tuple:
    """Validate raw bytes as a real image and save them as a JPEG
    (max 1024px per side, quality 85 - keeps chat data URLs small).
    Returns (filename, path) or (None, None) when the bytes are not
    a usable image. Falls back to saving the raw bytes when Pillow
    cannot decode them but the magic bytes are PNG/JPEG."""
    name = (f"img-{time.strftime('%Y%m%d-%H%M%S')}-"
            f"{uuid.uuid4().hex[:8]}.jpg")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        error("tools", f"generate_image: cannot create {out_dir}: {exc}")
        return None, None
    try:
        from PIL import Image
        im = Image.open(BytesIO(data))
        im.verify()
        im = Image.open(BytesIO(data))  # verify() closes the image
        if im.format not in ("JPEG", "PNG", "WEBP"):
            return None, None
        im = im.convert("RGB")
        im.thumbnail((1024, 1024), Image.LANCZOS)
        path = out_dir / name
        im.save(path, "JPEG", quality=85)
        return name, path
    except Exception:
        # Pillow could not decode it - trust only PNG/JPEG magic bytes.
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            name = name[:-4] + ".png"
        elif data[:3] == b"\xff\xd8\xff":
            pass  # name already .jpg
        else:
            return None, None
        try:
            path = out_dir / name
            path.write_bytes(data)
            return name, path
        except Exception as exc:
            error("tools", f"generate_image: cannot save {path}: {exc}")
            return None, None


def _tool_generate_image(args: dict) -> str:
    """Generate an image from a text prompt (free, no API key).
    Read-only + network only: no permission dialog (same as
    web_search/system_status). The image is downloaded, validated as
    real image bytes, saved under app/data/images/, and the result
    carries a [[IMAGE:<filename>]] marker line - the brain MUST echo
    that line verbatim in its reply so the chat UI renders the image.
    On ANY failure it says so honestly - the brain must never claim
    an image was generated when it was not. The prompt is logged
    truncated (first 80 chars, diagnosis only) - never phone numbers
    or message contents."""
    prompt = str(args.get("prompt", "") or "").strip()
    if not prompt:
        return "قولي أوصفي الصورة اللي عايزها — الوصف فاضي."
    if len(prompt) > _IMAGE_GEN_MAX_PROMPT:
        return (f"الوصف طويل أوي ({len(prompt)} حرف، الحد "
                f"{_IMAGE_GEN_MAX_PROMPT}) — اختصره شوية.")
    width, height = _parse_image_size(args.get("size", "1024x1024"))
    seed = random.randint(0, 999999)
    url = (_IMAGE_GEN_URL.format(prompt=quote(prompt, safe=""))
           + f"?width={width}&height={height}&model=flux"
           + f"&nologo=true&seed={seed}")
    try:
        import requests
    except Exception:
        return ("مكتبة الشبكة (requests) مش متاحة في النسخة دي — "
                "مقدرتش أولّد الصورة.")
    try:
        resp = requests.get(url, timeout=_IMAGE_GEN_TIMEOUT)
    except Exception as exc:
        # requests.Timeout is a subclass - named separately in the log
        # so a slow service reads differently from no network.
        kind = ("TIMEOUT" if exc.__class__.__name__ == "Timeout"
                else type(exc).__name__)
        error("tools", f"generate_image {kind} ({_IMAGE_GEN_TIMEOUT}s): "
                       f"prompt {prompt[:80]!r}: {exc}")
        if kind == "TIMEOUT":
            return ("مقدرتش أولّد الصورة — خدمة توليد الصور خدت أكتر من "
                    "90 ثانية ومردتش. جرّب تاني كمان شوية.")
        return (f"مقدرتش أولّد الصورة — مشكلة في الشبكة/الخدمة ({exc}). "
                "قولها بصراحة للمستخدم ومتدّعيش إن الصورة اتولدت.")
    if resp.status_code != 200:
        error("tools", f"generate_image HTTP {resp.status_code}: "
                       f"prompt {prompt[:80]!r}")
        return (f"مقدرتش أولّد الصورة — خدمة توليد الصور رجّعت خطأ "
                f"(HTTP {resp.status_code}). جرّب تاني كمان شوية.")
    data = resp.content or b""
    content_type = (resp.headers.get("content-type") or "").lower()
    if not content_type.startswith("image/") or len(data) < 100:
        # 100 bytes is only a trivial-body guard - Pillow below is
        # the real validator (a tiny solid-colour PNG can be ~300B).
        error("tools", f"generate_image: non-image reply "
                       f"(content-type {content_type!r}, {len(data)} bytes): "
                       f"prompt {prompt[:80]!r}")
        return ("مقدرتش أولّد الصورة — خدمة توليد الصور رجّعت رد مش "
                "صورة. جرّب تاني كمان شوية.")
    # DATA_DIR is resolved per call (tests redirect it; same pattern
    # as whatsapp.py) - never import-time, never under the install dir.
    from settings_store import DATA_DIR
    filename, path = _save_generated_image(data, DATA_DIR / _IMAGE_DIRNAME)
    if not filename:
        error("tools", "generate_image: downloaded bytes are not a "
                       f"usable image ({len(data)} bytes, {content_type})")
        return ("مقدرتش أولّد الصورة — الصورة اللي رجعت مش صالحة. "
                "جرّب تاني كمان شوية.")
    with _generated_lock:
        _generated_images.append(filename)
    info("tools", f"generate_image OK: '{prompt[:80]}' -> {filename} "
                  f"({len(data)} bytes in, {path.stat().st_size} bytes saved)")
    return (f"ولّدت الصورة ✅\n[[IMAGE:{filename}]]\n"
            f"الوصف: {prompt[:200]}\nمحفوظة في: {path}")


# ---------------------------------------------------------------------------
# Dispatcher + tool schemas (OpenAI function-calling format, for Groq)
# ---------------------------------------------------------------------------

def execute_tool(name: str, arguments: dict) -> str:
    """Run one tool call from the brain. Returns a result string for the model.
    Unknown tools and bad arguments are reported, never crash.
    v1.4.1: the result is GUARANTEED to be a string - a None result
    used to become a tool message with content=None, and the NEXT
    Groq/Ollama request 400d on it (the v1.4.0 field failure)."""
    info("tools", f"Tool requested: {name} {json.dumps(arguments, ensure_ascii=False)[:200]}")
    try:
        if name == "open_app":
            result = _tool_open_app(arguments)
        elif name == "create_file":
            result = _tool_create_file(arguments, overwrite=False)
        elif name == "write_file":
            result = _tool_create_file(arguments, overwrite=True)
        elif name == "edit_file":
            result = _tool_edit_file(arguments)
        elif name == "read_file":
            result = _tool_read_file(arguments)
        elif name == "list_dir":
            result = _tool_list_dir(arguments)
        elif name == "run_command":
            result = _tool_run_command(arguments)
        elif name == "send_whatsapp_message":
            result = _tool_send_whatsapp(arguments)
        elif name == "web_search":
            result = _tool_web_search(arguments)
        elif name == "system_status":
            result = _tool_system_status(arguments)
        elif name == "generate_image":
            result = _tool_generate_image(arguments)
        else:
            warn("tools", f"Unknown tool requested: {name}")
            return f"الأداة '{name}' مش موجودة."
    except Exception as exc:
        error("tools", f"Tool {name} crashed: {exc}")
        return f"حصل خطأ وأنا بنفذ {name}: {exc}"
    if result is None:
        return ""
    return result if isinstance(result, str) else str(result)


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Open a desktop application on this laptop (VS Code, Notepad, File Explorer, Terminal, Chrome, Edge). Call this tool WHENEVER the user asks to open, run, or launch an app — never answer in text that you cannot open apps; you CAN, through this tool. Use 'app' for a known app, or 'path' for a specific exe path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {"type": "string",
                            "description": "Known app: vscode, notepad, explorer, terminal, chrome, edge — or what the user called it (e.g. 'vs code')."},
                    "path": {"type": "string",
                             "description": "Full path to an exe/lnk when the app is not in the known list."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Create a NEW text file at the given path with the given content. Call this WHENEVER the user asks to create/make/write a file — do it with the tool instead of only explaining how. Fails if the file already exists (use edit_file or write_file then).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Full file path, e.g. C:\\Users\\DELL\\Desktop\\notes.txt"},
                    "content": {"type": "string", "description": "The file content."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write text to a file, creating it or overwriting it if it exists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Full file path."},
                    "content": {"type": "string", "description": "The file content."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace one exact piece of text (old_text) with new_text inside a file. old_text must occur exactly once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Full file path."},
                    "old_text": {"type": "string", "description": "The exact text to replace."},
                    "new_text": {"type": "string", "description": "The replacement text."},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file (first 8000 characters). Read-only, no permission dialog.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Full file path."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List the contents of a folder. Read-only, no permission dialog.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Folder path (default: home folder)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a PowerShell command on Windows and return its output. Call this WHENEVER the user asks to run/execute a command — do it with the tool instead of only explaining how. Destructive commands are blocked. Every run asks the user first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The full PowerShell command."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_whatsapp_message",
            "description": "Send a WhatsApp TEXT message from the linked third number. Call this WHENEVER the user asks to send a WhatsApp message — do it with the tool instead of only saying you sent it. The recipient defaults to the owner's main number (from Settings) when 'phone' is omitted; pass 'phone' only when the user names a different number. The tool returns success ONLY after WhatsApp confirms the send — if it says it failed, tell the user honestly and never claim it was sent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {"type": "string", "description": "Recipient phone number (digits, e.g. 01557887719). Omit to send to the owner's main number."},
                    "text": {"type": "string", "description": "The message text (text only, max 4000 characters)."},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web (DuckDuckGo, free, no API key) and return titles, snippets and source URLs. Call this WHENEVER the user asks about something you do not know (current events, prices, facts past your training) — search instead of guessing or saying 'I don't know'. Answer from the results and name the source. If the search fails, say so honestly and never guess.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."},
                    "count": {"type": "integer", "description": "How many results (1-10, default 5)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "system_status",
            "description": "Read the laptop's LIVE status: RAM use, disk free space, CPU load, battery, and CPU temperature when available. Call this WHENEVER the user asks how the laptop is doing ('how is the laptop?', 'how much RAM?', 'is it hot?') — answer with these real numbers, never from memory. If the temperature is unavailable, say so honestly and never guess a number.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "Generate an image from a text description (free service, no API key). Call this WHENEVER the user asks to draw or generate an image ('ارسم لي...', 'ولّد صورة...', 'draw/generate an image of...') — do it with the tool instead of saying you cannot generate images; you CAN, through this tool. Write the 'prompt' in ENGLISH (translate the user's Arabic description into English - the image model understands English far better). The tool returns a line [[IMAGE:<filename>]] — you MUST include that exact line verbatim in your reply so the image shows in the chat. If the tool says generation failed, tell the user honestly that no image was generated and never claim one was made.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "The image description IN ENGLISH, e.g. 'a small red cat sitting on a rug, soft daylight, photorealistic'."},
                    "size": {"type": "string", "description": "Optional 'WIDTHxHEIGHT', e.g. '1024x1024' (default square), '768x1024' (portrait), '1024x768' (landscape). Each side 256-1280."},
                },
                "required": ["prompt"],
            },
        },
    },
]
