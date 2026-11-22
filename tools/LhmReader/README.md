# LhmReader — embedded CPU-temperature helper for LONA

Reads the CPU temperature straight from LibreHardwareMonitorLib
(the official LibreHardwareMonitor library, MPL-2.0 — see
`THIRD_PARTY.md`), so LONA needs no separate LibreHardwareMonitor
app installed and never has an "is LHM running?" failure mode.

## Build (Windows; CI does this before PyInstaller)

```
dotnet build tools/LhmReader -c Release
```

Output: `tools/LhmReader/bin/Release/net472/LhmReader.exe` next to
`LibreHardwareMonitorLib.dll`. Both files are bundled into the
installer by `installer/memo-ai.spec` (under `app/lhm/`) and found
at runtime by `app/lhm_embedded.py`.

`net472` was chosen on purpose: it runs on any Windows 10/11 with
no extra runtime to install. The .NET 8 SDK builds it via the NuGet
targeting pack — no .NET Framework SDK needed on the build machine.

## Run

```
LhmReader.exe
{"celsius": 51.5}
```

One JSON line on stdout, exit code always 0 when a line was
printed. On failure the line is an error object instead:

```
{"error": "needs_admin", "detail": "CPU found but no temperature sensor returned a value; reading it may need administrator privileges"}
```

Error values: `not_available` (no CPU / no sensor / library could
not open), `needs_admin` (CPU present, no value, process not
elevated — the app shows the honest hint + a relaunch-as-admin
option instead of a wrong number). The helper never prompts for
elevation itself and never prints a guessed temperature: a reading
outside 0–125 °C is sensor garbage and is skipped, and GPU/board
sensors are never reported as CPU temperature.

## License

LibreHardwareMonitorLib is MPL-2.0 (full text in `THIRD_PARTY.md`).
It is used unmodified from NuGet; only this helper (LONA's own
code) calls its public API.
