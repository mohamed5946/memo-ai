#!/usr/bin/env python3
"""Memo AI v1.4.0 - WhatsApp headless tests (no phone, no WhatsApp account).

Run from the repo root:  python3 scripts/whatsapp-test.py
Covers (all offline):
  1. Number normalization / JID parsing / masking (privacy).
  2. Owner vs guest routing (mocked brain): owner -> ask_with_tools with
     the owner's own history; guest -> brain.ask only, private history.
  3. tools.trusted_caller: auto-allow ONLY in a trusted thread; the
     destructive blocklist still refuses in a trusted thread.
  4. Sidecar JSON-lines protocol via --self-test (fake QR, ping, errors).
  5. Full Python service against a FAKE sidecar (canned qr/ready/message
     events): state machine off -> waiting_qr -> linked, owner + guest
     replies sent back, logout wipes the session dir.

Exit code 0 = all pass. Prints PASS/FAIL per case.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import brain  # noqa: E402
import tools  # noqa: E402
import whatsapp  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


def test_numbers() -> None:
    print("== numbers ==")
    cases = [
        ("01557887719", "201557887719"),
        ("+20 155 788 7719", "201557887719"),
        ("201557887719", "201557887719"),
        ("1557887719", "201557887719"),
        ("00201557887719", "201557887719"),
        ("", ""),
        ("abc", ""),
    ]
    for raw, want in cases:
        check(f"normalize {raw!r}", whatsapp.normalize_number(raw) == want,
              f"got {whatsapp.normalize_number(raw)!r} want {want!r}")
    check("jid -> number",
          whatsapp.jid_to_number("201557887719@s.whatsapp.net") == "201557887719")
    check("lid jid -> ''",
          whatsapp.jid_to_number("123456@lid") == "")
    check("group jid -> ''",
          whatsapp.jid_to_number("123@g.us") == "")
    check("mask", whatsapp.mask_number("201557887719") == "…19")
    check("mask empty", whatsapp.mask_number("") == "…??")
    check("mask never contains full number",
          "201557887719" not in whatsapp.mask_number("201557887719"))


def test_trusted_caller() -> None:
    print("== trusted caller ==")
    # In a trusted thread, permission is auto-allowed (no dialog, no wait).
    with tools.trusted_caller():
        check("trusted auto-allow",
              tools.request_permission("open_app", "open VS Code") is True)
    # Outside a trusted thread, permission BLOCKS (waiting for the UI):
    # it must not return within 0.5s, then resolves via the UI path.
    result = {}

    def _ask():
        result["ok"] = tools.request_permission("open_app", "open VS Code")

    t = threading.Thread(target=_ask, daemon=True)
    t.start()
    time.sleep(0.5)
    check("untrusted blocks (no auto-allow)", t.is_alive())
    pending = tools.get_pending()
    check("pending request visible to UI", pending is not None)
    if pending:
        tools.resolve_permission(pending["id"], True)
    t.join(timeout=5)
    check("untrusted resolves via UI", result.get("ok") is True)
    # The destructive blocklist still refuses INSIDE a trusted thread.
    with tools.trusted_caller():
        out = tools.execute_tool("run_command", {"command": "format C:"})
        check("blocklist still refuses (trusted)",
              "مقدرتش" in out or "ممنوع" in out, f"got {out!r}")


def test_sidecar_selftest() -> None:
    print("== sidecar --self-test ==")
    sidecar = ROOT / "app" / "whatsapp_sidecar" / "sidecar.js"
    node = shutil.which("node")
    if node is None:
        print("  SKIP sidecar self-test (no node on PATH)")
        return
    cmds = ('{"cmd":"ping"}\n'
            '{"cmd":"send","to":"201234567890@s.whatsapp.net","text":"hi"}\n'
            '{"cmd":"bogus"}\n'
            '{"cmd":"stop"}\n')
    proc = subprocess.run(
        [node, str(sidecar), tempfile.mkdtemp(), "--self-test"],
        input=cmds, capture_output=True, text=True, timeout=30)
    events = []
    for line in proc.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"event": "NON-JSON", "line": line[:80]})
    kinds = [e.get("event") for e in events]
    check("self-test exit 0", proc.returncode == 0, f"rc={proc.returncode}")
    check("self-test stdout is pure JSON lines",
          all(e.get("event") != "NON-JSON" for e in events))
    check("self-test emits QR data URL",
          any(e.get("event") == "qr" and
              str(e.get("qr", "")).startswith("data:image/png;base64,")
              for e in events))
    check("self-test pong", "pong" in kinds)
    check("self-test send-without-socket errors",
          any(e.get("event") == "error" for e in events))
    check("self-test stderr has no protocol leak",
          "data:image/png" not in proc.stderr)


FAKE_SIDECAR = """\
// Fake Baileys sidecar for headless tests: emits canned events, logs
// every command it receives to sends.log next to the auth dir (argv[2]).
import { createInterface } from "node:readline";
import { appendFileSync } from "node:fs";
import path from "node:path";
const SENDS = path.join(path.dirname(process.argv[2]), "sends.log");
const out = (o) => process.stdout.write(JSON.stringify(o) + "\\n");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
await sleep(200);
out({ event: "qr", qr: "data:image/png;base64,FAKEQR" });
await sleep(200);
out({ event: "ready", me: { id: "201111111111@s.whatsapp.net", name: "MemoThird" } });
await sleep(200);
out({ event: "message", id: "m-owner", from: "201557887719@s.whatsapp.net", from_alt: "", pushName: "Mohamed", text: "افتح المفكرة", ts: 1 });
await sleep(200);
out({ event: "message", id: "m-guest", from: "201000000000@s.whatsapp.net", from_alt: "", pushName: "Guest", text: "hello", ts: 2 });
const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", (line) => {
  appendFileSync(SENDS, line + "\\n");
  let cmd; try { cmd = JSON.parse(line); } catch { return; }
  if (cmd.cmd === "stop" || cmd.cmd === "logout") process.exit(0);
});
"""


def test_service_with_fake_sidecar() -> None:
    print("== service + fake sidecar ==")
    node = shutil.which("node")
    if node is None:
        print("  SKIP fake-sidecar test (no node on PATH)")
        return
    tmp = Path(tempfile.mkdtemp(prefix="wa-test-"))
    fake_js = tmp / "fake-sidecar.mjs"
    fake_js.write_text(FAKE_SIDECAR, encoding="utf-8")
    sends_log = tmp / "sends.log"
    sends_log.write_text("", encoding="utf-8")
    auth_dir = tmp / "auth"
    auth_dir.mkdir()

    # Mock the brain (no Groq, no network): record which entry point ran.
    calls = {"tools": [], "ask": [], "owner_flags": []}

    def fake_ask_with_tools(text, ai_name, user_name, lang, history=None,
                            owner_unlocked=False):
        calls["tools"].append(text)
        calls["owner_flags"].append(owner_unlocked)
        (history if history is not None else []).append(
            {"role": "user", "content": text})
        return {"reply": "تمام، فتحت المفكرة.", "engine": "groq",
                "tools_used": 1, "tools_disabled": False}

    def fake_ask(text, ai_name, user_name, lang, history=None,
                 owner_unlocked=False):
        calls["ask"].append(text)
        (history if history is not None else []).append(
            {"role": "user", "content": text})
        return {"reply": "Hi! I'm Memo.", "engine": "groq"}

    orig_tools = brain.ask_with_tools
    orig_ask = brain.ask
    brain.ask_with_tools = fake_ask_with_tools
    brain.ask = fake_ask
    orig_js = whatsapp.SIDECAR_JS
    orig_auth = whatsapp.AUTH_DIR
    whatsapp.SIDECAR_JS = fake_js
    whatsapp.AUTH_DIR = auth_dir
    try:
        settings = {"whatsapp_enabled": True,
                    "whatsapp_owner_phone": "01557887719",
                    "ai_name": "Memo", "user_name": "Mohamed",
                    "language": "ar"}
        svc = whatsapp.WhatsAppService(settings)
        check("service starts", svc.start() is True)
        # Wait for the fake sidecar's canned events + replies.
        deadline = time.time() + 10
        sends = []
        while time.time() < deadline:
            if sends_log.exists():
                sends = [json.loads(ln) for ln in
                         sends_log.read_text(encoding="utf-8").splitlines()
                         if ln.strip()]
            if len(sends) >= 2:
                break
            time.sleep(0.2)
        state = svc.get_state()
        check("state linked after ready", state["status"] == "linked",
              f"got {state['status']}")
        check("linked phone masked", state["linked_phone"] == "…11",
              f"got {state['linked_phone']!r}")
        check("owner routed to tools",
              calls["tools"] == ["افتح المفكرة"], f"got {calls['tools']!r}")
        check("owner routed with owner mode on (v1.4.1)",
              calls["owner_flags"] == [True],
              f"got {calls['owner_flags']!r}")
        check("guest routed to chat only",
              calls["ask"] == ["hello"], f"got {calls['ask']!r}")
        check("two replies sent", len(sends) == 2, f"got {len(sends)}")
        if len(sends) == 2:
            check("owner reply to owner JID",
                  sends[0].get("to") == "201557887719@s.whatsapp.net")
            check("guest reply to guest JID",
                  sends[1].get("to") == "201000000000@s.whatsapp.net")
            check("replies quote the original message",
                  sends[0].get("reply_to") == "m-owner" and
                  sends[1].get("reply_to") == "m-guest")
        # Owner history stayed private (guest text never entered it).
        check("owner history has no guest text",
              all("hello" not in str(m) for m in svc._owner_history))
        svc.stop()
        time.sleep(0.5)
        check("service stops", svc.get_state()["status"] == "off")
        # Logout wipes the session dir.
        auth_dir.mkdir(exist_ok=True)
        (auth_dir / "creds.json").write_text("{}", encoding="utf-8")
        svc.start()
        time.sleep(1.0)
        svc.logout()
        check("logout wipes session dir", not auth_dir.exists())
    finally:
        brain.ask_with_tools = orig_tools
        brain.ask = orig_ask
        whatsapp.SIDECAR_JS = orig_js
        whatsapp.AUTH_DIR = orig_auth
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("Memo AI WhatsApp headless tests (v1.4.0)")
    test_numbers()
    test_trusted_caller()
    test_sidecar_selftest()
    test_service_with_fake_sidecar()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
