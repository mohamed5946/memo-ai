"""LONA v2 — encrypted backup (settings export/import).

Payload (settings + tasks + memories + API keys) is encrypted with
scrypt + AES-GCM under the user's password. Wrong password or a
tampered file fails closed with a clear error — never a partial
restore.
"""

import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 32


def _derive(password: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=_KEY_LEN, n=_SCRYPT_N,
                 r=_SCRYPT_R, p=_SCRYPT_P)
    return kdf.derive(password.encode("utf-8"))


def export_backup(password: str, payload: dict) -> dict:
    """Returns {ok, data} where data is a base64 blob, or {ok, error}."""
    if not password or len(password) < 6:
        return {"ok": False, "error": "الباسورد لازم 6 حروف على الأقل"}
    try:
        salt = os.urandom(16)
        nonce = os.urandom(12)
        key = _derive(password, salt)
        plain = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ct = AESGCM(key).encrypt(nonce, plain, None)
        blob = base64.b64encode(salt + nonce + ct).decode()
        return {"ok": True, "data": blob}
    except Exception as exc:
        from .logger import error
        error("app", f"Backup export failed: {exc}")
        return {"ok": False, "error": "مقدرتش أعمل النسخة"}


def import_backup(password: str, blob: str) -> dict:
    """Returns {ok, payload} or {ok, error}."""
    if not password:
        return {"ok": False, "error": "اكتب الباسورد الأول"}
    try:
        raw = base64.b64decode(blob)
        salt, nonce, ct = raw[:16], raw[16:28], raw[28:]
        key = _derive(password, salt)
        plain = AESGCM(key).decrypt(nonce, ct, None)
        payload = json.loads(plain.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("bad payload")
        return {"ok": True, "payload": payload}
    except Exception:
        return {"ok": False,
                "error": "الباسورد غلط أو الملف مش نسخة احتياطية صالحة"}
