# Memo AI - Diagnostics logger (standing rule: logs from day one)
# Writes categorized entries to app/data/logs/memo-ai-YYYY-MM-DD.log (JSON lines).
# Levels: info / warn / error. Categories: app, brain, hear, speak, net,
# keys, weather, ui, rules. Exact failure reasons are always recorded.

import json
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "data" / "logs"


def _log_dir() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR


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


def read_recent(limit: int = 200) -> list:
    """Return the most recent log entries (newest last), across today + yesterday."""
    entries = []
    try:
        files = sorted(_log_dir().glob("memo-ai-*.log"))[-2:]
        for path in files:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
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
