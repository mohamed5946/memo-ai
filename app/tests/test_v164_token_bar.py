# LONA v1.6.4 rc.2 - W10: token bar in Settings ("التوكينز").
#
# The Settings section shows, per engine (Groq, Gemini), the
# REMAINING tokens as a decreasing bar + percentage, with the
# used/limit numbers and the window reset time. Data comes from
# the pure read Api.get_token_usage (W8) - never a probe.
#
# This file proves:
#   * the "التوكينز" section exists in the settings menu and has
#     its own Bsec builder + icon;
#   * the JS calls get_token_usage (once, from refreshTokenUsage),
#     after every chat reply and when Settings opens - and NOT
#     from any poll loop;
#   * has_data false renders "—" / "لا يوجد استخدام بعد", never a
#     fake 100% or a bar;
#   * estimated usage is labelled "تقديري";
#   * bar width/percentage math is remaining/limit, clamped to
#     0-100%, with no division by a null limit (Gemini has no
#     per-minute token limit configured - it gets no bar, and its
#     limit renders "—");
#   * the reset countdown reads "يتجدد بعد N ثانية/دقيقة";
#   * the .tok-* styles exist in lona.css;
#   * the inline script in index.html still parses (node --check).
#
#   python3 -m pytest app/tests/test_v164_token_bar.py -q

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
UI_DIR = APP_DIR / "ui"
INDEX = UI_DIR / "index.html"
CSS = UI_DIR / "assets" / "lona.css"

NO_DATA_GROQ = {"used": None, "limit": 8000, "remaining": None,
                "reset_in_seconds": None, "estimated": True,
                "has_data": False}
NO_DATA_GEMINI = {"used": None, "limit": None, "remaining": None,
                  "reset_in_seconds": None, "estimated": True,
                  "has_data": False}


def _read(path):
    return path.read_text(encoding="utf-8")


def _pure_js():
    """The pure token-bar helpers from index.html, between the
    TOKEN_BAR_PURE markers - no DOM, no bridge, so node can run
    them directly."""
    src = _read(INDEX)
    m = re.search(r"/\* TOKEN_BAR_PURE_BEGIN \*/(.*?)"
                  r"/\* TOKEN_BAR_PURE_END \*/", src, re.S)
    assert m, "TOKEN_BAR_PURE markers missing from index.html"
    return m.group(1)


def _run_js(cases):
    """Run [{fn, args}] against the pure helpers under node and
    return the list of results (JSON round-tripped)."""
    driver = (
        "const cases = JSON.parse(process.argv[2]);\n"
        "const out = cases.map(c => {\n"
        "  const fn = eval(c.fn);\n"
        "  return fn.apply(null, c.args);\n"
        "});\n"
        "process.stdout.write(JSON.stringify(out));\n")
    with tempfile.NamedTemporaryFile("w", suffix=".js",
                                    encoding="utf-8",
                                    delete=False) as fh:
        fh.write(_pure_js() + "\n" + driver)
        tmp = fh.name
    try:
        proc = subprocess.run(
            ["node", tmp, json.dumps(cases)],
            capture_output=True, text=True, timeout=30,
            # encoding="utf-8": node writes UTF-8, but text=True
            # decodes with the locale encoding - cp1252 on the
            # Windows CI runner, which mojibakes every Arabic string
            # node returns (same bug class as the NamedTemporaryFile
            # write above; the write-side fix alone left these 4
            # tests failing on CI with mojibake assertions).
            encoding="utf-8")
    finally:
        Path(tmp).unlink(missing_ok=True)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout)


