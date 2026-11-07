# Memo AI - Brain: Ollama (local) + Groq (online)
# The model is only the "brain" that plans and writes replies.
# Doing things on the laptop is the Python layer's job ("hands") -
# no model ever touches the laptop by itself.
#
# Routing: online -> Groq gpt-oss-120b (free tier, rate limits only).
#          offline -> local Ollama qwen2.5:3b.
#          Groq HTTP 429 (daily limit hit) -> fall back to local Qwen 3B.

import json
import socket

import requests
from logger import error, info, warn
from settings_store import keys_status, read_key

import capabilities
import tools

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


def _capability_block(language: str) -> str:
    """Truthful capability state, built from the REAL app state
    (v1.3.2). The brain must never guess what it can do and never
    refuse blindly: for anything not connected/not built, it explains
    WHAT is missing and HOW to connect it, and offers an alternative.
    A flat refusal ("مش هقدر أساعدك") with no reason is forbidden.
    """
    keys = keys_status()
    groq_ok = bool(keys.get("groq"))
    gemini_ok = bool(keys.get("gemini"))
    if language == "ar":
        lines = [
            "قدراتك الحقيقية (من حالة التطبيق الفعلية — متخترعش غيرها):",
            f"- المخ الأونلاين (Groq): {'متصل ✅' if groq_ok else 'مش متصل — مفتاح Groq مش موجود في keys/'}",
            f"- الصوت الأونلاين (Gemini TTS): {'متصل ✅' if gemini_ok else 'مش متصل — مفتاح Gemini مش موجود'}",
            "- الأدوات على اللابتوب ✅: تقدر تفتح برامج، تعمل ملفات وتكتب فيها، تعدّل ملفات، تقرأ ملفات، وتشغّل أوامر — عن طريق أدواتك. كل إجراء بيظهر لصاحبك نافذة سماح/رفض لوحدها، فماتسألوش عن الإذن في الكلام — نادي الأداة على طول.",
            "- " + capabilities.whatsapp_status_line("ar"),
            "- " + capabilities.not_built_line("ترجمة الشاشة", "Screen translation", "ar"),
            "- " + capabilities.not_built_line("المجلس متعدد النماذج", "Multi-model council", "ar"),
            "- " + capabilities.not_built_line("تنبيهات الجيميل والتقويم في التحية الصباحية", "Gmail/calendar in the morning briefing", "ar"),
            "قواعد الرفض (مهمة — اتبعها دايماً):",
            "1) ممنوع ترفض رفض جاف من غير سبب ('مش هقدر، آسف مش هقدر أساعدك') — دي جملة ممنوعة.",
            "2) لو مش قادر تعمل حاجة، قول السبب الحقيقي المقنع بأسلوب ودود زي صاحب بيشرح، وقول إيه اللي ناقص وإزاي يتوصل، وقدّم بديل.",
            "3) ممنوع تكرر نفس جملة الرفض مرتين — لو سألك 'ليه؟' اشرح السبب نفسه بالتفصيل مش تعيد الجملة.",
            "4) متدّعيش قدرة مش موجودة، ومتقولش إن حاجة مربوطة وهي مش مربوطة.",
        ]
        return " ".join(lines)
    lines = [
        "Your real capabilities (from the app's actual state — never invent others):",
        f"- Online brain (Groq): {'connected ✅' if groq_ok else 'not connected — no Groq key in keys/'}",
        f"- Online voice (Gemini TTS): {'connected ✅' if gemini_ok else 'not connected — no Gemini key'}",
        "- Laptop tools ✅: you can open apps, create/write/edit/read files, and run commands — through your tools. Every action shows the user an Allow/Deny dialog by itself, so do NOT ask for permission in text — call the tool directly.",
        "- " + capabilities.whatsapp_status_line("en"),
        "- " + capabilities.not_built_line("Screen translation", "Screen translation", "en"),
        "- " + capabilities.not_built_line("Multi-model council", "Multi-model council", "en"),
        "- " + capabilities.not_built_line("Gmail/calendar in the morning briefing", "Gmail/calendar in the morning briefing", "en"),
        "Refusal rules (important — always follow them):",
        "1) Never refuse flatly with no reason ('I can't, sorry I can't help with that') — that phrasing is forbidden.",
        "2) If you cannot do something, give the real, convincing reason in a warm, human, friend-like way, say what is missing and how to connect it, and offer an alternative.",
        "3) Never repeat the same refusal twice — if asked 'why?', explain the same reason in detail instead of repeating the sentence.",
        "4) Never claim a capability you do not have, and never say something is connected when it is not.",
    ]
    return " ".join(lines)


