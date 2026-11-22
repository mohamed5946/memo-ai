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
#       appeared, while the UI still said "saved". Fixed three ways:
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
import functools
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
from settings_store import (APP_DIR, BASE_DIR, DATA_DIR, keys_status,
                            load_settings, save_settings)

import brain
import hear
import rules
import speak
import tools
import tray as tray_mod
import updater
import weather
import winshape
import island_canvas
import autostart
import bridge_v2

# v1.6.0: backend workstream modules. All guarded like WhatsApp: a
# missing/broken module never blocks startup - the feature just
# reports "unavailable" and the rest of the app runs.
try:
    import invite as invite_mod
except Exception:
    invite_mod = None
try:
    import apikeys as apikeys_mod
except Exception:
    apikeys_mod = None
try:
    import browser_control as browser_mod
except Exception:
    browser_mod = None
try:
    import companion as companion_mod
except Exception:
    companion_mod = None
try:
    import fixer as fixer_mod
except Exception:
    fixer_mod = None
try:
    import selfview as selfview_mod
except Exception:
    selfview_mod = None
try:
    import tempwatch as tempwatch_mod
except Exception:
    tempwatch_mod = None

# v1.6.2: Chrome native host (extension bridge). Guarded like the
# rest -- a missing module never blocks startup; the listener just
# never starts and extension calls report "not connected" honestly.
try:
    import native_host as native_host_mod
except Exception:
    native_host_mod = None

# v1.4.0: WhatsApp linking (Baileys sidecar, app/whatsapp.py). Guarded:
# if the module fails to import (missing sidecar files), the app runs
# fine without WhatsApp - nothing else in main.py depends on it.
try:
    import whatsapp as whatsapp_mod
except Exception:
    whatsapp_mod = None

# v1.5: wake word "يا لونا" (app/wakeword.py). Guarded the same way:
# a broken/missing wake-word module never blocks startup - the
# feature just reports "unavailable" in Settings.
try:
    import wakeword as wakeword_mod
except Exception:
    wakeword_mod = None

VERSION = "1.6.4-rc.1"
# v1.6.2: bigger default main window (was 960x720) -- more room for
# the chat. Frameless since v1.6.2: the native title bar is gone and
# the in-app header owns minimize/maximize/close + drag.
MAIN_W, MAIN_H = 1280, 860
# v1.6.2 (F4): the idle pill is 380x60 -- the 190x52 numbers were the
# old small-pill design; the pill is the full 380-wide island.
ISLAND_IDLE_W, ISLAND_IDLE_H = 380, 60
# v1.6.4 reskin (fixed canvas, WindyLand/Nusa method): the island window
# is created ONCE at the expanded canvas size and never resized.
ISLAND_CANVAS_W, ISLAND_CANVAS_H = 720, 400
PET_SIZE = 150

# Single-instance mutex (Windows named mutex, no admin needed).
_MUTEX_NAME = "Local\\LONA-SingleInstance"
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

# v1.6.2 (A2/A4): background-check results, same shape as _chats.
# check_updates() / test_api_key() return at once; the UI polls the
# matching getter until "checking"/"pending" is False.
_CHECK_UPDATES = {"checking": False, "update": None, "error": None,
                  "id": None}
_CHECK_UPDATES_LOCK = threading.Lock()
_TEST_KEY_RESULTS: dict = {}
_TEST_KEY_LOCK = threading.Lock()

# v1.6.4 perf (a): island slow-call results. island_music (SMTC, up
# to 5 s), island_lyrics (LRCLIB network, up to 8 s) and
# island_notifications (WinRT, unbounded) MUST NOT run on the
# pywebview bridge thread - they return {"pending": True, "id"} at
# once and the island UI polls bridge_v2.job_result(id), exactly
# like test_api_key / check_updates above. Entry: {"done", "created",
# "result"}; pruned like _chats (oldest finished first, cap 50).
_ISLAND_JOBS: dict = {}
_ISLAND_JOBS_LOCK = threading.Lock()

# Result of the startup bridge-files self-check (None = not run yet).
_BRIDGE_FILES_OK = None

# v1.3.1: the window backgrounds behind the WebView2 controls. With
# window regions (winshape) these colors are invisible - the region
# clips everything outside the pill/cat silhouette - but if a region
# ever failed to apply, the fallback is a dark color, never magenta.
# v1.6.2 (F4): ISLAND_BG is the PILL's own CSS color only -- it is
# no longer passed as the island window's background (the window is
# created transparent, so only the pill paints).
ISLAND_BG = "#0D121C"
PET_BG = "#0A0A0A"


def _find_hwnd_by_title(title: str, max_width: int | None = None,
                        max_height: int | None = None):
    """Fallback HWND lookup (EnumWindows): first top-level window whose
    title matches exactly. max_width distinguishes the small island
    ("LONA", <=400px) from the big main window (same title).

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
                if (max_width is None or
                        (rect.right - rect.left) <= max_width) and \
                        (max_height is None or
                         (rect.bottom - rect.top) <= max_height):
                    found.append(hwnd)
            return True

        user32.EnumWindows(_enum, None)
        return found[0] if found else None
    except Exception:
        return None


def _find_main_hwnd():
    """HWND of the MAIN window: title "LONA" (shared with the
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
            if buf.value == "LONA":
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
                     f"gemini-voice={ks['gemini-voice']} "
                     f"gemini-chat={ks['gemini-chat']} azure={ks['azure']}")
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
        paths = (
            f"SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\{guid}",
            f"SOFTWARE\\WOW6432Node\\Microsoft\\EdgeUpdate\\Clients\\{guid}",
        )
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for path in paths:
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
              "LONA first) and check the log.")


def _acquire_single_instance_lock() -> bool:
    """Only one LONA may run at a time.

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

# v1.6.1 item 21: shared fallback monitor for the rare path where the
# main monitor never started (_APP._monitor is None). It used to be a
# throwaway SystemMonitor constructed + synchronously sampled on EVERY
# poll -- a fresh PowerShell spawn per 5 s poll on the UI thread. One
# shared instance whose sampler thread feeds the background cache.
_FALLBACK_MONITOR = None


def _fallback_monitor():
    """The shared fallback monitor (sampler thread started once)."""
    global _FALLBACK_MONITOR
    if _FALLBACK_MONITOR is None:
        import monitor as monitor_mod
        _FALLBACK_MONITOR = monitor_mod.SystemMonitor(_APP.settings)
        _FALLBACK_MONITOR.start_sampler()
    return _FALLBACK_MONITOR


def screen_size() -> tuple:
    try:
        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080


def _log_slow(fn):
    """v1.6.1 item 21: diagnostics evidence for the next freeze. Any
    bridge call taking over 1 s is logged with its name and duration --
    the UI thread must never block silently. Applied to every bridge
    method that can touch the disk, the network, a subprocess or psutil."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        t0 = time.time()
        try:
            return fn(*args, **kwargs)
        finally:
            dt = time.time() - t0
            if dt > 1.0:
                warn("app", f"Slow bridge call {fn.__name__} "
                     f"took {dt:.1f}s")
    return wrapper


