# LONA v1.6.0 - Proactive companion: LONA as a living person inside
# the computer, not a tool you have to ask.
#
# - Morning brief: one short message on startup (battery, RAM, today's
#   appointments from data, anything needing attention - spoken only
#   when voice output is on).
# - End-of-day recap: one evening line (done today + tomorrow's
#   appointments).
# - Focus mode: start/stop - silences non-urgent alerts, runs Pomodoro
#   25min work / 5min break with a celebration at the end, sets mood
#   مركّز (via the monitor's focus flag).
# - Smart battery warning: warn before the battery dies, offering to
#   enable power-saving itself.
# - Quiet hours / smart interruption: during a movie/game (fullscreen
#   app detected) only truly important alerts pass.
# - Routine learning: record usual study/Pomodoro times in memory.json
#   routines; suggest at those times.
# - Proactive update alerts: "في update لكذا… أعملها ولا تحب أعملها أنا؟"
# - Proactive check-ins when idle (laptop open + idle, e.g. watching a
#   movie): "ازيك عامل ايه، ممكن أساعدك في حاجة دلوقتي؟" + suggestions.
#
# Idle detection via GetLastInputInfo (Windows, guarded). ALL companion
# messages go through the existing _on_system_alert / in-app alerts
# channel (on_alert callback) so the UI can show them. The background
# loop is best-effort and never blocks startup.

import ctypes
import threading
import time
from datetime import date, datetime, timedelta

from logger import info, warn

POMODORO_WORK_MIN = 25
POMODORO_BREAK_MIN = 5
IDLE_CHECKIN_SEC = 15 * 60
BATTERY_WARN_PCT = 20


# -- exam countdown (scope item 7) ----------------------------------------

def _parse_exam_date(raw: str):
    try:
        return date.fromisoformat(str(raw or "").strip())
    except Exception:
        return None


def get_next_exam(exams: list | None, today: date | None = None) -> dict:
    """Nearest upcoming exam from settings' exams list
    ([{"subject", "date": "YYYY-MM-DD"}]). Returns {"subject", "date",
    "days_left"} or {"set": False, "detail": ...} when the list is empty
    / has no valid future date. NEVER invents a date: an empty list is
    honestly "no date set"."""
    today = today or date.today()
    best = None
    for item in exams or []:
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subject") or "").strip()
        when = _parse_exam_date(item.get("date"))
        if not subject or when is None or when < today:
            continue
        days = (when - today).days
        if best is None or days < best["days_left"]:
            best = {"subject": subject, "date": when.isoformat(),
                    "days_left": days}
    if best is None:
        return {"set": False,
                "detail": "مفيش موعد امتحان متسجل — ضيف مواعيدك من الإعدادات"}
    return {"set": True, **best}


# -- idle / fullscreen detection (Windows, guarded) ------------------------

def idle_seconds() -> float | None:
    """Seconds since last input (GetLastInputInfo). None off Windows or
    on failure - never guessed."""
    if sys_platform() != "win32":
        return None
    try:
        class _LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint),
                        ("dwTime", ctypes.c_uint)]

        obj = _LASTINPUTINFO()
        obj.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(obj)):
            return None
        tick = ctypes.windll.kernel32.GetTickCount()
        return max(0.0, (tick - obj.dwTime) / 1000.0)
    except Exception:
        return None


def sys_platform() -> str:
    import sys
    return sys.platform


def is_fullscreen_app() -> bool:
    """True when the foreground window covers the whole screen (movie /
    game). Windows only, guarded - False on any failure."""
    if sys_platform() != "win32":
        return False
    try:
        user32 = ctypes.windll.user32

        class _RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        rect = _RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        return (rect.right - rect.left >= sw and
                rect.bottom - rect.top >= sh)
    except Exception:
        return False


# -- companion --------------------------------------------------------------

