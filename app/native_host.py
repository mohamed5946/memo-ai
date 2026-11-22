# LONA — Chrome native-messaging host (v1.6.0, extension workstream).
#
# Stdlib only, so it runs standalone (pythonw) and inside the frozen
# app (the exe is launched with "--native-host" — see
# extension/NATIVE_HOST_MAIN_HOOK.txt; Chrome itself launches the exe
# with the extension origin as its first argument, which the hook
# also detects).
#
# Protocol: Chrome Native Messaging — 4-byte little-endian length
# prefix + UTF-8 JSON, binary stdin/stdout.
#
# Architecture (two processes, one bridge):
#   * Chrome launches THIS process when the LONA Browser extension
#     connects (run_host_main). Its stdin/stdout belong to Chrome.
#     It also opens a loopback TCP listener on 127.0.0.1 (ephemeral
#     port, written to app/data/native-host.port) so the LONA desktop
#     app can reach it: desktop -> loopback -> host -> Chrome ->
#     extension -> active tab, and replies come back the same way.
#   * If no Chrome host is running, start_native_host_listener()
#     spawns this file in --standalone mode instead. That process
#     validates commands and answers ping, but browser commands
#     honestly report {"ok": False, "error": "extension_not_connected"}
#     — a command is NEVER reported as executed when the extension
#     did not run it.
#
# SAFETY (adopted suggestion 1 — dangerous-action safety, gates the
# extension):
#   * Page content is DATA, never commands. This host only accepts
#     commands from the LONA desktop app (loopback / its own stdin)
#     or tab-state reports from the extension. Nothing found in a
#     web page can become a command here.
#   * Command allowlist only (see ALLOWED_COMMANDS). Dangerous
#     commands (pay, delete, login, purchase, …) are rejected with
#     "dangerous_action_needs_permission" — they need an explicit
#     permission card in the LONA desktop app first, and this host
#     cannot grant one.
#   * navigate() only allows http(s) URLs on an allowlisted host
#     (youtube.com, www.youtube.com, google.com). javascript:, data:,
#     file: and every other host are rejected.
#   * Logs never contain full URLs: host + path only, no query
#     string (a query can hold private data, e.g. a search term).
#
# Logs: app/data/logs/native-host.log (next to this file's data dir).

import json
import os
import queue
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

HOST_ID = "com.memoai.host"  # NOT renamed — the host id stays as-is.
MAX_MESSAGE_BYTES = 1024 * 1024  # 1 MB — Chrome's own limit.

ALLOWED_COMMANDS = {
    "play_first_youtube", "navigate", "click", "type",
    "get_tab_info", "ping", "scroll",
}

# Dangerous actions: never executed by the extension/host. They need
# an explicit permission card in the LONA desktop app first.
DANGEROUS_COMMANDS = {
    "pay", "payment", "delete", "remove", "login", "log_in", "sign_in",
    "purchase", "buy", "checkout", "transfer", "withdraw", "send_money",
    "run", "eval", "exec", "download", "upload", "install",
}

ALLOWED_HOSTS = {"youtube.com", "www.youtube.com", "google.com"}

MAX_SELECTOR_LENGTH = 500
MAX_TEXT_LENGTH = 2000
MAX_QUERY_LENGTH = 200

YOUTUBE_FIRST_RESULT_SELECTORS = [
    "a#video-title",
    "ytd-video-renderer a#video-title",
    "ytd-video-renderer a.yt-simple-endpoint",
]

# ---------------------------------------------------------------------------
# Paths + logging (stdlib only — no app imports, so this runs standalone)
# ---------------------------------------------------------------------------

def _data_dir() -> Path:
    return Path(__file__).resolve().parent / "data"


def _log_path() -> Path:
    return _data_dir() / "logs" / "native-host.log"


def _log(level: str, message: str) -> None:
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "level": level, "message": message}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # logging must never crash the host


def _safe_url_for_log(url: str) -> str:
    """Host + path only — never the query string (private data)."""
    try:
        parsed = urlparse(str(url or ""))
        host = (parsed.netloc or "").lower()
        return f"{host}{parsed.path or ''}"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Framing (Chrome Native Messaging protocol)
# ---------------------------------------------------------------------------

def encode_message(message: dict) -> bytes:
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError("message too large")
    return struct.pack("<I", len(payload)) + payload


