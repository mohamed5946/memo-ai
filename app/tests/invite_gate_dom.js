// LONA v1.6.1 (15) — headless DOM test for the invite gate.
//
// Runs the REAL checkInviteGate() + wireInvite() from app/ui/app.js
// inside jsdom (no Windows, no WebView2), against the REAL index.html,
// with a stubbed pywebview bridge. Reports JSON for the Python suite
// (app/tests/test_v161_invite_gate.py).
//
//   NODE_PATH=/tmp/jsdom-test/node_modules node app/tests/invite_gate_dom.js
//
// Scenarios:
//   * activated install -> gate stays hidden, main window usable.
//   * fresh install (not activated) -> gate shows, first box focused.
//   * correct code -> gate hides forever, lands on the chat view.
//   * wrong code -> gate stays open with a clear message, boxes cleared
//     for an immediate retry.
//   * type 6 digits -> Backspace-delete them -> type again (the v1.6.0
//     field freeze, item 21): every box keeps accepting input and the
//     focus never gets stuck. The harness models the REAL engine's
//     default action (Backspace deletes from the element focused AFTER
//     the keydown handlers run, unless preventDefault() was called) --
//     jsdom implements no default action, so pressBackspace() below
//     applies it explicitly. Verdict: jsdom does NOT reproduce a freeze
//     with either the old or the new code (the old code's values still
//     come out right under this model); the freeze is a WebView2/MSHTML
//     focus-state hazard, removed by preventDefault() + the reentrancy
//     guard + the activate timeout below.
//   * rapid interleave: type 3, delete 2, type 3, delete all, retype.
//   * activate with a hung bridge: the button locks, "بيتحقق…" shows,
//     and the 10 s timeout turns it into a retryable message.

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

async function runScenario(activated, opts) {
  opts = opts || {};
  const dom = new JSDOM(html, { url: "https://localhost/", runScripts: "outside-only" });
  const { window } = dom;
  const { document } = window;
  if (opts.timeoutMs) window.__inviteCheckTimeoutMs = opts.timeoutMs;
  // Stubbed bridge: activation state + invite check (hash check is
  // Python-side; here the stub accepts exactly one code).
  window.pywebview = {
    api: {
      get_activation_state: async () => ({ activated }),
      check_invite: opts.check_invite || (async (code) => {
        const ok = code === "123456";
        return { ok, activated: ok };
      }),
    },
  };
  // Minimal globals the extracted code touches.
  window.requestAnimationFrame = (fn) => setTimeout(fn, 0);
  const prelude = [
    "function $(id) { return document.getElementById(id); }",
    "function getApi() { return (window.pywebview && window.pywebview.api) ? window.pywebview.api : null; }",
    "function t(k) { return k; }",
    "function addMsg() {}",
    "function refreshStatus() {}",
    "function refreshDeviceOverview() {}",
    "function refreshExamCountdown() {}",
    "function renderProcList() {}",
  ].join("\n");
  const fns = [
    extract(jsSrc, "function showView(name)"),
    extract(jsSrc, "function inviteCodeString()"),
    extract(jsSrc, "async function checkInviteGate()"),
    extract(jsSrc, "function wireInvite()"),
  ].join("\n");
  window.eval(prelude + "\n" + fns);
  window.eval("wireInvite();");
  await window.eval("checkInviteGate()");
  return { window, document };
}

function boxesOf(document) {
  return Array.from(document.querySelectorAll("#invite-boxes .invite-box"));
}
function typeDigit(window, document, box, digit) {
  box.value = digit;
  box.dispatchEvent(new window.Event("input", { bubbles: true }));
}
function pressBackspace(window, document, box) {
  // The real engine (WebView2/Chromium) runs the Backspace default
  // action AFTER the keydown handlers, on the element focused THEN --
  // unless a handler called preventDefault(). jsdom implements no
  // default action, so apply it here explicitly. With the hardened
  // handler the default never fires on a moved focus (preventDefault),
  // so the handler alone owns the deletion.
  const ev = new window.KeyboardEvent("keydown", { key: "Backspace", bubbles: true, cancelable: true });
  box.dispatchEvent(ev);
  if (!ev.defaultPrevented) {
    const focused = document.activeElement;
    if (focused && focused.value) focused.value = focused.value.slice(0, -1);
  }
}
function activeBoxIndex(document) {
  const boxes = boxesOf(document);
  return boxes.indexOf(document.activeElement);
}

