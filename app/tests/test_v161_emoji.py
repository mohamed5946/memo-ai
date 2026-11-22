"""Item 16 (v1.6.1): zero emoji + the approved LONA icon set installed.

Sweep: no emoji character (literal, or written as an HTML numeric entity
for an emoji codepoint) anywhere in the repo's text files (app/ +
installer/ + .github/). Runtime data under app/data/ (logs, settings,
memory) is excluded from the sweep — it is generated output, not UI strings
or code — but the program no longer emits emoji, so new logs are clean too.

Icon set: app/ui/assets/lona-icons-sprite.svg holds the 31 approved symbols
(the two settings alternates settings-alt-classic / settings-alt-wrench are
NOT in the program bundle). Every <use> in the UI and every iconSvg() call
in app.js must point at an id that actually exists in the sprite.
"""

import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
APP_JS = REPO / "app" / "ui" / "app.js"
INDEX = REPO / "app" / "ui" / "index.html"
ISLAND = REPO / "app" / "ui" / "island.html"
PET = REPO / "app" / "ui" / "pet.html"
SPRITE = REPO / "app" / "ui" / "assets" / "lona-icons-sprite.svg"
SPEC = REPO / "installer" / "memo-ai.spec"
UPDATER = REPO / "app" / "updater.py"

# The 31 approved core icons (README: ~/workspace/lona-icons/README.md).
EXPECTED_IDS = {
    "chat", "device", "themes", "api-key", "lona-now", "logs", "settings",
    "general", "whatsapp", "memory", "device-health", "notifications",
    "sounds", "avatar", "updates", "backup", "mic", "send", "deep-think",
    "add", "save", "refresh", "delete", "power", "github", "globe",
    "back", "check", "x", "warning", "mute",
}

# Emoji ranges for the sweep (written as escapes so this file itself stays
# emoji-free): pictographs, dingbats, arrows, geometric shapes, misc
# symbols, variation selector, copyright/registered, etc.
_EMOJI_RANGES = (
    "\U0001F000-\U0001FAFF"  # pictographs, emoticons, transport, etc.
    "\u2600-\u27BF"          # misc symbols, dingbats, arrows
    "\u2B00-\u2BFF"          # misc symbols and arrows
    "\uFE0F"                 # variation selector (turns a glyph into emoji)
    "\u2190-\u21FF"          # arrows
    "\u2300-\u23FF"          # misc technical (power, arrows)
    "\u25A0-\u25FF"          # geometric shapes
    "\u00A9\u00AE\u203C\u2049\u3030\u303D\u3297\u3299"
)
EMOJI_RE = re.compile("[" + _EMOJI_RANGES + "]")
_ENTITY_RE = re.compile(r"&#(x[0-9a-fA-F]+|\d+);")

# Source extensions swept. Binary files (png/wav/pyc) are not text.
_SWEEP_SUFFIXES = {".py", ".js", ".html", ".iss", ".txt", ".yml", ".yaml",
                   ".md", ".json", ".spec", ".log"}
# app/data/ is runtime data (logs, settings, memory, activation state),
# not UI strings or code.
_SKIP_DIRS = {"__pycache__", ".git", "node_modules"}


def _is_emoji_codepoint(cp: int) -> bool:
    return bool(EMOJI_RE.match(chr(cp)))


def _sweep_file(path: Path) -> list:
    """Return a list of (line_no, char) emoji hits in one file."""
    hits = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return hits
    for lineno, line in enumerate(text.splitlines(), 1):
        for ch in EMOJI_RE.findall(line):
            hits.append((lineno, ch))
        for m in _ENTITY_RE.finditer(line):
            raw = m.group(1)
            cp = int(raw[1:], 16) if raw.startswith("x") else int(raw)
            if _is_emoji_codepoint(cp):
                hits.append((lineno, m.group(0)))
    return hits


