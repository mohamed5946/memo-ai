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

# WhatsApp linking: DESIGNED (3rd number only, WhatsApp Web QR, Memo AI
# as a linked device; main number -> owner mode, others -> reply mode)
# but NOT BUILT in v1.3.2. v1.4.0 (branch feat/whatsapp, separate build)
# implements it and flips WHATSAPP to "ready" + sets WHATSAPP_LINKED
# from the real session. Until then the brain must say: "not linked
# yet — linking is being built, here's what it will need (QR + 3rd
# number)", never a flat "I can't".
WHATSAPP = "building"
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


def whatsapp_status_line(language: str = "ar") -> str:
    """One truthful line about WhatsApp, for the system prompt."""
    if WHATSAPP_LINKED:
        if language == "ar":
            return ("الواتساب مربوط ✅ — تقدر تستقبل رسايل واتساب وترد عليها "
                    "من هنا.")
        return "WhatsApp is linked ✅ — you can receive and reply to WhatsApp messages from here."
    if WHATSAPP == "building":
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
