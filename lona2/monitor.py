"""LONA v2 — device monitor (cache-first).

The live overview is read from cache (5s TTL). A full re-read of the
OS happens only when the cache is empty or stale — never on every
poll. Temperature prefers LibreHardwareMonitor sensors BY NAME (CPU
Package / Core); a thermal-zone value is labeled honestly as NOT the
CPU temperature (the v1.6.3 bug showed the room's 24C as CPU temp).
"""

import subprocess
import sys
import threading
import time

IS_WINDOWS = sys.platform == "win32"

_CACHE_TTL = 5.0
_lock = threading.Lock()
_cache = {"at": 0.0, "data": None}


def _top_processes(limit: int = 8):
    try:
        import psutil
        rows = []
        for proc in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
            try:
                info = proc.info
                mem = (info.get("memory_info") or {}).rss if isinstance(
                    info.get("memory_info"), object) else 0
                rows.append({
                    "name": info.get("name") or "?",
                    "cpu": round(float(info.get("cpu_percent") or 0), 1),
                    "mem_mb": round(mem / (1024 * 1024), 1),
                })
            except Exception:
                continue
        rows.sort(key=lambda r: r["cpu"], reverse=True)
        return rows[:limit]
    except Exception:
        return []


def _lhm_temps():
    """CPU Package/Core temps from LibreHardwareMonitor, by sensor name.
    Returns a list of (name, value). Empty when LHM is not running."""
    if not IS_WINDOWS:
        return []
    try:
        ps = (
            "Get-CimInstance -Namespace root\\LibreHardwareMonitor "
            "-ClassName Sensor -ErrorAction Stop | "
            "Where-Object { $_.SensorType -eq 'Temperature' } | "
            "Select-Object Name, Value | ConvertTo-Json -Compress"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=10)
        if out.returncode != 0 or not out.stdout.strip():
            return []
        import json
        data = json.loads(out.stdout)
        if isinstance(data, dict):
            data = [data]
        found = []
        for row in data or []:
            name = str(row.get("Name", ""))
            try:
                value = float(row.get("Value"))
            except Exception:
                continue
            if "CPU Package" in name or "Core " in name or "Core #" in name:
                found.append((name, value))
        return found
    except Exception:
        return []


def _thermal_zone_temp():
    """MSAcpi thermal-zone value — NOT the CPU temperature. Labeled so."""
    if not IS_WINDOWS:
        return None
    try:
        ps = (
            "Get-CimInstance -Namespace root\\wmi -ClassName "
            "MSAcpi_ThermalZoneTemperature -ErrorAction Stop | "
            "Select-Object -First 1 CurrentTemperature | ConvertTo-Json -Compress"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=10)
        if out.returncode != 0 or not out.stdout.strip():
            return None
        import json
        data = json.loads(out.stdout)
        raw = data.get("CurrentTemperature")
        if raw is None:
            return None
        return (float(raw) - 2732) / 10.0
    except Exception:
        return None


def read_cpu_temp() -> dict:
    """{value, source, label}. source: 'lhm' | 'thermal-zone' |
    'unavailable'. A thermal-zone value is never presented as CPU temp."""
    lhm = _lhm_temps()
    if lhm:
        package = [v for n, v in lhm if "CPU Package" in n]
        cores = [v for n, v in lhm if "Core " in n or "Core #" in n]
        if package:
            return {"value": round(package[0], 1), "source": "lhm",
                    "label": "CPU Package"}
        if cores:
            return {"value": round(max(cores), 1), "source": "lhm",
                    "label": "Core Max"}
        return {"value": round(lhm[0][1], 1), "source": "lhm",
                "label": lhm[0][0]}
    zone = _thermal_zone_temp()
    if zone is not None:
        return {"value": round(zone, 1), "source": "thermal-zone",
                "label": "thermal zone — مش حرارة المعالج"}
    return {"value": None, "source": "unavailable",
            "label": "مش متاحة على الجهاز ده"}


def get_device_overview(force: bool = False) -> dict:
    """Live overview from cache unless stale/forced."""
    now = time.monotonic()
    with _lock:
        if not force and _cache["data"] is not None \
                and now - _cache["at"] < _CACHE_TTL:
            return _cache["data"]
    overview = _read_overview()
    with _lock:
        _cache["data"] = overview
        _cache["at"] = time.monotonic()
    return overview


def _read_overview() -> dict:
    try:
        import psutil
        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.1)
        try:
            battery = psutil.sensors_battery()
            bat = {"percent": int(battery.percent) if battery else None,
                   "charging": bool(battery.power_plugged) if battery else None}
        except Exception:
            bat = {"percent": None, "charging": None}
        try:
            disk = psutil.disk_usage("C:\\" if IS_WINDOWS else "/")
            disk_info = {"free_gb": round(disk.free / (1024 ** 3), 1),
                         "free_pct": round(100 * disk.free / disk.total, 1)}
        except Exception:
            disk_info = {"free_gb": None, "free_pct": None}
        ram = {"percent": round(mem.percent, 1),
               "used_gb": round(mem.used / (1024 ** 3), 1),
               "total_gb": round(mem.total / (1024 ** 3), 1)}
    except Exception as exc:
        from .logger import error
        error("monitor", f"Could not read device overview: {exc}")
        return {"ram": {"percent": None, "used_gb": None, "total_gb": None},
                "cpu": {"percent": None},
                "battery": {"percent": None, "charging": None},
                "disk": {"free_gb": None, "free_pct": None},
                "temp": {"value": None, "source": "unavailable",
                         "label": "مش متاحة على الجهاز ده"},
                "top_processes": []}
    return {"ram": ram, "cpu": {"percent": round(float(cpu), 1)},
            "battery": bat, "disk": disk_info,
            "temp": read_cpu_temp(),
            "top_processes": _top_processes()}
