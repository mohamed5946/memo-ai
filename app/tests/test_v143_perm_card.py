# Memo AI v1.6.1 (items 12+18) — tests for the redesigned permission card
# (run on Linux, no Windows, no keys, no network except mocked):
#
#   python -m unittest app.tests.test_v143_perm_card -v
#
# What is proven here (supersedes the v1.4.3 detail-clamp spec --
# Mohamed approved the redesign 2026-11-22 01:37):
#   * the card is small: computed max-width 320px, tight padding/type.
#   * entrance animation: perm-in keyframes scale 0.9 -> 1 + fade,
#     0.22s ease-out; press: buttons scale to 0.96 on :active;
#     resolved state fades in (perm-state-in). No sudden pop.
#   * the new shape: Allow/Deny are dashed .choice-card rectangles
#     (same as the option cards); the picked one goes solid (.picked),
#     the other dims (.dimmed). The old collapsible detail is GONE --
#     no .perm-detail element, no .perm-more toggle; the summary
#     carries the request.
#   * the severity dot (item 18) sits inside the card next to the
#     buttons, coloured by the backend tier (green/yellow/red) with a
#     tooltip; unknown/missing tier fails closed to red.
#   * DOM behaviour (jsdom harness app/tests/severity_dom.js, runs the
#     REAL addPermCard/finalizePermCard/renderActionMessage/
#     pollProactiveFeed from app/ui/app.js): Allow calls
#     resolve_permission(id, true) and flips the card to "تم السماح"
#     with dead buttons and the result line ON the same card (no
#     separate message); Deny mirrors it; timeout dims both with no
#     pick; a trial action message shows the dot pulsing while running;
#     routine suggestions render as action messages with the dot.
#
# The jsdom part needs `jsdom` installed where node can see it, e.g.:
#   npm install jsdom --prefix /tmp/jsdom-test
#   NODE_PATH=/tmp/jsdom-test/node_modules python -m unittest ...
# Without jsdom the DOM test skips; the static CSS/JS checks still run.
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

    def test_no_collapsible_detail(self):
        # Item 12: the old collapsible detail is gone -- the summary
        # carries the request. The CSS rules may stay (dead), but the
        # card must never build a detail element or a toggle.
        js = _read("app/ui/app.js")
        block = js.split("function addPermCard(p)")[1].split(
            "function finalizePermCard")[0]
        self.assertNotIn("perm-detail", block)
        self.assertNotIn("perm-more", block)
        self.assertNotIn("toggleDetail", block)
        self.assertNotIn("classList.toggle(\"expanded\")", block)

    def test_buttons_are_choice_cards(self):
        # Item 12: Allow/Deny are dashed .choice-card rectangles.
        js = _read("app/ui/app.js")
        block = js.split("function addPermCard(p)")[1].split(
            "function finalizePermCard")[0]
        self.assertEqual(block.count('className = "choice-card btn-3d"'), 2)
        self.assertNotIn("gold-btn", block)
        self.assertNotIn("danger-btn", block)
        css = _read("app/ui/styles.css")
        self.assertIn(".choice-card { border: 1.5px dashed", css)
        self.assertIn(".choice-card.picked { border-style: solid;", css)
        self.assertIn(".choice-card.dimmed { opacity: .45; }", css)

    def test_pick_dims_the_other(self):
        js = _read("app/ui/app.js")
        block = js.split("function finalizePermCard")[1].split(
            "function startPermPolling")[0]
        self.assertIn('classList.add("picked")', block)
        self.assertIn('classList.add("dimmed")', block)

    def test_severity_dot_inside_card(self):
        # Item 18: the dot sits in the card next to the buttons.
        js = _read("app/ui/app.js")
        block = js.split("function addPermCard(p)")[1].split(
            "function finalizePermCard")[0]
        self.assertIn("severityDot(p.tier)", block)
        self.assertIn("btns.appendChild(severityDot(p.tier));", block)
        dot = js.split("const SEVERITY = {")[1].split(
            "function addPermCard")[0]
        for tier, color, title in (("green", "#3ECF8E", "آمن"),
                                   ("yellow", "#F2B544", "محتاج إذن"),
                                   ("red", "#EF6461", "خطير — إذن صريح")):
            self.assertIn(f'{tier}: {{ color: "{color}", title: "{title}" }}',
                          dot, tier)
        # Unknown tier fails closed to red.
        self.assertIn('const lv = SEVERITY[tier] ? tier : "red";',
                      js.split("function severityDot(tier)")[1].split(
                          "function addPermCard")[0])

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
        harness = APP_DIR / "tests" / "severity_dom.js"
        proc = subprocess.run(
            ["node", str(harness)],
            capture_output=True, text=True, cwd=str(ROOT),
            env={**os.environ, "NODE_PATH": node_path})
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail("harness did not print JSON:\n" + proc.stdout + proc.stderr)
        failed = [k for k, v in result.items() if not v["pass"]]
        self.assertEqual([], failed,
                         "jsdom checks failed: " + json.dumps(result, ensure_ascii=False))
        # The headline checks -- keep them in the test output.
        for name in ("green_dot_color", "allow_picked_solid",
                     "allow_other_dimmed", "allow_result_on_card",
                     "allow_no_separate_message", "deny_picked_solid",
                     "timeout_both_dimmed", "action_dot_pulsing",
                     "feed_routine_is_action_msg"):
            self.assertTrue(result[name]["pass"], name)


if __name__ == "__main__":
    unittest.main()
