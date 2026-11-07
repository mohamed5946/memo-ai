# Memo AI v1.4.0 - WhatsApp linking (Baileys sidecar over stdio JSON lines).
#
# The WhatsApp leg runs as a small Node sidecar (app/whatsapp_sidecar/):
# Baileys speaks the WhatsApp Web multi-device protocol directly over a
# WebSocket - no browser, no Puppeteer - and authenticates as a LINKED
# DEVICE on the user's barely-used THIRD number, via a QR shown in the
# app's Settings. This module spawns the sidecar, reads its JSON-lines
# events, routes incoming messages to the brain, and sends the replies.
#
# CHANNEL RULES (agreed with the user):
#   * The linked account is the THIRD number. NEVER the work number,
#     NEVER the main number. The QR is scanned from WhatsApp on the
#     third number (Settings > Linked devices > Link a device).
#   * Messages FROM the main number (settings.whatsapp_owner_phone) get
#     OWNER mode: the brain WITH tools (ask_with_tools) - it can open
#     apps, make files, run commands on the laptop, exactly like owner
#     mode in the app. The number IS the authentication (a message can
#     only arrive from that SIM), so tool dialogs are auto-allowed in
#     this channel - but the destructive-command blocklist still applies
#     BEFORE any permission check, same as in-app chat.
#   * Messages from ANY other number get REPLY mode: chat only
#     (brain.ask, no tools), in the message's language.
#   * Groups, status broadcasts, self-messages and media are ignored.
#   * The session (Signal keys) lives in app/data/whatsapp_auth/ - local
#     only, never leaves the laptop, never in the source zip, never in
#     git. Reconnect is automatic (saved creds, no new QR) unless the
#     session is logged out/revoked - then a fresh QR is needed.
#
# PRIVACY: no phone number and no message content is ever written to
# the log. Logs carry only a masked sender (last 2 digits), the mode
# (owner/guest), char counts and the engine used.
#
# DIAGNOSTICS: category "whatsapp" from day one - link state, QR
# readiness, connect/disconnect with the exact reason, send failures.

import json
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import brain
import tools
from logger import error, info, warn
from settings_store import APP_DIR, DATA_DIR

SIDECAR_DIR = APP_DIR / "whatsapp_sidecar"
SIDECAR_JS = SIDECAR_DIR / "sidecar.js"
NODE_BUNDLED_WIN = SIDECAR_DIR / "node" / "node.exe"   # frozen installer
NODE_BUNDLED_POSIX = SIDECAR_DIR / "node" / "node"     # dev on Linux/macOS
AUTH_DIR = DATA_DIR / "whatsapp_auth"

STATUS_OFF = "off"
STATUS_CONNECTING = "connecting"
STATUS_WAITING_QR = "waiting_qr"
STATUS_LINKED = "linked"
STATUS_DISCONNECTED = "disconnected"
STATUS_ERROR = "error"

MAX_REPLY_CHARS = 4000
MAX_INCOMING_CHARS = 4000
_SEND_TIMEOUT = 30.0
_MAX_PROC_RESTARTS = 5


# ---------------------------------------------------------------------------
# Numbers (digits only, Egypt-aware). Never logged in full.
# ---------------------------------------------------------------------------

def normalize_number(raw: str) -> str:
    """Digits only, Egypt-aware: '01557887719' -> '201557887719',
    '+20 155 788 7719' -> '201557887719', '201557887719' stays."""
    digits = re.sub(r"\D", "", str(raw or ""))
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("01"):
        digits = "2" + digits          # 0155... -> 20155...
    elif len(digits) == 10 and digits.startswith("1"):
        digits = "20" + digits         # 155... -> 20155...
    return digits


def mask_number(digits: str) -> str:
    """Last 2 digits only, for logs: '…19'. Never the full number."""
    d = re.sub(r"\D", "", str(digits or ""))
    return f"…{d[-2:]}" if len(d) >= 2 else "…??"


def jid_to_number(jid: str) -> str:
    """'201557887719@s.whatsapp.net' -> '201557887719'. Returns '' for
    LID JIDs (@lid), groups (@g.us) and broadcasts - those carry no
    phone number (see from_alt handling in _handle_message)."""
    jid = str(jid or "")
    if not jid.endswith("@s.whatsapp.net"):
        return ""
    return re.sub(r"\D", "", jid.split("@")[0])


