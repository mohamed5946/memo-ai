# Memo AI - In-app updater (v1.2, setup flow added in v1.2.1)
# Updates arrive INSIDE the app: Settings -> "التحقق من التحديثات".
# Never forced, only on the user's tap, never mid-task.
#
# v1.4.3 - DOWNLOAD IS SEPARATED FROM INSTALL (user's choice):
#   1. check_update() reads app/data/update.json for the manifest URL.
#      If the URL is empty, updates are simply not configured yet.
#   2. The manifest is a small JSON file hosted somewhere stable:
#        {"version": "1.3.0", "url": "https://.../memo-ai-v1.3.zip",
#         "sha256": "<hex>", "notes": "short changelog in Arabic",
#         "setup_url": "https://.../MemoAI-Setup-1.3.0.exe",   (optional)
#         "setup_sha256": "<hex>",                            (optional)
#         "features": [{"icon": "💬", "title": "...",          (optional)
#                       "desc": "..."}, ...]}  (see parse_features)
#   3. download_update() fetches the manifest, downloads the zip/setup
#      into the PERSISTENT folder app/data/updates/ (never a temp dir
#      that gets wiped), verifies its SHA-256, and records it in
#      app/data/update_downloaded.json. State becomes "downloaded".
#      NOTHING IS INSTALLED at this point - no auto-install, ever.
#   4. The user then chooses, in the Apple-style "What's new" slide:
#      "Install now"  -> apply_downloaded() installs the saved file
#                        (zip: extract over the app folder; frozen:
#                        run the saved setup.exe silently) and the app
#                        restarts, exactly like the old flow.
#      "Install later" -> the slide closes, the downloaded file STAYS
#                        on disk, and a reminder flag (dated) makes
#                        sure the reminder shows at most once a day /
#                        once a session. Settings -> Updates keeps an
#                        "Install the downloaded update" button.
#   5. If the saved file is deleted from disk or its SHA no longer
#      matches, apply_downloaded() refuses it ({"redownload": True})
#      and the UI re-downloads instead of installing a broken file.
#   6. NEVER touched: keys/ (your API keys) and app/data/ (settings,
#      rules, Q&A, logs, and this config file itself) - except that
#      app/data/updates/ is where downloads now live, and it is
#      preserved across zip updates like the rest of app/data/.
#   Legacy one-shot helpers install_update() / install_setup()
#   (download + install in one call) are kept for compatibility and
#   are implemented as download_update() + apply_downloaded(); the
#   UI no longer calls them directly.

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from logger import error, info, warn
from settings_store import APP_DIR, BASE_DIR

CONFIG_PATH = APP_DIR / "data" / "update.json"
# v1.4.3: persistent home for downloaded installers (NOT a temp dir -
# the file must survive "Install later" and app restarts) and the
# JSON file recording which update is downloaded and ready.
DOWNLOAD_DIR = APP_DIR / "data" / "updates"
DOWNLOADED_STATE_PATH = APP_DIR / "data" / "update_downloaded.json"

