# Memo AI - Groq Whisper test (online hearing)
# Records 5 seconds from the mic and sends it to Groq Whisper large-v3.
# Setup: save your Groq API key in a file named groq-key.txt
#        in the ../keys/ folder (next to this scripts/ folder).
#        One key per file, first line only. Never paste the key in chat.
# First install: pip install groq sounddevice
# Run from this folder:  chcp 65001
#                        python memo-ai-groq-whisper-test.py

import wave
from pathlib import Path

import sounddevice as sd
from groq import Groq

DURATION = 5
RATE = 16000

key_path = Path(__file__).resolve().parent.parent / "keys" / "groq-key.txt"
api_key = key_path.read_text(encoding="utf-8").strip()
if not api_key.startswith("gsk_"):
    raise SystemExit("groq-key.txt does not look like a Groq key (should start with gsk_)")

print("Recording 5 seconds - speak now in Arabic!")
audio = sd.rec(int(DURATION * RATE), samplerate=RATE, channels=1, dtype="int16")
sd.wait()
print("Recording done, sending to Groq Whisper...")

with wave.open("test_voice.wav", "wb") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(RATE)
    wf.writeframes(audio.tobytes())

client = Groq(api_key=api_key)
with open("test_voice.wav", "rb") as f:
    transcription = client.audio.transcriptions.create(
        file=("test_voice.wav", f.read()),
        model="whisper-large-v3",
        language="ar",
        response_format="text",
    )
print("Groq Whisper heard:")
print(" ", transcription)
print("Saved audio: test_voice.wav")
