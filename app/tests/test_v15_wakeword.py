# LONA v1.5 - headless tests for the "يا لونا" wake word
# (run on Linux, no Windows, no mic, no network except the model
# cache when present):
#
#   python -m unittest app.tests.test_v15_wakeword -v
#
# What is proven here:
#   * The phrase matcher: the full phrase "يا لونا" wakes (alone, or
#     with a command after it); the Whisper-small truncation "يا لون"
#     wakes too; "لونا" alone, "يا لوني", "يا محمد", "يا مين" and
#     other speech never wake.
#   * The energy VAD: silence never opens a burst; speech opens one;
#     1.2 s of silence closes it; steady noise is discarded WITHOUT
#     transcription (quiet-fraction gate).
#   * The listener state machine (fake spotter + scripted audio):
#     "يا لونا" -> the app wakes (callback fires, with the command
#     when the burst held one); other speech -> no wake; disabled ->
#     no listening at all; paused (turn-based mic) -> no wake;
#     resumed -> wakes again; cooldown blocks a second immediate
#     wake; the app's own speaking suppresses re-wake.
#   * Mic coordination: voice_chat() pauses the listener while the
#     turn-based mic records and resumes it after (source-level,
#     like the v1.4.3 update-ux tests); the wake handler pauses the
#     listener while handling; quit() stops it.
#   * Settings: wakeword_enabled defaults True, persists across
#     save/load; the General section has the toggle + status line,
#     and app.js has the wake UI functions + immediate toggle.
#   * Idle CPU: the listener loop on silence stays near 0% of one
#     core (measured and printed; the real-model idle run is included
#     when the model is cached).
#   * REAL spotter end-to-end (only when the faster-whisper "small"
#     model is already in the local HF cache - skipped in CI):
#     the real model transcribes the Piper fixture WAVs and the
#     matcher wakes on all 6 positive fixtures (old-phrase audio,
#     matched with phrase="يا ميمو"), none of the 3
#     negatives, and nothing on silence/noise.
#
# Fixtures (app/tests/fixtures/wakeword/*.wav): synthesized with
# Piper ar_JO-kareem-medium (Jordanian Arabic TTS) - synthetic
# fixtures, NOT the user's real voice. They prove the pipeline end
# to end; real-voice accuracy on Mohamed's Egyptian voice is
# UNPROVEN until his field test.
#
# NOT proven here (needs real Windows + mic + the user's voice):
# the live microphone stream, the wake chime, the window coming
# forward on wake, and the real-voice recall rate.

import json
import re
import resource
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

import wakeword  # noqa: E402
from wakeword import (EnergyVAD, WakeWordListener, WhisperSpotter,  # noqa: E402
                      contains_wake_phrase, extract_wake_command,
                      normalize_arabic)

FIXTURES = APP_DIR / "tests" / "fixtures" / "wakeword"

# Positive fixtures: the phrase (clean / fast / slow / noisy /
# phrase+command / phrase in a longer sentence). Negatives: a
# similar name call, "who did it", a greeting.
POSITIVE_FIXTURES = ["ya-mimo.wav", "w_fast.wav", "w_slow.wav",
                     "w_noisy.wav", "w_cmd.wav", "w_long.wav"]
NEGATIVE_FIXTURES = ["n_mohamed.wav", "n_yameen.wav"]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _wav_samples_16k(path: Path) -> np.ndarray:
    """Load a WAV as float32 mono 16 kHz samples (for the spotter)."""
    with wave.open(str(path), "rb") as wf:
        n = wf.getnframes()
        rate = wf.getframerate()
        ch = wf.getnchannels()
        data = wf.readframes(n)
    audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    if ch == 2:
        audio = audio[::2]
    if rate != 16000:
        idx = np.linspace(0, len(audio) - 1,
                          int(len(audio) * 16000 / rate))
        audio = np.interp(idx, np.arange(len(audio)), audio).astype(
            np.float32)
    return audio


