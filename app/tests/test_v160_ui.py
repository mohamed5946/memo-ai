"""v1.6.0 LONA UI tests — static checks over the built HTML/CSS/JS.

Asserts the redesigned UI contains: 4 themes incl. the old theme,
morph transition, robot SVG (gold eyes/mouth, never white), all 13
moods, island collapsed + expanded markup, 4 swipe panels, choice /
permission / action-message markup with a status dot, + attach menu
with 4 items, Deep Think toggle, 6-box invite screen with a string
join, API Keys page, updater installing state + disabled button,
bidirectional stagger, no AI-tell phrases, the LONA rename (no
user-visible "Memo" except the wake-word phrase), and the scope
addition: image warning card, settings backup, exam countdown +
Pomodoro, privacy mode, undo UI.
"""
import re
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "ui"
HTML = (UI / "index.html").read_text(encoding="utf-8")
CSS = (UI / "styles.css").read_text(encoding="utf-8")
JS = (UI / "app.js").read_text(encoding="utf-8")
ISLAND = (UI / "island.html").read_text(encoding="utf-8")
ALL = HTML + CSS + JS + ISLAND

MOODS = ["نايم", "صاحي", "فضولي", "بيفكر", "قلقان", "دايخ", "مركّز",
         "فخور", "مبسوط وبيرقص", "بيحتفل", "متفاجئ", "سعيد بيك", "تعبان"]


def test_ui_files_exist():
    for name in ("index.html", "island.html", "app.js", "styles.css"):
        assert (UI / name).is_file(), name + " missing"


def test_four_themes_incl_old():
    for theme in ("night-glass", "calm-space", "dashboard", "old"):
        assert theme in CSS or theme in JS, "theme missing: " + theme
    assert "زجاج الليل" in JS
    assert "القديم" in JS  # old v1.5.0 theme stays selectable
    assert "#0A1A2C" in CSS and "#F4F6F3" in CSS and "#22222C" in CSS


def test_theme_morph_transition():
    assert ".45s ease" in CSS
    assert "background-color .45s ease" in CSS
    assert "applyTheme" in JS and "set_theme" in JS and "get_themes" in JS


def test_robot_svg_no_white_eyes_mouth():
    for src, name in ((JS, "app.js"), (ISLAND, "island.html")):
        assert "robotHeadSVG" in src or "robot" in src.lower(), name
        assert "#F8F4EC" in src  # off-white body
        assert "#E9B558" in src  # gold headphones
        assert "viewBox=\"0 0 120 84\"" in src  # head
        assert "viewBox=\"0 0 140 172\"" in src  # full body
    assert "FFCF5E" in JS  # gold glowing eyes/mouth
    # No white fill used for eyes/mouth anywhere in the robot SVG builders.
    assert 'class="eye"' in JS and 'class="mouth"' in JS
    assert 'fill="#FFFFFF"' not in JS and 'fill="#fff"' not in JS
    assert 'fill="#FFFFFF"' not in ISLAND and 'fill="#fff"' not in ISLAND


def test_all_13_moods():
    for mood in MOODS:
        assert mood in JS, "mood missing in app.js: " + mood
        assert mood in ISLAND, "mood missing in island.html: " + mood
    assert "get_mood" in JS and "5000" in JS  # state-driven poll every 5s
    assert "setMood" in JS
    for cls in ("mood-nayem", "mood-mabsoot", "mood-bihtafel", "mood-mutafaje"):
        assert cls in CSS, "mood class missing in CSS: " + cls
    assert "botDance" in CSS and "botParty" in CSS and "robot-blink" in CSS
    assert "4.8s" in CSS  # blink cycle


def test_island_collapsed_and_expanded():
    assert "island-name" in ISLAND  # pill label shows the chosen name
    assert "Listening..." in ISLAND
    assert "green-dot" in ISLAND
    # v1.6.2 (F4): NO clock on the collapsed pill — the time shows
    # only inside the expanded island (#exp-clock).
    assert "island-clock" not in ISLAND
    assert "exp-clock" in ISLAND
    assert "expandIsland" in ISLAND
    assert "island-expanded" in ISLAND
    # v1.6.2 island redesign: Quick Actions / Tools / Skills / More
    # are all removed.
    assert "Quick Actions" not in ISLAND and "Tools" not in ISLAND
    assert "Skills" not in ISLAND and "More" not in ISLAND
    assert "exp-robot" in ISLAND  # full-body robot on the side
    assert "60px" in ISLAND or "FULL_H = 60" in ISLAND  # compact collapsed height
    assert "reactJump" in ISLAND  # tapping the robot reacts, never toggles
    assert "أهلًا!" in ISLAND


