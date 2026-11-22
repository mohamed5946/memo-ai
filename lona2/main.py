"""LONA v2 — app entry (main window + separate Dynamic Island window).

Window rules (learned in the field): the island is its OWN frameless
pywebview window, created HIDDEN, region applied, then shown — never
created visible (that flashed a black/white rectangle). The region is
SetWindowRgn (winshape), re-applied on every resize. The main window
is frameless 1280x860; its header owns minimize/maximize/close via
window.maximize()/restore() with a flag (pywebview has no
is-maximized query). No window call is ever made from a background
thread through .native/.Handle — HWND lookup is EnumWindows only.
"""

import ctypes
import os
import sys
import threading
import time
from pathlib import Path

try:
    import webview
except Exception:
    webview = None

from . import VERSION, invite, winshape
from .bridge import Api
from .logger import error, info, set_log_file, warn
from .monitor import get_device_overview
from .stores import DATA_DIR, get_settings
from . import health as health_mod

APP_DIR = Path(__file__).resolve().parent
UI_DIR = APP_DIR / "ui"

MAIN_W, MAIN_H = 1280, 860
ISLAND_W, ISLAND_H = 380, 60
ISLAND_EXPANDED_W, ISLAND_EXPANDED_H = 720, 400


def _find_hwnd_by_title(title: str, max_width: int | None = None):
    """EnumWindows only — never .native/.Handle from a background
    thread (that froze the window in v1.3.1)."""
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


