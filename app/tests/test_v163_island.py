"""LONA v1.6.3 regression tests — the island idle shrink (critic finding
on v1.6.2), ported to the v1.6.4 fixed-canvas island (WindyLand/Nusa
method).

The v1.6.2 code created the island window at 380x60 but the JS state
machine still believed the idle size was 190x52: 7s after any greeting
goIdle() animated the window down to 190x52 while the pill CSS stays
380px wide, so the pill was clipped to its central 190px (robot bubble
invisible, texts cut mid-word) most of the time. The first goActive()
also squeezed the real 380-wide window down toward 190 before growing
back, because JS believed curW was 190.

The v1.6.3 fix ("idle window = 380x60") removed the idle shrink; the
v1.6.4 fixed canvas goes further and deletes the whole resize
machinery by design:
  * the island window is created ONCE at 720x400 (ISLAND_CANVAS_W/H in
    main.py + island_canvas.py) and NEVER resized (resizable=False);
  * collapsed <-> expanded animates purely in HTML/CSS inside that
    canvas (pill 380x60 <-> panel 720x400, the .open class);
  * Api.island_resize(w, h) is a STATE SHIM only: it records
    _island_expanded and toggles WS_EX_TRANSPARENT - it does NOT
    resize the window and does NOT re-apply any region;
  * refresh_island_region() is a deliberate NO-OP; the region is
    applied ONCE at creation (apply_island_canvas_region);
  * the 7s idle timer is GONE: no goIdle(), no curW/curH belief, no
    __islandIdleMs hook. The replacement behavior is the expanded-
    timers lifecycle: mood/reminder/panel timers start on expand
    (startExpandedTimers) and are ALL cleared on collapse, so nothing
    polls while the island is collapsed.

Covers:
  * fixed-canvas constants: 720x400 canvas, 380x60 pill (no 190x52)
  * no JS size belief (no curW/curH, no animateSize), window created
    at canvas size, resizable=False
  * setOpen() is the only island_resize call site (720x400 / 380x60)
    and collapse kills every JS timer
  * no idle timer at all; the expanded-timers lifecycle replaces it
  * Python ISLAND_CANVAS_W/H == 720/400, ISLAND_IDLE_W/H == 380/60
  * jsdom runtime harness (app/tests/v163_dom.js): STALE for the
    fixed canvas - it drives the removed showIslandText/curW/curH/
    __islandIdleMs API, so it is skipped here when jsdom is absent
    and would need a rewrite (not part of this port) to run again.
"""

import json
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "app" / "ui"
TESTS = ROOT / "app" / "tests"

ISLAND = (UI / "island.html").read_text(encoding="utf-8")
MAIN = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
CANVAS = (ROOT / "app" / "island_canvas.py").read_text(encoding="utf-8")


def _extract(src, marker):
    """Return the function/block starting at marker, brace-matched."""
    start = src.index(marker)
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError("unbalanced braces after: " + marker)


def _find_jsdom_node_path():
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


class TestIdleSizeConstants(unittest.TestCase):
    def test_idle_equals_full_380x60(self):
        # v1.6.4 fixed canvas: the IDLE/FULL/EXPANDED constant line is
        # gone - the sizes now live in three places that must agree:
        # the 720x400 canvas constants (Python, both modules), the
        # 380x60 pill + 720x400 panel in the island CSS, and the
        # 380x60 pill constants kept in main.py.
        m = re.search(r"ISLAND_CANVAS_W, ISLAND_CANVAS_H = (\d+), (\d+)",
                      MAIN)
        assert m, "canvas constants missing from main.py"
        assert (int(m.group(1)), int(m.group(2))) == (720, 400)
        m = re.search(r"ISLAND_CANVAS_W = (\d+)\nISLAND_CANVAS_H = (\d+)",
                      CANVAS)
        assert m, "canvas constants missing from island_canvas.py"
        assert (int(m.group(1)), int(m.group(2))) == (720, 400)
        assert ("body.fixed-canvas .island{width:380px;height:60px}"
                in ISLAND), "pill CSS must be 380x60"
        assert (".island.open{width:720px;height:400px" in ISLAND), \
            "panel CSS must be 720x400"
        m = re.search(r"ISLAND_IDLE_W, ISLAND_IDLE_H = (\d+), (\d+)",
                      MAIN)
        assert m, "pill constants missing from main.py"
        assert (int(m.group(1)), int(m.group(2))) == (380, 60)

    def test_no_190x52_sizing_anywhere(self):
        # The 190x52 numbers are gone by design (fixed canvas): no
        # 190 or 52 anywhere in island.html, and no 190 anywhere in
        # main.py CODE (the only 190x52 mentions left are historical
        # comments describing the deleted small-pill design).
        assert "190" not in ISLAND, "190px sizing still present"
        assert "52" not in ISLAND, "52px sizing still present"
        # No 190 in main.py CODE: strip comments AND string literals
        # (docstrings included) with tokenize - the only 190x52
        # mentions left are historical prose, never a live size.
        import io
        import tokenize
        toks = tokenize.generate_tokens(io.StringIO(MAIN).readline)
        code_only = " ".join(
            t.string for t in toks
            if t.type not in (tokenize.COMMENT, tokenize.STRING,
                              tokenize.NL, tokenize.NEWLINE,
                              tokenize.ENCODING, tokenize.ENDMARKER))
        assert "190" not in code_only, \
            "190px sizing still in main.py code"
        assert re.search(r"\(\s*190\s*,\s*52\s*\)", code_only) is None
        assert "190" not in CANVAS and "52" not in CANVAS
        # And the region is applied once at creation, never on resize:
        assert "apply_island_canvas_region" in MAIN
        assert "refresh_island_region" in MAIN

    def test_cur_starts_at_real_window_size(self):
        # v1.6.4 fixed canvas: there is NO JS size belief at all -
        # curW/curH and animateSize are deleted, because the window is
        # never resized. The replacement: the window is created at the
        # canvas size and marked non-resizable, so no first-expand
        # squeeze is possible by construction.
        assert "curW" not in ISLAND, "curW belief still present"
        assert "curH" not in ISLAND, "curH belief still present"
        assert "animateSize" not in ISLAND
        assert "width=ISLAND_CANVAS_W, height=ISLAND_CANVAS_H" in MAIN
        assert "resizable=False" in MAIN


