# LONA v1.6.4 reskin - island fixed-canvas window logic.
#
# Island method adapted from WindyLand (MIT, Copyright (c) 2026 Shabab)
# - method and interaction ideas only; no WindyLand code, palette,
# icons, or assets used. Full license text: THIRD_PARTY.md (repo root).
#
# WindyLand's proven shape (read from its Rust source, 2026-11-22):
# ONE fixed-size window, never resized; the pill shape is DRAWN, the
# animation is a spring on the drawn size, and click-through is a
# WS_EX_TRANSPARENT toggle driven by a ~90 ms cursor poll. A white or
# black box is impossible by construction - there is no region race
# to lose, because nothing is ever re-applied on a resize.
#
# Nusa (static analysis of nusa.exe 1.3.0, 2026-11-22) CORROBORATES
# this exactly, independently: one WS_POPUP window, DirectComposition
# + D3D11 + Direct2D, the pill DRAWN as a rounded-rect path with
# per-pixel alpha, DWM compositing it, no window background bitmap -
# so no rectangle is possible by construction. Nusa imports
# SetWindowRgn + CreateRectRgn too, most plausibly a RECTANGLE region
# bounding the hit-test/clip box to the pill's current bounding box:
# the rounded shape is DRAWN, not cut. Two lessons folded in here:
#   (1) the region's role is HIT-TEST only - it bounds which clicks
#       reach the window; it does not need to match the rounded
#       shape exactly (see apply_pill_hit_region);
#   (2) click-through preference order: WindyLand's WS_EX_TRANSPARENT
#       toggle first (precise - the rounded corners stay
#       click-through); Nusa's rect-region as the FALLBACK (simpler,
#       but coarser - clicks in the bounding rect's rounded corners
#       still hit the window). The passthrough loop below implements
#       that order and switches modes itself if the toggle fails.
# Nusa's DirectComposition itself is NOT portable to pywebview (it
# would fight the framework) - same caveat as WindyLand's layered
# window. The portable lesson is the PATTERN: fixed window + drawn
# alpha + region for hit-test only. Idle 0% CPU in both comes from a
# blocking message loop + timers; LONA's 90 ms cursor poll is the
# one place polling is unavoidable (a click-through window receives
# no mouse messages, so there is no event to wait on) - it is two
# cheap Win32 calls + a sleep, and the JS timers stay minimal while
# collapsed.
#
# LONA cannot use WindyLand's true layered window: pywebview owns the
# window and WebView2 renders via DirectComposition, and WindyLand
# gives no evidence that works inside a layered window. So LONA takes
# the fixed-canvas half of the method instead:
#   - the island window is created ONCE at the expanded canvas size
#     (520x300 logical) and NEVER resized;
#   - SetWindowRgn is applied ONCE at creation for the full canvas
#     (rounded rect, radius 26) and never touched again - it then
#     verifies cleanly by read-back, because nothing moves under it;
#   - collapsed <-> expanded animates purely in HTML/CSS inside the
#     fixed canvas (the pill is 380x60, the panel 520x300);
#   - when collapsed, WS_EX_TRANSPARENT makes the invisible band
#     click-through, with a 90 ms GetCursorPos poll that removes the
#     flag and opens the island the moment the cursor is over the pill
#     (WindyLand's sync_passthrough, same idea for a maximized window
#     underneath the island).
#   - DPI: the scale is read with GetDpiForWindow at creation; the
#     poll loop re-places the canvas if the DPI changes (no window
#     procedure subclassing - pywebview owns the window).
#
# Everything Win32 is guarded: off Windows every function returns a
# safe no-op value (False / None / 1.0) and never raises. The pure
# helpers (rect math, DPI scaling) work everywhere so headless tests
# can prove them; the Win32 paths can only be proven on real Windows
# (field test: screenshots on light + dark wallpapers, pill click,
# click-through above a maximized window, move to another monitor).

import ctypes
import threading
import time

import winshape
from logger import error, info, warn

IS_WINDOWS = winshape.IS_WINDOWS

# Fixed canvas (logical px). The window is created at this size once
# and never resized. Mohamed's laptop is 150% scaling, so the canvas
# is 780x450 physical there. Must match ISLAND_CANVAS_W/H in main.py
# (main.py duplicates these constants - it does not import them).
ISLAND_CANVAS_W = 520
ISLAND_CANVAS_H = 300
ISLAND_PILL_W = 380
ISLAND_PILL_H = 60
ISLAND_CANVAS_RADIUS = 26
ISLAND_TOP_Y = 12

