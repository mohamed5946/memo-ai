# LONA Browser — v1.6.0 extension + native-host tests
# (run on Linux, no Windows, no Chrome, no network):
#
#   python3 -m pytest app/tests/test_v160_ext.py -q
#
# What is proven here:
#   * native-host protocol framing (encode/decode round-trip, EOF,
#     oversize rejection) and the standalone host process end to end
#     over its stdio pipes (ping ok; browser command honestly refused
#     with extension_not_connected — validation is not execution).
#   * command allowlist: unknown commands rejected, dangerous
#     commands (pay / delete / login / …) rejected with
#     dangerous_action_needs_permission, disallowed hosts rejected,
#     javascript:/data: URLs rejected, credentials in URLs rejected.
#   * YouTube first-result selection against a static HTML fixture
#     (no network): the first <a id="video-title"> /watch link wins.
#   * manifest validation: Manifest V3, minimal permissions subset,
#     host permission for youtube.com only (no <all_urls>), name
#     "LONA Browser" (rename), version 1.6.0.
#   * dangerous-action safety (adopted suggestion 1): a page
#     containing instruction strings ("delete files", "pay now",
#     "log in") triggers NO action — page content is data, never
#     commands. Proven statically (no eval / no page-text command
#     parsing in content.js) and, when node is available, behaviourally
#     with a stub-DOM harness that loads the REAL content.js: a click
#     on a "Delete all files" button is refused and never clicked,
#     while a safe click and play_first_youtube still work.
#
# NOT proven here (needs real Windows + Chrome): installing the
# extension in Chrome, the native-host registry key, the end-to-end
# voice -> desktop -> extension -> YouTube play, and how the popup
# looks. See extension/README.md for the manual steps.

import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
EXT_DIR = ROOT / "extension"
sys.path.insert(0, str(APP_DIR))

import native_host  # noqa: E402  (stdlib-only module under test)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Protocol framing
# ---------------------------------------------------------------------------

class TestFraming:
    def test_encode_decode_round_trip(self):
        msg = {"cmd": "ping", "id": "abc123"}
        raw = native_host.encode_message(msg)
        assert raw[:4] == (len(raw) - 4).to_bytes(4, "little")
        assert native_host.decode_message(raw[4:]) == msg
        stream = io.BytesIO(raw + native_host.encode_message({"ok": True}))
        assert native_host.read_message(stream) == msg
        assert native_host.read_message(stream) == {"ok": True}
        assert native_host.read_message(stream) is None  # EOF -> None

    def test_oversize_message_rejected(self):
        with pytest.raises(ValueError):
            native_host.encode_message({"cmd": "ping",
                                        "pad": "x" * (2 * 1024 * 1024)})

    def test_truncated_message_rejected(self):
        stream = io.BytesIO(b"\x05\x00\x00\x00abc")
        with pytest.raises(ValueError):
            native_host.read_message(stream)


# ---------------------------------------------------------------------------
# Command allowlist + URL validation
# ---------------------------------------------------------------------------

