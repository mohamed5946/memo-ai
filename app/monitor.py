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
import re
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
    # rc.3 (d): token-limit-near rule -- warn when the measured
    # remaining tokens for an engine (Groq 8000/min window) fall to
    # this, critical at the lower threshold. Fires on the MEASURED
    # remaining from brain.get_token_usage(), never a guess; an engine
    # with no measured remaining (Gemini has no per-minute limit in
    # the tracker) never fires.
    "monitor_token_warn_remaining": 1000,
    "monitor_token_critical_remaining": 200,
    # rc.3 (d): slow-reply rule -- warn when one finished reply took
    # longer than this (measured wall seconds). 30 s sits well under
    # the 180 s chat deadline in brain.py, so it warns long before
    # the deadline kills the reply; a normal reply takes seconds.
    "monitor_slow_reply_sec": 30,
}

# ---------------------------------------------------------------------------
# rc.3 (d): named alert rules with severity.
#
# Every rule the monitor can fire, with a stable machine name, an
# Arabic label, a base severity ("warning" or "critical") and the
# threshold it fires on, built from the LIVE settings values -- so
# this list and the Settings health section (which renders it) can
# never drift apart. Rules that escalate carry critical_when: the
# condition that lifts them from warning to critical; "" when the
# rule never escalates. Grouped rules name their members in
# grouped_from. list_alert_rules() is the single source of truth
# for the rule list; it is pure (no I/O) so tests and the UI can
# read it without a monitor instance.
# ---------------------------------------------------------------------------

# Group kinds -> their member kinds (see _fire_group/_clear_group).
_GROUP_MEMBERS = {
    "cpu_temp": ("cpu_high", "temp_high"),
    "ram_disk": ("ram_low", "disk_low"),
}


def list_alert_rules(settings=None):
    """All alert rules as [{"name", "name_ar", "severity",
    "severity_ar", "threshold", "threshold_ar", "critical_when",
    "grouped_from"}]. `severity` is the BASE severity ("warning" or
    "critical"); `critical_when` names the escalation ("" when the
    rule never escalates). Every rule fires on a measured value --
    there is no rule here that cannot fire."""
    s = dict(DEFAULT_MONITOR_SETTINGS)
    if settings:
        s.update({k: v for k, v in settings.items()
                  if k in DEFAULT_MONITOR_SETTINGS})
    return [
        {"name": "ram-low", "name_ar": "الرام شبه ممتلئة",
         "severity": "warning", "severity_ar": "تحذير",
         "threshold": (f"free RAM < {s['monitor_ram_free_mb']} MB or "
                       f"used > {s['monitor_ram_percent']}%"),
         "threshold_ar": (f"الرام الفاضي أقل من "
                          f"{s['monitor_ram_free_mb']} ميجا أو الاستخدام "
                          f"أعلى من {s['monitor_ram_percent']}%"),
         "critical_when": "الفاضي أقل من 200 ميجا أو الاستخدام 97% أو أكتر",
         "grouped_from": []},
        {"name": "disk-low", "name_ar": "مساحة القرص قربت تخلص",
         "severity": "warning", "severity_ar": "تحذير",
         "threshold": (f"free disk < {s['monitor_disk_free_gb']} GB or "
                       f"free < {s['monitor_disk_percent_free']}%"),
         "threshold_ar": (f"المساحة الفاضية أقل من "
                          f"{s['monitor_disk_free_gb']} جيجا أو أقل من "
                          f"{s['monitor_disk_percent_free']}%"),
         "critical_when": "الفاضي أقل من 2 جيجا أو أقل من 5%",
         "grouped_from": []},
        {"name": "temp-high", "name_ar": "حرارة المعالج عالية",
         "severity": "warning", "severity_ar": "تحذير",
         "threshold": f"CPU temp >= {s['monitor_temp_warn_c']} C",
         "threshold_ar": (f"حرارة المعالج {s['monitor_temp_warn_c']}°م "
                          "أو أكتر"),
         "critical_when": (f"الحرارة {s['monitor_temp_critical_c']}°م "
                           "أو أكتر"),
         "grouped_from": []},
        {"name": "cpu-high", "name_ar": "حمل المعالج المستمر",
         "severity": "warning", "severity_ar": "تحذير",
         "threshold": (f"avg CPU > {s['monitor_cpu_sustained_percent']}% "
                       f"over {s['monitor_cpu_sustained_minutes']} min"),
         "threshold_ar": (f"المعالج فوق "
                          f"{s['monitor_cpu_sustained_percent']}% في "
                          f"المتوسط لمدة "
                          f"{s['monitor_cpu_sustained_minutes']} دقايق"),
         "critical_when": "",
         "grouped_from": []},
        {"name": "battery-low", "name_ar": "البطارية قربت تخلص",
         "severity": "warning", "severity_ar": "تحذير",
         "threshold": (f"battery < {s['monitor_battery_warn_percent']}% "
                       "while discharging"),
         "threshold_ar": (f"البطارية أقل من "
                          f"{s['monitor_battery_warn_percent']}% والشاحن "
                          "مش متوصل"),
         "critical_when": (f"البطارية أقل من "
                           f"{s['monitor_battery_critical_percent']}%"),
         "grouped_from": []},
        {"name": "cpu-temp", "name_ar": "المعالج سخن وشغال على آخره",
         "severity": "warning", "severity_ar": "تحذير",
         "threshold": "cpu-high AND temp-high at the same time",
         "threshold_ar": (f"حمل مستمر فوق "
                          f"{s['monitor_cpu_sustained_percent']}% لمدة "
                          f"{s['monitor_cpu_sustained_minutes']} دقايق "
                          f"+ حرارة {s['monitor_temp_warn_c']}°م أو أكتر "
                          "في نفس الوقت"),
         "critical_when": (f"الحرارة {s['monitor_temp_critical_c']}°م "
                           "أو أكتر"),
         "grouped_from": ["cpu-high", "temp-high"]},
        {"name": "ram-disk", "name_ar": "موارد الجهاز شبه مستنفدة",
         "severity": "warning", "severity_ar": "تحذير",
         "threshold": "ram-low AND disk-low at the same time",
         "threshold_ar": (f"الرام الفاضي أقل من "
                          f"{s['monitor_ram_free_mb']} ميجا والقرص "
                          f"الفاضي أقل من {s['monitor_disk_free_gb']} "
                          "جيجا في نفس الوقت"),
         "critical_when": "",
         "grouped_from": ["ram-low", "disk-low"]},
        {"name": "token-limit-near", "name_ar": "توكنات المحرك قربت تخلص",
         "severity": "warning", "severity_ar": "تحذير",
         "threshold": (f"measured remaining tokens <= "
                       f"{s['monitor_token_warn_remaining']}"),
         "threshold_ar": (f"المتبقي من حد الدقيقة "
                          f"{s['monitor_token_warn_remaining']} توكن أو "
                          "أقل (Groq؛ Gemini لما يبقى ليها حد مقاس)"),
         "critical_when": (f"المتبقي "
                           f"{s['monitor_token_critical_remaining']} "
                           "توكن أو أقل"),
         "grouped_from": []},
        {"name": "slow-reply", "name_ar": "الرد بطيء",
         "severity": "warning", "severity_ar": "تحذير",
         "threshold": (f"one reply took > "
                       f"{s['monitor_slow_reply_sec']} s (measured)"),
         "threshold_ar": (f"الرد أخد أكتر من "
                          f"{s['monitor_slow_reply_sec']} ثانية"),
         "critical_when": "",
         "grouped_from": []},
    ]