def decode_message(raw: bytes) -> dict:
    return json.loads(raw.decode("utf-8"))


def read_message(stream) -> dict | None:
    """Read one framed message from a binary stream. None on EOF."""
    header = stream.read(4)
    if not header:
        return None
    if len(header) < 4:
        raise ValueError("truncated length prefix")
    (length,) = struct.unpack("<I", header)
    if length > MAX_MESSAGE_BYTES:
        raise ValueError("message too large")
    payload = b""
    while len(payload) < length:
        chunk = stream.read(length - len(payload))
        if not chunk:
            raise ValueError("truncated message body")
        payload += chunk
    return decode_message(payload)


def write_message(stream, message: dict) -> None:
    stream.write(encode_message(message))
    stream.flush()


# ---------------------------------------------------------------------------
# Validation (allowlist only — everything else is rejected)
# ---------------------------------------------------------------------------

def validate_url(url) -> tuple[bool, str]:
    """Return (ok, reason). Only http(s) on an allowlisted host."""
    if not isinstance(url, str) or not url:
        return False, "missing_url"
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "bad_url"
    if parsed.scheme.lower() not in ("http", "https"):
        return False, "scheme_not_allowed"
    if parsed.username or parsed.password:
        return False, "credentials_in_url_not_allowed"
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        return False, "host_not_allowed"
    return True, ""


def youtube_search_url(query: str) -> str:
    from urllib.parse import quote_plus
    return ("https://www.youtube.com/results?search_query="
            + quote_plus(query))


def validate_command(msg) -> tuple[dict | None, dict | None]:
    """Validate an incoming command.

    Returns (cleaned_command, None) on success, or (None, error_dict)
    on rejection. The cleaned command is the only thing ever
    forwarded to the extension."""
    if not isinstance(msg, dict):
        return None, {"ok": False, "error": "bad_message"}
    cmd = msg.get("cmd")
    if not isinstance(cmd, str) or not cmd:
        return None, {"ok": False, "error": "missing_cmd"}
    if cmd in DANGEROUS_COMMANDS:
        return None, {"ok": False, "error": "dangerous_action_needs_permission",
                      "cmd": cmd,
                      "detail": "Payment, delete, login and similar "
                      "actions need an explicit permission card in the "
                      "LONA desktop app first."}
    if cmd not in ALLOWED_COMMANDS:
        return None, {"ok": False, "error": "unknown_command", "cmd": cmd}
    cleaned = {"cmd": cmd}
    if msg.get("id") is not None:
        cleaned["id"] = str(msg["id"])
    if cmd == "navigate":
        ok, reason = validate_url(msg.get("url"))
        if not ok:
            return None, {"ok": False, "error": reason, "cmd": cmd}
        cleaned["url"] = msg["url"]
    elif cmd == "play_first_youtube":
        query = msg.get("query")
        if not isinstance(query, str) or not query.strip():
            return None, {"ok": False, "error": "missing_query", "cmd": cmd}
        if len(query) > MAX_QUERY_LENGTH:
            return None, {"ok": False, "error": "query_too_long", "cmd": cmd}
        cleaned["query"] = query.strip()
    elif cmd == "click":
        selector = msg.get("selector")
        if (not isinstance(selector, str) or not selector.strip()
                or len(selector) > MAX_SELECTOR_LENGTH):
            return None, {"ok": False, "error": "bad_selector", "cmd": cmd}
        cleaned["selector"] = selector
    elif cmd == "type":
        selector = msg.get("selector")
        text = msg.get("text")
        if (not isinstance(selector, str) or not selector.strip()
                or len(selector) > MAX_SELECTOR_LENGTH):
            return None, {"ok": False, "error": "bad_selector", "cmd": cmd}
        if not isinstance(text, str) or len(text) > MAX_TEXT_LENGTH:
            return None, {"ok": False, "error": "bad_text", "cmd": cmd}
        cleaned["selector"] = selector
        cleaned["text"] = text
    elif cmd == "scroll":
        dy = msg.get("dy", 400)
        try:
            dy = int(dy)
        except Exception:
            return None, {"ok": False, "error": "bad_scroll", "cmd": cmd}
        cleaned["dy"] = max(-10000, min(10000, dy))
    # get_tab_info / ping take no arguments.
    return cleaned, None


# ---------------------------------------------------------------------------
# YouTube first-result selection (Python mirror of content.js, for
# offline validation / tests — the live path runs in content.js)
# ---------------------------------------------------------------------------

