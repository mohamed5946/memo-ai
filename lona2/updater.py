"""LONA v2 — in-app updater.

Rules from the field: the download runs in a background thread with
visible progress; the setup is NEVER deleted before install; the app
relaunches only after a successful install. The downloaded file is
re-hashed once and the hash is cached — the 123MB re-hash on every
Updates-page open was one of the v1.6.0 freezes.
"""

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

REPO = "mohamed5946/memo-ai"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"

_lock = threading.Lock()
_state = {"state": "idle", "progress": 0.0, "version": None,
          "error": None, "path": None, "sha256": None}
_downloaded_sha = {"path": None, "sha256": None}


def _download_dir() -> Path:
    from .stores import DATA_DIR
    return DATA_DIR / "updates"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _set(**fields):
    with _lock:
        _state.update(fields)


def get_update_status() -> dict:
    with _lock:
        return dict(_state)


def check_updates() -> dict:
    """Start a background update check. Returns {checking:true}."""
    with _lock:
        if _state["state"] == "checking":
            return {"checking": True}
        _state.update({"state": "checking", "error": None})
    thread = threading.Thread(target=_check_worker, daemon=True)
    thread.start()
    return {"checking": True}


def _check_worker() -> None:
    try:
        from . import VERSION
        latest = None
        last_error = None
        for _ in range(3):
            try:
                resp = requests.get(API_LATEST, timeout=15)
                if resp.status_code == 200:
                    latest = resp.json()
                    break
                last_error = f"GitHub رجّع {resp.status_code}"
            except requests.RequestException as exc:
                last_error = str(exc)
            time.sleep(2)
        if latest is None:
            _set(state="error", error=last_error or "تعذّر الوصول لـ GitHub")
            return
        tag = str(latest.get("tag_name", "")).lstrip("v")
        if tag and tag != VERSION:
            _set(state="available", version=tag)
        else:
            _set(state="up-to-date", version=tag or VERSION)
    except Exception as exc:
        _set(state="error", error=str(exc))


def download_update(asset_url: str) -> dict:
    """Start a background download. Returns {downloading:true}."""
    with _lock:
        if _state["state"] == "downloading":
            return {"downloading": True}
        _state.update({"state": "downloading", "progress": 0.0,
                       "error": None})
    thread = threading.Thread(target=_download_worker, daemon=True,
                              args=(asset_url,))
    thread.start()
    return {"downloading": True}


def _download_worker(asset_url: str) -> None:
    try:
        dest_dir = _download_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "LONA-Setup-new.exe"
        digest = hashlib.sha256()
        with requests.get(asset_url, stream=True, timeout=60) as resp:
            if resp.status_code != 200:
                _set(state="error",
                     error=f"التحميل فشل ({resp.status_code})")
                return
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with dest.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if total:
                        _set(progress=round(100 * done / total, 1))
        sha = digest.hexdigest()
        _downloaded_sha.update({"path": str(dest), "sha256": sha})
        _set(state="downloaded", progress=100.0, path=str(dest), sha256=sha)
    except Exception as exc:
        _set(state="error", error=str(exc))


def install_update() -> dict:
    """Install the downloaded setup. The setup file is deleted only
    AFTER a successful install, by the install script itself — never
    here, never before the installer runs."""
    with _lock:
        if _state["state"] != "downloaded" or not _state.get("path"):
            return {"ok": False, "error": "مفيش تحديث منزّل"}
        setup_path = _state["path"]
        _state["state"] = "installing"
    if not IS_WINDOWS:
        _set(state="error", error="التثبيت على ويندوز بس")
        return {"ok": False, "error": "التثبيت على ويندوز بس"}
    try:
        bat = _download_dir() / "run-setup.bat"
        bat.write_text(
            "@echo off\r\n"
            "timeout /t 3 /nobreak >nul\r\n"
            f'"{setup_path}" /SILENT\r\n'
            "if %ERRORLEVEL% EQU 0 (\r\n"
            f'  del "{setup_path}"\r\n'
            '  start "" "%LOCALAPPDATA%\\Programs\\LONA\\LONA.exe"\r\n'
            ")\r\n",
            encoding="utf-8")
        subprocess.Popen(["cmd", "/c", str(bat)],
                         creationflags=subprocess.DETACHED_PROCESS)
        return {"ok": True}
    except Exception as exc:
        _set(state="error", error=str(exc))
        return {"ok": False, "error": str(exc)}


IS_WINDOWS = sys.platform == "win32"


def verify_downloaded_sha(path: str):
    """Cached re-hash: hash the file once, reuse the result."""
    if _downloaded_sha.get("path") == path and _downloaded_sha.get("sha256"):
        return _downloaded_sha["sha256"]
    sha = _sha256_file(Path(path))
    _downloaded_sha.update({"path": path, "sha256": sha})
    return sha