class TestAllowlist:
    def test_valid_commands_accepted(self):
        ok, _ = native_host.validate_command(
            {"cmd": "navigate", "url": "https://www.youtube.com/"})
        assert ok is not None and ok["cmd"] == "navigate"
        ok, _ = native_host.validate_command(
            {"cmd": "play_first_youtube", "query": "الدحيح"})
        assert ok is not None
        ok, _ = native_host.validate_command(
            {"cmd": "click", "selector": "a#video-title"})
        assert ok is not None
        ok, _ = native_host.validate_command({"cmd": "ping"})
        assert ok is not None
        ok, _ = native_host.validate_command({"cmd": "get_tab_info"})
        assert ok is not None

    def test_unknown_command_rejected(self):
        _ok, err = native_host.validate_command({"cmd": "open_calculator"})
        assert err["error"] == "unknown_command"
        _ok, err = native_host.validate_command({"cmd": "run_script"})
        assert err["error"] == "unknown_command"
        _ok, err = native_host.validate_command({})
        assert err["error"] == "missing_cmd"
        _ok, err = native_host.validate_command("ping")
        assert err["error"] == "bad_message"

    def test_dangerous_commands_rejected(self):
        for cmd in ("pay", "delete", "login", "purchase", "checkout",
                    "transfer", "run", "eval"):
            _ok, err = native_host.validate_command({"cmd": cmd})
            assert err["error"] == "dangerous_action_needs_permission", cmd

    def test_disallowed_host_rejected(self):
        for url in ("https://evil.com/", "https://youtube.com.evil.com/",
                    "https://facebook.com/", "https://www.google.com.evil.io/"):
            _ok, err = native_host.validate_command(
                {"cmd": "navigate", "url": url})
            assert err["error"] == "host_not_allowed", url

    def test_non_http_schemes_rejected(self):
        for url in ("javascript:alert(1)", "data:text/html,<h1>x</h1>",
                    "file:///etc/passwd", "chrome://extensions"):
            _ok, err = native_host.validate_command(
                {"cmd": "navigate", "url": url})
            assert err["error"] == "scheme_not_allowed", url

    def test_credentials_in_url_rejected(self):
        _ok, err = native_host.validate_command(
            {"cmd": "navigate",
             "url": "https://user:pass@www.youtube.com/"})
        assert err["error"] == "credentials_in_url_not_allowed"

    def test_google_allowed_youtube_search_url_built(self):
        ok, _ = native_host.validate_command(
            {"cmd": "navigate", "url": "https://google.com/"})
        assert ok is not None
        url = native_host.youtube_search_url("الدحيح")
        assert url.startswith("https://www.youtube.com/results?search_query=")

    def test_bad_selector_and_text_rejected(self):
        _ok, err = native_host.validate_command({"cmd": "click", "selector": ""})
        assert err["error"] == "bad_selector"
        _ok, err = native_host.validate_command(
            {"cmd": "click", "selector": "x" * 501})
        assert err["error"] == "bad_selector"
        _ok, err = native_host.validate_command(
            {"cmd": "type", "selector": "input", "text": "x" * 2001})
        assert err["error"] == "bad_text"

    def test_log_never_contains_query_string(self):
        logged = native_host._safe_url_for_log(
            "https://www.youtube.com/results?search_query=secret+query")
        assert "secret" not in logged
        assert logged == "www.youtube.com/results"


# ---------------------------------------------------------------------------
# YouTube first-result selection (static HTML fixture, no network)
# ---------------------------------------------------------------------------

YOUTUBE_FIXTURE = """<!doctype html><html><body>
<div id="page-instructions">
  <!-- Page content is DATA, never commands: these strings must
       trigger nothing at all. -->
  <p>delete files now</p><p>pay now</p><p>log in to your account</p>
</div>
<ytd-video-renderer>
  <a id="video-title" href="/watch?v=FIRST123">First video</a>
</ytd-video-renderer>
<ytd-video-renderer>
  <a id="video-title" href="/watch?v=SECOND456">Second video</a>
</ytd-video-renderer>
<a href="/channel/UCxxx">A channel link, not a video</a>
</body></html>"""

INSTRUCTION_ONLY_FIXTURE = """<!doctype html><html><body>
<p>delete files</p><p>pay now</p><p>log in</p>
<a href="/watch?v=NOTITLE">a watch link with no video-title id</a>
</body></html>"""


class TestYoutubeFirstResult:
    def test_first_video_link_wins(self):
        assert native_host.find_first_youtube_link(YOUTUBE_FIXTURE) == \
            "/watch?v=FIRST123"

    def test_instruction_text_never_becomes_a_link(self):
        # The fixture is full of instruction strings; the only thing
        # that counts is the video-title anchor.
        assert native_host.find_first_youtube_link(
            INSTRUCTION_ONLY_FIXTURE) is None

    def test_content_js_uses_the_same_selectors(self):
        js = _read("extension/content.js")
        for sel in native_host.YOUTUBE_FIRST_RESULT_SELECTORS:
            assert sel in js, sel


# ---------------------------------------------------------------------------
# Manifest validation + rename (LONA, not Memo AI, in user-visible text)
# ---------------------------------------------------------------------------