# Temperature is probed at most this often (a WMI/PowerShell probe costs
# a subprocess spawn; there is no point doing it every 60 s).
_TEMP_PROBE_INTERVAL_SEC = 600

# Background sampler cadence (v1.6.1 item 21): fast psutil fields refresh
# this often (the UI polls every 5 s); slow fields (the GPU PowerShell
# probe, all-drive storage) refresh much less often -- spawning
# powershell.exe every 5 s for numbers that barely move is pure waste.
_SAMPLE_FAST_SEC = 5
_SAMPLE_SLOW_SEC = 60
# v1.6.4 perf (b): the GPU counter is itself a PowerShell spawn, so
# it is probed at most this often inside the 60 s slow sample - the
# Device tab reads the cache every 5 s, so the display never waits
# on it, and GPU usage moves too slowly to justify a spawn a minute.
_GPU_PROBE_INTERVAL_SEC = 120
# A cached reading older than this is re-seeded synchronously with fast
# fields only (psutil, never a subprocess) -- so a dead sampler thread
# can never leave the UI showing stale numbers, and the UI thread never
# waits on a subprocess spawn.
_STATUS_MAX_AGE_SEC = 10

# A temperature reading outside this range is sensor garbage, not a
# real temperature - it is discarded, never reported.
_TEMP_PLAUSIBLE_C = (0.0, 125.0)

# A fan speed outside this range is sensor garbage, not a real RPM
# — it is discarded, never reported. Same range as the embedded
# helper (tools/LhmReader/Program.cs) and app/lhm_embedded.py.
_FAN_PLAUSIBLE_RPM = (0.0, 20000.0)


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Island toast payload (v1.6.4, WindyLand method: brief island expansions
# for alerts instead of only in-app banners)
# ---------------------------------------------------------------------------

def alert_to_toast(alert: dict) -> dict:
    """Map a monitor/notification alert dict to the island toast
    payload {"title", "body", "kind", "level"}. Pure - no I/O, no
    window access. The caller (MemoApp._on_system_alert) decides
    whether the island is enabled and shows it with
    island_window.evaluate_js("showIslandToast(...)"). Unknown or
    empty alerts still return a payload with empty strings - the
    caller skips toasts with no title AND no body."""
    alert = alert or {}
    title = str(alert.get("title_ar") or alert.get("title") or "")
    body = str(alert.get("message_ar") or alert.get("message") or "")
    notification_title = str(alert.get("notification_title") or "")
    if notification_title and notification_title not in body:
        body = f"{notification_title}\n{body}" if body \
            else notification_title
    return {"title": title, "body": body,
            "kind": str(alert.get("kind") or ""),
            "level": str(alert.get("level") or "warn")}


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


# v1.6.4 (F-E): Libre/OpenHardwareMonitor sensor names. A CPU sensor is
# named like "CPU Package" or "Core #1" (identifier like
# "/intelcpu/0/temperature/0"); a GPU sensor ("GPU Core") or a board
# sensor ("Temperature #1") is NOT a CPU sensor, and its value must
# never be reported as CPU temperature.
_CPU_TEMP_SENSOR_RE = re.compile(r"cpu|core|package", re.I)
_GPU_SENSOR_RE = re.compile(r"gpu", re.I)


def _is_cpu_temp_sensor(name, identifier="") -> bool:
    """True when an LHM/OHM Temperature sensor measures the CPU."""
    text = f"{name or ''} {identifier or ''}"
    if _GPU_SENSOR_RE.search(text):
        return False
    return bool(_CPU_TEMP_SENSOR_RE.search(text))


def _wmi_temps() -> list:
    """CPU temperatures via the `wmi` package, if it happens to be
    installed. Sources, in order (v1.6.4 F-E): LibreHardwareMonitor /
    OpenHardwareMonitor sensors NAMED for the CPU (CPU Package / Core)
    first, then Win32_TemperatureProbe (root\\cimv2), then the Dell
    DCIM thermal sensors (root\\dcim\\sysman). ACPI thermal zones are
    NOT here — see _wmi_thermal_zones(): a zone reading (~24 C room
    value) is not a CPU temperature and must never be presented as
    one. Every namespace is best-effort: a missing namespace or class
    is skipped silently, never raised."""
    temps = []
    try:
        import wmi as _wmi
    except Exception:
        return temps
    for namespace in (r"root\LibreHardwareMonitor",
                      r"root\OpenHardwareMonitor"):
        try:
            c = _wmi.WMI(namespace=namespace)
            for sensor in c.Sensor():
                if getattr(sensor, "SensorType", "") == "Temperature":
                    if not _is_cpu_temp_sensor(
                            getattr(sensor, "Name", ""),
                            getattr(sensor, "Identifier", "")):
                        continue
                    value = getattr(sensor, "Value", None)
                    if value is not None:
                        temps.append(float(value))
        except Exception:
            pass
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


def _wmi_thermal_zones() -> list:
    """ACPI thermal-zone readings via the `wmi` package
    (MSAcpi_ThermalZoneTemperature, root\\wmi). These are ZONE
    temperatures, not CPU temperatures — read_cpu_temp() only logs
    them (honestly labelled) and never reports them as CPU temp."""
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
    return temps


def _powershell_temps() -> list:
    """CPU temperatures via ONE PowerShell spawn (v1.6.2 B24): a single
    script tries every CPU source in order (Libre/OpenHardwareMonitor
    sensors NAMED for the CPU — CPU Package / Core — then
    Win32_TemperatureProbe, then Dell DCIM sensors) and emits the
    first source's Celsius values, one per line. The old code spawned
    up to 7 separate powershell.exe processes, one per source. Each
    stage is a cheap no-op when its source does not exist; the first
    stage that yields a value wins. ACPI thermal zones are NOT
    queried here (v1.6.4 F-E) — see _powershell_thermal_zones(): a
    zone reading (~24 C room value) is not a CPU temperature."""
    import subprocess

    script = r"""
$out = @()
try { foreach ($ns in 'root/LibreHardwareMonitor','root/OpenHardwareMonitor') { $vals = Get-CimInstance -Namespace $ns -ClassName Sensor -ErrorAction Stop | Where-Object { $_.SensorType -eq 'Temperature' -and $_.Value -ne $null -and (($_.Name -match 'CPU|Core|Package') -or ($_.Identifier -match 'cpu')) -and ($_.Name -notmatch 'GPU') } | Select-Object -ExpandProperty Value; foreach ($v in $vals) { $out += [double]$v }; if ($out.Count -gt 0) { break } } } catch {}
if ($out.Count -eq 0) {
  try { $r = Get-CimInstance -Namespace root/cimv2 -ClassName Win32_TemperatureProbe -ErrorAction Stop | Select-Object -ExpandProperty CurrentReading; foreach ($v in $r) { $c = $null; if ($v -gt 200) { $c = $v / 10 - 273.15 } elseif ($v -ge 0 -and $v -le 125) { $c = [double]$v }; if ($null -ne $c) { $out += $c } } } catch {}
}
if ($out.Count -eq 0) {
  try { foreach ($cls in 'DCIM_ThermalSensor','CIM_TemperatureSensor') { $r = Get-CimInstance -Namespace root/dcim/sysman -ClassName $cls -ErrorAction Stop | Select-Object -ExpandProperty CurrentReading; foreach ($v in $r) { $c = $null; if ($v -gt 200) { $c = $v / 10 - 273.15 } elseif ($v -ge 0 -and $v -le 125) { $c = [double]$v }; if ($null -ne $c) { $out += $c } }; if ($out.Count -gt 0) { break } } } catch {}
}
$out | ForEach-Object { "$_" }
"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-Command", script],
            capture_output=True, text=True, timeout=15,
            **hidden_spawn.run_kwargs())
        out = proc.stdout or ""
    except Exception:
        return []
    temps = []
    for line in out.splitlines():
        try:
            temps.append(float(line.strip()))
        except (TypeError, ValueError):
            pass
    return temps


def _powershell_thermal_zones() -> list:
    """ACPI thermal-zone readings via ONE PowerShell spawn (v1.6.4
    F-E): MSAcpi thermal zones, then the Thermal Zone perf counters.
    ZONE temperatures, not CPU temperatures — read_cpu_temp() only
    logs them (honestly labelled) and never reports them as CPU
    temp. Hidden spawn (no console flash), like _powershell_temps."""
    import subprocess

    script = r"""
$out = @()
try { $t = Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction Stop | Select-Object -ExpandProperty CurrentTemperature; foreach ($v in $t) { $out += ($v / 10 - 273.15) } } catch {}
if ($out.Count -eq 0) {
  try { $c = Get-Counter '\Thermal Zone Information(*)\Temperature' -ErrorAction Stop | Select-Object -ExpandProperty CounterSamples | Select-Object -ExpandProperty CookedValue; foreach ($v in $c) { $out += ([double]$v - 273.15) } } catch {}
}
$out | ForEach-Object { "$_" }
"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-Command", script],
            capture_output=True, text=True, timeout=15,
            **hidden_spawn.run_kwargs())
        out = proc.stdout or ""
    except Exception:
        return []
    temps = []
    for line in out.splitlines():
        try:
            temps.append(float(line.strip()))
        except (TypeError, ValueError):
            pass
    return temps