# v1.3.1 DOWNLOAD PROGRESS (the v1.3.0 update took ~90s with zero
# feedback, so the user thought nothing was happening). The installer
# threads update this dict per chunk; the Settings UI polls it through
# Api.get_update_progress() and renders a bar + "x MB / y MB (z%)".
# v1.4.3: a new terminal state, "downloaded" - the file is on disk,
# verified, and waiting for the user's Install-now / Install-later
# choice. Nothing installs while the state is "downloaded".
_PROGRESS = {"state": "idle",           # idle|downloading|verifying|downloaded|done|error
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


# ---------------------------------------------------------------------------
# v1.4.3: "What's new" features - structured list when the manifest has
# one, plain-notes fallback when it doesn't.
#
# MANIFEST FORMAT (for the release integrator - add this to version.json
# when publishing a release; every field is optional except "title"):
#   "features": [
#     {"icon": "💬", "title": "إصلاح الرد على الواتساب",
#      "desc": "تعريف المرسل LID + قارئ stdout بـ UTF-8 ميموتش"},
#     {"icon": "🧠", "title": "ذاكرة طويلة",
#      "desc": "البرنامج بيفتكر اللي اتفقتوا عليه"},
#     {"icon": "🔍", "title": "بحث ويب مجاني", "desc": "إجابات بأحدث المعلومات"}
#   ]
# Notes on the format:
#   * "icon" - any single emoji / glyph; omitted -> a default from the
#     cycle below (✨ 🛠️ 🧠 💬 🔍 ...). "title" - short, one line.
#     "desc" - one line of description (may be empty).
#   * A plain string instead of an object is also accepted and becomes
#     the card title ("features": ["إصلاح الرد على الواتساب", ...]).
#   * If "features" is absent/empty, the UI falls back to splitting
#     the plain "notes" text (see _features_from_notes): each chunk
#     separated by a newline, "•" or "—" becomes one card.
# ---------------------------------------------------------------------------
_FEATURE_ICONS = ["✨", "🛠️", "🧠", "💬", "🔍", "⚡", "🎨", "🔒"]


def _features_from_notes(notes: str) -> list:
    """Split a plain-text changelog into feature cards.

    The current manifests write notes as one long Arabic string with
    " — " between features (e.g. v1.4.2). Splitting on newlines, "•"
    and "—" recovers one card per feature. A leading "vX.Y.Z:" prefix
    on the first chunk is stripped (it is the version, not a feature).
    A "Title: description" chunk keeps the short head as the title.
    """
    chunks = re.split(r"[\n•—]+", str(notes or ""))
    out = []
    for i, raw in enumerate(chunks):
        text = raw.strip().lstrip("- ").strip()
        if i == 0:
            text = re.sub(r"^v?\d+(?:\.\d+){1,2}\s*:\s*", "", text)
        if not text:
            continue
        title, desc = text, ""
        if ":" in text:
            head, rest = text.split(":", 1)
            if len(head.strip()) <= 40 and rest.strip():
                title, desc = head.strip(), rest.strip()
        if len(title) > 60 and not desc:
            # Long one-liner: first clause is the title, rest the desc.
            cut = title.rfind(" ", 0, 60)
            if cut > 20:
                title, desc = title[:cut].strip(), title[cut:].strip()
        out.append({"icon": _FEATURE_ICONS[len(out) % len(_FEATURE_ICONS)],
                    "title": title, "desc": desc})
    return out


def parse_features(manifest: dict) -> list:
    """Normalize a manifest into [{"icon", "title", "desc"}, ...].

    Structured "features" win when present; otherwise fall back to
    splitting "notes". Always returns a list (possibly empty)."""
    if not isinstance(manifest, dict):
        return []
    raw = manifest.get("features")
    if isinstance(raw, list) and raw:
        out = []
        for i, item in enumerate(raw):
            if isinstance(item, str):
                title, desc = item.strip(), ""
            elif isinstance(item, dict):
                title = str(item.get("title") or item.get("text") or "").strip()
                desc = str(item.get("desc") or item.get("description") or "").strip()
                if not title:
                    continue
            else:
                continue
            if not title:
                continue
            icon = ""
            if isinstance(item, dict):
                icon = str(item.get("icon") or "").strip()
            out.append({"icon": icon or _FEATURE_ICONS[i % len(_FEATURE_ICONS)],
                        "title": title, "desc": desc})
        if out:
            return out
    return _features_from_notes(str(manifest.get("notes", "") or ""))


# ---------------------------------------------------------------------------
# v1.4.3: downloaded-update state (app/data/update_downloaded.json).
#   {"version": "1.4.3", "kind": "zip"|"setup", "path": "<absolute>",
#    "sha256": "<hex>", "size": 12345, "downloaded_at": "ISO timestamp",
#    "features": [{"icon", "title", "desc"}, ...],
#    "reminder_shown_date": "2026-11-07" | null}
# "reminder_shown_date" is the day the "update ready" reminder was last
# shown, so the reminder appears at most once per day (and the UI also
# keeps a per-session flag, so it never pops twice in one session).
# ---------------------------------------------------------------------------
def _state_path() -> Path:
    return DOWNLOADED_STATE_PATH


def load_downloaded() -> dict:
    """The saved downloaded-update record, or {} when there is none."""
    try:
        if _state_path().exists():
            data = json.loads(_state_path().read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("version"):
                return data
    except Exception as exc:
        warn("update", f"Could not read downloaded-update state: {exc}")
    return {}


def _save_downloaded(state: dict):
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(state, ensure_ascii=False, indent=2),
                             encoding="utf-8")


def clear_downloaded():
    """Forget the downloaded update (also deletes the saved file)."""
    state = load_downloaded()
    path = state.get("path")
    if path:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass
    try:
        _state_path().unlink(missing_ok=True)
    except Exception:
        pass


def _sha256_file(path: Path) -> str | None:
    try:
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 64), b""):
                sha.update(chunk)
        return sha.hexdigest()
    except Exception:
        return None


