// LONA v1.6.3 — headless DOM regression test for the island idle shrink.
//
// The critic finding on v1.6.2: the island window is created 380x60 but
// the JS state machine still believed the idle size was 190x52, so 7s
// after any greeting/mood display goIdle() animated the window down to
// 190x52 while the pill CSS stays 380px wide — the pill was clipped to
// its central 190px (robot bubble invisible, texts cut mid-word) most of
// the time. The first goActive() also squeezed the real 380-wide window
// down toward 190 before growing back (JS believed curW was 190).
//
// This harness MUST FAIL on the v1.6.2 code and PASS on the fix:
//   * no island_resize call ever carries w < 380 or h < 60 in any
//     collapsed state (idle/active); only the 720x400 expanded state
//     may exceed 380x60, never go below it.
//   * after the idle timer fires, the believed size stays 380x60.
//   * expand -> 720x400, collapse -> back to 380x60, idle timer after
//     that still never shrinks below 380x60.
//
//   NODE_PATH=/tmp/jsdom-test/node_modules node app/tests/v163_dom.js
//
// window.__islandIdleMs is a test hook (same pattern as the existing
// window.__islandCapMs): the 7s idle timer is shortened to 80ms so the
// harness runs fast. On code without the hook the harness waits out the
// real 7s timer instead, so it still fails correctly on v1.6.2.

const fs = require("fs");
const path = require("path");

const { JSDOM } = require("jsdom");

const ROOT = path.resolve(__dirname, "..", "..");
const islandSrc = fs.readFileSync(path.join(ROOT, "app", "ui", "island.html"), "utf8");

const results = {};
function check(name, cond, extra) {
  results[name] = { pass: !!cond, extra: extra || "" };
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const resizes = [];
  const dom = new JSDOM(islandSrc, {
    url: "https://localhost/",
    runScripts: "dangerously",
    pretendToBeVisual: true,
    beforeParse(window) {
      window.__islandCapMs = 80;
      window.__islandIdleMs = 80;
      window.pywebview = {
        api: {
          island_resize: async (w, h) => { resizes.push([w, h]); return { ok: true }; },
          get_mood: async () => ({ mood: "صاحي" }),
          get_status: async () => ({ settings: { ai_name: "LONA" } }),
          log_js: async () => ({ ok: true }),
          chat: async () => ({ pending: true, chat_id: "c1" }),
          chat_result: async () => ({ done: false }),
        },
      };
    },
  });
  const { window } = dom;
  await sleep(150); // let the load-time script settle (startup size is a no-op)

  const belowMin = () => resizes.filter(([w, h]) => w < 380 || h < 60);
  const believed = () => {
    try { return [window.eval("curW"), window.eval("curH")]; } catch (e) { return [-1, -1]; }
  };

  // 1. Greeting -> goActive(): the first activation must NOT squeeze the
  //    window down toward 190 (v1.6.2: JS believed curW was 190, so the
  //    first animation frames resized the real 380-wide window down).
  window.eval("showIslandText('أهلًا يا محمد')");
  await sleep(700); // animation is 450ms
  check("idle_first_active_no_shrink", belowMin().length === 0,
    "resizes=" + JSON.stringify(resizes));

  // 2. Idle timer fires -> goIdle(): the pill must stay 380x60.
  //    With the __islandIdleMs hook the timer is 80ms; without it (v1.6.2
  //    code) the real 7s timer applies, so wait it out there.
  const hasIdleHook = islandSrc.includes("__islandIdleMs");
  await sleep(hasIdleHook ? 1200 : 8500);
  check("idle_timer_never_shrinks", belowMin().length === 0,
    "resizes=" + JSON.stringify(resizes));
  const [w1, h1] = believed();
  check("idle_size_stays_380x60", w1 === 380 && h1 === 60, w1 + "x" + h1);

  // 3. Expand -> 720x400, collapse -> 380x60, idle again -> still 380x60.
  window.eval("expandIsland()");
  await sleep(700);
  const [w2, h2] = believed();
  check("expand_is_720x400", w2 === 720 && h2 === 400, w2 + "x" + h2);
  check("expand_never_below_380", belowMin().length === 0,
    "resizes=" + JSON.stringify(resizes));
  window.eval("collapseIsland()");
  await sleep(700);
  const [w3, h3] = believed();
  check("collapse_back_to_380x60", w3 === 380 && h3 === 60, w3 + "x" + h3);
  await sleep(hasIdleHook ? 1200 : 8500);
  check("idle_after_collapse_never_shrinks", belowMin().length === 0,
    "resizes=" + JSON.stringify(resizes));
  const [w4, h4] = believed();
  check("idle_after_collapse_stays_380x60", w4 === 380 && h4 === 60, w4 + "x" + h4);

  dom.window.close();
  process.stdout.write(JSON.stringify(results) + "\n");
})().catch((e) => {
  process.stdout.write(JSON.stringify({ harness_error: String((e && e.stack) || e) }) + "\n");
});
