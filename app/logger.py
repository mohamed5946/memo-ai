# Memo AI - Diagnostics logger (standing rule: logs from day one)
# Writes categorized entries to app/data/logs/memo-ai-YYYY-MM-DD.log (JSON lines).
# Levels: info / warn / error. Categories: app, brain, hear, speak, net,
# keys, weather, ui, rules. Exact failure reasons are always recorded.

import json
from datetime import datetime
from pathlib import Path

# LOG_DIR is resolved lazily (see _log_dir): logger is imported by
# settings_store, and settings_store owns the frozen-aware path helper,
# so importing it here at module level would be a circular import.


def _log_dir() -> Path:
    from settings_store import APP_DIR  # local import: no circular import
    log_dir = APP_DIR / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def log(level: str, category: str, message: str) -> dict:
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": level,
        "category": category,
        "message": message,
    }
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
    end) -- the old version parsed both files fully on every call."""
    entries = []
    try:
        files = sorted(_log_dir().glob("memo-ai-*.log"))[-2:]
        for path in files:
            for line in _tail_lines(path, limit):
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    entries.append({"time": "", "level": "info",
                                    "category": "app", "message": line})
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