class Api:
    """JS bridge: every method here is callable from the UI as pywebview.api.*

    Holds NO state and NO reference to the app/windows - only plain methods.
    App access goes through the module-level _APP global.
    """

    # -- status ------------------------------------------------------------
    @_log_slow
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
            "settings": bridge_v2.with_v2_aliases(
                {k: v for k, v in _APP.settings.items()
                 if k != "owner_unlocked"}),
            "owner_unlocked": _APP.owner_unlocked,
        }

    @_log_slow
    def get_settings(self):
        return bridge_v2.with_v2_aliases(
            {k: v for k, v in _APP.settings.items()
             if k != "owner_unlocked"})

    @_log_slow
    def save_settings(self, new_values):
        try:
            data = json.loads(new_values) if isinstance(new_values, str) else new_values
        except Exception as exc:
            error("app", f"Bad settings payload: {exc}")
            return {"ok": False}
        if not isinstance(data, dict):
            return {"ok": False}
        split = bridge_v2.translate_save_settings_patch(data)
        canonical = split["settings"]
        for key, value in canonical.items():
            _APP.settings[key] = value
        ok = save_settings(_APP.settings)
        info("app", "Settings saved from UI")
        autostart.set_enabled(bool(_APP.settings.get("autostart", True)))
        out = {"ok": ok}
        wa = split.get("whatsapp") or {}
        if wa:
            try:
                wa_res = self.whatsapp_save(wa)
                out["whatsapp_pending_id"] = wa_res.get("id")
            except Exception as exc:
                error("app", f"WhatsApp save from settings failed: {exc}")
        # Monitor thresholds + wake-word toggle apply at once. The
        # monitor snapshots settings at start (monitor.py __init__),
        # so a plain save would only take effect after a restart.
        # stop/start joins can take ~4s - run off the bridge thread.
        monitor_changed = any(k.startswith("monitor_") for k in canonical)
        wake_changed = "wakeword_enabled" in canonical
        if monitor_changed or wake_changed:
            def _apply_live():
                if monitor_changed:
                    try:
                        if _APP._monitor is not None:
                            _APP._monitor.stop()
                            _APP._monitor = None
                        _APP._start_monitor()
                    except Exception as exc:
                        warn("app", f"Monitor restart after save failed: {exc}")
                if wake_changed:
                    try:
                        if _APP.settings.get("wakeword_enabled"):
                            _APP.start_wakeword()
                        else:
                            _APP.stop_wakeword()
                    except Exception as exc:
                        warn("app", f"Wake-word toggle after save failed: {exc}")
            threading.Thread(target=_apply_live, daemon=True).start()
        return out

    # -- chat --------------------------------------------------------------
    @_log_slow
    def _chat_raw(self, text):
        text = (text or "").strip()
        if not text:
            return {"reply": "", "engine": ""}
        # v1.6.1 (item 21c): a new message supersedes any permission
        # still pending from an older turn - cancel it now, so its
        # card never appears in this turn's context and the old brain
        # thread finishes fast (denied) instead of hanging to the
        # 120s timeout.
        try:
            tools.cancel_pending()
        except Exception:
            pass
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
        # v1.6.1: the mood reacts to how he talks to it - note the
        # message tone (best effort; a missing monitor never blocks
        # the chat). The owner-code hash text above is never noted.
        try:
            if _APP._monitor is not None:
                _APP._monitor.note_user_message(text)
        except Exception:
            pass
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
                               "tools_used": 0, "images": [],
                               "created": time.time()}
            if len(_chats) > 50:  # prune oldest finished entries
                for old_id in sorted(
                        _chats, key=lambda i: _chats[i]["created"])[:10]:
                    if _chats[old_id]["done"]:
                        _chats.pop(old_id, None)
        threading.Thread(target=_APP.run_tool_chat,
                         args=(chat_id, text), daemon=True).start()
        # v1.6.1: push the thinking mood to the island AT ONCE (it also
        # picks it up from get_mood() on its 5s poll). Guarded - a
        # missing/closed island window never breaks the chat.
        try:
            if _APP.island_window is not None:
                _APP.island_window.evaluate_js("setMood(\"بيفكر\")")
        except Exception:
            pass
        info("app", "Chat started in background (thinking mood on)")
        return {"reply": "", "engine": "groq", "pending": True,
                "chat_id": chat_id}

    @_log_slow
    def chat(self, text, opts=None):
        """v1.6.4 reskin: v2 chat(text, {deep_think}) — applies the
        per-message Deep Think flag, then runs the v1 chat flow."""
        return bridge_v2.chat(self, text, opts)

    @_log_slow
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

    @_log_slow
    def chat_result(self, chat_id):
        """Poll the result of an async (tool) chat. {"done": False} while
        the brain thread is still working (possibly waiting on permission)."""
        with _chats_lock:
            entry = _chats.get(str(chat_id or ""))
        if not entry:
            return {"done": False}
        if entry.get("done"):
            # v1.6.1: the reply arrived - restore the island robot from
            # "بيفكر" to the stabilized mood at once (its 5s poll would
            # do the same, this just skips the wait). Guarded.
            try:
                if _APP.island_window is not None:
                    mood = self.get_mood().get("mood", "صاحي")
                    _APP.island_window.evaluate_js(
                        f"setMood({json.dumps(mood, ensure_ascii=False)})")
            except Exception:
                pass
        return {"done": bool(entry["done"]), "reply": entry.get("reply", ""),
                "engine": entry.get("engine", "groq"),
                "tools_used": entry.get("tools_used", 0),
                "images": entry.get("images", []),
                # v1.6.2 (A1): voice turns carry what was heard.
                "heard": entry.get("heard", "")}

    @_log_slow
    def voice_chat(self):
        """Record 5s -> transcribe -> answer -> speak. The recording
        (5s sd.wait) and the transcription NEVER run on the bridge:
        v1.6.2 (A1) starts a background daemon thread (same pattern
        as chat) and returns {"pending": True, "chat_id": ...} at
        once; the UI polls chat_result(chat_id), which now also
        carries the heard text. The turn is stored in _chats like
        run_tool_chat does (reply + heard text + engine)."""
        chat_id = uuid.uuid4().hex[:8]
        with _chats_lock:
            _chats[chat_id] = {"done": False, "reply": "", "engine": "groq",
                               "tools_used": 0, "images": [], "heard": "",
                               "created": time.time()}
            if len(_chats) > 50:  # prune oldest finished entries
                for old_id in sorted(
                        _chats, key=lambda i: _chats[i]["created"])[:10]:
                    if _chats[old_id]["done"]:
                        _chats.pop(old_id, None)

        def _run():
            lang = _APP.settings.get("language", "ar")
            # v1.5: the wake-word listener is PAUSED while the
            # turn-based mic records (one mic, one owner - the two
            # streams must never fight over it, and the listener must
            # not hear its own recording and re-wake). It resumes in
            # the finally, whatever happens.
            _APP.pause_wakeword()
            try:
                heard = hear.listen(lang)
            finally:
                _APP.resume_wakeword()
            if not heard["text"]:
                with _chats_lock:
                    entry = _chats.get(chat_id)
                    if entry is not None:
                        entry.update({
                            "done": True,
                            "reply": heard.get(
                                "error", "مقدرتش أسمعك."),
                            "engine": "", "heard": ""})
                return
            # Reuse the normal chat path (rules / owner code / tool
            # loop) and copy its result into THIS turn, so one poll
            # shape (chat_result) serves text and voice alike.
            inner = self.chat(heard["text"])
            inner_id = inner.get("chat_id")
            reply, engine, tools_used, images = "", "groq", 0, []
            if inner_id:
                deadline = time.time() + 300
                while time.time() < deadline:
                    with _chats_lock:
                        inner_entry = _chats.get(inner_id)
                        done = bool(inner_entry
                                    and inner_entry.get("done"))
                    if done:
                        break
                    time.sleep(0.2)
                with _chats_lock:
                    inner_entry = _chats.get(inner_id) or {}
                reply = inner_entry.get("reply", "")
                engine = inner_entry.get("engine", "groq")
                tools_used = inner_entry.get("tools_used", 0)
                images = inner_entry.get("images", [])
            else:
                # chat() answered synchronously (rule hit / owner code).
                reply = inner.get("reply", "")
                engine = inner.get("engine", "")
            with _chats_lock:
                entry = _chats.get(chat_id)
                if entry is not None:
                    entry.update({"done": True, "reply": reply,
                                  "engine": engine,
                                  "tools_used": tools_used,
                                  "images": images,
                                  "heard": heard["text"]})

        threading.Thread(target=_run, daemon=True).start()
        return {"pending": True, "chat_id": chat_id}

    @_log_slow
    def stop_speaking(self):
        speak.stop()
        return {"ok": True}

    # -- voice output toggle (v1.3.0) --------------------------------------
    @_log_slow
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

    # -- wake word "يا لونا" (v1.5) ---------------------------------------
    @_log_slow
    def get_wakeword_status(self):
        """Wake-word listener state for the Settings UI (enabled,
        state, model readiness, wake count). Honest when the module
        is missing or the model failed to load."""
        if wakeword_mod is None:
            return {"enabled": False, "state": "unavailable",
                    "detail": "wake-word module not available in "
                              "this build",
                    "model_ready": False,
                    "phrase": "يا لونا", "wake_count": 0,
                    "last_wake_at": "", "bursts_checked": 0,
                    "bursts_discarded_noise": 0}
        return _APP.get_wakeword_status()

    @_log_slow
    def set_wakeword_enabled(self, enabled):
        """Settings toggle for always-listening: applies at once
        (starts/stops the listener) and persists. Turning it off
        closes the microphone completely."""
        enabled = bool(enabled)
        _APP.settings["wakeword_enabled"] = enabled
        ok = save_settings(_APP.settings)
        if enabled:
            _APP.start_wakeword()
        else:
            _APP.stop_wakeword()
        info("app", f"Wake word {'ON' if enabled else 'OFF'} "
                    "(Settings toggle)")
        return {"ok": ok, "wakeword_enabled": enabled,
                "status": _APP.get_wakeword_status()}

    # -- tool permissions (v1.3.0, in-chat cards since v1.4.2) --------------
    @_log_slow
    def get_pending_permission(self):
        """Permission state for the in-chat Allow/Deny cards: the oldest
        unresolved request (if any) + recently resolved ones, so a card
        can flip to its final state. Polled by the UI while a tool chat
        runs. (v1.4.2: no modal anymore - the card lives in the chat.)"""
        return tools.get_permission_state()

    @_log_slow
    def resolve_permission(self, req_id, allowed):
        ok = tools.resolve_permission(str(req_id or ""), bool(allowed))
        return {"ok": ok}

    @_log_slow
    def cancel_pending_permissions(self):
        """Cancel every still-pending permission (v1.6.1, item 21c):
        a new user message supersedes the old turn's unanswered
        requests - their cards must not surface in the new turn.
        Called from chat() on every new turn and from the UI when a
        message is queued while a turn is still running."""
        try:
            count = tools.cancel_pending()
        except Exception as exc:
            error("app", f"cancel_pending_permissions failed: {exc}")
            count = 0
        return {"ok": True, "cancelled": count}

    # -- generated images (v1.4.3) -----------------------------------------
    @_log_slow
    def get_generated_image(self, filename):
        """Return a generated image as a data URL for the chat UI.
        The chat page is a local file, so a file:// path into
        app/data/images/ would be blocked by the WebView - the bridge
        hands the bytes over instead. Filename only (no paths, no
        traversal): the file must live under app/data/images/."""
        import base64
        name = str(filename or "").strip()
        if (not name or "/" in name or "\\" in name or ".." in name):
            return {"ok": False}
        path = DATA_DIR / "images" / name
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp"}.get(
                    path.suffix.lower())
        if not mime or not path.exists():
            return {"ok": False}
        try:
            data = path.read_bytes()
        except Exception as exc:
            error("app", f"get_generated_image failed ({name}): {exc}")
            return {"ok": False}
        if len(data) > 5 * 1024 * 1024:
            return {"ok": False}
        return {"ok": True,
                "data_url": ("data:" + mime + ";base64,"
                             + base64.b64encode(data).decode("ascii"))}

    # -- v1.6.0: themes / device overview / self-view / API keys /
    # invite / deep think / proactive feed / fixing / browser / focus /
    # AI name / backup / undo ---------------------------------------
    @_log_slow
    def get_themes(self):
        """Themes picker: the OLD theme is never deleted - it stays as
        "old" beside the new ones. Python only persists the id; the UI
        applies it (smooth morph)."""
        themes = [
            {"id": "old", "name": "القديم"},
            {"id": "night-glass", "name": "زجاج الليل الداكن"},
            {"id": "calm-space", "name": "المساحة الهادية الفاتحة"},
            {"id": "dashboard", "name": "لوحة القيادة الجرافيتية"},
        ]
        # Legacy ids from v1.6.0's Python side (calm-light/graphite) never
        # existed in the frontend; map them to the real ids so a stored
        # legacy value still round-trips instead of silently falling back.
        _legacy = {"calm-light": "calm-space", "graphite": "dashboard"}
        current = _APP.settings.get("theme", "old")
        current = _legacy.get(current, current)
        return {"themes": themes, "current": current}

    @_log_slow
    def set_theme(self, theme_id):
        theme_id = str(theme_id or "").strip()
        theme_id = {"calm-light": "calm-space",
                    "graphite": "dashboard"}.get(theme_id, theme_id)
        known = {"old", "night-glass", "calm-space", "dashboard"}
        if theme_id not in known:
            return {"ok": False, "error": "unknown_theme"}
        _APP.settings["theme"] = theme_id
        ok = save_settings(_APP.settings)
        info("app", f"Theme set to {theme_id}")
        return {"ok": ok, "theme": theme_id}

    @_log_slow
    def get_device_overview(self):
        """Live device overview (RAM/CPU/battery/temp/storage/processes/
        mood) - served from the monitor's background sample cache
        (v1.6.1 item 21), never sampled synchronously on the UI thread.
        v1.6.4 reskin: translated to the v2 UI's shape by bridge_v2
        (GB aliases, temp {value,label}, top_processes {name,cpu,
        mem_mb}) - the v1 keys (incl. mood/mood_reason) are kept."""
        return bridge_v2.get_device_overview_v2(self)

    @_log_slow
    def get_mood(self):
        # v1.6.1 THINKING MOOD: while any tool chat is still running
        # (background thread, incl. Deep Think reasoning / tool calls),
        # the robot shows "بيفكر" in BOTH the main window and the
        # island - both poll this same method every 5s. This is a
        # TEMPORARY display override only: it bypasses the stabilizer
        # entirely, so it never resets the hold/confirm counters, and
        # the stabilized mood returns on the next poll after the reply.
        try:
            with _chats_lock:
                thinking = any(
                    not e.get("done")
                    and time.time() - float(e.get("created", 0)) < 180
                    for e in _chats.values())
            if thinking:
                return {"mood": "بيفكر",
                        "reason": "بيفكر في ردك…"}
        except Exception:
            pass
        try:
            result = self.get_device_overview()
            overview = result.get("overview") or {}
            return {"mood": overview.get("mood", "صاحي"),
                    "reason": overview.get("mood_reason", "")}
        except Exception as exc:
            error("app", f"get_mood failed: {exc}")
            return {"mood": "صاحي", "reason": ""}

    @_log_slow
    def get_self_view(self):
        """LONA seeing herself inside her own program (8 rows).
        v1.6.2 (A5/B16): the overview is fetched ONCE here and passed
        down as `status` -- the old code called get_mood() (which
        re-read the monitor) and then selfview read the monitor a
        second time."""
        if selfview_mod is None:
            return {"ok": False, "rows": []}
        try:
            overview = self.get_device_overview().get("overview") or {}
            mood = overview.get("mood", "صاحي")
            # Thinking override (same rule as get_mood, without
            # re-reading the monitor): a running turn shows "بيفكر".
            try:
                with _chats_lock:
                    thinking = any(
                        not e.get("done")
                        and time.time() - float(e.get("created", 0)) < 180
                        for e in _chats.values())
                if thinking:
                    mood = "بيفكر"
            except Exception:
                pass
            settings = dict(_APP.settings)
            settings["deep_file_access_granted"] = \
                tools.is_deep_file_access_granted()
            rows = selfview_mod.get_self_view(
                mood=mood, monitor=_APP._monitor, settings=settings,
                status=overview)
            return {"ok": True, "rows": rows}
        except Exception as exc:
            error("app", f"get_self_view failed: {exc}")
            return {"ok": False, "rows": []}

    # -- v1.6.4 reskin: v2 UI bridge methods -------------------------
    # Thin delegates to bridge_v2 (the v2 UI's flat call names).
    @_log_slow
    def set_muted(self, on):
        return bridge_v2.set_muted(self, on)

    @_log_slow
    def get_morning_brief(self):
        return bridge_v2.get_morning_brief(self)

    @_log_slow
    def clean_cache(self, kind="temp"):
        return bridge_v2.clean_cache(self, kind)

    @_log_slow
    def get_clean_cache_result_v2(self, req_id):
        return bridge_v2.get_clean_cache_result_v2(self, req_id)

    @_log_slow
    def undo_clean(self, token):
        return bridge_v2.undo_clean(self, token)

    @_log_slow
    def get_tasks(self):
        return bridge_v2.get_tasks(self)

    @_log_slow
    def add_task(self, text):
        return bridge_v2.add_task(self, text)

    @_log_slow
    def toggle_task(self, task_id):
        return bridge_v2.toggle_task(self, task_id)

    @_log_slow
    def delete_task(self, task_id):
        return bridge_v2.delete_task(self, task_id)

    @_log_slow
    def get_memories(self):
        return bridge_v2.get_memories(self)

    @_log_slow
    def delete_memory(self, item_id):
        return bridge_v2.delete_memory(self, item_id)

    @_log_slow
    def clear_memories(self):
        return bridge_v2.clear_memories(self)

    @_log_slow
    def get_log(self):
        return bridge_v2.get_log(self)

    @_log_slow
    def log_error(self, source, message):
        return bridge_v2.log_error(self, source, message)

    @_log_slow
    def job_result(self, job_id):
        return bridge_v2.job_result(self, job_id)

    @_log_slow
    def get_update_status(self, job_id=None):
        return bridge_v2.get_update_status(self, job_id)

    @_log_slow
    def check_owner_code(self, code):
        return bridge_v2.check_owner_code(self, code)

    @_log_slow
    def island_expand(self):
        return bridge_v2.island_expand(self)

    @_log_slow
    def island_collapse(self):
        return bridge_v2.island_collapse(self)

    @_log_slow
    def get_themes_v2(self):
        return bridge_v2.get_themes_v2(self)

    @_log_slow
    def set_theme_v2(self, theme_id):
        return bridge_v2.set_theme_v2(self, theme_id)

    @_log_slow
    def get_api_keys_status(self):
        if apikeys_mod is None:
            return {"ok": False, "groq": False, "gemini-voice": False,
                    "gemini-chat": False}
        try:
            status = apikeys_mod.get_api_keys_status()
            return {"ok": True, **status}
        except Exception as exc:
            error("app", f"get_api_keys_status failed: {exc}")
            return {"ok": False, "groq": False, "gemini-voice": False,
                    "gemini-chat": False}

    @_log_slow
    def get_api_key_value(self, provider):
        """Return the SAVED key value for one provider -- ONLY for the
        explicit 'show' tap on an empty API-key field: the user's own
        key, on his own device, on his own explicit tap (the same as
        'show password' in any program). The value is NEVER logged
        and NEVER returned by any other method: get_api_keys_status
        stays presence-only by design, and get_status only reports
        presence too."""
        if apikeys_mod is None:
            return {"ok": False, "value": None}
        name = str(provider or "").strip().lower()
        if name not in apikeys_mod.PROVIDER_FILES:
            return {"ok": False, "value": None}
        try:
            value = apikeys_mod.read_key_value(name)
        except Exception as exc:
            error("app", f"get_api_key_value failed: {exc}")
            return {"ok": False, "value": None}
        return {"ok": True, "value": value or None}

    @_log_slow
    def save_api_key(self, provider, value):
        """Save a Groq/Gemini key from Settings (never logged)."""
        if apikeys_mod is None:
            return {"ok": False, "error": "unavailable"}
        try:
            return apikeys_mod.save_key(provider, value)
        except Exception as exc:
            error("app", f"save_api_key failed: {exc}")
            return {"ok": False, "error": str(exc)}

    @_log_slow
    def test_api_key(self, provider):
        """Tiny live validity check (15s timeout). Presence/validity
        only - the key value is never returned or logged. v1.6.2
        (A4): runs in a background thread like chat() -- the bridge
        returns {"pending": true, "id": ...} at once and the UI polls
        get_test_api_key_result(id)."""
        req_id = uuid.uuid4().hex[:8]
        with _TEST_KEY_LOCK:
            _TEST_KEY_RESULTS[req_id] = {"pending": True, "ok": False,
                                         "valid": False, "detail": ""}
        if apikeys_mod is None:
            with _TEST_KEY_LOCK:
                _TEST_KEY_RESULTS[req_id] = {
                    "pending": False, "ok": False, "valid": False,
                    "detail": "unavailable"}
            return {"pending": True, "id": req_id}

        def _run():
            try:
                result = apikeys_mod.test_key(provider)
            except Exception as exc:
                error("app", f"test_api_key failed: {exc}")
                result = {"ok": False, "valid": False, "detail": str(exc)}
            with _TEST_KEY_LOCK:
                _TEST_KEY_RESULTS[req_id] = {
                    "pending": False,
                    "ok": bool(result.get("ok")),
                    "valid": bool(result.get("valid")),
                    "detail": str(result.get("detail", ""))}

        threading.Thread(target=_run, daemon=True).start()
        return {"pending": True, "id": req_id}

    @_log_slow
    def get_test_api_key_result(self, req_id):
        """Poll the result of a test_api_key() call."""
        with _TEST_KEY_LOCK:
            entry = _TEST_KEY_RESULTS.get(str(req_id or ""))
        if not entry:
            return {"pending": False, "ok": False, "valid": False,
                    "detail": "unknown id"}
        return {"pending": bool(entry.get("pending")),
                "ok": bool(entry.get("ok")),
                "valid": bool(entry.get("valid")),
                "detail": str(entry.get("detail", ""))}

    @_log_slow
    def check_invite(self, code):
        """First-install invite check (local only). On success the
        install is marked activated and the screen never shows again."""
        if invite_mod is None:
            return {"ok": False, "activated": False}
        try:
            ok = invite_mod.check(code)
            if ok:
                invite_mod.mark_activated()
            return {"ok": ok, "activated": invite_mod.is_activated()}
        except Exception as exc:
            error("app", f"check_invite failed: {exc}")
            return {"ok": False, "activated": False}

    @_log_slow
    def get_activation_state(self):
        if invite_mod is None:
            return {"activated": True}  # no gate when the module is missing
        try:
            return {"activated": invite_mod.is_activated()}
        except Exception:
            return {"activated": False}

    @_log_slow
    def set_deep_think(self, enabled):
        _APP.settings["deep_think_enabled"] = bool(enabled)
        ok = save_settings(_APP.settings)
        info("app", f"Deep Think {'ON' if enabled else 'OFF'}")
        return {"ok": ok,
                "deep_think_enabled": bool(_APP.settings.get(
                    "deep_think_enabled", False))}

    @_log_slow
    def get_deep_think(self):
        return {"deep_think_enabled": bool(_APP.settings.get(
            "deep_think_enabled", False))}

    # -- model choice (v1.6.2) -------------------------------------------
    @_log_slow
    def get_model_options(self):
        """Model picker state: the saved choice + one option per
        model with REAL availability (key present? Ollama reachable?).
        "auto" is always available -- it is the Groq -> Gemini ->
        Ollama chain."""
        choice = str(_APP.settings.get("model_choice", "auto") or "auto")
        choice = choice.strip().lower()
        if choice not in ("auto", "groq", "gemini", "local"):
            choice = "auto"
        try:
            present = keys_status()
        except Exception:
            present = {}
        groq_ok = bool(present.get("groq"))
        gemini_ok = bool(present.get("gemini-chat"))
        try:
            local_ok = bool(brain.ollama_available())
        except Exception:
            local_ok = False
        options = [
            {"id": "auto", "label": "تلقائي", "available": True,
             "reason": ""},
            {"id": "groq", "label": "Groq gpt-oss-120b",
             "available": groq_ok,
             "reason": "" if groq_ok else "محتاج مفتاح Groq"},
            {"id": "gemini", "label": "Gemini 2.5 Flash",
             "available": gemini_ok,
             "reason": "" if gemini_ok else "محتاج مفتاح Gemini"},
            {"id": "local", "label": "محلي Ollama Qwen 2.5 3B",
             "available": local_ok,
             "reason": "" if local_ok else "Ollama مش متثبت"},
        ]
        return {"choice": choice, "options": options}

    @_log_slow
    def set_model_choice(self, choice):
        """Save the model choice (auto/groq/gemini/local). It applies
        to every reply after this -- the brain reads it per turn, so
        no restart is needed. An invalid value is refused."""
        wanted = str(choice or "").strip().lower()
        if wanted not in ("auto", "groq", "gemini", "local"):
            return {"ok": False,
                    "choice": str(_APP.settings.get("model_choice",
                                                    "auto"))}
        _APP.settings["model_choice"] = wanted
        ok = save_settings(_APP.settings)
        info("app", f"Model choice set to {wanted}")
        return {"ok": bool(ok), "choice": wanted}

    @_log_slow
    def get_proactive_feed(self):
        """Companion feed (morning brief, check-ins, battery warnings,
        routine suggestions) + the next exam countdown."""
        feed = []
        try:
            if _APP._companion is not None:
                feed = _APP._companion.get_feed()
        except Exception as exc:
            warn("app", f"get_proactive_feed failed: {exc}")
        exam = self.get_exam_countdown()
        return {"feed": feed, "exam": exam}

    @_log_slow
    def get_exam_countdown(self):
        """Nearest upcoming exam + days left, or an honest 'no date
        set' state - never an invented date."""
        try:
            if companion_mod is not None:
                return companion_mod.get_next_exam(
                    _APP.settings.get("exams") or [])
        except Exception as exc:
            error("app", f"get_exam_countdown failed: {exc}")
        return {"set": False,
                "detail": "مفيش موعد امتحان متسجل — ضيف مواعيدك من الإعدادات"}

    @_log_slow
    def request_clean_cache(self):
        """Clean caches now (GREEN tier, user-initiated from the UI).
        v1.6.2 (B19): runs in a background daemon thread -- the
        bridge returns {"pending": True, "id": ...} at once and the UI
        polls get_clean_cache_result(id). Returns the size freed."""
        req_id = uuid.uuid4().hex[:8]
        _APP._clean_cache_results[req_id] = {"done": False, "result": None}

        def _run():
            if fixer_mod is None:
                out = {"ok": False, "error": "unavailable"}
            else:
                try:
                    result = fixer_mod.clear_temp_files(dry_run=False)
                    out = {"ok": True, **result}
                except Exception as exc:
                    error("app", f"request_clean_cache failed: {exc}")
                    out = {"ok": False, "error": str(exc)}
            _APP._clean_cache_results[req_id] = {"done": True,
                                                 "result": out}

        threading.Thread(target=_run, daemon=True).start()
        return {"pending": True, "id": req_id}

    @_log_slow
    def get_clean_cache_result(self, req_id):
        """Poll the result of a request_clean_cache() call."""
        entry = _APP._clean_cache_results.get(str(req_id or ""), {})
        result = entry.get("result") or {}
        return {"done": bool(entry.get("done")), **result}

    @_log_slow
    def _request_deep_file_access_raw(self, path=None):
        """Deep-file access (RED): starts the permission-card flow in
        a background thread (never blocks the bridge) and returns a
        pending id; poll get_deep_file_access_result(id)."""
        req_id = uuid.uuid4().hex[:8]
        _APP._deep_file_results[req_id] = {"done": False, "result": ""}

        def _run():
            try:
                result = tools.execute_tool(
                    "deep_file_access", {"path": str(path or "")})
            except Exception as exc:
                result = f"مقدرتش أوصل للملفات العميقة: {exc}"
            _APP._deep_file_results[req_id] = {"done": True,
                                               "result": result}
        threading.Thread(target=_run, daemon=True).start()
        return {"pending": True, "id": req_id}

    @_log_slow
    def request_deep_file_access(self, path=None):
        """v1.6.4 reskin: v2 request_deep_file_access — the ported UI
        reads res.message synchronously and never polls, so the
        adapter adds that message at once."""
        return bridge_v2.request_deep_file_access_v2(self, path)

    @_log_slow
    def get_deep_file_access_result(self, req_id):
        entry = _APP._deep_file_results.get(str(req_id or ""), {})
        return {"done": bool(entry.get("done")),
                "result": entry.get("result", "")}

    @_log_slow
    def browser_open_url(self, url):
        if browser_mod is None:
            return {"ok": False, "error": "unavailable"}
        try:
            ok = browser_mod.open_default_browser(url)
            return {"ok": bool(ok), "url": url}
        except Exception as exc:
            error("app", f"browser_open_url failed: {exc}")
            return {"ok": False, "error": str(exc)}

    @_log_slow
    def focus_start(self, work_min=25, break_min=5):
        """Start focus mode: silences non-urgent alerts, runs Pomodoro,
        sets mood مركّز."""
        try:
            if _APP._companion is not None:
                result = _APP._companion.focus_start(
                    work_min=int(work_min or 25),
                    break_min=int(break_min or 5))
            else:
                result = {"ok": False, "reason": "companion_unavailable"}
            _APP.settings["focus_mode"] = True
            save_settings(_APP.settings)
            if _APP._monitor is not None:
                _APP._monitor.set_focus_mode(True)
            return result
        except Exception as exc:
            error("app", f"focus_start failed: {exc}")
            return {"ok": False, "error": str(exc)}

    @_log_slow
    def focus_stop(self):
        try:
            if _APP._companion is not None:
                result = _APP._companion.focus_stop()
            else:
                result = {"ok": False, "reason": "companion_unavailable"}
            _APP.settings["focus_mode"] = False
            save_settings(_APP.settings)
            if _APP._monitor is not None:
                _APP._monitor.set_focus_mode(False)
            return result
        except Exception as exc:
            error("app", f"focus_stop failed: {exc}")
            return {"ok": False, "error": str(exc)}

    @_log_slow
    def get_focus_state(self):
        if _APP._companion is not None:
            try:
                return _APP._companion.get_focus_state()
            except Exception:
                pass
        return {"active": bool(_APP.settings.get("focus_mode", False)),
                "phase": "", "remaining_sec": 0,
                "work_min": 25, "break_min": 5}

    @_log_slow
    def set_ai_name(self, name):
        """Rename the AI from Settings (customizable name, v1.6.0).
        The wake word re-syncs from the new name ("يا {name}") and
        persists with it; the listener picks the new phrase up live.
        The spoken name is never hardcoded - it always comes from
        this setting."""
        cleaned = str(name or "").strip()
        if not cleaned or len(cleaned) > 30 or any(
                ch in cleaned for ch in "\n\r\t"):
            return {"ok": False, "error": "invalid_name"}
        try:
            from settings_store import derive_wake_phrase
            phrase = derive_wake_phrase(cleaned)
        except Exception:
            phrase = f"يا {cleaned}"
        _APP.settings["ai_name"] = cleaned
        _APP.settings["wakeword_phrase"] = phrase
        ok = save_settings(_APP.settings)
        info("app", f"AI renamed to {cleaned} - wake phrase is now {phrase}")
        return {"ok": ok, "ai_name": cleaned, "wakeword_phrase": phrase}

    @_log_slow
    def get_undoable(self):
        """Undoable autonomous-fix actions (killed processes +
        Recycle-Bin files)."""
        if fixer_mod is None:
            return {"ok": False, "entries": []}
        try:
            return {"ok": True, "entries": fixer_mod.list_undoable()}
        except Exception as exc:
            error("app", f"get_undoable failed: {exc}")
            return {"ok": False, "entries": []}

    @_log_slow
    def relaunch_undo(self, index):
        """Relaunch a killed process from the undo list."""
        if fixer_mod is None:
            return {"ok": False, "error": "unavailable"}
        try:
            entries = fixer_mod.list_undoable()
            entry = entries[int(index)]
            if entry.get("kind") != "process":
                return {"ok": False,
                        "error": "only processes can be relaunched"}
            return fixer_mod.relaunch_process(entry)
        except Exception as exc:
            error("app", f"relaunch_undo failed: {exc}")
            return {"ok": False, "error": str(exc)}

    @_log_slow
    def list_undoable(self):
        """Undo list, raw (the UI renders one button per entry and
        passes the entry INDEX back to relaunch_process /
        restore_deleted)."""
        if fixer_mod is None:
            return []
        try:
            return fixer_mod.list_undoable()
        except Exception as exc:
            error("app", f"list_undoable failed: {exc}")
            return []

    @_log_slow
    def relaunch_process(self, index):
        """UI name for relaunch_undo (index into list_undoable)."""
        return self.relaunch_undo(index)

    @_log_slow
    def restore_deleted(self, index):
        """Restore a file previously sent to the Recycle Bin (undo).
        Index into list_undoable()."""
        if fixer_mod is None:
            return {"ok": False, "error": "unavailable"}
        try:
            entries = fixer_mod.list_undoable()
            entry = entries[int(index)]
            if entry.get("kind") != "file":
                return {"ok": False,
                        "error": "only files can be restored"}
            return fixer_mod.restore_deleted(entry)
        except Exception as exc:
            error("app", f"restore_deleted failed: {exc}")
            return {"ok": False, "error": str(exc)}

    @_log_slow
    def set_privacy_mode(self, enabled):
        """Privacy mode on/off (redact secrets before anything is
        sent to the AI). Persists in settings."""
        try:
            _APP.settings["privacy_mode"] = bool(enabled)
            ok = save_settings(_APP.settings)
            info("app", f"Privacy mode {'ON' if enabled else 'OFF'}")
            return {"ok": ok,
                    "privacy_mode": bool(_APP.settings["privacy_mode"])}
        except Exception as exc:
            error("app", f"set_privacy_mode failed: {exc}")
            return {"ok": False, "error": str(exc)}

    @_log_slow
    def analyze_image(self, data_url):
        """Send an attached image to Gemini 2.5 Flash (vision).
        The UI shows the 'this leaves the device' warning and the
        user confirms BEFORE calling this - that confirmation is
        what confirmed=True means here. v1.6.2 (A3): the Gemini call
        (timeout=30) runs in a background daemon thread -- the bridge
        returns {"pending": True, "id": ...} at once and the UI polls
        get_analyze_image_result(id)."""
        try:
            import base64 as _b64
        except Exception as exc:
            req_id = uuid.uuid4().hex[:8]
            _APP._image_results[req_id] = {
                "done": True,
                "result": {"ok": False, "reply": "مقدرتش أجهّز فهم الصور.",
                           "error": str(exc)}}
            return {"pending": True, "id": req_id}
        try:
            raw = str(data_url or "")
            if ";base64," not in raw:
                req_id = uuid.uuid4().hex[:8]
                _APP._image_results[req_id] = {
                    "done": True,
                    "result": {"ok": False,
                               "reply": "الصورة مش بصيغة مفهومة — جرّب صورة تانية.",
                               "reason": "invalid_data"}}
                return {"pending": True, "id": req_id}
            payload = raw.split(";base64,", 1)[1]
            image_bytes = _b64.b64decode(payload, validate=False)
        except Exception as exc:
            req_id = uuid.uuid4().hex[:8]
            _APP._image_results[req_id] = {
                "done": True,
                "result": {"ok": False,
                           "reply": "مقدرتش أقرأ الصورة — جرّب صورة تانية.",
                           "reason": "invalid_data", "error": str(exc)}}
            return {"pending": True, "id": req_id}
        req_id = uuid.uuid4().hex[:8]
        _APP._image_results[req_id] = {"done": False, "result": None}

        def _run():
            try:
                import image_understanding as _img
                res = _img.send_image_to_gemini(
                    image_bytes, "اشرح اللي في الصورة دي بالعربي.",
                    confirmed=True)
                if res.get("ok"):
                    out = {"ok": True, "reply": res.get("text", "")}
                else:
                    out = {"ok": False,
                           "reply": res.get("detail", "مقدرتش أفهم الصورة."),
                           "reason": res.get("reason", "failed")}
            except Exception as exc:
                out = {"ok": False, "reply": "مقدرتش أجهّز فهم الصور.",
                       "error": str(exc)}
            _APP._image_results[req_id] = {"done": True, "result": out}

        threading.Thread(target=_run, daemon=True).start()
        return {"pending": True, "id": req_id}

    @_log_slow
    def get_analyze_image_result(self, req_id):
        """Poll the result of an analyze_image() call."""
        entry = _APP._image_results.get(str(req_id or ""), {})
        result = entry.get("result") or {}
        return {"done": bool(entry.get("done")), **result}

    @_log_slow
    def export_backup(self, password):
        """Export settings + encrypted keys (password from the UI -
        never logged, never stored). v1.6.4 reskin: one arg (the v2
        UI passes the password only); the path defaults under
        DATA_DIR via bridge_v2."""
        return bridge_v2.export_backup(self, password)

    @_log_slow
    def import_backup(self, path, password):
        """Import a settings backup (password from the UI - never
        logged). On success the live settings are reloaded."""
        try:
            import backup as backup_mod
            result = backup_mod.import_settings_backup(path, password)
            if result.get("ok"):
                _APP.settings = load_settings()
            return result
        except Exception as exc:
            error("app", f"import_backup failed: {exc}")
            return {"ok": False, "detail": str(exc)}

    @_log_slow
    def understand_image(self, image_path, prompt, confirmed=False):
        """Image/screenshot understanding (Gemini vision). Refused
        unless confirmed=True (the UI warning + the user's yes).
        v1.6.2 (A3): the Gemini call runs in a background daemon
        thread -- the bridge returns {"pending": True, "id": ...} at
        once and the UI polls get_understand_image_result(id)."""
        req_id = uuid.uuid4().hex[:8]
        _APP._image_results[req_id] = {"done": False, "result": None}

        def _run():
            try:
                result = tools.execute_tool("understand_image", {
                    "image_path": str(image_path or ""),
                    "prompt": str(prompt or ""),
                    "confirmed": bool(confirmed)})
                out = {"ok": True, "text": result}
            except Exception as exc:
                error("app", f"understand_image failed: {exc}")
                out = {"ok": False, "text": str(exc)}
            _APP._image_results[req_id] = {"done": True, "result": out}

        threading.Thread(target=_run, daemon=True).start()
        return {"pending": True, "id": req_id}

    @_log_slow
    def get_understand_image_result(self, req_id):
        """Poll the result of an understand_image() call."""
        entry = _APP._image_results.get(str(req_id or ""), {})
        result = entry.get("result") or {}
        return {"done": bool(entry.get("done")), **result}

    # -- windows -----------------------------------------------------------
    @_log_slow
    def open_main(self):
        _APP.show_main()
        return {"ok": True}

    @_log_slow
    def island_resize(self, w, h):
        # v1.6.4 fixed canvas (WindyLand/Nusa): the island window is
        # NEVER resized anymore. This is a STATE SHIM for the old call
        # path (island.html calls island_expand/island_collapse, which
        # call this with 720x400 / 380x60): it records the state and
        # toggles WS_EX_TRANSPARENT so the invisible canvas band stays
        # click-through while collapsed. The visual animation is the
        # .open class in island.html, inside the fixed 720x400 canvas.
        # No window resize, no region re-application - the region was
        # applied once at creation and the shape is drawn, not cut.
        try:
            expanded = int(w) >= ISLAND_CANVAS_W
            _APP._island_expanded = bool(expanded)
            hwnd = _APP._island_hwnd
            if hwnd:
                island_canvas.set_click_through(hwnd, not expanded)
        except Exception as exc:
            error("ui", f"Could not apply island state: {exc}")
        return {"ok": True}

    # -- island features (v1.6.4, app/island_features.py) ----------------
    # v1.6.4 perf (a): the slow island calls run off the bridge.
    def _run_island_job(self, fn):
        """Run fn() on a daemon thread; return {"pending": True, "id"}
        at once. The island UI polls job_result(id) until done.
        Same shape as test_api_key / check_updates."""
        req_id = uuid.uuid4().hex[:8]
        with _ISLAND_JOBS_LOCK:
            _ISLAND_JOBS[req_id] = {"done": False,
                                    "created": time.time(),
                                    "result": None}
            if len(_ISLAND_JOBS) > 50:  # prune oldest finished entries
                for old_id in sorted(
                        _ISLAND_JOBS,
                        key=lambda i: _ISLAND_JOBS[i].get("created", 0))[:10]:
                    if _ISLAND_JOBS[old_id].get("done"):
                        _ISLAND_JOBS.pop(old_id, None)

        def _run():
            try:
                result = fn() or {}
            except Exception as exc:
                result = {"ok": False, "detail": str(exc)}
            with _ISLAND_JOBS_LOCK:
                entry = _ISLAND_JOBS.get(req_id)
                if entry is not None:
                    entry["done"] = True
                    entry["result"] = result

        threading.Thread(target=_run, daemon=True).start()
        return {"pending": True, "id": req_id}

    @_log_slow
    def island_music(self):
        # v1.6.4 perf (a): SMTC read (up to 5 s) off the bridge.
        import island_features as _isl
        return self._run_island_job(_isl.now_playing)

    @_log_slow
    def island_lyrics(self, artist, title, album="", duration=0):
        # v1.6.4 perf (a): LRCLIB network call (up to 8 s) off the
        # bridge. Cached per track inside island_features.lyrics.
        import island_features as _isl
        return self._run_island_job(
            lambda: _isl.lyrics(artist, title, album, duration))

    @_log_slow
    def island_timer_state(self):
        import island_features as _isl
        return _isl.get_timer().state()

    @_log_slow
    def island_timer_start(self, minutes):
        import island_features as _isl
        return _isl.get_timer().start_countdown(minutes)

    @_log_slow
    def island_stopwatch_start(self):
        import island_features as _isl
        return _isl.get_timer().start_stopwatch()

    @_log_slow
    def island_timer_pause(self):
        import island_features as _isl
        return _isl.get_timer().pause()

    @_log_slow
    def island_timer_resume(self):
        import island_features as _isl
        return _isl.get_timer().resume()

    @_log_slow
    def island_timer_reset(self):
        import island_features as _isl
        return _isl.get_timer().reset()

    @_log_slow
    def island_timer_lap(self):
        import island_features as _isl
        return _isl.get_timer().lap()

    @_log_slow
    def island_task_add(self, text, due=None, remind_at=None):
        import tasks as _tasks
        return _tasks.add_task(text, due=due, remind_at=remind_at)

    @_log_slow
    def island_task_set_due(self, task_id, due=None, remind_at=None):
        import tasks as _tasks
        return _tasks.set_task_due(task_id, due=due, remind_at=remind_at)

    @_log_slow
    def island_tasks_due(self):
        import island_features as _isl
        return _isl.due_task_reminders()

    @_log_slow
    def island_clipboard_list(self):
        # v1.6.4 perf (d): LAZY start - the clipboard watcher starts
        # the first time this panel is opened, never at launch.
        _APP._start_island_clipboard()
        import island_features as _isl
        return _isl.get_clipboard().list()

    @_log_slow
    def island_clipboard_set_enabled(self, enabled):
        """Enable/disable background clipboard capture while the panel
        is closed. Default False (see settings_store): history is
        captured only while the panel has been opened, unless the
        user explicitly opts into background capture. Disabling
        stops the watcher; enabling starts it at once."""
        on = bool(enabled)
        _APP.settings["island_clipboard_enabled"] = on
        save_settings(_APP.settings)
        if on:
            _APP._start_island_clipboard()
        else:
            _APP._stop_island_clipboard()
        return {"ok": True, "island_clipboard_enabled": on}

    @_log_slow
    def island_clipboard_pin(self, entry_id, pinned):
        import island_features as _isl
        return _isl.get_clipboard().pin(entry_id, pinned)

    @_log_slow
    def island_clipboard_delete(self, entry_id):
        import island_features as _isl
        return _isl.get_clipboard().delete(entry_id)

    @_log_slow
    def island_clipboard_clear(self):
        import island_features as _isl
        return _isl.get_clipboard().clear()

    @_log_slow
    def island_clipboard_copy(self, entry_id):
        import island_features as _isl
        return _isl.get_clipboard().copy_back(entry_id)

    @_log_slow
    def island_shelf_list(self):
        import island_features as _isl
        return _isl.shelf_list()

    @_log_slow
    def island_shelf_add(self):
        # Add-via-picker: WM_DROPFILES drag-in is not feasible in
        # pywebview, so the shelf adds through the file dialog.
        import island_features as _isl
        try:
            paths = _APP.island_window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=True)
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}
        if not paths:
            return {"ok": False, "detail": "cancelled"}
        added = []
        for path in paths:
            res = _isl.shelf_add(path)
            if res.get("ok"):
                added.append(path)
        return {"ok": bool(added),
                "files": _isl.shelf_list().get("files", [])}

    @_log_slow
    def island_shelf_remove(self, entry_id):
        import island_features as _isl
        return _isl.shelf_remove(entry_id)

    @_log_slow
    def island_shelf_open(self, entry_id):
        import island_features as _isl
        return _isl.shelf_open(entry_id)

    @_log_slow
    def island_water_state(self):
        import island_features as _isl
        return _isl.water_state()

    @_log_slow
    def island_water_add(self, ml):
        import island_features as _isl
        return _isl.water_add(ml)

    @_log_slow
    def island_water_goal(self, ml):
        import island_features as _isl
        return _isl.water_set_goal(ml)

    @_log_slow
    def island_water_reminder(self, enabled, interval_min=None):
        import island_features as _isl
        return _isl.water_set_reminder(enabled, interval_min)

    @_log_slow
    def island_prayer(self, method="egypt"):
        import island_features as _isl
        return _isl.prayer_times(method=method)

    @_log_slow
    def island_notifications(self):
        # v1.6.4 perf (a): WinRT notification read (asyncio.run, no
        # timeout of its own) off the bridge - it used to block the
        # whole app for seconds on first WinRT init.
        import island_features as _isl
        return self._run_island_job(
            lambda: _isl.recent_notifications(_APP.settings))

    @_log_slow
    def island_media_indicators(self):
        import island_features as _isl
        return _isl.media_indicators()

    @_log_slow
    def minimize_to_island(self):
        # v1.4.1: the island is gone - this now hides the main window
        # to the system tray (MemoApp.hide_main branches on the flag).
        # Kept under its old bridge name for compatibility.
        _APP.hide_main()
        return {"ok": True}

    @_log_slow
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

    @_log_slow
    def set_pet_bubble(self, on):
        """The pet page shows/hides the "مياو!" bubble: the window region
        must include the bubble zone only while it is visible (v1.3.1)."""
        # v1.4.1: the pet is disabled - no-op.
        if not _APP.settings.get("pet_enabled", False):
            return {"ok": False, "disabled": True}
        _APP.set_pet_bubble(bool(on))
        return {"ok": True}

    @_log_slow
    def park_pet(self):
        # v1.4.1: the pet is disabled - no-op.
        if not _APP.settings.get("pet_enabled", False):
            return {"ok": False, "disabled": True}
        _APP.settings["pet_parked"] = True
        save_settings(_APP.settings)
        _APP.park_pet()
        return {"ok": True}

    @_log_slow
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

    @_log_slow
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

    @_log_slow
    def pet_interaction(self, kind):
        # v1.4.1: the pet is disabled - no-op (nothing to log).
        if not _APP.settings.get("pet_enabled", False):
            return {"ok": True, "disabled": True}
        if kind == "click":
            info("pet", "Pet clicked - happy reaction (مياو!)")
        return {"ok": True}

    # -- in-app updates (v1.2) ---------------------------------------------
    @_log_slow
    def check_updates(self):
        """v1.6.2 (A2): the manifest fetch (up to 3x15s + sleeps) runs
        in a background thread -- this returns {"checking": true} at
        once and never blocks the bridge. The UI polls
        get_check_updates_result()."""
        with _CHECK_UPDATES_LOCK:
            if _CHECK_UPDATES["checking"]:
                return {"pending": True,
                        "id": _CHECK_UPDATES.get("id")}
            req_id = uuid.uuid4().hex[:8]
            _CHECK_UPDATES.update({"checking": True, "update": None,
                                   "error": None, "id": req_id})

        def _run():
            try:
                result = updater.check_update()
                with _CHECK_UPDATES_LOCK:
                    _CHECK_UPDATES.update({"checking": False,
                                           "update": result, "error": None})
            except Exception as exc:
                error("update", f"check_updates failed: {exc}")
                with _CHECK_UPDATES_LOCK:
                    _CHECK_UPDATES.update({"checking": False,
                                           "update": None,
                                           "error": str(exc)})

        threading.Thread(target=_run, daemon=True).start()
        return {"pending": True, "id": req_id}

    @_log_slow
    def get_check_updates_result(self):
        """Poll the result of a check_updates() call: {"checking",
        "update" (the dict check_update() used to return, or None),
        "error" (or None)}."""
        with _CHECK_UPDATES_LOCK:
            return {"checking": bool(_CHECK_UPDATES["checking"]),
                    "update": _CHECK_UPDATES["update"],
                    "error": _CHECK_UPDATES["error"]}

    @_log_slow
    def get_update_progress(self):
        """v1.3.1: the Settings UI polls this while an update downloads
        (downloaded/total/percent/state) to render the progress bar."""
        return updater.get_progress()

    @_log_slow
    def download_update(self):
        """v1.4.3: download ONLY - the UI polls get_update_progress()
        until state is "downloaded", then shows the Install-now /
        Install-later choice slide. Nothing installs here, ever."""
        info("update", "Download requested from Settings")
        def _run():
            updater.download_update()
        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True, "downloading": True}

    @_log_slow
    def get_downloaded_update(self):
        """v1.4.3: the saved downloaded-but-not-installed update
        (version/kind/path/valid/remind), or {"downloaded": False}."""
        return updater.get_downloaded_update()

    @_log_slow
    def dismiss_update_reminder(self):
        """v1.4.3: "Install later" - record that the reminder was shown
        today so it pops at most once a day (the UI also keeps a
        per-session flag). The downloaded file itself is untouched."""
        updater.mark_reminder_shown()
        return {"ok": True}

    @_log_slow
    def install_update(self):
        """v1.4.3: "Install now" - installs ONLY an already-downloaded
        update (see updater.apply_downloaded). If the saved file is
        gone or corrupt, a fresh download starts instead and the UI
        shows the choice slide again once it finishes; a broken file
        is never installed. After a successful install the app
        restarts, like before."""
        info("update", "Install requested from Settings")
        def _run():
            result = updater.apply_downloaded()
            if result.get("ok"):
                info("update", "Update installed - restarting LONA")
                time.sleep(0.5)
                try:
                    if getattr(sys, "frozen", False):
                        # Frozen: the setup was already launched by
                        # apply_downloaded; just quit so it can run.
                        pass
                    else:
                        import hidden_spawn
                        subprocess.Popen([sys.executable, str(APP_DIR / "main.py")],
                                         cwd=str(BASE_DIR),
                                         **hidden_spawn.popen_kwargs())
                except Exception as exc:
                    error("update", f"Could not restart after update: {exc}")
                    return
                _APP.quit()
                return
            if result.get("redownload"):
                info("update", "Saved update unusable - re-downloading it")
                updater.download_update()
        threading.Thread(target=_run, daemon=True).start()
        return {"pending": True, "id": uuid.uuid4().hex[:8],
                "ok": True, "installing": True}

    @_log_slow
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

    @_log_slow
    def open_portfolio(self):
        webbrowser.open("https://mohamed5946.github.io/portfolio/")
        return {"ok": True}

    @_log_slow
    def open_github(self):
        # v1.4.1: the About section's GitHub button (public info only,
        # same as the "who is the developer?" answer).
        webbrowser.open("https://github.com/mohamed5946")
        return {"ok": True}

    # -- frameless main-window controls (v1.6.2 item 3) -------------------
    # The main window is frameless (no native title bar); the in-app
    # header owns these. The island is a separate window and is never
    # touched by any of them.
    @_log_slow
    def window_minimize(self):
        """Minimize the main window to the taskbar."""
        try:
            if _APP.main_window is not None:
                _APP.main_window.minimize()
            return {"ok": True}
        except Exception as exc:
            error("app", f"window_minimize failed: {exc}")
            return {"ok": False, "error": str(exc)}

    def _main_is_maximized(self) -> bool:
        """Maximized state from Win32, not from _main_maximized.

        GetWindowPlacement().showCmd == SW_SHOWMAXIMIZED (3) is the
        truth even when the user maximized via Win+Up / double-click
        on the title bar, which never touches _main_maximized.
        Falls back to the flag off Windows or on any failure."""
        try:
            if sys.platform != "win32":
                return bool(_APP._main_maximized)
            hwnd = _find_main_hwnd()
            if not hwnd:
                return bool(_APP._main_maximized)

            class WINDOWPLACEMENT(ctypes.Structure):
                _fields_ = [("length", ctypes.c_uint),
                            ("flags", ctypes.c_uint),
                            ("showCmd", ctypes.c_uint),
                            ("ptMinPosition", ctypes.c_long * 2),
                            ("ptMaxPosition", ctypes.c_long * 2),
                            ("rcNormalPosition", ctypes.c_long * 4)]

            placement = WINDOWPLACEMENT()
            placement.length = ctypes.sizeof(WINDOWPLACEMENT)
            if not ctypes.windll.user32.GetWindowPlacement(
                    hwnd, ctypes.byref(placement)):
                return bool(_APP._main_maximized)
            return placement.showCmd == 3  # SW_SHOWMAXIMIZED
        except Exception:
            return bool(_APP._main_maximized)

    @_log_slow
    def window_toggle_maximize(self):
        """Toggle the main window between maximized and restored.
        Returns {"maximized": bool} -- the state AFTER the toggle."""
        try:
            if _APP.main_window is not None:
                if self._main_is_maximized():
                    _APP.main_window.restore()
                    _APP._main_maximized = False
                else:
                    _APP.main_window.maximize()
                    _APP._main_maximized = True
            maximized = self._main_is_maximized()
            _APP._main_maximized = maximized
            return {"maximized": bool(maximized)}
        except Exception as exc:
            error("app", f"window_toggle_maximize failed: {exc}")
            return {"maximized": bool(_APP._main_maximized),
                    "ok": False, "error": str(exc)}

    @_log_slow
    def window_close(self):
        """Close the program -- exactly the same path as the old
        quit_app() (background quit thread, tray stopped first)."""
        info("app", "Close requested from the frameless header")
        threading.Thread(target=_APP.quit, daemon=True).start()
        return {"ok": True}

    @_log_slow
    def window_move_to(self, x, y):
        """Move the main window so its top-left corner lands at
        (x, y) screen coordinates. The frontend header computes the
        coordinates from the grab offset while dragging."""
        try:
            if _APP.main_window is not None:
                _APP.main_window.move(int(x), int(y))
            return {"ok": True, "x": int(x), "y": int(y)}
        except Exception as exc:
            error("app", f"window_move_to failed: {exc}")
            return {"ok": False, "error": str(exc)}

    @_log_slow
    def quit_app(self):
        info("app", "Quit requested from inside the app")
        threading.Thread(target=_APP.quit, daemon=True).start()
        return {"ok": True}

    # -- logs --------------------------------------------------------------
    @_log_slow
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

    @_log_slow
    def get_logs(self):
        return {"logs": read_recent(200)}

    @_log_slow
    def get_log_text(self):
        return {"text": log_text(200)}

    # -- long-term memory (v1.4.2) -----------------------------------------
    @_log_slow
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

    @_log_slow
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

    @_log_slow
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
    @_log_slow
    def get_alerts(self):
        """Recent system/notification alerts for the in-app banner.
        The UI polls this and renders unseen ids."""
        with _APP._alerts_lock:
            return {"alerts": [dict(a) for a in _APP._alerts]}

    # -- system monitor (v1.4.2) -------------------------------------------
    @_log_slow
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
                status = _fallback_monitor().get_status()
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

    @_log_slow
    def save_monitor_settings(self, payload):
        """Save monitor thresholds/toggles from Settings; restart the
        background monitor so the new thresholds apply at once.
        v1.6.2 (B18): the monitor stop/start joins (up to ~4s) run in
        a background thread -- the bridge returns {"pending": True,
        "id": ...} at once and the UI polls get_settings_save_result(id)."""
        try:
            data = json.loads(payload) if isinstance(payload, str) else payload
        except Exception as exc:
            error("app", f"Bad monitor settings payload: {exc}")
            req_id = uuid.uuid4().hex[:8]
            _APP._settings_save_results[req_id] = {
                "done": True, "result": {"ok": False}}
            return {"pending": True, "id": req_id}
        req_id = uuid.uuid4().hex[:8]
        _APP._settings_save_results[req_id] = {"done": False, "result": None}

        def _run():
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
            _APP._settings_save_results[req_id] = {
                "done": True, "result": {"ok": bool(ok)}}

        threading.Thread(target=_run, daemon=True).start()
        return {"pending": True, "id": req_id}

    @_log_slow
    def get_settings_save_result(self, req_id):
        """Poll the result of a save_monitor_settings() /
        save_notification_settings() call."""
        entry = _APP._settings_save_results.get(str(req_id or ""), {})
        result = entry.get("result") or {}
        return {"done": bool(entry.get("done")),
                "ok": bool(result.get("ok"))}

    # -- important notifications (v1.4.2) -----------------------------------
    @_log_slow
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

    @_log_slow
    def save_notification_settings(self, payload):
        """Save the notification filter lists / toggle; restart the
        watcher so the new filters apply at once. v1.6.2 (B18): the
        watcher stop/start joins run in a background thread -- the
        bridge returns {"pending": True, "id": ...} at once and the UI
        polls get_settings_save_result(id)."""
        try:
            data = json.loads(payload) if isinstance(payload, str) else payload
        except Exception as exc:
            error("app", f"Bad notification settings payload: {exc}")
            req_id = uuid.uuid4().hex[:8]
            _APP._settings_save_results[req_id] = {
                "done": True, "result": {"ok": False}}
            return {"pending": True, "id": req_id}
        req_id = uuid.uuid4().hex[:8]
        _APP._settings_save_results[req_id] = {"done": False, "result": None}

        def _run():
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
            _APP._settings_save_results[req_id] = {
                "done": True, "result": {"ok": bool(ok)}}

        threading.Thread(target=_run, daemon=True).start()
        return {"pending": True, "id": req_id}

    @_log_slow
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
    @_log_slow
    def get_whatsapp_status(self):
        """Link state for the Settings UI (status + QR + masked number).
        The brain reads the same state via whatsapp.get_state() to answer
        truthfully about WhatsApp (v1.3.2 system prompt).
        v1.6.4 reskin: translated to the v2 UI's shape by bridge_v2
        (connected/number/qr_data_url/message)."""
        return bridge_v2.get_whatsapp_status_v2(self)

    @_log_slow
    def whatsapp_save(self, payload):
        """Save WhatsApp settings from the UI: {enabled, owner_phone}.
        Starts/stops the sidecar to match. (Api.save_settings and its
        key whitelist are untouched by v1.4.0 - this is a separate path.)
        v1.6.2 (B18): the whole save -- including the disable path's
        stop_service() (up to 5s) -- runs in a background thread; the
        bridge returns {"pending": True, "id": ...} at once and the UI
        polls get_whatsapp_save_result(id)."""
        if whatsapp_mod is None:
            req_id = uuid.uuid4().hex[:8]
            _APP._whatsapp_save_results[req_id] = {
                "done": True,
                "result": {"ok": False,
                           "error": "WhatsApp module not available"}}
            return {"pending": True, "id": req_id}
        try:
            data = json.loads(payload) if isinstance(payload, str) else payload
        except Exception as exc:
            error("app", f"Bad WhatsApp settings payload: {exc}")
            req_id = uuid.uuid4().hex[:8]
            _APP._whatsapp_save_results[req_id] = {
                "done": True, "result": {"ok": False}}
            return {"pending": True, "id": req_id}
        req_id = uuid.uuid4().hex[:8]
        _APP._whatsapp_save_results[req_id] = {"done": False, "result": None}

        def _run():
            if "enabled" in data:
                _APP.settings["whatsapp_enabled"] = bool(data["enabled"])
            if "owner_phone" in data:
                _APP.settings["whatsapp_owner_phone"] = \
                    whatsapp_mod.normalize_number(str(data["owner_phone"]))
            ok = save_settings(_APP.settings)
            if _APP.settings.get("whatsapp_enabled", False):
                svc = whatsapp_mod.ensure_started(_APP.settings)
                if svc is None or not svc.start():
                    out = {"ok": False,
                           "error": "could not start WhatsApp sidecar (see log)",
                           "state": whatsapp_mod.get_state()}
                else:
                    out = {"ok": ok, "state": whatsapp_mod.get_state()}
            else:
                whatsapp_mod.stop_service()
                out = {"ok": ok, "state": whatsapp_mod.get_state()}
            _APP._whatsapp_save_results[req_id] = {"done": True,
                                                   "result": out}

        threading.Thread(target=_run, daemon=True).start()
        return {"pending": True, "id": req_id}

    @_log_slow
    def get_whatsapp_save_result(self, req_id):
        """Poll the result of a whatsapp_save() call."""
        entry = _APP._whatsapp_save_results.get(str(req_id or ""), {})
        result = entry.get("result") or {}
        return {"done": bool(entry.get("done")), **result}

    @_log_slow
    def whatsapp_disconnect(self):
        """Log out + wipe the session (fresh QR next time). v1.6.2
        (B18): logout waits on the sidecar (up to 15s), so it runs in
        a background thread -- the bridge returns {"pending": True,
        "id": ...} at once and the UI polls
        get_whatsapp_action_result(id)."""
        req_id = uuid.uuid4().hex[:8]
        _APP._whatsapp_action_results[req_id] = {"done": False,
                                                 "result": None}

        def _run():
            if whatsapp_mod is None:
                out = {"ok": False}
            else:
                svc = whatsapp_mod.get_service()
                if svc is not None:
                    svc.logout()
                out = {"ok": True}
            _APP._whatsapp_action_results[req_id] = {"done": True,
                                                     "result": out}

        threading.Thread(target=_run, daemon=True).start()
        return {"pending": True, "id": req_id}

    @_log_slow
    def whatsapp_relink(self):
        """v1.4.3: clean re-link in one click (used by the degraded
        WhatsApp state): log out + wipe the broken session keys, then
        start the sidecar again so a fresh QR appears. The old keys
        are gone either way — even if the restart itself fails, the
        next enable/start shows a fresh QR instead of reusing the
        undecryptable session. v1.6.2 (B18): background thread + poll
        (same shape as whatsapp_disconnect)."""
        req_id = uuid.uuid4().hex[:8]
        _APP._whatsapp_action_results[req_id] = {"done": False,
                                                 "result": None}

        def _run():
            if whatsapp_mod is None:
                out = {"ok": False,
                       "error": "WhatsApp module not available"}
            else:
                svc = whatsapp_mod.get_service()
                if svc is not None:
                    svc.logout()
                if _APP.settings.get("whatsapp_enabled", False):
                    svc = whatsapp_mod.ensure_started(_APP.settings)
                    if svc is None:
                        out = {"ok": False,
                               "error": "could not start WhatsApp sidecar (see log)",
                               "state": whatsapp_mod.get_state()}
                    else:
                        out = {"ok": True,
                               "state": whatsapp_mod.get_state()}
                else:
                    out = {"ok": True,
                           "state": whatsapp_mod.get_state()}
            _APP._whatsapp_action_results[req_id] = {"done": True,
                                                     "result": out}

        threading.Thread(target=_run, daemon=True).start()
        return {"pending": True, "id": req_id}

    @_log_slow
    def get_whatsapp_action_result(self, req_id):
        """Poll the result of a whatsapp_disconnect() /
        whatsapp_relink() call."""
        entry = _APP._whatsapp_action_results.get(str(req_id or ""), {})
        result = entry.get("result") or {}
        return {"done": bool(entry.get("done")), **result}


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
        # v1.6.0: proactive companion + deep-file request results
        # (created in run() - best effort like the monitor).
        self._companion = None
        self._deep_file_results: dict = {}
        # v1.6.2: background-task results for the async bridge methods
        # (same pending/id + poll shape as _deep_file_results):
        # image understanding, cache cleaning, settings saves,
        # WhatsApp actions. Each entry: {"done": bool, "result": ...}.
        self._image_results: dict = {}
        self._clean_cache_results: dict = {}
        self._settings_save_results: dict = {}
        self._whatsapp_action_results: dict = {}
        self._whatsapp_save_results: dict = {}
        # v1.6.2: the Chrome native-host client (extension bridge),
        # started in run() on a background thread (None = not started).
        self._native_host_client = None
        # v1.5: wake-word listener (created in run() when enabled -
        # best effort like the monitor; None = never started / module
        # missing). _speaking_until is the mic guard: while the app is
        # playing a reply, its own voice must never re-wake it.
        self._wakeword = None
        self._speaking_until = 0.0
        self._alerts: list = []
        self._alerts_lock = threading.Lock()
        self._alert_seq = 0
        self.api = Api()
        self.main_window = None
        self.island_window = None
        # v1.6.2: frameless main window -- pywebview exposes no
        # "is maximized" query, so the toggle tracks it here. It can
        # desync if the user maximizes some other way (Win+Up); the
        # header button is the normal path, so this stays correct.
        self._main_maximized = False
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
        self._island_expanded = False
        self._island_stop = threading.Event()
        self._island_clipboard_thread = None
        # v1.6.4 perf (d): the clipboard watcher is LAZY - it starts
        # on first clipboard-panel open (or when the
        # island_clipboard_enabled setting opts into background
        # capture), never at launch. Its own stop event lets the
        # setting turn it off without touching _island_stop (quit).
        self._island_clipboard_stop = threading.Event()
        self._island_clipboard_lock = threading.Lock()
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
                 "assets/lona.css", "assets/bridge.js",
                 "assets/mascot.webp", "assets/fonts/fonts.css",
                 "assets/avatar-cat.png", "assets/avatar-dog.png",
                 "assets/avatar-boy.png", "assets/avatar-girl.png"]
        if self.settings.get("island_enabled", False):
            files.append("island.html")
        try:
            if invite_mod is not None and not invite_mod.is_activated():
                files.append("invite.html")
        except Exception:
            pass
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
        return (max(0, (sw - ISLAND_CANVAS_W) // 2), 12,
                ISLAND_CANVAS_W, ISLAND_CANVAS_H)

    def _open_island_from_hover(self):
        """Passthrough loop: the cursor is over the pill while the
        window is click-through. Open via the page (it owns the .open
        class, which calls island_expand -> the island_resize shim)."""
        if self._island_expanded or self.island_window is None:
            return
        try:
            self.island_window.evaluate_js("openIslandFromHover()")
        except Exception:
            pass

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
        """v1.6.4 fixed canvas: NO-OP by design. The region is applied
        ONCE at creation for the full canvas (island_canvas.
        apply_island_canvas_region) and never touched again - the shape
        is drawn in HTML/CSS, the region is only the hit-test bounding
        box (Nusa lesson). Kept so old callers keep working."""
        return

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
            # v1.6.0: self-view summary (mood, own usage, temp) goes
            # into the system prompt each turn; deep think + privacy
            # mode come from settings. All guarded - a failed summary
            # never blocks the chat.
            self_view = ""
            try:
                if selfview_mod is not None:
                    mood = "صاحي"
                    if self._monitor is not None:
                        mood = self._monitor.get_device_overview().get(
                            "mood", "صاحي")
                    settings = dict(self.settings)
                    settings["deep_file_access_granted"] = \
                        tools.is_deep_file_access_granted()
                    self_view = selfview_mod.self_view_summary(
                        mood=mood, monitor=self._monitor,
                        settings=settings)
            except Exception:
                self_view = ""
            result = brain.ask_with_tools(
                text, self.settings.get("ai_name", "LONA"),
                self.settings.get("user_name", "Mohamed"),
                self.settings.get("language", "ar"),
                # v1.4.1: the model MUST know when owner mode is open,
                # or its system prompt refuses source-code questions
                # with "تواصل مع المطور" even after a successful unlock.
                owner_unlocked=self.owner_unlocked,
                deep_think=bool(self.settings.get(
                    "deep_think_enabled", False)),
                privacy_mode=self.settings.get("privacy_mode", True),
                self_view=self_view,
                # v1.6.2: the saved model choice (auto = the chain,
                # anything else = that model only, strict).
                model_choice=self.settings.get("model_choice", "auto"))
        except Exception as exc:
            error("brain", f"Tool chat failed: {exc}")
            result = {"reply": "حصلت مشكلة وأنا بنفذ، جرّب تاني.",
                      "engine": "", "tools_used": 0}
        # v1.4.3: [[IMAGE:...]] is a machine token, not text - strip it
        # from the spoken/shown reply and attach the files as `images`
        # instead, so the UI renders them as real pictures. The tracker
        # is the safety net: even if the model forgot to echo the
        # marker, the image the tool saved this turn still reaches
        # the chat.
        clean_reply, marker_images = tools.extract_image_markers(
            result["reply"])
        tracked = tools.take_generated_images()
        images = marker_images + [n for n in tracked
                                  if n not in marker_images]
        with _chats_lock:
            entry = _chats.get(chat_id)
            if entry is not None:
                entry.update({"done": True, "reply": clean_reply,
                              "engine": result["engine"],
                              "tools_used": result.get("tools_used", 0),
                              "images": images})
        self.speak_async(clean_reply)

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
                    notif_mod.show_toast(item["title"] or "LONA", msg)
                except Exception:
                    pass
            # v1.6.4: brief island expansion for the alert (WindyLand
            # toast). The page owns open/auto-collapse; Python only
            # hands it the payload (monitor.alert_to_toast).
            try:
                if self.settings.get("island_enabled", False) \
                        and self.island_window is not None:
                    import monitor as monitor_mod
                    import json as _json
                    payload = monitor_mod.alert_to_toast(item)
                    if payload["title"] or payload["body"]:
                        self.island_window.evaluate_js(
                            "showIslandToast("
                            + _json.dumps(payload["title"],
                                         ensure_ascii=False) + ", "
                            + _json.dumps(payload["body"],
                                         ensure_ascii=False) + ", 6000)")
            except Exception:
                pass
        except Exception as exc:
            warn("app", f"Alert delivery failed: {exc}")

    def _start_native_host(self) -> None:
        """Chrome-extension native host (v1.6.2 re-wire). Starts the
        listener on a background thread and hands the shared client
        to native_host so browser_control.send_command() and
        selfview's is_connected probe work end-to-end. Best effort:
        a missing/broken native_host module never blocks startup."""
        if native_host_mod is None:
            return
        if self._native_host_client is not None:
            return

        def _run():
            try:
                client = native_host_mod.start_native_host_listener()
                if client is not None:
                    native_host_mod.set_shared_client(client)
                    self._native_host_client = client
                    info("app", "Native host listener started "
                                "(extension bridge live)")
            except Exception as exc:
                warn("app", f"Native host listener failed: {exc}")

        threading.Thread(target=_run, daemon=True).start()

    def _start_monitor(self) -> None:
        """Background system monitor (v1.4.2). Best effort."""
        # v1.6.2 (A5): idempotent -- run() warms the monitor BEFORE
        # the first window, and the settings-save path restarts it;
        # a second start while one is alive is a no-op.
        if self._monitor is not None:
            return
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
        # v1.4.3: the system_status tool reads through this live
        # monitor (warm CPU history, shared temperature cache).
        tools.set_monitor_provider(lambda: _APP._monitor)
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


    def _start_companion(self) -> None:
        """Proactive companion (v1.6.0). Best effort - never blocks
        startup. Morning brief, check-ins, battery warnings, focus
        mode and routine suggestions all flow through _on_system_alert
        (the in-app alerts channel)."""
        if companion_mod is None:
            warn("app", "Companion module unavailable")
            return
        if not self.settings.get("companion_enabled", True):
            info("app", "Companion disabled in settings")
            return
        try:
            self._companion = companion_mod.Companion(
                self.settings, on_alert=self._on_system_alert,
                monitor_fn=(lambda: self._monitor.get_status()
                            if self._monitor is not None else None))
            if self._companion.start():
                info("app", "Proactive companion started")
            else:
                self._companion = None
        except Exception as exc:
            warn("app", f"Companion did not start: {exc}")
            self._companion = None

    def _start_tempwatch(self) -> None:
        """Background temperature monitor registration (v1.6.0): HKCU
        Run ("LONA TempWatch") so it runs even when the app is closed.
        Best effort - a failed registration never blocks startup."""
        if tempwatch_mod is None:
            warn("app", "TempWatch module unavailable")
            return
        try:
            tempwatch_mod.apply_tempwatch_setting(
                bool(self.settings.get("tempwatch_enabled", True)))
        except Exception as exc:
            warn("app", f"TempWatch registration failed: {exc}")

    def _companion_morning_brief(self) -> None:
        """One short device brief on startup (battery/RAM/temp/exams),
        into the proactive feed + in-app alerts (not spoken - the
        greeting thread owns the spoken greeting)."""
        if self._companion is None:
            return
        try:
            status = self._monitor.get_status() \
                if self._monitor is not None else None
            self._companion.morning_brief(status)
        except Exception as exc:
            warn("app", f"Companion morning brief failed: {exc}")

    # -- wake word "يا لونا" (v1.5) --------------------------------------
    def _wake_is_speaking(self) -> bool:
        """Mic guard: True while the app is playing a spoken reply
        (the listener ignores detections then - its own TTS must
        never re-wake it)."""
        return time.time() < self._speaking_until

    def start_wakeword(self):
        """Start the always-listening wake-word listener (best
        effort - a missing module / model / mic never blocks anything;
        the state surfaces honestly in Settings)."""
        if wakeword_mod is None:
            warn("wakeword", "Wake-word module unavailable - feature off")
            return
        if not self.settings.get("wakeword_enabled", True):
            return
        if self._wakeword is not None and self._wakeword.running:
            return
        try:
            self._wakeword = wakeword_mod.WakeWordListener(
                self.settings, on_wake=self._on_wake_word,
                is_speaking=self._wake_is_speaking)
            self._wakeword.start()
        except Exception as exc:
            warn("wakeword", f"Wake-word listener did not start: {exc}")
            self._wakeword = None

    def stop_wakeword(self):
        """Stop the listener fully (toggle off / quitting). The mic
        is closed; the model object is dropped."""
        if self._wakeword is not None:
            try:
                self._wakeword.stop()
            except Exception:
                pass
            self._wakeword = None

    def pause_wakeword(self):
        """Pause listening (turn-based mic recording). No-op when the
        listener is not running."""
        if self._wakeword is not None:
            try:
                self._wakeword.pause()
            except Exception:
                pass

    def resume_wakeword(self):
        """Resume listening after a pause. No-op when not running."""
        if self._wakeword is not None:
            try:
                self._wakeword.resume()
            except Exception:
                pass

    def get_wakeword_status(self) -> dict:
        if wakeword_mod is None:
            return {"enabled": False, "state": "unavailable",
                    "detail": "wake-word module not available in "
                              "this build",
                    "model_ready": False,
                    "phrase": wakeword_mod.WAKE_PHRASE
                    if wakeword_mod else "يا لونا",
                    "wake_count": 0, "last_wake_at": "",
                    "bursts_checked": 0, "bursts_discarded_noise": 0}
        if self._wakeword is None:
            return {"enabled": bool(self.settings.get(
                "wakeword_enabled", True)),
                "state": "off", "detail": "",
                "model_ready": False,
                "phrase": wakeword_mod.WAKE_PHRASE,
                "wake_count": 0, "last_wake_at": "",
                "bursts_checked": 0, "bursts_discarded_noise": 0}
        return self._wakeword.status()

    def _wake_chime(self):
        """Short two-tone chime on wake (Windows only, no network, no
        TTS quota - a spoken "نعم؟" would burn the Gemini TTS daily
        limit on every wake). Guarded: silence on failure."""
        if sys.platform != "win32":
            return
        try:
            import winsound
            winsound.Beep(880, 120)
            time.sleep(0.05)
            winsound.Beep(1318, 150)
        except Exception:
            pass

    def _on_wake_word(self, command: str):
        """A wake was detected (listener thread). Show the main window,
        chime + flash the UI, then get the command: if the wake burst
        already held one ("يا لونا افتح النور"), run it directly; else
        record 5 s with the normal mic path. The listener stays
        paused until the reply has finished playing, so the mic never
        hears its own TTS. The transcript itself is never logged."""
        info("wakeword", "Wake handled - bringing the main window "
                         "forward")
        try:
            self.show_main()
        except Exception:
            pass
        try:
            if self.main_window:
                self.main_window.evaluate_js("onWakeWord()")
        except Exception:
            pass
        self._wake_chime()
        if self._wakeword is not None:
            try:
                self._wakeword.pause()
            except Exception:
                pass
        try:
            lang = self.settings.get("language", "ar")
            heard_text = (command or "").strip()
            if heard_text:
                # The command rode along in the wake burst - no second
                # recording needed.
                try:
                    if self.main_window:
                        self.main_window.evaluate_js(
                            "wakeHeard(" +
                            json.dumps(heard_text, ensure_ascii=False) +
                            ")")
                except Exception:
                    pass
            else:
                # Phrase only - record the command (normal mic path).
                heard = hear.listen(lang)
                heard_text = (heard.get("text") or "").strip()
                if not heard_text:
                    try:
                        if self.main_window:
                            self.main_window.evaluate_js(
                                "wakeNoCommand()")
                    except Exception:
                        pass
                    return
                try:
                    if self.main_window:
                        self.main_window.evaluate_js(
                            "wakeHeard(" +
                            json.dumps(heard_text, ensure_ascii=False) +
                            ")")
                except Exception:
                    pass
            # Run the command through the normal chat path (rules +
            # tool loop). The reply is pushed to the UI when done.
            result = self.api.chat(heard_text)
            if result.get("pending"):
                chat_id = result["chat_id"]
                deadline = time.time() + 120
                done = None
                while time.time() < deadline:
                    done = self.api.chat_result(chat_id)
                    if done.get("done"):
                        break
                    time.sleep(0.5)
                if done and done.get("done"):
                    reply = done.get("reply", "")
                else:
                    reply = "طوّلت شوية — جرّب تاني."
            else:
                reply = result.get("reply", "")
            try:
                if self.main_window:
                    self.main_window.evaluate_js(
                        "wakeReply(" +
                        json.dumps(reply, ensure_ascii=False) + ")")
            except Exception:
                pass
        except Exception as exc:
            error("wakeword", f"Wake handling failed: {exc}")
        finally:
            # Resume only after the spoken reply has finished (the
            # mic guard covers the TTS window) + the cooldown.
            try:
                deadline = time.time() + 35
                while time.time() < self._speaking_until and \
                        time.time() < deadline:
                    time.sleep(0.5)
                time.sleep(float(self.settings.get(
                    "wakeword_cooldown_sec", 5)))
            except Exception:
                pass
            self.resume_wakeword()

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
        # v1.6.4 reskin: the header mute button (set_muted) silences
        # future replies too, not just the one currently playing.
        if self.settings.get("muted", False):
            info("speak", "Muted - reply stays text-only (no TTS call)")
            return
        # v1.5 wake-word guard: the app is about to speak, so the
        # always-listening mic must ignore detections until the reply
        # has finished (its own TTS must never re-wake it). Set
        # optimistically now (max 30 s), refined to the real duration
        # once synthesis returns.
        self._speaking_until = time.time() + 30

        def _run():
            online = brain.is_online()
            result = speak.speak(text, self.settings.get("voice", "girl_warm"), online)
            if result["engine"] != "none" and result["duration"] > 0:
                self._speaking_until = time.time() + min(
                    result["duration"], 30)
                try:
                    if self.main_window:
                        self.main_window.evaluate_js("setSpeaking(true)")
                    time.sleep(min(result["duration"], 30))
                    if self.main_window:
                        self.main_window.evaluate_js("setSpeaking(false)")
                except Exception:
                    pass
            else:
                # Nothing was actually spoken - lift the guard at once.
                self._speaking_until = time.time()
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
        self._island_stop.set()
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
        # v1.6.0: stop the proactive companion.
        if self._companion is not None:
            try:
                self._companion.stop()
            except Exception:
                pass
        # v1.6.2: close the Chrome-extension native-host client
        # (best effort -- the listener thread is a daemon).
        if self._native_host_client is not None:
            try:
                self._native_host_client.close()
            except Exception:
                pass
            self._native_host_client = None
        # v1.5: stop the wake-word listener (closes the mic).
        try:
            self.stop_wakeword()
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
        info("app", f"LONA v{VERSION} starting")
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
        # v1.6.2 (A5/B17): warm the background services BEFORE any
        # window exists -- the monitor sampler feeds its cache and the
        # connectivity watcher probes once, so the first paint already
        # has live numbers and get_status() never samples on the
        # bridge. Neither may block startup (both are best-effort).
        try:
            self._start_monitor()
        except Exception as exc:
            warn("app", f"System monitor did not start: {exc}")
        try:
            brain.start_online_watcher()
        except Exception:
            pass
        # v1.6.2: Chrome-extension native host (re-wire) -- the
        # listener runs on its own thread; the shared client lets
        # browser_control.send_command() reach the extension.
        try:
            self._start_native_host()
        except Exception as exc:
            warn("app", f"Native host did not start: {exc}")
        # v1.3.1 second-launch focus: create the named event FIRST, so a
        # second instance can signal the running one to show its window
        # instead of exiting silently (which made the user think the app
        # would not open at all).
        self._show_main_event = winshape.create_show_main_event()
        # One instance only: a second launch (zip or installed) signals
        # the running instance and exits BEFORE any window is created.
        if not _acquire_single_instance_lock():
            if winshape.signal_show_main():
                info("app", "Another LONA instance is already running - "
                            "signaled it to show the main window")
            else:
                info("app", "Another LONA instance is already running - "
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
        # v1.6.1 invite gate: on first launch (not activated) the main
        # window MUST be visible - the gate lives inside it, and the
        # island pill would hide it. The island is skipped until
        # activation (it appears on the next launch, once activated).
        not_activated = invite_mod is not None and \
            not invite_mod.is_activated()
        # v1.4.1: the island window is NEVER created while island_enabled
        # is False (default) - the code stays, behind the flag.
        if self.settings.get("island_enabled", False) and not not_activated:
            # v1.6.4 fixed canvas (WindyLand/Nusa): created ONCE at the
            # expanded canvas size (720x400 logical) and NEVER resized.
            # The pill/panel animation is pure HTML/CSS inside it.
            # Centered with the system DPI scale (the passthrough loop
            # re-places it from the real HWND DPI once known).
            _scale = island_canvas.system_dpi_scale()
            _canvas_pw, _canvas_ph = island_canvas.physical_size(
                ISLAND_CANVAS_W, ISLAND_CANVAS_H, _scale)
            island_x = island_canvas.center_x(sw, _canvas_pw)
            self.island_window = webview.create_window(
                "LONA", str(ui_dir / "island.html"),
                width=ISLAND_CANVAS_W, height=ISLAND_CANVAS_H,
                x=island_x, y=12,
                # v1.6.2 (F4): transparent window -- only the pill
                # shows, no dark rectangle around it. ISLAND_BG is the
                # pill's own CSS color (in island.html), never the
                # window background. NEVER a chroma-key color here.
                frameless=True, transparent=True, on_top=True,
                resizable=False, js_api=self.api)
        # v1.4.1: the main window opens DIRECTLY at startup (hidden only
        # while the island is enabled - the old morph opened it from the
        # pill). min_size=(640,520): the morph to a 190x52 pill is gone
        # with the island, so the window keeps its full minimum.
        # v1.6.1 invite gate: never hidden on first launch - the gate
        # must be seen, even with the island on.
        self.main_window = webview.create_window(
            "LONA", str(ui_dir / "index.html"),
            width=MAIN_W, height=MAIN_H,
            hidden=self.settings.get("island_enabled", False)
            and not not_activated,
            # v1.6.2: frameless main window (item 3) -- no native
            # title bar; the in-app header owns the window controls
            # (window_minimize / window_toggle_maximize / window_close)
            # and dragging (window_move_to). The island is a separate
            # window and is untouched by this.
            frameless=True, resizable=True,
            min_size=(640, 520), js_api=self.api)
        if not self.settings.get("island_enabled", False) or not_activated:
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
                "LONA Pet", str(ui_dir / "pet.html"),
                width=PET_SIZE, height=PET_SIZE,
                x=sw - PET_SIZE - 20, y=sh - PET_SIZE - 90,
                frameless=True, transparent=False, on_top=True,
                resizable=False, background_color=PET_BG, js_api=self.api,
                hidden=not self.settings.get("pet_visible", False))

        # v1.4.1: system tray (the way back after closing the window
        # while the island is off). A missing tray backend never
        # blocks startup; X then minimizes to the taskbar instead.
        # v1.6.1: the tray icon is the gold robot logo, not the cat.
        try:
            self._tray = tray_mod.TrayManager(
                on_open=self.show_main, on_quit=self.quit,
                icon_path=APP_DIR / "ui" / "assets" / "mascot.webp")
            if self._tray.start():
                info("app", "System tray icon started")
            else:
                warn("app", "System tray not available - closing the "
                            "window will minimize to the taskbar instead")
        except Exception as exc:
            self._tray = None
            warn("app", f"Could not start system tray: {exc}")

        threading.Thread(target=self.morning_briefing, daemon=True).start()
        # v1.4.2: important-notification watcher (best effort - it may
        # not block startup or crash it). The system monitor already
        # started above, before the first window (v1.6.2 A5).
        try:
            self._start_notifications()
        except Exception as exc:
            warn("app", f"Notification watcher did not start: {exc}")
        # v1.6.0: proactive companion + TempWatch registration + invite
        # gate (best effort, like the monitor). The gate is a UI
        # concern: the full-screen #invite-gate overlay shows only
        # when not activated (main window forced visible above);
        # Python only answers get_activation_state()/check_invite().
        try:
            self._start_companion()
        except Exception as exc:
            warn("app", f"Companion did not start: {exc}")
        try:
            self._start_tempwatch()
        except Exception as exc:
            warn("app", f"TempWatch did not start: {exc}")
        try:
            if invite_mod is not None:
                activated = invite_mod.is_activated()
                info("app", "Activation: "
                            f"{'activated' if activated else 'invite screen (first install)'}")
                if not activated:
                    # Migrate any txt keys now (first run): the vault
                    # gets them before the user even reaches Settings.
                    try:
                        if apikeys_mod is not None:
                            apikeys_mod.migrate_txt_keys()
                    except Exception:
                        pass
            threading.Thread(target=self._companion_morning_brief,
                             daemon=True).start()
        except Exception as exc:
            warn("app", f"Invite/companion brief failed: {exc}")
        # v1.5: always-listening wake word (best effort - the model
        # loads in the background; a missing mic/model never blocks
        # startup or the chat).
        try:
            self.start_wakeword()
        except Exception as exc:
            warn("app", f"Wake-word listener did not start: {exc}")
        # v1.3.2: bridge-ready watchdog - each page logs READY on
        # pywebviewready; after 20s the log names any page that never
        # became ready, with the exact reason. v1.4.1: only the enabled
        # pages are waited for (main only, by default).
        threading.Thread(target=_bridge_ready_watchdog,
                         args=(self._watchdog_pages(),),
                         daemon=True).start()
        info("app", "LONA started - main window only "
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
        info("app", "LONA exited")

    def _start_island_clipboard(self) -> bool:
        """Start the clipboard-history watcher, once. LAZY (v1.6.4
        perf d): called from Api.island_clipboard_list (first panel
        open), from Api.island_clipboard_set_enabled(True), or from
        _apply_window_regions when the island_clipboard_enabled
        setting opts into background capture. Never at launch
        unconditionally. Idempotent; False off Windows (the watcher
        itself is Windows-only). Stops at quit via _island_stop."""
        try:
            import island_features as _isl_feat
        except Exception:
            return False
        if not getattr(_isl_feat, "IS_WINDOWS", False):
            return False
        with self._island_clipboard_lock:
            thread = self._island_clipboard_thread
            if thread is not None and thread.is_alive():
                return True
            self._island_clipboard_stop.clear()
            thread = threading.Thread(
                target=_isl_feat.clipboard_watch_loop,
                args=(lambda: self._quitting
                      or self._island_stop.is_set()
                      or self._island_clipboard_stop.is_set(),),
                daemon=True, name="memo-island-clipboard")
            self._island_clipboard_thread = thread
            thread.start()
            info("app", "Island clipboard watcher started "
                        "(lazy - first panel open or setting enabled)")
            return True

    def _stop_island_clipboard(self) -> None:
        """Stop the clipboard watcher (setting turned off). The
        thread exits within ~0.1 s of the flag; no join here, so a
        bridge call never waits on it. Quit still stops it too via
        _island_stop."""
        self._island_clipboard_stop.set()

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
                # v1.6.4 fixed canvas: the window is already 720x400 -
                # find it by title + canvas height (main is 860 tall),
                # apply the region ONCE, then start the passthrough +
                # clipboard loops. No region is ever applied again by
                # this method (the loop owns the Nusa fallback itself).
                _scale = island_canvas.system_dpi_scale()
                _max_h = int(ISLAND_CANVAS_H * _scale) + 40
                _max_w = int(ISLAND_CANVAS_W * _scale) + 40
                hwnd = _find_hwnd_by_title("LONA", max_width=_max_w,
                                           max_height=_max_h)
                if hwnd:
                    self._island_hwnd = hwnd
                    winshape.log_window("island", hwnd, "before region")
                    island_canvas.apply_island_canvas_region(hwnd)
                    winshape.log_window("island", hwnd, "after region")
                    self._island_expanded = False
                    island_canvas.set_click_through(hwnd, True)
                    island_canvas.start_passthrough_loop(
                        hwnd,
                        lambda: bool(self._island_expanded),
                        self._open_island_from_hover,
                        lambda: self._quitting
                        or self._island_stop.is_set())
                    # v1.6.4 perf (d): NO permanent clipboard thread
                    # here anymore. The watcher starts lazily on
                    # first clipboard-panel open (island_clipboard_
                    # list), or now only when the user explicitly
                    # opted into background capture.
                    if self.settings.get("island_clipboard_enabled",
                                         False):
                        self._start_island_clipboard()
                    pending["island"] = False
            if pending.get("pet"):
                hwnd = _find_hwnd_by_title("LONA Pet")
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
    # v1.6.4 reskin (F-H): the native-host listener can be spawned
    # standalone by the frozen exe ([exe, --standalone]); it runs the
    # stdio loop instead of opening the GUI.
    if "--standalone" in sys.argv:
        from native_host import _run_standalone
        raise SystemExit(_run_standalone())
    # v1.6.0 (LONA Browser): Chrome native-messaging host mode.
    # Chrome launches the exe with the extension origin as an argument
    # (the manifest has no args field), so both triggers are needed.
    if "--native-host" in sys.argv or any(
            a.startswith("chrome-extension://") for a in sys.argv[1:]):
        from native_host import run_host_main
        raise SystemExit(run_host_main())
    # v1.6.1 rename: first launch of LONA migrates data from an old
    # "Memo AI" install (settings, invite state, memory, keys) before
    # anything loads settings - otherwise the app would ask for the
    # invite code again. Frozen installs only (a source checkout never
    # migrates into itself); the installer already does this for a
    # setup.exe upgrade, so this is the fallback. Never blocks startup.
    if getattr(sys, "frozen", False):
        try:
            import migrate as migrate_mod
            migrate_mod.migrate_from_old_install(BASE_DIR, DATA_DIR)
        except Exception as exc:
            error("app", f"Old-install migration failed ({exc}) - continuing")
    MemoApp().run()


if __name__ == "__main__":
    main()
