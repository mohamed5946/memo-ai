# Memo AI v1.4.3 - headless Bad MAC decrypt-storm tests (run on Linux,
# no Windows, no real WhatsApp, no network).
#
#   python -m unittest app.tests.test_v143_badmac -v
#   python -m unittest discover -s app/tests -v   (also runs these)
#
# What is proven here (every v1.4.3 Bad MAC scope item):
#   (a) SIDECAR, REAL FILE (--self-test-decrypt): a simulated storm —
#       12 libsignal Bad MAC console.error lines (with the field log's
#       session id 231864564510752.0) + 6 distinct CIPHERTEXT stubs
#       from one sender + 2 re-deliveries of the same id — produces a
#       BOUNDED result: exactly 6 decrypt_failed events (re-deliveries
#       silent, count not inflated), exactly ONE re-key attempt
#       (assertSessions called once, with the sender JID + force=True),
#       exactly ONE decrypt_degraded event (needs_relink=True), and a
#       decrypt_recovered event after one good message. stderr carries
#       2 masked Bad MAC summary lines (one per session-key) instead of
#       24 raw lines — the raw session id never appears, and the raw
#       "Failed to decrypt message with any known session..." line
#       never passes through. The process exits promptly (no loop).
#   (b) PYTHON aggregation: decrypt_failed events count up, the link
#       stays "linked" below the threshold and flips to status
#       "decrypt_failed" (degraded, needs_relink) at 5 failures; a
#       decrypt_degraded event degrades immediately; decrypt_recovered
#       re-greens the link and clears the counters; a "ready" event
#       does NOT re-green a degraded link (same keys); logout() clears
#       the decrypt state. get_state() exposes the decrypt_* fields
#       (masked senders only — no full number anywhere).
#   (c) PYTHON stderr gate (defense in depth): repeated Bad MAC lines
#       log once (masked) and are then swallowed — stack-frame lines
#       right after a Bad MAC line are swallowed too — while unrelated
#       lines still pass through.
#   (d) SENDS STILL WORK WHILE DEGRADED: send_message() against a fake
#       sidecar reports success after the fake's {"type":"sent"} even
#       when the status is "decrypt_failed" (the socket is live; only
#       inbound decrypt is broken).
#   (e) BRAIN TRUTHFULNESS: capabilities.whatsapp_status_line() for a
#       degraded state says messages are NOT decrypting / never reach
#       the brain, and points at the re-link (ar + en).
#
# NOT proven here (needs real Windows + a live WhatsApp link): the real
# Baileys socket failing to decrypt a real message, the real
# assertSessions re-key healing a live session, the real QR re-link.
# Those are field tests.

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

import capabilities  # noqa: E402
import whatsapp  # noqa: E402

OWNER_LOCAL = "01557887719"            # -> 201557887719
GUEST_PHONE = "201000000001"
GUEST_JID = f"{GUEST_PHONE}@s.whatsapp.net"
# Senders in decrypt events are ALREADY masked by the sidecar — the
# Python side must never see (or log) a full number from this path.
GUEST_MASKED = "…01@s.whatsapp.net"
BAD_MAC_REASON = "No matching sessions found for message"
SESSION_ID = "231864564510752"         # field log's session id (masked)


def _svc(testcase, tmp, **settings):
    """A service whose LID cache lives in tmp (never the real data dir)."""
    base = {"whatsapp_enabled": True, "ai_name": "Memo",
            "user_name": "Mohamed", "language": "ar",
            "whatsapp_owner_phone": OWNER_LOCAL}
    base.update(settings)
    patcher = mock.patch.object(whatsapp, "DATA_DIR", Path(tmp))
    patcher.start()
    testcase.addCleanup(patcher.stop)
    return whatsapp.WhatsAppService(base)


def _link(svc):
    """Pretend the sidecar linked (no process needed for state tests)."""
    svc._on_event({"event": "ready",
                   "me": {"id": "201234567890@s.whatsapp.net",
                          "name": "Memo"}})
    assert svc._status == whatsapp.STATUS_LINKED


