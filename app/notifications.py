# Memo AI v1.4.2 - Important Windows notification alerts
# Listens to Windows toast notifications (winrt
# UserNotificationListener), filters for the IMPORTANT ones (important
# app OR keyword match), and hands them to the parent through ONE
# callback (on_important) - the same delivery pattern as the system
# monitor's on_alert, so the parent wires both to a single handler
# (in-app alert + Memo AI toast).
#
# Everything winrt is FULLY import-guarded: on Linux, or without the
# `winrt` package, the module imports fine, available=False, and every
# status function says so honestly - it NEVER claims to work when it
# does not. First run on Windows needs the user to grant notification
# access (see request_access); until then access is "not_requested".
#
# PRIVACY: only the app name + matched/not-matched is ever logged.
# Notification title/body text is NEVER written to any log - it can
# contain private content. The title is carried in the alert dict (for
# the user to see in-app / in the toast) but is never logged.

import asyncio
import sys
import threading
import time
from datetime import datetime

try:
    from logger import info, warn
except Exception:  # standalone import (tests import with app/ on path)
    def info(*args, **kwargs):
        return None

    def warn(*args, **kwargs):
        return None


try:
    from winrt.windows.ui.notifications import (
        ToastNotification,
        ToastNotificationManager,
        UserNotificationListener,
    )
    from winrt.windows.data.xml.dom import XmlDocument
    _WINRT_AVAILABLE = True
    _WINRT_IMPORT_ERROR = ""
except Exception as exc:  # ImportError on Linux / without `winrt`
    ToastNotification = None
    ToastNotificationManager = None
    UserNotificationListener = None
    XmlDocument = None
    _WINRT_AVAILABLE = False
    _WINRT_IMPORT_ERROR = str(exc)


# Exact settings keys the parent must add to settings_store.DEFAULTS.
# (Flat keys, same style as the rest of settings_store.)
DEFAULT_NOTIFICATION_SETTINGS = {
    "notifications_enabled": True,
    # App display names / substrings that are always important.
    "notifications_important_apps": [
        "WhatsApp",
        "Outlook", "Microsoft Outlook",
        "Mail", "Gmail",
        "Windows Security", "Windows Update",
    ],
    # Keywords (EN + AR, case-insensitive) that make ANY app's
    # notification important when they appear in its title/body.
    "notifications_keywords": [
        "urgent", "security", "update", "password",
        "critical", "error", "alert",
        "مهم", "عاجل", "تنبيه", "تحديث", "كلمة السر", "خطأ", "حرج", "أمن",
    ],
}

# Matches in the app-name list are substring matches (case-insensitive):
# "Microsoft Outlook" matches, and so does "Mail" inside
# "Mail and Calendar". The user can narrow this from Settings.
DEFAULT_IMPORTANT_APPS = DEFAULT_NOTIFICATION_SETTINGS[
    "notifications_important_apps"]
DEFAULT_KEYWORDS = DEFAULT_NOTIFICATION_SETTINGS["notifications_keywords"]

# How often the watcher re-reads the current toasts (seconds).
_DEFAULT_POLL_INTERVAL_SEC = 30


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _norm(text) -> str:
    return (text or "").strip().lower()


# ---------------------------------------------------------------------------
# Listener availability / permission (honest status, no pretending)
# ---------------------------------------------------------------------------

def get_listener_status() -> dict:
    """{"available": bool, "access": "granted"|"denied"|"not_requested"|
    "unavailable", "detail": str} - shown as-is in Settings, so the
    user always sees the truth about the listener."""
    if not _WINRT_AVAILABLE:
        return {
            "available": False,
            "access": "unavailable",
            "detail": ("Windows notification listener is unavailable: "
                       "the `winrt` package is not installed or this is "
                       "not Windows"
                       + (f" ({_WINRT_IMPORT_ERROR})"
                          if _WINRT_IMPORT_ERROR else "")),
        }
    try:
        listener = UserNotificationListener.get_current()
        status = listener.get_access_status()
        name = str(getattr(status, "name", status)).upper()
        if "ALLOWED" in name:
            return {"available": True, "access": "granted",
                    "detail": "Notification access granted - "
                              "important notifications are watched."}
        if "DENIED" in name:
            return {"available": True, "access": "denied",
                    "detail": ("Notification access denied - allow Memo AI "
                               "in Windows Settings > System > Notifications "
                               "to watch important notifications.")}
        return {"available": True, "access": "not_requested",
                "detail": ("Notification access not requested yet - "
                           "Memo AI will ask once on first run.")}
    except Exception as exc:
        return {"available": False, "access": "unavailable",
                "detail": f"Could not check notification access: {exc}"}


