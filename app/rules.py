# Memo AI - Custom rules + exact-match Q&A
# The user edits app/data/rules.json and app/data/qa.json himself.
# Order of checks: blocked questions -> exact Q&A -> brain.
# Owner mode (secret code typed in chat) bypasses the source-code rule.

import json
import re
import unicodedata
from pathlib import Path

from logger import info

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"

# Source-code requests are refused unless the user is in owner mode.
SOURCE_CODE_PATTERNS = [
    "source code", "السورس", "الكود المصدري", "كود البرنامج",
    "source", "سورس كود", "الكود بتاع البرنامج", "ابعت الكود",
]
SOURCE_CODE_REPLY_AR = "لو عايز الكود أو السورس، تواصل مع المطور."
SOURCE_CODE_REPLY_EN = "If you need the source code, contact the developer."


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[؟?!.،,;:\-()\[\]\"'«»]", " ", text)
    return " ".join(text.split())


def _load_json(name: str, default):
    path = DATA_DIR / name
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def check(text: str, owner_unlocked: bool, language: str = "ar") -> dict | None:
    """Return {"reply": str, "source": "blocked"|"qa"|"rule"} or None."""
    norm = _normalize(text)

    # 1. Blocked questions (polite refusal + log).
    rules = _load_json("rules.json", {})
    for item in rules.get("blocked", []):
        match = _normalize(str(item.get("match", "")))
        if match and match in norm:
            info("rules", f"Blocked question matched: '{item.get('match')}'")
            return {"reply": item.get("reply", "مش هقدر أساعد في ده."),
                    "source": "blocked"}

    # 2. Source-code rule (owner mode bypasses it).
    if not owner_unlocked:
        for pat in SOURCE_CODE_PATTERNS:
            if _normalize(pat) in norm:
                info("rules", "Source-code request refused (not owner mode)")
                return {"reply": SOURCE_CODE_REPLY_AR if language == "ar"
                        else SOURCE_CODE_REPLY_EN, "source": "blocked"}

    # 3. Exact-match Q&A.
    qa = _load_json("qa.json", {})
    for item in qa.get("questions", []):
        if _normalize(str(item.get("q", ""))) == norm and norm:
            info("rules", f"Exact Q&A match: '{item.get('q')}'")
            return {"reply": item.get("a", ""), "source": "qa"}

    # 4. Substring rules (simple "if user says X, answer Y").
    for item in rules.get("rules", []):
        match = _normalize(str(item.get("match", "")))
        if match and match in norm:
            info("rules", f"Rule matched: '{item.get('match')}'")
            return {"reply": item.get("reply", ""), "source": "rule"}

    return None
