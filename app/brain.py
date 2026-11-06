# Memo AI - Brain: Ollama (local) + Groq (online)
# The model is only the "brain" that plans and writes replies.
# Doing things on the laptop is the Python layer's job ("hands") -
# no model ever touches the laptop by itself.
#
# Routing: online -> Groq gpt-oss-120b (free tier, rate limits only).
#          offline -> local Ollama qwen2.5:3b.
#          Groq HTTP 429 (daily limit hit) -> fall back to local Qwen 3B.

import socket

import requests
from logger import error, info, warn
from settings_store import read_key

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:3b"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"

_history: list = []


def is_online(timeout: float = 2.0) -> bool:
    """Cheap internet check (DNS socket, no HTTP)."""
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=timeout).close()
        return True
    except OSError:
        return False


def system_prompt(ai_name: str, user_name: str, language: str) -> str:
    if language == "ar":
        return (
            f"أنت {ai_name}، مساعد ذكي محلي على لابتوب {user_name}. "
            "رد بالعربي المصري البسيط والودود، واختصر على قد ما تقدر. "
            "لو المستخدم كتب بالإنجليزي رد بالإنجليزي. "
            "لو سألك عن الكود أو السورس بتاع البرنامج، قوله: "
            "«تواصل مع المطور» — إلا لو هو المطور نفسه (هيقولك الكود السري). "
            "ممنوع تذكر أي تفاصيل تقنية عن النماذج اللي بتشغلك."
        )
    return (
        f"You are {ai_name}, a local AI assistant on {user_name}'s laptop. "
        "Reply in short, friendly English. "
        "If asked about the program's source code, say: "
        "«Contact the developer» — unless he is the developer himself "
        "(he will tell you the secret code)."
    )


def _ollama(messages: list, timeout: int = 120) -> str:
    resp = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "messages": messages, "stream": False},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"].strip()


def _groq(messages: list, timeout: int = 60) -> str:
    api_key = read_key("groq-key.txt")
    if not api_key:
        raise RuntimeError("no-groq-key")
    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json={"model": GROQ_MODEL, "messages": messages, "temperature": 0.7},
        timeout=timeout,
    )
    if resp.status_code == 429:
        raise RuntimeError("groq-429")
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def ask(user_text: str, ai_name: str, user_name: str, language: str) -> dict:
    """Ask the brain. Returns {"reply": str, "engine": "groq"|"ollama"|"none"}."""
    lang = "ar" if any("\u0600" <= ch <= "\u06ff" for ch in user_text) \
        else language
    system = {"role": "system",
              "content": system_prompt(ai_name, user_name, lang)}
    messages = [system] + _history[-20:] + [{"role": "user", "content": user_text}]

    online = is_online()
    if online:
        try:
            reply = _groq(messages)
            info("brain", "Answered via Groq gpt-oss-120b")
            engine = "groq"
        except RuntimeError as exc:
            if str(exc) == "groq-429":
                warn("brain", "Groq rate limit (429) - falling back to local Qwen 3B")
            elif str(exc) == "no-groq-key":
                warn("brain", "No Groq key in keys/groq-key.txt - using local Qwen 3B")
            else:
                warn("brain", f"Groq failed ({exc}) - falling back to local Qwen 3B")
            reply, engine = _ask_local(messages)
        except Exception as exc:
            warn("brain", f"Groq request failed ({exc}) - falling back to local Qwen 3B")
            reply, engine = _ask_local(messages)
    else:
        info("brain", "Offline - using local Qwen 3B")
        reply, engine = _ask_local(messages)

    if engine != "none":
        _history.append({"role": "user", "content": user_text})
        _history.append({"role": "assistant", "content": reply})
    return {"reply": reply, "engine": engine}


def _ask_local(messages: list) -> tuple:
    try:
        reply = _ollama(messages)
        info("brain", "Answered via local Ollama qwen2.5:3b")
        return reply, "ollama"
    except requests.ConnectionError:
        error("brain", "Ollama is not running on http://localhost:11434 "
              "- start it with: ollama serve")
        return ("المخ المحلي مش شغال — شغّل Ollama الأول "
                "(افتح PowerShell واكتب: ollama serve)", "none")
    except Exception as exc:
        error("brain", f"Ollama request failed: {exc}")
        return ("حصلت مشكلة وأنا بفكر، جرّب تاني.", "none")


def clear_history() -> None:
    _history.clear()
