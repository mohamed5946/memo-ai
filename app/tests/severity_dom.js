// LONA v1.6.1 (12+18) — headless DOM test for the permission card +
// action messages.
//
// Runs the REAL addPermCard() / finalizePermCard() / renderActionMessage()
// / pollProactiveFeed() from app/ui/app.js inside jsdom (no Windows, no
// WebView2), against the REAL index.html, with a stubbed pywebview
// bridge. Reports JSON for the Python suite
// (app/tests/test_v161_severity.py).
//
//   NODE_PATH=/tmp/jsdom-test/node_modules node app/tests/severity_dom.js
//
// Scenarios:
//   * a card per tier shows the dot in the right colour + tooltip next
//     to the Allow/Deny buttons; unknown/missing tier fails closed red.
//   * Allow/Deny are dashed .choice-card rectangles (no collapsible
//     detail, no dark-box detail block); tapping one makes it solid
//     (.picked) and dims the other, and the result line appears on the
//     SAME card -- no separate result message.
//   * timeout dims both buttons with no pick.
//   * a trial action message shows the dot pulsing while running.
//   * the proactive feed ({feed, exam} object, not an array) renders
//     routine suggestions as action messages with the dot, and plain
//     items as alert banners; the action's run sends the routine to
//     the chat for real (stubbed sendText records it).

const fs = require("fs");
const path = require("path");

const { JSDOM } = require("jsdom");

const ROOT = path.resolve(__dirname, "..", "..");
const jsSrc = fs.readFileSync(path.join(ROOT, "app", "ui", "app.js"), "utf8");
const html = fs.readFileSync(path.join(ROOT, "app", "ui", "index.html"), "utf8");

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

function makeDom(bridge) {
  const dom = new JSDOM(html, { url: "https://localhost/", runScripts: "outside-only" });
  const { window } = dom;
  const { document } = window;
  window.pywebview = { api: bridge };
  window.requestAnimationFrame = (fn) => setTimeout(fn, 0);
  const prelude = [
    "let LANG = 'ar';",
    "let permCards = {};",
    "let seenAlerts = {};",
    "let busy = false;",
    "const __sent = [];",
    "window.__sent = __sent;",
    "function sendText(text) { window.__sent.push(text); }",
    "function refreshLiveCards() {}",
    "function addMsg() {}",
  ].join("\n");
  const fns = [
    extract(jsSrc, "const I18N = {"),
    extract(jsSrc, "function t(key)"),
    extract(jsSrc, "function $(id)"),
    extract(jsSrc, "function getApi()"),
    extract(jsSrc, "const SEVERITY = {"),
    extract(jsSrc, "function severityDot(tier)"),
    extract(jsSrc, "function iconSvg(id, size)"),
    extract(jsSrc, "function addPermCard(p)"),
    extract(jsSrc, "function finalizePermCard(id, status)"),
    extract(jsSrc, "function renderActionMessage(suggestion, level, runFn)"),
    extract(jsSrc, "async function renderUndoAffordance(parentEl)"),
    extract(jsSrc, "async function pollProactiveFeed()"),
  ].join("\n");
  window.eval(prelude + "\n" + fns);
  return { window, document };
}

function dotOf(cardEl) {
  return cardEl.querySelector(".perm-btns .status-dot");
}