def get_downloaded_update() -> dict:
    """What the UI needs about a downloaded-but-not-installed update.

    {"downloaded": bool, "valid": bool, "version": "", "kind": "",
     "path": "", "size": 0, "features": [...],
     "remind": bool}  # True only when a valid download is waiting AND
    # the reminder has NOT been shown yet today.
    "valid" is False when the file is gone or its SHA no longer
    matches - the UI must then re-download, never install it.
    """
    state = load_downloaded()
    if not state:
        return {"downloaded": False, "valid": False, "version": "",
                "kind": "", "path": "", "size": 0, "features": [],
                "remind": False}
    path = Path(str(state.get("path") or ""))
    valid = bool(path.is_file())
    if valid and state.get("sha256"):
        valid = _sha256_file(path) == str(state["sha256"]).lower()
    today = date.today().isoformat()
    remind = bool(valid and state.get("reminder_shown_date") != today)
    return {"downloaded": True, "valid": valid,
            "version": str(state.get("version", "")),
            "kind": str(state.get("kind", "")),
            "path": str(path), "size": int(state.get("size") or 0),
            "features": state.get("features") or [],
            "remind": remind}


def mark_reminder_shown(day: str | None = None):
    """Record that the 'update ready' reminder was shown (default: today).

    The reminder then stays quiet until tomorrow (and the UI's
    per-session flag stops it re-appearing in the same session)."""
    state = load_downloaded()
    if not state:
        return
    state["reminder_shown_date"] = day or date.today().isoformat()
    _save_downloaded(state)


def should_remind() -> bool:
    return bool(get_downloaded_update().get("remind"))


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
    # v1.4.2 RETRY: the v1.4.1 field log showed the first manifest fetch
    # failing on a transient "Read timed out (read timeout=15)" while the
    # retry worked - so fetch up to 3 times with backoff (2s, 5s) before
    # giving up. A single transient network blip must not hide an update.
    last_exc = None
    for attempt in (1, 2, 3):
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            manifest = resp.json()
            if not isinstance(manifest, dict) or "version" not in manifest:
                error("update", "Manifest is not a valid version.json (no 'version')")
                return None
            if attempt > 1:
                info("update", f"Manifest fetched on attempt {attempt}/3")
            return manifest
        except Exception as exc:
            last_exc = exc
            if attempt < 3:
                wait = 2 if attempt == 1 else 5
                warn("update", f"Manifest fetch attempt {attempt}/3 failed "
                               f"({exc}) - retrying in {wait}s")
                time.sleep(wait)
    error("update", f"Could not fetch update manifest from {url} "
                    f"(3 attempts): {last_exc}")
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
            "features": parse_features(manifest),
            "url": str(manifest.get("url", "")),
            "sha256": str(manifest.get("sha256", "")),
            "setup_url": setup_url,
            "setup_sha256": str(manifest.get("setup_sha256", "")),
            "kind": kind,
            # v1.4.3: if this version was already downloaded (Install
            # later), the UI can offer "Install the downloaded update"
            # instead of downloading it again.
            "downloaded": get_downloaded_update()}


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


