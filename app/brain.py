# Memo AI - Brain: Ollama (local) + Groq (online)
# The model is only the "brain" that plans and writes replies.
# Doing things on the laptop is the Python layer's job ("hands") -
# no model ever touches the laptop by itself.
#
# Routing: online -> Groq gpt-oss-120b (free tier, rate limits only).
#          Groq down -> Gemini 2.5 Flash (CHAT key only, item 23).
#          offline / all online down -> local Ollama qwen2.5:3b.
#          rc.3: a TRIVIAL turn in auto mode (greeting, the time, a
#          short factual ask) tries local Ollama FIRST (15 s cap) -
#          zero cloud tokens; any local failure falls through to
#          the chain above, silently.
#          Groq HTTP 429 -> read the REAL limit from the response
#          (body names it, headers carry retry-after); the failure
#          note names the limit kind.

import functools
import json
import re
import shutil
import socket
import threading
import time
from datetime import datetime

import requests
from logger import error, info, warn
from settings_store import keys_status, read_key

import capabilities
import memory as memory_mod
import tools

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:3b"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"

# v1.6.2 (B10/B11): one total deadline for an ask_with_tools/ask cycle
# (180 s); Deep Think gets 120-180 s per call, normal replies 60 s,
# Ollama 60 s (was 120 s).
_CHAT_DEADLINE_SEC = 180
_DEEP_THINK_TIMEOUT_SEC = 150
_GROQ_TIMEOUT_SEC = 60
_OLLAMA_TIMEOUT_SEC = 60
# rc.3 (local-first): the cap for a local-first attempt on a trivial
# turn. The 3B model is slow on this laptop (no GPU), so a local
# attempt that drags must give way to the cloud quickly - never
# leave the user waiting on a slow local call.
_LOCAL_FIRST_TIMEOUT_SEC = 15
# v1.6.2 (B10): at most 5 tool calls executed per round.
_MAX_TOOL_CALLS_PER_ROUND = 5

# v1.6.4 rc.2 (W8 token efficiency): the Groq 8000 tokens/min limit
# was hit in the field because EVERY turn re-sent the whole history
# (40 messages) plus a device snapshot. The caps below are the fix:
#   * _CHAT_HISTORY_SEND - only the last 10 history messages ride
#     verbatim; older turns ride as a short deterministic summary
#     (see _context_history) - a 30-message chat no longer sends the
#     full history each time.
#   * _MAX_TOKENS_NORMAL / _MAX_TOKENS_DEEP_THINK - per-reply output
#     caps (Groq max_completion_tokens / Gemini maxOutputTokens).
#     Deep Think gets more; a normal reply is short by design.
#   * _GROQ_TPM_LIMIT - the free-tier per-minute token limit for
#     gpt-oss-120b (see _classify_groq_429); the token tracker
#     measures usage against it for the Settings token bar.
_CHAT_HISTORY_SEND = 10
_CHAT_HISTORY_SUMMARY_MAX_CHARS = 1200
_MAX_TOKENS_NORMAL = 600
_MAX_TOKENS_DEEP_THINK = 2000
_GROQ_TPM_LIMIT = 8000
_TOKEN_WINDOW_SEC = 60.0

_history: list = []

# v1.4.2: long-term memory (local file, see memory.py). The store is
# lazy - a missing/corrupt file must never break the brain - and
# replaceable for tests (set_memory_store). A failed load is remembered
# as False so we don't retry (and re-log) on every turn.
_memory_store = None


def get_memory_store():
    global _memory_store
    if _memory_store is None:
        try:
            _memory_store = memory_mod.get_store()
        except Exception:
            _memory_store = False
    return _memory_store or None


def set_memory_store(store) -> None:
    global _memory_store
    _memory_store = store


# v1.6.2 (B17): connectivity is state kept by a background thread,
# never a socket on the caller's thread. is_online() reads the cache
# only; the watcher probes 8.8.8.8:53 every ~10 s. Before the first
# probe lands the cache says offline (honest: unknown, not "online").
# start_online_watcher() is called early at app startup so the cache
# is warm by the time the user chats; is_online() also starts it
# lazily so standalone callers never hang on a first probe.
_ONLINE_CACHE = {"at": 0.0, "value": False}
_ONLINE_LOCK = threading.Lock()
_ONLINE_WATCHER = None
_ONLINE_INTERVAL_SEC = 10.0
_ONLINE_STALE_SEC = 15.0


def _online_probe(timeout: float = 2.0) -> bool:
    """One cheap internet check (DNS socket, no HTTP)."""
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=timeout).close()
        return True
    except OSError:
        return False


def _online_watcher_loop() -> None:
    while True:
        try:
            value = _online_probe()
        except Exception:
            value = False
        with _ONLINE_LOCK:
            _ONLINE_CACHE.update({"at": time.time(), "value": bool(value)})
        time.sleep(_ONLINE_INTERVAL_SEC)


def start_online_watcher() -> bool:
    """Start the background connectivity watcher (daemon, idempotent).
    Returns True when the watcher is running."""
    global _ONLINE_WATCHER
    with _ONLINE_LOCK:
        if _ONLINE_WATCHER is not None and _ONLINE_WATCHER.is_alive():
            return True
        thread = threading.Thread(target=_online_watcher_loop,
                                  daemon=True, name="memo-online-watch")
        thread.start()
        _ONLINE_WATCHER = thread
        return True


def is_online(timeout: float = 2.0) -> bool:
    """Cached connectivity (v1.6.2 B17): reads the background
    watcher's state, never opens a socket on the caller's thread --
    the bridge (Api.get_status) calls this every poll. The timeout
    argument is kept for callers that used to probe directly; a
    stale/empty cache starts the watcher and reports the last known
    value (False before the first probe lands)."""
    start_online_watcher()
    with _ONLINE_LOCK:
        return bool(_ONLINE_CACHE["value"])


def _online_cache_age() -> float:
    with _ONLINE_LOCK:
        return time.time() - float(_ONLINE_CACHE["at"])


def ollama_available(timeout: float = 0.5) -> bool:
    """True when a local Ollama server answers on localhost:11434.
    Cheap local socket, never the network -- used by the model
    picker to say honestly whether "local" can answer right now."""
    try:
        socket.create_connection(("127.0.0.1", 11434),
                                 timeout=timeout).close()
        return True
    except OSError:
        return False


# v1.6.2: model choice (Settings -> model picker). "auto" is the
# Groq -> Gemini -> Ollama chain; anything else is STRICT -- that
# model only, no silent fallback to another model, so the badge
# under the reply always names the engine that really answered.
_MODEL_CHOICES = ("auto", "groq", "gemini", "local")
_MODEL_DISPLAY = {
    "groq": "Groq (gpt-oss-120b)",
    "gemini": "Gemini 2.5 Flash",
    "local": "Ollama المحلي (Qwen 2.5 3B)",
}


def _normalize_model_choice(choice) -> str:
    """Anything unknown (or blank) is "auto" -- the saved value is
    validated again in Api.set_model_choice, this is the backstop."""
    value = str(choice or "").strip().lower()
    return value if value in _MODEL_CHOICES else "auto"


def _explicit_model_failure(model: str, kind: str, lang: str,
                            detail: str = "") -> str:
    """Failure reply for a STRICT model choice: names the model the
    user picked AND the reason it could not answer. No other model
    is tried after this -- the reply IS the answer, so the engine
    badge stays honest (empty = nobody answered)."""
    reasons_ar = {
        "offline": "مفيش نت دلوقتي",
        "no-groq-key": ("مفيش مفتاح Groq محفوظ — دخّله من الإعدادات » "
                        "مفاتيح API"),
        "groq-429": "Groq وصل للحد المسموح — جرّب تاني كمان شوية",
        "groq-429-tpm": "Groq وصل لحد التوكنز في الدقيقة — جرّب كمان دقيقة",
        "groq-429-rpm": "Groq وصل لحد الطلبات في الدقيقة — جرّب كمان دقيقة",
        "groq-429-tpd": ("Groq وصل للحد اليومي للتوكنز — بيتصفّر "
                         "02:00 بتوقيت القاهرة"),
        "groq-429-rpd": ("Groq وصل للحد اليومي للطلبات — بيتصفّر "
                         "02:00 بتوقيت القاهرة"),
        "groq-key-rejected": "مفتاح Groq مرفوض — اتأكد من المفتاح",
        "groq-empty-reply": "Groq رجّع رد فاضي",
        "groq-unavailable": "سيرفرات Groq مش متاحة دلوقتي — جرّب تاني كمان شوية",
        "groq-unreachable": "مش قادر أوصل لـ Groq — اتأكد من النت",
        "groq-bad-request": "Groq رفض الطلب — التفاصيل في السجل",
        "no-gemini-chat-key": ("مفيش مفتاح Gemini للمحادثة محفوظ — دخّله "
                               "من الإعدادات » مفاتيح API"),
        "gemini-429": "Gemini وصل للحد — جرّب كمان دقيقة",
        "gemini-key-rejected": "مفتاح Gemini مرفوض — اتأكد من المفتاح",
        "gemini-failed": "Gemini فشل يرد",
        "gemini-empty-reply": "Gemini رجّع رد فاضي",
        "gemini-api-not-enabled": ("Gemini API مش مفعّلة على مشروع المفتاح — "
                                   "فعّلها من Google Cloud أو استخدم مفتاح "
                                   "جديد من AI Studio"),
        "gemini-model-not-found": "موديل Gemini مش موجود — حدّث البرنامج",
        "gemini-unavailable": "سيرفرات Gemini مش متاحة دلوقتي — جرّب تاني كمان شوية",
        "gemini-unreachable": "مش قادر أوصل لـ Gemini — اتأكد من النت",
        "gemini-bad-request": "Gemini رفض الطلب — التفاصيل في السجل",
        "gemini-blocked": "Gemini رفض يرد على الرسالة دي (فلتر أمان)",
        "ollama_down": "Ollama مش شغال — شغّله الأول",
        "failed": "حصل خطأ وهو بيرد",
    }
    reasons_en = {
        "offline": "there is no internet right now",
        "no-groq-key": ("no Groq key saved — add it in Settings » "
                        "API keys"),
        "groq-429": "Groq hit its limit — try again in a bit",
        "groq-429-tpm": "Groq hit its per-minute token limit — try in a minute",
        "groq-429-rpm": "Groq hit its per-minute request limit — try in a minute",
        "groq-429-tpd": "Groq hit its daily token limit — it resets at 02:00 Cairo time",
        "groq-429-rpd": "Groq hit its daily request limit — it resets at 02:00 Cairo time",
        "groq-key-rejected": "the Groq key was rejected — check the key",
        "groq-empty-reply": "Groq returned an empty reply",
        "groq-unavailable": "Groq's servers are unavailable right now — try again in a bit",
        "groq-unreachable": "cannot reach Groq — check the internet",
        "groq-bad-request": "Groq refused the request — details in the log",
        "no-gemini-chat-key": ("no Gemini chat key saved — add it in "
                               "Settings » API keys"),
        "gemini-429": "Gemini hit its limit — try again in a minute",
        "gemini-key-rejected": "the Gemini key was rejected — check the key",
        "gemini-failed": "Gemini failed to answer",
        "gemini-empty-reply": "Gemini returned an empty reply",
        "gemini-api-not-enabled": ("the Gemini API is not enabled on the key's "
                                   "project — enable it in Google Cloud or use "
                                   "a new AI Studio key"),
        "gemini-model-not-found": "the Gemini model was not found — update the app",
        "gemini-unavailable": "Gemini's servers are unavailable right now — try again in a bit",
        "gemini-unreachable": "cannot reach Gemini — check the internet",
        "gemini-bad-request": "Gemini refused the request — details in the log",
        "gemini-blocked": "Gemini refused to answer this message (safety filter)",
        "ollama_down": "Ollama is not running — start it first",
        "failed": "something went wrong while it was answering",
    }
    kind = str(kind or "failed")
    base_kind = kind if kind in reasons_ar else (
        "groq-429" if kind.startswith("groq-429") else "failed")
    display = _MODEL_DISPLAY.get(model, model)
    if lang == "ar":
        reason = reasons_ar.get(base_kind, reasons_ar["failed"])
        text = (f"اخترت {display} بس مش قادر يرد دلوقتي ({reason}) — "
                "ومش هبدّل لموديل تاني من غير ما تقولي.")
    else:
        reason = reasons_en.get(base_kind, reasons_en["failed"])
        text = (f"You picked {display}, but it cannot answer right now "
                f"({reason}) — I will not switch to another model "
                "without you saying so.")
    if detail:
        text += " " + str(detail).strip()
    return text


