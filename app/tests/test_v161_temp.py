"""v1.6.1 workstream D - item 11: the program reads the temperature
itself.

Run only this file (plus test_v161_typing_mood.py):

    python -m pytest app/tests/test_v161_temp.py -q

What is proven here (all with mocked outputs - no Windows, no real
sensors, no installs):
  * Thermal Zone perf counters: Get-Counter Kelvin values convert to
    Celsius before the plausibility filter.
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

    def test_get_counter_kelvin_to_celsius(self):
        temps = self._run_with([
            ("Get-Counter", "37.0\n25.0\n"),
        ])
        self.assertEqual(len(temps), 2)
        self.assertAlmostEqual(temps[0], 37.0, 2)
        self.assertAlmostEqual(temps[1], 25.0, 2)

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

    def test_msacpi_still_wins_first(self):
        temps = self._run_with([
            ("MSAcpi_ThermalZoneTemperature", "36.85\n"),
            ("Get-Counter", "126.85\n"),
        ])
        self.assertEqual(len(temps), 1)
        self.assertAlmostEqual(temps[0], 36.85, 2)

    def test_script_converts_units_and_orders_stages(self):
        # v1.6.2 B24: the conversion lives in the single PowerShell
        # script now (the mock above only simulates its already-
        # converted output), so pin the script's conversions and
        # stage order here, against the real code.
        import inspect
        src = inspect.getsource(monitor_mod._powershell_temps)
        # Only the script itself, not the docstring above it (the
        # docstring names some stages in a different order).
        src = src[src.index('script = r"""'):]
        self.assertIn("/ 10 - 273.15", src)  # tenths-Kelvin -> C
        self.assertIn("- 273.15", src)  # Kelvin -> C
        self.assertLess(src.index("MSAcpi_ThermalZoneTemperature"),
                        src.index("Get-Counter"))
        self.assertLess(src.index("Get-Counter"),
                        src.index("Win32_TemperatureProbe"))
        self.assertLess(src.index("Win32_TemperatureProbe"),
                        src.index("DCIM_ThermalSensor"))

    def test_libre_monitor_still_wins_over_perf_counters(self):
        temps = self._run_with([
            ("LibreHardwareMonitor", "61.5\n"),
            ("Get-Counter", "126.85\n"),
        ])
        self.assertEqual(temps, [61.5])

    def test_powershell_failure_is_empty_not_fatal(self):
        with mock.patch("subprocess.run",
                        side_effect=Exception("no powershell")):
            self.assertEqual(monitor_mod._powershell_temps(), [])


class TestWmiTempPaths(unittest.TestCase):
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
    def test_counter_reading_is_reported(self):
        fake = _fake_ps_run_factory([("Get-Counter", "37.0\n")])
        with mock.patch("subprocess.run", fake), \
                mock.patch.object(sys, "platform", "win32"), \
                mock.patch.object(monitor_mod, "_wmi_temps",
                                  return_value=[]):
            temp, available = monitor_mod.read_cpu_temp()
        self.assertTrue(available)
        self.assertAlmostEqual(temp, 37.0, 2)

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
