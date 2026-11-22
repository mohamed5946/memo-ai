"""LONA v2 — API keys.

Windows: stored in Windows Credential Manager (DPAPI) via CredWrite /
CredRead, so the keys never sit in a plain file. Other platforms
(Linux CI/tests only): an AES-GCM file under DATA_DIR, clearly marked
as a fallback — the real app is Windows-only.

A saved key's VALUE is revealed only on the user's explicit "show"
press (get_api_key_value) and is never written to any log.
"""

import base64
import ctypes
import json
import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

PROVIDERS = ("groq", "gemini_voice", "gemini_chat")

# --- Windows Credential Manager (DPAPI) -----------------------------------
if IS_WINDOWS:
    _advapi32 = ctypes.windll.advapi32

    class _CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", ctypes.c_ulong),
            ("Type", ctypes.c_ulong),
            ("TargetName", ctypes.c_wchar_p),
            ("Comment", ctypes.c_wchar_p),
            ("LastWritten", ctypes.c_ulonglong),
            ("CredentialBlobSize", ctypes.c_ulong),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", ctypes.c_ulong),
            ("AttributeCount", ctypes.c_ulong),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", ctypes.c_wchar_p),
            ("UserName", ctypes.c_wchar_p),
        ]

    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2


def _target(provider: str) -> str:
    return f"LONA/api-key/{provider}"


def _fallback_path() -> Path:
    from .stores import DATA_DIR
    return DATA_DIR / "keys-fallback.json"


def _fallback_key() -> bytes:
    """Machine-scoped key for the non-Windows fallback only."""
    import hashlib
    seed = (os.environ.get("USER", "") + "|" +
            os.environ.get("HOSTNAME", "") + "|lona-v2-fallback").encode()
    return hashlib.sha256(seed).digest()


def _fallback_save(provider: str, value: str) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    path = _fallback_path()
    data = {}
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    nonce = os.urandom(12)
    ct = AESGCM(_fallback_key()).encrypt(nonce, value.encode("utf-8"), None)
    data[provider] = base64.b64encode(nonce + ct).decode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _fallback_load(provider: str):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        path = _fallback_path()
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = base64.b64decode(data[provider])
        return AESGCM(_fallback_key()).decrypt(raw[:12], raw[12:], None).decode("utf-8")
    except Exception:
        return None


def _fallback_delete(provider: str) -> None:
    try:
        path = _fallback_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            data.pop(provider, None)
            path.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def save_api_key(provider: str, value: str) -> dict:
    if provider not in PROVIDERS:
        return {"ok": False, "error": "provider غير معروف"}
    value = (value or "").strip()
    if not value:
        return {"ok": False, "error": "اكتب المفتاح الأول"}
    if not IS_WINDOWS:
        try:
            _fallback_save(provider, value)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    try:
        blob = value.encode("utf-16-le")
        cred = _CREDENTIAL()
        cred.Flags = 0
        cred.Type = _CRED_TYPE_GENERIC
        cred.TargetName = _target(provider)
        cred.Comment = "LONA API key"
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ctypes.cast(
            ctypes.create_string_buffer(blob, len(blob)),
            ctypes.POINTER(ctypes.c_ubyte))
        cred.Persist = _CRED_PERSIST_LOCAL_MACHINE
        cred.UserName = "LONA"
        if not _advapi32.CredWriteW(ctypes.byref(cred), 0):
            raise OSError(f"CredWrite failed: {ctypes.get_last_error()}")
        return {"ok": True}
    except Exception as exc:
        from .logger import error
        error("app", f"Could not save API key for {provider}: {exc}")
        return {"ok": False, "error": "مقدرتش أحفظ المفتاح"}


def get_api_key_value(provider: str):
    """The raw value — call ONLY on the user's explicit 'show' press."""
    if provider not in PROVIDERS:
        return None
    if not IS_WINDOWS:
        return _fallback_load(provider)
    try:
        out = ctypes.POINTER(_CREDENTIAL)()
        if not _advapi32.CredReadW(_target(provider), _CRED_TYPE_GENERIC,
                                   0, ctypes.byref(out)):
            return None
        try:
            cred = out.contents
            size = cred.CredentialBlobSize
            raw = bytes(cred.CredentialBlob[i] for i in range(size))
            return raw.decode("utf-16-le")
        finally:
            _advapi32.CredFree(ctypes.cast(out, ctypes.c_void_p))
    except Exception:
        return None


def has_api_key(provider: str) -> bool:
    return get_api_key_value(provider) not in (None, "")


def get_api_keys_status() -> dict:
    return {p: has_api_key(p) for p in PROVIDERS}


def delete_api_key(provider: str) -> dict:
    if provider not in PROVIDERS:
        return {"ok": False, "error": "provider غير معروف"}
    if not IS_WINDOWS:
        _fallback_delete(provider)
        return {"ok": True}
    try:
        _advapi32.CredDeleteW(_target(provider), _CRED_TYPE_GENERIC, 0)
        return {"ok": True}
    except Exception:
        return {"ok": False, "error": "مقدرتش أمسح المفتاح"}
