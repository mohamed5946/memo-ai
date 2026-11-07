// Memo AI v1.4.3 — Bad MAC decrypt-failure watch (pure logic, no I/O).
//
// WHY THIS EXISTS (field log, user, 2026-11-07 12:52:42 -> 12:55:18):
// WhatsApp messages reached the sidecar but NEVER decrypted — the log
// repeated every ~5s:
//   "Failed to decrypt message with any known session..."
//   "Session error: Error: Bad MAC"
//     at libsignal verifyMAC -> SessionCipher.doDecryptWhisperMessage
//     -> decryptWithSessions — same session id every time.
//
// WHERE IT COMES FROM (Baileys 6.7.24, installed in node_modules):
//   * Decryption happens INSIDE Baileys, before messages.upsert:
//     lib/Utils/decode-wa-message.js decryptMessageNode().decrypt()
//     catches the libsignal SessionError and marks the message
//     messageStubType = CIPHERTEXT (proto.WebMessageInfo.StubType
//     .CIPHERTEXT = 2, WAProto/index.d.ts:53139) with
//     messageStubParameters = [err.message]. Baileys then sends a
//     RETRY RECEIPT automatically (lib/Socket/messages-recv.js
//     sendRetryRequest: retry #1 = placeholder resend, retry #>1
//     includes fresh pre-keys via getNextPreKeys — i.e. the sender is
//     asked to re-encrypt with fresh keys = session re-establishment),
//     capped at maxMsgRetryCount = 5 PER MESSAGE (lib/Defaults/
//     index.js:40). There is NO public sendRetryReceipt API in 6.7.24
//     — the retry receipt is internal and already fires on its own.
//   * The LOG FLOOD is libsignal, not Baileys: node_modules/libsignal
//     /src/session_cipher.js decryptWithSessions() calls console.error
//     ("Failed to decrypt message with any known session..." + a full
//     stack per error), bypassing Baileys' pino logger (which the
//     sidecar silences). Python faithfully logs every stderr line, so
//     the diagnostics log fills every ~5s. The ~5s cadence matches
//     requestPlaceholderResend's 5s delay + the sender/server
//     re-delivering: each NEW message restarts Baileys' per-message
//     retry counter, so the storm never ends while the session is
//     broken. There is no infinite retry loop *inside* Baileys for one
//     message (cap 5) — the storm is per-new-message + the unthrottled
//     console.error. This module fixes exactly that: it dedupes the
//     log, caps OUR OWN re-key attempts, and escalates to "degraded"
//     instead of retrying forever.
//
// WHAT THIS MODULE DOES (pure — unit-tested without WhatsApp):
//   * maskDigits / maskJid: never a full number/LID in any log or
//     event (digit runs of 6+ become "…"+last 2 digits).
//   * DecryptWatch.recordFailure(): per-sender counting of DISTINCT
//     failed messages (same msg id retried does not inflate the
//     count). After FAILURES_BEFORE_REKEY distinct failures it says
//     "rekey now" (the sidecar then calls the only public re-key
//     primitive Baileys 6.7.24 exposes: sock.assertSessions([jid],
//     true) — lib/Socket/messages-send.js:158 — which fetches a fresh
//     pre-key bundle from the server and injects a new E2E session).
//     Re-keys are capped (MAX_REKEYS per sender) with a cooldown, so
//     a broken session is not re-keyed forever. After
//     FAILURES_BEFORE_DEGRADED distinct failures (or re-keys
//     exhausted + more failures) it says "degraded": the session is
//     unrecoverable from here — the user needs a clean re-link
//     (logout + fresh QR). A successfully decrypted message from the
//     same sender (recordSuccess) clears the whole state: the session
//     healed, recovery is real, not assumed.
//   * DecryptWatch.noteConsoleError(): the gate for libsignal's
//     console.error flood — the FIRST Bad MAC line per session-key
//     logs once (masked); repeats are counted and suppressed, with a
//     periodic "…+N suppressed" summary instead of a stack every 5s.
//
// The sidecar (sidecar.js) wires this in; Python (whatsapp.py) mirrors
// the escalation so the Settings UI shows "degraded" instead of a
// green "linked" while messages are being lost.

