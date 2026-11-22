"""LONA v2 — diagnostics log (day one, redacted).

Every entry is categorized (source) with an exact failure reason.
Secrets are redacted BEFORE they reach the log: the token itself is
removed, not just the scheme word — a "Bearer <token>" line must come
out with the token gone. Nothing in this module ever prints a raw
secret, key, or token.
"""

import re
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

_LEVELS = ("debug", "info", "warn", "error")
_MAX_LINES = 2000

_lock = threading.Lock()
_lines = deque(maxlen=_MAX_LINES)
_log_file = None

# Order matters: the Bearer rule must eat the whole token, and the
# generic key rules must not leave a recognizable remainder behind.
_REDACTIONS = [
    (re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.~+/=]+", re.IGNORECASE),
     r"\1[REDACTED]"),
    (re.compile(r"\bgsk_[A-Za-z0-9_\-]{10,}\b"), "[REDACTED-GROQ-KEY]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}\b"), "[REDACTED-GEMINI-KEY]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{10,}\b"), "[REDACTED-KEY]"),
    (re.compile(r"(api[_-]?key\s*[:=]\s*['\"]?)[^'\"\s,}]+",
                re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(password\s*[:=]\s*['\"]?)[^'\"\s,}]+",
                re.IGNORECASE), r"\1[REDACTED]"),
]


def redact(text) -> str:
    """Remove secrets from a string. Returns the redacted copy."""
    if text is None:
        return ""
    out = str(text)
    for pattern, replacement in _REDACTIONS:
        out = pattern.sub(replacement, out)
    return out


def set_log_file(path) -> None:
    global _log_file
    try:
        _log_file = Path(path)
        _log_file.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        _log_file = None


def _emit(level: str, source: str, message: str) -> None:
    line = (f"[{datetime.now().strftime('%H:%M:%S')}] "
            f"{level.upper():5s} {source}: {redact(message)}")
    with _lock:
        _lines.append(line)
        if _log_file is not None:
            try:
                with _log_file.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except Exception:
                pass


def debug(source: str, message: str) -> None:
    _emit("debug", source, message)


def info(source: str, message: str) -> None:
    _emit("info", source, message)


def warn(source: str, message: str) -> None:
    _emit("warn", source, message)


def error(source: str, message: str) -> None:
    _emit("error", source, message)


def get_lines(count: int = 200):
    """Newest-first lines (at most count)."""
    with _lock:
        return list(_lines)[-max(1, int(count)):]


def log_slow(name: str, seconds: float, threshold: float = 2.0) -> None:
    """Record a slow call once it passes its threshold."""
    if seconds >= threshold:
        warn("slow", f"{name} took {seconds:.1f}s (threshold {threshold}s)")


def timed(name: str, threshold: float = 2.0):
    """Decorator: log slow calls, never change the return value."""
    import functools

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.monotonic()
            try:
                return func(*args, **kwargs)
            finally:
                log_slow(name, time.monotonic() - start, threshold)
        return wrapper
    return decorator
