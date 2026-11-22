# LONA v2 - Windows window shaping.
#
# REPLACES the v1.3.0 chroma-key transparency, which is DELETED (see
# "WHY THE CHROMA-KEY FAILED" below). Do NOT bring the color key back:
# it made the magenta box stay AND killed all clicks.
#
# WHY THE CHROMA-KEY FAILED (field evidence 2026-11-07, user log +
# screenshots): the island/pet pages painted pure magenta and main.py applied
# LWA_COLORKEY to the top-level Form HWND. But the visible pixels are
# painted by the WebView2 CHILD HWND, which docks Fill over the Form;
# SetLayeredWindowAttributes only affects the window it is called on,
# so the child's magenta stayed visible. Worse, the Form's whole surface
# is magenta, so hit-testing returned HTTRANSPARENT for EVERY point and
# the whole window became click-through - the pill and the cat stopped
# receiving clicks entirely (user: "خليتهم شفافين من الدوسة").
# One mechanism, both symptoms. The log even claimed "applied" while the
# screen proved otherwise - so this module NEVER logs success without
# verification (see apply_* + _verify_region).
#
# THE FIX: SetWindowRgn. A window region clips the window AND all its
# children to an exact shape, and hit-testing follows the region - so
# there is physically no box (no magenta, no black, no edge) AND clicks
# land on the pill/cat while passing through everywhere else. No
# dependence on WebView2 internals at all.
#   - island: rounded-rect region matching the pill (radius = h//2 for
#     the pill states, 22px for the expanded state).
#   - pet: region from the cat PNG's alpha channel (threshold + dilation
#     for the bob/happy-jump travel), unioned with the bubble + ball
#     zones (applied only while those elements are visible, so they
#     never leave a dark patch behind).
#
# Everything here is guarded for non-Windows: on Linux all Win32 calls
# are no-ops returning False/None, and the pure helpers (region math,
# easing, pet-region rects) work everywhere so headless tests can prove
# them. The Win32 parts can only be proven on real Windows - the field
# test (screenshots on light + dark wallpapers, pill click, cat click,
# click-through around the cat) is the proof.

import ctypes
import os
import sys
import threading
import time
from pathlib import Path

from .logger import error, info, warn

IS_WINDOWS = sys.platform == "win32"

# --- Win32 constants -------------------------------------------------------
_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOPMOST = 0x00000008
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000
_RGN_OR = 2
_RGN_ERROR = 0
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x00000102
_EVENT_MODIFY_STATE = 0x00000002
_SYNCHRONIZE = 0x00100000

class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _user32():
    return ctypes.windll.user32


# --- window description (diagnostics) --------------------------------------
def describe_window(hwnd: int) -> dict:
    """HWND class/title/rect/extended styles for the diagnostics log.
    Returns {} when the HWND cannot be inspected (never raises)."""
    if not IS_WINDOWS or not hwnd:
        return {}
    try:
        user32 = _user32()
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        cls = buf.value
        user32.GetWindowTextW(hwnd, buf, 256)
        title = buf.value
        rect = _RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        ex = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        flags = []
        for name, bit in (("LAYERED", _WS_EX_LAYERED),
                          ("TRANSPARENT", _WS_EX_TRANSPARENT),
                          ("TOPMOST", _WS_EX_TOPMOST),
                          ("TOOLWINDOW", _WS_EX_TOOLWINDOW),
                          ("NOACTIVATE", _WS_EX_NOACTIVATE)):
            if ex & bit:
                flags.append(name)
        return {"hwnd": int(hwnd), "class": cls, "title": title,
                "rect": (rect.left, rect.top, rect.right, rect.bottom),
                "w": rect.right - rect.left, "h": rect.bottom - rect.top,
                "exstyle": f"0x{ex & 0xFFFFFFFF:08x}", "exflags": flags}
    except Exception:
        return {}


