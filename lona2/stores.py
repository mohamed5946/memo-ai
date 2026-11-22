"""LONA v2 — persisted stores (settings / tasks / memories).

All state lives in JSON files under DATA_DIR with atomic writes
(write temp + replace). Settings defaults come from the brief:
assistant name "لونا", user "Mohamed", wake word OFF by default
(continuous listening heated the laptop to 99C in the field).
"""

import json
import os
import sys
import threading
from pathlib import Path

if sys.platform == "win32":
    _base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    DATA_DIR = _base / "LONA"
else:
    _base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    DATA_DIR = _base / "lona"

_lock = threading.RLock()  # RLock: save_settings() calls get_settings()
# while already holding the lock — a plain Lock deadlocks there.

DEFAULT_SETTINGS = {
    "ai_name": "لونا",
    "user_name": "Mohamed",
    "language": "ar",
    "greet_morning": True,
    "voice_reply": True,
    "autostart": False,
    "island_enabled": True,
    "listen_enabled": False,
    "privacy_mode": False,
    "monitor_enabled": True,
    "r1_ram_free_mb": 500,
    "r2_ram_pct": 92,
    "r3_disk_free_gb": 10,
    "r4_disk_free_pct": 10,
    "r5_cpu_temp_c": 85,
    "r6_cpu_load_pct": 85,
    "r6_load_minutes": 5,
    "r7_battery_pct": 20,
    "r8_alert_cooldown_min": 30,
    "notify_in_app": True,
    "notify_toast": True,
    "notifications_enabled": True,
    "notify_apps": "",
    "notify_keywords": "",
    "voice_choice": "m1",
    "avatar": "cat",
    "model_choice": "auto",
    "muted": False,
    "lite_mode": False,
    "theme": "lg",
    "whatsapp_enabled": False,
    "whatsapp_owner_number": "",
    "deep_files_allowed": False,
}

THEMES = ["lg", "night", "graphite", "calm", "old"]


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default):
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return default


def get_settings() -> dict:
    with _lock:
        stored = _read_json(DATA_DIR / "settings.json", {})
        merged = dict(DEFAULT_SETTINGS)
        merged.update({k: v for k, v in stored.items() if k in merged})
        return merged


def save_settings(patch: dict) -> dict:
    """Merge a patch into settings. Unknown keys are dropped."""
    if not isinstance(patch, dict):
        return {"ok": False, "error": "patch must be an object"}
    with _lock:
        current = get_settings()
        clean = {k: v for k, v in patch.items() if k in DEFAULT_SETTINGS}
        current.update(clean)
        try:
            _atomic_write(DATA_DIR / "settings.json", current)
            return {"ok": True}
        except Exception as exc:
            from .logger import error
            error("app", f"Could not save settings: {exc}")
            return {"ok": False, "error": "مقدرتش أحفظ الإعدادات"}


def get_tasks() -> list:
    with _lock:
        return list(_read_json(DATA_DIR / "tasks.json", []))


def save_tasks(tasks: list) -> dict:
    with _lock:
        try:
            _atomic_write(DATA_DIR / "tasks.json", list(tasks))
            return {"ok": True}
        except Exception as exc:
            from .logger import error
            error("app", f"Could not save tasks: {exc}")
            return {"ok": False, "error": "مقدرتش أحفظ المهام"}


def get_memories() -> list:
    with _lock:
        return list(_read_json(DATA_DIR / "memories.json", []))


def save_memories(memories: list) -> dict:
    with _lock:
        try:
            _atomic_write(DATA_DIR / "memories.json", list(memories))
            return {"ok": True}
        except Exception as exc:
            from .logger import error
            error("app", f"Could not save memories: {exc}")
            return {"ok": False, "error": "مقدرتش أحفظ الذاكرة"}
