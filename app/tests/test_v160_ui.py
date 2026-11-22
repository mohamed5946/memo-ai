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

# v1.6.4 reskin: the LIVE main window is index.html (inline script) +
# assets/lona.css + assets/bridge.js, and the invite gate is
# invite.html. app.js / styles.css are dead v1 files kept in the tree;
# tests ported to the reskin read the live files below, never JS/CSS.
LONA_CSS = (UI / "assets" / "lona.css").read_text(encoding="utf-8")
BRIDGE = (UI / "assets" / "bridge.js").read_text(encoding="utf-8")
INVITE = (UI / "invite.html").read_text(encoding="utf-8")
# The mood engine lives in monitor.py; the bridge + island constants
# in main.py. Both are live reskin sources (app.js is dead v1).
MONITOR = (UI.parent / "monitor.py").read_text(encoding="utf-8")
MAIN = (UI.parent / "main.py").read_text(encoding="utf-8")

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
    # v1.6.4 reskin: the vector robot SVG (robotHeadSVG, gold #FFCF5E
    # eyes/mouth) is GONE - the robot is the mascot image now, shown
    # in the main window hero and on the island (pill bubble + expanded
    # stand). The requirement survives in its visual form: the mascot
    # is a white robot with GOLD glowing eyes (verified by eye-pixel
    # sampling below), never white eyes/mouth, and no white-filled
    # robot SVG remains in any live file.
    assert 'src="assets/mascot.webp"' in HTML
    assert 'src="assets/mascot.webp"' in ISLAND
    from PIL import Image
    img = Image.open(str(UI / "assets" / "mascot.webp"))
    assert img.size == (1600, 1600)
    rgb = img.convert("RGB")
    # Eye centers (fraction of the 1600px image, measured from the
    # rendered mascot): left ~(0.40, 0.55), right ~(0.60, 0.55). The
    # eyes are glowing gold: strong red, mid green, low blue - never
    # near-white (all channels > 240) and never white-filled.
    for cx, cy in ((0.40, 0.55), (0.60, 0.55)):
        px = int(1600 * cx)
        py = int(1600 * cy)
        rs, gs, bs, n = 0, 0, 0, 0
        for dx in range(-15, 16, 5):
            for dy in range(-15, 16, 5):
                r, g, b = rgb.getpixel((px + dx, py + dy))
                rs += r
                gs += g
                bs += b
                n += 1
        r, g, b = rs // n, gs // n, bs // n
        # Gold/amber hue: red clearly dominates, blue is low - never
        # near-white (all channels > 240) and never white-filled.
        assert r > 100 and r > g > b and (r - b) > 80, \
            f"eye at ({cx}, {cy}) is not gold: rgb({r},{g},{b})"
        assert not (r > 240 and g > 240 and b > 240), \
            "eye is white, not gold"
    # No white-filled robot SVG in the live files (the SVG builder
    # survives only in dead app.js / styles.css, never shown).
    for src, name in ((HTML, "index.html"), (ISLAND, "island.html"),
                      (BRIDGE, "bridge.js"), (LONA_CSS, "lona.css")):
        assert "robotHeadSVG" not in src, "SVG robot remains in " + name
        assert 'fill="#FFFFFF"' not in src and 'fill="#fff"' not in src


# The reskin mood engine's vocabulary: device_state_to_mood + the
# message-tone mapper in monitor.py, plus "بيفكر" while a reply is
# pending (main.py get_mood). The other four v1.6.0 moods (فضولي،
# فخور، بيحتفل، متفاجئ) were faces of the deleted SVG robot - they
# never existed in the engine (not even in the v1.6.3 base) and have
# no visual expression on a static mascot image, so the live
# vocabulary is these nine, surfaced as text in both windows.
LIVE_MOODS = ["نايم", "صاحي", "بيفكر", "قلقان", "دايخ", "مركّز",
              "مبسوط وبيرقص", "سعيد بيك", "تعبان"]
SVG_ONLY_MOODS = ["فضولي", "فخور", "بيحتفل", "متفاجئ"]


def test_all_13_moods():
    for mood in LIVE_MOODS:
        assert mood in MONITOR or mood in MAIN, \
            "mood missing from the engine: " + mood
    for mood in SVG_ONLY_MOODS:
        assert mood not in MONITOR and mood not in MAIN, \
            "SVG-only mood leaked into the engine: " + mood
    # The mood reaches both windows via get_mood, polled every 5s.
    assert "def get_mood" in MAIN
    assert "get_mood" in HTML and "setInterval(pollMood, 5000)" in HTML
    assert "get_mood" in ISLAND
    assert "setInterval(pollMood, 5000)" in ISLAND
    # The island restores the mood at once when a reply lands.
    assert "window.setMood" in ISLAND