def log_window(label: str, hwnd: int, when: str) -> None:
    """One diagnostics line describing a window (before/after shaping)."""
    desc = describe_window(hwnd)
    if not desc:
        warn("app", f"Window {label} ({when}): HWND {hwnd} "
                    f"could not be inspected")
        return
    info("app", f"Window {label} ({when}): HWND {desc['hwnd']} "
                f"class={desc['class']!r} title={desc['title']!r} "
                f"rect={desc['rect']} exstyle={desc['exstyle']} "
                f"flags={desc['exflags'] or 'none'}")


def get_window_rect(hwnd: int):
    """(x, y, w, h) of an HWND, or None."""
    if not IS_WINDOWS or not hwnd:
        return None
    try:
        rect = _RECT()
        if not _user32().GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return (rect.left, rect.top,
                rect.right - rect.left, rect.bottom - rect.top)
    except Exception:
        return None


# --- region application (verified, never a blind claim) --------------------
def _verify_region(hwnd: int, expected_box):
    """Read the region back (GetWindowRgn + GetRgnBox) and compare its
    bounding box with the expected one. Returns (ok, box-or-None).
    v1.4.1: ±1px tolerance per coordinate. GetRgnBox reads back 1px
    smaller on right/bottom for round-rect regions (the field log
    shows a constant 1px difference, e.g. (0,0,300,150) vs
    (0,0,301,151)) while the region IS applied - exact equality
    spammed VERIFICATION FAILED errors for a correct region."""
    try:
        user32 = _user32()
        gdi32 = ctypes.windll.gdi32
        hrgn = gdi32.CreateRectRgn(0, 0, 0, 0)
        try:
            kind = user32.GetWindowRgn(hwnd, hrgn)
            if kind == _RGN_ERROR:
                return False, None
            box = _RECT()
            gdi32.GetRgnBox(hrgn, ctypes.byref(box))
            got = (box.left, box.top, box.right, box.bottom)
            ok = all(abs(g - e) <= 1
                     for g, e in zip(got, tuple(expected_box)))
            return ok, got
        finally:
            gdi32.DeleteObject(hrgn)
    except Exception:
        return False, None


def _set_region(hwnd: int, hrgn, label: str, expected_box) -> bool:
    """Apply an HRGN to an HWND and VERIFY it. On success the system owns
    the region (do not delete it); on failure we delete it. Returns True
    only when SetWindowRgn succeeded AND the read-back box matches."""
    if not IS_WINDOWS:
        return False
    try:
        user32 = _user32()
        gdi32 = ctypes.windll.gdi32
        ok = user32.SetWindowRgn(hwnd, hrgn, True)
        if not ok:
            gdi32.DeleteObject(hrgn)
            error("app", f"SetWindowRgn FAILED for {label} (HWND {hwnd}) "
                         f"- last error {ctypes.get_last_error()}")
            return False
        verified, got = _verify_region(hwnd, expected_box)
        if not verified:
            error("app", f"Region applied to {label} (HWND {hwnd}) but "
                         f"VERIFICATION FAILED - read-back box {got}, "
                         f"expected {tuple(expected_box)}")
            return False
        info("app", f"Window region applied + verified for {label} "
                    f"(HWND {hwnd}) - box {got}")
        return True
    except Exception as exc:
        error("app", f"SetWindowRgn failed for {label}: {exc}")
        return False


def apply_round_rect_region(hwnd: int, w: int, h: int, radius: int,
                            label: str) -> bool:
    """Clip an HWND to a rounded rect of its own size (island pill)."""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        gdi32 = ctypes.windll.gdi32
        hrgn = gdi32.CreateRoundRectRgn(0, 0, int(w) + 1, int(h) + 1,
                                        int(radius) * 2, int(radius) * 2)
        return _set_region(hwnd, hrgn, label, (0, 0, int(w) + 1, int(h) + 1))
    except Exception as exc:
        error("app", f"Round-rect region failed for {label}: {exc}")
        return False


