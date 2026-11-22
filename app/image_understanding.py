# LONA v1.6.0 - Image / screenshot understanding (backend part).
#
# send_image_to_gemini(image_bytes, prompt): Gemini 2.5 Flash vision,
# 30s timeout. ONLY when the UI passes confirmed=True - the UI shows
# the "this leaves the device" warning first and the user confirms;
# without confirmation the send is REFUSED (privacy rule, scope item
# 10). Image bytes are never logged (only the byte size, for diagnosis).

import base64

from logger import error, info, warn

GEMINI_MODEL = "gemini-2.5-flash"
_GENERATE_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
                 f"{GEMINI_MODEL}:generateContent")
_TIMEOUT = 30.0
_MAX_BYTES = 10 * 1024 * 1024


def send_image_to_gemini(image_bytes: bytes, prompt: str,
                         confirmed: bool = False,
                         timeout: float = _TIMEOUT) -> dict:
    """Send an image + prompt to Gemini 2.5 Flash and return the model's
    description. Returns {"ok", "text"} or {"ok": False, "reason",
    "detail"}. Refused unless confirmed=True (the UI warning + the
    user's yes). The image bytes are never logged - only their size."""
    if not confirmed:
        warn("image", "Image send refused: no user confirmation "
                      "(leaves-device warning not confirmed)")
        return {"ok": False, "reason": "needs_confirmation",
                "detail": ("الصورة بتتبعت لجوجل عشان تتفهم — أكّد الأول "
                           "من الشاشة إنك موافق.")}
    if not image_bytes or not isinstance(image_bytes, (bytes, bytearray)):
        return {"ok": False, "reason": "empty_image",
                "detail": "مفيش صورة تتبعت."}
    if len(image_bytes) > _MAX_BYTES:
        return {"ok": False, "reason": "too_large",
                "detail": "الصورة كبيرة أوي (أكتر من 10MB)."}
    prompt = str(prompt or "").strip() or "اشرح اللي في الصورة دي بالعربي."
    try:
        import apikeys
        # v1.6.1: vision is a Gemini 2.5 Flash LLM call, so it uses
        # the CHAT key - never the voice key.
        key = apikeys.read_key_value("gemini-chat")
    except Exception as exc:
        return {"ok": False, "reason": "key_unavailable",
                "detail": f"مقدرتش أقرأ مفتاح Gemini: {exc}"}
    if not key:
        return {"ok": False, "reason": "no_key",
                "detail": ("مفيش مفتاح Gemini للمحادثة — دخّله من "
                           "الإعدادات » مفاتيح API الأول.")}
    try:
        import requests
    except Exception:
        return {"ok": False, "reason": "network_unavailable",
                "detail": "مكتبة الشبكة مش متاحة في النسخة دي."}
    mime = "image/png"
    head = bytes(image_bytes[:4])
    if head[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif head[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    payload = {"contents": [{
        "parts": [
            {"text": prompt},
            {"inline_data": {
                "mime_type": mime,
                "data": base64.b64encode(bytes(image_bytes)).decode("ascii")}},
        ]}]}
    try:
        resp = requests.post(_GENERATE_URL, params={"key": key},
                             json=payload, timeout=timeout)
    except Exception as exc:
        error("image", f"Gemini vision request failed "
                       f"({type(exc).__name__}: {exc})")
        return {"ok": False, "reason": "request_failed",
                "detail": f"مقدرتش أوصل لـ Gemini ({type(exc).__name__})."}
    if resp.status_code != 200:
        warn("image", f"Gemini vision HTTP {resp.status_code}")
        return {"ok": False, "reason": "http_error",
                "detail": f"Gemini رجّع خطأ (HTTP {resp.status_code})."}
    try:
        parts = (resp.json()["candidates"][0]["content"]["parts"])
        text = "".join(p.get("text", "") for p in parts).strip()
    except Exception as exc:
        error("image", f"Gemini vision reply unparsable: {exc}")
        return {"ok": False, "reason": "bad_reply",
                "detail": "رد Gemini مش مفهوم."}
    if not text:
        return {"ok": False, "reason": "empty_reply",
                "detail": "Gemini رجّع رد فاضي."}
    info("image", f"Gemini vision OK ({len(image_bytes)} bytes in, "
                  f"{len(text)} chars out)")
    return {"ok": True, "text": text}
