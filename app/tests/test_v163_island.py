"""LONA v1.6.3 regression tests — the island idle shrink (critic finding
on v1.6.2).

Run:  python3 -m pytest app/tests/test_v163_island.py -q

The v1.6.2 code created the island window at 380x60 but the JS state
machine still believed the idle size was 190x52: 7s after any greeting
goIdle() animated the window down to 190x52 while the pill CSS stays
380px wide, so the pill was clipped to its central 190px (robot bubble
invisible, texts cut mid-word) most of the time. The first goActive()
also squeezed the real 380-wide window down toward 190 before growing
back, because JS believed curW was 190.

The fix (per the original F4 order, "idle window = 380x60"): the idle
shrink is removed entirely — IDLE == FULL (380x60) in every collapsed
state; only the expanded state is 720x400. The 7s idle timer stays but
only clears the expanded-reply text; it must NOT resize the window.
curW/curH start at the real window size (380x60), so the first
goActive() is a no-op, not a shrink-then-grow.

Covers:
  * sizing constants: IDLE_W/H == FULL_W/H == 380/60 (no 190x52)
  * goIdle() contains no resize call
  * curW/curH start at the real window size
  * Python ISLAND_IDLE_W/H == 380/60 (window creation size)
  * jsdom runtime harness (app/tests/v163_dom.js): the island window is
    never resized below 380x60 in any collapsed state, and
    island_resize is never called with w < 380 except for the 720x400
    expanded state. The harness FAILS on the v1.6.2 code and PASSES
    on the fix (verified both ways during development).
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
        m = re.search(
            r"const IDLE_W = (\d+), IDLE_H = (\d+), "
            r"FULL_W = (\d+), FULL_H = (\d+), "
            r"EXPANDED_W = (\d+), EXPANDED_H = (\d+);", ISLAND)
        assert m, "sizing constants line not found in island.html"
        idle_w, idle_h, full_w, full_h, exp_w, exp_h = map(int, m.groups())
        assert (idle_w, idle_h) == (380, 60), f"idle is {idle_w}x{idle_h}"
        assert (full_w, full_h) == (380, 60), f"full is {full_w}x{full_h}"
        assert (exp_w, exp_h) == (720, 400), f"expanded is {exp_w}x{exp_h}"

    def test_no_190x52_sizing_anywhere(self):
        m = re.search(
            r"const IDLE_W = .*?;", ISLAND)
        assert "190" not in m.group(0), "190px idle width still present"
        assert "52" not in m.group(0).split("FULL_W")[0], \
            "52px idle height still present"

    def test_cur_starts_at_real_window_size(self):
        # The JS belief must match the window Python creates (380x60),
        # or the first goActive() squeezes the window down before
        # growing it back.
        assert re.search(r"let curW = IDLE_W, curH = IDLE_H", ISLAND), \
            "curW/curH must start at IDLE (380x60), not at a smaller size"


class TestIdleTimerIsHarmless(unittest.TestCase):
    def test_go_idle_never_resizes(self):
        body = _extract(ISLAND, "function goIdle()")
        assert "animateSize" not in body, \
            "goIdle must not resize the window: " + body
        assert "island_resize" not in body
        # It still does something harmless: clears the expanded reply.
        assert 'exp-reply' in body

    def test_idle_timer_hook_exists(self):
        assert "window.__islandIdleMs" in ISLAND, \
            "test hook for the 7s idle timer missing"


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