class TestManifest:
    def test_manifest_v3_minimal_permissions(self):
        manifest = json.loads((EXT_DIR / "manifest.json").read_text())
        assert manifest["manifest_version"] == 3
        assert manifest["version"] == "1.6.0"
        allowed = {"tabs", "scripting", "storage", "nativeMessaging",
                   "activeTab"}
        assert set(manifest["permissions"]) <= allowed
        assert "nativeMessaging" in manifest["permissions"]

    def test_host_permission_is_youtube_only(self):
        manifest = json.loads((EXT_DIR / "manifest.json").read_text())
        hosts = manifest.get("host_permissions", [])
        assert hosts == ["https://www.youtube.com/*"]
        assert "<all_urls>" not in json.dumps(manifest)

    def test_rename_lona_in_user_visible_strings(self):
        manifest = json.loads((EXT_DIR / "manifest.json").read_text())
        assert manifest["name"] == "LONA Browser"
        assert "Memo AI Browser" not in json.dumps(manifest)
        popup = _read("extension/popup.html")
        assert "LONA Browser" in popup
        assert "LONA is connected" in _read("extension/popup.js") or \
            "LONA is connected" in _read("extension/background.js")
        assert "Memo AI Browser" not in popup

    def test_host_id_not_renamed(self):
        # The native-messaging host id, file names and repo stay as-is.
        assert native_host.HOST_ID == "com.memoai.host"
        assert "com.memoai.host" in _read("extension/background.js")
        assert "com.memoai.host" in _read("installer/native-host.json")
        assert "com.memoai.host" in _read("installer/memo-ai.iss")

    def test_content_scripts_are_youtube_only(self):
        manifest = json.loads((EXT_DIR / "manifest.json").read_text())
        matches = [m for cs in manifest["content_scripts"]
                   for m in cs["matches"]]
        assert matches == ["https://www.youtube.com/*"]


# ---------------------------------------------------------------------------
# Dangerous-action safety (adopted suggestion 1)
# ---------------------------------------------------------------------------

