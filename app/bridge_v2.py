# LONA v1.6.4 reskin - v2 UI bridge adapter.
#
# Thin translation layer ONLY. Every function here delegates to a v1
# Api method (or a v1 module) and translates names/shapes between the
# v2 UI's flat calls and v1's backend. No v1 backend logic is
# duplicated, changed, or re-implemented here, and nothing is imported
# from the v2 rebuild tree.
#
# Wiring note for the coordinator: the functions take the v1 Api
# instance as their first argument. Methods whose v2 name COLLIDES
# with an existing v1 method (chat, get_device_overview,
# get_whatsapp_status, request_deep_file_access) are listed in the
# patch spec in the build report - the v1 body must be kept under a
# private name (or, for get_device_overview / get_whatsapp_status,
# replaced by a one-line delegation, since those two adapters read
# the v1 modules directly and never call back into Api).
#
# No secrets are ever logged here: values are never written to the
# log, only key names (in the "dropped" list) and counts.

import json
import sys
import threading
import uuid
from datetime import datetime

from logger import error as log_error_fn
from logger import info
from settings_store import (DATA_DIR, derive_wake_phrase, load_settings,
                            save_settings)

# -- voice / avatar ids -------------------------------------------------
# v2 UI voice cards (m1/m2/f1/f2) -> v1 voice ids (speak.py GEMINI_VOICES).
VC_MAP = {"m1": "boy_calm", "m2": "boy_fun",
          "f1": "girl_warm", "f2": "girl_fast"}
VC_REVERSE = {v: k for k, v in VC_MAP.items()}
V1_VOICES = frozenset(VC_MAP.values())
# v1 avatar ids (ui/index.html) are the same four as the v2 UI, plus
# the default "robot" the identity migration sets.
AVATARS = {"cat", "dog", "boy", "girl", "robot"}

# -- themes --------------------------------------------------------------
# The v2 UI owns theme application (CSS variables) and persists the id
# through save_settings; Python only stores the string. v1's
# set_theme() known-set ({old, night-glass, calm-space, dashboard})
# is NOT on that path, so no change to it is needed. These maps exist
# for the get_themes_v2 / set_theme_v2 API pair (parity with v1's
# get_themes / set_theme, which translate the same way v1 already
# maps its own legacy ids calm-light/graphite).
V2_TO_V1_THEME = {"lg": "night-glass", "night": "night-glass",
                  "graphite": "dashboard", "calm": "calm-space",
                  "old": "old"}
V1_TO_V2_THEME = {"night-glass": "lg", "dashboard": "graphite",
                  "calm-space": "calm", "old": "old"}
V2_THEMES = [
    {"id": "lg", "name": "Liquid Glass"},
    {"id": "night", "name": "زجاج الليل"},
    {"id": "graphite", "name": "لوحة القيادة"},
    {"id": "calm", "name": "المساحة الهادية"},
    {"id": "old", "name": "الثيم القديم"},
]

# -- monitor thresholds ---------------------------------------------------
# v2 health-section flat keys (r1..r8, r6m) -> canonical monitor keys.
MONITOR_MAP = {
    "r1": "monitor_ram_free_mb",
    "r2": "monitor_ram_percent",
    "r3": "monitor_disk_free_gb",
    "r4": "monitor_disk_percent_free",
    "r5": "monitor_temp_warn_c",
    "r6": "monitor_cpu_sustained_percent",
    "r6m": "monitor_cpu_sustained_minutes",
    "r7": "monitor_battery_warn_percent",
    "r8": "monitor_cooldown_min",
}
MONITOR_REVERSE = {v: k for k, v in MONITOR_MAP.items()}

# -- helpers ---------------------------------------------------------------

def _main_module(api):
    """The live main.py module object behind an Api instance.

    Works whether main.py runs as a script (__main__) or is imported
    (app.main): Api.__module__ names the module that defined it, and
    sys.modules holds the live copy with the real _APP / _chats /
    _TEST_KEY_RESULTS / _CHECK_UPDATES globals. Never imports main
    (that would execute it a second time under a new name)."""
    try:
        return sys.modules.get(getattr(api.__class__, "__module__", ""))
    except Exception:
        return None


def _app(api):
    """The live MemoApp instance, or None (tests / early startup)."""
    mod = _main_module(api)
    app = getattr(mod, "_APP", None) if mod is not None else None
    if app is None:
        app = getattr(api, "_app", None)  # test seam
    return app


def _to_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off", ""):
            return False
    return default


def _to_int01(value, default=0):
    return 1 if _to_bool(value, bool(default)) else 0


def _to_int(value):
    """int() or None when the value is not a number (caller drops it)."""
    try:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and value.strip() != "":
            return int(float(value.strip()))
    except (TypeError, ValueError):
        pass
    return None


def _to_lines(value):
    """Textarea text (one item per line) or a list -> clean str list."""
    if isinstance(value, str):
        return [ln.strip() for ln in value.splitlines() if ln.strip()]
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return None


def _voice_choice(value):
    """A v2 (m1..f2) or v1 voice id -> canonical v1 voice id, else None."""
    v = str(value or "").strip()
    if v in VC_MAP:
        return VC_MAP[v]
    if v in V1_VOICES:
        return v
    return None


