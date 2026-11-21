# LONA v1.5 - Wake word "يا لونا" (always-listening, offline)
#
# The user calls LONA by name ("يا لونا") and it wakes up: shows the
# main window, plays a short chime, and listens for the command - no
# tap on the mic button, no opening the app by hand.
#
# The phrase DERIVES from the AI's name (settings.ai_name, default
# "LONA"): "يا {ai_name}" (see settings_store.derive_wake_phrase).
# Any user can rename the AI from Settings and the wake phrase follows
# the new name - the matcher takes the phrase as a parameter
# (extract_wake_command(transcript, phrase)); WAKE_PHRASE below is
# only the default.
#
# ENGINE CHOICE (measured 2026-11-07, before writing this module -
# the measurements below were for the previous phrase "يا ميمو"; the
# engine + fallback approach is unchanged for "يا لونا", and real-voice
# accuracy on Mohamed's Egyptian voice is UNPROVEN until his field
# test):
#   * Porcupine: OUT - no Arabic for self-service custom wake words,
#     and its free tier was discontinued 2026-06-30.
#   * Vosk (ar-mgb2-0.4): OUT - the model does not know the name
#     "لونا", grammar mode is unsupported by that model ("Runtime
#     graphs are not supported"), and it needs ~670 MB RAM resident.
#   * openWakeWord (train a custom model): OUT for v1.5 - a model
#     trained on synthetic-only positives gets ~8% recall on a real
#     voice; ~90% recall needs ~50 REAL recordings of the user saying
#     the phrase, which we do not have. (That is the v1.6 path: ask
#     Mohamed to record "يا لونا" ~50 times, then train.)
#   * faster-whisper "tiny"/"base": OUT - tiny heard the old phrase
#     exactly only on clean default-speed speech (1/5 variations),
#     base 1/6.
#   * faster-whisper "small" (int8): IN - heard the old phrase exactly
#     in 4/6 synthetic variations (fast/slow/noisy/clean) and, with the
#     truncated-pair fallback below, 6/6; 0 false positives on the
#     negatives ("يا محمد", "يا مين اللي عمل كده", "السلام عليكم"). No
#     new dependency (faster-whisper is already required), no key, no
#     account, fully offline. Cost: ~500 MB model download on first
#     enable + ~485 MB RAM resident (measured). Wake latency is
#     ~2-4 s (VAD silence gap + transcription) - slower than a
#     dedicated KWS engine (~0.2 s), and real-voice accuracy on
#     Mohamed's Egyptian voice is UNPROVEN until his field test.
#
# HOW IT WORKS:
#   * A sounddevice InputStream (16 kHz mono) feeds 30 ms frames into
#     a queue. An energy VAD (RMS) opens a burst on speech and closes
#     it after 1.2 s of silence (or a 4 s cap). Bursts shorter than
#     0.3 s, and bursts with almost no quiet frames (steady noise -
#     a fan/hiss has no inter-word gaps), are discarded WITHOUT
#     transcription, so the transcriber only ever runs on real speech.
#   * The burst is transcribed with faster-whisper "small" and the
#     transcript is matched against the wake phrase (see
#     extract_wake_command). If the burst also contains a command
#     ("يا لونا افتح النور"), the command rides along - no second
#     recording is needed. Otherwise the app records 5 s for the
#     command (the normal turn-based mic path).
#   * The listener PAUSES while the app itself is speaking (its own
#     TTS must never re-wake it), while the turn-based mic records,
#     and for a cooldown after each wake. The mic is fully closed
#     when the feature is toggled off in Settings.
#   * Privacy: NO transcript and NO audio is ever logged - a wake
#     event is logged as "Wake word detected (#N, command=yes/no)"
#     only. Audio is never written to disk, never sent anywhere.

import json
import re
import threading
import time
import queue
from pathlib import Path

import numpy as np

from logger import error, info, warn

