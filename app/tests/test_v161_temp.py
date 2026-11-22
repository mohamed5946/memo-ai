"""v1.6.1 workstream D - item 11: the program reads the temperature
itself.

Run only this file (plus test_v161_typing_mood.py):

    python -m pytest app/tests/test_v161_temp.py -q

What is proven here (all with mocked outputs - no Windows, no real
sensors, no installs):
  * Thermal Zone perf counters + MSAcpi zones are ZONE readings, not
    CPU temperature: they live in _powershell_thermal_zones() /
    _wmi_thermal_zones() only, and read_cpu_temp() reports
    (None, False) when no CPU sensor answers — never a ~24 C room
    value as CPU temperature.
  * Libre/OpenHardwareMonitor sensors are filtered by name: only
    CPU-named sensors (CPU Package / Core) count; GPU and board
    sensors are ignored.
  * Win32_TemperatureProbe (root\\cimv2): tenths-of-Kelvin readings
    convert; plain-Celsius readings pass through; garbage is dropped.
  * Dell DCIM sensors (root\\dcim\\sysman): DCIM_ThermalSensor and
    CIM_TemperatureSensor readings are picked up; a missing DCIM
    namespace is a silent no-op.
  * Implausible values are never reported: read_cpu_temp() returns
    (None, False) instead of inventing a number.
  * temp_hint contract: get_device_overview() carries the exact
    agreed {"text", "url"} dict while the temperature is unavailable,
    and None once a real reading exists.
"""
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import monitor as monitor_mod  # noqa: E402
from monitor import SystemMonitor, _probe_value_to_celsius  # noqa: E402

TEMP_HINT = {
    "text": "الحرارة مش متاحة على الجهاز ده — ثبّت "
            "LibreHardwareMonitor وخليه شغال في الخلفية عشان تظهر",
    "url": "https://github.com/LibreHardwareMonitor/"
           "LibreHardwareMonitor/releases",
}


class _FakeProc:
    def __init__(self, stdout):
        self.stdout = stdout


def _fake_ps_run_factory(outputs):
    """subprocess.run replacement simulating the v1.6.2 single-script
    behaviour: the script tries every source in order and emits the
    first source's values, already converted to Celsius by the script
    itself. outputs: list of (stage, stdout) in script order; the
    first non-empty stdout wins (the script's `if ($out.Count -eq 0)`
    chain); anything else yields "".
    """
    calls = []

    def _run(argv, **kwargs):
        script = argv[4] if len(argv) > 4 else ""
        calls.append(script)
        for _stage, stdout in outputs:
            if stdout:
                return _FakeProc(stdout)
        return _FakeProc("")

    _run.calls = calls
    return _run


class _FakeObj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeNS:
    """A fake WMI namespace: each class name maps to a list of rows."""

    def __init__(self, mapping):
        self._mapping = mapping

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda: list(self._mapping.get(name, []))


def _fake_wmi_module(namespaces):
    """A fake `wmi` module whose WMI(namespace=...) raises for unknown
    namespaces (like a missing namespace) and returns rows otherwise."""
    mod = types.ModuleType("wmi")

    def _wmi(namespace=None):
        if namespace not in namespaces:
            raise Exception("namespace not found: %s" % namespace)
        return namespaces[namespace]

    mod.WMI = _wmi
    return mod


def _reading(**overrides):
    reading = {
        "ram": {"total_mb": 8192, "used_mb": 4096, "free_mb": 4096,
                "percent": 50.0},
        "disk": {"free_gb": 100.0, "total_gb": 256.0,
                 "percent_free": 80.0},
        "cpu_percent": 10.0,
        "battery": {"percent": 80, "charging": True},
        "temp_c": None,
        "temp_available": False,
        "top_processes": [],
        "gpu": {"available": False, "percent": None, "detail": ""},
        "storage": [],
        "battery_drain": {},
    }
    reading.update(overrides)
    return reading


