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
# v1.2.0 backdrop-filter removal was treating a symptom.
#
# v1.3.0's chroma-key ATTEMPT (magenta page + WS_EX_LAYERED +
# SetLayeredWindowAttributes on the Form HWND) FAILED IN THE FIELD
# (2026-11-07, user log + screenshots) and is DELETED in v1.3.1 - see
# app/winshape.py for the full post-mortem. Short version: the color
# key was applied to the top-level Form, but the visible pixels are
# painted by the WebView2 CHILD HWND, which the key never touches - so
# the magenta stayed - AND the Form's whole surface is magenta, so
# hit-testing returned HTTRANSPARENT for every point and BOTH windows
# went fully click-through (pill click + cat click dead). One mechanism,
# both symptoms. The log even claimed "applied" while the screen proved
# otherwise - v1.3.1 never logs success without verification.
#
# v1.3.1 TRANSPARENCY (2026-11-07): SetWindowRgn (app/winshape.py).
# A window region clips the window AND all its children to an exact
# shape, and hit-testing follows the region: the island is a rounded
# rect matching the pill, the pet is the cat's PNG alpha silhouette
# (unioned with the bubble/ball zones only while visible). No box of
# any color on any wallpaper, and clicks land on the pill/cat while
# passing through everywhere else. FIELD TEST STILL REQUIRED (cannot be
# proven from Linux): screenshots over a light AND a dark desktop
# wallpaper, pill click opens the main window, cat click says "مياو!",
# clicks pass through the empty space around the cat.