class App:
    def __init__(self):
        self.settings = get_settings()
        set_log_file(DATA_DIR / "logs" / "lona.log")
        self.api = Api(self)
        self.main_window = None
        self.island_window = None
        self.invite_window = None
        self._main_maximized = False
        self._main_visible = False
        self._island_hwnd = None
        self._stop = threading.Event()
        self._tray = None

    # --- windows ----------------------------------------------------------
    def _screen_size(self):
        try:
            screen = webview.screens[0]
            return screen.width, screen.height
        except Exception:
            return 1920, 1080

    def _create_windows(self):
        sw, _sh = self._screen_size()
        island_on = self.settings.get("island_enabled", True)
        not_activated = not invite.is_activated()
        if island_on and not not_activated:
            island_x = max(0, (sw - ISLAND_W) // 2)
            self.island_window = webview.create_window(
                "LONA Island", str(UI_DIR / "island.html"),
                width=ISLAND_W, height=ISLAND_H, x=island_x, y=12,
                frameless=True, transparent=True, on_top=True,
                resizable=False, js_api=self.api, hidden=True)
        self.main_window = webview.create_window(
            "LONA", str(UI_DIR / "index.html"),
            width=MAIN_W, height=MAIN_H,
            hidden=island_on and not not_activated,
            frameless=True, resizable=True,
            min_size=(640, 520), js_api=self.api)
        if not island_on or not_activated:
            self._main_visible = True
        try:
            self.main_window.events.closing += self._on_main_closing
        except Exception:
            pass

    def _apply_island_region(self):
        """Runs in webview.start(func)'s thread BEFORE windows exist:
        poll for the island HWND, apply the region, then show the
        window. The region is verified on read-back; success is never
        claimed without it."""
        if not winshape.IS_WINDOWS:
            return
        deadline = time.time() + 60
        while time.time() < deadline and self._island_hwnd is None:
            hwnd = _find_hwnd_by_title("LONA Island", max_width=800)
            if hwnd:
                self._island_hwnd = hwnd
                winshape.log_window("island", hwnd, "before region")
                rect = winshape.get_window_rect(hwnd)
                w, h = (rect[2], rect[3]) if rect else (ISLAND_W, ISLAND_H)
                ok = winshape.apply_round_rect_region(
                    hwnd, w, h, winshape.island_radius(w, h), "island")
                winshape.log_window("island", hwnd, "after region")
                if not ok:
                    error("app", "Island region FAILED - pill may show a box")
                try:
                    if self.island_window is not None:
                        self.island_window.show()
                except Exception as exc:
                    error("app", f"Could not show island: {exc}")
                return
            time.sleep(0.5)
        error("app", "Island HWND never appeared - island stays hidden")

    def refresh_island_region(self, w: int, h: int):
        """Re-apply the pill region for the island's current size."""
        if not self._island_hwnd:
            return False
        try:
            return winshape.apply_round_rect_region(
                self._island_hwnd, int(w), int(h),
                winshape.island_radius(int(w), int(h)), "island")
        except Exception as exc:
            error("app", f"refresh_island_region failed: {exc}")
            return False

    def island_resize(self, w, h):
        if self.island_window is None:
            return {"ok": False, "error": "الجزيرة مش شغالة"}
        try:
            sw, _ = self._screen_size()
            self.island_window.resize(int(w), int(h))
            self.island_window.move(max(0, (sw - int(w)) // 2), 12)
            self.refresh_island_region(int(w), int(h))
            return {"ok": True}
        except Exception as exc:
            error("app", f"island_resize failed: {exc}")
            return {"ok": False, "error": str(exc)}

    def island_expand(self):
        return self.island_resize(ISLAND_EXPANDED_W, ISLAND_EXPANDED_H)

    def island_collapse(self):
        # NO idle shrink: collapsed == full == 380x60, always.
        return self.island_resize(ISLAND_W, ISLAND_H)

    def push_mood(self, mood):
        try:
            if self.island_window is not None:
                import json
                self.island_window.evaluate_js(
                    "window.setMood && window.setMood(" +
                    json.dumps(mood, ensure_ascii=False) + ")")
        except Exception:
            pass

    # --- main-window controls ----------------------------------------------
    def window_minimize(self):
        try:
            if self.main_window is not None:
                self.main_window.minimize()
            return {"ok": True}
        except Exception as exc:
            error("app", f"window_minimize failed: {exc}")
            return {"ok": False, "error": str(exc)}

    def window_toggle_maximize(self):
        """pywebview HAS maximize()/restore() — use them with a flag
        (no is-maximized query exists)."""
        try:
            if self.main_window is not None:
                if self._main_maximized:
                    self.main_window.restore()
                    self._main_maximized = False
                else:
                    self.main_window.maximize()
                    self._main_maximized = True
            return {"maximized": bool(self._main_maximized)}
        except Exception as exc:
            error("app", f"window_toggle_maximize failed: {exc}")
            return {"maximized": bool(self._main_maximized),
                    "ok": False, "error": str(exc)}

    def window_close(self):
        info("app", "Close requested from the frameless header")
        threading.Thread(target=self.quit, daemon=True).start()
        return {"ok": True}

    def window_move_to(self, x, y):
        try:
            if self.main_window is not None:
                self.main_window.move(int(x), int(y))
            return {"ok": True}
        except Exception as exc:
            error("app", f"window_move_to failed: {exc}")
            return {"ok": False, "error": str(exc)}

    def show_main(self):
        try:
            if self.main_window is not None:
                self.main_window.show()
                self.main_window.restore()
                self._main_visible = True
        except Exception:
            pass

    def _on_main_closing(self):
        # Closing the main window hides it to the tray when the island
        # is on; otherwise it quits. (pywebview: returning False vetoes
        # the close — the hide below is what actually puts the window
        # in the tray; without it, closing would silently do nothing.)
        if self.settings.get("island_enabled", True) and self._tray:
            try:
                if self.main_window is not None:
                    self.main_window.hide()
                    self._main_visible = False
            except Exception:
                pass
            return False
        return True

    # --- tray / monitor ------------------------------------------------------
    def _start_tray(self):
        try:
            import pystray
            from PIL import Image
            icon_path = UI_DIR / "assets" / "mascot.webp"
            try:
                image = Image.open(icon_path).convert("RGBA")
            except Exception:
                image = Image.new("RGBA", (64, 64), (240, 199, 94, 255))
            menu = pystray.Menu(
                pystray.MenuItem("إظهار لونا", lambda: self.show_main()),
                pystray.MenuItem("إغلاق", lambda: self.quit()))
            self._tray = pystray.Icon("LONA", image, "LONA", menu)
            threading.Thread(target=self._tray.run, daemon=True).start()
        except Exception as exc:
            warn("app", f"Tray unavailable: {exc}")
            self._tray = None

    def _monitor_loop(self):
        while not self._stop.wait(60):
            try:
                overview = get_device_overview()
                alerts = health_mod.evaluate(overview, get_settings())
                for alert in alerts:
                    warn("health", f"{alert['message']} "
                                   f"(value {alert['value']})")
            except Exception as exc:
                error("monitor", f"Monitor tick failed: {exc}")

    # --- lifecycle -------------------------------------------------------------
    def quit(self):
        self._stop.set()
        try:
            if self._tray is not None:
                self._tray.stop()
        except Exception:
            pass
        try:
            if self.main_window is not None:
                self.main_window.destroy()
        except Exception:
            pass
        try:
            if self.island_window is not None:
                self.island_window.destroy()
        except Exception:
            pass
        os._exit(0)

    def run(self):
        if webview is None:
            raise RuntimeError("pywebview is not installed")
        if not winshape.IS_WINDOWS:
            info("app", "LONA v2 starting (non-Windows — window will fail)")
        # Single instance: if the event already exists, another
        # instance is running — signal it and exit. Otherwise create
        # the event and watch it.
        if winshape.signal_show_main():
            info("app", "Another instance is already running")
            return
        # Hold the installer mutex (Local\LONA-SingleInstance) so Inno
        # Setup's AppMutex check can see a running instance.
        winshape.create_single_instance_mutex()
        event = winshape.create_show_main_event()
        if event is not None:
            threading.Thread(
                target=winshape.watch_show_main_event,
                args=(event, self.show_main, self._stop.is_set),
                daemon=True).start()
        info("app", f"LONA v2 {VERSION} starting")
        if not invite.is_activated():
            self.invite_window = webview.create_window(
                "LONA — كود الدعوة", str(UI_DIR / "invite.html"),
                width=480, height=640, frameless=True, resizable=False,
                js_api=self.api)
            webview.start()
            if not invite.is_activated():
                return
        self._create_windows()
        self._start_tray()
        threading.Thread(target=self._monitor_loop, daemon=True).start()
        if self.settings.get("island_enabled", True):
            webview.start(self._apply_island_region)
        else:
            webview.start()
        info("app", "LONA exited")


def main():
    App().run()


if __name__ == "__main__":
    main()
