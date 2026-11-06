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
# v1.2 TRANSPARENCY (2026-11-06): the island/pet still showed a white
# rectangle on the laptop. Root cause, verified against the pywebview 6.2
# source (platforms/winforms.py + edgechromium.py): with transparent=True
# pywebview sets the WebView2 control's DefaultBackgroundColor to Transparent
# - but ONLY on the EdgeChromium backend. If the WebView2 runtime is missing
# or broken, pywebview silently falls back to the old MSHTML (IE) backend,
# which cannot do transparency at all and paints background_color (default
# #FFFFFF) - a white rectangle. A second culprit: `backdrop-filter: blur()`
# in the island CSS forces the window layer to composite opaquely on some
# WebView2 builds. Fixes in v1.2: (1) backdrop-filter removed from the
# island, replaced by a solid near-opaque navy; (2) island/pet windows pass
# background_color="#0B0F16" (dark navy) so a transparency failure degrades
# to a dark rectangle instead of a glaring white one; (3) both pages set
# html/body background transparent in JS on load, belt and braces;
# (4) README lists the checks: pywebview>=6.2 actually installed, and
# WebView2 runtime present/updated. The pet now also roams, reacts to
# clicks, and plays with a ball when parked (see pet.html).

import ctypes
import json
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
BASE_DIR = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

import webview
from logger import error, info, log_text, read_recent
from settings_store import keys_status, load_settings, save_settings

import brain
import hear
import rules
import speak
import updater
import weather

VERSION = "1.2.0"
MAIN_W, MAIN_H = 960, 720
ISLAND_W, ISLAND_H = 380, 56
ISLAND_EXPANDED_H = 120
PET_SIZE = 150

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
            "keys": keys_status(),
            "settings": {k: v for k, v in _APP.settings.items()
                         if k != "owner_code"},
            "owner_unlocked": _APP.settings.get("owner_unlocked", False),
        }

    def get_settings(self):
        return {k: v for k, v in _APP.settings.items()
                if k not in ("owner_code", "owner_unlocked")}

    def save_settings(self, new_values):
        try:
            data = json.loads(new_values) if isinstance(new_values, str) else new_values
        except Exception as exc:
            error("app", f"Bad settings payload: {exc}")
            return {"ok": False}
        for key in ("ai_name", "user_name", "language", "voice",
                    "greeting_enabled", "avatar", "pet_visible", "pet_parked"):
            if key in data:
                _APP.settings[key] = data[key]
        # Secret code: only overwritten when the field is non-empty.
        if data.get("owner_code"):
            _APP.settings["owner_code"] = str(data["owner_code"])
            info("app", "Owner secret code updated (value never logged)")
        ok = save_settings(_APP.settings)
        info("app", "Settings saved from UI")
        _APP.apply_settings()
        return {"ok": ok}

    # -- chat --------------------------------------------------------------
    def chat(self, text):
        text = (text or "").strip()
        if not text:
            return {"reply": "", "engine": "none"}
        # Secret code unlocks owner mode (never echoed back).
        code = _APP.settings.get("owner_code", "")
        if code and text == code:
            _APP.settings["owner_unlocked"] = True
            info("app", "Owner mode unlocked via secret code")
            return {"reply": "تمام، وضع المالك اتفتح — تقدر تسألني عن أي حاجة، "
                             "بما فيها الكود والسورس.",
                    "engine": "owner"}
        lang = _APP.settings.get("language", "ar")
        hit = rules.check(text, _APP.settings.get("owner_unlocked", False), lang)
        if hit:
            _APP.speak_async(hit["reply"])
            return {"reply": hit["reply"], "engine": hit["source"]}
        result = brain.ask(text, _APP.settings.get("ai_name", "Memo"),
                           _APP.settings.get("user_name", "Mohamed"), lang)
        _APP.speak_async(result["reply"])
        return {"reply": result["reply"], "engine": result["engine"]}

    def voice_chat(self):
        """Record 5s -> transcribe -> answer -> speak. Returns heard + reply."""
        lang = _APP.settings.get("language", "ar")
        heard = hear.listen(lang)
        if not heard["text"]:
            return {"heard": "", "reply": heard.get("error", "مقدرتش أسمعك."),
                    "engine": "none"}
        result = self.chat(heard["text"])
        return {"heard": heard["text"], "reply": result["reply"],
                "engine": result["engine"], "hear_engine": heard["engine"]}

    def stop_speaking(self):
        speak.stop()
        return {"ok": True}

    # -- windows -----------------------------------------------------------
    def open_main(self):
        _APP.show_main()
        return {"ok": True}

    def island_expand(self):
        try:
            if _APP.island_window:
                _APP.island_window.resize(ISLAND_W, ISLAND_EXPANDED_H)
        except Exception as exc:
            error("ui", f"Could not expand island: {exc}")
        return {"ok": True}

    def island_shrink(self):
        try:
            if _APP.island_window:
                _APP.island_window.resize(ISLAND_W, ISLAND_H)
        except Exception as exc:
            error("ui", f"Could not shrink island: {exc}")
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

    def speak_async(self, text: str):
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

    # -- startup -------------------------------------------------------------
    def _on_main_closing(self):
        # X button / taskbar close = minimize to island, NOT quit.
        if self._quitting:
            return True
        self.hide_main()
        return False

    def run(self):
        info("app", f"Memo AI v{VERSION} starting")
        sw, sh = screen_size()
        island_x = max(0, (sw - ISLAND_W) // 2)

        ui_dir = APP_DIR / "ui"
        self.island_window = webview.create_window(
            "Memo AI", str(ui_dir / "island.html"),
            width=ISLAND_W, height=ISLAND_H, x=island_x, y=12,
            frameless=True, transparent=True, on_top=True,
            resizable=False, background_color="#0B0F16", js_api=self.api)
        self.main_window = webview.create_window(
            "Memo AI", str(ui_dir / "index.html"),
            width=MAIN_W, height=MAIN_H, hidden=True,
            min_size=(640, 520), js_api=self.api)
        self.main_window.events.closing += self._on_main_closing
        self.pet_window = webview.create_window(
            "Memo AI Pet", str(ui_dir / "pet.html"),
            width=PET_SIZE, height=PET_SIZE,
            x=sw - PET_SIZE - 20, y=sh - PET_SIZE - 90,
            frameless=True, transparent=True, on_top=True,
            resizable=False, background_color="#0B0F16", js_api=self.api)
        if not self.settings.get("pet_visible", True):
            self.pet_window.hide()

        threading.Thread(target=self.morning_briefing, daemon=True).start()
        info("app", "Island started - full window opens only on tap")
        webview.start()
        info("app", "Memo AI exited")


def main():
    MemoApp().run()


if __name__ == "__main__":
    main()
