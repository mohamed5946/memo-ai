# LONA v1.6.4 reskin - tiny JSON-backed task store.
# New in the v2 UI (Tasks view); v1 has no equivalent, so this is the
# only genuinely new backend file in the reskin. Minimal on purpose:
# CRUD + toggle, atomic writes, no other dependencies.

import json
import threading
import uuid
from datetime import datetime

from settings_store import DATA_DIR

_TASKS_PATH = DATA_DIR / "tasks.json"
_LOCK = threading.Lock()


def _load() -> list:
    try:
        if _TASKS_PATH.exists():
            data = json.loads(_TASKS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [t for t in data
                        if isinstance(t, dict) and t.get("id")
                        and isinstance(t.get("text"), str)]
    except Exception:
        pass
    return []


def _save(tasks: list) -> bool:
    try:
        _TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _TASKS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(tasks, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(_TASKS_PATH)
        return True
    except Exception:
        return False


def get_tasks() -> dict:
    """All tasks, in creation order: {"ok", "tasks": [{id, text,
    done, created, due, remind_at}]}. The v2 UI also accepts a bare
    list. `due` / `remind_at` are "YYYY-MM-DD HH:MM" or None (island
    reminders, v1.6.4); old tasks without them read as None."""
    with _LOCK:
        return {"ok": True, "tasks": [_normalize(t) for t in _load()]}


def _normalize(task: dict) -> dict:
    """One task dict with due/remind_at always present (None when
    unset) so callers never KeyError on old tasks."""
    out = dict(task)
    out.setdefault("due", None)
    out.setdefault("remind_at", None)
    return out


def _parse_when(value):
    """"YYYY-MM-DD HH:MM" (or a date alone) -> normalized string, or
    None when empty/invalid. Stored as text on purpose - no timezone
    math in the store; the island compares wall-clock strings."""
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.strftime("%Y-%m-%d %H:%M")
        except Exception:
            continue
    return None


def due_tasks(now=None) -> list:
    """Not-done tasks whose remind_at has passed (wall clock). Used
    by the island reminder check; the caller marks them reminded by
    clearing remind_at (set_task_due) so they do not fire twice."""
    now_text = (now.strftime("%Y-%m-%d %H:%M") if now is not None
                else datetime.now().strftime("%Y-%m-%d %H:%M"))
    with _LOCK:
        return [_normalize(t) for t in _load()
                if not t.get("done") and t.get("remind_at")
                and str(t["remind_at"]) <= now_text]


def add_task(text, due=None, remind_at=None) -> dict:
    """Add one task. Empty text is refused, never stored. `due` /
    `remind_at` are optional "YYYY-MM-DD HH:MM" strings (island
    reminders); invalid values are stored as None, never rejected."""
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "error": "empty_task"}
    with _LOCK:
        tasks = _load()
        task = {"id": uuid.uuid4().hex[:8], "text": text, "done": False,
                "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "due": _parse_when(due),
                "remind_at": _parse_when(remind_at)}
        tasks.append(task)
        ok = _save(tasks)
    return {"ok": bool(ok), "task": _normalize(task)}


def set_task_due(task_id, due=None, remind_at=None) -> dict:
    """Set (or clear, with None) a task's due / remind_at. Unknown
    id -> {"ok": False}. Clearing remind_at is how a fired reminder
    is silenced."""
    with _LOCK:
        tasks = _load()
        for task in tasks:
            if str(task.get("id")) == str(task_id):
                task["due"] = _parse_when(due)
                task["remind_at"] = _parse_when(remind_at)
                ok = _save(tasks)
                return {"ok": bool(ok), "task": _normalize(task)}
    return {"ok": False, "error": "not_found"}


def toggle_task(task_id) -> dict:
    """Flip one task's done flag. Unknown id -> {"ok": False}."""
    with _LOCK:
        tasks = _load()
        for task in tasks:
            if str(task.get("id")) == str(task_id):
                task["done"] = not bool(task.get("done"))
                ok = _save(tasks)
                return {"ok": bool(ok), "task": _normalize(task)}
    return {"ok": False, "error": "not_found"}


def delete_task(task_id) -> dict:
    """Delete one task. Unknown id -> {"ok": False}."""
    with _LOCK:
        tasks = _load()
        kept = [t for t in tasks if str(t.get("id")) != str(task_id)]
        if len(kept) == len(tasks):
            return {"ok": False, "error": "not_found"}
        ok = _save(kept)
    return {"ok": bool(ok), "deleted": True}
