# Memo AI - Settings + API key handling
# Keys live ONLY in ../keys/*.txt (one key per file, first line only).
# No key is ever written into source code, settings, or logs.

import json
import sys
from pathlib import Path

from logger import error, warn


def resolve_app_paths() -> tuple:
    """ONE frozen-aware path helper, used by every module.

    - Normal run (zip / source): APP_DIR = app/ next to this file,
      BASE_DIR = the memo-ai project folder.
    - Frozen run (PyInstaller one-folder install): the bootloader extracts
      the bundle into _internal/ next to the exe, so APP_DIR =
      <exe dir>/_internal/app (via sys._MEIPASS) and BASE_DIR = <exe dir>.
      keys/ stays OUTSIDE _internal — it lives next to the exe
      (BASE_DIR/keys), exactly like the zip install, so users can see
      and edit their key files. (PyInstaller 6 puts ALL collected files
      under _internal/; only the exe sits at the bundle root.)
    In both cases the layout works the same for keys/ and app/data/.
    """
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        meipass = Path(getattr(sys, "_MEIPASS", exe_dir / "_internal"))
        return meipass / "app", exe_dir
    app_dir = Path(__file__).resolve().parent
    return app_dir, app_dir.parent


APP_DIR, BASE_DIR = resolve_app_paths()
KEYS_DIR = BASE_DIR / "keys"
DATA_DIR = APP_DIR / "data"
SETTINGS_PATH = DATA_DIR / "settings.json"

DEFAULTS = {
    "ai_name": "Memo",
    "user_name": "Mohamed",
    "language": "ar",            # "ar" or "en"
    "voice": "girl_warm",        # boy_calm | boy_fun | girl_warm | girl_fast
    "voice_output": True,        # v1.3.0: speaker toggle - speak replies?
    "greeting_enabled": True,
    "avatar": "cat",
    # v1.3.1: the pet is HIDDEN by default - it only ever appears when
    # the user shows it (Settings toggle / 🐾 button), never on its own
    # at startup or at Windows boot. The state persists once he chooses.
    "pet_visible": False,
    "pet_parked": False,
    # v1.3.1: autostart with Windows (HKCU Run key), toggleable, ON.
    "autostart": True,
    # v1.3.1 internal: one-time migration - existing installs saved
    # pet_visible=True, so the new hidden-by-default rule is applied
    # once on the first v1.3.1 run, then the user's own choice rules.
    "pet_default_v131": False,
    # v1.4.1: the pet + island are DISABLED by the user's decision
    # (2026-11-07: "they still have problems") - their windows are
    # NEVER created and no pet/island code runs at startup. The code
    # stays behind these flags (not deleted) so they can come back
    # once fixed - flip them in settings.json to re-enable.
    "pet_enabled": False,
    "island_enabled": False,
    # v1.3.0: NO owner_code setting anymore. The owner code is stored
    # ONLY as a SHA-256 hash in main.py (OWNER_CODE_HASH) - never as
    # plaintext in settings.json, logs, or anywhere else.
    "owner_unlocked": False,     # session flag, never persisted as true
    # v1.4.0: WhatsApp linking (Baileys sidecar, QR, 3rd number only).
    "whatsapp_enabled": False,   # off by default - opt in from Settings
    # The owner's MAIN number (digits, e.g. 01557887719) - owner mode
    # over WhatsApp. Empty = nobody is owner, everyone gets reply mode.
    "whatsapp_owner_phone": "",
}


def load_settings() -> dict:
    settings = dict(DEFAULTS)
    try:
        if SETTINGS_PATH.exists():
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                settings.update({k: v for k, v in saved.items() if k in DEFAULTS})
    except Exception as exc:
        error("app", f"Could not load settings.json, using defaults: {exc}")
    settings["owner_unlocked"] = False  # owner mode never survives a restart
    return settings


def save_settings(settings: dict) -> bool:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        safe = {k: v for k, v in settings.items()
                if k in DEFAULTS and k != "owner_unlocked"}
        SETTINGS_PATH.write_text(json.dumps(safe, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        return True
    except Exception as exc:
        error("app", f"Could not save settings.json: {exc}")
        return False


def read_key(name: str, quiet: bool = False) -> str:
    """Read a key file (e.g. 'groq-key.txt') from ../keys/. Returns '' if missing."""
    path = KEYS_DIR / name
    try:
        if not path.exists():
            if not quiet:
                warn("keys", f"Key file missing: keys/{name} "
                     f"(add it to use that feature)")
            return ""
        value = path.read_text(encoding="utf-8").strip()
        if not value:
            if not quiet:
                warn("keys", f"Key file is empty: keys/{name}")
            return ""
        return value
    except Exception as exc:
        error("keys", f"Could not read keys/{name}: {exc}")
        return ""


def read_azure() -> tuple:
    """Azure key file: line 1 = key, line 2 = region. Returns (key, region)."""
    path = KEYS_DIR / "azure-key.txt"
    try:
        if not path.exists():
            return "", ""
        lines = [ln.strip() for ln in
                 path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        key = lines[0] if len(lines) > 0 else ""
        region = lines[1] if len(lines) > 1 else ""
        if key and not region:
            warn("keys", "azure-key.txt has a key but no region on line 2")
        return key, region
    except Exception as exc:
        error("keys", f"Could not read keys/azure-key.txt: {exc}")
        return "", ""


def keys_status() -> dict:
    """Which optional keys are present (presence only, never values)."""
    groq = bool(read_key("groq-key.txt", quiet=True))
    gemini = bool(read_key("gemini-key.txt", quiet=True))
    azure_key, azure_region = read_azure()
    return {
        "groq": groq,
        "gemini": gemini,
        "azure": bool(azure_key and azure_region),
    }