# -- settings: flat v2 patch -> canonical v1 ------------------------------
# The v2 UI sends flat short keys (sectionPatch + one-off calls) and
# often sends BOTH the flat and the canonical key with the same value
# (e.g. {ai_name, name}, {voice_output, voice}). Canonical wins on a
# clash. Returns {"settings": {...}, "whatsapp": {...}, "dropped": [...]}
# where "whatsapp" is the part that must go through Api.whatsapp_save
# (it starts/stops the sidecar; a plain settings write cannot do that)
# and "dropped" lists key NAMES only (never values).

def translate_save_settings_patch(patch):
    settings = {}
    whatsapp = {}
    dropped = []
    try:
        data = json.loads(patch) if isinstance(patch, str) else patch
    except Exception:
        return {"settings": settings, "whatsapp": whatsapp,
                "dropped": ["<bad json>"]}
    if not isinstance(data, dict):
        return {"settings": settings, "whatsapp": whatsapp,
                "dropped": ["<not an object>"]}

    def drop(key):
        dropped.append(str(key))

    for key, value in data.items():
        # -- identity ---------------------------------------------------
        if key in ("name", "ai_name"):
            name = str(value or "").strip()
            if not name or len(name) > 30:
                drop(key)
                continue
            settings["ai_name"] = name
            # Renaming re-syncs the wake phrase, exactly like
            # Api.set_ai_name does ("يا {name}").
            settings["wakeword_phrase"] = derive_wake_phrase(name)
        elif key in ("user", "user_name"):
            settings["user_name"] = str(value or "")
        elif key in ("lang", "language"):
            settings["language"] = str(value or "").strip() or "ar"
        # -- toggles (0/1 in the v2 UI) ---------------------------------
        elif key in ("greet", "greeting_enabled"):
            settings["greeting_enabled"] = _to_bool(value, True)
        elif key in ("auto", "autostart"):
            settings["autostart"] = _to_bool(value, True)
        elif key in ("island", "island_enabled"):
            settings["island_enabled"] = _to_bool(value, True)
        elif key in ("listen", "wakeword_enabled"):
            settings["wakeword_enabled"] = _to_bool(value, False)
        elif key in ("priv", "privacy_mode"):
            settings["privacy_mode"] = _to_bool(value, True)
        elif key in ("mon", "monitor_enabled"):
            settings["monitor_enabled"] = _to_bool(value, True)
        elif key in ("notif", "notifications_enabled"):
            settings["notifications_enabled"] = _to_bool(value, True)
        # -- voice: "voice" is OVERLOADED in the v2 UI. The general
        # section sends voice: 0/1 (the voice-output toggle); the
        # sound section sends voice: "m1".."f2" (the voice choice).
        # The value disambiguates: a known voice id is the choice,
        # anything else is the toggle.
        elif key == "voice":
            choice = _voice_choice(value)
            if choice is not None:
                settings["voice"] = choice
            else:
                settings["voice_output"] = _to_bool(value, True)
        elif key == "voice_output":
            settings["voice_output"] = _to_bool(value, True)
        elif key == "vc":
            choice = _voice_choice(value)
            if choice is not None:
                settings["voice"] = choice
            else:
                drop(key)
        # -- avatar ------------------------------------------------------
        elif key in ("av", "avatar"):
            av = str(value or "").strip()
            if av in AVATARS:
                settings["avatar"] = av
            else:
                drop(key)
        # -- WhatsApp (routed out: needs whatsapp_save, not a plain
        # settings write) ------------------------------------------------
        elif key in ("waNum", "owner_number", "whatsapp_owner_phone"):
            whatsapp["owner_phone"] = str(value or "").strip()
        elif key == "wa":
            whatsapp["enabled"] = _to_bool(value, False)
        # -- monitor thresholds ------------------------------------------
        elif key in MONITOR_MAP:
            num = _to_int(value)
            if num is None:
                drop(key)
            else:
                settings[MONITOR_MAP[key]] = num
        elif key in MONITOR_REVERSE:
            num = _to_int(value)
            if num is None:
                drop(key)
            else:
                settings[key] = num
        elif key in ("inapp", "monitor_alert_in_app"):
            settings["monitor_alert_in_app"] = _to_bool(value, True)
        elif key in ("toast", "monitor_alert_toast"):
            settings["monitor_alert_toast"] = _to_bool(value, True)
        # -- notification filter lists (textarea: one per line) ----------
        elif key == "apps":
            lines = _to_lines(value)
            if lines is None:
                drop(key)
            else:
                settings["notifications_important_apps"] = lines
        elif key == "kws":
            lines = _to_lines(value)
            if lines is None:
                drop(key)
            else:
                settings["notifications_keywords"] = lines
        elif key in ("notifications_important_apps",
                     "notifications_keywords"):
            lines = _to_lines(value)
            if lines is None:
                drop(key)
            else:
                settings[key] = lines
        # -- v2-only settings (new DEFAULTS in settings_store) ----------
        elif key == "muted":
            settings["muted"] = _to_bool(value, False)
        elif key == "lite":
            num = _to_int(value)
            settings["lite"] = 1 if num else 0
        # -- theme: stored VERBATIM (v2 id). The v2 UI applies the
        # theme itself from CSS and reads the id back from
        # get_settings; v1's set_theme() known-set is never on this
        # path, so it needs no change.
        elif key == "theme":
            theme = str(value or "").strip()
            if theme:
                settings["theme"] = theme
            else:
                drop(key)
        # -- canonical passthrough (already v1 keys) ----------------------
        elif key in ("pet_visible", "pet_parked"):
            settings[key] = _to_bool(value, False)
        elif key == "wakeword_phrase":
            settings[key] = str(value or "")
        elif key in ("deep_think_enabled", "focus_mode",
                     "quiet_hours_enabled", "companion_enabled",
                     "tempwatch_enabled", "morning_brief_enabled",
                     "routine_learning_enabled",
                     "island_clipboard_enabled"):
            settings[key] = _to_bool(value, False)
        # -- v1.6.4-rc.3: one-tap modes + habit learning -------------
        elif key in ("habits_enabled", "toasts_important_only",
                     "study_reminders_enabled",
                     "exam_countdown_pinned"):
            settings[key] = _to_bool(value, False)
        elif key == "active_mode":
            mode = str(value or "").strip()
            if mode in ("", "exam", "work", "quiet"):
                settings[key] = mode
            else:
                drop(key)
        elif key == "model_choice":
            choice = str(value or "").strip().lower()
            if choice in ("auto", "groq", "gemini", "local"):
                settings[key] = choice
            else:
                drop(key)
        elif key == "exams":
            if isinstance(value, list):
                settings[key] = value
            else:
                drop(key)
        elif key == "monitor_interval_sec":
            num = _to_int(value)
            if num is None:
                drop(key)
            else:
                settings[key] = num
        else:
            # Unknown keys are dropped, never stored. ("owner" - the
            # owner-code input - is dropped on purpose: the code is
            # never a setting; check_owner_code handles it.)
            drop(key)
    return {"settings": settings, "whatsapp": whatsapp, "dropped": dropped}