def _capability_block(language: str) -> str:
    """Truthful capability state, built from the REAL app state
    (v1.3.2). The brain must never guess what it can do and never
    refuse blindly: for anything not connected/not built, it explains
    WHAT is missing and HOW to connect it, and offers an alternative.
    A flat refusal ("مش هقدر أساعدك") with no reason is forbidden.
    """
    keys = keys_status()
    groq_ok = bool(keys.get("groq"))
    gemini_voice_ok = bool(keys.get("gemini-voice"))
    gemini_chat_ok = bool(keys.get("gemini-chat"))
    if language == "ar":
        lines = [
            "قدراتك الحقيقية (من حالة التطبيق الفعلية):",
            f"- المخ الأونلاين (Groq): {'متصل' if groq_ok else 'مش متصل — مفتاح Groq مش متسجل (الإعدادات » مفاتيح API)'}",
            f"- الصوت الأونلاين (Gemini TTS): {'متصل' if gemini_voice_ok else 'مش متصل — مفتاح Gemini للصوت مش متسجل'}",
            f"- المخ البديل (Gemini 2.5 Flash): {'متصل' if gemini_chat_ok else 'مش متصل — مفتاح Gemini للمحادثة مش متسجل (بيشتغل بديل Groq لما يوصل للحد)'}",
            "- الأدوات على اللابتوب: تفتح برامج، تعمل وتعدّل وتقرأ ملفات، تشغّل أوامر، تبعت واتساب، تدور على النت، تشوف حالة اللابتوب (رام، قرص، حرارة، بطارية)، وتولّد صور (خدمة مجانية من غير مفتاح) — عن طريق أدواتك. كل إجراء بيظهر لصاحبك رسالة سماح/رفض في الشات، فماتسألوش عن الإذن في الكلام — نادي الأداة على طول.",
            "- " + capabilities.whatsapp_status_line("ar"),
            "- " + capabilities.not_built_line("ترجمة الشاشة", "Screen translation", "ar"),
            "- " + capabilities.not_built_line("المجلس متعدد النماذج", "Multi-model council", "ar"),
            "- " + capabilities.not_built_line("تنبيهات الجيميل والتقويم في التحية الصباحية", "Gmail/calendar in the morning briefing", "ar"),
            "قواعد الرفض:",
            "1) ممنوع ترفض رفض جاف من غير سبب ('مش هقدر، آسف مش هقدر أساعدك') — دي جملة ممنوعة.",
            "2) لو مش قادر تعمل حاجة، قول السبب الحقيقي وإيه اللي ناقص وإزاي يتوصل، وقدّم بديل.",
            "3) متكررش نفس جملة الرفض — لو سألك 'ليه؟' اشرح السبب بالتفصيل.",
            "4) متدّعيش قدرة مش موجودة، ومتقولش إن حاجة مربوطة وهي مش مربوطة.",
        ]
        return " ".join(lines)
    lines = [
        "Your real capabilities:",
        f"- Online brain (Groq): {'connected' if groq_ok else 'not connected — no Groq key saved (Settings > API Keys)'}",
        f"- Online voice (Gemini TTS): {'connected' if gemini_voice_ok else 'not connected — no Gemini voice key saved'}",
        f"- Backup brain (Gemini 2.5 Flash): {'connected' if gemini_chat_ok else 'not connected — no Gemini chat key saved (answers instead of Groq at its limit)'}",
        "- Laptop tools: open apps, create/edit/read files, run commands, send WhatsApp, search the web, check the laptop's status (RAM, disk, temperature, battery), generate images (free, no key) — through your tools. Every action shows the user an Allow / Deny message, so do NOT ask for permission in text — call the tool directly.",
        "- " + capabilities.whatsapp_status_line("en"),
        "- " + capabilities.not_built_line("Screen translation", "Screen translation", "en"),
        "- " + capabilities.not_built_line("Multi-model council", "Multi-model council", "en"),
        "- " + capabilities.not_built_line("Gmail/calendar in the morning briefing", "Gmail/calendar in the morning briefing", "en"),
        "Refusal rules:",
        "1) Never refuse flatly with no reason ('I can't, sorry I can't help with that') — that phrasing is forbidden.",
        "2) If you cannot do something, give the real reason, what is missing, how to connect it, and an alternative.",
        "3) Never repeat the same refusal — if asked 'why?', explain in detail.",
        "4) Never claim a capability you do not have, or say something is connected when it is not.",
    ]
    return " ".join(lines)


def _developer_block(language: str) -> str:
    """v1.4.1: who the developer is - PUBLIC info only. The brain must
    answer "who is the developer?" with these facts and NEVER with
    private info (no phone, no student code, no emails, no addresses,
    no keys) - even for the owner. Same facts as the Settings About
    section and the built-in rules.py reply (deterministic path)."""
    if language == "ar":
        return (
            "المطوّر (معلومات عامة بس — ممنوع تماماً تذكر أي معلومات "
            "خاصة عنه: لا تليفون، لا كود طالب، لا إيميلات، لا "
            "عناوين، لا مفاتيح — حتى للمالك نفسه): "
            "محمد السيد (Mohamed El Sayed) — طالب علوم حاسب "
            "(Computer Science)، مطوّر لونا (LONA) (وكمان DeliveryFlow "
            "وتطبيقات تانية). البورتفوليو: "
            "https://mohamed5946.github.io/portfolio/ — GitHub: "
            "https://github.com/mohamed5946")
    return (
        "The developer (public info ONLY - never mention any private "
        "info about him: no phone, no student code, no emails, no "
        "addresses, no keys - even for the owner himself): Mohamed El "
        "Sayed — a Computer Science student who built LONA (and "
        "DeliveryFlow and other apps). "
        "Portfolio: https://mohamed5946.github.io/portfolio/ — "
        "GitHub: https://github.com/mohamed5946")


def system_prompt(ai_name: str, user_name: str, language: str,
                  tools_available: bool = False,
                  owner_unlocked: bool = False,
                  memory_block: str = "",
                  self_view: str = "") -> str:
    """v1.3.2: the user's name is stated EXPLICITLY (the v1.3.1 prompt
    only said 'on {user_name}'s laptop', so the brain answered 'I don't
    know your name'). The capability block is always included, so the
    brain knows what is really connected vs not built.
    v1.4.1: owner_unlocked flips the source-code instruction - with
    owner mode open the user IS the developer, so the model answers
    source-code questions normally instead of refusing with "contact
    the developer" (the v1.4.0 field bug: the unlock confirmed but the
    model still refused, because the prompt never knew owner mode was
    open). Also carries the public-only developer block.
    v1.4.2: memory_block carries the long-term memory for THIS turn
    (retrieved facts + learned style + correction rules + routines +
    rolling summary) - empty when there is nothing to remember yet.
    v1.6.0: LONA speaks warm Egyptian dialect like Selena - calm, soft,
    feminine forms about herself, masculine forms for Mohamed. The
    self_view summary (current mood, own resource usage, temp) is
    injected each turn so LONA knows her own state from inside the app.
    v1.6.4-rc.3: tightened per Anthropic's Fable 5 prompting habits -
    SHORT structured direction, not a rulebook: a WHY line, no-auto-fix
    (assessment first, act only when told), act-once-enough (recommend
    instead of surveying options), evidence-grounded device numbers.
    Token-neutral by construction: the tools block, persona and
    capability block were compressed by MORE than the guardrails added
    (measured with tiktoken cl100k; see test_v164_rc3_prompt)."""
    name = (user_name or "").strip() or "غير معروف"
    if language == "ar":
        if name == "غير معروف":
            name_sentence = ("اسم صاحبك لسه مش متسجل عندك — اسأله مرة واحدة "
                             "'اسمك إيه؟' واستخدمه بعد كده في الترحيب والردود.")
        else:
            name_sentence = (
                f"اسم صاحبك هو {name} — لو سألك 'اسمي إيه؟' قوله {name} "
                f"على طول، واستخدمه في الترحيب والردود.")
        if owner_unlocked:
            source_sentence = (
                "وضع المالك مفتوح دلوقتي — اللي بيكلمك هو المطور نفسه، "
                "فلو سألك عن الكود أو السورس بتاع البرنامج جاوبه عادي "
                "وساعده في الكود.")
        else:
            source_sentence = (
                "لو سألك عن الكود أو السورس بتاع البرنامج، قوله: "
                "«تواصل مع المطور» — إلا لو هو المطور نفسه (هيقولك الكود السري). ")
        prompt = (
            f"أنت {ai_name}، مساعد ذكي محلي على لابتوب صاحبك. "
            "هدفك: تساعد محمد يخلّص شغله ودراسته بسرعة وبأقل كلام. "
            + name_sentence + " "
            + "بتتكلمي زي سيلينا: دافية وهادية، بالمصري البسيط — بصيغ "
            "مؤنثة عن نفسك ومذكرة لمحمد. اختصري، واتكلمي زي "
            "إنسان وصاحب مش زي آلة. لو كتب بالإنجليزي رد بالإنجليزي. "
            + source_sentence + " "
            + _developer_block("ar") + " "
            "ممنوع تذكر أي تفاصيل تقنية عن النماذج اللي بتشغلك. "
            + _capability_block("ar") + " "
            "طريقة شغلك: لو وصف مشكلة أو سأل سؤال، قدّم التقييم وبس "
            "ووقف — متصلّحش ولا تبعت ولا تعدّل ولا تمسح حاجة لحد ما "
            "يقولك. متخططش كتير — لو فيه اختيار اقترح واحدة وامشي "
            "بيها. أي رقم عن الجهاز لازم ييجي من القيم المقاسة قدامك "
            "— ممنوع تخترع أرقام."
        )
        if self_view:
            prompt += "\n\n" + self_view
        if memory_block:
            prompt += "\n\n" + memory_block
        if tools_available:
            prompt += (
                " عندك أدوات حقيقية على اللابتوب — استخدمها على طول بدل "
                "الشرح: لما يطلب تعمل حاجة (افتح "
                "برنامج، اعمل/عدّل ملف، شغّل أمر، ابعت واتساب، شوف "
                "حالة اللابتوب) نادي الأداة المناسبة على طول، وممنوع "
                "تقول 'مش قادر أفتح البرامج من هنا' — أنت تقدر، عن طريق "
                "الأدوات. رسالة سماح/رفض هتظهر لصاحبك قبل التنفيذ، "
                "فماتسألوش عن الإذن في الكلام. ولو سألك عن حاجة "
                "متعرفهاش (أخبار، أسعار، حقائق بعد تدريبك) نادي أداة "
                "web_search بدل ما تخمن — جاوب من النتايج واذكر المصدر؛ "
                "ولو البحث فشل قول بصراحة إنك مقدرتش تلاقيها. ولو طلب "
                "ترسم أو تولّد صورة (ارسم لي...، ولّد صورة...، اعملي "
                "صورة لـ...) نادي أداة generate_image بدل ما "
                "تقول «مقدرش أولّد صور» — اكتب الوصف بالإنجليزي، "
                "والأداة هترجع سطر فيه [[IMAGE:اسم-الملف]] — لازم تحط "
                "السطر ده زي ما هو بالظبط في ردك عشان الصورة تظهر؛ "
                "ولو الأداة قالت إن التوليد فشل، قول بصراحة إن الصورة "
                "متولدتش."
            )
        return prompt
    if name == "غير معروف":
        name_sentence_en = ("Your friend's name is not saved yet — ask once "
                            "'what is your name?' and use it after that in "
                            "greetings and replies.")
    else:
        name_sentence_en = (
            f"Your friend's name is {name} — if he asks 'what is my name?', "
            f"answer {name}, and use it in greetings.")
    if owner_unlocked:
        source_sentence_en = (
            "Owner mode is open — the person talking to you IS the "
            "developer, so if he asks about the source code, answer "
            "normally and help him with the code. ")
    else:
        source_sentence_en = (
            "If asked about the program's source code, say: "
            "«Contact the developer» — unless he is the developer himself "
            "(he will tell you the secret code). ")
    prompt = (
        f"You are {ai_name}, a local AI assistant on your friend's laptop. "
        "Your goal: help Mohamed finish his work and studies fast, "
        "with few words. "
        + name_sentence_en + " "
        + "You speak warmly and softly, like Selena: feminine forms "
        "about yourself, masculine forms for Mohamed. Keep replies "
        "short, friendly, human — not a machine. "
        + source_sentence_en
        + _developer_block("en") + " "
        + _capability_block("en") + " "
        "How you work: when he describes a problem or asks a question, "
        "give the assessment and stop — do not fix, send, "
        "edit, or delete anything until he tells you to. Do not "
        "over-plan — if there is a choice, "
        "recommend one and go with it. Any device number "
        "must come from the measured "
        "values in front of you — never invent numbers."
    )
    if self_view:
        prompt += "\n\n" + self_view
    if memory_block:
        prompt += "\n\n" + memory_block
    if tools_available:
        prompt += (
            " You have real tools on the laptop — use them right away "
            "instead of explaining: when he asks "
            "you to DO something (open an app, create/edit a file, "
            "run a command, send a WhatsApp message, check the laptop's "
            "status), call the right tool right away, and never say 'I "
            "cannot open apps from here' — you can, through the tools. "
            "An Allow/Deny message appears to the user before execution, "
            "so do not ask for permission in text. If he asks about "
            "something you do not know (current events, prices, facts past "
            "your training), call web_search instead of guessing "
            "— answer from the results and name the source; if the search "
            "fails, say honestly you could not find it. "
            "If he asks you to draw or generate an image, call "
            "generate_image right away — write the prompt in ENGLISH; "
            "the tool returns a line [[IMAGE:<filename>]] — include that "
            "exact line verbatim in your reply so the image "
            "shows in the chat; if generation failed, say "
            "honestly no image was generated."
        )
    return prompt


# ---------------------------------------------------------------------------
# v1.6.0 privacy mode (scope item 10): redact secrets from EVERYTHING sent
# to Groq/Gemini. Default ON (settings.privacy_mode). Redaction is
# pattern-based and imperfect by nature - the standing rule stays "don't
# send sensitive screens at all"; this is the second line of defence.
# ---------------------------------------------------------------------------

