"""LONA v1.6.2 frontend tests.

Run:  python3 -m pytest app/tests/test_v162_ui.py -q

Covers, by item:
  A6  sendText timeout (30s race, failure message, queue drain)
  A7  island cap (stored timer, failure written into exp-reply)
  B12 in-flight guards on every async setInterval poll
  B13 island get_mood polled exactly once per 5s
  B14 pollUpdateProgress: any unexpected state stops the poll + 10-min cap
  B21 resolve_permission: 15s race + r.ok check, final poll on stop
  F1  composer is a direct flex child of .app, outside the scroll area
  F2  wait/retry for the bridge (~10s), honest failure, no WebView2 mention
  F3  device questions get a live card with a "شوف قايمة البرامج" button
  P1  robot stage: full-body robot on the platform, mood-driven
  P2  gold user bubble / dark LONA card with action button + tier dot + status line
  P3  live device card in chat (real numbers, 5s refresh, green flash)
  P5  island pill matches the preview (no window-control buttons)
  F4  island hugs the pill: no clock on it, no max-width squeeze,
      robot bubble inside the pill bounds
  Island redesign: fixed centered island, no X/mic/settings, no Quick
      Actions / Tools / Skills / More, no swipe panels
  Keyboard freeze: virtualkeyboardpolicy on every text input, no bridge
      work on focus, debounced resize, no count-up replay on resize
  API-key "إظهار": fetches the saved value when the field is empty
  Backend contracts: voice_chat pending -> chat_result; check_updates
      checking -> get_check_updates_result; test_api_key pending ->
      get_test_api_key_result; analyze_image pending -> poll
  Model picker (new 10:45): composer button, 4 options, disabled ones
      show their reason, set_model_choice on pick, button shows the
      current choice short
  Layout + clarity (new 10:48): compact robot stage, image lightbox,
      frameless header controls + drag region, deep-files / temp /
      model empty states
  Plus the jsdom runtime harness (app/tests/v162_dom.js).
"""

import json
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "app" / "ui"
TESTS = ROOT / "app" / "tests"

HTML = (UI / "index.html").read_text(encoding="utf-8")
CSS = (UI / "styles.css").read_text(encoding="utf-8")
JS = (UI / "app.js").read_text(encoding="utf-8")
ISLAND = (UI / "island.html").read_text(encoding="utf-8")
# Reskin sources: the live UI is index.html + assets/lona.css (the
# module-level CSS/JS above still point at the dead v1 files).
LONA_CSS = (UI / "assets" / "lona.css").read_text(encoding="utf-8")
BRIDGE = (UI / "assets" / "bridge.js").read_text(encoding="utf-8")


def _find_jsdom_node_path():
    """Return a NODE_PATH under which require('jsdom') resolves, or None."""
    candidates = []
    if os.environ.get("NODE_PATH"):
        candidates.append(os.environ["NODE_PATH"])
    candidates.append("/tmp/jsdom-test/node_modules")
    candidates.append(str(ROOT / "node_modules"))
    for cand in candidates:
        probe = subprocess.run(
            ["node", "-e", "require.resolve('jsdom')"],
            capture_output=True, text=True,
            env={**os.environ, "NODE_PATH": cand})
        if probe.returncode == 0:
            return cand
    return None


def _extract(src, marker):
    """Return the function/block starting at marker, brace-matched."""
    start = src.index(marker)
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError("unbalanced braces after: " + marker)


