# Memo AI v1.4.3 - hidden process spawning (no visible console window)
#
# Any subprocess the app spawns on Windows MUST use these kwargs, or
# the user sees a black Terminal window pop up - and (worse) a
# supervisor that restarts the process re-opens the window every time
# the user closes it (v1.4.2 field report: the WhatsApp sidecar's
# node.exe console, and the monitor's PowerShell temperature probe).
#
# CREATE_NO_WINDOW (0x08000000) is defined here, not via
# subprocess.CREATE_NO_WINDOW (which only exists on Windows), so this
# module imports on every platform. On non-Windows the kwargs are
# empty and spawning is unchanged.
"""Hidden subprocess spawning: no console window on Windows."""

import sys

CREATE_NO_WINDOW = 0x08000000


def popen_kwargs() -> dict:
    """Extra kwargs for subprocess.Popen / subprocess.run that keep the
    child hidden on Windows (no console window). Empty elsewhere."""
    if sys.platform == "win32":
        return {"creationflags": CREATE_NO_WINDOW}
    return {}


# subprocess.run() takes the same kwargs as Popen.
run_kwargs = popen_kwargs