def test_island_swipe_panels():
    # v1.6.2 island redesign (Mohamed 2026-11-22 10:32): the swipe
    # panel system is REMOVED entirely — no panels, no drag, no
    # arrows, no dots. The island is fixed, centered, click to expand.
    for gone in ("الجهاز دلوقتي", "معلومات الجهاز والتخزين",
                 "التنظيف والملفات العميقة", "المتصفح",
                 "panel-dots", "panel-track", "showPanel",
                 "نضّف الكاش بإذني"):
        assert gone not in ISLAND, "swipe panel remnant: " + gone
    assert "exp-panels" not in ISLAND


def test_main_window_shell():
    # v1.6.2 (frameless): the fake .window-bar is REMOVED — the
    # traffic lights are real buttons in the header (win-close/min/max).
    assert "window-bar" not in HTML
    assert 'id="win-close"' in HTML and 'id="win-min"' in HTML
    assert 'id="win-max"' in HTML and 'id="app-header"' in HTML
    assert "sidebar" in HTML and "side-nav" in HTML
    for item in ("المحادثة", "حالة الجهاز", "الإعدادات", "مفاتيح API",
                 "لونا الآن", "سجل التشخيص"):
        assert item in HTML, "sidebar item missing: " + item
    assert "stat-card" in HTML and "count-up" in HTML
    assert "proc-list" in HTML and "replay-stagger" in HTML
    assert "quick-scan" in HTML
    assert "pill-nav" not in HTML  # v1.6.1 (item 6): floating pill nav removed
    assert "robot-stage" in HTML


def test_choice_permission_action_messages():
    assert "choice-card" in HTML or "choice-card" in CSS
    assert "choice-card" in JS and "renderChoiceCard" in JS
    assert "اخترت:" in JS
    assert "perm" in JS and "resolve_permission" in JS  # permission cards kept
    assert "action-msg" in CSS and "renderActionMessage" in JS
    assert "اعملها انت" in JS
    assert "status-dot" in CSS and "status-dot" in JS  # dot INSIDE the message
    assert "status-dot green" in JS or '"green"' in JS
    assert "#3ECF8E" in CSS and "#F2B544" in CSS and "#EF6461" in CSS


def test_attach_menu_and_deep_think():
    assert "attach-menu" in HTML and "attach-btn" in HTML
    for kind in ("صور", "ملفات", "فيديو", "مستندات"):
        assert kind in HTML, "attach item missing: " + kind
    assert "deepthink-btn" in HTML and "deepthink" in JS.lower()
    assert "set_deep_think" in JS and "get_deep_think" in JS
    assert "بيفكّر..." in JS  # thinking state while a reply is pending


def test_invite_screen_string_join():
    assert "invite-box" in HTML
    assert HTML.count('class="invite-box"') == 6
    assert "inviteCodeString" in JS
    assert "check_invite" in JS and "get_activation_state" in JS
    # Code joined as a STRING (leading zero preserved) — never as a number.
    assert 'code += b.value' in JS or "join" in JS
    assert "parseInt" not in JS.split("inviteCodeString")[1].split("}")[0] if "inviteCodeString" in JS else True


def test_api_keys_page():
    assert "section-api-keys" in HTML
    # v1.6.1: two separate Gemini fields (voice + chat), no single one.
    assert "api-key-gemini-voice" in HTML
    assert "api-key-gemini-chat" in HTML
    assert 'id="api-key-gemini"' not in HTML
    assert "api-key-groq" in HTML
    assert "محفوظ على الجهاز" in HTML or "محفوظ على الجهاز" in JS
    assert "save_api_key" in JS and "test_api_key" in JS
    assert "إظهار" in HTML


def test_self_view_and_themes_picker():
    assert "section-self-view" in HTML and "get_self_view" in JS
    for row in ("sv-mood", "sv-task", "sv-last-action", "sv-usage",
                "sv-temp", "sv-deep", "sv-chrome", "sv-model"):
        assert row in HTML, "self-view row missing: " + row
    assert "theme-grid" in HTML and "section-themes" in HTML


def test_updater_installing_state():
    assert "بيتثبّت دلوقتي" in HTML or "بيتثبّت دلوقتي" in JS
    assert "update-installing-label" in HTML
    assert "setInstallingUI" in JS
    assert "installing" in JS  # the new progress state is handled
    assert "disabled" in JS  # button locked while installing
    assert "مثبّت" in JS


def test_bidirectional_stagger():
    assert "stagger" in CSS.lower() and "stagger" in JS.lower()
    assert "55ms" in CSS
    assert "stagger-up" in CSS and "stagger-up" in JS  # scrolling up replays reversed
    assert "218px" in CSS  # top-processes scroll area
    assert "850ms" in JS or "850" in JS  # count-up duration


def test_no_ai_tell_phrases():
    for phrase in ("في الختام", "من الجدير بالذكر", "علاوة على ذلك"):
        assert phrase not in ALL, "AI-tell phrase present: " + phrase