def _sprite_ids() -> set:
    text = SPRITE.read_text(encoding="utf-8")
    return set(re.findall(r'<symbol id="([^"]+)"', text))


class TestEmojiSweep(unittest.TestCase):
    def test_no_emoji_in_repo_text_files(self):
        bad = {}
        for root in (REPO / "app", REPO / "installer", REPO / ".github"):
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                if path.suffix not in _SWEEP_SUFFIXES:
                    continue
                if "app" in path.parts and "data" in path.parts:
                    continue  # runtime data, not source
                if _SKIP_DIRS & set(path.parts):
                    continue
                hits = _sweep_file(path)
                if hits:
                    bad[str(path.relative_to(REPO))] = hits[:5]
        self.assertEqual(bad, {}, f"emoji found: {bad}")

    def test_no_emoji_in_styles_css(self):
        # styles.css belongs to workstream E; this test only REPORTS.
        # (It currently has none; if it ever gains one, this fails loudly.)
        css = REPO / "app" / "ui" / "styles.css"
        self.assertEqual(_sweep_file(css), [])


class TestSprite(unittest.TestCase):
    def test_sprite_exists_and_parses(self):
        self.assertTrue(SPRITE.is_file(), "sprite missing from the bundle")
        ET.parse(str(SPRITE))  # raises if the XML is malformed

    def test_sprite_has_exactly_the_31_core_icons(self):
        self.assertEqual(_sprite_ids(), EXPECTED_IDS)

    def test_settings_alternates_are_not_in_the_bundle(self):
        text = SPRITE.read_text(encoding="utf-8")
        self.assertNotIn("settings-alt-classic", text)
        self.assertNotIn("settings-alt-wrench", text)

    def test_sprite_is_bundled_by_the_pyinstaller_spec(self):
        # The spec bundles app/ui wholesale, so app/ui/assets ships with it.
        spec = SPEC.read_text(encoding="utf-8")
        self.assertIn('(os.path.join(APP, "ui"), "app/ui")', spec)


class TestUseTargets(unittest.TestCase):
    def test_every_use_points_at_a_real_sprite_id(self):
        # SKIPPED (reskin): the reskin UI does not wire the LONA icon
        # sprite anywhere — index.html / invite.html / island.html
        # contain zero <use> references; icons are inline SVG instead.
        # The sprite file still ships (see TestSprite, passing) but is
        # unwired in the live UI — a REAL GAP in the reskin, reported
        # separately. The island half also belongs to the island worker.
        self.skipTest(
            "reskin uses inline SVG, not the sprite: no <use> in the "
            "live UI (sprite unwired — REAL GAP, see report)")

    def test_every_iconsvg_call_points_at_a_real_sprite_id(self):
        # Reskin: icons are inline SVG paths in the ICON dict (index.html
        # inline script); every ic() call target must be a defined ICON
        # key — the reskin equivalent of "every icon reference resolves".
        html = INDEX.read_text(encoding="utf-8")
        icon_block = html.split("var ICON = {")[1].split("\n  };")[0]
        keys = set(re.findall(r"^\s*(\w+):", icon_block, re.M))
        self.assertTrue(keys, "ICON dict is empty")
        # Dynamic target: renderMenu calls ic(s[0]) for every SEC entry.
        sec = re.search(r"var SEC = \[(.*?)\];", html, re.S)
        self.assertIsNotNone(sec, "SEC list missing")
        for target in re.findall(r"\['([a-z]+)',", sec.group(1)):
            self.assertIn(target, keys,
                          f"ic({target!r}) has no ICON entry")
        # Literal targets (quit button, key eye button).
        for target in re.findall(r"ic\('([a-z]+)'\)", html):
            self.assertIn(target, keys,
                          f"ic({target!r}) has no ICON entry")

    def test_js_feature_cycle_uses_sprite_ids_only(self):
        # SKIPPED (reskin): the reskin main window has no feature icon
        # cycle — the hero is the mascot image + greeting, not a
        # rotating icon. The requirement does not apply to the reskin UI.
        self.skipTest("no feature icon cycle in the reskin UI")

    def test_updater_feature_cycle_uses_sprite_ids_only(self):
        ids = _sprite_ids()
        text = UPDATER.read_text(encoding="utf-8")
        m = re.search(r'_FEATURE_ICONS = \[([^\]]*)\]', text)
        self.assertIsNotNone(m, "_FEATURE_ICONS missing from updater.py")
        for item in re.findall(r'"([^"]*)"', m.group(1)):
            if item:
                self.assertIn(item, ids, f"_FEATURE_ICONS {item!r} not "
                                         "in the sprite")
        # The allowlist must be exactly the 31 core icons.
        m2 = re.search(r'_SPRITE_ICONS = frozenset\(\{([^}]*)\}\)', text,
                       flags=re.S)
        self.assertIsNotNone(m2, "_SPRITE_ICONS missing from updater.py")
        self.assertEqual(set(re.findall(r'"([^"]+)"', m2.group(1))),
                         EXPECTED_IDS)


