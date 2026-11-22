// LONA v1.6.1 (21-refined) — headless DOM test for the field freeze:
// Groq 429 + dead Ollama + a stale YouTube permission + "ازاي".
//
// Runs the REAL sendText() + waitForChatResult() + startPermPolling()
// + addPermCard()/finalizePermCard() from app/ui/app.js inside jsdom,
// with a stubbed pywebview bridge. Reports JSON for the Python suite
// (app/tests/test_v161_freeze_field.py).
//
//   NODE_PATH=/tmp/jsdom-test/node_modules node app/tests/freeze_field_dom.js
//
// Scenarios:
//   * field_queue: a message sent while a turn runs is QUEUED (bubble
//     shows at once, never silently dropped), the stale permission is
//     cancelled on the new message, the dots stay visible until the
//     FINAL reply, and the queued message is then sent for real.
//   * cap_timeout: chat_result never finishes -> the 150 s cap resolves
//     with a clear failure message, NEVER "…" as a reply.
//   * cancelled_card: a cancelled permission flips its card to
//     "اتلغى — سؤال جديد بدأ" with both buttons dimmed, no pick.
//   * no_stale_card_in_new_turn: after the cancel, a new turn shows no
//     card for the old permission.

const fs = require("fs");
const path = require("path");

const { JSDOM } = require("jsdom");

const ROOT = path.resolve(__dirname, "..", "..");
const jsSrc = fs.readFileSync(path.join(ROOT, "app", "ui", "app.js"), "utf8");

// ---- extract the real code under test from app.js (brace matching) ----
function extract(src, startMarker) {
  const start = src.indexOf(startMarker);
  if (start < 0) throw new Error("marker not found: " + startMarker);
  let i = src.indexOf("{", start);
  let depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}") { depth--; if (depth === 0) return src.slice(start, i + 1); }
  }
  throw new Error("unbalanced braces after: " + startMarker);
}

