// Memo AI v1.4.0 — WhatsApp sidecar (Node + Baileys 6.7.x).
//
// Memo AI itself is Python; WhatsApp Web has no trustworthy free Python
// client (yowsup is dead pre-multi-device, pywa is the paid Business
// API), so the WhatsApp leg runs as this small Node sidecar next to the
// Python app. Baileys speaks the WhatsApp Web multi-device protocol
// directly over a WebSocket — NO browser, NO Puppeteer, ~50 MB RAM —
// and authenticates as a LINKED DEVICE (the user's barely-used 3rd
// number) via QR, exactly like WhatsApp Web in a browser.
//
// PROTOCOL (JSON lines on stdio — stdout is ONLY protocol JSON, every
// diagnostic goes to stderr, so Python can parse stdout safely):
//   Python -> sidecar (stdin, one JSON object per line):
//     {"cmd":"send","to":"<jid>","text":"...","reply_to":"<msg-id>"}
//         legacy fire-and-forget reply (v1.4.0): -> {"event":"sent",...}
//         or {"event":"error","error":"..."} on failure.
//     {"type":"send","req_id":"...","to":"<jid>","text":"...",
//      "reply_to":"<msg-id>"}   v1.4.2 confirmed send:
//         -> {"type":"sent","req_id":"...","to":"<jid>","id":"..."}
//         or {"type":"send_error","req_id":"...","error":"..."}.
//         Success is reported ONLY after sock.sendMessage resolves.
//     {"cmd":"lookup","jids":["<jid>",...]}  /  {"type":"lookup",...}
//         v1.4.2: sock.onWhatsApp(...jids) (phone -> LID):
//         -> {"type":"lookup_result","req_id":"...",
//             "results":[{"jid":"...","exists":bool,"lid":"...@lid"}]}
//     {"cmd":"logout"}   {"cmd":"stop"}   {"cmd":"ping"}
//   Sidecar -> Python (stdout, one JSON object per line):
//     {"event":"qr","qr":"data:image/png;base64,..."}   (scan me)
//     {"event":"ready","me":{"id":"<jid>","name":"...","lid":"...@lid"}}
//     {"event":"message","id":"...","from":"<jid>","from_alt":"<jid>",
//      "participant":"<jid>","participant_alt":"<jid>",
//       "participantLid":"...@lid","participantPn":"<digits>",
//       "remoteJid":"<jid>","remoteJidAlt":"<jid>",
//       "fromMe":bool,"pushName":"...","text":"...","ts":123}
//       v1.4.2: ALL raw sender fields are passed through (never message
//       content beyond the text itself) so Python can identify a sender
//       that arrives as a LID (@lid) instead of a phone JID.
//     {"event":"phone_share","lid":"...@lid","jid":"...@s.whatsapp.net"}
//       v1.4.2: WhatsApp pairing a LID with its phone number.
//     {"event":"sent","to":"<jid>","id":"..."}
//     {"event":"disconnected","reason":"...","logged_out":bool}
//     {"event":"logged_out"}   {"event":"pong"}   {"event":"error","error":"..."}
//     {"event":"log","level":"info|warn|error","msg":"..."}
//
// SAFETY: only 1:1 text messages are surfaced (groups, status,
// self-messages and media are ignored). No message content is ever
// written to stderr. The session (Signal keys) lives in the auth dir
// Python passes in — local only, never leaves the laptop.
//
// Usage: node sidecar.js <auth-dir> [--self-test]
//   --self-test: exercise the JSON-lines protocol without connecting
//   (CI/headless check: fake QR + ping + send-without-socket error).

import { createInterface } from "node:readline";
import { promises as fs } from "node:fs";
import path from "node:path";

import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  useMultiFileAuthState,
} from "@whiskeysockets/baileys";
import { Boom } from "@hapi/boom";
import QRCode from "qrcode";
import pino from "pino";
import { HttpsProxyAgent } from "https-proxy-agent";

const AUTH_DIR = process.argv[2] || "./whatsapp_auth";
const SELF_TEST = process.argv.includes("--self-test");

