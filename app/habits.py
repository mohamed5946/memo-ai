# LONA v1.6.4-rc.3 - habit learning, first HONEST version
# (OpenJarvis learning loop, local-only).
#
# What this IS: a small on-device store that records WHEN he uses
# LONA and WHAT KIND of thing he asks about - hour of day + a coarse
# category only. It then shows ONE morning suggestion ("you usually
# do X around this time") when the same pattern repeats on at least
# two different days. The suggestion's action button does something
# real (morning brief / focus session).
#
# What this is NOT, and never pretends to be:
# - No message text is ever stored. The category comes from keyword
#   matching on the text at record time; the text itself is dropped
#   immediately. Logs carry category + hour only, never text.
# - No "shift times" are claimed: the store only knows activity
#   hours. A repeated morning hour is reported as exactly that.
# - No phrases/topics are stored - only the coarse categories below.
# - Nothing leaves the device: one JSON file under app/data, no
#   network call, no cloud sync. The store respects the
#   `habits_enabled` setting (default ON, switchable in Settings ->
#   الأوضاع) and can be wiped from the same place.
# - No suggestion is ever invented: with too little data (fewer
#   than 3 events in the hour window, or all on one day) the
#   suggestion call returns not_enough_data and the UI shows
#   nothing at all.
#
# Storage shape (habits.json): {"version": 1, "events": [{day, hour,
# kind, category}]} - events older than 30 days are pruned on
# load/save, and the list is capped at 1000 (oldest dropped first).

import json
import threading
from datetime import datetime, timedelta

from logger import info
from settings_store import DATA_DIR

_HABITS_PATH = DATA_DIR / "habits.json"
_LOCK = threading.Lock()
_MAX_EVENTS = 1000
_RETENTION_DAYS = 30
# A suggestion needs a real repeat: at least 3 events in the hour
# window, spread over at least 2 different days, with one category
# clearly on top.
_MIN_EVENTS = 3
_MIN_DAYS = 2

# Coarse categories only. Keywords are matched against the message
# at record time; the message itself is never kept. Arabic first -
# he writes in Arabic - plus the English words he uses.
_CATEGORY_KEYWORDS = {
    "study": (
        "امتحان", "مذاكر", "ذاكر", "محاضر", "سيكشن", "درس", "واجب",
        "تكليف", "كويز", "ميدتيرم", "شرح", "مراجع",
        "exam", "study", "homework", "lecture", "quiz", "midterm",
        "assignment", "explain", "revise",
    ),
    "delivery": (
        "توصيل", "شغل", "طلب", "اوردر", "أوردر", "جواب", "امازون",
        "أمازون", "شيفت", "وردية", "ديليفري", "عميل",
        "delivery", "order", "shift", "work", "amazon", "gawab",
        "customer", "package",
    ),
    "device": (
        "بطارية", "رام", "حرارة", "تنظيف", "جهاز", "معالج", "مساحة",
        "battery", "ram", "cpu", "temp", "clean", "device", "storage",
        "memory", "disk",
    ),
    "money": (
        "فلوس", "راتب", "بونص", "خصم", "مرتب", "قبض", "مصاريف",
        "salary", "money", "pay", "bonus", "cash",
    ),
}

# What the suggestion says + what its button does, per category.
# Action "brief" opens the morning brief; "focus" starts a focus
# (Pomodoro) session. Both are real bridge calls.
_CATEGORY_SUGGESTIONS = {
    "study": {
        "label_ar": "المذاكرة",
        "text_ar": "عادةً بتذاكر في الوقت ده — نبدأ جلسة تركيز؟",
        "action": "focus",
        "action_label_ar": "ابدأ جلسة تركيز",
    },
    "delivery": {
        "label_ar": "الشغل",
        "text_ar": "عادةً بتبدأ شغلك في الوقت ده — أجهّز لك نشرة الصباح؟",
        "action": "brief",
        "action_label_ar": "افتح نشرة الصباح",
    },
    "device": {
        "label_ar": "الجهاز",
        "text_ar": "عادةً بتطّمن على جهازك في الوقت ده — أجهّز لك نشرة الصباح؟",
        "action": "brief",
        "action_label_ar": "افتح نشرة الصباح",
    },
    "money": {
        "label_ar": "الفلوس",
        "text_ar": "عادةً بتراجع فلوسك في الوقت ده — أجهّز لك نشرة الصباح؟",
        "action": "brief",
        "action_label_ar": "افتح نشرة الصباح",
    },
    "general": {
        "label_ar": "الكلام مع لونا",
        "text_ar": "عادةً بتفتح لونا في الوقت ده — أجهّز لك نشرة الصباح؟",
        "action": "brief",
        "action_label_ar": "افتح نشرة الصباح",
    },
}


def enabled(settings: dict | None) -> bool:
    """The habits_enabled setting (default ON). A settings dict is
    passed in by the caller; None means "defaults"."""
    if settings is None:
        return True
    return bool(settings.get("habits_enabled", True))