def _pick_download(manifest: dict) -> dict | None:
    """Choose which file this install downloads (zip vs setup.exe).

    Frozen installs must use the setup (a source zip cannot replace
    the frozen exe); anything else uses the zip. Returns
    {"kind", "url", "sha256"} or None (already logged)."""
    from main import VERSION
    remote = str(manifest.get("version", ""))
    if _version_tuple(remote) <= _version_tuple(VERSION):
        info("update", "Update aborted: already on latest version")
        return None
    setup_url = str(manifest.get("setup_url", "") or "").strip()
    if getattr(sys, "frozen", False):
        if not setup_url:
            error("update", "Manifest has no 'setup_url' - frozen installs "
                            "cannot update from a source zip; download the "
                            "setup.exe from the releases page instead")
            return None
        return {"kind": "setup", "url": setup_url,
                "sha256": str(manifest.get("setup_sha256", "") or "").strip().lower()}
    url = str(manifest.get("url", "") or "").strip()
    expected_sha = str(manifest.get("sha256", "") or "").strip().lower()
    if not url:
        error("update", "Manifest has no download 'url'")
        return None
    if not expected_sha:
        error("update", "Manifest has no 'sha256' - refusing to download an unverified zip")
        return None
    return {"kind": "zip", "url": url, "sha256": expected_sha}


def _download_filename(kind: str, version: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(version))
    if kind == "setup":
        return f"MemoAI-Setup-v{safe}.exe"
    return f"memo-ai-v{safe}.zip"


