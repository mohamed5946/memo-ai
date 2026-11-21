# LONA v1.6.0 - API keys entered manually in Settings (NO bundled key).
#
# STORAGE (v1.6.0, scope item 2): keys live in the WINDOWS CREDENTIAL
# MANAGER (DPAPI) via advapi32 CredWrite/CredRead through ctypes
# (stdlib only, no new dependency); target names "LONA:groq" and
# "LONA:gemini". Plaintext key files are gone from the design.
#
# MIGRATION: on first key read, if keys/*.txt files exist (e.g. the
# installer migrated them from an old zip install into keys/), they are
# moved into Credential Manager, the read-back is verified, and only
# then are the txt files deleted and the migration logged. If
# verification fails, the txt files are KEPT and a warning is logged.
# On non-Windows (dev/tests only) the old keys/*.txt path is used with
# a warning - Credential Manager does not exist there.
#
# No key value is ever logged, printed, or returned - only
# presence/validity. test_key() does a tiny live presence check with a
# 15s timeout.

import os
import sys

from logger import error, info, warn

PROVIDER_FILES = {
    "groq": "groq-key.txt",
    "gemini": "gemini-key.txt",
}

CRED_TARGETS = {
    "groq": "LONA:groq",
    "gemini": "LONA:gemini",
}

GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"

_TEST_TIMEOUT = 15.0

# Migration runs at most once per process (first key read).
_migration_done = False


def _keys_dir():
    # Resolved lazily (tests redirect settings_store.KEYS_DIR).
    from settings_store import KEYS_DIR
    return KEYS_DIR


def _normalize_provider(provider: str) -> str | None:
    return str(provider or "").strip().lower() or None


# ---------------------------------------------------------------------------
# Windows Credential Manager (advapi32 CredWriteW / CredReadW, ctypes)
# ---------------------------------------------------------------------------

def _cred_available() -> bool:
    return sys.platform == "win32"