def request_access() -> dict:
    """Ask Windows for notification-listener access. Blocks briefly (the
    winrt async call runs on its own event loop in this thread - the
    parent should call it from a background thread, never the JS
    bridge thread). Returns {"ok": bool, "access": ..., "detail": str}.

    On first run Windows shows the user a permission prompt; until he
    answers, access stays "not_requested". Safe to call on Linux - it
    just reports unavailable.
    """
    if not _WINRT_AVAILABLE:
        return {"ok": False, "access": "unavailable",
                "detail": get_listener_status()["detail"]}
    try:
        async def _ask():
            listener = UserNotificationListener.get_current()
            return await listener.request_access_async()

        status = asyncio.run(_ask())
        name = str(getattr(status, "name", status)).upper()
        if "ALLOWED" in name:
            info("notifications", "Notification access granted")
            return {"ok": True, "access": "granted",
                    "detail": "Notification access granted."}
        if "DENIED" in name:
            warn("notifications", "Notification access denied by user")
            return {"ok": False, "access": "denied",
                    "detail": "Notification access denied."}
        return {"ok": False, "access": "not_requested",
                "detail": "Notification access not decided yet."}
    except Exception as exc:
        warn("notifications", f"request_access failed: {exc}")
        return {"ok": False, "access": "unavailable",
                "detail": f"Could not request notification access: {exc}"}


# ---------------------------------------------------------------------------
# Toast output (best effort - False means "parent falls back to in-app")
# ---------------------------------------------------------------------------

def show_toast(title: str, message: str) -> bool:
    """Show a Windows toast via winrt ToastNotificationManager.
    Returns True only if the toast was actually handed to Windows;
    False on Linux / without winrt / on any failure (the parent then
    shows the in-app alert only)."""
    if not _WINRT_AVAILABLE:
        return False
    try:
        xml = XmlDocument()
        xml.load_xml(
            "<toast><visual><binding template=\"ToastText02\">"
            f"<text id=\"1\">{_xml_escape(title)}</text>"
            f"<text id=\"2\">{_xml_escape(message)}</text>"
            "</binding></visual></toast>")
        notifier = (ToastNotificationManager
                    .create_toast_notifier("Memo AI"))
        notifier.show(ToastNotification(xml))
        return True
    except Exception as exc:
        warn("notifications", f"Could not show toast: {exc}")
        return False