_REDACT_PATTERNS = [
    # API keys first (longest, most specific).
    (re.compile(r"sk-[A-Za-z0-9_\-]{10,}"), "[مفتاح مخفي]"),
    (re.compile(r"sk-proj-[A-Za-z0-9_\-]{10,}"), "[مفتاح مخفي]"),
    (re.compile(r"gsk_[A-Za-z0-9]{10,}"), "[مفتاح مخفي]"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "[مفتاح مخفي]"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"), "[مفتاح مخفي]"),
    # Password assignments (Arabic + English).
    (re.compile(r"(?i)(password|passwd|pwd|كلمة السر|كلمة المرور)\s*[:=]\s*\S+"),
     r"\1: [مخفي]"),
    # Egyptian mobile numbers: 01xxxxxxxxx.
    (re.compile(r"\b01[0125]\d{8}\b"), "[رقم مخفي]"),
    # Egyptian national ID: 14 digits.
    (re.compile(r"\b\d{14}\b"), "[رقم مخفي]"),
    # Card numbers: 16 digits in 4s (spaces/dashes allowed), or a bare
    # 13-19 digit run.
    (re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"), "[بطاقة مخفية]"),
    (re.compile(r"\b\d{13,19}\b"), "[رقم مخفي]"),
]


def redact(text: str, enabled: bool = True) -> str:
    """Redact passwords, card numbers, Egyptian phones, national IDs
    and API keys from a string. Returns the text unchanged when
    `enabled` is False or the input is not a string. A redacted value
    can never be recovered from the output."""
    if not enabled or not isinstance(text, str) or not text:
        return text
    out = text
    for pattern, replacement in _REDACT_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def _resolve_privacy_mode(privacy_mode: bool | None) -> bool:
    """Explicit value wins; None reads settings.privacy_mode (default
    ON). A failed settings read fails closed (privacy ON)."""
    if privacy_mode is not None:
        return bool(privacy_mode)
    try:
        from settings_store import load_settings
        return bool(load_settings().get("privacy_mode", True))
    except Exception:
        return True


def _apply_privacy(messages: list, privacy_mode: bool) -> list:
    """Redact every message's content before it leaves the device for
    Groq/Gemini. Tool-call arguments are redacted too (a pasted secret
    must not ride along inside a tool call either)."""
    if not privacy_mode:
        return messages
    out = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        new = dict(m)
        if isinstance(new.get("content"), str):
            new["content"] = redact(new["content"], True)
        calls = new.get("tool_calls")
        if calls:
            new_calls = []
            for c in calls:
                if isinstance(c, dict):
                    c2 = dict(c)
                    fn = c2.get("function")
                    if isinstance(fn, dict) and isinstance(
                            fn.get("arguments"), str):
                        fn = dict(fn)
                        fn["arguments"] = redact(fn["arguments"], True)
                        c2["function"] = fn
                    new_calls.append(c2)
                else:
                    new_calls.append(c)
            new["tool_calls"] = new_calls
        out.append(new)
    return out


def _sanitize_messages(messages: list) -> list:
    """Repair a message list before sending it to a model (v1.4.1).
    The v1.4.0 field log proved BOTH Groq and Ollama reject our
    requests with 400 after the first tool call - the history recorded
    raw tool rounds, and hist[-20:] could cut a round in half. Two
    defects, both 400 on Groq AND on Ollama (same broken payload):
      * a message with content=None (JSON null) - Groq and Ollama
        both require content to be a string;
      * an assistant tool_calls message without its tool results in
        the same payload (or a tool result without its call) - Groq
        400s on the unpaired round.
    This keeps well-formed rounds intact and repairs the rest:
    content is always a string (None -> ""), orphan tool messages
    are dropped, and tool_calls with no matching result in this
    payload are stripped from the assistant message (its text, if
    any, is kept). Called at the _groq/_groq_message choke points,
    so EVERY Groq request is validated - including legacy poisoned
    history from a v1.4.0 run."""
    clean = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("system", "user", "assistant", "tool"):
            continue
        content = m.get("content")
        if content is None:
            content = ""
        elif not isinstance(content, str):
            content = str(content)
        new = {"role": role, "content": content}
        if role == "assistant":
            calls = m.get("tool_calls")
            if calls:
                new["tool_calls"] = calls
        if role == "tool":
            tcid = m.get("tool_call_id")
            new["tool_call_id"] = tcid if isinstance(tcid, str) and tcid else ""
        clean.append(new)
    # Pair tool_calls with their results (both directions).
    call_ids = set()
    for m in clean:
        if m["role"] == "assistant":
            for c in m.get("tool_calls") or []:
                if isinstance(c, dict) and c.get("id"):
                    call_ids.add(c["id"])
    result_ids = {m.get("tool_call_id") for m in clean
                  if m["role"] == "tool" and m.get("tool_call_id")}
    out = []
    for m in clean:
        if m["role"] == "assistant":
            if m.get("tool_calls"):
                kept = [c for c in m["tool_calls"]
                        if isinstance(c, dict)
                        and (c.get("function") or {}).get("name")
                        and c.get("id") in result_ids]
                if kept:
                    m["tool_calls"] = kept
                else:
                    # No results for these calls in this payload (history
                    # was cut, or the round never finished) - send the
                    # assistant text alone, or drop it if it has none.
                    m.pop("tool_calls")
            # An assistant message with no text and no (kept) calls is
            # dead weight - drop it (a None content arrived here as "").
            if not m.get("tool_calls") and not m["content"].strip():
                continue
        elif m["role"] == "tool":
            # Orphan result (its call is not in this payload) - drop it.
            if m.get("tool_call_id") not in call_ids:
                continue
        out.append(m)
    return out


def _clean_messages_for_local(messages: list) -> list:
    """Message list for the LOCAL Ollama fallback (v1.4.1): sanitize
    first, then strip ALL tool material. Qwen 2.5 3B is not a
    tool-capable model - Ollama 400s on tool_calls/tool messages for
    it, which is exactly why the v1.4.0 fallback failed with the same
    400 as Groq instead of saving the reply. The local brain only
    needs the plain conversation (system/user/assistant text)."""
    out = []
    for m in _sanitize_messages(messages):
        if m["role"] == "tool":
            continue
        if m["role"] == "assistant":
            m = {"role": "assistant", "content": m["content"]}
            if not m["content"].strip():
                continue
        out.append(m)
    return out


def _ollama(messages: list, timeout: int = _OLLAMA_TIMEOUT_SEC) -> str:
    # v1.4.1: the payload is cleaned first (see above) - the raw
    # tool-round history 400s on Ollama for the 3B model.
    resp = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL,
              "messages": _clean_messages_for_local(messages),
              "stream": False},
        timeout=timeout,
    )
    resp.raise_for_status()
    content = (resp.json().get("message") or {}).get("content")
    # v1.4.1: a null/empty content used to crash .strip() below and
    # surface as the generic "حصلت مشكلة" - name it instead.
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("ollama-empty-reply")
    return content.strip()


class GroqRateLimit(RuntimeError):
    """A Groq HTTP 429 with the limit kind actually read from the
    response (v1.6.1 - the old bare "groq-429" threw the body away,
    so every 429 was reported as the daily limit, which was wrong).

    limit: "tpm" | "rpm" | "tpd" | "rpd" | "unknown".
    retry_after: seconds from the retry-after header (or the body's
    "try again in Xs"), or None when Groq did not say.
    str() stays "groq-429" so old handlers/tests keep matching."""

    def __init__(self, limit: str = "unknown",
                 retry_after: float | None = None):
        self.limit = limit
        self.retry_after = retry_after
        super().__init__("groq-429")


def _classify_groq_429(resp) -> GroqRateLimit:
    """Read a Groq 429 response and say WHICH limit tripped.

    What Groq actually sends (console.groq.com/docs/rate-limits):
    - Body: {"error": {"message": "Rate limit reached for model
      `openai/gpt-oss-120b` in organization `org_...` service tier
      `on_demand` on tokens per minute (TPM): Limit 8000, Used 7600,
      Requested 950. Please try again in 4.1s. ...",
      "type": "tokens" | "requests", "code": "rate_limit_exceeded"}}
      The message names the window: "tokens per minute (TPM)",
      "requests per minute (RPM)", "tokens per day (TPD)",
      "requests per day (RPD)".
    - Headers (retry-after only on 429): retry-after (seconds),
      x-ratelimit-limit-requests (always RPD), x-ratelimit-limit-tokens
      (always TPM), x-ratelimit-remaining-requests (RPD),
      x-ratelimit-remaining-tokens (TPM), x-ratelimit-reset-requests
      (duration like "2m59.56s"), x-ratelimit-reset-tokens ("7.66s").
    Free tier for gpt-oss-120b: 30 RPM, 1000 RPD, 8000 TPM, 200000 TPD.
    When neither body nor headers name the limit, the kind is
    "unknown" - the caller must then NEVER claim it was the daily
    limit."""
    text = ""
    err_type = ""
    try:
        text = str(getattr(resp, "text", "") or "")
    except Exception:
        text = ""
    try:
        err = (resp.json() or {}).get("error") or {}
        err_type = str(err.get("type") or "")
        if err.get("message"):
            text = str(err["message"])
    except Exception:
        pass
    low = text.lower()
    if "tokens per day" in low or "tpd" in low:
        limit = "tpd"
    elif "requests per day" in low or "rpd" in low:
        limit = "rpd"
    elif "tokens per minute" in low or "tpm" in low:
        limit = "tpm"
    elif "requests per minute" in low or "rpm" in low:
        limit = "rpm"
    else:
        limit = "unknown"
    if limit == "unknown":
        # Header fallback: limit-tokens is always TPM and
        # limit-requests is always RPD, so a zero remaining counter
        # names the tripped counter even when the body is empty.
        try:
            headers = getattr(resp, "headers", None) or {}
            if str(headers.get("x-ratelimit-remaining-tokens")) == "0":
                limit = "tpm"
            elif str(headers.get("x-ratelimit-remaining-requests")) == "0":
                limit = "rpd"
        except Exception:
            pass
    retry_after = None
    try:
        headers = getattr(resp, "headers", None) or {}
        raw = headers.get("retry-after")
        if raw is not None:
            retry_after = float(raw)
    except Exception:
        retry_after = None
    if retry_after is None:
        m = re.search(r"try again in\s*([0-9]+(?:\.[0-9]+)?)\s*s", low)
        if m:
            try:
                retry_after = float(m.group(1))
            except Exception:
                retry_after = None
    return GroqRateLimit(limit=limit, retry_after=retry_after)


def _groq_429_info(exc) -> tuple:
    """Map a caught Groq exception to (failure-note kind, retry_after).
    A plain RuntimeError("groq-429") from old code/tests has no limit
    info, so it stays the neutral "groq-429" kind."""
    if isinstance(exc, GroqRateLimit):
        kind = ("groq-429-" + exc.limit
                if exc.limit in ("tpm", "rpm", "tpd", "rpd")
                else "groq-429")
        return kind, exc.retry_after
    return str(exc), None


def _groq_failure_kind(exc) -> str:
    """Map a caught Groq exception to a failure-note kind (v1.6.4
    rc.2). Known RuntimeError kinds ("no-groq-key",
    "groq-empty-reply") are kept verbatim so the reason is not lost;
    an HTTPError is classified by status (401 = rejected key, 5xx =
    Groq down, other 4xx = refused request); a transport failure is
    "groq-unreachable". Anything else stays "failed" - the generic
    note, used only when nothing more specific is known. A 429 never
    reaches here (GroqRateLimit is handled by _groq_429_info)."""
    if isinstance(exc, GroqRateLimit):
        return _groq_429_info(exc)[0]
    kind = str(exc or "")
    if kind in ("no-groq-key", "groq-empty-reply"):
        return kind
    if isinstance(exc, requests.HTTPError):
        status = None
        try:
            status = exc.response.status_code
        except Exception:
            status = None
        if status == 401:
            return "groq-key-rejected"
        if status is not None and status >= 500:
            return "groq-unavailable"
        if status is not None and 400 <= status < 500:
            return "groq-bad-request"
        return "failed"
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return "groq-unreachable"
    return "failed"


def _groq_message(messages: list, use_tools: bool = False,
                  timeout: int = 60, deep_think: bool = False,
                  privacy_mode: bool | None = None) -> dict:
    """One Groq call returning the FULL assistant message (content and/or
    tool_calls). Raises RuntimeError("no-groq-key") / ("groq-429") like _groq.
    v1.6.0: deep_think=True adds "reasoning_effort": "high" (gpt-oss-120b
    is a reasoning model; Groq supports low/medium/high). Default (off)
    sends NO reasoning_effort field - current behaviour unchanged. The
    response's "reasoning" field, if present, is DROPPED - only "content"
    / tool_calls are used. Privacy mode redacts secrets before sending."""
    api_key = read_key("groq-key.txt")
    if not api_key:
        raise RuntimeError("no-groq-key")
    payload = {"model": GROQ_MODEL,
               # v1.4.1: sanitize at the choke point (content None ->
               # "", unpaired tool rounds repaired) - the v1.4.0 log
               # proved Groq 400s on the raw history after a tool call.
               "messages": _apply_privacy(
                   _sanitize_messages(messages),
                   _resolve_privacy_mode(privacy_mode)),
               "temperature": 0.7,
               # rc.2 W8: cap the reply length - a normal reply is
               # short by design; Deep Think gets more room.
               "max_completion_tokens": (
                   _MAX_TOKENS_DEEP_THINK if deep_think
                   else _MAX_TOKENS_NORMAL)}
    if deep_think:
        payload["reasoning_effort"] = "high"
    if use_tools:
        payload["tools"] = tools.TOOL_SCHEMAS
        payload["tool_choice"] = "auto"
    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if resp.status_code == 429:
        # v1.6.1: read WHICH limit from the body/headers - never
        # report "daily" for a per-minute 429.
        raise _classify_groq_429(resp)
    resp.raise_for_status()
    data = resp.json()
    # rc.2 W8: record the reply's token usage (per-reply log +
    # token-bar counters) from the response usage field.
    try:
        _record_engine_usage("groq", (data or {}).get("usage"),
                             getattr(resp, "headers", None))
    except Exception:
        pass
    message = dict((data or {}).get("choices", [{}])[0].get("message")
                   or {})
    message.pop("reasoning", None)  # v1.6.0: reasoning is never surfaced
    return message