def system_prompt(ai_name: str, user_name: str, language: str,
                  tools_available: bool = False) -> str:
    """v1.3.2: the user's name is stated EXPLICITLY (the v1.3.1 prompt
    only said 'on {user_name}'s laptop', so the brain answered 'I don't
    know your name'). The capability block is always included, so the
    brain knows what is really connected vs not built."""
    name = (user_name or "").strip() or "غير معروف"
    if language == "ar":
        if name == "غير معروف":
            name_sentence = ("اسم صاحبك لسه مش متسجل عندك — اسأله مرة واحدة "
                             "'اسمك إيه؟' واستخدمه بعد كده في الترحيب والردود.")
        else:
            name_sentence = (
                f"اسم صاحبك هو {name} — لو سألك 'اسمي إيه؟' قوله {name} "
                f"على طول، واستخدم اسمه في الترحيب والردود.")
        prompt = (
            f"أنت {ai_name}، مساعد ذكي محلي على لابتوب صاحبك. "
            + name_sentence + " "
            + "رد بالعربي المصري البسيط والودود، واختصر على قد ما تقدر، "
            "واتكلم زي إنسان وصاحب مش زي آلة — طبيعي ودود، مش رسمي ومش آلي. "
            "لو المستخدم كتب بالإنجليزي رد بالإنجليزي. "
            "لو سألك عن الكود أو السورس بتاع البرنامج، قوله: "
            "«تواصل مع المطور» — إلا لو هو المطور نفسه (هيقولك الكود السري). "
            "ممنوع تذكر أي تفاصيل تقنية عن النماذج اللي بتشغلك. "
            + _capability_block("ar")
        )
        if tools_available:
            prompt += (
                " عندك أدوات حقيقية على اللابتوب — استخدمها: لما المستخدم "
                "يطلب منك تعمل حاجة (افتح برنامج، اعمل ملف، عدّل ملف، شغّل "
                "أمر) نادي الأداة المناسبة على طول بدل ما تشرحله إزاي يعملها "
                "بنفسه، وممنوع تقول 'مش قادر أفتح البرامج من هنا' — أنت تقدر، "
                "عن طريق الأدوات. نافذة السماح/الرفض هتظهر لصاحبك لوحدها "
                "قبل التنفيذ، فماتسألوش عن الإذن في الكلام."
            )
        return prompt
    if name == "غير معروف":
        name_sentence_en = ("Your friend's name is not saved yet — ask once "
                            "'what is your name?' and use it after that in "
                            "greetings and replies.")
    else:
        name_sentence_en = (
            f"Your friend's name is {name} — if he asks 'what is my name?', "
            f"answer {name} right away, and use his name in greetings and replies.")
    prompt = (
        f"You are {ai_name}, a local AI assistant on your friend's laptop. "
        + name_sentence_en + " "
        + "Reply in short, friendly English, and talk like a human friend, not a machine — natural and warm, not formal or robotic. "
        "If asked about the program's source code, say: "
        "«Contact the developer» — unless he is the developer himself "
        "(he will tell you the secret code). "
        + _capability_block("en")
    )
    if tools_available:
        prompt += (
            " You have real tools on the laptop — use them: when the user "
            "asks you to DO something (open an app, create a file, edit a "
            "file, run a command), call the right tool right away instead "
            "of explaining how he can do it himself, and never say 'I "
            "cannot open apps from here' — you can, through the tools. "
            "The Allow/Deny dialog appears to the user by itself before "
            "execution, so do not ask for permission in text."
        )
    return prompt


def _ollama(messages: list, timeout: int = 120) -> str:
    resp = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "messages": messages, "stream": False},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"].strip()


def _groq_message(messages: list, use_tools: bool = False,
                  timeout: int = 60) -> dict:
    """One Groq call returning the FULL assistant message (content and/or
    tool_calls). Raises RuntimeError("no-groq-key") / ("groq-429") like _groq."""
    api_key = read_key("groq-key.txt")
    if not api_key:
        raise RuntimeError("no-groq-key")
    payload = {"model": GROQ_MODEL, "messages": messages, "temperature": 0.7}
    if use_tools:
        payload["tools"] = tools.TOOL_SCHEMAS
        payload["tool_choice"] = "auto"
    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if resp.status_code == 429:
        raise RuntimeError("groq-429")
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]


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


_ACTION_WORDS = (
    "افتح", "افتحلي", "افتحي", "open", "شغل", "شغّل", "run",
    "اعمل", "انشئ", "انشئلي", "create", "اكتب", "write", "احفظ", "save",
    "عدل", "عدّل", "edit", "اقرا", "اقرأ", "read", "اعرض", "list",
    "امسح", "delete", "نفذ", "execute",
)


def _looks_like_action(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in _ACTION_WORDS)


