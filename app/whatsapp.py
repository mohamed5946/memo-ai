# Memo AI v1.4.2 - WhatsApp linking (Baileys sidecar over stdio JSON lines).
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
#
# v1.4.2 - SENDER IDENTIFICATION (the v1.4.1 field failure):
#   WhatsApp now sometimes delivers the sender as a LID JID
#   ("123456789012345@lid") instead of a phone JID
#   ("201557887719@s.whatsapp.net"), and v1.4.1 dropped every such
#   message as "unidentifiable". Baileys 6.7.24 offers NO public
#   LID->phone API (no lidMapping / getPNForLID; SignalRepository has
#   no LID methods; remoteJidAlt is a proto field that may arrive on
#   the wire; USyncUser.withLid() exists but the shipped protocols do
#   not convey it). The ONLY direction Baileys exposes is phone->LID:
#   sock.onWhatsApp(...jids) returns {jid, exists, lid}. So this
#   module identifies senders from EVERY observable source and
#   persists what it learns in app/data/lid_cache.json (local only):
#     1. any @s.whatsapp.net JID in the message (from / from_alt /
#        participant / participantAlt / key.* / remoteJidAlt) - the
#        phone, when WhatsApp supplies it alongside the LID;
#     2. a persistent LID->phone cache, learned from (a) onWhatsApp
#        lookups of the owner phone at link time, (b) any inbound
#        message where a LID and a phone are observable together,
#        (c) Baileys' chats.phoneNumberShare event, which WhatsApp
#        itself sends to pair a LID with its phone number;
#     3. the OWNER'S OWN LID, learned once via onWhatsApp(owner
#        phone) and persisted: a message whose sender LID matches it
#        IS the owner even if no phone mapping exists for it;
#     4. the chat remoteJid as a last resort when no sender field
#        identifies anyone (1:1 chats: the chat IS the sender).
#   Only when ALL of those fail is the message ignored - and then the
#   raw sender fields are logged MASKED (last 2 digits) plus the
#   message-key shape, so the next failure can be diagnosed instead
#   of guessed at.
#
# v1.4.2 - UTF-8 STDOUT READER: the sidecar's stdout/stderr are read
#   as UTF-8 with errors='replace' (v1.4.1 used the Windows locale
#   codec, so one 0x81 byte killed the reader thread and no WhatsApp
#   event ever reached the app again). The reader also decodes raw
#   byte lines defensively and never dies on a single bad line.
#
# v1.4.2 - SIDECAR SUPERVISION: every exit logs its exit code plus
#   the last ~20 stderr lines; restarts back off 1s/2s/5s/10s/30s with
#   a counter that actually increments; after 5 crashes the sidecar
#   is marked DEAD (status "dead" in get_state(), with the reason)
#   and is NOT restarted again until the user re-enables WhatsApp or
#   the app restarts.
#
# v1.4.2 - CONFIRMED SEND: WhatsAppService.send_message(to, text)
#   sends via the sidecar and reports success ONLY after Baileys
#   confirms the send ({"type":"sent"}); anything else - sidecar
#   down, not linked, timeout, Baileys error - is ok=False with a
#   reason. Brain-initiated sends (tools.py) use this; the auto-reply
#   path uses it too when linked, and falls back to the legacy
#   fire-and-forget write when the service is not linked (so a reply
#   is still attempted if link state lags behind reality).

import collections
import json
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
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
LID_CACHE_FILENAME = "lid_cache.json"

STATUS_OFF = "off"
STATUS_CONNECTING = "connecting"
STATUS_WAITING_QR = "waiting_qr"
STATUS_LINKED = "linked"
STATUS_DISCONNECTED = "disconnected"
STATUS_ERROR = "error"
STATUS_DEAD = "dead"   # v1.4.2: sidecar crashed too often, not restarting

MAX_REPLY_CHARS = 4000
MAX_INCOMING_CHARS = 4000
_SEND_TIMEOUT = 30.0
_MAX_PROC_RESTARTS = 5
# v1.4.2: real backoff between crash restarts (attempt 1 -> 1s, ...).
_BACKOFF_DELAYS = (1.0, 2.0, 5.0, 10.0, 30.0)
_MAX_STDERR_TAIL = 20


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