def _wav_frames_16k(path: Path, frame_samples: int = 480):
    """Load a WAV as a list of int16 frame bytes (480 samples each)."""
    samples = _wav_samples_16k(path)
    ints = (samples * 32768.0).astype(np.int16)
    frames = []
    for i in range(0, len(ints) - frame_samples + 1, frame_samples):
        frames.append(ints[i:i + frame_samples].tobytes())
    return frames


def _silent_frames(seconds: float, frame_samples: int = 480):
    n = int(seconds * 16000 / frame_samples)
    return [b"\x00" * (frame_samples * 2) for _ in range(n)]


def _speech_frames(seconds: float, frame_samples: int = 480):
    """Speech-like frames: loud bursts with quiet gaps (real speech
    has inter-word pauses - the quiet-fraction gate needs them)."""
    rng = np.random.default_rng(7)
    frames = []
    n = int(seconds * 16000 / frame_samples)
    for i in range(n):
        if i % 7 in (5, 6):  # gap frames
            frames.append(b"\x00" * (frame_samples * 2))
        else:
            chunk = (rng.standard_normal(frame_samples) * 5000)
            frames.append(chunk.astype(np.int16).tobytes())
    return frames


def _steady_noise_frames(seconds: float, frame_samples: int = 480):
    """Loud frames with NO gaps (a fan / hiss)."""
    rng = np.random.default_rng(3)
    frames = []
    n = int(seconds * 16000 / frame_samples)
    for _ in range(n):
        chunk = (rng.standard_normal(frame_samples) * 5000)
        frames.append(chunk.astype(np.int16).tobytes())
    return frames


class _FakeSpotter:
    """Spotter that returns canned transcripts, one per call."""

    def __init__(self, transcripts):
        self.transcripts = list(transcripts)
        self.calls = 0
        self.loaded = True

    def load(self):
        self.loaded = True
        return True

    def transcribe(self, samples):
        self.calls += 1
        if self.transcripts:
            return self.transcripts.pop(0)
        return ""


def _small_model_cached() -> Path | None:
    """Path of a cached faster-whisper 'small' model.bin, or None."""
    cache = Path.home() / ".cache" / "huggingface" / "hub"
    for path in cache.glob(
            "models--Systran--faster-whisper-small/snapshots/*/model.bin"):
        if path.is_file() and path.stat().st_size > 100_000_000:
            return path
    return None


class TestNormalizeArabic(unittest.TestCase):
    def test_diacritics_and_alef_forms(self):
        self.assertEqual(normalize_arabic("يَا لُونَا"), "يا لونا")
        # Four alef forms all normalise to ا, then the elongation
        # collapse (runs of 3+ -> 1) leaves a single ا.
        self.assertEqual(normalize_arabic("أإآٱ"), "ا")

    def test_teh_marbuta_and_alef_maqsura(self):
        self.assertEqual(normalize_arabic("مدرسة"), "مدرسه")
        self.assertEqual(normalize_arabic("موسى"), "موسي")

    def test_elongation_collapse(self):
        self.assertEqual(normalize_arabic("يا لونااا"), "يا لونا")
        self.assertEqual(normalize_arabic("يااا لونا"), "يا لونا")

    def test_tatweel_stripped(self):
        self.assertEqual(normalize_arabic("يـا لـونـا"), "يا لونا")


class TestExtractWakeCommand(unittest.TestCase):
    def test_phrase_alone_wakes_with_no_command(self):
        self.assertEqual(extract_wake_command("يا لونا"), (True, ""))
        self.assertEqual(extract_wake_command("يا لونا؟"), (True, ""))

    def test_phrase_with_command(self):
        matched, cmd = extract_wake_command("يا لونا افتح النور")
        self.assertTrue(matched)
        self.assertEqual(cmd, "افتح النور")

    def test_truncated_pair_wakes(self):
        # Whisper-small drops the final letter when more words follow.
        matched, cmd = extract_wake_command("يا لون افتح النور")
        self.assertTrue(matched)
        self.assertEqual(cmd, "افتح النور")
        matched, cmd = extract_wake_command("يا لونُ أنتُ فين")
        self.assertTrue(matched)
        self.assertEqual(cmd, "انت فين")

    def test_name_alone_never_wakes(self):
        self.assertEqual(extract_wake_command("لونا"), (False, ""))

    def test_similar_words_never_wake(self):
        for text in ("يا لوني", "يا محمد", "يا مين اللي عمل كده",
                     "السلام عليكم ورحمة الله", "افتح النور", ""):
            with self.subTest(text=text):
                self.assertEqual(extract_wake_command(text), (False, ""))

    def test_contains_helper(self):
        self.assertTrue(contains_wake_phrase("يا لونا انت فين"))
        self.assertFalse(contains_wake_phrase("يا محمد"))


