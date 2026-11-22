"""LONA v2 — first-install invite gate (local check only, no server).

The code is handled as a STRING everywhere (a leading zero matters).
ONLY the SHA-256 hex of the code lives in source — the plaintext code
value is never written, logged, printed, committed, or put in a test
or a comment anywhere. Never try to recover it.

Rules from the brief: 5 wrong attempts -> 60s lockout; a failed
activation-file write must NOT fail a correct code.
"""

import hashlib
import hmac
import json
import threading
import time
from datetime import datetime
from pathlib import Path

# SHA-256 hex of the 6-digit trial code. THIS HASH IS THE ONLY THING
# THAT GOES IN SOURCE.
INVITE_CODE_HASH = ("ce0e90a75246a53c921aaf19e18f179e2f243c6442214b34ffb60"
                    "91cc5e6bf03")

CODE_LENGTH = 6
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 60

_lock = threading.Lock()
_failures = 0
_locked_until = 0.0

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _activated_path() -> Path:
    from .stores import DATA_DIR
    return DATA_DIR / "activated.json"


def normalize(raw) -> str | None:
    """Strip + translate Arabic-Indic digits. Returns the 6-digit
    string or None when the shape is wrong."""
    try:
        text = str(raw or "").strip().translate(_ARABIC_DIGITS)
    except Exception:
        return None
    if len(text) != CODE_LENGTH or not text.isdigit():
        return None
    return text


def _is_locked() -> bool:
    return time.monotonic() < _locked_until


def verify(raw) -> str:
    """'ok' | 'format' | 'invalid' | 'locked'. A correct code returns
    'ok' even when the activation file cannot be written."""
    global _failures, _locked_until
    with _lock:
        if _is_locked():
            return "locked"
        code = normalize(raw)
        if code is None:
            return "format"
        digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(digest, INVITE_CODE_HASH):
            _failures += 1
            if _failures >= MAX_ATTEMPTS:
                _locked_until = time.monotonic() + LOCKOUT_SECONDS
                _failures = 0
            return "invalid"
        _failures = 0
        _mark_activated()  # failure here must not fail the code
        return "ok"


def _mark_activated() -> bool:
    """Record activation (local flag only). Never raises."""
    try:
        from .logger import error, info
    except Exception:
        def info(*a, **k):
            return None

        def error(*a, **k):
            return None
    try:
        path = _activated_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"activated": True,
             "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")},
            ensure_ascii=False), encoding="utf-8")
        info("app", "Invite code accepted - install activated")
        return True
    except Exception as exc:
        error("app", f"Could not write activation flag: {exc}")
        return False


def is_activated() -> bool:
    """Missing/corrupt flag = not activated (invite screen shows once)."""
    try:
        path = _activated_path()
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        return bool(isinstance(data, dict) and data.get("activated") is True)
    except Exception:
        return False


def reset_lockout_for_tests() -> None:
    """Test hook only: clear failures + lockout."""
    global _failures, _locked_until
    with _lock:
        _failures = 0
        _locked_until = 0.0