export const CIPHERTEXT_STUB_TYPE = 2; // proto.WebMessageInfo.StubType.CIPHERTEXT
export const FAILURES_BEFORE_REKEY = 3; // distinct failed msgs -> force re-key
export const FAILURES_BEFORE_DEGRADED = 5; // distinct failed msgs -> degraded
export const MAX_REKEYS_PER_SENDER = 2; // then stop re-keying, wait for relink
export const REKEY_COOLDOWN_MS = 60_000; // min gap between forced re-keys
export const LOG_SUPPRESS_WINDOW_MS = 10 * 60_000; // re-log a storm summary
export const FAILURE_DECAY_MS = 30 * 60_000; // quiet sender: counts reset

// Digit runs of 6+ (phone numbers, LIDs, session ids) -> "…"+last 2.
export function maskDigits(text) {
  return String(text ?? "").replace(/\d{6,}/g, (run) => `…${run.slice(-2)}`);
}

// "201557887719@s.whatsapp.net" -> "…19@s.whatsapp.net",
// "123456789012345@lid" -> "…45@lid". Never the full number/LID.
export function maskJid(jid) {
  const s = String(jid || "");
  if (!s.includes("@")) return maskDigits(s);
  const [user, domain] = s.split("@", 2);
  const digits = user.replace(/\D/g, "");
  return `…${digits.length >= 2 ? digits.slice(-2) : "??"}@${domain}`;
}

// Does this stub-parameter text look like a decrypt failure (not some
// other stub)? The reason strings Baileys puts in messageStubParameters
// for a failed decrypt are libsignal errors: "No matching sessions
// found for message", "Key used already or never filled", "Bad MAC".
export function looksLikeDecryptFailure(reason) {
  return /no matching sessions|bad mac|key used already|never filled|decrypt/i
    .test(String(reason || ""));
}

export class DecryptWatch {
  constructor(opts = {}) {
    this.failuresBeforeRekey = opts.failuresBeforeRekey ?? FAILURES_BEFORE_REKEY;
    this.failuresBeforeDegraded = opts.failuresBeforeDegraded ?? FAILURES_BEFORE_DEGRADED;
    this.maxRekeys = opts.maxRekeys ?? MAX_REKEYS_PER_SENDER;
    this.rekeyCooldownMs = opts.rekeyCooldownMs ?? REKEY_COOLDOWN_MS;
    this.logSuppressWindowMs = opts.logSuppressWindowMs ?? LOG_SUPPRESS_WINDOW_MS;
    this.failureDecayMs = opts.failureDecayMs ?? FAILURE_DECAY_MS;
    this.now = opts.now ?? (() => Date.now());
    // sender (raw JID, in-memory only — never logged) ->
    // { msgIds:Set, count, firstAt, lastAt, rekeys, lastRekeyAt,
    //   degraded, degradedAt }
    this.senders = new Map();
    // console.error flood gate: sessionKey -> { firstAt, lastLogAt, suppressed }
    this.logGate = new Map();
  }

  _entry(senderJid, now) {
    let e = this.senders.get(senderJid);
    if (e && now - e.lastAt > this.failureDecayMs) {
      // Quiet for a while: old failures are stale, start fresh.
      this.senders.delete(senderJid);
      e = undefined;
    }
    if (!e) {
      e = { msgIds: new Set(), count: 0, firstAt: now, lastAt: now,
            rekeys: 0, lastRekeyAt: 0, degraded: false, degradedAt: 0 };
      this.senders.set(senderJid, e);
    }
    return e;
  }

