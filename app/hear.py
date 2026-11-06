# Memo AI - Hearing: mic -> text
# Online: Groq Whisper large-v3 (accurate on Egyptian Arabic, verified).
# Offline fallback: local faster-whisper "small" (known: weak on Egyptian
# dialect - it garbles short Egyptian phrases, but it is the best local
# option on 8 GB RAM until a bigger model / RAM upgrade).

import tempfile
import wave
from pathlib import Path

from logger import error, info, warn
from settings_store import read_key

DURATION = 5
RATE = 16000
LOCAL_MODEL = "small"


def _record_wav(seconds: int = DURATION) -> Path:
    import numpy as np
    import sounddevice as sd

    info("hear", f"Recording {seconds}s from microphone...")
    audio = sd.rec(int(seconds * RATE), samplerate=RATE, channels=1, dtype="int16")
    sd.wait()
    tmp = Path(tempfile.gettempdir()) / "memo-ai-voice.wav"
    with wave.open(str(tmp), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(audio.tobytes())
    return tmp


def _groq_transcribe(wav_path: Path, language: str = "ar") -> str:
    from groq import Groq

    api_key = read_key("groq-key.txt")
    if not api_key:
        raise RuntimeError("no-groq-key")
    client = Groq(api_key=api_key)
    with open(wav_path, "rb") as f:
        result = client.audio.transcriptions.create(
            file=("memo-ai-voice.wav", f.read()),
            model="whisper-large-v3",
            language=language,
            response_format="text",
        )
    return str(result).strip()


def _local_transcribe(wav_path: Path, language: str = "ar") -> str:
    import numpy as np
    from faster_whisper import WhisperModel

    info("hear", "Transcribing locally with faster-whisper small...")
    # Pass the raw samples directly (skips the av/PyAV decoder path).
    with wave.open(str(wav_path), "rb") as wf:
        frames = wf.readframes(wf.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    model = WhisperModel(LOCAL_MODEL, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(audio, language=language)
    return "".join(seg.text for seg in segments).strip()


def listen(language: str = "ar") -> dict:
    """Record 5s and transcribe. Returns {"text": str, "engine": "groq"|"local"|"none"}."""
    try:
        wav_path = _record_wav()
    except Exception as exc:
        error("hear", f"Microphone recording failed: {exc}")
        return {"text": "", "engine": "none",
                "error": "المايك مش شغال — اتأكد إنه متوصل ومسموح للتطبيقات."}

    from brain import is_online
    if is_online():
        try:
            text = _groq_transcribe(wav_path, language)
            info("hear", f"Groq Whisper heard: {text[:80]}")
            return {"text": text, "engine": "groq"}
        except RuntimeError as exc:
            if str(exc) == "no-groq-key":
                warn("hear", "No Groq key - falling back to local Whisper")
            else:
                warn("hear", f"Groq Whisper failed ({exc}) - local fallback")
        except Exception as exc:
            warn("hear", f"Groq Whisper failed ({exc}) - local fallback")
    else:
        info("hear", "Offline - using local Whisper")

    try:
        text = _local_transcribe(wav_path, language)
        info("hear", f"Local Whisper heard: {text[:80]}")
        return {"text": text, "engine": "local"}
    except Exception as exc:
        error("hear", f"Local Whisper failed: {exc}")
        return {"text": "", "engine": "none",
                "error": "مقدرتش أفهم اللي اتقال — جرّب تاني."}