class _FirstVideoLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.link = None

    def handle_starttag(self, tag, attrs):
        if self.link is not None or tag != "a":
            return
        attrs = dict(attrs)
        if attrs.get("id") == "video-title":
            href = attrs.get("href") or ""
            if "/watch?" in href:
                self.link = href


def find_first_youtube_link(html: str) -> str | None:
    """First <a id="video-title" href=".../watch?..."> in the page.

    Page text around it is data only: an instruction string in the
    page ("delete files", "pay now", …) can never become a link or a
    command here — only the video-title anchor counts."""
    parser = _FirstVideoLinkParser()
    try:
        parser.feed(html or "")
    except Exception:
        return None
    return parser.link


# ---------------------------------------------------------------------------
# Host process (Chrome-launched): stdio loop + loopback listener
# ---------------------------------------------------------------------------

class NativeHost:
    """The Chrome-launched bridge. stdin/stdout = Chrome; a loopback
    TCP listener lets the LONA desktop app send it commands."""

    def __init__(self):
        self._stdout_lock = threading.Lock()
        self._pending: dict[str, queue.Queue] = {}
        self._pending_lock = threading.Lock()
        self.latest_tab_state: dict = {}
        self._stop = threading.Event()
        self._port = 0
        self._server = None

    # -- Chrome side ----------------------------------------------------
    def _send_to_chrome(self, message: dict) -> bool:
        try:
            with self._stdout_lock:
                write_message(sys.stdout.buffer, message)
            return True
        except Exception as exc:
            _log("error", f"write to Chrome failed: {exc}")
            return False

    def _stdio_loop(self):
        while not self._stop.is_set():
            try:
                msg = read_message(sys.stdin.buffer)
            except Exception as exc:
                _log("error", f"read from Chrome failed: {exc}")
                break
            if msg is None:  # stdin EOF — Chrome went away, exit cleanly
                break
            if not isinstance(msg, dict):
                continue
            msg_id = msg.get("id")
            if msg_id is not None:
                with self._pending_lock:
                    waiter = self._pending.pop(str(msg_id), None)
                if waiter is not None:
                    waiter.put(msg)
                    continue
            # Unsolicited message from the extension: tab state is
            # data — log host+path only, store it, act on nothing.
            if msg.get("type") == "tab_state":
                self.latest_tab_state = {
                    "title": msg.get("title", ""),
                    "url": msg.get("url", ""),
                }
                _log("info", "tab_state " +
                     _safe_url_for_log(self.latest_tab_state["url"]))
            elif msg.get("type") == "open_app_request":
                _log("info", "open_app requested from extension popup")
            elif msg.get("type") == "control_state":
                _log("info", "browser control enabled="
                     f"{msg.get('enabled')}")
            else:
                _log("info", f"extension message type={msg.get('type')}")
        self._stop.set()

    # -- Desktop-app side (loopback) ------------------------------------
    def _handle_desktop_command(self, msg: dict) -> dict:
        cleaned, err = validate_command(msg)
        if err is not None:
            err = dict(err)
            err["id"] = msg.get("id") if isinstance(msg, dict) else None
            _log("warn", f"rejected command: {err.get('error')} "
                         f"cmd={msg.get('cmd') if isinstance(msg, dict) else None}")
            return err
        if cleaned["cmd"] == "ping":
            return {"ok": True, "cmd": "ping", "id": cleaned.get("id"),
                    "extension_connected": True}
        # Forward to the extension and wait for its reply.
        msg_id = cleaned.get("id") or uuid.uuid4().hex[:8]
        cleaned["id"] = msg_id
        waiter: queue.Queue = queue.Queue()
        with self._pending_lock:
            self._pending[msg_id] = waiter
        if not self._send_to_chrome(cleaned):
            with self._pending_lock:
                self._pending.pop(msg_id, None)
            return {"ok": False, "error": "extension_not_connected",
                    "id": msg_id}
        _log("info", f"forwarded cmd={cleaned['cmd']} "
                     f"{_safe_url_for_log(cleaned.get('url', ''))}".rstrip())
        try:
            reply = waiter.get(timeout=15)
        except queue.Empty:
            with self._pending_lock:
                self._pending.pop(msg_id, None)
            return {"ok": False, "error": "extension_timeout", "id": msg_id}
        if not isinstance(reply, dict):
            return {"ok": False, "error": "bad_extension_reply", "id": msg_id}
        reply.setdefault("id", msg_id)
        return reply

    def _loopback_server(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(5)
        server.settimeout(0.5)
        self._server = server
        self._port = server.getsockname()[1]
        self._write_port_file()
        _log("info", f"loopback listener on 127.0.0.1:{self._port}")
        while not self._stop.is_set():
            try:
                conn, _addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve_loopback_conn,
                             args=(conn,), daemon=True).start()

    def _serve_loopback_conn(self, conn):
        try:
            stream = conn.makefile("rwb")
            while not self._stop.is_set():
                try:
                    msg = read_message(stream)
                except Exception:
                    break
                if msg is None:
                    break
                reply = self._handle_desktop_command(msg)
                try:
                    write_message(stream, reply)
                except Exception:
                    break
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _write_port_file(self):
        try:
            path = _data_dir() / "native-host.port"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"port": self._port,
                                        "pid": os.getpid()}),
                            encoding="utf-8")
        except Exception:
            pass

    def _remove_port_file(self):
        try:
            (_data_dir() / "native-host.port").unlink(missing_ok=True)
        except Exception:
            pass

    def run(self) -> int:
        threading.Thread(target=self._loopback_server, daemon=True).start()
        try:
            self._stdio_loop()
        finally:
            self._stop.set()
            self._remove_port_file()
            if self._server is not None:
                try:
                    self._server.close()
                except Exception:
                    pass
        return 0


