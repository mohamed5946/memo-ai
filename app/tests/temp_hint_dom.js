// LONA v1.6.1 (11b) — headless DOM test for the temperature hint.
//
// Runs the REAL refreshDeviceOverview() from app/ui/app.js inside jsdom
// (no Windows, no WebView2), against the REAL index.html + styles.css,
// with a stubbed pywebview bridge returning overviews in the exact
// contract shape workstream D agreed: temp_hint {text, url} is present
// ONLY while the temperature is unavailable, and absent/None when it
// is available. Reports JSON for the Python suite
// (app/tests/test_v161_readings.py).
//
//   node app/tests/temp_hint_dom.js
//
// Requires jsdom (not a repo dependency — install once wherever the
// suite runs, e.g. `npm install jsdom --prefix /tmp/jsdom-test` and
// set NODE_PATH=/tmp/jsdom-test/node_modules).

const fs = require("fs");
const path = require("path");

const { JSDOM } = require("jsdom");

const ROOT = path.resolve(__dirname, "..", "..");
const jsSrc = fs.readFileSync(path.join(ROOT, "app", "ui", "app.js"), "utf8");
const html = fs.readFileSync(path.join(ROOT, "app", "ui", "index.html"), "utf8");
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

// For a statement block (not a function body) slice to its end marker.
function extractStatement(src, startMarker, endMarker) {
  const start = src.indexOf(startMarker);
  if (start < 0) throw new Error("marker not found: " + startMarker);
  const end = src.indexOf(endMarker, start);
  if (end < 0) throw new Error("end marker not found: " + endMarker);
  return src.slice(start, end + endMarker.length);
}

const DOLLAR_SRC = extract(jsSrc, "function $(id)");
const GETAPI_SRC = extract(jsSrc, "function getApi()");
const COUNTUP_SRC = extract(jsSrc, "function animateCountUps()");
const PROCLIST_SRC = extract(jsSrc, "function renderProcList(rows)");
const REFRESH_SRC = extract(jsSrc, "async function refreshDeviceOverview()");
const FLAG_SRC = "let tempHintDismissed = false;";
const DISMISS_SRC = extractStatement(jsSrc, "const thd = $(\"temp-hint-dismiss\");", "  });");

const HINT_TEXT = "الحرارة مش متاحة على الجهاز ده — ثبّت LibreHardwareMonitor وخليه شغال في الخلفية عشان تظهر";
const HINT_URL = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases";

function overview(o) {
  return Object.assign({
    ram: { percent: 50.0 }, cpu: { percent: 10.0 },
    battery: { percent: 80, charging: false },
    temp_c: null, temp_available: false, top_processes: [],
    mood: "صاحي", mood_reason: "",
  }, o);
}

function freshSession() {
  const dom = new JSDOM(html, { url: "http://localhost/", pretendToBeVisual: true });
  const { window } = dom;
  const { document } = window;
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);
  let current = null;
  window.pywebview = { api: { get_device_overview: async () => ({ ok: true, overview: current }) } };
  // jsdom's window.eval does not expose `document` as a bare global, so
  // the extracted functions run through a Function wrapper instead
  // (same approach as app/tests/severity_dom.js).
  const prelude = [DOLLAR_SRC, GETAPI_SRC, COUNTUP_SRC, PROCLIST_SRC,
                   FLAG_SRC, REFRESH_SRC, DISMISS_SRC].join("\n");
  const factory = new Function(
    "document", "window", "performance", "requestAnimationFrame",
    prelude + "\n;return { refreshDeviceOverview };");
  const api = factory(document, window, window.performance,
                      window.requestAnimationFrame.bind(window));
  const hint = () => document.getElementById("temp-hint");
  return {
    window,
    setOverview: (o) => { current = o; },
    refresh: () => api.refreshDeviceOverview(),
    dismiss: () => document.getElementById("temp-hint-dismiss").click(),
    state: () => ({
      hidden: hint().hidden,
      text: document.getElementById("temp-hint-text").textContent,
      href: document.getElementById("temp-hint-link").href,
    }),
  };
}

async function main() {
  const out = {};

  // Scenario 1 — temp unavailable, hint present: shows once, exact text + link.
  const s1 = freshSession();
  s1.setOverview(overview({ temp_available: false,
    temp_hint: { text: HINT_TEXT, url: HINT_URL } }));
  await s1.refresh();
  const st1 = s1.state();
  out.shows_when_unavailable = st1.hidden === false;
  out.text_matches_contract = st1.text === HINT_TEXT;
  out.link_matches_contract = st1.href === HINT_URL;

  // Scenario 2 — next 5s poll: still visible, not duplicated or re-hidden.
  await s1.refresh();
  out.stays_visible_on_repoll = s1.state().hidden === false;

  // Scenario 3 — dismiss: hides, and the next poll does NOT bring it back.
  s1.dismiss();
  out.dismiss_hides = s1.state().hidden === true;
  await s1.refresh();
  out.stays_hidden_after_dismiss = s1.state().hidden === true;

  // Scenario 4 — temp available, no hint field: never shown.
  const s4 = freshSession();
  s4.setOverview(overview({ temp_available: true, temp_c: 55.0 }));
  await s4.refresh();
  out.hidden_when_available = s4.state().hidden === true;

  // Scenario 5 — temp available AND a hint field anyway (contract
  // violation guard): availability wins, never shown.
  const s5 = freshSession();
  s5.setOverview(overview({ temp_available: true, temp_c: 55.0,
    temp_hint: { text: HINT_TEXT, url: HINT_URL } }));
  await s5.refresh();
  out.hidden_when_available_even_with_hint_field = s5.state().hidden === true;

  // Scenario 6 — temp unavailable but no hint field yet (older API /
  // D's monitor change not shipped): stays hidden, no crash.
  const s6 = freshSession();
  s6.setOverview(overview({ temp_available: false }));
  await s6.refresh();
  out.hidden_without_hint_field_no_crash = s6.state().hidden === true;

  // Scenario 7 — temp becomes available after the hint was shown:
  // the hint disappears.
  const s7 = freshSession();
  s7.setOverview(overview({ temp_available: false,
    temp_hint: { text: HINT_TEXT, url: HINT_URL } }));
  await s7.refresh();
  const shownFirst = s7.state().hidden === false;
  s7.setOverview(overview({ temp_available: true, temp_c: 55.0 }));
  await s7.refresh();
  out.hides_when_temp_becomes_available = shownFirst && s7.state().hidden === true;

  out.all_pass = Object.values(out).every(Boolean);
  console.log(JSON.stringify(out, null, 2));
}

main().catch((e) => { console.error(e); process.exit(1); });
