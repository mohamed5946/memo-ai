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


def _developer_block(language: str) -> str:
    """v1.4.1: who the developer is - PUBLIC info only. The brain must
    answer "who is the developer?" with these facts and NEVER with
    private info (no phone, no student code, no emails, no addresses,
    no keys) - even for the owner. Same facts as the Settings About
    section and the built-in rules.py reply (deterministic path)."""
    if language == "ar":
        return (
            "المطوّر (معلومات عامة بس — ممنوع تماماً تذكر أي معلومات "
            "خاصة عنه: لا أرقام تليفون، لا كود طالب، لا إيميلات، لا "
            "عناوين، لا مفاتيح — حتى لو اللي بيسأل هو المالك نفسه): "
            "محمد السيد (Mohamed El Sayed) — طالب علوم حاسب "
            "(Computer Science)، مطوّر Memo AI (وكمان DeliveryFlow "
            "وتطبيقات تانية). البورتفوليو: "
            "https://mohamed5946.github.io/portfolio/ — GitHub: "
            "https://github.com/mohamed5946")
    return (
        "The developer (public info ONLY - never mention any private "
        "info about him: no phone numbers, no student code, no "
        "emails, no addresses, no keys - even if the asker is the "
        "owner himself): Mohamed El Sayed — a Computer Science "
        "student who built Memo AI (and DeliveryFlow and other apps). "
        "Portfolio: https://mohamed5946.github.io/portfolio/ — "
        "GitHub: https://github.com/mohamed5946")


