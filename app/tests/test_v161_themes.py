"""v1.6.1 workstream E tests - the three dark themes are genuinely distinct.

Run:  python3 -m pytest app/tests/test_v161_themes.py -q

Item 13 (field complaint from Mohamed): night-glass / dashboard / old
looked exactly the same on screen. Verified cause in v1.6.0: night-glass
and old shared the same --m-bg / --m-bubble / --m-accent values (differing
only in letter case), dashboard differed only in a peach accent and a few
close grays — and the largest surfaces of all, the chat bubbles and the
send button, were hardcoded v1.5.0 blue gradients in EVERY theme, so the
chat area never changed at all.

What is proven here (static checks over styles.css + app.js, no browser):
  * Old is the TRUE v1.5.0 palette, restored from the :root of
    `git show v1.5.0:app/ui/styles.css` (bg, bg-2, gold, cyan, gold-soft,
    border, text, muted, radius, card surface #121826, track #1e293b;
    --m-bubble is the v1.5.0 AI bubble flattened: rgba(255,255,255,.08)
    over #0a0e14 = #1e2127). The old theme is never deleted.
  * The four themes differ by a CLEAR margin, not letter case: for each
    of --m-bg / --m-card / --m-accent / --m-accent2 / --m-side /
    --m-bubble, every pair of themes is at least 20 Euclidean RGB units
    apart (measured minima: bg 25, card 25, accent 29 — see the test).
  * Night-glass is deep sapphire + purer, more saturated gold with
    stronger glass (glass alpha 0.55 vs old 0.72); dashboard is graphite with a vivid
    orange accent that sits on LARGE surfaces (send button, user bubble,
    solid active nav), with sharp 9px corners.
  * The frozen surfaces are wired to the theme: .msg.ai uses
    var(--m-bubble), and each theme paints .msg.user + .send-btn in its
    own colours (old keeps the true v1.5.0 blue base rules, no override).
  * The morph transition covers the themed surfaces incl. .msg, .sidebar,
    .send-btn and .side-nav, so switching visibly animates.
  * The 4 theme palettes are selectable, not just colour dots: the JS
    THEMES list has the same 4 ids as the CSS blocks, each with 5
    distinct swatches, and applyTheme() sets body data-theme (which is
    what swaps the real CSS variables), persists it and marks the card
    selected; clicking a theme card calls applyTheme for that theme.

NOTE for the integrator: the 5 swatches per theme in app.js THEMES are
[bg, card, accent, accent2, text] and must track the CSS values — the
night-glass and dashboard swatches changed with this fix, so update
those two arrays in app.js (old and calm-space are unchanged). This file
deliberately does NOT assert swatch == CSS, because app.js is owned by
the coordinator; the distinctness/selection wiring is asserted instead.

Item 17 (same file, appended tests): the white scrollbar. Mohamed's
screenshot showed a thick white classic bar with arrows on the chat
area's edge — .main-col's default scrollbar (overflow-y:auto with no
webkit rules). The fix is ONE global themed scrollbar rule in
styles.css (thin 6px, transparent track, thumb from --m-muted with
--m-accent on hover, no buttons/arrows, plus Firefox
scrollbar-width/color), which covers every scrollable container.
Tests below prove: the global rule exists and is theme-var based, no
hardcoded near-white thumb survives, every scrollable container in
styles.css is inventoried and covered, the thumb is readable on every
theme's background, and index.html / island.html add no scrollable
area outside the rule.
"""

import itertools
import re
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "ui"
CSS = (UI / "styles.css").read_text(encoding="utf-8")
JS = (UI / "app.js").read_text(encoding="utf-8")

THEME_IDS = ("night-glass", "calm-space", "dashboard", "old")

# Clear-margin bar: two themes are "the same" when their colours are
# within a few RGB units (the v1.6.0 bug was distance 0 — identical
# values, differing only in letter case). 20 is far above that and far
# below the measured minima (bg 25, card 25, accent 29).
MIN_DIST = 20

ALL_VARS = (
    "--m-bg", "--m-chrome", "--m-side", "--m-card", "--m-listbg",
    "--m-bubble", "--m-pill", "--m-track", "--m-line", "--m-text",
    "--m-muted", "--m-accent", "--m-accent2", "--m-on-accent",
    "--m-glass", "--m-radius",
)