  recordFailure({ senderJid, msgId, reason = "" } = {}) {
    const now = this.now();
    const sender = String(senderJid || "");
    const e = this._entry(sender, now);
    const isNew = !!msgId && !e.msgIds.has(msgId);
    if (msgId) e.msgIds.add(msgId);
    // A re-delivery of the SAME undecryptable message (Baileys' retry
    // cycle / server redelivery every ~5s) must not inflate the count
    // or re-trigger a re-key: only distinct messages count.
    if (isNew) e.count += 1;
    e.lastAt = now;
    const degradedBefore = e.degraded;
    let rekeyDue = false;
    if (isNew && !e.degraded && e.count >= this.failuresBeforeRekey &&
        e.rekeys < this.maxRekeys &&
        now - e.lastRekeyAt >= this.rekeyCooldownMs) {
      rekeyDue = true;
      e.rekeys += 1;
      e.lastRekeyAt = now;
    }
    let degradedNow = false;
    if (!degradedBefore && e.count >= this.failuresBeforeDegraded) {
      e.degraded = true;
      e.degradedAt = now;
      degradedNow = true;
    }
    return {
      senderMasked: maskJid(sender),
      count: e.count,
      isNewMessage: isNew,
      rekeyDue,
      rekeyAttempt: e.rekeys,
      degraded: e.degraded,
      degradedNow,
      needsRelink: e.degraded,
      reason: String(reason || "").slice(0, 120),
    };
  }

  // Call this when the sidecar actually attempted the forced re-key
  // (assertSessions). Kept for the summary; the attempt itself is
  // counted in recordFailure so a failed call cannot loop.
  noteRekeyResult({ senderJid, ok, error = "" } = {}) {
    return { senderMasked: maskJid(senderJid), ok: !!ok,
             error: String(error || "").slice(0, 120) };
  }

  // A real, decrypted message from this sender: the session healed
  // (e.g. the retry receipt + fresh keys worked). Returns whether
  // this sender was degraded — i.e. whether a "recovered" event is due.
  recordSuccess(senderJid) {
    const now = this.now();
    const e = this.senders.get(String(senderJid || ""));
    if (!e) return { recovered: false, wasDegraded: false };
    const wasDegraded = e.degraded;
    // A success proves the session works again: drop the whole state.
    // (lastAt is kept fresh so the decay logic never resurrects it.)
    this.senders.delete(String(senderJid || ""));
    return { recovered: wasDegraded, wasDegraded,
             senderMasked: maskJid(senderJid) };
  }

  // Gate for libsignal's console.error flood. Returns { log, text }:
  // log=true means "emit this one line" (masked summary); log=false
  // means "swallow it, it was counted". A storm re-logs a summary
  // once per LOG_SUPPRESS_WINDOW_MS so the log still shows the storm
  // is alive without a stack every 5 seconds.
  noteConsoleError(text) {
    const now = this.now();
    const raw = String(text ?? "");
    if (!/bad mac|failed to decrypt message with any known session/i.test(raw)) {
      return { log: true, text: raw, gated: false };
    }
    // Session key: the long digit run in the line (e.g. the
    // "231864564510752.0" session id in the field log) — masked.
    const m = raw.match(/\d{6,}/);
    const key = m ? maskDigits(m[0]) : "unknown";
    let g = this.logGate.get(key);
    if (!g) {
      g = { firstAt: now, lastLogAt: 0, suppressed: 0 };
      this.logGate.set(key, g);
    }
    if (now - g.lastLogAt >= this.logSuppressWindowMs || g.lastLogAt === 0) {
      const extra = g.suppressed > 0 ? ` (+${g.suppressed} repeats suppressed)` : "";
      g.lastLogAt = now;
      g.suppressed = 0;
      return { log: true, gated: true,
               text: `WhatsApp decrypt failed (Bad MAC, session ${key}) — ` +
                 `messages from this sender are not decrypting; ` +
                 `retry receipts + re-key in progress${extra}` };
    }
    g.suppressed += 1;
    return { log: false, gated: true, text: "" };
  }

  snapshot() {
    const out = {};
    for (const [jid, e] of this.senders) {
      out[maskJid(jid)] = { failures: e.count, rekeys: e.rekeys,
                            degraded: e.degraded };
    }
    return out;
  }
}
