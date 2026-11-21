# LONA v1.6.0 - Voice browser control.
#
# "افتح يوتيوب" -> open the DEFAULT browser on YouTube. "شغّل الدحيح
# من على اليوتيوب" -> open the default browser on the YouTube search
# page AND ask the Chrome extension to play the first result. The
# extension part is owned by the extension worker (native host): if it
# is not connected, play_youtube_first() says so honestly and the UI
# tells the user the search page is already open - never a fake claim
# that a video is playing.

import re
import webbrowser
from urllib.parse import quote_plus

from logger import error, info, warn

YOUTUBE_HOME = "https://www.youtube.com/"
YOUTUBE_SEARCH = "https://www.youtube.com/results?search_query={query}"


def youtube_search_url(query: str) -> str:
    """YouTube search URL for a query (URL-quoted, never raw)."""
    return YOUTUBE_SEARCH.format(query=quote_plus(str(query or "").strip()))


def open_default_browser(url: str) -> bool:
    """Open a URL in the system default browser. Returns True when the
    browser was launched. Only http(s) URLs are ever opened."""
    url = str(url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        warn("browser", f"Refused to open non-http(s) URL: {url[:80]}")
        return False
    try:
        ok = bool(webbrowser.open(url))
        info("browser", f"Opened default browser: {url[:120]}")
        return ok
    except Exception as exc:
        error("browser", f"Could not open browser ({url[:80]}): {exc}")
        return False


def open_youtube(query: str | None = None) -> dict:
    """Open YouTube in the default browser: the search page when a
    query is given, the homepage otherwise."""
    url = youtube_search_url(query) if (query or "").strip() else YOUTUBE_HOME
    ok = open_default_browser(url)
    return {"ok": ok, "url": url, "query": (query or "").strip() or None}


def _extension_play_first(query: str) -> dict:
    """Ask the Chrome extension (via the native host) to play the first
    YouTube result for `query`. The native host module is owned by the
    extension worker and imported lazily - when it is absent, this is
    honestly {"ok": False, "reason": "extension_not_connected"}."""
    try:
        import native_host  # owned by the extension worker
    except Exception:
        return {"ok": False, "reason": "extension_not_connected"}
    try:
        send = getattr(native_host, "send_command", None)
        if send is None:
            return {"ok": False, "reason": "extension_not_connected"}
        return dict(send({"action": "play_first_youtube_result",
                          "query": query}) or {})
    except Exception as exc:
        warn("browser", f"Extension play-first failed: {exc}")
        return {"ok": False, "reason": "extension_not_connected",
                "detail": str(exc)}


def play_youtube_first(query: str) -> dict:
    """Voice "play X from YouTube": open the search page in the default
    browser, then ask the extension to play the first result. When the
    extension is not connected the search page is still open and the
    result says so honestly (the UI tells the user to tap the first
    video himself)."""
    query = str(query or "").strip()
    if not query:
        return {"ok": False, "reason": "empty_query"}
    opened = open_youtube(query)
    ext = _extension_play_first(query)
    if ext.get("ok"):
        info("browser", f"Playing first YouTube result for: {query[:80]}")
        return {"ok": True, "url": opened["url"], "query": query,
                "via": "extension"}
    return {"ok": False, "reason": "extension_not_connected",
            "url": opened["url"], "query": query,
            "opened_search": bool(opened.get("ok")),
            "detail": ("The YouTube search page is open - tap the first "
                       "video to play it (the browser extension is not "
                       "connected).")}


# -- voice intent parsing (Arabic + English) -------------------------------

_OPEN_YOUTUBE_RE = re.compile(
    r"(افتح(لي)?\s+(يوتيوب|يوتوب|youtube))|"
    r"(open\s+(youtube|you\s*tube))",
    re.IGNORECASE)

_PLAY_YOUTUBE_RE = re.compile(
    r"(شغ[ّ]?ل|شغل|العب|play)\s+(.+?)\s+من\s+على\s+(اليوتيوب|يوتيوب|youtube)"
    r"|(play)\s+(.+?)\s+(on|from)\s+(youtube|you\s*tube)",
    re.IGNORECASE)


def parse_voice_intent(text: str) -> dict | None:
    """Parse a voice/text command into a browser action, or None when
    it is not a browser command. Returns {"action": "open_youtube"} or
    {"action": "play_youtube", "query": ...}."""
    raw = str(text or "").strip()
    if not raw:
        return None
    m = _PLAY_YOUTUBE_RE.search(raw)
    if m:
        query = (m.group(2) or m.group(5) or "").strip()
        query = re.sub(r"\s+", " ", query)
        if query:
            return {"action": "play_youtube", "query": query}
    if _OPEN_YOUTUBE_RE.search(raw):
        return {"action": "open_youtube"}
    return None


def handle_voice_intent(text: str) -> dict | None:
    """Parse + execute a browser voice intent, or None when the text is
    not a browser command. The caller (brain/tools) falls through to
    normal chat when this returns None."""
    intent = parse_voice_intent(text)
    if intent is None:
        return None
    if intent["action"] == "play_youtube":
        return play_youtube_first(intent["query"])
    return open_youtube()