def _read_cpu_temp_capped(timeout: float = 15.0) -> tuple:
    """read_cpu_temp() in a worker thread (v1.6.2 B24): a stuck WMI /
    PowerShell probe can never block the caller past `timeout` --
    on timeout this is (None, False), never a hang."""
    result: dict = {}

    def _run():
        try:
            result["value"] = read_cpu_temp()
        except Exception:
            result["value"] = (None, False)

    thread = threading.Thread(target=_run, daemon=True,
                              name="memo-temp-probe")
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        warn("monitor", "CPU temperature probe timed out "
                        f"({timeout:.0f}s) - reporting unavailable")
        return None, False
    return result.get("value", (None, False))


def _read_temp_status_capped(timeout: float = 15.0) -> dict | None:
    """read_temp_status() in a worker thread (rc.2, same cap as
    _read_cpu_temp_capped): a stuck WMI / PowerShell / embedded-helper
    probe can never block the caller past `timeout` -- on timeout this
    is None (the caller keeps the last cached status), never a hang."""
    result: dict = {}

    def _run():
        try:
            result["value"] = read_temp_status()
        except Exception:
            result["value"] = None

    thread = threading.Thread(target=_run, daemon=True,
                              name="memo-temp-probe")
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        warn("monitor", "CPU temperature probe timed out "
                        f"({timeout:.0f}s) - keeping the last status")
        return None
    return result.get("value")


def read_cpu_temp() -> tuple:
    """Best-effort CPU temperature.

    Returns (temp_c or None, available bool). On non-Windows, or when
    no CPU sensor answers, this is (None, False) - the caller MUST
    report that honestly (the Device panel shows _TEMP_HINT: "not
    available on this device" + the LibreHardwareMonitor install
    link) and never substitute a number. CPU sources, in order
    (v1.6.4 F-E): Libre/OpenHardwareMonitor sensors NAMED for the CPU
    (CPU Package / Core), Win32_TemperatureProbe, Dell DCIM thermal
    sensors. ACPI thermal zones are probed LAST and only for the
    diagnostics log, labelled as what they are — a zone/room reading
    (~24 C), NOT a CPU temperature — so they can never be presented
    as one again.

    rc.2: this is the (temp, available) view of read_temp_status() --
    the tuple shape is kept for tempwatch.py and the existing tests,
    and the reason WHY the temperature is missing lives in the
    status dict (see read_temp_status)."""
    status = read_temp_status()
    return status["celsius"], status["available"]


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

_LHM_RELEASES_URL = ("https://github.com/LibreHardwareMonitor/"
                     "LibreHardwareMonitor/releases")


# ---------------------------------------------------------------------------
# rc.2: temperature status WITH the real reason (W3).
#
# Field evidence: the device card and the chat both said "مش متاحة /
# مفيش حساس" when the truth was that LibreHardwareMonitor was
# INSTALLED but not RUNNING -- its WMI namespaces
# (root\LibreHardwareMonitor) exist only while LHM runs, so "no WMI
# namespace answers" could mean either "not installed" or "installed
# but not running", and the old "ثبّت LibreHardwareMonitor" button
# told Mohamed to install something he already had. The status dict
# below names the real cause, and the device card / chat consume it
# instead of guessing.
# ---------------------------------------------------------------------------

# The exact reasons read_temp_status() can report. Other workers
# consume this dict -- do not rename the keys or the reason strings.
_TEMP_REASONS = ("ok", "lhm_not_running", "not_installed", "no_sensor",
                 "needs_admin")

# rc.2: the field case -- LHM is on disk but its process is not up,
# so no WMI namespace answers. Exact wording from the rc.2 brief.
_TEMP_HINT_LHM_NOT_RUNNING = (
    "LibreHardwareMonitor متثبت بس مش شغال — افتحه وسيبه شغال "
    "في الخلفية عشان الحرارة تظهر")
_TEMP_HINT_NO_SENSOR = ("مفيش حساس حرارة للمعالج على الجهاز ده — "
                        "الحرارة مش متاحة")
_TEMP_HINT_NEEDS_ADMIN = ("LibreHardwareMonitor محتاج صلاحيات مدير "
                          "عشان يقرأ حساس الحرارة — شغّله كمسؤول "
                          "(Run as administrator)")
_TEMP_HINT_WINDOWS_ONLY = ("قراءة الحرارة عن طريق LibreHardwareMonitor "
                           "متاحة على ويندوز بس")


def _temp_status(available, celsius, reason, hint) -> dict:
    """The exact temp-status shape the device card and the chat
    consume: {"available", "celsius", "reason", "hint"}. `reason` is
    one of _TEMP_REASONS; `celsius` is a real CPU reading or None --
    never a guessed number; `hint` is the Arabic sentence shown to
    Mohamed for that reason ("" when the temperature is fine)."""
    return {"available": bool(available),
            "celsius": (float(celsius) if celsius is not None else None),
            "reason": reason,
            "hint": str(hint or "")}


def _sanitize_fans(value) -> list:
    """Fan list for the device card: [{"name": str, "rpm": float}]
    with real numbers only. A malformed entry is dropped — a fan
    speed is never guessed, and a dropped entry simply does not
    show. [] when the chip reports no fan sensors (or the value was
    never a fan list at all, e.g. an old injected sampler)."""
    out = []
    if not isinstance(value, (list, tuple)):
        return out
    for fan in value:
        if not isinstance(fan, dict):
            continue
        rpm = fan.get("rpm")
        if isinstance(rpm, bool) or not isinstance(rpm, (int, float)):
            continue
        rpm = float(rpm)
        if rpm != rpm or not (_FAN_PLAUSIBLE_RPM[0] <= rpm
                              <= _FAN_PLAUSIBLE_RPM[1]):
            continue
        name = fan.get("name")
        out.append({"name": str(name) if name else "المروحة",
                    "rpm": rpm})
    return out