class TestProbeValueConversion(unittest.TestCase):
    def test_tenths_kelvin_converts(self):
        self.assertAlmostEqual(_probe_value_to_celsius(3100), 36.85, 2)
        self.assertAlmostEqual(_probe_value_to_celsius(2982), 25.05, 2)

    def test_plain_celsius_passes_through(self):
        self.assertEqual(_probe_value_to_celsius(45), 45.0)
        self.assertEqual(_probe_value_to_celsius(0), 0.0)

    def test_ambiguous_and_bad_values_are_dropped(self):
        for bad in (150, 200, -5, None, "abc", ""):
            self.assertIsNone(_probe_value_to_celsius(bad), repr(bad))


class TestPowerShellTempPaths(unittest.TestCase):
    def _run_with(self, outputs):
        fake = _fake_ps_run_factory(outputs)
        with mock.patch("subprocess.run", fake):
            return monitor_mod._powershell_temps()

    def _run_zones_with(self, outputs):
        fake = _fake_ps_run_factory(outputs)
        with mock.patch("subprocess.run", fake):
            return monitor_mod._powershell_thermal_zones()

    def test_get_counter_kelvin_to_celsius(self):
        # v1.6.4 (F-E): the Thermal Zone perf counter is a ZONE
        # reading, not CPU temperature — it lives in
        # _powershell_thermal_zones() now, and its Kelvin values
        # still convert to Celsius before the plausibility filter.
        temps = self._run_zones_with([
            ("Get-Counter", "37.0\n25.0\n"),
        ])
        self.assertEqual(len(temps), 2)
        self.assertAlmostEqual(temps[0], 37.0, 2)
        self.assertAlmostEqual(temps[1], 25.0, 2)

    def test_msacpi_zone_is_not_a_cpu_temp(self):
        # v1.6.4 (F-E): an MSAcpi thermal-zone value must never come
        # back as a CPU temperature — the zone script parses it, the
        # CPU script never queries it (pinned by source below, since
        # the mock above cannot simulate stage selection).
        temps = self._run_zones_with([
            ("MSAcpi_ThermalZoneTemperature", "36.85\n"),
        ])
        self.assertEqual(len(temps), 1)
        self.assertAlmostEqual(temps[0], 36.85, 2)

    def test_win32_temperature_probe_tenths_kelvin(self):
        temps = self._run_with([
            ("Win32_TemperatureProbe", "25.05\n"),
        ])
        self.assertEqual(len(temps), 1)
        self.assertAlmostEqual(temps[0], 25.05, 2)

    def test_dcim_thermal_sensor_celsius(self):
        temps = self._run_with([
            ("DCIM_ThermalSensor", "52\n"),
        ])
        self.assertEqual(temps, [52.0])

    def test_cim_temperature_sensor_fallback(self):
        # DCIM_ThermalSensor empty -> CIM_TemperatureSensor is tried.
        temps = self._run_with([
            ("DCIM_ThermalSensor", ""),
            ("CIM_TemperatureSensor", "36.85\n"),
        ])
        self.assertEqual(len(temps), 1)
        self.assertAlmostEqual(temps[0], 36.85, 2)

    def test_all_sources_empty_is_empty(self):
        self.assertEqual(self._run_with([]), [])
        self.assertEqual(self._run_zones_with([]), [])

    def test_script_converts_units_and_orders_stages(self):
        # v1.6.2 B24: the conversion lives in the single PowerShell
        # script now (the mock above only simulates its already-
        # converted output), so pin the script's conversions and
        # stage order here, against the real code.
        # v1.6.4 (F-E): CPU sensors first (Libre/OpenHardwareMonitor
        # CPU-named sensors, then Win32_TemperatureProbe, then DCIM);
        # thermal zones are NOT in the CPU script at all — they live
        # in _powershell_thermal_zones(), probed last and only for
        # the honestly-labelled diagnostics log.
        import inspect
        src = inspect.getsource(monitor_mod._powershell_temps)
        # Only the script itself, not the docstring above it (the
        # docstring names some stages in a different order).
        src = src[src.index('script = r"""'):]
        self.assertIn("/ 10 - 273.15", src)  # tenths-Kelvin -> C
        self.assertLess(src.index("LibreHardwareMonitor"),
                        src.index("Win32_TemperatureProbe"))
        self.assertLess(src.index("Win32_TemperatureProbe"),
                        src.index("DCIM_ThermalSensor"))
        self.assertNotIn("MSAcpi_ThermalZoneTemperature", src)
        self.assertNotIn("Get-Counter", src)
        # The CPU-name filter: only CPU Package / Core sensors count;
        # GPU sensors are excluded.
        self.assertIn("CPU|Core|Package", src)
        self.assertIn("-notmatch 'GPU'", src)
        zone_src = inspect.getsource(monitor_mod._powershell_thermal_zones)
        zone_src = zone_src[zone_src.index('script = r"""'):]
        self.assertIn("MSAcpi_ThermalZoneTemperature", zone_src)
        self.assertIn("Get-Counter", zone_src)
        self.assertIn("- 273.15", zone_src)  # Kelvin -> C

    def test_libre_monitor_cpu_sensor_is_returned(self):
        temps = self._run_with([
            ("LibreHardwareMonitor", "61.5\n"),
        ])
        self.assertEqual(temps, [61.5])

    def test_powershell_failure_is_empty_not_fatal(self):
        with mock.patch("subprocess.run",
                        side_effect=Exception("no powershell")):
            self.assertEqual(monitor_mod._powershell_temps(), [])