# -- settings: canonical v1 -> flat v2 aliases (for get_status /
# get_settings, so saved values display in the v2 UI) --------------------
# "voice" keeps its CANONICAL meaning here (the voice-choice id):
# the v2 UI reads the toggle from voice_output first and the choice
# from vc / voice_choice, so overwriting "voice" with 0/1 would lose
# the choice on the round-trip.

def with_v2_aliases(settings):
    out = dict(settings or {})
    out["name"] = out.get("ai_name", "")
    out["user"] = out.get("user_name", "")
    out["lang"] = out.get("language", "ar")
    out["greet"] = _to_int01(out.get("greeting_enabled", True), 1)
    out["voice_output"] = bool(out.get("voice_output", True))
    out["auto"] = _to_int01(out.get("autostart", True), 1)
    out["island"] = _to_int01(out.get("island_enabled", True), 1)
    out["listen"] = _to_int01(out.get("wakeword_enabled", False), 0)
    out["priv"] = _to_int01(out.get("privacy_mode", True), 1)
    phone = out.get("whatsapp_owner_phone", "") or ""
    out["waNum"] = phone
    out["owner_number"] = phone
    out["mon"] = _to_int01(out.get("monitor_enabled", True), 1)
    for v1_key, flat in MONITOR_REVERSE.items():
        out[flat] = out.get(v1_key)
    out["inapp"] = _to_int01(out.get("monitor_alert_in_app", True), 1)
    out["toast"] = _to_int01(out.get("monitor_alert_toast", True), 1)
    out["notif"] = _to_int01(out.get("notifications_enabled", True), 1)
    out["apps"] = "\n".join(
        out.get("notifications_important_apps", []) or [])
    out["kws"] = "\n".join(out.get("notifications_keywords", []) or [])
    vc = VC_REVERSE.get(str(out.get("voice", "girl_warm")), "f1")
    out["vc"] = vc
    out["voice_choice"] = vc
    out["av"] = out.get("avatar", "robot")
    out["lite"] = _to_int01(out.get("lite", 0), 0)
    out["muted"] = bool(out.get("muted", False))
    # v1.6.4-rc.3: modes + habits. The UI toggle reads `habits`
    # (0/1); the canonical `habits_enabled` bool rides along raw.
    # `active_mode` (""/exam/work/quiet) rides along raw too.
    out["habits"] = _to_int01(out.get("habits_enabled", True), 1)
    return out


# -- mute ------------------------------------------------------------------

def set_muted(api, on):
    """Header mute button: persist `muted` and stop anything playing
    right now. NOTE: muting FUTURE replies needs the coordinator's
    patch to MemoApp.speak_async (check `muted` beside
    `voice_output`); this adapter only stores the flag + stops speech.
    `muted` is independent of the Settings voice toggle."""
    app = _app(api)
    if app is None:
        return {"ok": False, "error": "unavailable"}
    muted = _to_bool(on, False)
    app.settings["muted"] = muted
    save_settings(app.settings)
    if muted:
        try:
            api.stop_speaking()
        except Exception:
            pass
    return {"ok": True, "muted": muted}


# -- themes ------------------------------------------------------------------

def translate_theme_v2_to_v1(theme_id):
    """v2 theme id -> v1 set_theme() id (None = unknown)."""
    return V2_TO_V1_THEME.get(str(theme_id or "").strip())


