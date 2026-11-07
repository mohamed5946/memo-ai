# Memo AI - Windows window shaping (v1.3.1).
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

from logger import error, info, warn

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

# The pet window is always this size (see main.py PET_SIZE).
PET_SIZE = 150


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
    bounding box with the expected one. Returns (ok, box-or-None)."""
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
            return got == tuple(expected_box), got
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


def _combine_rects(rects, roundrects):
    """Build one HRGN from rects + roundrects (RGN_OR). Returns the HRGN
    or None. The caller passes it to _set_region (ownership moves)."""
    try:
        gdi32 = ctypes.windll.gdi32
        combined = gdi32.CreateRectRgn(0, 0, 0, 0)
        tmp = gdi32.CreateRectRgn(0, 0, 0, 0)
        try:
            for (x0, y0, x1, y1) in rects:
                gdi32.SetRectRgn(tmp, int(x0), int(y0), int(x1), int(y1))
                gdi32.CombineRgn(combined, combined, tmp, _RGN_OR)
            for (x0, y0, x1, y1, r) in roundrects:
                rr = gdi32.CreateRoundRectRgn(int(x0), int(y0), int(x1),
                                              int(y1), int(r) * 2, int(r) * 2)
                try:
                    gdi32.CombineRgn(combined, combined, rr, _RGN_OR)
                finally:
                    gdi32.DeleteObject(rr)
            return combined
        finally:
            gdi32.DeleteObject(tmp)
    except Exception as exc:
        error("app", f"Could not build window region: {exc}")
        return None


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


def apply_rect_list_region(hwnd: int, rects, roundrects, label: str) -> bool:
    """Clip an HWND to a union of rects + roundrects (pet silhouette).
    The expected box is the union's bounding box."""
    if not IS_WINDOWS or not hwnd:
        return False
    if not rects and not roundrects:
        error("app", f"No region rects for {label} - refusing empty region")
        return False
    xs0 = [r[0] for r in rects] + [r[0] for r in roundrects]
    ys0 = [r[1] for r in rects] + [r[1] for r in roundrects]
    xs1 = [r[2] for r in rects] + [r[2] for r in roundrects]
    ys1 = [r[3] for r in rects] + [r[3] for r in roundrects]
    expected = (min(xs0), min(ys0), max(xs1), max(ys1))
    hrgn = _combine_rects(rects, roundrects)
    if hrgn is None:
        return False
    return _set_region(hwnd, hrgn, label, expected)


# --- island pill radius (pure, testable) ------------------------------------
def island_radius(w: int, h: int) -> int:
    """Corner radius of the island pill for a window size. The pill is
    fully rounded (radius = h/2) in the idle/full states; the expanded
    reply state uses the CSS border-radius of 22px."""
    if h >= 100:
        return 22
    return max(1, int(h) // 2)


# --- pet silhouette region (pure rect math, testable on Linux) -------------
def build_pet_region_rects(png_path, window_size: int = PET_SIZE,
                           alpha_threshold: int = 32,
                           dilate_up: int = 7, dilate_down: int = 7,
                           dilate_side: int = 4):
    """Rects covering the cat silhouette in window coordinates.

    The cat PNG is rendered with object-fit: contain in a square window,
    so it is scaled to fit and centered; the mask is built at that
    displayed size. Dilation covers the bob (+/-7px) and the happy
    jump (-6px) so the cat is never clipped mid-animation. Returns a
    list of (x0, y0, x1, y1) rects (rows merged), or None on failure.
    Needs Pillow + numpy (both are app dependencies)."""
    try:
        import numpy as np
        from PIL import Image
    except Exception as exc:
        error("app", f"Pillow/numpy missing - cannot build pet region: {exc}")
        return None
    try:
        img = Image.open(png_path).convert("RGBA")
        iw, ih = img.size
        scale = min(window_size / iw, window_size / ih)
        dw, dh = max(1, int(iw * scale)), max(1, int(ih * scale))
        ox, oy = (window_size - dw) // 2, (window_size - dh) // 2
        small = img.resize((dw, dh), Image.LANCZOS)
        alpha = np.asarray(small)[:, :, 3]
        mask = alpha > alpha_threshold
        # Dilate: OR of shifted copies (numpy slicing, no wrap-around).
        dil = mask.copy()
        h, w = mask.shape
        for dy in range(-dilate_up, dilate_down + 1):
            for dx in range(-dilate_side, dilate_side + 1):
                if dx == 0 and dy == 0:
                    continue
                y0s, y1s = max(0, -dy), min(h, h - dy)
                x0s, x1s = max(0, -dx), min(w, w - dx)
                y0d, y1d = max(0, dy), min(h, h + dy)
                x0d, x1d = max(0, dx), min(w, w + dx)
                shifted = np.zeros_like(mask)
                shifted[y0d:y1d, x0d:x1d] = mask[y0s:y1s, x0s:x1s]
                dil |= shifted
        # Row runs, merging consecutive rows with identical runs.
        rects = []
        open_runs = {}  # (x0, x1) -> y0
        for y in range(dh):
            row = dil[y]
            runs = []
            x = 0
            while x < w:
                if row[x]:
                    x0 = x
                    while x < w and row[x]:
                        x += 1
                    runs.append((x0, x))
                else:
                    x += 1
            run_set = set(runs)
            for key in list(open_runs):
                if key not in run_set:
                    rects.append((ox + key[0], oy + open_runs.pop(key),
                                  ox + key[1], oy + y))
            for key in runs:
                if key not in open_runs:
                    open_runs[key] = y
        for (x0, x1), y0 in open_runs.items():
            rects.append((ox + x0, oy + y0, ox + x1, oy + dh))
        return rects
    except Exception as exc:
        error("app", f"Could not build pet region from {png_path}: {exc}")
        return None


# Pet overlay zones (window coords, 150x150). The bubble (white "مياو!")
# sits top-right; the ball rolls along the bottom-right when parked.
# They are added to the region ONLY while visible, so they never leave
# a dark patch behind when hidden.
PET_BUBBLE_ZONE = (76, 0, 148, 26, 11)   # x0, y0, x1, y1, radius
PET_BALL_ZONE = (82, 114, 122, 138, 10)


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
_SHOW_MAIN_EVENT = "Local\\MemoAI-ShowMain"


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


# --- main-window MinimumSize (morph needs to go below it) -------------------
# v1.3.2: DELETED. clear_min_size()/restore_min_size() touched the
# Form's MinimumSize .NET property from the morph thread (cross-thread
# .NET access - one of the v1.3.1 hang suspects). The main window is now
# created with min_size=(190,52) in main.py, so the morph can shrink
# into the pill without any MinimumSize juggling. Do NOT bring these
# helpers back: any future minimum-size work must happen on the GUI
# thread or not at all.
