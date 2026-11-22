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
        assert "waitForIslandChatResult" in ISLAND
        assert "clearTimeout(capTimer)" in ISLAND
        assert "window.__islandCapMs" in ISLAND

    def test_cap_writes_failure_into_exp_reply(self):
        assert "الرد اتأخر أوي" in ISLAND
        assert 'resolve({ reply: ""' not in ISLAND


class TestB12InFlightGuards(unittest.TestCase):
    def test_every_async_poll_has_a_guard(self):
        for guard in ("permPollInFlight", "_statusInFlight", "_waInFlight",
                      "_proactiveInFlight", "moodPollInFlight",
                      "_liveCardsInFlight", "updatePollInFlight"):
            assert guard in JS, "guard missing: " + guard
        for guard in ("moodPollInFlight", "aiNameInFlight"):
            assert guard in ISLAND, "island guard missing: " + guard
        # Recursive-setTimeout pollers (chat result) need no interval guard.
        assert "pollInFlight" in JS

    def test_guards_use_try_finally(self):
        assert JS.count("finally {") >= 6


class TestB13IslandSingleMoodPoll(unittest.TestCase):
    def test_started_guard_and_stored_interval(self):
        assert "moodPollingStarted" in ISLAND
        assert "moodPollTimer" in ISLAND
        assert ISLAND.count("startMoodPolling()") >= 2
        assert "get_mood" in ISLAND
        assert "5000" in ISLAND


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
        main_col = HTML.split('id="main-col"')[1]
        composer_at = main_col.index('<div class="composer"')
        assert main_col[:composer_at].rstrip().endswith("</div>\n</div>")

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
        assert "robot-stage" in HTML
        assert "stage-robot" in HTML
        assert "robot-rings" in HTML

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
        assert "island-robot" in ISLAND
        assert "island-name" in ISLAND
        assert "green-dot" in ISLAND
        assert "Listening..." in ISLAND

    def test_no_window_control_buttons(self):
        assert "island-mic" not in ISLAND
        assert "island-settings" not in ISLAND
        assert "island-close" not in ISLAND


class TestF4IslandHugsPill(unittest.TestCase):
    def test_no_clock_on_collapsed_pill(self):
        assert "island-clock" not in ISLAND

    def test_clock_only_inside_expanded(self):
        assert "exp-clock" in ISLAND

    def test_no_max_width_squeeze(self):
        assert "calc(100vw - 16px)" not in ISLAND

    def test_robot_bubble_inside_pill(self):
        assert "inset-inline-start: -6px" not in ISLAND
        m = re.search(r"\.island \.robot-bubble\s*\{([^}]*)\}", ISLAND)
        assert m and "inset-inline-start: 0" in m.group(1)


class TestIslandRedesign(unittest.TestCase):
    def test_no_x_mic_settings_buttons(self):
        assert "island-mic" not in ISLAND
        assert "island-settings" not in ISLAND

    def test_no_quick_actions_tools_skills_more(self):
        for gone in ("Quick Actions", "Tools", "Skills", "More"):
            assert gone not in ISLAND, gone

    def test_no_swipe_panel_system(self):
        for gone in ("exp-panels", "panel-dots", "showPanel",
                     "panel-prev", "panel-next"):
            assert gone not in ISLAND, gone
        assert "pointerdown" not in ISLAND or "exp-panels" not in ISLAND

    def test_keeps_expand_collapse_greeting_robot_composer(self):
        assert "expandIsland" in ISLAND
        assert "collapseIsland" in ISLAND
        assert "exp-greet" in ISLAND
        assert "exp-robot" in ISLAND
        assert "exp-input" in ISLAND
        assert "exp-send" in ISLAND


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
        for field in ("api-key-gemini-voice", "api-key-gemini-chat",
                      "api-key-groq"):
            assert field in HTML
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
        assert 'id="model-btn"' in HTML
        assert 'id="model-menu"' in HTML
        composer = HTML.split('<div class="composer"')[1]
        assert "model-btn" in composer.split("</div>", 1)[0] or \
            composer.index("model-btn") < composer.index("text-input")

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
        assert 'id="win-min"' in HTML or "window_minimize" in JS
        assert 'id="win-max"' in HTML or "window_toggle_maximize" in JS
        assert 'id="win-close"' in HTML or "window_close" in JS
        assert "window_move_to" in JS
        assert "wireWindowControls" in JS
        assert 'id="app-header"' in HTML

    def test_deep_files_empty_state_has_request_button(self):
        assert "محتاجة إذن صريح" in JS or "محتاجة إذن صريح" in HTML
        assert "اطلب الإذن" in JS or "اطلب الإذن" in HTML
        assert "request_deep_file_access" in JS

    def test_temp_empty_state_names_lhm(self):
        assert "الحرارة مش متاحة على الجهاز ده" in JS or \
            "الحرارة مش متاحة على الجهاز ده" in HTML
        assert "LibreHardwareMonitor" in JS or "LibreHardwareMonitor" in HTML

    def test_model_indicator_is_permanent(self):
        assert 'id="model-indicator"' in HTML or "model-indicator" in HTML
        assert "model-indicator" in CSS


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