class TestWmiTempPaths(unittest.TestCase):
    def test_cpu_named_lhm_sensor_wins_and_gpu_is_ignored(self):
        # v1.6.4 (F-E): only CPU-named LHM sensors count ("CPU
        # Package" / "Core #1"); a GPU sensor and a board sensor are
        # ignored, and the MSAcpi zone is NOT a CPU temp at all.
        namespaces = {
            r"root\wmi": _FakeNS({"MSAcpi_ThermalZoneTemperature": [
                _FakeObj(CurrentTemperature=2972)]}),  # 24.05 C zone
            r"root\LibreHardwareMonitor": _FakeNS({"Sensor": [
                _FakeObj(SensorType="Temperature", Name="CPU Package",
                         Identifier="/intelcpu/0/temperature/0",
                         Value=61.5),
                _FakeObj(SensorType="Temperature", Name="Core #1",
                         Identifier="/intelcpu/0/temperature/1",
                         Value=58.0),
                _FakeObj(SensorType="Temperature", Name="GPU Core",
                         Identifier="/nvidiagpu/0/temperature/0",
                         Value=70.0),
                _FakeObj(SensorType="Temperature", Name="Temperature #1",
                         Identifier="/lpc/nct6798d/temperature/0",
                         Value=30.0),
            ]}),
            r"root\cimv2": _FakeNS({"Win32_TemperatureProbe": []}),
            r"root\dcim\sysman": _FakeNS({"DCIM_ThermalSensor": [],
                                          "CIM_TemperatureSensor": []}),
        }
        fake = _fake_wmi_module(namespaces)
        with mock.patch.dict(sys.modules, {"wmi": fake}):
            temps = monitor_mod._wmi_temps()
            zones = monitor_mod._wmi_thermal_zones()
        self.assertEqual(sorted(temps), [58.0, 61.5])
        self.assertEqual(len(zones), 1)
        self.assertAlmostEqual(zones[0], 24.05, 2)

    def test_probe_and_dcim_via_wmi_package(self):
        namespaces = {
            r"root\wmi": _FakeNS({"MSAcpi_ThermalZoneTemperature": []}),
            r"root\cimv2": _FakeNS({"Win32_TemperatureProbe": [
                _FakeObj(CurrentReading=3100)]}),
            r"root\dcim\sysman": _FakeNS({"DCIM_ThermalSensor": [
                _FakeObj(CurrentReading=52)],
                "CIM_TemperatureSensor": []}),
        }
        fake = _fake_wmi_module(namespaces)
        with mock.patch.dict(sys.modules, {"wmi": fake}):
            temps = monitor_mod._wmi_temps()
        self.assertAlmostEqual(max(temps), 52.0, 2)
        self.assertIn(36.85, [round(t, 2) for t in temps])

    def test_missing_dcim_namespace_is_noop(self):
        namespaces = {
            r"root\wmi": _FakeNS({"MSAcpi_ThermalZoneTemperature": []}),
            r"root\cimv2": _FakeNS({"Win32_TemperatureProbe": []}),
        }
        fake = _fake_wmi_module(namespaces)
        with mock.patch.dict(sys.modules, {"wmi": fake}):
            self.assertEqual(monitor_mod._wmi_temps(), [])

    def test_wmi_package_absent_is_noop(self):
        # sys.modules["wmi"] = None makes `import wmi` raise
        # ImportError, which _wmi_temps treats as "no wmi package".
        with mock.patch.dict(sys.modules, {"wmi": None}):
            self.assertEqual(monitor_mod._wmi_temps(), [])


