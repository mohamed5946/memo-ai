# Memo AI v1.2 - Desktop app (Python + pywebview, WebView2 on Windows)
# Runs as a NORMAL desktop program: own window + taskbar icon, NOT a browser.
# Starts as the Dynamic Island ONLY. The full window never opens by itself.
# The app NEVER fully closes via X/taskbar - only via Quit inside Settings.
# No ads. No model touches the laptop by itself - this Python layer is the
# "hands" (mic, speakers, files, weather); the model is only the "brain".
#
# v1.1 FIX (2026-11-06): the JS bridge class Api used to hold a reference to
# the MemoApp (self.app), and the app holds the pywebview Window objects.
# pywebview walks the js_api object with dir()/getattr to build the JS bridge,
# and on pywebview 5.x its cycle check `if obj in exposed_objects` compares
# objects with == - which explodes when it reaches a .NET object inside a
# Window (TypeError: Rectangle.op_Equality). The Api now holds NO reference
# to the app at all: it reaches the app only through the module-level _APP
# global, which dir(api) never sees. pywebview>=6.2 is also pinned in
# requirements.txt (its introspection is id-based and cannot hit this).
#
# v1.2.1 INSTALLER (2026-11-06): real Windows setup.exe (PyInstaller
# one-folder + Inno Setup, per-user install, no admin, no Python/pip).
# Frozen-aware paths: settings_store.resolve_app_paths() — in the frozen
# install the app files live in _internal/app/ (PyInstaller 6 puts all
# collected files under _internal/), while keys/ stays next to the exe,
# so keys/ and app/data/ work exactly like the zip install. The in-app
# updater in frozen mode installs the new setup.exe silently (see
# updater.install_setup) because a source zip cannot replace the frozen
# exe itself.
#
# v1.3.0 CHROMA-KEY TRANSPARENCY (2026-11-07) - CORRECTS the v1.2
# mental model below (kept for history): `transparent=True` in pywebview
# on Windows does NOT make the WINDOW transparent. Verified against the
# pywebview 6.2 source (platforms/winforms.py + edgechromium.py):
#   - edgechromium.py:106-114: transparent=True only sets the WebView2
#     control's DefaultBackgroundColor to Transparent - i.e. transparent
#     INSIDE the control, showing "hosting app content" behind it.
#     Behind the control there is nothing: it docks Fill over the whole
#     window, so transparent pixels render as BLACK (absence of paint,
#     not a drawn color - grep for Color.Black in the backend = zero).
#   - winforms.py:286-291: the WinForms Form itself is NEVER made
#     transparent - no TransparencyKey, no WS_EX_LAYERED, no
#     SetLayeredWindowAttributes anywhere in the backend. A plain HWND
#     is opaque; DWM paints its transparent pixels black.
#   - winforms.py:289 is dead code anyway: EdgeChrome has no
#     DefaultBackgroundColor attribute, so that assignment creates a
#     plain Python attribute that never reaches the control.
#   - Qt/Cocoa/GTK backends DO real translucency (WA_TranslucentBackground
#     etc.) - WinForms alone does not. 6.2.1 changes nothing here.
# So no CSS/HTML change could ever fix the black rectangle, and the
# v1.2.0 backdrop-filter removal was treating a symptom. The real fix:
# CHROMA-KEY. The island/pet pages paint pure magenta #FF00FF as their
# background (a color used NOWHERE in the UI), and main.py applies a
# Win32 layered window to each HWND (WS_EX_LAYERED +
# SetLayeredWindowAttributes LWA_COLORKEY), so Windows itself removes
# the magenta pixels - the pill/cat alone remain, with no rectangle.
# Known trade-offs (tell the user): (1) ~1px pinkish fringe on
# anti-aliased edges (rounded pill corners, semi-transparent fur edges
# blend with magenta) - clearest on light wallpapers; (2) keyed pixels
# become click-through - DESIRED here (clicks pass through the empty
# space around the cat/island; the pill itself stays clickable).
# The HWND comes from window.native.Handle (pywebview sets .native to
# the WinForms Form, winforms.py:194), with an EnumWindows title+size
# fallback. Applied from a thread started by webview.start(func) -
# which runs BEFORE any window exists, so it polls up to 60s for the
# HWNDs. The style lives on the HWND, so it survives hide/show.
# FIELD TEST STILL REQUIRED (cannot be proven from Linux): screenshots
# over a light AND a dark desktop wallpaper, pill/mic clicks still
# working, clicks passing through the empty space around the cat.

