# Memo AI v1.4.2 - Background system monitor
# Polls RAM / disk / CPU / battery / CPU temperature and fires ONE alert
# per problem (per-kind cooldown, re-arms only after the condition
# clears). All readings are REAL numbers from psutil - nothing is ever
# invented, and a missing sensor is reported honestly as unavailable.
#
# Windows-only code (temperature via WMI) is fully import-guarded: on
# Linux the module imports and works, with temp_available=False and
# battery possibly None. The parent (main.py) owns all wiring - this
# module never touches settings_store, the UI, or the network.
#
# Settings come in as a plain dict (defaults merged in), so tests can
# inject their own values. The parent passes self.settings straight
# through; the exact keys live in DEFAULT_MONITOR_SETTINGS below and
# must be added to settings_store.DEFAULTS by the parent.

import ctypes
import os
import sys
import threading
import time
from datetime import datetime

import hidden_spawn  # v1.4.3: PowerShell temp probe must not pop a console

try:
    import psutil
except Exception:  # psutil is a required dependency (see requirements.txt)
    psutil = None

try:
    from logger import info, warn
except Exception:  # standalone import (tests import with app/ on path)
    def info(*args, **kwargs):
        return None

    def warn(*args, **kwargs):
        return None


# Exact settings keys the parent must add to settings_store.DEFAULTS.
# (Flat keys, same style as the rest of settings_store.)
DEFAULT_MONITOR_SETTINGS = {
    "monitor_enabled": True,
    "monitor_interval_sec": 60,        # int, seconds between polls
    "monitor_ram_free_mb": 500,        # alert when free RAM < this (MB)
    "monitor_ram_percent": 92,         # ... OR used RAM > this (%)
    "monitor_disk_free_gb": 10,        # alert when free disk < this (GB)
    "monitor_disk_percent_free": 10,   # ... OR free disk < this (%)
    "monitor_temp_warn_c": 85,         # CPU temp warning (>=, if available)
    "monitor_temp_critical_c": 90,     # CPU temp critical (>=, if available)
    "monitor_cpu_sustained_percent": 85,   # sustained CPU load threshold (%)
    "monitor_cpu_sustained_minutes": 5,    # ... averaged over this long
    "monitor_battery_warn_percent": 20,    # battery alert while discharging
    "monitor_battery_critical_percent": 10,
    "monitor_cooldown_min": 30,        # min gap between repeat alerts
                                        # of the same kind (see _should_fire)
}

# Temperature is probed at most this often (a WMI/PowerShell probe costs
# a subprocess spawn; there is no point doing it every 60 s).
_TEMP_PROBE_INTERVAL_SEC = 600

# Background sampler cadence (v1.6.1 item 21): fast psutil fields refresh
# this often (the UI polls every 5 s); slow fields (the GPU PowerShell
# probe, all-drive storage) refresh much less often -- spawning
# powershell.exe every 5 s for numbers that barely move is pure waste.
_SAMPLE_FAST_SEC = 5
_SAMPLE_SLOW_SEC = 60
# A cached reading older than this is re-seeded synchronously with fast
# fields only (psutil, never a subprocess) -- so a dead sampler thread
# can never leave the UI showing stale numbers, and the UI thread never
# waits on a subprocess spawn.
_STATUS_MAX_AGE_SEC = 10

# A temperature reading outside this range is sensor garbage, not a
# real temperature - it is discarded, never reported.
_TEMP_PLAUSIBLE_C = (0.0, 125.0)


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _system_drive() -> str:
    """Drive whose free space we watch: the system drive on Windows,
    '/' everywhere else."""
    if sys.platform == "win32":
        return os.environ.get("SystemDrive", "C:") + "\\"
    return "/"


# ---------------------------------------------------------------------------
# CPU temperature (Windows only, best effort, NEVER guessed)
# ---------------------------------------------------------------------------