# v1.2 TRANSPARENCY (2026-11-06) - SUPERSEDED twice. Kept as history:
# the island/pet showed a white rectangle. The v1.2 theory (MSHTML
# fallback paints background_color white) was wrong on the user's
# machine - the v1.2.5 log proved WebView2 WAS the engine, yet the black
# rectangle stayed. Real cause: see the v1.3.0 note above (and the
# v1.3.1 region fix in app/winshape.py).
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
# itself - the v1.3.0 investigation proved it was never WebView2;
# fixed in v1.3.1 by window regions, see app/winshape.py.)
# Implementation: the .island div is 100% of the window, and the WINDOW
# animates in small steps via rAF -> the new Api.island_resize(w, h)
# (which also re-centers it on top); the texts
# fade in as it grows. Old island_expand/island_shrink removed.
#
# v1.3.2 (2026-11-07) - ONE BATCHED VERSION ("version واحدة"):
#   (1) HANG FIX (field: v1.3.1 first launch "Not Responding" 09:55):
#       the morph loop was already bounded (28 steps) - the real risks
#       were cross-thread .NET touches: _window_hwnd() read
#       window.native.Handle from the region-polling thread (WinForms
#       creates a Form's handle ON THE CALLING THREAD if it does not
#       exist yet - the GUI thread then owns a handle it did not create
#       -> frozen window), and _animate_window_rect touched
#       Form.MinimumSize from the morph thread. BOTH ARE DELETED:
#       HWND lookup is EnumWindows-only now (never .native/.Handle),
#       and the morph never touches .NET properties - the main window is
#       created with min_size=(190,52) so it can shrink into the pill
#       without any MinimumSize juggling (trade-off: the user can
#       resize the main window smaller than 640x520; the flex layout
#       still works, just cramped). The other hang vector was the
#       install itself: v1.3.1 was installed OVER a running v1.3.0
#       (mixed old/new files) - the installer now refuses while the app
#       runs (AppMutex + CloseApplications in memo-ai.iss).
#   (2) TOOLS FOR EVERYONE: v1.3.0 gated the tool loop behind the
#       secret owner code, so in NORMAL chat Groq got no tools at all
#       and refused "open VS Code" in text. The user IS the owner of his
#       own laptop - Api.chat now ALWAYS runs brain.ask_with_tools in a
#       background thread (Allow/Deny still gates every action). The
#       owner code now only bypasses the source-code rule. The tools
#       prompt also stopped telling the model to "explain and wait for
#       permission" (that taught it to ASK instead of calling the tool).
#   (3) NAME BUG: the system prompt only said "on {user_name}'s
#       laptop" - the brain answered "I don't know your name". The name
#       is now stated explicitly, and the prompt carries the real
#       capability state (see capabilities.py) so the brain explains
#       WHAT is missing and HOW to connect it instead of refusing
#       flatly ("مش هقدر أساعدك" is forbidden phrasing now).
#   (4) BLACK BOX = PET WINDOW: pet.html roamed unconditionally when
#       the bridge was not ready yet, and pywebview's window.move()
#       uses SetWindowPos(SW_SHOWWINDOW) - it SHOWS a hidden window.
#       So the hidden pet got shown and roamed while rendering black
#       (region never applied / page not painting). Fixed three ways:
#       Api.move_pet ignores moves while the pet is hidden; pet.html
#       only roams when settings confirm it is visible (unconditional
#       fallbacks deleted) and suspends roaming when hidden; hiding the
#       pet now also calls suspendRoaming() from Python.
#   (5) PET DRAG: same root causes as (4) - a non-painting page has no
#       working drag handlers. With the page actually painting + the
#       region verified, drag works; the drag path is now instrumented
#       (pointerdown/move logs) so the diagnostics log proves it.
#   (6) STARTUP SELF-TEST: page/asset/api.js/WebView2-version checks
#       at startup + a bridge-ready watchdog (each page logs READY on
#       pywebviewready; after 20s the log names any page that never
#       became ready, with the exact reason - no more generic "install
#       WebView2" advice when WebView2 is fine).
#   (7) CAT FLIES TO HEADER + BIGGER CHAT (app.js/styles.css): on the
#       first message the hero cat flies (eased) to the small header
#       avatar and the hero collapses, so the chat takes the freed space.
#   (8) WhatsApp linking is NOT in v1.3.2 (v1.4.0, separate build,
#       branch feat/whatsapp) - but the brain now answers truthfully
#       about it ("being built, links via QR with a 3rd number") instead
#       of a flat refusal; see app/capabilities.py.
#   (1) ISLAND CLICK FIX (critical regression): in v1.3.0 the island
#       click stopped opening the main window - and the pet click died
#       too. Cause: the chroma-key (see above) made both windows fully
#       click-through. Fix: window regions (winshape) + the chroma-key
#       code deleted outright. Clicks are instrumented (JS logs the
#       pill/pet pointerdown, Python logs the HWND/class/styles).
#   (2) REAL TRANSPARENCY: SetWindowRgn - pill roundrect + cat alpha
#       silhouette. No box of any color on any wallpaper.
#   (3) UPDATER PROGRESS BAR: updater.py tracks downloaded/total/% and
#       the Settings UI polls it; manifest fetch has a cache-buster
#       (checks at 01:02/01:06 returned a stale "already latest").
#   (4) AUTOSTART on Windows boot (HKCU Run key, toggleable, default
#       ON - see app/autostart.py). Starts as the island only.
#   (5) MORPH ANIMATION main window <-> island (~0.45s eased): closing
#       shrinks the main window into the pill; opening grows it out.
#   (6) SECOND LAUNCH FOCUSES the running instance (named event) instead
#       of exiting silently - that silence made the user think the app
#       would not open at all (8 launches 01:13-01:14).
#   (7) PET RUNNING ANIMATION: while roaming the cat runs with visible
#       legs (6-frame sprite, step rate synced to the roam speed) instead
#       of sliding like a robot; parked/sitting pose unchanged.
#   (8) PET HIDDEN BY DEFAULT: the cat never appears on its own - only
#       when the user shows it (Settings toggle / paw button); the state
#       persists. One-time migration applies the new default to existing
#       installs (see _apply_pet_default_migration).

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
import winshape
import autostart

VERSION = "1.3.2"
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
# in any comment. Typing it in chat unlocks owner mode (v1.3.2: owner
# mode now only bypasses the source-code rule - tools are on for every
# chat, see Api.chat; the Allow/Deny dialogs still apply in owner mode).

# Owner-mode code, SHA-256 hash ONLY (see note above). Compared with
# hmac.compare_digest; the plaintext value exists nowhere in the repo.
OWNER_CODE_HASH = "900e5e36492a56eed142c11368b25b677487dec61cea264a0e4a1f64edebaa47"

# Async tool-chat results: chat_id -> {"done", "reply", "engine", ...}.
_chats: dict = {}
_chats_lock = threading.Lock()

# Result of the startup bridge-files self-check (None = not run yet).
_BRIDGE_FILES_OK = None

