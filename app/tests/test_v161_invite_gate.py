# LONA v1.6.1 - tests for the invite-code gate (item 15)
# (run on Linux, no Windows, no keys, no network):
#
#   python3 -m pytest app/tests/test_v161_invite_gate.py -q
#
# What is proven here:
#   * "First run" is gone as a browsable destination: no sidebar
#     button, no settings-menu entry, no section-first-run view, and
#     no user-visible "أول تشغيل" anywhere in index.html / app.js.
#     The gate is called "كود الدعوة" everywhere.
#   * The gate (#invite-gate) is a full-screen overlay with the same
#     6 boxes + activate button + status element, wired by the same
#     wireInvite() (hash check + activation save unchanged).
#   * checkInviteGate() shows the gate only when not activated, hides
#     it when activated, runs at init and again when the bridge
#     arrives late (pywebviewready), and never blocks the UI without
#     a bridge.
#   * A correct code hides the gate and lands on chat; a wrong code
#     stays on the gate with a clear message and cleared boxes.
#   * main.py forces the main window visible (and skips the island)
#     on first launch, or the gate would be unreachable behind the
#     island pill.
#   * The upgrade never re-asks: activated.json migrates with the
#     item-14 migration (installer staging + app/migrate.py).
#
# The live behaviour (focus, typing, retry) runs in the jsdom
# harness app/tests/invite_gate_dom.js; the Python test runs it when
# jsdom is installed and skips it otherwise (same pattern as the
# temp-hint / perm-card harnesses).

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

import migrate as migrate_mod  # noqa: E402


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


HTML = _read("app/ui/index.html")
JS = _read("app/ui/app.js")
MAIN = _read("app/main.py")
# v1.6.4 reskin: the invite gate is its own page (invite.html) with the
# shared Liquid Glass styling (assets/lona.css) — no longer an overlay
# inside index.html.
INVITE = _read("app/ui/invite.html")
LONA_CSS = _read("app/ui/assets/lona.css")


class TestNoFirstRunDestination(unittest.TestCase):
    def test_no_sidebar_or_menu_first_run(self):
        self.assertNotIn('data-view="first-run"', HTML)
        self.assertNotIn('data-section="first-run"', HTML)
        self.assertNotIn('id="section-first-run"', HTML)

    def test_no_first_run_label_anywhere_user_visible(self):
        # "أول تشغيل" must not appear in any user-visible string.
        self.assertNotIn("أول تشغيل", HTML)
        self.assertNotIn("أول تشغيل", JS)

    def test_no_dead_first_run_navigation_in_js(self):
        self.assertNotIn('"first-run"', JS)
        self.assertNotIn("'first-run'", JS)
        self.assertNotIn("refreshActivationState", JS)


class TestGateMarkup(unittest.TestCase):
    def test_gate_is_full_screen_overlay(self):
        # v1.6.4 reskin: the gate is a standalone page (invite.html) that
        # fills its own window — a centered glass card on a full-window
        # stage, not an overlay inside index.html.
        self.assertIn('class="stage invite-wrap"', INVITE)
        self.assertIn('class="glass invite-card"', INVITE)
        self.assertIn(".invite-wrap{", LONA_CSS)
        self.assertIn("min-height:100vh", LONA_CSS)
        self.assertIn("align-items:center", LONA_CSS)
        self.assertIn("justify-content:center", LONA_CSS)
        self.assertNotIn("invite-gate", HTML)

    def test_gate_title_is_invite_code_only(self):
        # The gate's title is the invite code, never "first run".
        self.assertIn("<title>LONA — كود الدعوة</title>", INVITE)
        self.assertNotIn("أول تشغيل", INVITE.split("<title>")[1].split("</title>")[0])
        # NOTE: the body copy descriptively says the code is needed
        # "في أول تشغيل بس" (on first launch only) — that is the gate
        # explaining when it appears, not a "first run" destination:
        # there is no first-run section, button, or view anywhere.
        self.assertNotIn('data-v="first-run"', INVITE)
        self.assertNotIn("section-first-run", INVITE)

    def test_gate_has_six_boxes_and_activate(self):
        self.assertEqual(INVITE.count('class="pin"'), 6)
        self.assertIn('id="pinRow"', INVITE)
        self.assertIn('id="checkBtn"', INVITE)
        self.assertIn('id="inviteMsg"', INVITE)

    def test_gate_is_not_inside_settings_views(self):
        # The gate lives in its own file: no invite markup at all in
        # index.html, and no settings modal anywhere (v1.6.1 item 20
        # removed the settings modal entirely).
        self.assertNotIn('id="settings-modal"', HTML,
                         "the settings modal is gone in v1.6.1")
        self.assertNotIn('class="pin"', HTML)
        self.assertNotIn('id="checkBtn"', HTML)
        self.assertNotIn('id="inviteMsg"', HTML)
        self.assertNotIn("invite-gate", HTML)


