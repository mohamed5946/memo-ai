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
# v1.4.2 (2026-11-07) - ONE BATCHED VERSION ("version واحدة"):
#   (1) WHATSAPP NO-REPLY FIX (field log v1.4.1): three messages from
#       the owner's number were dropped as "unidentifiable sender" -
#       WhatsApp now sends the sender as a LID (@lid) instead of a
#       phone JID. Sender identification now resolves LIDs (persistent
#       LID->phone cache, learned owner LID, chat remoteJid fallback)
#       and logs the raw sender fields (masked) when it still fails.
#   (2) SIDECAR UTF-8 READER: the stdout reader died on one non-UTF-8
#       byte (0x81, Windows charmap) and no WhatsApp events arrived
#       after that - it now reads UTF-8 with errors='replace' and can
#       never die on a bad byte. Sidecar exits now log stderr + exit
#       code, with real backoff (5 attempts, then DEAD - surfaced in
#       Settings instead of a silent restart loop).
#   (3) SEND_WHATSAPP_MESSAGE tool: the brain had NO WhatsApp send
#       tool, so it could claim a send it never made - now a real tool
#       (owner number by default), success only after Baileys
#       confirms the send, same permission model as the other tools.
#   (4) LONG MEMORY (app/data/memory.json, local only): facts, style,
#       corrections (highest priority), routines; retrieved into the
#       system prompt each turn; history window 20 -> 40 with rollover
#       summaries; manageable in Settings (view / delete / wipe).
#   (5) WEB_SEARCH tool (ddgs, free, no API key): the brain searches
#       instead of guessing or saying "I don't know"; sources cited.
#   (6) SYSTEM MONITOR (app/monitor.py, psutil): RAM/disk/CPU/battery
#       + CPU temp when available (honestly "unavailable" otherwise);
#       alerts in-app + Windows toast with cooldown; thresholds tuned
#       to his 8GB machine (free RAM < 500MB or usage > 92%).
#   (7) IMPORTANT-NOTIFICATION ALERTS (app/notifications.py, winrt
#       UserNotificationListener): app list + keywords, configurable;
#       honestly "unavailable" when the listener is denied/absent.
#   (8) IN-CHAT PERMISSIONS: tool Allow/Deny is now a chat message
#       with سماح/رفض buttons (120s timeout -> auto-deny), not a
#       popup; destructive blocklist + trusted-owner auto-allow
#       unchanged. (9) UPDATER manifest fetch retries 3x on timeout.
#
# v1.4.1 (2026-11-07) - ONE BATCHED VERSION ("version واحدة"):
#   (1) PET + ISLAND REMOVED from the running app (user decision:
#       "they still have problems" - the pet rendered black / could
#       not be grabbed, the island rendered gray / clicks died). Their
#       windows are NEVER created and no pet/island code runs at
#       startup - the code stays behind pet_enabled/island_enabled
#       flags (settings_store, default False - NOT deleted) so they can
#       come back once fixed. The app starts with the MAIN WINDOW
#       directly (autostart too), and the chat is big from the start
#       (the hero starts collapsed - see app.js).
#   (2) CLOSE -> SYSTEM TRAY (app/tray.py, pystray): X hides the main
#       window to the tray (menu: Open / Quit) instead of quitting -
#       the standing rule holds (never fully close except explicit
#       Quit, from the tray menu or Settings). If no tray backend
#       exists, X minimizes to the taskbar instead (a hidden window
#       with no tray would strand the user). Explicit Quit still exits
#       fully (tray stopped first).
#   (3) VOICE/AVATAR PERSISTENCE BUG (field report): picking a voice
#       or avatar did not stick - after restart it reverted. Root
#       causes in app.js: (a) a voice click only updated the in-memory
#       variable - persistence happened ONLY via the Settings Save
#       button; (b) an avatar click for dog/boy/girl did NOTHING
#       (early return "v1: cat only"); (c) the save payload never
#       included "avatar" at all; (d) the load path never restored the
#       avatar selection (index.html hardcoded cat as selected); (e)
#       the 15s refreshStatus poll reverted any unsaved pick to the
#       saved value. Fixed: voice/avatar save IMMEDIATELY on click,
#       all 4 avatars are selectable (real dog/boy/girl images now
#       exist in ui/assets), the load path restores + applies them
#       before the UI paints, and refreshStatus never overwrites the
#       field the user is currently typing in / focused on.
#   (4) WHATSAPP SETTINGS BUGS (v1.4.0 field report): (a) the owner
#       number vanished after saving; (b) the QR never appeared.
#       SAME root cause for both: Api.save_settings called
#       apply_settings() -> evaluate_js("applySettingsFromPython()")
#       -> refreshStatus() re-rendered the whole Settings form from
#       server state WHILE the save handler was still running - it
#       wiped the not-yet-read WhatsApp fields (owner number +
#       enabled toggle) back to their old values, and the handler then
#       read the wiped DOM values and saved THOSE (empty number,
#       disabled) - so the sidecar never started and no QR ever
#       appeared, while the UI still said "saved ✅". Fixed three ways:
#       the save handler snapshots the WhatsApp fields BEFORE any
#       bridge call; the WhatsApp section saves ONLY via whatsapp_save
#       (no save_settings first); and save_settings no longer
#       re-renders the form mid-save (the handler re-renders once at
#       the end, after all saves). The whatsapp_save result is now
#       checked and its error shown instead of swallowed.
#   (5) SETTINGS REORGANIZED (DeliveryFlow-style): the Settings modal
#       opens as a vertical menu of buttons (General / WhatsApp /
#       Voices / Avatar / Diagnostics log / Updates), each opening its
#       own section with a back button to the menu. General holds the
#       name, language, speaker toggle, autostart, morning briefing,
#       owner-code unlock, and About (public info only now - see (6)).
#   (6) "WHO IS THE DEVELOPER?" - the brain answers with PUBLIC info
#       only (name, CS student, Memo AI + DeliveryFlow, portfolio,
#       GitHub) via a built-in rule (rules.py, deterministic) and a
#       system-prompt block (brain.py, model fallback incl. WhatsApp).
#       Phone numbers, student code, emails, addresses and keys are
#       FORBIDDEN in the answer - even for the owner. About shows the
#       same public info (email/phone lines removed).
#   (7) WhatsApp linking is NOT in v1.3.2 (v1.4.0, separate build,
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
import tray as tray_mod
import updater
import weather
import winshape
import autostart

