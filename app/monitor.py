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
        return {
            "ram": ram,
            "disk": disk,
            "cpu_percent": cpu_pct,
            "battery": battery,
            "temp_c": temp_c,
            "temp_available": temp_available,
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
        return {
            "ram": reading.get("ram"),
            "disk": reading.get("disk"),
            "cpu": {"percent": (float(cpu_pct) if cpu_pct is not None
                                else None),
                    "sustained_high": self._sustained_high()},
            "battery": reading.get("battery"),
            "temp_c": reading.get("temp_c"),
            "temp_available": bool(reading.get("temp_available", False)),
            "available": psutil is not None,
        }

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
