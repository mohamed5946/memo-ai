"""LONA v2 — the AI brain (Groq primary, Gemini fallback, Ollama local).

Rules: no silent fallback — the engine badge under a reply always
names the model that actually answered; an unavailable model option
is disabled with a clear reason; every network call has a timeout;
chat runs on a background thread and returns {pending, id}.
"""

import threading
import time
import uuid

import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "gpt-oss-120b"
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
              + GEMINI_MODEL + ":generateContent")
OLLAMA_TAGS = "http://localhost:11434/api/tags"
OLLAMA_CHAT = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:3b"

_jobs = {}
_jobs_lock = threading.Lock()


class BrainError(Exception):
    """A chat failure with a user-facing message."""


def _groq_ask(text: str, key: str, deep_think: bool) -> str:
    body = {"model": GROQ_MODEL,
            "messages": [{"role": "user", "content": text}]}
    if deep_think:
        body["reasoning_effort"] = "high"
    try:
        resp = requests.post(
            GROQ_URL, json=body, timeout=60,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
    except requests.RequestException as exc:
        raise BrainError(f"Groq مش متاح دلوقتي ({exc})")
    if resp.status_code == 429:
        raise BrainError("Groq وصل لحد الاستخدام — جرّب بعد شوية")
    if resp.status_code != 200:
        raise BrainError(f"Groq رجّع خطأ {resp.status_code}")
    try:
        return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        raise BrainError("رد Groq مش مفهوم")


def _gemini_ask(text: str, key: str) -> str:
    body = {"contents": [{"parts": [{"text": text}]}]}
    try:
        resp = requests.post(GEMINI_URL, json=body, timeout=60,
                             params={"key": key})
    except requests.RequestException as exc:
        raise BrainError(f"Gemini مش متاح دلوقتي ({exc})")
    if resp.status_code != 200:
        raise BrainError(f"Gemini رجّع خطأ {resp.status_code}")
    try:
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        raise BrainError("رد Gemini مش مفهوم")


def _ollama_available() -> bool:
    try:
        resp = requests.get(OLLAMA_TAGS, timeout=2)
        if resp.status_code != 200:
            return False
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        return any(OLLAMA_MODEL in m for m in models)
    except Exception:
        return False


def get_model_options(keys: dict, settings: dict) -> list:
    """All options with availability + reason. Nothing is silently
    dropped: an unavailable option is disabled with its reason."""
    groq_ok = bool(keys.get("groq"))
    gemini_ok = bool(keys.get("gemini_chat"))
    ollama_ok = _ollama_available()
    return [
        {"id": "auto", "label": "تلقائي",
         "available": groq_ok or gemini_ok,
         "reason": "" if (groq_ok or gemini_ok)
                  else "مفيش مفتاح محفوظ"},
        {"id": "groq", "label": "Groq · gpt-oss-120b",
         "available": groq_ok,
         "reason": "" if groq_ok else "مفتاح Groq مش محفوظ"},
        {"id": "gemini", "label": "Gemini · 2.5 Flash",
         "available": gemini_ok,
         "reason": "" if gemini_ok else "مفتاح Gemini شات مش محفوظ"},
        {"id": "ollama", "label": "محلي · Qwen 2.5 3B",
         "available": ollama_ok,
         "reason": "" if ollama_ok else "Ollama مش متثبت"},
    ]


def _run_chat(job_id: str, text: str, model_choice: str,
              deep_think: bool, keys: dict) -> None:
    """Background chat worker. Never raises out of the thread."""
    try:
        chain = []
        if model_choice == "groq":
            chain = [("groq",)]
        elif model_choice == "gemini":
            chain = [("gemini",)]
        elif model_choice == "ollama":
            chain = [("ollama",)]
        else:  # auto: Groq first, then Gemini
            chain = [("groq",), ("gemini",)]
        last_error = None
        for (engine,) in chain:
            try:
                if engine == "groq":
                    if not keys.get("groq"):
                        raise BrainError("مفتاح Groq مش محفوظ")
                    reply = _groq_ask(text, keys["groq"], deep_think)
                    engine_label = "Groq · gpt-oss-120b"
                elif engine == "gemini":
                    if not keys.get("gemini_chat"):
                        raise BrainError("مفتاح Gemini شات مش محفوظ")
                    reply = _gemini_ask(text, keys["gemini_chat"])
                    engine_label = "Gemini · 2.5 Flash"
                else:
                    raise BrainError("Ollama مش متثبت")
                with _jobs_lock:
                    _jobs[job_id] = {"done": True, "reply": reply,
                                     "engine": engine_label, "error": None}
                return
            except BrainError as exc:
                last_error = str(exc)
                continue
        with _jobs_lock:
            _jobs[job_id] = {"done": True, "reply": None, "engine": None,
                             "error": last_error or "فشل غير معروف"}
    except Exception as exc:
        with _jobs_lock:
            _jobs[job_id] = {"done": True, "reply": None, "engine": None,
                             "error": str(exc)}


def chat(text: str, model_choice: str, deep_think: bool,
         keys: dict) -> dict:
    """Start a chat in the background. Returns {pending, id}."""
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"done": False, "reply": None,
                         "engine": None, "error": None}
    thread = threading.Thread(target=_run_chat, daemon=True,
                              args=(job_id, text, model_choice,
                                    deep_think, keys))
    thread.start()
    return {"pending": True, "id": job_id}


def chat_result(job_id: str) -> dict:
    """Poll a chat job. {done:false} until finished, then the reply
    or a clear failure message — never an empty reply."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return {"done": True, "reply": None, "engine": None,
                "error": "المحادثة دي مش موجودة"}
    if not job["done"]:
        return {"done": False, "reply": None, "engine": None, "error": None}
    return {"done": True, "reply": job["reply"],
            "engine": job["engine"], "error": job["error"]}