def _temp_hint_for_status(temp_status) -> dict:
    """{text, url} hint for an unavailable temperature, from the
    reasoned status dict. Install-related reasons link the LHM
    releases page; no_sensor / needs_admin carry no link (installing
    again would not help / is not the fix). With no reasoned status
    (injected samplers, old callers) this is the exact agreed
    _TEMP_HINT, so the v1.6.1 contract still holds."""
    if isinstance(temp_status, dict):
        reason = temp_status.get("reason")
        hint = temp_status.get("hint")
        if reason in _TEMP_REASONS and isinstance(hint, str) and hint:
            if reason in ("lhm_not_running", "not_installed"):
                return {"text": hint, "url": _LHM_RELEASES_URL}
            return {"text": hint, "url": None}
    return dict(_TEMP_HINT)


def _lhm_exe_candidates() -> list:
    """Where a LibreHardwareMonitor install lives: the standard
    install folders plus anything on PATH."""
    candidates = []
    for env_key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432",
                    "LOCALAPPDATA"):
        base = os.environ.get(env_key)
        if base:
            candidates.append(os.path.join(
                base, "LibreHardwareMonitor",
                "LibreHardwareMonitor.exe"))
    try:
        import shutil
        found = shutil.which("LibreHardwareMonitor")
        if found:
            candidates.append(found)
    except Exception:
        pass
    return candidates


def _lhm_installed() -> bool:
    """True when LibreHardwareMonitor is installed -- its exe sits in
    a known install folder, or it is registered in the Uninstall
    registry key -- even when it is not running. Best effort, never
    raises: off Windows (or without winreg) the registry half is
    skipped, and a missing key is simply "not installed"."""
    for path in _lhm_exe_candidates():
        try:
            if path and os.path.isfile(path):
                return True
        except Exception:
            pass
    try:
        import winreg
        uninstall = (r"SOFTWARE\Microsoft\Windows\CurrentVersion"
                     r"\Uninstall")
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for view in (0, winreg.KEY_WOW64_64KEY,
                         winreg.KEY_WOW64_32KEY):
                try:
                    key = winreg.OpenKey(root, uninstall, 0,
                                         winreg.KEY_READ | view)
                except Exception:
                    continue
                try:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(key, i)
                        except OSError:
                            break
                        i += 1
                        try:
                            with winreg.OpenKey(key, sub) as subkey:
                                name, _ = winreg.QueryValueEx(
                                    subkey, "DisplayName")
                        except Exception:
                            continue
                        if isinstance(name, str) and \
                                "librehardwaremonitor" in name.lower():
                            return True
                finally:
                    winreg.CloseKey(key)
    except Exception:
        pass
    return False


def _lhm_running() -> bool:
    """True when LibreHardwareMonitor.exe is a live process right now
    (psutil scan). False when psutil is missing or the scan fails --
    never raises."""
    if psutil is None:
        return False
    try:
        for proc in psutil.process_iter(["name"]):
            try:
                name = (proc.info or {}).get("name") or ""
            except Exception:
                continue
            if str(name).lower() == "librehardwaremonitor.exe":
                return True
    except Exception:
        pass
    return False


def _read_embedded_temp():
    """Embedded-LHM temperature (rc.2), read defensively: the module
    lives in app/lhm_embedded.py, owned by another worker, so it may
    not exist yet, may have no read_embedded_temp, or the call may
    fail -- every failure is None here, never a raise, so the WMI
    path below still gets its chance. On success this is the helper's
    dict: {"celsius": float} or {"error": ..., "detail": ...}."""
    for mod_name in ("lhm_embedded", "app.lhm_embedded"):
        try:
            mod = __import__(mod_name, fromlist=["read_embedded_temp"])
            fn = getattr(mod, "read_embedded_temp", None)
            if not callable(fn):
                continue
            try:
                res = fn()
            except Exception:
                return None
            return res if isinstance(res, dict) else None
        except Exception:
            continue
    return None


def _read_embedded_fans():
    """Embedded-LHM fan list (rc.2, W7), read defensively like
    _read_embedded_temp: the module lives in app/lhm_embedded.py,
    owned by another worker, so it may not exist yet, may have no
    read_embedded_fans, or the call may fail -- every failure is []
    here, never a raise, so the temperature probe below still gets
    its chance and the device card simply shows no fans. Shares ONE
    helper invocation with the temperature probe: lhm_embedded
    caches a single helper run, so calling this right after
    read_temp_status() spawns nothing new. On success this is the
    sanitized list: [{"name": str, "rpm": float}]."""
    for mod_name in ("lhm_embedded", "app.lhm_embedded"):
        try:
            mod = __import__(mod_name, fromlist=["read_embedded_fans"])
            fn = getattr(mod, "read_embedded_fans", None)
            if not callable(fn):
                continue
            try:
                res = fn()
            except Exception:
                return []
            return _sanitize_fans(res)
        except Exception:
            continue
    return []