class TestIdleTimerIsHarmless(unittest.TestCase):
    def test_go_idle_never_resizes(self):
        # v1.6.4 fixed canvas: goIdle() itself is deleted - the island
        # no longer shrinks on idle and no longer resizes at all. The
        # replacement behavior: setOpen() is the ONLY island_resize
        # call site (720x400 on expand, 380x60 on collapse), and
        # collapsing kills every JS timer at once.
        assert "goIdle" not in ISLAND, "goIdle still present"
        assert ISLAND.count("B.call('island_resize'") == 1, \
            "island_resize must have exactly one call site (setOpen)"
        body = _extract(ISLAND, "function setOpen(")
        assert "var w = opening ? 720 : 380" in body
        assert "var h = opening ? 400 : 60" in body
        assert "stopExpandedTimers()" in body, \
            "collapse must stop the mood/reminder timers"
        assert "stopPanelRefresh()" in body, \
            "collapse must stop the panel refresh timer"

    def test_idle_timer_hook_exists(self):
        # v1.6.4 fixed canvas: the 7s idle timer is GONE by design
        # (no __islandIdleMs hook - assert its absence to lock the
        # deletion). The replacement: the expanded-timers lifecycle -
        # timers start on expand and are cleared on collapse, so
        # NOTHING polls while the island is collapsed.
        assert "window.__islandIdleMs" not in ISLAND, \
            "idle-timer hook must be gone with the idle timer"
        assert "startExpandedTimers" in ISLAND
        assert "stopExpandedTimers" in ISLAND
        assert "setInterval(pollMood, 5000)" in ISLAND
        # No mood poll at load: pollMood is invoked only from
        # startExpandedTimers (definition + that one call).
        assert "No mood poll at load" in ISLAND
        assert ISLAND.count("pollMood()") == 2
        body = _extract(ISLAND, "function stopExpandedTimers()")
        assert "clearInterval(moodTimer)" in body
        assert "clearInterval(reminderTimer)" in body


class TestPythonIdleSize(unittest.TestCase):
    def test_python_idle_size_380x60(self):
        # main.py cannot be imported as-is on Linux (no pywebview),
        # so install the same minimal webview stub test_v162_backend.py
        # uses, then import main and read the constants.
        import sys
        import types as _types
        app_dir = ROOT / "app"
        if str(app_dir) not in sys.path:
            sys.path.insert(0, str(app_dir))
        if "webview" not in sys.modules:
            fake = _types.ModuleType("webview")
            fake.create_window = lambda *a, **k: None
            fake.start = lambda *a, **k: None
            fake.windows = []
            sys.modules["webview"] = fake
        import main as main_mod
        assert main_mod.ISLAND_IDLE_W == 380
        assert main_mod.ISLAND_IDLE_H == 60


class TestV163DomHarness(unittest.TestCase):
    def test_jsdom_harness(self):
        node_path = _find_jsdom_node_path()
        if node_path is None:
            self.skipTest(
                "jsdom not installed; run "
                "`npm install jsdom --prefix /tmp/jsdom-test` and set "
                "NODE_PATH=/tmp/jsdom-test/node_modules")
        harness = TESTS / "v163_dom.js"
        proc = subprocess.run(
            ["node", str(harness)],
            capture_output=True, text=True, cwd=str(ROOT),
            env={**os.environ, "NODE_PATH": node_path})
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail("harness did not print JSON:\n" + proc.stdout + proc.stderr)
        if "harness_error" in result:
            self.fail("harness error: " + result["harness_error"])
        failed = [k for k, v in result.items() if not v["pass"]]
        assert failed == [], "jsdom checks failed: " + json.dumps(
            result, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