def test_island_collapsed_and_expanded():
    # v1.6.4-rc.2 (Mohamed 2026-11-23 00:20, island redesign spec):
    # the island keeps the PILL shape always. Collapsed = a NARROW
    # pill (220x60) inside the fixed 520x300 canvas - mascot FACE
    # circle (.bubble.mascot-face, mascot.webp cropped to the face)
    # + live ai_name title + green .dot, and NO mic icon, NO
    # "Listening" text. The waveform bars animate ONLY while music
    # is playing (.island.playing gates .wave in lona.css).
    # Expanded = a larger rounded pill (520x300, border-radius 44px),
    # NOT the old 720x400 rectangle. Expanding toggles the .open
    # class (setOpen -> island_expand/island_collapse); the window
    # never resizes. No clock widget anywhere in the island (time
    # shows only inside the expanded timer/music panels, via fmtClock).
    assert 'id="mini"' in ISLAND
    assert 'class="i-mini"' in ISLAND
    assert 'class="bubble mascot-face"' in ISLAND
    assert 'src="assets/mascot.webp"' in ISLAND
    # The pill title is the LIVE ai_name (id="pillName", default لونا,
    # set from get_settings) — never a static label.
    assert 'id="pillName"' in ISLAND
    assert "ai_name" in ISLAND
    # rc.2 removals: no mic icon and no "Listening" text on the pill.
    assert "micBtn" not in ISLAND
    assert "Listening" not in ISLAND
    assert '<span class="dot">' in ISLAND
    assert ".dot{" in LONA_CSS
    assert 'class="wave"' in ISLAND
    # Waveform only when playing: hidden by default, shown only
    # under .island.playing.
    assert ".wave{display:none" in LONA_CSS
    assert ".island.playing .wave" in LONA_CSS
    assert "island-clock" not in ISLAND
    assert "exp-clock" not in ISLAND
    assert "setOpen" in ISLAND
    assert "classList.toggle('open'" in ISLAND
    assert "island_expand" in ISLAND and "island_collapse" in ISLAND
    assert "body.fixed-canvas .island{width:220px;height:60px}" in ISLAND
    assert ".island.open{width:520px;height:300px;border-radius:44px}" in ISLAND
    # v1.6.2 island redesign: Quick Actions / Tools / Skills / More
    # are all removed.
    assert "Quick Actions" not in ISLAND and "Tools" not in ISLAND
    assert "Skills" not in ISLAND and "More" not in ISLAND
    assert 'class="stand"' in ISLAND  # full mascot on the expanded side
    assert 'class="mascot-face"' in ISLAND  # face circle, pill + stand
    assert 'id="greet"' in ISLAND
    assert "أهلًا، أقدر أساعدك في إيه؟" in ISLAND
    # Tapping the island toggles it open/closed (the old reactJump
    # robot-tap animation is gone with the SVG robot); tapping the
    # FACE greets instead (greetFromFace, rc.2 interactive face).
    assert "isl.addEventListener('click'" in ISLAND
    assert "reactJump" not in ISLAND
    assert "greetFromFace" in ISLAND


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
    # v1.6.4-rc.2 (Mohamed 2026-11-23 00:24, title-bar requirement):
    # the main window has a STANDARD Windows title bar (native
    # minimize / maximize / close, like Task Manager). The custom frameless header with the three
    # macOS-style colored dots is GONE: no tlClose/tlMin/tlMax in the
    # UI at all. The in-app header (.bar: brand/model/clock/mute)
    # remains, as does the sidebar (.side/#nav with data-v
    # destinations) and the hero with the mascot face circle.
    # (The native frame itself is covered by
    # test_v162_backend.TestMainWindow.test_main_window_native_frame_resizable.)
    assert "window-bar" not in HTML
    assert 'id="tlClose"' not in HTML
    assert 'id="tlMin"' not in HTML
    assert 'id="tlMax"' not in HTML
    assert "native Windows title bar" in HTML
    assert 'class="bar"' in HTML
    assert 'class="side' in HTML and 'id="nav"' in HTML
    for item in ("الشات", "الجهاز", "المهام", "الثيمات", "الإعدادات"):
        assert item in HTML, "sidebar item missing: " + item
    # Stat cards (blank "--" until the first real sample) + process list.
    assert 'class="stat' in HTML
    for stat_id in ("vRam", "vCpu", "vBat", "vTemp"):
        assert f'id="{stat_id}"' in HTML, "stat missing: " + stat_id
    assert 'id="procList"' in HTML and "replayStagger" in HTML
    # The v1 quick-scan bar is gone by design — the hero action buttons
    # (e.g. نشرة الصباح) are the reskin's quick actions.
    assert 'id="briefBtn"' in HTML
    assert "quick-scan" not in HTML
    assert "pill-nav" not in HTML  # v1.6.1 (item 6): floating pill nav removed
    assert 'id="hero"' in HTML and "assets/mascot.webp" in HTML


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
    # v1.6.4 reskin: the composer keeps the attach + Deep Think buttons
    # (attachBtn / deepBtn in index.html); Deep Think is a toggle whose
    # state is passed to chat() as { deep_think: ... }, and the thinking
    # state while a reply is pending is the 3-dot typing indicator.
    assert 'id="attachBtn"' in HTML
    # REAL GAP (reskin): the attach MENU is gone — the button only shows
    # an "إرفاق الملفات لسه مش متاح في النسخة دي" message, so the four
    # attach kinds have no menu in the reskin UI.
    for kind in ("صور", "ملفات", "فيديو", "مستندات"):
        assert 'data-kind="%s"' % kind in HTML, \
            "attach item missing: " + kind
    assert 'id="deepBtn"' in HTML
    assert 'aria-label="Deep Think"' in HTML
    assert "deepThink" in HTML
    assert "{ deep_think: deepThink }" in HTML
    assert 'class="typing"' in HTML  # thinking state while reply pending