WAKE_PHRASE = "يا لونا"  # default only - the live phrase derives from
# settings.ai_name ("يا {ai_name}"); see settings_store.derive_wake_phrase.
MODEL_SIZE = "small"          # faster-whisper model (see header)
SAMPLE_RATE = 16000
FRAME_SAMPLES = 480           # 30 ms @ 16 kHz
# VAD: a frame is "speech" above this int16 RMS. Measured: Piper
# speech median RMS ~4700-6900 (p10 ~130-730); silence 0; steady
# white noise at 1% amplitude ~330. 500 keeps speech (median 10x
# above) while rejecting a quiet room + faint hiss.
VAD_RMS_THRESHOLD = 500
SPEECH_FRAMES_TO_OPEN = 2     # 60 ms of speech opens a burst
SILENCE_FRAMES_TO_CLOSE = 40  # 1.2 s of silence closes a burst
MIN_BURST_SEC = 0.3           # shorter bursts cannot hold the phrase
MAX_BURST_SEC = 4.0           # cap: transcribe, then keep listening
# A burst must contain at least this fraction of quiet frames
# (inter-word gaps). Steady noise has none - it is discarded
# without transcription, so a loud fan cannot burn CPU.
MIN_QUIET_FRACTION = 0.10
WAKE_COOLDOWN_SEC = 5         # default; settings: wakeword_cooldown_sec

# Arabic diacritics + tatweel, stripped for matching.
_AR_DIACRITICS = re.compile(r"[\u064b-\u0652\u0670\u0640]")