def _probe_value_to_celsius(raw) -> float | None:
    """One raw CurrentReading from a WMI temperature-probe class
    (Win32_TemperatureProbe / DCIM_ThermalSensor / CIM_TemperatureSensor)
    -> Celsius, or None when it cannot be read honestly.

    Vendors disagree: most report tenths of Kelvin (~2730-3100 for a
    live machine), some report Celsius directly. Tenths-Kelvin values
    are always >200, real Celsius is always <=125, so the two ranges
    never overlap; anything in between (126-200) is discarded rather
    than guessed. The plausibility filter in read_cpu_temp() still
    applies on top."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value > 200:
        return value / 10.0 - 273.15
    if _TEMP_PLAUSIBLE_C[0] <= value <= _TEMP_PLAUSIBLE_C[1]:
        return value
    return None


def _wmi_temps() -> list:
    """Temperatures via the `wmi` package, if it happens to be installed.
    Tries MSAcpi_ThermalZoneTemperature (root\\wmi) and the
    LibreHardwareMonitor / OpenHardwareMonitor Sensor classes, plus
    (v1.6.1) Win32_TemperatureProbe (root\\cimv2) and the Dell DCIM
    thermal sensors (root\\dcim\\sysman, present when Dell Command
    Monitor is installed). Every namespace is best-effort: a missing
    namespace or class is skipped silently, never raised."""
    temps = []
    try:
        import wmi as _wmi
    except Exception:
        return temps
    try:
        c = _wmi.WMI(namespace=r"root\wmi")
        for zone in c.MSAcpi_ThermalZoneTemperature():
            kelvin_tenths = getattr(zone, "CurrentTemperature", None)
            if kelvin_tenths:
                temps.append(kelvin_tenths / 10.0 - 273.15)
    except Exception:
        pass
    for namespace in (r"root\LibreHardwareMonitor",
                      r"root\OpenHardwareMonitor"):
        try:
            c = _wmi.WMI(namespace=namespace)
            for sensor in c.Sensor():
                if getattr(sensor, "SensorType", "") == "Temperature":
                    value = getattr(sensor, "Value", None)
                    if value is not None:
                        temps.append(float(value))
        except Exception:
            pass
    # v1.6.1: generic temperature probes + Dell DCIM sensors.
    try:
        c = _wmi.WMI(namespace=r"root\cimv2")
        for probe in c.Win32_TemperatureProbe():
            celsius = _probe_value_to_celsius(
                getattr(probe, "CurrentReading", None))
            if celsius is not None:
                temps.append(celsius)
    except Exception:
        pass
    try:
        c = _wmi.WMI(namespace=r"root\dcim\sysman")
        for cls in ("DCIM_ThermalSensor", "CIM_TemperatureSensor"):
            try:
                for sensor in getattr(c, cls)():
                    celsius = _probe_value_to_celsius(
                        getattr(sensor, "CurrentReading", None))
                    if celsius is not None:
                        temps.append(celsius)
            except Exception:
                pass
    except Exception:
        pass
    return temps


def _powershell_temps() -> list:
    """Temperatures via PowerShell Get-CimInstance (no extra packages).
    Same three sources as _wmi_temps, in the same order, plus (v1.6.1)
    three more built-in Windows paths that need no install, driver or
    admin: Thermal Zone perf counters (Get-Counter, Kelvin -> Celsius),
    Win32_TemperatureProbe (root\\cimv2), and the Dell DCIM thermal
    sensors (root\\dcim\\sysman, when Dell Command Monitor is present).
    Each stage is a cheap no-op when its source does not exist; the
    first stage that yields a value wins."""
    import subprocess

    temps = []

    def _run(script: str) -> str:
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-Command", script],
                capture_output=True, text=True, timeout=10,
                **hidden_spawn.run_kwargs())
            return proc.stdout or ""
        except Exception:
            return ""

    out = _run("Get-CimInstance -Namespace root/wmi "
               "-ClassName MSAcpi_ThermalZoneTemperature "
               "| Select-Object -ExpandProperty CurrentTemperature")
    for line in out.splitlines():
        try:
            temps.append(float(line.strip()) / 10.0 - 273.15)
        except (TypeError, ValueError):
            pass
    if temps:
        return temps
    for namespace in ("root/LibreHardwareMonitor",
                      "root/OpenHardwareMonitor"):
        out = _run(f"Get-CimInstance -Namespace {namespace} "
                   "-ClassName Sensor "
                   "| Where-Object { $_.SensorType -eq 'Temperature' "
                   "-and $_.Value -ne $null } "
                   "| Select-Object -ExpandProperty Value")
        for line in out.splitlines():
            try:
                temps.append(float(line.strip()))
            except (TypeError, ValueError):
                pass
        if temps:
            return temps
    # v1.6.1 (1): Thermal Zone perf counters. CookedValue is Kelvin.
    out = _run("Get-Counter "
               "'\\Thermal Zone Information(*)\\Temperature' "
               "-ErrorAction SilentlyContinue "
               "| Select-Object -ExpandProperty CounterSamples "
               "| Select-Object -ExpandProperty CookedValue")
    for line in out.splitlines():
        try:
            temps.append(float(line.strip()) - 273.15)
        except (TypeError, ValueError):
            pass
    if temps:
        return temps
    # v1.6.1 (2): Win32_TemperatureProbe - CurrentReading is usually
    # tenths of Kelvin, sometimes Celsius; the shared helper decides.
    out = _run("Get-CimInstance -Namespace root/cimv2 "
               "-ClassName Win32_TemperatureProbe "
               "| Select-Object -ExpandProperty CurrentReading")
    for line in out.splitlines():
        try:
            celsius = _probe_value_to_celsius(float(line.strip()))
        except (TypeError, ValueError):
            celsius = None
        if celsius is not None:
            temps.append(celsius)
    if temps:
        return temps
    # v1.6.1 (3): Dell DCIM thermal sensors (Dell Command Monitor).
    for cls in ("DCIM_ThermalSensor", "CIM_TemperatureSensor"):
        out = _run(f"Get-CimInstance -Namespace root/dcim/sysman "
                   f"-ClassName {cls} "
                   "| Select-Object -ExpandProperty CurrentReading")
        for line in out.splitlines():
            try:
                celsius = _probe_value_to_celsius(float(line.strip()))
            except (TypeError, ValueError):
                celsius = None
            if celsius is not None:
                temps.append(celsius)
        if temps:
            return temps
    return temps


def read_cpu_temp() -> tuple:
    """Best-effort CPU temperature.

    Returns (temp_c or None, available bool). On non-Windows, or when no
    WMI / perf-counter source answers, this is (None, False) - the
    caller MUST report that honestly and never substitute a number.
    Sources, in order: MSAcpi thermal zones, Libre/OpenHardwareMonitor
    sensors, Thermal Zone perf counters, Win32_TemperatureProbe, Dell
    DCIM thermal sensors (v1.6.1 added the last three)."""
    if sys.platform != "win32":
        return None, False
    candidates = _wmi_temps() or _powershell_temps()
    plausible = [t for t in candidates
                 if _TEMP_PLAUSIBLE_C[0] <= t <= _TEMP_PLAUSIBLE_C[1]]
    if not plausible:
        return None, False
    # Several zones / sensors: the hottest one is the one that matters.
    return max(plausible), True


# v1.6.1: when no temperature source answers, the device panel shows
# this hint (agreed contract with the UI workstream - exact shape).
# The temperature is never invented; the hint points at the one
# install that publishes it (LibreHardwareMonitor, kept running).
_TEMP_HINT = {
    "text": "الحرارة مش متاحة على الجهاز ده — ثبّت "
            "LibreHardwareMonitor وخليه شغال في الخلفية عشان تظهر",
    "url": "https://github.com/LibreHardwareMonitor/"
           "LibreHardwareMonitor/releases",
}


# ---------------------------------------------------------------------------
# v1.6.0: per-process usage, GPU, storage, battery drain, mood mapping
# ---------------------------------------------------------------------------

def _read_top_processes(limit: int = 10) -> list:
    """Top processes by memory (RSS). Each entry:
    {"pid", "name", "cpu_percent", "memory_mb", "status"}.
    cpu_percent is the non-blocking (interval=None) value - 0.0 on the
    first call for a process, which is psutil's honest behaviour, not a
    bug. A process that vanishes mid-iteration is skipped, never fatal."""
    if psutil is None:
        return []
    out = []
    try:
        for proc in psutil.process_iter(
                ["pid", "name", "cpu_percent", "memory_info", "status"]):
            try:
                info_d = proc.info
                mem = info_d.get("memory_info")
                out.append({
                    "pid": int(info_d.get("pid") or 0),
                    "name": str(info_d.get("name") or "?"),
                    "cpu_percent": float(info_d.get("cpu_percent") or 0.0),
                    "memory_mb": int((mem.rss if mem else 0) // (1024 * 1024)),
                    "status": str(info_d.get("status") or ""),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied,
                    psutil.ZombieProcess):
                continue
    except Exception as exc:
        warn("monitor", f"Top-processes reading failed: {exc}")
        return []
    out.sort(key=lambda p: p["memory_mb"], reverse=True)
    return out[:max(1, int(limit))]


def read_gpu_usage() -> dict:
    """Per-process GPU usage where a source answers, else honestly
    unavailable. Windows: Win32_PerfFormattedData GPU engine counters
    via PowerShell (no extra package). The counter exists only on
    Windows 10+ with a WDDM driver; on Linux / without it this is
    {"available": False, "percent": None, ...} - NEVER a guessed number."""
    result = {"available": False, "percent": None, "detail": ""}
    if sys.platform != "win32":
        result["detail"] = "GPU counters are Windows-only in this build"
        return result
    try:
        import subprocess
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-Counter '\\GPU Engine(*)\\Utilization Percentage' "
             "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty "
             "CounterSamples | Measure-Object -Property CookedValue -Sum "
             "| Select-Object -ExpandProperty Sum"],
            capture_output=True, text=True, timeout=10,
            **hidden_spawn.run_kwargs())
        raw = (proc.stdout or "").strip()
        value = float(raw.splitlines()[0].strip()) if raw else None
        if value is None or not (0.0 <= value <= 100.0 * 64):
            result["detail"] = ("GPU counter answered but gave no usable "
                                "value")
            return result
        # The counter sums across engines, so clamp to a 0-100 scale
        # and say so - it is an aggregate, not a per-engine breakdown.
        result.update({"available": True, "percent": round(min(value, 100.0), 1),
                       "detail": "aggregate GPU Engine utilization"})
        return result
    except Exception as exc:
        result["detail"] = f"GPU counter probe failed: {exc}"
        return result


def read_storage() -> list:
    """Per-drive storage: [{"mount", "device", "total_gb", "free_gb",
    "percent_free"}]. One failing drive never kills the list."""
    if psutil is None:
        return []
    out = []
    try:
        parts = psutil.disk_partitions(all=False)
    except Exception as exc:
        warn("monitor", f"Disk partitions reading failed: {exc}")
        return []
    for part in parts:
        try:
            du = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue
        if not du.total:
            continue
        out.append({
            "mount": str(part.mountpoint),
            "device": str(part.device),
            "total_gb": round(du.total / (1024 ** 3), 2),
            "free_gb": round(du.free / (1024 ** 3), 2),
            "percent_free": round(100.0 * du.free / du.total, 1),
        })
    return out


def battery_drain_estimate(battery: dict | None,
                           top_processes: list | None) -> dict:
    """Per-app battery drain ESTIMATE (labelled as such - it is a share
    of CPU time while discharging, not a measured mAh draw, and the
    result says so). Only meaningful while discharging; otherwise the
    top consumers are still listed with drain None."""
    discharging = bool(battery and not battery.get("charging"))
    consumers = []
    procs = list(top_processes or [])
    total_cpu = sum(p.get("cpu_percent") or 0.0 for p in procs)
    for p in sorted(procs, key=lambda x: x.get("cpu_percent") or 0.0,
                    reverse=True)[:5]:
        cpu = float(p.get("cpu_percent") or 0.0)
        consumers.append({
            "name": p.get("name", "?"),
            "pid": p.get("pid"),
            "cpu_percent": cpu,
            # Share of the top-process CPU time - an estimate, not a
            # measured battery draw.
            "cpu_share_percent": (round(100.0 * cpu / total_cpu, 1)
                                  if total_cpu > 0 else 0.0),
        })
    return {"discharging": discharging,
            "estimate": True,
            "note": ("CPU-time share while discharging - an estimate, "
                     "not a measured battery draw"),
            "top_consumers": consumers}


def _idle_seconds() -> float | None:
    """Seconds since the last keyboard/mouse input (Windows
    GetLastInputInfo). None off Windows or on any failure - never a
    guessed number. Guarded: a broken probe must never raise."""
    if sys.platform != "win32":
        return None
    try:
        class _LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint),
                        ("dwTime", ctypes.c_uint)]

        info_obj = _LASTINPUTINFO()
        info_obj.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(
                ctypes.byref(info_obj)):
            return None
        tick = ctypes.windll.kernel32.GetTickCount()
        return max(0.0, (tick - info_obj.dwTime) / 1000.0)
    except Exception:
        return None


# v1.6.0 mood thresholds (explicit, in priority order - the first match
# wins, so a hot + low-battery machine reads as worried, not tired).
_MOOD_TEMP_WORRIED_C = 90.0
_MOOD_RAM_WORRIED_MB = 300
_MOOD_BATTERY_TIRED_PCT = 15
_MOOD_CPU_DIZZY_PCT = 85
_MOOD_IDLE_SLEEP_SEC = 10 * 60
_MOOD_RECENT_INTERACTION_SEC = 5 * 60


def device_state_to_mood(status: dict, focus_mode: bool = False,
                         idle_seconds: float | None = None) -> tuple:
    """Map a device status dict to (mood, reason). `status` is the shape
    get_status() / get_device_overview() return (ram/cpu/battery/temp).
    Priority: worried (temp>=90 or free RAM<300MB) -> tired (battery<15%
    discharging) -> dizzy (sustained CPU>85%) -> focused (focus mode on)
    -> sleeping (idle>10min + otherwise healthy) -> happy (healthy +
    recent interaction) -> awake (default). Never raises: a malformed
    status reads as awake with the reason named."""
    try:
        ram = (status or {}).get("ram") or {}
        cpu = (status or {}).get("cpu") or {}
        battery = (status or {}).get("battery")
        temp_c = (status or {}).get("temp_c")
        free_mb = ram.get("free_mb")
        if (temp_c is not None and float(temp_c) >= _MOOD_TEMP_WORRIED_C) \
                or (free_mb is not None and float(free_mb) < _MOOD_RAM_WORRIED_MB):
            if temp_c is not None and float(temp_c) >= _MOOD_TEMP_WORRIED_C:
                return ("قلقان", f"حرارة المعالج {temp_c}°م — فوق 90°")
            return ("قلقان", f"الرام الفاضية {free_mb} ميجا بس — أقل من 300")
        if battery and not battery.get("charging"):
            bpct = battery.get("percent")
            if bpct is not None and float(bpct) < _MOOD_BATTERY_TIRED_PCT:
                return ("تعبان", f"البطارية {bpct}% والشاحن مش متوصل")
        sustained = cpu.get("sustained_high")
        cpu_pct = cpu.get("percent")
        if sustained or (cpu_pct is not None
                         and float(cpu_pct) > _MOOD_CPU_DIZZY_PCT
                         and sustained is True):
            return ("دايخ", "المعالج شغال فوق 85% من فترة — حمل مستمر")
        if focus_mode:
            return ("مركّز", "وضع التركيز شغال")
        if idle_seconds is not None and idle_seconds > _MOOD_IDLE_SLEEP_SEC:
            return ("نايم", "الجهاز هادي ومفيش استخدام بقاله أكتر من 10 دقايق")
        if idle_seconds is not None and idle_seconds <= _MOOD_RECENT_INTERACTION_SEC:
            return ("مبسوط وبيرقص", "كل حاجة تمام ولسه كنت بتكلمني")
        return ("صاحي", "الجهاز شغال عادي ومفيش مشاكل")
    except Exception as exc:
        warn("monitor", f"Mood mapping failed ({exc}) - defaulting to awake")
        return ("صاحي", "مقدرتش أقرأ حالة الجهاز دلوقتي")


# ---------------------------------------------------------------------------
# v1.6.1: message tone -> mood + mood stabilization.
#
# The robot's mood used to flip on every 5s poll whenever the device
# numbers flickered. Two fixes, both in the monitor (the single place
# get_mood() reads from, so the main window AND the island agree):
#   (a) MoodStabilizer: a mood holds for >= _MOOD_HOLD_SEC and a new
#       mood must be confirmed on _MOOD_CONFIRM_POLLS consecutive polls
#       before it takes over. Only a critical temperature (>=90C)
#       switches immediately.
#   (b) analyze_message_tone(): simple Arabic/Egyptian heuristics on
#       what Mohamed says - praise/thanks/laughter -> happy, frustration
#       / anger / urgency -> worried (sympathetic), greeting -> cheerful,
#       sad talk -> worried (comforting). Within the healthy range the
#       tone drives the mood; a real device problem (worried / tired /
#       dizzy) always wins over the tone.
# ---------------------------------------------------------------------------

_MOOD_HOLD_SEC = 150.0        # 2.5 min minimum hold (spec: 2-3 min)
_MOOD_CONFIRM_POLLS = 2       # new mood confirmed on 2 polls (spec: 2-3)
_MOOD_TONE_TTL_SEC = 600.0    # a message's tone steers the mood for 10 min
_MOOD_PROBLEM = ("قلقان", "تعبان", "دايخ")  # real device problems

_TONE_DISTRESS_WORDS = (
    # frustration / anger / urgency / sadness (Egyptian Arabic)
    "زهقت", "زهقان", "قرف", "زفت", "باظ", "مش شغال", "مش راضي",
    "خربان", "تعبت", "مش قادر", "مخنوق", "مضايق", "متضايق",
    "عصبي", "غضبان", "زعلان", "حزين", "مكتئب", "وحش", "فاشل",
    "بسرعة", "ضروري", "الحقني", "ساعدني", "ليه كده", "مش عارف",
    "تعبان", "موجوع", "قلقان", "خايف",
)
_TONE_HAPPY_WORDS = (
    # praise / thanks / jokes / laughter
    "شكرا", "تسلم", "برافو", "جميل", "حلو", "رائع", "ممتاز",
    "عظيم", "ههه", "هاهاها", "ضحك", "نكتة", "مبسوط", "فرحان",
    "بحبك", "احبك", "عجبني", "تمام اوي", "الله عليك",
)
_TONE_GREETING_WORDS = (
    "صباح الخير", "مساء الخير", "سلام", "اهلا", "ازيك",
    "عامل ايه", "هاي", "هلا", "صباح النور", "مساء النور",
)

# Arabic normalization for tone matching: alef forms, teh marbuta,
# alef maksura, diacritics and tatweel all collapse so "شكراً" and
# "شكرا" match the same way.
_AR_NORM = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي",
    "ـ": "", "ً": "", "ٌ": "", "ٍ": "", "َ": "", "ُ": "",
    "ِ": "", "ّ": "", "ْ": "",
})


def _normalize_ar(text: str) -> str:
    return (text or "").translate(_AR_NORM).lower()


def analyze_message_tone(text: str):
    """Heuristic tone of one user message -> (mood, reason) or (None, "").

    Distress first (frustration/anger/urgency/sadness all read as a
    sympathetic worried mood), then praise/thanks/laughter (happy),
    then greeting (cheerful). Neutral text returns (None, "") - it
    never forces a mood. Never raises; never logs the message text."""
    try:
        norm = _normalize_ar(text)
        if not norm.strip():
            return (None, "")
        for word in _TONE_DISTRESS_WORDS:
            if _normalize_ar(word) in norm:
                return ("قلقان", "كلامك باين عليه الزعل أو الاستعجال")
        for word in _TONE_HAPPY_WORDS:
            if _normalize_ar(word) in norm:
                return ("مبسوط وبيرقص", "كلامك باين عليه الفرحة أو الشكر")
        for word in _TONE_GREETING_WORDS:
            if _normalize_ar(word) in norm:
                return ("سعيد بيك", "سلّمت عليّا — مبسوط بيك")
        return (None, "")
    except Exception:
        return (None, "")


class MoodStabilizer:
    """Hold + confirm gate for the companion mood.

    A mood holds at least `hold_seconds` and a different candidate must
    appear on `confirm_polls` consecutive stabilize() calls before it
    takes over, so one flickering poll never flips the robot. A critical
    temperature (>=90C, read from the status dict) bypasses both rules
    immediately. The candidate is chosen as: real device problem mood
    (worried/tired/dizzy) first, then the user's tone mood (only within
    the healthy range), then the raw device mood."""

    def __init__(self, hold_seconds: float = _MOOD_HOLD_SEC,
                 confirm_polls: int = _MOOD_CONFIRM_POLLS,
                 clock=None):
        self._hold = float(hold_seconds)
        self._confirm = max(1, int(confirm_polls))
        self._clock = clock or time.time
        self._current = None
        self._current_reason = ""
        self._current_since = 0.0
        self._pending = None
        self._pending_count = 0

    def _is_critical(self, status) -> bool:
        try:
            temp_c = (status or {}).get("temp_c")
            return temp_c is not None and float(temp_c) >= _MOOD_TEMP_WORRIED_C
        except Exception:
            return False

    def stabilize(self, raw_mood: str, raw_reason: str = "",
                  status: dict | None = None, tone_mood=None,
                  tone_reason: str = "") -> tuple:
        """Return the (mood, reason) to display for this poll."""
        now = self._clock()
        if raw_mood in _MOOD_PROBLEM:
            candidate, cand_reason = raw_mood, raw_reason
        elif tone_mood:
            candidate, cand_reason = tone_mood, tone_reason
        else:
            candidate, cand_reason = raw_mood, raw_reason
        if self._current is None:
            self._adopt(candidate, cand_reason, now)
            return (self._current, self._current_reason)
        if self._is_critical(status):
            if candidate != self._current or self._current != "قلقان":
                self._adopt("قلقان", cand_reason or raw_reason, now)
            return (self._current, self._current_reason)
        if candidate == self._current:
            self._pending = None
            self._pending_count = 0
            return (self._current, self._current_reason)
        if self._pending != candidate:
            self._pending = candidate
            self._pending_count = 1
        else:
            self._pending_count += 1
        if (self._pending_count >= self._confirm
                and now - self._current_since >= self._hold):
            self._adopt(candidate, cand_reason, now)
        return (self._current, self._current_reason)

    def _adopt(self, mood: str, reason: str, now: float) -> None:
        self._current = mood
        self._current_reason = reason
        self._current_since = now
        self._pending = None
        self._pending_count = 0


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class SystemMonitor:
    """Background system monitor.

    settings: plain dict (merged over DEFAULT_MONITOR_SETTINGS).
    on_alert: callback(alert: dict) -> None, called for every alert that
        fires. The parent wires it to the shared in-app-alert + toast
        handler. May be None (alerts are still returned by check_once).
    sample_fn: () -> reading dict (see _sample_real for the shape).
        Defaults to the real machine; tests inject fake readings.
    clock: () -> float seconds (default time.time); tests inject a fake
        clock via check_once(..., now=...) instead.
    """

    def __init__(self, settings=None, on_alert=None, sample_fn=None,
                 clock=None):
        merged = dict(DEFAULT_MONITOR_SETTINGS)
        if settings:
            merged.update({k: v for k, v in settings.items()
                           if k in DEFAULT_MONITOR_SETTINGS})
        self.settings = merged
        self.on_alert = on_alert
        self.sample_fn = sample_fn or self._sample_real
        self._clock = clock or time.time
        self._cpu_history = []      # [(timestamp, cpu_percent)]
        self._active = set()        # alert kinds whose condition is true
        self._last_fired = {}       # kind -> timestamp of last firing
        self._temp_cache = {"at": 0.0, "temp_c": None, "available": False}
        self._uses_injected_sampler = sample_fn is not None
        # v1.6.1 item 21: background sample cache. The UI thread (pywebview
        # bridge) never waits on a subprocess or a full process scan: it
        # reads this cache, refreshed by a daemon sampler thread. Slow
        # fields (GPU/temp/storage) only ever come from that thread.
        self._cached_reading = None
        self._cached_at = 0.0
        self._cache_lock = threading.Lock()
        self._sampler_thread = None
        self._sampler_stop = threading.Event()
        self._focus_mode = False  # v1.6.0: companion focus mode -> mood مركّز
        # v1.6.1: mood stability (hold + confirm) + tone blending.
        self._mood_stabilizer = MoodStabilizer(clock=self._clock)
        self._tone_mood = None
        self._tone_reason = ""
        self._tone_at = 0.0
        self._stop_event = threading.Event()
        self._thread = None
        self.running = False

    # -- sampling -----------------------------------------------------------

    def _read_temp_cached(self):
        """Temperature, probed at most once per _TEMP_PROBE_INTERVAL_SEC."""
        now = self._clock()
        if now - self._temp_cache["at"] < _TEMP_PROBE_INTERVAL_SEC:
            return (self._temp_cache["temp_c"],
                    self._temp_cache["available"])
        # v1.4.3: the probe must never raise - a failed probe is
        # "unavailable", not a dead monitor.
        try:
            temp_c, available = read_cpu_temp()
        except Exception as exc:
            warn("monitor", f"CPU temperature probe failed: {exc}")
            temp_c, available = None, False
        self._temp_cache.update({"at": now, "temp_c": temp_c,
                                 "available": available})
        if not available:
            info("monitor", "CPU temperature unavailable "
                 "(no WMI thermal source answered)")
        return temp_c, available

    def _sample_fast(self) -> dict:
        """psutil-only reading (RAM/disk/CPU/battery/top processes).
        Never spawns a subprocess, so it is safe on any thread --
        including a one-off seed on the first get_status() call. The
        slow fields (temp/GPU/storage) are NOT in here; they arrive
        from the background sampler via _sample_slow()."""
        # v1.4.3: each source is read on its own - one failing source
        # must NEVER kill the whole sample (v1.4.2 field report: the
        # brain said it "couldn't fetch" RAM too, because a single
        # failure took the entire system_status tool down). A failed
        # source is None (never a fake 0), and the reason is logged.
        if psutil is None:
            return {"ram": None, "disk": None, "cpu_percent": None,
                    "battery": None, "top_processes": [],
                    "battery_drain": {}}
        ram = None
        try:
            vm = psutil.virtual_memory()
            ram = {"total_mb": int(vm.total // (1024 * 1024)),
                   "used_mb": int(vm.used // (1024 * 1024)),
                   "free_mb": int(vm.available // (1024 * 1024)),
                   "percent": float(vm.percent)}
        except Exception as exc:
            warn("monitor", f"RAM reading failed: {exc}")
        disk = None
        try:
            du = psutil.disk_usage(_system_drive())
            disk = {"free_gb": round(du.free / (1024 ** 3), 2),
                    "total_gb": round(du.total / (1024 ** 3), 2),
                    "percent_free": round(100.0 * du.free / du.total, 1)
                    if du.total else 0.0}
        except Exception as exc:
            warn("monitor", f"Disk reading failed: {exc}")
        cpu_pct = None
        try:
            cpu_pct = float(psutil.cpu_percent(interval=None))
        except Exception as exc:
            warn("monitor", f"CPU reading failed: {exc}")
        battery = None
        try:
            raw = psutil.sensors_battery()
        except Exception:
            raw = None
        if raw is not None:
            battery = {"percent": int(raw.percent),
                       "charging": bool(raw.power_plugged)}
        top_processes = []
        try:
            top_processes = _read_top_processes()
        except Exception as exc:
            warn("monitor", f"Top-processes reading failed: {exc}")
        return {
            "ram": ram,
            "disk": disk,
            "cpu_percent": cpu_pct,
            "battery": battery,
            "top_processes": top_processes,
            "battery_drain": battery_drain_estimate(battery, top_processes),
        }

    def _sample_slow(self) -> dict:
        """Slow fields only: temperature (already 10-min cached),
        GPU (a PowerShell subprocess, up to 10 s) and all-drive
        storage. Runs ONLY on the background sampler thread -- never
        on the UI thread, so a slow PowerShell start can never freeze
        the window."""
        temp_c, temp_available = self._read_temp_cached()
        gpu = {"available": False, "percent": None, "detail": ""}
        try:
            gpu = read_gpu_usage()
        except Exception as exc:
            warn("monitor", f"GPU reading failed: {exc}")
        storage = []
        try:
            storage = read_storage()
        except Exception as exc:
            warn("monitor", f"Storage reading failed: {exc}")
        return {
            "temp_c": temp_c,
            "temp_available": temp_available,
            "gpu": gpu,
            "storage": storage,
        }

    def _sample_real(self) -> dict:
        """One full real reading from this machine. Shape (the contract
        sample_fn must honour):

            {"ram": {"total_mb", "used_mb", "free_mb", "percent"},
             "disk": {"free_gb", "total_gb", "percent_free"},
             "cpu_percent": float,
             "battery": {"percent", "charging"} | None,
             "temp_c": float | None, "temp_available": bool}
        """
        fast = self._sample_fast()
        slow = self._sample_slow()
        fast.update(slow)
        return fast

    # -- background sample cache (v1.6.1 item 21) ---------------------------

    def _merge_into_cache(self, partial: dict) -> None:
        """Merge a partial (fast or slow) reading into the cached one."""
        with self._cache_lock:
            merged = dict(self._cached_reading or {})
            merged.update(partial)
            self._cached_reading = merged
            self._cached_at = self._clock()

    def _read_for_status(self) -> dict:
        """Reading for get_status()/check_once(). Injected samplers
        (tests) sample fresh every time, exactly as before. The real
        sampler serves the background cache: the UI thread never waits
        on a subprocess spawn or a full process scan. With no fresh
        sample yet, fast fields are seeded synchronously (psutil only,
        never a subprocess) so the first paint has real numbers; slow
        fields stay absent (honest None) until the sampler lands."""
        if self._uses_injected_sampler:
            return self.sample_fn()
        with self._cache_lock:
            cached = self._cached_reading
            age = self._clock() - self._cached_at
        if cached is not None and age < _STATUS_MAX_AGE_SEC:
            return dict(cached)
        seed = self._sample_fast()
        self._merge_into_cache(seed)
        with self._cache_lock:
            return dict(self._cached_reading or seed)

    def start_sampler(self) -> bool:
        """Start the background sampler thread (daemon). Safe to call
        twice. Returns False when psutil is missing (nothing to sample
        with). Unlike start(), this does not check the monitor_enabled
        flag: sampling for display is not alerting."""
        if self._sampler_thread is not None \
                and self._sampler_thread.is_alive():
            return True
        if psutil is None:
            return False
        self._sampler_stop.clear()
        self._sampler_thread = threading.Thread(
            target=self._sample_loop, daemon=True, name="memo-sampler")
        self._sampler_thread.start()
        return True

    def stop_sampler(self) -> None:
        """Stop the background sampler thread (called from stop())."""
        self._sampler_stop.set()
        if self._sampler_thread is not None:
            self._sampler_thread.join(timeout=2)
            self._sampler_thread = None

    def _sample_loop(self):
        # Seed immediately with one full sample so the slow fields exist
        # early, then fast every 5 s, slow every 60 s. A slow sample
        # only delays the next one -- it never blocks the UI thread.
        try:
            self._merge_into_cache(self._sample_real())
        except Exception as exc:
            warn("monitor", f"Sampler seed failed: {exc}")
        slow_at = self._clock()
        while not self._sampler_stop.is_set():
            t0 = self._clock()
            try:
                self._merge_into_cache(self._sample_fast())
            except Exception as exc:
                warn("monitor", f"Fast sample failed: {exc}")
            if self._clock() - slow_at >= _SAMPLE_SLOW_SEC:
                slow_at = self._clock()
                try:
                    self._merge_into_cache(self._sample_slow())
                except Exception as exc:
                    warn("monitor", f"Slow sample failed: {exc}")
            dt = self._clock() - t0
            if dt > 1.0:
                # Evidence for the next freeze report: a slow background
                # sample is logged, and the UI was never blocked by it.
                warn("monitor", f"Background sample took {dt:.1f}s "
                     "(UI unaffected)")
            self._sampler_stop.wait(max(_SAMPLE_FAST_SEC - dt, 0.5))

    # -- status ---------------------------------------------------------------

    def get_status(self) -> dict:
        """Current status with REAL numbers, for the brain's system_status
        tool ("اللابتوب عامل إيه؟") and the Settings screen:

            {"ram": {"total_mb", "used_mb", "free_mb", "percent"} | None,
             "disk": {"free_gb", "total_gb", "percent_free"} | None,
             "cpu": {"percent": float | None, "sustained_high": bool},
             "battery": {"percent", "charging"} | None,
             "temp_c": float | None, "temp_available": bool,
             "available": bool}   # False only when psutil is missing
        Any field may be None when its source failed to read (v1.4.3:
        partial data, never a crash, never fake zeros).
        """
        # v1.4.3: a failing sample_fn must not raise out of get_status -
        # the caller gets a degraded (all-None) reading instead, and
        # reports it honestly. v1.6.1 item 21: _read_for_status serves
        # the background cache for the real sampler, so this never waits
        # on a subprocess spawn.
        try:
            reading = self._read_for_status()
        except Exception as exc:
            warn("monitor", f"Sampling failed: {exc}")
            reading = {"ram": None, "disk": None, "cpu_percent": None,
                       "battery": None, "temp_c": None,
                       "temp_available": False}
        cpu_pct = reading.get("cpu_percent")
        self._record_cpu(cpu_pct if cpu_pct is not None else 0.0)
        # v1.6.0 additions: top processes / GPU / storage / battery drain
        # come from the reading when the sampler provides them (the real
        # sampler does); injected test readings omit them, so they are
        # read live here instead of failing or faking a value.
        top_processes = reading.get("top_processes")
        if top_processes is None:
            try:
                top_processes = _read_top_processes()
            except Exception:
                top_processes = []
        gpu = reading.get("gpu")
        if gpu is None:
            try:
                gpu = read_gpu_usage()
            except Exception:
                gpu = {"available": False, "percent": None, "detail": ""}
        storage = reading.get("storage")
        if storage is None:
            try:
                storage = read_storage()
            except Exception:
                storage = []
        battery = reading.get("battery")
        drain = reading.get("battery_drain")
        if drain is None:
            drain = battery_drain_estimate(battery, top_processes)
        status = {
            "ram": reading.get("ram"),
            "disk": reading.get("disk"),
            "cpu": {"percent": (float(cpu_pct) if cpu_pct is not None
                                else None),
                    "sustained_high": self._sustained_high()},
            "battery": battery,
            "temp_c": reading.get("temp_c"),
            "temp_available": bool(reading.get("temp_available", False)),
            "available": psutil is not None,
            "top_processes": top_processes,
            "gpu": gpu,
            "storage": storage,
            "battery_drain": drain,
        }
        mood, mood_reason = device_state_to_mood(
            status, focus_mode=bool(self._focus_mode),
            idle_seconds=_idle_seconds())
        # v1.6.1: stabilize + tone-blend. The raw device mood flickers;
        # the displayed mood holds (2.5 min) and needs 2 confirming
        # polls, and within the healthy range the user's tone steers it.
        # The stabilizer owns the hold/confirm state, so a thinking
        # override elsewhere never resets it.
        try:
            now = self._clock()
            tone_mood = tone_reason = None
            if (self._tone_mood is not None
                    and now - self._tone_at <= _MOOD_TONE_TTL_SEC):
                tone_mood, tone_reason = self._tone_mood, self._tone_reason
            mood, mood_reason = self._mood_stabilizer.stabilize(
                mood, mood_reason, status=status,
                tone_mood=tone_mood, tone_reason=tone_reason or "")
        except Exception as exc:
            warn("monitor", f"Mood stabilization failed ({exc}) - "
                            "showing the raw device mood")
        status["mood"] = mood
        status["mood_reason"] = mood_reason
        return status

    def get_device_overview(self) -> dict:
        """v1.6.0: one flat overview for the island / device panels:
        {ram, cpu, battery, temp_c, temp_available, disk, storage,
        top_processes, gpu, battery_drain, mood, mood_reason}.
        "disk" is the system-drive summary (back-compat key); "storage"
        is the per-drive list. Nothing here is ever a guessed number."""
        status = self.get_status()
        temp_available = bool(status.get("temp_available"))
        return {
            "ram": status.get("ram"),
            "cpu": status.get("cpu"),
            "battery": status.get("battery"),
            "temp_c": status.get("temp_c"),
            "temp_available": temp_available,
            # v1.6.1: present only while the temperature is unavailable;
            # None once a real reading exists.
            "temp_hint": None if temp_available else dict(_TEMP_HINT),
            "disk": status.get("disk"),
            "storage": status.get("storage") or [],
            "top_processes": status.get("top_processes") or [],
            "gpu": status.get("gpu") or {"available": False,
                                         "percent": None, "detail": ""},
            "battery_drain": status.get("battery_drain") or {},
            "mood": status.get("mood", "صاحي"),
            "mood_reason": status.get("mood_reason", ""),
        }

    def note_user_message(self, text: str) -> None:
        """v1.6.1: remember the tone of the user's latest message so the
        mood can react to HIM, not just the hardware. The tone steers
        the mood for _MOOD_TONE_TTL_SEC (10 min), only within the
        healthy range - a real device problem still wins. Never raises;
        the message text is never logged."""
        try:
            tone, reason = analyze_message_tone(text)
            if tone:
                self._tone_mood = tone
                self._tone_reason = reason
                self._tone_at = self._clock()
                info("monitor", f"Message tone noted: mood -> {tone}")
        except Exception as exc:
            warn("monitor", f"Tone note failed ({exc})")

    def set_focus_mode(self, on: bool) -> None:
        """v1.6.0: the companion's focus mode feeds the mood mapping
        (focused while it is on)."""
        self._focus_mode = bool(on)

    # -- sustained CPU ---------------------------------------------------------

    def _record_cpu(self, percent: float, now: float | None = None):
        now = self._clock() if now is None else now
        window = float(self.settings["monitor_cpu_sustained_minutes"]) * 60.0
        self._cpu_history.append((now, float(percent)))
        cutoff = now - window
        self._cpu_history = [(t, p) for (t, p) in self._cpu_history
                             if t >= cutoff]

    def _sustained_high(self) -> bool:
        """True when the average CPU over the window is above the
        threshold AND the window is mostly covered by samples (so two
        quick high polls alone never trigger it - it must be sustained).
        """
        if len(self._cpu_history) < 2:
            return False
        window = float(self.settings["monitor_cpu_sustained_minutes"]) * 60.0
        span = self._cpu_history[-1][0] - self._cpu_history[0][0]
        if span < window * 0.8:
            return False
        avg = sum(p for _, p in self._cpu_history) / len(self._cpu_history)
        return avg > float(self.settings["monitor_cpu_sustained_percent"])

    # -- alert evaluation -------------------------------------------------------

    def _should_fire(self, kind: str, now: float) -> bool:
        """Per-kind cooldown, rising-edge semantics:

        - fires the FIRST time a condition appears;
        - never re-fires while the condition stays continuously true
          (the kind stays in _active);
        - after the condition clears, it may fire again - but not sooner
          than monitor_cooldown_min after the previous firing (so a
          flickering condition cannot spam).
        """
        if kind in self._active:
            return False
        last = self._last_fired.get(kind)
        if last is not None:
            cooldown = float(self.settings["monitor_cooldown_min"]) * 60.0
            if now - last < cooldown:
                return False
        return True

    def _fire(self, kind: str, level: str, title_ar: str,
              message_ar: str, now: float) -> dict:
        alert = {"kind": kind, "level": level, "title_ar": title_ar,
                 "message_ar": message_ar, "at": _now_iso()}
        self._active.add(kind)
        self._last_fired[kind] = now
        info("monitor", f"Alert fired: {kind} ({level})")
        if self.on_alert is not None:
            try:
                self.on_alert(alert)
            except Exception as exc:
                warn("monitor", f"on_alert callback failed: {exc}")
        return alert

    def _evaluate(self, reading: dict, now: float) -> list:
        """Check one reading against every threshold. Returns the list of
        alerts that fired. Kinds whose condition is false are removed
        from _active, so they can fire again when they re-appear."""
        fired = []
        s = self.settings
        ram = reading.get("ram") or {}
        disk = reading.get("disk") or {}

        # RAM: free < 500 MB OR used > 92% (his machine idles at 84-86%,
        # so the threshold must sit well above idle or it spams him).
        free_mb = ram.get("free_mb")
        pct = ram.get("percent")
        ram_bad = ((free_mb is not None and free_mb < s["monitor_ram_free_mb"])
                   or (pct is not None and pct > s["monitor_ram_percent"]))
        if ram_bad:
            level = ("critical" if (free_mb is not None and free_mb < 200)
                     or (pct is not None and pct >= 97) else "warn")
            if self._should_fire("ram_low", now):
                fired.append(self._fire(
                    "ram_low", level, "الذاكرة (RAM) شبه ممتلئة",
                    f"الرام المستخدمة {pct}% والفاضي {free_mb} ميجا بس - "
                    "اقفل برامج تقيلة أو اعمل ريستارت للجهاز.", now))
        else:
            self._active.discard("ram_low")

        # Disk: free < 10 GB OR free < 10%.
        free_gb = disk.get("free_gb")
        pct_free = disk.get("percent_free")
        disk_bad = ((free_gb is not None
                     and free_gb < s["monitor_disk_free_gb"])
                    or (pct_free is not None
                        and pct_free < s["monitor_disk_percent_free"]))
        if disk_bad:
            level = ("critical" if (free_gb is not None and free_gb < 2)
                     or (pct_free is not None and pct_free < 5) else "warn")
            if self._should_fire("disk_low", now):
                fired.append(self._fire(
                    "disk_low", level, "مساحة القرص قربت تخلص",
                    f"الفاضي في القرص {free_gb} جيجا بس "
                    f"({pct_free}% من القرص) - امسح ملفات مش محتاجها.",
                    now))
        else:
            self._active.discard("disk_low")

        # CPU temperature: only when a real sensor answered.
        temp_c = reading.get("temp_c")
        if temp_c is not None and reading.get("temp_available"):
            if temp_c >= s["monitor_temp_critical_c"]:
                if self._should_fire("temp_high", now):
                    fired.append(self._fire(
                        "temp_high", "critical", "حرارة المعالج عالية جداً",
                        f"حرارة المعالج {temp_c:.0f}°م - سيّب الجهاز يبرد "
                        "واقفل البرامج التقيلة فوراً.", now))
            elif temp_c >= s["monitor_temp_warn_c"]:
                if self._should_fire("temp_high", now):
                    fired.append(self._fire(
                        "temp_high", "warn", "حرارة المعالج عالية",
                        f"حرارة المعالج {temp_c:.0f}°م - خفّف الشغل على "
                        "الجهاز شوية.", now))
            else:
                self._active.discard("temp_high")
        else:
            # No sensor: never invent a temperature, never alert on it.
            self._active.discard("temp_high")

        # Sustained CPU load (rolling window, see _sustained_high).
        if self._sustained_high():
            if self._should_fire("cpu_high", now):
                fired.append(self._fire(
                    "cpu_high", "warn", "المعالج شغال على آخره من فترة",
                    f"المعالج شغال فوق "
                    f"{s['monitor_cpu_sustained_percent']}% في المتوسط "
                    f"بقاله {s['monitor_cpu_sustained_minutes']} دقايق - "
                    "شوف إيه البرنامج اللي ساحب كل ده.", now))
        else:
            self._active.discard("cpu_high")

        # Battery: only while discharging.
        battery = reading.get("battery")
        if battery and not battery.get("charging"):
            bpct = battery.get("percent")
            if bpct is not None and bpct < s["monitor_battery_warn_percent"]:
                level = ("critical" if bpct < s["monitor_battery_critical_percent"]
                         else "warn")
                if self._should_fire("battery_low", now):
                    fired.append(self._fire(
                        "battery_low", level, "البطارية قربت تخلص",
                        f"البطارية {bpct}% والشاحن مش متوصل - "
                        "وصّل الشاحن.", now))
            else:
                self._active.discard("battery_low")
        else:
            self._active.discard("battery_low")

        return fired

    def check_once(self, reading: dict | None = None,
                   now: float | None = None) -> list:
        """One poll: sample (or use the given reading), evaluate, fire.
        Returns the list of alerts that fired (usually []). `now` is an
        injectable timestamp for tests; production passes nothing."""
        now = self._clock() if now is None else now
        if reading is None:
            try:
                # v1.6.1 item 21: the real sampler serves the background
                # cache here too; injected samplers (tests) still sample
                # fresh, exactly as before.
                reading = self._read_for_status()
            except Exception as exc:
                warn("monitor", f"Sampling failed: {exc}")
                return []
        self._record_cpu(reading.get("cpu_percent") or 0.0, now=now)
        return self._evaluate(reading, now)

    # -- background thread -----------------------------------------------------

    def start(self) -> bool:
        """Start the polling thread. Returns False when disabled or when
        psutil is missing (nothing to poll with)."""
        if self.running:
            return True
        if not self.settings.get("monitor_enabled", True):
            info("monitor", "Monitor disabled in settings - not started")
            return False
        if psutil is None:
            warn("monitor", "psutil is missing - monitor cannot start")
            return False
        # v1.6.1 item 21: the alert loop reads the same background
        # cache the live cards use -- one sampler, no duplicate polls.
        self.start_sampler()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="memo-monitor")
        self._thread.start()
        self.running = True
        info("monitor", "System monitor started "
             f"(every {self.settings['monitor_interval_sec']}s)")
        return True

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                self.check_once()
            except Exception as exc:
                warn("monitor", f"Monitor poll failed: {exc}")
            interval = float(self.settings.get("monitor_interval_sec", 60))
            self._stop_event.wait(max(interval, 5))

    def stop(self):
        """Stop the polling thread (called from MemoApp.quit)."""
        self._stop_event.set()
        self.stop_sampler()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self.running = False
        info("monitor", "System monitor stopped")