class TestGateLogic(unittest.TestCase):
    def test_check_invite_gate_defined_and_wired(self):
        self.assertIn("async function checkInviteGate()", JS)
        self.assertIn("checkInviteGate();", JS)
        self.assertIn('checkInviteGate(); });', JS,
                      "gate re-checked when the bridge arrives late")
        self.assertIn("get_activation_state", JS)
        self.assertIn("gate.hidden = false", JS)
        self.assertIn("gate.hidden = true", JS)

    def test_correct_code_hides_gate_and_shows_chat(self):
        self.assertIn('if (r && r.ok) {', JS)
        self.assertIn('if (gate) gate.hidden = true;', JS)
        self.assertIn('showView("chat");', JS)

    def test_wrong_code_stays_with_message_and_cleared_boxes(self):
        self.assertIn('"الكود غلط — جرّب تاني"', JS)
        self.assertIn('boxes.forEach(bx => { bx.value = ""; });', JS)

    def test_hash_check_and_activation_save_unchanged(self):
        self.assertIn("function inviteCodeString()", JS)
        self.assertIn("b.check_invite(code)", JS)
        self.assertIn("function wireInvite()", JS)


class TestMainWindowForcedVisible(unittest.TestCase):
    def test_first_launch_forces_main_window_visible(self):
        self.assertIn("not_activated = invite_mod is not None", MAIN)
        self.assertIn("not invite_mod.is_activated()", MAIN)
        self.assertIn("and not not_activated", MAIN,
                      "island skipped + main window shown on first launch")

    def test_activation_state_migrates_with_upgrade(self):
        # An upgrade from v1.6.0 must carry activated.json over, or
        # the gate would re-appear and ask for the code again.
        tmp = Path(tempfile.mkdtemp())
        old = tmp / "Memo AI"
        data = old / "_internal" / "app" / "data"
        data.mkdir(parents=True)
        (data / "activated.json").write_text(
            '{"activated": true, "at": "2026-11-21T10:00:00"}',
            encoding="utf-8")
        (data / "settings.json").write_text('{"ai_name": "LONA"}',
                                            encoding="utf-8")
        new_base = tmp / "LONA"
        new_data = new_base / "_internal" / "app" / "data"
        new_data.mkdir(parents=True)
        result = migrate_mod.migrate_from_old_install(new_base, new_data,
                                                      old)
        self.assertTrue(result["migrated"])
        self.assertTrue(
            json.loads((new_data / "activated.json").read_text())
            ["activated"], "upgrade must not re-ask for the invite code")


class TestInviteGateDomHarness(unittest.TestCase):
    def test_dom_harness_passes(self):
        node_path = "/tmp/jsdom-test/node_modules"
        try:
            probe = subprocess.run(
                ["node", "-e", "require.resolve('jsdom')"],
                capture_output=True, text=True, timeout=30,
                env={**os.environ, "NODE_PATH": node_path})
        except Exception:
            probe = None
        if probe is None or probe.returncode != 0:
            self.skipTest(
                "jsdom not installed; run "
                "npm install jsdom --prefix /tmp/jsdom-test")
        run = subprocess.run(
            ["node", str(APP_DIR / "tests" / "invite_gate_dom.js")],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "NODE_PATH": node_path})
        try:
            report = json.loads(run.stdout)
        except Exception:
            self.fail("harness produced no JSON report:\n" + run.stdout +
                      "\n" + run.stderr)
        failed = [k for k, v in report.items() if not v.get("pass")]
        self.assertEqual(failed, [], "jsdom gate checks failed: " +
                         json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    unittest.main()
