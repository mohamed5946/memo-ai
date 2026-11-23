# Memo AI - Settings + API key handling
# Keys live ONLY in ../keys/*.txt (one key per file, first line only).
# No key is ever written into source code, settings, or logs.

import json
import sys
from pathlib import Path

from logger import error, info, warn


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
    "ai_name": "LONA",
    "user_name": "Mohamed",
    "language": "ar",            # "ar" or "en"
    "voice": "girl_warm",        # boy_calm | boy_fun | girl_warm | girl_fast
    "voice_output": True,        # v1.3.0: speaker toggle - speak replies?
    "greeting_enabled": True,
    "avatar": "robot",
    # v1.3.1: the pet is HIDDEN by default - it only ever appears when
    # the user shows it (Settings toggle / pet button), never on its own
    # at startup or at Windows boot. The state persists once he chooses.
    "pet_visible": False,
    "pet_parked": False,
    # v1.3.1: autostart with Windows (HKCU Run key), toggleable, ON.
    "autostart": True,
    # v1.3.1 internal: one-time migration - existing installs saved
    # pet_visible=True, so the new hidden-by-default rule is applied
    # once on the first v1.3.1 run, then the user's own choice rules.
    "pet_default_v131": False,
    # v1.4.1: the pet + island were DISABLED by the user's decision
    # (2026-11-07: "they still have problems") - their windows are
    # NEVER created and no pet/island code runs at startup. The code
    # stays behind these flags (not deleted).
    # v1.6.1: the island was rebuilt in v1.6.0, so it is ON by default
    # now - the pill shows at startup and the main window opens from
    # it. The pet stays OFF (still disabled by that decision).
    "pet_enabled": False,
    "island_enabled": True,
    # v1.6.4 perf (d): background clipboard capture while the
    # clipboard panel is closed. Default False - history is captured
    # only once the panel has been opened, unless the user opts in.
    "island_clipboard_enabled": False,
    # v1.6.1 one-time migration: existing installs saved
    # island_enabled=False, but no Settings toggle ever existed, so
    # nobody could have explicitly turned it off - the first v1.6.1
    # run enables it once (see load_settings), then the user's own
    # choice rules.
    "island_migrated_v161": False,
    # v1.6.1 one-time identity migration (rename Memo -> LONA).
    # The pre-rename default identity was ai_name "Memo" + whatever
    # avatar was picked; it migrates as ONE unit (see load_settings).
    # A custom name is never touched by it.
    "identity_migrated_v161": False,
    # v1.3.0: NO owner_code setting anymore. The owner code is stored
    # ONLY as a SHA-256 hash in main.py (OWNER_CODE_HASH) - never as
    # plaintext in settings.json, logs, or anywhere else.
    "owner_unlocked": False,     # session flag, never persisted as true
    # v1.4.0: WhatsApp linking (Baileys sidecar, QR, 3rd number only).
    "whatsapp_enabled": False,   # off by default - opt in from Settings
    # The owner's MAIN number (digits, e.g. 01557887719) - owner mode
    # over WhatsApp. Empty = nobody is owner, everyone gets reply mode.
    "whatsapp_owner_phone": "",
    # v1.4.2: system monitor (thresholds tuned to his 8GB laptop:
    # normally 84-86% RAM used, ~1.1GB free - so the RAM alert only
    # fires when free < 500MB or usage > 92%, never at 80%).
    "monitor_enabled": True,
    "monitor_interval_sec": 60,
    "monitor_ram_free_mb": 500,
    "monitor_ram_percent": 92,
    "monitor_disk_free_gb": 10,
    "monitor_disk_percent_free": 10,
    "monitor_temp_warn_c": 85,
    "monitor_temp_critical_c": 90,
    "monitor_cpu_sustained_percent": 85,
    "monitor_cpu_sustained_minutes": 5,
    "monitor_battery_warn_percent": 20,
    "monitor_battery_critical_percent": 10,
    "monitor_cooldown_min": 30,
    # rc.3 (d): token-limit-near rule thresholds (measured remaining
    # tokens per engine, Groq 8000/min window) and the slow-reply
    # rule threshold (measured reply seconds, well under the 180 s
    # chat deadline in brain.py). See monitor.DEFAULT_MONITOR_SETTINGS.
    "monitor_token_warn_remaining": 1000,
    "monitor_token_critical_remaining": 200,
    "monitor_slow_reply_sec": 30,
    "monitor_alert_in_app": True,   # show alerts inside the app
    "monitor_alert_toast": True,    # ... and as a Windows toast
    # v1.5: wake word (always-listening, offline). The phrase DERIVES
    # from the AI's name: "يا {ai_name}" (default "يا لونا"). Any user
    # can rename the AI from Settings (ai_name) - set_ai_name() re-syncs
    # this phrase, and the listener matches the synced phrase.
    # rc.2: OFF by default - the always-on Whisper listener pushed the
    # laptop to 99 C in the field; the user enables it from Settings.
    "wakeword_enabled": False,
    "wakeword_phrase": "يا لونا",
    "wakeword_cooldown_sec": 5,
    # v1.4.2: important-notification alerts (Windows toast listener).
    "notifications_enabled": True,
    "notifications_important_apps": [
        "WhatsApp",
        "Outlook", "Microsoft Outlook",
        "Mail", "Gmail",
        "Windows Security", "Windows Update",
    ],
    "notifications_keywords": [
        "urgent", "security", "update", "password",
        "critical", "error", "alert",
        "مهم", "عاجل", "تنبيه", "تحديث", "كلمة السر", "خطأ", "حرج", "أمن",
    ],
    # v1.6.0: themes (old theme is never deleted - it stays as "old"
    # beside the new ones; the UI applies the id, Python only persists it).
    "theme": "old",
    # v1.6.4 reskin: header mute (v2 UI) + light-glass mode. Both are
    # separate from voice_output (the Settings voice toggle) and from
    # theme; the v2 UI persists them through save_settings.
    "muted": False,
    "lite": 0,
    # v1.6.0: Deep Think (Groq reasoning_effort="high" when on).
    "deep_think_enabled": False,
    # v1.6.2: model choice ("auto" = Groq -> Gemini -> Ollama chain,
    # "groq" / "gemini" / "local" = that model only, strict -- no
    # silent fallback). Added to DEFAULTS so load/save_settings keep
    # it (both filter to DEFAULTS keys).
    "model_choice": "auto",
    # v1.6.0: proactive companion / focus mode.
    "focus_mode": False,
    "quiet_hours_enabled": True,
    "companion_enabled": True,
    "tempwatch_enabled": True,
    "morning_brief_enabled": True,
    "routine_learning_enabled": True,
    # v1.6.0: privacy mode (default ON) - redact secrets from anything
    # sent to Groq/Gemini (see brain.redact).
    "privacy_mode": True,
    # v1.6.0: exam countdown - list of {"subject", "date": "YYYY-MM-DD"}.
    # Default EMPTY: no date is ever invented; the UI says "no date set".
    "exams": [],
}