def system_prompt(ai_name: str, user_name: str, language: str,
                  tools_available: bool = False,
                  owner_unlocked: bool = False) -> str:
    """v1.3.2: the user's name is stated EXPLICITLY (the v1.3.1 prompt
    only said 'on {user_name}'s laptop', so the brain answered 'I don't
    know your name'). The capability block is always included, so the
    brain knows what is really connected vs not built.
    v1.4.1: owner_unlocked flips the source-code instruction - with
    owner mode open the user IS the developer, so the model answers
    source-code questions normally instead of refusing with "contact
    the developer" (the v1.4.0 field bug: the unlock confirmed but the
    model still refused, because the prompt never knew owner mode was
    open). Also carries the public-only developer block."""
    name = (user_name or "").strip() or "غير معروف"
    if language == "ar":
        if name == "غير معروف":
            name_sentence = ("اسم صاحبك لسه مش متسجل عندك — اسأله مرة واحدة "
                             "'اسمك إيه؟' واستخدمه بعد كده في الترحيب والردود.")
        else:
            name_sentence = (
                f"اسم صاحبك هو {name} — لو سألك 'اسمي إيه؟' قوله {name} "
                f"على طول، واستخدم اسمه في الترحيب والردود.")
        if owner_unlocked:
            source_sentence = (
                "وضع المالك مفتوح دلوقتي — اللي بيكلمك هو المطور نفسه، "
                "فلو سألك عن الكود أو السورس بتاع البرنامج جاوبه عادي "
                "وساعده في الكود.")
        else:
            source_sentence = (
                "لو سألك عن الكود أو السورس بتاع البرنامج، قوله: "
                "«تواصل مع المطور» — إلا لو هو المطور نفسه (هيقولك الكود السري). ")
        prompt = (
            f"أنت {ai_name}، مساعد ذكي محلي على لابتوب صاحبك. "
            + name_sentence + " "
            + "رد بالعربي المصري البسيط والودود، واختصر على قد ما تقدر، "
            "واتكلم زي إنسان وصاحب مش زي آلة — طبيعي ودود، مش رسمي ومش آلي. "
            "لو المستخدم كتب بالإنجليزي رد بالإنجليزي. "
            + source_sentence + " "
            + _developer_block("ar") + " "
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
    if owner_unlocked:
        source_sentence_en = (
            "Owner mode is open — the person talking to you IS the "
            "developer, so if he asks about the source code, answer "
            "normally and help him with the code. ")
    else:
        source_sentence_en = (
            "If asked about the program's source code, say: "
            "«Contact the developer» — unless he is the developer himself "
            "(he will tell you the secret code). ")
    prompt = (
        f"You are {ai_name}, a local AI assistant on your friend's laptop. "
        + name_sentence_en + " "
        + "Reply in short, friendly English, and talk like a human friend, not a machine — natural and warm, not formal or robotic. "
        + source_sentence_en
        + _developer_block("en") + " "
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


def _sanitize_messages(messages: list) -> list:
    """Repair a message list before sending it to a model (v1.4.1).
    The v1.4.0 field log proved BOTH Groq and Ollama reject our
    requests with 400 after the first tool call - the history recorded
    raw tool rounds, and hist[-20:] could cut a round in half. Two
    defects, both 400 on Groq AND on Ollama (same broken payload):
      * a message with content=None (JSON null) - Groq and Ollama
        both require content to be a string;
      * an assistant tool_calls message without its tool results in
        the same payload (or a tool result without its call) - Groq
        400s on the unpaired round.
    This keeps well-formed rounds intact and repairs the rest:
    content is always a string (None -> ""), orphan tool messages
    are dropped, and tool_calls with no matching result in this
    payload are stripped from the assistant message (its text, if
    any, is kept). Called at the _groq/_groq_message choke points,
    so EVERY Groq request is validated - including legacy poisoned
    history from a v1.4.0 run."""
    clean = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("system", "user", "assistant", "tool"):
            continue
        content = m.get("content")
        if content is None:
            content = ""
        elif not isinstance(content, str):
            content = str(content)
        new = {"role": role, "content": content}
        if role == "assistant":
            calls = m.get("tool_calls")
            if calls:
                new["tool_calls"] = calls
        if role == "tool":
            tcid = m.get("tool_call_id")
            new["tool_call_id"] = tcid if isinstance(tcid, str) and tcid else ""
        clean.append(new)
    # Pair tool_calls with their results (both directions).
    call_ids = set()
    for m in clean:
        if m["role"] == "assistant":
            for c in m.get("tool_calls") or []:
                if isinstance(c, dict) and c.get("id"):
                    call_ids.add(c["id"])
    result_ids = {m.get("tool_call_id") for m in clean
                  if m["role"] == "tool" and m.get("tool_call_id")}
    out = []
    for m in clean:
        if m["role"] == "assistant":
            if m.get("tool_calls"):
                kept = [c for c in m["tool_calls"]
                        if isinstance(c, dict)
                        and (c.get("function") or {}).get("name")
                        and c.get("id") in result_ids]
                if kept:
                    m["tool_calls"] = kept
                else:
                    # No results for these calls in this payload (history
                    # was cut, or the round never finished) - send the
                    # assistant text alone, or drop it if it has none.
                    m.pop("tool_calls")
            # An assistant message with no text and no (kept) calls is
            # dead weight - drop it (a None content arrived here as "").
            if not m.get("tool_calls") and not m["content"].strip():
                continue
        elif m["role"] == "tool":
            # Orphan result (its call is not in this payload) - drop it.
            if m.get("tool_call_id") not in call_ids:
                continue
        out.append(m)
    return out


def _clean_messages_for_local(messages: list) -> list:
    """Message list for the LOCAL Ollama fallback (v1.4.1): sanitize
    first, then strip ALL tool material. Qwen 2.5 3B is not a
    tool-capable model - Ollama 400s on tool_calls/tool messages for
    it, which is exactly why the v1.4.0 fallback failed with the same
    400 as Groq instead of saving the reply. The local brain only
    needs the plain conversation (system/user/assistant text)."""
    out = []
    for m in _sanitize_messages(messages):
        if m["role"] == "tool":
            continue
        if m["role"] == "assistant":
            m = {"role": "assistant", "content": m["content"]}
            if not m["content"].strip():
                continue
        out.append(m)
    return out


def _ollama(messages: list, timeout: int = 120) -> str:
    # v1.4.1: the payload is cleaned first (see above) - the raw
    # tool-round history 400s on Ollama for the 3B model.
    resp = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL,
              "messages": _clean_messages_for_local(messages),
              "stream": False},
        timeout=timeout,
    )
    resp.raise_for_status()
    content = (resp.json().get("message") or {}).get("content")
    # v1.4.1: a null/empty content used to crash .strip() below and
    # surface as the generic "حصلت مشكلة" - name it instead.
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("ollama-empty-reply")
    return content.strip()


