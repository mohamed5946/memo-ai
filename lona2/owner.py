"""LONA v2 — owner code (local, salted PBKDF2).

The owner code is a 6-digit string (leading zero matters). It is
stored as salt + PBKDF2-HMAC-SHA256 with 600,000 iterations — the
OWASP floor for PBKDF2-HMAC-SHA256 — never as plaintext. The code
value is never logged.
"""

import hashlib
import hmac
import json
import os
from pathlib import Path

ITERATIONS = 600_000
SALT_BYTES = 16


def _owner_path() -> Path:
    from .stores import DATA_DIR
    return DATA_DIR / "owner.json"


def set_owner_code(code: str) -> dict:
    """Store a new owner code. Returns {ok} or {ok, error}."""
    try:
        text = str(code or "").strip()
    except Exception:
        text = ""
    if len(text) != 6 or not text.isdigit():
        return {"ok": False, "error": "الكود لازم 6 أرقام"}
    salt = os.urandom(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", text.encode("utf-8"),
                                 salt, ITERATIONS)
    try:
        path = _owner_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"salt": salt.hex(), "hash": digest.hex(),
             "iterations": ITERATIONS}, ensure_ascii=False),
            encoding="utf-8")
        return {"ok": True}
    except Exception as exc:
        from .logger import error
        error("app", f"Could not store owner code: {exc}")
        return {"ok": False, "error": "مقدرتش أحفظ الكود"}


def check_owner_code(code: str) -> bool:
    """True only when the code matches the stored hash."""
    try:
        path = _owner_path()
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        salt = bytes.fromhex(data["salt"])
        expected = bytes.fromhex(data["hash"])
        iterations = int(data.get("iterations", ITERATIONS))
        text = str(code or "").strip()
        digest = hashlib.pbkdf2_hmac("sha256", text.encode("utf-8"),
                                     salt, iterations)
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False


def has_owner_code() -> bool:
    try:
        return _owner_path().exists()
    except Exception:
        return False
