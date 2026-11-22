# LONA v1.6.4 reskin - island feature state store.
#
# Island method adapted from WindyLand (MIT, Copyright (c) 2026 Shabab)
# - method and interaction ideas only; no WindyLand code, palette,
# icons, or assets used. Full license text: THIRD_PARTY.md (repo root).
#
# Tiny atomic JSON store for the island panels (clipboard history,
# file shelf, water, timer). Same shape as tasks.py on purpose:
# one file per feature under DATA_DIR, lock-guarded, atomic replace,
# never raises - a corrupt or missing file reads as the default.

import json
import threading

from settings_store import DATA_DIR

_LOCK = threading.Lock()


def _path(name: str):
    return DATA_DIR / f"island_{name}.json"


def load(name: str, default):
    """Read the stored value, or `default` when missing/corrupt."""
    try:
        path = _path(name)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, type(default)) or data is None:
                return data
    except Exception:
        pass
    return default


def save(name: str, data) -> bool:
    """Atomic write. Returns False on failure (never raises)."""
    try:
        with _LOCK:
            path = _path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(path)
        return True
    except Exception:
        return False