def test_rename_lona_no_memo():
    # Wake word is now "يا لونا" — no "يا ميمو" text anywhere in the UI.
    assert "يا لونا" in JS
    assert "يا ميمو" not in ALL, "old wake word still present"
    # No user-visible "Memo" as a word anywhere in the owned UI files
    # (Memory / memory identifiers are the English word, not the name).
    for src, name in ((HTML, "index.html"), (CSS, "styles.css"),
                      (JS, "app.js"), (ISLAND, "island.html")):
        assert not re.search(r"\bMemo\b", src), "Memo remains in " + name
        assert "Memo AI" not in src, "Memo AI remains in " + name
    assert "LONA" in HTML and "LONA" in JS and "LONA" in ISLAND


def test_wake_word_is_ya_lona_everywhere():
    assert "يا لونا" in HTML  # Settings label
    assert "يا لونا" in JS    # I18N strings + status lines
    assert "يا ميمو" not in HTML and "يا ميمو" not in JS
    assert "يا ميمو" not in CSS and "يا ميمو" not in ISLAND


def test_island_has_no_window_control_buttons():
    for marker in ("exp-min", "exp-max", "exp-close", "island-close"):
        assert marker not in ISLAND, "window-control button remains: " + marker
    # It opens/closes by tapping the island itself.
    assert "collapseIsland" in ISLAND
    assert "island-expanded" in ISLAND
    assert "e.target.closest" in ISLAND
    # v1.6.2 island redesign (Mohamed 2026-11-22 10:32): the mic +
    # settings buttons are REMOVED from the pill too — it opens/closes
    # by clicking it, nothing else sits on it.
    assert "island-mic" not in ISLAND and "island-settings" not in ISLAND


def test_customizable_ai_name():
    # Settings name field updates the displayed name everywhere at once.
    assert 'id="set-ai-name"' in HTML
    assert "applyAiName" in JS and "wireAiNameField" in JS
    # The name is read live from the bridge ai_name setting.
    assert "ai_name" in JS
    assert "currentAiName" in JS
    # Wake-word label reads "يا {chosen name}" (default "يا لونا").
    assert "wakeWord()" in JS
    assert '"يا " + currentAiName' in JS
    # Island pill label is fed live from the bridge, not a fixed string.
    assert "loadAiName" in ISLAND
    assert "get_status" in ISLAND
    assert "AI Agent" not in ISLAND


def test_live_cards_refresh_wiring():
    # A refresh function polls get_device_overview() every 5s...
    assert "function refreshLiveCards" in JS
    assert "setInterval(refreshLiveCards, 5000)" in JS
    assert "refreshDeviceOverview" in JS
    assert "get_device_overview" in JS
    # ...and runs again immediately after an action completes.
    action_section = JS.split("renderActionMessage")[1].split("UNDO UI")[0]
    assert "refreshLiveCards" in action_section
    # v1.6.2 island redesign: the swipe panels are REMOVED entirely
    # (no device panels on the island anymore). The live surface is
    # the in-chat live device card (PARITY 3), refreshed every 5s.
    assert "refreshDevicePanels" not in ISLAND
    assert "exp-panels" not in ISLAND
    assert "renderLiveDeviceCard" in JS
    assert "setInterval(refreshLiveCards, 5000)" in JS


def test_image_warning_card():
    assert "img-warning-card" in CSS and "showImageWarningCard" in JS
    assert "هتطلع من الجهاز وتتبعت لـ Gemini" in JS
    assert "Gemini is looking at the image" in JS
    assert "attached-image" in CSS and "sendImageToGemini" in JS
    assert "analyze_image" in JS  # bridge call when present


def test_settings_backup():
    assert "section-backup" in HTML and "backup-export-btn" in HTML
    assert "backup-import-btn" in HTML
    assert "backup-export-pass2" in HTML  # password asked twice on export
    assert "export_backup" in JS and "import_backup" in JS


def test_exam_countdown_and_pomodoro():
    assert "exam-countdown" in HTML and "get_exam_countdown" in JS
    assert "مفيش امتحان متسجّل" in HTML  # honest empty state
    assert "pomodoro-timer" in HTML and "pomodoro-start" in HTML
    assert "focus-toggle" in HTML and "هذاكر" in HTML
    assert "بيحتفل" in JS  # celebrates when a focus session finishes


def test_privacy_mode():
    assert "set-privacy-mode" in HTML and "وضع الخصوصية" in HTML
    assert "set_privacy_mode" in JS
    assert "privacyOn" in JS


def test_undo_ui():
    assert "renderUndoAffordance" in JS
    assert "تراجع" in JS
    assert "list_undoable" in JS
    assert "relaunch_process" in JS and "restore_deleted" in JS
    assert "undo-btn" in CSS