def test_invite_screen_string_join():
    # v1.6.4 reskin: the invite gate is its own page, invite.html —
    # 6 pin boxes + activate button + status element, wired by
    # codeString() which joins the boxes as a STRING.
    assert INVITE.count('class="pin"') == 6
    assert 'id="pinRow"' in INVITE
    assert 'id="checkBtn"' in INVITE
    assert 'id="inviteMsg"' in INVITE
    assert "function codeString()" in INVITE
    assert "check_invite" in INVITE and "get_activation_state" in INVITE
    # Code joined as a STRING (leading zero preserved) — never as a number.
    assert ".join('')" in INVITE
    assert "parseInt" not in INVITE.split("function codeString")[1].split("}")[0]
    # The gate lives in its own file now, not inside index.html.
    assert 'class="pin"' not in HTML
    assert "invite-gate" not in HTML


def test_api_keys_page():
    # v1.6.4 reskin: the API keys live in the Settings "api" section,
    # rendered by the Bsec.api builder in index.html — THREE fields now
    # (Gemini voice + Gemini chat + Groq), each with save + test + eye.
    assert "['api', 'مفاتيح API']" in HTML
    # The three providers (the builder renders data-key='<id>' per row).
    assert "[['gemini-voice', 'Gemini صوت'], ['gemini-chat', 'Gemini شات'], ['groq', 'Groq']]" in HTML
    assert "data-key='" in HTML
    assert "Gemini صوت" in HTML
    assert "Gemini شات" in HTML
    assert "Groq" in HTML
    assert 'data-key="gemini"' not in HTML  # no single Gemini field
    assert "محفوظ على الجهاز" in HTML
    assert "save_api_key" in HTML and "test_api_key" in HTML
    assert "إظهار" in HTML


def test_self_view_and_themes_picker():
    # v1.6.4 reskin: "لونا شايفة نفسها" is a Settings section (Bsec.self,
    # fed by get_self_view) and the themes picker is the theme grid
    # (thGrid, built by buildThemeGrid) with the same 5 themes.
    assert "['self', 'لونا شايفة نفسها']" in HTML
    assert "get_self_view" in HTML
    for row in ("الاتصال", "الموديل", "الملفات العميقة"):
        assert row in HTML, "self-view row missing: " + row
    assert 'id="thGrid"' in HTML and "buildThemeGrid" in HTML
    for theme in ("lg", "night", "graphite", "calm", "old"):
        assert f"{theme}:" in HTML or f"'{theme}'" in HTML, \
            "theme missing: " + theme
    assert "الثيم القديم" in HTML  # old v1.5.0 theme stays selectable


