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

def _wmi_temps() -> list:
    """Temperatures via the `wmi` package, if it happens to be installed.
    Tries MSAcpi_ThermalZoneTemperature (root\\wmi) and the
    LibreHardwareMonitor / OpenHardwareMonitor Sensor classes."""
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
    return temps


def _powershell_temps() -> list:
    """Temperatures via PowerShell Get-CimInstance (no extra packages).
    Same three sources as _wmi_temps, in the same order."""
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
    return temps


def read_cpu_temp() -> tuple:
    """Best-effort CPU temperature.

    Returns (temp_c or None, available bool). On non-Windows, or when no
    WMI source answers, this is (None, False) - the caller MUST report
    that honestly and never substitute a number."""
    if sys.platform != "win32":
        return None, False
    candidates = _wmi_temps() or _powershell_temps()
    plausible = [t for t in candidates
                 if _TEMP_PLAUSIBLE_C[0] <= t <= _TEMP_PLAUSIBLE_C[1]]
    if not plausible:
        return None, False
    # Several zones / sensors: the hottest one is the one that matters.
    return max(plausible), True


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
        self._focus_mode = False  # v1.6.0: companion focus mode -> mood مركّز
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

    def _sample_real(self) -> dict:
        """One real reading from this machine. Shape (the contract
        sample_fn must honour):

            {"ram": {"total_mb", "used_mb", "free_mb", "percent"},
             "disk": {"free_gb", "total_gb", "percent_free"},
             "cpu_percent": float,
             "battery": {"percent", "charging"} | None,
             "temp_c": float | None, "temp_available": bool}
        """
        # v1.4.3: each source is read on its own - one failing source
        # must NEVER kill the whole sample (v1.4.2 field report: the
        # brain said it "couldn't fetch" RAM too, because a single
        # failure took the entire system_status tool down). A failed
        # source is None (never a fake 0), and the reason is logged.
        if psutil is None:
            return {"ram": None, "disk": None, "cpu_percent": None,
                    "battery": None,
                    "temp_c": None, "temp_available": False}
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
        temp_c, temp_available = self._read_temp_cached()
        # v1.6.0: per-process + GPU + storage are best-effort additions -
        # each one failing leaves the core reading intact (same rule as
        # the v1.4.3 partial-data fix: never a crash, never fake zeros).
        top_processes = []
        try:
            top_processes = _read_top_processes()
        except Exception as exc:
            warn("monitor", f"Top-processes reading failed: {exc}")
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
            "ram": ram,
            "disk": disk,
            "cpu_percent": cpu_pct,
            "battery": battery,
            "temp_c": temp_c,
            "temp_available": temp_available,
            "top_processes": top_processes,
            "gpu": gpu,
            "storage": storage,
            "battery_drain": battery_drain_estimate(battery, top_processes),
        }

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
        # reports it honestly.
        try:
            reading = self.sample_fn()
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
        return {
            "ram": status.get("ram"),
            "cpu": status.get("cpu"),
            "battery": status.get("battery"),
            "temp_c": status.get("temp_c"),
            "temp_available": status.get("temp_available"),
            "disk": status.get("disk"),
            "storage": status.get("storage") or [],
            "top_processes": status.get("top_processes") or [],
            "gpu": status.get("gpu") or {"available": False,
                                         "percent": None, "detail": ""},
            "battery_drain": status.get("battery_drain") or {},
            "mood": status.get("mood", "صاحي"),
            "mood_reason": status.get("mood_reason", ""),
        }

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
                reading = self.sample_fn()
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
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self.running = False
        info("monitor", "System monitor stopped")
