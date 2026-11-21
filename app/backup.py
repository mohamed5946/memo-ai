# LONA v1.6.0 - Settings backup export / import (scope item 4).
#
# export_settings_backup(path, password) -> a ZIP containing:
#   settings.json  (plain - settings hold no secrets)
#   keys.enc       (Groq + Gemini keys, encrypted with a password-derived
#                   Fernet key: PBKDF2HMAC-SHA256, 100k iterations)
#   manifest.json  ({"salt": base64, "kdf": "PBKDF2HMAC-SHA256",
#                    "iterations": 100000, "version": 1})
# import_settings_backup(path, password) restores settings + keys into
# Credential Manager (via apikeys.save_key). The password is never
# logged, never stored, and only ever lives in the caller's memory.
# A wrong password fails cleanly (invalid token) without touching the
# current settings or keys.

import base64
import io
import json
import zipfile
from pathlib import Path

from logger import error, info, warn

_KDF_ITERATIONS = 100_000
_MANIFEST_VERSION = 1


def _derive_key(password: str, salt: bytes) -> bytes:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=_KDF_ITERATIONS)
    return base64.urlsafe_b64encode(
        kdf.derive(str(password or "").encode("utf-8")))


def export_settings_backup(path: str, password: str) -> dict:
    """Export settings + encrypted keys to a ZIP. Returns {"ok", "path",
    "detail"}. The password is never logged."""
    if not password:
        return {"ok": False, "path": "", "detail": "password_required"}
    try:
        from settings_store import DATA_DIR, SETTINGS_PATH, load_settings
        import apikeys
    except Exception as exc:
        return {"ok": False, "path": "", "detail": str(exc)}
    try:
        import os
        from cryptography.fernet import Fernet
        settings = load_settings()
        keys = {provider: apikeys.read_key_value(provider)
                for provider in apikeys.PROVIDER_FILES}
        keys = {k: v for k, v in keys.items() if v}
        salt = os.urandom(16)
        token = Fernet(_derive_key(password, salt)).encrypt(
            json.dumps(keys, ensure_ascii=False).encode("utf-8"))
        manifest = {"salt": base64.b64encode(salt).decode("ascii"),
                    "kdf": "PBKDF2HMAC-SHA256",
                    "iterations": _KDF_ITERATIONS,
                    "version": _MANIFEST_VERSION}
        out = Path(str(path or "").strip())
        out.parent.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("settings.json", json.dumps(
                settings, ensure_ascii=False, indent=2))
            zf.writestr("keys.enc", token)
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        out.write_bytes(buf.getvalue())
        info("backup", f"Settings backup exported ({out.name}, "
                       f"{len(keys)} key(s) encrypted)")
        return {"ok": True, "path": str(out),
                "detail": "backup_written"}
    except Exception as exc:
        error("backup", f"Export failed: {exc}")
        return {"ok": False, "path": "", "detail": str(exc)}


def import_settings_backup(path: str, password: str) -> dict:
    """Import a backup ZIP. Wrong password / corrupt file -> {"ok":
    False} and NOTHING is changed (settings + keys stay as they were).
    On success: settings are restored (only known keys) and keys go
    into Credential Manager."""
    if not password:
        return {"ok": False, "detail": "password_required"}
    try:
        from settings_store import DEFAULTS, save_settings
        import apikeys
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}
    try:
        from cryptography.fernet import Fernet, InvalidToken
        src = Path(str(path or "").strip())
        if not src.is_file():
            return {"ok": False, "detail": "file_not_found"}
        with zipfile.ZipFile(src) as zf:
            names = set(zf.namelist())
            if not {"settings.json", "keys.enc", "manifest.json"} <= names:
                return {"ok": False, "detail": "not_a_lona_backup"}
            manifest = json.loads(zf.read("manifest.json"))
            salt = base64.b64decode(manifest["salt"])
            token = zf.read("keys.enc")
            settings_raw = json.loads(zf.read("settings.json"))
        try:
            keys_raw = Fernet(_derive_key(password, salt)).decrypt(token)
            keys = json.loads(keys_raw.decode("utf-8"))
        except InvalidToken:
            warn("backup", "Import failed: wrong password")
            return {"ok": False, "detail": "wrong_password"}
        if not isinstance(settings_raw, dict) or not isinstance(keys, dict):
            return {"ok": False, "detail": "corrupt_backup"}
        # Settings first (only known keys, never owner_unlocked).
        safe = {k: v for k, v in settings_raw.items() if k in DEFAULTS
                and k != "owner_unlocked"}
        if not save_settings(safe):
            return {"ok": False, "detail": "settings_save_failed"}
        restored = []
        for provider, value in keys.items():
            if provider not in apikeys.PROVIDER_FILES or not value:
                continue
            result = apikeys.save_key(provider, str(value))
            if result.get("ok"):
                restored.append(provider)
        info("backup", f"Settings backup imported ({len(restored)} key(s) "
                       "restored)")
        return {"ok": True, "detail": "backup_restored",
                "keys_restored": restored}
    except Exception as exc:
        error("backup", f"Import failed: {exc}")
        return {"ok": False, "detail": str(exc)}