def test_updater_installing_state():
    # v1.6.4 reskin: the Updates section (Bsec.update) checks with
    # check_updates and installs the downloaded update with
    # install_update; the install button starts hidden and is only
    # revealed when an update is actually available.
    assert "['update', 'التحديثات']" in HTML
    assert "check_updates" in HTML and "install_update" in HTML
    assert "data-ucheck" in HTML
    assert "data-uinst" in HTML
    assert "<button class='act th' data-uinst hidden>" in HTML
    assert "بيتم التثبيت" in HTML  # installing state while it installs
    assert "تم التثبيت" in HTML
    assert "id='updNote'" in HTML


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
    # v1.6.4 reskin: the wake-word label is DERIVED, not a literal -
    # index.html renders 'يا ' + the chosen AI name (default 'لونا'),
    # and settings_store.derive_wake_phrase() maps the default name
    # LONA -> "يا لونا". The literal "يا لونا" survives as the backend
    # default phrase in main.py.
    assert "'يا ' + (String(S.name).trim() || 'لونا')" in HTML
    assert '"يا لونا"' in MAIN
    assert "يا ميمو" not in HTML and "يا ميمو" not in JS
    assert "يا ميمو" not in CSS and "يا ميمو" not in ISLAND


def test_island_has_no_window_control_buttons():
    for marker in ("exp-min", "exp-max", "exp-close", "island-close"):
        assert marker not in ISLAND, "window-control button remains: " + marker
    # It opens/closes by tapping the island itself (setOpen toggles
    # the .open class - collapseIsland / island-expanded are gone).
    assert "setOpen" in ISLAND
    assert "classList.toggle('open'" in ISLAND
    assert "e.target.closest" in ISLAND
    # v1.6.4-rc.2 (Mohamed 2026-11-23 00:20): the mic icon is REMOVED
    # from the pill entirely - no micBtn, no mic-live indicator, no
    # "بسمعك…" text - and there is still no settings button on the
    # pill. The face circle (.mascot-face) is the pill's only
    # interactive element: tapping it greets (greetFromFace).
    assert 'id="micBtn"' not in ISLAND
    assert "mic-live" not in ISLAND
    assert "بسمعك…" not in ISLAND
    assert "island-settings" not in ISLAND
    assert 'class="bubble mascot-face"' in ISLAND
    assert "greetFromFace" in ISLAND


def test_customizable_ai_name():
    # Settings name field updates the displayed name: the field exists
    # in the General section, loads from ai_name, saves as ai_name,
    # and the wake-word label reads "يا {chosen name}".
    assert "row('اسم المساعد', inp('name'))" in HTML
    assert "S.name = s.ai_name || s.name || S.name" in HTML
    assert "ai_name: S.name" in HTML
    assert "'يا ' + (String(S.name).trim() || 'لونا')" in HTML
    # Backend: renaming re-syncs the wake phrase.
    assert "def set_ai_name" in MAIN
    # v1.6.4 reskin: the island pill shows the LIVE ai_name
    # (id="pillName", default لونا, set from get_settings at boot) —
    # the old static "AI Agent" label is gone.
    assert "AI Agent" not in ISLAND, \
        "REAL GAP: island pill must show the live ai_name, not a static label"


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
    # v1.6.4 reskin: the backup section (Bsec.backup) exports with the
    # password asked twice (bx1/bx2) and imports by path + password.
    assert "['backup', 'نسخة احتياطية']" in HTML
    assert "id='bx1'" in HTML and "id='bx2'" in HTML  # password twice
    assert "data-bexp" in HTML and "data-bimp" in HTML
    assert "export_backup" in HTML and "import_backup" in HTML
    assert "id='bmPath'" in HTML and "id='bm1'" in HTML


def test_exam_countdown_and_pomodoro():
    # REAL GAP (reskin, left failing on purpose): the exam countdown +
    # Pomodoro have NO surface in the reskin UI — index.html contains
    # no exam-countdown / pomodoro markup at all (the backend
    # get_exam_countdown still exists in main.py, but nothing shows
    # it). Ported to the reskin DOM, every assertion below fails.
    assert "exam-countdown" in HTML and "get_exam_countdown" in JS
    assert "مفيش امتحان متسجّل" in HTML  # honest empty state
    assert "pomodoro-timer" in HTML and "pomodoro-start" in HTML
    assert "focus-toggle" in HTML and "هذاكر" in HTML
    assert "بيحتفل" in JS  # celebrates when a focus session finishes


def test_privacy_mode():
    # v1.6.4 reskin: privacy mode is a toggle in the General section
    # (tg('priv')), saved with the rest of General via save_settings.
    assert "وضع الخصوصية" in HTML
    assert "tg('priv'" in HTML
    assert "privacy_mode" in HTML
    assert "save_settings" in HTML


def test_undo_ui():
    assert "renderUndoAffordance" in JS
    assert "تراجع" in JS
    assert "list_undoable" in JS
    assert "relaunch_process" in JS and "restore_deleted" in JS
    assert "undo-btn" in CSS