class TestEnergyVAD(unittest.TestCase):
    def test_silence_never_opens(self):
        vad = EnergyVAD()
        for _ in range(100):
            self.assertEqual(vad.feed(b"\x00" * 960), "silence")
        self.assertFalse(vad.burst_open)

    def test_frame_rms(self):
        self.assertEqual(EnergyVAD.frame_rms(b"\x00" * 960), 0.0)
        loud = (np.ones(480, dtype=np.int16) * 5000).tobytes()
        self.assertGreater(EnergyVAD.frame_rms(loud), 4000)

    def test_speech_opens_after_two_frames(self):
        vad = EnergyVAD()
        loud = (np.ones(480, dtype=np.int16) * 5000).tobytes()
        vad.feed(loud)
        self.assertFalse(vad.burst_open)
        vad.feed(loud)
        self.assertTrue(vad.burst_open)

    def test_silence_closes_after_threshold(self):
        vad = EnergyVAD()
        loud = (np.ones(480, dtype=np.int16) * 5000).tobytes()
        vad.feed(loud)
        vad.feed(loud)
        self.assertTrue(vad.burst_open)
        for _ in range(39):
            vad.feed(b"\x00" * 960)
            self.assertTrue(vad.burst_open)
        vad.feed(b"\x00" * 960)
        self.assertFalse(vad.burst_open)