def translate_theme_v1_to_v2(theme_id):
    """Stored (v1 or v2) theme id -> v2 id. A v2 id passes through,
    so a value saved verbatim via save_settings round-trips exactly."""
    tid = str(theme_id or "").strip()
    if tid in V2_TO_V1_THEME:
        return tid
    return V1_TO_V2_THEME.get(tid, "lg")


def get_themes_v2(api):
    """The 5 v2 themes + current (translated from the stored id).
    The v2 UI builds its own grid and never calls this; it exists for
    API parity with v1's get_themes."""
    app = _app(api)
    current = "lg"
    if app is not None:
        current = translate_theme_v1_to_v2(
            app.settings.get("theme", "old"))
    return {"themes": [dict(t) for t in V2_THEMES], "current": current}


def set_theme_v2(api, theme_id):
    """Set a v2 theme id through v1's set_theme (translated)."""
    v1_id = translate_theme_v2_to_v1(theme_id)
    if v1_id is None:
        return {"ok": False, "error": "unknown_theme"}
    res = api.set_theme(v1_id) or {}
    return {"ok": bool(res.get("ok")), "theme": str(theme_id).strip()}


# -- chat --------------------------------------------------------------------

def chat(api, text, opts=None):
    """v2 chat(text, {deep_think}): apply the per-message Deep Think
    flag through v1 set_deep_think, then run the v1 chat flow. The
    reply carries BOTH "chat_id" (v1) and "id" (v2) so the v2 UI's
    `res.id || res.chat_id` works either way. Calls _chat_raw when
    the coordinator renamed the v1 body there (see the patch spec),
    else the original Api.chat - never the wrapper itself."""
    if isinstance(opts, dict) and "deep_think" in opts:
        try:
            api.set_deep_think(bool(opts.get("deep_think")))
        except Exception:
            pass
    # v1.6.4-rc.3: habit learning hook (bridge layer only - brain.py
    # is untouched). Records hour + coarse category only; the text
    # itself is categorized and dropped inside habits.record_chat,
    # and the store respects the habits_enabled off switch. A
    # failure here never blocks the chat.
    try:
        if isinstance(text, str) and text.strip():
            _habits_mod().record_chat(
                text, _current_settings(api))
    except Exception:
        pass
    raw = getattr(api, "_chat_raw", None) or api.chat
    res = raw(text) or {}
    if isinstance(res, dict) and res.get("chat_id") and "id" not in res:
        res = dict(res)
        res["id"] = res["chat_id"]
    return res


# -- morning brief -------------------------------------------------------------

def get_morning_brief(api):
    """v2 get_morning_brief -> {brief}. First choice is the
    companion's morning_brief() (battery/RAM/appointments, exactly
    what the button promises); if the companion is off or absent, the
    brief is composed from the proactive feed + exam countdown - never
    invented. The UI also accepts res.text / res.items."""
    app = _app(api)
    comp = getattr(app, "_companion", None) if app is not None else None
    if comp is not None:
        try:
            brief = comp.morning_brief()
            if isinstance(brief, dict) and brief.get("ok") \
                    and brief.get("text"):
                return {"ok": True, "brief": brief["text"],
                        "items": [brief["text"]]}
        except Exception:
            pass
    texts = []
    try:
        feed = api.get_proactive_feed() or {}
        for item in feed.get("feed") or []:
            if isinstance(item, dict):
                text = item.get("text") or item.get("message_ar") \
                    or item.get("message") or ""
                if text:
                    texts.append(str(text))
        exam = feed.get("exam") or {}
        if isinstance(exam, dict) and exam.get("set"):
            texts.append(f"امتحان {exam.get('subject', '')} بعد "
                         f"{exam.get('days_left', '?')} يوم")
    except Exception:
        pass
    if texts:
        return {"ok": True, "brief": "\n".join(texts), "items": texts}
    return {"ok": False, "brief": "",
            "items": [], "message": "مفيش نشرة متاحة دلوقتي."}


# -- device overview -----------------------------------------------------------
# v1 overview (monitor.get_device_overview):
#   ram {total_mb, used_mb, free_mb, percent}, cpu {percent,
#   sustained_high}, battery {percent, charging}, temp_c /
#   temp_available / temp_hint {text, url}, top_processes [{pid, name,
#   cpu_percent, memory_mb, status}]
# v2 UI wants: ram.used_gb / total_gb (GB numbers), temp {value,
# label}, top_processes [{name, cpu, mem_mb}]. This adapter reads the
# monitor directly (like Api.get_device_overview does) so the
# coordinator can replace that method with a one-line delegation
# without a self-call loop.

def _translate_overview(overview):
    out = dict(overview or {})
    ram = overview.get("ram") or {}
    if isinstance(ram, dict) and ram:
        ram2 = dict(ram)
        for mb_key, gb_key in (("total_mb", "total_gb"),
                               ("used_mb", "used_gb"),
                               ("free_mb", "free_gb")):
            if isinstance(ram.get(mb_key), (int, float)):
                ram2[gb_key] = round(ram[mb_key] / 1024.0, 2)
        out["ram"] = ram2
    temp_c = overview.get("temp_c")
    if overview.get("temp_available") \
            and isinstance(temp_c, (int, float)):
        # v1 carries no sensor label in the overview - the value is
        # shown bare (the UI shows label/source only when present).
        out["temp"] = {"value": temp_c, "label": "", "source": ""}
    else:
        out["temp"] = {}
    procs = []
    for proc in overview.get("top_processes") or []:
        if not isinstance(proc, dict):
            continue
        procs.append({
            "name": proc.get("name", "?"),
            "cpu": proc.get("cpu_percent"),
            "mem_mb": proc.get("memory_mb"),
            "pid": proc.get("pid"),
        })
    out["top_processes"] = procs
    return out


