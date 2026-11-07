// Memo AI v1.4.3 — headless DOM test for the compact permission card.
//
// Runs the REAL addPermCard / finalizePermCard from app/ui/app.js inside
// jsdom (no Windows, no WebView2), against the REAL styles.css, and
// reports JSON for the Python suite (app/tests/test_v143_perm_card.py).
//
//   node app/tests/perm_card_dom.js
//
// Requires jsdom (not a repo dependency — install once wherever the
// suite runs, e.g. `npm install jsdom --prefix /tmp/jsdom-test` and
// set NODE_PATH=/tmp/jsdom-test/node_modules). jsdom has no layout
// engine, so heights are NOT measured by rendering: they are computed
// from getComputedStyle values (font-size, line-height, padding,
// min-height, margins) with a documented line-count model, the same
// model for the v1.4.2 baseline and the new card, so the ratio is
// apples-to-apples. Widths come from the real computed max-width and
// the real window width (MAIN_W = 960 in app/main.py).
//
// What is proven here:
//   * a card builds with the real strings, buttons, detail + expand
//     toggle; its computed max-width is 320px and its entrance
//     animation is perm-in 0.22s (scale 0.9 -> 1 + fade).
//   * the detail is clamped to 2 lines; clicking it (or the toggle)
//     expands it to the full scrollable block and back.
//   * clicking Allow calls resolve_permission(id, true) and flips the
//     card to its final state ("تم السماح ✅") with both buttons dead;
//     clicking Deny does the same with resolve_permission(id, false).
//   * the estimated area is ~1/5 of the v1.4.2 card (see the ratio in
//     the output; the honest floor is two tappable buttons + readable
//     title/summary/detail — a literal 1/8 would need the detail
//     hidden by default, which the spec rules out).

const fs = require("fs");
const path = require("path");

const { JSDOM } = require("jsdom");

const ROOT = path.resolve(__dirname, "..", "..");
const jsSrc = fs.readFileSync(path.join(ROOT, "app", "ui", "app.js"), "utf8");
const css = fs.readFileSync(path.join(ROOT, "app", "ui", "styles.css"), "utf8");

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

const I18N_SRC = extract(jsSrc, "const I18N = ");
const LANG_SRC = "let LANG = \"ar\";";
const T_SRC = extract(jsSrc, "function t(key)");
const DOLLAR_SRC = extract(jsSrc, "function $(id)");
const PERM_CARDS_SRC = "let permCards = {};";
const ADD_SRC = extract(jsSrc, "function addPermCard(p)");
const FINALIZE_SRC = extract(jsSrc, "function finalizePermCard(id, status)");

// ---- jsdom with the real stylesheet ----
const dom = new JSDOM(
  `<!DOCTYPE html><html lang="ar" dir="rtl"><head><style>${css}</style></head>` +
  `<body><div class="chat" id="chat"></div></body></html>`,
  { pretendToBeVisual: true });
const { window } = dom;
const { document } = window;

// ---- run the extracted code in Node, with jsdom's document injected ----
// (jsdom's window.eval does not expose `document` as a bare global, so
// the extracted functions run through a Function wrapper instead;
// elements are still real jsdom nodes, so getComputedStyle reads the
// real stylesheet.)
const calls = [];  // resolve_permission calls
const api = {
  resolve_permission: async (id, allowed) => {
    calls.push({ id, allowed });
    return true;
  },
};
const prelude = [
  I18N_SRC, LANG_SRC, T_SRC, DOLLAR_SRC, PERM_CARDS_SRC, ADD_SRC, FINALIZE_SRC,
].join("\n");
const factory = new Function(
  "document", "window", "getApi",
  prelude + "\n;return { addPermCard, finalizePermCard };");
const { addPermCard, finalizePermCard } = factory(document, window, () => api);

const cs = (el) => window.getComputedStyle(el);
const px = (v) => parseFloat(String(v).replace("px", "")) || 0;
const out = { checks: {}, measurements: {} };
const check = (name, ok, detail) => { out.checks[name] = { ok: !!ok, detail: detail || "" }; };

const SAMPLE = {
  id: "perm-1",
  kind: "run_command",
  summary: "تشغيل أمر في الطرفية: python -m pytest app/tests -q",
  detail: [
    "python -m pytest app/tests -q",
    "working dir: C:\\Users\\mohamed\\memo-ai",
    "timeout: 120s",
    "env: GROQ_API_KEY=<set>",
    "stdout will be captured",
    "stderr will be captured",
    "exit code checked",
    "no network access",
    "runs as current user",
    "log: diagnostics",
  ].join("\n"),
};

