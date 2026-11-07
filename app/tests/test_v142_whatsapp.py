# Memo AI v1.4.2 - headless WhatsApp tests (run on Linux, no Windows,
# no real WhatsApp, no network).
#
#   python -m pytest app/tests/test_v142_whatsapp.py -v
#   python -m unittest discover -s app/tests -v   (also runs these)
#
# What is proven here (every v1.4.2 WhatsApp scope item):
#   (a) LID senders: a message whose sender is a LID (@lid) is
#       identified as the owner when the LID was learned for the owner
#       phone (via onWhatsApp lookup_result), and routed to the owner
#       path (brain.ask_with_tools, owner_unlocked=True); a LID mapped
#       to a non-owner phone routes to the guest/chat path (brain.ask,
#       no tools). The owner LID alone identifies the owner even when
#       the phone mapping is gone, and a LID seen together with a phone
#       teaches the persistent cache (a fresh service reloads it).
#       A message with no identifiable sender is still ignored - but
#       the masked sender fields + key shape are now logged.
#   (b) a non-UTF-8 byte (0x81) in the sidecar stdout stream does not
#       kill the reader: the bad line is skipped and later events are
#       still delivered (same for the stderr reader).
#   (c) a sidecar exit captures its exit code + stderr tail in the
#       state/log, the restart attempt counter increments across
#       restarts with real backoff delays (1s, 2s, 5s, 10s), and after
#       5 crashes the sidecar is DEAD (status "dead", reason exposed)
#       and is not restarted again.
#   (d) send_message() against a FAKE sidecar (a small Node script
#       speaking the same stdio JSON protocol): the fake receives the
#       correct payload (normalized JID + text) and Python reports
#       success ONLY after the fake's {"type":"sent"}; a Baileys-style
#       send_error and a silent sidecar (timeout) both report ok=False;
#       a down / not-linked sidecar reports ok=False without sending.
#
# NOT proven here (needs real Windows + a live WhatsApp link): the real
# Baileys socket delivering a LID message, the real onWhatsApp lookup
# against WhatsApp's servers, the real QR scan. Those are field tests.

import io
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

import brain  # noqa: E402
import whatsapp  # noqa: E402

OWNER_LOCAL = "01557887719"            # -> 201557887719
OWNER_PHONE = "201557887719"
OWNER_LID = "123456789012345@lid"
GUEST_PHONE = "201000000001"
GUEST_LID = "999888777666555@lid"


def _svc(testcase, tmp, **settings):
    """A service whose LID cache lives in tmp (never the real data dir).

    The DATA_DIR patch stays up for the whole test (addCleanup): the
    cache path is resolved per access, so a patch that ends after
    construction would send later saves to the real data dir."""
    base = {"whatsapp_enabled": True, "ai_name": "Memo",
            "user_name": "Mohamed", "language": "ar",
            "whatsapp_owner_phone": OWNER_LOCAL}
    base.update(settings)
    patcher = mock.patch.object(whatsapp, "DATA_DIR", Path(tmp))
    patcher.start()
    testcase.addCleanup(patcher.stop)
    return whatsapp.WhatsAppService(base)


