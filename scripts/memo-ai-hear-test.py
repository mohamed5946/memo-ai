# Memo AI - Hearing test (Step 2b) - v4
# Records 5 seconds from the laptop mic and transcribes it with local
# Whisper (small). Needs NO API key - fully offline.
# v4: "small" model - much more accurate on Arabic (Egyptian dialect) than "base".
# First run downloads the small model (~500 MB) - takes a few minutes.
# Language is forced to Arabic ("ar"). For English, change "ar" to "en".
# Known: local "small" is still weak on short Egyptian phrases -
# the accurate path is Groq Whisper (see memo-ai-groq-whisper-test.py).
# First install: pip install faster-whisper sounddevice
# Run from this folder:  chcp 65001
#                        python memo-ai-hear-test.py

import wave

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

DURATION = 5
RATE = 16000
MODEL = "small"  # "base" = fast but weak Arabic; "small" = much better Arabic
LANGUAGE = "ar"  # "ar" for Arabic, "en" for English

print("Recording 5 seconds - speak now in Arabic!")
audio = sd.rec(int(DURATION * RATE), samplerate=RATE, channels=1, dtype="int16")
sd.wait()
print("Recording done, transcribing...")

# Save a copy for reference
with wave.open("test_voice.wav", "wb") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(RATE)
    wf.writeframes(audio.tobytes())

# Whisper wants float32 mono in [-1, 1] - pass the array directly (skips av decoding)
audio_float = audio.astype(np.float32).flatten() / 32768.0

model = WhisperModel(MODEL, device="cpu", compute_type="int8")
segments, info = model.transcribe(audio_float, language=LANGUAGE)
print("Model:", MODEL, "| language:", info.language)
print("You said:")
for seg in segments:
    print(" ", seg.text)
print("Saved audio: test_voice.wav")