# --- island pill radius (pure, testable) ------------------------------------
def island_radius(w: int, h: int) -> int:
    """Corner radius of the island pill for a window size. The pill is
    fully rounded (radius = h/2) in the idle/full states; the expanded
    reply state uses the CSS border-radius of 22px."""
    if h >= 100:
        return 22
    return max(1, int(h) // 2)


# --- morph animation math (pure, testable) ----------------------------------
def ease_in_out_cubic(p: float) -> float:
    """0 -> 0, 0.5 -> 0.5, 1 -> 1, smooth in the middle."""
    p = min(1.0, max(0.0, float(p)))
    if p < 0.5:
        return 4 * p * p * p
    return 1 - ((-2 * p + 2) ** 3) / 2


def lerp_rect(a, b, t: float):
    """Interpolate (x, y, w, h) between two rects."""
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(4))


# --- second-launch focus (v1.3.1): a named event ---------------------------
# A second launch used to exit silently ("Another instance is already
# running") and the user thought the app would not open at all. Now the
# second instance signals the running one through a named auto-reset
# event, and the running instance brings its main window forward.
_SHOW_MAIN_EVENT = "Local\\LONA-ShowMain"


def create_show_main_event():
    """Create the event the running instance watches. Returns the handle
    or None (non-Windows / failure)."""
    if not IS_WINDOWS:
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateEventW(None, False, False, _SHOW_MAIN_EVENT)
        if not handle:
            return None
        return handle
    except Exception:
        return None


def signal_show_main() -> bool:
    """Second instance: ask the running instance to show its main window.
    Returns True when the event was signaled."""
    if not IS_WINDOWS:
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenEventW(_EVENT_MODIFY_STATE | _SYNCHRONIZE,
                                     False, _SHOW_MAIN_EVENT)
        if not handle:
            return False
        try:
            return bool(kernel32.SetEvent(handle))
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def watch_show_main_event(event_handle, on_signal, stop_flag,
                          poll_seconds: float = 0.5):
    """Wait for the event in a loop and call on_signal() each time it is
    set. Runs until stop_flag() is True. Never raises."""
    if not IS_WINDOWS or not event_handle:
        return
    kernel32 = ctypes.windll.kernel32
    while not stop_flag():
        try:
            res = kernel32.WaitForSingleObject(
                event_handle, int(poll_seconds * 1000))
        except Exception:
            return
        if res == _WAIT_OBJECT_0:
            try:
                on_signal()
            except Exception:
                pass
        elif res != _WAIT_TIMEOUT:
            return


# --- single-instance mutex (the installer checks it) ----------------------
# Inno Setup's AppMutex is Local\LONA-SingleInstance. The app must hold
# this mutex while it runs, or Setup cannot tell a running LONA from a
# stopped one (CloseApplications still catches it, but the mutex is the
# first line of defence against installing over a running app — the
# v1.3.1 "Not Responding" lesson). The ShowMain event above handles
# second-launch focus; the mutex exists only for the installer.
_SINGLE_INSTANCE_MUTEX = "Local\\LONA-SingleInstance"
_mutex_handle = None


def create_single_instance_mutex():
    """Create + hold the installer mutex. Returns the handle or None.
    The handle is kept module-level so it lives for the process."""
    global _mutex_handle
    if not IS_WINDOWS:
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_MUTEX)
        if not handle:
            return None
        _mutex_handle = handle
        return handle
    except Exception:
        return None


# --- main-window MinimumSize (morph needs to go below it) -------------------
# v1.3.2: DELETED. clear_min_size()/restore_min_size() touched the
# Form's MinimumSize .NET property from the morph thread (cross-thread
# .NET access - one of the v1.3.1 hang suspects). The main window is now
# created with min_size=(190,52) in main.py, so the morph can shrink
# into the pill without any MinimumSize juggling. Do NOT bring these
# helpers back: any future minimum-size work must happen on the GUI
# thread or not at all.
