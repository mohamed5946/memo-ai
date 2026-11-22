// LhmReader — LONA's embedded CPU-temperature helper (v1.6.4-rc.2).
//
// Reads the CPU temperature straight from LibreHardwareMonitorLib,
// the official LibreHardwareMonitor library, so LONA needs NO
// separate LibreHardwareMonitor app installed and never has an
// "is LHM running?" failure mode. One-shot console program: it
// prints exactly one JSON line to stdout and exits.
//
//   success: {"celsius": 51.5, "fans": [{"name": "Fan #1", "rpm": 2400}]}
//   failure: {"error": "not_available"|"needs_admin", "detail": "..."}
//
// "fans" is the list of Fan-type sensors from the same
// LibreHardwareMonitor Computer object (SuperIO / embedded
// controller — IsMotherboardEnabled + IsControllerEnabled below):
// real RPM values only, [] when the chip reports no fan sensors
// (e.g. an unsupported laptop EC — the app then simply shows no
// fans, never a faked RPM). A reading outside 0-20000 RPM is
// sensor garbage, never a fan speed. The error shape has no fans
// key; the temperature logic is unchanged.
//
// Exit code is always 0 when a JSON line was printed; the Python
// wrapper (app/lhm_embedded.py) parses stdout, never the exit code.
//
// Sensor choice mirrors app/monitor.py: only CPU temperature
// sensors count ("CPU Package" / "Core #n", identifier .../cpu/...),
// GPU and board sensors are never reported as CPU temperature, and
// a reading outside 0-125 C is sensor garbage, never a temperature.
//
// Some sensors need administrator privileges. This helper NEVER
// asks for elevation itself (no UAC prompt): it tries without
// admin, and when the CPU is there but no temperature value comes
// back while not elevated, it says needs_admin so the app can show
// the honest hint + a relaunch-as-admin option instead of a wrong
// number. See THIRD_PARTY.md for the LibreHardwareMonitor license
// (MPL-2.0).

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Security.Principal;
using LibreHardwareMonitor.Hardware;

namespace LhmReader
{
    internal static class Program
    {
        // A temperature outside this range is sensor garbage, not a
        // real temperature — same range as app/monitor.py.
        private const float PlausibleMinC = 0.0f;
        private const float PlausibleMaxC = 125.0f;

        // A fan speed outside this range is sensor garbage, not a
        // real RPM — same range as app/lhm_embedded.py.
        private const float PlausibleMinRpm = 0.0f;
        private const float PlausibleMaxRpm = 20000.0f;

        private static int Main()
        {
            try
            {
                Console.WriteLine(ReadCpuTemperature());
            }
            catch (Exception ex)
            {
                // The wrapper must always get parseable JSON, even if
                // something unexpected blew up above.
                Console.WriteLine(JsonError("not_available",
                    "unexpected error: " + ex.GetType().Name + ": " + ex.Message));
            }
            return 0;
        }

        private static string ReadCpuTemperature()
        {
            bool isAdmin = IsAdministrator();
            // Fans live on the SuperIO chip / embedded controller,
            // not on the CPU: motherboard + controller hardware must
            // be enabled or no fan sensor is ever enumerated. The
            // temperature walk below still only counts CPU sensors.
            var computer = new Computer
            {
                IsCpuEnabled = true,
                IsMotherboardEnabled = true,
                IsControllerEnabled = true,
            };
            try
            {
                computer.Open();
            }
            catch (UnauthorizedAccessException ex)
            {
                // The driver could not load without elevation.
                return JsonError(
                    isAdmin ? "not_available" : "needs_admin",
                    "LibreHardwareMonitor could not open the hardware monitor: " + ex.Message);
            }
            catch (Exception ex)
            {
                return JsonError("not_available",
                    "LibreHardwareMonitor could not open the hardware monitor: "
                    + ex.GetType().Name + ": " + ex.Message);
            }

            try
            {
                // Fan sensors, from every piece of hardware (SuperIO
                // / EC included) — collected before any temperature
                // early-return, so a successful reading always
                // carries them, even when the temperature comes from
                // an averaged core list.
                var fans = new List<FanReading>();
                foreach (IHardware hardware in computer.Hardware)
                {
                    CollectFans(hardware, fans);
                    foreach (IHardware sub in hardware.SubHardware)
                        CollectFans(sub, fans);
                }

                bool sawCpu = false;
                float? package = null;
                var cores = new List<float>();
                float? any = null;

                foreach (IHardware hardware in computer.Hardware)
                {
                    if (hardware.HardwareType != HardwareType.Cpu)
                        continue;
                    sawCpu = true;
                    CollectCpuTemps(hardware, ref package, cores, ref any);
                    foreach (IHardware sub in hardware.SubHardware)
                        CollectCpuTemps(sub, ref package, cores, ref any);
                }

                if (package.HasValue)
                    return JsonSuccess(package.Value, fans);
                if (cores.Count > 0)
                {
                    // No "CPU Package" sensor: average the cores.
                    float sum = 0;
                    foreach (float c in cores)
                        sum += c;
                    return JsonSuccess(sum / cores.Count, fans);
                }
                if (any.HasValue)
                    return JsonSuccess(any.Value, fans);

                if (!sawCpu)
                    return JsonError("not_available",
                        "no CPU hardware was found by LibreHardwareMonitor");
                // The CPU is there but no temperature value came back.
                // Without admin some sensors stay unreadable; with
                // admin and still nothing, the machine has no sensor.
                return JsonError(
                    isAdmin ? "not_available" : "needs_admin",
                    isAdmin
                        ? "CPU found but no temperature sensor returned a value"
                        : "CPU found but no temperature sensor returned a value; "
                          + "reading it may need administrator privileges");
            }
            finally
            {
                try { computer.Close(); } catch { /* best effort */ }
            }
        }

