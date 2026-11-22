# LONA v1.6.4 reskin - island feature backends.
#
# Island method adapted from WindyLand (MIT, Copyright (c) 2026 Shabab)
# - method and interaction ideas only; no WindyLand code, palette,
# icons, or assets used. Full license text: THIRD_PARTY.md (repo root).
#
# Every function returns a plain JSON-safe dict and NEVER raises - a
# feature that cannot run here answers {"available": False, ...} (or
# {"ok": False, ...}) instead of crashing the bridge. Windows-only
# paths (SMTC music, clipboard listener, toast history) degrade
# gracefully off Windows; the pure parts (timer, prayer times, water,
# shelf bookkeeping) work everywhere and are headless-testable.
#
# DEFERRED, honestly: mic/camera in-use dots. There is no public
# Win32 or WinRT API that reports whether the mic or camera is in use
# without opening the device yourself - and opening it would itself
# light the indicator. Windows already shows its own mic/camera dots
# in the tray, so LONA does not fake a second one. media_indicators()
# says so and the island UI hides the dots. If a real API ever
# appears, this is the one function to replace.

import math
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import date, datetime, timedelta

import island_store
from logger import error, info, warn

IS_WINDOWS = sys.platform == "win32"

# Cairo (Mohamed's city). Prayer times default here; the island panel
# lets him switch method (Egypt / Makkah) but not the city yet.
_PRAYER_LAT = 30.0444
_PRAYER_LON = 31.2357
_PRAYER_TZ = "Africa/Cairo"
_PRAYER_METHODS = {
    "egypt": {"fajr": 19.5, "isha": 17.5, "isha_minutes": None},
    "makkah": {"fajr": 18.5, "isha": None, "isha_minutes": 90},
}

_TIMER_PRESETS_MIN = (1, 5, 10, 15, 25, 30, 45, 60)
_WATER_DEFAULT_GOAL_ML = 2000
_CLIPBOARD_MAX = 30
_CLIPBOARD_TEXT_MAX = 4000
_LRCLIB_URL = "https://lrclib.net/api/get"


