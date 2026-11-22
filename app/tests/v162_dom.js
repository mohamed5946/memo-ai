// LONA v1.6.2 — headless DOM tests for the frontend work.
//
// Runs the REAL functions from app/ui/app.js + app/ui/island.html
// inside jsdom, with a stubbed pywebview bridge. Reports JSON for the
// Python suite (app/tests/test_v162_ui.py).
//
//   NODE_PATH=/tmp/jsdom-test/node_modules node app/tests/v162_dom.js
//
// Scenarios:
//   * chat_timeout: bridge.chat() never resolves -> 30s race (hook)
//     shows a failure message, frees busy, drains the queue with a
//     failure message instead of silently queuing.
//   * bridge_late: no bridge at first, it arrives 300ms in -> the
//     wait/retry (250ms polls, ~10s) lets the send succeed.
//   * live_card: a device question earns a live card with REAL
//     get_device_overview() numbers + "آخر قراءة: مباشر HH:MM:SS" +
//     a programs button that opens the device view.
//   * apikey_show: "إظهار" on an empty field fetches the saved value
//     via get_api_key_value(); a user-typed value is only toggled.
//   * apikey_test_pending: test_api_key() pending -> poll the result.
//   * model_picker: menu opens with 4 options, unavailable ones
//     disabled with their reason; picking calls set_model_choice.
//   * perm_timeout: resolve_permission() never resolves -> the card
//     finalizes as timeout, never allowed/denied.
//   * voice_timeout: voice_chat() never resolves -> 60s race cleans
//     mic + status + busy and shows a failure message.
//   * updates_poll: check_updates() checking -> poll the new getter.
//   * island_cap: island chat_result() never finishes -> the 150s cap
//     WRITES a failure message into exp-reply (never "بيفكّر..." forever).

const fs = require("fs");
const path = require("path");

const { JSDOM } = require("jsdom");

const ROOT = path.resolve(__dirname, "..", "..");
const jsSrc = fs.readFileSync(path.join(ROOT, "app", "ui", "app.js"), "utf8");
const islandSrc = fs.readFileSync(path.join(ROOT, "app", "ui", "island.html"), "utf8");

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

