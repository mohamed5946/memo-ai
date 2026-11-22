# Memo AI v1.6.2 - tests for the installer bounded-wait fix (A9)
# (Inno Setup cannot run on Linux, so these inspect installer/memo-ai.iss,
# following the existing pattern in test_v161_webview2.py):
#
#   python3 -m pytest app/tests/test_v162_installer.py -q
#
# What is proven here (field bug, reported 2026-11-22 00:40: after
# running the setup.exe directly, the installer hung on
# "التحضير للتثبيت" / Preparing to install - the silent WebView2
# download/install in PrepareToInstall showed no progress and, on a
# slow connection, blocked setup indefinitely):
#   * The WebView2 download has a 60-second timeout (-TimeoutSec 60
#     on Invoke-WebRequest) and the downloaded file must be non-empty
#     (FileSizeOf > 0) - a 0-byte file counts as a failed download,
#     not a success.
#   * The WebView2 runtime installer no longer runs with
#     ewWaitUntilTerminated: it starts with ewNoWait and is waited on
#     for at most ~120 seconds (ExecWithTimeout + a Sleep/tasklist
#     poll loop); setup continues even when it times out or fails,
#     and success is still judged by WebView2 actually being present.
#   * The same bounded treatment applies to the old Memo AI
#     uninstaller in RemoveOldInstall: it gets ~120 seconds, then
#     setup carries on and removes the leftovers by hand.
#   * PrepareToInstall still never aborts the setup and still shows
#     the two distinct warnings (download failed vs install did not
#     complete).
#   * The WebView2 detection logic is unchanged (the v1.6.1
#     four-registry-view check still decides "missing").
#   * version.json is bumped to 1.6.2 with v1.6.2 release URLs, while
#     the SHA-256 hashes are left untouched (CI overwrites them with
#     the real built-file hashes on main after the tag build).
#
# NOT proven here (needs real Windows + Inno Setup): compiling the
# script, and the waits/timings running against a real system.

import json
import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent

GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def _iss() -> str:
    return (ROOT / "installer" / "memo-ai.iss").read_text(encoding="utf-8")


def _manifest() -> dict:
    return json.loads((ROOT / "version.json").read_text(encoding="utf-8"))


def _code_block(iss: str, decl: str) -> str:
    """The Pascal Script body starting at the `decl` declaration, up
    to the next top-level `function`/`procedure` (or end of file)."""
    start = iss.index(decl)
    rest = iss[start:]
    m = re.search(r"\n(function|procedure) ", rest[len(decl):])
    return rest if m is None else rest[:m.start() + len(decl)]


class TestDownloadBounded:
    def test_download_has_60s_timeout(self):
        body = _code_block(_iss(), "function PrepareToInstall(")
        assert "Invoke-WebRequest" in body
        assert "-TimeoutSec 60" in body

    def test_download_verified_non_empty(self):
        iss = _iss()
        body = _code_block(iss, "function PrepareToInstall(")
        # A download that "succeeded" but left a 0-byte file counts
        # as failed and falls through to the download-failed warning.
        assert "FileSizeOf(SetupPath) > 0" in body
        # The helper exists, is declared before PrepareToInstall,
        # and measures the file with a stream (Inno Setup has no
        # built-in file-size function).
        assert iss.index("function FileSizeOf(") < iss.index(
            "function PrepareToInstall(")
        helper = _code_block(iss, "function FileSizeOf(")
        assert "TFileStream" in helper
        assert "Stream.Size" in helper
        assert "Result := -1;" in helper

    def test_webview2_installer_uses_bounded_wait(self):
        body = _code_block(_iss(), "function PrepareToInstall(")
        # The old unbounded call is gone entirely...
        assert "Exec(SetupPath, '/silent /install'" not in body
        assert "ewWaitUntilTerminated" in body  # ...but only on the
        # PowerShell download, which now self-times-out at 60s:
        assert body.count("ewWaitUntilTerminated") == 1
        # ...replaced by ewNoWait + a ~120s bounded wait.
        assert "ExecWithTimeout(SetupPath, '/silent /install', 120)" in body
        # Success is still judged by the runtime really being there.
        assert "WebView2IsPresent()" in body

    def test_exec_with_timeout_helper(self):
        iss = _iss()
        assert iss.index("function ExecWithTimeout(") < iss.index(
            "function PrepareToInstall(")
        helper = _code_block(iss, "function ExecWithTimeout(")
        assert "ewNoWait" in helper
        assert "WaitForProcessExit(ExtractFileName(FileName), TimeoutSeconds)" in helper
        # Starting the program failing is not a hang either.
        assert "if not Exec(FileName, Params, '', SW_HIDE, ewNoWait, ResultCode) then" in helper

    def test_wait_loop_polls_with_sleep_and_timeout(self):
        iss = _iss()
        helper = _code_block(iss, "function WaitForProcessExit(")
        assert "tasklist.exe" in helper
        assert "Sleep(1000)" in helper
        assert "TimeoutSeconds" in helper
        assert "while Elapsed <= TimeoutSeconds" in helper
        # A tasklist failure counts as "gone" - the check itself must
        # never become a second hang.
        assert "if not Found then" in helper