# WindyLand's cursor poll interval (90 ms).
PASSTHROUGH_POLL_SEC = 0.09
# How often the loop re-checks the DPI (cheap: two Win32 calls).
DPI_CHECK_SEC = 1.0


# --- pure helpers (testable on Linux) ---------------------------------------
def physical_size(logical_w: int, logical_h: int, dpi_scale: float):
    """Logical size -> physical pixels at a DPI scale."""
    scale = float(dpi_scale) if dpi_scale else 1.0
    return (max(1, int(round(logical_w * scale))),
            max(1, int(round(logical_h * scale))))


def center_x(screen_w: int, canvas_w: int) -> int:
    """Top-center x for a canvas of `canvas_w` on a screen `screen_w`
    (both in the SAME units - physical with physical, logical with
    logical - or the pill lands off-center)."""
    return max(0, (int(screen_w) - int(canvas_w)) // 2)


def pill_rect_for_canvas(canvas_rect, dpi_scale: float = 1.0):
    """The pill rect (x, y, w, h) inside a canvas rect (x, y, w, h):
    380x60 logical, centered horizontally, pinned to the top. The
    canvas rect comes from GetWindowRect, so it is already in the
    window's own (physical, when DPI-aware) coordinate space - the
    pill size is scaled by the same DPI factor."""
    cx, cy, cw, _ch = canvas_rect
    pw, ph = physical_size(ISLAND_PILL_W, ISLAND_PILL_H, dpi_scale)
    return (cx + max(0, (cw - pw) // 2), cy, pw, ph)


def point_in_rect(point, rect) -> bool:
    """True when (x, y) lies inside (x, y, w, h)."""
    px, py = point
    rx, ry, rw, rh = rect
    return rx <= px < rx + rw and ry <= py < ry + rh


# --- Win32 helpers (via winshape, guarded) ----------------------------------
def get_dpi_scale(hwnd: int) -> float:
    """DPI scale for an HWND (GetDpiForWindow / 96). 1.0 off Windows
    or when the DPI cannot be read."""
    if not IS_WINDOWS or not hwnd:
        return 1.0
    try:
        dpi = winshape.get_dpi_for_window(hwnd)
        if dpi and dpi > 0:
            return dpi / 96.0
    except Exception:
        pass
    return 1.0


def system_dpi_scale() -> float:
    """System DPI scale (GetDpiForSystem / 96). 1.0 off Windows or on
    failure. Used ONLY before any HWND exists (initial canvas
    placement + the HWND lookup bound); once the HWND is known,
    get_dpi_scale(hwnd) is authoritative and the passthrough loop
    re-places the canvas if the per-monitor DPI differs."""
    if not IS_WINDOWS:
        return 1.0
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        if dpi and dpi > 0:
            return dpi / 96.0
    except Exception:
        pass
    return 1.0


def apply_island_canvas_region(hwnd: int) -> bool:
    """Apply the canvas region ONCE: a rounded rect over the window's
    CURRENT physical rect (read back from the HWND, so logical vs
    physical DPI questions cannot make it wrong). Verified by
    read-back inside winshape; returns True only when verified.
    Role (Nusa lesson): HIT-TEST bounding box for the canvas - the
    rounded shape itself is drawn in HTML/CSS, not cut by this
    region, so the region only needs to bound the canvas."""
    if not IS_WINDOWS or not hwnd:
        return False
    rect = winshape.get_window_rect(hwnd)
    if not rect:
        error("app", "Island canvas region: no HWND rect - not applied")
        return False
    ok = winshape.apply_round_rect_region(
        hwnd, rect[2], rect[3], ISLAND_CANVAS_RADIUS, "island-canvas")
    if ok:
        info("app", f"Island canvas region applied once (rect {rect}, "
                    f"radius {ISLAND_CANVAS_RADIUS}) - never re-applied")
    return ok


def apply_pill_hit_region(hwnd: int, expanded: bool,
                         dpi_scale: float = 1.0) -> bool:
    """Nusa-style FALLBACK: the region is a RECTANGLE bounding the
    hit-test box - the pill's rect while collapsed, the full canvas
    while expanded. The rounded shape is DRAWN (HTML/CSS), not cut;
    the region only decides which clicks reach the window. Coarser
    than the WS_EX_TRANSPARENT toggle (clicks in the pill bounding
    rect's rounded corners still hit the window) - that is why this
    is the fallback, used only when the toggle fails. Applied +
    verified; returns True only when verified. Coordinates are
    window-relative; the pill rect is DPI-aware (150% -> 570x90
    physical on Mohamed's laptop)."""
    if not IS_WINDOWS or not hwnd:
        return False
    rect = winshape.get_window_rect(hwnd)
    if not rect:
        return False
    if expanded:
        x0, y0, x1, y1 = 0, 0, rect[2], rect[3]
    else:
        pill = pill_rect_for_canvas(rect, dpi_scale)
        x0, y0 = pill[0] - rect[0], pill[1] - rect[1]
        x1, y1 = x0 + pill[2], y0 + pill[3]
    ok = winshape.apply_rect_region(hwnd, x0, y0, x1, y1, "island-hit")
    if ok:
        info("app", f"Island hit-test region applied "
                    f"({'expanded' if expanded else 'pill'} "
                    f"({x0},{y0})-({x1},{y1}))")
    return ok


def set_click_through(hwnd: int, on: bool) -> bool:
    """Toggle WS_EX_TRANSPARENT on the island HWND. Returns True when
    the flag now matches `on`."""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        return winshape.set_click_through(hwnd, bool(on))
    except Exception as exc:
        error("app", f"Island click-through toggle failed: {exc}")
        return False


def is_click_through(hwnd: int) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        return winshape.is_click_through(hwnd)
    except Exception:
        return False


def get_cursor_pos():
    """Current cursor position (x, y), or None."""
    if not IS_WINDOWS:
        return None
    try:
        return winshape.get_cursor_pos()
    except Exception:
        return None


def is_foreground_maximized() -> bool:
    """True when the foreground window is maximized (a maximized app
    is underneath the island). Off Windows: False."""
    if not IS_WINDOWS:
        return False
    try:
        return winshape.is_foreground_maximized()
    except Exception:
        return False


def reposition_canvas(hwnd: int, dpi_scale: float) -> bool:
    """Re-place the canvas top-center at the physical size for
    `dpi_scale` (DPI-change recovery). Does NOT resize the pywebview
    window object - the HWND is moved with SetWindowPos and the
    canvas region is re-applied once for the new size (a creation-time
    event, not a resize race)."""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        import ctypes as _ct
        user32 = _ct.windll.user32
        sw = user32.GetSystemMetrics(0)
        pw, ph = physical_size(ISLAND_CANVAS_W, ISLAND_CANVAS_H, dpi_scale)
        x = center_x(sw, pw)
        ok = winshape.reposition_window(hwnd, x, ISLAND_TOP_Y, pw, ph)
        if ok:
            apply_island_canvas_region(hwnd)
            info("app", f"Island canvas re-placed after DPI change "
                        f"(scale {dpi_scale:.2f}, {pw}x{ph} at x={x})")
        return ok
    except Exception as exc:
        error("app", f"Island canvas re-place failed: {exc}")
        return False


# --- passthrough poll loop (WindyLand sync_passthrough) ---------------------
def island_passthrough_loop(hwnd: int, is_expanded, on_hover_open,
                            stop_flag, poll_seconds: float = PASSTHROUGH_POLL_SEC):
    """Daemon loop. Preference order (Nusa finding #5):
    1. WindyLand toggle (PREFERRED): while collapsed the window is
       click-through (WS_EX_TRANSPARENT) so the invisible 520x300 band
       never eats clicks on the apps underneath. Every ~90 ms it polls
       the cursor: over the pill -> remove the flag and open the island
       (on_hover_open); anywhere else -> keep the flag. While expanded
       the flag stays off so the panel receives clicks. The poll is
       unavoidable here - a click-through window receives no mouse
       messages, so there is no event to wait on - but it is two cheap
       Win32 calls + a sleep (Nusa finding #7: idle stays ~0% CPU).
    2. Nusa rect-region (FALLBACK): if the toggle ever fails, the loop
       switches mode by itself and the region becomes the hit-test box
       (pill rect collapsed, full canvas expanded - see
       apply_pill_hit_region). In this mode the pill IS clickable
       directly, so no hover-open is needed; clicks in the bounding
       rect's rounded corners still hit the window (the known coarser
       trade-off). The region is swapped only on a state change, then
       re-applied once ~100 ms later, then left alone - never per
       frame, never on a resize (there is no resize).
    Also watches the DPI about once a second and re-places the canvas
    when it changes (Nusa finding #6: DPI is runtime-aware; at 150%
    the region math MUST be DPI-aware - every size here comes from
    the real HWND rect + GetDpiForWindow, never from an assumption).
    Never raises; exits when stop_flag() is True."""
    if not IS_WINDOWS or not hwnd:
        return
    last_dpi = get_dpi_scale(hwnd)
    last_dpi_check = 0.0
    mode = "toggle"  # "toggle" (WindyLand, preferred) | "region" (Nusa)
    region_applied_for = None  # (expanded, dpi) the hit region matches
    region_settled_at = 0.0
    region_reapplied = True
    try:
        while not stop_flag():
            try:
                now = time.monotonic()
                if now - last_dpi_check >= DPI_CHECK_SEC:
                    last_dpi_check = now
                    dpi = get_dpi_scale(hwnd)
                    if dpi and abs(dpi - last_dpi) > 0.01:
                        last_dpi = dpi
                        reposition_canvas(hwnd, dpi)
                        region_applied_for = None
                expanded = bool(is_expanded())
                if mode == "toggle":
                    if expanded:
                        if is_click_through(hwnd):
                            if not set_click_through(hwnd, False):
                                mode = "region"
                                region_applied_for = None
                                warn("app", "Island click-through toggle "
                                            "failed while expanding - "
                                            "falling back to Nusa "
                                            "rect-region hit-test")
                    else:
                        pos = get_cursor_pos()
                        rect = winshape.get_window_rect(hwnd)
                        over_pill = False
                        if pos is not None and rect:
                            over_pill = point_in_rect(
                                pos, pill_rect_for_canvas(rect, last_dpi))
                        if over_pill:
                            # Cursor is on the pill: make it clickable
                            # and open. The maximized-window check is
                            # the same outcome - collapsed is
                            # click-through either way; hovering still
                            # opens, as in WindyLand.
                            if is_click_through(hwnd):
                                set_click_through(hwnd, False)
                            try:
                                on_hover_open()
                            except Exception:
                                pass
                        else:
                            if not is_click_through(hwnd):
                                if not set_click_through(hwnd, True):
                                    mode = "region"
                                    region_applied_for = None
                                    warn("app", "Island click-through "
                                                "toggle failed - falling "
                                                "back to Nusa rect-region "
                                                "hit-test")
                                    apply_pill_hit_region(
                                        hwnd, False, last_dpi)
                                    region_applied_for = (False, last_dpi)
                                    region_settled_at = time.monotonic()
                                    region_reapplied = False
                else:
                    # Nusa fallback: the region IS the hit-test box.
                    key = (expanded, last_dpi)
                    if region_applied_for != key:
                        apply_pill_hit_region(hwnd, expanded, last_dpi)
                        region_applied_for = key
                        region_settled_at = time.monotonic()
                        region_reapplied = False
                    elif (not region_reapplied
                          and time.monotonic() - region_settled_at >= 0.1):
                        # One re-apply ~100 ms after the swap, then
                        # leave it alone.
                        apply_pill_hit_region(hwnd, expanded, last_dpi)
                        region_reapplied = True
            except Exception as exc:
                warn("app", f"Island passthrough poll failed: {exc}")
            # Sleep in small slices so stop is prompt. Region mode
            # needs no cursor poll, so it sleeps longer (~0% CPU).
            sleep_for = max(0.02, float(poll_seconds)) \
                if mode == "toggle" else 0.1
            deadline = time.monotonic() + sleep_for
            while time.monotonic() < deadline:
                if stop_flag():
                    return
                time.sleep(0.02)
    except Exception as exc:
        warn("app", f"Island passthrough loop ended: {exc}")


def start_passthrough_loop(hwnd: int, is_expanded, on_hover_open,
                           stop_flag) -> threading.Thread | None:
    """Start the passthrough loop on a daemon thread. None off
    Windows (the loop has nothing to do there)."""
    if not IS_WINDOWS or not hwnd:
        return None
    thread = threading.Thread(
        target=island_passthrough_loop,
        args=(hwnd, is_expanded, on_hover_open, stop_flag),
        daemon=True, name="memo-island-passthrough")
    thread.start()
    info("app", "Island passthrough loop started (90 ms cursor poll)")
    return thread
