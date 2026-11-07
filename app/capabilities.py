# Memo AI - Capability state (v1.3.2).
#
# The brain must NEVER guess what it can do, and NEVER refuse blindly:
# it answers from THIS file's real state. When a feature ships, the
# builder that ships it flips the flag here (and only here) — the
# system prompt is built from these flags, so the brain's answers stay
# truthful without anyone editing prompt text by hand.
#
# States: "ready" (works now) / "building" (being built, not usable yet)
#        / "not_built" (designed, not built yet) / "not_connected"
#        (needs the user to connect something first).

# WhatsApp linking: BUILT in v1.4.0 (branch feat/whatsapp) — Baileys
# sidecar, QR in Settings, linked device on the 3rd number only; main
# number -> owner mode, others -> reply mode. WHATSAPP_LINKED is NOT a
# static flag anymore: whatsapp_status_line() reads the LIVE session
# state from whatsapp.get_state() (lazy import — brain imports this
# module, and whatsapp imports brain, so a top-level import would be a
# cycle). The constants below are only the fallback when the WhatsApp
# module is missing from the build.
WHATSAPP = "ready"
WHATSAPP_LINKED = False

# Screen translation ("ترجم الشاشة"): designed, NOT built yet.
SCREEN_TRANSLATION = "not_built"

# Multi-model council: approved, NOT built yet.
COUNCIL = "not_built"

# Gmail / calendar in the morning briefing: needs a Google account
# connection, NOT built yet.
GMAIL_CALENDAR = "not_built"

# 7B local model: NOT installed (needs the RAM upgrade first).
MODEL_7B = "not_built"

# XTTS voice cloning: needs the RAM upgrade first.
XTTS_VOICE = "not_built"


def _live_whatsapp_state() -> dict | None:
    """Live link state from the WhatsApp module (lazy import: brain ->
    capabilities -> whatsapp would be a cycle at module level, and
    whatsapp imports brain. Returns None if the module is unavailable
    in this build)."""
    try:
        from whatsapp import get_state
        return get_state()
    except Exception:
        return None


def whatsapp_status_line(language: str = "ar") -> str:
    """One truthful line about WhatsApp, for the system prompt.
    v1.4.0: reads the LIVE session state (linked / waiting for QR /
    connecting / off) instead of a static flag, so the brain never
    claims a link that does not exist - and never refuses blindly
    when linking just needs a QR scan."""
    st = _live_whatsapp_state()
    if st is None:
        # WhatsApp module missing from this build: static fallback.
        if WHATSAPP_LINKED:
            return _linked_line(language, None)
        if WHATSAPP == "building":
            return _building_line(language)
        return _not_built_line(language)
    status = st.get("status")
    if status == "linked":
        return _linked_line(language, st.get("linked_phone"))
    if status == "decrypt_failed":
        # v1.4.3: the link is up but incoming messages are NOT
        # decrypting (Bad MAC) — the brain must never claim WhatsApp
        # messages are arriving, and must point at the re-link.
        failures = st.get("decrypt_failures") or 0
        if language == "ar":
            return ("الواتساب مربوط بس رسايله مش بتتفك (مشكلة تشفير) — "
                    f"وصلت {failures} رسايل مش مقروءة ومش بتوصلني، "
                    "فمتقولش إنك استقبلتها؛ الحل إعادة الربط من "
                    "الإعدادات ← واتساب ← إعادة الربط، وبعدها امسح "
                    "الـ QR الجديد من واتساب على الرقم التالت.")
        return (f"WhatsApp is linked but its messages are NOT decrypting "
                f"(encryption failure) — {failures} messages arrived "
                "unreadable and never reached me, so never claim I "
                "received them; the fix is a clean re-link from "
                "Settings > WhatsApp > Re-link, then scan the new QR "
                "from WhatsApp on the third number.")
    if status == "waiting_qr":
        if language == "ar":
            return ("الواتساب مستني مسح الـ QR — الكود ظاهر في الإعدادات، "
                    "امسحه من واتساب على الرقم التالت (مش الأساسي) وهيتربط.")
        return ("WhatsApp is waiting for the QR scan — the code is shown "
                "in Settings; scan it from WhatsApp on the third number "
                "(not the main one) and it will link.")
    if status in ("connecting", "disconnected"):
        if language == "ar":
            return ("الواتساب بيحاول يتصل دلوقتي — لو فضل كده شوف السجل، "
                    "ولو اتقطع هيرجع لوحده من غير QR جديد.")
        return ("WhatsApp is (re)connecting right now — if it stays like "
                "that, check the log; a dropped connection comes back on "
                "its own without a new QR.")
    if status == "error":
        if language == "ar":
            return ("الواتساب فيه مشكلة — شوف السجل للسبب الدقيق، "
                    "وغالباً الحل إعادة تشغيل الربط من الإعدادات.")
        return ("WhatsApp has a problem — check the log for the exact "
                "reason; usually restarting the link from Settings fixes it.")
    # off / disabled: not linked yet, needs enabling + a QR scan.
    if language == "ar":
        return ("الواتساب مش مربوط لسه — فعّله من الإعدادات وامسح الـ QR "
                "من واتساب على الرقم التالت (مش الأساسي ومش رقم الشغل)، "
                "وبعدها أقدر أستقبل رسايل واتساب وأرد عليها؛ رسايل الرقم "
                "الأساسي أوامر للابتوب، وأي رقم تاني ردود شات بس.")
    return ("WhatsApp is not linked yet — enable it in Settings and scan "
            "the QR from WhatsApp on the third number (not the main one, "
            "not the work number); then I can receive and reply to "
            "WhatsApp messages. Messages from the main number are laptop "
            "commands; any other number gets chat replies only.")