def _run_standalone() -> int:
    """Standalone mode (desktop client spawned us, no Chrome).

    stdin/stdout = the desktop client. ping answers ok; every browser
    command is validated and then honestly refused with
    extension_not_connected — validation is not execution."""
    while True:
        try:
            msg = read_message(sys.stdin.buffer)
        except Exception as exc:
            _log("error", f"standalone read failed: {exc}")
            return 1
        if msg is None:
            return 0
        cleaned, err = validate_command(msg if isinstance(msg, dict) else {})
        if err is not None:
            reply = dict(err)
            reply["id"] = msg.get("id") if isinstance(msg, dict) else None
        elif cleaned["cmd"] == "ping":
            reply = {"ok": True, "cmd": "ping", "id": cleaned.get("id"),
                     "extension_connected": False}
        else:
            reply = {"ok": False, "error": "extension_not_connected",
                     "cmd": cleaned["cmd"], "id": cleaned.get("id"),
                     "validated": True}
        try:
            write_message(sys.stdout.buffer, reply)
        except Exception:
            return 1


def run_host_main() -> int:
    """Entry point for the Chrome-launched host (and --native-host).

    Called by main.py's argv hook — see
    extension/NATIVE_HOST_MAIN_HOOK.txt. Exits cleanly on stdin EOF."""
    _log("info", "native host started (Chrome mode)")
    return NativeHost().run()


# ---------------------------------------------------------------------------
# Desktop-app client (used by main.py — the backend worker owns wiring)
# ---------------------------------------------------------------------------

class _ReplyRouter:
    """Reads framed replies on a binary stream, dispatching by id.

    read_stream and write_stream are separate for a subprocess
    (stdout / stdin); for a loopback socket they are the same
    makefile("rwb") object."""

    def __init__(self, read_stream, write_stream, write_lock, on_eof=None):
        self._stream = read_stream
        self._write_stream = write_stream
        self._write_lock = write_lock
        self._on_eof = on_eof
        self._pending: dict[str, queue.Queue] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                msg = read_message(self._stream)
            except Exception:
                break
            if msg is None:
                break
            if isinstance(msg, dict) and msg.get("id") is not None:
                with self._lock:
                    waiter = self._pending.pop(str(msg["id"]), None)
                if waiter is not None:
                    waiter.put(msg)
        self._stop.set()
        if self._on_eof:
            try:
                self._on_eof()
            except Exception:
                pass

    def send_and_wait(self, message: dict, timeout: float = 15.0) -> dict:
        msg_id = message.get("id") or uuid.uuid4().hex[:8]
        message = dict(message)
        message["id"] = msg_id
        waiter: queue.Queue = queue.Queue()
        with self._lock:
            self._pending[msg_id] = waiter
        try:
            with self._write_lock:
                write_message(self._write_stream, message)
        except Exception as exc:
            with self._lock:
                self._pending.pop(msg_id, None)
            return {"ok": False, "error": "transport_error",
                    "detail": str(exc), "id": msg_id}
        try:
            return waiter.get(timeout=timeout)
        except queue.Empty:
            with self._lock:
                self._pending.pop(msg_id, None)
            return {"ok": False, "error": "timeout", "id": msg_id}

    def close(self):
        self._stop.set()