# v1.3.1: the window backgrounds behind the WebView2 controls. With
# window regions (winshape) these colors are invisible - the region
# clips everything outside the pill/cat silhouette - but if a region
# ever failed to apply, the fallback is a dark color, never magenta.
ISLAND_BG = "#0D121C"
PET_BG = "#0A0A0A"


def _find_hwnd_by_title(title: str, max_width: int | None = None):
    """Fallback HWND lookup (EnumWindows): first top-level window whose
    title matches exactly. max_width distinguishes the small island
    ("Memo AI", <=400px) from the big main window (same title).

    v1.3.2: this is the ONLY HWND lookup now. The old _window_hwnd()
    read window.native.Handle from background threads - WinForms creates
    a Form's handle ON THE CALLING THREAD when it does not exist yet,
    so a polling thread could steal handle creation from the GUI thread
    and freeze the whole window ("Not Responding", v1.3.1 first launch).
    EnumWindows only ever reads HWNDs the GUI thread already created,
    so it can never do that. Never bring .native/.Handle back here."""
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


def _find_main_hwnd():
    """HWND of the MAIN window: title "Memo AI" (shared with the
    island), picked as the WIDEST match (main is 960px, island <=400px).
    EnumWindows-only (v1.3.2 hang fix - never .native/.Handle)."""
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
            if buf.value == "Memo AI":
                rect = RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                found.append((rect.right - rect.left, hwnd))
            return True

        user32.EnumWindows(_enum, None)
        if not found:
            return None
        found.sort(reverse=True)
        return found[0][1]
    except Exception:
        return None


def _check_bridge_files() -> bool:
    """Loud startup self-check: pywebview builds window.pywebview from its
    js/ files (util.load_js_files -> get_js_dir -> <webview>/js/api.js).
    If they are missing from the bundle, the bridge never exists and every
    button silently does nothing — the exact failure the user reported as
    preview mode + dead buttons. Never fail silently: log a LOUD error.
    Returns True when api.js is present. A failure here never blocks
    startup (the app still runs; the log + the UI message say why the
    buttons are dead).

    v1.3.2: extended into the full STARTUP SELF-TEST - page files,
    assets, settings, WebView2 version are all checked and logged with
    their exact result, so a first-launch failure names its cause
    instead of the generic 'install WebView2' advice (WebView2 was
    FINE on the user's machine in v1.3.1 - the real causes were mixed
    install files + a bridge that never became ready)."""
    global _BRIDGE_FILES_OK
    ok = True
    # 1. UI pages + assets (a missing file = a window that never paints).
    ui_dir = APP_DIR / "ui"
    for rel in ("index.html", "island.html", "pet.html",
                "assets/avatar-cat.png", "assets/cat-run.png",
                "app.js", "styles.css"):
        p = ui_dir / rel
        if p.is_file():
            info("selftest", f"UI file present: ui/{rel}")
        else:
            error("selftest", f"UI file MISSING: ui/{rel} - the window "
                              "that needs it will not paint")
            ok = False
    # 2. Settings load.
    try:
        from settings_store import load_settings as _ls
        _ls()
        info("selftest", "settings.json loads OK")
    except Exception as exc:
        error("selftest", f"settings.json FAILED to load: {exc}")
        ok = False
    # 3. Keys (presence only - never values).
    ks = keys_status()
    info("selftest", f"Keys present: groq={ks['groq']} "
                     f"gemini={ks['gemini']} azure={ks['azure']}")
    if not ks["groq"]:
        warn("selftest", "No Groq key in keys/groq-key.txt - online brain "
                         "unavailable, local Qwen 3B only")
    # 4. WebView2 runtime version (Windows registry; informational -
    #    pywebview falls back to MSHTML without it, which cannot do
    #    transparency and paints rectangles).
    if sys.platform == "win32":
        ver = _webview2_version()
        if ver:
            info("selftest", f"WebView2 runtime version: {ver}")
        else:
            warn("selftest", "WebView2 runtime NOT FOUND in the registry - "
                             "pywebview will fall back to MSHTML; install "
                             "WebView2 from https://go.microsoft.com/fwlink/"
                             "p/?LinkId=2124703")
    # 5. pywebview JS bridge files.
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
    _BRIDGE_FILES_OK = ok
    if ok:
        info("selftest", "Startup self-test PASSED (files + keys + WebView2)")
    else:
        warn("selftest", "Startup self-test found problems above - see the "
                         "error lines for the exact cause")
    return ok