# v1.4.0: WhatsApp linking (Baileys sidecar, app/whatsapp.py). Guarded:
# if the module fails to import (missing sidecar files), the app runs
# fine without WhatsApp - nothing else in main.py depends on it.
try:
    import whatsapp as whatsapp_mod
except Exception:
    whatsapp_mod = None

VERSION = "1.4.2"
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


def _check_bridge_files(ui_files=None) -> bool:
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
    install files + a bridge that never became ready).
    v1.4.1: the file list is a parameter (MemoApp._selftest_ui_files)
    - island/pet files are only required when their flags are on."""
    global _BRIDGE_FILES_OK
    ok = True
    # 1. UI pages + assets (a missing file = a window that never paints).
    ui_dir = APP_DIR / "ui"
    if ui_files is None:
        ui_files = ("index.html", "island.html", "pet.html",
                    "assets/avatar-cat.png", "assets/cat-run.png",
                    "app.js", "styles.css")
    for rel in ui_files:
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


def _bridge_ready_watchdog(want=None) -> None:
    """Wait up to 20s for all three pages to report READY, then log the
    outcome. Runs on a daemon thread; never blocks startup.
    v1.4.1: `want` is a parameter (MemoApp._watchdog_pages) - with the
    pet + island disabled, only "main" is waited for."""
    deadline = time.time() + 20
    if want is None:
        want = {"main", "island", "pet"}
    want = set(want)
    while time.time() < deadline:
        with _READY_LOCK:
            if want <= _READY_PAGES:
                break
        time.sleep(0.5)
    with _READY_LOCK:
        got = set(_READY_PAGES)
    missing = want - got
    if not missing:
        info("selftest",
             "Bridge READY on all pages: " + " + ".join(sorted(want)))
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
            "owner_unlocked": _APP.owner_unlocked,
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
        # v1.4.1: apply_settings() is NOT called here anymore. It used to
        # re-render the whole Settings form mid-save (via evaluate_js ->
        # refreshStatus), which wiped the not-yet-read WhatsApp fields
        # back to their old values and the handler then saved THOSE -
        # the v1.4.0 owner-number / missing-QR bug. The JS save handler
        # re-renders once at the end, after all saves. apply_settings()
        # itself stays for the flag-on path (pet visibility only).
        return {"ok": ok}

    # -- chat --------------------------------------------------------------
    def chat(self, text):
        text = (text or "").strip()
        if not text:
            return {"reply": "", "engine": ""}
        # Owner code unlocks owner mode (v1.3.0: compared as a SHA-256 hash
        # only - the plaintext code is nowhere in the repo, settings, or
        # logs, and the typed text is never echoed back). v1.3.2: owner
        # mode now only bypasses the source-code rule (tools are on for
        # every chat - see below); without it the app stays reply-only
        # for source-code questions.
        # v1.4.1 OWNER-MODE FIX (field report: the unlock confirmed but
        # did not work - "تواصل مع المطور" still came back): the flag
        # is a PLAIN INSTANCE ATTRIBUTE now, the single source of truth,
        # session-only (never in the settings dict, never persisted -
        # save_settings used to write the whole dict, so a v1.4.0 unlock
        # could leak owner_unlocked=true into settings.json and survive
        # a restart). The REAL bug was (c) below, not the flag itself:
        # rules.check DID honor it, but the question then went to the
        # MODEL, whose system prompt had the blanket "say contact the
        # developer" instruction with no idea owner mode was open (the
        # code turn never enters brain history - this method returns
        # directly). So the model refused. Fixed by passing the flag
        # into brain.ask_with_tools -> system_prompt (owner_unlocked),
        # which flips that instruction when owner mode is open.
        if hmac.compare_digest(
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
                OWNER_CODE_HASH):
            _APP.owner_unlocked = True
            info("app", "Owner mode unlocked via secret code")
            return {"reply": "تمام، وضع المالك اتفتح — تقدر تسألني عن أي حاجة، "
                             "وأقدر كمان أفتح برامج وأعمل ملفات وأنفذ أوامر "
                             "(كل إجراء هيطلب إذنك الأول).",
                    "engine": "owner"}
        lang = _APP.settings.get("language", "ar")
        hit = rules.check(text, _APP.owner_unlocked, lang)
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

    def unlock_owner(self, code):
        """Owner-code unlock from the Settings General section (v1.4.1:
        same hash compare as chat() - the typed code is never logged,
        echoed, or stored; only the session flag is set)."""
        try:
            ok = hmac.compare_digest(
                hashlib.sha256(str(code or "").strip().encode("utf-8")
                               ).hexdigest(), OWNER_CODE_HASH)
        except Exception:
            ok = False
        if ok:
            _APP.owner_unlocked = True
            info("app", "Owner mode unlocked from Settings")
        return {"ok": ok, "owner_unlocked": bool(_APP.owner_unlocked)}

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
                    "engine": ""}
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

    # -- tool permissions (v1.3.0, in-chat cards since v1.4.2) --------------
    def get_pending_permission(self):
        """Permission state for the in-chat Allow/Deny cards: the oldest
        unresolved request (if any) + recently resolved ones, so a card
        can flip to its final state. Polled by the UI while a tool chat
        runs. (v1.4.2: no modal anymore - the card lives in the chat.)"""
        return tools.get_permission_state()

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
        # v1.4.1: the island is gone - this now hides the main window
        # to the system tray (MemoApp.hide_main branches on the flag).
        # Kept under its old bridge name for compatibility.
        _APP.hide_main()
        return {"ok": True}

    def toggle_pet(self, visible=None):
        # v1.4.1: the pet is disabled - no pet window exists to show.
        if not _APP.settings.get("pet_enabled", False):
            return {"visible": False, "disabled": True}
        if visible is None:
            visible = not _APP.settings.get("pet_visible", False)
        _APP.settings["pet_visible"] = bool(visible)
        save_settings(_APP.settings)
        _APP.apply_pet_visibility()
        return {"visible": bool(visible)}

    def set_pet_bubble(self, on):
        """The pet page shows/hides the "مياو!" bubble: the window region
        must include the bubble zone only while it is visible (v1.3.1)."""
        # v1.4.1: the pet is disabled - no-op.
        if not _APP.settings.get("pet_enabled", False):
            return {"ok": False, "disabled": True}
        _APP.set_pet_bubble(bool(on))
        return {"ok": True}

    def park_pet(self):
        # v1.4.1: the pet is disabled - no-op.
        if not _APP.settings.get("pet_enabled", False):
            return {"ok": False, "disabled": True}
        _APP.settings["pet_parked"] = True
        save_settings(_APP.settings)
        _APP.park_pet()
        return {"ok": True}

    def mark_parked(self):
        """Pet was dragged to a screen edge: save parked state (no move)."""
        # v1.4.1: the pet is disabled - no-op.
        if not _APP.settings.get("pet_enabled", False):
            return {"ok": False, "disabled": True}
        _APP.settings["pet_parked"] = True
        save_settings(_APP.settings)
        _APP.refresh_pet_region()  # ball zone joins the region
        info("pet", "Pet parked at a screen edge - playing with the ball")
        return {"ok": True}

    def unpark_pet(self):
        """Pet dragged away (or double-clicked) while parked: resume roaming."""
        # v1.4.1: the pet is disabled - no-op.
        if not _APP.settings.get("pet_enabled", False):
            return {"ok": False, "disabled": True}
        _APP.settings["pet_parked"] = False
        save_settings(_APP.settings)
        _APP.refresh_pet_region()  # ball zone leaves the region
        info("pet", "Pet unparked - roaming resumed")
        return {"ok": True}

    def pet_interaction(self, kind):
        # v1.4.1: the pet is disabled - no-op (nothing to log).
        if not _APP.settings.get("pet_enabled", False):
            return {"ok": True, "disabled": True}
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
        # v1.4.1: the pet is disabled - there is no pet window at all.
        if not _APP.settings.get("pet_enabled", False):
            return {"ok": False, "disabled": True}
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

    def open_github(self):
        # v1.4.1: the About section's GitHub button (public info only,
        # same as the "who is the developer?" answer).
        webbrowser.open("https://github.com/mohamed5946")
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

    # -- long-term memory (v1.4.2) -----------------------------------------
    def get_memory(self):
        """Everything the brain remembers (for the Settings Memory
        section): facts, learned rules, style profile, routines and the
        rolling summary. Local only - this is the user's own data."""
        try:
            import memory as memory_mod
            store = memory_mod.get_store()
            return {"ok": True,
                    "facts": list(store.data.get("facts", [])),
                    "rules": list(store.data.get("rules", [])),
                    "style": dict(store.data.get("style", {})),
                    "routines": list(store.data.get("routines", [])),
                    "summary": store.data.get("summary", "")}
        except Exception as exc:
            error("app", f"get_memory failed: {exc}")
            return {"ok": False, "facts": [], "rules": [], "style": {},
                    "routines": [], "summary": ""}

    def delete_memory_item(self, kind, item_id):
        """Delete one memory (kind: fact|rule|routine) from Settings."""
        try:
            import memory as memory_mod
            store = memory_mod.get_store()
            if kind == "fact":
                ok = store.delete_fact(str(item_id or ""))
            elif kind == "rule":
                ok = store.delete_rule(str(item_id or ""))
            elif kind == "routine":
                ok = store.delete_routine(str(item_id or ""))
            else:
                return {"ok": False}
            return {"ok": ok}
        except Exception as exc:
            error("app", f"delete_memory_item failed: {exc}")
            return {"ok": False}

    def clear_memory(self):
        """Wipe ALL memories (Settings Memory section)."""
        try:
            import memory as memory_mod
            memory_mod.get_store().clear_all()
            return {"ok": True}
        except Exception as exc:
            error("app", f"clear_memory failed: {exc}")
            return {"ok": False}

    # -- system alerts (v1.4.2) --------------------------------------------
    def get_alerts(self):
        """Recent system/notification alerts for the in-app banner.
        The UI polls this and renders unseen ids."""
        with _APP._alerts_lock:
            return {"alerts": [dict(a) for a in _APP._alerts]}

    # -- system monitor (v1.4.2) -------------------------------------------
    def get_monitor_status(self):
        """Live system numbers + thresholds for the Settings System
        section. temp_available says honestly whether the CPU
        temperature can be read on this machine (never a guessed
        number)."""
        status = None
        try:
            if _APP._monitor is not None:
                status = _APP._monitor.get_status()
            else:
                import monitor as monitor_mod
                status = monitor_mod.SystemMonitor(
                    _APP.settings).get_status()
        except Exception as exc:
            error("app", f"get_monitor_status failed: {exc}")
        keys = ("monitor_enabled", "monitor_ram_free_mb",
                "monitor_ram_percent", "monitor_disk_free_gb",
                "monitor_disk_percent_free", "monitor_temp_warn_c",
                "monitor_temp_critical_c", "monitor_cpu_sustained_percent",
                "monitor_cpu_sustained_minutes",
                "monitor_battery_warn_percent",
                "monitor_battery_critical_percent", "monitor_cooldown_min",
                "monitor_alert_in_app", "monitor_alert_toast")
        return {"ok": status is not None, "status": status,
                "temp_available": bool((status or {}).get("temp_available")),
                "settings": {k: _APP.settings.get(k) for k in keys}}

    def save_monitor_settings(self, payload):
        """Save monitor thresholds/toggles from Settings; restart the
        background monitor so the new thresholds apply at once."""
        try:
            data = json.loads(payload) if isinstance(payload, str) else payload
        except Exception as exc:
            error("app", f"Bad monitor settings payload: {exc}")
            return {"ok": False}
        keys = ("monitor_enabled", "monitor_ram_free_mb",
                "monitor_ram_percent", "monitor_disk_free_gb",
                "monitor_disk_percent_free", "monitor_temp_warn_c",
                "monitor_temp_critical_c", "monitor_cpu_sustained_percent",
                "monitor_cpu_sustained_minutes",
                "monitor_battery_warn_percent",
                "monitor_battery_critical_percent", "monitor_cooldown_min",
                "monitor_alert_in_app", "monitor_alert_toast")
        for key in keys:
            if key in data:
                _APP.settings[key] = data[key]
        ok = save_settings(_APP.settings)
        try:
            if _APP._monitor is not None:
                _APP._monitor.stop()
                _APP._monitor = None
            _APP._start_monitor()
        except Exception as exc:
            warn("app", f"Monitor restart after save failed: {exc}")
        return {"ok": ok}

    # -- important notifications (v1.4.2) -----------------------------------
    def get_notification_status(self):
        """Notification-listener state for the Settings section: whether
        the listener is available, whether access was granted, and the
        current filter lists. Honest when unavailable/denied."""
        try:
            import notifications as notif_mod
            listener = notif_mod.get_listener_status()
        except Exception as exc:
            listener = {"available": False, "access": "unavailable",
                        "detail": f"notifications module failed: {exc}"}
        return {"ok": True, "listener": listener,
                "enabled": bool(_APP.settings.get("notifications_enabled", True)),
                "apps": list(_APP.settings.get("notifications_important_apps", [])),
                "keywords": list(_APP.settings.get("notifications_keywords", []))}

    def save_notification_settings(self, payload):
        """Save the notification filter lists / toggle; restart the
        watcher so the new filters apply at once."""
        try:
            data = json.loads(payload) if isinstance(payload, str) else payload
        except Exception as exc:
            error("app", f"Bad notification settings payload: {exc}")
            return {"ok": False}
        if "enabled" in data:
            _APP.settings["notifications_enabled"] = bool(data["enabled"])
        if isinstance(data.get("apps"), list):
            _APP.settings["notifications_important_apps"] = [
                str(a).strip() for a in data["apps"] if str(a).strip()]
        if isinstance(data.get("keywords"), list):
            _APP.settings["notifications_keywords"] = [
                str(k).strip() for k in data["keywords"] if str(k).strip()]
        ok = save_settings(_APP.settings)
        try:
            if _APP._notif_watcher is not None:
                _APP._notif_watcher.stop()
                _APP._notif_watcher = None
            _APP._start_notifications()
        except Exception as exc:
            warn("app", f"Notification watcher restart failed: {exc}")
        return {"ok": ok}

    def request_notification_access(self):
        """Ask Windows for notification-listener access (Settings
        button). Starts the watcher flow in a background thread and
        returns at once - the UI polls get_notification_status()."""
        try:
            _APP._start_notifications()
            return {"ok": True, "started": True}
        except Exception as exc:
            error("app", f"request_notification_access failed: {exc}")
            return {"ok": False, "started": False, "detail": str(exc)}

    # -- WhatsApp linking (v1.4.0) ----------------------------------------
    def get_whatsapp_status(self):
        """Link state for the Settings UI (status + QR + masked number).
        The brain reads the same state via whatsapp.get_state() to answer
        truthfully about WhatsApp (v1.3.2 system prompt)."""
        if whatsapp_mod is None:
            return {"enabled": False, "running": False,
                    "status": "unavailable", "qr": None,
                    "linked_phone": None, "owner_configured": False,
                    "error": "WhatsApp module not available in this build"}
        return whatsapp_mod.get_state()

    def whatsapp_save(self, payload):
        """Save WhatsApp settings from the UI: {enabled, owner_phone}.
        Starts/stops the sidecar to match. (Api.save_settings and its
        key whitelist are untouched by v1.4.0 - this is a separate path.)"""
        if whatsapp_mod is None:
            return {"ok": False, "error": "WhatsApp module not available"}
        try:
            data = json.loads(payload) if isinstance(payload, str) else payload
        except Exception as exc:
            error("app", f"Bad WhatsApp settings payload: {exc}")
            return {"ok": False}
        if "enabled" in data:
            _APP.settings["whatsapp_enabled"] = bool(data["enabled"])
        if "owner_phone" in data:
            _APP.settings["whatsapp_owner_phone"] = \
                whatsapp_mod.normalize_number(str(data["owner_phone"]))
        ok = save_settings(_APP.settings)
        if _APP.settings.get("whatsapp_enabled", False):
            svc = whatsapp_mod.ensure_started(_APP.settings)
            if svc is None or not svc.start():
                return {"ok": False,
                        "error": "could not start WhatsApp sidecar (see log)",
                        "state": whatsapp_mod.get_state()}
        else:
            whatsapp_mod.stop_service()
        return {"ok": ok, "state": whatsapp_mod.get_state()}

    def whatsapp_disconnect(self):
        """Log out + wipe the session (fresh QR next time)."""
        if whatsapp_mod is None:
            return {"ok": False}
        svc = whatsapp_mod.get_service()
        if svc is not None:
            svc.logout()
        return {"ok": True}