        private sealed class FanReading
        {
            public string Name;
            public float Rpm;
        }

        // Every Fan-type sensor on this piece of hardware that
        // returned a plausible value. A stopped fan reads 0 RPM —
        // a real value, kept. One unreadable piece of hardware
        // must not take the whole reading down with it.
        private static void CollectFans(IHardware hardware,
            List<FanReading> fans)
        {
            try
            {
                hardware.Update();
            }
            catch
            {
                return;
            }
            foreach (ISensor sensor in hardware.Sensors)
            {
                if (sensor.SensorType != SensorType.Fan)
                    continue;
                if (!sensor.Value.HasValue)
                    continue;
                float rpm = sensor.Value.Value;
                if (rpm < PlausibleMinRpm || rpm > PlausibleMaxRpm)
                    continue;
                string name = sensor.Name;
                if (string.IsNullOrEmpty(name))
                    name = "Fan";
                fans.Add(new FanReading { Name = name, Rpm = rpm });
            }
        }

        private static void CollectCpuTemps(IHardware hardware,
            ref float? package, List<float> cores, ref float? any)
        {
            try
            {
                hardware.Update();
            }
            catch
            {
                // One unreadable piece of hardware must not take the
                // whole reading down with it.
                return;
            }
            foreach (ISensor sensor in hardware.Sensors)
            {
                if (sensor.SensorType != SensorType.Temperature)
                    continue;
                if (!sensor.Value.HasValue)
                    continue;
                float value = sensor.Value.Value;
                if (value < PlausibleMinC || value > PlausibleMaxC)
                    continue;
                string name = sensor.Name ?? "";
                string identifier = sensor.Identifier != null
                    ? sensor.Identifier.ToString() : "";
                if (!IsCpuTempSensor(name, identifier))
                    continue;
                if (!any.HasValue)
                    any = value;
                if (name.IndexOf("package", StringComparison.OrdinalIgnoreCase) >= 0)
                    package = value;
                else if (name.IndexOf("core", StringComparison.OrdinalIgnoreCase) >= 0)
                    cores.Add(value);
            }
        }

        // Same rule as monitor._is_cpu_temp_sensor: named for the CPU
        // (cpu / core / package), never a GPU sensor.
        private static bool IsCpuTempSensor(string name, string identifier)
        {
            string text = name + " " + identifier;
            if (text.IndexOf("gpu", StringComparison.OrdinalIgnoreCase) >= 0)
                return false;
            return text.IndexOf("cpu", StringComparison.OrdinalIgnoreCase) >= 0
                || text.IndexOf("core", StringComparison.OrdinalIgnoreCase) >= 0
                || text.IndexOf("package", StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private static bool IsAdministrator()
        {
            try
            {
                using (WindowsIdentity identity = WindowsIdentity.GetCurrent())
                {
                    return new WindowsPrincipal(identity)
                        .IsInRole(WindowsBuiltInRole.Administrator);
                }
            }
            catch
            {
                return false;
            }
        }

        private static string JsonSuccess(float celsius,
            List<FanReading> fans)
        {
            string fansJson = "";
            for (int i = 0; i < fans.Count; i++)
            {
                if (i > 0)
                    fansJson += ", ";
                fansJson += "{\"name\": \"" + JsonEscape(fans[i].Name)
                    + "\", \"rpm\": "
                    + ((double)fans[i].Rpm).ToString(
                        CultureInfo.InvariantCulture) + "}";
            }
            return "{\"celsius\": "
                + ((double)celsius).ToString(CultureInfo.InvariantCulture)
                + ", \"fans\": [" + fansJson + "]}";
        }

        private static string JsonError(string error, string detail)
        {
            return "{\"error\": \"" + JsonEscape(error)
                + "\", \"detail\": \"" + JsonEscape(detail) + "\"}";
        }

        private static string JsonEscape(string s)
        {
            if (s == null)
                return "";
            return s.Replace("\\", "\\\\").Replace("\"", "\\\"")
                .Replace("\r", "\\r").Replace("\n", "\\n")
                .Replace("\t", "\\t");
        }
    }
}