def categorize(text: str) -> str:
    """Message text -> one coarse category. Keyword match only;
    anything unmatched is "general". The text is never stored."""
    haystack = str(text or "").strip().lower()
    if not haystack:
        return "general"
    for category, keywords in _CATEGORY_KEYWORDS.items():
        for word in keywords:
            if word in haystack:
                return category
    return "general"


def _prune(events: list, today=None) -> list:
    """Drop events older than the retention window; cap the list.
    Pure - no I/O."""
    today = today or datetime.now().date()
    cutoff = (today - timedelta(days=_RETENTION_DAYS)).isoformat()
    kept = [e for e in events
            if isinstance(e, dict) and str(e.get("day") or "") >= cutoff
            and isinstance(e.get("hour"), int)]
    return kept[-_MAX_EVENTS:]


def _load() -> list:
    try:
        if _HABITS_PATH.exists():
            data = json.loads(_HABITS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(
                    data.get("events"), list):
                return _prune(data["events"])
    except Exception:
        pass
    return []


def _save(events: list) -> bool:
    try:
        _HABITS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _HABITS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"version": 1, "events": events},
                                  ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(_HABITS_PATH)
        return True
    except Exception:
        return False


def record_event(kind: str, category: str = "general",
                 settings: dict | None = None,
                 now: datetime | None = None) -> dict:
    """Record one usage event: day + hour + kind + coarse category.
    No text is ever passed in or stored. Respects the off switch."""
    if not enabled(settings):
        return {"ok": False, "reason": "disabled"}
    now = now or datetime.now()
    kind = str(kind or "").strip() or "chat"
    category = str(category or "general").strip()
    if category not in _CATEGORY_SUGGESTIONS:
        category = "general"
    with _LOCK:
        events = _load()
        events.append({"day": now.date().isoformat(),
                       "hour": now.hour, "kind": kind,
                       "category": category})
        ok = _save(_prune(events, now.date()))
    if ok:
        info("habits", f"Recorded {kind} event "
                        f"(hour {now.hour}, category {category})")
    return {"ok": bool(ok)}


def record_chat(text: str, settings: dict | None = None,
                now: datetime | None = None) -> dict:
    """Record one chat turn. The text is categorized and dropped -
    only the hour + category are stored. Empty text is ignored."""
    if not str(text or "").strip():
        return {"ok": False, "reason": "empty"}
    return record_event("chat", categorize(text), settings, now)


def record_app_open(settings: dict | None = None,
                    now: datetime | None = None) -> dict:
    """Record one app start (hour only, category "general")."""
    return record_event("open", "general", settings, now)


def get_suggestion(settings: dict | None = None,
                   now: datetime | None = None) -> dict:
    """The morning suggestion, ONLY from recorded data. Looks at
    events from the last 14 days in the hour window around `now`
    (this hour +/- 1). Needs >= 3 events over >= 2 different days
    with one category clearly on top; otherwise {"ok": False,
    "reason": "not_enough_data"} and the UI shows nothing."""
    if not enabled(settings):
        return {"ok": False, "reason": "disabled"}
    now = now or datetime.now()
    today = now.date()
    cutoff = (today - timedelta(days=14)).isoformat()
    hours = {(now.hour - 1) % 24, now.hour, (now.hour + 1) % 24}
    with _LOCK:
        events = _load()
    window = [e for e in events
              if str(e.get("day") or "") >= cutoff
              and e.get("hour") in hours]
    if len(window) < _MIN_EVENTS:
        return {"ok": False, "reason": "not_enough_data"}
    days = {str(e.get("day")) for e in window}
    if len(days) < _MIN_DAYS:
        return {"ok": False, "reason": "not_enough_data"}
    counts: dict = {}
    for event in window:
        cat = str(event.get("category") or "general")
        counts[cat] = counts.get(cat, 0) + 1
    top = max(counts, key=lambda c: counts[c])
    # One category must clearly lead - a tie is not a habit.
    if list(counts.values()).count(counts[top]) > 1:
        return {"ok": False, "reason": "not_enough_data"}
    spec = _CATEGORY_SUGGESTIONS[top]
    return {"ok": True,
            "category": top,
            "category_label_ar": spec["label_ar"],
            "text": spec["text_ar"],
            "action": spec["action"],
            "action_label": spec["action_label_ar"],
            "events": len(window),
            "days": len(days),
            "hour": now.hour}


def clear() -> dict:
    """Wipe the habit store (Settings -> الأوضاع -> مسح سجل
    العادات). The file is deleted; a missing file reads as empty."""
    with _LOCK:
        try:
            if _HABITS_PATH.exists():
                _HABITS_PATH.unlink()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    info("habits", "Habit store cleared by the user")
    return {"ok": True}


def status(settings: dict | None = None) -> dict:
    """Honest status for Settings: enabled, recorded event count,
    distinct days covered. Never returns message text (none is
    stored)."""
    with _LOCK:
        events = _load()
    days = {str(e.get("day")) for e in events}
    return {"ok": True, "enabled": enabled(settings),
            "events": len(events), "days": len(days)}
