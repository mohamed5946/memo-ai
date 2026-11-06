# Memo AI - Voice output: Gemini TTS -> Azure Speech -> Piper
# Chain order (voice is a setting, never auto-switches by itself - but the
# *fallback* chain runs when the chosen engine is unavailable):
#   1. Gemini TTS (online, key in keys/gemini-key.txt, ~15 req/day free)
#   2. Azure Speech F0 (online, optional, keys/azure-key.txt:
#      line 1 = key, line 2 = region; Egyptian voices Salma / Shakir)
#   3. Piper offline (binary `piper` on PATH + model ar_JO-kareem-medium)
# If nothing works: log it and stay silent (text reply still shows).
# Playback on Windows uses the built-in winsound module (WAV only).

import base64
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import requests
from logger import error, info, warn
from settings_store import APP_DIR, read_azure, read_key

# Gemini prebuilt voices for the 4 Settings voices (2 boys + 2 girls).
GEMINI_VOICES = {
    "boy_calm": "Charon",    # ولد 1 - هادي ورزين
    "boy_fun": "Puck",       # ولد 2 - مرح وخفيف
    "girl_warm": "Kore",     # بنت 1 - دافية وودودة
    "girl_fast": "Zephyr",   # بنت 2 - عملية وسريعة
}

AZURE_VOICES = {
    "boy_calm": "ar-EG-ShakirNeural",
    "boy_fun": "ar-EG-ShakirNeural",
    "girl_warm": "ar-EG-SalmaNeural",
    "girl_fast": "ar-EG-SalmaNeural",
}

GEMINI_TTS_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
                  "gemini-2.5-flash-preview-tts:generateContent")
PIPER_MODEL_NAME = "ar_JO-kareem-medium.onnx"
MAX_SPEAK_CHARS = 1000


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        return 0.0


def _play_wav(path: Path) -> float:
    """Play a WAV file (Windows). Returns its duration in seconds."""
    duration = _wav_duration(path)
    if sys.platform != "win32":
        warn("speak", "Audio playback is Windows-only (winsound) - skipping")
        return duration
    try:
        import winsound
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception as exc:
        error("speak", f"Playback failed: {exc}")
    return duration


def stop() -> None:
    if sys.platform == "win32":
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass


def _gemini_tts(text: str, voice_key: str) -> Path:
    api_key = read_key("gemini-key.txt")
    if not api_key:
        raise RuntimeError("no-gemini-key")
    voice = GEMINI_VOICES.get(voice_key, "Kore")
    resp = requests.post(
        GEMINI_TTS_URL,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
            },
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    try:
        b64 = data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("gemini-no-audio-in-response")
    pcm = base64.b64decode(b64)
    out = Path(tempfile.gettempdir()) / "memo-ai-gemini.wav"
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(pcm)
    return out


def _azure_tts(text: str, voice_key: str) -> Path:
    api_key, region = read_azure()
    if not api_key or not region:
        raise RuntimeError("no-azure-key")
    voice = AZURE_VOICES.get(voice_key, "ar-EG-SalmaNeural")
    ssml = (f'<speak version="1.0" xml:lang="ar-EG">'
            f'<voice name="{voice}">{xml_escape(text)}</voice></speak>')
    resp = requests.post(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
        headers={
            "Ocp-Apim-Subscription-Key": api_key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm",
        },
        data=ssml.encode("utf-8"),
        timeout=60,
    )
    resp.raise_for_status()
    out = Path(tempfile.gettempdir()) / "memo-ai-azure.wav"
    out.write_bytes(resp.content)
    return out


def _find_piper_model() -> Path | None:
    candidates = [
        APP_DIR / "data" / "piper" / PIPER_MODEL_NAME,
        Path.home() / "piper" / PIPER_MODEL_NAME,
        Path(os.environ.get("PIPER_MODEL", "")) if os.environ.get("PIPER_MODEL") else None,
    ]
    for cand in candidates:
        if cand and cand.exists():
            return cand
    return None


def _piper_tts(text: str) -> Path:
    binary = shutil.which("piper")
    if not binary:
        raise RuntimeError("no-piper-binary")
    model = _find_piper_model()
    if not model:
        raise RuntimeError("no-piper-model")
    out = Path(tempfile.gettempdir()) / "memo-ai-piper.wav"
    proc = subprocess.run(
        [binary, "--model", str(model), "--output_file", str(out)],
        input=text.encode("utf-8"),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120,
    )
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"piper failed: {proc.stderr.decode('utf-8', 'ignore')[:200]}")
    return out


def speak(text: str, voice_key: str = "girl_warm", online: bool = True) -> dict:
    """Speak text through the fallback chain.
    Returns {"engine": "gemini"|"azure"|"piper"|"none", "duration": seconds}."""
    clean = " ".join(text.split())
    if not clean:
        return {"engine": "none", "duration": 0.0}
    if len(clean) > MAX_SPEAK_CHARS:
        info("speak", f"Reply too long for speech ({len(clean)} chars) - trimming")
        clean = clean[:MAX_SPEAK_CHARS]

    if online:
        try:
            wav = _gemini_tts(clean, voice_key)
            duration = _play_wav(wav)
            info("speak", "Spoke via Gemini TTS")
            return {"engine": "gemini", "duration": duration}
        except RuntimeError as exc:
            if str(exc) == "no-gemini-key":
                warn("speak", "No Gemini key in keys/gemini-key.txt - trying Azure")
            else:
                warn("speak", f"Gemini TTS failed ({exc}) - trying Azure")
        except Exception as exc:
            warn("speak", f"Gemini TTS failed ({exc}) - trying Azure")

        try:
            wav = _azure_tts(clean, voice_key)
            duration = _play_wav(wav)
            info("speak", "Spoke via Azure Speech")
            return {"engine": "azure", "duration": duration}
        except RuntimeError as exc:
            if str(exc) == "no-azure-key":
                info("speak", "No Azure key - trying Piper (offline)")
            else:
                warn("speak", f"Azure TTS failed ({exc}) - trying Piper")
        except Exception as exc:
            warn("speak", f"Azure TTS failed ({exc}) - trying Piper")
    else:
        info("speak", "Offline - skipping online voices, using Piper")

    try:
        wav = _piper_tts(clean)
        duration = _play_wav(wav)
        info("speak", "Spoke via Piper (offline)")
        return {"engine": "piper", "duration": duration}
    except RuntimeError as exc:
        if str(exc) == "no-piper-binary":
            error("speak", "Piper binary not found on PATH - install Piper "
                  "or use an online voice")
        elif str(exc) == "no-piper-model":
            error("speak", f"Piper model {PIPER_MODEL_NAME} not found - "
                  f"put it in app/data/piper/")
        else:
            error("speak", f"Piper failed: {exc}")
    except Exception as exc:
        error("speak", f"Piper failed: {exc}")
    return {"engine": "none", "duration": 0.0}