def get_device_overview_v2(api):
    """v2 get_device_overview -> {ok, overview} in the v2 UI's shape."""
    app = _app(api)
    mod = _main_module(api)
    overview = None
    try:
        if app is not None and getattr(app, "_monitor", None) is not None:
            overview = app._monitor.get_device_overview()
        elif mod is not None and hasattr(mod, "_fallback_monitor"):
            overview = mod._fallback_monitor().get_device_overview()
    except Exception as exc:
        error("app", f"get_device_overview_v2 failed: {exc}")
        return {"ok": False, "overview": None}
    if not isinstance(overview, dict):
        return {"ok": False, "overview": None}
    return {"ok": True, "overview": _translate_overview(overview)}


# -- cache clean + (non-)undo ---------------------------------------------------
# v1's clean is a PENDING pattern: request_clean_cache() starts a
# background thread and the UI polls get_clean_cache_result(id). The
# v2 UI's handler must be ported to that pattern (see the build
# report); this adapter starts the job under the v2 name and
# normalizes the poll result (bytes_freed_mb -> freed_mb).
# UNDO: fixer.clear_temp_files() permanently deletes (entry.unlink(),
# fixer.py) and registers NO undo entries - v1 undo
# (get_undoable / restore_deleted) only covers killed processes and
# Recycle-Bin files. There is nothing to restore, so undo_clean
# honestly reports not_undoable and clean results carry token None
# (the v2 UI only shows its undo button when res.token is set).

def clean_cache(api, kind="temp"):
    """Start a temp-cache clean (v2 name for the v1 pending flow).
    Returns {"pending": True, "id"} - poll get_clean_cache_result_v2.
    Only "temp" exists; anything else is refused, not faked."""
    if str(kind or "temp").strip().lower() != "temp":
        return {"ok": False,
                "message": "التنظيف المتاح دلوقتي للملفات المؤقتة بس"}
    return api.request_clean_cache()


def get_clean_cache_result_v2(api, req_id):
    """Poll a clean_cache job: {"done", "ok", "freed_mb",
    "files_removed", "token": None}. `done` is always present - the
    v2 UI's pollUntil resolves only on res.done."""
    res = api.get_clean_cache_result(req_id) or {}
    done = bool(res.get("done"))
    if not done:
        return {"done": False}
    ok = bool(res.get("ok"))
    freed = res.get("bytes_freed_mb")
    if not isinstance(freed, (int, float)):
        freed = 0
    return {"done": True, "ok": ok, "freed_mb": freed,
            "files_removed": res.get("files_removed", 0),
            "token": None,
            "message": ("تم التنظيف" if ok else "التنظيف فشل")}


def undo_clean(api, token):
    """Undo a cache clean. Always not_undoable: temp files are deleted
    permanently (fixer.clear_temp_files never registers an undo
    entry), so pretending the clean was undone would be a lie. The
    token is accepted (and ignored) only so the call never crashes."""
    return {"ok": False, "error": "not_undoable",
            "message": "الملفات اللي اتمسحت من الكاش اتمسحت نهائيًا "
                       "ومينفعش ترجع."}


# -- tasks (thin wrappers over the new tasks.py store) --------------------------

def _tasks_mod():
    import tasks as tasks_mod
    return tasks_mod


def get_tasks(api):
    return _tasks_mod().get_tasks()


def add_task(api, text):
    return _tasks_mod().add_task(text)


def toggle_task(api, task_id):
    return _tasks_mod().toggle_task(task_id)


def delete_task(api, task_id):
    return _tasks_mod().delete_task(task_id)


# -- modes + habits (v1.6.4-rc.3) ------------------------------------------
# One-tap modes (modes.py) + local habit learning (habits.py). Both
# are thin wrappers: modes apply through Api.save_settings (so the
# live-apply paths run), habits read the live settings dict.

def _modes_mod():
    import modes as modes_mod
    return modes_mod


def _habits_mod():
    import habits as habits_mod
    return habits_mod


def _current_settings(api):
    """The live settings dict, or None (tests / early startup)."""
    app = _app(api)
    if app is not None:
        return getattr(app, "settings", None)
    return None


def get_modes(api):
    """The three modes + which one is active now."""
    settings = _current_settings(api) or {}
    return {"ok": True,
            "modes": _modes_mod().list_modes(),
            "active": _modes_mod().active_mode(settings)}