class TestLidSenderIdentification(unittest.TestCase):
    def test_owner_lid_from_lookup_routes_to_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            # Startup learning: onWhatsApp(owner phone) -> owner LID.
            svc._on_event({"event": "lookup_result", "results": [
                {"jid": f"{OWNER_PHONE}@s.whatsapp.net", "exists": True,
                 "lid": OWNER_LID}]})
            self.assertEqual(svc._owner_lid, OWNER_LID)
            self.assertEqual(svc._lid_to_phone.get(OWNER_LID), OWNER_PHONE)
            sends = []
            svc._send_raw = lambda obj: sends.append(obj) or True
            captured = {}

            def fake_ask(text, ai_name, user_name, lang, history=None,
                         owner_unlocked=False):
                captured["owner_unlocked"] = owner_unlocked
                return {"reply": "تمام.", "engine": "groq",
                        "tools_used": 0, "tools_disabled": False}

            # The v1.4.1 failure, replayed: sender is a bare LID only.
            with mock.patch.object(brain, "ask_with_tools",
                                   side_effect=fake_ask):
                svc._handle_message({"from": OWNER_LID,
                                     "text": "عايز الكود المصدري",
                                     "id": "m-lid-owner"})
            self.assertIs(captured.get("owner_unlocked"), True,
                          "LID owner must reach the owner path")
            self.assertEqual(len(sends), 1, "owner gets a reply")

    def test_owner_lid_identifies_owner_without_phone_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            svc._learn_owner_lid(OWNER_LID)
            svc._lid_to_phone.clear()  # mapping lost; LID alone remains
            ident = svc.identify_sender({"from": OWNER_LID, "text": "hi"})
            self.assertEqual(ident["via"], "owner_lid")
            self.assertTrue(ident["is_owner"])
            self.assertEqual(ident["phone"], OWNER_PHONE)

    def test_lid_guest_routes_to_chat_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            # WhatsApp pairing the guest LID with its phone number.
            svc._on_event({"event": "phone_share", "lid": GUEST_LID,
                           "jid": f"{GUEST_PHONE}@s.whatsapp.net"})
            self.assertEqual(svc._lid_to_phone.get(GUEST_LID), GUEST_PHONE)
            sends = []
            svc._send_raw = lambda obj: sends.append(obj) or True
            with mock.patch.object(brain, "ask",
                                   return_value={"reply": "أهلاً.",
                                                 "engine": "groq"}), \
                    mock.patch.object(brain, "ask_with_tools") as m_tools:
                svc._handle_message({"from": GUEST_LID,
                                     "text": "hello", "id": "m-lid-guest"})
            m_tools.assert_not_called()
            self.assertEqual(len(sends), 1)

    def test_lid_plus_phone_in_one_message_teaches_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            ident = svc.identify_sender(
                {"from": GUEST_LID,
                 "from_alt": f"{GUEST_PHONE}@s.whatsapp.net"})
            self.assertEqual(ident["phone"], GUEST_PHONE)
            self.assertFalse(ident["is_owner"])
            self.assertEqual(ident["via"], "phone")
            self.assertEqual(svc._lid_to_phone.get(GUEST_LID), GUEST_PHONE)
            # ...and the cache survives a restart (fresh service).
            svc2 = _svc(self, tmp)
            self.assertEqual(svc2._lid_to_phone.get(GUEST_LID), GUEST_PHONE)
            self.assertEqual(
                svc2.identify_sender({"from": GUEST_LID})["phone"],
                GUEST_PHONE)

    def test_chat_remotejid_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            # No sender field at all, only the 1:1 chat JID.
            ident = svc.identify_sender(
                {"remoteJid": f"{GUEST_PHONE}@s.whatsapp.net"})
            self.assertEqual(ident["phone"], GUEST_PHONE)
            self.assertEqual(ident["via"], "chat_remoteJid")

    def test_unidentifiable_still_ignored_but_logged_masked(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            with mock.patch.object(whatsapp, "warn") as m_warn, \
                    mock.patch.object(brain, "ask") as m_ask, \
                    mock.patch.object(brain, "ask_with_tools") as m_tools:
                svc._handle_message(
                    {"from": "777777777777777@lid",
                     "key": {"id": "abc", "remoteJid": "777777777777777@lid"},
                     "text": "hello", "id": "m-nobody"})
            m_ask.assert_not_called()
            m_tools.assert_not_called()
            self.assertTrue(m_warn.called)
            logged = " ".join(str(c.args[1]) for c in m_warn.call_args_list)
            self.assertIn("unidentifiable sender", logged)
            self.assertIn("key=[id,remoteJid]", logged)
            # Masked only: full LID / full phone must never appear.
            self.assertNotIn("777777777777777", logged)
            self.assertNotIn(OWNER_PHONE, logged)

    def test_masking_helpers(self):
        self.assertEqual(whatsapp.mask_jid(f"{OWNER_PHONE}@s.whatsapp.net"),
                         "…19@s.whatsapp.net")
        self.assertEqual(whatsapp.mask_jid(OWNER_LID), "…45@lid")
        self.assertEqual(whatsapp.mask_number(OWNER_PHONE), "…19")
        self.assertEqual(whatsapp.normalize_phone("01557887719"),
                         OWNER_PHONE)


class TestUtf8Reader(unittest.TestCase):
    class _FakeProc:
        def __init__(self, stdout_lines, stderr_lines=()):
            self.stdout = stdout_lines
            self.stderr = stderr_lines
            self.stdin = None

    def test_bad_byte_does_not_kill_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            # 0x81 is the exact byte that killed the v1.4.1 reader
            # (cp1252 'charmap' codec cannot decode it).
            lines = [
                b'{"event": "qr", "qr": "data:image/png;base64,QRDATA"}\n',
                b"\x81 not utf-8 at all\n",
                b'{"event": "pong"}\n',
                b'{"event": "qr", "qr": "data:image/png;base64,QR2"}\n',
            ]
            svc._proc = self._FakeProc(iter(lines))
            svc._reader_loop()  # returns when the stream is exhausted
            st = svc.get_state()
            self.assertEqual(st["status"], "waiting_qr")
            self.assertEqual(st["qr"], "data:image/png;base64,QR2",
                             "events AFTER the bad byte must still arrive")

    def test_decode_line_replaces_bad_bytes(self):
        self.assertEqual(whatsapp.WhatsAppService._decode_line(b"\x81abc"),
                         "\ufffdabc")
        self.assertEqual(whatsapp.WhatsAppService._decode_line("plain"),
                         "plain")

    def test_bad_byte_in_stderr_does_not_kill_stderr_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            svc._proc = self._FakeProc(
                [], [b"[sidecar:error] boom \x81\n",
                     b"[sidecar:info] still alive\n"])
            svc._stderr_loop()
            self.assertIn("still alive", svc.get_state()["last_stderr"])


class TestSidecarSupervision(unittest.TestCase):
    class _FakeProc:
        returncode = 3

        def wait(self, timeout=None):
            return self.returncode

    def test_exit_captures_code_and_stderr_counter_increments(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            svc._stderr_tail.append("sidecar boom line 1")
            svc._stderr_tail.append("sidecar boom line 2")
            svc._proc = self._FakeProc()
            svc._proc.returncode = 3
            starts = []
            sleeps = []
            with mock.patch.object(svc, "start",
                                  side_effect=lambda **k: starts.append(k)
                                  or True), \
                    mock.patch.object(time, "sleep",
                                      side_effect=lambda s: sleeps.append(s)):
                svc._watchdog_loop()
            self.assertEqual(svc._restarts, 1)
            self.assertEqual(sleeps, [1.0], "first backoff is 1s")
            self.assertEqual(starts, [{"reset_restarts": False}],
                             "watchdog restarts must NOT reset the counter")
            st = svc.get_state()
            self.assertIn("3", st["error"], "exit code must be in the error")
            self.assertIn("sidecar boom line 2", st["last_stderr"])
            self.assertFalse(st["sidecar_dead"])

    def test_backoff_grows_then_dead_after_five(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            starts, sleeps = [], []
            with mock.patch.object(svc, "start",
                                  side_effect=lambda **k: starts.append(k)
                                  or True), \
                    mock.patch.object(time, "sleep",
                                      side_effect=lambda s: sleeps.append(s)):
                for _ in range(5):
                    svc._proc = self._FakeProc()
                    svc._stderr_tail.append("crash: something failed")
                    svc._watchdog_loop()
            self.assertEqual(svc._restarts, 5)
            self.assertEqual(sleeps, [1.0, 2.0, 5.0, 10.0],
                             "backoff 1s/2s/5s/10s, then dead (no 5th sleep)")
            self.assertEqual(len(starts), 4, "only 4 restarts, then dead")
            st = svc.get_state()
            self.assertEqual(st["status"], "dead")
            self.assertTrue(st["sidecar_dead"])
            self.assertIn("5 times", st["dead_reason"])
            self.assertIn("3", st["dead_reason"])
            # A dead sidecar is not restarted by the watchdog path.
            self.assertIs(svc.start(reset_restarts=False), False)
            # ...but a user start (re-enable / app restart) clears it.
            with mock.patch.object(whatsapp.WhatsAppService, "_find_node",
                                   return_value=None):
                svc.start(reset_restarts=True)
            self.assertFalse(svc._sidecar_dead)
            self.assertEqual(svc._restarts, 0)


_FAKE_SIDECAR_JS = r"""
const fs = require("node:fs");
const readline = require("node:readline");
const record = process.argv[2];
const mode = process.argv[3] || "ok";   // ok | fail | silent
const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  let cmd;
  try { cmd = JSON.parse(line); } catch { return; }
  if (cmd.type === "send") {
    fs.appendFileSync(record, JSON.stringify(cmd) + "\n");
    if (mode === "silent") return;
    if (mode === "fail") {
      process.stdout.write(JSON.stringify(
        { type: "send_error", req_id: cmd.req_id,
          error: "boom: baileys send failed" }) + "\n");
      return;
    }
    process.stdout.write(JSON.stringify(
      { type: "sent", req_id: cmd.req_id, to: cmd.to,
        id: "wamid.FAKE123" }) + "\n");
  } else if (cmd.type === "lookup") {
    process.stdout.write(JSON.stringify(
      { type: "lookup_result", req_id: cmd.req_id,
        results: [{ jid: cmd.jids[0], exists: true,
                    lid: "123456789012345@lid" }] }) + "\n");
  } else if (cmd.cmd === "stop") {
    process.exit(0);
  }
});
"""


class TestConfirmedSend(unittest.TestCase):
    def _spawn_fake(self, tmp, mode="ok"):
        script = Path(tmp) / f"fake-sidecar-{mode}.js"
        script.write_text(_FAKE_SIDECAR_JS, encoding="utf-8")
        record = Path(tmp) / f"received-{mode}.jsonl"
        proc = subprocess.Popen(
            ["node", str(script), str(record), mode],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
            encoding="utf-8", errors="replace")
        return proc, record

    def _wire(self, svc, proc):
        """Attach a live fake process as the service's sidecar."""
        svc._proc = proc
        svc._stop.clear()
        threading.Thread(target=svc._reader_loop, daemon=True).start()
        threading.Thread(target=svc._stderr_loop, daemon=True).start()
        # Link it: the fake answers the owner lookup, proving the
        # ready -> onWhatsApp learning path end to end.
        svc._on_event({"event": "ready",
                       "me": {"id": "201234567890@s.whatsapp.net",
                              "name": "Memo"}})
        deadline = time.time() + 5
        while time.time() < deadline and not svc._owner_lid:
            time.sleep(0.05)
        self.assertEqual(svc._owner_lid, OWNER_LID)

    def test_send_reports_success_only_after_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            proc, record = self._spawn_fake(tmp, "ok")
            try:
                self._wire(svc, proc)
                res = svc.send_message(OWNER_LOCAL, "أهلاً يا محمد")
                self.assertEqual(res, {"ok": True, "detail": "wamid.FAKE123"})
                payloads = [json.loads(ln) for ln in
                            record.read_text(encoding="utf-8").splitlines()]
                self.assertEqual(len(payloads), 1)
                self.assertEqual(payloads[0]["to"],
                                 f"{OWNER_PHONE}@s.whatsapp.net",
                                 "phone must be normalized to a JID")
                self.assertEqual(payloads[0]["text"], "أهلاً يا محمد")
                self.assertIn("req_id", payloads[0])
            finally:
                svc.stop()

    def test_send_error_reports_baileys_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            proc, _record = self._spawn_fake(tmp, "fail")
            try:
                self._wire(svc, proc)
                res = svc.send_message(OWNER_LOCAL, "hi")
                self.assertFalse(res["ok"])
                self.assertIn("boom", res["detail"])
            finally:
                svc.stop()

    def test_send_timeout_when_sidecar_goes_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            svc._send_timeout = 0.5
            proc, _record = self._spawn_fake(tmp, "silent")
            try:
                self._wire(svc, proc)
                res = svc.send_message(OWNER_LOCAL, "hi")
                self.assertFalse(res["ok"])
                self.assertIn("timed out", res["detail"])
            finally:
                svc.stop()

    def test_send_with_sidecar_down_and_not_linked(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            res = svc.send_message(OWNER_LOCAL, "hi")
            self.assertEqual(res["ok"], False)
            self.assertIn("not running", res["detail"])
            # Process up but not linked: still refused, nothing sent.
            proc, record = self._spawn_fake(tmp, "ok")
            try:
                svc._proc = proc
                svc._status = "connecting"
                res = svc.send_message(OWNER_LOCAL, "hi")
                self.assertFalse(res["ok"])
                self.assertIn("not linked", res["detail"])
                self.assertFalse(record.exists())
            finally:
                svc.stop()
            # Bad inputs never touch the sidecar either.
            self.assertFalse(svc.send_message("", "hi")["ok"])
            self.assertFalse(svc.send_message(OWNER_LOCAL, "")["ok"])


class TestSidecarSelfTest(unittest.TestCase):
    def test_self_test_protocol_shapes(self):
        """The real sidecar's --self-test speaks the v1.4.2 shapes:
        type:send -> send_error (not connected), type:lookup ->
        lookup_result (empty), so the Python side can be exercised
        against the real file without WhatsApp."""
        sidecar = APP_DIR / "whatsapp_sidecar" / "sidecar.js"
        if not sidecar.is_file():
            self.skipTest("sidecar.js missing")
        proc = subprocess.Popen(
            ["node", str(sidecar), tempfile.mkdtemp(), "--self-test"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
            encoding="utf-8", errors="replace")
        try:
            proc.stdin.write(json.dumps(
                {"type": "send", "req_id": "r1", "to": "20155@s.whatsapp.net",
                 "text": "hi"}) + "\n")
            proc.stdin.write(json.dumps(
                {"type": "lookup", "req_id": "r2",
                 "jids": ["20155@s.whatsapp.net"]}) + "\n")
            proc.stdin.write(json.dumps({"cmd": "ping"}) + "\n")
            proc.stdin.write(json.dumps({"cmd": "stop"}) + "\n")
            proc.stdin.flush()
            out, _err = proc.communicate(timeout=20)
            events = [json.loads(ln) for ln in out.splitlines() if ln.strip()]
            by_req = {e.get("req_id"): e for e in events if e.get("req_id")}
            self.assertEqual(by_req["r1"]["type"], "send_error")
            self.assertIn("not connected", by_req["r1"]["error"])
            self.assertEqual(by_req["r2"]["type"], "lookup_result")
            self.assertEqual(by_req["r2"]["results"], [])
            self.assertTrue(any(e.get("event") == "pong" for e in events))
            self.assertTrue(any(e.get("event") == "qr" for e in events))
        finally:
            if proc.poll() is None:
                proc.kill()


if __name__ == "__main__":
    unittest.main()