def _webview2_version() -> str:
    """WebView2 Evergreen runtime version from the registry, or ''."""
    if sys.platform != "win32":
        return ""
    try:
        import winreg
        guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        path = f"SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\{guid}"
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root, path) as key:
                    val, _ = winreg.QueryValueEx(key, "pv")
                    if val:
                        return str(val)
            except OSError:
                continue
    except Exception:
        pass
    return ""


# v1.3.2 bridge-ready watchdog: each UI page logs "READY <page>" on
# pywebviewready (see app.js / island.html / pet.html). If a page never
# reports ready within 20s of startup, the log names it loudly with the
# exact cause instead of the generic WebView2 advice.
_READY_PAGES: set = set()
_READY_LOCK = threading.Lock()


def _note_bridge_ready(page: str) -> None:
    with _READY_LOCK:
        _READY_PAGES.add(page)


def _bridge_ready_watchdog() -> None:
    """Wait up to 20s for all three pages to report READY, then log the
    outcome. Runs on a daemon thread; never blocks startup."""
    deadline = time.time() + 20
    want = {"main", "island", "pet"}
    while time.time() < deadline:
        with _READY_LOCK:
            if want <= _READY_PAGES:
                break
        time.sleep(0.5)
    with _READY_LOCK:
        got = set(_READY_PAGES)
    missing = want - got
    if not missing:
        info("selftest", "Bridge READY on all pages: main + island + pet")
    else:
        error("selftest", "Bridge NOT READY after 20s - pages missing: "
              f"{', '.join(sorted(missing))}. The page file may be missing "
              "from the bundle (see the self-test lines above), the "
              "WebView2 control may have failed to start, or the install "
              "may have mixed old/new files - reinstall cleanly (close "
              "Memo AI first) and check the log.")


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
        with _READY_LOCK:
            ready = sorted(_READY_PAGES)
        return {
            "online": online,
            "version": VERSION,
            "bridge_files_missing": _BRIDGE_FILES_OK is False,
            "bridge_ready": ready,
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
                    "pet_visible", "pet_parked", "autostart"):
            if key in data:
                _APP.settings[key] = data[key]
        ok = save_settings(_APP.settings)
        info("app", "Settings saved from UI")
        # v1.3.1: autostart applies immediately (HKCU Run key follows
        # the setting; the pet toggle takes effect at once too).
        autostart.set_enabled(bool(_APP.settings.get("autostart", True)))
        _APP.apply_settings()
        return {"ok": ok}

    # -- chat --------------------------------------------------------------
    def chat(self, text):
        text = (text or "").strip()
        if not text:
            return {"reply": "", "engine": "none"}
        # Owner code unlocks owner mode (v1.3.0: compared as a SHA-256 hash
        # only - the plaintext code is nowhere in the repo, settings, or
        # logs, and the typed text is never echoed back). v1.3.2: owner
        # mode now only bypasses the source-code rule (tools are on for
        # every chat - see below); without it the app stays reply-only
        # for source-code questions.
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
        # v1.3.2: EVERY chat runs the tool loop, in a BACKGROUND thread.
        # v1.3.0 gated tools behind the owner code, so normal chat had no
        # tools at all and Groq refused "open VS Code" in text. The user
        # IS the owner of his own laptop; Allow/Deny still gates every
        # action. The JS bridge must never block waiting for a permission,
        # or the Allow/Deny dialog could never answer (deadlock) - and a
        # synchronous Groq call on the bridge thread would freeze the UI
        # ("Not Responding") for the whole reply. The UI polls
        # chat_result() + get_pending_permission() meanwhile.
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
        # v1.3.1: the window REGION follows the size - a resized window
        # keeps its old region, so re-apply it on every resize step.
        try:
            if _APP.island_window:
                sw, _sh = screen_size()
                _APP.island_window.resize(int(w), int(h))
                _APP.island_window.move(max(0, (sw - int(w)) // 2), 12)
                _APP.refresh_island_region(int(w), int(h))
        except Exception as exc:
            error("ui", f"Could not resize island: {exc}")
        return {"ok": True}

    def minimize_to_island(self):
        _APP.hide_main()
        return {"ok": True}

    def toggle_pet(self, visible=None):
        if visible is None:
            visible = not _APP.settings.get("pet_visible", False)
        _APP.settings["pet_visible"] = bool(visible)
        save_settings(_APP.settings)
        _APP.apply_pet_visibility()
        return {"visible": bool(visible)}

    def set_pet_bubble(self, on):
        """The pet page shows/hides the "مياو!" bubble: the window region
        must include the bubble zone only while it is visible (v1.3.1)."""
        _APP.set_pet_bubble(bool(on))
        return {"ok": True}

    def park_pet(self):
        _APP.settings["pet_parked"] = True
        save_settings(_APP.settings)
        _APP.park_pet()
        return {"ok": True}

    def mark_parked(self):
        """Pet was dragged to a screen edge: save parked state (no move)."""
        _APP.settings["pet_parked"] = True
        save_settings(_APP.settings)
        _APP.refresh_pet_region()  # ball zone joins the region
        info("pet", "Pet parked at a screen edge - playing with the ball")
        return {"ok": True}

    def unpark_pet(self):
        """Pet dragged away (or double-clicked) while parked: resume roaming."""
        _APP.settings["pet_parked"] = False
        save_settings(_APP.settings)
        _APP.refresh_pet_region()  # ball zone leaves the region
        info("pet", "Pet unparked - roaming resumed")
        return {"ok": True}

    def pet_interaction(self, kind):
        if kind == "click":
            info("pet", "Pet clicked - happy reaction (مياو!)")
        return {"ok": True}

    # -- in-app updates (v1.2) ---------------------------------------------
    def check_updates(self):
        return updater.check_update()

    def get_update_progress(self):
        """v1.3.1: the Settings UI polls this while an update downloads
        (downloaded/total/percent/state) to render the progress bar."""
        return updater.get_progress()

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
        # v1.3.2 BLACK-BOX FIX: pywebview's window.move() uses
        # SetWindowPos(SW_SHOWWINDOW) - it SHOWS a hidden window. The
        # hidden pet used to get shown by its own roam moves (the black
        # rectangle roaming the screen in v1.3.1). A hidden pet is NEVER
        # moved - the move is dropped and logged.
        if not _APP.settings.get("pet_visible", False):
            return {"ok": False, "hidden": True}
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
            msg = str(message)[:500]
            # v1.3.2 bridge-ready watchdog: pages log "READY <page>" on
            # pywebviewready so the log proves which bridges came up.
            if msg.startswith("READY "):
                _note_bridge_ready(msg.split(None, 1)[1].strip())
            info("ui", f"JS: {msg}")
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
        # v1.3.1 morph state: is the main window currently shown, and
        # what rect should it restore to (the user's last size/position)?
        self._main_visible = False
        self._main_rect = None            # (x, y, w, h) or None = default
        self._morph_lock = threading.Lock()
        # v1.3.1 window shaping: HWNDs once known + cached pet rects.
        self._island_hwnd = None
        self._pet_hwnd = None
        self._pet_region_rects = None     # built once from the cat PNG
        self._pet_bubble = False          # is the "مياو!" bubble showing?
        # v1.3.1 second-launch focus: named event handle + watcher.
        self._show_main_event = None

    # -- window management ---------------------------------------------------
    def _island_rect(self):
        """Current island rect (x, y, w, h). Prefers the real HWND rect;
        falls back to the idle pill centered at the top."""
        rect = winshape.get_window_rect(self._island_hwnd)
        if rect:
            return rect
        sw, _sh = screen_size()
        return (max(0, (sw - ISLAND_IDLE_W) // 2), 12,
                ISLAND_IDLE_W, ISLAND_IDLE_H)

    def _main_full_rect(self):
        """Rect the main window restores to (last size/position, or the
        centered default)."""
        if self._main_rect:
            return self._main_rect
        sw, sh = screen_size()
        return (max(0, (sw - MAIN_W) // 2), max(0, (sh - MAIN_H) // 2),
                MAIN_W, MAIN_H)

    def _animate_window_rect(self, window, from_rect, to_rect,
                             duration_ms: int = 450):
        """Morph a window's rect (x, y, w, h) with an eased ~0.45s
        animation. Runs on the calling thread (callers use a daemon
        thread). Any failure aborts the animation quietly - the caller
        always finishes with an explicit show/hide, so the window can
        never be left in a broken state. The loop is BOUNDED (fixed
        step count, ~28 frames) - it always terminates.
        v1.3.2: NO .NET property touches here anymore. The old code
        cleared/restored the Form's MinimumSize from this thread
        (cross-thread .NET access - one of the v1.3.1 hang suspects);
        the main window is now created with min_size=(190,52) so the
        morph can shrink into the pill without any juggling. The
        move/resize calls go through pywebview (SetWindowPos - safe
        from any thread once the handle exists)."""
        if not window or self._quitting:
            return
        try:
            steps = max(1, int(duration_ms / 16))
            for i in range(1, steps + 1):
                if self._quitting:
                    return
                t = winshape.ease_in_out_cubic(i / steps)
                x, y, w, h = winshape.lerp_rect(from_rect, to_rect, t)
                try:
                    window.move(int(x), int(y))
                    window.resize(int(w), int(h))
                except Exception:
                    return
                time.sleep(0.016)
        except Exception:
            return

    def show_main(self):
        """Open the main window out of the island pill (v1.3.1 morph:
        it grows from the pill to full size in ~0.45s). If it is already
        open, just bring it forward."""
        if not self.main_window:
            return
        if self._main_visible:
            try:
                self.main_window.show()
            except Exception:
                pass
            return
        self._main_visible = True
        island_rect = self._island_rect()
        full_rect = self._main_full_rect()

        def _open():
            # Wait for a close-morph in flight (max ~0.45s) instead of
            # racing it: the loser used to leave the window at pill size.
            if not self._morph_lock.acquire(timeout=2):
                try:
                    self.main_window.show()
                except Exception:
                    pass
                if self.island_window:
                    try:
                        self.island_window.hide()
                    except Exception:
                        pass
                return
            try:
                try:
                    self.main_window.move(island_rect[0], island_rect[1])
                    self.main_window.resize(island_rect[2], island_rect[3])
                except Exception:
                    pass
                try:
                    self.main_window.show()
                except Exception:
                    pass
                if self.island_window:
                    try:
                        self.island_window.hide()
                    except Exception:
                        pass
                self._animate_window_rect(
                    self.main_window, island_rect, full_rect)
                self._main_rect = full_rect
                if not self._main_visible:
                    # Closed again mid-animation: leave it hidden.
                    try:
                        self.main_window.hide()
                    except Exception:
                        pass
            finally:
                self._morph_lock.release()

        threading.Thread(target=_open, daemon=True).start()
        info("ui", "Main window opened from island")

    def hide_main(self):
        """Close the main window back into the island pill (v1.3.1 morph:
        it shrinks into the pill in ~0.45s, then the island appears)."""
        if not self.main_window or not self._main_visible:
            return
        self._main_visible = False
        # Remember the rect to restore to (exact, from the OS).
        rect = winshape.get_window_rect(_find_main_hwnd())
        if rect:
            self._main_rect = rect
        island_rect = self._island_rect()
        start_rect = self._main_rect

        def _close():
            # Wait for an open-morph in flight (max ~0.45s) instead of
            # racing it, so the shrink always starts from the full rect.
            if not self._morph_lock.acquire(timeout=2):
                try:
                    self.main_window.hide()
                except Exception:
                    pass
                if self.island_window:
                    try:
                        self.island_window.show()
                    except Exception:
                        pass
                return
            try:
                self._animate_window_rect(
                    self.main_window, start_rect, island_rect)
            finally:
                self._morph_lock.release()
            if self._main_visible:
                return  # re-opened mid-animation - leave it open
            try:
                self.main_window.hide()
            except Exception:
                pass
            if self.island_window:
                try:
                    self.island_window.show()
                    self.island_window.restore()
                except Exception:
                    pass

        threading.Thread(target=_close, daemon=True).start()
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
            if self.settings.get("pet_visible", False):
                self.pet_window.show()
                self.refresh_pet_region()
                # The pet page skips roaming while hidden; wake it up.
                try:
                    self.pet_window.evaluate_js("resumeRoaming()")
                except Exception:
                    pass
            else:
                # v1.3.2: stop the roam timers too - a hidden page keeps
                # running JS, and its roam moves used to SHOW the hidden
                # window (black box). suspendRoaming() cancels everything.
                try:
                    self.pet_window.evaluate_js("suspendRoaming()")
                except Exception:
                    pass
                self.pet_window.hide()
        except Exception as exc:
            error("ui", f"Could not toggle pet window: {exc}")

    def set_pet_bubble(self, on: bool):
        """The "مياو!" bubble is showing/hiding: the window region must
        include its zone only while visible (v1.3.1)."""
        self._pet_bubble = bool(on)
        self.refresh_pet_region()

    def refresh_island_region(self, w: int, h: int):
        """Re-apply the pill roundrect region for the island's current
        size (called on every island_resize step). No-op off Windows or
        before the HWND is known."""
        if not self._island_hwnd:
            return
        try:
            winshape.apply_round_rect_region(
                self._island_hwnd, int(w), int(h),
                winshape.island_radius(int(w), int(h)), "island")
        except Exception as exc:
            error("app", f"Island region refresh failed: {exc}")

    def refresh_pet_region(self):
        """Re-apply the pet silhouette region for the current state
        (parked -> ball zone joins; bubble showing -> bubble zone joins).
        No-op off Windows or before the HWND/rects are known."""
        if not self._pet_hwnd or not self._pet_region_rects:
            return
        try:
            roundrects = []
            if self._pet_bubble:
                roundrects.append(winshape.PET_BUBBLE_ZONE)
            if self.settings.get("pet_parked", False):
                roundrects.append(winshape.PET_BALL_ZONE)
            winshape.apply_rect_list_region(
                self._pet_hwnd, self._pet_region_rects, roundrects, "pet")
        except Exception as exc:
            error("app", f"Pet region refresh failed: {exc}")

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
            self.refresh_pet_region()  # ball zone joins the region
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
        # Close the second-launch event (the watcher thread exits on
        # its next poll once _quitting is True).
        try:
            if self._show_main_event:
                ctypes.windll.kernel32.CloseHandle(self._show_main_event)
                self._show_main_event = None
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

    def _apply_pet_default_migration(self):
        """v1.3.1 one-time migration: existing installs saved
        pet_visible=True, but the new rule is hidden-by-default (the cat
        only ever appears when the user shows it). Applied once, then
        the user's own choice rules from then on."""
        if self.settings.get("pet_default_v131", False):
            return
        self.settings["pet_visible"] = False
        self.settings["pet_default_v131"] = True
        save_settings(self.settings)
        info("app", "Pet default applied (v1.3.1 migration): pet hidden "
                    "until the user shows it")

    def run(self):
        info("app", f"Memo AI v{VERSION} starting")
        try:
            pv = getattr(webview, "__version__", "unknown")
        except Exception:
            pv = "unknown"
        info("app", f"pywebview version: {pv}")
        # Bridge-files self-check (v1.2.5): loud log if api.js is missing.
        _check_bridge_files()
        # v1.3.1: pet hidden-by-default migration (one-time) + autostart
        # registry follows the setting (default ON).
        self._apply_pet_default_migration()
        autostart.apply_on_startup(self.settings.get("autostart", True))
        # v1.3.1 second-launch focus: create the named event FIRST, so a
        # second instance can signal the running one to show its window
        # instead of exiting silently (which made the user think the app
        # would not open at all).
        self._show_main_event = winshape.create_show_main_event()
        # One instance only: a second launch (zip or installed) signals
        # the running instance and exits BEFORE any window is created.
        if not _acquire_single_instance_lock():
            if winshape.signal_show_main():
                info("app", "Another Memo AI instance is already running - "
                            "signaled it to show the main window")
            else:
                info("app", "Another Memo AI instance is already running - "
                            "exiting (could not signal it)")
            return
        threading.Thread(
            target=winshape.watch_show_main_event,
            args=(self._show_main_event, self.show_main,
                  lambda: self._quitting),
            daemon=True).start()
        sw, sh = screen_size()
        # The island starts in its IDLE compact state (v1.2.3).
        island_x = max(0, (sw - ISLAND_IDLE_W) // 2)

        ui_dir = APP_DIR / "ui"
        self.island_window = webview.create_window(
            "Memo AI", str(ui_dir / "island.html"),
            width=ISLAND_IDLE_W, height=ISLAND_IDLE_H, x=island_x, y=12,
            frameless=True, transparent=False, on_top=True,
            resizable=False, background_color=ISLAND_BG, js_api=self.api)
        self.main_window = webview.create_window(
            "Memo AI", str(ui_dir / "index.html"),
            width=MAIN_W, height=MAIN_H, hidden=True,
            # v1.3.2: min_size=(190,52) so the morph can shrink the window
            # into the island pill WITHOUT touching Form.MinimumSize from
            # a background thread (v1.3.1 hang suspect). Trade-off: the
            # user can resize the main window smaller than 640x520; the
            # flex layout still works, just cramped.
            min_size=(190, 52), js_api=self.api)
        self.main_window.events.closing += self._on_main_closing
        # v1.3.1: the pet starts HIDDEN unless the user left it visible
        # (hidden= at creation avoids a flash of the cat at startup).
        self.pet_window = webview.create_window(
            "Memo AI Pet", str(ui_dir / "pet.html"),
            width=PET_SIZE, height=PET_SIZE,
            x=sw - PET_SIZE - 20, y=sh - PET_SIZE - 90,
            frameless=True, transparent=False, on_top=True,
            resizable=False, background_color=PET_BG, js_api=self.api,
            hidden=not self.settings.get("pet_visible", False))

        threading.Thread(target=self.morning_briefing, daemon=True).start()
        # v1.3.2: bridge-ready watchdog - each page logs READY on
        # pywebviewready; after 20s the log names any page that never
        # became ready, with the exact reason.
        threading.Thread(target=_bridge_ready_watchdog, daemon=True).start()
        info("app", "Island started - full window opens only on tap")
        # v1.3.1: webview.start(func) runs func in a thread BEFORE any
        # window exists, so _apply_window_regions polls until the HWNDs
        # appear (up to 60s) and then shapes the windows with regions.
        webview.start(self._apply_window_regions)
        info("app", "Memo AI exited")

    def _apply_window_regions(self):
        """Apply the Win32 window regions (v1.3.1, app/winshape.py) to
        the island + pet windows. Runs in the thread webview.start(func)
        spawns, which starts BEFORE the windows are created - hence the
        polling. Each window is shaped once its HWND is found; the
        region lives on the HWND so it survives hide/show. The island
        region is re-applied on every resize (Api.island_resize). Every
        outcome is logged, and success is only claimed after a read-back
        verification (see winshape)."""
        # The pet silhouette rects are built once from the bundled PNG.
        try:
            png = APP_DIR / "ui" / "assets" / "avatar-cat.png"
            self._pet_region_rects = winshape.build_pet_region_rects(png)
            if self._pet_region_rects:
                info("app", f"Pet region rects built from avatar-cat.png "
                            f"({len(self._pet_region_rects)} rects)")
            else:
                error("app", "Pet region rects NOT built - pet window "
                             "will stay rectangular until this is fixed")
        except Exception as exc:
            error("app", f"Pet region build failed: {exc}")
        pending = {"island": True, "pet": True}
        deadline = time.time() + 60
        while time.time() < deadline and any(pending.values()):
            if pending["island"]:
                # v1.3.2: EnumWindows ONLY - the old _window_hwnd() read
                # window.native.Handle from THIS thread (WinForms creates
                # a Form's handle on the calling thread -> frozen window).
                hwnd = _find_hwnd_by_title("Memo AI", max_width=400)
                if hwnd:
                    self._island_hwnd = hwnd
                    winshape.log_window("island", hwnd, "before region")
                    rect = winshape.get_window_rect(hwnd)
                    w, h = (rect[2], rect[3]) if rect else \
                        (ISLAND_IDLE_W, ISLAND_IDLE_H)
                    winshape.apply_round_rect_region(
                        hwnd, w, h, winshape.island_radius(w, h), "island")
                    winshape.log_window("island", hwnd, "after region")
                    pending["island"] = False
            if pending["pet"]:
                hwnd = _find_hwnd_by_title("Memo AI Pet")
                if hwnd:
                    self._pet_hwnd = hwnd
                    winshape.log_window("pet", hwnd, "before region")
                    self.refresh_pet_region()
                    winshape.log_window("pet", hwnd, "after region")
                    pending["pet"] = False
            if any(pending.values()):
                time.sleep(0.5)
        for label, still_pending in pending.items():
            if still_pending:
                warn("app", f"Window region NOT applied to {label} (no HWND "
                            f"found in 60s) - the window will stay a box")


def main():
    MemoApp().run()


if __name__ == "__main__":
    main()
