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
#   the UI (main window) polls get_pending_permission() and shows the
#   Allow/Deny dialog. resolve_permission() is called from the JS
#   bridge and wakes the waiting thread. A 120s timeout denies.
#   When a permission is requested, main.py brings the main window
#   forward (via the on_permission_needed hook) so the dialog is seen
#   even if the user was talking through the island only.

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

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
        warn("tools", f"Permission TIMEOUT (120s, denied): {kind} - {summary}")
        return False
    allowed = req.get("allowed", False)
    req["status"] = "allowed" if allowed else "denied"
    _log_request(req, req["status"])
    return allowed


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
]