# --- timer + stopwatch (pure, in-memory + persisted paused state) ----------
class IslandTimer:
    """One countdown timer OR stopwatch. State survives a restart only
    as a PAUSED snapshot (a running countdown is restored paused with
    the remaining time it had when saved) - monotonic clocks do not
    survive a restart, and silently finishing a timer overnight would
    be worse than pausing it."""

    def __init__(self):
        # RLock, not Lock: the public methods call self.state() while
        # already holding the lock (a plain Lock deadlocked here - the
        # countdown start never returned, found by headless test).
        self._lock = threading.RLock()
        self.mode = "idle"          # idle | countdown | stopwatch
        self.running = False
        self.duration_sec = 0       # countdown length
        self.ends_at = 0.0          # monotonic, countdown only
        self.remaining_sec = 0.0    # paused countdown
        self.started_at = 0.0       # monotonic, stopwatch only
        self.accumulated_sec = 0.0  # stopwatch elapsed while paused
        self.laps = []
        self._finished = False
        self._restore()

    # -- persistence -------------------------------------------------------
    def _restore(self):
        try:
            saved = island_store.load("timer", {}) or {}
            if isinstance(saved, dict) and saved.get("mode") in (
                    "countdown", "stopwatch"):
                self.mode = saved["mode"]
                self.duration_sec = float(saved.get("duration_sec") or 0)
                self.remaining_sec = float(saved.get("remaining_sec") or 0)
                self.accumulated_sec = float(
                    saved.get("accumulated_sec") or 0)
                self.laps = list(saved.get("laps") or [])
                self.running = False
        except Exception:
            pass

    def _persist(self):
        try:
            island_store.save("timer", {
                "mode": self.mode,
                "duration_sec": self.duration_sec,
                "remaining_sec": self._remaining_now(),
                "accumulated_sec": self._elapsed_now(),
                "laps": self.laps,
            })
        except Exception:
            pass

    # -- math ---------------------------------------------------------------
    def _remaining_now(self) -> float:
        if self.mode != "countdown":
            return 0.0
        if self.running:
            return max(0.0, self.ends_at - time.monotonic())
        return max(0.0, self.remaining_sec)

    def _elapsed_now(self) -> float:
        if self.mode != "stopwatch":
            return 0.0
        if self.running:
            return self.accumulated_sec + (
                time.monotonic() - self.started_at)
        return self.accumulated_sec

    # -- API -----------------------------------------------------------------
    def state(self) -> dict:
        with self._lock:
            finished = self._finished
            self._finished = False
            if self.mode == "countdown" and self.running \
                    and self._remaining_now() <= 0:
                self.running = False
                self.mode = "idle"
                finished = True
                self._persist()
            return {
                "ok": True,
                "mode": self.mode,
                "running": self.running,
                "remaining_sec": round(self._remaining_now(), 1),
                "elapsed_sec": round(self._elapsed_now(), 1),
                "duration_sec": self.duration_sec,
                "laps": [round(l, 1) for l in self.laps],
                "finished": finished,
                "presets_min": list(_TIMER_PRESETS_MIN),
            }

    def start_countdown(self, minutes) -> dict:
        try:
            seconds = float(minutes or 0) * 60.0
        except Exception:
            seconds = 0.0
        if seconds <= 0:
            return {"ok": False, "detail": "bad_minutes"}
        with self._lock:
            self.mode = "countdown"
            self.duration_sec = seconds
            self.remaining_sec = seconds
            self.ends_at = time.monotonic() + seconds
            self.running = True
            self.laps = []
            self._finished = False
            self._persist()
            return self.state()

    def start_stopwatch(self) -> dict:
        with self._lock:
            self.mode = "stopwatch"
            self.started_at = time.monotonic()
            self.accumulated_sec = 0.0
            self.running = True
            self.laps = []
            self._finished = False
            self._persist()
            return self.state()

    def pause(self) -> dict:
        with self._lock:
            if not self.running:
                return self.state()
            if self.mode == "countdown":
                self.remaining_sec = self._remaining_now()
            elif self.mode == "stopwatch":
                self.accumulated_sec = self._elapsed_now()
            self.running = False
            self._persist()
            return self.state()

    def resume(self) -> dict:
        with self._lock:
            if self.running or self.mode == "idle":
                return self.state()
            if self.mode == "countdown":
                if self.remaining_sec <= 0:
                    self.mode = "idle"
                    return self.state()
                self.ends_at = time.monotonic() + self.remaining_sec
            elif self.mode == "stopwatch":
                self.started_at = time.monotonic()
            self.running = True
            self._persist()
            return self.state()

    def reset(self) -> dict:
        with self._lock:
            self.mode = "idle"
            self.running = False
            self.duration_sec = 0
            self.remaining_sec = 0.0
            self.accumulated_sec = 0.0
            self.laps = []
            self._finished = False
            self._persist()
            return self.state()

    def lap(self) -> dict:
        with self._lock:
            if self.mode == "stopwatch":
                self.laps.append(self._elapsed_now())
            elif self.mode == "countdown":
                self.laps.append(self._remaining_now())
            self._persist()
            return self.state()


_TIMER = None
_TIMER_LOCK = threading.Lock()


def get_timer() -> IslandTimer:
    global _TIMER
    with _TIMER_LOCK:
        if _TIMER is None:
            _TIMER = IslandTimer()
        return _TIMER


# --- prayer times (pure solar math, standard astronomical formulas) --------
def _fix_angle(a: float) -> float:
    return a - 360.0 * math.floor(a / 360.0)


def _fix_hour(a: float) -> float:
    return a - 24.0 * math.floor(a / 24.0)


def _julian_date(year: int, month: int, day: int) -> float:
    if month <= 2:
        year -= 1
        month += 12
    a = math.floor(year / 100)
    b = 2 - a + math.floor(a / 4)
    return (math.floor(365.25 * (year + 4716))
            + math.floor(30.6001 * (month + 1))
            + day + b - 1524.5)


def _sun_position(jd: float):
    """(declination deg, equation-of-time hours) for a Julian day."""
    d = jd - 2451545.0
    g = _fix_angle(357.529 + 0.98560028 * d)
    q = _fix_angle(280.459 + 0.98564736 * d)
    lam = _fix_angle(q + 1.915 * math.sin(math.radians(g))
                     + 0.020 * math.sin(math.radians(2 * g)))
    e = 23.439 - 0.00000036 * d
    decl = math.degrees(math.asin(math.sin(math.radians(e))
                                  * math.sin(math.radians(lam))))
    ra = math.degrees(math.atan2(
        math.cos(math.radians(e)) * math.sin(math.radians(lam)),
        math.cos(math.radians(lam)))) / 15.0
    eqt = q / 15.0 - _fix_hour(ra)
    if eqt > 12:
        eqt -= 24
    elif eqt < -12:
        eqt += 24
    return decl, eqt


