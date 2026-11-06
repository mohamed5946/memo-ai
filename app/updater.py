# Memo AI - In-app updater (v1.2, setup flow added in v1.2.1)
# Updates arrive INSIDE the app: Settings -> "التحقق من التحديثات".
# Never forced, only on the user's tap, never mid-task.
#
# How it works:
#   1. check_update() reads app/data/update.json for the manifest URL.
#      If the URL is empty, updates are simply not configured yet.
#   2. The manifest is a small JSON file hosted somewhere stable:
#        {"version": "1.3.0", "url": "https://.../memo-ai-v1.3.zip",
#         "sha256": "<hex>", "notes": "short changelog in Arabic",
#         "setup_url": "https://.../MemoAI-Setup-1.3.0.exe",   (optional)
#         "setup_sha256": "<hex>"}                            (optional)
#   3. Zip install (not frozen): install_update() re-fetches the manifest,
#      downloads the zip, verifies its SHA-256, extracts it over the app
#      folder, then the app restarts itself (see Api.install_update).
#   4. Installed exe (frozen): a source zip cannot replace the frozen exe,
#      so install_setup() downloads the new setup.exe instead, verifies its
#      SHA-256 when the manifest carries one, then runs it silently
#      (/VERYSILENT) after a 3-second delay and relaunches the app.
#      The installer itself never deletes keys/ (see memo-ai.iss).
#   5. NEVER touched: keys/ (your API keys) and app/data/ (settings,
#      rules, Q&A, logs, and this config file itself).

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from logger import error, info, warn
from settings_store import APP_DIR, BASE_DIR

CONFIG_PATH = APP_DIR / "data" / "update.json"

# v1.3.1 DOWNLOAD PROGRESS (the v1.3.0 update took ~90s with zero
# feedback, so the user thought nothing was happening). The installer
# threads update this dict per chunk; the Settings UI polls it through
# Api.get_update_progress() and renders a bar + "x MB / y MB (z%)".
_PROGRESS = {"state": "idle",           # idle|downloading|verifying|done|error
             "downloaded": 0,           # bytes written so far
             "total": None,             # bytes (Content-Length) or None
             "percent": None,           # 0-100 or None when total unknown
             "version": ""}
_PROGRESS_LOCK = threading.Lock()
_PROGRESS_LOG_STEP = 25                 # log every 25% (not every chunk)


def get_progress() -> dict:
    """A copy of the current download progress (safe to call any time)."""
    with _PROGRESS_LOCK:
        return dict(_PROGRESS)


def _set_progress(**fields):
    with _PROGRESS_LOCK:
        _PROGRESS.update(fields)


def _note_downloaded(total, downloaded, version):
    """Update the progress dict per chunk; log every 25% only."""
    percent = None
    if total:
        percent = min(100, int(downloaded * 100 / total))
    with _PROGRESS_LOCK:
        prev = _PROGRESS.get("percent")
    # Single update path: _set_progress owns the dict write.
    _set_progress(state="downloading", downloaded=downloaded,
                  total=total, percent=percent, version=version)
    if percent is not None and prev is not None and \
            percent // _PROGRESS_LOG_STEP > prev // _PROGRESS_LOG_STEP:
        info("update", f"Downloading v{version}: {percent}% "
                       f"({downloaded / 1048576:.1f} MB"
                       f"{' / ' + format(total / 1048576, '.1f') + ' MB' if total else ''})")

# Top-level entries (and app/data as a whole) that an update must never touch.
PRESERVE_TOP = {"keys"}
PRESERVE_DATA = True               # app/data/* : settings, rules, qa, logs


def load_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(cfg, dict):
                return cfg
    except Exception as exc:
        warn("update", f"Could not read update.json, updates disabled: {exc}")
    return {}