def _linked_line(language: str, masked_phone: str | None) -> str:
    if language == "ar":
        line = ("الواتساب مربوط ✅ — بتستقبل رسايل واتساب وترد عليها من هنا؛ "
                "رسايل الرقم الأساسي أوامر للابتوب، وأي رقم تاني ردود شات بس.")
        if masked_phone:
            line += f" (الرقم المربوط: {masked_phone})"
        return line
    line = ("WhatsApp is linked ✅ — you can receive and reply to WhatsApp "
            "messages from here; messages from the main number are laptop "
            "commands, any other number gets chat replies only.")
    if masked_phone:
        line += f" (Linked number: {masked_phone})"
    return line


def _building_line(language: str) -> str:
    if language == "ar":
        return ("الواتساب لسه مش مربوط — الربط نفسه بيتبني دلوقتي "
                "(هيتربط برقم تالت بـ QR من واتساب ويب)، فلحد ما يخلص "
                "مقدرش أسجّل دخول بالواتساب ولا أبعت/أستقبل رسايل واتساب. "
                "لو سألك عنه، اشرحله كده وعرّفه إنه هيتربط بـ QR أول ما "
                "النسخة الجديدة تنزل، وقدّم بديل (يكتب هنا في الشات).")
    return ("WhatsApp is not linked yet — linking is being built right "
            "now (it will link with a third number via a WhatsApp Web "
            "QR code), so until then I cannot sign in to WhatsApp or "
            "send/receive WhatsApp messages. If asked, explain that and "
            "offer to chat here instead.")


def _not_built_line(language: str) -> str:
    if language == "ar":
        return ("الواتساب مش مربوط لسه — الربط متصمم بس مش مبني: محتاج "
                "يتربط الأول برقم تالت بـ QR من واتساب ويب، وبعدها أقدر "
                "أستقبل رسايل واتساب وأرد عليها.")
    return ("WhatsApp is not linked yet — linking is designed but not "
            "built: it needs to be linked first with a third number via a "
            "WhatsApp Web QR code, then I can receive and reply to "
            "WhatsApp messages.")


def not_built_line(feature_ar: str, feature_en: str,
                   language: str = "ar") -> str:
    """Truthful line for a designed-but-not-built feature."""
    if language == "ar":
        return (f"{feature_ar} متصممة بس لسه مش مبنية — مقدرش أعملها "
                "دلوقتي، ولو سألك عنها اشرحله إنها جاية في نسخة جاية "
                "وقدّم بديل.")
    return (f"{feature_en} is designed but not built yet — I cannot do "
            "it right now; if asked, explain it is coming in a later "
            "version and offer an alternative.")