class NativeHostClient:
    """Client the desktop app uses to talk to the native host.

    send(cmd) -> dict; is_connected() is True while the transport to
    the host process is alive. It does NOT by itself mean the Chrome
    extension executed anything — every reply carries
    "extension_connected", and browser commands with no extension
    return ok=False / extension_not_connected."""

    def __init__(self, router, extension_connected: bool, proc=None,
                 sock=None):
        self._router = router
        self.extension_connected = bool(extension_connected)
        self._proc = proc
        self._sock = sock
        self._closed = False

    def send(self, cmd: dict, timeout: float = 15.0) -> dict:
        if self._closed:
            return {"ok": False, "error": "client_closed"}
        if not isinstance(cmd, dict) or "cmd" not in cmd:
            return {"ok": False, "error": "bad_message"}
        cleaned, err = validate_command(cmd)
        if err is not None:
            return err
        return self._router.send_and_wait(cleaned, timeout=timeout)

    def is_connected(self) -> bool:
        if self._closed:
            return False
        if self._proc is not None:
            return self._proc.poll() is None
        if self._sock is not None:
            try:
                self._sock.getpeername()
                return True
            except Exception:
                return False
        return False

    def close(self) -> None:
        self._closed = True
        try:
            self._router.close()
        except Exception:
            pass
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass


def _try_loopback_client(timeout: float = 3.0):
    """Connect to a Chrome-launched host via the port file, if any."""
    try:
        port_path = _data_dir() / "native-host.port"
        info = json.loads(port_path.read_text(encoding="utf-8"))
        port = int(info["port"])
    except Exception:
        return None
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    except Exception:
        return None
    stream = sock.makefile("rwb")
    router = _ReplyRouter(stream, stream, threading.Lock())
    router.start()
    client = NativeHostClient(router, extension_connected=True, sock=sock)
    reply = client.send({"cmd": "ping"}, timeout=timeout)
    if reply.get("ok"):
        return client
    client.close()
    return None


def _hidden_popen_kwargs() -> dict:
    """CREATE_NO_WINDOW on Windows, so spawning the standalone host
    never flashes a console window (v1.6.4 console-flash audit).
    Prefers hidden_spawn; falls back to the literal flag so this file
    still runs standalone if copied somewhere without app/ on path."""
    try:
        import hidden_spawn
        return hidden_spawn.popen_kwargs()
    except Exception:
        if sys.platform == "win32":
            return {"creationflags": 0x08000000}
        return {}


# v1.6.4 (F-H): how many times start_native_host_listener() retries
# a failed spawn + ping handshake before giving up.
_LISTENER_START_ATTEMPTS = 3


def _spawn_standalone_listener() -> NativeHostClient:
    """One spawn + ping-handshake attempt for the standalone host.
    Raises RuntimeError when the handshake fails (the spawned process
    is closed first, so a retry never leaves a stray process)."""
    exe = sys.executable or sys.argv[0]
    if getattr(sys, "frozen", False):
        # Frozen: the exe IS the program — its entry point is main.py,
        # which handles --standalone (hook added with this fix). The
        # .py path does not exist on disk in a frozen bundle, so it
        # must NOT be passed as an argument.
        argv = [exe, "--standalone"]
    else:
        argv = [exe, str(Path(__file__).resolve()), "--standalone"]
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        # v1.6.4: no console window when spawning the host process.
        **_hidden_popen_kwargs())
    router = _ReplyRouter(proc.stdout, proc.stdin, threading.Lock())
    router.start()
    client = NativeHostClient(router, extension_connected=False, proc=proc)
    reply = client.send({"cmd": "ping"}, timeout=5)
    if not reply.get("ok"):
        client.close()
        raise RuntimeError("native host failed to start: "
                           f"{reply.get('error')}")
    return client