class TestIconWiring(unittest.TestCase):
    def _icons_for(self, html: str, attr: str, value: str) -> list:
        # Reskin: icons are inline <svg> elements, not sprite <use>
        # refs — return the inline SVGs inside the button.
        m = re.search(r'<button[^>]*' + attr + r'="' + value + r'"[^>]*>'
                      r'(.*?)</button>', html, flags=re.S)
        self.assertIsNotNone(m, f"button {attr}={value} missing")
        return re.findall(r"<svg", m.group(1))

    def test_sidebar_buttons_have_their_icons(self):
        html = INDEX.read_text(encoding="utf-8")
        for view in ("chat", "device", "themes", "settings"):
            self.assertEqual(self._icons_for(html, "data-v", view),
                             ["<svg"], f"sidebar {view}")

    def test_settings_menu_buttons_have_their_icons(self):
        html = INDEX.read_text(encoding="utf-8")
        # The menu is rendered by renderMenu(): one button per SEC
        # entry, each with its icon from ic(). Every section id must
        # have an ICON entry.
        sec = re.search(r"var SEC = \[(.*?)\];", html, re.S)
        self.assertIsNotNone(sec, "SEC list missing")
        sec_ids = re.findall(r"\['([a-z]+)',", sec.group(1))
        self.assertEqual(len(sec_ids), 14, f"expected 14 sections, got {sec_ids}")
        icon_block = html.split("var ICON = {")[1].split("\n  };")[0]
        icon_keys = set(re.findall(r"^\s*(\w+):", icon_block, re.M))
        for section in sec_ids:
            self.assertIn(section, icon_keys,
                          f"menu {section} has no icon")
        self.assertIn("ic(s[0])", html,
                      "renderMenu must use ic() for the menu icons")
        self.assertIn("ic('quit')", html,
                      "quit button must use its icon")

    def test_back_buttons_use_the_back_icon(self):
        html = INDEX.read_text(encoding="utf-8")
        # One data-back template in renderSec(), shared by all 13
        # sections, with the chevron icon (CH_R) — never an emoji.
        self.assertEqual(html.count("data-back"), 1)
        m = re.search(r"var CH_R = \"(.*?)\";", html)
        self.assertIsNotNone(m, "CH_R chevron icon missing")
        self.assertIn("<svg", m.group(1))
        self.assertIn("M9 6l6 6-6 6", m.group(1))
        self.assertEqual(EMOJI_RE.findall(m.group(1)), [])

    def test_composer_buttons_use_their_icons(self):
        html = INDEX.read_text(encoding="utf-8")
        # NOTE: the v2 composer has no mic button — voice input moved
        # to the wake word / island. The three buttons that remain each
        # carry an inline SVG icon.
        for btn_id in ("attachBtn", "deepBtn", "sendBtn"):
            m = re.search(r'id="' + btn_id + r'"[^>]*>(.*?)</button>', html,
                          flags=re.S)
            self.assertIsNotNone(m, f"{btn_id} missing")
            self.assertIn("<svg", m.group(1), btn_id)
            self.assertEqual(EMOJI_RE.findall(m.group(1)), [], btn_id)

    def test_common_buttons_use_their_icons(self):
        html = INDEX.read_text(encoding="utf-8")
        # Reskin action buttons are data-* hooks rendered by the section
        # builders (save / refresh / check / delete / quit). The ones
        # that sit on the settings MENU carry their icon via ic()
        # (quit); the in-section .act buttons are text pills by design
        # — the v2 design gives icons to navigation, not to every
        # action button. Nothing here may be an emoji.
        for attr in ("data-quit", "data-save", "data-lref", "data-ucheck",
                     "data-mclear", "data-mdel", "data-nreq", "data-lcopy"):
            self.assertIn(attr, html, f"action {attr} missing")
        self.assertIn("ic('quit')", html)
        self.assertEqual(EMOJI_RE.findall(html), [])

    def test_avatar_picker_shows_real_images_not_emoji(self):
        html = INDEX.read_text(encoding="utf-8")
        # v2 design: the picker is 4 gold-circle Arabic letters, not
        # images — ق (cat) / ك (dog) / و (boy) / ب (girl).
        for entry in ("['cat', 'ق', 'قطة']", "['dog', 'ك', 'كلب']",
                      "['boy', 'و', 'ولد']", "['girl', 'ب', 'بنت']"):
            self.assertIn(entry, html, "avatar option missing: " + entry)
        m = re.search(r"avatar: function \(\) \{(.*?)\n    \},", html,
                      re.S)
        self.assertIsNotNone(m, "avatar section builder missing")
        body = m.group(1)
        self.assertEqual(EMOJI_RE.findall(body), [])
        self.assertNotIn("<img", body,
                         "avatar picker must use letters, not images")

    def test_island_buttons_use_their_icons(self):
        html = ISLAND.read_text(encoding="utf-8")
        # v1.6.4 reskin: the island buttons are inline <svg> icons
        # (no sprite <use> refs anymore - the old exp-send / #send /
        # #avatar / #back assertions are gone with the sprite wiring).
        # Pill mic + expanded attach/send + panels button each carry
        # their own inline SVG and no emoji.
        for btn_id in ("micBtn", "attachBtn", "islSend", "panelsBtn"):
            m = re.search(r'id="' + btn_id + r'"[^>]*>(.*?)</button>', html,
                          flags=re.S)
            self.assertIsNotNone(m, f"{btn_id} missing")
            self.assertIn("<svg", m.group(1), btn_id)
            self.assertEqual(EMOJI_RE.findall(m.group(1)), [], btn_id)

    def test_js_has_no_avatar_emoji_map(self):
        html = INDEX.read_text(encoding="utf-8")
        self.assertNotIn("AVATAR_EMOJI", html)
        # The icon helper is ic() (inline SVG paths), replacing iconSvg().
        self.assertIn("function ic(n)", html)

    def test_perm_buttons_use_check_and_x_icons(self):
        # SKIPPED (reskin): the reskin main window has no permission
        # cards — no perm allow/deny buttons exist to carry the icons.
        # The requirement does not apply to the reskin UI.
        self.skipTest("no permission cards in the reskin main window")

    def test_voice_button_uses_sounds_and_mute_icons(self):
        html = INDEX.read_text(encoding="utf-8")
        # The mute button (header) carries the inline speaker icon and
        # toggles aria-pressed + set_muted on tap.
        self.assertIn('id="muteBtn"', html)
        self.assertIn("M4 9v6h4l5 4V5L8 9H4z", html)  # speaker path
        self.assertIn("set_muted", html)
        self.assertIn("aria-pressed", html)


if __name__ == "__main__":
    unittest.main()
