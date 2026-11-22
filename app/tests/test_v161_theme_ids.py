"""Theme-id fix (v1.6.1): the Python bridge and the frontend must agree on
theme ids, or a saved calm/dashboard theme silently falls back after restart.

Bug: main.py get_themes returned ids night-glass / calm-light / graphite and
set_theme only knew those, but app.js THEMES and the CSS use calm-space /
dashboard. applyTheme() calls b.set_theme(id) with the frontend id, so
picking calm-space/dashboard never persisted (unknown_theme), and
loadTheme() applies get_themes().current at startup -- a stored calm-space
would have been unknown to the old Python side.

Fix: main.py now returns and accepts the frontend ids (old, night-glass,
calm-space, dashboard). The two legacy Python ids are kept only as aliases
mapping to the real ones, so a value stored by v1.6.0 still round-trips
instead of silently falling back to night-glass.

main.py cannot be imported in this sandbox (pywebview is not installed), so
the round-trip test extracts the two methods from the source and executes
them against stubbed _APP / save_settings / info.
"""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "app" / "main.py"
APP_JS = ROOT / "app" / "ui" / "app.js"
CSS = ROOT / "app" / "ui" / "styles.css"

EXPECTED_IDS = ("old", "night-glass", "calm-space", "dashboard")
LEGACY_MAP = {"calm-light": "calm-space", "graphite": "dashboard"}


def _src():
    return MAIN.read_text(encoding="utf-8")


def _exec_methods():
    """Exec get_themes/set_theme from main.py against stubs; return them."""
    tree = ast.parse(_src())
    fns = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name in ("get_themes", "set_theme"):
            fns[node.name] = node
    assert set(fns) == {"get_themes", "set_theme"}, \
        "get_themes/set_theme not found in main.py"
    lines = _src().splitlines()
    ns = {}
    for name, node in fns.items():
        seg = "\n".join(lines[node.lineno - 1:node.end_lineno])
        dedented = re.sub(r"^    ", "", seg, flags=re.M)
        exec(compile(dedented, str(MAIN), "exec"), ns)
    return ns["get_themes"], ns["set_theme"]


class _FakeApp:
    def __init__(self):
        self.settings = {}


@pytest.fixture()
def api():
    get_themes, set_theme = _exec_methods()
    fake = _FakeApp()
    saved = {}

    def save_settings(settings):
        saved.update(settings)
        return True

    ns = {"_APP": fake, "save_settings": save_settings,
          "info": lambda *a, **k: None}
    return fake, saved, get_themes, set_theme, ns


def test_get_themes_ids_match_frontend():
    """get_themes returns exactly the ids THEMES in app.js uses."""
    js = APP_JS.read_text(encoding="utf-8")
    js_ids = re.findall(r'id:\s*"([a-z-]+)"', js.split("const THEMES")[1].split("];")[0])
    src = _src()
    get_block = src.split("def get_themes")[1].split("def set_theme")[0]
    py_ids = re.findall(r'"id":\s*"([a-z-]+)"', get_block)
    assert set(py_ids) == set(js_ids) == set(EXPECTED_IDS), \
        f"Python {py_ids} vs JS {js_ids}"


def test_set_theme_known_set_matches_frontend():
    src = _src()
    set_block = src.split("def set_theme")[1].split("def get_device_overview")[0]
    known = re.search(r'known\s*=\s*\{([^}]*)\}', set_block)
    assert known, "known set not found in set_theme"
    ids = set(re.findall(r'"([a-z-]+)"', known.group(1)))
    assert ids == set(EXPECTED_IDS), f"known set {ids} != {set(EXPECTED_IDS)}"


def test_no_legacy_id_returned_or_accepted_as_itself():
    src = _src()
    get_block = src.split("def get_themes")[1].split("def set_theme")[0]
    # Only the returned theme list counts here -- the _legacy alias map and
    # its comment intentionally name the old ids.
    returned = re.findall(r'"id":\s*"([a-z-]+)"', get_block)
    assert not (set(returned) & set(LEGACY_MAP)), \
        f"get_themes still returns a legacy id: {returned}"
    set_block = src.split("def set_theme")[1].split("def get_device_overview")[0]
    known = re.search(r'known\s*=\s*\{([^}]*)\}', set_block).group(1)
    assert "calm-light" not in known and "graphite" not in known, \
        "set_theme known set still contains a legacy id"


def test_css_has_blocks_for_every_id():
    css = CSS.read_text(encoding="utf-8")
    for theme_id in EXPECTED_IDS:
        assert f'data-theme="{theme_id}"' in css, \
            f"styles.css has no block for {theme_id}"


def test_round_trip_calm_space(api):
    fake, saved, get_themes, set_theme, ns = api
    # Call the extracted plain functions with the stub namespace globals.
    get_themes.__globals__.update(ns)
    set_theme.__globals__.update(ns)
    r = set_theme(fake, "calm-space")
    assert r == {"ok": True, "theme": "calm-space"}, r
    assert fake.settings["theme"] == "calm-space"
    assert get_themes(fake)["current"] == "calm-space"


def test_round_trip_dashboard(api):
    fake, saved, get_themes, set_theme, ns = api
    get_themes.__globals__.update(ns)
    set_theme.__globals__.update(ns)
    assert set_theme(fake, "dashboard")["theme"] == "dashboard"
    assert get_themes(fake)["current"] == "dashboard"


def test_legacy_ids_map_to_real_ids(api):
    fake, saved, get_themes, set_theme, ns = api
    get_themes.__globals__.update(ns)
    set_theme.__globals__.update(ns)
    for legacy, real in LEGACY_MAP.items():
        assert set_theme(fake, legacy) == {"ok": True, "theme": real}, legacy
        assert fake.settings["theme"] == real
        fake.settings["theme"] = legacy  # as stored by v1.6.0
        assert get_themes(fake)["current"] == real, legacy


def test_unknown_theme_still_rejected(api):
    fake, saved, get_themes, set_theme, ns = api
    get_themes.__globals__.update(ns)
    set_theme.__globals__.update(ns)
    assert set_theme(fake, "no-such-theme") == {
        "ok": False, "error": "unknown_theme"}


def test_startup_applies_stored_theme():
    """loadTheme() in app.js applies get_themes().current at startup."""
    js = APP_JS.read_text(encoding="utf-8")
    assert "get_themes" in js
    assert re.search(r"applyTheme\(\s*r\.current\s*\)", js), \
        "startup does not apply get_themes().current"