def _groq_message(messages: list, use_tools: bool = False,
                  timeout: int = 60) -> dict:
    """One Groq call returning the FULL assistant message (content and/or
    tool_calls). Raises RuntimeError("no-groq-key") / ("groq-429") like _groq."""
    api_key = read_key("groq-key.txt")
    if not api_key:
        raise RuntimeError("no-groq-key")
    payload = {"model": GROQ_MODEL,
               # v1.4.1: sanitize at the choke point (content None ->
               # "", unpaired tool rounds repaired) - the v1.4.0 log
               # proved Groq 400s on the raw history after a tool call.
               "messages": _sanitize_messages(messages),
               "temperature": 0.7}
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
        # v1.4.1: sanitize (see _groq_message) + guard a null content
        # (Groq can return content=None on an empty/filtered reply -
        # the old .strip() crashed it into the generic error).
        json={"model": GROQ_MODEL,
              "messages": _sanitize_messages(messages),
              "temperature": 0.7},
        timeout=timeout,
    )
    if resp.status_code == 429:
        raise RuntimeError("groq-429")
    resp.raise_for_status()
    content = (resp.json()["choices"][0]["message"] or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("groq-empty-reply")
    return content.strip()


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
                   language: str, history: list | None = None,
                   owner_unlocked: bool = False) -> dict:
    """Brain WITH tools (v1.3.2: this is the NORMAL chat path now, not
    owner-only — v1.3.0 gated tools behind the secret code, so in normal
    chat Groq had no tools at all and refused 'open VS Code' in text.
    The user IS the owner of his own laptop; Allow/Deny still gates
    every single action). Groq with real tool-calling (function calling).
    Runs the agentic loop (max 5 tool rounds): model -> tool call ->
    permission dialog -> execute -> result back to the model -> reply.
    Returns {"reply": str, "engine": "groq"|"ollama"|"",
             "tools_used": int, "tools_disabled": bool}.
    v1.4.1: engine is "" (empty) when NO engine answered - never
    "none" (that string leaked into the chat UI as literal text).

    NOTE: the local Qwen 3B fallback is CHAT-ONLY on purpose - a 3B model
    is too weak for reliable tool calls (it hallucinates tool names and
    arguments). If Groq is unavailable, tools are disabled with a clear
    note and the chat continues on the local model.

    v1.4.0: `history` lets another channel (WhatsApp owner) keep its own
    history instead of the shared in-app one. None = the shared history
    (existing behaviour, unchanged)."""
    lang = "ar" if any("\u0600" <= ch <= "\u06ff" for ch in user_text) \
        else language
    hist = _history if history is None else history
    system = {"role": "system",
              "content": system_prompt(ai_name, user_name, lang, True,
                                       owner_unlocked=owner_unlocked)}
    # `turn` collects ONLY this turn's messages (user + tool calls +
    # tool results) so the shared history never duplicates old entries.
    turn = [{"role": "user", "content": user_text}]
    messages = [system] + hist[-20:] + turn

    if not is_online():
        info("brain", "Offline - tools disabled, local Qwen 3B (chat only)")
        reply, engine = _ask_local(messages, lang)
        if not engine:
            # v1.4.1: the local brain failed too - say BOTH (no net +
            # local down) instead of a bare local error.
            reply = _groq_failure_note("offline", lang) + " " + reply
        note = ("الأدوات (فتح برامج، ملفات، أوامر) محتاجة Groq أونلاين — "
                "دلوقتي مفيش نت فأقدر أجاوب بس.")
        if _looks_like_action(user_text):
            reply = reply + "\n\n" + note
        _record_turn(user_text, reply, engine, hist)
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
                # v1.4.1: record ONLY the plain-text turn. The old code
                # did hist.extend(turn) - the raw tool round-trip
                # (assistant content None/"" + tool_calls + tool
                # results) went into the shared history, and the next
                # request carried it to Groq AND Ollama, which BOTH
                # 400d on it - the v1.4.0 field failure ("after a few
                # good replies"). The tool round-trip lives in the
                # working `messages` list only; history stays clean.
                _record_turn(user_text, reply, "groq", hist)
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
                # v1.4.1: a tool message's content MUST be a string -
                # a None here (or a missing tool_call_id) made the
                # NEXT Groq/Ollama request 400. Coerce defensively;
                # the sanitizer at the request choke point is the
                # second line of defence.
                tool_msg = {"role": "tool",
                            "tool_call_id": call.get("id") or "",
                            "content": result if isinstance(result, str)
                            else ("" if result is None else str(result))}
                messages.append(tool_msg)
                turn.append(tool_msg)
        # 5 rounds and the model still wants more tools: stop politely.
        reply = ("عملت اللي أقدر عليه في الخطوات دي — لو لسه ناقص حاجة "
                 "قولي أكمل إيه بالظبط.")
        _record_turn(user_text, reply, "groq", hist)
        return {"reply": reply, "engine": "groq", "tools_used": tools_used,
                "tools_disabled": False}
    except RuntimeError as exc:
        kind = str(exc)
        if kind == "groq-429":
            warn("brain", "Groq rate limit (429) - tools disabled, local Qwen 3B")
        elif kind == "no-groq-key":
            warn("brain", "No Groq key - tools disabled, local Qwen 3B")
        else:
            warn("brain", f"Groq failed ({type(exc).__name__}: {exc}) - "
                          f"tools disabled, local Qwen 3B")
            kind = "failed"
        reply, engine = _ask_local(messages, lang)
        if not engine:
            # v1.4.1: the fallback failed too - name BOTH failures.
            reply = _groq_failure_note(kind, lang) + " " + reply
        if _looks_like_action(user_text):
            reply += ("\n\nالأدوات (فتح برامج، ملفات، أوامر) محتاجة Groq "
                      "أونلاين — دلوقتي شغال على المخ المحلي فأقدر أجاوب بس.")
        _record_turn(user_text, reply, engine, hist)
        return {"reply": reply, "engine": engine, "tools_used": 0,
                "tools_disabled": True}
    except Exception as exc:
        warn("brain", f"Groq tool loop failed ({type(exc).__name__}: {exc}) "
                      f"- falling back to local Qwen 3B")
        reply, engine = _ask_local(messages, lang)
        if not engine:
            reply = _groq_failure_note("failed", lang) + " " + reply
        _record_turn(user_text, reply, engine, hist)
        return {"reply": reply, "engine": engine, "tools_used": 0,
                "tools_disabled": True}