def normalize_arabic(text: str) -> str:
    """Normalize Arabic for phrase matching: strip diacritics/tatweel,
    unify alef forms, ة->ه, ى->ي, collapse whitespace and runs of the
    same letter (length >= 3, e.g. "لوناووو" -> "لونا")."""
    t = (text or "")
    t = _AR_DIACRITICS.sub("", t)
    t = (t.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
          .replace("ٱ", "ا").replace("ة", "ه").replace("ى", "ي"))
    # Collapse runs of 3+ identical chars to one (elongation).
    t = re.sub(r"(.)\1{2,}", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def extract_wake_command(transcript: str, phrase: str | None = None) -> tuple:
    """Match the wake phrase in a transcript.

    `phrase` is the live wake phrase ("يا {ai_name}", default
    WAKE_PHRASE = "يا لونا"). Returns (matched, command): matched is
    True when the full phrase is present, OR when the truncated pair
    (name minus its last letter, e.g. "يا لون") ends a word pair
    (Whisper-small drops the final letter when more words follow:
    "يا لون افتح النور" is really "يا لونا افتح النور"). The command
    is the text after the phrase, cleaned; "" means "wake, no command
    yet - record one". The bare name alone ("لونا"), "يا لوني",
    "يا محمد", "يا مين" never match.
    """
    norm = normalize_arabic(transcript)
    if not norm:
        return False, ""
    norm_phrase = normalize_arabic(phrase or WAKE_PHRASE)
    parts = norm_phrase.split()
    if not parts:
        return False, ""
    if len(parts) >= 2:
        prefix, name = parts[0], " ".join(parts[1:])
    else:
        prefix, name = "", parts[0]
    # Full phrase first: يا + optional space + name.
    prefix_part = (re.escape(prefix) + r"\s*") if prefix else ""
    m = re.search(prefix_part + re.escape(name), norm)
    if m:
        cmd = norm[m.end():].strip(" ،.,!؟?")
        return True, cmd
    # Truncated pair: name minus its last letter, as a complete pair
    # (space/end after) - only for Arabic-letter names.
    if len(name) > 1 and re.search(r"[\u0600-\u06ff]$", name):
        trunc = name[:-1]
        m = re.search(prefix_part + re.escape(trunc) + r"(?=\s|$)", norm)
        if m:
            cmd = norm[m.end():].strip(" ،.,!؟?")
            return True, cmd
    return False, ""


def contains_wake_phrase(transcript: str, phrase: str | None = None) -> bool:
    return extract_wake_command(transcript, phrase)[0]


class EnergyVAD:
    """RMS energy VAD over 30 ms frames. Pure + stateless per frame
    except the open/close smoothing: 2 speech frames open, 40
    silence frames close. feed() returns "speech"/"silence" for the
    frame and the current burst state ("open"/"closed")."""

    def __init__(self, threshold: int = VAD_RMS_THRESHOLD):
        self.threshold = threshold
        self._speech_run = 0
        self._silence_run = 0
        self.burst_open = False

    @staticmethod
    def frame_rms(frame_bytes: bytes) -> float:
        if not frame_bytes:
            return 0.0
        samples = np.frombuffer(frame_bytes, dtype=np.int16).astype(
            np.float64)
        if len(samples) == 0:
            return 0.0
        return float(np.sqrt((samples ** 2).mean()))

    def feed(self, frame_bytes: bytes) -> str:
        rms = self.frame_rms(frame_bytes)
        if rms >= self.threshold:
            self._speech_run += 1
            self._silence_run = 0
            if not self.burst_open and \
                    self._speech_run >= SPEECH_FRAMES_TO_OPEN:
                self.burst_open = True
            return "speech"
        self._silence_run += 1
        self._speech_run = 0
        if self.burst_open and \
                self._silence_run >= SILENCE_FRAMES_TO_CLOSE:
            self.burst_open = False
        return "silence"

    def reset(self):
        self._speech_run = 0
        self._silence_run = 0
        self.burst_open = False


class WhisperSpotter:
    """faster-whisper "small" (int8) as a keyword spotter: transcribe
    a speech burst, return the transcript. Loaded lazily (the model
    downloads ~500 MB on first use, from the HuggingFace cache).
    The import is lazy so importing wakeword.py never fails on a
    machine without faster-whisper - the listener degrades honestly
    instead (state "error", exact reason)."""

    def __init__(self, model_size: str = MODEL_SIZE):
        self.model_size = model_size
        self._model = None
        self.loaded = False

    def load(self) -> bool:
        """Load the model (downloads on first use). Returns True on
        success; on failure returns False and the caller logs the
        exact reason."""
        if self.loaded:
            return True
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            error("wakeword", f"faster-whisper unavailable: {exc}")
            return False
        try:
            info("wakeword", f"Loading Whisper '{self.model_size}' "
                             "(first enable downloads ~500 MB, once)")
            self._model = WhisperModel(self.model_size, device="cpu",
                                       compute_type="int8")
            self.loaded = True
            info("wakeword", f"Whisper '{self.model_size}' loaded")
            return True
        except Exception as exc:
            error("wakeword", f"Whisper model load failed: {exc}")
            self._model = None
            return False

    def transcribe(self, samples_float32) -> str:
        """Transcribe raw float32 mono 16 kHz samples (the burst).
        Returns the transcript ("" on failure - never raises)."""
        if not self.loaded or self._model is None:
            return ""
        try:
            # Raw samples (like hear.py) - skips the av/PyAV decoder
            # path, which is broken on some av versions.
            segments, _info = self._model.transcribe(
                samples_float32, language="ar")
            return "".join(s.text for s in segments).strip()
        except Exception as exc:
            error("wakeword", f"Spotter transcription failed: {exc}")
            return ""


class WakeWordListener:
    """Always-listening wake-word listener.

    settings: the app settings dict (wakeword_enabled,
    wakeword_cooldown_sec). on_wake(command: str) is called on the
    worker thread when the phrase is detected (command is "" when the
    burst held only the phrase). is_speaking: optional callable
    returning True while the app is playing a reply - detections then
    are ignored (the mic must never hear its own TTS and re-wake).
    spotter / audio_iter are injectable for headless tests: spotter
    is a WhisperSpotter-like (transcribe(samples)->str), audio_iter
    yields int16 frame bytes (480 samples each); when audio_iter is
    None the real microphone (sounddevice) is used.
    """

    def __init__(self, settings, on_wake, is_speaking=None,
                 spotter=None, audio_iter=None):
        self.settings = settings
        self.on_wake = on_wake
        self.is_speaking = is_speaking or (lambda: False)
        self._spotter = spotter
        self._audio_iter = audio_iter
        self._stop = threading.Event()
        self._paused = threading.Event()   # set = paused
        self._thread = None
        self._loader_thread = None
        self._stream = None
        self._queue: queue.Queue = queue.Queue(maxsize=200)
        self._state = "off"               # off|loading-model|listening|paused|error
        self._detail = ""
        self._state_lock = threading.Lock()
        self.wake_count = 0
        self.last_wake_at = ""
        self._last_wake_time = 0.0
        self.bursts_checked = 0
        self.bursts_discarded_noise = 0

    # -- state ---------------------------------------------------------
    def _set_state(self, state: str, detail: str = ""):
        with self._state_lock:
            self._state = state
            self._detail = detail

    def status(self) -> dict:
        with self._state_lock:
            state, detail = self._state, self._detail
        return {
            "enabled": bool(self.settings.get("wakeword_enabled", True)),
            "state": state,
            "detail": detail,
            "model_ready": bool(self._spotter is not None and
                                getattr(self._spotter, "loaded", False)),
            # The LIVE phrase derives from the AI's name (settings) -
            # WAKE_PHRASE is only the default.
            "phrase": str(self.settings.get("wakeword_phrase")
                          or WAKE_PHRASE),
            "wake_count": self.wake_count,
            "last_wake_at": self.last_wake_at,
            "bursts_checked": self.bursts_checked,
            "bursts_discarded_noise": self.bursts_discarded_noise,
        }

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._state in ("loading-model", "listening", "paused")

    # -- lifecycle -----------------------------------------------------
    def start(self) -> bool:
        """Start listening (loads the model in the background first).
        Returns False immediately only when disabled; model failures
        surface later via status() (state "error")."""
        if not self.settings.get("wakeword_enabled", True):
            info("wakeword", "Wake word disabled in settings - not starting")
            return False
        if self.running:
            return True
        self._stop.clear()
        self._paused.clear()
        self._set_state("loading-model",
                        "loading the spotter model (first enable "
                        "downloads ~500 MB, once)")
        self._loader_thread = threading.Thread(
            target=self._load_and_run, daemon=True,
            name="memo-wakeword-load")
        self._loader_thread.start()
        return True

    def _load_and_run(self):
        try:
            if self._spotter is None:
                self._spotter = WhisperSpotter()
            if not self._spotter.load():
                self._set_state(
                    "error",
                    "could not load the spotter model - see the log "
                    "for the exact reason (check the network / disk "
                    "space, then toggle the feature off and on)")
                return
            if self._stop.is_set():
                self._set_state("off")
                return
            if self._paused.is_set():
                self._set_state("paused")
            else:
                self._set_state("listening")
            info("wakeword", "Listening for "
                             f"'{self.settings.get('wakeword_phrase') or WAKE_PHRASE}' (always-on)")
            self._run_loop()
        except Exception as exc:
            error("wakeword", f"Wake-word listener failed: {exc}")
            self._set_state("error", str(exc))

    def _run_loop(self):
        """Consume audio frames, VAD-gate bursts, transcribe, match.
        Runs on the loader thread after the model is ready.

        Silence frames are held in a pending buffer and only merged
        into the burst if speech resumes (real inter-word gaps). The
        trailing silence run that CLOSES a burst is never part of it,
        so the quiet-fraction gate measures genuine gaps inside the
        speech (continuous loud noise has none -> discarded without
        transcription). A burst only finalises once it actually
        OPENED: a lone speech blip followed by silence is dropped,
        not finalised as a 1-frame burst (that bug used to reset the
        VAD after every frame, so a burst could never open at all).
        """
        vad = EnergyVAD()
        burst_frames: list = []
        pending_quiet: list = []
        burst_quiet = 0
        burst_total = 0
        burst_opened = False
        stream = None
        if self._audio_iter is None:
            # Reuse a stream resume() already opened (pause during
            # model load), instead of opening a second one.
            stream = self._stream
            if stream is None:
                stream = self._open_stream()
            if stream is None:
                self._set_state(
                    "error",
                    "could not open the microphone - check it is "
                    "connected and allowed for apps")
                return
            self._stream = stream
        try:
            while not self._stop.is_set():
                if self._paused.is_set():
                    self._drain_queue()
                    burst_frames, pending_quiet = [], []
                    burst_quiet, burst_total = 0, 0
                    burst_opened = False
                    vad.reset()
                    time.sleep(0.05)
                    continue
                frame = self._next_frame()
                if frame is None:
                    if self._audio_iter is not None:
                        break  # test source exhausted
                    time.sleep(0.01)
                    continue
                kind = vad.feed(frame)
                if vad.burst_open:
                    burst_opened = True
                if kind == "speech":
                    # Gaps since the last speech frame are real gaps
                    # inside the utterance - merge them into the burst.
                    if burst_frames and pending_quiet:
                        burst_frames.extend(pending_quiet)
                        burst_total += len(pending_quiet)
                        burst_quiet += len(pending_quiet)
                    pending_quiet = []
                    burst_frames.append(frame)
                    burst_total += 1
                else:
                    # Hold the silence: it only joins the burst if
                    # speech resumes. The closing run stays out.
                    pending_quiet.append(frame)
                # Burst complete: silence closed it, or it hit the cap.
                burst_sec = len(burst_frames) * FRAME_SAMPLES / SAMPLE_RATE
                if burst_frames and burst_opened and (
                    not vad.burst_open or burst_sec >= MAX_BURST_SEC
                ):
                    self._handle_burst(burst_frames, burst_quiet,
                                       burst_total)
                    burst_frames, pending_quiet = [], []
                    burst_quiet, burst_total = 0, 0
                    burst_opened = False
                    vad.reset()
                elif burst_frames and not burst_opened and (
                    len(pending_quiet) >= SILENCE_FRAMES_TO_CLOSE
                ):
                    # Lone blip(s) that never became a burst - drop them.
                    burst_frames, pending_quiet = [], []
                    burst_quiet, burst_total = 0, 0
                    vad.reset()
        finally:
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
                self._stream = None
            if not self._stop.is_set():
                self._set_state("off")

    def _handle_burst(self, frames, quiet_count, total_count):
        """Decide whether a finished burst is worth transcribing, and
        fire the wake callback on a phrase match. No transcript is
        ever logged."""
        self.bursts_checked += 1
        burst_sec = len(frames) * FRAME_SAMPLES / SAMPLE_RATE
        if burst_sec < MIN_BURST_SEC:
            return
        quiet_frac = (quiet_count / total_count) if total_count else 0.0
        if quiet_frac < MIN_QUIET_FRACTION:
            # Steady noise (fan/hiss): no inter-word gaps. Discard
            # WITHOUT transcription - it cannot hold the phrase and
            # transcribing it every 4 s would burn the CPU.
            self.bursts_discarded_noise += 1
            return
        if self._spotter is None:
            return
        samples = np.frombuffer(b"".join(frames), dtype=np.int16)
        audio = samples.astype(np.float32) / 32768.0
        text = self._spotter.transcribe(audio)
        # The phrase is read from settings on every burst, so renaming
        # the AI (set_ai_name) takes effect without restarting the
        # listener.
        phrase = str(self.settings.get("wakeword_phrase") or WAKE_PHRASE)
        matched, command = extract_wake_command(text, phrase)
        if not matched:
            return
        now = time.time()
        cooldown = float(self.settings.get("wakeword_cooldown_sec",
                                           WAKE_COOLDOWN_SEC))
        if now - self._last_wake_time < cooldown:
            return
        if self.is_speaking():
            # The app is playing a reply - its own voice must never
            # re-wake it.
            return
        self._last_wake_time = now
        self.wake_count += 1
        from datetime import datetime
        self.last_wake_at = datetime.now().strftime("%H:%M:%S")
        info("wakeword", f"Wake word detected (#{self.wake_count}, "
                         f"command={'yes' if command else 'no'})")
        try:
            self.on_wake(command)
        except Exception as exc:
            error("wakeword", f"Wake callback failed: {exc}")

    # -- audio source --------------------------------------------------
    def _open_stream(self):
        """Open the microphone InputStream (lazy sounddevice import -
        the module must import fine without a mic). Returns the stream
        or None on failure (exact reason logged)."""
        try:
            import sounddevice as sd
        except Exception as exc:
            error("wakeword", f"sounddevice unavailable: {exc}")
            return None
        try:
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                blocksize=FRAME_SAMPLES,
                callback=self._audio_callback)
            stream.start()
            return stream
        except Exception as exc:
            error("wakeword", f"Could not open microphone stream: {exc}")
            return None

    def _audio_callback(self, indata, frames, time_info, status):
        try:
            self._queue.put_nowait(bytes(indata))
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(bytes(indata))
            except queue.Full:
                pass

    def _next_frame(self):
        if self._audio_iter is not None:
            try:
                return next(self._audio_iter)
            except StopIteration:
                return None
        try:
            return self._queue.get(timeout=0.1)
        except queue.Empty:
            return None

    def _drain_queue(self):
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    # -- pause / resume / stop ------------------------------------------
    def pause(self):
        """Pause listening (turn-based mic recording / the app speaking
        / handling a wake). The model stays loaded; the mic stream is
        closed so nothing else can fight over it."""
        if not self.running:
            return
        self._paused.set()
        self._set_state("paused")
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        info("wakeword", "Listening paused")

    def resume(self):
        """Resume listening after a pause. Reopens the mic stream."""
        if not self.running:
            return
        if not self._paused.is_set():
            return
        self._paused.clear()
        if self._audio_iter is None and self._stream is None:
            stream = self._open_stream()
            if stream is None:
                self._set_state(
                    "error",
                    "could not reopen the microphone after pause")
                return
            self._stream = stream
        self._set_state("listening")
        info("wakeword", "Listening resumed")

    def stop(self):
        """Stop listening fully (feature toggled off / app quitting).
        The mic is closed; the model object is dropped."""
        self._stop.set()
        self._paused.clear()
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        self._set_state("off")
        info("wakeword", "Wake-word listening stopped")
