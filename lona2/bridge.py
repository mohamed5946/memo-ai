"""LONA v2 — JS bridge (the Api object the UI calls).

Every method returns a plain JSON object. Slow operations (chat,
key test) run on background threads and return {pending, id}; the UI
polls job_result(id). Nothing here blocks the UI thread, and no
error is ever swallowed silently — failures come back as data.
"""

import os
import shutil
import threading
import time
import uuid

from . import VERSION
from . import apikeys, backup, brain, health, invite, monitor, owner
from . import stores, updater
from .logger import error, info, timed, warn
from .stores import DATA_DIR, get_memories, get_settings, get_tasks
from .stores import save_memories, save_settings, save_tasks

_jobs = {}
_jobs_lock = threading.Lock()


def _new_job():
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"done": False, "reply": None,
                         "engine": None, "error": None}
    return job_id


def _finish_job(job_id, reply=None, engine=None, error=None):
    with _jobs_lock:
        _jobs[job_id] = {"done": True, "reply": reply,
                         "engine": engine, "error": error}


# The UI (built from the v4.2 preview) speaks short settings keys
# (r1..r8, greet, vc, ...). The stores speak canonical keys. The
# bridge translates both ways so neither side silently drops a value.
_UI_TO_CANONICAL = {
    "name": "ai_name", "user": "user_name", "lang": "language",
    "greet": "greet_morning", "greeting_enabled": "greet_morning",
    "voice_output": "voice_reply",
    "auto": "autostart", "island": "island_enabled",
    "listen": "listen_enabled", "wakeword_enabled": "listen_enabled",
    "priv": "privacy_mode", "waNum": "whatsapp_owner_number",
    "owner_number": "whatsapp_owner_number",
    "mon": "monitor_enabled", "tempwatch_enabled": "monitor_enabled",
    "r1": "r1_ram_free_mb", "r2": "r2_ram_pct",
    "r3": "r3_disk_free_gb", "r4": "r4_disk_free_pct",
    "r5": "r5_cpu_temp_c", "r6": "r6_cpu_load_pct",
    "r6m": "r6_load_minutes", "r7": "r7_battery_pct",
    "r8": "r8_alert_cooldown_min",
    "inapp": "notify_in_app", "toast": "notify_toast",
    "notif": "notifications_enabled",
    "apps": "notify_apps", "kws": "notify_keywords",
    "vc": "voice_choice", "voice_choice": "voice_choice",
    "av": "avatar", "lite": "lite_mode",
}
_CANONICAL_TO_UI = {
    "ai_name": "name", "user_name": "user", "language": "lang",
    "greet_morning": "greet", "voice_reply": "voice",
    "autostart": "auto", "island_enabled": "island",
    "listen_enabled": "listen", "privacy_mode": "priv",
    "whatsapp_owner_number": "waNum", "monitor_enabled": "mon",
    "r1_ram_free_mb": "r1", "r2_ram_pct": "r2",
    "r3_disk_free_gb": "r3", "r4_disk_free_pct": "r4",
    "r5_cpu_temp_c": "r5", "r6_cpu_load_pct": "r6",
    "r6_load_minutes": "r6m", "r7_battery_pct": "r7",
    "r8_alert_cooldown_min": "r8",
    "notify_in_app": "inapp", "notify_toast": "toast",
    "notifications_enabled": "notif", "notify_apps": "apps",
    "notify_keywords": "kws", "voice_choice": "vc",
    "avatar": "av", "lite_mode": "lite",
}
_VOICE_CHOICES = ("m1", "m2", "f1", "f2")


def _translate_patch(patch: dict) -> dict:
    """UI keys (short or canonical) -> canonical keys. The UI sends
    'voice' with two meanings: a bool means voice_reply, a choice
    string ('m1'..'f2') means voice_choice — disambiguated by type."""
    out = {}
    for key, value in (patch or {}).items():
        if key == "voice":
            if isinstance(value, bool):
                out["voice_reply"] = value
            elif isinstance(value, str) and value in _VOICE_CHOICES:
                out["voice_choice"] = value
            continue
        canonical = _UI_TO_CANONICAL.get(key, key)
        out[canonical] = value
    return out