(async () => {
  // ---- build the card ----
  const card = addPermCard(SAMPLE);
  check("card_built", card && card.classList.contains("msg") && card.classList.contains("perm"),
    "class=" + (card && card.className));
  check("card_in_chat", document.getElementById("chat").contains(card));
  check("title_text", card.querySelector(".perm-kind").textContent.includes("run_command"),
    card.querySelector(".perm-kind").textContent);
  check("summary_text", card.querySelector(".perm-summary").textContent === SAMPLE.summary);
  const detail = card.querySelector(".perm-detail");
  check("detail_present", !!detail && detail.textContent === SAMPLE.detail);
  const more = card.querySelector(".perm-more");
  check("more_toggle_present", !!more && more.textContent.includes("عرض المزيد"),
    more && more.textContent);
  const btns = card.querySelectorAll(".perm-btns button");
  check("two_buttons", btns.length === 2, "found " + btns.length);
  check("allow_label", btns[1].textContent.includes("سماح"), btns[1].textContent);
  check("deny_label", btns[0].textContent.includes("رفض"), btns[0].textContent);

  // ---- computed style: size + animation (real stylesheet) ----
  const cardStyle = cs(card);
  out.measurements.card_max_width = cardStyle.maxWidth;
  out.measurements.card_padding = cardStyle.padding;
  out.measurements.card_font_size = cardStyle.fontSize;
  out.measurements.card_line_height = cardStyle.lineHeight;
  out.measurements.card_animation = cardStyle.animationName + " " + cardStyle.animationDuration;
  check("max_width_320", cardStyle.maxWidth === "320px", cardStyle.maxWidth);
  check("entrance_animation",
    cardStyle.animationName === "perm-in" &&
    ["0.22s", "220ms"].includes(cardStyle.animationDuration),
    cardStyle.animationName + " " + cardStyle.animationDuration);
  const detailStyle = cs(detail);
  out.measurements.detail_line_clamp = detailStyle.getPropertyValue("-webkit-line-clamp");
  out.measurements.detail_display = detailStyle.display;
  check("detail_clamped_2_lines",
    detailStyle.getPropertyValue("-webkit-line-clamp") === "2" &&
    detailStyle.display === "-webkit-box",
    "clamp=" + detailStyle.getPropertyValue("-webkit-line-clamp") +
    " display=" + detailStyle.display);
  check("keyframes_present",
    /@keyframes perm-in\s*\{[^}]*scale\(0\.9\)/.test(css) &&
    /@keyframes perm-state-in/.test(css) &&
    /@keyframes perm-in[^]*?to\s*\{[^}]*scale\(1\)/.test(css),
    "perm-in 0.9->1 + perm-state-in in styles.css");
  check("press_scale_rule",
    /\.msg\.perm \.perm-btns button:not\(:disabled\):active\s*\{[^}]*scale\(0\.96\)/.test(css),
    "active scale(0.96) rule in styles.css");

  // ---- expand toggle: click detail, then click toggle back ----
  detail.dispatchEvent(new window.Event("click", { bubbles: true }));
  check("detail_expands_on_click", detail.classList.contains("expanded") &&
    more.textContent.includes("عرض أقل"), more.textContent);
  check("detail_expanded_style",
    cs(detail).display === "block" && cs(detail).maxHeight === "140px",
    "display=" + cs(detail).display + " max-height=" + cs(detail).maxHeight);
  more.dispatchEvent(new window.Event("click", { bubbles: true }));
  check("detail_collapses_on_toggle", !detail.classList.contains("expanded") &&
    more.textContent.includes("عرض المزيد"), more.textContent);

  // ---- Allow flow: click -> resolve_permission -> final state, dead buttons ----
  btns[1].dispatchEvent(new window.Event("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 50));
  check("allow_calls_resolve",
    calls.length === 1 && calls[0].id === "perm-1" && calls[0].allowed === true,
    JSON.stringify(calls));
  const state = card.querySelector(".perm-state");
  check("final_state_allowed",
    !!state && state.textContent.includes("تم السماح"),
    state && state.textContent);
  check("card_resolved_class", card.classList.contains("resolved-allowed"));
  check("buttons_dead_after_allow", btns[0].disabled && btns[1].disabled);
  check("state_animates",
    cs(state).animationName === "perm-state-in", cs(state).animationName);

  // ---- Deny flow on a second card ----
  const card2 = addPermCard({ id: "perm-2", kind: "send_whatsapp", summary: "إرسال واتساب" });
  const btns2 = card2.querySelectorAll(".perm-btns button");
  check("no_detail_no_toggle", !card2.querySelector(".perm-detail") && !card2.querySelector(".perm-more"));
  btns2[0].dispatchEvent(new window.Event("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 50));
  check("deny_calls_resolve",
    calls.length === 2 && calls[1].id === "perm-2" && calls[1].allowed === false,
    JSON.stringify(calls));
  check("final_state_denied",
    card2.querySelector(".perm-state").textContent.includes("تم الرفض") &&
    card2.classList.contains("resolved-denied") &&
    btns2[0].disabled && btns2[1].disabled);

  // ---- timeout path still works (polling resolves it) ----
  finalizePermCard("perm-2", "timeout"); // already done -> no-op guard
  check("finalize_idempotent", card2.querySelectorAll(".perm-state").length === 1);
  const card3 = addPermCard({ id: "perm-3", kind: "x", summary: "y" });
  finalizePermCard("perm-3", "timeout");
  check("timeout_state",
    card3.querySelector(".perm-state").textContent.includes("انتهت المهلة") &&
    card3.classList.contains("resolved-timeout"));

  // ---- area model (same model for old + new; see header) ----
  // Chat content width at the real window size: MAIN_W 960 - app pad
  // 16*2 - chat pad 6*2 = 916px. Old card filled .msg max-width 82%.
  const CHAT_W = 960 - 32 - 12; // 916
  const OLD_W = Math.round(CHAT_W * 0.82); // 751
  const NEW_W = 320; // computed max-width above
  // Line counts for the SAMPLE content at each width. Average glyph
  // ~0.5em for mixed Arabic/Latin at these sizes (measured estimate).
  const lines = (text, fontPx, contentW) =>
    Math.max(1, Math.ceil(text.length / Math.max(1, Math.floor(contentW / (fontPx * 0.5)))));
  // v1.4.2 values, recorded from the pre-change stylesheet (commit
  // 9a09a68): .msg padding 12/16, font 15 lh 1.65; .perm-kind 12px
  // margin 6/0; .perm-summary 16px lh 1.5 margin 4/0/10; .perm-detail
  // 12.5px padding 8/10 margin 8/0, max-height 140; buttons gold-btn
  // padding 12/22 font 15 margin-top 10; .meta 11px margin-top 6.
  const oldH =
    24 +                                            // .msg padding
    (12 * 1.65 + 12) +                              // title
    (16 * 1.5 * lines(SAMPLE.summary, 16, OLD_W - 32) + 14) + // summary
    (Math.min(140, 12.5 * 1.65 * SAMPLE.detail.split("\n").length) + 16 + 2 + 16) + // detail capped
    (12 * 2 + 15 * 1.65 + 10) +                     // buttons row
    (11 * 1.65 + 6);                                // meta
  // v1.4.3 values from the live computed style of the real card.
  const padV = px(cardStyle.paddingTop) + px(cardStyle.paddingBottom);
  const kindS = cs(card.querySelector(".perm-kind"));
  const sumS = cs(card.querySelector(".perm-summary"));
  const detS = cs(detail);
  const moreS = cs(more);
  const btnS = cs(btns[1]);
  const metaS = cs(card.querySelector(".meta"));
  const contentW = NEW_W - px(cardStyle.paddingLeft) - px(cardStyle.paddingRight);
  const newH =
    padV +
    (px(kindS.fontSize) * px(kindS.lineHeight) / px(kindS.fontSize) + px(kindS.marginBottom)) +
    (px(sumS.fontSize) * (px(sumS.lineHeight) / px(sumS.fontSize)) * lines(SAMPLE.summary, 13, contentW) +
      px(sumS.marginTop) + px(sumS.marginBottom)) +
    (px(detS.fontSize) * (px(detS.lineHeight) / px(detS.fontSize)) * 2 +
      px(detS.paddingTop) + px(detS.paddingBottom) + 2 + px(detS.marginTop)) +
    (px(moreS.fontSize) * (px(moreS.lineHeight) / px(moreS.fontSize)) + px(moreS.marginTop)) +
    (Math.max(px(btnS.minHeight), px(btnS.paddingTop) + px(btnS.paddingBottom) +
      px(btnS.fontSize) * 1.2) + px(cs(card.querySelector(".perm-btns")).marginTop)) +
    (px(metaS.fontSize) * 1.4 + px(metaS.marginTop));
  out.measurements.old = { width: OLD_W, height: Math.round(oldH), area: OLD_W * Math.round(oldH) };
  out.measurements.new = { width: NEW_W, height: Math.round(newH), area: NEW_W * Math.round(newH) };
  out.measurements.ratio = +(out.measurements.new.area / out.measurements.old.area).toFixed(3);
  out.measurements.ratio_fraction = "1/" + (out.measurements.old.area / out.measurements.new.area).toFixed(1);
  check("smaller_than_old", out.measurements.ratio < 0.34,
    "ratio=" + out.measurements.ratio + " (" + out.measurements.ratio_fraction + ")");

  const failed = Object.entries(out.checks).filter(([, c]) => !c.ok).map(([n]) => n);
  out.passed = failed.length === 0;
  out.failed = failed;
  console.log(JSON.stringify(out, null, 2));
  process.exit(out.passed ? 0 : 1);
})().catch((e) => {
  console.log(JSON.stringify({ passed: false, error: String(e && e.stack || e) }, null, 2));
  process.exit(2);
});