def _xml_escape(text: str) -> str:
    return (str(text or "")
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ---------------------------------------------------------------------------
# Importance filter + watcher
# ---------------------------------------------------------------------------

class NotificationWatcher:
    """Filters Windows notifications for the important ones.

    settings: plain dict (merged over DEFAULT_NOTIFICATION_SETTINGS).
    on_important: callback(alert: dict) -> None, called for every
        important notification. The parent wires it to the SAME handler
        as the system monitor's on_alert (in-app alert + Memo AI toast).
        May be None (on_notification still returns the alert dict).
    """

    def __init__(self, settings=None, on_important=None):
        merged = dict(DEFAULT_NOTIFICATION_SETTINGS)
        if settings:
            merged.update({k: v for k, v in settings.items()
                           if k in DEFAULT_NOTIFICATION_SETTINGS})
        self.settings = merged
        self.on_important = on_important
        self._stop_event = threading.Event()
        self._thread = None
        self.running = False
        self._seen_ids = set()  # toast ids already delivered this session

    # -- matching -----------------------------------------------------------

    def _app_matches(self, app_name: str) -> bool:
        name = _norm(app_name)
        if not name:
            return False
        return any(_norm(entry) and _norm(entry) in name
                   for entry in self.settings["notifications_important_apps"])

    def _keyword_matches(self, title: str, body: str) -> str | None:
        haystack = f"{_norm(title)} {_norm(body)}"
        for keyword in self.settings["notifications_keywords"]:
            if _norm(keyword) and _norm(keyword) in haystack:
                return keyword
        return None

    def is_important(self, app_name: str, title: str = "",
                     body: str = "") -> tuple:
        """(important bool, reason str). Reason is "app" when the app is
        in the important list, "keyword:<word>" on a keyword match,
        "" when not important."""
        if self._app_matches(app_name):
            return True, "app"
        keyword = self._keyword_matches(title, body)
        if keyword:
            return True, f"keyword:{keyword}"
        return False, ""

    # -- delivery -------------------------------------------------------------

    def on_notification(self, app_name: str, title: str = "",
                        body: str = "") -> dict | None:
        """One notification in. Important -> alert dict (AND the
        on_important callback); not important -> None.

        Only the app name + matched/not-matched is logged - the title
        and body are NEVER logged (private content). The title is
        carried in the alert dict (field "notification_title") so the
        parent can show the user what the notification was about; the
        body is not stored anywhere.
        """
        important, reason = self.is_important(app_name, title, body)
        # Log: app name + verdict only. Never title/body.
        info("notifications",
             f"Notification from '{app_name or '?'}': "
             f"{'important (' + reason + ')' if important else 'ignored'}")
        if not important:
            return None
        alert = {
            "kind": "notification",
            "level": "warn",
            "title_ar": "إشعار مهم",
            "message_ar": f"وصل إشعار مهم من {app_name or 'تطبيق'}",
            "notification_title": title or "",
            "app": app_name or "",
            "at": _now_iso(),
        }
        if self.on_important is not None:
            try:
                self.on_important(alert)
            except Exception as exc:
                warn("notifications",
                     f"on_important callback failed: {exc}")
        return alert

    # -- live listener (Windows only, polling the current toasts) -----------

    def _fetch_current(self) -> list:
        """Current Windows toasts as [{"id", "app", "title", "body"}].
        Empty list when winrt is unavailable or the fetch fails."""
        if not _WINRT_AVAILABLE:
            return []
        try:
            async def _get():
                listener = UserNotificationListener.get_current()
                return await listener.get_notifications_async()

            notifications = asyncio.run(_get())
            out = []
            for note in notifications:
                try:
                    toast = note.toast_notification
                    content = toast.content if toast is not None else None
                    texts = []
                    if content is not None:
                        nodes = content.get_elements_by_tag_name("text")
                        for i in range(nodes.length):
                            texts.append(nodes.item(i).inner_text or "")
                    app = ""
                    try:
                        app = note.app_info.display_info.display_name or ""
                    except Exception:
                        pass
                    out.append({
                        "id": str(getattr(note, "id", "") or
                                  f"{app}|{texts}"),
                        "app": app,
                        "title": texts[0] if texts else "",
                        "body": " ".join(texts[1:]) if len(texts) > 1 else "",
                    })
                except Exception:
                    continue
            return out
        except Exception as exc:
            warn("notifications",
                 f"Could not read current notifications: {exc}")
            return []

    def poll_once(self) -> list:
        """One poll: read the current toasts, deliver the important NEW
        ones (by id). Returns the list of alerts that fired."""
        fired = []
        for note in self._fetch_current():
            if note["id"] in self._seen_ids:
                continue
            self._seen_ids.add(note["id"])
            alert = self.on_notification(note["app"], note["title"],
                                         note["body"])
            if alert is not None:
                fired.append(alert)
        return fired

    def start(self, interval_sec: int = _DEFAULT_POLL_INTERVAL_SEC) -> bool:
        """Start the watcher thread. Returns False when disabled, when
        winrt is unavailable, or when access is not granted (the parent
        must call request_access() first and check the status)."""
        if self.running:
            return True
        if not self.settings.get("notifications_enabled", True):
            info("notifications",
                 "Notification alerts disabled in settings - not started")
            return False
        status = get_listener_status()
        if not status["available"] or status["access"] != "granted":
            warn("notifications",
                 "Notification watcher not started: "
                 f"{status['access']} - {status['detail']}")
            return False
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, args=(interval_sec,),
            daemon=True, name="memo-notifications")
        self._thread.start()
        self.running = True
        info("notifications", "Important-notification watcher started")
        return True

    def _poll_loop(self, interval_sec: int):
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception as exc:
                warn("notifications", f"Notification poll failed: {exc}")
            self._stop_event.wait(max(interval_sec, 5))

    def stop(self):
        """Stop the watcher thread (called from MemoApp.quit)."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self.running = False
        info("notifications", "Important-notification watcher stopped")
