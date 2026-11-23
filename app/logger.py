# Memo AI - Diagnostics logger (standing rule: logs from day one)
# Writes categorized entries to app/data/logs/memo-ai-YYYY-MM-DD.log (JSON lines).
# Levels: info / warn / error. Categories: app, brain, hear, speak, net,
# keys, weather, ui, rules. Exact failure reasons are always recorded.
#
# rc.3 (a) SECRET REDACTION (27-item security checklist, verified by
# test, not just claimed): no API key, token or password ever reaches
# the log file or the in-app viewer. redact_secrets() runs on every
# write (log) AND on every read (read_recent / log_text), so even an
# entry written before this change can never show a secret.
# rc.3 (c) RETENTION: trim_old_logs() drops entries older than 30
# days (Loki-style retention, LONA-sized); entries with no parseable
# timestamp are KEPT, and a file is never emptied entirely.

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

# LOG_DIR is resolved lazily (see _log_dir): logger is imported by
# settings_store, and settings_store owns the frozen-aware path helper,
# so importing it here at module level would be a circular import.


def _log_dir() -> Path:
    from settings_store import APP_DIR  # local import: no circular import
    log_dir = APP_DIR / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


# ---------------------------------------------------------------------------
# rc.3 (a): secret redaction
# ---------------------------------------------------------------------------

# key=value shapes: the value after the key is masked, the key stays
# (so the log still says WHAT was logged, just not the secret).
# Covers the app's own key field names (api_key / groq / gemini keys
# travel as api_key=... or apikey=... in diagnostics) plus the generic
# token / secret / password shapes.
_SECRET_KV_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|"
    r"refresh[_-]?token|id[_-]?token|token|secret|client[_-]?secret|"
    r"password|passwd|pwd)\s*([:=])\s*(['\"]?)"
    r"([^\s'\",}\]]+)")

# A bare "key" field ("key": "..." / key=...): masked only when the
# value looks like an opaque secret (16+ word chars, no spaces), so
# ordinary uses ("key": "Enter", "key": "temperature") are untouched.
_GENERIC_KEY_RE = re.compile(
    r"(?i)\b(key)\s*([:=])\s*(['\"]?)([A-Za-z0-9_\-]{16,})")

# "Authorization: Bearer <token>" / "bearer <token>".
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+([A-Za-z0-9._\-~+/=]{8,})")

# Bare key shapes, with no key name attached: OpenAI-style sk-...,
# Groq gsk_..., Google AIza... (35 chars after the prefix).
_BARE_KEY_RES = (
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bgsk_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
)

_MASK = "***"


def _mask_kv(match) -> str:
    # Keep the key, the separator and any opening quote, so a JSON
    # line stays valid JSON with the value replaced by ***.
    return (f"{match.group(1)}{match.group(2)} "
            f"{match.group(3)}{_MASK}")


def redact_secrets(text) -> str:
    """Mask API keys, tokens and passwords in `text`.

    Only shapes that actually look like secrets are masked: a
    key-named field (api_key=... / token=... / password=...), a
    Bearer token, or a bare sk-/gsk_/AIza key. Ordinary words
    (e.g. "password" with no value, or a short token id) are left
    alone, so real diagnostics text is never mangled. Non-strings
    pass through unchanged."""
    if not isinstance(text, str):
        return text
    out = _SECRET_KV_RE.sub(_mask_kv, text)
    out = _GENERIC_KEY_RE.sub(_mask_kv, out)
    out = _BEARER_RE.sub(lambda m: f"{m.group(1)} {_MASK}", out)
    for pattern in _BARE_KEY_RES:
        out = pattern.sub(_MASK, out)
    return out


# ---------------------------------------------------------------------------
# rc.3 (c): 30-day retention
# ---------------------------------------------------------------------------

_LOG_RETENTION_DAYS = 30
_LOG_TIME_FMT = "%Y-%m-%d %H:%M:%S"
# Trim runs at most once per process (on the first log write): the
# files are small, but there is no reason to re-scan them per write.
_trim_done = False


