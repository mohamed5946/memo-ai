"""v1.6.1 workstream D - typing indicator + mood stability/tone +
thinking mood.

Run only this file (plus test_v161_temp.py):

    python -m pytest app/tests/test_v161_typing_mood.py -q

What is proven here:
  * Typing bubble (item 8): sendText() keeps the 3-dot bubble up
    through the whole pending poll - hideTyping() is NOT called
    between bridge.chat() returning pending and waitForChatResult()
    resolving; it is hidden in a finally, so errors hide it too.
    showTyping() is idempotent (one bubble, never stacked).
  * The dots really animate: styles.css gives .msg.typing span the
    blink keyframes (opacity + translateY bounce) with staggered
    delays, and no rule overrides the animation.
  * Mood stability (item 9): a mood holds >=150s and a new mood needs
    2 consecutive confirming polls; temp >=90C switches immediately.
    Tone heuristics: praise/thanks/laughter -> happy, frustration /
    anger / urgency / sadness -> worried (sympathetic), greeting ->
    cheerful, neutral -> no mood. Within the healthy range the tone
    drives the mood; a real device problem always wins over tone.
  * Thinking mood (item 10): while thinking, the main window shows
    "بيفكر" and restores the previous mood afterwards; the mood poll
    never clobbers the thinking override; the backend get_mood()
    returns "بيفكر" while a chat is pending (the island polls the
    same method, so it shows it too) without touching the
    stabilizer - verified by source contract, since main.py needs
    pywebview and is not imported here.
"""
import re
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from monitor import (  # noqa: E402
    MoodStabilizer,
    SystemMonitor,
    analyze_message_tone,
    device_state_to_mood,
)

UI = APP_DIR / "ui"
JS = (UI / "app.js").read_text(encoding="utf-8")
CSS = (UI / "styles.css").read_text(encoding="utf-8")
MAIN = (APP_DIR / "main.py").read_text(encoding="utf-8")


def _fn_source(src: str, start_marker: str) -> str:
    """Source of one top-level JS function (ends at the first
    column-0 closing brace)."""
    idx = src.index(start_marker)
    end = src.index("\n}\n", idx)
    return src[idx:end]


def _py_fn_source(src: str, start_marker: str) -> str:
    """Source of one Python method (ends at the next method def)."""
    idx = src.index(start_marker)
    nxt = src.find("\n    def ", idx + len(start_marker))
    return src[idx:] if nxt == -1 else src[idx:nxt]


def _reading(**overrides):
    reading = {
        "ram": {"total_mb": 8192, "used_mb": 4096, "free_mb": 4096,
                "percent": 50.0},
        "disk": {"free_gb": 100.0, "total_gb": 256.0,
                 "percent_free": 80.0},
        "cpu_percent": 10.0,
        "battery": {"percent": 80, "charging": True},
        "temp_c": None,
        "temp_available": False,
        "top_processes": [],
        "gpu": {"available": False, "percent": None, "detail": ""},
        "storage": [],
        "battery_drain": {},
    }
    reading.update(overrides)
    return reading


def _status_for_mood(**overrides):
    st = {
        "ram": {"total_mb": 8192, "used_mb": 4096, "free_mb": 4096,
                "percent": 50.0},
        "cpu": {"percent": 10.0, "sustained_high": False},
        "battery": {"percent": 80, "charging": True},
        "temp_c": None, "temp_available": False,
    }
    st.update(overrides)
    return st


class TestToneHeuristics(unittest.TestCase):
    def test_praise_and_thanks_are_happy(self):
        for text in ("شكرا يا لونا", "تسلم إيدك", "برافو عليك",
                     "جميل جدا", "الله عليك"):
            mood, _ = analyze_message_tone(text)
            self.assertEqual(mood, "مبسوط وبيرقص", text)

    def test_laughter_and_jokes_are_happy(self):
        for text in ("ههههههه", "دي نكتة حلوة", "ضحكتني"):
            mood, _ = analyze_message_tone(text)
            self.assertEqual(mood, "مبسوط وبيرقص", text)

    def test_frustration_and_anger_are_worried(self):
        for text in ("زهقت من البرنامج", "النت مش شغال",
                     "باظ كل حاجة", "أنا عصبي دلوقتي"):
            mood, _ = analyze_message_tone(text)
            self.assertEqual(mood, "قلقان", text)

    def test_sad_talk_is_worried_sympathetic(self):
        for text in ("أنا زعلان", "حزين النهاردة", "مخنوق"):
            mood, _ = analyze_message_tone(text)
            self.assertEqual(mood, "قلقان", text)

    def test_urgency_is_worried(self):
        mood, _ = analyze_message_tone("الحقني بسرعة ضروري")
        self.assertEqual(mood, "قلقان")

    def test_greeting_is_cheerful(self):
        for text in ("صباح الخير", "مساء الخير يا لونا",
                     "ازيك عامل ايه"):
            mood, _ = analyze_message_tone(text)
            self.assertEqual(mood, "سعيد بيك", text)

    def test_neutral_and_empty_note_no_mood(self):
        for text in ("افتح VS Code", "الساعة كام", "", "   ", None):
            mood, _ = analyze_message_tone(text)
            self.assertIsNone(mood, repr(text))

    def test_distress_beats_greeting(self):
        mood, _ = analyze_message_tone("صباح الخير بس أنا زهقت")
        self.assertEqual(mood, "قلقان")