class Companion:
    """Proactive companion. settings: plain dict (merged over defaults
    in main.py). on_alert: callback(alert dict) - wired to
    MemoApp._on_system_alert. monitor_fn: () -> status dict (or None).
    memory_store: the long-term memory store (routines live there) or
    None. All feed items are also kept in self._feed for
    Api.get_proactive_feed()."""

    def __init__(self, settings=None, on_alert=None, monitor_fn=None,
                 memory_store=None, idle_fn=None, fullscreen_fn=None):
        self.settings = dict(settings or {})
        self.on_alert = on_alert
        self.monitor_fn = monitor_fn
        self.memory_store = memory_store
        self._idle_fn = idle_fn or idle_seconds
        self._fullscreen_fn = fullscreen_fn or is_fullscreen_app
        self._feed: list = []
        self._feed_lock = threading.Lock()
        self._seq = 0
        self._stop = threading.Event()
        self._thread = None
        self.running = False
        self._briefed = False
        self._recapped = False
        self._last_checkin_at = 0.0
        self._last_battery_warn_at = 0.0
        # Focus / Pomodoro state (durations injectable for tests).
        self._focus = {"active": False, "phase": "",
                       "ends_at": 0.0, "work_min": POMODORO_WORK_MIN,
                       "break_min": POMODORO_BREAK_MIN}
        self._focus_stop = threading.Event()
        self._focus_thread = None

    # -- feed ----------------------------------------------------------------

    def _push(self, kind: str, text: str, level: str = "info") -> dict:
        """Add one item to the proactive feed AND the in-app alert
        channel (so the UI shows it). Returns the item."""
        with self._feed_lock:
            self._seq += 1
            item = {"id": f"proactive-{self._seq}", "kind": kind,
                    "text": text, "level": level,
                    "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            self._feed.append(item)
            if len(self._feed) > 30:
                self._feed = self._feed[-30:]
        info("companion", f"Proactive ({kind}): {text[:120]}")
        if self.on_alert is not None:
            try:
                self.on_alert({"kind": f"proactive_{kind}",
                               "level": level,
                               "title_ar": "لونا",
                               "message_ar": text,
                               "at": item["at"]})
            except Exception as exc:
                warn("companion", f"on_alert failed: {exc}")
        return item

    def get_feed(self) -> list:
        with self._feed_lock:
            return [dict(i) for i in self._feed]

    # -- quiet hours / smart interruption ------------------------------------

    def should_alert(self, level: str, important: bool = False) -> bool:
        """Smart interruption: during a fullscreen app (movie/game) only
        truly important alerts pass; otherwise everything passes. Quiet
        hours (when enabled) apply the same rule during fullscreen."""
        if not self.settings.get("quiet_hours_enabled", True):
            return True
        try:
            if self._fullscreen_fn():
                return bool(important or level == "critical")
        except Exception:
            pass
        return True

    # -- morning brief / recap ----------------------------------------------

    def _today_appointments(self) -> list:
        """Today's appointments from data: exams today + memory routines
        tagged for today. No calendar is connected yet (capabilities.py
        says so honestly), so this only ever lists what is actually
        stored - never invented appointments."""
        out = []
        try:
            exams = self.settings.get("exams") or []
            today = date.today().isoformat()
            for item in exams or []:
                if isinstance(item, dict) and item.get("date") == today:
                    out.append(f"امتحان {item.get('subject', '')} النهاردة")
        except Exception:
            pass
        return out

    def morning_brief(self, status: dict | None = None) -> dict:
        """One short startup message: battery, RAM, today's appointments,
        anything needing attention. Spoken only when voice output is on
        (the caller - main.py - owns speak_async; this returns the text
        and a speak flag)."""
        if not self.settings.get("morning_brief_enabled", True):
            return {"ok": False, "reason": "disabled"}
        if status is None and self.monitor_fn is not None:
            try:
                status = self.monitor_fn()
            except Exception:
                status = None
        parts = []
        if status:
            battery = status.get("battery")
            if battery:
                parts.append(f"البطارية {battery.get('percent')}%"
                             + (" (بتشحن)" if battery.get("charging")
                                else ""))
            ram = status.get("ram") or {}
            if ram.get("free_mb") is not None:
                parts.append(f"الرام الفاضية {ram['free_mb']} ميجا")
            temp_c = status.get("temp_c")
            if temp_c is not None:
                parts.append(f"الحرارة {temp_c}°م")
        appts = self._today_appointments()
        if appts:
            parts.append("مواعيد النهاردة: " + "، ".join(appts))
        exam = get_next_exam(self.settings.get("exams") or [])
        if exam.get("set") and exam.get("days_left", 99) <= 14:
            parts.append(f"امتحان {exam['subject']} بعد "
                         f"{exam['days_left']} يوم")
        text = ("صباح الخير " + (" — ".join(parts) if parts
                                     else "كل حاجة تمام، يومك حلو إن شاء الله"))
        self._briefed = True
        item = self._push("morning_brief", text)
        return {"ok": True, "text": text, "item": item,
                "speak": bool(self.settings.get("voice_output", True))}

    def end_of_day_recap(self, done_today: str = "") -> dict:
        """One evening line: what was done today + tomorrow's
        appointments. `done_today` comes from the caller (the app's own
        record) - never invented here."""
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        appts = []
        try:
            for item in self.settings.get("exams") or []:
                if isinstance(item, dict) and item.get("date") == tomorrow:
                    appts.append(f"امتحان {item.get('subject', '')} بكرة")
        except Exception:
            pass
        text = "يومك خلص "
        text += (f"اللي اتعمل النهاردة: {done_today}. "
                 if done_today else "النهاردة عدى على خير. ")
        text += ("مواعيد بكرة: " + "، ".join(appts) if appts
                 else "بكرة مفيش مواعيد متسجلة — نام مرتاح.")
        self._recapped = True
        item = self._push("recap", text)
        return {"ok": True, "text": text, "item": item}

    # -- smart battery warning ------------------------------------------------

    def smart_battery_warning(self, status: dict | None = None) -> dict:
        """Warn BEFORE the battery dies (below 20% discharging, at most
        once per 30 min), offering to enable power-saving itself."""
        if status is None and self.monitor_fn is not None:
            try:
                status = self.monitor_fn()
            except Exception:
                status = None
        battery = (status or {}).get("battery")
        if not battery or battery.get("charging"):
            return {"ok": False, "reason": "charging_or_unknown"}
        pct = battery.get("percent")
        if pct is None or int(pct) >= BATTERY_WARN_PCT:
            return {"ok": False, "reason": "battery_ok"}
        now = time.time()
        if now - self._last_battery_warn_at < 30 * 60:
            return {"ok": False, "reason": "cooldown"}
        self._last_battery_warn_at = now
        text = (f"البطارية {pct}% وقربت تخلص — وصّل الشاحن، أو تحب "
                "أشغّل لك وضع توفير الطاقة بنفسي؟")
        item = self._push("battery_warning", text, level="warn")
        return {"ok": True, "text": text, "item": item}

    # -- proactive update alerts ----------------------------------------------

    def proactive_update_alert(self, name: str, detail: str = "") -> dict:
        """'في update لكذا… أعملها ولا تحب أعملها أنا؟' - sent unasked
        when the caller (updater/monitor) spots an update that could
        cause problems."""
        text = (f"في update لـ{name}… " + (f"{detail} " if detail else "")
                + "أعملها ولا تحب أعملها أنا؟")
        item = self._push("update_alert", text)
        return {"ok": True, "text": text, "item": item}

    # -- idle check-ins ---------------------------------------------------------

    def check_idle_and_checkin(self, idle_sec: float | None = None) -> dict:
        """Laptop open + idle (e.g. watching a movie): one check-in with
        suggestions. At most once per 30 min, and never during focus
        mode or fullscreen-critical moments (a fullscreen app still
        gets the check-in - that IS the movie case - but only when the
        idle time is real)."""
        if self._focus["active"]:
            return {"ok": False, "reason": "focus_mode"}
        if idle_sec is None:
            try:
                idle_sec = self._idle_fn()
            except Exception:
                idle_sec = None
        if idle_sec is None or idle_sec < IDLE_CHECKIN_SEC:
            return {"ok": False, "reason": "not_idle"}
        now = time.time()
        if now - self._last_checkin_at < 30 * 60:
            return {"ok": False, "reason": "cooldown"}
        self._last_checkin_at = now
        text = ("ازيك عامل ايه، ممكن أساعدك في حاجة دلوقتي؟ "
                "أقدر أذكّرك بمواعيدك، أشغّل لك حاجة على يوتيوب، "
                "أو نذاكر سوا لو تحب.")
        item = self._push("checkin", text)
        return {"ok": True, "text": text, "item": item}

    # -- routine learning -------------------------------------------------------

    def record_routine(self, text: str) -> dict:
        """Record a usual study/Pomodoro time in memory.json routines
        (via the memory store when available; otherwise the feed notes
        it). Returns {"ok", "routine_id"|"reason"}."""
        if not self.settings.get("routine_learning_enabled", True):
            return {"ok": False, "reason": "disabled"}
        text = str(text or "").strip()
        if not text:
            return {"ok": False, "reason": "empty"}
        store = self.memory_store
        if store is None:
            try:
                import memory as memory_mod
                store = memory_mod.get_store()
                self.memory_store = store
            except Exception:
                store = None
        if store is None:
            return {"ok": False, "reason": "memory_unavailable"}
        try:
            rid = store.add_routine(text)
            info("companion", f"Routine learned: {text[:80]}")
            return {"ok": True, "routine_id": rid}
        except Exception as exc:
            warn("companion", f"Could not record routine: {exc}")
            return {"ok": False, "reason": str(exc)}

    def suggest_routines(self, now: datetime | None = None) -> list:
        """Routines whose usual hour matches `now` (within the hour) -
        the caller suggests them at those times. Returns the matching
        routine texts (possibly [])."""
        now = now or datetime.now()
        out = []
        store = self.memory_store
        if store is None:
            try:
                import memory as memory_mod
                store = memory_mod.get_store()
                self.memory_store = store
            except Exception:
                return out
        try:
            routines = (store.data or {}).get("routines") or []
        except Exception:
            return out
        for r in routines:
            text = str((r or {}).get("text") or "")
            # Routines are free text ("study at 19:00") - match an
            # HH:MM / Hh pattern against the current hour.
            for token in text.replace(":", " ").split():
                try:
                    hour = int(token)
                except ValueError:
                    continue
                if 0 <= hour <= 23 and hour == now.hour:
                    out.append(text)
                    break
        return out

    # -- focus mode / Pomodoro ---------------------------------------------------

    def focus_start(self, work_min: int = POMODORO_WORK_MIN,
                    break_min: int = POMODORO_BREAK_MIN) -> dict:
        """Start focus mode: silences non-urgent alerts, runs Pomodoro
        (25min work / 5min break), sets mood مركّز. Durations are
        parameters so tests can run a 1-second Pomodoro."""
        if self._focus["active"]:
            return {"ok": False, "reason": "already_active",
                    "state": self.get_focus_state()}
        self._focus.update({"active": True, "phase": "work",
                            "work_min": int(work_min),
                            "break_min": int(break_min),
                            "ends_at": time.time() + int(work_min) * 60})
        self._focus_stop.clear()
        self._push("focus_start",
                   "وضع التركيز شغال — هكتم أي حاجة مش مهمة، "
                   f"ونشتغل {int(work_min)} دقيقة وبعدها بريك.")
        self._focus_thread = threading.Thread(
            target=self._pomodoro_loop, daemon=True, name="lona-focus")
        self._focus_thread.start()
        info("companion", "Focus mode started")
        return {"ok": True, "state": self.get_focus_state()}

    def _pomodoro_loop(self):
        work_sec = max(1, int(self._focus["work_min"]) * 60)
        break_sec = max(1, int(self._focus["break_min"]) * 60)
        if self._focus_stop.wait(timeout=work_sec):
            return  # stopped mid-work
        if not self._focus["active"]:
            return
        self._focus["phase"] = "break"
        self._focus["ends_at"] = time.time() + break_sec
        self._push("focus_break",
                   "خلصت جلسة الشغل خد بريك "
                   f"{self._focus['break_min']} دقايق — تستاهلها!")
        if self._focus_stop.wait(timeout=break_sec):
            return
        if not self._focus["active"]:
            return
        self._focus["phase"] = "done"
        self._push("focus_done",
                   "خلص البريك كمان — جاهز لجولة تانية؟ قولي "
                   "«ابدأ تركيز» وأنا معاك.")
        self._focus["active"] = False

    def focus_stop(self) -> dict:
        """Stop focus mode (and the Pomodoro timer)."""
        self._focus["active"] = False
        self._focus["phase"] = ""
        self._focus_stop.set()
        info("companion", "Focus mode stopped")
        return {"ok": True, "state": self.get_focus_state()}

    def get_focus_state(self) -> dict:
        remaining = 0
        if self._focus["active"]:
            remaining = max(0, int(self._focus["ends_at"] - time.time()))
        return {"active": bool(self._focus["active"]),
                "phase": self._focus["phase"],
                "remaining_sec": remaining,
                "work_min": self._focus["work_min"],
                "break_min": self._focus["break_min"]}

    # -- background loop ----------------------------------------------------------

    def start(self) -> bool:
        if self.running:
            return True
        if not self.settings.get("companion_enabled", True):
            info("companion", "Companion disabled in settings")
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="lona-companion")
        self._thread.start()
        self.running = True
        info("companion", "Proactive companion started")
        return True

    def _loop(self):
        while not self._stop.is_set():
            try:
                hour = datetime.now().hour
                if hour >= 22 and not self._recapped:
                    self.end_of_day_recap()
                self.check_idle_and_checkin()
                self.smart_battery_warning()
                for text in self.suggest_routines():
                    self._push("routine_suggestion",
                               f"دلوقتي معادك المعتاد: {text} — نبدأ؟")
            except Exception as exc:
                warn("companion", f"Companion loop failed: {exc}")
            self._stop.wait(60)

    def stop(self):
        self._stop.set()
        try:
            self.focus_stop()
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self.running = False
        info("companion", "Proactive companion stopped")