def _is_arabic(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06ff" for ch in text)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class WhatsAppService:
    def __init__(self, settings: dict):
        self.settings = settings
        self._lock = threading.Lock()
        self._proc = None
        self._stop = threading.Event()
        self._status = STATUS_OFF
        self._qr = None
        self._me = ""            # linked account JID (the 3rd number)
        self._me_name = ""
        self._last_error = ""
        self._restarts = 0
        self._msg_queue: queue.Queue = queue.Queue()
        self._seen_ids: set = set()
        self._worker = None
        # Brain histories: owner keeps his own (shared with no one);
        # each guest sender gets a small private history.
        self._owner_history: list = []
        self._guest_histories: dict = {}

    # -- state ---------------------------------------------------------------
    def enabled(self) -> bool:
        return bool(self.settings.get("whatsapp_enabled", False))

    def owner_number(self) -> str:
        return normalize_number(self.settings.get("whatsapp_owner_phone", ""))

    def get_state(self) -> dict:
        """Link state for the Settings UI + the brain (v1.3.2 system
        prompt reads this to answer truthfully about WhatsApp). The QR
        data URL is included when waiting for a scan; the linked number
        is masked (last 2 digits only)."""
        with self._lock:
            return {
                "enabled": self.enabled(),
                "running": self._proc is not None,
                "status": self._status,
                "qr": self._qr,
                "linked_phone": mask_number(jid_to_number(self._me))
                                if self._me else None,
                "owner_configured": bool(self.owner_number()),
                "error": self._last_error,
            }

    # -- lifecycle -----------------------------------------------------------
    def _find_node(self) -> str | None:
        """Bundled Node runtime first (frozen installer), then node on
        PATH (zip/dev installs). None = WhatsApp unavailable."""
        for cand in (NODE_BUNDLED_WIN, NODE_BUNDLED_POSIX):
            if cand.is_file():
                return str(cand)
        found = shutil.which("node")
        if found:
            return found
        return None

    def start(self) -> bool:
        """Spawn the sidecar (no-op if already running). Returns True if
        the process is up. Runs in background threads; never blocks."""
        with self._lock:
            if self._proc is not None:
                return True
            node = self._find_node()
            if node is None:
                self._status = STATUS_ERROR
                self._last_error = ("Node runtime missing (bundled node.exe "
                                    "not found and node is not on PATH)")
                error("whatsapp", self._last_error)
                return False
            if not SIDECAR_JS.is_file():
                self._status = STATUS_ERROR
                self._last_error = "sidecar.js missing from the install"
                error("whatsapp", self._last_error)
                return False
            AUTH_DIR.mkdir(parents=True, exist_ok=True)
            try:
                self._proc = subprocess.Popen(
                    [node, str(SIDECAR_JS), str(AUTH_DIR)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, bufsize=1,
                    cwd=str(SIDECAR_DIR))
            except Exception as exc:
                self._status = STATUS_ERROR
                self._last_error = f"could not start sidecar: {exc}"
                error("whatsapp", self._last_error)
                return False
            self._stop.clear()
            self._restarts = 0
            self._status = STATUS_CONNECTING
            self._qr = None
            info("whatsapp", "Sidecar started (Baileys) - waiting for link")
        threading.Thread(target=self._reader_loop, daemon=True,
                         name="wa-reader").start()
        threading.Thread(target=self._stderr_loop, daemon=True,
                         name="wa-stderr").start()
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._worker_loop,
                                            daemon=True, name="wa-worker")
            self._worker.start()
        threading.Thread(target=self._watchdog_loop, daemon=True,
                         name="wa-watchdog").start()
        return True

    def stop(self) -> None:
        """Stop the sidecar (graceful 'stop' command, then kill)."""
        proc = None
        with self._lock:
            proc = self._proc
            self._proc = None
            self._status = STATUS_OFF
            self._qr = None
        self._stop.set()
        if proc is not None:
            try:
                self._send_raw({"cmd": "stop"})
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        info("whatsapp", "Sidecar stopped")

    def logout(self) -> None:
        """Log out: revoke the linked device + wipe the local session, so
        the next connect shows a fresh QR. (Also clears AUTH_DIR.)"""
        with self._lock:
            proc = self._proc
        if proc is not None:
            try:
                self._send_raw({"cmd": "logout"})
                proc.wait(timeout=10)
            except Exception:
                pass
        self.stop()
        try:
            import shutil as _shutil
            _shutil.rmtree(AUTH_DIR, ignore_errors=True)
        except Exception:
            pass
        info("whatsapp", "Logged out - session wiped, fresh QR needed next time")

    # -- sidecar protocol ----------------------------------------------------
    def _send_raw(self, obj: dict) -> bool:
        """Write one JSON command line to the sidecar's stdin."""
        with self._lock:
            proc = self._proc
        if proc is None or proc.stdin is None:
            return False
        try:
            proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            return True
        except Exception as exc:
            warn("whatsapp", f"Could not write to sidecar stdin: {exc}")
            return False

    def _reader_loop(self) -> None:
        """Read stdout JSON-lines events from the sidecar."""
        with self._lock:
            proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                if self._stop.is_set():
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    warn("whatsapp", "Non-JSON line from sidecar (ignored)")
                    continue
                self._on_event(event)
        except Exception as exc:
            if not self._stop.is_set():
                warn("whatsapp", f"Sidecar stdout reader ended: {exc}")

    def _stderr_loop(self) -> None:
        """Sidecar diagnostics (stderr) -> the diagnostics log, capped."""
        with self._lock:
            proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                if self._stop.is_set():
                    break
                line = line.strip()
                if line:
                    info("whatsapp-sidecar", line[:300])
        except Exception:
            pass

    def _watchdog_loop(self) -> None:
        """If the sidecar process dies (crash), restart it a few times
        with backoff; then give up loudly (status=error) instead of
        spinning forever. (The sidecar reconnects to WhatsApp on its own;
        this only covers process crashes.)"""
        with self._lock:
            proc = self._proc
        if proc is None:
            return
        proc.wait()
        if self._stop.is_set():
            return
        with self._lock:
            self._proc = None
            self._restarts += 1
            restarts = self._restarts
        if restarts > _MAX_PROC_RESTARTS:
            with self._lock:
                self._status = STATUS_ERROR
                self._last_error = (f"sidecar crashed {restarts} times - "
                                    "not restarting (see log)")
            error("whatsapp", self._last_error)
            return
        warn("whatsapp", f"Sidecar process exited - restarting "
                         f"(attempt {restarts}/{_MAX_PROC_RESTARTS})")
        time.sleep(5)
        if not self._stop.is_set():
            self.start()

    def _on_event(self, event: dict) -> None:
        kind = event.get("event")
        with self._lock:
            if kind == "qr":
                self._qr = event.get("qr") or None
                self._status = STATUS_WAITING_QR
                info("whatsapp", "QR ready - scan from WhatsApp on the "
                                 "3rd number (Settings > Linked devices)")
            elif kind == "ready":
                me = event.get("me") or {}
                self._me = me.get("id", "")
                self._me_name = me.get("name", "")
                self._qr = None
                self._status = STATUS_LINKED
                self._last_error = ""
                info("whatsapp", "WhatsApp linked as "
                     f"{mask_number(jid_to_number(self._me))} "
                     f"({self._me_name or 'no name'})")
            elif kind == "message":
                self._msg_queue.put(event)
            elif kind == "disconnected":
                self._status = STATUS_DISCONNECTED
                self._last_error = str(event.get("reason", ""))[:200]
                warn("whatsapp", "Disconnected: "
                     f"{self._last_error} (sidecar reconnects)")
            elif kind == "logged_out":
                self._status = STATUS_OFF
                self._qr = None
                self._me = ""
                warn("whatsapp", "Logged out (session revoked) - "
                                 "fresh QR needed")
            elif kind == "error":
                self._last_error = str(event.get("error", ""))[:200]
                warn("whatsapp", f"Sidecar error: {self._last_error}")
            elif kind == "log":
                level = event.get("level", "info")
                msg = str(event.get("msg", ""))[:300]
                if level == "error":
                    error("whatsapp-sidecar", msg)
                elif level == "warn":
                    warn("whatsapp-sidecar", msg)
                else:
                    info("whatsapp-sidecar", msg)
            # 'sent' / 'pong': no action needed.

    # -- message handling ----------------------------------------------------
    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._msg_queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self._handle_message(event)
            except Exception as exc:
                error("whatsapp", f"Message handling failed: {exc}")

    def _handle_message(self, event: dict) -> None:
        msg_id = str(event.get("id", ""))
        if msg_id:
            if msg_id in self._seen_ids:
                return
            self._seen_ids.add(msg_id)
            if len(self._seen_ids) > 200:
                self._seen_ids.clear()
        # Prefer the phone-number JID (remoteJidAlt) when Baileys
        # delivered a LID JID as remoteJid.
        sender = jid_to_number(event.get("from_alt") or "") or \
            jid_to_number(event.get("from") or "")
        if not sender:
            warn("whatsapp", "Message from an unidentifiable sender - ignored")
            return
        text = str(event.get("text", "") or "").strip()
        if not text or len(text) > MAX_INCOMING_CHARS:
            return
        owner = self.owner_number()
        lang = "ar" if _is_arabic(text) else \
            self.settings.get("language", "ar")
        ai_name = self.settings.get("ai_name", "Memo")
        user_name = self.settings.get("user_name", "Mohamed")

        if owner and sender == owner:
            # OWNER mode: tools on. The number IS the authentication, so
            # the tool dialogs are auto-allowed in this thread (see
            # tools.trusted_caller); the destructive blocklist still
            # applies before any permission check.
            # v1.4.1: the owner phone IS owner mode - the model's
            # source-code instruction flips (it answers source-code
            # questions instead of refusing), same as the in-app code
            # unlock. Guests (below) keep the default False.
            with tools.trusted_caller():
                result = brain.ask_with_tools(
                    text, ai_name, user_name, lang,
                    history=self._owner_history, owner_unlocked=True)
            reply = result.get("reply", "")
            engine = result.get("engine", "")
            mode = "owner"
        else:
            # REPLY mode: chat only, no tools, private per-sender history
            # (never the shared in-app history - other people must not
            # see the owner's chats, and vice versa).
            hist = self._guest_histories.setdefault(sender, [])
            result = brain.ask(text, ai_name, user_name, lang, history=hist)
            self._guest_histories[sender] = hist[-20:]
            if len(self._guest_histories) > 8:  # cap tracked senders
                oldest = next(iter(self._guest_histories))
                del self._guest_histories[oldest]
            reply = result.get("reply", "")
            engine = result.get("engine", "")
            mode = "guest"
            if not owner:
                warn("whatsapp", "Message arrived but no owner number is "
                                 "set - replying as guest (set your main "
                                 "number in Settings to enable owner mode)")

        reply = (reply or "").strip()
        # v1.4.1: engine is "" when no engine answered (never "none").
        if not engine or not reply:
            reply = ("مش قادر أفكر دلوقتي — المخ مش متاح، جرّب تاني كمان شوية."
                     if lang == "ar" else
                     "I can't think right now — the brain is unavailable, try again shortly.")
        reply = reply[:MAX_REPLY_CHARS]
        info("whatsapp", f"Message from {mode} sender {mask_number(sender)}, "
                         f"{len(text)} chars -> reply {len(reply)} chars "
                         f"(engine {engine})")
        ok = self._send_raw({"cmd": "send", "to": event.get("from", ""),
                             "text": reply, "reply_to": msg_id or None})
        if not ok:
            error("whatsapp", "Could not send WhatsApp reply "
                              "(sidecar not running)")


# ---------------------------------------------------------------------------
# Module-level singleton (wired by main.py; safe to call before start)
# ---------------------------------------------------------------------------

_SERVICE: WhatsAppService | None = None


def start_service(settings: dict) -> WhatsAppService | None:
    """Create the service (once) and start the sidecar if WhatsApp is
    enabled in settings. Safe to call repeatedly."""
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = WhatsAppService(settings)
    if _SERVICE.enabled():
        _SERVICE.start()
    return _SERVICE


def ensure_started(settings: dict) -> WhatsAppService | None:
    """Create the service if needed and start it if enabled (used by the
    Settings UI after the user turns WhatsApp on)."""
    svc = start_service(settings)
    if svc is not None and svc.enabled():
        svc.start()
    return svc


def get_service() -> WhatsAppService | None:
    return _SERVICE


def stop_service() -> None:
    global _SERVICE
    if _SERVICE is not None:
        _SERVICE.stop()
        _SERVICE = None


def get_state() -> dict:
    """Link state for the brain (v1.3.2 system prompt) + Settings UI.
    Safe to call before any service exists."""
    if _SERVICE is None:
        return {"enabled": False, "running": False, "status": STATUS_OFF,
                "qr": None, "linked_phone": None,
                "owner_configured": False, "error": ""}
    return _SERVICE.get_state()
