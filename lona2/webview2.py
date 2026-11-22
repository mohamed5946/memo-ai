"""LONA v2 — WebView2 runtime detection.

Checks HKLM64/HKLM32/HKCU64/HKCU32 INCLUDING WOW6432Node — the v1.6.0
installer checked only the 64-bit hives and wrongly reported "not
installed" while WebView2 157 was present under WOW6432Node.
On non-Windows the check honestly reports it cannot run there.
"""

import sys

IS_WINDOWS = sys.platform == "win32"

_CLIENT_KEY = (r"SOFTWARE\Microsoft\EdgeUpdate\Clients"
               r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")
_WOW_KEY = (r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"
            r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")


def _read_version(root, subkey: str):
    try:
        import winreg
        with winreg.OpenKey(root, subkey) as key:
            version, _ = winreg.QueryValueEx(key, "pv")
            return str(version)
    except Exception:
        return None


def check_webview2() -> dict:
    """{installed, version, detail}. installed is None when the check
    cannot run on this platform."""
    if not IS_WINDOWS:
        return {"installed": None, "version": None,
                "detail": "الفحص ده على ويندوز بس"}
    import winreg
    found = []
    for root, label in ((winreg.HKEY_LOCAL_MACHINE, "HKLM64"),
                        (winreg.HKEY_CURRENT_USER, "HKCU64")):
        version = _read_version(root, _CLIENT_KEY)
        if version:
            found.append((label, version))
    for root, label in ((winreg.HKEY_LOCAL_MACHINE, "HKLM32"),
                        (winreg.HKEY_CURRENT_USER, "HKCU32")):
        version = _read_version(root, _WOW_KEY)
        if version:
            found.append((label, version))
    if found:
        label, version = found[0]
        return {"installed": True, "version": version,
                "detail": f"WebView2 {version} ({label})"}
    return {"installed": False, "version": None,
            "detail": "WebView2 مش متثبت"}