# Back-compat alias: the task brief (and tools.py wiring) call it
# normalize_phone; the historic name is normalize_number.
normalize_phone = normalize_number


def mask_number(digits: str) -> str:
    """Last 2 digits only, for logs: '…19'. Never the full number."""
    d = re.sub(r"\D", "", str(digits or ""))
    return f"…{d[-2:]}" if len(d) >= 2 else "…??"


def mask_jid(jid: str) -> str:
    """Mask a JID for logs: '…19@s.whatsapp.net' / '…45@lid'.
    Never the full number, never the full LID."""
    jid = str(jid or "")
    if "@" not in jid:
        return mask_number(jid)
    user, domain = jid.split("@", 1)
    d = re.sub(r"\D", "", user)
    return f"…{d[-2:] if len(d) >= 2 else '??'}@{domain}"


def jid_to_number(jid: str) -> str:
    """'201557887719@s.whatsapp.net' -> '201557887719'. Returns '' for
    LID JIDs (@lid), groups (@g.us) and broadcasts - those carry no
    phone number (see identify_sender for the LID fallbacks)."""
    jid = str(jid or "")
    if not jid.endswith("@s.whatsapp.net"):
        return ""
    return re.sub(r"\D", "", jid.split("@")[0])


def _is_lid_jid(jid: str) -> bool:
    return str(jid or "").endswith("@lid")


