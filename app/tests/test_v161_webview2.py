# Memo AI v1.6.1 - tests for the WebView2 detection fix
# (Inno Setup cannot run on Linux, so these inspect installer/memo-ai.iss,
# following the existing pattern in test_v160_ext.py):
#
#   python3 -m pytest app/tests/test_v161_webview2.py -q
#
# What is proven here (field bug, reported 2026-11-22: the standalone
# WebView2 installer said "already installed for the system", yet the
# Memo AI installer still tried to download the runtime - and when the
# download failed it wrongly told the user WebView2 was not installed):
#   * The registry check covers all four views (64-bit machine,
#     32-bit/WOW6432Node machine, 64-bit per-user, 32-bit per-user) -
#     the old code checked only HKLM64 + HKCU64 and missed a
#     per-machine standalone install registered in the 32-bit view.
#   * The runtime folder is checked as well (per-machine under
#     Program Files (x86) and per-user under local app data): a
#     version subfolder holding msedgewebview2.exe at or above the
#     86.0.622.0 minimum counts as present.
#   * WebView2IsPresent() uses the folder check as a fallback when the
#     registry says nothing.
#   * Field case pinned (2026-11-22 01:20, Mohamed's own PowerShell):
#     WebView2 157.0.4341.60 registered ONLY under
#     HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\
#     {F3017226-FE2A-4295-8BDF-00C3A9A7E4C5} (pv = 157.0.4341.60) -
#     the v1.6.0 .iss looked only in HKLM64/HKCU64 and missed exactly
#     this. The HKLM32 branch must query that exact key and be reached
#     when the 64-bit view has nothing, and 157.0.4341.60 must pass the
#     minimum-version rule.
#   * PrepareToInstall() never aborts the setup (still returns '').
#   * The warning distinguishes a failed DOWNLOAD from a missing
#     runtime: the download-failed message says the download could not
#     happen (offline), never that WebView2 is not installed; the old
#     "not installed on this device" claim is gone from the script.
#
# NOT proven here (needs real Windows + Inno Setup): compiling the
# script, and the detection running against a real registry/folder.

import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent

GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def _iss() -> str:
    return (ROOT / "installer" / "memo-ai.iss").read_text(encoding="utf-8")


def _function_body(iss: str, name: str) -> str:
    """The Pascal Script body of `name`, from its declaration to the
    next top-level `function`/`procedure` (or end of the [Code] section
    for the last one)."""
    start = iss.index(f"function {name}")
    rest = iss[start:]
    m = re.search(r"\n(function|procedure) ", rest[10:])
    return rest if m is None else rest[:m.start() + 10]


def _version_at_least(version: str) -> bool:
    """Mirror of WebView2VersionAtLeast() in the .iss, with the
    threshold numbers read out of the Pascal expression itself, so the
    test tracks the script instead of a hardcoded copy of the rule."""
    iss = _iss()
    major = int(re.search(r"\(A > (\d+)\)", iss).group(1))
    minor = int(re.search(r"\(B > (\d+)\)", iss).group(1))
    build = int(re.search(r"\(C >= (\d+)\)", iss).group(1))
    a, b, c = (int(p) for p in version.split(".")[:3])
    return (a > major) or ((a == major) and ((b > minor) or (
        (b == minor) and (c >= build))))


class TestWebView2Detection:
    def test_registry_check_covers_all_four_views(self):
        body = _function_body(_iss(), "GetWebView2Version()")
        for root in ("HKLM64", "HKLM32", "HKCU64", "HKCU32"):
            assert root in body, f"{root} missing from GetWebView2Version"
        # All four look up the same EdgeUpdate Clients GUID key.
        assert body.count(GUID) >= 4
        assert body.count("'pv'") >= 4

    def test_wow6432node_only_install_is_detected(self):
        # Field case, 2026-11-22 01:20 (Mohamed's own PowerShell run):
        # WebView2 157.0.4341.60 registered ONLY under
        #   HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\
        #   {F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}   (pv = 157.0.4341.60)
        # The v1.6.0 .iss looked only in HKLM64/HKCU64, so this exact
        # install was reported missing and a download was attempted.
        body = _function_body(_iss(), "GetWebView2Version()")
        # The .iss registry-key literals use single backslashes
        # (Pascal has no backslash escapes), so build the expected
        # literal without backslash escapes in source.
        bs = chr(92)
        key = ("SOFTWARE" + bs + "Microsoft" + bs
               + "EdgeUpdate" + bs + "Clients" + bs + GUID)
        # Every view queries the exact same key (including the
        # WOW6432Node view, HKLM32) with the same 'pv' value name.
        assert body.count(key) == 4
        assert body.count("'pv'") >= 4
        # The HKLM32 branch is an else-if after the HKLM64 lookup, so
        # it is reached exactly when the 64-bit view has no key - the
        # field machine's state.
        assert body.index("HKLM64") < body.index("HKLM32")
        assert "else if RegQueryStringValue(HKLM32," in body
        # And the field version passes the minimum-version rule, so
        # WebView2IsPresent() returns True for this machine.
        assert _version_at_least("157.0.4341.60")
        assert not _version_at_least("85.0.564.0")

    def test_runtime_folder_checked(self):
        iss = _iss()
        body = _function_body(iss, "WebView2FolderVersion(")
        assert "msedgewebview2.exe" in body
        assert "WebView2VersionAtLeast(Rec.Name)" in body
        assert "FindFirst" in body
        # Both install locations are consulted.
        assert "{pf32}\\Microsoft\\EdgeWebView\\Application" in iss
        assert "{localappdata}\\Microsoft\\EdgeWebView\\Application" in iss

    def test_presence_uses_folder_fallback(self):
        body = _function_body(_iss(), "WebView2IsPresent()")
        assert "GetWebView2Version()" in body
        assert "WebView2FolderVersion" in body
        # Registry first, folder only when the registry says nothing.
        assert body.index("GetWebView2Version()") < body.index(
            "WebView2FolderVersion")

    def test_prepare_to_install_never_aborts(self):
        body = _function_body(_iss(), "PrepareToInstall(")
        assert "Result := '';" in body
        assert "WebView2IsPresent()" in body
        assert "Abort" not in body

    def test_messages_distinguish_download_failure_from_missing(self):
        iss = _iss()
        # The old claim is gone: it said "not installed on this
        # device" even when only the download had failed.
        assert "مش متثبت على الجهاز ده" not in iss
        assert "مش متثبت" not in iss
        body = _function_body(iss, "PrepareToInstall(")
        # Download failure has its own message (offline is the cause
        # named); install failure has a different one (the install
        # did not complete - the runtime may already be there).
        assert "مقدرناش ننزّل" in body
        assert "مفيش اتصال بالإنترنت" in body
        assert "التثبيت مكملش" in body
        assert "ممكن يكون متثبت أصلاً" in body
        # Both messages still point at the official download link and
        # still let the LONA install continue.
        assert body.count("https://go.microsoft.com/fwlink/p/?LinkId=2124703") >= 2
        assert "هيكمل تثبيت LONA عادي" in body


if __name__ == "__main__":
    import unittest
    unittest.main()