(async () => {
  // --- 1. activated install: gate stays hidden ---
  {
    const { document } = await runScenario(true);
    check("activated_gate_hidden", document.getElementById("invite-gate").hidden);
  }
  // --- 2. fresh install: gate shows, first box focused ---
  {
    const { document } = await runScenario(false);
    const gate = document.getElementById("invite-gate");
    check("fresh_gate_shown", !gate.hidden);
    check("fresh_first_box_focused", activeBoxIndex(document) === 0,
          "active=" + activeBoxIndex(document));
  }
  // --- 3. correct code: gate hides, lands on chat ---
  {
    const { window, document } = await runScenario(false);
    const boxes = boxesOf(document);
    "123456".split("").forEach((d, i) => typeDigit(window, document, boxes[i], d));
    document.getElementById("invite-activate").click();
    await new Promise((r) => setTimeout(r, 50));
    check("correct_code_hides_gate", document.getElementById("invite-gate").hidden,
          "status=" + document.getElementById("invite-status").textContent);
    check("correct_code_shows_chat", !document.getElementById("view-chat").hidden);
  }
  // --- 4. wrong code: gate stays, clear message, boxes cleared for retry ---
  {
    const { window, document } = await runScenario(false);
    const boxes = boxesOf(document);
    "999999".split("").forEach((d, i) => typeDigit(window, document, boxes[i], d));
    document.getElementById("invite-activate").click();
    await new Promise((r) => setTimeout(r, 50));
    const gate = document.getElementById("invite-gate");
    const status = document.getElementById("invite-status").textContent;
    check("wrong_code_gate_stays", !gate.hidden);
    check("wrong_code_message", /غلط/.test(status), "status=" + status);
    check("wrong_code_boxes_cleared", boxesOf(document).every((b) => b.value === ""));
    check("wrong_code_first_focused", activeBoxIndex(document) === 0,
          "active=" + activeBoxIndex(document));
  }
  // --- 5. type -> Backspace-delete -> type again (item 21 scenario) ---
  {
    const { window, document } = await runScenario(false);
    const boxes = boxesOf(document);
    // Type 6 digits (input handler auto-advances focus).
    "123456".split("").forEach((d, i) => typeDigit(window, document, boxes[i], d));
    check("scenario_typed_all", boxes.map((b) => b.value).join("") === "123456",
          "values=" + boxes.map((b) => b.value).join(""));
    // Delete them all with Backspace from the last box. The helper
    // applies the real engine's default action after each handler.
    for (let k = 0; k < 6; k++) {
      const idx = activeBoxIndex(document);
      const box = boxes[idx < 0 ? 5 : idx];
      pressBackspace(window, document, box);
    }
    check("scenario_deleted_all", boxes.every((b) => b.value === ""),
          "values=" + boxes.map((b) => b.value).join(""));
    // Type again: every box must accept input, focus must advance.
    "654321".split("").forEach((d, i) => typeDigit(window, document, boxes[i], d));
    check("scenario_retyped_all", boxes.map((b) => b.value).join("") === "654321",
          "values=" + boxes.map((b) => b.value).join(""));
    check("scenario_focus_advances", activeBoxIndex(document) === 5,
          "active=" + activeBoxIndex(document));
    // And the activate button enables with 6 fresh digits.
    check("scenario_activate_enabled", !document.getElementById("invite-activate").disabled);
  }
  // --- 6. paste fills the boxes ---
  {
    const { window, document } = await runScenario(false);
    const boxes = boxesOf(document);
    const ev = new window.Event("paste", { bubbles: true, cancelable: true });
    ev.clipboardData = { getData: () => "123456" };
    boxes[0].dispatchEvent(ev);
    check("paste_fills_boxes", boxes.map((b) => b.value).join("") === "123456",
          "values=" + boxes.map((b) => b.value).join(""));
  }
  // --- 7. rapid interleave: type 3, delete 2, type 3, delete all, retype ---
  {
    const { window, document } = await runScenario(false);
    const boxes = boxesOf(document);
    "123".split("").forEach((d, i) => typeDigit(window, document, boxes[i], d));
    check("rapid_typed_3", boxes.map((b) => b.value).join("") === "123",
          "values=" + boxes.map((b) => b.value).join(""));
    for (let k = 0; k < 2; k++) {
      pressBackspace(window, document, boxes[activeBoxIndex(document)]);
    }
    check("rapid_deleted_2", boxes.map((b) => b.value).join("") === "1",
          "values=" + boxes.map((b) => b.value).join(""));
    "456".split("").forEach((d, i) => typeDigit(window, document, boxes[i + 1], d));
    check("rapid_typed_456", boxes.map((b) => b.value).join("") === "1456",
          "values=" + boxes.map((b) => b.value).join(""));
    for (let k = 0; k < 4; k++) {
      pressBackspace(window, document, boxes[activeBoxIndex(document)]);
    }
    check("rapid_deleted_all", boxes.every((b) => b.value === ""),
          "values=" + boxes.map((b) => b.value).join(""));
    "123456".split("").forEach((d, i) => typeDigit(window, document, boxes[i], d));
    check("rapid_retyped_all", boxes.map((b) => b.value).join("") === "123456",
          "values=" + boxes.map((b) => b.value).join(""));
    check("rapid_activate_enabled", !document.getElementById("invite-activate").disabled);
  }
  // --- 8. paste, then delete all, then retype ---
  {
    const { window, document } = await runScenario(false);
    const boxes = boxesOf(document);
    const ev = new window.Event("paste", { bubbles: true, cancelable: true });
    ev.clipboardData = { getData: () => "123456" };
    boxes[0].dispatchEvent(ev);
    check("paste_delete_pasted", boxes.map((b) => b.value).join("") === "123456");
    for (let k = 0; k < 6; k++) {
      pressBackspace(window, document, boxes[activeBoxIndex(document)]);
    }
    check("paste_delete_cleared", boxes.every((b) => b.value === ""));
    "654321".split("").forEach((d, i) => typeDigit(window, document, boxes[i], d));
    check("paste_delete_retyped", boxes.map((b) => b.value).join("") === "654321",
          "values=" + boxes.map((b) => b.value).join(""));
  }
  // --- 9. hung bridge: activate locks, shows checking, times out retryable ---
  {
    const { window, document } = await runScenario(false, {
      timeoutMs: 50,
      check_invite: () => new Promise(() => {}), // never resolves
    });
    const boxes = boxesOf(document);
    const btn = document.getElementById("invite-activate");
    const status = document.getElementById("invite-status");
    "123456".split("").forEach((d, i) => typeDigit(window, document, boxes[i], d));
    btn.click();
    check("timeout_button_locks", btn.disabled, "disabled=" + btn.disabled);
    check("timeout_checking_shown", /بيتحقق/.test(status.textContent),
          "status=" + status.textContent);
    await new Promise((r) => setTimeout(r, 150));
    check("timeout_message", /اتأخر/.test(status.textContent),
          "status=" + status.textContent);
    check("timeout_gate_stays", !document.getElementById("invite-gate").hidden);
    check("timeout_button_reenabled", !btn.disabled, "disabled=" + btn.disabled);
    check("timeout_boxes_kept", boxes.map((b) => b.value).join("") === "123456",
          "values=" + boxes.map((b) => b.value).join(""));
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