class TestMoodStabilizer(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.stab = MoodStabilizer(clock=lambda: self.now[0])

    def test_first_mood_adopted_immediately(self):
        mood, _ = self.stab.stabilize("صاحي", "عادي")
        self.assertEqual(mood, "صاحي")

    def test_single_flicker_poll_does_not_flip(self):
        self.stab.stabilize("صاحي", "عادي")
        mood, _ = self.stab.stabilize("نايم", "هادي")
        self.assertEqual(mood, "صاحي")

    def test_confirm_without_hold_still_does_not_flip(self):
        self.stab.stabilize("صاحي", "عادي")
        self.stab.stabilize("نايم", "هادي")
        mood, _ = self.stab.stabilize("نايم", "هادي")
        self.assertEqual(mood, "صاحي")

    def test_flips_after_confirm_and_hold_elapsed(self):
        self.stab.stabilize("صاحي", "عادي")
        self.stab.stabilize("نايم", "هادي")
        self.now[0] += 200.0  # past the 150s hold
        mood, _ = self.stab.stabilize("نايم", "هادي")
        self.assertEqual(mood, "نايم")

    def test_pending_resets_when_candidate_changes(self):
        self.stab.stabilize("صاحي", "عادي")
        self.stab.stabilize("نايم", "هادي")
        self.now[0] += 200.0
        # A different candidate restarts the confirmation count.
        mood, _ = self.stab.stabilize("مبسوط وبيرقص", "تمام")
        self.assertEqual(mood, "صاحي")
        mood, _ = self.stab.stabilize("مبسوط وبيرقص", "تمام")
        self.assertEqual(mood, "مبسوط وبيرقص")

    def test_critical_temp_bypasses_hold_immediately(self):
        self.stab.stabilize("صاحي", "عادي")
        hot = _status_for_mood(temp_c=95.0, temp_available=True)
        mood, _ = self.stab.stabilize("قلقان", "حرارة 95", status=hot)
        self.assertEqual(mood, "قلقان")

    def test_tone_drives_mood_within_healthy_range(self):
        self.stab.stabilize("صاحي", "عادي")
        self.stab.stabilize("صاحي", "عادي",
                            tone_mood="مبسوط وبيرقص", tone_reason="شكر")
        self.now[0] += 200.0
        mood, reason = self.stab.stabilize(
            "صاحي", "عادي", tone_mood="مبسوط وبيرقص", tone_reason="شكر")
        self.assertEqual(mood, "مبسوط وبيرقص")
        self.assertIn("شكر", reason)

    def test_device_problem_beats_tone(self):
        self.stab.stabilize("صاحي", "عادي")
        self.stab.stabilize("قلقان", "حرارة عالية",
                            tone_mood="مبسوط وبيرقص", tone_reason="شكر")
        self.now[0] += 200.0
        mood, _ = self.stab.stabilize(
            "قلقان", "حرارة عالية",
            tone_mood="مبسوط وبيرقص", tone_reason="شكر")
        self.assertEqual(mood, "قلقان")


class TestMonitorToneIntegration(unittest.TestCase):
    def test_noted_tone_steers_overview_mood(self):
        mon = SystemMonitor(sample_fn=lambda: _reading())
        mon.note_user_message("شكرا يا لونا، برافو")
        overview = mon.get_device_overview()
        self.assertEqual(overview["mood"], "مبسوط وبيرقص")

    def test_neutral_message_changes_nothing(self):
        mon = SystemMonitor(sample_fn=lambda: _reading())
        mon.note_user_message("افتح VS Code")
        overview = mon.get_device_overview()
        self.assertEqual(overview["mood"], "صاحي")

    def test_overview_mood_holds_across_flickering_readings(self):
        now = [1000.0]
        readings = [_reading(),
                    _reading(battery={"percent": 10, "charging": False})]
        mon = SystemMonitor(sample_fn=lambda: readings[0],
                            clock=lambda: now[0])
        self.assertEqual(mon.get_device_overview()["mood"], "صاحي")
        # Battery collapses -> raw mood is "تعبان", but the hold keeps
        # the displayed mood until confirmed + hold elapsed.
        mon.sample_fn = lambda: readings[1]
        self.assertEqual(mon.get_device_overview()["mood"], "صاحي")
        mon.sample_fn = lambda: readings[1]
        self.assertEqual(mon.get_device_overview()["mood"], "صاحي")
        now[0] += 200.0
        self.assertEqual(mon.get_device_overview()["mood"], "تعبان")

    def test_raw_mapping_unchanged(self):
        mood, _ = device_state_to_mood(_status_for_mood(temp_c=92.0))
        self.assertEqual(mood, "قلقان")


class TestTypingIndicatorJS(unittest.TestCase):
    def test_bubble_stays_up_through_pending_poll(self):
        src = _fn_source(JS, "async function sendText(text, _bubbleShown)")
        # hideTyping() must NOT appear before the pending poll starts.
        self.assertIn("waitForChatResult", src)
        before_poll = src[:src.index("waitForChatResult")]
        self.assertNotIn("hideTyping()", before_poll,
                         "bubble is hidden as soon as chat() returns "
                         "pending - it must stay up through the poll")
        # ...and IS hidden once the reply arrives (finally).
        self.assertIn("finally", src)
        after_finally = src[src.index("finally"):]
        self.assertIn("hideTyping()", after_finally)

    def test_send_text_enters_and_exits_thinking_mood(self):
        src = _fn_source(JS, "async function sendText(text, _bubbleShown)")
        self.assertIn("beginThinking()", src)
        self.assertIn("endThinking()", src)

    def test_show_typing_is_idempotent(self):
        src = _fn_source(JS, "function showTyping()")
        self.assertIn('if ($("typing-indicator")) return;', src)

    def test_voice_chat_keeps_bubble_through_pending_poll(self):
        src = _fn_source(JS, "async function voiceChat()")
        self.assertIn("showTyping()", src)
        self.assertIn("beginThinking()", src)
        self.assertIn("hideTyping()", src)
        self.assertIn("endThinking()", src)

    def test_thinking_override_helpers(self):
        self.assertIn("function beginThinking()", JS)
        self.assertIn('setMood("بيفكر")', JS)
        self.assertIn("function endThinking()", JS)
        self.assertIn("thinkingPrevMood", JS)

    def test_mood_poll_never_clobbers_thinking(self):
        src = _fn_source(JS, "function startMoodPolling()")
        self.assertIn("thinkingActive) return;", src)
        # v1.6.2 (B12): the guard shares its line with the in-flight
        # guard — the poll still never clobbers an active "بيفكر".
        self.assertIn("if (moodPollInFlight || thinkingActive) return;", src)

    def test_robot_tap_keeps_thinking_mood(self):
        src = _fn_source(JS, "function robotReact()")
        self.assertIn("thinkingActive", src)


class TestTypingAnimationCSS(unittest.TestCase):
    def test_dots_have_blink_animation(self):
        self.assertIn(".msg.typing span", CSS)
        self.assertIn("animation: blink 1.2s ease-in-out infinite", CSS)
        self.assertIn("@keyframes blink", CSS)
        # The bounce: dots rise and brighten mid-cycle (WhatsApp-style).
        self.assertIn("translateY(-3px)", CSS)
        # Staggered delays so the three dots bounce in sequence.
        self.assertIn("animation-delay: 0.2s", CSS)
        self.assertIn("animation-delay: 0.4s", CSS)

    def test_no_rule_kills_the_typing_animation(self):
        self.assertNotIn(".msg.typing span { animation: none", CSS)
        self.assertNotIn(".msg.typing { animation: none", CSS)


class TestThinkingMoodBackendContract(unittest.TestCase):
    def test_get_mood_returns_thinking_while_chat_pending(self):
        get_mood = _py_fn_source(MAIN, "def get_mood(self):")
        self.assertIn('"بيفكر"', get_mood)
        self.assertIn("_chats", get_mood)
        # The override bypasses the stabilizer: no stabilize call on
        # the thinking path, so hold/confirm counters keep running.
        thinking_branch = get_mood[:get_mood.index("get_device_overview")]
        self.assertNotIn("stabilize(", thinking_branch)
        self.assertNotIn("note_user_message(", thinking_branch)

    def test_chat_notes_tone_and_pushes_thinking_to_island(self):
        chat = _py_fn_source(MAIN, "def chat(self, text):")
        self.assertIn("note_user_message(text)", chat)
        self.assertIn('setMood(\\"بيفكر\\")', chat)

    def test_chat_result_restores_island_mood(self):
        result = _py_fn_source(MAIN, "def chat_result(self, chat_id):")
        self.assertIn("island_window", result)
        self.assertIn("get_mood()", result)


if __name__ == "__main__":
    unittest.main()