def _cairo_utc_offset(day: date) -> float:
    """UTC offset in hours for Africa/Cairo on `day`. ZoneInfo first;
    Egypt's DST rule (last Friday of April -> last Thursday of
    October, +3 inside) as the fallback when the tz database is not
    available (Windows ships none for zoneinfo without tzdata)."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(_PRAYER_TZ)
        noon = datetime(day.year, day.month, day.day, 12, 0, tzinfo=tz)
        return noon.utcoffset().total_seconds() / 3600.0
    except Exception:
        pass
    def _last_weekday(year, month, weekday):
        # Last `weekday` (Mon=0) of the month.
        if month == 12:
            nxt = date(year + 1, 1, 1)
        else:
            nxt = date(year, month + 1, 1)
        d = nxt - timedelta(days=1)
        while d.weekday() != weekday:
            d -= timedelta(days=1)
        return d
    start = _last_weekday(day.year, 4, 4)    # last Friday of April
    end = _last_weekday(day.year, 10, 3)     # last Thursday of October
    return 3.0 if start <= day <= end else 2.0


def _hour_angle(alt_deg: float, lat: float, decl: float) -> float:
    """Hour angle in HOURS for a solar altitude `alt_deg`. Raises
    ValueError when the sun never reaches that altitude that day."""
    lat_r, decl_r = math.radians(lat), math.radians(decl)
    cos_h = ((math.sin(math.radians(alt_deg))
              - math.sin(lat_r) * math.sin(decl_r))
             / (math.cos(lat_r) * math.cos(decl_r)))
    cos_h = min(1.0, max(-1.0, cos_h))
    return math.degrees(math.acos(cos_h)) / 15.0


def _fmt(hours: float) -> str:
    hours = _fix_hour(hours)
    h = int(hours)
    m = int(round((hours - h) * 60))
    if m == 60:
        h = (h + 1) % 24
        m = 0
    return f"{h:02d}:{m:02d}"


def prayer_times(day: date | None = None, method: str = "egypt",
                 lat: float = _PRAYER_LAT, lon: float = _PRAYER_LON) -> dict:
    """Prayer times for `day` (default today) as {"ok", "date",
    "method", "times": {fajr, sunrise, dhuhr, asr, maghrib, isha}}.
    Methods: egypt (fajr 19.5, isha 17.5), makkah (fajr 18.5, isha
    90 min after maghrib). Asr is Shafi'i (shadow factor 1)."""
    try:
        day = day or date.today()
        cfg = _PRAYER_METHODS.get(str(method or "egypt").lower())
        if cfg is None:
            return {"ok": False, "detail": "bad_method"}
        jd = _julian_date(day.year, day.month, day.day)
        decl, eqt = _sun_position(jd)
        tz = _cairo_utc_offset(day)
        # Solar noon, local clock time.
        noon = 12.0 + tz - lon / 15.0 - eqt
        fajr = noon - _hour_angle(-cfg["fajr"], lat, decl)
        sunrise = noon - _hour_angle(-0.833, lat, decl)
        sunset = noon + _hour_angle(-0.833, lat, decl)
        # Asr altitude: cot(angle) = 1 + tan(|lat - decl|).
        asr_alt = math.degrees(math.atan(
            1.0 / (1.0 + math.tan(math.radians(abs(lat - decl))))))
        asr = noon + _hour_angle(asr_alt, lat, decl)
        if cfg["isha_minutes"] is not None:
            isha_h = sunset + cfg["isha_minutes"] / 60.0
        else:
            isha_h = noon + _hour_angle(-cfg["isha"], lat, decl)
        return {
            "ok": True,
            "date": day.isoformat(),
            "method": str(method).lower(),
            "times": {
                "fajr": _fmt(fajr),
                "sunrise": _fmt(sunrise),
                "dhuhr": _fmt(noon),
                "asr": _fmt(asr),
                "maghrib": _fmt(sunset),
                "isha": _fmt(isha_h),
            },
        }
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