# v1.2 TRANSPARENCY (2026-11-06) - SUPERSEDED by the v1.3.0 chroma-key
# fix above. Kept as history: the island/pet showed a white rectangle.
# The v1.2 theory (MSHTML fallback paints background_color white) was
# wrong on the user's machine - the v1.2.5 log proved WebView2 WAS the
# engine, yet the black rectangle stayed. Real cause: see v1.3.0 note.
# (1) backdrop-filter removed from the island; (2) background_color
# "#0B0F16" so failure degraded to a dark rectangle; (3) html/body
# background transparent in JS. None of it could work - see above.

# v1.2.5 BRIDGE FILES (2026-11-06): a second, deeper cause of the dead
# buttons — pywebview builds window.pywebview from its js/ files, and if
# they are missing from the frozen bundle the bridge never exists at all
# (silently, inside the .NET event handler). The spec now pins webview/js
# explicitly, main.py self-checks for api.js at startup and logs a LOUD
# bridge error if missing, and the no_bridge message tells the user to
# restart / install WebView2 instead of calling it "preview mode".
#
# v1.2.3 IDLE/ACTIVE ISLAND (2026-11-06): the user wants the island
# SMALL when idle (cat + mic only, ~190x52) and growing GRADUALLY
# (~0.45s ease) when active (greeting / listening / reply), then
# shrinking back after ~7s idle. The pill's dark navy color is NOT
# changed. (The black background he saw was the opaque WinForms window
# itself - the v1.3.0 investigation proved it was never WebView2; fixed
# by chroma-key, see the v1.3.0 note at the top.) Implementation: the
# .island div is 100% of the window, and the WINDOW animates in small
# steps via rAF -> the new Api.island_resize(w, h) (which also
# re-centers it on top); the texts
# fade in as it grows. Old island_expand/island_shrink removed.

import ctypes
import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path

# Frozen-aware paths live in settings_store (one helper for all modules).
# The sys.path insert below is only needed for the zip/source run; in the
# frozen (PyInstaller) run all modules are already bundled.
if not getattr(sys, "frozen", False):
    _SCRIPT_DIR = Path(__file__).resolve().parent
    if str(_SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPT_DIR))

import webview
from logger import error, info, log_text, read_recent, warn
from settings_store import (APP_DIR, BASE_DIR, keys_status, load_settings,
                            save_settings)

import brain
import hear
import rules
import speak
import tools
import updater
import weather

VERSION = "1.3.0"
MAIN_W, MAIN_H = 960, 720
ISLAND_W, ISLAND_H = 380, 56
ISLAND_EXPANDED_H = 120
ISLAND_IDLE_W, ISLAND_IDLE_H = 190, 52
PET_SIZE = 150

# Single-instance mutex (Windows named mutex, no admin needed).
_MUTEX_NAME = "Local\\MemoAI-SingleInstance"
_mutex_handle = None

# v1.3.0 TOOLS ("hands") + VOICE-OUTPUT TOGGLE + OWNER CODE (2026-11-07):
# The brain can now DO things: open apps, create/edit/read files, run
# commands - through app/tools.py, with a real Groq function-calling loop
# (brain.ask_with_tools). Every write/run/open action asks the user FIRST
# in an in-app Allow/Deny dialog showing the exact action; reads are
# logged, no dialog; destructive commands are blocklisted; there is NO
# delete tool and NO "always allow". The local Qwen 3B stays chat-only.
# Chat with tools runs in a BACKGROUND thread (see Api.chat): the JS
# bridge must never block waiting for a permission, or the Allow/Deny
# dialog could never answer (deadlock). The UI polls chat_result() and
# get_pending_permission() while a tool chat is running.
# Voice output toggle: settings.voice_output (default ON, persisted).
# speak_async() is the single choke point - when OFF, no TTS engine is
# called at all (no Gemini/Azure/Piper API use). The mic button is
# untouched (voice INPUT is separate).
# Owner code: stored ONLY as a SHA-256 hash below (OWNER_CODE_HASH).
# The plaintext value is never in this file, in settings, in logs, or
# in any comment. Typing it in chat unlocks owner mode (tools on);
# without it the app stays reply-only (chat, no tools). Allow/Deny
# dialogs still apply in owner mode.