def _failed(svc, sender=GUEST_MASKED, reason=BAD_MAC_REASON):
    svc._on_event({"event": "decrypt_failed", "sender": sender,
                   "failures": svc._decrypt_failures + 1, "reason": reason})


class TestDecryptEvents(unittest.TestCase):
    def test_one_failure_counts_but_stays_linked(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            _link(svc)
            _failed(svc)
            st = svc.get_state()
            self.assertEqual(svc._status, whatsapp.STATUS_LINKED)
            self.assertFalse(st["decrypt_failed"])
            self.assertEqual(st["decrypt_failures"], 1)
            self.assertEqual(st["decrypt_senders"], {GUEST_MASKED: 1})
            self.assertFalse(st["decrypt_needs_relink"])
            self.assertEqual(st["decrypt_reason"], BAD_MAC_REASON)
            self.assertIsNotNone(st["decrypt_since"])

    def test_five_failures_degrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            _link(svc)
            for _ in range(5):
                _failed(svc)
            st = svc.get_state()
            self.assertEqual(svc._status, whatsapp.STATUS_DEGRADED)
            self.assertEqual(st["status"], "decrypt_failed")
            self.assertTrue(st["decrypt_failed"])
            self.assertTrue(st["decrypt_needs_relink"])
            self.assertEqual(st["decrypt_failures"], 5)
            self.assertIn("decrypt", st["error"].lower())
            # Masked sender only — no full number anywhere in the state.
            blob = json.dumps(st)
            self.assertNotIn(GUEST_PHONE, blob)

    def test_degraded_event_degrades_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            _link(svc)
            _failed(svc)
            svc._on_event({"event": "decrypt_degraded",
                           "sender": GUEST_MASKED, "failures": 1,
                           "needs_relink": True})
            self.assertEqual(svc._status, whatsapp.STATUS_DEGRADED)
            self.assertTrue(svc.get_state()["decrypt_needs_relink"])

    def test_rekey_event_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            _link(svc)
            svc._on_event({"event": "decrypt_rekey",
                           "sender": GUEST_MASKED, "attempt": 1,
                           "attempted": True, "ok": True})
            self.assertEqual(svc.get_state()["decrypt_rekeys"], 1)

    def test_recovered_regreens_and_clears(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            _link(svc)
            for _ in range(5):
                _failed(svc)
            self.assertEqual(svc._status, whatsapp.STATUS_DEGRADED)
            svc._on_event({"event": "decrypt_recovered",
                           "sender": GUEST_MASKED})
            st = svc.get_state()
            self.assertEqual(svc._status, whatsapp.STATUS_LINKED)
            self.assertFalse(st["decrypt_failed"])
            self.assertEqual(st["decrypt_failures"], 0)
            self.assertEqual(st["decrypt_senders"], {})
            self.assertFalse(st["decrypt_needs_relink"])
            self.assertEqual(st["error"], "")

    def test_ready_does_not_regreen_a_degraded_link(self):
        # A (re)connect reuses the SAME Signal keys — it cannot heal a
        # broken session, so it must not silently flip the status back
        # to green "linked".
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            _link(svc)
            for _ in range(5):
                _failed(svc)
            self.assertEqual(svc._status, whatsapp.STATUS_DEGRADED)
            svc._on_event({"event": "ready",
                           "me": {"id": "201234567890@s.whatsapp.net",
                                  "name": "Memo"}})
            self.assertEqual(svc._status, whatsapp.STATUS_DEGRADED)
            self.assertEqual(svc._decrypt_failures, 5)

    def test_logout_clears_decrypt_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            _link(svc)
            for _ in range(5):
                _failed(svc)
            svc.logout()
            st = svc.get_state()
            self.assertEqual(st["decrypt_failures"], 0)
            self.assertFalse(st["decrypt_needs_relink"])
            self.assertEqual(svc._status, whatsapp.STATUS_OFF)

    def test_state_fields_present_before_any_service(self):
        # Module-level get_state() with no service: same decrypt keys.
        with mock.patch.object(whatsapp, "_SERVICE", None):
            st = whatsapp.get_state()
        for key in ("decrypt_failed", "decrypt_failures",
                    "decrypt_senders", "decrypt_since", "decrypt_rekeys",
                    "decrypt_needs_relink", "decrypt_reason",
                    "decrypt_stderr_suppressed"):
            self.assertIn(key, st)


class TestStderrBadMacGate(unittest.TestCase):
    def test_repeated_bad_mac_logs_once_masked(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            line = ("Failed to decrypt message with any known session... "
                    f"[{SESSION_ID}.0]")
            log_it, text = svc._gate_decrypt_stderr_line(line)
            self.assertTrue(log_it)
            self.assertIn("Bad MAC", text)
            self.assertNotIn(SESSION_ID, text)
            # Repeats inside the window: swallowed, counted.
            for _ in range(5):
                log_it, _ = svc._gate_decrypt_stderr_line(
                    "Session error: Error: Bad MAC")
                self.assertFalse(log_it)
            self.assertEqual(svc._decrypt_stderr_suppressed, 5)

    def test_stack_frames_after_bad_mac_are_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            svc._gate_decrypt_stderr_line("Session error: Error: Bad MAC")
            log_it, _ = svc._gate_decrypt_stderr_line(
                "    at verifyMAC (libsignal/src/crypto.js:87:15)")
            self.assertFalse(log_it)

    def test_unrelated_lines_pass_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            log_it, text = svc._gate_decrypt_stderr_line(
                "connection closed (code 428): stream error")
            self.assertTrue(log_it)
            self.assertEqual(text, "connection closed (code 428): "
                                   "stream error")


# Minimal fake sidecar (same stdio JSON protocol as the real one;
# copied pattern from test_v142_whatsapp's _FAKE_SIDECAR_JS, trimmed
# to the confirmed-send path this test needs).
_FAKE_SIDECAR_JS = r"""
const fs = require("node:fs");
const readline = require("node:readline");
const record = process.argv[2];
const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  let cmd;
  try { cmd = JSON.parse(line); } catch { return; }
  if (cmd.type === "send") {
    fs.appendFileSync(record, JSON.stringify(cmd) + "\n");
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


class TestSendWhileDegraded(unittest.TestCase):
    def test_send_reports_success_when_degraded(self):
        """A degraded link still sends: the socket is live and the
        send path asserts a fresh session itself — only inbound
        decrypt is broken."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = _svc(self, tmp)
            script = Path(tmp) / "fake-sidecar.js"
            script.write_text(_FAKE_SIDECAR_JS, encoding="utf-8")
            record = Path(tmp) / "received.jsonl"
            proc = subprocess.Popen(
                ["node", str(script), str(record)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1,
                encoding="utf-8", errors="replace")
            try:
                svc._proc = proc
                svc._stop.clear()
                threading.Thread(target=svc._reader_loop,
                                 daemon=True).start()
                threading.Thread(target=svc._stderr_loop,
                                 daemon=True).start()
                _link(svc)
                for _ in range(5):
                    _failed(svc)
                self.assertEqual(svc._status, whatsapp.STATUS_DEGRADED)
                res = svc.send_message(GUEST_JID, "hello")
                self.assertTrue(res["ok"], res)
                self.assertEqual(res["detail"], "wamid.FAKE123")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        pass


class TestSidecarDecryptSelfTest(unittest.TestCase):
    def test_bad_mac_storm_is_bounded_and_masked(self):
        """The REAL sidecar file, --self-test-decrypt: 12 libsignal
        Bad MAC lines + 6 distinct CIPHERTEXT stubs + 2 same-id
        re-deliveries + 1 good message. Asserts: bounded events
        (6 failed / 1 re-key / 1 degraded / 1 recovered), the re-key
        called assertSessions once with (sender JID, force=True),
        stderr has 2 masked Bad MAC lines and never the raw session
        id, and the process exits (no infinite loop)."""
        sidecar = APP_DIR / "whatsapp_sidecar" / "sidecar.js"
        if not sidecar.is_file():
            self.skipTest("sidecar.js missing")
        proc = subprocess.Popen(
            ["node", str(sidecar), tempfile.mkdtemp(),
             "--self-test-decrypt"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
            encoding="utf-8", errors="replace")
        try:
            out, err = proc.communicate(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
        self.assertEqual(proc.returncode, 0)
        events = [json.loads(ln) for ln in out.splitlines() if ln.strip()]
        by_kind = {}
        for e in events:
            by_kind.setdefault(e.get("event"), []).append(e)
        failed = by_kind.get("decrypt_failed", [])
        self.assertEqual(len(failed), 6,
                         "re-deliveries must not emit decrypt_failed")
        self.assertEqual([e["failures"] for e in failed], [1, 2, 3, 4, 5, 6])
        self.assertTrue(all(e["sender"] == GUEST_MASKED for e in failed))
        # Masking holds where it must: every decrypt_* sender and every
        # log line carries the masked sender only. (The real JID
        # legitimately appears in the "message" event's from/remoteJid
        # — v1.4.2 passes raw sender fields through for LID
        # identification — and in rekey_calls, which is the actual
        # assertSessions argument, not a log.)
        for e in events:
            if e.get("event", "").startswith("decrypt_"):
                self.assertEqual(e.get("sender"), GUEST_MASKED)
            if e.get("event") == "log":
                self.assertNotIn(GUEST_PHONE, e.get("msg", ""))
        rekeys = by_kind.get("decrypt_rekey", [])
        self.assertEqual(len(rekeys), 1, "exactly one forced re-key")
        self.assertTrue(rekeys[0]["attempted"])
        self.assertTrue(rekeys[0]["ok"])
        degraded = by_kind.get("decrypt_degraded", [])
        self.assertEqual(len(degraded), 1)
        self.assertTrue(degraded[0]["needs_relink"])
        self.assertEqual(degraded[0]["failures"], 5)
        recovered = by_kind.get("decrypt_recovered", [])
        self.assertEqual(len(recovered), 1)
        # The good message itself still delivered (the healed id was
        # never marked seen while it was a stub).
        self.assertTrue(any(e.get("event") == "message" and
                            e.get("id") == "good-1" for e in events))
        done = by_kind.get("self_test_decrypt_done", [])
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["rekey_calls"],
                         [{"jids": [GUEST_JID], "force": True}])
        # stderr: the flood is gated to 2 masked summary lines (one
        # per session-key); the raw session id never appears, and the
        # raw libsignal line never passes through.
        self.assertNotIn(SESSION_ID, err)
        self.assertNotIn("Failed to decrypt message with any known "
                         "session", err)
        bad_mac_lines = [ln for ln in err.splitlines() if "Bad MAC" in ln]
        self.assertEqual(len(bad_mac_lines), 2)
        self.assertTrue(all("…52" in ln or "unknown" in ln
                            for ln in bad_mac_lines))


class TestCapabilitiesDegradedLine(unittest.TestCase):
    def test_degraded_line_is_truthful_ar_and_en(self):
        degraded = {"status": "decrypt_failed", "decrypt_failures": 5,
                    "linked_phone": "…78"}
        with mock.patch.object(capabilities, "_live_whatsapp_state",
                               return_value=degraded):
            ar = capabilities.whatsapp_status_line("ar")
            en = capabilities.whatsapp_status_line("en")
        for line in (ar, en):
            self.assertIn("5", line)
            self.assertNotIn("✅", line)  # never a green "linked" claim
        self.assertIn("مش بتتفك", ar)
        self.assertIn("إعادة الربط", ar)
        self.assertIn("NOT decrypting", en)
        self.assertIn("re-link", en)


if __name__ == "__main__":
    unittest.main()