class TestTokenBarSection(unittest.TestCase):
    """The section exists in the settings menu."""

    def test_section_in_menu_and_builder(self):
        src = _read(INDEX)
        self.assertIn("['tokens', 'التوكينز']", src)
        self.assertIn("tokens: function () {", src)
        self.assertIn("tokens: \"<path d='M5 19a9 9", src)
        self.assertIn("id='tokRoot'", src)

    def test_css_classes_exist(self):
        css = _read(CSS)
        for cls in (".tok-card", ".tok-head", ".tok-pct", ".tok-bar",
                    ".tok-fill", ".tok-meta"):
            self.assertIn(cls, css, f"{cls} missing from lona.css")
        self.assertIn(".tok-fill.low", css)

    def test_inline_script_parses(self):
        src = _read(INDEX)
        scripts = re.findall(r"<script>(.*?)</script>", src, re.S)
        self.assertTrue(scripts, "no inline script found")
        with tempfile.NamedTemporaryFile("w", suffix=".js",
                                        encoding="utf-8",
                                        delete=False) as fh:
            fh.write("\n".join(scripts))
            tmp = fh.name
        try:
            proc = subprocess.run(["node", "--check", tmp],
                                  capture_output=True, text=True,
                                  timeout=30, encoding="utf-8")
        finally:
            Path(tmp).unlink(missing_ok=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TestTokenBarWiring(unittest.TestCase):
    """get_token_usage is called after every reply and on open,
    never from a poll loop."""

    def test_js_calls_get_token_usage_once(self):
        src = _read(INDEX)
        self.assertEqual(src.count("B.call('get_token_usage')"), 1,
                         "get_token_usage must be called from exactly "
                         "one place (refreshTokenUsage)")
        self.assertIn("function refreshTokenUsage() {", src)

    def test_refresh_after_every_reply(self):
        src = _read(INDEX)
        # sendChat's final .then (chatBusy reset) re-fetches usage.
        self.assertIn("chatBusy = false; refreshTokenUsage();", src)

    def test_refresh_when_settings_opens(self):
        src = _read(INDEX)
        self.assertIn(
            "if (v === 'settings') { renderMenu(); refreshTokenUsage(); }",
            src)
        # And when the tokens section itself renders.
        self.assertIn("} else if (id === 'tokens') {\n"
                      "      load = refreshTokenUsage();", src)
        self.assertIn("if (id === 'tokens') { paintTokenUsage(); }",
                      src)

    def test_no_poll_loop(self):
        src = _read(INDEX)
        self.assertNotIn("setInterval(refreshTokenUsage", src)
        self.assertNotIn("setInterval(function () { refreshTokenUsage",
                         src)
        # The clock tick is the only setInterval in the file; no
        # interval may touch the token usage at all.
        for m in re.finditer(r"setInterval\([^;]{0,120}", src):
            self.assertNotIn("get_token_usage", m.group(0))
            self.assertNotIn("refreshTokenUsage", m.group(0))


class TestTokenBarMath(unittest.TestCase):
    """Bar width/percentage = remaining/limit, clamped 0-100%,
    no division by a null limit."""

    def test_percentage_math(self):
        res = _run_js([
            {"fn": "tokenBarPct",
             "args": [{"remaining": 4950, "limit": 8000}]},
            {"fn": "tokenBarPct",
             "args": [{"remaining": 8000, "limit": 8000}]},
            {"fn": "tokenBarPct",
             "args": [{"remaining": 0, "limit": 8000}]},
            {"fn": "tokenBarPct",
             "args": [{"remaining": 9000, "limit": 8000}]},
            {"fn": "tokenBarPct",
             "args": [{"remaining": -50, "limit": 8000}]},
            {"fn": "tokenBarPct",
             "args": [{"remaining": 100, "limit": None}]},
            {"fn": "tokenBarPct",
             "args": [{"remaining": None, "limit": 8000}]},
            {"fn": "tokenBarPct", "args": [{}]},
        ])
        self.assertEqual(res, [62, 100, 0, 100, 0, None, None, None])

    def test_reset_text(self):
        res = _run_js([
            {"fn": "tokenResetText",
             "args": [{"reset_in_seconds": 40}]},
            {"fn": "tokenResetText",
             "args": [{"reset_in_seconds": 1}]},
            {"fn": "tokenResetText",
             "args": [{"reset_in_seconds": 90}]},
            {"fn": "tokenResetText",
             "args": [{"reset_in_seconds": None}]},
            {"fn": "tokenResetText", "args": [{}]},
        ])
        self.assertEqual(res, ["يتجدد بعد 40 ثانية",
                               "يتجدد بعد 1 ثانية",
                               "يتجدد بعد 2 دقيقة",
                               "—", "—"])


class TestTokenBarRender(unittest.TestCase):
    """What the user actually sees per engine state."""

    def test_no_data_renders_dash_never_fake_100(self):
        res = _run_js([
            {"fn": "tokenEngineHtml", "args": ["Groq", NO_DATA_GROQ]},
            {"fn": "tokenEngineHtml",
             "args": ["Gemini", NO_DATA_GEMINI]},
        ])
        for html in res:
            self.assertIn("—", html)
            self.assertIn("لا يوجد استخدام بعد", html)
            self.assertNotIn("100%", html)
            self.assertNotIn("tok-bar", html)
            self.assertNotIn("width:", html)

    def test_groq_with_data_shows_bar_numbers_reset(self):
        res = _run_js([
            {"fn": "tokenEngineHtml", "args": ["Groq", {
                "used": 3050, "limit": 8000, "remaining": 4950,
                "reset_in_seconds": 40, "estimated": True,
                "has_data": True}]}])
        html = res[0]
        self.assertIn("62%", html)
        self.assertIn("width:62%", html)
        self.assertIn("المستخدم: 3,050", html)
        self.assertIn("الحد: 8,000", html)
        self.assertIn("المتبقي: 4,950", html)
        self.assertIn("يتجدد بعد 40 ثانية", html)
        self.assertIn("تقديري", html)

    def test_header_true_remaining_has_no_estimated_label(self):
        res = _run_js([
            {"fn": "tokenEngineHtml", "args": ["Groq", {
                "used": 110, "limit": 8000, "remaining": 7890,
                "reset_in_seconds": 55, "estimated": False,
                "has_data": True}]}])
        html = res[0]
        self.assertNotIn("تقديري", html)
        self.assertIn("99%", html)
        self.assertIn("width:99%", html)

    def test_gemini_no_limit_means_no_bar(self):
        # Gemini has no per-minute token limit configured: the
        # limit renders "—" and there is no bar to invent.
        res = _run_js([
            {"fn": "tokenEngineHtml", "args": ["Gemini", {
                "used": 240, "limit": None, "remaining": None,
                "reset_in_seconds": None, "estimated": True,
                "has_data": True}]}])
        html = res[0]
        self.assertIn("المستخدم: 240", html)
        self.assertIn("الحد: —", html)
        self.assertIn("—", html.split("tok-pct")[1][:12])
        self.assertNotIn("tok-bar", html)
        self.assertNotIn("width:", html)

    def test_low_remaining_gets_low_class(self):
        res = _run_js([
            {"fn": "tokenEngineHtml", "args": ["Groq", {
                "used": 7000, "limit": 8000, "remaining": 1000,
                "reset_in_seconds": 30, "estimated": True,
                "has_data": True}]},
            {"fn": "tokenEngineHtml", "args": ["Groq", {
                "used": 3050, "limit": 8000, "remaining": 4950,
                "reset_in_seconds": 40, "estimated": True,
                "has_data": True}]},
        ])
        self.assertIn("tok-fill low", res[0])
        self.assertIn("13%", res[0])
        self.assertNotIn("tok-fill low", res[1])


if __name__ == "__main__":
    unittest.main()