def _record_turn(user_text: str, reply: str, engine: str,
                 history: list | None = None) -> None:
    # v1.4.0: `history` (None = the shared _history) - see ask_with_tools.
    # v1.4.1: engine is "" on failure (never "none") - failed turns
    # are not recorded, and only PLAIN text is recorded (see
    # ask_with_tools: the tool round-trip never enters the history).
    hist = _history if history is None else history
    if engine:
        hist.append({"role": "user", "content": user_text})
        hist.append({"role": "assistant", "content": reply})


def ask(user_text: str, ai_name: str, user_name: str, language: str,
        history: list | None = None, owner_unlocked: bool = False) -> dict:
    """Ask the brain. Returns {"reply": str, "engine": "groq"|"ollama"|""}.

    v1.4.0: `history` lets another channel (WhatsApp guests) keep its own
    history instead of the shared in-app one. None = shared (unchanged).
    v1.4.1: `owner_unlocked` (WhatsApp owner phone) flips the
    source-code instruction in the system prompt, same as in-app.
    Engine is "" when no engine answered - never "none"."""
    lang = "ar" if any("\u0600" <= ch <= "\u06ff" for ch in user_text) \
        else language
    hist = _history if history is None else history
    system = {"role": "system",
              "content": system_prompt(ai_name, user_name, lang,
                                       owner_unlocked=owner_unlocked)}
    messages = [system] + hist[-20:] + [{"role": "user", "content": user_text}]

    online = is_online()
    if online:
        try:
            reply = _groq(messages)
            info("brain", "Answered via Groq gpt-oss-120b")
            engine = "groq"
        except RuntimeError as exc:
            kind = str(exc)
            if kind == "groq-429":
                warn("brain", "Groq rate limit (429) - falling back to local Qwen 3B")
            elif kind == "no-groq-key":
                warn("brain", "No Groq key in keys/groq-key.txt - using local Qwen 3B")
            else:
                warn("brain", f"Groq failed ({type(exc).__name__}: {exc}) - falling back to local Qwen 3B")
                kind = "failed"
            reply, engine = _ask_local(messages, lang)
            if not engine:
                # v1.4.1: the fallback failed too - name BOTH failures.
                reply = _groq_failure_note(kind, lang) + " " + reply
        except Exception as exc:
            warn("brain", f"Groq request failed ({type(exc).__name__}: {exc}) - falling back to local Qwen 3B")
            reply, engine = _ask_local(messages, lang)
            if not engine:
                reply = _groq_failure_note("failed", lang) + " " + reply
    else:
        info("brain", "Offline - using local Qwen 3B")
        reply, engine = _ask_local(messages, lang)
        if not engine:
            reply = _groq_failure_note("offline", lang) + " " + reply

    if engine:
        hist.append({"role": "user", "content": user_text})
        hist.append({"role": "assistant", "content": reply})
    return {"reply": reply, "engine": engine}