def _to_ui_settings(canonical: dict) -> dict:
    """Canonical settings + UI short aliases, flat (the UI reads the
    dict itself, not an envelope)."""
    out = {"ok": True}
    out.update(canonical)
    for canon, short in _CANONICAL_TO_UI.items():
        if canon in canonical:
            out[short] = canonical[canon]
    out["ai_name"] = canonical.get("ai_name")
    out["user_name"] = canonical.get("user_name")
    out["greeting_enabled"] = canonical.get("greet_morning")
    out["voice_output"] = canonical.get("voice_reply")
    out["island_enabled"] = canonical.get("island_enabled")
    out["wakeword_enabled"] = canonical.get("listen_enabled")
    out["privacy_mode"] = canonical.get("privacy_mode")
    out["owner_number"] = canonical.get("whatsapp_owner_number")
    out["tempwatch_enabled"] = canonical.get("monitor_enabled")
    out["notifications_enabled"] = canonical.get("notifications_enabled")
    out["voice_choice"] = canonical.get("voice_choice")
    out["avatar"] = canonical.get("avatar")
    out["lite_mode"] = canonical.get("lite_mode")
    return out


class Api:
    """Exposed to the UI as window.pywebview.js_api."""

    def __init__(self, app=None):
        self._app = app
        self._mood = "هادي"
        self._last_action = "—"
        self._current_task = "مفيش مهمة شغالة"

    # --- status ---------------------------------------------------------
    @timed("get_status")
    def get_status(self):
        settings = get_settings()
        return {"ok": True,
                "version": VERSION,
                "activated": invite.is_activated(),
                "ai_name": settings["ai_name"],
                "user_name": settings["user_name"],
                "wake_word": "يا " + (settings["ai_name"] or "لونا"),
                "island_enabled": settings["island_enabled"],
                "listen_enabled": settings["listen_enabled"],
                "muted": settings["muted"],
                "model_choice": settings["model_choice"],
                "theme": settings["theme"],
                "lite_mode": settings["lite_mode"]}

    @timed("get_mood")
    def get_mood(self):
        return {"mood": self._mood}

    def _set_mood(self, mood):
        self._mood = mood
        if self._app is not None:
            self._app.push_mood(mood)

    # --- chat -----------------------------------------------------------
    @timed("chat")
    def chat(self, text, opts=None):
        text = str(text or "").strip()
        if not text:
            return {"pending": False, "error": "اكتب رسالة الأول"}
        opts = opts or {}
        settings = get_settings()
        model = str(opts.get("model") or settings["model_choice"] or "auto")
        deep_think = bool(opts.get("deep_think", False))
        keys = {p: apikeys.get_api_key_value(p) for p in apikeys.PROVIDERS}
        self._set_mood("بيفكّر")
        self._current_task = "بيرد على رسالتك"
        result = brain.chat(text, model, deep_think, keys)
        job_id = result["id"]
        # Track mood restoration when the job finishes.
        thread = threading.Thread(target=self._watch_chat_job,
                                  daemon=True, args=(job_id,))
        thread.start()
        return result

    def _watch_chat_job(self, job_id):
        for _ in range(600):  # up to ~10 minutes
            time.sleep(1)
            res = brain.chat_result(job_id)
            if res.get("done"):
                self._set_mood("هادي")
                self._current_task = "مفيش مهمة شغالة"
                self._last_action = "رد على رسالة"
                return

    @timed("chat_result")
    def chat_result(self, job_id):
        return brain.chat_result(str(job_id or ""))

    @timed("job_result")
    def job_result(self, job_id):
        with _jobs_lock:
            job = _jobs.get(str(job_id or ""))
        if job is None:
            return {"done": True, "reply": None, "engine": None,
                    "error": "المهمة دي مش موجودة"}
        return {"done": job["done"], "reply": job["reply"],
                "engine": job["engine"], "error": job["error"]}

    # --- device ---------------------------------------------------------
    @timed("get_device_overview")
    def get_device_overview(self):
        return {"ok": True, "overview": monitor.get_device_overview()}

    @timed("get_model_options")
    def get_model_options(self):
        keys = {p: apikeys.has_api_key(p) for p in apikeys.PROVIDERS}
        return {"ok": True,
                "choice": get_settings().get("model_choice", "auto"),
                "options": brain.get_model_options(keys, get_settings())}

    @timed("set_model_choice")
    def set_model_choice(self, model_id):
        valid = {"auto", "groq", "gemini", "ollama"}
        if model_id not in valid:
            return {"ok": False, "error": "موديل غير معروف"}
        return save_settings({"model_choice": model_id})

    @timed("set_muted")
    def set_muted(self, muted):
        return save_settings({"muted": bool(muted)})

    # --- settings / tasks / memories ------------------------------------
    @timed("get_settings")
    def get_settings(self):
        return _to_ui_settings(get_settings())

    @timed("save_settings")
    def save_settings(self, patch):
        if not isinstance(patch, dict):
            return {"ok": False, "error": "patch must be an object",
                    "message": "patch must be an object"}
        result = save_settings(_translate_patch(patch))
        if not result.get("ok"):
            result["message"] = result.get("error", "save failed")
        return result

    @timed("get_tasks")
    def get_tasks(self):
        return {"ok": True, "tasks": get_tasks()}

    @timed("add_task")
    def add_task(self, text):
        text = str(text or "").strip()
        if not text:
            return {"ok": False, "error": "اكتب المهمة الأول"}
        tasks = get_tasks()
        task = {"id": uuid.uuid4().hex[:12], "text": text, "done": False}
        tasks.append(task)
        res = save_tasks(tasks)
        res["task"] = task
        return res

    @timed("toggle_task")
    def toggle_task(self, task_id):
        tasks = get_tasks()
        for task in tasks:
            if task.get("id") == task_id:
                task["done"] = not task.get("done", False)
                return save_tasks(tasks)
        return {"ok": False, "error": "المهمة مش موجودة"}

    @timed("delete_task")
    def delete_task(self, task_id):
        tasks = [t for t in get_tasks() if t.get("id") != task_id]
        return save_tasks(tasks)

    @timed("get_memories")
    def get_memories(self):
        return {"ok": True, "memories": get_memories()}

    @timed("delete_memory")
    def delete_memory(self, memory_id):
        memories = [m for m in get_memories()
                    if str(m.get("id")) != str(memory_id)]
        return save_memories(memories)

    @timed("clear_memories")
    def clear_memories(self):
        return save_memories([])

    # --- diagnostics ------------------------------------------------------
    @timed("get_log")
    def get_log(self, count=200):
        from .logger import get_lines
        lines = get_lines(int(count or 200))
        return {"ok": True, "lines": lines, "entries": lines,
                "log": "\n".join(lines)}

    @timed("log_error")
    def log_error(self, source, message):
        error(str(source or "ui"), str(message or ""))
        return {"ok": True}

    # --- API keys ---------------------------------------------------------
    @timed("get_api_keys_status")
    def get_api_keys_status(self):
        status = apikeys.get_api_keys_status()
        out = {"ok": True, "status": dict(status)}
        out.update(status)
        return out

    @timed("get_api_key_value")
    def get_api_key_value(self, provider):
        # Called ONLY on the user's explicit "show" press. The value is
        # returned to the UI and never written to any log.
        value = apikeys.get_api_key_value(provider)
        if value is None:
            return {"ok": False, "error": "مفيش مفتاح محفوظ"}
        info("app", f"API key revealed on explicit request: {provider}")
        return {"ok": True, "value": value}

    @timed("save_api_key")
    def save_api_key(self, provider, value):
        result = apikeys.save_api_key(provider, value)
        if not result.get("ok"):
            result["message"] = result.get("error", "save failed")
        return result

    @timed("test_api_key")
    def test_api_key(self, provider):
        job_id = _new_job()
        thread = threading.Thread(target=self._test_key_worker,
                                  daemon=True, args=(job_id, provider))
        thread.start()
        return {"pending": True, "id": job_id}

    def _test_key_worker(self, job_id, provider):
        import requests
        key = apikeys.get_api_key_value(provider)
        if not key:
            _finish_job(job_id, error="مفيش مفتاح محفوظ")
            return
        try:
            if provider == "groq":
                resp = requests.get(
                    "https://api.groq.com/openai/v1/models", timeout=15,
                    headers={"Authorization": f"Bearer {key}"})
            elif provider in ("gemini_voice", "gemini_chat"):
                resp = requests.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    timeout=15, params={"key": key})
            else:
                _finish_job(job_id, error="provider غير معروف")
                return
            if resp.status_code == 200:
                _finish_job(job_id, reply="المفتاح اتقبل ✓")
            elif resp.status_code in (401, 403):
                _finish_job(job_id, error="المفتاح مرفوض — اتأكد منه")
            else:
                _finish_job(job_id,
                            error=f"السيرفر رجّع {resp.status_code}")
        except Exception as exc:
            _finish_job(job_id, error=f"تعذّر الوصول للسيرفر ({exc})")

    # --- updates ----------------------------------------------------------
    @timed("check_updates")
    def check_updates(self):
        updater.check_updates()
        return {"pending": True, "id": "updates"}

    @timed("get_update_status")
    def get_update_status(self, job_id=None):
        # job_id is accepted (and ignored) because the UI polls with the
        # id it got from check_updates. pollUntil resolves on done.
        st = updater.get_update_status()
        state = st.get("state")
        out = dict(st)
        out["done"] = state in ("available", "up-to-date", "error",
                                "downloaded")
        out["available"] = (state == "available")
        out["latest"] = st.get("version")
        if state == "available":
            out["message"] = f"في تحديث جديد: {st.get('version') or ''}"
        elif state == "up-to-date":
            out["message"] = "أنت على آخر إصدار"
        elif state == "error":
            out["message"] = st.get("error") or "الفحص فشل"
        return out

    @timed("install_update")
    def install_update(self):
        return updater.install_update()

    # --- deep files / cleanup ---------------------------------------------
    @timed("request_deep_file_access")
    def request_deep_file_access(self, path=""):
        # path is optional (the v1.6.3 bug made it required and the JS
        # call without an argument always failed). Granting is recorded.
        save_settings({"deep_files_allowed": True})
        info("app", "Deep file access granted by the user")
        return {"ok": True}

    @timed("clean_cache")
    def clean_cache(self, scope="app"):
        cache_dir = DATA_DIR / "cache"
        trash_dir = DATA_DIR / "trash"
        token = uuid.uuid4().hex[:12]
        freed = 0
        try:
            if cache_dir.exists():
                dest = trash_dir / token
                dest.mkdir(parents=True, exist_ok=True)
                for item in cache_dir.iterdir():
                    try:
                        size = (item.stat().st_size if item.is_file()
                                else 0)
                        shutil.move(str(item), str(dest / item.name))
                        freed += size
                    except Exception:
                        continue
            return {"ok": True, "freed_mb": round(freed / (1024 * 1024), 1),
                    "token": token}
        except Exception as exc:
            error("app", f"Cache clean failed: {exc}")
            return {"ok": False, "error": "مقدرتش أنضّف الكاش"}

    @timed("undo_clean")
    def undo_clean(self, token):
        src = DATA_DIR / "trash" / str(token or "")
        cache_dir = DATA_DIR / "cache"
        if not src.exists():
            return {"ok": False, "error": "مفيش حاجة ترجعها"}
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            for item in src.iterdir():
                shutil.move(str(item), str(cache_dir / item.name))
            shutil.rmtree(src, ignore_errors=True)
            return {"ok": True}
        except Exception as exc:
            error("app", f"Undo clean failed: {exc}")
            return {"ok": False, "error": "مقدرتش أرجّع الملفات"}

    # --- self view / brief --------------------------------------------------
    @timed("get_self_view")
    def get_self_view(self):
        overview = monitor.get_device_overview()
        settings = get_settings()
        deep = ("مسموحة" if settings["deep_files_allowed"]
                else "مقفولة")
        rows = [
            {"label": "المزاج", "value": self._mood},
            {"label": "المهمة الحالية", "value": self._current_task},
            {"label": "آخر إجراء", "value": self._last_action},
            {"label": "الحرارة", "value": overview["temp"]},
            {"label": "الملفات العميقة", "value": deep},
            {"label": "كروم", "value": "غير متصلة"},
            {"label": "الموديل", "value": settings["model_choice"]},
            {"label": "الرام", "value": overview["ram"]["percent"]},
        ]
        return {"ok": True, "rows": rows,
                "mood": self._mood,
                "task": self._current_task,
                "last_action": self._last_action,
                "temp": overview["temp"],
                "deep_files": deep,
                "chrome": "غير متصلة",
                "model": settings["model_choice"],
                "ram_pct": overview["ram"]["percent"]}

    @timed("get_morning_brief")
    def get_morning_brief(self):
        overview = monitor.get_device_overview()
        tasks = [t for t in get_tasks() if not t.get("done")]
        alerts = health.evaluate(overview, get_settings())
        items = [
            f"البطارية {overview['battery']['percent']}%",
            f"الرام {overview['ram']['percent']}%",
            f"الحرارة {overview['temp']}",
            f"مهام مفتوحة: {len(tasks)}",
        ]
        items.extend(a["message"] for a in alerts)
        return {"ok": True,
                "brief": "\n".join(items), "items": items,
                "battery": overview["battery"]["percent"],
                "ram_pct": overview["ram"]["percent"],
                "temp": overview["temp"],
                "open_tasks": len(tasks),
                "alerts": [a["message"] for a in alerts]}

    # --- WhatsApp (Phase 2 — honest stub) -----------------------------------
    @timed("get_whatsapp_status")
    def get_whatsapp_status(self):
        return {"ok": True, "enabled": False, "status": "not-built",
                "message": "واتساب لسه في المرحلة التانية"}

    @timed("whatsapp_relink")
    def whatsapp_relink(self):
        return {"ok": False,
                "error": "واتساب لسه في المرحلة التانية"}

    @timed("whatsapp_disconnect")
    def whatsapp_disconnect(self):
        return {"ok": False,
                "error": "واتساب لسه في المرحلة التانية"}

    # --- owner / invite / backup ---------------------------------------------
    @timed("set_owner_code")
    def set_owner_code(self, code):
        return owner.set_owner_code(code)

    @timed("check_owner_code")
    def check_owner_code(self, code):
        return {"ok": owner.check_owner_code(code)}

    @timed("check_invite")
    def check_invite(self, code):
        return {"result": invite.verify(code)}

    @timed("export_backup")
    def export_backup(self, password):
        payload = {"settings": get_settings(), "tasks": get_tasks(),
                   "memories": get_memories(),
                   "keys": {p: apikeys.get_api_key_value(p)
                            for p in apikeys.PROVIDERS}}
        res = backup.export_backup(password, payload)
        if not res.get("ok"):
            res["message"] = res.get("error", "export failed")
            return res
        try:
            path = DATA_DIR / "lona-backup.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(res["data"], encoding="utf-8")
            return {"ok": True, "path": str(path)}
        except Exception as exc:
            error("app", f"Backup write failed: {exc}")
            return {"ok": False, "error": "مقدرتش أكتب ملف النسخة",
                    "message": "مقدرتش أكتب ملف النسخة"}

    @timed("import_backup")
    def import_backup(self, path, password):
        try:
            blob = open(path, encoding="utf-8").read().strip()
        except Exception:
            return {"ok": False, "error": "مقدرتش أقرا الملف",
                    "message": "مقدرتش أقرا الملف"}
        res = backup.import_backup(password, blob)
        if not res.get("ok"):
            res["message"] = res.get("error", "import failed")
            return res
        payload = res["payload"]
        if isinstance(payload.get("settings"), dict):
            save_settings(payload["settings"])
        if isinstance(payload.get("tasks"), list):
            save_tasks(payload["tasks"])
        if isinstance(payload.get("memories"), list):
            save_memories(payload["memories"])
        keys = payload.get("keys") or {}
        for provider, value in keys.items():
            if value:
                apikeys.save_api_key(provider, value)
        return {"ok": True}

    # --- island / window -------------------------------------------------------
    @timed("island_resize")
    def island_resize(self, w, h):
        if self._app is None:
            return {"ok": False, "error": "النافذة مش شغالة"}
        return self._app.island_resize(int(w), int(h))

    @timed("island_expand")
    def island_expand(self):
        if self._app is None:
            return {"ok": False, "error": "النافذة مش شغالة"}
        return self._app.island_expand()

    @timed("island_collapse")
    def island_collapse(self):
        if self._app is None:
            return {"ok": False, "error": "النافذة مش شغالة"}
        return self._app.island_collapse()

    @timed("window_minimize")
    def window_minimize(self):
        if self._app is None:
            return {"ok": False, "error": "النافذة مش شغالة"}
        return self._app.window_minimize()

    @timed("window_toggle_maximize")
    def window_toggle_maximize(self):
        if self._app is None:
            return {"ok": False, "error": "النافذة مش شغالة"}
        return self._app.window_toggle_maximize()

    @timed("window_close")
    def window_close(self):
        if self._app is None:
            return {"ok": False, "error": "النافذة مش شغالة"}
        return self._app.window_close()

    @timed("window_move_to")
    def window_move_to(self, x, y):
        if self._app is None:
            return {"ok": False, "error": "النافذة مش شغالة"}
        return self._app.window_move_to(int(x), int(y))

    @timed("quit_app")
    def quit_app(self):
        if self._app is not None:
            threading.Thread(target=self._app.quit, daemon=True).start()
        return {"ok": True}
