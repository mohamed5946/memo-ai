"""Item 20 (v1.6.1): sidebar destinations open as FULL views, not a modal.

Before: clicking a sidebar item other than chat/device opened
#settings-modal -> .modal (width min(620px, 92vw) -- a small centred box).
Now: every sidebar destination is a .main-view inside #main-col, exactly
like view-chat / view-device. The settings modal markup is gone entirely
(no other modal use existed); the menu and all 13 sections live in the
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

SIDEBAR_VIEWS = ("chat", "device", "themes", "api-keys", "self-view",
                 "logs", "settings")
SECTIONS = ("general", "whatsapp", "memory", "system", "notifications",
            "voices", "avatar", "logs", "themes", "api-keys", "self-view",
            "backup", "updates")


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
            self.assertIn(f'data-view="{view}"', HTML, view)

    def test_chat_and_device_views_unchanged(self):
        self.assertIn('id="view-chat"', HTML)
        self.assertIn('id="view-device"', HTML)

    def test_settings_menu_is_a_main_view(self):
        self.assertIn(
            'id="settings-menu" class="main-view settings-view" hidden',
            HTML)

    def test_every_section_is_a_main_view(self):
        for section in SECTIONS:
            self.assertIn(
                f'id="section-{section}" class="main-view settings-view"',
                HTML, section)

    def test_views_live_inside_main_col(self):
        main_col = HTML.split('id="main-col"')[1].split(
            'id="invite-gate"')[0]
        for section in SECTIONS:
            self.assertIn(f'id="section-{section}"', main_col, section)
        self.assertIn('id="settings-menu"', main_col)
        self.assertIn('id="view-chat"', main_col)
        self.assertIn('id="view-device"', main_col)

    def test_all_views_start_hidden_except_chat(self):
        # Default view is chat; every other view must start hidden or it
        # would render below the chat in the main column.
        self.assertIn('id="view-device" hidden', HTML)
        for section in SECTIONS:
            self.assertIn(
                f'id="section-{section}" class="main-view settings-view" hidden',
                HTML, section)
        self.assertIn(
            'id="settings-menu" class="main-view settings-view" hidden',
            HTML)

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
        # 13 sections, one back button each (menu has none).
        self.assertEqual(HTML.count('class="back-btn btn-3d"'), 13)


if __name__ == "__main__":
    unittest.main()