def apply_mode(api, mode):
    """Apply one mode ("exam" / "work" / "quiet") or "normal" (back
    to the pre-mode settings). Persists through Api.save_settings
    so monitor / notification / companion live-apply runs exactly
    as for a manual save. Returns {"ok", "mode", ...}."""
    mode = str(mode or "").strip()
    settings = _current_settings(api) or {}
    if mode == "normal":
        res = _modes_mod().restore_normal(settings)
    else:
        res = _modes_mod().apply_mode(settings, mode)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error", "unknown_mode"),
                "mode": ""}
    try:
        saved = api.save_settings(res["patch"]) or {}
    except Exception as exc:
        error("app", f"apply_mode save failed: {exc}")
        return {"ok": False, "error": "save_failed", "mode": ""}
    if saved.get("ok") is False:
        return {"ok": False, "error": "save_failed", "mode": ""}
    out = {"ok": True, "mode": res["mode"]}
    if res.get("restored_from"):
        out["restored_from"] = res["restored_from"]
    return out


def get_habit_suggestion(api):
    """The morning suggestion from recorded habit data only. With
    too little data -> {"ok": False, "reason": "not_enough_data"}
    and the UI shows nothing - never a fake suggestion."""
    try:
        return _habits_mod().get_suggestion(_current_settings(api))
    except Exception as exc:
        error("app", f"get_habit_suggestion failed: {exc}")
        return {"ok": False, "reason": "error"}


def clear_habits(api):
    """Wipe the local habit store (Settings -> الأوضاع)."""
    try:
        return _habits_mod().clear()
    except Exception as exc:
        error("app", f"clear_habits failed: {exc}")
        return {"ok": False, "error": str(exc)}


def get_habits_status(api):
    """Honest habit status for Settings: enabled, event count, days
    covered. Never returns message text (none is stored)."""
    try:
        return _habits_mod().status(_current_settings(api))
    except Exception as exc:
        error("app", f"get_habits_status failed: {exc}")
        return {"ok": False, "enabled": True, "events": 0, "days": 0}


# -- memory ----------------------------------------------------------------------

def get_memories(api):
    """Flatten v1 get_memory() {facts, rules, routines} into the v2
    UI's [{id, text, kind}] list (kind: fact | rule | routine). The
    style line + summary are NOT list items - the v2 memory section
    only renders the list."""
    res = api.get_memory() or {}
    items = []
    for key, kind in (("facts", "fact"), ("rules", "rule"),
                      ("routines", "routine")):
        for entry in res.get(key) or []:
            if not isinstance(entry, dict):
                continue
            items.append({"id": entry.get("id"),
                          "text": entry.get("text", ""),
                          "kind": kind})
    return items


def delete_memory(api, item_id):
    """Resolve a flat id -> (kind, id) by scanning the store, then
    delete through v1 delete_memory_item(kind, item_id)."""
    wanted = str(item_id or "")
    res = api.get_memory() or {}
    for key, kind in (("facts", "fact"), ("rules", "rule"),
                      ("routines", "routine")):
        for entry in res.get(key) or []:
            if isinstance(entry, dict) \
                    and str(entry.get("id")) == wanted:
                return api.delete_memory_item(kind, wanted)
    return {"ok": False, "error": "not_found"}


def clear_memories(api):
    return api.clear_memory()


# -- WhatsApp ----------------------------------------------------------------------
# v1 get_state(): {enabled, running, status, qr, linked_phone,
# owner_configured, error, ...} with status in off / connecting /
# waiting_qr / linked / disconnected / error / dead / decrypt_failed.
# The v2 UI wants {connected, number, qr_data_url, qr, message}.
# The QR value IS already a data:image/png;base64 URL (the sidecar
# builds it with QRCode.toDataURL), so qr_data_url is the same value.
# "connected" includes decrypt_failed: the link is up, only incoming
# decryption is broken (v1.4.3 keeps it green for sending).

def get_whatsapp_status_v2(api):
    """v2 get_whatsapp_status. Reads whatsapp.get_state() directly
    (like Api.get_whatsapp_status does) so the coordinator can
    replace that method with a one-line delegation."""
    mod = _main_module(api)
    whatsapp_mod = getattr(mod, "whatsapp_mod", None) \
        if mod is not None else None
    if whatsapp_mod is None:
        return {"enabled": False, "running": False,
                "status": "unavailable", "qr": None, "linked_phone": None,
                "owner_configured": False, "error":
                    "WhatsApp module not available in this build",
                "connected": False, "number": None, "qr_data_url": None,
                "message": "واتساب مش متاح في النسخة دي"}
    try:
        state = whatsapp_mod.get_state() or {}
    except Exception as exc:
        error("app", f"get_whatsapp_status_v2 failed: {exc}")
        return {"enabled": False, "running": False, "status": "error",
                "qr": None, "linked_phone": None,
                "owner_configured": False, "error": str(exc),
                "connected": False, "number": None, "qr_data_url": None,
                "message": "مقدرتش أقرأ حالة واتساب — شوف السجل"}
    status = str(state.get("status") or "off")
    connected = status in ("linked", "decrypt_failed")
    number = state.get("linked_phone")
    if status == "linked":
        message = "متصل" + (f" — {number}" if number else "")
    elif status == "decrypt_failed":
        message = "متصل لكن في مشكلة فك تشفير — جرّب إعادة الربط"
    elif status == "waiting_qr":
        message = "امسح الكود من واتساب على موبايلك"
    elif status == "off":
        message = "واتساب مش مفعّل"
    else:
        message = str(state.get("error") or "واتساب مش متصل")
    out = dict(state)
    out.update({"connected": connected, "number": number,
                "qr_data_url": state.get("qr"), "message": message})
    return out