class TestReadCpuTemp(unittest.TestCase):
    def test_cpu_sensor_reading_is_reported(self):
        fake = _fake_ps_run_factory([("LibreHardwareMonitor", "61.5\n")])
        with mock.patch("subprocess.run", fake), \
                mock.patch.object(sys, "platform", "win32"), \
                mock.patch.object(monitor_mod, "_wmi_temps",
                                  return_value=[]):
            temp, available = monitor_mod.read_cpu_temp()
        self.assertTrue(available)
        self.assertAlmostEqual(temp, 61.5, 2)

    def test_zone_only_reading_is_not_reported_as_cpu_temp(self):
        # v1.6.4 (F-E): the field bug — a ~24 C thermal-zone value
        # presented as CPU temperature. With no CPU sensor answering,
        # only zones do: read_cpu_temp() is (None, False) and the
        # caller shows "not available" + the LHM hint instead.
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch.object(monitor_mod, "_wmi_temps",
                                  return_value=[]), \
                mock.patch.object(monitor_mod, "_powershell_temps",
                                  return_value=[]), \
                mock.patch.object(monitor_mod, "_wmi_thermal_zones",
                                  return_value=[24.0]), \
                mock.patch.object(monitor_mod, "_powershell_thermal_zones",
                                  return_value=[]):
            self.assertEqual(monitor_mod.read_cpu_temp(), (None, False))

    def test_implausible_kelvin_is_never_reported(self):
        # 500 K = 226.85 C: outside 0-125, so no number is invented.
        fake = _fake_ps_run_factory([("Get-Counter", "500\n")])
        with mock.patch("subprocess.run", fake), \
                mock.patch.object(sys, "platform", "win32"), \
                mock.patch.object(monitor_mod, "_wmi_temps",
                                  return_value=[]):
            self.assertEqual(monitor_mod.read_cpu_temp(), (None, False))

    def test_nothing_answering_is_unavailable(self):
        fake = _fake_ps_run_factory([])
        with mock.patch("subprocess.run", fake), \
                mock.patch.object(sys, "platform", "win32"), \
                mock.patch.object(monitor_mod, "_wmi_temps",
                                  return_value=[]):
            self.assertEqual(monitor_mod.read_cpu_temp(), (None, False))

    def test_non_windows_is_unavailable(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertEqual(monitor_mod.read_cpu_temp(), (None, False))


class TestTempHintContract(unittest.TestCase):
    def test_hint_present_with_exact_shape_when_unavailable(self):
        mon = SystemMonitor(sample_fn=lambda: _reading())
        overview = mon.get_device_overview()
        self.assertFalse(overview["temp_available"])
        self.assertEqual(overview["temp_hint"], TEMP_HINT)

    def test_hint_is_none_when_temperature_available(self):
        mon = SystemMonitor(
            sample_fn=lambda: _reading(temp_c=55.0,
                                       temp_available=True))
        overview = mon.get_device_overview()
        self.assertTrue(overview["temp_available"])
        self.assertEqual(overview["temp_c"], 55.0)
        self.assertIsNone(overview["temp_hint"])

    def test_hint_is_a_copy_not_shared_state(self):
        mon = SystemMonitor(sample_fn=lambda: _reading())
        hint = mon.get_device_overview()["temp_hint"]
        hint["text"] = "mutated"
        self.assertEqual(mon.get_device_overview()["temp_hint"], TEMP_HINT)


if __name__ == "__main__":
    unittest.main()