def start_native_host_listener() -> NativeHostClient:
    """Start (or attach to) the native-host bridge for the desktop app.

    Prefers a Chrome-launched host (extension already connected);
    otherwise spawns this file in --standalone mode, which validates
    commands and answers ping but reports browser commands as
    extension_not_connected until the extension connects.

    v1.6.4 (F-H): the spawn used to pass the .py path as an argument
    ([exe, native_host.py, --standalone]), which the FROZEN exe does
    not understand — its entry point is main.py, which only handles
    --native-host, so the spawned exe opened the GUI instead of the
    stdio loop and the listener could never start on a real install.
    Frozen now spawns [exe, --standalone] (main.py gains a matching
    --standalone hook); source runs keep the script path. The start
    is retried (3 attempts, backoff) because a slow interpreter start
    can fail the first ping even when the spawn itself worked."""
    client = _try_loopback_client()
    if client is not None:
        return client
    last_exc = None
    for attempt in range(_LISTENER_START_ATTEMPTS):
        try:
            client = _spawn_standalone_listener()
        except Exception as exc:
            last_exc = exc
            _log("warn", "native host start attempt "
                         f"{attempt + 1}/{_LISTENER_START_ATTEMPTS} "
                         f"failed: {exc}")
            if attempt + 1 < _LISTENER_START_ATTEMPTS:
                time.sleep(0.5 * (attempt + 1))
            continue
        return client
    raise RuntimeError("native host failed to start: "
                       f"{last_exc}")


# ---------------------------------------------------------------------------
# v1.6.2: module-level shared client (the extension re-wire).
#
# browser_control.py used to call getattr(native_host, "send_command")
# and selfview.py getattr(native_host, "is_connected") -- neither
# existed at module level (only NativeHostClient.send / .is_connected
# on an instance), so every extension call silently reported
# "not connected" even when the host was up. The shared client fixes
# that: the app starts ONE listener at startup (main.py, background
# thread) and registers it here; send_command() / is_connected()
# use it, and send_command() starts the listener on demand when the
# app never registered one (voice "play El-Daheeh" from a fresh
# process still works).
# ---------------------------------------------------------------------------
_SHARED_CLIENT: NativeHostClient | None = None
_SHARED_LOCK = threading.Lock()

# browser_control's action names -> native-host command names.
_ACTION_TO_CMD = {
    "play_first_youtube_result": "play_first_youtube",
}


def set_shared_client(client) -> None:
    """Register the app's listener client (called once at startup).
    A None client clears the registration."""
    global _SHARED_CLIENT
    with _SHARED_LOCK:
        _SHARED_CLIENT = client


def get_shared_client():
    """The registered client, or None when the app never started one."""
    with _SHARED_LOCK:
        return _SHARED_CLIENT


def is_connected() -> bool:
    """True while the shared native-host client is alive. Cheap and
    non-blocking: it never starts a listener -- False simply means
    "no client registered (yet)". Whether the EXTENSION executed
    anything is a separate question, answered per-reply."""
    client = get_shared_client()
    if client is None:
        return False
    try:
        return bool(client.is_connected())
    except Exception:
        return False


def send_command(cmd: dict, timeout: float = 15.0) -> dict:
    """Send one command to the extension via the shared client.

    Accepts the native {"cmd": ...} shape AND browser_control's
    {"action": "play_first_youtube_result", "query": ...} shape
    (translated here). Starts the listener on demand when the app
    never registered one. Never raises -- failures are dicts, so
    callers can report them honestly."""
    if not isinstance(cmd, dict):
        return {"ok": False, "error": "bad_message"}
    normalized = dict(cmd)
    if "cmd" not in normalized and "action" in normalized:
        action = str(normalized.pop("action") or "")
        mapped = _ACTION_TO_CMD.get(action)
        if mapped is None:
            return {"ok": False, "error": "unknown_command",
                    "cmd": action}
        normalized["cmd"] = mapped
    client = get_shared_client()
    if client is None or not client.is_connected():
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        try:
            client = start_native_host_listener()
        except Exception as exc:
            return {"ok": False, "error": "extension_not_connected",
                    "detail": str(exc)}
        set_shared_client(client)
    try:
        return dict(client.send(normalized, timeout=timeout) or {})
    except Exception as exc:
        return {"ok": False, "error": "extension_not_connected",
                "detail": str(exc)}


if __name__ == "__main__":
    if "--standalone" in sys.argv:
        sys.exit(_run_standalone())
    # Chrome launches the host with the extension origin as argv[1];
    # the frozen exe is launched with --native-host (see the hook file).
    sys.exit(run_host_main())
