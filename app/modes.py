# LONA v1.6.4-rc.3 - one-tap modes / presets (OpenJarvis presets).
#
# Three modes, each a REAL settings bundle applied by one tap. Every
# key in a bundle is a real setting in settings_store.DEFAULTS that
# app code actually reads - no fake toggles:
#
#   notifications_enabled  -> NotificationWatcher start/stop
#                             (main.Api.save_settings applies it live)
#   monitor_alert_toast    -> Windows toast + island toast, read live
#                             in MemoApp._on_system_alert
#   monitor_alert_in_app   -> in-app alert list, read live there too
#   toasts_important_only  -> same place: toasts only for important
#                             alerts (critical level, or an important
#                             notification)
#   study_reminders_enabled -> companion loop routine suggestions
#   exam_countdown_pinned  -> countdown strip on the chat welcome
#                             screen (Device view always shows it)
#   morning_brief_enabled  -> companion.morning_brief at startup
#   island_enabled         -> island window created at startup
#
# "Back to normal" restores the values that were in place BEFORE the
# first mode was applied (kept in data/mode_previous.json). Switching
# straight from one mode to another keeps the ORIGINAL pre-mode
# values, so normal always means "how it was before any mode". When
# no previous bundle exists (file lost / mode applied by an older
# build), normal falls back to the shipped DEFAULTS for the bundle
# keys - stated honestly in the returned dict.
#
# Modes never touch: wake-word (stays OFF by default), the token bar,
# island sizes, the face, the title bar, or any v1.6.3 feature.

import json
import threading

from logger import error, info
from settings_store import DATA_DIR, DEFAULTS

_PREVIOUS_PATH = DATA_DIR / "mode_previous.json"
_LOCK = threading.Lock()

MODES = {
    "exam": {
        "label_ar": "وضع الامتحانات",
        "description_ar": "هدوء: إشعارات للمهم بس، العد التنازلي للامتحان قدّام، وتذكير المذاكرة شغال",
        "settings": {
            "notifications_enabled": True,
            "monitor_alert_toast": True,
            "monitor_alert_in_app": True,
            "toasts_important_only": True,
            "study_reminders_enabled": True,
            "exam_countdown_pinned": True,
            "morning_brief_enabled": True,
        },
    },
    "work": {
        "label_ar": "وضع الشغل",
        "description_ar": "تركيز على التوصيل: الإشعارات كلها شغالة، وتذكير المذاكرة مطفي",
        "settings": {
            "notifications_enabled": True,
            "monitor_alert_toast": True,
            "monitor_alert_in_app": True,
            "toasts_important_only": False,
            "study_reminders_enabled": False,
            "exam_countdown_pinned": False,
            "morning_brief_enabled": True,
        },
    },
    "quiet": {
        "label_ar": "وضع هادي",
        "description_ar": "سكون: مفيش أي إشعار منبثق، والجزيرة بتفضل مقفولة على نفسها",
        "settings": {
            "notifications_enabled": False,
            "monitor_alert_toast": False,
            "monitor_alert_in_app": False,
            "toasts_important_only": False,
            "study_reminders_enabled": False,
            "exam_countdown_pinned": False,
        },
    },
}

# Every key any mode can change. Used for the "back to normal"
# snapshot and for the defaults fallback.
MODE_KEYS = sorted({k for m in MODES.values() for k in m["settings"]})


def list_modes() -> list:
    """The three modes for the UI: [{id, label_ar, description_ar}]."""
    return [{"id": mode_id, "label_ar": spec["label_ar"],
             "description_ar": spec["description_ar"]}
            for mode_id, spec in MODES.items()]


def active_mode(settings: dict) -> str:
    """The stored active mode ("" = normal). Unknown values read as
    normal - a bad value never invents a mode."""
    mode = str((settings or {}).get("active_mode") or "").strip()
    return mode if mode in MODES else ""


def _load_previous() -> dict | None:
    """The saved pre-mode bundle, or None when there is none."""
    try:
        if _PREVIOUS_PATH.exists():
            data = json.loads(
                _PREVIOUS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(
                    data.get("settings"), dict):
                return {k: v for k, v in data["settings"].items()
                        if k in MODE_KEYS}
    except Exception:
        pass
    return None


def _save_previous(settings: dict) -> bool:
    """Snapshot the current values of the mode keys (the state
    "normal" restores to). Atomic write, same pattern as tasks.py."""
    snapshot = {k: settings.get(k, DEFAULTS.get(k)) for k in MODE_KEYS}
    try:
        _PREVIOUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PREVIOUS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"settings": snapshot},
                                  ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(_PREVIOUS_PATH)
        return True
    except Exception as exc:
        error("modes", f"Could not save previous mode settings: {exc}")
        return False


def apply_mode(settings: dict, mode: str) -> dict:
    """One-tap apply. Returns {"ok", "mode", "patch"} where `patch`
    is the FULL settings patch to persist (bundle + active_mode) -
    the caller (bridge) persists it through Api.save_settings so
    the live-apply paths (monitor / notifications / companion)
    run exactly as for a manual save. The pre-mode snapshot is
    taken only when coming from normal, so chaining modes keeps
    the ORIGINAL normal values. Unknown mode -> {"ok": False}."""
    mode = str(mode or "").strip()
    if mode not in MODES:
        return {"ok": False, "error": "unknown_mode"}
    with _LOCK:
        if not active_mode(settings):
            _save_previous(settings)
        patch = dict(MODES[mode]["settings"])
        patch["active_mode"] = mode
    info("modes", f"Mode applied: {mode} "
                  f"({MODES[mode]['label_ar']})")
    return {"ok": True, "mode": mode, "patch": patch}


def restore_normal(settings: dict) -> dict:
    """Back to normal: the saved pre-mode values, or the shipped
    DEFAULTS for the bundle keys when no snapshot exists (the
    caller is told which one it was - `restored_from`). Returns
    {"ok", "mode": "", "patch", "restored_from"}; already normal
    is a no-op that still returns the current values."""
    with _LOCK:
        previous = _load_previous()
        if previous is not None:
            patch = {k: previous.get(k, DEFAULTS.get(k))
                     for k in MODE_KEYS}
            restored_from = "previous"
        else:
            patch = {k: DEFAULTS.get(k) for k in MODE_KEYS}
            restored_from = "defaults"
        patch["active_mode"] = ""
        try:
            if _PREVIOUS_PATH.exists():
                _PREVIOUS_PATH.unlink()
        except Exception:
            pass
    info("modes", "Mode restored to normal "
                  f"(from {restored_from})")
    return {"ok": True, "mode": "", "patch": patch,
            "restored_from": restored_from}