def derive_wake_phrase(ai_name: str) -> str:
    """The wake word derives from the AI's name: "يا {ai_name}".
    Default ("LONA") -> "يا لونا". An empty/blank name falls back to
    the default phrase. The spoken name is NEVER hardcoded - callers
    always read the ai_name setting and derive from it."""
    name = str(ai_name or "").strip()
    if not name:
        return "يا لونا"
    # The default name "LONA" is spoken in Arabic as "لونا" - the wake
    # word is an Arabic phrase, so a Latin name is never spoken raw.
    if name.upper() == "LONA":
        return "يا لونا"
    return f"يا {name}"


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
    # v1.6.1: one-time island migration (see DEFAULTS) - existing
    # installs saved island_enabled=False under the old kill-switch.
    # The marker persists on the next save_settings() call; until then
    # re-applying True on every load is harmless (a user's own choice
    # is only ever written through save_settings, which also persists
    # the marker, so it can never be re-enabled against their will).
    if not settings.get("island_migrated_v161", False):
        was_off = settings.get("island_enabled") is False
        settings["island_enabled"] = True
        settings["island_migrated_v161"] = True
        if was_off:
            info("app", "Island enabled by v1.6.1 migration "
                        "(was off under the v1.4.1 kill-switch)")
    # v1.6.1: one-time identity migration (rename Memo -> LONA).
    # The pre-rename default identity was ai_name "Memo" (or empty)
    # + the picked avatar; it migrates as ONE unit - the name becomes
    # "LONA" and the header/identity avatar becomes the gold robot.
    # A custom name is left alone, avatar included: only the old
    # default moves. The canned self-introductions in qa.json /
    # rules.json ("أنا Memo") move with it, so the bot never
    # introduces itself by the old name again.
    if not settings.get("identity_migrated_v161", False):
        name = str(settings.get("ai_name") or "").strip()
        if name == "" or name == "Memo":
            settings["ai_name"] = "LONA"
            settings["avatar"] = "robot"
            _migrate_identity_answers()
            info("app", "Identity migrated to LONA + robot avatar "
                        "(was the pre-rename default)")
        settings["identity_migrated_v161"] = True
    return settings