function makeAppDom(opts) {
  opts = opts || {};
  const dom = new JSDOM(
    `<!DOCTYPE html><html><body>
      <div id="chat"></div>
      <input id="text-input" />
      <div id="status"></div>
      <button id="mic-btn"></button>
      <div class="model-wrap"><button id="model-btn">تلقائي</button>
      <div id="model-menu"></div></div>
      <input id="api-key-groq" type="password" />
      <button id="api-show-groq" type="button">إظهار</button>
      <button id="api-test-groq" type="button">اختبار الاتصال</button>
      <span id="api-badge-groq"></span>
      <span id="api-status-groq"></span>
      <div id="robot-stage"></div>
      <header id="app-header">
        <div class="win-controls">
          <button class="win-btn close" id="win-close" type="button"></button>
          <button class="win-btn min" id="win-min" type="button"></button>
          <button class="win-btn max" id="win-max" type="button"></button>
        </div>
        <span class="header-clock" id="header-clock">--:--</span>
        <span class="model-indicator" id="model-indicator">تلقائي</span>
      </header>
      <div class="lightbox" id="lightbox" hidden><img id="lightbox-img" alt="صورة مولّدة"><button class="lightbox-close" id="lightbox-close" type="button">×</button></div>
    </body></html>`,
    { url: "https://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
  const { window } = dom;
  const { document } = window;
  if (opts.chatTimeoutMs) window.__v162ChatTimeoutMs = opts.chatTimeoutMs;
  if (opts.permTimeoutMs) window.__v162PermTimeoutMs = opts.permTimeoutMs;
  if (opts.voiceTimeoutMs) window.__v162VoiceTimeoutMs = opts.voiceTimeoutMs;
  if (opts.bridgeWaitMs) window.__bridgeWaitMs = opts.bridgeWaitMs;

  const calls = { chat: [], getKeyValue: 0, setModel: [], testKey: 0,
                  testKeyResult: 0, checkResult: 0, resolve: [] };
  let chatN = 0;
  const polls = {};
  let keyResultCalls = 0;
  let checkResultCalls = 0;
  const bridge = {
    chat: async (text) => {
      chatN += 1;
      calls.chat.push(text);
      if (opts.chatHang) return new Promise(() => {});
      if (opts.chatPending) return { pending: true, chat_id: "c" + chatN };
      return { reply: opts.chatReply || "تمام", engine: "groq" };
    },
    chat_result: async (chatId) => {
      polls[chatId] = (polls[chatId] || 0) + 1;
      if (opts.neverDone) return { done: false };
      if (polls[chatId] >= (opts.pollsToDone || 2)) {
        return { done: true, reply: "رد الجهاز تمام", engine: "groq", images: [] };
      }
      return { done: false };
    },
    get_pending_permission: async () => ({ pending: null, resolved: [] }),
    resolve_permission: async (id, allowed) => {
      calls.resolve.push([id, allowed]);
      if (opts.resolveHang) return new Promise(() => {});
      return { ok: true };
    },
    cancel_pending_permissions: async () => ({ ok: true, cancelled: 0 }),
    get_device_overview: async () => ({
      overview: {
        ram: { percent: 78, used_mb: 6348, total_mb: 8192 },
        cpu: { percent: 34 },
        battery: { percent: 82 },
        disk: { free_gb: 44, total_gb: 256, percent_free: 17.2 },
        temp_c: 61,
      },
    }),
    get_api_key_value: async (provider) => {
      calls.getKeyValue += 1;
      if (opts.noSavedKey) return { ok: true, value: null };
      return { ok: true, value: "sk-saved-123" };
    },
    test_api_key: async (p) => {
      calls.testKey += 1;
      if (opts.testPending) return { pending: true, id: "t1" };
      return { ok: true, valid: true };
    },
    get_test_api_key_result: async (id) => {
      calls.testKeyResult += 1;
      keyResultCalls += 1;
      if (keyResultCalls >= 2) return { pending: false, ok: true, valid: true, detail: "ok" };
      return { pending: true };
    },
    get_model_options: async () => ({
      choice: "auto",
      options: [
        { id: "auto", label: "تلقائي", available: true, reason: "" },
        { id: "groq", label: "Groq — gpt-oss-120b", available: true, reason: "" },
        { id: "gemini", label: "Gemini 2.5 Flash", available: false, reason: "محتاج مفتاح Gemini" },
        { id: "local", label: "محلي — Ollama Qwen 2.5 3B", available: false, reason: "Ollama مش متثبت" },
      ],
    }),
    set_model_choice: async (choice) => {
      calls.setModel.push(choice);
      return { ok: true, choice };
    },
    get_check_updates_result: async () => {
      calls.checkResult += 1;
      checkResultCalls += 1;
      if (checkResultCalls >= 2) {
        return { checking: false, update: { version: "1.6.2", features: [], notes: "" } };
      }
      return { checking: true };
    },
    window_minimize: async () => { calls.winMin = (calls.winMin || 0) + 1; return { ok: true }; },
    window_toggle_maximize: async () => { calls.winMax = (calls.winMax || 0) + 1; return { maximized: true }; },
    window_close: async () => { calls.winClose = (calls.winClose || 0) + 1; return { ok: true }; },
    window_move_to: async (x, y) => { (calls.moves = calls.moves || []).push([x, y]); },
    get_generated_image: async (name) => ({ ok: true, data_url: "data:image/png;base64,AAA" }),
    voice_chat: async () => {
      if (opts.voiceHang) return new Promise(() => {});
      return { heard: "حالة الجهاز إيه؟", reply: "الجهاز تمام", engine: "groq" };
    },
  };
  if (!opts.noBridgeAtStart) window.pywebview = { api: bridge };
  window.__stubBridge = bridge;

  const prelude = [
    "let LANG = 'ar';",
    "let busy = false;",
    "let permCards = {};",
    "let permPollTimer = null;",
    "let heroFlown = false;",
    "let currentMood = 'صاحي';",
    "let thinkingActive = false, thinkingPrevMood = null;",
    "let _sendQueue = [];",
    "let permPollInFlight = false;",
    "let liveDeviceCards = [];",
    "let _dragOffX = 0, _dragOffY = 0, _dragging = false, _dragRaf = 0;",
    "let currentModelChoice = 'auto';",
    "const MOODS = {'صاحي': 1, 'بيفكر': 1};",
    "function setMood(m) { window.__moods.push(m); }",
    "window.__moods = [];",
    "function $(id) { return document.getElementById(id); }",
    "function getApi() { return (window.pywebview && window.pywebview.api) ? window.pywebview.api : null; }",
    "window.__isBusy = () => busy;",
    "window.__queueLen = () => _sendQueue.length;",
    "function showView(name) { window.__view = name; }",
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
    extract(jsSrc, "function waitForBridge(timeoutMs)"),
    extract(jsSrc, "function raceBridgeTimeout(promise"),
    extract(jsSrc, "function waitForPendingResult(pollFn"),
    extract(jsSrc, "function pollTestApiKeyResult(b, id)"),
    extract(jsSrc, "function isDeviceQuestion(text)"),
    extract(jsSrc, "function _fmtGB(mb)"),
    extract(jsSrc, "function renderLiveDeviceCard()"),
    extract(jsSrc, "async function refreshLiveDeviceCards()"),
    extract(jsSrc, "function maybeShowDeviceCard(text)"),
    extract(jsSrc, "async function sendText(text"),
    extract(jsSrc, "function waitForChatResult(chatId)"),
    extract(jsSrc, "function startPermPolling()"),
    extract(jsSrc, "async function stopPermPolling()"),
    extract(jsSrc, "const SEVERITY = {"),
    extract(jsSrc, "function severityDot(tier)"),
    extract(jsSrc, "function iconSvg(id, size)"),
    extract(jsSrc, "function addPermCard(p)"),
    extract(jsSrc, "function finalizePermCard(id, status)"),
    extract(jsSrc, "async function voiceChat()"),
    extract(jsSrc, "const MODEL_SHORT = {"),
    // MODEL_FALLBACK_OPTIONS is an array literal (brace matching
    // cannot extract it), so mirror its 4 entries here.
    `const MODEL_FALLBACK_OPTIONS = [
      { id: "auto", label: "تلقائي", available: true, reason: "" },
      { id: "groq", label: "Groq — gpt-oss-120b", available: false, reason: "غير متاح في النسخة دي" },
      { id: "gemini", label: "Gemini 2.5 Flash", available: false, reason: "محتاج مفتاح Gemini" },
      { id: "local", label: "محلي — Ollama Qwen 2.5 3B", available: false, reason: "Ollama مش متثبت" },
    ];`,
    extract(jsSrc, "function applyModelChoice(choice)"),
    extract(jsSrc, "function renderModelMenu(options)"),
    extract(jsSrc, "async function refreshModelOptions()"),
    extract(jsSrc, "function wireModelPicker()"),
    extract(jsSrc, "function apiKeyBadge(provider)"),
    extract(jsSrc, "function wireApiKeys()"),
    extract(jsSrc, "function pollCheckUpdatesResult(b)"),
    extract(jsSrc, "function openLightbox(dataUrl)"),
    extract(jsSrc, "function closeLightbox()"),
    extract(jsSrc, "function wireLightbox()"),
    extract(jsSrc, "function wireWindowControls()"),
    extract(jsSrc, "function tickHeaderClock()"),
  ].join("\n");
  window.eval(prelude + "\n" + fns);
  return { window, document, calls, bridge };
}

function chatText(document) {
  return Array.from(document.querySelectorAll("#chat .msg"))
    .map((el) => el.textContent).join(" | ");
}

(async () => {
  // --- 1. chat timeout: hung bridge.chat, queued message drains with failure ---
  {
    const { window, document, calls } = makeAppDom({ chatHang: true, chatTimeoutMs: 80 });
    const p1 = window.eval("sendText('هلو')");
    await sleep(30);
    check("timeout_busy_while_hung", window.eval("__isBusy()"));
    window.eval("sendText('تاني')");
    await sleep(30);
    check("timeout_queued_bubble_shown", chatText(document).includes("تاني"), chatText(document));
    check("timeout_queue_len_1", window.eval("__queueLen()") === 1);
    await p1;
    await sleep(50);
    const text = chatText(document);
    check("timeout_failure_shown", text.includes("المحرك مش بيرد"), text);
    check("timeout_busy_false", !window.eval("__isBusy()"));
    check("timeout_queue_drained", window.eval("__queueLen()") === 0);
    check("timeout_queue_got_failure",
      (text.match(/المحرك مش بيرد/g) || []).length >= 2, text);
  }

  // --- 2. bridge arrives late: wait/retry lets the send succeed ---
  {
    const { window, document } = makeAppDom({ noBridgeAtStart: true, bridgeWaitMs: 3000 });
    const p = window.eval("sendText('هلو')");
    await sleep(300);
    check("late_waiting_text_shown",
      document.getElementById("status").textContent.includes("لسه بيجهز")
      || chatText(document).includes("لسه بيجهز"),
      chatText(document) + " / " + document.getElementById("status").textContent);
    // The bridge arrives now (late injection, like MSHTML).
    window.eval("window.pywebview = { api: window.__stubBridge };");
    await sleep(100);
    check("late_no_webview2_mention", !chatText(document).includes("WebView2"));
    await p;
  }

  // --- 3. live device card with real numbers ---
  {
    const { window, document } = makeAppDom({});
    await window.eval("sendText('حالة الجهاز إيه؟')");
    await sleep(80);
    const card = document.querySelector(".live-device-msg");
    check("live_card_rendered", !!card);
    const text = card ? card.textContent : "";
    check("live_card_header", text.includes("دي حالة جهازك دلوقتي"), text);
    check("live_card_ram_real", text.includes("6.2 / 8.0 GB"), text);
    check("live_card_cpu_real", text.includes("34%"), text);
    check("live_card_batt_real", text.includes("82%"), text);
    check("live_card_time", /آخر قراءة: مباشر \d{2}:\d{2}:\d{2}/.test(text), text);
    check("live_card_no_placeholder", !text.includes("62%") || text.includes("6.2 / 8 GB"), text);
    const btn = card && card.querySelector(".live-programs-btn");
    check("live_card_programs_btn", !!btn && btn.textContent.includes("شوف قايمة البرامج"));
    if (btn) {
      btn.click();
      check("live_card_btn_opens_device", window.__view === "device", String(window.__view));
    }
  }

  // --- 4. API-key show button fetches the saved value ---
  {
    const { window, document, calls } = makeAppDom({});
    window.eval("wireApiKeys()");
    const input = document.getElementById("api-key-groq");
    const showBtn = document.getElementById("api-show-groq");
    showBtn.click();
    await sleep(60);
    check("apikey_show_fills_saved", input.value === "sk-saved-123", input.value);
    check("apikey_show_reveals", input.type === "text", input.type);
    check("apikey_show_fetched_once", calls.getKeyValue === 1, String(calls.getKeyValue));
    showBtn.click();
    await sleep(30);
    check("apikey_hide_back_to_password", input.type === "password", input.type);
    input.value = "user-typed-key";
    showBtn.click();
    await sleep(30);
    check("apikey_user_typed_only_toggles", input.type === "text" && input.value === "user-typed-key", input.type + "/" + input.value);
    check("apikey_user_typed_no_fetch", calls.getKeyValue === 1, String(calls.getKeyValue));
  }

  // --- 5. test_api_key pending -> poll ---
  {
    const { window, document, calls } = makeAppDom({ testPending: true });
    window.eval("wireApiKeys()");
    document.getElementById("api-test-groq").click();
    await sleep(900);
    check("apikey_test_pending_ok",
      document.getElementById("api-status-groq").textContent.includes("الاتصال تمام"),
      document.getElementById("api-status-groq").textContent);
    check("apikey_test_polled", calls.testKeyResult >= 1, String(calls.testKeyResult));
  }

  // --- 6. model picker ---
  {
    const { window, document, calls } = makeAppDom({});
    window.eval("wireModelPicker()");
    await window.eval("refreshModelOptions()");
    check("model_btn_default", document.getElementById("model-btn").textContent === "تلقائي",
      document.getElementById("model-btn").textContent);
    document.getElementById("model-btn").click();
    await sleep(60);
    const menu = document.getElementById("model-menu");
    const btns = Array.from(menu.querySelectorAll("button"));
    check("model_menu_opens_4", menu.classList.contains("open") && btns.length === 4,
      "btns=" + btns.length);
    const geminiBtn = btns.find((b) => b.textContent.includes("Gemini"));
    const localBtn = btns.find((b) => b.textContent.includes("Ollama"));
    check("model_gemini_disabled_with_reason",
      !!geminiBtn && geminiBtn.disabled && geminiBtn.textContent.includes("محتاج مفتاح Gemini"),
      geminiBtn ? geminiBtn.textContent : "missing");
    check("model_local_disabled_with_reason",
      !!localBtn && localBtn.disabled && localBtn.textContent.includes("Ollama مش متثبت"),
      localBtn ? localBtn.textContent : "missing");
    const groqBtn = btns.find((b) => b.textContent.includes("Groq"));
    groqBtn.click();
    await sleep(60);
    check("model_pick_calls_bridge", calls.setModel.length === 1 && calls.setModel[0] === "groq",
      JSON.stringify(calls.setModel));
    check("model_btn_updates", document.getElementById("model-btn").textContent === "Groq",
      document.getElementById("model-btn").textContent);
    const before = calls.setModel.length;
    geminiBtn.click();
    await sleep(40);
    check("model_disabled_not_clickable", calls.setModel.length === before,
      String(calls.setModel.length));
  }

  // --- 7. permission resolve timeout -> timeout, never allowed/denied ---
  {
    const { window, document } = makeAppDom({ resolveHang: true, permTimeoutMs: 80 });
    window.eval("addPermCard({id: 'p9', kind: 'test', summary: 'اختبار', tier: 'red'})");
    const card = document.querySelector(".msg.perm");
    const allowBtn = card.querySelectorAll(".perm-btns button")[1];
    allowBtn.click();
    await sleep(250);
    check("perm_timeout_resolved", card.classList.contains("resolved-timeout"),
      card.className);
    const state = card.querySelector(".perm-state");
    check("perm_timeout_text", !!state && state.textContent.includes("انتهت المهلة"),
      state ? state.textContent : "no state");
    check("perm_timeout_no_pick",
      !allowBtn.classList.contains("picked"), allowBtn.className);
  }

  // --- 8. voice chat timeout -> mic/status/busy cleaned ---
  {
    const { window, document } = makeAppDom({ voiceHang: true, voiceTimeoutMs: 80 });
    const p = window.eval("voiceChat()");
    await sleep(250);
    await p;
    const mic = document.getElementById("mic-btn");
    check("voice_timeout_failure", chatText(document).includes("المحرك مش بيرد"), chatText(document));
    check("voice_timeout_mic_clean", !mic.classList.contains("recording"), mic.className);
    check("voice_timeout_status_clean", document.getElementById("status").textContent === "",
      document.getElementById("status").textContent);
    check("voice_timeout_busy_false", !window.eval("__isBusy()"));
  }

  // --- 9. check_updates polling ---
  {
    const { window } = makeAppDom({});
    const r = await window.eval("pollCheckUpdatesResult(window.pywebview.api)");
    check("updates_poll_available", r && r.status === "update_available" && r.version === "1.6.2",
      JSON.stringify(r));
    const r2 = await window.eval("pollCheckUpdatesResult({})");
    check("updates_poll_no_getter_timed_out", r2 && r2.timedOut === true, JSON.stringify(r2));
  }

  // --- 11. generated image: capped in chat, click opens lightbox ---
  {
    const { window, document } = makeAppDom({});
    window.eval("wireLightbox()");
    await window.eval("addGeneratedImages(['a.png'])");
    await sleep(30);
    const img = document.querySelector(".gen-img");
    check("genimg_capped_class", !!img, img ? img.className : "no img");
    img.click();
    check("lightbox_opens", !document.getElementById("lightbox").hidden
      && document.getElementById("lightbox-img").src.includes("base64"),
      document.getElementById("lightbox").hidden ? "still hidden" : "open");
    document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    check("lightbox_esc_closes", document.getElementById("lightbox").hidden);
    img.click();
    document.getElementById("lightbox").click();
    check("lightbox_click_closes", document.getElementById("lightbox").hidden);
  }

  // --- 12. frameless window controls + drag ---
  {
    const { window, document, calls } = makeAppDom({});
    window.eval("wireWindowControls()");
    document.getElementById("win-min").click();
    await sleep(30);
    check("win_min_calls_bridge", (calls.winMin || 0) === 1, String(calls.winMin));
    document.getElementById("win-max").click();
    await sleep(30);
    check("win_max_calls_bridge", (calls.winMax || 0) === 1, String(calls.winMax));
    document.getElementById("win-close").click();
    await sleep(30);
    check("win_close_calls_bridge", (calls.winClose || 0) === 1, String(calls.winClose));
    const header = document.getElementById("app-header");
    header.dispatchEvent(new window.MouseEvent("pointerdown", { screenX: 100, screenY: 80, bubbles: true }));
    header.dispatchEvent(new window.MouseEvent("pointermove", { screenX: 150, screenY: 120, bubbles: true }));
    await sleep(60);
    check("win_drag_moves", (calls.moves || []).length >= 1
      && calls.moves[0][0] === 50 && calls.moves[0][1] === 40,
      JSON.stringify(calls.moves || []));
    header.dispatchEvent(new window.MouseEvent("pointerup", { bubbles: true }));
  }

  // --- 13. header clock + model indicator follow the choice ---
  {
    const { window, document } = makeAppDom({});
    window.eval("tickHeaderClock()");
    check("header_clock_shows_time",
      /^\d{2}:\d{2}$/.test(document.getElementById("header-clock").textContent),
      document.getElementById("header-clock").textContent);
    window.eval("wireModelPicker()");
    await window.eval("refreshModelOptions()");
    document.getElementById("model-btn").click();
    await sleep(60);
    const groqBtn = Array.from(document.querySelectorAll("#model-menu button"))
      .find((b) => b.textContent.includes("Groq"));
    groqBtn.click();
    await sleep(60);
    check("model_indicator_updates",
      document.getElementById("model-indicator").textContent === "Groq",
      document.getElementById("model-indicator").textContent);
  }

  // --- 10. island: cap writes a failure into exp-reply ---
  {
    const dom = new JSDOM(islandSrc, {
      url: "https://localhost/",
      runScripts: "dangerously",
      pretendToBeVisual: true,
      beforeParse(window) {
        window.__islandCapMs = 80;
        window.pywebview = {
          api: {
            chat: async () => ({ pending: true, chat_id: "c1" }),
            chat_result: async () => ({ done: false }),
          },
        };
      },
    });
    const { window } = dom;
    const { document } = window;
    await sleep(50);
    check("island_no_mic_btn", !document.getElementById("island-mic"));
    check("island_no_settings_btn", !document.getElementById("island-settings"));
    check("island_no_clock_on_pill", !document.getElementById("island-clock"));
    check("island_clock_inside_expanded", !!document.getElementById("exp-clock"));
    check("island_no_actions", !document.querySelector(".exp-actions"));
    check("island_no_panels", !document.getElementById("exp-panels"));
    document.getElementById("exp-input").value = "هلو";
    document.getElementById("exp-send").click();
    await sleep(120);
    const reply = document.getElementById("exp-reply").textContent;
    check("island_cap_writes_failure", reply.includes("الرد اتأخر أوي"), reply);
    check("island_cap_not_thinking_forever", reply !== "بيفكّر...", reply);
    dom.window.close();
  }

  process.stdout.write(JSON.stringify(results) + "\n");
})().catch((e) => {
  process.stdout.write(JSON.stringify({ harness_error: String(e && e.stack || e) }) + "\n");
});
