# Memo AI v1.4.2 - Long memory + learning from the user.
#
# A persistent LOCAL memory file (app/data/memory.json - on his laptop
# only, never uploaded, never in git) that lets the brain remember
# durable facts about the user across restarts, learn his style, keep
# corrections as high-priority rules, and notice routines.
#
# What lives here:
#   * facts    - durable facts (name, preferences, projects, decisions,
#                recurring context). Added explicitly ("افتكر كده") or
#                auto-extracted from conversation (preferences, routines).
#   * rules    - LEARNED CORRECTIONS (highest priority). When he corrects
#                the brain ("لا، الصح كده..."), the correction is saved
#                here and injected into EVERY system prompt afterwards,
#                so the mistake is never repeated.
#   * style    - his style profile: Egyptian dialect, masculine forms
#                only, tone (short/detailed - learned from his reactions:
#                "اختصر" / "اشرح بالتفصيل"), favourite phrases.
#   * routines - recurring patterns (work shifts, study schedule,
#                projects, things he asks about a lot), so the brain can
#                answer with context instead of asking the same question.
#   * summary  - rolling extractive summary of old turns. When the active
#                history grows past the cap, the oldest turns are
#                CONDENSED here instead of silently dropped.
#
# PRIVACY: everything is local. Settings shows the memories and lets him
# delete one fact / one rule / one routine, or wipe everything.
#
# The store never crashes the app: every I/O failure is logged and the
# in-memory copy keeps working.

import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from logger import info, warn
from settings_store import DATA_DIR

MEMORY_PATH = DATA_DIR / "memory.json"

