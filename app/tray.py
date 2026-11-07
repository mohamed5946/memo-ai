# Memo AI - System tray (v1.4.1).
#
# The Dynamic Island is gone (disabled by the user's decision), so the
# tray is the way back to the main window after closing it: closing the
# window (X) hides it to the tray instead of quitting, and the tray
# menu offers "Open Memo AI" / "Quit". The standing rule still holds:
# the app never fully closes except through an explicit Quit (tray menu
# or Settings) - never from the window's X button alone.
#
# Implementation: pystray (pure Python, Win32 backend via ctypes - no
# pywin32 needed). The icon runs its own message loop in a daemon
# thread, so it never blocks the pywebview GUI thread. The icon image
# is the bundled cat avatar (Pillow is already a dependency).
#
# Headless safety: the pystray import is guarded - on a machine without
# a tray backend (Linux without X, or a broken install) the module
# still imports and TrayManager reports available=False, so the app
# falls back to minimizing the window to the taskbar instead of hiding
# it (a hidden window with no tray would strand the user). Tests inject
# a fake icon factory and never touch the real backend.

import threading
from pathlib import Path

try:
    import pystray
    from PIL import Image
    _PYSTRAY_OK = True
except Exception:
    pystray = None
    Image = None
    _PYSTRAY_OK = False


def _default_icon_factory(name, image, title, menu):
    """Real pystray icon (only called when pystray imported fine)."""
    return pystray.Icon(name, image, title, menu)


def _default_menu(on_open, on_quit):
    """Tray menu: Open (default action on left-click) + Quit."""
    return pystray.Menu(
        pystray.MenuItem("فتح Memo AI", lambda icon, item: on_open(),
                         default=True),
        pystray.MenuItem("إنهاء", lambda icon, item: on_quit()),
    )


class TrayManager:
    """Owns the tray icon. Callbacks run on the tray thread - they must
    be thread-safe (MemoApp.show_main/quit already are: show() posts to
    the GUI thread, quit() destroys windows from any thread)."""

    def __init__(self, on_open, on_quit, icon_path=None,
                 icon_factory=None):
        self._on_open = on_open
        self._on_quit = on_quit
        self._icon_path = Path(icon_path) if icon_path else None
        # Tests inject a fake factory; production uses pystray.
        self._factory = icon_factory
        self._icon = None
        self._thread = None
        self._running = False

    @property
    def available(self) -> bool:
        """True when a real tray backend exists (or a test factory)."""
        return self._factory is not None or _PYSTRAY_OK

    @property
    def running(self) -> bool:
        return self._running and self._icon is not None

    def _load_image(self):
        """Tray icon image (64x64 RGBA). Falls back to a plain square
        if the avatar file is missing - never fail startup over an icon."""
        try:
            if self._icon_path and self._icon_path.is_file():
                img = Image.open(str(self._icon_path)).convert("RGBA")
                img.thumbnail((64, 64))
                return img
        except Exception:
            pass
        try:
            return Image.new("RGBA", (64, 64), (13, 18, 28, 255))
        except Exception:
            return None

    def start(self) -> bool:
        """Create the icon and run it in a daemon thread. False when no
        tray backend exists (caller falls back to taskbar minimize)."""
        if self._running:
            return True
        if self._factory is None and not _PYSTRAY_OK:
            return False
        try:
            if self._factory is not None:
                menu = None  # the fake factory records callbacks itself
                image = None
                self._icon = self._factory(
                    "Memo AI", image, "Memo AI", menu,
                    on_open=self._on_open, on_quit=self._on_quit)
            else:
                image = self._load_image()
                if image is None:
                    return False
                menu = _default_menu(self._on_open, self._on_quit)
                self._icon = _default_icon_factory(
                    "Memo AI", image, "Memo AI", menu)
            self._thread = threading.Thread(
                target=self._icon.run, daemon=True, name="memo-tray")
            self._thread.start()
            self._running = True
            return True
        except Exception:
            self._icon = None
            self._running = False
            return False

    def stop(self) -> None:
        """Remove the icon. Safe to call when never started."""
        icon, thread = self._icon, self._thread
        self._icon = None
        self._thread = None
        self._running = False
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
        if thread is not None and thread is not threading.current_thread():
            try:
                thread.join(timeout=2)
            except Exception:
                pass

    def set_visible(self, visible: bool) -> None:
        """Show/hide the icon (pystray supports .visible on Windows).
        Best-effort: a missing attribute never raises."""
        if self._icon is None:
            return
        try:
            self._icon.visible = bool(visible)
        except Exception:
            pass