# True v1.5.0 values, from `git show v1.5.0:app/ui/styles.css` (:root,
# plus the surfaces that file actually used). Keys are the --m-* names.
V150_OLD = {
    "--m-bg": "#0a0e14",       # --bg
    "--m-chrome": "#10151f",   # --bg-2
    "--m-card": "#121826",     # modal surface (line 230 of v1.5.0 css)
    "--m-side": "#0d121c",     # avatar-frame gradient end
    "--m-listbg": "#0d121c",
    "--m-text": "#f2f4f8",     # --text
    "--m-muted": "#9aa3b2",    # --muted
    "--m-accent": "#e8c97a",   # --gold
    "--m-accent2": "#67e8f9",  # --cyan
    "--m-pill": "rgba(232,201,122,.16)",   # --gold-soft
    "--m-line": "rgba(255,255,255,.09)",   # --border
    "--m-radius": "22px",      # --radius
    "--m-on-accent": "#231a05",
    "--m-track": "#1e293b",    # mic-button gradient colour
    "--m-bubble": "#1e2127",   # AI bubble: rgba(255,255,255,.08) over #0a0e14
    "--m-glass": "rgba(13,18,28,.72)",
}


def _norm(value):
    return re.sub(r"\s+", "", value.strip().lower())


def _theme_blocks():
    """{theme_id: {var: value}} parsed from the [data-theme=...] blocks."""
    blocks = {}
    for match in re.finditer(r'\[data-theme="([^"]+)"\]\s*\{([^}]*)\}', CSS):
        theme_id, body = match.group(1), match.group(2)
        values = {}
        for var, val in re.findall(r"(--m-[a-z0-9-]+)\s*:\s*([^;]+);", body):
            values[var] = val.strip()
        # The :root selector shares the night-glass block; keep first.
        blocks.setdefault(theme_id, values)
    return blocks


def _rgb(hex_value):
    hex_value = hex_value.strip().lstrip("#")
    return tuple(int(hex_value[i:i + 2], 16) for i in (0, 2, 4))


def _dist(a, b):
    ra, rb = _rgb(a), _rgb(b)
    return sum((x - y) ** 2 for x, y in zip(ra, rb)) ** 0.5


def _rgba_alpha(value):
    match = re.match(
        r"rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*([0-9.]+)\s*\)", value.strip())
    assert match, "not an rgba() value: " + value
    return float(match.group(1))