const results = {};
function check(name, cond, extra) {
  results[name] = { pass: !!cond, extra: extra || "" };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function makeDom(opts) {
  opts = opts || {};
  const dom = new JSDOM(
    `<!DOCTYPE html><html><body>
      <div id="chat"></div>
      <input id="text-input" />
      <div id="status"></div>
    </body></html>`,
    { url: "https://localhost/", runScripts: "outside-only" });
  const { window } = dom;
  const { document } = window;
  if (opts.capMs) window.__chatResultCapMs = opts.capMs;

  // --- stubbed bridge ---
  // chat(): first call -> chat c1 (pending), second call -> c2.
  // chat_result(): done after `pollsToDone` polls per chat (or never).
  // get_pending_permission(): a stale YouTube permission from the old
  // turn until cancel_pending_permissions() is called, then resolved.
  const calls = { chat: [], cancel: 0, resolve: [] };
  let chatN = 0;
  const polls = {};
  let permCancelled = false;
  const bridge = {
    chat: async (text) => {
      chatN += 1;
      calls.chat.push(text);
      return { pending: true, chat_id: "c" + chatN };
    },
    chat_result: async (chatId) => {
      polls[chatId] = (polls[chatId] || 0) + 1;
      if (opts.neverDone) return { done: false };
      if (polls[chatId] >= (opts.pollsToDone || 2)) {
        return { done: true,
                 reply: chatId === "c1" ? "رد قديم من Groq" : "الحد ده حد الدقيقة",
                 engine: "groq", images: [] };
      }
      return { done: false };
    },
    get_pending_permission: async () => {
      if (permCancelled) {
        return { pending: null,
                 resolved: [{ id: "p1", kind: "browser_play_youtube",
                              summary: "تشغيل من يوتيوب: الدحيح",
                              status: "cancelled", tier: "green" }] };
      }
      return { pending: { id: "p1", kind: "browser_play_youtube",
                          summary: "تشغيل من يوتيوب: الدحيح",
                          detail: "هيفتح يوتيوب ويدوّر على: الدحيح",
                          tier: "green", status: "pending",
                          created: "01:28:00" },
               resolved: [] };
    },
    resolve_permission: async (id, allowed) => {
      calls.resolve.push([id, allowed]);
      return { ok: true };
    },
    cancel_pending_permissions: async () => {
      calls.cancel += 1;
      permCancelled = true;
      return { ok: true, cancelled: 1 };
    },
  };
  window.pywebview = { api: bridge };

  const prelude = [
    "let LANG = 'ar';",
    "let busy = false;",
    "let permCards = {};",
    "let permPollTimer = null;",
    "let heroFlown = false;",
    "let currentMood = 'صاحي';",
    "let thinkingActive = false, thinkingPrevMood = null;",
    "let _sendQueue = [];",
    "const MOODS = {'صاحي': 1, 'بيفكر': 1};",
    "function setMood(m) { window.__moods.push(m); }",
    "window.__moods = [];",
    "function $(id) { return document.getElementById(id); }",
    "function getApi() { return (window.pywebview && window.pywebview.api) ? window.pywebview.api : null; }",
    // `let` bindings do not survive across window.eval calls, so expose
    // the busy flag through a closure for the checks below.
    "window.__isBusy = () => busy;",
    "window.__queueLen = () => _sendQueue.length;",
  ].join("\n");
  const fns = [
    extract(jsSrc, "const I18N = {"),
    extract(jsSrc, "function t(key)"),
    extract(jsSrc, "function safeReply(reply)"),
    extract(jsSrc, "function metaEngine(engine)"),
    extract(jsSrc, "function flyHeroToHeader()"),
    extract(jsSrc, "function addMsg(text, who, meta)"),
    extract(jsSrc, "async function addGeneratedImages(filenames)"),
    extract(jsSrc, "function showTyping()"),
    extract(jsSrc, "function hideTyping()"),
    extract(jsSrc, "function beginThinking()"),
    extract(jsSrc, "function endThinking()"),
    extract(jsSrc, "async function sendText(text"),
    extract(jsSrc, "function waitForChatResult(chatId)"),
    extract(jsSrc, "function startPermPolling()"),
    extract(jsSrc, "function stopPermPolling()"),
    extract(jsSrc, "const SEVERITY = {"),
    extract(jsSrc, "function severityDot(tier)"),
    extract(jsSrc, "function iconSvg(id, size)"),
    extract(jsSrc, "function addPermCard(p)"),
    extract(jsSrc, "function finalizePermCard(id, status)"),
  ].join("\n");
  window.eval(prelude + "\n" + fns);
  return { window, document, calls };
}

function chatText(document) {
  return Array.from(document.querySelectorAll("#chat .msg"))
    .map((el) => el.textContent).join(" | ");
}
function dotsVisible(document) {
  return !!document.getElementById("typing-indicator");
}

(async () => {
  // --- 1. field scenario: queue + cancel + dots until the final reply ---
  {
    const { window, document, calls } = makeDom({ pollsToDone: 2 });
    const sendPromise = window.eval("sendText('شغّل الدحيح')");
    await sleep(50);
    check("field_first_turn_shows_dots", dotsVisible(document));
    // The user types "ازاي" while the first turn is still running.
    window.eval("sendText('ازاي')");
    await sleep(50);
    check("field_queued_bubble_shown_at_once",
          chatText(document).includes("ازاي"),
          chatText(document));
    check("field_queued_not_sent_yet", calls.chat.length === 1,
          "chat calls=" + calls.chat.length);
    check("field_stale_perm_cancelled_on_new_message",
          calls.cancel === 1, "cancel calls=" + calls.cancel);
    check("field_dots_still_visible_while_queued",
          dotsVisible(document));
    await sendPromise; // first reply arrives, queue drains
    await sleep(100);
    check("field_first_reply_shown",
          chatText(document).includes("رد قديم من Groq"),
          chatText(document));
    check("field_queued_message_sent_after_first",
          calls.chat.length === 2 && calls.chat[1] === "ازاي",
          JSON.stringify(calls.chat));
    // The second turn is now running: dots are up again.
    check("field_dots_up_for_queued_turn", dotsVisible(document));
    // Wait for the second reply (2 polls x 1200ms).
    await sleep(3000);
    check("field_second_reply_shown",
          chatText(document).includes("الحد ده حد الدقيقة"),
          chatText(document));
    check("field_dots_hidden_after_final_reply",
          !dotsVisible(document));
  }
  // --- 2. the 150 s cap never returns "…" ---
  {
    const { window, document } = makeDom({ neverDone: true, capMs: 150 });
    window.eval("sendText('ازاي')");
    await sleep(500);
    const text = chatText(document);
    check("cap_error_message_shown_not_dots",
          text.includes("الرد اتأخر أوي") && !text.includes("…"),
          text);
    check("cap_dots_hidden", !dotsVisible(document));
    check("cap_busy_released", window.__isBusy() === false);
  }
  // --- 3. cancelled card flips to "اتلغى" with both buttons dimmed ---
  {
    // pollsToDone 99 + a short cap so the turn ends on its own.
    const { window, document } = makeDom({ pollsToDone: 99, capMs: 3500 });
    const sendPromise = window.eval("sendText('شغّل الدحيح')");
    await sleep(1500); // one poll: the stale card appears
    const card = document.querySelector(".msg.perm");
    check("cancel_card_appears", !!card);
    if (card) {
      await window.pywebview.api.cancel_pending_permissions();
      await sleep(1500); // next poll finalizes it as cancelled
      const state = card.querySelector(".perm-state");
      check("cancel_card_text",
            !!state && state.textContent.includes("اتلغى"),
            state ? state.textContent : "no .perm-state");
      const btns = card.querySelectorAll("button");
      check("cancel_card_both_dimmed",
            btns.length === 2 &&
            btns[0].classList.contains("dimmed") &&
            btns[1].classList.contains("dimmed"));
      check("cancel_card_no_pick",
            !card.querySelector(".picked"));
    }
    window.eval("stopPermPolling()");
    // Let the turn finish (cap) so no timers leak into the next scenario.
    await sendPromise;
  }
  // --- 4. after the cancel, a new turn shows no card for the old perm ---
  {
    const { window, document, calls } = makeDom({ pollsToDone: 2 });
    await window.pywebview.api.cancel_pending_permissions();
    await window.eval("sendText('ازاي')");
    await sleep(3000);
    check("no_stale_card_in_new_turn",
          !document.querySelector(".msg.perm"),
          chatText(document));
    check("new_turn_sent", calls.chat.length === 1 &&
          calls.chat[0] === "ازاي");
  }
  console.log(JSON.stringify(results));
  process.exit(0);
})().catch((e) => {
  console.log(JSON.stringify({ harness_error: String(e && e.stack || e) }));
  process.exit(1);
});