# Owner-mode code, SHA-256 hash ONLY (see note above). Compared with
# hmac.compare_digest; the plaintext value exists nowhere in the repo.
OWNER_CODE_HASH = "900e5e36492a56eed142c11368b25b677487dec61cea264a0e4a1f64edebaa47"

# Async tool-chat results: chat_id -> {"done", "reply", "engine", ...}.
_chats: dict = {}
_chats_lock = threading.Lock()

# Result of the startup bridge-files self-check (None = not run yet).
_BRIDGE_FILES_OK = None

# v1.3.0 chroma-key color: the island/pet pages paint their background
# this exact magenta; Windows removes it from the window (see the
# v1.3.0 transparency note at the top of this file). COLORREF is
# 0x00BBGGRR, so RGB(255,0,255) -> 0x00FF00FF.
CHROMA_KEY_HEX = "#FF00FF"
CHROMA_KEY_COLORREF = 0x00FF00FF


def _window_hwnd(window):
    """HWND of a pywebview window, or None if it is not created yet.
    WinForms backend sets window.native to the Form (pywebview 6.2
    winforms.py:194); Form.Handle is the HWND."""
    try:
        native = getattr(window, "native", None)
        if native is not None:
            return int(native.Handle.ToInt32())
    except Exception:
        pass
    return None


def _find_hwnd_by_title(title: str, max_width: int | None = None):
    """Fallback HWND lookup (EnumWindows): first top-level window whose
    title matches exactly. max_width distinguishes the small island
    ("Memo AI", <=400px) from the big main window (same title)."""
    try:
        user32 = ctypes.windll.user32
        found = []

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _enum(hwnd, _lparam):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value == title:
                rect = RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                if max_width is None or \
                        (rect.right - rect.left) <= max_width:
                    found.append(hwnd)
            return True

        user32.EnumWindows(_enum, None)
        return found[0] if found else None
    except Exception:
        return None


def _apply_chroma_key(hwnd: int, label: str) -> bool:
    """Make pure #FF00FF pixels transparent AND click-through on a Win32
    HWND (WS_EX_LAYERED + SetLayeredWindowAttributes LWA_COLORKEY).
    Returns True on success; every outcome is logged (diagnostics)."""
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        LWA_COLORKEY = 0x00000001
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
        ok = user32.SetLayeredWindowAttributes(
            hwnd, CHROMA_KEY_COLORREF, 0, LWA_COLORKEY)
        if ok:
            info("app", f"Chroma-key transparency applied to {label} "
                        f"(HWND {hwnd}) - magenta pixels now see-through")
            return True
        error("app", f"SetLayeredWindowAttributes FAILED for {label} "
                     f"(HWND {hwnd}) - black rectangle may remain")
        return False
    except Exception as exc:
        error("app", f"Chroma-key failed for {label}: {exc}")
        return False


def _check_bridge_files() -> bool:
    """Loud startup self-check: pywebview builds window.pywebview from its
    js/ files (util.load_js_files -> get_js_dir -> <webview>/js/api.js).
    If they are missing from the bundle, the bridge never exists and every
    button silently does nothing — the exact failure the user reported as
    preview mode + dead buttons. Never fail silently: log a LOUD error.
    Returns True when api.js is present. A failure here never blocks
    startup (the app still runs; the log + the UI message say why the
    buttons are dead)."""
    global _BRIDGE_FILES_OK
    js_dir = ""
    try:
        from webview import util as _wv_util
        js_dir = _wv_util.get_js_dir()
    except Exception:
        try:
            import webview as _wv
            js_dir = os.path.join(os.path.dirname(_wv.__file__), "js")
        except Exception:
            js_dir = ""
    api_js = os.path.join(js_dir, "api.js") if js_dir else ""
    if not js_dir or not os.path.isfile(api_js):
        error("bridge", "pywebview JS bridge files MISSING from bundle "
              f"(js dir: {js_dir or 'unknown'}) — window.pywebview will "
              "never exist, every button will be dead. Rebuild with the "
              "webview/js datas entry in installer/memo-ai.spec.")
        _BRIDGE_FILES_OK = False
        return False
    info("bridge", f"pywebview JS bridge files present ({js_dir})")
    _BRIDGE_FILES_OK = True
    return True