def _cred_write(provider: str, value: str) -> bool:
    """Write one key to Credential Manager. Returns True on success.
    Never logs the value. Windows only - False elsewhere."""
    if not _cred_available():
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class _FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD),
                        ("dwHighDateTime", wintypes.DWORD)]

        class _CREDENTIALW(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", _FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        advapi32 = ctypes.windll.advapi32
        blob = value.encode("utf-8")
        buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred = _CREDENTIALW()
        cred.Flags = 0
        cred.Type = 1  # CRED_TYPE_GENERIC
        cred.TargetName = CRED_TARGETS[provider]
        cred.Comment = None
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
        cred.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE
        cred.AttributeCount = 0
        cred.Attributes = None
        cred.TargetAlias = None
        cred.UserName = None
        ok = advapi32.CredWriteW(ctypes.byref(cred), 0)
        if not ok:
            raise OSError(f"CredWriteW failed (error "
                          f"{ctypes.get_last_error()})")
        return True
    except Exception as exc:
        error("keys", f"Could not store {provider} key in Credential "
                      f"Manager: {exc}")
        return False


def _cred_read(provider: str) -> str:
    """Read one key from Credential Manager, or "" when absent/failed.
    Never logs the value. Windows only - "" elsewhere."""
    if not _cred_available():
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        class _FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD),
                        ("dwHighDateTime", wintypes.DWORD)]

        class _CREDENTIALW(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", _FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        advapi32 = ctypes.windll.advapi32
        out = ctypes.POINTER(_CREDENTIALW)()
        ok = advapi32.CredReadW(CRED_TARGETS[provider], 1, 0,
                                ctypes.byref(out))
        if not ok:
            return ""  # absent (or unreadable) - presence check only
        try:
            cred = out.contents
            size = int(cred.CredentialBlobSize)
            if size <= 0 or not cred.CredentialBlob:
                return ""
            raw = bytes(bytearray(cred.CredentialBlob[:size]))
            return raw.decode("utf-8", errors="strict").strip()
        finally:
            advapi32.CredFree(ctypes.cast(out, ctypes.c_void_p))
    except Exception as exc:
        warn("keys", f"Could not read {provider} key from Credential "
                     f"Manager: {exc}")
        return ""


def _cred_delete(provider: str) -> bool:
    """Delete one Credential Manager entry (best effort)."""
    if not _cred_available():
        return False
    try:
        import ctypes
        advapi32 = ctypes.windll.advapi32
        return bool(advapi32.CredDeleteW(CRED_TARGETS[provider], 1, 0))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Plaintext fallback (non-Windows dev/tests only) + migration
# ---------------------------------------------------------------------------

def _txt_path(provider: str):
    return _keys_dir() / PROVIDER_FILES[provider]


def _txt_read(provider: str) -> str:
    """Read keys/<provider>-key.txt directly (first line only). This is
    the non-Windows fallback AND the migration source - it never goes
    through settings_store.read_key (that would recurse: read_key now
    delegates here)."""
    try:
        path = _txt_path(provider)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except Exception:
        return ""


def _txt_write(provider: str, value: str) -> bool:
    try:
        keys_dir = _keys_dir()
        keys_dir.mkdir(parents=True, exist_ok=True)
        path = _txt_path(provider)
        path.write_text(value + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass  # Windows has no POSIX modes - best effort only
        return True
    except Exception as exc:
        error("keys", f"Could not write {provider} key file: {exc}")
        return False


def migrate_txt_keys() -> dict:
    """Move any keys/*.txt files into Credential Manager (Windows
    only). Per provider: write to the vault, verify by reading back,
    delete the txt file ONLY when the read-back matches. A failed
    verification keeps the txt file and logs a warning. Returns
    {provider: "migrated" | "kept" | "absent" | "unsupported"}."""
    global _migration_done
    _migration_done = True
    results = {}
    if not _cred_available():
        for provider in PROVIDER_FILES:
            results[provider] = "unsupported"
        return results
    for provider in PROVIDER_FILES:
        txt_value = _txt_read(provider)
        if not txt_value:
            results[provider] = "absent"
            continue
        if not _cred_write(provider, txt_value):
            warn("keys", f"Migration of the {provider} key failed at "
                         "write - keeping the txt file")
            results[provider] = "kept"
            continue
        if _cred_read(provider) != txt_value:
            warn("keys", f"Migration of the {provider} key failed "
                         "verification - keeping the txt file")
            results[provider] = "kept"
            continue
        try:
            _txt_path(provider).unlink(missing_ok=True)
        except Exception as exc:
            warn("keys", f"Migrated {provider} key but could not delete "
                         f"the txt file: {exc}")
        info("keys", f"Migrated the {provider} key from keys/*.txt into "
                     "Credential Manager and deleted the txt file")
        results[provider] = "migrated"
    return results


def _maybe_migrate() -> None:
    global _migration_done
    if _migration_done:
        return
    _migration_done = True
    if not _cred_available():
        return
    try:
        migrate_txt_keys()
    except Exception as exc:
        warn("keys", f"Key migration failed: {exc}")


def reset_migration_flag() -> None:
    """Tests only: allow the once-per-process migration to run again."""
    global _migration_done
    _migration_done = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_key_value(provider: str) -> str:
    """Read one key from its real store: Credential Manager on Windows
    (migrating any txt files first), keys/*.txt elsewhere. Returns ""
    when absent. The value is returned to the caller only - never logged."""
    provider = _normalize_provider(provider)
    if provider not in PROVIDER_FILES:
        return ""
    _maybe_migrate()
    if _cred_available():
        value = _cred_read(provider)
        if value:
            return value
        # Vault empty: fall back to a txt file that migration could not
        # move (kept + warned) rather than losing the key.
        txt_value = _txt_read(provider)
        if txt_value:
            warn("keys", f"{provider} key read from keys/*.txt fallback "
                         "(Credential Manager copy missing)")
        return txt_value
    warn("keys", f"{provider} key read from keys/*.txt (non-Windows "
                 "fallback - Credential Manager is Windows-only)")
    return _txt_read(provider)


def save_key(provider: str, value: str) -> dict:
    """Save one key (first line only, whitespace stripped). Windows:
    Credential Manager, verified by read-back, then any txt copy is
    deleted so no plaintext copy remains. Elsewhere: keys/*.txt mode
    0o600. Returns {"ok", "provider", "present"}. The value itself is
    never logged - only that a key was saved for the provider."""
    provider = _normalize_provider(provider)
    if provider not in PROVIDER_FILES:
        return {"ok": False, "provider": provider or "",
                "present": False, "error": "unknown provider"}
    text = str(value or "").strip().splitlines()
    cleaned = text[0].strip() if text else ""
    if not cleaned:
        return {"ok": False, "provider": provider, "present": False,
                "error": "empty key"}
    _maybe_migrate()
    if _cred_available():
        if not _cred_write(provider, cleaned):
            return {"ok": False, "provider": provider, "present": False,
                    "error": "could not store in Credential Manager"}
        if _cred_read(provider) != cleaned:
            error("keys", f"{provider} key verification failed after save")
            return {"ok": False, "provider": provider, "present": False,
                    "error": "verification failed after save"}
        try:
            _txt_path(provider).unlink(missing_ok=True)
        except Exception:
            pass
        info("keys", f"API key saved for {provider} in Credential Manager "
                     "(value never logged)")
        return {"ok": True, "provider": provider, "present": True}
    if not _txt_write(provider, cleaned):
        return {"ok": False, "provider": provider, "present": False,
                "error": "could not write key file"}
    info("keys", f"API key saved for {provider} (txt fallback, "
                 "value never logged)")
    return {"ok": True, "provider": provider, "present": True}


def delete_key(provider: str) -> dict:
    """Delete one key from both stores (vault + txt)."""
    provider = _normalize_provider(provider)
    if provider not in PROVIDER_FILES:
        return {"ok": False, "provider": provider or ""}
    _cred_delete(provider)
    try:
        _txt_path(provider).unlink(missing_ok=True)
    except Exception:
        pass
    info("keys", f"API key deleted for {provider}")
    return {"ok": True, "provider": provider}


def get_api_keys_status() -> dict:
    """Presence only, per provider - never values."""
    return {provider: bool(read_key_value(provider))
            for provider in PROVIDER_FILES}


def test_key(provider: str, timeout: float = _TEST_TIMEOUT) -> dict:
    """Tiny live validity check (15s timeout). Groq: GET /models with
    the key as a Bearer token. Gemini: GET /v1beta/models with the key
    as a query param (the API's own auth scheme - the key is sent only
    to Google, never logged). Returns {"ok", "provider", "valid",
    "detail"} - "valid" is False on any failure, with the reason named
    (no key / network / HTTP status), never the key value."""
    provider = _normalize_provider(provider)
    if provider not in PROVIDER_FILES:
        return {"ok": False, "provider": provider or "", "valid": False,
                "detail": "unknown provider"}
    key = read_key_value(provider)
    if not key:
        return {"ok": False, "provider": provider, "valid": False,
                "detail": "no key saved for this provider"}
    try:
        import requests
    except Exception as exc:
        return {"ok": False, "provider": provider, "valid": False,
                "detail": f"network library unavailable: {exc}"}
    try:
        if provider == "groq":
            resp = requests.get(
                GROQ_MODELS_URL,
                headers={"Authorization": f"Bearer {key}"},
                timeout=timeout)
        else:
            resp = requests.get(
                GEMINI_MODELS_URL, params={"key": key}, timeout=timeout)
    except Exception as exc:
        warn("keys", f"{provider} key test failed "
                     f"({type(exc).__name__}: {exc})")
        return {"ok": False, "provider": provider, "valid": False,
                "detail": f"request failed ({type(exc).__name__})"}
    if resp.status_code == 200:
        info("keys", f"{provider} key test OK (HTTP 200)")
        return {"ok": True, "provider": provider, "valid": True,
                "detail": "key accepted by the provider"}
    if resp.status_code in (401, 403):
        warn("keys", f"{provider} key test rejected (HTTP {resp.status_code})")
        return {"ok": False, "provider": provider, "valid": False,
                "detail": "key rejected by the provider (check the key)"}
    warn("keys", f"{provider} key test HTTP {resp.status_code}")
    return {"ok": False, "provider": provider, "valid": False,
            "detail": f"provider returned HTTP {resp.status_code}"}