def download_update() -> dict:
    """v1.4.3: download ONLY - never installs anything.

    Fetches the manifest, downloads the update into the persistent
    app/data/updates/ folder, verifies its SHA-256, records it in
    update_downloaded.json and sets progress state "downloaded".
    Returns {"ok": True, "status": "downloaded", "version", "kind",
    "path", "features"} on success, {"ok": False, "error": ...} on
    failure (partial file deleted, no state saved). The user still
    has to tap "Install now" - there is no automatic install.
    Runs in a background thread (see Api.download_update)."""
    manifest = _fetch_manifest()
    if manifest is None:
        _set_progress(state="error")
        return {"ok": False, "error": "manifest"}
    pick = _pick_download(manifest)
    if pick is None:
        _set_progress(state="error")
        return {"ok": False, "error": "no_update"}
    remote = str(manifest.get("version", ""))
    features = parse_features(manifest)
    try:
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dest = DOWNLOAD_DIR / _download_filename(pick["kind"], remote)
        # A stale file from an older attempt must not be mistaken for
        # this download (or pass a SHA check it never went through).
        if dest.exists():
            dest.unlink()
        info("update", f"Downloading v{remote} ({pick['kind']}) ...")
        got_sha = _download_file(pick["url"], dest, remote,
                                 timeout=300 if pick["kind"] == "setup" else 120)
        if got_sha is None:
            _set_progress(state="error")
            dest.unlink(missing_ok=True)
            return {"ok": False, "error": "download"}
        if pick["sha256"]:
            if got_sha != pick["sha256"]:
                error("update", "SHA-256 mismatch - download ABORTED, file deleted")
                _set_progress(state="error")
                dest.unlink(missing_ok=True)
                return {"ok": False, "error": "sha_mismatch"}
            info("update", "SHA-256 verified")
        else:
            warn("update", "Manifest has no 'setup_sha256' - the setup was "
                           "downloaded over HTTPS from the official releases "
                           "page without a hash check")
        _save_downloaded({
            "version": remote, "kind": pick["kind"], "path": str(dest),
            "sha256": got_sha, "size": dest.stat().st_size,
            "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "features": features, "reminder_shown_date": None})
        _set_progress(state="downloaded", version=remote)
        info("update", f"Update v{remote} downloaded and ready "
                       f"({dest}, {dest.stat().st_size / 1048576:.1f} MB) - "
                       f"waiting for the user's choice, nothing installed")
        return {"ok": True, "status": "downloaded", "version": remote,
                "kind": pick["kind"], "path": str(dest), "features": features}
    except Exception as exc:
        error("update", f"Download failed: {exc}")
        _set_progress(state="error")
        return {"ok": False, "error": str(exc)}


def _apply_zip(zip_path: Path, version: str) -> bool:
    """Extract a verified zip over the app folder (keys/ + app/data/
    preserved). Returns True on success."""
    tmp = Path(tempfile.mkdtemp(prefix="memo-ai-update-"))
    try:
        extract_dir = tmp / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)
        _safe_extract(zip_path, extract_dir)
        # Our zips contain a single top-level memo-ai/ folder.
        children = [p for p in extract_dir.iterdir() if p.is_dir()]
        root = children[0] if len(children) == 1 else extract_dir
        if not (root / "app" / "main.py").exists():
            error("update", "Update zip has no app/main.py - aborting, nothing changed")
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
        info("update", f"Update v{version} installed ({copied} entries copied over, "
                       f"keys/ and app/data/ preserved)")
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _launch_setup(setup_path: Path) -> bool:
    """Run a downloaded setup.exe silently and relaunch the app.

    A small .bat waits 3 seconds (app exit), runs the setup silently,
    then relaunches the app. Returns True when launched. NOTE: the
    .bat + setup live in the persistent updates folder (NOT a temp
    dir), because "Install later" means the file must still be there
    when the user finally taps "Install now"."""
    app_exe = BASE_DIR / "Memo AI.exe"
    bat = setup_path.parent / "run-setup.bat"
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


def apply_downloaded() -> dict:
    """v1.4.3: install ONLY a previously downloaded update.

    This is what "Install now" calls. It validates the saved file
    first: no record -> {"ok": False, "error": "no_download"}; file
    deleted from disk or SHA mismatch -> the record is cleared and
    {"ok": False, "error": "missing"|"corrupt", "redownload": True}
    so the UI re-downloads instead of installing a broken file.
    A stale record (version <= current) is cleared the same way.
    On success the record + file are consumed (deleted) and
    {"ok": True, "status": "installed", "version", "kind"} is
    returned; the caller then restarts the app (see Api.install_update).
    """
    from main import VERSION
    state = load_downloaded()
    if not state:
        return {"ok": False, "error": "no_download", "redownload": False}
    remote = str(state.get("version", ""))
    if _version_tuple(remote) <= _version_tuple(VERSION):
        info("update", "Downloaded update is no longer newer - discarding it")
        clear_downloaded()
        return {"ok": False, "error": "stale", "redownload": False}
    path = Path(str(state.get("path") or ""))
    if not path.is_file():
        error("update", "Downloaded update file is gone from disk - "
                        "will re-download instead of installing")
        clear_downloaded()
        return {"ok": False, "error": "missing", "redownload": True}
    expected = str(state.get("sha256") or "").strip().lower()
    if expected:
        got = _sha256_file(path)
        if got != expected:
            error("update", "Downloaded update SHA-256 mismatch - "
                            "file discarded, will re-download")
            clear_downloaded()
            return {"ok": False, "error": "corrupt", "redownload": True}
    kind = str(state.get("kind", "zip"))
    try:
        if kind == "setup":
            ok = _launch_setup(path)
        else:
            ok = _apply_zip(path, remote)
        if not ok:
            _set_progress(state="error")
            return {"ok": False, "error": "apply_failed", "redownload": False}
        # Installed: the downloaded file is consumed (the new version
        # brings its own files; a setup leaves nothing to keep).
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            _state_path().unlink(missing_ok=True)
        except Exception:
            pass
        _set_progress(state="done", version=remote)
        return {"ok": True, "status": "installed", "version": remote,
                "kind": kind}
    except Exception as exc:
        error("update", f"Install failed (nothing may have changed): {exc}")
        _set_progress(state="error")
        return {"ok": False, "error": str(exc), "redownload": False}


def install_update() -> bool:
    """Legacy one-shot helper (download + install). Kept for
    compatibility; the UI now calls download_update() then
    apply_downloaded() so the user chooses when to install."""
    result = download_update()
    if not result.get("ok"):
        return False
    return bool(apply_downloaded().get("ok"))


def install_setup() -> bool:
    """Legacy one-shot helper for frozen installs (download the new
    setup.exe and run it silently). Kept for compatibility; the UI
    now uses download_update() + apply_downloaded() instead.

    A source zip cannot replace the frozen exe, so the update comes as
    the installer itself. The installer overwrites the program files
    but never deletes keys/ (see installer/memo-ai.iss). Returns True
    when the installer was launched; the caller (Api.install_update)
    then quits the app so the installer can replace the running files.
    """
    if not getattr(sys, "frozen", False):
        error("update", "install_setup called on a non-frozen install")
        return False
    result = download_update()
    if not result.get("ok"):
        return False
    return bool(apply_downloaded().get("ok"))
