# Memo AI v1.4.3 — headless tests for the compact permission card
# (run on Linux, no Windows, no keys, no network except mocked):
#
#   python -m unittest app.tests.test_v143_perm_card -v
#
# What is proven here:
#   * the card is small: computed max-width 320px (was 82% of the chat
#     = 751px at the 960px window), tight padding/type, detail clamped
#     to 2 lines with a tap-to-expand toggle — estimated area ~1/4.4 of
#     the v1.4.2 card (60,480 vs 263,601 px^2 for the same sample
#     permission). The literal 1/8 the user asked for is NOT reached:
#     the floor is two tappable buttons + readable title/summary/
#     detail; 1/8 would need the detail hidden by default, which the
#     spec rules out (2-3 lines visible). Width -57%, height -46%.
#   * entrance animation: perm-in keyframes scale 0.9 -> 1 + fade,
#     0.22s ease-out; press: buttons scale to 0.96 on :active;
#     resolved state fades in (perm-state-in). No sudden pop.
#   * DOM behaviour (jsdom harness app/tests/perm_card_dom.js, runs
#     the REAL addPermCard/finalizePermCard from app/ui/app.js):
#     Allow calls resolve_permission(id, true) and flips the card to
#     "تم السماح ✅" with dead buttons; Deny mirrors it; the detail
#     expands/collapses on tap; timeout state still works; polling
#     (1s) and resolve logic are untouched.
#
# The jsdom part needs `jsdom` installed where node can see it, e.g.:
#   npm install jsdom --prefix /tmp/jsdom-test
#   NODE_PATH=/tmp/jsdom-test/node_modules python -m unittest ...
# Without jsdom the DOM test skips; the static CSS/JS checks still run.
# jsdom has no layout engine, so heights are computed from
# getComputedStyle with a documented line-count model (see the
# harness header) — same model for old and new, so the ratio is
# apples-to-apples.
#
# NOT proven here (needs real Windows + WebView2): how the card looks
# and feels on the user's laptop — field check pending.

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _find_jsdom_node_path() -> str | None:
    """Return a NODE_PATH under which require('jsdom') resolves, or None."""
    candidates = []
    if os.environ.get("NODE_PATH"):
        candidates.append(os.environ["NODE_PATH"])
    candidates.append("/tmp/jsdom-test/node_modules")
    candidates.append(str(ROOT / "node_modules"))
    for cand in candidates:
        probe = subprocess.run(
            ["node", "-e", "require.resolve('jsdom')"],
            capture_output=True, text=True,
            env={**os.environ, "NODE_PATH": cand})
        if probe.returncode == 0:
            return cand
    return None


class TestPermCardStatic(unittest.TestCase):
    def test_card_is_capped_narrow(self):
        css = _read("app/ui/styles.css")
        self.assertIn("max-width: 320px;", css)
        self.assertIn("align-self: flex-end;", css)

    def test_entrance_animation(self):
        css = _read("app/ui/styles.css")
        self.assertIn("animation: perm-in 0.22s var(--ease) both;", css)
        self.assertIn("@keyframes perm-in", css)
        # scale-up from ~0.9 to 1 + fade, ease-out, 150-250ms
        self.assertRegex(css, r"@keyframes perm-in\s*\{\s*from\s*\{[^}]*opacity:\s*0;[^}]*scale\(0\.9\)")
        self.assertRegex(css, r"@keyframes perm-in[\s\S]*?to\s*\{[^}]*opacity:\s*1;[^}]*scale\(1\)")

    def test_press_animation(self):
        css = _read("app/ui/styles.css")
        self.assertIn(
            ".msg.perm .perm-btns button:not(:disabled):active { transform: scale(0.96); }",
            css)

    def test_detail_clamped_with_expand(self):
        css = _read("app/ui/styles.css")
        self.assertIn("-webkit-line-clamp: 2;", css)
        self.assertIn(".msg.perm .perm-detail.expanded", css)
        self.assertIn("max-height: 140px;", css)
        js = _read("app/ui/app.js")
        self.assertIn('detail.classList.toggle("expanded")', js)
        self.assertIn('className = "perm-more"', js)
        self.assertIn('perm_more: "عرض المزيد ▾"', js)
        self.assertIn('perm_less: "عرض أقل ▴"', js)
        self.assertIn('perm_more: "Show more ▾"', js)

    def test_resolved_state_animates(self):
        css = _read("app/ui/styles.css")
        self.assertIn("@keyframes perm-state-in", css)
        self.assertIn("animation: perm-state-in 0.2s var(--ease) both;", css)

    def test_polling_and_resolve_untouched(self):
        js = _read("app/ui/app.js")
        # polling still every 1s, resolve still the same call
        self.assertIn("}, 1000);", js)
        self.assertIn("await b.resolve_permission(p.id, allowed);", js)
        self.assertIn('finalizePermCard(p.id, allowed ? "allowed" : "denied");', js)
        # card still builds as a chat message, not a modal
        self.assertIn('div.className = "msg perm";', js)
        self.assertNotIn("perm-modal", js)

    def test_buttons_still_tappable(self):
        css = _read("app/ui/styles.css")
        # compact but still finger-sized: min-height 30px + padding
        self.assertIn("min-height: 30px;", css)
        self.assertIn("padding: 6px 12px;", css)


class TestPermCardDom(unittest.TestCase):
    def test_dom_build_click_and_measure(self):
        node_path = _find_jsdom_node_path()
        if node_path is None:
            self.skipTest(
                "jsdom not installed; run "
                "`npm install jsdom --prefix /tmp/jsdom-test` and set "
                "NODE_PATH=/tmp/jsdom-test/node_modules")
        harness = APP_DIR / "tests" / "perm_card_dom.js"
        proc = subprocess.run(
            ["node", str(harness)],
            capture_output=True, text=True, cwd=str(ROOT),
            env={**os.environ, "NODE_PATH": node_path})
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail("harness did not print JSON:\n" + proc.stdout + proc.stderr)
        failed = result.get("failed") or []
        self.assertEqual([], failed,
                         "jsdom checks failed: " + json.dumps(result, ensure_ascii=False))
        self.assertTrue(result.get("passed"), "harness reported failure")
        m = result["measurements"]
        # the headline numbers — keep them in the test output
        print(f"\n  perm card area: old {m['old']['area']} px^2 "
              f"({m['old']['width']}x{m['old']['height']}) -> "
              f"new {m['new']['area']} px^2 "
              f"({m['new']['width']}x{m['new']['height']}), "
              f"ratio {m['ratio']} ({m['ratio_fraction']})")
        self.assertLess(m["ratio"], 0.34)
        self.assertEqual(m["new"]["width"], 320)
        self.assertIn("perm-in", m["card_animation"])


if __name__ == "__main__":
    unittest.main()
