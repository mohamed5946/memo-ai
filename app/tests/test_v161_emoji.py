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
        ids = _sprite_ids()
        use_re = re.compile(
            r'<use href="assets/lona-icons-sprite\.svg#([^"]+)"/>')
        for path in (INDEX, ISLAND):
            text = path.read_text(encoding="utf-8")
            found = use_re.findall(text)
            self.assertTrue(found, f"no <use> in {path.name}")
            for target in found:
                self.assertIn(target, ids, f"{path.name}: #{target} "
                                           "not in the sprite")
        # pet.html has no icon (plain-text bubble) — the sweep above
        # already proves it has no emoji either.
        self.assertEqual(_sweep_file(PET), [])

    def test_every_iconsvg_call_points_at_a_real_sprite_id(self):
        ids = _sprite_ids()
        js = APP_JS.read_text(encoding="utf-8")
        # iconSvg("check"), iconSvg("updates", 32), ...
        for target in re.findall(r'iconSvg\("([a-z0-9-]+)"', js):
            self.assertIn(target, ids, f"app.js iconSvg({target!r}) "
                                       "not in the sprite")
        # The ternary in updateVoiceBtn passes literals too.
        for target in ("sounds", "mute"):
            self.assertIn(target, ids)
            self.assertIn(f'"{target}"', js)

    def test_js_feature_cycle_uses_sprite_ids_only(self):
        ids = _sprite_ids()
        js = APP_JS.read_text(encoding="utf-8")
        m = re.search(r'const icons = \[([^\]]*)\];', js)
        self.assertIsNotNone(m, "feature icon cycle missing from app.js")
        for item in re.findall(r'"([^"]*)"', m.group(1)):
            if item:
                self.assertIn(item, ids, f"cycle icon {item!r} not in "
                                         "the sprite")

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
        m = re.search(r'<button[^>]*' + attr + r'="' + value + r'"[^>]*>'
                      r'(.*?)</button>', html, flags=re.S)
        self.assertIsNotNone(m, f"button {attr}={value} missing")
        return re.findall(r'#([a-z0-9-]+)"', m.group(1))

    def test_sidebar_buttons_have_their_icons(self):
        html = INDEX.read_text(encoding="utf-8")
        want = {"chat": "chat", "device": "device", "themes": "themes",
                "api-keys": "api-key", "self-view": "lona-now",
                "logs": "logs", "settings": "settings"}
        for view, icon in want.items():
            self.assertEqual(self._icons_for(html, "data-view", view),
                             [icon], f"sidebar {view}")

    def test_settings_menu_buttons_have_their_icons(self):
        html = INDEX.read_text(encoding="utf-8")
        want = {"general": "general", "whatsapp": "whatsapp",
                "memory": "memory", "system": "device-health",
                "notifications": "notifications", "voices": "sounds",
                "avatar": "avatar", "logs": "logs", "updates": "updates",
                "themes": "themes", "api-keys": "api-key",
                "self-view": "lona-now", "backup": "backup"}
        for section, icon in want.items():
            self.assertEqual(self._icons_for(html, "data-section", section),
                             [icon], f"menu {section}")

    def test_back_buttons_use_the_back_icon(self):
        html = INDEX.read_text(encoding="utf-8")
        backs = re.findall(r'class="back-btn btn-3d">(.*?)</button>', html,
                           flags=re.S)
        self.assertEqual(len(backs), 13, "expected 13 back buttons")
        for body in backs:
            self.assertIn('#back"', body)
            self.assertIn('data-i18n="back"', body)

    def test_composer_buttons_use_their_icons(self):
        html = INDEX.read_text(encoding="utf-8")
        for btn_id, icon in (("deepthink-btn", "deep-think"),
                             ("send-btn", "send"), ("mic-btn", "mic")):
            m = re.search(r'id="' + btn_id + r'"[^>]*>(.*?)</button>', html,
                          flags=re.S)
            self.assertIsNotNone(m, f"{btn_id} missing")
            self.assertIn(f"#{icon}\"", m.group(1), btn_id)

    def test_common_buttons_use_their_icons(self):
        html = INDEX.read_text(encoding="utf-8")
        want = {"quit-btn": "power", "portfolio-btn": "globe",
                "github-btn": "github", "save-general": "save",
                "save-whatsapp": "save", "save-system": "save",
                "save-notifications": "save", "wa-relink-btn": "refresh",
                "memory-clear-btn": "delete",
                "notif-access-btn": "notifications",
                "logs-refresh": "refresh",
                "check-updates-btn": "refresh",
                "install-downloaded-btn": "updates"}
        for btn_id, icon in want.items():
            m = re.search(r'id="' + btn_id + r'"[^>]*>(.*?)</button>', html,
                          flags=re.S)
            self.assertIsNotNone(m, f"{btn_id} missing")
            self.assertIn(f"#{icon}\"", m.group(1), btn_id)

    def test_avatar_picker_shows_real_images_not_emoji(self):
        html = INDEX.read_text(encoding="utf-8")
        m = re.search(r'id="avatar-pick">(.*?)</div>', html, flags=re.S)
        self.assertIsNotNone(m, "avatar picker missing")
        body = m.group(1)
        self.assertEqual(
            sorted(re.findall(r'src="(assets/avatar-[a-z]+\.png)"', body)),
            ["assets/avatar-boy.png", "assets/avatar-cat.png",
             "assets/avatar-dog.png", "assets/avatar-girl.png"])
        self.assertEqual(EMOJI_RE.findall(body), [])

    def test_island_buttons_use_their_icons(self):
        html = ISLAND.read_text(encoding="utf-8")
        # v1.6.2 island redesign: mic + settings are removed from the
        # pill; only the expanded composer's send button remains.
        for btn_id, icon in (("exp-send", "send"),):
            m = re.search(r'id="' + btn_id + r'"[^>]*>(.*?)</button>', html,
                          flags=re.S)
            self.assertIsNotNone(m, f"{btn_id} missing")
            self.assertIn(f"#{icon}\"", m.group(1), btn_id)
        self.assertIn('#avatar"', html)
        self.assertIn('#back"', html)  # name-row chevron (rotated)

    def test_js_has_no_avatar_emoji_map(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertNotIn("AVATAR_EMOJI", js)
        self.assertIn("function iconSvg(id, size)", js)

    def test_perm_buttons_use_check_and_x_icons(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn('iconSvg("check") + " " + t("perm_allow")', js)
        self.assertIn('iconSvg("x") + " " + t("perm_deny")', js)

    def test_voice_button_uses_sounds_and_mute_icons(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn('iconSvg(voiceOutput ? "sounds" : "mute")', js)


if __name__ == "__main__":
    unittest.main()