def whatsapp_relink(api):
    """Pass-through: v1 already returns the pending shape
    {"pending": True, "id"}; the v2 UI does not poll it (it just
    re-renders the section, which re-reads the QR)."""
    return api.whatsapp_relink()


def whatsapp_disconnect(api):
    """Pass-through: v1 returns {"pending": True, "id"}; the v2 UI
    just re-renders the section after the call."""
    return api.whatsapp_disconnect()


# -- diagnostics log ---------------------------------------------------------------

def get_log(api):
    """v2 get_log -> {text}: v1's get_log_text() already returns
    exactly that shape (last 200 lines)."""
    try:
        return api.get_log_text()
    except Exception:
        return {"text": ""}


def log_error(api, source, message):
    """v2 log_error(source, message) -> the diagnostics log. v1 only
    has log_js(message); this delegates to logger.error directly.
    Values are truncated (source 40 chars, message 500) and the
    message is never a key value - the UI only sends error strings."""
    try:
        log_error_fn(str(source or "ui")[:40], str(message or "")[:500])
    except Exception:
        pass
    return {"ok": True}


# -- generic job resolver ------------------------------------------------------------
# The v2 UI polls ONE resolver for every background job: pollUntil
# resolves only when res.done is true, so every branch below ALWAYS
# includes "done". Resolution order (per spec): _TEST_KEY_RESULTS,
# then _chats, then _ISLAND_JOBS (v1.6.4 perf (a)), then
# _CHECK_UPDATES, then clean-cache results.
# NOTE: _CHECK_UPDATES has no id - it only answers when a check is
# actually in flight or has a result; otherwise the lookup falls
# through to the clean-cache results and then "unknown id".

def _key_test_result(entry):
    pending = bool(entry.get("pending", False))
    done = not pending
    detail = str(entry.get("detail", ""))
    valid = bool(entry.get("valid", False))
    out = {"done": done, "pending": pending,
           "ok": bool(entry.get("ok", False)), "valid": valid,
           "detail": detail, "reply": "", "error": ""}
    if done and valid:
        out["reply"] = detail or "المفتاح اتقبل"
    elif done:
        out["error"] = detail or "المفتاح مرفوض"
    return out


def _update_status_from_state(checking, update, error):
    """_CHECK_UPDATES state -> the v2 UI's {done, available, ...}
    shape (also used by get_update_status)."""
    if checking:
        return {"done": False, "checking": True}
    if error:
        return {"done": True, "available": False,
                "message": str(error)}
    if isinstance(update, dict):
        status = update.get("status")
        if status == "update_available":
            return {"done": True, "available": True,
                    "version": update.get("version"),
                    "notes": update.get("notes", ""),
                    "message": update.get("notes", ""),
                    "url": update.get("url", "")}
        if status == "up_to_date":
            return {"done": True, "available": False,
                    "message": "أنت على آخر إصدار"}
        if status == "error":
            return {"done": True, "available": False,
                    "message": update.get("message")
                    or "مقدرتش أوصل لملف التحديثات — شوف السجل."}
        if status == "not_configured":
            return {"done": True, "available": False,
                    "message": "التحديثات مش متظبطة في النسخة دي"}
    return {"done": True, "available": False,
            "message": "أنت على آخر إصدار"}


def job_result(api, job_id):
    """Resolve any background job by id -> always includes "done"."""
    jid = str(job_id or "")
    mod = _main_module(api)
    if mod is not None:
        # 1. API-key test results.
        try:
            with mod._TEST_KEY_LOCK:
                entry = mod._TEST_KEY_RESULTS.get(jid)
            if entry is not None:
                return _key_test_result(dict(entry))
        except Exception:
            pass
        # 2. Tool chats.
        try:
            with mod._chats_lock:
                chat = mod._chats.get(jid)
            if chat is not None:
                return {"done": bool(chat.get("done")),
                        "reply": chat.get("reply", ""),
                        "engine": chat.get("engine", "groq"),
                        "tools_used": chat.get("tools_used", 0),
                        "images": chat.get("images", []),
                        "heard": chat.get("heard", "")}
        except Exception:
            pass
        # 3. Island slow-call jobs (v1.6.4 perf (a): island_music /
        # island_lyrics / island_notifications run off the bridge and
        # resolve here). BEFORE the update-check branch: that branch
        # answers for ANY id while a check result lingers, so island
        # ids must resolve first.
        try:
            with mod._ISLAND_JOBS_LOCK:
                entry = mod._ISLAND_JOBS.get(jid)
            if entry is not None:
                if entry.get("done"):
                    out = {"done": True}
                    out.update(entry.get("result") or {})
                    return out
                return {"done": False}
        except Exception:
            pass
        # 4. Update check (global state, no id).
        try:
            with mod._CHECK_UPDATES_LOCK:
                cu = dict(mod._CHECK_UPDATES)
            if cu.get("checking") or cu.get("update") is not None \
                    or cu.get("error"):
                return _update_status_from_state(
                    bool(cu.get("checking")), cu.get("update"),
                    cu.get("error"))
        except Exception:
            pass
        # 5. Clean-cache results.
        try:
            app = _app(api)
            entry = (getattr(app, "_clean_cache_results", None)
                     or {}).get(jid)
            if entry is not None:
                result = entry.get("result") or {}
                freed = result.get("bytes_freed_mb")
                return {"done": bool(entry.get("done")),
                        "ok": bool(result.get("ok")),
                        "freed_mb": freed if isinstance(
                            freed, (int, float)) else 0,
                        "files_removed": result.get("files_removed", 0),
                        "token": None}
        except Exception:
            pass
    else:
        # No main module (standalone test): only the key-test getter
        # can tell "unknown" from "pending" (it says "unknown id").
        try:
            res = api.get_test_api_key_result(jid) or {}
            if res.get("detail") != "unknown id":
                return _key_test_result(res)
        except Exception:
            pass
        try:
            cu = api.get_check_updates_result() or {}
            if cu.get("checking") or cu.get("update") is not None \
                    or cu.get("error"):
                return _update_status_from_state(
                    bool(cu.get("checking")), cu.get("update"),
                    cu.get("error"))
        except Exception:
            pass
    return {"done": False, "ok": False, "detail": "unknown id"}