def _migrate_identity_answers() -> None:
    """One-time rewrite of the canned self-introductions in
    app/data/qa.json + rules.json ("أنا Memo" -> "أنا LONA").
    Exact strings only; a missing/unreadable file is skipped."""
    for fname, pairs in (("qa.json", (("أنا Memo،", "أنا LONA،"),
                                     ("I'm Memo,", "I'm LONA,"))),
                         ("rules.json", (("أنا Memo،", "أنا LONA،"),))):
        try:
            path = DATA_DIR / fname
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            new = text
            for old, new_s in pairs:
                new = new.replace(old, new_s)
            if new != text:
                path.write_text(new, encoding="utf-8")
                info("app", f"Identity answers updated in {fname}")
        except Exception as exc:
            warn("app", f"Could not migrate identity answers in {fname}: "
                        f"{exc}")


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
    """Read a key file (e.g. 'groq-key.txt'). Returns '' if missing.

    v1.6.0: Groq/Gemini keys live in the Windows Credential Manager
    (DPAPI) - see apikeys.py. This function delegates to that store so
    every existing caller (brain, speak, ...) keeps working unchanged.
    Azure keys are not in the vault - they still read from txt. The
    value is never logged. (Lazy import: apikeys imports this module
    for paths, so a top-level import would be a cycle.)"""
    provider = {"groq-key.txt": "groq",
                # v1.6.1: two separate Gemini keys - voice (TTS) and
                # chat (LLM fallback + vision). The old single
                # "gemini-key.txt" is migrated by apikeys (see
                # migrate_gemini_split) and no longer maps anywhere.
                "gemini-voice-key.txt": "gemini-voice",
                "gemini-chat-key.txt": "gemini-chat"}.get(
                    str(name or "").strip())
    if provider:
        try:
            import apikeys
            value = apikeys.read_key_value(provider)
        except Exception as exc:
            error("keys", f"Could not read {provider} key: {exc}")
            return ""
        if not value and not quiet:
            warn("keys", f"Key missing: {provider} "
                         f"(add it in Settings -> API Keys)")
        return value
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
    gemini_voice = bool(read_key("gemini-voice-key.txt", quiet=True))
    gemini_chat = bool(read_key("gemini-chat-key.txt", quiet=True))
    azure_key, azure_region = read_azure()
    return {
        "groq": groq,
        "gemini-voice": gemini_voice,
        "gemini-chat": gemini_chat,
        "azure": bool(azure_key and azure_region),
    }