def _parse_log_time(value):
    """A log entry's "time" string -> datetime, or None when it has
    no parseable timestamp (undated entries are KEPT, never dropped)."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), _LOG_TIME_FMT)
    except Exception:
        return None


def trim_old_logs(days: int = _LOG_RETENTION_DAYS,
                  now: datetime | None = None) -> dict:
    """Drop log entries older than `days` from every log file.

    Trimming is by each ENTRY's own timestamp, never by filename:
    an entry with no parseable timestamp is kept (it cannot be
    dated, so deleting it would destroy evidence). A file is never
    emptied entirely -- when every entry in a file is old, the
    newest one is kept, so the log always keeps at least a trace.
    Files with nothing to drop are left untouched (no rewrite).
    Returns {"files_trimmed": int, "entries_dropped": int}. Never
    raises: trimming must never break logging."""
    now = now or datetime.now()
    try:
        cutoff = now - timedelta(days=int(days))
    except Exception:
        return {"files_trimmed": 0, "entries_dropped": 0}
    files_trimmed = 0
    entries_dropped = 0
    try:
        log_dir = _log_dir()
        paths = sorted(log_dir.glob("memo-ai-*.log"))
    except Exception:
        return {"files_trimmed": 0, "entries_dropped": 0}
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8",
                                   errors="replace").splitlines()
        except Exception:
            continue
        kept = []
        dropped = []
        for line in lines:
            stamp = None
            try:
                stamp = _parse_log_time(json.loads(line).get("time"))
            except Exception:
                stamp = None
            if stamp is not None and stamp < cutoff:
                dropped.append(line)
            else:
                kept.append(line)
        if not dropped:
            continue
        if not kept:
            # Never empty a file: keep the newest old entry (lines
            # are chronological, so the last dropped line is it).
            kept = [dropped[-1]]
            dropped = dropped[:-1]
        if not dropped:
            continue
        try:
            tmp = path.with_suffix(".log.tmp")
            tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
            tmp.replace(path)
        except Exception:
            continue
        files_trimmed += 1
        entries_dropped += len(dropped)
    return {"files_trimmed": files_trimmed,
            "entries_dropped": entries_dropped}


def log(level: str, category: str, message: str) -> dict:
    # rc.3 (a): redact BEFORE the entry is built, so the secret
    # never reaches the file -- and the returned entry (which callers
    # may hold or print) is redacted too.
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": level,
        "category": category,
        "message": redact_secrets(message),
    }
    # rc.3 (c): retention trim, at most once per process (cheap:
    # no full re-read per write -- see trim_old_logs).
    global _trim_done
    if not _trim_done:
        _trim_done = True
        try:
            trim_old_logs()
        except Exception:
            pass
    try:
        path = _log_dir() / f"memo-ai-{datetime.now():%Y-%m-%d}.log"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # logging must never crash the app
    return entry


def info(category: str, message: str) -> dict:
    return log("info", category, message)


def warn(category: str, message: str) -> dict:
    return log("warn", category, message)


def error(category: str, message: str) -> dict:
    return log("error", category, message)


def _tail_lines(path: Path, limit: int) -> list:
    """The last `limit` lines of a file, read from the END (seek,
    no full-file parse). Log lines are single JSON objects, so a
    trailing partial line (from a mid-write cut) is dropped."""
    try:
        size = path.stat().st_size
    except Exception:
        return []
    if size == 0 or limit <= 0:
        return []
    chunk = 8192
    data = b""
    pos = size
    lines: list = []
    with open(path, "rb") as f:
        while pos > 0 and len(lines) <= limit:
            step = min(chunk, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
            lines = data.split(b"\n")
            if pos == 0:
                break
            # Enough lines buffered (plus one spare for the cut edge).
            if len(lines) > limit + 1:
                break
    # Drop a first partial line when we did not start at byte 0.
    if pos > 0 and lines:
        lines = lines[1:]
    # v1.6.2 fix: the file ends with a newline, so split() leaves a
    # trailing empty element -- it used to eat one slot of the limit
    # window (asking for the last 5 lines returned 4). Filter
    # empties FIRST, then take the last `limit` real lines.
    out = []
    for raw in lines:
        line = raw.decode("utf-8", errors="replace").strip()
        if line:
            out.append(line)
    return out[-limit:]


def read_recent(limit: int = 200) -> list:
    """Return the most recent log entries (newest last), across today + yesterday.

    v1.6.2 (B16): reads only the TAIL of each log file (seek from the
    end) -- the old version parsed both files fully on every call.
    rc.3 (a): every returned entry is redacted again on the way out,
    so the in-app log viewer can never show a secret even if an
    entry was written before redaction existed (or by another
    writer). Only string fields are redacted; nothing else changes."""
    entries = []
    try:
        files = sorted(_log_dir().glob("memo-ai-*.log"))[-2:]
        for path in files:
            for line in _tail_lines(path, limit):
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    entry = {"time": "", "level": "info",
                             "category": "app", "message": line}
                if isinstance(entry, dict):
                    entry = {k: (redact_secrets(v)
                                 if isinstance(v, str) else v)
                             for k, v in entry.items()}
                entries.append(entry)
    except Exception as exc:
        entries.append({"time": "", "level": "error", "category": "app",
                        "message": f"Could not read logs: {exc}"})
    return entries[-limit:]


def log_text(limit: int = 200) -> str:
    lines = []
    for e in read_recent(limit):
        lines.append(f"[{e.get('time', '')}] [{e.get('level', '')}] "
                     f"[{e.get('category', '')}] {e.get('message', '')}")
    return "\n".join(lines)