def ask_with_tools(user_text: str, ai_name: str, user_name: str,
                   language: str) -> dict:
    """Brain WITH tools (v1.3.2: this is the NORMAL chat path now, not
    owner-only — v1.3.0 gated tools behind the secret code, so in normal
    chat Groq had no tools at all and refused 'open VS Code' in text.
    The user IS the owner of his own laptop; Allow/Deny still gates
    every single action). Groq with real tool-calling (function calling).
    Runs the agentic loop (max 5 tool rounds): model -> tool call ->
    permission dialog -> execute -> result back to the model -> reply.
    Returns {"reply": str, "engine": "groq"|"ollama"|"none",
             "tools_used": int, "tools_disabled": bool}.

    NOTE: the local Qwen 3B fallback is CHAT-ONLY on purpose - a 3B model
    is too weak for reliable tool calls (it hallucinates tool names and
    arguments). If Groq is unavailable, tools are disabled with a clear
    note and the chat continues on the local model.
    """
    lang = "ar" if any("\u0600" <= ch <= "\u06ff" for ch in user_text) \
        else language
    system = {"role": "system",
              "content": system_prompt(ai_name, user_name, lang, True)}
    # `turn` collects ONLY this turn's messages (user + tool calls +
    # tool results) so the shared history never duplicates old entries.
    turn = [{"role": "user", "content": user_text}]
    messages = [system] + _history[-20:] + turn

    if not is_online():
        info("brain", "Offline - tools disabled, local Qwen 3B (chat only)")
        reply, engine = _ask_local(messages)
        note = ("الأدوات (فتح برامج، ملفات، أوامر) محتاجة Groq أونلاين — "
                "دلوقتي مفيش نت فأقدر أجاوب بس.")
        if _looks_like_action(user_text):
            reply = reply + "\n\n" + note
        _record_turn(user_text, reply, engine)
        return {"reply": reply, "engine": engine, "tools_used": 0,
                "tools_disabled": True}

    tools_used = 0
    try:
        for _round in range(5):
            msg = _groq_message(messages, use_tools=True)
            calls = msg.get("tool_calls") or []
            if not calls:
                reply = (msg.get("content") or "").strip()
                if not reply:
                    reply = "تمام."
                info("brain", f"Answered via Groq gpt-oss-120b "
                              f"({tools_used} tool calls)")
                _history.extend(turn)
                _history.append({"role": "assistant", "content": reply})
                return {"reply": reply, "engine": "groq",
                        "tools_used": tools_used, "tools_disabled": False}
            # Feed the assistant's tool-call message back, then execute.
            assistant_msg = {"role": "assistant",
                             "content": msg.get("content") or "",
                             "tool_calls": calls}
            messages.append(assistant_msg)
            turn.append(assistant_msg)
            for call in calls:
                fn = (call.get("function") or {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                result = tools.execute_tool(name, args)
                tools_used += 1
                tool_msg = {"role": "tool",
                            "tool_call_id": call.get("id", ""),
                            "content": result}
                messages.append(tool_msg)
                turn.append(tool_msg)
        # 5 rounds and the model still wants more tools: stop politely.
        reply = ("عملت اللي أقدر عليه في الخطوات دي — لو لسه ناقص حاجة "
                 "قولي أكمل إيه بالظبط.")
        _record_turn(user_text, reply, "groq")
        return {"reply": reply, "engine": "groq", "tools_used": tools_used,
                "tools_disabled": False}
    except RuntimeError as exc:
        if str(exc) == "groq-429":
            warn("brain", "Groq rate limit (429) - tools disabled, local Qwen 3B")
        elif str(exc) == "no-groq-key":
            warn("brain", "No Groq key - tools disabled, local Qwen 3B")
        else:
            warn("brain", f"Groq failed ({exc}) - tools disabled, local Qwen 3B")
        reply, engine = _ask_local(messages)
        if _looks_like_action(user_text):
            reply += ("\n\nالأدوات (فتح برامج، ملفات، أوامر) محتاجة Groq "
                      "أونلاين — دلوقتي شغال على المخ المحلي فأقدر أجاوب بس.")
        _record_turn(user_text, reply, engine)
        return {"reply": reply, "engine": engine, "tools_used": 0,
                "tools_disabled": True}
    except Exception as exc:
        warn("brain", f"Groq tool loop failed ({exc}) - falling back to local Qwen 3B")
        reply, engine = _ask_local(messages)
        _record_turn(user_text, reply, engine)
        return {"reply": reply, "engine": engine, "tools_used": 0,
                "tools_disabled": True}


def _record_turn(user_text: str, reply: str, engine: str) -> None:
    if engine != "none":
        _history.append({"role": "user", "content": user_text})
        _history.append({"role": "assistant", "content": reply})


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
