"""Items 12+18 (v1.6.1): permission card redesign + severity dot.

Item 18:
  (a) Every permission request carries a severity tier from the fixer
      registry (fixer.tier_for): GREEN safe / YELLOW ask first / RED
      explicit permission only. Unknown kinds fail closed to RED --
      that is the registry's own rule (it predates item 18); the
      steer's example default (yellow) does NOT override it.
  (b) The dot displays inside the permission card next to Allow/Deny,
      coloured by tier, with a tooltip (safe / needs permission /
      dangerous -- explicit permission). The .status-dot colours in
      styles.css are scoped to .action-msg only, so the dot also
      carries its colour inline to render here (see severityDot).
  (c) renderActionMessage is wired into the proactive feed: routine
      suggestions render as action messages with the dot, and the
      "do it" run sends the routine to the chat for real. This also
      fixed the feed being dead: pollProactiveFeed iterated the
      {feed, exam} response object as an array, so it threw on every
      poll and nothing ever showed.

Item 12 (approved 2026-11-22 01:37, done with 18 in the same card):
  Allow/Deny are dashed .choice-card rectangles (picked solid, other
  dimmed); the result updates LIVE on the same card; the old
  collapsible detail is gone.

Tests: registry tiers (real fixer import), tier plumbing through
tools.request_permission (threaded -- it blocks until resolved), JS
source inspection, and the jsdom harness (severity_dom.js).
"""

import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "app"
sys.path.insert(0, str(APP_DIR))

import fixer as fixer_mod  # noqa: E402
import tools as tools_mod  # noqa: E402

JS = (ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")


class TestRegistryTiers(unittest.TestCase):
    # (permission kind, expected tier) -- from the fixer registry.
    EXPECTED = {
        # Fixer actions (pre-existing).
        "kill_hung_process": "green",
        "kill_ram_hog": "green",
        "clear_temp": "green",
        "close_startup_app": "green",
        "kill_app_with_unsaved_work": "yellow",
        "change_system_setting": "yellow",
        "delete_user_file": "red",
        "change_driver": "red",
        "hardware_fault": "red",
        # In-chat permission kinds (item 18).
        "clean_cache": "green",
        "browser_open": "green",
        "browser_play_youtube": "green",
        "open_app": "green",
        "fix_hung_process": "yellow",
        "create_file": "yellow",
        "send_whatsapp": "yellow",
        "write_file": "red",
        "edit_file": "red",
        "run_command": "red",
        "deep_file_access": "red",
    }

    def test_every_known_kind_has_its_tier(self):
        for kind, tier in self.EXPECTED.items():
            self.assertEqual(fixer_mod.tier_for(kind), tier, kind)

    def test_unknown_kind_fails_closed_to_red(self):
        # The registry's own rule -- NOT yellow. An unknown action is
        # never assumed safe.
        for kind in ("something_new", "", None, "BROWSER_OPEN"):
            self.assertEqual(fixer_mod.tier_for(kind), "red", repr(kind))


class TestTierPlumbing(unittest.TestCase):
    def test_request_permission_carries_tier(self):
        # request_permission blocks until resolved: ask in a thread,
        # inspect the pending request, then resolve it.
        with tools_mod._pending_lock:
            tools_mod._pending.clear()
        result = {}

        def ask():
            result["ok"] = tools_mod.request_permission(
                "run_command", "تشغيل أمر", "echo hi")

        t = threading.Thread(target=ask, daemon=True)
        t.start()
        try:
            deadline = time.time() + 5
            pending = None
            while time.time() < deadline:
                pending = tools_mod.get_pending()
                if pending is not None:
                    break
                time.sleep(0.05)
            self.assertIsNotNone(pending, "request never appeared")
            self.assertEqual(pending["kind"], "run_command")
            self.assertEqual(pending["tier"], "red")
            self.assertNotIn("event", pending, "Event must never leak")
            tools_mod.resolve_permission(pending["id"], True)
            t.join(timeout=5)
            self.assertTrue(result.get("ok"))
            state = tools_mod.get_permission_state()
            resolved = [r for r in state["resolved"]
                        if r["id"] == pending["id"]]
            self.assertEqual(len(resolved), 1)
            self.assertEqual(resolved[0]["tier"], "red")
            self.assertEqual(resolved[0]["status"], "allowed")
        finally:
            if t.is_alive():
                with tools_mod._pending_lock:
                    tools_mod._pending.clear()

    def test_green_kind_carries_green_tier(self):
        with tools_mod._pending_lock:
            tools_mod._pending.clear()
        result = {}

        def ask():
            result["ok"] = tools_mod.request_permission(
                "browser_play_youtube", "شغّل فيديو", "")

        t = threading.Thread(target=ask, daemon=True)
        t.start()
        try:
            deadline = time.time() + 5
            pending = None
            while time.time() < deadline:
                pending = tools_mod.get_pending()
                if pending is not None:
                    break
                time.sleep(0.05)
            self.assertIsNotNone(pending)
            self.assertEqual(pending["tier"], "green")
            tools_mod.resolve_permission(pending["id"], False)
            t.join(timeout=5)
            self.assertFalse(result.get("ok"))
        finally:
            if t.is_alive():
                with tools_mod._pending_lock:
                    tools_mod._pending.clear()


class TestCardWiring(unittest.TestCase):
    def test_action_message_wired_to_routine_suggestions(self):
        block = JS.split("async function pollProactiveFeed()")[1].split(
            "// ---------- v1.6.0 init")[0]
        # The feed-shape bug: the bridge returns {feed, exam}.
        self.assertIn("const items = (res && res.feed) || [];", block)
        self.assertIn('if (item.kind === "routine_suggestion") {', block)
        self.assertIn("renderActionMessage(item.text || \"\", level,", block)
        # The run is real: it sends the routine to the chat. If a chat
        # is already running it throws instead of faking completion.
        self.assertIn("return sendText(", block)
        self.assertIn('if (busy) throw new Error("busy");', block)
        # Plain items still render as banners, with their text.
        self.assertIn('msg.textContent = item.text || item.message || "";',
                      block)

    def test_action_message_dot_pulses_while_running(self):
        block = JS.split("function renderActionMessage")[1].split(
            "// ---------- (6) UNDO UI")[0]
        self.assertIn('dot.className = "status-dot " + lv;', block)
        self.assertIn('dot.classList.add("running");', block)
        self.assertIn('dot.classList.remove("running");', block)


class TestSeverityDomHarness(unittest.TestCase):
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
            ["node", str(ROOT / "app" / "tests" / "severity_dom.js")],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "NODE_PATH": node_path})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        results = json.loads(run.stdout)
        failed = [k for k, v in results.items() if not v["pass"]]
        self.assertEqual(failed, [], f"harness failures: {failed}")
        for scenario in ("green_dot_color", "yellow_dot_color",
                         "red_dot_color", "unknown_tier_red",
                         "allow_picked_solid", "allow_result_on_card",
                         "allow_no_separate_message", "deny_picked_solid",
                         "timeout_both_dimmed", "action_dot_pulsing",
                         "feed_routine_is_action_msg",
                         "feed_routine_sent_to_chat"):
            self.assertIn(scenario, results, scenario)
            self.assertTrue(results[scenario]["pass"], scenario)


if __name__ == "__main__":
    unittest.main()