def read_temp_status() -> dict:
    """Best-effort CPU temperature WITH the real reason it is missing.

    Returns {"available": bool, "celsius": float|None,
             "reason": "ok"|"lhm_not_running"|"not_installed"
                       |"no_sensor"|"needs_admin",
             "hint": str} -- the exact shape the device card and the
    chat consume (other workers: do not change the keys or the reason
    strings). Sources, in order (rc.2): (1) embedded
    LibreHardwareMonitor (app.lhm_embedded, defensive -- absent or
    failing is never fatal); (2) WMI Libre/OpenHardwareMonitor CPU
    sensors, Win32_TemperatureProbe, Dell DCIM sensors (the v1.6.4
    F-E order); (3) ACPI thermal zones LAST and only for the
    diagnostics log -- a zone reading is a room value, never a CPU
    temperature, so it can never be reported as one. When no CPU
    sensor answers, the reason names the real cause: LHM installed
    but not running, LHM not installed at all, no sensor on the
    machine, or the embedded reader needs admin rights. Never
    returns a wrong temperature, never raises."""
    if sys.platform != "win32":
        return _temp_status(False, None, "not_installed",
                            _TEMP_HINT_WINDOWS_ONLY)
    # (1) Embedded LHM -- the rc.2 path (no separate LHM app needed).
    needs_admin = False
    try:
        embedded = _read_embedded_temp()
    except Exception:
        # Defensive to the last: a failing embedded reader is
        # "unavailable", never a crash, and WMI still gets its chance.
        embedded = None
    if isinstance(embedded, dict):
        celsius = embedded.get("celsius")
        if isinstance(celsius, (int, float)) and \
                _TEMP_PLAUSIBLE_C[0] <= float(celsius) \
                <= _TEMP_PLAUSIBLE_C[1]:
            return _temp_status(True, float(celsius), "ok", "")
        if embedded.get("error") == "needs_admin":
            # Not fatal yet: a running standalone LHM may still
            # answer over WMI below. Only if nothing else answers
            # is this the reason reported.
            needs_admin = True
    # (2) WMI / PowerShell CPU sensors (v1.6.4 F-E order).
    candidates = _wmi_temps() or _powershell_temps()
    plausible = [t for t in candidates
                 if _TEMP_PLAUSIBLE_C[0] <= t <= _TEMP_PLAUSIBLE_C[1]]
    if plausible:
        # Several sensors: the hottest one is the one that matters.
        return _temp_status(True, max(plausible), "ok", "")
    # (3) Thermal zones LAST -- logged honestly, never reported.
    # v1.6.4 (F-E): no CPU sensor -- say so honestly. A thermal zone
    # may still answer, but its value is a zone/room reading, not CPU
    # temperature: log it labelled as such, never return it. The
    # caller shows the reasoned hint instead.
    zone = _wmi_thermal_zones() or _powershell_thermal_zones()
    zone_plausible = [t for t in zone
                      if _TEMP_PLAUSIBLE_C[0] <= t <= _TEMP_PLAUSIBLE_C[1]]
    if zone_plausible:
        info("monitor", "No CPU temperature sensor answered; "
                        f"thermal zone reads {max(zone_plausible):.1f}C "
                        "- a zone/room reading, NOT CPU temperature, "
                        "so it is not reported as one")
    if needs_admin:
        return _temp_status(False, None, "needs_admin",
                            _TEMP_HINT_NEEDS_ADMIN)
    if _lhm_running():
        # LHM is up but published no CPU sensor: the machine (or its
        # sensor set) has nothing to read, not a missing install.
        return _temp_status(False, None, "no_sensor",
                            _TEMP_HINT_NO_SENSOR)
    if _lhm_installed():
        return _temp_status(False, None, "lhm_not_running",
                            _TEMP_HINT_LHM_NOT_RUNNING)
    return _temp_status(False, None, "not_installed", _TEMP_HINT["text"])


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
    token_fn: () -> brain.get_token_usage() dict, or None. When set,
        check_once() also evaluates the token-limit-near rule on the
        MEASURED remaining (rc.3 d). The parent passes
        brain.get_token_usage here -- the monitor never imports brain
        itself, so this module stays decoupled from it.
    """

    def __init__(self, settings=None, on_alert=None, sample_fn=None,
                 clock=None, token_fn=None):
        merged = dict(DEFAULT_MONITOR_SETTINGS)
        if settings:
            merged.update({k: v for k, v in settings.items()
                           if k in DEFAULT_MONITOR_SETTINGS})
        self.settings = merged
        self.on_alert = on_alert
        self.sample_fn = sample_fn or self._sample_real
        self.token_fn = token_fn
        self._clock = clock or time.time
        self._cpu_history = []      # [(timestamp, cpu_percent)]
        self._active = set()        # alert kinds whose condition is true
        self._last_fired = {}       # kind -> timestamp of last firing
        self._temp_cache = {"at": 0.0, "temp_c": None, "available": False,
                            # rc.2: the full reasoned temp status (see
                            # read_temp_status) -- cached with the probe,
                            # never re-detected per poll. Pre-probe this
                            # is the old install hint, exactly what the
                            # card showed before rc.2, until the
                            # sampler's first slow sample lands seconds
                            # after start.
                            "status": _temp_status(False, None,
                                                   "not_installed",
                                                   _TEMP_HINT["text"]),
                            # rc.2 (W7): fan speeds from the same probe
                            # -- cached with the temp status, same
                            # probe interval, no extra OS reads per
                            # poll. [] = the chip reported no fan
                            # sensors (or no probe has landed yet).
                            "fans": []}
        # v1.6.4 perf (b): last GPU probe (a PowerShell spawn) - see
        # _sample_slow. None = never probed (first slow sample probes).
        self._last_gpu_at = None
        self._last_gpu = {"available": False, "percent": None,
                          "detail": ""}
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

    def _temp_status_cached(self) -> dict:
        """The last probed temp status (a fresh copy), or the pre-probe
        default when the sampler has not landed a slow sample yet."""
        cached = self._temp_cache.get("status")
        if isinstance(cached, dict) \
                and cached.get("reason") in _TEMP_REASONS:
            return dict(cached)
        return _temp_status(False, None, "not_installed",
                            _TEMP_HINT["text"])

    def _read_temp_cached(self):
        """Temperature, probed at most once per _TEMP_PROBE_INTERVAL_SEC.
        Caches the full reasoned status dict (see read_temp_status);
        returns the (temp_c, available) pair _sample_slow expects."""
        now = self._clock()
        if now - self._temp_cache["at"] < _TEMP_PROBE_INTERVAL_SEC:
            return (self._temp_cache["temp_c"],
                    self._temp_cache["available"])
        # v1.4.3: the probe must never raise - a failed probe is
        # "unavailable", not a dead monitor. v1.6.2 (B24): capped at
        # 15 s in a worker thread, so a stuck probe never blocks the
        # sampler either. A timed-out probe keeps the last status
        # (and still bumps "at", so it is not retried every minute).
        try:
            status = _read_temp_status_capped()
        except Exception as exc:
            warn("monitor", f"CPU temperature probe failed: {exc}")
            status = None
        if isinstance(status, dict):
            self._temp_cache.update(
                {"at": now, "temp_c": status.get("celsius"),
                 "available": bool(status.get("available")),
                 "status": dict(status)})
            # rc.2 (W7): fans ride the same probe — the embedded
            # reader caches ONE helper invocation, so this call
            # right after the temperature probe spawns nothing new,
            # and the list is cached here with the temp status
            # (same probe interval, no extra OS reads per poll).
            # A failed fans read is [] (unsupported chip), never a
            # fake RPM, and never fatal to the temperature.
            try:
                self._temp_cache["fans"] = _read_embedded_fans()
            except Exception:
                self._temp_cache["fans"] = []
        else:
            self._temp_cache["at"] = now
        temp_c = self._temp_cache.get("temp_c")
        available = self._temp_cache.get("available", False)
        if not available:
            reason = (self._temp_cache.get("status") or {}).get("reason")
            info("monitor", "CPU temperature unavailable "
                            f"(reason: {reason or 'unknown'})")
        return temp_c, available

    def _sample_fast(self) -> dict:
        """psutil-only reading (RAM/disk/CPU/battery).
        Never spawns a subprocess, so it is safe on any thread --
        including a one-off seed on the first get_status() call. The
        slow fields (temp/GPU/storage/top_processes) are NOT in here;
        they arrive from the background sampler via _sample_slow().
        v1.6.2 (A5): top_processes is NOT read here either -- the
        process scan is the slow part, so it runs on the sampler
        thread only (see _sample_slow). The key is ABSENT here on
        purpose: a fast partial carrying "top_processes": [] would
        wipe the slow-delivered list out of the merged cache on
        every 5 s sample (dict.update clobbers it), so the process
        list would flicker to empty forever. get_status() reports
        [] until the sampler delivers (reading.get() -> None -> [])."""
        # v1.4.3: each source is read on its own - one failing source
        # must NEVER kill the whole sample (v1.4.2 field report: the
        # brain said it "couldn't fetch" RAM too, because a single
        # failure took the entire system_status tool down). A failed
        # source is None (never a fake 0), and the reason is logged.
        if psutil is None:
            return {"ram": None, "disk": None, "cpu_percent": None,
                    "battery": None, "battery_drain": {}}
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
        # v1.6.2 (A5): the process scan left the fast seed -- it is
        # the slow part, so it runs on the sampler thread only (see
        # _sample_slow). The key stays OUT of this dict on purpose:
        # merging a fast partial that carries "top_processes": []
        # would wipe the slow-delivered list from the cache on every
        # 5 s sample. Only the drain estimate needs a (currently
        # empty) process list here.
        return {
            "ram": ram,
            "disk": disk,
            "cpu_percent": cpu_pct,
            "battery": battery,
            "battery_drain": battery_drain_estimate(battery, top_processes),
        }

    def _sample_slow(self) -> dict:
        """Slow fields only: temperature (already 10-min cached),
        GPU (a PowerShell subprocess, up to 10 s), all-drive storage
        and the top-process scan. Runs ONLY on the background sampler
        thread -- never on the UI thread, so a slow PowerShell start
        or a full process scan can never freeze the window."""
        temp_c, temp_available = self._read_temp_cached()
        # v1.6.4 perf (b): the GPU counter costs a PowerShell spawn,
        # so it is probed at most every _GPU_PROBE_INTERVAL_SEC
        # (120 s) - the UI reads this cache, never the probe, and GPU
        # usage moves too slowly to justify a spawn every slow sample.
        gpu = dict(self._last_gpu)
        now = self._clock()
        if self._last_gpu_at is None \
                or now - self._last_gpu_at >= _GPU_PROBE_INTERVAL_SEC:
            try:
                gpu = read_gpu_usage()
            except Exception as exc:
                warn("monitor", f"GPU reading failed: {exc}")
                gpu = {"available": False, "percent": None, "detail": ""}
            self._last_gpu_at = now
            self._last_gpu = dict(gpu)
        storage = []
        try:
            storage = read_storage()
        except Exception as exc:
            warn("monitor", f"Storage reading failed: {exc}")
        top_processes = []
        try:
            top_processes = _read_top_processes()
        except Exception as exc:
            warn("monitor", f"Top-processes reading failed: {exc}")
        return {
            "temp_c": temp_c,
            "temp_available": temp_available,
            # rc.2: the reasoned status travels with the slow sample
            # into the background cache, so get_status() /
            # get_device_overview() serve it without ever probing.
            "temp_status": self._temp_status_cached(),
            # rc.2 (W7): fan speeds, cached with the temperature
            # probe above (same slow sample, no extra OS reads).
            # [] = the chip reported no fan sensors.
            "fans": list(self._temp_cache.get("fans") or []),
            "gpu": gpu,
            "storage": storage,
            "top_processes": top_processes,
        }

    def _sample_real(self) -> dict:
        """One full real reading from this machine. Shape (the contract
        sample_fn must honour):

            {"ram": {"total_mb", "used_mb", "free_mb", "percent"},
             "disk": {"free_gb", "total_gb", "percent_free"},
             "cpu_percent": float,
             "battery": {"percent", "charging"} | None,
             "temp_c": float | None, "temp_available": bool,
             "fans": [{"name", "rpm"}] (possibly empty)}
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
        # v1.6.2 (A5): seed with the FAST sample only, then one
        # one-shot slow sample on this same background thread -- the
        # slow fields exist early without ever blocking the UI thread.
        # After that: fast every 5 s, slow every 60 s. A slow sample
        # only delays the next one -- it never blocks the UI thread.
        try:
            self._merge_into_cache(self._sample_fast())
            self._merge_into_cache(self._sample_slow())
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
             "temp_status": {"available", "celsius", "reason", "hint"},
                 # rc.2: WHY the temperature is missing (see
                 # read_temp_status) -- the chat reads this instead of
                 # guessing "no sensor".
             "fans": [{"name", "rpm"}] -- real fan speeds from the
                 embedded LHM reader (rc.2, W7); [] when the chip
                 reports no fan sensors, never a faked RPM.
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
        # v1.6.2 (A5): NO live fallbacks here. When the sampler has
        # not delivered a field yet, the honest answer is "not
        # available yet" (gpu unavailable / empty lists) or the last
        # stored value from the cache -- get_status() NEVER samples
        # synchronously, so it can never spawn a subprocess or scan
        # processes on the bridge thread.
        top_processes = reading.get("top_processes")
        if top_processes is None:
            top_processes = []
        gpu = reading.get("gpu")
        if gpu is None:
            gpu = {"available": False, "percent": None, "detail": ""}
        storage = reading.get("storage")
        if storage is None:
            storage = []
        battery = reading.get("battery")
        drain = reading.get("battery_drain")
        if drain is None:
            drain = battery_drain_estimate(battery, top_processes)
        # rc.2: the reasoned temp status. The real sampler carries it
        # in the reading (via _sample_slow); an injected sampler (or
        # a reading from before the first slow sample) falls back to
        # the monitor's cached status -- or "ok" when the reading
        # itself carries a real temperature but no status dict.
        temp_status = reading.get("temp_status")
        if not (isinstance(temp_status, dict)
                and temp_status.get("reason") in _TEMP_REASONS):
            if reading.get("temp_available") \
                    and reading.get("temp_c") is not None:
                temp_status = _temp_status(
                    True, reading.get("temp_c"), "ok", "")
            else:
                temp_status = self._temp_status_cached()
        status = {
            "ram": reading.get("ram"),
            "disk": reading.get("disk"),
            "cpu": {"percent": (float(cpu_pct) if cpu_pct is not None
                                else None),
                    "sustained_high": self._sustained_high()},
            "battery": battery,
            "temp_c": reading.get("temp_c"),
            "temp_available": bool(reading.get("temp_available", False)),
            "temp_status": dict(temp_status),
            # rc.2 (W7): fan speeds. The real sampler carries them
            # in the reading (via _sample_slow); an injected sampler
            # (or an old reading) without them reports [].
            "fans": _sanitize_fans(reading.get("fans")),
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
        {ram, cpu, battery, temp_c, temp_available, temp_status,
        temp_hint, fans, disk, storage, top_processes, gpu,
        battery_drain, mood, mood_reason}.
        "disk" is the system-drive summary (back-compat key); "storage"
        is the per-drive list. Nothing here is ever a guessed number."""
        status = self.get_status()
        temp_available = bool(status.get("temp_available"))
        temp_status = status.get("temp_status")
        return {
            "ram": status.get("ram"),
            "cpu": status.get("cpu"),
            "battery": status.get("battery"),
            "temp_c": status.get("temp_c"),
            "temp_available": temp_available,
            # rc.2: the reasoned status (exact shape from
            # read_temp_status) rides along for the card + the chat.
            "temp_status": dict(temp_status)
                if isinstance(temp_status, dict) else None,
            # rc.2 (W7): real fan speeds, [] when the chip reports
            # no fan sensors — the card then shows nothing for fans.
            "fans": status.get("fans") or [],
            # v1.6.1: present only while the temperature is unavailable;
            # None once a real reading exists. rc.2: the text names the
            # REAL reason (installed-but-not-running vs not installed
            # vs no sensor vs needs admin) instead of always the
            # install hint; with no reasoned status (injected samplers)
            # it is the exact agreed _TEMP_HINT.
            "temp_hint": None if temp_available
                else _temp_hint_for_status(temp_status),
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

    # -- rc.3 (b): grouping + resolved notices ---------------------------
    # Alertmanager-style, LONA-sized: related conditions that fire
    # together send ONE alert naming the likely root cause (labelled
    # as likely -- never a fabricated certainty), and when a condition
    # clears, ONE short "resolved" notice goes out through the same
    # on_alert channel (in-app + toast via alert_to_toast). Grouping
    # never resets or bypasses the 30-min cooldown: a grouped firing
    # stamps _last_fired for the group AND for every member, so a
    # member cannot re-fire individually inside the cooldown; a
    # resolved notice fires at most once per active -> clear
    # transition, and never touches _last_fired (it is not a firing).

    def _fire_group(self, kind: str, members, level: str,
                    title_ar: str, message_ar: str, now: float) -> dict:
        """One alert for several related conditions at once. The
        members are marked active with the same timestamp, so they
        cannot also fire individually while the group holds."""
        alert = {"kind": kind, "level": level, "title_ar": title_ar,
                 "message_ar": message_ar, "at": _now_iso(),
                 "grouped_from": list(members)}
        self._active.add(kind)
        self._last_fired[kind] = now
        for member in members:
            self._active.add(member)
            self._last_fired[member] = now
        info("monitor", f"Grouped alert fired: {kind} ({level}) "
                        f"from {','.join(members)}")
        if self.on_alert is not None:
            try:
                self.on_alert(alert)
            except Exception as exc:
                warn("monitor", f"on_alert callback failed: {exc}")
        return alert

    def _fire_resolved(self, kind: str, title_ar: str,
                       message_ar: str) -> dict | None:
        """One short "resolved" notice for a kind that had fired and
        whose condition just cleared. Delivered through on_alert (so
        it reaches the in-app list AND the toast via alert_to_toast),
        but NOT added to _active/_last_fired and NOT returned by
        check_once -- the check_once return list keeps its existing
        meaning (new problem alerts only). Returns the notice, or
        None when there is no callback to deliver it to."""
        alert = {"kind": kind, "level": "info", "resolved": True,
                 "title_ar": title_ar, "message_ar": message_ar,
                 "at": _now_iso()}
        info("monitor", f"Alert resolved: {kind}")
        if self.on_alert is not None:
            try:
                self.on_alert(alert)
            except Exception as exc:
                warn("monitor", f"on_alert callback failed: {exc}")
            return alert
        return None

    def _clear_kind(self, kind: str, title_ar: str,
                    message_ar: str) -> None:
        """A single kind's condition just cleared: if it had fired
        (it is in _active), drop it and send its resolved notice
        exactly once. A kind that never fired sends nothing."""
        if kind in self._active:
            self._active.discard(kind)
            self._fire_resolved(kind, title_ar, message_ar)

    def _clear_group(self, kind: str, title_ar: str,
                     message_ar: str) -> None:
        """A group's condition just cleared: if the group had fired,
        send ONE resolved notice for the group and drop the members
        silently -- they were only ever announced as part of the
        group, so they get no individual resolved notice."""
        if kind in self._active:
            self._active.discard(kind)
            for member in _GROUP_MEMBERS.get(kind, ()):
                self._active.discard(member)
            self._fire_resolved(kind, title_ar, message_ar)

    # -- rc.3 (d): event-driven rules (token limit, slow reply) ----------

    def list_rules(self) -> list:
        """This monitor's alert rules (see list_alert_rules), with
        the live settings values baked into the thresholds."""
        return list_alert_rules(self.settings)

    def check_token_usage(self, usage, now: float | None = None) -> list:
        """token-limit-near rule (rc.3 d): `usage` is the
        brain.get_token_usage() shape -- {engine: {"remaining",
        "has_data", ...}}. Fires token_low_<engine> (warn, critical
        at the lower threshold) when the MEASURED remaining falls to
        the threshold, and a resolved notice when it recovers. An
        engine with no measured remaining (has_data False, or
        remaining None -- Gemini has no per-minute limit in the
        tracker, so this is its normal state) never fires and never
        resolves: no data means no alert, never a guessed one.
        Returns the problem alerts that fired (resolved notices go
        through on_alert only, like check_once)."""
        now = self._clock() if now is None else now
        fired = []
        if not isinstance(usage, dict):
            return fired
        for engine in ("groq", "gemini"):
            state = usage.get(engine)
            kind = f"token_low_{engine}"
            if not isinstance(state, dict) or not state.get("has_data"):
                self._active.discard(kind)
                continue
            remaining = state.get("remaining")
            if remaining is None:
                self._active.discard(kind)
                continue
            try:
                remaining = float(remaining)
            except (TypeError, ValueError):
                continue
            warn_at = float(self.settings["monitor_token_warn_remaining"])
            crit_at = float(
                self.settings["monitor_token_critical_remaining"])
            if remaining <= warn_at:
                level = "critical" if remaining <= crit_at else "warn"
                if self._should_fire(kind, now):
                    fired.append(self._fire(
                        kind, level, f"توكنات {engine} قربت تخلص",
                        f"المتبقي من حد الدقيقة لـ {engine} "
                        f"{int(remaining)} توكن بس — الردود الجاية ممكن "
                        "تتأخر أو تقف لحد ما الحد يتجدد.",
                        now))
            else:
                self._clear_kind(
                    kind, f"توكنات {engine} رجعت كافية",
                    f"المتبقي من حد الدقيقة لـ {engine} رجع فوق الحد — "
                    "تنبيه التوكنات اتحل.")
        return fired

    def check_reply_time(self, elapsed_sec, now: float | None = None) -> list:
        """slow-reply rule (rc.3 d): `elapsed_sec` is the MEASURED
        wall time of one finished reply, in seconds (the caller
        times brain.ask_with_tools -- see the integration point in
        the rc.3 report). Fires slow_reply (warn) when a reply took
        longer than monitor_slow_reply_sec, and a resolved notice
        when a later reply comes back inside the threshold. A reply
        is either slow or it is not -- there is no guessing here.
        Returns the problem alert that fired, if any (resolved
        notices go through on_alert only, like check_once)."""
        now = self._clock() if now is None else now
        try:
            elapsed = float(elapsed_sec)
        except (TypeError, ValueError):
            return []
        threshold = float(self.settings["monitor_slow_reply_sec"])
        if elapsed > threshold:
            if self._should_fire("slow_reply", now):
                return [self._fire(
                    "slow_reply", "warn", "الرد أخد وقت أطول من المعتاد",
                    f"الرد أخد {elapsed:.0f} ثانية (الحد {threshold:.0f} "
                    "ثانية) — ممكن يكون النت بطيء أو المحرك مضغوط، "
                    "جرّب تاني لو اتأخر أكتر.",
                    now)]
            return []
        self._clear_kind(
            "slow_reply", "سرعة الرد رجعت طبيعي",
            "الرد الأخير جه في الوقت المعتاد — تنبيه الرد البطيء اتحل.")
        return []

    def _evaluate(self, reading: dict, now: float) -> list:
        """Check one reading against every threshold. Returns the list
        of problem alerts that fired (resolved notices go through
        on_alert only, never in this list -- see _fire_resolved).
        Kinds whose condition is false are removed from _active, so
        they can fire again when they re-appear (after the cooldown).

        rc.3 (b): related conditions are measured FIRST, then grouped
        before any individual firing -- CPU+temp together and
        RAM+disk together each send ONE alert instead of two, and a
        member never fires individually while its group holds (its
        _last_fired is stamped by the group firing, so the cooldown
        cannot be bypassed through the member kind either)."""
        fired = []
        s = self.settings
        ram = reading.get("ram") or {}
        disk = reading.get("disk") or {}

        # -- measure every condition (no firing yet) ----------------------
        # RAM: free < 500 MB OR used > 92% (his machine idles at 84-86%,
        # so the threshold must sit well above idle or it spams him).
        free_mb = ram.get("free_mb")
        pct = ram.get("percent")
        ram_bad = ((free_mb is not None and free_mb < s["monitor_ram_free_mb"])
                   or (pct is not None and pct > s["monitor_ram_percent"]))
        ram_level = ("critical" if (free_mb is not None and free_mb < 200)
                     or (pct is not None and pct >= 97) else "warn")

        # Disk: free < 10 GB OR free < 10%.
        free_gb = disk.get("free_gb")
        pct_free = disk.get("percent_free")
        disk_bad = ((free_gb is not None
                     and free_gb < s["monitor_disk_free_gb"])
                    or (pct_free is not None
                        and pct_free < s["monitor_disk_percent_free"]))
        disk_level = ("critical" if (free_gb is not None and free_gb < 2)
                      or (pct_free is not None and pct_free < 5) else "warn")

        # CPU temperature: only when a real sensor answered.
        temp_c = reading.get("temp_c")
        temp_level = None
        if temp_c is not None and reading.get("temp_available"):
            if temp_c >= s["monitor_temp_critical_c"]:
                temp_level = "critical"
            elif temp_c >= s["monitor_temp_warn_c"]:
                temp_level = "warn"

        # Sustained CPU load (rolling window, see _sustained_high).
        cpu_bad = self._sustained_high()

        # Battery: only while discharging.
        battery = reading.get("battery")
        battery_bad = False
        battery_level = "warn"
        bpct = None
        if battery and not battery.get("charging"):
            bpct = battery.get("percent")
            if bpct is not None and bpct < s["monitor_battery_warn_percent"]:
                battery_bad = True
                battery_level = ("critical" if bpct < s["monitor_battery_critical_percent"]
                                 else "warn")

        # -- groups (rc.3 b): one alert for related conditions -----------
        # CPU + temp together: sustained load heating the CPU. The
        # cause is labelled as likely ("الغالب") -- it is the most
        # plausible reading of two simultaneous measurements, not a
        # proven fact.
        cpu_temp_group = cpu_bad and temp_level is not None
        if cpu_temp_group:
            if self._should_fire("cpu_temp", now):
                fired.append(self._fire_group(
                    "cpu_temp", _GROUP_MEMBERS["cpu_temp"],
                    "critical" if temp_level == "critical" else "warn",
                    "المعالج سخن وشغال على آخره",
                    f"المعالج شغال فوق "
                    f"{s['monitor_cpu_sustained_percent']}% في المتوسط "
                    f"بقاله {s['monitor_cpu_sustained_minutes']} دقايق "
                    f"وحرارته {temp_c:.0f}°م في نفس الوقت — الغالب إن "
                    "الحمل المستمر هو اللي مسخّن الجهاز، فخفّف الشغل "
                    "عليه الأول.",
                    now))
        else:
            self._clear_group(
                "cpu_temp", "المعالج والحرارة رجعوا طبيعي",
                "الحمل على المعالج والحرارة رجعوا طبيعي — تنبيه "
                "المعالج السخن اتحل.")

        # RAM + disk together: the machine is out of headroom on both
        # fronts at once -- one resource-pressure alert, not two.
        # (No single cause is claimed here: the two shortages are
        # stated as what they are.)
        ram_disk_group = ram_bad and disk_bad
        if ram_disk_group:
            if self._should_fire("ram_disk", now):
                fired.append(self._fire_group(
                    "ram_disk", _GROUP_MEMBERS["ram_disk"],
                    "critical" if (ram_level == "critical"
                                   and disk_level == "critical")
                    else "warn",
                    "موارد الجهاز شبه مستنفدة",
                    f"الرام الفاضي {free_mb} ميجا والقرص الفاضي "
                    f"{free_gb} جيجا في نفس الوقت — الجهاز مضغوط من "
                    "ناحيتين، فاقفل برامج تقيلة وامسح ملفات مش "
                    "محتاجها.",
                    now))
        else:
            self._clear_group(
                "ram_disk", "موارد الجهاز رجعت كافية",
                "الرام والقرص رجعوا فوق الحد — تنبيه الموارد اتحل.")

        # -- individuals (skipped while their group holds) ---------------
        if ram_bad:
            if not ram_disk_group and self._should_fire("ram_low", now):
                fired.append(self._fire(
                    "ram_low", ram_level, "الذاكرة (RAM) شبه ممتلئة",
                    f"الرام المستخدمة {pct}% والفاضي {free_mb} ميجا بس - "
                    "اقفل برامج تقيلة أو اعمل ريستارت للجهاز.", now))
        elif free_mb is not None or pct is not None:
            # Measured and healthy -> genuinely resolved. A MISSING
            # RAM reading is not a clear: it drops the state silently
            # below instead, never claiming "back to normal".
            self._clear_kind(
                "ram_low", "الرام رجعت طبيعي",
                "الذاكرة الفاضية رجعت فوق الحد — تنبيه الرام اتحل.")
        else:
            self._active.discard("ram_low")

        if disk_bad:
            if not ram_disk_group and self._should_fire("disk_low", now):
                fired.append(self._fire(
                    "disk_low", disk_level, "مساحة القرص قربت تخلص",
                    f"الفاضي في القرص {free_gb} جيجا بس "
                    f"({pct_free}% من القرص) - امسح ملفات مش محتاجها.",
                    now))
        elif free_gb is not None or pct_free is not None:
            self._clear_kind(
                "disk_low", "مساحة القرص رجعت كافية",
                "المساحة الفاضية في القرص رجعت فوق الحد — تنبيه القرص "
                "اتحل.")
        else:
            self._active.discard("disk_low")

        if temp_level is not None:
            if not cpu_temp_group and self._should_fire("temp_high", now):
                fired.append(self._fire(
                    "temp_high", temp_level,
                    "حرارة المعالج عالية جداً" if temp_level == "critical"
                    else "حرارة المعالج عالية",
                    f"حرارة المعالج {temp_c:.0f}°م - سيّب الجهاز يبرد "
                    "واقفل البرامج التقيلة فوراً."
                    if temp_level == "critical" else
                    f"حرارة المعالج {temp_c:.0f}°م - خفّف الشغل على "
                    "الجهاز شوية.",
                    now))
        elif temp_c is not None and reading.get("temp_available"):
            # A real reading back under the warn line -> resolved.
            self._clear_kind(
                "temp_high", "حرارة المعالج رجعت طبيعي",
                "حرارة المعالج رجعت تحت الحد — تنبيه الحرارة اتحل.")
        else:
            # No sensor: never invent a temperature, never alert on
            # it -- and never claim it "resolved" either. The state
            # just drops silently.
            self._active.discard("temp_high")

        if cpu_bad:
            if not cpu_temp_group and self._should_fire("cpu_high", now):
                fired.append(self._fire(
                    "cpu_high", "warn", "المعالج شغال على آخره من فترة",
                    f"المعالج شغال فوق "
                    f"{s['monitor_cpu_sustained_percent']}% في المتوسط "
                    f"بقاله {s['monitor_cpu_sustained_minutes']} دقايق - "
                    "شوف إيه البرنامج اللي ساحب كل ده.", now))
        else:
            self._clear_kind(
                "cpu_high", "حمل المعالج رجع طبيعي",
                "حمل المعالج المستمر رجع تحت الحد — تنبيه المعالج "
                "اتحل.")

        if battery_bad:
            if self._should_fire("battery_low", now):
                fired.append(self._fire(
                    "battery_low", battery_level, "البطارية قربت تخلص",
                    f"البطارية {bpct}% والشاحن مش متوصل - "
                    "وصّل الشاحن.", now))
        elif battery is not None:
            # Measured and healthy (charging, or back above the warn
            # line) -> genuinely resolved. A missing battery reading
            # drops the state silently, never as a "resolved".
            self._clear_kind(
                "battery_low", "البطارية رجعت فوق الحد",
                "البطارية رجعت فوق الحد أو الشاحن اتوصل — تنبيه "
                "البطارية اتحل.")
        else:
            self._active.discard("battery_low")

        return fired

    def check_once(self, reading: dict | None = None,
                   now: float | None = None) -> list:
        """One poll: sample (or use the given reading), evaluate, fire.
        Returns the list of problem alerts that fired (usually []).
        Resolved notices go through on_alert only, never in this
        list (see _fire_resolved). `now` is an injectable timestamp
        for tests; production passes nothing. When a token_fn was
        passed to the constructor, the token-limit-near rule is also
        evaluated here on the measured remaining (rc.3 d)."""
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
        fired = self._evaluate(reading, now)
        if self.token_fn is not None:
            try:
                fired += self.check_token_usage(self.token_fn(), now=now)
            except Exception as exc:
                warn("monitor", f"Token usage check failed: {exc}")
        return fired

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