def _version_tuple(version: str) -> tuple:
    parts = str(version).strip().lstrip("v").split(".")
    out = []
    for p in parts[:3]:
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def _fetch_manifest():
    """Return the manifest dict, or None (already logged)."""
    cfg = load_config()
    url = str(cfg.get("manifest_url", "") or "").strip()
    if not url:
        info("update", "Updates not configured yet (update.json has no manifest_url)")
        return None
    # v1.3.1 CACHE-BUSTER: raw.githubusercontent serves the manifest
    # with cache headers, so checks at 01:02/01:06 still said "already
    # latest" after v1.3.0 was published at 01:04 (found only at
    # 01:08:58). A timestamp query param forces a fresh read every time.
    try:
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query))
        query["t"] = str(int(time.time()))
        url = urlunsplit(parts._replace(query=urlencode(query)))
    except Exception:
        pass
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        manifest = resp.json()
        if not isinstance(manifest, dict) or "version" not in manifest:
            error("update", "Manifest is not a valid version.json (no 'version')")
            return None
        return manifest
    except Exception as exc:
        error("update", f"Could not fetch update manifest from {url}: {exc}")
        return None


def check_update() -> dict:
    """Called by the Settings button. Never downloads anything."""
    from main import VERSION
    manifest = _fetch_manifest()
    if manifest is None:
        cfg = load_config()
        if not str(cfg.get("manifest_url", "") or "").strip():
            return {"status": "not_configured", "current": VERSION}
        return {"status": "error", "current": VERSION,
                "message": "مقدرتش أوصل لملف التحديثات — شوف السجل."}
    remote = str(manifest["version"])
    if _version_tuple(remote) <= _version_tuple(VERSION):
        info("update", f"Already on latest version (v{VERSION})")
        return {"status": "up_to_date", "current": VERSION}
    info("update", f"Update available: v{VERSION} -> v{remote}")
    setup_url = str(manifest.get("setup_url", "") or "").strip()
    kind = "setup" if (getattr(sys, "frozen", False) and setup_url) else "zip"
    return {"status": "update_available", "current": VERSION,
            "version": remote, "notes": str(manifest.get("notes", "")),
            "url": str(manifest.get("url", "")),
            "sha256": str(manifest.get("sha256", "")),
            "setup_url": setup_url, "kind": kind}


def _download_file(url: str, dest: Path, version: str,
                   timeout: int) -> str | None:
    """Stream a file to dest, tracking progress (v1.3.1) and returning
    its SHA-256 hex, or None on failure (already logged)."""
    _set_progress(state="downloading", downloaded=0, total=None,
                  percent=None, version=version)
    resp = requests.get(url, timeout=timeout, stream=True)
    resp.raise_for_status()
    try:
        total = int(resp.headers.get("Content-Length") or 0) or None
    except ValueError:
        total = None
    sha = hashlib.sha256()
    downloaded = 0
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(1024 * 64):
            if chunk:
                sha.update(chunk)
                f.write(chunk)
                downloaded += len(chunk)
                _note_downloaded(total, downloaded, version)
    _set_progress(state="verifying")
    return sha.hexdigest()


def _safe_extract(zip_path: Path, dest: Path):
    """Extract a zip without zip-slip (no absolute paths, no '..')."""
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            name = member.filename.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"Unsafe zip entry: {member.filename}")
        zf.extractall(dest)


def install_update() -> bool:
    """Download + verify + extract the update. Returns True on success.
    Runs in a background thread (see Api.install_update); every step is logged."""
    manifest = _fetch_manifest()
    if manifest is None:
        return False
    from main import VERSION
    remote = str(manifest.get("version", ""))
    if _version_tuple(remote) <= _version_tuple(VERSION):
        info("update", "Install aborted: already on latest version")
        return False
    url = str(manifest.get("url", "") or "").strip()
    expected_sha = str(manifest.get("sha256", "") or "").strip().lower()
    if not url:
        error("update", "Manifest has no download 'url'")
        return False
    if not expected_sha:
        error("update", "Manifest has no 'sha256' - refusing to install unverified zip")
        return False

    try:
        info("update", f"Downloading v{remote} ...")
        tmp = Path(tempfile.mkdtemp(prefix="memo-ai-update-"))
        zip_path = tmp / "update.zip"
        got_sha = _download_file(url, zip_path, remote, timeout=120)
        if got_sha is None:
            _set_progress(state="error")
            shutil.rmtree(tmp, ignore_errors=True)
            return False
        if got_sha != expected_sha:
            error("update", "SHA-256 mismatch - update ABORTED, nothing was changed")
            _set_progress(state="error")
            shutil.rmtree(tmp, ignore_errors=True)
            return False
        info("update", "SHA-256 verified, extracting...")

        extract_dir = tmp / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)
        _safe_extract(zip_path, extract_dir)
        # Our zips contain a single top-level memo-ai/ folder.
        children = [p for p in extract_dir.iterdir() if p.is_dir()]
        root = children[0] if len(children) == 1 else extract_dir
        if not (root / "app" / "main.py").exists():
            error("update", "Update zip has no app/main.py - aborting, nothing changed")
            shutil.rmtree(tmp, ignore_errors=True)
            return False

        copied = 0
        for item in root.iterdir():
            if item.name in PRESERVE_TOP:
                continue
            dest = BASE_DIR / item.name
            if item.is_dir():
                if item.name == "app" and PRESERVE_DATA:
                    # Copy app/ but keep the existing app/data/ untouched.
                    for sub in item.iterdir():
                        if sub.name == "data":
                            continue
                        sub_dest = dest / sub.name
                        if sub.is_dir():
                            shutil.copytree(sub, sub_dest, dirs_exist_ok=True)
                        else:
                            shutil.copy2(sub, sub_dest)
                        copied += 1
                else:
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                    copied += 1
            else:
                shutil.copy2(item, dest)
                copied += 1
        info("update", f"Update v{remote} installed ({copied} entries copied over, "
                       f"keys/ and app/data/ preserved)")
        _set_progress(state="done")
        shutil.rmtree(tmp, ignore_errors=True)
        return True
    except Exception as exc:
        error("update", f"Install failed (nothing may have changed): {exc}")
        _set_progress(state="error")
        return False