def _acquire_single_instance_lock() -> bool:
    """Only one Memo AI may run at a time.

    The zip install and the setup.exe install are the same app, so a
    second launch (from either location) must exit quietly instead of
    opening a second island + second pet. Returns True if this process
    owns the lock. Any failure to create the mutex is logged and treated
    as "lock acquired" so a broken check can never block startup.
    """
    global _mutex_handle
    if sys.platform != "win32":
        return True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if not handle:
            error("app", "Could not create single-instance mutex "
                         "(continuing without the lock)")
            return True
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return False
        _mutex_handle = handle
        return True
    except Exception as exc:
        error("app", f"Single-instance check failed ({exc}) - continuing")
        return True

# Module-level app reference. Deliberately NOT an attribute of Api:
# pywebview introspects the js_api instance, so anything stored on Api
# (app, windows, ...) gets walked and can crash the bridge (see note above).
_APP = None


def screen_size() -> tuple:
    try:
        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080


class Api:
    """JS bridge: every method here is callable from the UI as pywebview.api.*

    Holds NO state and NO reference to the app/windows - only plain methods.
    App access goes through the module-level _APP global.
    """

    # -- status ------------------------------------------------------------
    def get_status(self):
        online = brain.is_online()
        return {
            "online": online,
            "version": VERSION,
            "bridge_files_missing": _BRIDGE_FILES_OK is False,
            "keys": keys_status(),
            "settings": {k: v for k, v in _APP.settings.items()
                         if k != "owner_unlocked"},
            "owner_unlocked": _APP.settings.get("owner_unlocked", False),
        }

    def get_settings(self):
        return {k: v for k, v in _APP.settings.items()
                if k != "owner_unlocked"}

    def save_settings(self, new_values):
        try:
            data = json.loads(new_values) if isinstance(new_values, str) else new_values
        except Exception as exc:
            error("app", f"Bad settings payload: {exc}")
            return {"ok": False}
        for key in ("ai_name", "user_name", "language", "voice",
                    "greeting_enabled", "voice_output", "avatar",
                    "pet_visible", "pet_parked"):
            if key in data:
                _APP.settings[key] = data[key]
        ok = save_settings(_APP.settings)
        info("app", "Settings saved from UI")
        _APP.apply_settings()
        return {"ok": ok}

    # -- chat --------------------------------------------------------------
    def chat(self, text):
        text = (text or "").strip()
        if not text:
            return {"reply": "", "engine": "none"}
        # Owner code unlocks owner mode (v1.3.0: compared as a SHA-256 hash
        # only - the plaintext code is nowhere in the repo, settings, or
        # logs, and the typed text is never echoed back).
        if hmac.compare_digest(
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
                OWNER_CODE_HASH):
            _APP.settings["owner_unlocked"] = True
            info("app", "Owner mode unlocked via secret code")
            return {"reply": "تمام، وضع المالك اتفتح — تقدر تسألني عن أي حاجة، "
                             "وأقدر كمان أفتح برامج وأعمل ملفات وأنفذ أوامر "
                             "(كل إجراء هيطلب إذنك الأول).",
                    "engine": "owner"}
        lang = _APP.settings.get("language", "ar")
        hit = rules.check(text, _APP.settings.get("owner_unlocked", False), lang)
        if hit:
            _APP.speak_async(hit["reply"])
            return {"reply": hit["reply"], "engine": hit["source"]}
        if _APP.settings.get("owner_unlocked", False):
            # Owner mode: the brain gets tools, in a BACKGROUND thread.
            # The JS bridge must never block waiting for a permission, or
            # the Allow/Deny dialog could never answer (deadlock). The UI
            # polls chat_result() + get_pending_permission() meanwhile.
            chat_id = uuid.uuid4().hex[:8]
            with _chats_lock:
                _chats[chat_id] = {"done": False, "reply": "", "engine": "groq",
                                   "tools_used": 0, "created": time.time()}
                if len(_chats) > 50:  # prune oldest finished entries
                    for old_id in sorted(
                            _chats, key=lambda i: _chats[i]["created"])[:10]:
                        if _chats[old_id]["done"]:
                            _chats.pop(old_id, None)
            threading.Thread(target=_APP.run_tool_chat,
                             args=(chat_id, text), daemon=True).start()
            return {"reply": "", "engine": "groq", "pending": True,
                    "chat_id": chat_id}
        result = brain.ask(text, _APP.settings.get("ai_name", "Memo"),
                           _APP.settings.get("user_name", "Mohamed"), lang)
        _APP.speak_async(result["reply"])
        return {"reply": result["reply"], "engine": result["engine"]}

    def chat_result(self, chat_id):
        """Poll the result of an async (tool) chat. {"done": False} while
        the brain thread is still working (possibly waiting on permission)."""
        with _chats_lock:
            entry = _chats.get(str(chat_id or ""))
        if not entry:
            return {"done": False}
        return {"done": bool(entry["done"]), "reply": entry.get("reply", ""),
                "engine": entry.get("engine", "groq"),
                "tools_used": entry.get("tools_used", 0)}

    def voice_chat(self):
        """Record 5s -> transcribe -> answer -> speak. Returns heard + reply.
        In owner mode the answer runs the tool loop in the background, so
        this returns {"pending": True, "chat_id": ...} and the UI polls
        chat_result() (same as chat)."""
        lang = _APP.settings.get("language", "ar")
        heard = hear.listen(lang)
        if not heard["text"]:
            return {"heard": "", "reply": heard.get("error", "مقدرتش أسمعك."),
                    "engine": "none"}
        result = self.chat(heard["text"])
        if result.get("pending"):
            return {"heard": heard["text"], "reply": "", "engine": "groq",
                    "pending": True, "chat_id": result["chat_id"],
                    "hear_engine": heard["engine"]}
        return {"heard": heard["text"], "reply": result["reply"],
                "engine": result["engine"], "hear_engine": heard["engine"]}

    def stop_speaking(self):
        speak.stop()
        return {"ok": True}

    # -- voice output toggle (v1.3.0) --------------------------------------
    def toggle_voice_output(self):
        """One-tap speaker toggle (OUTPUT only - the mic button is separate).
        When OFF, speak_async() never calls any TTS engine at all, so no
        Gemini/Azure/Piper API use happens. State is persisted."""
        new = not _APP.settings.get("voice_output", True)
        _APP.settings["voice_output"] = new
        save_settings(_APP.settings)
        if not new:
            speak.stop()  # stop anything currently playing
        info("app", f"Voice output {'ON' if new else 'OFF'} (speaker button)")
        return {"voice_output": new}

    # -- tool permissions (v1.3.0) -----------------------------------------
    def get_pending_permission(self):
        """Oldest unresolved Allow/Deny request, or None. Polled by the UI
        while a tool chat is running."""
        pending = tools.get_pending()
        return pending if pending else {"pending": None}

    def resolve_permission(self, req_id, allowed):
        ok = tools.resolve_permission(str(req_id or ""), bool(allowed))
        return {"ok": ok}

    # -- windows -----------------------------------------------------------
    def open_main(self):
        _APP.show_main()
        return {"ok": True}

    def island_resize(self, w, h):
        # v1.2.3: the island animates its window size (idle compact <->
        # full pill <-> expanded reply). Keep it centered at the top.
        try:
            if _APP.island_window:
                sw, _sh = screen_size()
                _APP.island_window.resize(int(w), int(h))
                _APP.island_window.move(max(0, (sw - int(w)) // 2), 12)
        except Exception as exc:
            error("ui", f"Could not resize island: {exc}")
        return {"ok": True}

    def minimize_to_island(self):
        _APP.hide_main()
        return {"ok": True}

    def toggle_pet(self, visible=None):
        if visible is None:
            visible = not _APP.settings.get("pet_visible", True)
        _APP.settings["pet_visible"] = bool(visible)
        save_settings(_APP.settings)
        _APP.apply_pet_visibility()
        return {"visible": bool(visible)}

    def park_pet(self):
        _APP.settings["pet_parked"] = True
        save_settings(_APP.settings)
        _APP.park_pet()
        return {"ok": True}

    def mark_parked(self):
        """Pet was dragged to a screen edge: save parked state (no move)."""
        _APP.settings["pet_parked"] = True
        save_settings(_APP.settings)
        info("pet", "Pet parked at a screen edge - playing with the ball")
        return {"ok": True}

    def unpark_pet(self):
        """Pet dragged away (or double-clicked) while parked: resume roaming."""
        _APP.settings["pet_parked"] = False
        save_settings(_APP.settings)
        info("pet", "Pet unparked - roaming resumed")
        return {"ok": True}

    def pet_interaction(self, kind):
        if kind == "click":
            info("pet", "Pet clicked - happy reaction (مياو!)")
        return {"ok": True}

    # -- in-app updates (v1.2) ---------------------------------------------
    def check_updates(self):
        return updater.check_update()

    def install_update(self):
        info("update", "Install requested from Settings")
        def _run():
            if getattr(sys, "frozen", False):
                # Installed exe: a source zip cannot replace the frozen exe,
                # so the update comes as the new setup.exe (silent install).
                if updater.install_setup():
                    info("update", "Setup update installed - restarting Memo AI")
                    _APP.quit()
                return
            if updater.install_update():
                info("update", "Update installed - restarting Memo AI")
                time.sleep(0.5)
                try:
                    subprocess.Popen([sys.executable, str(APP_DIR / "main.py")],
                                     cwd=str(BASE_DIR))
                except Exception as exc:
                    error("update", f"Could not restart after update: {exc}")
                    return
                _APP.quit()
        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True, "installing": True}

    def move_pet(self, x, y):
        try:
            _APP.pet_window.move(int(x), int(y))
        except Exception as exc:
            error("ui", f"Could not move pet window: {exc}")
        return {"ok": True}

    def open_portfolio(self):
        webbrowser.open("https://mohamed5946.github.io/portfolio/")
        return {"ok": True}

    def quit_app(self):
        info("app", "Quit requested from inside the app")
        threading.Thread(target=_APP.quit, daemon=True).start()
        return {"ok": True}

    # -- logs --------------------------------------------------------------
    def log_js(self, message):
        # Browser-engine probe: the UI pages send navigator.userAgent on
        # load so the log shows which engine draws the windows (WebView2
        # UA has "Edg/" + "Chrome/"; MSHTML UA has "MSIE"/"Trident/").
        # A user-agent string carries no secrets.
        try:
            info("ui", f"JS: {str(message)[:500]}")
        except Exception:
            pass
        return {"ok": True}

    def get_logs(self):
        return {"logs": read_recent(200)}

    def get_log_text(self):
        return {"text": log_text(200)}


class MemoApp:
    def __init__(self):
        global _APP
        _APP = self
        self.settings = load_settings()
        self.api = Api()
        self.main_window = None
        self.island_window = None
        self.pet_window = None
        self._quitting = False
        self._briefed = False
        # v1.3.0: when a tool waits for Allow/Deny, bring the main window
        # forward so the user sees the dialog.
        tools.on_permission_needed = self._show_main_for_permission

    # -- window management ---------------------------------------------------
    def show_main(self):
        if self.main_window:
            self.main_window.show()
            if self.island_window:
                self.island_window.hide()
        info("ui", "Main window opened from island")

    def hide_main(self):
        if self.main_window:
            self.main_window.hide()
        if self.island_window:
            self.island_window.show()
            self.island_window.restore()
        info("ui", "Main window minimized back to island")

    def apply_settings(self):
        self.apply_pet_visibility()
        try:
            if self.main_window:
                self.main_window.evaluate_js("applySettingsFromPython()")
        except Exception:
            pass

    def apply_pet_visibility(self):
        if not self.pet_window:
            return
        try:
            if self.settings.get("pet_visible", True):
                self.pet_window.show()
            else:
                self.pet_window.hide()
        except Exception as exc:
            error("ui", f"Could not toggle pet window: {exc}")

    def park_pet(self):
        if not self.pet_window:
            return
        try:
            sw, sh = screen_size()
            self.pet_window.move(sw - PET_SIZE - 20, sh - PET_SIZE - 90)
            # Tell the pet page to switch to the parked pose + ball.
            try:
                self.pet_window.evaluate_js("setParked(true, true)")
            except Exception:
                pass
            info("ui", "Pet parked in bottom-right corner")
        except Exception as exc:
            error("ui", f"Could not park pet: {exc}")

    def run_tool_chat(self, chat_id: str, text: str):
        """Background thread for an owner-mode (tool) chat. Never runs on
        the JS bridge thread: tools may wait on an Allow/Deny dialog that
        only the bridge can resolve."""
        try:
            result = brain.ask_with_tools(
                text, self.settings.get("ai_name", "Memo"),
                self.settings.get("user_name", "Mohamed"),
                self.settings.get("language", "ar"))
        except Exception as exc:
            error("brain", f"Tool chat failed: {exc}")
            result = {"reply": "حصلت مشكلة وأنا بنفذ، جرّب تاني.",
                      "engine": "none", "tools_used": 0}
        with _chats_lock:
            entry = _chats.get(chat_id)
            if entry is not None:
                entry.update({"done": True, "reply": result["reply"],
                              "engine": result["engine"],
                              "tools_used": result.get("tools_used", 0)})
        self.speak_async(result["reply"])

    def _show_main_for_permission(self):
        """Permission hook (tools.on_permission_needed): a tool is waiting
        for Allow/Deny, so bring the main window forward - the dialog
        lives there, even if the user was talking through the island."""
        try:
            self.show_main()
        except Exception as exc:
            warn("tools", f"Could not bring main window forward: {exc}")

    def speak_async(self, text: str):
        # v1.3.0 voice-output toggle: the SINGLE choke point for all
        # spoken replies (chat, rules, morning briefing). When OFF, no
        # TTS engine is called at all - no Gemini/Azure/Piper API use,
        # not just muting after synthesis.
        if not self.settings.get("voice_output", True):
            info("speak", "Voice output is OFF - reply stays text-only "
                          "(no TTS call)")
            return
        def _run():
            online = brain.is_online()
            result = speak.speak(text, self.settings.get("voice", "girl_warm"), online)
            if result["engine"] != "none" and result["duration"] > 0:
                try:
                    if self.main_window:
                        self.main_window.evaluate_js("setSpeaking(true)")
                    time.sleep(min(result["duration"], 30))
                    if self.main_window:
                        self.main_window.evaluate_js("setSpeaking(false)")
                except Exception:
                    pass
        threading.Thread(target=_run, daemon=True).start()

    def morning_briefing(self):
        """Speak greeting + weather once per startup (if enabled)."""
        if self._briefed:
            return
        self._briefed = True
        if not self.settings.get("greeting_enabled", True):
            return
        time.sleep(1.5)
        hour = datetime.now().hour
        name = self.settings.get("user_name", "Mohamed")
        lang = self.settings.get("language", "ar")
        if lang == "ar":
            if 5 <= hour < 12:
                greet = f"صباح الخير يا {name}"
            elif 12 <= hour < 18:
                greet = f"مساء الخير يا {name}"
            else:
                greet = f"مساء الخير يا {name}"
        else:
            greet = (f"Good morning, {name}" if 5 <= hour < 12
                     else f"Good evening, {name}")
        w = weather.get_weather()
        if w:
            if lang == "ar":
                greet += f" — الجو النهاردة {w['desc']}، {w['temp']} درجة"
            else:
                greet += f" — {w['temp']}°C, {w['desc']}"
        # TODO(v2): add Gmail important mail + calendar + reminders here.
        # (Needs Google account connection - not in v1.)
        try:
            if self.island_window:
                self.island_window.evaluate_js(
                    f"showIslandText({json.dumps(greet, ensure_ascii=False)})")
        except Exception:
            pass
        info("app", f"Morning briefing: {greet}")
        self.speak_async(greet)

    def quit(self):
        self._quitting = True
        speak.stop()
        try:
            if self.pet_window:
                self.pet_window.destroy()
        except Exception:
            pass
        try:
            if self.island_window:
                self.island_window.destroy()
        except Exception:
            pass
        try:
            if self.main_window:
                self.main_window.destroy()
        except Exception:
            pass
        # Release the single-instance lock (the OS would do it on exit
        # anyway; closing explicitly keeps the intent clear).
        global _mutex_handle
        try:
            if _mutex_handle:
                ctypes.windll.kernel32.CloseHandle(_mutex_handle)
                _mutex_handle = None
        except Exception:
            pass

    # -- startup -------------------------------------------------------------
    def _on_main_closing(self):
        # X button / taskbar close = minimize to island, NOT quit.
        if self._quitting:
            return True
        self.hide_main()
        return False

    def run(self):
        info("app", f"Memo AI v{VERSION} starting")
        try:
            pv = getattr(webview, "__version__", "unknown")
        except Exception:
            pv = "unknown"
        info("app", f"pywebview version: {pv}")
        # Bridge-files self-check (v1.2.5): loud log if api.js is missing.
        _check_bridge_files()
        # One instance only: a second launch (zip or installed) exits
        # quietly BEFORE any window is created.
        if not _acquire_single_instance_lock():
            info("app", "Another Memo AI instance is already running - exiting")
            return
        sw, sh = screen_size()
        # The island starts in its IDLE compact state (v1.2.3).
        island_x = max(0, (sw - ISLAND_IDLE_W) // 2)

        ui_dir = APP_DIR / "ui"
        self.island_window = webview.create_window(
            "Memo AI", str(ui_dir / "island.html"),
            width=ISLAND_IDLE_W, height=ISLAND_IDLE_H, x=island_x, y=12,
            frameless=True, transparent=False, on_top=True,
            resizable=False, background_color=CHROMA_KEY_HEX, js_api=self.api)
        self.main_window = webview.create_window(
            "Memo AI", str(ui_dir / "index.html"),
            width=MAIN_W, height=MAIN_H, hidden=True,
            min_size=(640, 520), js_api=self.api)
        self.main_window.events.closing += self._on_main_closing
        self.pet_window = webview.create_window(
            "Memo AI Pet", str(ui_dir / "pet.html"),
            width=PET_SIZE, height=PET_SIZE,
            x=sw - PET_SIZE - 20, y=sh - PET_SIZE - 90,
            frameless=True, transparent=False, on_top=True,
            resizable=False, background_color=CHROMA_KEY_HEX, js_api=self.api)
        if not self.settings.get("pet_visible", True):
            self.pet_window.hide()

        threading.Thread(target=self.morning_briefing, daemon=True).start()
        info("app", "Island started - full window opens only on tap")
        # v1.3.0: webview.start(func) runs func in a thread BEFORE any
        # window exists, so _apply_chroma_keys polls until the HWNDs
        # appear (up to 60s) and then keys out the magenta background.
        webview.start(self._apply_chroma_keys)
        info("app", "Memo AI exited")

    def _apply_chroma_keys(self):
        """Apply the Win32 chroma-key (magenta -> transparent) to the
        island + pet windows. Runs in the thread webview.start(func)
        spawns, which starts BEFORE the windows are created - hence the
        polling. Each window is attempted once its HWND is found; the
        style lives on the HWND so it survives hide/show. Every outcome
        is logged (diagnostics, visible in-app)."""
        pending = {"island": True, "pet": True}
        deadline = time.time() + 60
        while time.time() < deadline and any(pending.values()):
            if pending["island"]:
                hwnd = _window_hwnd(self.island_window) or \
                    _find_hwnd_by_title("Memo AI", max_width=400)
                if hwnd:
                    _apply_chroma_key(hwnd, "island")
                    pending["island"] = False
            if pending["pet"]:
                hwnd = _window_hwnd(self.pet_window) or \
                    _find_hwnd_by_title("Memo AI Pet")
                if hwnd:
                    _apply_chroma_key(hwnd, "pet")
                    pending["pet"] = False
            if any(pending.values()):
                time.sleep(0.5)
        for label, still_pending in pending.items():
            if still_pending:
                warn("app", f"Chroma-key NOT applied to {label} (no HWND "
                            f"found in 60s) - black rectangle may remain")


def main():
    MemoApp().run()


if __name__ == "__main__":
    main()