def _normalize_lid(jid: str) -> str:
    """'123456789012345@lid' -> same, canonical. Bare digits that came
    from a LID field -> 'digits@lid'. Anything else -> ''."""
    jid = str(jid or "").strip()
    if not jid:
        return ""
    if jid.endswith("@lid"):
        digits = re.sub(r"\D", "", jid.split("@")[0])
        return f"{digits}@lid" if digits else ""
    return ""


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
        self._me_lid = ""        # linked account's own LID (if Baileys gives it)
        self._last_error = ""
        self._restarts = 0
        self._sidecar_dead = False
        self._stderr_tail: collections.deque = collections.deque(
            maxlen=_MAX_STDERR_TAIL)
        self._msg_queue: queue.Queue = queue.Queue()
        self._seen_ids: set = set()
        self._worker = None
        self._send_timeout = _SEND_TIMEOUT
        # Pending confirmed sends: req_id -> Queue[dict]. Filled by
        # _on_event on {"type":"sent"/"send_error"} from the sidecar.
        self._pending_sends: dict = {}
        # Brain histories: owner keeps his own (shared with no one);
        # each guest sender gets a small private history.
        self._owner_history: list = []
        self._guest_histories: dict = {}
        # v1.4.2: persistent LID -> phone cache + the owner's own LID.
        self._lid_lock = threading.Lock()
        self._lid_to_phone: dict = {}
        self._owner_lid = ""
        self._load_lid_cache()

    # -- LID cache (persistent, local only) ---------------------------------
    @property
    def _lid_cache_path(self) -> Path:
        # Resolved per access (not at import): tests point DATA_DIR at
        # a temp dir before constructing the service.
        return DATA_DIR / LID_CACHE_FILENAME

    def _load_lid_cache(self) -> None:
        try:
            path = self._lid_cache_path
            if not path.is_file():
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            mapping = data.get("lid_to_phone") or {}
            if isinstance(mapping, dict):
                self._lid_to_phone = {
                    _normalize_lid(k): normalize_number(v)
                    for k, v in mapping.items()
                    if _normalize_lid(k) and normalize_number(v)}
            owner_lid = _normalize_lid(data.get("owner_lid") or "")
            if owner_lid:
                self._owner_lid = owner_lid
        except Exception as exc:
            warn("whatsapp", f"Could not load LID cache, starting empty: {exc}")
            self._lid_to_phone = {}
            self._owner_lid = ""

    def _save_lid_cache(self) -> None:
        try:
            path = self._lid_cache_path
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(
                {"version": 1, "lid_to_phone": self._lid_to_phone,
                 "owner_lid": self._owner_lid},
                ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
        except Exception as exc:
            warn("whatsapp", f"Could not save LID cache: {exc}")

    def _learn_lid_mapping(self, lid_jid: str, phone: str) -> bool:
        """Record LID -> phone (persisted). True if it is new/changed."""
        lid = _normalize_lid(lid_jid)
        phone = normalize_number(phone)
        if not lid or not phone:
            return False
        with self._lid_lock:
            if self._lid_to_phone.get(lid) == phone:
                return False
            self._lid_to_phone[lid] = phone
            self._save_lid_cache()
        info("whatsapp", f"Learned LID mapping {mask_jid(lid)} -> "
                         f"phone {mask_number(phone)}")
        return True

    def _learn_owner_lid(self, lid_jid: str) -> bool:
        """Record the owner's own LID (persisted). True if new."""
        lid = _normalize_lid(lid_jid)
        if not lid:
            return False
        with self._lid_lock:
            if self._owner_lid == lid:
                return False
            self._owner_lid = lid
            self._save_lid_cache()
        info("whatsapp", f"Learned owner LID {mask_jid(lid)}")
        return True

    # -- state ---------------------------------------------------------------
    def enabled(self) -> bool:
        return bool(self.settings.get("whatsapp_enabled", False))

    def owner_number(self) -> str:
        return normalize_number(self.settings.get("whatsapp_owner_phone", ""))

    def get_state(self) -> dict:
        """Link state for the Settings UI + the brain (v1.3.2 system
        prompt reads this to answer truthfully about WhatsApp). The QR
        data URL is included when waiting for a scan; the linked number
        is masked (last 2 digits only). v1.4.2 adds the sidecar-dead
        flag/reason, the restart count and the last stderr tail, so
        the UI can show WHY WhatsApp is down instead of a blank."""
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
                # v1.4.2 supervision fields:
                "sidecar_dead": self._sidecar_dead,
                "dead_reason": self._last_error if self._sidecar_dead else "",
                "restarts": self._restarts,
                "last_stderr": "\n".join(self._stderr_tail),
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

    def start(self, reset_restarts: bool = True) -> bool:
        """Spawn the sidecar (no-op if already running). Returns True if
        the process is up. Runs in background threads; never blocks.
        reset_restarts=True (user start / re-enable) clears the crash
        counter and the dead flag; the watchdog restarts with
        reset_restarts=False so the counter actually increments."""
        with self._lock:
            if self._proc is not None:
                return True
            if self._sidecar_dead and not reset_restarts:
                return False
            if reset_restarts:
                # A user start (re-enable / app restart) clears the
                # crash counter and the dead flag up front, even if
                # this start itself then fails (e.g. node missing) -
                # the failure is reported via status=error instead.
                self._restarts = 0
                self._sidecar_dead = False
                self._last_error = ""
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
                # v1.4.2: UTF-8 with errors='replace' on BOTH streams.
                # text=True alone uses the Windows locale codec
                # (cp1252), so one non-UTF-8 byte (0x81 in the v1.4.1
                # log) raised in the reader and killed WhatsApp input
                # for the rest of the session.
                self._proc = subprocess.Popen(
                    [node, str(SIDECAR_JS), str(AUTH_DIR)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, bufsize=1,
                    encoding="utf-8", errors="replace",
                    cwd=str(SIDECAR_DIR))
            except Exception as exc:
                self._status = STATUS_ERROR
                self._last_error = f"could not start sidecar: {exc}"
                error("whatsapp", self._last_error)
                return False
            self._stop.clear()
            self._stderr_tail.clear()
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
            self._sidecar_dead = False
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

    @staticmethod
    def _decode_line(raw) -> str:
        """One stdout/stderr line as text. Bytes are decoded UTF-8
        with errors='replace' - a bad byte must NEVER kill the reader
        (v1.4.1: 0x81 under the cp1252 locale codec did exactly that)."""
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)

    def _reader_loop(self) -> None:
        """Read stdout JSON-lines events from the sidecar. NEVER dies
        on a bad byte or a bad line: each line is handled in its own
        try/except, so one corrupt line cannot silence WhatsApp."""
        with self._lock:
            proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for raw in proc.stdout:
                if self._stop.is_set():
                    break
                try:
                    line = self._decode_line(raw).strip()
                except Exception:
                    continue
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    warn("whatsapp", "Non-JSON line from sidecar (ignored)")
                    continue
                try:
                    self._on_event(event)
                except Exception as exc:
                    error("whatsapp", f"Sidecar event handling failed: {exc}")
        except Exception as exc:
            if not self._stop.is_set():
                warn("whatsapp", f"Sidecar stdout reader ended: {exc}")

    def _stderr_loop(self) -> None:
        """Sidecar diagnostics (stderr) -> the diagnostics log, capped.
        Also keeps the last ~20 lines for the watchdog, so a crash
        report can name the exit code AND what the sidecar said."""
        with self._lock:
            proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for raw in proc.stderr:
                if self._stop.is_set():
                    break
                try:
                    line = self._decode_line(raw).strip()
                except Exception:
                    continue
                if line:
                    with self._lock:
                        self._stderr_tail.append(line[:300])
                    info("whatsapp-sidecar", line[:300])
        except Exception:
            pass

    def _watchdog_loop(self) -> None:
        """If the sidecar process dies (crash), restart it a few times
        with REAL backoff (1s, 2s, 5s, 10s, 30s) and a counter that
        actually increments; then give up loudly (status=dead) instead
        of spinning forever. (The sidecar reconnects to WhatsApp on
        its own; this only covers process crashes.)"""
        with self._lock:
            proc = self._proc
        if proc is None:
            return
        try:
            proc.wait()
        except Exception:
            pass
        if self._stop.is_set():
            return
        with self._lock:
            if self._proc is not proc:
                return  # superseded by a newer sidecar process
            self._proc = None
            self._restarts += 1
            restarts = self._restarts
            stderr_tail = list(self._stderr_tail)
            try:
                exit_code = proc.returncode
            except Exception:
                exit_code = None
        tail_text = "\n".join(stderr_tail)
        reason = f"sidecar exited (code {exit_code})"
        if tail_text:
            reason += f"; last stderr: {tail_text[-1000:]}"
        with self._lock:
            self._last_error = reason[:500]
        error("whatsapp", reason)
        if restarts >= _MAX_PROC_RESTARTS:
            with self._lock:
                self._status = STATUS_DEAD
                self._sidecar_dead = True
                self._last_error = (
                    f"sidecar crashed {restarts} times "
                    f"(last exit code {exit_code}) - not restarting; "
                    "re-enable WhatsApp or restart the app")
            error("whatsapp", self._last_error)
            return
        delay = _BACKOFF_DELAYS[min(restarts - 1, len(_BACKOFF_DELAYS) - 1)]
        warn("whatsapp", f"Sidecar process exited (code {exit_code}) - "
                         f"restarting (attempt {restarts}/{_MAX_PROC_RESTARTS}) "
                         f"in {delay:g}s")
        time.sleep(delay)
        if not self._stop.is_set() and not self._sidecar_dead:
            self.start(reset_restarts=False)

    # -- LID learning via onWhatsApp -----------------------------------------
    def _request_lookup(self, jids: list) -> bool:
        """Ask the sidecar to run sock.onWhatsApp(...jids) (phone ->
        LID). The reply arrives as a lookup_result event."""
        jids = [j for j in (jids or []) if j]
        if not jids:
            return False
        return self._send_raw({"type": "lookup",
                               "req_id": uuid.uuid4().hex[:12],
                               "jids": jids})

    def _request_owner_lookup(self) -> bool:
        """Learn the owner's LID (and phone->LID mapping) once the
        socket is up. Called on the ready event: onWhatsApp needs a
        live socket, so asking at start() time would always fail."""
        owner = self.owner_number()
        if not owner:
            return False
        ok = self._request_lookup([f"{owner}@s.whatsapp.net"])
        if ok:
            info("whatsapp", "Requested onWhatsApp lookup for the owner "
                             f"number {mask_number(owner)} (learning its LID)")
        return ok

    def _on_lookup_result(self, event: dict) -> None:
        err = event.get("error")
        if err:
            warn("whatsapp", f"onWhatsApp lookup failed: {str(err)[:200]}")
            return
        for item in event.get("results") or []:
            if not isinstance(item, dict):
                continue
            phone = jid_to_number(item.get("jid") or "") or \
                normalize_number(item.get("jid") or "")
            lid = _normalize_lid(item.get("lid") or "")
            if lid and phone:
                self._learn_lid_mapping(lid, phone)
                if phone == self.owner_number():
                    self._learn_owner_lid(lid)

    def _on_event(self, event: dict) -> None:
        # v1.4.2: the sidecar emits both legacy {"event": ...} and new
        # {"type": ...} shapes; either names the kind.
        kind = event.get("event") or event.get("type")
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
                self._me_lid = _normalize_lid(me.get("lid") or "")
                self._qr = None
                self._status = STATUS_LINKED
                self._last_error = ""
                self._restarts = 0  # a live link is a healthy sidecar
                info("whatsapp", "WhatsApp linked as "
                     f"{mask_number(jid_to_number(self._me))} "
                     f"({self._me_name or 'no name'})")
            elif kind == "message":
                self._msg_queue.put(event)
            elif kind == "phone_share":
                # WhatsApp itself pairing a LID with its phone number.
                lid = _normalize_lid(event.get("lid") or "")
                phone = jid_to_number(event.get("jid") or "")
                if lid and phone:
                    self._learn_lid_mapping(lid, phone)
            elif kind == "lookup_result":
                self._on_lookup_result(event)
            elif kind in ("sent", "send_error"):
                req_id = event.get("req_id")
                if req_id and req_id in self._pending_sends:
                    self._pending_sends[req_id].put({
                        "ok": kind == "sent",
                        "id": event.get("id", ""),
                        "error": event.get("error", ""),
                    })
            elif kind == "disconnected":
                self._status = STATUS_DISCONNECTED
                self._last_error = str(event.get("reason", ""))[:200]
                warn("whatsapp", "Disconnected: "
                     f"{self._last_error} (sidecar reconnects)")
            elif kind == "logged_out":
                self._status = STATUS_OFF
                self._qr = None
                self._me = ""
                self._me_lid = ""
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
            # 'pong': no action needed.
        # The owner-LID lookup needs the socket, so it goes out AFTER
        # the ready event is fully recorded (and outside the lock, so
        # _send_raw's own lock acquisition cannot deadlock).
        if kind == "ready":
            self._request_owner_lookup()

    # -- confirmed send (v1.4.2) ---------------------------------------------
    def send_message(self, to, text, reply_to=None) -> dict:
        """Send a WhatsApp text message and report success ONLY after
        Baileys confirms the send.

        to: a digits phone ("01557887719", "+20 155...", "20155...")
            (normalized exactly like normalize_number) or a full JID
            ("20155...@s.whatsapp.net" / "123...@lid").
        text: the message text (non-empty).
        reply_to: optional message id to quote (internal replies only).

        Returns {"ok": bool, "detail": str}:
          ok=True  -> detail is the sent message id (or "sent").
          ok=False -> detail names the reason: "invalid recipient",
            "empty message text", "sidecar not running",
            "WhatsApp not linked (status=...)", "could not write to
            sidecar", "send timed out after 30s ...", or the Baileys
            error text. Numbers are masked in the log (last 2 digits).
        Waits up to ~30s for the sidecar's {"type":"sent"} /
        {"type":"send_error"}; call from a background thread, never
        from the UI thread."""
        text = str(text or "")
        if not text:
            return {"ok": False, "detail": "empty message text"}
        raw_to = str(to or "").strip()
        if "@" in raw_to:
            jid = raw_to
        else:
            digits = normalize_number(raw_to)
            if not digits:
                return {"ok": False, "detail": "invalid recipient"}
            jid = f"{digits}@s.whatsapp.net"
        with self._lock:
            proc = self._proc
            status = self._status
        if proc is None:
            return {"ok": False, "detail": "sidecar not running"}
        if status != STATUS_LINKED:
            return {"ok": False, "detail": f"WhatsApp not linked (status={status})"}
        req_id = uuid.uuid4().hex[:12]
        waiter: queue.Queue = queue.Queue()
        with self._lock:
            self._pending_sends[req_id] = waiter
        try:
            payload = {"type": "send", "req_id": req_id,
                       "to": jid, "text": text}
            if reply_to:
                payload["reply_to"] = reply_to
            if not self._send_raw(payload):
                return {"ok": False, "detail": "could not write to sidecar"}
            try:
                result = waiter.get(timeout=self._send_timeout)
            except queue.Empty:
                return {"ok": False,
                        "detail": f"send timed out after {self._send_timeout:g}s "
                                  "(no confirmation from sidecar)"}
            if result.get("ok"):
                info("whatsapp", f"WhatsApp message sent to {mask_jid(jid)} "
                                 f"(id {result.get('id') or '?'})")
                return {"ok": True, "detail": result.get("id") or "sent"}
            return {"ok": False,
                    "detail": str(result.get("error") or "send failed")[:200]}
        finally:
            with self._lock:
                self._pending_sends.pop(req_id, None)

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

    def identify_sender(self, event: dict) -> dict:
        """Identify the sender of an inbound message event.

        Returns {"phone": str, "is_owner": bool, "lid": str, "via": str}:
          phone: digits phone ("20155...") or "" if no phone is known.
          is_owner: True only when the sender is proven to be the owner
            (phone == owner number, or sender LID == learned owner LID).
          lid: the sender's LID JID ("...@lid") when one was observed.
          via: how it was identified - "phone", "lid_cache",
            "owner_lid", "chat_remoteJid", or "" when unidentified.

        Sources, in order (see the v1.4.2 header note for WHY):
          1. any @s.whatsapp.net JID among the sender fields the
             sidecar passes through (from / from_alt / participant /
             participantAlt / key.*) - a LID seen together with a phone
             also teaches the cache for next time;
          2. the persistent LID->phone cache for a @lid sender;
          3. the learned owner LID (owner even with no phone mapping);
          4. the chat remoteJid when no sender field identifies anyone
             (1:1 chats: the chat IS the sender).
        Never logs a full number or any message content."""
        if not isinstance(event, dict):
            return {"phone": "", "is_owner": False, "lid": "", "via": ""}
        key = event.get("key") if isinstance(event.get("key"), dict) else {}

        def _fields(*names):
            for name in names:
                value = event.get(name) or key.get(name)
                if value:
                    yield str(value)

        sender_phones = []
        sender_lids = []
        for value in _fields("from_alt", "participant_alt", "participant",
                             "from", "remoteJidAlt"):
            if value.endswith("@s.whatsapp.net"):
                sender_phones.append(jid_to_number(value))
            elif _is_lid_jid(value):
                sender_lids.append(_normalize_lid(value))
        for value in _fields("participantPn", "participant_pn"):
            phone = normalize_number(value)
            if phone:
                sender_phones.append(phone)
        for value in _fields("participantLid", "participant_lid"):
            lid = _normalize_lid(value)
            if lid:
                sender_lids.append(lid)

        chat_phones = []
        chat_lids = []
        for value in _fields("remoteJid",):
            if value.endswith("@s.whatsapp.net"):
                chat_phones.append(jid_to_number(value))
            elif _is_lid_jid(value):
                chat_lids.append(_normalize_lid(value))

        # A LID observed together with a phone teaches the cache.
        for lid in sender_lids + chat_lids:
            for phone in sender_phones + chat_phones:
                if lid and phone:
                    self._learn_lid_mapping(lid, phone)

        owner = self.owner_number()
        lid = sender_lids[0] if sender_lids else (
            chat_lids[0] if chat_lids else "")
        if sender_phones:
            phone = sender_phones[0]
            return {"phone": phone,
                    "is_owner": bool(owner and phone == owner),
                    "lid": lid, "via": "phone"}
        if lid:
            if self._me_lid and lid == self._me_lid:
                return {"phone": "", "is_owner": False,
                        "lid": lid, "via": "self_lid"}
            with self._lid_lock:
                phone = self._lid_to_phone.get(lid, "")
            if phone:
                return {"phone": phone,
                        "is_owner": bool(owner and phone == owner),
                        "lid": lid, "via": "lid_cache"}
            if self._owner_lid and lid == self._owner_lid and owner:
                return {"phone": owner, "is_owner": True,
                        "lid": lid, "via": "owner_lid"}
            return {"phone": "", "is_owner": False, "lid": lid, "via": ""}
        if chat_phones:
            phone = chat_phones[0]
            return {"phone": phone,
                    "is_owner": bool(owner and phone == owner),
                    "lid": lid, "via": "chat_remoteJid"}
        return {"phone": "", "is_owner": False, "lid": lid, "via": ""}

    def _handle_message(self, event: dict) -> None:
        msg_id = str(event.get("id", ""))
        if msg_id:
            if msg_id in self._seen_ids:
                return
            self._seen_ids.add(msg_id)
            if len(self._seen_ids) > 200:
                self._seen_ids.clear()
        ident = self.identify_sender(event)
        sender = ident["phone"]
        if not sender:
            # v1.4.1 logged only "unidentifiable sender" and the cause
            # stayed invisible. Log the raw sender fields MASKED plus
            # the key shape, then ignore as before.
            raw_fields = []
            for name in ("from", "from_alt", "participant",
                         "participant_alt", "remoteJid", "remoteJidAlt",
                         "participantLid", "participantPn"):
                value = event.get(name)
                if value:
                    raw_fields.append(f"{name}={mask_jid(str(value))}")
            key = event.get("key")
            key_shape = (f"key=[{','.join(sorted(key.keys()))}]"
                         if isinstance(key, dict) else f"key={type(key).__name__}")
            warn("whatsapp", "Message from an unidentifiable sender - ignored "
                             f"({' '.join(raw_fields) or 'no sender fields'}; "
                             f"{key_shape}; lid={mask_jid(ident['lid'])})")
            return
        text = str(event.get("text", "") or "").strip()
        if not text or len(text) > MAX_INCOMING_CHARS:
            return
        owner = self.owner_number()
        lang = "ar" if _is_arabic(text) else \
            self.settings.get("language", "ar")
        ai_name = self.settings.get("ai_name", "Memo")
        user_name = self.settings.get("user_name", "Mohamed")

        if ident["is_owner"]:
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
            # v1.4.2: memory_enabled=False - a guest's words must never
            # enter the owner's long-term memory, style, rules or
            # routines (see brain._record_turn).
            hist = self._guest_histories.setdefault(sender, [])
            result = brain.ask(text, ai_name, user_name, lang, history=hist,
                               memory_enabled=False)
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
        # v1.4.3: an [[IMAGE:...]] marker is a desktop-chat token, not
        # text - strip it before sending to WhatsApp (text only), and
        # drain any image this turn generated so it never attaches to
        # a later in-app chat. (The image itself stays saved under
        # app/data/images/; the desktop chat is where it renders.)
        raw_had_text = bool(reply)
        reply, _img_names = tools.extract_image_markers(reply)
        tools.take_generated_images()
        # v1.4.1: engine is "" when no engine answered (never "none").
        if not engine or not raw_had_text:
            reply = ("مش قادر أفكر دلوقتي — المخ مش متاح، جرّب تاني كمان شوية."
                     if lang == "ar" else
                     "I can't think right now — the brain is unavailable, try again shortly.")
        elif not reply:
            # The reply was only an image marker: WhatsApp gets text
            # only, so say where the picture is.
            reply = ("ولّدت الصورة ✅ — هتلاقيها في شات التطبيق على اللابتوب."
                     if lang == "ar" else
                     "Image generated ✅ — you'll find it in the desktop app chat.")
        reply = reply[:MAX_REPLY_CHARS]
        info("whatsapp", f"Message from {mode} sender {mask_number(sender)}, "
                         f"{len(text)} chars -> reply {len(reply)} chars "
                         f"(engine {engine})")
        # v1.4.2: confirmed send when linked (success only after
        # Baileys confirms); legacy fire-and-forget when not linked,
        # so a reply is still attempted if link state lags reality.
        reply_to_jid = event.get("from") or event.get("remoteJid") or ""
        if self._status == STATUS_LINKED and self._proc is not None:
            res = self.send_message(reply_to_jid, reply, reply_to=msg_id or None)
            if not res["ok"]:
                error("whatsapp", "Could not send WhatsApp reply: "
                                 f"{res['detail']}")
        else:
            ok = self._send_raw({"cmd": "send", "to": reply_to_jid,
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
                "owner_configured": False, "error": "",
                "sidecar_dead": False, "dead_reason": "", "restarts": 0,
                "last_stderr": ""}
    return _SERVICE.get_state()


def send_message(to: str, text: str) -> dict:
    """Module-level send (v1.4.2, for the brain's send_whatsapp_message
    tool in tools.py): delegate to the running service's confirmed
    send. Returns {"ok": bool, "detail": str} - ok=True ONLY after
    Baileys confirms the send. {"ok": False, "detail": "WhatsApp
    service not running"} when no service exists."""
    if _SERVICE is None:
        return {"ok": False, "detail": "WhatsApp service not running"}
    return _SERVICE.send_message(to, text)