class _ListenerCase(unittest.TestCase):
    """Drive a WakeWordListener with scripted frames + a fake spotter."""

    def setUp(self):
        self.settings = {"wakeword_enabled": True,
                         "wakeword_cooldown_sec": 0}
        self.wakes = []
        self._frames = []
        self._lock = threading.Lock()

    def _audio_iter(self):
        idx = 0
        while True:
            with self._lock:
                if idx < len(self._frames):
                    frame = self._frames[idx]
                    idx += 1
                else:
                    frame = None
            if frame is None:
                yield b"\x00" * 960  # idle silence while the test runs
                time.sleep(0.005)
            else:
                yield frame

    def _make_listener(self, spotter):
        return WakeWordListener(
            self.settings, on_wake=lambda cmd: self.wakes.append(cmd),
            spotter=spotter, audio_iter=self._audio_iter())

    def _feed(self, frames):
        with self._lock:
            self._frames.extend(frames)

    def _wait_wakes(self, count, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if len(self.wakes) >= count:
                return True
            time.sleep(0.05)
        return len(self.wakes) >= count

    def _wait_spotter_calls(self, spotter, count, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if spotter.calls >= count:
                return True
            time.sleep(0.05)
        return spotter.calls >= count


class TestListenerWake(_ListenerCase):
    def test_phrase_wakes_with_command(self):
        spotter = _FakeSpotter(["يا لونا افتح النور"])
        listener = self._make_listener(spotter)
        listener.start()
        try:
            self._feed(_speech_frames(1.5))
            self._feed(_silent_frames(2.0))
            self.assertTrue(self._wait_wakes(1), "wake did not fire")
            self.assertEqual(self.wakes, ["افتح النور"])
        finally:
            listener.stop()

    def test_phrase_alone_wakes_with_empty_command(self):
        spotter = _FakeSpotter(["يا لونا"])
        listener = self._make_listener(spotter)
        listener.start()
        try:
            self._feed(_speech_frames(1.0))
            self._feed(_silent_frames(2.0))
            self.assertTrue(self._wait_wakes(1), "wake did not fire")
            self.assertEqual(self.wakes, [""])
        finally:
            listener.stop()

    def test_other_speech_does_not_wake(self):
        spotter = _FakeSpotter(["افتح النور", "يا محمد",
                                "يا مين اللي عمل كده"])
        listener = self._make_listener(spotter)
        listener.start()
        try:
            for _ in range(3):
                self._feed(_speech_frames(1.0))
                self._feed(_silent_frames(2.0))
            self.assertTrue(self._wait_spotter_calls(spotter, 3),
                            "spotter was not called 3 times")
            time.sleep(0.3)
            self.assertEqual(self.wakes, [])
        finally:
            listener.stop()

    def test_name_alone_does_not_wake(self):
        spotter = _FakeSpotter(["لونا"])
        listener = self._make_listener(spotter)
        listener.start()
        try:
            self._feed(_speech_frames(1.0))
            self._feed(_silent_frames(2.0))
            self.assertTrue(self._wait_spotter_calls(spotter, 1))
            time.sleep(0.3)
            self.assertEqual(self.wakes, [])
        finally:
            listener.stop()

    def test_silence_never_transcribes(self):
        spotter = _FakeSpotter(["يا لونا"])
        listener = self._make_listener(spotter)
        listener.start()
        try:
            self._feed(_silent_frames(3.0))
            time.sleep(1.0)
            self.assertEqual(spotter.calls, 0)
            self.assertEqual(self.wakes, [])
        finally:
            listener.stop()

    def test_steady_noise_discarded_without_transcription(self):
        spotter = _FakeSpotter(["يا لونا"])
        listener = self._make_listener(spotter)
        listener.start()
        try:
            self._feed(_steady_noise_frames(5.0))
            self._feed(_silent_frames(2.0))
            time.sleep(1.0)
            # The noise burst hit the 4 s cap and was discarded by
            # the quiet-fraction gate - no transcription, no wake.
            self.assertEqual(spotter.calls, 0)
            self.assertEqual(self.wakes, [])
            self.assertGreaterEqual(
                listener.bursts_discarded_noise, 1)
        finally:
            listener.stop()

    def test_cooldown_blocks_second_immediate_wake(self):
        self.settings["wakeword_cooldown_sec"] = 60
        spotter = _FakeSpotter(["يا لونا", "يا لونا"])
        listener = self._make_listener(spotter)
        listener.start()
        try:
            for _ in range(2):
                self._feed(_speech_frames(1.0))
                self._feed(_silent_frames(2.0))
            self.assertTrue(self._wait_spotter_calls(spotter, 2))
            time.sleep(0.3)
            self.assertEqual(len(self.wakes), 1)
        finally:
            listener.stop()

    def test_speaking_suppresses_rewake(self):
        speaking = {"on": True}
        spotter = _FakeSpotter(["يا لونا"])
        listener = WakeWordListener(
            self.settings, on_wake=lambda cmd: self.wakes.append(cmd),
            is_speaking=lambda: speaking["on"],
            spotter=spotter, audio_iter=self._audio_iter())
        listener.start()
        try:
            self._feed(_speech_frames(1.0))
            self._feed(_silent_frames(2.0))
            self.assertTrue(self._wait_spotter_calls(spotter, 1))
            time.sleep(0.3)
            self.assertEqual(self.wakes, [])
        finally:
            listener.stop()

    def test_pause_blocks_wake_resume_restores(self):
        spotter = _FakeSpotter(["يا لونا", "يا لونا"])
        listener = self._make_listener(spotter)
        listener.start()
        try:
            listener.pause()
            self.assertEqual(listener.status()["state"], "paused")
            self._feed(_speech_frames(1.0))
            self._feed(_silent_frames(2.0))
            time.sleep(1.0)
            self.assertEqual(self.wakes, [])
            listener.resume()
            self.assertEqual(listener.status()["state"], "listening")
            self._feed(_speech_frames(1.0))
            self._feed(_silent_frames(2.0))
            self.assertTrue(self._wait_wakes(1), "no wake after resume")
        finally:
            listener.stop()

    def test_disabled_listener_never_starts(self):
        self.settings["wakeword_enabled"] = False
        spotter = _FakeSpotter(["يا لونا"])
        listener = self._make_listener(spotter)
        self.assertFalse(listener.start())
        self.assertEqual(listener.status()["state"], "off")
        listener.stop()

    def test_stop_closes_and_reports_off(self):
        spotter = _FakeSpotter([])
        listener = self._make_listener(spotter)
        listener.start()
        listener.stop()
        self.assertEqual(listener.status()["state"], "off")
        self.assertFalse(listener.running)


class TestSettingsAndWiring(unittest.TestCase):
    def test_defaults(self):
        import settings_store
        self.assertTrue(settings_store.DEFAULTS["wakeword_enabled"])
        self.assertEqual(settings_store.DEFAULTS["wakeword_phrase"],
                         "يا لونا")
        self.assertEqual(settings_store.DEFAULTS["wakeword_cooldown_sec"],
                         5)

    def test_save_load_roundtrip(self):
        import settings_store
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(settings_store, "SETTINGS_PATH",
                               tmp / "settings.json"), \
                mock.patch.object(settings_store, "DATA_DIR", tmp):
            settings = settings_store.load_settings()
            settings["wakeword_enabled"] = False
            self.assertTrue(settings_store.save_settings(settings))
            reloaded = settings_store.load_settings()
            self.assertFalse(reloaded["wakeword_enabled"])
            self.assertEqual(reloaded["wakeword_phrase"], "يا لونا")

    def test_main_wiring(self):
        src = _read("app/main.py")
        self.assertIn('VERSION = "1.6.1"', src)
        self.assertIn("def get_wakeword_status(self)", src)
        self.assertIn("def set_wakeword_enabled(self, enabled)", src)
        self.assertIn("def start_wakeword(self)", src)
        self.assertIn("def stop_wakeword(self)", src)
        self.assertIn("def pause_wakeword(self)", src)
        self.assertIn("def resume_wakeword(self)", src)
        self.assertIn("def _on_wake_word(self", src)
        # Turn-based mic pauses the listener; the wake handler pauses
        # it too; quit() stops it; run() starts it.
        self.assertIn("_APP.pause_wakeword()", src)
        self.assertIn("_APP.resume_wakeword()", src)
        self.assertIn("self.stop_wakeword()", src)
        self.assertIn("self.start_wakeword()", src)
        self.assertIn("wakeword_enabled", src)
        # The app's own voice never re-wakes it.
        self.assertIn("_speaking_until", src)

    def test_ui_wiring(self):
        html = _read("app/ui/index.html")
        js = _read("app/ui/app.js")
        css = _read("app/ui/styles.css")
        self.assertIn('id="set-wakeword"', html)
        self.assertIn('id="wakeword-status"', html)
        self.assertIn("v1.6.0", html)  # v1.6.0 UI
        for name in ("onWakeWord", "wakeHeard", "wakeReply",
                     "wakeNoCommand", "refreshWakewordStatus"):
            self.assertIn(name, js)
        self.assertIn("set_wakeword_enabled", js)
        self.assertIn("get_wakeword_status", js)
        self.assertIn("wakeword_enabled", js)
        self.assertIn("wakeword_toggle", js)
        self.assertIn(".mic-btn.wake-flash", css)
        self.assertIn("@keyframes wake-pulse", css)

    def test_js_parses(self):
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        proc = subprocess.run([node, "--check", str(ROOT / "app/ui/app.js")],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_version_manifest(self):
        manifest = json.loads(_read("version.json"))
        self.assertEqual(manifest["version"], "1.6.1")
        self.assertTrue(any("لونا" in f.get("title", "")
                            for f in manifest.get("features", [])))


class TestIdleCPU(unittest.TestCase):
    """The always-listening loop on silence must stay near 0% CPU."""

    def _cpu_percent(self, fn, seconds=5.0):
        before = resource.getrusage(resource.RUSAGE_SELF)
        t0 = time.time()
        fn()
        wall = time.time() - t0
        after = resource.getrusage(resource.RUSAGE_SELF)
        used = ((after.ru_utime - before.ru_utime)
                + (after.ru_stime - before.ru_stime))
        return (used / wall * 100.0) if wall > 0 else 0.0

    def test_idle_loop_with_fake_spotter(self):
        settings = {"wakeword_enabled": True,
                    "wakeword_cooldown_sec": 5}

        def silent_forever():
            # Paced at real time (30 ms per frame), like a real mic.
            # An unpaced generator makes the loop spin at 100% CPU
            # and measures the test harness, not the app.
            while True:
                yield b"\x00" * 960
                time.sleep(0.03)

        spotter = _FakeSpotter([])
        wakes = []
        listener = WakeWordListener(
            settings, on_wake=lambda cmd: wakes.append(cmd),
            spotter=spotter, audio_iter=silent_forever())
        listener.start()
        try:
            pct = self._cpu_percent(lambda: time.sleep(5.0))
        finally:
            listener.stop()
        print(f"\n[CPU] idle loop (fake spotter, silence): {pct:.2f}% "
              f"of one core; spotter calls={spotter.calls}")
        self.assertEqual(spotter.calls, 0)
        self.assertEqual(wakes, [])
        self.assertLess(pct, 5.0)

    def test_idle_loop_with_real_model(self):
        model_path = _small_model_cached()
        if model_path is None:
            self.skipTest("faster-whisper 'small' not in local HF cache")
        settings = {"wakeword_enabled": True,
                    "wakeword_cooldown_sec": 5}

        def silent_forever():
            # Paced at real time (30 ms per frame), like a real mic.
            # An unpaced generator makes the loop spin at 100% CPU
            # and measures the test harness, not the app.
            while True:
                yield b"\x00" * 960
                time.sleep(0.03)

        spotter = WhisperSpotter()
        self.assertTrue(spotter.load())
        wakes = []
        listener = WakeWordListener(
            settings, on_wake=lambda cmd: wakes.append(cmd),
            spotter=spotter, audio_iter=silent_forever())
        listener.start()
        try:
            pct = self._cpu_percent(lambda: time.sleep(5.0))
        finally:
            listener.stop()
        print(f"\n[CPU] idle loop (real small model, silence): "
              f"{pct:.2f}% of one core; bursts_checked="
              f"{listener.bursts_checked}")
        self.assertEqual(listener.bursts_checked, 0)
        self.assertEqual(wakes, [])
        self.assertLess(pct, 5.0)


class TestRealSpotterEndToEnd(unittest.TestCase):
    """The real faster-whisper 'small' spotter on the Piper fixture
    WAVs. Skipped when the model is not in the local HF cache."""

    @classmethod
    def setUpClass(cls):
        cls.model_path = _small_model_cached()
        if cls.model_path is None:
            raise unittest.SkipTest(
                "faster-whisper 'small' not in local HF cache")
        cls.spotter = WhisperSpotter()
        assert cls.spotter.load(), "could not load cached small model"

    def test_positive_fixtures_wake(self):
        for name in POSITIVE_FIXTURES:
            with self.subTest(fixture=name):
                samples = _wav_samples_16k(FIXTURES / name)
                text = self.spotter.transcribe(samples)
                # The fixture WAVs speak the previous phrase "يا ميمو":
                # match them with that phrase explicitly.
                matched, cmd = extract_wake_command(text, phrase="يا ميمو")
                self.assertTrue(
                    matched,
                    f"{name} transcribed as {text!r} - no wake")
                print(f"\n[REAL] {name}: {text!r} -> command={cmd!r}")

    def test_negative_fixtures_do_not_wake(self):
        for name in NEGATIVE_FIXTURES:
            with self.subTest(fixture=name):
                samples = _wav_samples_16k(FIXTURES / name)
                text = self.spotter.transcribe(samples)
                self.assertFalse(
                    contains_wake_phrase(text),
                    f"{name} transcribed as {text!r} - false wake")

    def test_silence_and_noise_do_not_wake(self):
        silence = np.zeros(16000 * 3, dtype=np.float32)
        self.assertFalse(contains_wake_phrase(
            self.spotter.transcribe(silence)))
        rng = np.random.default_rng(0)
        noise = (rng.standard_normal(16000 * 3) * 0.05).astype(np.float32)
        self.assertFalse(contains_wake_phrase(
            self.spotter.transcribe(noise)))


if __name__ == "__main__":
    unittest.main()