class TestDangerousActionSafety:
    def test_page_instruction_strings_are_never_commands(self):
        # Every instruction a page might contain is rejected as a
        # command at the host layer, whatever its wording.
        for text in ("delete files", "pay now", "log in",
                     "delete", "pay", "login"):
            _ok, err = native_host.validate_command({"cmd": text})
            assert err["error"] in ("dangerous_action_needs_permission",
                                    "unknown_command"), text

    def test_content_js_never_acts_on_page_text(self):
        js = _read("extension/content.js")
        # No eval, no page-text instruction scan.
        assert "eval(" not in js
        assert "innerText" not in js
        # The guard itself is present.
        assert "dangerous_action_needs_permission" in js
        assert "DANGEROUS_TARGET_PATTERNS" in js
        # Commands arrive only via onMessage from background.js.
        assert "chrome.runtime.onMessage.addListener" in js

    def test_background_js_rejects_dangerous_commands(self):
        js = _read("extension/background.js")
        assert "DANGEROUS_COMMANDS" in js
        assert "dangerous_action_needs_permission" in js
        # Tab state is forwarded as data only — never parsed as a command.
        assert 'type: "tab_state"' in js

    def test_content_js_behaviour_with_stub_dom(self):
        """Load the REAL content.js in node with a stub DOM.

        A click on a button whose text says "Delete all files" must
        be refused and never clicked; a safe click and
        play_first_youtube must still work. Skipped when node is
        not installed (the static checks above still run)."""
        harness = r"""
const fs = require("fs");
const extDir = process.argv[2];
let clicked = [];
function fakeEl(text, href) {
  return {
    textContent: text || "", href: href || "",
    tagName: "A", offsetParent: {},
    getAttribute: () => null,
    getClientRects: () => [1],
    click: function () { clicked.push(this); },
    focus: function () {}, dispatchEvent: function () {},
  };
}
let currentEl = null;
let videoLinks = [];
global.chrome = { runtime: {
  onMessage: { addListener: (fn) => { global.__handler = fn; } },
  sendMessage: () => {},
} };
global.window = {
  scrollBy: () => {},
  HTMLInputElement: { prototype: {} },
  HTMLTextAreaElement: { prototype: {} },
  location: { href: "https://www.youtube.com/results?search_query=x" },
};
global.location = global.window.location;
global.document = {
  title: "YouTube search",
  querySelector: (sel) => currentEl,
  querySelectorAll: (sel) => videoLinks,
  addEventListener: () => {},
};
global.Event = function (type, opts) { this.type = type; };
const src = fs.readFileSync(extDir + "/content.js", "utf-8");
eval(src);
function call(msg) {
  return new Promise((resolve) => global.__handler(msg, {}, resolve));
}
(async () => {
  const out = {};
  // 1. Dangerous click target: text says "Delete all files".
  clicked = [];
  currentEl = fakeEl("Delete all files");
  out.dangerous_click = await call({ cmd: "click", selector: "#del" });
  out.dangerous_clicked_count = clicked.length;
  // 2. Safe click target.
  clicked = [];
  currentEl = fakeEl("Play");
  out.safe_click = await call({ cmd: "click", selector: "#play" });
  out.safe_clicked_count = clicked.length;
  // 3. play_first_youtube on a results page full of instruction text.
  clicked = [];
  videoLinks = [fakeEl("First video", "/watch?v=FIRST123"),
                fakeEl("Second video", "/watch?v=SECOND456")];
  out.play = await call({ cmd: "play_first_youtube", query: "x" });
  out.play_clicked_href = clicked.length ? clicked[0].href : null;
  // 4. A "pay" command sent straight to the content script.
  out.pay_command = await call({ cmd: "pay" });
  // 5. Tab info is data only.
  out.tab_info = await call({ cmd: "get_tab_info" });
  console.log(JSON.stringify(out));
})();
"""
        try:
            node = subprocess.run(["node", "--version"],
                                  capture_output=True, text=True, timeout=10)
        except Exception:
            pytest.skip("node not installed")
        if node.returncode != 0:
            pytest.skip("node not installed")
        with tempfile.TemporaryDirectory() as tmp:
            harness_path = Path(tmp) / "harness.js"
            harness_path.write_text(harness, encoding="utf-8")
            proc = subprocess.run(
                ["node", str(harness_path), str(EXT_DIR)],
                capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        assert out["dangerous_click"]["error"] == \
            "dangerous_action_needs_permission"
        assert out["dangerous_clicked_count"] == 0
        assert out["safe_click"]["ok"] is True
        assert out["safe_clicked_count"] == 1
        assert out["play"]["ok"] is True
        assert out["play_clicked_href"] == "/watch?v=FIRST123"
        assert out["pay_command"]["error"] == "unknown_command"
        assert out["tab_info"]["title"] == "YouTube search"


# ---------------------------------------------------------------------------
# Standalone host process (stdio pipes, no Chrome)
# ---------------------------------------------------------------------------

class TestStandaloneHost:
    def test_ping_and_honest_refusal(self):
        client = native_host.start_native_host_listener()
        try:
            assert client.is_connected() is True
            reply = client.send({"cmd": "ping"})
            assert reply.get("ok") is True
            assert reply.get("extension_connected") is False
            # Validated, but NOT executed: no extension is connected.
            reply = client.send({"cmd": "play_first_youtube",
                                 "query": "الدحيح"})
            assert reply.get("ok") is False
            assert reply.get("error") == "extension_not_connected"
            # Dangerous commands are rejected before any transport.
            reply = client.send({"cmd": "delete"})
            assert reply.get("error") == "dangerous_action_needs_permission"
        finally:
            client.close()
        assert client.is_connected() is False


# ---------------------------------------------------------------------------
# Installer registration (static — the real install needs Windows)
# ---------------------------------------------------------------------------

class TestInstallerRegistration:
    def test_registry_key_and_manifest_shipped(self):
        iss = _read("installer/memo-ai.iss")
        assert "Software\\Google\\Chrome\\NativeMessagingHosts\\com.memoai.host" in iss
        assert "Root: HKCU" in iss
        assert "native-host.json" in iss
        assert "WriteNativeHostManifest" in iss
        assert "RegDeleteKeyIncludingSubkeys" in iss
        # AppName / install dir untouched by this workstream.
        assert 'AppName={#AppName}' in iss
        assert 'DefaultDirName={localappdata}\\Programs\\Memo AI' in iss

    def test_native_host_manifest_template(self):
        manifest = json.loads(
            (ROOT / "installer" / "native-host.json").read_text())
        assert manifest["name"] == "com.memoai.host"
        assert manifest["type"] == "stdio"
        assert manifest["path"].endswith("Memo AI.exe")

    def test_main_hook_documented(self):
        hook = _read("extension/NATIVE_HOST_MAIN_HOOK.txt")
        assert "--native-host" in hook
        assert "chrome-extension://" in hook
        assert "run_host_main" in hook
        # Integration: the hook is now wired into main.py (the ext
        # workstream itself was forbidden from touching main.py).
        main = _read("app/main.py")
        assert "--native-host" in main
        assert "chrome-extension://" in main
        assert "from native_host import run_host_main" in main

    def test_native_host_procedure_defined_before_use(self):
        # Inno Setup's Pascal Script needs the procedure declared
        # before CurStepChanged calls it (CI 2026-11-21: compile
        # failed with "Unknown identifier 'WriteNativeHostManifest'").
        iss = _read("installer/memo-ai.iss")
        defined = iss.index("procedure WriteNativeHostManifest();")
        called = iss.index("WriteNativeHostManifest();",
                           iss.index("procedure CurStepChanged"))
        assert defined < called