# History window: the brain sends the last 40 messages (20 turns) and
# stores up to 60; past that, the oldest 20 are summarized, not dropped.
HISTORY_KEEP = 40
HISTORY_MAX = 60
HISTORY_ROLLOVER = 20


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _norm_ar(text: str) -> str:
    """Arabic-aware normalization for dedupe + search: strip diacritics
    and tatweel, unify alef/teh-marbuta/ya forms, drop punctuation."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("ـ", "")
    text = (text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
                .replace("ة", "ه").replace("ى", "ي"))
    text = text.lower()
    text = re.sub(r"[^\w\s\u0600-\u06ff]", " ", text)
    return " ".join(text.split())


def _tokens(text: str) -> list:
    out = []
    for t in _norm_ar(text).split():
        if len(t) <= 1:
            continue
        # Strip the Arabic definite article for matching: "الامتحان"
        # and "امتحان" are the same token ("الله" -> "له" is a harmless
        # over-strip for search purposes; stopwords are removed later).
        if t.startswith("ال") and len(t) > 3:
            t = t[2:]
        out.append(t)
    return out


_STOPWORDS = {
    "انا", "انت", "هو", "هي", "احنا", "ده", "دي", "دول", "في", "من",
    "الي", "اللي", "علي", "على", "مع", "بعد", "قبل", "لما", "لو",
    "ان", "انه", "انها", "كان", "كانت", "يكون", "ايه", "اي", "او",
    "the", "a", "an", "is", "are", "was", "were", "to", "of", "in",
    "on", "for", "and", "or", "my", "your", "his", "her", "it", "this",
    "that", "what", "when", "how", "why", "do", "does", "did", "can",
}


def _content_tokens(text: str) -> list:
    return [t for t in _tokens(text) if t not in _STOPWORDS]


def _new_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{os.urandom(2).hex()}"


_DEFAULT_STYLE = {
    "dialect": "ar-EG",        # Egyptian Arabic - always
    "forms": "masculine",      # masculine forms ONLY - always
    "tone": "auto",            # auto | short | detailed (learned)
    "phrases": [],             # his favourite phrases, learned
    "notes": [],               # free-form style notes, learned
}


class MemoryStore:
    """One JSON file, loaded once, saved atomically (temp + replace)."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else MEMORY_PATH
        self.data = self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> dict:
        data = {"version": 1, "facts": [], "rules": [],
                "style": dict(_DEFAULT_STYLE), "routines": [],
                "summary": "", "updated": _now_iso()}
        try:
            if self.path.exists():
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    for key in ("facts", "rules", "routines"):
                        if isinstance(saved.get(key), list):
                            data[key] = saved[key]
                    if isinstance(saved.get("style"), dict):
                        data["style"].update(
                            {k: v for k, v in saved["style"].items()
                             if k in _DEFAULT_STYLE})
                    if isinstance(saved.get("summary"), str):
                        data["summary"] = saved["summary"]
        except Exception as exc:
            warn("memory", f"Could not load memory.json, starting fresh: {exc}")
        return data

    def save(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.data["updated"] = _now_iso()
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.path)
            return True
        except Exception as exc:
            warn("memory", f"Could not save memory.json: {exc}")
            return False

    # -- facts -------------------------------------------------------------
    def add_fact(self, text: str, source: str = "explicit",
                 priority: str = "normal") -> str | None:
        text = (text or "").strip()
        if len(text) < 3:
            return None
        norm = _norm_ar(text)
        for f in self.data["facts"]:
            if _norm_ar(f.get("text", "")) == norm:
                return f.get("id")  # already known - no duplicate
        fid = _new_id("fact")
        self.data["facts"].append({
            "id": fid, "text": text, "source": source,
            "priority": priority, "created": _now_iso(), "hits": 0})
        self.save()
        info("memory", f"Fact saved ({source}): {text[:80]}")
        return fid

    def delete_fact(self, fact_id: str) -> bool:
        before = len(self.data["facts"])
        self.data["facts"] = [f for f in self.data["facts"]
                              if f.get("id") != fact_id]
        if len(self.data["facts"]) != before:
            self.save()
            return True
        return False

    def delete_facts_matching(self, query: str) -> int:
        """Delete every fact relevant to `query`. Returns how many."""
        hits = self.search(query, limit=100)
        ids = {f["id"] for f in hits}
        before = len(self.data["facts"])
        self.data["facts"] = [f for f in self.data["facts"]
                              if f.get("id") not in ids]
        removed = before - len(self.data["facts"])
        if removed:
            self.save()
        return removed

    def clear_all(self) -> None:
        self.data = {"version": 1, "facts": [], "rules": [],
                     "style": dict(_DEFAULT_STYLE), "routines": [],
                     "summary": "", "updated": _now_iso()}
        self.save()
        info("memory", "All memories wiped by the user")

    # -- learned rules (corrections - highest priority) ---------------------
    def add_rule(self, text: str) -> str | None:
        text = (text or "").strip()
        if len(text) < 3:
            return None
        norm = _norm_ar(text)
        for r in self.data["rules"]:
            if _norm_ar(r.get("text", "")) == norm:
                return r.get("id")
        rid = _new_id("rule")
        self.data["rules"].append({"id": rid, "text": text,
                                   "created": _now_iso()})
        self.save()
        info("memory", f"Learned rule saved: {text[:80]}")
        return rid

    def delete_rule(self, rule_id: str) -> bool:
        before = len(self.data["rules"])
        self.data["rules"] = [r for r in self.data["rules"]
                              if r.get("id") != rule_id]
        if len(self.data["rules"]) != before:
            self.save()
            return True
        return False

    # -- style profile ------------------------------------------------------
    def set_style(self, key: str, value) -> None:
        if key in _DEFAULT_STYLE:
            self.data["style"][key] = value
            self.save()

    def add_phrase(self, phrase: str) -> None:
        phrase = (phrase or "").strip()
        if phrase and phrase not in self.data["style"]["phrases"]:
            self.data["style"]["phrases"].append(phrase)
            self.save()

    def add_style_note(self, note: str) -> None:
        note = (note or "").strip()
        if note and note not in self.data["style"]["notes"]:
            self.data["style"]["notes"].append(note)
            self.save()

    # -- routines -----------------------------------------------------------
    def add_routine(self, text: str) -> str | None:
        text = (text or "").strip()
        if len(text) < 3:
            return None
        norm = _norm_ar(text)
        for r in self.data["routines"]:
            if _norm_ar(r.get("text", "")) == norm:
                return r.get("id")
        rid = _new_id("routine")
        self.data["routines"].append({"id": rid, "text": text,
                                      "created": _now_iso()})
        self.save()
        info("memory", f"Routine noted: {text[:80]}")
        return rid

    def delete_routine(self, routine_id: str) -> bool:
        before = len(self.data["routines"])
        self.data["routines"] = [r for r in self.data["routines"]
                                 if r.get("id") != routine_id]
        if len(self.data["routines"]) != before:
            self.save()
            return True
        return False

    # -- retrieval ----------------------------------------------------------
    def search(self, query: str, limit: int = 5) -> list:
        """Keyword-overlap retrieval over facts (Arabic-aware). A fact
        scores by the share of the query's content tokens it contains;
        at least one content token must match. Best first."""
        qtok = set(_content_tokens(query))
        if not qtok:
            return []
        scored = []
        for f in self.data["facts"]:
            ftok = set(_content_tokens(f.get("text", "")))
            if not ftok:
                continue
            matched = qtok & ftok
            if not matched:
                continue
            score = len(matched) / len(qtok)
            if len(qtok) <= 2 and len(matched) < 1:
                continue
            if len(qtok) > 2 and score < 0.34 and len(matched) < 2:
                continue
            scored.append((score, f.get("hits", 0), f))
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        out = []
        for _score, _hits, f in scored[:limit]:
            f["hits"] = f.get("hits", 0) + 1
            out.append(f)
        return out

    # -- prompt blocks --------------------------------------------------------
    def get_style_block(self, language: str = "ar") -> str:
        st = self.data["style"]
        tone = st.get("tone", "auto")
        if language == "ar":
            parts = ["أسلوب صاحبك اللي اتعلمته منه (طبّقه في كل رد): "
                     "عربي مصري، صيغ مذكر بس — ممنوع صيغ المؤنث تماماً."]
            if tone == "short":
                parts.append("بيحب الردود القصيرة والمباشرة — اختصر على قد ما تقدر.")
            elif tone == "detailed":
                parts.append("بيحب الشرح المفصّل — وضّح بالتفصيل.")
            else:
                parts.append("نبرته مختصرة ومباشرة في العادة — اختصر، ولو طلب تفصيل فصّل.")
            for ph in st.get("phrases", [])[:5]:
                parts.append(f"من عباراته المفضلة: «{ph}».")
            for note in st.get("notes", [])[:5]:
                parts.append(f"ملاحظة عن أسلوبه: {note}")
            return " ".join(parts)
        parts = ["Your friend's style (apply it in every reply): "
                 "Egyptian Arabic, masculine forms only — never feminine forms."]
        if tone == "short":
            parts.append("He likes short, direct replies — keep it brief.")
        elif tone == "detailed":
            parts.append("He likes detailed explanations.")
        else:
            parts.append("His tone is usually short and direct — keep it brief, expand when asked.")
        for ph in st.get("phrases", [])[:5]:
            parts.append(f"One of his favourite phrases: «{ph}».")
        return " ".join(parts)

    def get_rules_block(self, language: str = "ar") -> str:
        rules = self.data["rules"][-10:]
        if not rules:
            return ""
        if language == "ar":
            lines = ["قواعد اتعلمتها من تصحيحات صاحبك — ليها أولوية أعلى من أي حاجة تانية، ومتكررش الغلطة دي تاني أبداً:"]
            lines += [f"- {r['text']}" for r in rules]
            return "\n".join(lines)
        lines = ["Rules learned from your friend's corrections — highest priority, never repeat these mistakes:"]
        lines += [f"- {r['text']}" for r in rules]
        return "\n".join(lines)

    def get_routines_block(self, language: str = "ar") -> str:
        routines = self.data["routines"][-8:]
        if not routines:
            return ""
        if language == "ar":
            lines = ["روتين وعادات صاحبك (استخدمها عشان تتوقع احتياجه وتجاوب بسياق صح من غير ما تسأل نفس السؤال كل مرة):"]
            lines += [f"- {r['text']}" for r in routines]
            return "\n".join(lines)
        lines = ["Your friend's routines and habits (use them to anticipate his needs and answer with context):"]
        lines += [f"- {r['text']}" for r in routines]
        return "\n".join(lines)

    def relevant_block(self, query: str, language: str = "ar") -> str:
        """The whole memory block for one turn's system prompt:
        retrieved facts + style + learned rules + routines + rolling
        conversation summary. Empty string when there is nothing."""
        parts = []
        facts = self.search(query, limit=5)
        if facts:
            if language == "ar":
                lines = ["حقائق محفوظة عن صاحبك (من ذاكرتك الطويلة — استخدمها في الرد):"]
                lines += [f"- {f['text']}" for f in facts]
                parts.append("\n".join(lines))
            else:
                lines = ["Saved facts about your friend (from your long-term memory — use them in your reply):"]
                lines += [f"- {f['text']}" for f in facts]
                parts.append("\n".join(lines))
        style = self.get_style_block(language)
        if style:
            parts.append(style)
        rules = self.get_rules_block(language)
        if rules:
            parts.append(rules)
        routines = self.get_routines_block(language)
        if routines:
            parts.append(routines)
        summary = (self.data.get("summary") or "").strip()
        if summary:
            if language == "ar":
                parts.append(f"ملخص محادثات سابقة مع صاحبك:\n{summary}")
            else:
                parts.append(f"Summary of earlier conversations with your friend:\n{summary}")
        return "\n\n".join(parts)

    # -- rolling summary ------------------------------------------------------
    def summarize_turns(self, turns: list, language: str = "ar") -> str:
        """Extractive summary of old turns (no model call - deterministic
        and free): one line per user turn with a short reply excerpt."""
        lines = []
        i = 0
        while i < len(turns):
            msg = turns[i]
            if msg.get("role") == "user":
                user = (msg.get("content") or "").strip().replace("\n", " ")
                reply = ""
                if i + 1 < len(turns) and turns[i + 1].get("role") == "assistant":
                    reply = (turns[i + 1].get("content") or "").strip().replace("\n", " ")
                if user:
                    if language == "ar":
                        line = f"• سأل: {user[:120]}"
                        if reply:
                            line += f" — الرد كان: {reply[:120]}"
                    else:
                        line = f"• Asked: {user[:120]}"
                        if reply:
                            line += f" — reply was: {reply[:120]}"
                    lines.append(line)
                    i += 2
                    continue
            i += 1
        return "\n".join(lines)

    def rollover(self, hist: list, language: str = "ar") -> bool:
        """If the stored history exceeds HISTORY_MAX, condense the oldest
        HISTORY_ROLLOVER messages into the rolling summary and drop them.
        Returns True when a rollover happened."""
        if len(hist) <= HISTORY_MAX:
            return False
        old = hist[:HISTORY_ROLLOVER]
        del hist[:HISTORY_ROLLOVER]
        summary = self.summarize_turns(old, language)
        if summary:
            prev = (self.data.get("summary") or "").strip()
            combined = (prev + "\n" + summary).strip() if prev else summary
            # Cap the stored summary (~2000 chars) - oldest lines go first.
            if len(combined) > 2000:
                combined = combined[-2000:]
                combined = combined[combined.find("\n") + 1:] if "\n" in combined else combined
            self.data["summary"] = combined
            self.save()
            info("memory", f"History rolled over: {len(old)} old turns "
                           f"summarized ({len(summary)} chars), not dropped")
        return True

    # -- explicit memory commands ("افتكر كده" / "امسح ذاكرتك عن...") ---------
    def try_handle_command(self, text: str, language: str = "ar") -> str | None:
        """Intercept explicit memory commands BEFORE the brain. Returns
        the reply string, or None when the text is not a memory command
        (normal chat continues)."""
        raw = (text or "").strip()
        if not raw:
            return None
        low = _norm_ar(raw)

        # "امسح كل ذاكرتك" / "forget everything". NOTE: "كل" is MANDATORY
        # here - "امسح ذاكرتك عن X" (forget-one) must NOT match this
        # branch (the old pattern had (كل)? optional and wiped everything).
        if re.search(r"(امسح|امسحي)\s+كل\s+ذاكرتك|ذاكرتك كلها|انسي\s+كل\s+حاجه|انسى\s+كل\s+حاجه|forget everything|clear (your|all) memory|wipe (your )?memory", low):
            self.clear_all()
            return ("تمام، مسحت كل ذاكرتي عنك ✅ — هبدأ من الصفر."
                    if language == "ar" else
                    "Done, I wiped all my memories about you ✅ — starting fresh.")

        # "امسح ذاكرتك عن X" / "انسى X" — matched on the RAW text so the
        # target keeps its original spelling in the reply; search itself
        # normalizes, so variants still match.
        m = re.match(r"^(امسح|انسى|انسي|احذف|forget|delete)\s+(ذاكرتك\s+عن\s+|ذاكرتي\s+عن\s+|عن\s+)?(.+)$",
                     raw, re.IGNORECASE)
        if m and ("ذاكر" in low or "انس" in low or low.startswith(("forget", "delete"))):
            target = m.group(3).strip()
            removed = self.delete_facts_matching(target)
            if removed:
                return (f"تمام، مسحت {removed} من ذاكرتي عن «{target}» ✅."
                        if language == "ar" else
                        f"Done, I deleted {removed} memor{'y' if removed == 1 else 'ies'} about «{target}» ✅.")
            return ("ملقيتش حاجة في ذاكرتي عن «{}» عشان أمسحها."
                    .format(target) if language == "ar" else
                    f"I couldn't find anything in my memory about «{target}» to delete.")

        # "فاكر إيه عني؟" / "what do you remember about me?"
        if re.search(r"فاكر ايه عني|ايه اللي فاكره|قوللي اللي فاكره|قول اللي فاكره|what do you remember|what do you know about me", low):
            facts = self.data["facts"]
            rules = self.data["rules"]
            routines = self.data["routines"]
            if not facts and not rules and not routines:
                return ("لسه مفيش حاجة محفوظة عنك — قولي «افتكر إن...» "
                        "وأنا هحفظها."
                        if language == "ar" else
                        "Nothing saved about you yet — tell me “remember that...” and I'll save it.")
            if language == "ar":
                lines = ["اللي فاكره عنك:"]
                lines += [f"• {f['text']}" for f in facts[:10]]
                if rules:
                    lines.append("قواعد اتعلمتها من تصحيحاتك:")
                    lines += [f"• {r['text']}" for r in rules[:5]]
                if routines:
                    lines.append("روتينك وعاداتك:")
                    lines += [f"• {r['text']}" for r in routines[:5]]
                return "\n".join(lines)
            lines = ["What I remember about you:"]
            lines += [f"• {f['text']}" for f in facts[:10]]
            return "\n".join(lines)

        # "افتكر إن X" / "احفظ X" / "remember that X" — matched on the
        # RAW text so the saved fact keeps its original spelling.
        m = re.match(r"^(عايزك\s+|ممكن\s+|لو سمحت\s+)?(افتكر|احفظ|فتكر|خليك فاكر|اتذكر|remember|note that|remember that)\s+(إن\s+|ان\s+|أن\s+|that\s+)?(.+)$",
                     raw, re.IGNORECASE)
        if m:
            fact = m.group(4).strip()
            if len(fact) >= 3:
                self.add_fact(fact, source="explicit")
                return ("تمام، حفظتها ✅ — هفضل فاكرها."
                        if language == "ar" else
                        "Got it, saved ✅ — I'll remember that.")
        return None

    # -- learning from a turn -------------------------------------------------
    # Correction patterns: (regex, group holding the rule text).
    # Group 0 = the whole match ("قول X مش Y" IS the rule as stated).
    _CORRECTION_RES = [
        (re.compile(r"(لا|لأ)[،,\s]+(الصح|الصحيح|المفروض)\s*(ان|إن|هو)?\s*(.+)", re.S), 4),
        (re.compile(r"(غلط|مش كده|مش صحيح|مش صح)[،,\s]+(.+)", re.S), 2),
        (re.compile(r"الصح\s+(ان|إن|هو)\s+(.+)", re.S), 2),
        (re.compile(r"قول\s+(.+?)\s+مش\s+(.+)", re.S), 0),
        (re.compile(r"(no|wrong|incorrect)[,.\s]+(the correct (thing|answer) is|correct is|it is|it's)\s+(.+)", re.S | re.I), 4),
        (re.compile(r"don't say\s+(.+?)\s*,?\s*say\s+(.+)", re.S | re.I), 0),
    ]
    _SHORT_RES = [re.compile(p) for p in
                  (r"اختصر", r"باختصار", r"من غير تفاصيل", r"رد سريع",
                   r"keep it short", r"be brief", r"short answer")]
    # NOTE: no bare "فصل" here - it would false-positive on "الفصل
    # الدراسي" (semester), which he mentions all the time.
    _DETAILED_RES = [re.compile(p) for p in
                     (r"بالتفصيل", r"طول في", r"طوّل",
                      r"explain in detail", r"in detail")]
    _ROUTINE_RES = [re.compile(p) for p in
                    (r"كل يوم", r"كل اسبوع", r"كل خميس", r"كل اثنين",
                     r"كل ثلاثاء", r"كل اربعاء", r"كل سبت", r"كل احد",
                     r"كل جمعه", r"دايما", r"عادة", r"شغلي بيبدا",
                     r"بذاكر", r"جدولي", r"every day", r"every week",
                     r"my shift", r"my schedule")]
    _PREFERENCE_RES = [re.compile(p) for p in
                       (r"بحب", r"بفضل", r"عايزك ترد", r"بحب لما",
                        r"i like", r"i prefer")]

    def learn_from_turn(self, user_text: str, reply_text: str = "") -> list:
        """Learn from one turn. Returns a list of short descriptions of
        what was learned (for the log). Never raises."""
        learned = []
        try:
            text = (user_text or "").strip()
            if not text:
                return learned
            # 1. Corrections -> high-priority learned rules.
            for rx, group_idx in self._CORRECTION_RES:
                m = rx.search(text)
                if m:
                    try:
                        rule = m.group(group_idx)
                    except IndexError:
                        rule = m.group(0)
                    rule = (rule or "").strip()
                    if len(rule) >= 3:
                        rule = rule[:200]
                        if self.add_rule(rule):
                            learned.append(f"rule: {rule[:60]}")
                    break
            # 2. Tone signals -> style profile.
            tone = self.data["style"].get("tone", "auto")
            if any(rx.search(text) for rx in self._SHORT_RES):
                if tone != "short":
                    self.set_style("tone", "short")
                    learned.append("style tone=short")
            elif any(rx.search(text) for rx in self._DETAILED_RES):
                if tone != "detailed":
                    self.set_style("tone", "detailed")
                    learned.append("style tone=detailed")
            # 3. Recurring patterns -> routines.
            if any(rx.search(_norm_ar(text)) for rx in self._ROUTINE_RES):
                if self.add_routine(text[:200]):
                    learned.append(f"routine: {text[:60]}")
            # 4. Stated preferences -> auto facts.
            elif any(rx.search(_norm_ar(text)) for rx in self._PREFERENCE_RES):
                if self.add_fact(text[:200], source="auto"):
                    learned.append(f"preference: {text[:60]}")
        except Exception as exc:
            warn("memory", f"learn_from_turn failed: {exc}")
        if learned:
            info("memory", "Learned from user: " + "; ".join(learned))
        return learned


# ---------------------------------------------------------------------------
# Module-level singleton (brain.py uses this; tests inject their own)
# ---------------------------------------------------------------------------

_STORE: MemoryStore | None = None


def get_store() -> MemoryStore:
    global _STORE
    if _STORE is None:
        _STORE = MemoryStore()
    return _STORE


def set_store(store: MemoryStore | None) -> None:
    global _STORE
    _STORE = store