def install_setup() -> bool:
    """Frozen installs only: download the new setup.exe and run it silently.

    A source zip cannot replace the frozen exe, so the update comes as the
    installer itself. The installer overwrites the program files but never
    deletes keys/ (see installer/memo-ai.iss). Returns True when the
    installer was launched; the caller (Api.install_update) then quits the
    app so the installer can replace the running files. A small .bat waits
    3 seconds (app exit), runs the setup silently, then relaunches the app.
    """
    if not getattr(sys, "frozen", False):
        error("update", "install_setup called on a non-frozen install")
        return False
    manifest = _fetch_manifest()
    if manifest is None:
        return False
    from main import VERSION
    remote = str(manifest.get("version", ""))
    if _version_tuple(remote) <= _version_tuple(VERSION):
        info("update", "Install aborted: already on latest version")
        return False
    setup_url = str(manifest.get("setup_url", "") or "").strip()
    if not setup_url:
        error("update", "Manifest has no 'setup_url' - frozen installs "
                        "cannot update from a source zip; download the "
                        "setup.exe from the releases page instead")
        return False
    expected_sha = str(manifest.get("setup_sha256", "") or "").strip().lower()

    try:
        info("update", f"Downloading setup v{remote} ...")
        tmp = Path(tempfile.mkdtemp(prefix="memo-ai-update-"))
        setup_path = tmp / "MemoAI-Setup.exe"
        got_sha = _download_file(setup_url, setup_path, remote, timeout=300)
        if got_sha is None:
            _set_progress(state="error")
            shutil.rmtree(tmp, ignore_errors=True)
            return False
        if expected_sha:
            if got_sha != expected_sha:
                error("update", "Setup SHA-256 mismatch - update ABORTED")
                _set_progress(state="error")
                shutil.rmtree(tmp, ignore_errors=True)
                return False
            info("update", "Setup SHA-256 verified")
        else:
            warn("update", "Manifest has no 'setup_sha256' - installing "
                           "the setup downloaded over HTTPS from the "
                           "official releases page without a hash check")
        _set_progress(state="done")
        app_exe = BASE_DIR / "Memo AI.exe"
        bat = tmp / "run-setup.bat"
        bat.write_text(
            "@echo off\r\n"
            "timeout /t 3 /nobreak >nul\r\n"
            f'"{setup_path}" /VERYSILENT /NORESTART\r\n'
            f'start "" "{app_exe}"\r\n',
            encoding="utf-8",
        )
        info("update", f"Launching silent setup ({setup_path}) - app will quit now")
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x00000008 | 0x08000000  # DETACHED + NO_WINDOW
        subprocess.Popen(["cmd", "/c", str(bat)], close_fds=True, **kwargs)
        return True
    except Exception as exc:
        error("update", f"Setup install failed (nothing may have changed): {exc}")
        _set_progress(state="error")
        return False