def _groq_failure_note(kind: str, lang: str) -> str:
    """Short honest clause for a Groq failure, prefixed to the local
    fallback's own message when the fallback ALSO fails (v1.4.1) -
    so the user hears BOTH reasons, not just the local one."""
    notes = {
        "groq-429": ("Groq وصل للحد اليومي،",
                     "Groq hit its daily limit,"),
        "no-groq-key": ("مفيش مفتاح Groq،",
                        "There's no Groq key,"),
        "offline": ("مفيش نت،",
                    "No internet,"),
        "failed": ("Groq فشل،",
                   "Groq failed,"),
    }
    ar, en = notes.get(kind, notes["failed"])
    return ar if lang == "ar" else en


def _ask_local(messages: list, lang: str = "ar") -> tuple:
    """Local Ollama fallback. Returns (reply, engine) with engine
    "ollama" on success and "" (empty - NEVER "none") on failure.
    v1.4.1: the failure reply names the REAL local reason (not a bare
    generic message), and the log records the exact exception TYPE +
    message every time - the v1.4.0 log showed only the generic text
    while the real cause (400 on a poisoned payload) stayed hidden."""
    try:
        reply = _ollama(messages)
        info("brain", "Answered via local Ollama qwen2.5:3b")
        return reply, "ollama"
    except requests.Timeout as exc:
        error("brain", f"Ollama request timed out "
                       f"({type(exc).__name__}: {exc})")
        if lang == "ar":
            return ("المخ المحلي بياخد وقت طويل — جرّب تاني "
                    "(Ollama شغال بس الرد اتأخر).", "")
        return ("The local brain is taking too long — try again "
                "(Ollama is running but the reply timed out).", "")
    except requests.ConnectionError as exc:
        error("brain", f"Ollama is not running on http://localhost:11434 "
                       f"({type(exc).__name__}: {exc}) "
                       f"- start it with: ollama serve")
        if lang == "ar":
            return ("المخ المحلي مش شغال — شغّل Ollama الأول "
                    "(افتح PowerShell واكتب: ollama serve)", "")
        return ("The local brain isn't running — start Ollama first "
                "(open PowerShell and type: ollama serve)", "")
    except requests.HTTPError as exc:
        error("brain", f"Ollama refused the request "
                       f"({type(exc).__name__}: {exc})")
        if lang == "ar":
            return ("المخ المحلي رفض الطلب — جرّب تاني "
                    "(التفاصيل في السجل).", "")
        return ("The local brain refused the request — try again "
                "(details in the log).", "")
    except RuntimeError as exc:
        # ollama-empty-reply (v1.4.1): the model answered nothing.
        error("brain", f"Ollama empty reply ({type(exc).__name__}: {exc})")
        if lang == "ar":
            return ("المخ المحلي رجّع رد فاضي — جرّب تاني.", "")
        return ("The local brain returned an empty reply — try again.", "")
    except Exception as exc:
        error("brain", f"Ollama request failed "
                       f"({type(exc).__name__}: {exc})")
        if lang == "ar":
            return ("حصلت مشكلة وأنا بفكر — المخ المحلي رد بخطأ "
                    "(التفاصيل في السجل)، جرّب تاني.", "")
        return ("Something went wrong while thinking — the local brain "
                "returned an error (details in the log), try again.", "")


def clear_history() -> None:
    _history.clear()