class TestPrepareToInstallStillContinues:
    def test_never_aborts_and_warnings_kept(self):
        body = _code_block(_iss(), "function PrepareToInstall(")
        assert "Result := '';" in body
        assert "Abort" not in body
        assert "مقدرناش ننزّل" in body
        assert "التثبيت مكملش" in body
        assert "هيكمل تثبيت LONA عادي" in body

    def test_detection_logic_unchanged(self):
        # The v1.6.1 four-registry-view check still decides "missing"
        # - this item only bounds the waits, it does not touch it.
        body = _code_block(_iss(), "function GetWebView2Version()")
        for root in ("HKLM64", "HKLM32", "HKCU64", "HKCU32"):
            assert root in body, f"{root} missing from GetWebView2Version"
        assert body.count(GUID) >= 4


class TestRemoveOldInstallBounded:
    def test_old_uninstaller_uses_bounded_wait(self):
        body = _code_block(_iss(), "procedure RemoveOldInstall()")
        # The old unbounded call is gone; the uninstaller gets ~120s
        # and setup carries on regardless of the outcome.
        assert "ewWaitUntilTerminated" not in body
        assert "ExecWithTimeout(UninstExe, '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES', 120)" in body

    def test_leftovers_still_removed_afterwards(self):
        body = _code_block(_iss(), "procedure RemoveOldInstall()")
        # Even when the old uninstaller times out, its folder and
        # Add/Remove Programs entry are removed by hand below.
        assert body.index("ExecWithTimeout(UninstExe,") < body.index(
            "DelTree(OldDir, True, True, True)")
        assert "RegDeleteKeyIncludingSubkeys(HKCU," in body
        assert "OldAppId + '_is1'" in body

    def test_no_unbounded_exec_left_in_either_procedure(self):
        # The only ewWaitUntilTerminated calls left in the whole
        # script are the PowerShell download (self-timed-out at 60s)
        # and the tasklist poll (a fast local command).
        iss = _iss()
        hits = [m.start() for m in re.finditer(
            "ewWaitUntilTerminated", iss)]
        assert len(hits) == 2, (
            f"expected 2 bounded ewWaitUntilTerminated uses, found {len(hits)}")
        for h in hits:
            window = iss[max(0, h - 200):h]
            assert ("powershell.exe" in window
                    or "tasklist.exe" in window), window[-120:]


class TestVersionManifest:
    def test_version_bumped_with_v162_urls(self):
        m = _manifest()
        assert m["version"] == "1.6.2"
        assert m["url"] == (
            "https://github.com/mohamed5946/memo-ai/releases/download/"
            "v1.6.2/memo-ai-v1.6.2.zip")
        assert m["setup_url"] == (
            "https://github.com/mohamed5946/memo-ai/releases/download/"
            "v1.6.2/LONA-Setup-1.6.2.exe")
        assert "1.6.0" not in m["url"]
        assert "1.6.0" not in m["setup_url"]
        assert "1.6.1" not in m["url"]
        assert "1.6.1" not in m["setup_url"]

    def test_hashes_left_untouched_for_ci(self):
        # The v1.6.1 hashes stay as they are; the CI workflow
        # ("Update version.json manifest on main") overwrites them
        # with the real built-file hashes on main after the tag build.
        m = _manifest()
        assert m["sha256"] == (
            "e901b8c63dc7bd265dbaeff777accf7c7fd8ac49714cb50284d6cdbc9e9ae31d")
        assert m["setup_sha256"] == (
            "d19dd4a7ba7f7af977c65b8ba547e5606d6eb007ac9a2890a00f27a554057e15")

    def test_other_fields_preserved(self):
        m = _manifest()
        assert [f["title"] for f in m["features"]] == [
            "تصميم LONA الجديد",
            "الجزيرة والروبوت",
            "كروت حية",
            "نادي «يا لونا» فيرد عليك",
            "إصلاح ذاتي آمن",
            "تحكم في المتصفح",
        ]
        # Notes now open with the v1.6.2 entry; the v1.6.1 text is
        # kept verbatim as history, not rewritten.
        assert m["notes"].startswith("v1.6.2:")
        assert "v1.6.1: إصلاح المحدّث" in m["notes"]


if __name__ == "__main__":
    import unittest
    unittest.main()