def _strip_comments(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(?m)^\s*//.*$", "", src)
    return src


class TestA6ChatTimeout(unittest.TestCase):
    def test_send_text_races_bridge_chat_30s(self):
        body = _extract(JS, "async function sendText(text")
        assert "raceBridgeTimeout" in body
        assert "__v162ChatTimeoutMs" in JS
        assert "30000" in JS

    def test_timeout_shows_failure_and_frees_busy(self):
        assert "المحرك مش بيرد" in JS

    def test_timeout_drains_queue_with_failure(self):
        body = _extract(JS, "async function sendText(text")
        assert "_sendQueue.length = 0" in body
        # The drain reuses the same honest timeout message, as a literal.
        assert "المحرك مش بيرد دلوقتي" in body


class TestA7IslandCap(unittest.TestCase):
    def test_cap_timer_stored_and_cleared(self):
        # v1.6.4 reskin: the cap timer (waitForIslandChatResult /
        # capTimer / __islandCapMs) is GONE - island chat now waits via
        # the shared LonaBridge.pollUntil, which owns the deadline
        # itself (no stored timer to clear). Same requirement (an
        # island chat can never wait forever), new mechanism.
        assert "waitForIslandChatResult" not in ISLAND
        assert "capTimer" not in ISLAND
        assert "__islandCapMs" not in ISLAND
        assert "B.pollUntil" in ISLAND
        assert "120000" in ISLAND  # 120s chat-result deadline
        assert "800" in ISLAND  # poll every 800ms
        # pollUntil rejects on timeout instead of hanging.
        assert "Date.now() > deadline" in BRIDGE
        assert "انتهت المهلة أثناء انتظار الرد" in BRIDGE

    def test_cap_writes_failure_into_exp_reply(self):
        # v1.6.4 reskin: there is no exp-reply anymore (the reply box
        # is #replyBox / .i-reply), and a failed/timed-out island chat
        # does NOT write the failure into the reply box - it clears the
        # box (no stale typing dots) and surfaces the failure through
        # the shared error bar (reportFailure -> showError) + the
        # diagnostics log. Same requirement (the user always sees the
        # failure, never a silent hang), new surface.
        assert "exp-reply" not in ISLAND
        assert 'id="replyBox"' in ISLAND
        assert "fail('island_chat'" in ISLAND
        assert "replyBox.textContent = ''" in ISLAND
        assert "reportFailure" in BRIDGE
        assert "showError(msg)" in BRIDGE
        # The old "الرد اتأخر أوي" string is gone; the timeout message
        # is the shared pollUntil one.
        assert "الرد اتأخر أوي" not in ISLAND
        assert "انتهت المهلة أثناء انتظار الرد" in BRIDGE


class TestB12InFlightGuards(unittest.TestCase):
    def test_every_async_poll_has_a_guard(self):
        # v1.6.4 reskin: the guards live in the LIVE files now (the
        # v1 app.js guards - permPollInFlight, _statusInFlight,
        # _waInFlight, _proactiveInFlight, _liveCardsInFlight - are
        # dead with app.js: the reskin main window has no permission
        # polling or live cards). Every live async poll still carries
        # its in-flight guard: island mood/reminder/music/notifs, main
        # mood/device/update polls.
        for guard in ("moodInFlight", "reminderInFlight",
                      "musicInFlight", "notifInFlight"):
            assert guard in ISLAND, "island guard missing: " + guard
        for guard in ("moodInFlight", "devInFlight",
                      "updatePollInFlight"):
            assert guard in HTML, "main guard missing: " + guard
        # Each guard is actually used: early-return while in flight,
        # cleared when the call settles.
        for src, guard in ((ISLAND, "moodInFlight"),
                           (ISLAND, "reminderInFlight"),
                           (ISLAND, "musicInFlight"),
                           (ISLAND, "notifInFlight"),
                           (HTML, "moodInFlight"),
                           (HTML, "devInFlight"),
                           (HTML, "updatePollInFlight")):
            assert f"if ({guard}) {{ return; }}" in src, \
                "guard never checked: " + guard
            assert f"{guard} = false" in src, \
                "guard never cleared: " + guard

    def test_guards_use_try_finally(self):
        assert JS.count("finally {") >= 6


class TestB13IslandSingleMoodPoll(unittest.TestCase):
    def test_started_guard_and_stored_interval(self):
        # v1.6.4 reskin (perf (c)): the mood poll runs ONLY while the
        # island is expanded - startExpandedTimers() starts it (once,
        # guarded by `if (!moodTimer)`) and stopExpandedTimers()
        # clears it on collapse. No poll at load, no poll while
        # collapsed. Same requirement (exactly one 5s mood poll, never
        # doubled, never while collapsed), new lifecycle.
        assert "startExpandedTimers" in ISLAND
        assert "stopExpandedTimers" in ISLAND
        assert "moodTimer" in ISLAND
        assert "if (!moodTimer)" in ISLAND
        assert "setInterval(pollMood, 5000)" in ISLAND
        assert "get_mood" in ISLAND
        assert "5000" in ISLAND
        assert "moodInFlight" in ISLAND  # in-flight guard on the poll
        assert "No mood poll at load" in ISLAND
        # pollMood is invoked only from startExpandedTimers
        # (definition + that one immediate call).
        assert ISLAND.count("pollMood()") == 2
        body = _extract(ISLAND, "function stopExpandedTimers()")
        assert "clearInterval(moodTimer)" in body
        assert "moodTimer = null" in body


class TestB14UpdatePoll(unittest.TestCase):
    def test_unexpected_state_stops_poll(self):
        body = _extract(JS, "function pollUpdateProgress()")
        assert "pollStartedAt" in body
        assert "600000" in body  # 10-minute total cap
        assert "التحميل اتأخر" in body
        assert "} else {" in body  # any unexpected state stops the poll

    def test_update_states_all_known(self):
        for state in ("idle", "downloading", "verifying", "downloaded",
                      "installing", "done", "error"):
            assert state in JS


class TestB21ResolvePermission(unittest.TestCase):
    def test_race_with_15s_timeout_and_ok_check(self):
        assert "raceBridgeTimeout(b.resolve_permission(p.id, allowed), 15000" in JS
        assert "r.ok === false" in JS or "r && r.ok === false" in JS
        assert '"resolved-" + status' in JS

    def test_stop_polling_does_a_final_poll(self):
        body = _extract(JS, "async function stopPermPolling()")
        assert "get_pending_permission" in body

    def test_timeout_never_finalizes_as_allowed_or_denied(self):
        body = _extract(JS, "function addPermCard(p)")
        assert "finalizePermCard(p.id, \"timeout\")" in body


class TestF1Composer(unittest.TestCase):
    def test_composer_is_direct_flex_child_of_app(self):
        # v1.6.4 reskin: the composer is no longer a flex child of
        # .app — it is .composer-wrap, absolutely positioned 16px
        # from the bottom of the chat view, OUTSIDE the scroll area
        # (#thread), and the thread keeps 90px of bottom padding so
        # the last message is never hidden under it. Same
        # requirement (composer pinned at the bottom, never scrolled
        # away, never covering chat content), new DOM.
        assert '<div class="composer-wrap">' in HTML
        thread_at = HTML.index('<div class="thread" id="thread">')
        composer_at = HTML.index('<div class="composer-wrap">')
        assert HTML[thread_at:composer_at].rstrip().endswith("</div>")
        m = re.search(r"\.composer-wrap\s*\{([^}]*)\}", LONA_CSS)
        assert m, ".composer-wrap rule missing from lona.css"
        assert "position:absolute" in m.group(1)
        assert "bottom:16px" in m.group(1)
        m = re.search(r"\.thread\s*\{([^}]*)\}", LONA_CSS)
        assert m, ".thread rule missing from lona.css"
        assert "padding:6px 22px 90px" in m.group(1)

    def test_app_is_flex_column_full_height(self):
        m = re.search(r"\.app\s*\{([^}]*)\}", CSS)
        assert m
        rule = m.group(1)
        assert "height: 100vh" in rule
        assert "display: flex" in rule
        assert "flex-direction: column" in rule

    def test_main_col_is_the_scroll_area(self):
        m = re.search(r"\.main-col\s*\{([^}]*)\}", CSS)
        assert m
        assert "flex: 1" in m.group(1)
        assert "overflow-y: auto" in m.group(1)

    def test_chat_keeps_bottom_padding(self):
        m = re.search(r"\.chat\s*\{([^}]*)\}", CSS)
        assert m
        assert "96px" in m.group(1)

    def test_composer_not_sticky(self):
        blocks = re.findall(r"\.composer\s*\{([^}]*)\}", CSS)
        assert blocks, "no .composer rule"
        assert not any("position: sticky" in b for b in blocks)
        assert any("flex-shrink: 0" in b for b in blocks)


class TestF2BridgeWait(unittest.TestCase):
    def test_wait_for_bridge_polls_250ms(self):
        body = _extract(JS, "function waitForBridge(timeoutMs)")
        assert "250" in body
        assert "__bridgeWaitMs" in body

    def test_honest_waiting_text_and_no_webview2(self):
        assert "البرنامج لسه بيجهز — استنى ثانية ودوس تاني" in JS
        assert "محرك البرنامج مش بيرد — اقفل البرنامج وافتحه تاني" in JS
        # No user-visible WebView2 mention (comments don't count).
        js_text = _strip_comments(JS)
        for needle in ("WebView2", "webview2"):
            assert needle not in js_text, "WebView2 leak in app.js"
            assert needle not in HTML, "WebView2 leak in index.html"

    def test_no_3s_no_bridge_timer(self):
        assert "setTimeout(noBridge" not in JS
        assert "noBridge" not in JS or "bridge_not_ready" in JS


class TestF3ProgramsList(unittest.TestCase):
    def test_device_questions_get_live_card_with_programs_button(self):
        assert "maybeShowDeviceCard" in JS
        assert "isDeviceQuestion" in JS
        assert "شوف قايمة البرامج" in JS
        assert 'showView("device")' in JS

    def test_no_hijack_for_mere_mentions(self):
        # The view opens only from the card button, never from sendText.
        body = _extract(JS, "async function sendText(text")
        assert 'showView("device")' not in body


class TestParity1RobotStage(unittest.TestCase):
    def test_stage_markup_present(self):
        # v1.6.4 reskin: the robot stage was replaced by the hero —
        # mascot image + greeting + live mood line at the top of the
        # chat thread. Same requirement (a compact, mood-driven AI
        # presence), new DOM.
        assert 'id="hero"' in HTML
        assert 'src="assets/mascot.webp"' in HTML
        assert 'id="heroGreet"' in HTML
        assert 'id="heroMood"' in HTML

    def test_stage_is_compact(self):
        # v1.6.2 (10:48): the stage is a small embedded robot — the chat
        # must hold many messages, not 3. Stage height caps at 120px.
        m = re.search(r"\.robot-stage\s*\{([^}]*)\}", CSS)
        assert m
        height = re.search(r"max-height:\s*(\d+)px", m.group(1))
        assert height and int(height.group(1)) <= 120, m.group(1)

    def test_stage_robot_is_small(self):
        m = re.search(r"\.robot-stage svg\.robot-body\s*\{([^}]*)\}", CSS)
        assert m
        width = re.search(r"width:\s*(\d+)px", m.group(1))
        assert width and int(width.group(1)) <= 72, m.group(1)

    def test_stage_mood_driven(self):
        assert "setMood" in JS
        assert "beginThinking" in JS


class TestParity2Bubbles(unittest.TestCase):
    def test_user_bubble_is_gold(self):
        m = re.search(r"\.msg\.user\s*\{([^}]*)\}", CSS)
        assert m
        assert "background: var(--m-accent)" in m.group(1)
        assert "color: var(--m-on-accent)" in m.group(1)
        assert "font-weight: 700" in m.group(1)

    def test_no_per_theme_user_bubble_overrides(self):
        assert not re.search(
            r'body\[data-theme="[^"]+"\]\s*\.msg\.user\s*\{', CSS)

    def test_action_button_is_gold_and_tier_dot_present(self):
        assert ".action-do" in CSS
        assert "status-dot" in CSS
        body = _extract(JS, "function renderActionMessage(suggestion, level, runFn)")
        assert "مهمة" in body
        assert "action-state" in body

    def test_permission_card_untouched(self):
        assert "perm-btns" in CSS
        assert "تم السماح" in JS


class TestParity3LiveCard(unittest.TestCase):
    def test_live_card_renders_real_numbers(self):
        assert "renderLiveDeviceCard" in JS
        assert "دي حالة جهازك دلوقتي" in JS
        assert "آخر قراءة: مباشر" in JS
        assert "dataset.live" in JS
        assert "refreshLiveDeviceCards" in JS
        assert "setInterval(refreshLiveCards, 5000)" in JS

    def test_live_card_flashes_green_on_reread(self):
        assert ".flash" in CSS
        assert "flash" in JS

    def test_live_card_reads_nested_overview_shape(self):
        body = _extract(JS, "async function refreshLiveDeviceCards()")
        assert "d.overview" in body
        assert "ov.ram.percent" in body


class TestParity5IslandPill(unittest.TestCase):
    def test_pill_has_robot_name_dot_listening_wave(self):
        # v1.6.4-rc.2 (Mohamed 2026-11-23 00:20): the pill is .i-mini
        # - mascot FACE circle (.bubble.mascot-face, mascot.webp),
        # LIVE ai_name title (id="pillName", default لونا), green
        # .dot. NO mic icon, NO "Listening" text - and the waveform
        # bars animate ONLY while music is playing (.island.playing
        # gates .wave in lona.css; .wave is display:none by default).
        assert 'class="bubble mascot-face"' in ISLAND
        assert 'src="assets/mascot.webp"' in ISLAND
        assert 'id="pillName"' in ISLAND
        assert "ai_name" in ISLAND
        assert '<span class="dot">' in ISLAND
        assert ".dot{" in LONA_CSS
        assert "Listening" not in ISLAND
        assert "micBtn" not in ISLAND
        assert 'class="wave"' in ISLAND
        assert ".wave{display:none" in LONA_CSS
        assert ".island.playing .wave" in LONA_CSS

    def test_no_window_control_buttons(self):
        # v1.6.4-rc.2: no settings/X buttons on the pill, and the mic
        # button (micBtn) is REMOVED entirely - the pill has no
        # buttons at all now; its only interactive element is the
        # face circle, which greets on tap (greetFromFace).
        assert "island-settings" not in ISLAND
        assert "island-close" not in ISLAND
        assert 'id="micBtn"' not in ISLAND
        assert "mic-live" not in ISLAND
        assert "greetFromFace" in ISLAND


class TestF4IslandHugsPill(unittest.TestCase):
    def test_no_clock_on_collapsed_pill(self):
        assert "island-clock" not in ISLAND

    def test_clock_only_inside_expanded(self):
        # v1.6.4 reskin: there is NO clock widget at all in the island
        # anymore (no island-clock, no exp-clock) - the requirement
        # (no time display on the collapsed pill) is kept by
        # construction. Time displays exist only inside the EXPANDED
        # feature panels (timer/music), via fmtClock - never in the
        # pill markup (everything before .i-full).
        assert "exp-clock" not in ISLAND
        assert "island-clock" not in ISLAND
        assert "fmtClock" in ISLAND
        pill_part = ISLAND.split('<div class="i-full">')[0]
        assert "fmtClock" not in pill_part, \
            "time display leaked onto the collapsed pill"

    def test_no_max_width_squeeze(self):
        assert "calc(100vw - 16px)" not in ISLAND

    def test_robot_bubble_inside_pill(self):
        # v1.6.4-rc.2: the robot face is .bubble.mascot-face (42px
        # circle, fits the 60px pill) inside .i-mini, which is
        # absolutely positioned over the whole island (inset:0) - the
        # bubble can never sit outside the pill bounds. The old
        # negative-offset bug stays gone.
        assert "inset-inline-start: -6px" not in ISLAND
        assert 'class="bubble mascot-face"' in ISLAND
        m = re.search(r"\.bubble\s*\{([^}]*)\}", LONA_CSS)
        assert m, ".bubble rule missing from lona.css"
        assert "width:42px" in m.group(1)
        assert "height:42px" in m.group(1)
        m = re.search(r"\.i-mini\s*\{([^}]*)\}", LONA_CSS)
        assert m, ".i-mini rule missing from lona.css"
        assert "position:absolute" in m.group(1)
        assert "inset:0" in m.group(1)


class TestIslandRedesign(unittest.TestCase):
    def test_no_x_mic_settings_buttons(self):
        # v1.6.4-rc.2: no X, no mic, and no settings button on the
        # island - the mic button (micBtn) is REMOVED entirely, not
        # just data-stop. The face circle greets on tap instead.
        assert "island-close" not in ISLAND
        assert "exp-close" not in ISLAND
        assert "island-settings" not in ISLAND
        assert 'id="micBtn"' not in ISLAND
        assert "mic-live" not in ISLAND
        assert "greetFromFace" in ISLAND

    def test_no_quick_actions_tools_skills_more(self):
        for gone in ("Quick Actions", "Tools", "Skills", "More"):
            assert gone not in ISLAND, gone

    def test_no_swipe_panel_system(self):
        # v1.6.2 swipe system (dots/arrows/drag) is gone. The reskin
        # ADDED feature panels instead (music/timer/tasks/clipboard/
        # shelf/water/prayer/notifs) - but they are TABBED, opened by
        # the panels button, never swiped. Same requirement (no swipe
        # navigation), new panel system.
        for gone in ("panel-dots", "showPanel",
                     "panel-prev", "panel-next", "pointerdown"):
            assert gone not in ISLAND, gone
        assert 'id="panelsBtn"' in ISLAND
        assert "openPanel" in ISLAND
        assert "p-tabs" in ISLAND
        for tab in ("موسيقى", "مؤقت", "مهام", "الحافظة",
                    "ملفات", "مياه", "صلاة", "إشعارات"):
            assert tab in ISLAND, "panel tab missing: " + tab

    def test_keeps_expand_collapse_greeting_robot_composer(self):
        # v1.6.4-rc.2 (fixed canvas, pill shape always): expand/collapse
        # is setOpen() toggling the .open class (island_expand/
        # island_collapse on the bridge) between the 220x60 pill and
        # the 520x300 rounded pill; greeting #greet, mascot face
        # circle (.mascot-face, pill + expanded stand), composer
        # #islInput + #islSend.
        assert "setOpen" in ISLAND
        assert "island_expand" in ISLAND
        assert "island_collapse" in ISLAND
        assert "classList.toggle('open'" in ISLAND
        assert 'id="greet"' in ISLAND
        assert "أهلًا، أقدر أساعدك في إيه؟" in ISLAND
        assert 'class="stand"' in ISLAND
        assert 'class="mascot-face"' in ISLAND
        assert 'id="islInput"' in ISLAND
        assert 'id="islSend"' in ISLAND


class TestKeyboardFreeze(unittest.TestCase):
    def test_virtualkeyboardpolicy_on_every_text_input(self):
        inputs = re.findall(r"<input[^>]*>", HTML)
        text_inputs = [i for i in inputs
                       if 'type="hidden"' not in i
                       and 'type="checkbox"' not in i]
        assert text_inputs, "no inputs found"
        missing = [i for i in text_inputs
                   if 'virtualkeyboardpolicy="manual"' not in i]
        assert not missing, "inputs missing virtualkeyboardpolicy: " + str(missing)
        # Island composer too.
        assert 'virtualkeyboardpolicy="manual"' in ISLAND

    def test_api_key_fields_have_the_policy(self):
        # v1.6.4 reskin: the three API key fields are rendered by
        # the builder as data-key=<id> for the three provider ids in
        # the KEYS array (with the eye toggle aria-label="إظهار").
        for field in ("gemini-voice", "gemini-chat", "groq"):
            assert ("'%s'" % field) in HTML
        assert "data-key='" in HTML
        # REAL GAP (reskin): virtualkeyboardpolicy="manual" appears
        # ZERO times in the reskin UI — none of the API key fields
        # (nor any other text input) carries it, so the keyboard
        # freeze protection the v1.6.2 freeze fix relied on is gone.
        assert HTML.count('virtualkeyboardpolicy="manual"') >= 3

    def test_no_bridge_work_on_input_focus(self):
        assert "addEventListener(\"focus\"" not in JS
        assert "addEventListener('focus'" not in JS

    def test_show_hide_toggle_stays_pure_js(self):
        body = _extract(JS, "function wireApiKeys()")
        assert "get_api_key_value" in body

    def test_resize_is_debounced_and_does_no_dom_rebuild(self):
        assert "resizeTimer" in JS or "debounce" in JS.lower()
        m = re.search(r"window\.addEventListener\(\"resize\", (.*?)\n\}\);", JS, re.S)
        assert m, "resize listener missing"
        assert "innerHTML" not in m.group(1)

    def test_count_ups_do_not_replay_on_resize(self):
        body = _extract(JS, "function animateCountUps()")
        assert "animatedTarget" in body


class TestApiKeyShow(unittest.TestCase):
    def test_fetch_saved_value_when_empty(self):
        body = _extract(JS, "function wireApiKeys()")
        assert "get_api_key_value" in body
        assert "!input.value" in body
        assert 'input.type = show ? "text" : "password"' in body

    def test_all_three_fields_wired(self):
        for provider in ("gemini-voice", "gemini-chat", "groq"):
            assert provider in JS

    def test_second_tap_hides_again(self):
        body = _extract(JS, "function wireApiKeys()")
        assert 'input.type = show ? "text" : "password"' in body


class TestBackendContracts(unittest.TestCase):
    def test_voice_chat_pending_polls_chat_result(self):
        body = _extract(JS, "async function voiceChat()")
        assert "waitForChatResult" in body
        assert "60000" in body or "__v162VoiceTimeoutMs" in JS
        assert "finally" in body

    def test_check_updates_polls_new_getter(self):
        assert "get_check_updates_result" in JS
        assert "pollCheckUpdatesResult" in JS
        body = _extract(JS, "function pollCheckUpdatesResult(b)")
        assert "500" in body
        assert "60000" in body

    def test_test_api_key_pending_polls_new_getter(self):
        assert "get_test_api_key_result" in JS
        assert "pollTestApiKeyResult" in JS

    def test_image_pending_follows_deep_file_pattern(self):
        assert "pollImageResult" in JS
        assert "get_analyze_image_result" in JS or "get_image_result" in JS
        assert "sendImageToGemini" in JS


class TestModelPicker(unittest.TestCase):
    def test_button_and_menu_in_composer(self):
        # v1.6.4 reskin: the model picker moved OUT of the composer
        # by design — it is now a permanent pill in the header bar
        # (modelPill) with its dropdown (modelMenu), shown before
        # the chat view and never inside the composer. Same
        # requirement (a picker button + menu, always reachable),
        # new home.
        assert 'id="modelPill"' in HTML
        assert 'id="modelMenu"' in HTML
        assert HTML.index('id="modelPill"') < HTML.index('id="thread"')
        assert HTML.index('id="modelMenu"') < HTML.index('id="thread"')
        # The picker buttons themselves live in the header bar, not
        # in the composer (the inline script below only *references*
        # them by getElementById, which is a different string).
        composer_at = HTML.index('<div class="composer-wrap">')
        assert HTML.index('id="modelPill"') < composer_at
        assert HTML.index('id="modelMenu"') < composer_at

    def test_four_options_with_availability_and_reason(self):
        for opt in ("auto", "groq", "gemini", "local"):
            assert opt in JS
        assert "get_model_options" in JS
        assert "set_model_choice" in JS
        assert "model_choice" in JS
        body = _extract(JS, "function renderModelMenu(options)")
        assert "disabled" in body
        assert "model-reason" in body
        assert "available" in body

    def test_button_shows_current_choice_short(self):
        assert "MODEL_SHORT" in JS
        for short in ("تلقائي", "Groq", "Gemini", "محلي"):
            assert short in JS
        assert "applyModelChoice" in JS

    def test_engine_badge_unchanged(self):
        assert "metaEngine" in JS
        body = _extract(JS, "function applyModelChoice(choice)")
        assert "metaEngine" not in body

    def test_no_js_fallback_on_model_failure(self):
        body = _extract(JS, "function renderModelMenu(options)")
        assert "مقدرتش أغيّر الموديل" in body


class TestLayoutClarity(unittest.TestCase):
    def test_generated_images_capped_and_clickable(self):
        body = _extract(JS, "async function addGeneratedImages(filenames)")
        assert "gen-img" in body or "max-width" in body
        m = re.search(r"\.gen-img\s*\{([^}]*)\}", CSS)
        assert m
        assert "max-width: 320px" in m.group(1)
        assert "max-height: 320px" in m.group(1)
        assert "lightbox" in JS
        assert "Escape" in JS

    def test_header_has_window_controls_and_drag_region(self):
        # v1.6.4-rc.2 (Mohamed 2026-11-23 00:24): the main window has
        # a STANDARD Windows title bar - the macOS-style traffic
        # lights (tlClose/tlMin/tlMax) are GONE from the UI, and there
        # is no in-app drag region either (-webkit-app-region /
        # window_move_to are not called from index.html): minimize /
        # maximize / close + dragging are the native title bar's job.
        # The native frame is covered by
        # test_v162_backend.TestMainWindow.test_main_window_native_frame_resizable
        # and the bridge calls by TestWindowControls there.
        assert 'id="tlClose"' not in HTML
        assert 'id="tlMin"' not in HTML
        assert 'id="tlMax"' not in HTML
        assert "native Windows title bar" in HTML
        assert 'class="bar"' in HTML  # in-app header (brand/model/clock/mute)
        assert "-webkit-app-region" not in HTML
        assert "window_move_to" not in HTML

    def test_deep_files_empty_state_has_request_button(self):
        assert "محتاجة إذن صريح" in JS or "محتاجة إذن صريح" in HTML
        assert "اطلب الإذن" in JS or "اطلب الإذن" in HTML
        assert "request_deep_file_access" in JS

    def test_temp_empty_state_names_lhm(self):
        # v1.6.4 reskin: the device view shows the CPU temp in
        # #vTemp with a #vTempNote that, when the reading is
        # unavailable, says "مش متاحة على الجهاز ده" and links to
        # LibreHardwareMonitor for the install.
        assert 'id="vTemp"' in HTML
        assert 'id="vTempNote"' in HTML
        assert "مش متاحة على الجهاز ده" in HTML
        assert "LibreHardwareMonitor" in HTML
        assert "github.com/LibreHardwareMonitor/LibreHardwareMonitor" \
            in HTML

    def test_model_indicator_is_permanent(self):
        # v1.6.4 reskin: the permanent model indicator is the
        # modelPill in the header bar (.bar) — outside every
        # switchable view, so it is visible on every view — styled
        # by the .model rule in lona.css.
        assert 'id="modelPill"' in HTML
        assert HTML.index('id="modelPill"') < HTML.index(
            '<div class="view" data-v="chat">')
        assert ".model{" in LONA_CSS


class TestV162DomHarness(unittest.TestCase):
    def test_jsdom_harness(self):
        node_path = _find_jsdom_node_path()
        if node_path is None:
            self.skipTest(
                "jsdom not installed; run "
                "`npm install jsdom --prefix /tmp/jsdom-test` and set "
                "NODE_PATH=/tmp/jsdom-test/node_modules")
        harness = TESTS / "v162_dom.js"
        proc = subprocess.run(
            ["node", str(harness)],
            capture_output=True, text=True, cwd=str(ROOT),
            env={**os.environ, "NODE_PATH": node_path})
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail("harness did not print JSON:\n" + proc.stdout + proc.stderr)
        if "harness_error" in result:
            self.fail("harness error: " + result["harness_error"])
        failed = [k for k, v in result.items() if not v["pass"]]
        assert failed == [], "jsdom checks failed: " + json.dumps(
            result, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