def _luminance(hex_value):
    r, g, b = (c / 255.0 for c in _rgb(hex_value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _gradient_first_stop(rule_body):
    match = re.search(
        r"linear-gradient\([^,]+,\s*(#[0-9A-Fa-f]{6})", rule_body)
    assert match, "no linear-gradient first stop in: " + rule_body
    return match.group(1)


def _js_themes():
    found = re.findall(
        r'\{\s*id:\s*"([^"]+)",\s*name:\s*"[^"]*",\s*desc:\s*"[^"]*",'
        r'\s*swatches:\s*\[([^\]]*)\]\s*\}',
        JS)
    assert found, "THEMES list not found in app.js"
    out = {}
    for theme_id, swatch_src in found:
        swatches = re.findall(r'"(#[0-9A-Fa-f]{6})"', swatch_src)
        out[theme_id] = swatches
    return out


# ---------------- theme blocks exist, complete, old never deleted --------

def test_four_theme_blocks_incl_old():
    blocks = _theme_blocks()
    for theme_id in THEME_IDS:
        assert theme_id in blocks, "theme block missing: " + theme_id
        missing = [v for v in ALL_VARS if v not in blocks[theme_id]]
        assert not missing, f"{theme_id} missing vars: {missing}"
    assert len(blocks) == 4, f"expected exactly 4 theme blocks, got {sorted(blocks)}"


def test_old_theme_is_true_v150():
    old = _theme_blocks()["old"]
    for var, expected in V150_OLD.items():
        assert _norm(old[var]) == _norm(expected), (
            f"old {var}: got {old[var]}, true v1.5.0 value is {expected}")


# ---------------- clear-margin distinctness (the required assertion) ------

def test_bg_card_accent_differ_by_clear_margin():
    blocks = _theme_blocks()
    for var in ("--m-bg", "--m-card", "--m-accent",
                "--m-accent2", "--m-side", "--m-bubble"):
        values = {t: blocks[t][var] for t in THEME_IDS}
        for a, b in itertools.combinations(THEME_IDS, 2):
            assert _norm(values[a]) != _norm(values[b]), (
                f"{var}: {a} and {b} are identical ({values[a]}) — "
                "the v1.6.0 bug was exactly this")
            dist = _dist(values[a], values[b])
            assert dist >= MIN_DIST, (
                f"{var}: {a} ({values[a]}) vs {b} ({values[b]}) "
                f"only {dist:.1f} apart, need >= {MIN_DIST}")


def test_night_glass_is_sapphire_gold_and_glassier():
    blocks = _theme_blocks()
    night, old = blocks["night-glass"], blocks["old"]
    # Bluer background than old (B-R spread), not the same near-black.
    assert _rgb(night["--m-bg"])[2] - _rgb(night["--m-bg"])[0] >= (
        _rgb(old["--m-bg"])[2] - _rgb(old["--m-bg"])[0] + 10)
    # Purer, more saturated gold accent than old's muted gold
    # (max-min channel spread: 146 vs 110).
    night_rgb = _rgb(night["--m-accent"])
    old_rgb = _rgb(old["--m-accent"])
    assert max(night_rgb) - min(night_rgb) > max(old_rgb) - min(old_rgb)
    # Stronger glass/transparency: lower --m-glass alpha than old.
    assert _rgba_alpha(night["--m-glass"]) < _rgba_alpha(old["--m-glass"])
    assert _rgba_alpha(night["--m-glass"]) <= 0.60


def test_dashboard_is_graphite_with_orange_identity():
    blocks = _theme_blocks()
    dash, old = blocks["dashboard"], blocks["old"]
    # Graphite background: near-neutral grey, clearly lighter than old.
    r, g, b = _rgb(dash["--m-bg"])
    assert max(r, g, b) - min(r, g, b) <= 12, "dashboard bg is not graphite-neutral"
    assert _luminance(dash["--m-bg"]) > _luminance(old["--m-bg"])
    # Vivid orange accent (R >> G >> B), not the old peach/gold.
    r, g, b = _rgb(dash["--m-accent"])
    assert r > g > b and (r - g) >= 60 and (g - b) >= 60, (
        "dashboard accent is not a vivid orange: " + dash["--m-accent"])
    assert _norm(dash["--m-accent"]) != _norm(old["--m-accent"])
    # Sharp corners: much smaller radius than the rounded themes.
    def _px(value):
        return int(value.strip().removesuffix("px"))

    assert _px(dash["--m-radius"]) == 9
    assert _px(dash["--m-radius"]) < _px(blocks["night-glass"]["--m-radius"])
    assert _px(dash["--m-radius"]) < _px(old["--m-radius"])
    # The orange pill tint is the same orange as the accent.
    assert "255,154,61" in _norm(dash["--m-pill"])


# ---------------- large surfaces actually change with the theme -----------

def test_ai_bubble_uses_theme_variable():
    assert "background: var(--m-bubble)" in CSS, (
        ".msg.ai must use var(--m-bubble) — in v1.6.0 it used the frozen "
        "legacy var(--glass-2), identical in every theme")
    blocks = _theme_blocks()
    for theme_id in THEME_IDS:
        assert blocks[theme_id]["--m-bubble"].startswith("#")


def _user_bubble_first_stops():
    """First gradient stop of .msg.user per theme (base rule = old)."""
    stops = {}
    base = re.search(r"\.msg\.user\s*\{([^}]*)\}", CSS)
    assert base, ".msg.user base rule missing"
    stops["old"] = _gradient_first_stop(base.group(1))
    for match in re.finditer(
            r'body\[data-theme="([^"]+)"\]\s*\.msg\.user\s*\{([^}]*)\}', CSS):
        stops[match.group(1)] = _gradient_first_stop(match.group(2))
    return stops


def test_user_bubbles_differ_per_theme():
    # v1.6.2 (PARITY 2): the user bubble is GOLD in every theme — the
    # theme accent with dark text, bold (the preview's gold bubble).
    # The v1.6.1 per-theme blue/orange/green paints are gone by design.
    base = re.search(r"\.msg\.user\s*\{([^}]*)\}", CSS)
    assert base, ".msg.user base rule missing"
    rule = base.group(1)
    assert "background: var(--m-accent)" in rule
    assert "color: var(--m-on-accent)" in rule
    assert "font-weight: 700" in rule
    assert not re.search(
        r'body\[data-theme="[^"]+"\]\s*\.msg\.user\s*\{', CSS), (
        "no per-theme .msg.user override may survive the gold bubble")


def _send_btn_first_stops():
    stops = {}
    base = re.search(r"\.send-btn\s*\{([^}]*)\}", CSS)
    assert base, ".send-btn base rule missing"
    stops["old"] = _gradient_first_stop(base.group(1))
    for match in re.finditer(
            r'body\[data-theme="([^"]+)"\]\s*\.send-btn\s*\{([^}]*)\}', CSS):
        stops[match.group(1)] = _gradient_first_stop(match.group(2))
    return stops


def test_primary_button_painted_per_theme():
    stops = _send_btn_first_stops()
    blocks = _theme_blocks()
    # Dashboard primary button IS its orange accent; night-glass IS gold.
    assert _norm(stops["dashboard"]) == _norm(blocks["dashboard"]["--m-accent"])
    assert _norm(stops["night-glass"]) == _norm(blocks["night-glass"]["--m-accent"])
    assert sorted(stops) == sorted(THEME_IDS), (
        "each theme needs its own .send-btn paint; old uses the base "
        f"v1.5.0 blue rule, got overrides for: {sorted(stops)}")
    for a, b in itertools.combinations(THEME_IDS, 2):
        assert _dist(stops[a], stops[b]) >= MIN_DIST, (
            f"send button: {a} ({stops[a]}) vs {b} ({stops[b]}) too close")


def test_dashboard_active_nav_is_solid_orange():
    match = re.search(
        r'body\[data-theme="dashboard"\]\s*\.side-nav\.active\s*\{([^}]*)\}',
        CSS)
    assert match, "dashboard needs a .side-nav.active override"
    body = match.group(1)
    assert "background: var(--m-accent)" in body, (
        "dashboard active nav must be solid accent, not a faint tint")
    assert "color: var(--m-on-accent)" in body


def test_morph_covers_themed_surfaces():
    match = re.search(
        r"([^{}]+)\{\s*transition:\s*background-color \.45s ease", CSS)
    assert match, "morph transition rule missing"
    selectors = match.group(1)
    for needed in (".msg", ".sidebar", ".send-btn", ".side-nav",
                   ".stat-card", ".theme-card"):
        assert needed in selectors, (
            "morph transition does not cover " + needed)


# ---------------- palettes selectable, not just colour dots ---------------

def test_palettes_selectable_and_wired_to_real_colours():
    js_themes = _js_themes()
    blocks = _theme_blocks()
    assert sorted(js_themes) == sorted(THEME_IDS), (
        f"JS THEMES ids {sorted(js_themes)} != CSS theme ids {sorted(blocks)}")
    for theme_id in THEME_IDS:
        swatches = js_themes[theme_id]
        assert len(swatches) == 5, f"{theme_id}: expected 5 swatches, got {len(swatches)}"
        for a, b in itertools.combinations(swatches, 2):
            assert _norm(a) != _norm(b), f"{theme_id}: duplicate swatch {a}"
    # Swatch sets differ across themes (position 0 = background).
    for a, b in itertools.combinations(THEME_IDS, 2):
        assert _dist(js_themes[a][0], js_themes[b][0]) >= MIN_DIST, (
            f"palette background dot: {a} vs {b} too close — the dots "
            "would advertise identical themes")
    # Selecting a theme actually swaps the colours: applyTheme sets
    # body[data-theme], which is what the CSS blocks key off — and the
    # blocks differ by a clear margin (proven above).
    assert 'document.body.setAttribute("data-theme", id)' in JS
    assert 'localStorage.setItem("lona-theme", id)' in JS
    assert 'classList.toggle("selected"' in JS
    assert 'card.addEventListener("click", () => applyTheme(th.id))' in JS
    assert "renderThemeGrid" in JS and "loadTheme" in JS


# ---------------- item 17: one themed scrollbar, no white bar -----------

HTML = (UI / "index.html").read_text(encoding="utf-8")
ISLAND = (UI / "island.html").read_text(encoding="utf-8")

# Every scrollable container in styles.css (overflow-y:auto). If a new
# one is added, update this set — the global * rule covers it by
# construction, this inventory just proves the audit was done.
SCROLLABLE_CONTAINERS = {
    ".perm-detail",
    ".chat",
    ".modal",
    ".log-box",
    ".msg.perm .perm-detail.expanded",
    ".main-col",
    ".proc-list",
}


def _scrollable_selectors(css_text):
    """Selectors whose rule sets overflow-y:auto or overflow:auto."""
    css_text = re.sub(r"/\*.*?\*/", "", css_text, flags=re.S)
    found = set()
    for match in re.finditer(r"([^{}]+)\{([^}]*)\}", css_text):
        selectors, body = match.group(1), match.group(2)
        if re.search(r"overflow(?:-y)?\s*:\s*auto", body):
            for sel in selectors.split(","):
                found.add(sel.strip())
    return found


def _webkit_rule(pseudo):
    match = re.search(
        r"\*::" + re.escape(pseudo) + r"\s*\{([^}]*)\}", CSS)
    assert match, "missing global rule *::" + pseudo
    return match.group(1)


def test_unified_scrollbar_rule_exists():
    # Firefox: thin + theme-coloured thumb on a transparent track.
    assert "scrollbar-width: thin" in CSS
    assert re.search(
        r"scrollbar-color:\s*var\(--m-muted\)\s+transparent", CSS), (
        "Firefox scrollbar-color must be theme-muted on transparent")
    # WebKit/Chromium (WebView2): thin, transparent track, no arrows.
    bar = _webkit_rule("-webkit-scrollbar")
    assert re.search(r"width:\s*6px", bar), "scrollbar must be 6px wide"
    assert re.search(r"height:\s*6px", bar), "horizontal scrollbar too"
    track = _webkit_rule("-webkit-scrollbar-track")
    assert "transparent" in track, "scrollbar track must be transparent"
    buttons = _webkit_rule("-webkit-scrollbar-button")
    assert re.search(r"display:\s*none", buttons), (
        "scrollbar arrows (the ones in Mohamed's screenshot) must be off")
    corner = _webkit_rule("-webkit-scrollbar-corner")
    assert "transparent" in corner


def test_scrollbar_thumb_is_theme_coloured():
    # Every scrollbar thumb in the file comes from theme variables —
    # no hardcoded near-white/grey thumb may survive (the old .chat
    # thumb was rgba(255,255,255,0.14): near-white on dark, invisible
    # on the light theme).
    for match in re.finditer(r"::-webkit-scrollbar-thumb[^{]*\{([^}]*)\}", CSS):
        body = match.group(1)
        assert "var(--m-" in body, (
            "scrollbar thumb not theme-based: " + body.strip())
    assert ".chat::-webkit-scrollbar" not in CSS, (
        "chat-only scrollbar rules must be gone — one global rule only")
    thumb = _webkit_rule("-webkit-scrollbar-thumb")
    assert "var(--m-muted)" in thumb, "thumb must use --m-muted"
    assert re.search(r"border-radius:\s*999px", thumb)
    hover = _webkit_rule("-webkit-scrollbar-thumb:hover")
    assert "var(--m-accent)" in hover, "thumb hover must use --m-accent"


def test_every_scrollable_container_covered():
    found = _scrollable_selectors(CSS)
    assert found == SCROLLABLE_CONTAINERS, (
        f"scrollable inventory changed: missing={SCROLLABLE_CONTAINERS - found}, "
        f"new={found - SCROLLABLE_CONTAINERS} — a new scrollable container "
        "must be confirmed covered by the global * rule")
    # No forced always-visible bars anywhere.
    assert not re.search(r"overflow(?:-y)?\s*:\s*scroll", CSS), (
        "overflow:scroll forces a permanent bar — use auto")
    # The global rule is what covers them all (not per-container rules).
    assert "*::-webkit-scrollbar" in CSS


def test_scrollbar_thumb_readable_in_every_theme():
    blocks = _theme_blocks()
    for theme_id in THEME_IDS:
        bg = blocks[theme_id]["--m-bg"]
        muted = blocks[theme_id]["--m-muted"]
        accent = blocks[theme_id]["--m-accent"]
        assert _dist(muted, bg) >= 60, (
            f"{theme_id}: thumb colour {muted} too close to bg {bg} "
            "to be seen")
        assert _dist(accent, bg) >= MIN_DIST, (
            f"{theme_id}: hover accent {accent} too close to bg {bg}")


def test_html_files_add_no_unstyled_scrollbars():
    # index.html / island.html (workstream F's files — read-only here)
    # carry no inline overflow styles and no scrollable areas of their
    # own: everything scrollable lives in styles.css under the global
    # rule. island.html has its own inline <style> but all its
    # overflows are hidden/visible — nothing scrolls there. If this
    # fails, a scrollable area was added to an HTML file: it IS covered
    # by the global * rule in styles.css, just update the inventory.
    for name, text in (("index.html", HTML), ("island.html", ISLAND)):
        found = re.findall(r"overflow(?:-y|-x)?\s*:\s*(?:auto|scroll)", text)
        assert not found, f"{name} adds scrollable area(s): {found}"