# -- update status ---------------------------------------------------------------------
# Bridges BOTH v1 polls to the v2 UI's {done, available} shape:
# check_updates (via get_check_updates_result) and install_update /
# download_update (via updater.get_progress(), states idle /
# downloading / verifying / downloaded / installing / done / error).
# Install/download progress takes precedence once a check has
# finished, so a poll started by install_update never resolves with
# the stale check result. The id is accepted and ignored: v1 keeps
# ONE global check state and ONE global progress dict, so there is
# only ever one job of each kind in flight.

def get_update_status(api, job_id=None):
    try:
        cu = api.get_check_updates_result() or {}
    except Exception:
        cu = {}
    if cu.get("checking"):
        return {"done": False, "checking": True}
    try:
        import updater as updater_mod
        prog = updater_mod.get_progress() or {}
    except Exception:
        prog = {}
    state = str(prog.get("state") or "idle")
    if state in ("downloading", "verifying", "installing"):
        return {"done": False, "state": state,
                "percent": prog.get("percent"),
                "downloaded": prog.get("downloaded"),
                "total": prog.get("total"),
                "version": prog.get("version", "")}
    if state == "downloaded":
        return {"done": True, "available": True, "downloaded": True,
                "version": prog.get("version", ""),
                "message": "التحديث منزّل وجاهز للتثبيت"}
    if state == "done":
        return {"done": True, "ok": True, "message": "تم التثبيت"}
    if state == "error":
        return {"done": True, "ok": False, "available": False,
                "message": "حصل خطأ أثناء التحديث — شوف السجل."}
    return _update_status_from_state(
        False, cu.get("update"), cu.get("error"))


# -- deep-file access ------------------------------------------------------------------
# v1 returns {"pending": True, "id"} and the UI is expected to poll
# get_deep_file_access_result(id). The ported v2 UI does NOT poll -
# it reads res.message synchronously. This adapter adds that message
# right away (the permission-card flow really did start in a
# background thread), so the UI shows something truthful instead of
# "undefined". The poll getter stays available for any other caller.

def request_deep_file_access_v2(api, path):
    """v2 request_deep_file_access -> {pending, id, message} at once.
    Calls _request_deep_file_access_raw when the coordinator renamed
    the v1 body there (see the patch spec), else the original
    Api.request_deep_file_access - never the wrapper itself. The
    message is added synchronously because the ported v2 UI reads
    res.message right away and never polls."""
    raw = getattr(api, "_request_deep_file_access_raw", None) \
        or api.request_deep_file_access
    res = raw(path) or {}
    out = dict(res)
    out["ok"] = True
    out["message"] = "الطلب اتبعت — منتظر موافقتك"
    return out


# -- owner code / backup ------------------------------------------------------------------

def check_owner_code(api, code):
    """v2 check_owner_code -> v1 unlock_owner (same SHA-256 hash
    compare; the typed code is never logged, echoed, or stored)."""
    return api.unlock_owner(code)


def export_backup(api, password):
    """v2 export_backup(password) - one arg. v1 needs (path,
    password), so the adapter supplies the default path under
    DATA_DIR: lona-backup-<YYYYMMDD-HHMMSS>.lona. The password is
    never logged. Calls backup.export_settings_backup directly (not
    Api.export_backup) so the coordinator can replace that method
    with a one-line delegation to this adapter."""
    password = str(password or "")
    if not password:
        return {"ok": False, "detail": "password_required"}
    path = DATA_DIR / ("lona-backup-"
                       + datetime.now().strftime("%Y%m%d-%H%M%S")
                       + ".lona")
    try:
        import backup as backup_mod
        return backup_mod.export_settings_backup(str(path), password)
    except Exception as exc:
        error("backup", f"export_backup (v2 adapter) failed: {exc}")
        return {"ok": False, "path": "", "detail": str(exc)}


# -- island ---------------------------------------------------------------------------
# The island window resizes through v1 island_resize(w, h), which
# already re-applies the window region via refresh_island_region.
# The region re-application itself is GATED (WindyLand/Nusa study
# pending) - this adapter only calls the existing functions and
# changes no region logic. Sizes are the approved fixed ones:
# collapsed 220x60, expanded 520x300.

def island_expand(api):
    return api.island_resize(520, 300)


def island_collapse(api):
    return api.island_resize(220, 60)