def _groq(messages: list, timeout: int = 60, deep_think: bool = False,
          privacy_mode: bool | None = None) -> str:
    api_key = read_key("groq-key.txt")
    if not api_key:
        raise RuntimeError("no-groq-key")
    payload = {"model": GROQ_MODEL,
               "messages": _apply_privacy(
                   _sanitize_messages(messages),
                   _resolve_privacy_mode(privacy_mode)),
               "temperature": 0.7,
               # rc.2 W8: cap the reply length (see _groq_message).
               "max_completion_tokens": (
                   _MAX_TOKENS_DEEP_THINK if deep_think
                   else _MAX_TOKENS_NORMAL)}
    if deep_think:
        payload["reasoning_effort"] = "high"
    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        # v1.4.1: sanitize (see _groq_message) + guard a null content
        # (Groq can return content=None on an empty/filtered reply -
        # the old .strip() crashed it into the generic error).
        json=payload,
        timeout=timeout,
    )
    if resp.status_code == 429:
        # v1.6.1: read WHICH limit from the body/headers - never
        # report "daily" for a per-minute 429.
        raise _classify_groq_429(resp)
    resp.raise_for_status()
    data = resp.json()
    # rc.2 W8: record the reply's token usage (per-reply log +
    # token-bar counters) from the response usage field.
    try:
        _record_engine_usage("groq", (data or {}).get("usage"),
                             getattr(resp, "headers", None))
    except Exception:
        pass
    content = ((data or {}).get("choices", [{}])[0].get("message")
               or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("groq-empty-reply")
    return content.strip()


_ACTION_WORDS = (
    "افتح", "افتحلي", "افتحي", "open", "شغل", "شغّل", "run",
    "اعمل", "انشئ", "انشئلي", "create", "اكتب", "write", "احفظ", "save",
    "عدل", "عدّل", "edit", "اقرا", "اقرأ", "read", "اعرض", "list",
    "امسح", "delete", "نفذ", "execute",
    "ارسم", "ارسملي", "ارسمي", "رسم", "ولد", "ولّد", "صورة", "صوره",
    "draw", "generate", "image", "picture",
)


def _looks_like_action(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in _ACTION_WORDS)


# ---------------------------------------------------------------------------
# v1.6.4 rc.2 (W8 token efficiency)
# ---------------------------------------------------------------------------

def _norm_words(text: str) -> set:
    """Word set of a message: lowercased, Arabic diacritics stripped,
    so "شغّل" reads as "شغل" and "الرَّام" as "الرام". Each word also
    contributes its ال-stripped variant ("المساحة" matches "مساحة",
    "والرام" matches "رام") - whole words only, never substrings."""
    low = (text or "").lower()
    low = re.sub(r"[\u064B-\u0652\u0640]", "", low)
    words = set(re.findall(r"[\u0600-\u06FFa-z0-9]+", low))
    extra = set()
    for w in words:
        stripped = w
        if stripped.startswith("و"):
            stripped = stripped[1:]
        if stripped.startswith("ال") and len(stripped) > 3:
            stripped = stripped[2:]
        if stripped and stripped != w:
            extra.add(stripped)
    return words | extra


# Words that mean "this message is about the laptop itself" - the
# ONLY case where the device snapshot belongs in the prompt (item 2).
# A bare "الشغل" (work) is not "شغل" (play) - whole-word matching,
# never substrings.
_DEVICE_WORDS = {
    "الجهاز", "اللابتوب", "اللاب", "لابتوب", "جهاز",
    "الرام", "رام", "الذاكرة", "ذاكرة",
    "البطارية", "بطارية", "الشاحن",
    "المعالج", "معالج", "البروسيسور", "بروسيسور",
    "الحرارة", "حرارة", "سخن", "سخونة", "سخن؟",
    "القرص", "قرص", "الهارد", "هارد", "التخزين", "مساحة",
    "المروحة", "مروحة", "المراوح", "الفان", "فان",
    "العمليات", "البروسيسات",
    "الاستهلاك", "الأداء", "اداء",
    "laptop", "device", "ram", "memory", "battery", "cpu",
    "processor", "temperature", "temp", "hot", "overheating",
    "disk", "storage", "space", "fan", "process", "processes",
    "usage", "performance",
}


def is_device_question(text: str) -> bool:
    """True when this message asks about the laptop/device itself
    (public: main.py gates the per-turn device snapshot on it)."""
    return bool(_norm_words(text) & _DEVICE_WORDS)


# Words that mean "look something up on the web" - needs the
# web_search tool, so Groq (tools) answers, never the text-only
# simple route.
_WEB_WORDS = {
    "سعر", "أسعار", "الأسعار", "أخبار", "خبر", "دور", "ابحث", "إبحث",
    "الطقس", "طقس", "نتيجة", "نتائج",
    "price", "prices", "news", "search", "weather", "score",
}

# Fragments that mean "this is code / a hard question" - the small
# model must not take it.
_CODE_MARKERS = (
    "```", "def ", "import ", "class ", "function ", "public ",
    "private ", "int main", "console.log", "print(", "=>",
    "select ", "from ", "where ",
)

_SIMPLE_MAX_CHARS = 500


def _is_simple_chat(text: str, deep_think: bool = False) -> bool:
    """True when a turn is simple small-talk / a short plain question
    that needs no tools, no device numbers, no web lookup and no
    reasoning - the kind of turn the higher-limit fallback model can
    answer (item 4). Deep Think, actions, device questions, web
    lookups, code and long messages are all HARD (Groq 120b)."""
    if deep_think:
        return False
    msg = (text or "").strip()
    if not msg or len(msg) > _SIMPLE_MAX_CHARS:
        return False
    if _looks_like_action(msg):
        return False
    if is_device_question(msg):
        return False
    low = msg.lower()
    if any(m in low for m in _CODE_MARKERS):
        return False
    if _norm_words(msg) & _WEB_WORDS:
        return False
    return True


# rc.3 (local-first): the trivial subset of simple chat that the
# on-device 3B model can answer - greetings, the time, a short
# factual ask. Anything needing an explanation ("اشرح", "ازاي",
# "ليه") stays on cloud: the small model guesses at those.
# Actions, device questions, web lookups, code, Deep Think and
# long messages are already excluded by _is_simple_chat.
_LOCAL_FIRST_MAX_CHARS = 120
_LOCAL_FIRST_NO_WORDS = {
    # Explanation requests - the small model guesses at these.
    # The verb "اشرح" (explain) usually arrives with an attached
    # pronoun ("اشرحلي", "اشرحلنا"), so the common conjugated
    # forms are listed too. "ازايك" (how are you) is deliberately
    # NOT here - it is a greeting, and whole-word matching keeps
    # it local.
    "اشرح", "اشرحلي", "اشرحي", "اشرحلنا", "اشرحها", "اشرحهم",
    "اشرحله", "اشرحلك",
    "شرح", "شرحلي", "شرحها", "شرحه", "شرحي",
    "ازاي", "إزاي", "ليه", "لماذا",
    "why", "explain",
}


def _is_local_first(text: str, deep_think: bool = False) -> bool:
    """True when a turn is trivial enough for the local Ollama model
    first (rc.3): simple chat AND short AND not an explanation
    request. Never True for Deep Think or anything _is_simple_chat
    rejects (actions, device, web, code, long). Whole-word
    matching, so "ازايك" (how are you) is not "ازاي" (how-to)."""
    if deep_think:
        return False
    if not _is_simple_chat(text, deep_think):
        return False
    msg = (text or "").strip()
    if len(msg) > _LOCAL_FIRST_MAX_CHARS:
        return False
    if _norm_words(msg) & _LOCAL_FIRST_NO_WORDS:
        return False
    return True


def _now_line(lang: str) -> str:
    """One honest line with the laptop's current time, appended to
    the system prompt ONLY for a local-first attempt - the small
    model has no clock of its own, so "الساعة كام؟" would otherwise
    be a guess. datetime.now() is the machine's local time (Cairo
    on this laptop); the line says "on the laptop", never a city,
    so it stays true wherever the machine travels."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if lang == "ar":
        return f"الوقت الحالي على جهاز صاحبك: {now}."
    return f"The current time on your friend's laptop: {now}."


def _with_now_line(messages: list, lang: str) -> list:
    """Copy of a message list with the current-time line appended
    to the system message (rc.3 local-first only). The caller's
    list is never mutated - the cloud paths keep their exact
    prompt."""
    if not messages:
        return list(messages)
    head = dict(messages[0])
    head["content"] = (str(head.get("content") or "") + "\n\n"
                       + _now_line(lang))
    return [head] + list(messages[1:])


def _context_history(hist: list, store, language: str) -> tuple:
    """Split stored history into (recent, older_summary) for ONE
    request (item 1). Only the last _CHAT_HISTORY_SEND messages ride
    verbatim; the rest is condensed with the store's deterministic
    summarize_turns (no model call, no tokens) and capped, so a
    30-message chat never re-sends the whole history. The stored
    rolling summary (memory file, from rollover) covers turns already
    dropped from `hist`, so there is no overlap. store None (guests)
    means no summary - just the recent window."""
    hist = list(hist or [])
    recent = hist[-_CHAT_HISTORY_SEND:]
    older = hist[:-_CHAT_HISTORY_SEND]
    if not older or store is None:
        return recent, ""
    try:
        summary = store.summarize_turns(older, language)
    except Exception:
        return recent, ""
    summary = (summary or "").strip()
    if not summary:
        return recent, ""
    if len(summary) > _CHAT_HISTORY_SUMMARY_MAX_CHARS:
        summary = summary[-_CHAT_HISTORY_SUMMARY_MAX_CHARS:]
        nl = summary.find("\n")
        if nl != -1:
            summary = summary[nl + 1:]
    return recent, summary


# -- unchanged-snapshot cache (item 5) --------------------------------------
# The last device snapshot actually SENT in the system prompt (shared
# in-app history only). When the next turn's snapshot is identical,
# it is dropped: the numbers are still visible in the previous
# assistant reply inside the sent window, so resending them burns
# tokens for nothing. In-process only; hash() is fine here.
_LAST_DEVICE_VIEW = {"hash": None}


def _dedup_self_view(self_view: str, shared_history: bool) -> str:
    """Drop a device snapshot identical to the one sent last turn.
    Only for the shared history (history is None) - a WhatsApp
    guest's own history never contained it, so it always gets it.
    Returns the view to send ("" when dropped)."""
    if not self_view or not shared_history:
        return self_view
    h = hash(self_view)
    if _LAST_DEVICE_VIEW["hash"] == h:
        return ""
    _LAST_DEVICE_VIEW["hash"] = h
    return self_view


# -- per-engine token tracking (items 6 + Settings token bar) ---------------
# Accumulated from the engines' own usage fields
# (Groq usage.prompt/completion_tokens, Gemini usageMetadata) plus
# Groq's rate-limit headers when present (the TRUE remaining).
# Pure in-memory accounting for this session; never a probe, never
# a guess - an engine with no calls yet reports has_data False.
_TOKEN_LOCK = threading.Lock()
_TOKEN_USAGE = {
    "groq": {"used": 0, "window_start": 0.0, "calls": 0,
             "remaining": None, "reset_in": None, "header_at": 0.0,
             "used_at_header": 0},
    "gemini": {"used": 0, "window_start": 0.0, "calls": 0,
               "remaining": None, "reset_in": None, "header_at": 0.0,
               "used_at_header": 0},
}


def _reset_token_usage() -> None:
    """Clear the tracked counters (tests only)."""
    with _TOKEN_LOCK:
        for st in _TOKEN_USAGE.values():
            st.update({"used": 0, "window_start": 0.0, "calls": 0,
                       "remaining": None, "reset_in": None,
                       "header_at": 0.0, "used_at_header": 0})
    _reset_turn_usage()


# rc.3 (cost line): cloud tokens spent on THIS turn only, summed
# across every engine round in the turn (a tool loop can call Groq
# several times). Reset at the start of each ask()/ask_with_tools()
# call by the _with_cost_line wrapper; None until an engine reports
# a usage field, so "unknown" never reads as 0.
_TURN_USAGE = {"tokens": None}


def _reset_turn_usage() -> None:
    """Clear this turn's token total (tests + the cost-line wrapper)."""
    _TURN_USAGE["tokens"] = None


def _turn_tokens_for(engine: str):
    """Cloud tokens spent on this turn for the cost line, or 0 for a
    reply that used no cloud model (local Ollama runs on-device;
    memory/browser replies are deterministic), or None when the
    engine answered but reported no usage field - the UI shows
    "—" then, never a made-up number."""
    if engine in ("ollama", "memory", "browser"):
        return 0
    if engine in ("groq", "gemini"):
        return _TURN_USAGE["tokens"]
    return None


def _with_cost_line(fn):
    """Attach the measured cost line to an ask result: tokens_used
    (this turn's cloud tokens, 0 for local/deterministic replies,
    None when unknown) and elapsed_sec (wall time of the whole ask
    call, one decimal). Additive only - the wrapped result dict
    gains two keys, nothing else changes."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        _reset_turn_usage()
        t0 = time.monotonic()
        result = fn(*args, **kwargs)
        if isinstance(result, dict):
            result["elapsed_sec"] = round(time.monotonic() - t0, 1)
            result["tokens_used"] = _turn_tokens_for(
                result.get("engine") or "")
        return result
    return wrapper


def _parse_reset_seconds(raw) -> float | None:
    """Parse a Groq x-ratelimit-reset-tokens value: "7.66s",
    "2m59.56s" or plain seconds. None when unparsable."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    try:
        if s.endswith("ms"):
            return float(s[:-2]) / 1000.0
        if s.endswith("s"):
            s = s[:-1]
            if "m" in s:
                mins, secs = s.split("m", 1)
                return float(mins) * 60.0 + float(secs or 0)
            return float(s)
        return float(s)
    except Exception:
        return None


def _usage_pair(usage) -> tuple:
    """(prompt, completion) ints from a Groq usage dict or a Gemini
    usageMetadata dict; (0, 0) when the engine reported nothing."""
    if not isinstance(usage, dict):
        return 0, 0
    prompt = usage.get("prompt_tokens",
                       usage.get("promptTokenCount", 0))
    completion = usage.get("completion_tokens",
                           usage.get("candidatesTokenCount", 0))
    try:
        prompt = int(prompt or 0)
    except Exception:
        prompt = 0
    try:
        completion = int(completion or 0)
    except Exception:
        completion = 0
    return max(0, prompt), max(0, completion)


def _record_engine_usage(engine: str, usage, headers=None) -> None:
    """Record one engine reply: accumulate its prompt+completion
    tokens into the current 60 s window, keep the latest Groq
    rate-limit headers (true remaining), and log the per-reply
    token count to the diagnostics log (item 6) so the burn is
    visible. Never raises; a reply without a usage field is simply
    not counted."""
    if engine not in _TOKEN_USAGE:
        return
    prompt, completion = _usage_pair(usage)
    total = prompt + completion
    if total <= 0 and not headers:
        return
    if total > 0:
        # rc.3: this turn's cloud-token total for the cost line
        # (sums every engine round in the turn).
        if _TURN_USAGE["tokens"] is None:
            _TURN_USAGE["tokens"] = total
        else:
            _TURN_USAGE["tokens"] += total
    now = time.time()
    try:
        with _TOKEN_LOCK:
            st = _TOKEN_USAGE[engine]
            if now - st["window_start"] >= _TOKEN_WINDOW_SEC:
                st["used"] = 0
                st["window_start"] = now
            st["used"] += total
            st["calls"] += 1
            if headers:
                try:
                    rem = headers.get("x-ratelimit-remaining-tokens")
                    if rem is not None:
                        st["remaining"] = int(float(rem))
                        st["used_at_header"] = st["used"]
                        st["header_at"] = now
                    reset = _parse_reset_seconds(
                        headers.get("x-ratelimit-reset-tokens"))
                    if reset is not None:
                        st["reset_in"] = reset
                        st["header_at"] = now
                except Exception:
                    pass
            used = st["used"]
        if total > 0:
            info("brain", f"{engine} reply tokens: prompt={prompt} "
                          f"completion={completion} total={total} "
                          f"(window used={used})")
    except Exception:
        pass


def get_token_usage() -> dict:
    """Tracked token state for the Settings token bar (rc.2 W8).
    Pure read - never probes or calls an engine. Per engine:
    {"used", "limit", "remaining", "reset_in_seconds", "estimated",
    "has_data"}. used = tokens in the current 60 s window (None
    when the engine has never answered this session); remaining
    prefers the latest Groq rate-limit header (the TRUE remaining,
    aged by the tokens used since it arrived, estimated False) and
    otherwise falls back to limit - used (estimated True). Gemini
    has no per-minute token limit configured in the code, so its
    limit stays None - never invented. No data yet -> has_data
    False (the UI shows "—", never a fake 100%)."""
    now = time.time()
    out = {}
    try:
        with _TOKEN_LOCK:
            snapshot = {name: dict(st)
                        for name, st in _TOKEN_USAGE.items()}
    except Exception:
        snapshot = {}
    for engine, limit in (("groq", _GROQ_TPM_LIMIT),
                          ("gemini", None)):
        st = snapshot.get(engine) or {}
        has_data = bool(st.get("calls", 0) > 0
                        or st.get("header_at", 0) > 0)
        if not has_data:
            out[engine] = {"used": None, "limit": limit,
                           "remaining": None, "reset_in_seconds": None,
                           "estimated": True, "has_data": False}
            continue
        window_start = float(st.get("window_start") or 0)
        window_live = (now - window_start) < _TOKEN_WINDOW_SEC
        used = int(st.get("used") or 0) if window_live else 0
        header_at = float(st.get("header_at") or 0)
        header_fresh = (st.get("remaining") is not None
                        and (now - header_at) < _TOKEN_WINDOW_SEC)
        if header_fresh:
            # True remaining, aged by the tokens spent since the
            # header arrived; the reset countdown ages the same way.
            remaining = max(0, int(st["remaining"])
                            - (used - int(st.get("used_at_header") or 0)))
            reset = st.get("reset_in")
            reset = (max(0.0, float(reset) - (now - header_at))
                     if reset is not None else None)
            estimated = False
        elif limit is not None:
            remaining = max(0, limit - used)
            reset = (max(0.0, _TOKEN_WINDOW_SEC - (now - window_start))
                     if window_live else None)
            estimated = True
        else:
            remaining = None
            reset = None
            estimated = True
        out[engine] = {"used": used, "limit": limit,
                       "remaining": remaining,
                       "reset_in_seconds": (
                           int(round(reset))
                           if reset is not None else None),
                       "estimated": estimated, "has_data": True}
    return out


# v1.6.1 (item 21d): media tools run ONLY on an explicit play/open
# request in THIS turn's message. The tool schemas already say so in
# words, but words do not bind the model - a misfire (or a stale call
# from an old turn) once surfaced a YouTube-play card in the middle
# of an unrelated question ("ازاي وصل للحد"). The gate below enforces
# it in code, before any permission is asked or anything executes.
_MEDIA_TOOLS = ("browser_play_youtube", "browser_open")
_MEDIA_INTENT_WORDS = {"شغل", "افتح", "افتحلي", "افتحي", "play", "open"}


def _media_intent(text: str) -> bool:
    """Does this turn's message explicitly ask to play/open something?
    Diacritics are stripped so "شغّل" reads as "شغل", then whole
    words are matched - "الشغل" (work) is NOT "شغل" (play), and a
    bare "يوتيوب" or a question about the limit is not an intent."""
    low = (text or "").lower()
    low = re.sub(r"[\u064B-\u0652\u0640]", "", low)
    words = set(re.findall(r"[\u0600-\u06FFa-z]+", low))
    return bool(words & _MEDIA_INTENT_WORDS)


def _browser_intent_reply(user_text: str, lang: str, hist: list,
                          memory_enabled: bool):
    """Deterministic voice-browser fast path (v1.6.0): "افتح يوتيوب" /
    "شغّل X من على اليوتيوب" route straight to browser_control without
    a model round-trip. Returns the ask_with_tools result dict, or None
    when the text is not a browser command."""
    try:
        import browser_control as browser_mod
    except Exception:
        return None
    intent = browser_mod.parse_voice_intent(user_text)
    if intent is None:
        return None
    try:
        if intent["action"] == "play_youtube":
            result = browser_mod.play_youtube_first(intent["query"])
            if result.get("ok"):
                reply = (f"بشغّل «{intent['query']}» من يوتيوب"
                         if lang == "ar" else
                         f"Playing «{intent['query']}» from YouTube")
            else:
                reply = (f"فتحت صفحة البحث عن «{intent['query']}» على "
                         "يوتيوب — دوس على أول فيديو عشان يشتغل "
                         "(إضافة المتصفح مش متصلة دلوقتي)."
                         if lang == "ar" else
                         f"Opened the YouTube search for "
                         f"«{intent['query']}» — tap the first video to "
                         f"play it (the browser extension is not "
                         f"connected right now).")
        else:
            browser_mod.open_youtube()
            reply = ("فتحت يوتيوب في المتصفح" if lang == "ar" else
                     "Opened YouTube in the browser")
    except Exception as exc:
        warn("brain", f"Browser intent failed: {exc}")
        reply = ("مقدرتش أفتح المتصفح دلوقتي — شوف السجل للسبب."
                 if lang == "ar" else
                 "Could not open the browser right now - check the log.")
    _record_turn(user_text, reply, "browser", hist,
                 learn=memory_enabled, language=lang)
    return {"reply": reply, "engine": "browser", "tools_used": 1,
            "tools_disabled": False}


@_with_cost_line
def ask_with_tools(user_text: str, ai_name: str, user_name: str,
                   language: str, history: list | None = None,
                   owner_unlocked: bool = False,
                   memory_enabled: bool = True,
                   deep_think: bool = False,
                   privacy_mode: bool | None = None,
                   self_view: str = "",
                   model_choice: str = "auto") -> dict:
    """Brain WITH tools (v1.3.2: this is the NORMAL chat path now, not
    owner-only — v1.3.0 gated tools behind the secret code, so in normal
    chat Groq had no tools at all and refused 'open VS Code' in text.
    The user IS the owner of his own laptop; Allow/Deny still gates
    every single action). Groq with real tool-calling (function calling).
    Runs the agentic loop (max 5 tool rounds): model -> tool call ->
    permission dialog -> execute -> result back to the model -> reply.
    Returns {"reply": str, "engine": "groq"|"ollama"|"",
             "tools_used": int, "tools_disabled": bool,
             "tokens_used": int | None, "elapsed_sec": float}.
    (rc.3: tokens_used/elapsed_sec are attached by the
    _with_cost_line wrapper - see _turn_tokens_for.)
    v1.4.1: engine is "" (empty) when NO engine answered - never
    "none" (that string leaked into the chat UI as literal text).

    NOTE: the local Qwen 3B fallback is CHAT-ONLY on purpose - a 3B model
    is too weak for reliable tool calls (it hallucinates tool names and
    arguments). If Groq is unavailable, tools are disabled with a clear
    note and the chat continues on the local model.

    v1.4.0: `history` lets another channel (WhatsApp owner) keep its own
    history instead of the shared in-app one. None = the shared history
    (existing behaviour, unchanged)."""
    lang = "ar" if any("\u0600" <= ch <= "\u06ff" for ch in user_text) \
        else language
    hist = _history if history is None else history
    # v1.4.2: explicit memory commands are answered locally (no model
    # call, works offline); otherwise the retrieved memory block joins
    # the system prompt for this turn.
    _mem, mem_block, cmd_reply = _memory_for_turn(
        user_text, lang, memory_enabled)
    if cmd_reply is not None:
        _record_turn(user_text, cmd_reply, "memory", hist, learn=False,
                     language=lang)
        return {"reply": cmd_reply, "engine": "memory", "tools_used": 0,
                "tools_disabled": False}
    # v1.6.0: voice browser commands ("افتح يوتيوب" / "شغّل X من على
    # اليوتيوب") run deterministically, no model round-trip.
    browser_reply = _browser_intent_reply(user_text, lang, hist,
                                          memory_enabled)
    if browser_reply is not None:
        return browser_reply
    # rc.2 W8 (items 1+5): an unchanged device snapshot is not
    # re-sent - the numbers are still visible in the previous reply
    # inside the sent window. Shared history only; a WhatsApp
    # guest's own history never contained it, so it always gets it.
    self_view = _dedup_self_view(self_view, history is None)
    # Only the last ~10 history messages ride verbatim (was 40);
    # older turns ride as a short deterministic summary appended to
    # the memory block, so a long chat never re-sends the full
    # history. The stored rolling summary (memory file) covers turns
    # already rolled over, so there is no overlap.
    recent, older_summary = _context_history(hist, _mem, lang)
    if older_summary:
        extra = ("ملخص أقدم من المحادثة دي:\n" + older_summary
                 if lang == "ar" else
                 "Summary of the earlier part of this conversation:\n"
                 + older_summary)
        mem_block = (mem_block + "\n\n" + extra) if mem_block else extra
    system = {"role": "system",
              "content": system_prompt(ai_name, user_name, lang, True,
                                       owner_unlocked=owner_unlocked,
                                       memory_block=mem_block,
                                       self_view=self_view)}
    # `turn` collects ONLY this turn's messages (user + tool calls +
    # tool results) so the shared history never duplicates old entries.
    turn = [{"role": "user", "content": user_text}]
    messages = [system] + recent + turn

    # v1.6.2: STRICT model choice. "auto" falls through to the chain
    # below; an explicit pick answers with that model ONLY -- Gemini
    # and local are text-only (no tools), Deep Think rides along as a
    # normal call there (reasoning_effort is a Groq-only param).
    choice = _normalize_model_choice(model_choice)
    if choice in ("gemini", "local"):
        if choice == "gemini":
            reply, engine, fail_kind = _ask_gemini_chat(
                messages, lang, deep_think=deep_think)
            fail_kind = fail_kind or "gemini-failed"
        else:
            reply, engine = _ask_local(messages, lang)
            fail_kind = "ollama_down"
        if engine:
            if _looks_like_action(user_text):
                reply += ("\n\nالأدوات (فتح برامج، ملفات، أوامر) محتاجة "
                          "Groq أونلاين — دلوقتي شغال على "
                          + ("Gemini" if engine == "gemini"
                             else "المخ المحلي")
                          + " فأقدر أجاوب بس.")
            _record_turn(user_text, reply, engine, hist,
                         learn=memory_enabled, language=lang)
            return {"reply": reply, "engine": engine, "tools_used": 0,
                    "tools_disabled": True}
        fail = _explicit_model_failure(choice, fail_kind, lang,
                                       detail="" if choice == "gemini"
                                       else reply)
        _record_turn(user_text, fail, "", hist,
                     learn=memory_enabled, language=lang)
        return {"reply": fail, "engine": "", "tools_used": 0,
                "tools_disabled": True}

    if not is_online():
        # v1.6.2: a STRICT Groq pick never falls back to local when
        # offline -- the failure names Groq and the reason.
        if choice == "groq":
            fail = _explicit_model_failure("groq", "offline", lang)
            _record_turn(user_text, fail, "", hist,
                         learn=memory_enabled, language=lang)
            return {"reply": fail, "engine": "", "tools_used": 0,
                    "tools_disabled": True}
        info("brain", "Offline - tools disabled, local Qwen 3B (chat only)")
        reply, engine = _ask_local(messages, lang)
        if not engine:
            # v1.4.1: the local brain failed too - say BOTH (no net +
            # local down) instead of a bare local error.
            reply = _groq_failure_note("offline", lang) + " " + reply
        note = ("الأدوات (فتح برامج، ملفات، أوامر) محتاجة Groq أونلاين — "
                "دلوقتي مفيش نت فأقدر أجاوب بس.")
        if _looks_like_action(user_text):
            reply = reply + "\n\n" + note
        _record_turn(user_text, reply, engine, hist,
                     learn=memory_enabled, language=lang)
        return {"reply": reply, "engine": engine, "tools_used": 0,
                "tools_disabled": True}

    tools_used = 0
    # v1.6.2 (B10/B11): ONE total deadline for the whole ask cycle
    # (180 s). Every later call gets the time that is LEFT, so a slow
    # first round cannot stretch the turn past the deadline; Deep Think
    # gets 120-180 s per call, normal replies 60 s, Ollama 60 s.
    _deadline = time.monotonic() + _CHAT_DEADLINE_SEC
    _warned_slow = False

    def _remaining_timeout(base: int) -> int:
        """Timeout for the next call: the base value, capped by the
        time left on the cycle deadline (never below 5 s, so a call
        that starts late still gets a real chance)."""
        remaining = _deadline - time.monotonic()
        return max(5, min(int(base), int(remaining)))

    # v1.6.0: deep_think / privacy_mode are passed only when they
    # differ from the defaults, so the call shape stays identical to
    # v1.5.0 when both are off (older callers/mocks that only accept
    # (messages, use_tools, timeout) keep working).
    _groq_kwargs = {}
    if deep_think:
        _groq_kwargs["deep_think"] = True
    if privacy_mode is not None:
        _groq_kwargs["privacy_mode"] = privacy_mode
    # rc.3 (local-first): in auto mode a TRIVIAL turn (greeting, the
    # time, a short factual ask - never actions, device questions,
    # web lookups, code or Deep Think) tries the on-device Ollama
    # model FIRST: it costs zero Groq/Gemini tokens. Ollama is slow
    # on this laptop and may not be installed at all, so the attempt
    # is capped at 15 s and any failure falls through to the
    # Gemini/Groq chain below, silently - never a dead reply.
    if choice == "auto" and _is_local_first(user_text, deep_think) \
            and ollama_available():
        reply, engine = _ask_local(
            _with_now_line(messages, lang), lang,
            timeout=_LOCAL_FIRST_TIMEOUT_SEC)
        if engine:
            info("brain", "Trivial turn answered locally (Ollama) - "
                          "0 cloud tokens")
            _record_turn(user_text, reply, engine, hist,
                         learn=memory_enabled, language=lang)
            return {"reply": reply, "engine": engine, "tools_used": 0,
                    "tools_disabled": False}
    # rc.2 W8 (item 4): in auto mode a SIMPLE turn goes to Gemini
    # 2.5 Flash first (same routing as ask() - higher token limit,
    # Groq 120b kept for hard questions and tool use). A Gemini
    # failure falls through to the Groq tool loop below.
    if choice == "auto" and _is_simple_chat(user_text, deep_think):
        reply, engine, _simple_kind = _ask_gemini_chat(
            messages, lang,
            timeout=_remaining_timeout(_GROQ_TIMEOUT_SEC))
        if engine:
            info("brain", "Simple chat routed to Gemini 2.5 Flash "
                          "(auto) - Groq 120b kept for hard questions")
            _record_turn(user_text, reply, engine, hist,
                         learn=memory_enabled, language=lang)
            return {"reply": reply, "engine": engine, "tools_used": 0,
                    "tools_disabled": False}
    # rc.2 W8 (item 5): hashes of system_status results already sent
    # in THIS turn - a repeat call with an unchanged snapshot gets a
    # short "no change" note instead of the full numbers again.
    status_seen = {}
    try:
        for _round in range(5):
            # v1.6.2 (B10): the deadline is total -- a cycle that is
            # already past it stops instead of starting another round.
            if time.monotonic() >= _deadline:
                warn("brain", "Chat cycle hit the 180s deadline - "
                              "stopping the tool loop")
                break
            if (not _warned_slow
                    and time.monotonic() > _deadline - _CHAT_DEADLINE_SEC + 60):
                warn("brain", "Chat cycle passed 60s (deadline 180s)")
                _warned_slow = True
            _groq_kwargs["timeout"] = _remaining_timeout(
                _DEEP_THINK_TIMEOUT_SEC if deep_think else _GROQ_TIMEOUT_SEC)
            msg = _groq_message(messages, use_tools=True, **_groq_kwargs)
            calls = msg.get("tool_calls") or []
            # v1.6.2 (B10): at most 5 tool calls per round -- extra
            # calls are dropped (the model may re-ask next round).
            if len(calls) > _MAX_TOOL_CALLS_PER_ROUND:
                warn("brain", f"Tool round asked for {len(calls)} calls - "
                              f"capped at {_MAX_TOOL_CALLS_PER_ROUND}")
                calls = calls[:_MAX_TOOL_CALLS_PER_ROUND]
            if not calls:
                reply = (msg.get("content") or "").strip()
                if not reply:
                    reply = "تمام."
                info("brain", f"Answered via Groq gpt-oss-120b "
                              f"({tools_used} tool calls)")
                # v1.4.1: record ONLY the plain-text turn. The old code
                # did hist.extend(turn) - the raw tool round-trip
                # (assistant content None/"" + tool_calls + tool
                # results) went into the shared history, and the next
                # request carried it to Groq AND Ollama, which BOTH
                # 400d on it - the v1.4.0 field failure ("after a few
                # good replies"). The tool round-trip lives in the
                # working `messages` list only; history stays clean.
                _record_turn(user_text, reply, "groq", hist,
                             learn=memory_enabled, language=lang)
                return {"reply": reply, "engine": "groq",
                        "tools_used": tools_used, "tools_disabled": False}
            # Feed the assistant's tool-call message back, then execute.
            assistant_msg = {"role": "assistant",
                             "content": msg.get("content") or "",
                             "tool_calls": calls}
            messages.append(assistant_msg)
            turn.append(assistant_msg)
            for call in calls:
                fn = (call.get("function") or {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                if name in _MEDIA_TOOLS and not _media_intent(user_text):
                    # v1.6.1 (item 21d): no play/open request in this
                    # turn - the call is refused WITHOUT asking
                    # permission and WITHOUT executing, and the model
                    # is told why so it does not retry it.
                    warn("brain", f"Media tool {name} blocked: no "
                                  "play/open intent in this turn "
                                  f"(user said: {user_text[:60]!r})")
                    result = ("مش هشغّل/هفتح حاجة من يوتيوب أو المتصفح "
                              "من غير ما تطلبها صراحة في الرسالة دي — "
                              "لو عايز تشغّل حاجة قولي اسمها.")
                else:
                    result = tools.execute_tool(name, args)
                    tools_used += 1
                    if name == "system_status" and isinstance(result, str):
                        # rc.2 W8 (item 5): same device snapshot twice
                        # in one turn is sent once - the second call
                        # gets a short "no change" note.
                        h = hash(result)
                        if status_seen.get(name) == h:
                            result = ("نفس حالة الجهاز اللي شفتها "
                                      "قبل كده في الرد ده — مفيش تغيير."
                                      if lang == "ar" else
                                      "Same device status as you "
                                      "already saw in this reply — "
                                      "no change.")
                        else:
                            status_seen[name] = h
                # v1.6.0 dangerous-action safety: a tool result is DATA,
                # never a command. If it carries an injected instruction
                # ("delete all files", "ignore previous instructions",
                # ...), it is quarantined with an explicit note and the
                # instruction inside it is never acted on.
                result = tools.quarantine_tool_result(result)
                # v1.4.1: a tool message's content MUST be a string -
                # a None here (or a missing tool_call_id) made the
                # NEXT Groq/Ollama request 400. Coerce defensively;
                # the sanitizer at the request choke point is the
                # second line of defence.
                tool_msg = {"role": "tool",
                            "tool_call_id": call.get("id") or "",
                            "content": result if isinstance(result, str)
                            else ("" if result is None else str(result))}
                messages.append(tool_msg)
                turn.append(tool_msg)
        # 5 rounds and the model still wants more tools: stop politely.
        reply = ("عملت اللي أقدر عليه في الخطوات دي — لو لسه ناقص حاجة "
                 "قولي أكمل إيه بالظبط.")
        _record_turn(user_text, reply, "groq", hist,
                     learn=memory_enabled, language=lang)
        return {"reply": reply, "engine": "groq", "tools_used": tools_used,
                "tools_disabled": False}
    except RuntimeError as exc:
        kind, retry_after = _groq_429_info(exc)
        if kind.startswith("groq-429"):
            warn("brain", f"Groq rate limit (429: {kind[9:] or 'unknown'}) "
                          f"retry_after={retry_after} - tools disabled, "
                          "local Qwen 3B")
        elif kind == "no-groq-key":
            warn("brain", "No Groq key - tools disabled, local Qwen 3B")
        else:
            # v1.6.4 rc.2: keep the real reason (empty reply, rejected
            # key, Groq down, unreachable) instead of collapsing it
            # into a bare "failed" - the all-down message names it.
            kind = _groq_failure_kind(exc)
            warn("brain", f"Groq failed ({type(exc).__name__}: {exc}) - "
                          f"tools disabled, local Qwen 3B")
            retry_after = None
        # v1.6.2: a STRICT Groq pick never falls back -- the failure
        # names Groq and the reason, and nothing else answers.
        if choice == "groq":
            fail = _explicit_model_failure("groq", kind, lang)
            _record_turn(user_text, fail, "", hist,
                         learn=memory_enabled, language=lang)
            return {"reply": fail, "engine": "", "tools_used": 0,
                    "tools_disabled": True}
        reply, engine = _ask_fallback_chain(messages, lang, kind,
                                            retry_after,
                                            timeout=_remaining_timeout(
                                                _GROQ_TIMEOUT_SEC))
        if _looks_like_action(user_text):
            # v1.6.1: the tools suffix names the engine that actually
            # answered - Gemini (text-only, no tools) or local. When
            # nothing answered, the failure message already says
            # everything, so no suffix.
            if engine == "gemini":
                reply += ("\n\nالأدوات (فتح برامج، ملفات، أوامر) محتاجة "
                          "Groq أونلاين — دلوقتي شغال على Gemini فأقدر "
                          "أجاوب بس.")
            elif engine == "ollama":
                reply += ("\n\nالأدوات (فتح برامج، ملفات، أوامر) محتاجة "
                          "Groq أونلاين — دلوقتي شغال على المخ المحلي "
                          "فأقدر أجاوب بس.")
        _record_turn(user_text, reply, engine, hist,
                     learn=memory_enabled, language=lang)
        return {"reply": reply, "engine": engine, "tools_used": 0,
                "tools_disabled": True}
    except Exception as exc:
        warn("brain", f"Groq tool loop failed ({type(exc).__name__}: {exc}) "
                      f"- falling back to Gemini / local Qwen 3B")
        # v1.6.2: STRICT Groq pick -- no fallback chain.
        # v1.6.4 rc.2: name the real Groq reason in the strict failure
        # too (rejected key / Groq down / unreachable), never a bare
        # "failed".
        kind = _groq_failure_kind(exc)
        if choice == "groq":
            fail = _explicit_model_failure("groq", kind, lang)
            _record_turn(user_text, fail, "", hist,
                         learn=memory_enabled, language=lang)
            return {"reply": fail, "engine": "", "tools_used": 0,
                    "tools_disabled": True}
        reply, engine = _ask_fallback_chain(
            messages, lang, kind,
            timeout=_remaining_timeout(_GROQ_TIMEOUT_SEC))
        if _looks_like_action(user_text) and engine in ("gemini",
                                                        "ollama"):
            reply += ("\n\nالأدوات (فتح برامج، ملفات، أوامر) محتاجة "
                      "Groq أونلاين — دلوقتي شغال على "
                      + ("Gemini" if engine == "gemini" else "المخ المحلي")
                      + " فأقدر أجاوب بس.")
        _record_turn(user_text, reply, engine, hist,
                     learn=memory_enabled, language=lang)
        return {"reply": reply, "engine": engine, "tools_used": 0,
                "tools_disabled": True}


def _memory_for_turn(user_text: str, lang: str,
                     memory_enabled: bool):
    """v1.4.2: explicit memory commands + retrieved memory block for one
    turn. Returns (store_or_None, memory_block, command_reply_or_None).
    A command reply means the turn is answered locally (no model call)
    and works offline. Guests (memory_enabled=False, e.g. WhatsApp
    non-owner senders) never touch the owner's memory."""
    if not memory_enabled:
        return None, "", None
    store = get_memory_store()
    if store is None:
        return None, "", None
    try:
        cmd_reply = store.try_handle_command(user_text, lang)
    except Exception:
        cmd_reply = None
    if cmd_reply is not None:
        return store, "", cmd_reply
    try:
        block = store.relevant_block(user_text, lang)
    except Exception:
        block = ""
    return store, block, None


def _record_turn(user_text: str, reply: str, engine: str,
                 history: list | None = None, learn: bool = True,
                 language: str = "ar") -> None:
    # v1.4.0: `history` (None = the shared _history) - see ask_with_tools.
    # v1.4.1: engine is "" on failure (never "none") - failed turns
    # are not recorded, and only PLAIN text is recorded (see
    # ask_with_tools: the tool round-trip never enters the history).
    # v1.4.2: `learn` gates ALL memory writes for this turn (learning
    # from the turn + history rollover into the rolling summary).
    # WhatsApp guests pass learn=False - their words must never enter
    # the owner's memory or summary.
    hist = _history if history is None else history
    if engine:
        hist.append({"role": "user", "content": user_text})
        hist.append({"role": "assistant", "content": reply})
        if learn:
            try:
                store = get_memory_store()
                if store is not None:
                    store.learn_from_turn(user_text, reply)
                    store.rollover(hist, language)
            except Exception as exc:
                warn("brain", f"Memory learn/rollover failed: {exc}")


@_with_cost_line
def ask(user_text: str, ai_name: str, user_name: str, language: str,
        history: list | None = None, owner_unlocked: bool = False,
        memory_enabled: bool = True, deep_think: bool = False,
        privacy_mode: bool | None = None,
        self_view: str = "", model_choice: str = "auto") -> dict:
    """Ask the brain. Returns {"reply": str, "engine": "groq"|"ollama"|"",
    "tokens_used": int | None, "elapsed_sec": float}.
    (rc.3: tokens_used/elapsed_sec are attached by the
    _with_cost_line wrapper - see _turn_tokens_for.)

    v1.4.0: `history` lets another channel (WhatsApp guests) keep its own
    history instead of the shared in-app one. None = shared (unchanged).
    v1.4.1: `owner_unlocked` (WhatsApp owner phone) flips the
    source-code instruction in the system prompt, same as in-app.
    Engine is "" when no engine answered - never "none".
    v1.4.2: `memory_enabled=False` for WhatsApp guests - their words
    must never enter the owner's memory (see _record_turn)."""
    lang = "ar" if any("\u0600" <= ch <= "\u06ff" for ch in user_text) \
        else language
    hist = _history if history is None else history
    # v1.4.2: memory commands + retrieved memory block (same as tools path).
    _mem, mem_block, cmd_reply = _memory_for_turn(
        user_text, lang, memory_enabled)
    if cmd_reply is not None:
        _record_turn(user_text, cmd_reply, "memory", hist, learn=False,
                     language=lang)
        return {"reply": cmd_reply, "engine": "memory"}
    # rc.2 W8 (items 1+5): same context trimming + snapshot dedup
    # as ask_with_tools - last ~10 messages verbatim, older turns
    # as a short summary, unchanged device snapshot not re-sent.
    self_view = _dedup_self_view(self_view, history is None)
    recent, older_summary = _context_history(hist, _mem, lang)
    if older_summary:
        extra = ("ملخص أقدم من المحادثة دي:\n" + older_summary
                 if lang == "ar" else
                 "Summary of the earlier part of this conversation:\n"
                 + older_summary)
        mem_block = (mem_block + "\n\n" + extra) if mem_block else extra
    system = {"role": "system",
              "content": system_prompt(ai_name, user_name, lang,
                                       owner_unlocked=owner_unlocked,
                                       memory_block=mem_block,
                                       self_view=self_view)}
    messages = [system] + recent + [{"role": "user", "content": user_text}]

    # v1.6.2: STRICT model choice (same contract as ask_with_tools).
    choice = _normalize_model_choice(model_choice)
    if choice in ("gemini", "local"):
        if choice == "gemini":
            reply, engine, fail_kind = _ask_gemini_chat(
                messages, lang, deep_think=deep_think)
            fail_kind = fail_kind or "gemini-failed"
        else:
            reply, engine = _ask_local(messages, lang)
            fail_kind = "ollama_down"
        if not engine:
            reply = _explicit_model_failure(
                choice, fail_kind, lang,
                detail="" if choice == "gemini" else reply)
        _record_turn(user_text, reply, engine, hist,
                     learn=memory_enabled, language=lang)
        return {"reply": reply, "engine": engine}

    online = is_online()
    if choice == "groq" and not online:
        reply = _explicit_model_failure("groq", "offline", lang)
        _record_turn(user_text, reply, "", hist,
                     learn=memory_enabled, language=lang)
        return {"reply": reply, "engine": ""}
    if online:
        # rc.3 (local-first): in auto mode a TRIVIAL turn tries the
        # on-device Ollama model FIRST (15 s cap, zero cloud tokens);
        # any local failure falls through to the Gemini/Groq chain
        # below, silently - never a dead reply. Same routing as
        # ask_with_tools.
        if choice == "auto" and _is_local_first(user_text, deep_think) \
                and ollama_available():
            reply, engine = _ask_local(
                _with_now_line(messages, lang), lang,
                timeout=_LOCAL_FIRST_TIMEOUT_SEC)
            if engine:
                info("brain", "Trivial turn answered locally (Ollama) - "
                              "0 cloud tokens")
                _record_turn(user_text, reply, engine, hist,
                             learn=memory_enabled, language=lang)
                return {"reply": reply, "engine": engine}
        # rc.2 W8 (item 4): in auto mode a SIMPLE turn (small-talk,
        # a short plain question - no tools, no device numbers, no
        # web, no reasoning) goes to Gemini 2.5 Flash first: its
        # free-tier token limit is far above Groq's 8000/min, so
        # everyday chat stops burning the Groq budget. gpt-oss-120b
        # stays the engine for Deep Think, hard questions and
        # anything needing tools. A Gemini failure falls through to
        # the Groq call below (which still has its own fallback).
        if choice == "auto" and _is_simple_chat(user_text, deep_think):
            reply, engine, _simple_kind = _ask_gemini_chat(
                messages, lang, timeout=_GROQ_TIMEOUT_SEC)
            if engine:
                info("brain", "Simple chat routed to Gemini 2.5 Flash "
                              "(auto) - Groq 120b kept for hard questions")
                _record_turn(user_text, reply, engine, hist,
                             learn=memory_enabled, language=lang)
                return {"reply": reply, "engine": engine}
        try:
            # v1.6.0: same conditional-kwargs rule as ask_with_tools
            # (see above) - the default call shape is unchanged.
            # v1.6.2 (B10/B11): the single call gets the cycle timeout
            # directly -- 150 s for Deep Think, 60 s for a normal reply.
            _groq_kwargs = {"timeout": (_DEEP_THINK_TIMEOUT_SEC
                                        if deep_think else _GROQ_TIMEOUT_SEC)}
            if deep_think:
                _groq_kwargs["deep_think"] = True
            if privacy_mode is not None:
                _groq_kwargs["privacy_mode"] = privacy_mode
            reply = _groq(messages, **_groq_kwargs)
            info("brain", "Answered via Groq gpt-oss-120b")
            engine = "groq"
        except RuntimeError as exc:
            kind, retry_after = _groq_429_info(exc)
            if kind.startswith("groq-429"):
                warn("brain", f"Groq rate limit (429: {kind[9:] or 'unknown'}) "
                              f"retry_after={retry_after} - falling back "
                              "to Gemini / local Qwen 3B")
            elif kind == "no-groq-key":
                warn("brain", "No Groq key in keys/groq-key.txt - using Gemini / local Qwen 3B")
            else:
                # v1.6.4 rc.2: keep the real reason (empty reply,
                # rejected key, Groq down, unreachable) instead of
                # collapsing it into a bare "failed".
                kind = _groq_failure_kind(exc)
                warn("brain", f"Groq failed ({type(exc).__name__}: {exc}) - falling back to Gemini / local Qwen 3B")
                retry_after = None
            # v1.6.2: STRICT Groq pick -- no fallback chain.
            if choice == "groq":
                reply = _explicit_model_failure("groq", kind, lang)
                engine = ""
            else:
                reply, engine = _ask_fallback_chain(messages, lang, kind,
                                                    retry_after)
        except Exception as exc:
            warn("brain", f"Groq request failed ({type(exc).__name__}: {exc}) - falling back to Gemini / local Qwen 3B")
            # v1.6.2: STRICT Groq pick -- no fallback chain.
            # v1.6.4 rc.2: name the real Groq reason in the strict
            # failure too, never a bare "failed".
            kind = _groq_failure_kind(exc)
            if choice == "groq":
                reply = _explicit_model_failure("groq", kind, lang)
                engine = ""
            else:
                reply, engine = _ask_fallback_chain(messages, lang, kind)
    else:
        info("brain", "Offline - using local Qwen 3B")
        reply, engine = _ask_local(messages, lang)
        if not engine:
            reply = _groq_failure_note("offline", lang) + " " + reply

    _record_turn(user_text, reply, engine, hist,
                 learn=memory_enabled, language=lang)
    return {"reply": reply, "engine": engine}


def _groq_failure_note(kind: str, lang: str,
                       retry_after: float | None = None) -> str:
    """Short honest clause for a Groq failure, prefixed to the local
    fallback's own message when the fallback ALSO fails (v1.4.1) -
    so the user hears BOTH reasons, not just the local one.
    v1.6.1: the 429 kind is the limit actually read from Groq's
    response - per-minute limits say "try again in a minute", daily
    limits say the counter resets 02:00 Cairo time (00:00 UTC), and
    an unclassified 429 NEVER claims "daily" (it says the request
    was refused temporarily)."""
    wait_ar = "دقيقتين" if (retry_after or 0) > 90 else "دقيقة"
    wait_en = "two minutes" if (retry_after or 0) > 90 else "a minute"
    notes = {
        "groq-429-tpm": (
            "Groq وصل لحد التوكنز في الدقيقة "
            f"(8000 توكن/دقيقة)، جرّب كمان {wait_ar}،",
            "Groq hit the per-minute token limit "
            f"(8000 tokens/min), try again in {wait_en},"),
        "groq-429-rpm": (
            "Groq وصل لحد الطلبات في الدقيقة "
            f"(30 طلب/دقيقة)، جرّب كمان {wait_ar}،",
            "Groq hit the per-minute request limit "
            f"(30 requests/min), try again in {wait_en},"),
        "groq-429-tpd": (
            "Groq وصل للحد اليومي للتوكنز "
            "(200 ألف توكن/يوم)، بيتصفّر 02:00 بتوقيت القاهرة،",
            "Groq hit the daily token limit "
            "(200K tokens/day), it resets at 02:00 Cairo time,"),
        "groq-429-rpd": (
            "Groq وصل للحد اليومي للطلبات "
            "(1000 طلب/يوم)، بيتصفّر 02:00 بتوقيت القاهرة،",
            "Groq hit the daily request limit "
            "(1000 requests/day), it resets at 02:00 Cairo time,"),
        "groq-429": ("Groq رفض الطلب مؤقتًا — جرّب كمان دقيقة،",
                     "Groq temporarily refused the request — "
                     "try again in a minute,"),
        "groq-key-rejected": ("مفتاح Groq مرفوض — اتأكد من المفتاح،",
                              "The Groq key was rejected — check the key,"),
        "groq-empty-reply": ("Groq رجّع رد فاضي،",
                             "Groq returned an empty reply,"),
        "groq-unavailable": ("سيرفرات Groq مش متاحة دلوقتي — جرّب تاني "
                             "كمان شوية،",
                             "Groq's servers are unavailable right now — "
                             "try again in a bit,"),
        "groq-unreachable": ("مش قادر أوصل لـ Groq — اتأكد من النت،",
                             "Cannot reach Groq — check the internet,"),
        "groq-bad-request": ("Groq رفض الطلب — التفاصيل في السجل،",
                             "Groq refused the request — details in the log,"),
        "no-groq-key": ("مفيش مفتاح Groq،",
                        "There's no Groq key,"),
        "offline": ("مفيش نت،",
                    "No internet,"),
        "failed": ("Groq فشل،",
                   "Groq failed,"),
    }
    ar, en = notes.get(kind, notes["failed"])
    return ar if lang == "ar" else en


# ---------------------------------------------------------------------------
# Gemini 2.5 Flash chat fallback (v1.6.1, item 23)
# v1.6.4 rc.2: EVERY non-2xx is classified from the error body (see
# _classify_gemini_http_error) - the field failure was a bare "Gemini
# فشل" because Google answers an invalid key with HTTP 400, not
# 401/403, and the old code let that 400 fall through
# raise_for_status() into the generic kind.
# ---------------------------------------------------------------------------

GEMINI_CHAT_MODEL = "gemini-2.5-flash"
_GEMINI_CHAT_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{GEMINI_CHAT_MODEL}:generateContent")


def _messages_for_gemini(messages: list) -> tuple:
    """Convert our OpenAI-style messages to a Gemini generateContent
    payload. Returns (system_text, contents). The system message
    becomes systemInstruction; user/assistant become user/model
    contents; tool messages are DROPPED (the Gemini fallback is a
    text-only reply - it never executes tools, and tool payloads
    would 400). Consecutive same-role turns are merged, because
    Gemini requires alternating user/model turns, and a leading
    model turn is dropped (contents must start with user)."""
    system_parts = []
    contents = []
    for m in _sanitize_messages(messages):
        role = m.get("role")
        content = str(m.get("content") or "").strip()
        if role == "system":
            if content:
                system_parts.append(content)
            continue
        if role == "tool":
            continue
        if role == "assistant":
            gemini_role = "model"
        elif role == "user":
            gemini_role = "user"
        else:
            continue
        if not content:
            continue
        if contents and contents[-1]["role"] == gemini_role:
            contents[-1]["parts"][0]["text"] += "\n" + content
        else:
            contents.append({"role": gemini_role,
                             "parts": [{"text": content}]})
    while contents and contents[0]["role"] != "user":
        contents.pop(0)
    return "\n".join(system_parts), contents


def _classify_gemini_http_error(resp) -> RuntimeError:
    """Map a non-2xx Gemini response to a RuntimeError whose message
    IS the failure kind (v1.6.4 rc.2 - the field failure).

    What Google actually sends for generateContent errors:
    {"error": {"code": 400, "message": "API key not valid. Please
    pass a valid API key.", "status": "INVALID_ARGUMENT", "details":
    [{"@type": ".../google.rpc.ErrorInfo", "reason":
    "API_KEY_INVALID", "domain": "googleapis.com", ...}]}}.
    An invalid key is HTTP 400, NOT 401/403 - the old code only
    treated 401/403 as key-rejected, so every 400 (bad key, API not
    enabled on the key's project, malformed request) fell through
    raise_for_status() into the bare generic "gemini-failed", and the
    user saw "Gemini فشل" with no reason while the key was the
    actual problem. The body is read here, so the kind names the
    real cause; the Google message is logged (it never contains the
    key) and never shown to the user."""
    status = 0
    err = {}
    try:
        status = int(getattr(resp, "status_code", 0) or 0)
    except Exception:
        status = 0
    try:
        err = (resp.json() or {}).get("error") or {}
    except Exception:
        err = {}
    err_status = str(err.get("status") or "")
    message = str(err.get("message") or "")
    reason = ""
    try:
        for detail in err.get("details") or []:
            if isinstance(detail, dict) and detail.get("reason"):
                reason = str(detail["reason"])
                break
    except Exception:
        reason = ""
    low_msg = message.lower()
    if status == 429 or err_status == "RESOURCE_EXHAUSTED":
        kind = "gemini-429"
    elif (status in (401, 403)
          or reason in ("API_KEY_INVALID", "API_KEY_EXPIRED")
          or "api key not valid" in low_msg):
        kind = "gemini-key-rejected"
    elif (err_status == "FAILED_PRECONDITION"
          or reason in ("SERVICE_DISABLED", "BILLING_DISABLED")
          or "has not been used" in low_msg
          or "api not enabled" in low_msg
          or "free tier is not available" in low_msg):
        kind = "gemini-api-not-enabled"
    elif status == 404 or err_status == "NOT_FOUND":
        kind = "gemini-model-not-found"
    elif status >= 500 or err_status in ("UNAVAILABLE", "INTERNAL"):
        kind = "gemini-unavailable"
    elif status == 400:
        kind = "gemini-bad-request"
    else:
        kind = "gemini-failed"
    warn("brain", f"Gemini chat HTTP {status} "
                  f"({err_status or '-'}"
                  f"{'/' + reason if reason else ''})"
                  + (f": {message[:200]}" if message else ""))
    return RuntimeError(kind)


def _gemini_chat(messages: list, timeout: int = 60,
                 deep_think: bool = False) -> str:
    """One Gemini 2.5 Flash call (chat fallback). Uses the CHAT key
    ONLY - the voice key is never sent here. Raises
    RuntimeError("no-gemini-chat-key") / ("gemini-429") /
    ("gemini-key-rejected") / ("gemini-api-not-enabled") /
    ("gemini-model-not-found") / ("gemini-unavailable") /
    ("gemini-bad-request") / ("gemini-blocked") /
    ("gemini-empty-reply"); a transport failure (no route to Google)
    surfaces as requests.ConnectionError/Timeout and is classified
    by _ask_gemini_chat as "gemini-unreachable".
    v1.6.4 rc.2: every non-2xx status is classified from the error
    body (see _classify_gemini_http_error) - a 400 API_KEY_INVALID
    used to fall through raise_for_status() into the bare
    "gemini-failed"."""
    api_key = read_key("gemini-chat-key.txt", quiet=True)
    if not api_key:
        raise RuntimeError("no-gemini-chat-key")
    system_text, contents = _messages_for_gemini(messages)
    payload = {"contents": contents,
               # rc.2 W8: cap the reply length (see _groq_message).
               "generationConfig": {
                   "maxOutputTokens": (
                       _MAX_TOKENS_DEEP_THINK if deep_think
                       else _MAX_TOKENS_NORMAL)}}
    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}
    resp = requests.post(
        _GEMINI_CHAT_URL,
        headers={"x-goog-api-key": api_key,
                 "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if resp.status_code != 200:
        # v1.6.4 rc.2: classify from the body - never a bare "failed".
        raise _classify_gemini_http_error(resp)
    try:
        data = resp.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    # rc.2 W8: record the reply's token usage (per-reply log +
    # token-bar counters) from usageMetadata.
    try:
        _record_engine_usage("gemini", data.get("usageMetadata"),
                             getattr(resp, "headers", None))
    except Exception:
        pass
    # The block reason is read OUTSIDE the parts parse: a blocked
    # reply has no candidates at all, so candidates[0] raises and
    # must not wipe the reason we need to report.
    block_reason = ((data.get("promptFeedback") or {}).get("blockReason")
                    or "")
    try:
        candidates = data.get("candidates") or []
        first = candidates[0] if candidates else {}
        parts = (first.get("content") or {}).get("parts") or []
        text = "".join(str(p.get("text") or "") for p in parts
                       if isinstance(p, dict)).strip()
    except Exception:
        text = ""
    if not text:
        if block_reason:
            # v1.6.4 rc.2: a 200 with no candidates and a blockReason
            # is a safety refusal, not an empty reply - name it.
            warn("brain", f"Gemini chat blocked ({block_reason})")
            raise RuntimeError("gemini-blocked")
        raise RuntimeError("gemini-empty-reply")
    return text


def _ask_gemini_chat(messages: list, lang: str = "ar",
                     timeout: int = 60, deep_think: bool = False) -> tuple:
    """Gemini chat fallback. Returns (reply, engine, fail_kind) with
    engine "gemini" on success and "" on failure (fail_kind names
    the reason - see _gemini_failure_note). Never raises."""
    try:
        reply = _gemini_chat(messages, timeout=timeout,
                             deep_think=deep_think)
        info("brain", "Answered via Gemini 2.5 Flash (chat key)")
        return reply, "gemini", ""
    except RuntimeError as exc:
        kind = str(exc)
        if kind == "gemini-429":
            warn("brain", "Gemini chat rate limit (429) - falling back "
                          "to local Qwen 3B")
        elif kind == "no-gemini-chat-key":
            info("brain", "No Gemini chat key - skipping Gemini, "
                          "local Qwen 3B next")
        else:
            warn("brain", f"Gemini chat failed ({kind}) - falling back "
                          "to local Qwen 3B")
        return "", "", kind
    except (requests.ConnectionError, requests.Timeout) as exc:
        # v1.6.4 rc.2: no route to Google at all - distinct from a
        # refused key or a server error, and worth naming.
        warn("brain", f"Gemini chat unreachable "
                      f"({type(exc).__name__}: {exc}) - falling back "
                      "to local Qwen 3B")
        return "", "", "gemini-unreachable"
    except Exception as exc:
        warn("brain", f"Gemini chat failed ({type(exc).__name__}: {exc}) "
                      "- falling back to local Qwen 3B")
        return "", "", "gemini-failed"


def _gemini_failure_note(kind: str, lang: str) -> str:
    """Short honest clause for a Gemini chat failure, used when the
    whole chain is down (item 23) - the user hears WHY Gemini did
    not answer, next to the Groq and Ollama reasons."""
    notes = {
        "no-gemini-chat-key": (
            "مفتاح Gemini للمحادثة مش متسجل — دخّله من الإعدادات » "
            "مفاتيح API،",
            "No Gemini chat key saved — add it in Settings > API keys,"),
        "gemini-429": ("Gemini وصل للحد — جرّب كمان دقيقة،",
                       "Gemini hit its limit — try again in a minute,"),
        "gemini-key-rejected": (
            "مفتاح Gemini للمحادثة مرفوض — اتأكد من المفتاح،",
            "The Gemini chat key was rejected — check the key,"),
        "gemini-api-not-enabled": (
            "Gemini API مش مفعّلة على مشروع المفتاح — فعّلها من Google "
            "Cloud أو استخدم مفتاح جديد من AI Studio،",
            "The Gemini API is not enabled on the key's project — "
            "enable it in Google Cloud or use a new AI Studio key,"),
        "gemini-model-not-found": (
            "موديل Gemini مش موجود — حدّث البرنامج،",
            "The Gemini model was not found — update the app,"),
        "gemini-unavailable": (
            "سيرفرات Gemini مش متاحة دلوقتي — جرّب تاني كمان شوية،",
            "Gemini's servers are unavailable right now — "
            "try again in a bit,"),
        "gemini-unreachable": (
            "مش قادر أوصل لـ Gemini — اتأكد من النت،",
            "Cannot reach Gemini — check the internet,"),
        "gemini-bad-request": (
            "Gemini رفض الطلب — التفاصيل في السجل،",
            "Gemini refused the request — details in the log,"),
        "gemini-blocked": (
            "Gemini رفض يرد على الرسالة دي (فلتر أمان)،",
            "Gemini refused to answer this message (safety filter),"),
        "gemini-failed": ("Gemini فشل،",
                          "Gemini failed,"),
        "gemini-empty-reply": ("Gemini رجّع رد فاضي،",
                               "Gemini returned an empty reply,"),
    }
    ar, en = notes.get(kind, notes["gemini-failed"])
    return ar if lang == "ar" else en


def _ask_fallback_chain(messages: list, lang: str, groq_kind: str,
                        groq_retry_after: float | None = None,
                        timeout: int = 60) -> tuple:
    """The chat fallback chain after Groq fails (v1.6.1, item 23):
    Gemini 2.5 Flash (CHAT key only) -> local Ollama. Each engine
    gets its own timeout (v1.6.4 rc.2: the caller in ask_with_tools
    caps it by the time left on the 180 s cycle deadline, so the
    chain cannot blow the deadline). Returns (reply, engine). When
    EVERYTHING is down the reply names each engine and why: Groq
    (limit kind + when it clears, item 22), Gemini (the reason),
    Ollama (not installed / not running).
    No chat key -> the chain is Groq -> Ollama exactly as before."""
    reply, engine, gemini_kind = _ask_gemini_chat(messages, lang,
                                                  timeout=timeout)
    if engine:
        return reply, engine
    reply, engine = _ask_local(messages, lang, timeout=timeout)
    if engine:
        return reply, engine
    groq_note = _groq_failure_note(groq_kind, lang, groq_retry_after)
    gemini_note = _gemini_failure_note(gemini_kind, lang)
    return f"{groq_note} {gemini_note} {reply}", ""


def _ask_local(messages: list, lang: str = "ar",
               timeout: int = _OLLAMA_TIMEOUT_SEC) -> tuple:
    """Local Ollama fallback. Returns (reply, engine) with engine
    "ollama" on success and "" (empty - NEVER "none") on failure.
    v1.4.1: the failure reply names the REAL local reason (not a bare
    generic message), and the log records the exact exception TYPE +
    message every time - the v1.4.0 log showed only the generic text
    while the real cause (400 on a poisoned payload) stayed hidden."""
    try:
        reply = _ollama(messages, timeout=timeout)
        info("brain", "Answered via local Ollama qwen2.5:3b")
        # rc.3: a local reply costs 0 cloud tokens - say so in the
        # diagnostics log too, so the per-reply token count stays
        # truthful (the token bar only tracks Groq/Gemini, and a
        # local turn must never inflate their counters).
        info("brain", "ollama reply tokens: prompt=0 completion=0 "
                      "total=0 (local - no cloud tokens used)")
        return reply, "ollama"
    except requests.Timeout as exc:
        error("brain", f"Ollama request timed out "
                       f"({type(exc).__name__}: {exc})")
        if lang == "ar":
            return ("المخ المحلي بياخد وقت طويل — جرّب تاني "
                    "(Ollama شغال بس الرد اتأخر).", "")
        return ("The local brain is taking too long — try again "
                "(Ollama is running but the reply timed out).", "")
    except requests.ConnectionError as exc:
        error("brain", f"Ollama is not running on http://localhost:11434 "
                       f"({type(exc).__name__}: {exc}) "
                       f"- start it with: ollama serve")
        # v1.6.1: a refused connection cannot tell "not installed"
        # from "not running" - check the binary. On Mohamed's machine
        # Ollama was UNINSTALLED (2026-11-12), so telling him to run
        # "ollama serve" would be a dead instruction; say plainly
        # which state it is in instead.
        installed = shutil.which("ollama") is not None
        if lang == "ar":
            if installed:
                return ("المخ المحلي مش شغال — Ollama متثبت بس مش شغال "
                        "دلوقتي. شغّله (افتح PowerShell واكتب: "
                        "ollama serve)", "")
            return ("المخ المحلي مش متثبت — Ollama مش موجود على الجهاز، "
                    "فمفيش مخ محلي يرد. نزّله من ollama.com لو عايز "
                    "مخ محلي.", "")
        if installed:
            return ("The local brain isn't running — Ollama is installed "
                    "but not running right now. Start it (open PowerShell "
                    "and type: ollama serve)", "")
        return ("The local brain isn't installed — Ollama is not on this "
                "machine, so there is no local brain to answer. Download "
                "it from ollama.com if you want a local brain.", "")
    except requests.HTTPError as exc:
        error("brain", f"Ollama refused the request "
                       f"({type(exc).__name__}: {exc})")
        if lang == "ar":
            return ("المخ المحلي رفض الطلب — جرّب تاني "
                    "(التفاصيل في السجل).", "")
        return ("The local brain refused the request — try again "
                "(details in the log).", "")
    except RuntimeError as exc:
        # ollama-empty-reply (v1.4.1): the model answered nothing.
        error("brain", f"Ollama empty reply ({type(exc).__name__}: {exc})")
        if lang == "ar":
            return ("المخ المحلي رجّع رد فاضي — جرّب تاني.", "")
        return ("The local brain returned an empty reply — try again.", "")
    except Exception as exc:
        error("brain", f"Ollama request failed "
                       f"({type(exc).__name__}: {exc})")
        if lang == "ar":
            return ("حصلت مشكلة وأنا بفكر — المخ المحلي رد بخطأ "
                    "(التفاصيل في السجل)، جرّب تاني.", "")
        return ("Something went wrong while thinking — the local brain "
                "returned an error (details in the log), try again.", "")


def clear_history() -> None:
    _history.clear()
