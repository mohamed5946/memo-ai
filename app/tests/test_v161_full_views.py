"""Item 20 (v1.6.1): sidebar destinations open as FULL views, not a modal.

Before: clicking a sidebar item other than chat/device opened
#settings-modal -> .modal (width min(620px, 92vw) -- a small centred box).
Now: every sidebar destination is a .main-view inside #main-col, exactly
like view-chat / view-device. The settings modal markup is gone entirely
(no other modal use existed); the menu and all 14 sections live in the
main area, the composer + quick-scan bar hide in every view except
chat/device, and the header gear opens the settings as a full view too.

The section headings keep their styling via new .main-view h2/h3 rules
(the old .modal h2/h3 rules no longer apply to them).
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "app" / "ui" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "ui" / "styles.css").read_text(encoding="utf-8")
# v1.6.4 reskin: the live main window is index.html (inline script) +
# assets/lona.css. app.js / styles.css are dead v1 files kept in the
# tree; tests ported to the reskin read the live files below.
LONA_CSS = (ROOT / "app" / "ui" / "assets" / "lona.css").read_text(
    encoding="utf-8")

# v1.6.4 reskin sidebar destinations (data-v): chat / device / tasks /
# themes (a popover panel, not a view) / settings (the settings menu).
# The v1 sidebar entries api-keys / self-view / logs are Settings
# sections now, not sidebar destinations.
SIDEBAR_VIEWS = ("chat", "device", "tasks", "themes", "settings")
# The 14 Settings sections (SEC in index.html), each with a Bsec
# builder and a menu button with an inline-SVG icon.
SECTIONS = ("general", "wa", "memory", "health", "notif", "sound",
            "avatar", "log", "update", "themes", "api", "tokens",
            "self", "backup")


class TestNoModalForNavigation(unittest.TestCase):
    def test_settings_modal_markup_is_gone(self):
        self.assertNotIn('id="settings-modal"', HTML)
        self.assertNotIn('id="settings-close"', HTML)
        self.assertNotIn("settings-modal", JS)
        self.assertNotIn("settings-close", JS)

    def test_show_view_never_opens_a_modal(self):
        block = JS.split("function showView")[1].split(
            "// ---------- STATS")[0]
        self.assertNotIn("modal", block)
        self.assertNotIn('classList.add("open")', block)
        self.assertNotIn('classList.remove("open")', block)

    def test_header_gear_opens_settings_view(self):
        self.assertIn('showView("settings")', JS)


class TestEverySidebarDestinationIsAFullView(unittest.TestCase):
    def test_sidebar_buttons_still_exist(self):
        for view in SIDEBAR_VIEWS:
            self.assertIn(f'data-v="{view}"', HTML, view)

    def test_chat_and_device_views_unchanged(self):
        self.assertIn('class="view" data-v="chat"', HTML)
        self.assertIn('class="view page" data-v="device"', HTML)

    def test_settings_menu_is_a_main_view(self):
        # The settings menu is the data-v="settings" view; renderMenu()
        # builds its 13 section buttons into #setRoot.
        self.assertIn('class="view page" data-v="settings"', HTML)
        self.assertIn('id="setRoot"', HTML)
        self.assertIn("function renderMenu()", HTML)

    def test_every_section_is_a_main_view(self):
        # Every section is a SEC entry with a Bsec builder, rendered
        # into the settings view by renderSec() — no modal, no separate
        # static section markup anymore.
        for section in SECTIONS:
            self.assertIn(f"['{section}',", HTML, section)
            self.assertIn(f"{section}: function ()", HTML, section)
        self.assertIn("function renderSec(id)", HTML)

    def test_views_live_inside_main_col(self):
        # The views live inside .main (the reskin's main column):
        # chat / device / tasks / settings + the settings root.
        main = HTML.split('<div class="main">')[1]
        for view in ("chat", "device", "tasks", "settings"):
            self.assertIn(f'data-v="{view}"', main, view)
        self.assertIn('id="setRoot"', main)
        self.assertIn('id="thGrid"', main)

    def test_all_views_start_hidden_except_chat(self):
        # Default view is chat; every other view must start hidden or it
        # would render below the chat in the main column.
        self.assertIn('<div class="view" data-v="chat">', HTML)
        self.assertIn('data-v="device" hidden', HTML)
        self.assertIn('data-v="tasks" hidden', HTML)
        self.assertIn('data-v="settings" hidden', HTML)

    def test_show_view_hides_settings_views_for_chat_device(self):
        block = JS.split("function showView")[1].split(
            "// ---------- STATS")[0]
        # Both chat and device branches hide every settings view.
        self.assertEqual(
            block.count('document.querySelectorAll(".settings-view")'),
            2, "chat + device branches must hide the settings views")

    def test_section_navigation_hides_chat_device_and_composer(self):
        sec = JS.split("function showSettingsSection")[1].split(
            "function showSettingsMenu")[0]
        self.assertIn("_hideChatDeviceAndComposer();", sec)
        menu = JS.split("function showSettingsMenu")[1].split(
            "function init()")[0]
        self.assertIn("_hideChatDeviceAndComposer();", menu)
        helper = JS.split("function _hideChatDeviceAndComposer()")[1].split(
            "function showSettingsSection")[0]
        for needle in ('$("view-chat")', '$("view-device")',
                       'document.querySelector(".composer")',
                       '$("quick-scan")', 'style.display = "none"'):
            self.assertIn(needle, helper, needle)

    def test_composer_shown_only_for_chat_and_device(self):
        block = JS.split("function showView")[1].split(
            "// ---------- STATS")[0]
        self.assertEqual(block.count('composer.style.display = "";'), 2,
                         "only the chat + device branches restore it")


class TestFullViewStyling(unittest.TestCase):
    def test_main_view_headings_styled(self):
        # The sections used to get their h2/h3 styling from .modal; the
        # full views need the same treatment from .main-view rules.
        self.assertRegex(CSS, r"\.main-view h2\s*\{[^}]*font-size:\s*20px")
        self.assertRegex(CSS, r"\.main-view h3\s*\{[^}]*color:\s*var\(--gold\)")

    def test_main_view_hidden_rule_still_present(self):
        self.assertIn(".main-view[hidden] { display: none; }", CSS)

    def test_modal_width_rule_no_longer_governs_navigation(self):
        # The .modal rule may stay for other uses, but no sidebar
        # destination may live inside it -- covered by the markup tests
        # above; here just pin that the rule was not widened as a fake
        # "full screen" fix.
        m = re.search(r"\.modal\s*\{([^}]*)\}", CSS)
        self.assertIsNotNone(m, ".modal rule unexpectedly removed")
        self.assertIn("min(620px, 92vw)", m.group(1),
                      "the small-modal rule must stay small, not be "
                      "repurposed as the full-view fix")


class TestSectionWiringUnchanged(unittest.TestCase):
    def test_menu_and_back_buttons_still_wired(self):
        for needle in ('document.querySelectorAll(".menu-btn")',
                       'document.querySelectorAll(".back-btn")',
                       "function showSettingsSection",
                       "function showSettingsMenu"):
            self.assertIn(needle, JS, needle)

    def test_section_refresh_hooks_still_run(self):
        sec = JS.split("function showSettingsSection")[1].split(
            "function showSettingsMenu")[0]
        for needle in ('if (name === "logs") refreshLogs();',
                       'if (name === "whatsapp") refreshWhatsApp();',
                       'if (name === "themes"',
                       'if (name === "api-keys"',
                       'if (name === "self-view"',
                       'if (name === "backup"'):
            self.assertIn(needle, sec, needle)

    def test_back_button_count_unchanged(self):
        # 14 sections (rc.2 adds "tokens"), one back button each — in
        # the reskin they share a single data-back template rendered
        # by renderSec() for whichever section is open (so the static
        # markup has exactly one).
        self.assertEqual(HTML.count("data-back"), 1)
        m = re.search(r"var SEC = \[(.*?)\];", HTML, re.S)
        self.assertIsNotNone(m, "SEC list missing from index.html")
        sec = re.findall(r"\['([a-z]+)',", m.group(1))
        self.assertEqual(len(sec), 14, f"expected 14 sections, got {sec}")
        self.assertEqual(sorted(sec), sorted(SECTIONS))


if __name__ == "__main__":
    unittest.main()