class MemoApp:
    def __init__(self):
        global _APP
        _APP = self
        self.settings = load_settings()
        # v1.4.1: scrub a stale owner_unlocked key (v1.4.0's
        # save_settings could persist it into settings.json - the flag
        # is session-only now and lives on self.owner_unlocked, never
        # in the dict, so it can never survive a restart).
        self.settings.pop("owner_unlocked", None)
        # v1.4.1: owner mode is a SESSION-ONLY instance attribute, the
        # single source of truth (see Api.chat for the full story).
        self.owner_unlocked = False
        # v1.4.1: system tray manager (None until started; None also
        # means "no tray backend" - X then minimizes to the taskbar).
        self._tray = None
        # v1.4.2: system monitor + important-notification watcher
        # (created in run() - both are best-effort and must never block
        # startup). _alerts is the in-app banner feed (Api.get_alerts).
        self._monitor = None
        self._notif_watcher = None
        self._alerts: list = []
        self._alerts_lock = threading.Lock()
        self._alert_seq = 0
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

    # -- v1.4.1 window set ---------------------------------------------------
    def _enabled_windows(self):
        """Window pages that exist in this build (v1.4.1: main only by
        default - pet/island behind their flags, never created when
        disabled)."""
        wins = ["main"]
        if self.settings.get("island_enabled", False):
            wins.append("island")
        if self.settings.get("pet_enabled", False):
            wins.append("pet")
        return wins

    def _selftest_ui_files(self):
        """UI files the startup self-test checks (v1.4.1: island/pet
        files only when their flags are on - a disabled window's files
        are never required)."""
        files = ["index.html", "app.js", "styles.css",
                 "assets/avatar-cat.png", "assets/avatar-dog.png",
                 "assets/avatar-boy.png", "assets/avatar-girl.png"]
        if self.settings.get("island_enabled", False):
            files.append("island.html")
        if self.settings.get("pet_enabled", False):
            files += ["pet.html", "assets/cat-run.png"]
        return files

    def _watchdog_pages(self):
        """Pages the bridge-ready watchdog waits for (v1.4.1)."""
        return set(self._enabled_windows())

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
        open, just bring it forward.
        v1.4.1: the island is DISABLED by default - the main window
        just shows (there is no pill to grow from). The morph path
        below only runs when the island is re-enabled."""
        if not self.main_window:
            return
        if self._main_visible:
            try:
                self.main_window.show()
            except Exception:
                pass
            return
        self._main_visible = True
        if not self.settings.get("island_enabled", False):
            try:
                self.main_window.show()
            except Exception:
                pass
            self._main_rect = self._main_full_rect()
            info("ui", "Main window opened")
            return
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
        it shrinks into the pill in ~0.45s, then the island appears).
        v1.4.1: with the island DISABLED, X hides the main window to
        the SYSTEM TRAY instead (the tray icon is the way back - the
        standing rule holds: the app never fully closes except through
        an explicit Quit). If no tray backend exists, it minimizes to
        the taskbar instead - a hidden window with no tray would strand
        the user. The morph path below only runs when the island is
        re-enabled."""
        if not self.main_window or not self._main_visible:
            return
        self._main_visible = False
        # Remember the rect to restore to (exact, from the OS).
        rect = winshape.get_window_rect(_find_main_hwnd())
        if rect:
            self._main_rect = rect
        if not self.settings.get("island_enabled", False):
            try:
                self.main_window.hide()
            except Exception:
                pass
            if self._tray is not None and self._tray.running:
                info("ui", "Main window hidden to system tray")
            else:
                try:
                    self.main_window.minimize()
                except Exception:
                    pass
                info("ui", "Main window minimized (no tray backend)")
            return
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
        # v1.4.1: NO evaluate_js("applySettingsFromPython()") here
        # anymore - that re-rendered the Settings form mid-save and
        # wiped unsaved WhatsApp fields (the v1.4.0 owner-number /
        # missing-QR bug). Python-side effects only (pet visibility,
        # behind its flag). The JS save handler re-renders once at the
        # end, after all saves.
        self.apply_pet_visibility()

    def apply_pet_visibility(self):
        # v1.4.1: the pet is disabled - there is no pet window to show.
        if not self.settings.get("pet_enabled", False):
            return
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
                self.settings.get("language", "ar"),
                # v1.4.1: the model MUST know when owner mode is open,
                # or its system prompt refuses source-code questions
                # with "تواصل مع المطور" even after a successful unlock.
                owner_unlocked=self.owner_unlocked)
        except Exception as exc:
            error("brain", f"Tool chat failed: {exc}")
            result = {"reply": "حصلت مشكلة وأنا بنفذ، جرّب تاني.",
                      "engine": "", "tools_used": 0}
        with _chats_lock:
            entry = _chats.get(chat_id)
            if entry is not None:
                entry.update({"done": True, "reply": result["reply"],
                              "engine": result["engine"],
                              "tools_used": result.get("tools_used", 0)})
        self.speak_async(result["reply"])

    def _on_system_alert(self, alert: dict) -> None:
        """Shared delivery for monitor + important-notification alerts:
        keep it for the in-app banner (Api.get_alerts) AND try a Windows
        toast. Never raises - an alert must never crash the app. The
        two channel toggles (monitor_alert_in_app / monitor_alert_toast)
        are honored; if both are off the alert is only logged."""
        try:
            with self._alerts_lock:
                self._alert_seq += 1
                item = {"id": f"alert-{self._alert_seq}",
                        "kind": alert.get("kind", ""),
                        "level": alert.get("level", "warn"),
                        "title": alert.get("title_ar", ""),
                        "message": alert.get("message_ar", ""),
                        "app": alert.get("app", ""),
                        "notification_title": alert.get("notification_title", ""),
                        "at": alert.get("at", "")}
                if self.settings.get("monitor_alert_in_app", True):
                    self._alerts.append(item)
                    if len(self._alerts) > 20:
                        self._alerts = self._alerts[-20:]
            if self.settings.get("monitor_alert_toast", True):
                try:
                    import notifications as notif_mod
                    msg = item["message"]
                    if item.get("notification_title"):
                        msg = f"{item['notification_title']}\n{msg}"
                    notif_mod.show_toast(item["title"] or "Memo AI", msg)
                except Exception:
                    pass
        except Exception as exc:
            warn("app", f"Alert delivery failed: {exc}")

    def _start_monitor(self) -> None:
        """Background system monitor (v1.4.2). Best effort."""
        if not self.settings.get("monitor_enabled", True):
            info("app", "System monitor disabled in settings")
            return
        try:
            import monitor as monitor_mod
        except Exception as exc:
            warn("app", f"System monitor unavailable: {exc}")
            return
        self._monitor = monitor_mod.SystemMonitor(
            self.settings, on_alert=self._on_system_alert)
        if self._monitor.start():
            info("app", "System monitor started")
        else:
            self._monitor = None

    def _start_notifications(self) -> None:
        """Important-notification watcher (v1.4.2). The access request
        runs in a background thread - on first run Windows shows the
        user a permission prompt; until he grants it, the watcher
        simply does not start and Settings says so honestly."""
        if not self.settings.get("notifications_enabled", True):
            info("app", "Notification alerts disabled in settings")
            return
        try:
            import notifications as notif_mod
        except Exception as exc:
            warn("app", f"Notification alerts unavailable: {exc}")
            return

        def _run():
            try:
                access = notif_mod.request_access()
                if not access.get("ok"):
                    warn("app", "Notification access not granted: "
                                f"{access.get('detail', '')}")
                    return
                self._notif_watcher = notif_mod.NotificationWatcher(
                    self.settings, on_important=self._on_system_alert)
                self._notif_watcher.start()
            except Exception as exc:
                warn("app", f"Notification watcher failed: {exc}")

        threading.Thread(target=_run, daemon=True,
                         name="memo-notif-start").start()

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
        # v1.4.1: stop the tray icon first (it holds a window handle).
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:
                pass
        # v1.4.0: stop the WhatsApp sidecar first (before the windows die).
        if whatsapp_mod is not None:
            try:
                whatsapp_mod.stop_service()
            except Exception:
                pass
        # v1.4.2: stop the system monitor + notification watcher.
        if self._monitor is not None:
            try:
                self._monitor.stop()
            except Exception:
                pass
        if self._notif_watcher is not None:
            try:
                self._notif_watcher.stop()
            except Exception:
                pass
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
        # X button / taskbar close = hide to the tray (v1.4.1; was
        # "minimize to island" before the island was removed), NOT quit.
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
        # v1.4.1: the file list follows the enabled windows.
        _check_bridge_files(self._selftest_ui_files())
        # v1.3.1: pet hidden-by-default migration (one-time) + autostart
        # registry follows the setting (default ON). v1.4.1: the
        # migration only runs while the pet is enabled (otherwise it
        # would log about a window that does not exist).
        if self.settings.get("pet_enabled", False):
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
        # v1.4.0: WhatsApp linking - start the sidecar if enabled.
        # Guarded: a missing/broken WhatsApp module never blocks startup,
        # and a second instance never reaches this line (see lock above).
        if whatsapp_mod is not None:
            try:
                whatsapp_mod.start_service(self.settings)
            except Exception as exc:
                error("whatsapp", f"Could not start WhatsApp service: {exc}")
        threading.Thread(
            target=winshape.watch_show_main_event,
            args=(self._show_main_event, self.show_main,
                  lambda: self._quitting),
            daemon=True).start()
        sw, sh = screen_size()
        ui_dir = APP_DIR / "ui"
        # v1.4.1: the island window is NEVER created while island_enabled
        # is False (default) - the code stays, behind the flag.
        if self.settings.get("island_enabled", False):
            # The island starts in its IDLE compact state (v1.2.3).
            island_x = max(0, (sw - ISLAND_IDLE_W) // 2)
            self.island_window = webview.create_window(
                "Memo AI", str(ui_dir / "island.html"),
                width=ISLAND_IDLE_W, height=ISLAND_IDLE_H, x=island_x, y=12,
                frameless=True, transparent=False, on_top=True,
                resizable=False, background_color=ISLAND_BG, js_api=self.api)
        # v1.4.1: the main window opens DIRECTLY at startup (hidden only
        # while the island is enabled - the old morph opened it from the
        # pill). min_size=(640,520): the morph to a 190x52 pill is gone
        # with the island, so the window keeps its full minimum.
        self.main_window = webview.create_window(
            "Memo AI", str(ui_dir / "index.html"),
            width=MAIN_W, height=MAIN_H,
            hidden=self.settings.get("island_enabled", False),
            min_size=(640, 520), js_api=self.api)
        if not self.settings.get("island_enabled", False):
            # The window starts visible: _main_visible must agree, or
            # hide_main() would refuse to hide it on X (tray).
            self._main_visible = True
        self.main_window.events.closing += self._on_main_closing
        # v1.4.1: the pet window is NEVER created while pet_enabled is
        # False (default) - no roaming window, no black rectangle.
        if self.settings.get("pet_enabled", False):
            # v1.3.1: the pet starts HIDDEN unless the user left it visible
            # (hidden= at creation avoids a flash of the cat at startup).
            self.pet_window = webview.create_window(
                "Memo AI Pet", str(ui_dir / "pet.html"),
                width=PET_SIZE, height=PET_SIZE,
                x=sw - PET_SIZE - 20, y=sh - PET_SIZE - 90,
                frameless=True, transparent=False, on_top=True,
                resizable=False, background_color=PET_BG, js_api=self.api,
                hidden=not self.settings.get("pet_visible", False))

        # v1.4.1: system tray (the island is gone - the tray is the way
        # back after closing the window). A missing tray backend never
        # blocks startup; X then minimizes to the taskbar instead.
        try:
            self._tray = tray_mod.TrayManager(
                on_open=self.show_main, on_quit=self.quit,
                icon_path=APP_DIR / "ui" / "assets" / "avatar-cat.png")
            if self._tray.start():
                info("app", "System tray icon started")
            else:
                warn("app", "System tray not available - closing the "
                            "window will minimize to the taskbar instead")
        except Exception as exc:
            self._tray = None
            warn("app", f"Could not start system tray: {exc}")

        threading.Thread(target=self.morning_briefing, daemon=True).start()
        # v1.4.2: background system monitor + important-notification
        # watcher (best effort - neither may block startup or crash it).
        try:
            self._start_monitor()
        except Exception as exc:
            warn("app", f"System monitor did not start: {exc}")
        try:
            self._start_notifications()
        except Exception as exc:
            warn("app", f"Notification watcher did not start: {exc}")
        # v1.3.2: bridge-ready watchdog - each page logs READY on
        # pywebviewready; after 20s the log names any page that never
        # became ready, with the exact reason. v1.4.1: only the enabled
        # pages are waited for (main only, by default).
        threading.Thread(target=_bridge_ready_watchdog,
                         args=(self._watchdog_pages(),),
                         daemon=True).start()
        info("app", "Memo AI started - main window only "
                    "(pet + island disabled)")
        # v1.3.1: webview.start(func) runs func in a thread BEFORE any
        # window exists, so _apply_window_regions polls until the HWNDs
        # appear (up to 60s) and then shapes the windows with regions.
        # v1.4.1: with no island/pet there is nothing to shape - plain
        # webview.start() (the region thread only runs when at least
        # one of them is enabled).
        if self.settings.get("island_enabled", False) \
                or self.settings.get("pet_enabled", False):
            webview.start(self._apply_window_regions)
        else:
            webview.start()
        info("app", "Memo AI exited")

    def _apply_window_regions(self):
        """Apply the Win32 window regions (v1.3.1, app/winshape.py) to
        the island + pet windows. Runs in the thread webview.start(func)
        spawns, which starts BEFORE the windows are created - hence the
        polling. Each window is shaped once its HWND is found; the
        region lives on the HWND so it survives hide/show. The island
        region is re-applied on every resize (Api.island_resize). Every
        outcome is logged, and success is only claimed after a read-back
        verification (see winshape).
        v1.4.1: with the pet + island disabled there is nothing to
        shape - the method returns immediately (run() only calls it
        when at least one of them is enabled; this is belt and
        braces)."""
        # The pet silhouette rects are built once from the bundled PNG.
        # v1.4.1: only when the pet is enabled.
        if self.settings.get("pet_enabled", False):
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
        pending = {}
        if self.settings.get("island_enabled", False):
            pending["island"] = True
        if self.settings.get("pet_enabled", False):
            pending["pet"] = True
        if not pending:
            return
        deadline = time.time() + 60
        while time.time() < deadline and any(pending.values()):
            if pending.get("island"):
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
            if pending.get("pet"):
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
