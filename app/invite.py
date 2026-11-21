# LONA v1.6.0 - First-install invite code (local check only, NO server).
#
# The code is handled as a STRING everywhere (a leading zero matters).
# ONLY the SHA-256 hex of the code lives in source - the plaintext code
# value is never written, logged, printed, committed, or put in a test
# or a comment anywhere. Never try to recover it.
#
# Asked ONCE only at first install: main.py shows the invite screen
# only when is_activated() is False, never again after activation, and
# never re-asked on updates. The activation flag lives in
# app/data/activated.json: {"activated": true, "at": ISO timestamp}.

import hashlib
import hmac
import json
from datetime import datetime

# SHA-256 hex of the 6-digit trial code. THIS HASH IS THE ONLY THING
# THAT GOES IN SOURCE.
INVITE_CODE_HASH = ("ce0e90a75246a53c921aaf19e18f179e2f243c6442214b34ffb60"
                    "91cc5e6bf03")


def _activated_path():
    # Resolved lazily (tests redirect settings_store.DATA_DIR).
    from settings_store import DATA_DIR
    return DATA_DIR / "activated.json"


def check(code: str) -> bool:
    """True only for the exact 6-digit code (string compare, leading
    zero significant). Rejects anything that is not exactly 6 digits -
    wrong length, non-digits, empty, None - without ever revealing
    which part was wrong."""
    try:
        text = str(code or "").strip()
    except Exception:
        return False
    if len(text) != 6 or not text.isdigit():
        return False
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return hmac.compare_digest(digest, INVITE_CODE_HASH)


def is_activated() -> bool:
    """Has this install been activated? Missing/corrupt flag = not
    activated (the invite screen shows once)."""
    try:
        path = _activated_path()
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        return bool(isinstance(data, dict) and data.get("activated") is True)
    except Exception:
        return False


def mark_activated() -> bool:
    """Record activation (local flag only). Returns True when written."""
    try:
        from logger import info
    except Exception:
        def info(*args, **kwargs):
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
        try:
            from logger import error
            error("app", f"Could not write activation flag: {exc}")
        except Exception:
            pass
        return False