# --- water reminder (pure, per-day bookkeeping) ------------------------------
def _water_load() -> dict:
    data = island_store.load("water", {}) or {}
    if not isinstance(data, dict):
        data = {}
    today = date.today().isoformat()
    if data.get("day") != today:
        data = {"day": today, "goal_ml": data.get("goal_ml")
                or _WATER_DEFAULT_GOAL_ML,
                "entries": [], "enabled": data.get("enabled", True),
                "interval_min": data.get("interval_min") or 60}
        island_store.save("water", data)
    return data


def water_state() -> dict:
    try:
        data = _water_load()
        total = sum(int(e.get("ml") or 0) for e in data.get("entries", []))
        last = None
        if data.get("entries"):
            last = data["entries"][-1].get("at")
        return {"ok": True, "day": data.get("day"),
                "goal_ml": int(data.get("goal_ml") or _WATER_DEFAULT_GOAL_ML),
                "drank_ml": total,
                "remaining_ml": max(
                    0, int(data.get("goal_ml") or _WATER_DEFAULT_GOAL_ML)
                    - total),
                "last_drink_at": last,
                "enabled": bool(data.get("enabled", True)),
                "interval_min": int(data.get("interval_min") or 60)}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def water_add(ml) -> dict:
    try:
        amount = int(ml or 0)
    except Exception:
        amount = 0
    if amount <= 0 or amount > 2000:
        return {"ok": False, "detail": "bad_amount"}
    data = _water_load()
    data.setdefault("entries", []).append({
        "at": datetime.now().strftime("%H:%M"), "ml": amount})
    island_store.save("water", data)
    return water_state()


def water_set_goal(ml) -> dict:
    try:
        goal = int(ml or 0)
    except Exception:
        goal = 0
    if goal < 250 or goal > 6000:
        return {"ok": False, "detail": "bad_goal"}
    data = _water_load()
    data["goal_ml"] = goal
    island_store.save("water", data)
    return water_state()


def water_set_reminder(enabled, interval_min=None) -> dict:
    data = _water_load()
    data["enabled"] = bool(enabled)
    if interval_min is not None:
        try:
            data["interval_min"] = max(
                15, min(240, int(interval_min)))
        except Exception:
            pass
    island_store.save("water", data)
    return water_state()


# --- file shelf (pure bookkeeping; open/add-picker live in main.py) ---------
def _shelf_load() -> list:
    data = island_store.load("shelf", []) or []
    return [e for e in data if isinstance(e, dict) and e.get("id")
            and e.get("path")]


def shelf_list() -> dict:
    try:
        entries = _shelf_load()
        # Drop files that no longer exist, quietly.
        kept = [e for e in entries if os.path.isfile(e["path"])]
        if len(kept) != len(entries):
            island_store.save("shelf", kept)
        return {"ok": True, "files": kept}
    except Exception as exc:
        return {"ok": False, "detail": str(exc), "files": []}


def shelf_add(path) -> dict:
    path = str(path or "").strip()
    if not path or not os.path.isfile(path):
        return {"ok": False, "detail": "not_a_file"}
    entries = _shelf_load()
    if any(os.path.normcase(e["path"]) == os.path.normcase(path)
           for e in entries):
        return {"ok": False, "detail": "already_there"}
    entries.append({"id": uuid.uuid4().hex[:8],
                    "name": os.path.basename(path), "path": path,
                    "added": datetime.now().strftime("%Y-%m-%d %H:%M")})
    island_store.save("shelf", entries)
    return {"ok": True, "files": entries}


def shelf_remove(entry_id) -> dict:
    entries = [e for e in _shelf_load()
               if str(e.get("id")) != str(entry_id)]
    island_store.save("shelf", entries)
    return {"ok": True, "files": entries}


