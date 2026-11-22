"""UI static tests: every bridge call in the pages must map to a real
Api method; the UI must contain no CDN links, no Math.random, no
emoji; the bundled fonts + mascot must exist.
"""

import pathlib
import re

from lona2.bridge import Api

UI_DIR = pathlib.Path(__file__).resolve().parents[1] / "ui"

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    "\U0000FE0F]")


def _ui_text():
    texts = {}
    for path in sorted(UI_DIR.glob("*.html")):
        texts[path.name] = path.read_text(encoding="utf-8")
    # The pages call the bridge through bridge.js (B.call('method')),
    # so the JS is part of the UI surface too.
    for path in sorted((UI_DIR / "assets").glob("*.js")):
        texts["assets/" + path.name] = path.read_text(encoding="utf-8")
    return texts


def test_ui_pages_exist():
    names = {p.name for p in UI_DIR.glob("*.html")}
    assert {"index.html", "island.html", "invite.html"} <= names


def test_ui_calls_map_to_real_bridge_methods():
    methods = {name for name in dir(Api)
               if not name.startswith("_") and callable(getattr(Api, name))}
    called = set()
    for name, text in _ui_text().items():
        for match in re.finditer(r"js_api\.(\w+)\s*\(", text):
            called.add(match.group(1))
        for match in re.finditer(r"\bapi\.(\w+)\s*\(", text):
            called.add(match.group(1))
        # The pages invoke the bridge as string literals:
        # B.call('method', ...) / B.call("method", ...)
        for match in re.finditer(r"""B\.call\(\s*['"](\w+)['"]""", text):
            called.add(match.group(1))
        # Ternary form: B.call(cond ? 'a' : 'b') — capture both arms.
        for match in re.finditer(r"""B\.call\([^)]*\?\s*['"](\w+)['"]\s*:\s*['"](\w+)['"]""", text):
            called.add(match.group(1))
            called.add(match.group(2))
    assert called, "no bridge calls found in the UI at all"
    unknown = called - methods
    assert not unknown, f"UI calls unknown bridge methods: {unknown}"


def test_ui_has_no_cdn_links():
    for name, text in _ui_text().items():
        assert "fonts.googleapis" not in text, name
        assert "fonts.gstatic" not in text, name
        assert "http://cdn" not in text and "https://cdn" not in text, name


def test_ui_has_no_math_random():
    for name, text in _ui_text().items():
        assert "Math.random" not in text, name


def test_ui_has_no_emoji():
    for name, text in _ui_text().items():
        found = _EMOJI_RE.findall(text)
        assert not found, f"{name} contains emoji: {found[:5]}"


def test_ui_has_no_empty_catch():
    for name, text in _ui_text().items():
        assert not re.search(r"catch\s*\([^)]*\)\s*\{\s*\}", text), name


def test_bundled_fonts_exist():
    fonts = UI_DIR / "assets" / "fonts"
    assert (fonts / "fonts.css").exists()
    woff2 = sorted(fonts.glob("*.woff2"))
    assert len(woff2) >= 8, f"expected 8 woff2 files, got {len(woff2)}"
    for path in woff2:
        assert path.stat().st_size > 1000


def test_mascot_asset_exists():
    mascot = UI_DIR / "assets" / "mascot.webp"
    assert mascot.exists()
    assert mascot.stat().st_size > 10000
    for name in ("index.html", "island.html", "invite.html"):
        text = (UI_DIR / name).read_text(encoding="utf-8")
        assert "mascot.webp" in text or "mascot" in text, name


def test_island_has_no_idle_shrink():
    island = (UI_DIR / "island.html").read_text(encoding="utf-8")
    assert "190" not in island or "IDLE_W = 380" in island
    assert "IDLE_W = 380" in island or "380" in island