// stdout is the protocol channel — NEVER console.log anything else.
function emit(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}
function diag(level, msg) {
  console.error(`[sidecar:${level}] ${msg}`);
  emit({ event: "log", level, msg });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Proxy agent for the WebSocket (Baileys passes `agent` through to `ws`).
// Only active when HTTPS_PROXY/https_proxy is set — e.g. CI sandboxes.
// Normal Windows installs have no proxy env var, so this is undefined.
function proxyAgent() {
  const proxy = process.env.HTTPS_PROXY || process.env.https_proxy || "";
  if (!proxy) return undefined;
  try {
    return new HttpsProxyAgent(proxy);
  } catch (err) {
    diag("warn", `bad proxy URL, ignoring: ${err?.message || err}`);
    return undefined;
  }
}

// ---------------------------------------------------------------------------
// Message extraction (text only, 1:1 chats only)
// ---------------------------------------------------------------------------

function extractText(msg) {
  const m = msg.message;
  if (!m) return "";
  if (m.conversation) return m.conversation;
  if (m.extendedTextMessage?.text) return m.extendedTextMessage.text;
  if (m.imageMessage?.caption) return m.imageMessage.caption;
  if (m.videoMessage?.caption) return m.videoMessage.caption;
  return "";
}

function isIgnorable(msg) {
  const jid = msg.key?.remoteJid || "";
  if (!jid || msg.key?.fromMe) return true; // own messages / malformed
  if (jid.endsWith("@g.us")) return true; // groups — never auto-replied
  if (jid.endsWith("@broadcast")) return true; // status updates
  if (jid === "status@broadcast") return true;
  const m = msg.message || {};
  // Non-text payloads (reactions, polls, protocol, edits, stickers…)
  if (m.protocolMessage || m.reactionMessage || m.pollUpdateMessage ||
      m.editedMessage || m.stickerMessage || m.audioMessage ||
      m.documentMessage || m.contactMessage || m.locationMessage) {
    return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// Sidecar
// ---------------------------------------------------------------------------

class Sidecar {
  constructor(authDir) {
    this.authDir = authDir;
    this.sock = null;
    this.shouldRun = true;
    this.reconnectDelay = 2000;
    this.recentKeys = new Map(); // msg id -> { jid, key } (for quoted replies)
    this.seenIds = new Set(); // dedup: Baileys can deliver a message twice
  }

  async start() {
    while (this.shouldRun) {
      try {
        await this.connectOnce();
      } catch (err) {
        diag("error", `connect failed: ${err?.message || err}`);
      }
      if (!this.shouldRun) break;
      const delay = this.reconnectDelay;
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30000);
      diag("warn", `reconnecting in ${delay}ms`);
      await sleep(delay);
    }
  }

  async connectOnce() {
    const { state, saveCreds } = await useMultiFileAuthState(this.authDir);
    let version;
    try {
      ({ version } = await fetchLatestBaileysVersion());
    } catch {
      version = undefined; // offline at start: Baileys falls back to its default
      diag("warn", "could not fetch latest Baileys version (offline?)");
    }
    const sock = makeWASocket({
      version,
      auth: state,
      // The name the user sees in WhatsApp > Linked devices.
      browser: ["Memo AI", "Chrome", "1.0.0"],
      printQRInTerminal: false,
      logger: pino({ level: "silent" }),
      // Don't fetch full history on connect: Memo only answers live mail.
      syncFullHistory: false,
      markOnlineOnConnect: false,
      // Proxy support (Baileys passes `agent` to the WebSocket): only
      // used when HTTPS_PROXY is set (dev/CI sandboxes). On the user's
      // laptop there is no proxy env var, so this stays undefined.
      agent: proxyAgent(),
    });
    this.sock = sock;
    sock.ev.on("creds.update", saveCreds);

    const closed = new Promise((resolve) => {
      sock.ev.on("connection.update", async (update) => {
        const { connection, lastDisconnect, qr } = update;
        if (qr) {
          try {
            const dataUrl = await QRCode.toDataURL(qr, { width: 512, margin: 1 });
            emit({ event: "qr", qr: dataUrl });
            diag("info", "QR ready - scan from WhatsApp on the 3rd number");
          } catch (err) {
            diag("error", `QR render failed: ${err?.message || err}`);
          }
        }
        if (connection === "open") {
          this.reconnectDelay = 2000; // reset backoff after a good connect
          const me = sock.user || {};
          emit({ event: "ready", me: { id: me.id || "", name: me.name || "",
                                        lid: me.lid || "" } });
          diag("info", "WhatsApp linked (connection open)");
        }
        if (connection === "close") {
          const code = lastDisconnect?.error instanceof Boom
            ? lastDisconnect.error.output?.statusCode
            : undefined;
          const reason = lastDisconnect?.error?.message || "unknown";
          const loggedOut = code === DisconnectReason.loggedOut;
          diag("warn", `connection closed (code ${code}): ${reason}`);
          if (loggedOut) {
            // Session is dead (logged out from the phone / revoked):
            // wipe the creds so the next start shows a fresh QR.
            try {
              await fs.rm(this.authDir, { recursive: true, force: true });
            } catch { /* best effort */ }
            emit({ event: "logged_out" });
            this.shouldRun = false;
          } else {
            emit({ event: "disconnected", reason, logged_out: false });
          }
          resolve();
        }
      });
    });

    sock.ev.on("messages.upsert", ({ messages, type }) => {
      if (type !== "notify") return; // history sync replays are not live mail
      for (const msg of messages || []) {
        try {
          this.onMessage(msg);
        } catch (err) {
          diag("error", `message handler failed: ${err?.message || err}`);
        }
      }
    });

    // v1.4.2: WhatsApp itself pairs a LID with its phone number via a
    // SHARE_PHONE_NUMBER protocol message (Baileys surfaces it as
    // chats.phoneNumberShare). Forward it so Python can learn the
    // LID -> phone mapping the moment WhatsApp volunteers it.
    sock.ev.on("chats.phoneNumberShare", ({ lid, jid }) => {
      if (lid && jid) emit({ event: "phone_share", lid, jid });
    });

    await closed;
    try { sock.end(); } catch { /* already closed */ }
    this.sock = null;
  }

  onMessage(msg) {
    if (isIgnorable(msg)) return;
    const id = msg.key?.id || "";
    if (id && this.seenIds.has(id)) return; // dedup
    if (id) {
      this.seenIds.add(id);
      if (this.seenIds.size > 500) {
        // Trim oldest (insertion-ordered Set).
        const first = this.seenIds.values().next().value;
        this.seenIds.delete(first);
      }
      this.recentKeys.set(id, { jid: msg.key.remoteJid, key: msg.key });
      if (this.recentKeys.size > 50) {
        const first = this.recentKeys.keys().next().value;
        this.recentKeys.delete(first);
      }
    }
    const text = extractText(msg).trim();
    if (!text) return; // media without a caption
    if (text.length > 4000) return; // absurd input: ignore, don't feed the brain
    emit({
      event: "message",
      id,
      from: msg.key.remoteJid,
      // Baileys may deliver a LID JID (@lid) as remoteJid; remoteJidAlt
      // carries the real phone-number JID (@s.whatsapp.net) when it does.
      from_alt: msg.key.remoteJidAlt || "",
      // v1.4.2: pass through EVERY raw sender field Baileys gives us
      // (never message content beyond the text itself). Python needs
      // them all to identify a sender that arrives as a LID: the
      // participant fields (group/1:1 sender), the top-level
      // participantLid/participantPn pair, and the chat remoteJid.
      // Baileys 6.7.24 has no public LID->phone API, so identification
      // is done by combining these fields + a persistent cache in
      // Python; the sidecar must not drop any of them.
      participant: msg.key.participant || "",
      participant_alt: msg.key.participantAlt || "",
      participantLid: msg.participantLid || "",
      participantPn: msg.participantPn || "",
      remoteJid: msg.key.remoteJid || "",
      remoteJidAlt: msg.key.remoteJidAlt || "",
      fromMe: !!msg.key.fromMe,
      pushName: msg.pushName || "",
      text,
      ts: Number(msg.messageTimestamp) || 0,
    });
  }

  async lookup(jids) {
    // v1.4.2: phone -> LID, the ONLY direction Baileys 6.7.24 exposes
    // (onWhatsApp returns {jid, exists, lid}). Python calls this for
    // the owner phone at link time to learn the owner's LID, and
    // persists it so a LID-only message from the owner still routes
    // to the owner path. Throws when there is no live socket.
    if (!this.sock) throw new Error("not connected");
    const results = await this.sock.onWhatsApp(...jids);
    return (results || []).map((r) => ({
      jid: r?.jid || "",
      exists: !!r?.exists,
      lid: r?.lid || "",
    }));
  }

  async send(to, text, replyTo) {
    if (!this.sock) throw new Error("not connected");
    const opts = {};
    if (replyTo && this.recentKeys.has(replyTo)) {
      opts.quoted = this.recentKeys.get(replyTo).key;
    }
    const sent = await this.sock.sendMessage(to, { text }, opts);
    return sent?.key?.id || "";
  }

  async logout() {
    this.shouldRun = false;
    if (this.sock) {
      try { await this.sock.logout(); } catch { /* already out */ }
      try { this.sock.end(); } catch { /* already closed */ }
    }
    try {
      await fs.rm(this.authDir, { recursive: true, force: true });
    } catch { /* best effort */ }
    emit({ event: "logged_out" });
  }

  stop() {
    this.shouldRun = false;
    if (this.sock) {
      try { this.sock.end(); } catch { /* already closed */ }
    }
    process.exit(0);
  }
}

// ---------------------------------------------------------------------------
// stdin command loop (JSON lines)
// ---------------------------------------------------------------------------

async function main() {
  if (SELF_TEST) {
    // Headless protocol check (no network, no WhatsApp): fake QR, ping,
    // send-without-socket error, lookup-without-socket error, clean stop.
    // Used by CI + local tests.
    const fakeQr = await QRCode.toDataURL("memo-ai-self-test-qr", { width: 256, margin: 1 });
    emit({ event: "qr", qr: fakeQr });
    const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
    rl.on("line", (line) => {
      let cmd;
      try { cmd = JSON.parse(line); } catch { emit({ event: "error", error: "bad json" }); return; }
      if (cmd.cmd === "ping" || cmd.type === "ping") emit({ event: "pong" });
      else if (cmd.cmd === "send" || cmd.type === "send") emit({ event: "send_error", type: "send_error", req_id: cmd.req_id || null, error: "not connected (self-test)" });
      else if (cmd.cmd === "lookup" || cmd.type === "lookup") emit({ event: "lookup_result", type: "lookup_result", req_id: cmd.req_id || null, results: [], error: "not connected (self-test)" });
      else if (cmd.cmd === "stop") process.exit(0);
      else emit({ event: "error", error: `unknown cmd: ${cmd.cmd || cmd.type}` });
    });
    return;
  }

  const sidecar = new Sidecar(AUTH_DIR);
  const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
  rl.on("line", async (line) => {
    let cmd;
    try {
      cmd = JSON.parse(line);
    } catch {
      emit({ event: "error", error: "bad json command" });
      return;
    }
    const kind = cmd.cmd || cmd.type;
    try {
      if (kind === "ping") {
        emit({ event: "pong" });
      } else if (kind === "lookup") {
        // v1.4.2: phone -> LID lookup (see Sidecar.lookup).
        const results = await sidecar.lookup(
          (cmd.jids || []).map(String).filter(Boolean));
        emit({ event: "lookup_result", type: "lookup_result",
               req_id: cmd.req_id || null, results });
      } else if (kind === "send") {
        const id = await sidecar.send(cmd.to, String(cmd.text || ""), cmd.reply_to);
        if (cmd.type === "send") {
          // v1.4.2 confirmed send: success ONLY after sendMessage
          // resolves (Baileys confirmed the send).
          emit({ event: "sent", type: "sent", req_id: cmd.req_id || null,
                 to: cmd.to, id });
        } else {
          emit({ event: "sent", to: cmd.to, id });
        }
      } else if (kind === "logout") {
        await sidecar.logout();
        process.exit(0);
      } else if (kind === "stop") {
        sidecar.stop();
      } else {
        emit({ event: "error", error: `unknown cmd: ${kind}` });
      }
    } catch (err) {
      const msg = err?.message || String(err);
      if (cmd.type === "send") {
        emit({ event: "send_error", type: "send_error",
               req_id: cmd.req_id || null, error: msg });
      } else if (cmd.type === "lookup" || cmd.cmd === "lookup") {
        emit({ event: "lookup_result", type: "lookup_result",
               req_id: cmd.req_id || null, results: [], error: msg });
      } else {
        emit({ event: "error", error: msg });
      }
    }
  });

  process.on("SIGINT", () => sidecar.stop());
  process.on("SIGTERM", () => sidecar.stop());

  await sidecar.start();
  process.exit(0);
}

main().catch((err) => {
  diag("error", `sidecar fatal: ${err?.message || err}`);
  process.exit(1);
});