def shelf_open(entry_id) -> dict:
    for entry in _shelf_load():
        if str(entry.get("id")) == str(entry_id):
            path = entry["path"]
            try:
                if IS_WINDOWS:
                    os.startfile(path)  # noqa: S606 - user-picked file
                else:
                    subprocess.Popen(
                        ["xdg-open", path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
                return {"ok": True}
            except Exception as exc:
                return {"ok": False, "detail": str(exc)}
    return {"ok": False, "detail": "not_found"}


# --- clipboard history (Windows polling; off Windows: unavailable) ----------
_CF_UNICODETEXT = 13
_CF_DIB = 8
_CF_HDROP = 15


class ClipboardHistory:
    """Poll-based history (a real clipboard-viewer chain is not
    feasible from the pywebview process, so a 1 s poll is the honest
    trade - the brief sanctions polling via ctypes). Text, dropped
    files, and images (metadata only - the bitmap itself is not
    stored). Consecutive duplicates are ignored; max 30 entries."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries = island_store.load("clipboard", []) or []
        self._entries = [e for e in self._entries
                         if isinstance(e, dict) and e.get("id")]
        self._last_seen = None

    def available(self) -> bool:
        return IS_WINDOWS

    def _persist(self):
        island_store.save("clipboard", self._entries[-_CLIPBOARD_MAX:])

    def list(self) -> dict:
        if not IS_WINDOWS:
            return {"ok": False, "available": False,
                    "detail": "windows_only", "entries": []}
        with self._lock:
            return {"ok": True, "available": True,
                    "entries": list(reversed(self._entries))}

    def _push(self, entry: dict):
        with self._lock:
            self._entries.append(entry)
            self._entries = self._entries[-_CLIPBOARD_MAX:]
            self._persist()

    def pin(self, entry_id, pinned) -> dict:
        with self._lock:
            for entry in self._entries:
                if str(entry.get("id")) == str(entry_id):
                    entry["pinned"] = bool(pinned)
                    self._persist()
                    return {"ok": True, "entry": dict(entry)}
        return {"ok": False, "detail": "not_found"}

    def delete(self, entry_id) -> dict:
        with self._lock:
            self._entries = [e for e in self._entries
                             if str(e.get("id")) != str(entry_id)]
            self._persist()
            return {"ok": True}

    def clear(self) -> dict:
        with self._lock:
            self._entries = [e for e in self._entries if e.get("pinned")]
            self._persist()
            return {"ok": True}

    def copy_back(self, entry_id) -> dict:
        """Put a text entry back on the clipboard (text only - files
        and images cannot be re-set reliably through this path)."""
        if not IS_WINDOWS:
            return {"ok": False, "detail": "windows_only"}
        target = None
        with self._lock:
            for entry in self._entries:
                if str(entry.get("id")) == str(entry_id):
                    target = entry
                    break
        if target is None or target.get("kind") != "text":
            return {"ok": False, "detail": "not_text"}
        return _set_clipboard_text(target.get("text") or "")

    def poll_once(self):
        """One clipboard read. Returns the new entry, or None when
        nothing changed / the clipboard is busy / not text-like."""
        if not IS_WINDOWS:
            return None
        try:
            import ctypes as _ct
            user32 = _ct.windll.user32
            if not user32.OpenClipboard(None):
                return None
            try:
                if user32.IsClipboardFormatAvailable(_CF_UNICODETEXT):
                    data = user32.GetClipboardData(_CF_UNICODETEXT)
                    if data:
                        text = _ct.wstring_at(data) or ""
                        text = text.strip()
                        if text:
                            return self._note_text(text[:_CLIPBOARD_TEXT_MAX])
                elif user32.IsClipboardFormatAvailable(_CF_HDROP):
                    data = user32.GetClipboardData(_CF_HDROP)
                    if data:
                        files = _read_hdrop(data)
                        if files:
                            return self._note_files(files)
                elif user32.IsClipboardFormatAvailable(_CF_DIB):
                    data = user32.GetClipboardData(_CF_DIB)
                    if data:
                        width, height = _dib_size(data)
                        return self._note_image(width, height)
                return None
            finally:
                user32.CloseClipboard()
        except Exception:
            return None

    def _note_text(self, text: str):
        key = ("text", text)
        if key == self._last_seen:
            return None
        self._last_seen = key
        entry = {"id": uuid.uuid4().hex[:8], "kind": "text", "text": text,
                 "pinned": False,
                 "at": datetime.now().strftime("%H:%M")}
        self._push(entry)
        return entry

    def _note_files(self, files):
        key = ("files", tuple(files))
        if key == self._last_seen:
            return None
        self._last_seen = key
        entry = {"id": uuid.uuid4().hex[:8], "kind": "files",
                 "text": "\n".join(files), "files": files,
                 "pinned": False,
                 "at": datetime.now().strftime("%H:%M")}
        self._push(entry)
        return entry

    def _note_image(self, width, height):
        key = ("image", width, height)
        if key == self._last_seen:
            return None
        self._last_seen = key
        label = "صورة من الحافظة"
        if width and height:
            label += f" ({width}×{height})"
        entry = {"id": uuid.uuid4().hex[:8], "kind": "image",
                 "text": label, "pinned": False,
                 "at": datetime.now().strftime("%H:%M")}
        self._push(entry)
        return entry


def _read_hdrop(handle) -> list:
    """File paths from a CF_HDROP handle (DragQueryFileW)."""
    import ctypes as _ct
    shell32 = _ct.windll.shell32
    count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
    files = []
    for i in range(min(count, 20)):
        buf = _ct.create_unicode_buffer(260)
        if shell32.DragQueryFileW(handle, i, buf, 260):
            files.append(buf.value)
    return files


def _dib_size(handle):
    """(width, height) from a CF_DIB BITMAPINFOHEADER, or (0, 0)."""
    import ctypes as _ct
    try:
        ptr = _ct.cast(handle, _ct.POINTER(_ct.c_long))
        width, height = int(ptr[0]), abs(int(ptr[1]))
        return width, height
    except Exception:
        return 0, 0


def _set_clipboard_text(text: str) -> dict:
    import ctypes as _ct
    user32 = _ct.windll.user32
    kernel32 = _ct.windll.kernel32
    try:
        if not user32.OpenClipboard(None):
            return {"ok": False, "detail": "clipboard_busy"}
        try:
            user32.EmptyClipboard()
            buf = _ct.create_unicode_buffer(text)
            size = _ct.sizeof(buf)
            mem = kernel32.GlobalAlloc(0x0002, size)  # GMEM_MOVEABLE
            if not mem:
                return {"ok": False, "detail": "alloc_failed"}
            locked = _ct.cast(kernel32.GlobalLock(mem),
                              _ct.POINTER(_ct.c_wchar))
            _ct.memmove(locked, buf, size)
            kernel32.GlobalUnlock(mem)
            if not user32.SetClipboardData(_CF_UNICODETEXT, mem):
                kernel32.GlobalFree(mem)
                return {"ok": False, "detail": "set_failed"}
            return {"ok": True}
        finally:
            user32.CloseClipboard()
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


_CLIPBOARD = None
_CLIPBOARD_LOCK = threading.Lock()


def get_clipboard() -> ClipboardHistory:
    global _CLIPBOARD
    with _CLIPBOARD_LOCK:
        if _CLIPBOARD is None:
            _CLIPBOARD = ClipboardHistory()
        return _CLIPBOARD


def clipboard_watch_loop(stop_flag, interval_sec: float = 1.0):
    """Daemon poll loop for the clipboard history. Never raises."""
    if not IS_WINDOWS:
        return
    history = get_clipboard()
    while not stop_flag():
        try:
            history.poll_once()
        except Exception:
            pass
        deadline = time.monotonic() + max(0.2, float(interval_sec))
        while time.monotonic() < deadline:
            if stop_flag():
                return
            time.sleep(0.1)


# --- music: WinRT SMTC now-playing + LRCLIB synced lyrics -------------------
def _smtc_session_blocking(timeout_sec: float = 5.0) -> dict:
    """Read the current SMTC session on a worker thread (the WinRT
    calls are async; asyncio.run must not share the bridge thread's
    loop). Returns the raw media properties + playback info."""
    import asyncio
    import concurrent.futures

    def _run():
        async def _get():
            from winrt.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as SMTC)
            manager = await SMTC.request_async()
            session = manager.get_current_session()
            if session is None:
                return None
            props = await session.try_get_media_properties_async()
            info_ = session.get_playback_info()
            timeline = None
            try:
                timeline = session.get_timeline_properties()
            except Exception:
                timeline = None
            status = 0
            try:
                status = int(info_.playback_status)
            except Exception:
                pass
            return {
                "title": props.title or "",
                "artist": props.artist or "",
                "album": props.album_title or "",
                "status": status,
                "position": (timeline.position.total_seconds()
                             if timeline is not None and
                             timeline.position is not None else 0),
                "duration": (timeline.end_time.total_seconds()
                             if timeline is not None and
                             timeline.end_time is not None else 0),
            }
        return asyncio.run(_get())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run)
        return future.result(timeout=timeout_sec)


_STATUS_NAMES = {0: "closed", 1: "changing", 2: "stopped",
                 3: "playing", 4: "paused"}


def now_playing() -> dict:
    """Current media from Windows SMTC (Spotify, browser, any player
    that publishes a session). No session / no WinRT -> available
    False, never an invented track."""
    if not IS_WINDOWS:
        return {"ok": True, "available": False, "detail": "windows_only"}
    try:
        raw = _smtc_session_blocking()
    except Exception as exc:
        return {"ok": True, "available": False,
                "detail": f"smtc_failed: {exc}"}
    if not raw or not (raw.get("title") or raw.get("artist")):
        return {"ok": True, "available": False, "detail": "no_session"}
    return {"ok": True, "available": True,
            "title": raw["title"], "artist": raw["artist"],
            "album": raw["album"],
            "status": _STATUS_NAMES.get(raw["status"], "unknown"),
            "position_sec": round(float(raw.get("position") or 0), 1),
            "duration_sec": round(float(raw.get("duration") or 0), 1)}


_LYRIC_CACHE = {}
_LYRIC_CACHE_LOCK = threading.Lock()


def _parse_synced(raw: str):
    """'[mm:ss.xx] line' -> [{"t": seconds, "text": line}]."""
    out = []
    for line in (raw or "").splitlines():
        match = re.match(r"\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)", line)
        if match:
            minutes = int(match.group(1))
            seconds = float(match.group(2))
            out.append({"t": round(minutes * 60 + seconds, 2),
                        "text": match.group(3).strip()})
    out.sort(key=lambda item: item["t"])
    return out


def lyrics(artist, title, album="", duration_sec=0) -> dict:
    """Synced lyrics from LRCLIB (free, no key). Cached per track.
    Returns {"ok", "found", "synced": [...], "plain": "..."}."""
    artist = str(artist or "").strip()
    title = str(title or "").strip()
    if not artist or not title:
        return {"ok": False, "detail": "need_artist_title"}
    key = (artist.lower(), title.lower())
    with _LYRIC_CACHE_LOCK:
        if key in _LYRIC_CACHE:
            return dict(_LYRIC_CACHE[key])
    try:
        import requests
        params = {"artist_name": artist, "track_name": title,
                  "album_name": str(album or "")}
        try:
            duration = float(duration_sec or 0)
        except Exception:
            duration = 0
        if duration > 0:
            params["duration"] = int(duration)
        resp = requests.get(_LRCLIB_URL, params=params, timeout=8)
        if resp.status_code == 404:
            result = {"ok": True, "found": False, "synced": [],
                      "plain": ""}
        elif resp.status_code != 200:
            result = {"ok": False,
                      "detail": f"lrclib_{resp.status_code}"}
        else:
            data = resp.json() or {}
            synced = _parse_synced(data.get("syncedLyrics") or "")
            result = {"ok": True, "found": True, "synced": synced,
                      "plain": data.get("plainLyrics") or ""}
        with _LYRIC_CACHE_LOCK:
            _LYRIC_CACHE[key] = result
        return dict(result)
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


# --- Windows notifications inside the notch ---------------------------------
def recent_notifications(settings=None) -> dict:
    """Current Windows toasts (app + title + body), newest first,
    capped at 10. Read-only: a throwaway watcher, no thread, no
    delivery - the running watcher (main.py) still owns alerts. Off
    Windows or without WinRT access: available False."""
    if not IS_WINDOWS:
        return {"ok": True, "available": False,
                "detail": "windows_only", "notifications": []}
    try:
        import notifications as notif_mod
        watcher = notif_mod.NotificationWatcher(settings or {})
        items = watcher._fetch_current() or []
        return {"ok": True, "available": True,
                "notifications": items[:10]}
    except Exception as exc:
        return {"ok": True, "available": False,
                "detail": str(exc), "notifications": []}


# --- mic/camera dots (DEFERRED - see module header) --------------------------
def media_indicators() -> dict:
    """No public Win32/WinRT API reports mic/camera in-use state
    without opening the device yourself (which would itself light
    the indicator). Windows already shows its own tray dots. The
    island hides its dots until this ever becomes available."""
    return {"ok": True, "available": False,
            "detail": "no_public_api",
            "mic_in_use": False, "camera_in_use": False}


# --- due task reminders (thin wrapper over tasks.py) -------------------------
def due_task_reminders() -> dict:
    """Tasks whose remind_at has passed and are not done (for the
    island's reminder check)."""
    try:
        import tasks as tasks_mod
        return {"ok": True, "tasks": tasks_mod.due_tasks()}
    except Exception as exc:
        return {"ok": False, "detail": str(exc), "tasks": []}
