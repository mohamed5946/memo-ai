# LONA v1.6.0 - AI self-access: LONA can see itself inside its own program.
#
# get_self_view() returns exactly 8 rows for the island / self panel:
# current mood, current task, last action (diagnostics log tail), own
# process usage (LONA's own RAM/CPU), temp + status, deep files locked
# (yes/no), Chrome extension connected, model status (Groq online /
# offline, key present or not - presence only, never the key value).

import os

from logger import read_recent

try:
    import psutil
except Exception:
    psutil = None


def _own_usage() -> dict:
    """This process's own RAM/CPU via psutil (guarded - {} when psutil
    is missing or the read fails)."""
    if psutil is None:
        return {}
    try:
        proc = psutil.Process(os.getpid())
        mem = proc.memory_info()
        return {"memory_mb": int(mem.rss // (1024 * 1024)),
                "cpu_percent": float(proc.cpu_percent(interval=None)),
                "pid": int(os.getpid())}
    except Exception:
        return {}


def _extension_connected() -> bool:
    """Chrome extension connected? Probed via the native host (owned by
    the extension worker, lazy import). False when absent - never
    guessed True."""
    try:
        import native_host  # owned by the extension worker
        probe = getattr(native_host, "is_connected", None)
        if probe is None:
            return False
        return bool(probe())
    except Exception:
        return False


def _model_status() -> str:
    """Groq online/offline + key presence (presence only, never the key
    value, never the key itself)."""
    try:
        from settings_store import keys_status
        groq_key = bool(keys_status().get("groq"))
    except Exception:
        groq_key = False
    try:
        import brain as brain_mod
        online = bool(brain_mod.is_online())
    except Exception:
        online = False
    if groq_key and online:
        return "Groq متصل ✅ (المفتاح موجود)"
    if groq_key and not online:
        return "Groq: المفتاح موجود بس مفيش نت دلوقتي"
    if not groq_key and online:
        return "Groq: مفيش مفتاح — دخّل مفتاحك من الإعدادات ← مفاتيح API"
    return "Groq مش متصل (مفيش مفتاح ومفيش نت)"


def get_self_view(mood: str = "", monitor=None, settings: dict | None = None,
                  current_task: str = "") -> list:
    """8 rows, each {"label", "value"} (Arabic labels, user-facing).
    `monitor` is a SystemMonitor (or None - the rows then say the value
    is unavailable instead of guessing). `settings` carries the deep
    file grant flag + privacy mode; missing settings default to locked."""
    settings = dict(settings or {})
    mood = (mood or "").strip() or "صاحي"
    status = None
    if monitor is not None:
        try:
            status = monitor.get_status()
        except Exception:
            status = None
    own = _own_usage()
    # Last action: the tail of the diagnostics log (category + message,
    # no secrets - the log never holds key values by design).
    last_action = "مفيش حاجة متسجلة لسه"
    try:
        recent = read_recent(5)
        if recent:
            tail = recent[-1]
            last_action = (f"[{tail.get('category', '')}] "
                           f"{tail.get('message', '')[:120]}")
    except Exception:
        last_action = "تعذّرت قراءة السجل"
    temp_txt = "مش متاحة"
    if status is not None:
        temp_c = status.get("temp_c")
        if temp_c is not None:
            temp_txt = f"{temp_c}°م"
            if float(temp_c) >= 90:
                temp_txt += " — سخنة جداً ⚠️"
            elif float(temp_c) >= 85:
                temp_txt += " — عالية"
            else:
                temp_txt += " — طبيعية"
        elif status.get("temp_available") is False:
            temp_txt = "مش متاحة على الجهاز ده"
    own_txt = "مش متاحة"
    if own:
        own_txt = (f"{own.get('memory_mb', '?')} ميجا رام، "
                   f"{own.get('cpu_percent', '?')}% معالج")
    deep_locked = not bool(settings.get("deep_file_access_granted", False))
    rows = [
        {"label": "المود الحالي", "value": mood},
        {"label": "المهمة الحالية",
         "value": (current_task or "").strip() or "مستنية — مفيش مهمة شغالة"},
        {"label": "آخر حاجة عملتها", "value": last_action},
        {"label": "استهلاك لونا نفسها", "value": own_txt},
        {"label": "حرارة الجهاز", "value": temp_txt},
        {"label": "الملفات العميقة",
         "value": ("مقفولة 🔒 — محتاجة إذنك الأول" if deep_locked
                   else "مفتوحة بإذنك")},
        {"label": "إضافة Chrome",
         "value": ("متصلة ✅" if _extension_connected()
                   else "مش متصلة — افتح Chrome وثبّت الإضافة")},
        {"label": "حالة المخ", "value": _model_status()},
    ]
    return rows


def self_view_summary(mood: str = "", monitor=None,
                      settings: dict | None = None) -> str:
    """One short paragraph for the system prompt: current mood, own
    resource usage, temp. Built from get_self_view so the prompt and
    the panel never disagree."""
    rows = get_self_view(mood=mood, monitor=monitor, settings=settings)
    by_label = {r["label"]: r["value"] for r in rows}
    return ("حالتك دلوقتي (من جوّه البرنامج نفسه): مودك "
            f"{by_label.get('المود الحالي', '')}، استهلاكك "
            f"{by_label.get('استهلاك لونا نفسها', '')}، حرارة الجهاز "
            f"{by_label.get('حرارة الجهاز', '')}، "
            f"المخ: {by_label.get('حالة المخ', '')}.")