(async () => {
  const resolved = [];
  const bridge = {
    resolve_permission: async (id, allowed) => { resolved.push([id, allowed]); return { ok: true }; },
    list_undoable: async () => [],
    get_proactive_feed: async () => ({
      feed: [
        { id: "proactive-1", kind: "routine_suggestion",
          text: "دلوقتي معادك المعتاد: ذاكر — نبدأ؟", level: "warn" },
        { id: "proactive-2", kind: "battery_warning",
          text: "البطارية 15%", level: "warn" },
      ],
      exam: {},
    }),
  };

  // --- 1. green card: dot green, tooltip, dashed buttons, no detail ---
  {
    const { window, document } = makeDom(bridge);
    const card = window.eval(
      "addPermCard({id: 'p-green', kind: 'browser_play_youtube', summary: 'شغّل فيديو', tier: 'green'})");
    const dot = dotOf(card);
    check("green_dot_present", !!dot);
    check("green_dot_class", dot && dot.classList.contains("green"));
    check("green_dot_color", dot && dot.style.background === "rgb(62, 207, 142)",
          "bg=" + (dot && dot.style.background));
    check("green_dot_tooltip", dot && dot.title === "آمن", "title=" + (dot && dot.title));
    const btns = card.querySelectorAll(".perm-btns .choice-card");
    check("green_buttons_are_choice_cards", btns.length === 2);
    check("green_no_detail", !card.querySelector(".perm-detail") && !card.querySelector(".perm-more"));
    check("green_dot_next_to_buttons", !!card.querySelector(".perm-btns .status-dot"));
  }
  // --- 2. yellow card ---
  {
    const { window, document } = makeDom(bridge);
    const card = window.eval(
      "addPermCard({id: 'p-yellow', kind: 'create_file', summary: 'إنشاء ملف', tier: 'yellow'})");
    const dot = dotOf(card);
    check("yellow_dot_color", dot && dot.style.background === "rgb(242, 181, 68)",
          "bg=" + (dot && dot.style.background));
    check("yellow_dot_tooltip", dot && dot.title === "محتاج إذن");
  }
  // --- 3. red card ---
  {
    const { window, document } = makeDom(bridge);
    const card = window.eval(
      "addPermCard({id: 'p-red', kind: 'run_command', summary: 'تشغيل أمر', tier: 'red'})");
    const dot = dotOf(card);
    check("red_dot_color", dot && dot.style.background === "rgb(239, 100, 97)",
          "bg=" + (dot && dot.style.background));
    check("red_dot_tooltip", dot && dot.title === "خطير — إذن صريح");
  }
  // --- 4. unknown / missing tier fails closed to red ---
  {
    const { window, document } = makeDom(bridge);
    const weird = window.eval(
      "addPermCard({id: 'p-weird', kind: 'something_new', summary: 'x', tier: 'weird'})");
    const missing = window.eval(
      "addPermCard({id: 'p-missing', kind: 'something_new', summary: 'x'})");
    check("unknown_tier_red", dotOf(weird) && dotOf(weird).classList.contains("red"));
    check("missing_tier_red", dotOf(missing) && dotOf(missing).classList.contains("red"));
  }
  // --- 5. Allow: picked solid, other dimmed, result on the SAME card ---
  {
    const { window, document } = makeDom(bridge);
    const chat = document.getElementById("chat");
    const before = chat.querySelectorAll(".msg").length;
    const card = window.eval(
      "addPermCard({id: 'p-allow', kind: 'browser_play_youtube', summary: 'شغّل فيديو', tier: 'green'})");
    const [denyBtn, allowBtn] = Array.from(card.querySelectorAll(".perm-btns .choice-card"));
    allowBtn.click();
    await new Promise((r) => setTimeout(r, 50));
    check("allow_picked_solid", allowBtn.classList.contains("picked") && !allowBtn.classList.contains("dimmed"));
    check("allow_other_dimmed", denyBtn.classList.contains("dimmed") && !denyBtn.classList.contains("picked"));
    check("allow_buttons_dead", allowBtn.disabled && denyBtn.disabled);
    const state = card.querySelector(".perm-state");
    check("allow_result_on_card", !!state && /تم السماح/.test(state.textContent),
          "state=" + (state && state.textContent));
    check("allow_no_separate_message", chat.querySelectorAll(".msg").length === before + 1,
          "msgs=" + chat.querySelectorAll(".msg").length + " before=" + before);
    check("allow_resolved", resolved.some(([id, ok]) => id === "p-allow" && ok === true));
  }
  // --- 6. Deny: mirror ---
  {
    const { window, document } = makeDom(bridge);
    const chat = document.getElementById("chat");
    const before = chat.querySelectorAll(".msg").length;
    const card = window.eval(
      "addPermCard({id: 'p-deny', kind: 'run_command', summary: 'تشغيل أمر', tier: 'red'})");
    const [denyBtn, allowBtn] = Array.from(card.querySelectorAll(".perm-btns .choice-card"));
    denyBtn.click();
    await new Promise((r) => setTimeout(r, 50));
    check("deny_picked_solid", denyBtn.classList.contains("picked") && !denyBtn.classList.contains("dimmed"));
    check("deny_other_dimmed", allowBtn.classList.contains("dimmed") && !allowBtn.classList.contains("picked"));
    const state = card.querySelector(".perm-state");
    check("deny_result_on_card", !!state && /تم الرفض/.test(state.textContent),
          "state=" + (state && state.textContent));
    check("deny_no_separate_message", chat.querySelectorAll(".msg").length === before + 1);
    check("deny_resolved", resolved.some(([id, ok]) => id === "p-deny" && ok === false));
  }
  // --- 7. timeout: both dimmed, no pick, timeout line on the card ---
  {
    const { window, document } = makeDom(bridge);
    const card = window.eval(
      "addPermCard({id: 'p-timeout', kind: 'open_app', summary: 'فتح برنامج', tier: 'green'})");
    window.eval("finalizePermCard('p-timeout', 'timeout')");
    const [denyBtn, allowBtn] = Array.from(card.querySelectorAll(".perm-btns .choice-card"));
    check("timeout_both_dimmed", denyBtn.classList.contains("dimmed") && allowBtn.classList.contains("dimmed"));
    check("timeout_no_pick", !denyBtn.classList.contains("picked") && !allowBtn.classList.contains("picked"));
    const state = card.querySelector(".perm-state");
    check("timeout_line_on_card", !!state && /انتهت المهلة/.test(state.textContent),
          "state=" + (state && state.textContent));
  }
  // --- 8. trial action message: dot pulses while running ---
  {
    const { window, document } = makeDom(bridge);
    let ran = false;
    window.__runFn = async () => { ran = true; };
    const msg = window.eval("renderActionMessage('نظّف الكاش', 'yellow', window.__runFn)");
    const dot = msg.querySelector(".status-dot");
    check("action_dot_yellow", !!dot && dot.classList.contains("yellow"));
    const doBtn = msg.querySelector(".action-do");
    doBtn.click();
    check("action_dot_pulsing", dot.classList.contains("running"));
    check("action_running_state", /شغالة/.test(msg.querySelector(".action-state").textContent));
    await new Promise((r) => setTimeout(r, 50));
    check("action_dot_stops", !dot.classList.contains("running"));
    check("action_done_state", /اتعملت/.test(msg.querySelector(".action-state").textContent));
    check("action_ran", ran);
  }
  // --- 9. proactive feed: routine -> action message, info -> banner ---
  {
    const { window, document } = makeDom(bridge);
    await window.eval("pollProactiveFeed()");
    const actionMsg = document.querySelector(".action-msg");
    check("feed_routine_is_action_msg", !!actionMsg);
    const dot = actionMsg && actionMsg.querySelector(".status-dot");
    check("feed_routine_dot_yellow", !!dot && dot.classList.contains("yellow"),
          "dot=" + (dot && dot.className));
    const banner = document.querySelector(".alert-banner");
    check("feed_info_is_banner", !!banner && /البطارية 15%/.test(banner.textContent),
          "banner=" + (banner && banner.textContent));
    // The action's run sends the routine to the chat for real.
    actionMsg.querySelector(".action-do").click();
    await new Promise((r) => setTimeout(r, 50));
    const sent = window.__sent;
    check("feed_routine_sent_to_chat", sent.length === 1 && sent[0].indexOf("ذاكر") >= 0,
          "sent=" + JSON.stringify(sent));
    // A second poll does not duplicate either rendering.
    await window.eval("pollProactiveFeed()");
    check("feed_no_duplicates", document.querySelectorAll(".action-msg").length === 1
      && document.querySelectorAll(".alert-banner").length === 1);
  }

  console.log(JSON.stringify(results, null, 2));
  const failed = Object.entries(results).filter(([, r]) => !r.pass);
  if (failed.length) {
    console.error("FAILED: " + failed.map(([k]) => k).join(", "));
    process.exit(1);
  }
})().catch((e) => {
  console.error("HARNESS ERROR: " + (e && e.stack || e));
  process.exit(2);
});
