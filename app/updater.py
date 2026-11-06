# Memo AI - In-app updater (v1.2)
# Updates arrive INSIDE the app: Settings -> "التحقق من التحديثات".
# Never forced, only on the user's tap, never mid-task.
#
# How it works:
#   1. check_update() reads app/data/update.json for the manifest URL.
#      If the URL is empty, updates are simply not configured yet.
#   2. The manifest is a small JSON file hosted somewhere stable:
#        {"version": "1.3.0", "url": "https://.../memo-ai-v1.3.zip",
#         "sha256": "<hex>", "notes": "short changelog in Arabic"}
#   3. install_update() re-fetches the manifest, downloads the zip,
#      verifies its SHA-256, extracts it over the app folder, then the
#      app restarts itself (see Api.install_update in main.py).
#   4. NEVER touched: keys/ (your API keys) and app/data/ (settings,
#      rules, Q&A, logs, and this config file itself).
# No real manifest URL is shipped - it stays configurable in update.json
# until a stable release host is chosen.

import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import requests
from logger import error, info, warn

APP_DIR = Path(__file__).resolve().parent
BASE_DIR = APP_DIR.parent          # the memo-ai folder
CONFIG_PATH = APP_DIR / "data" / "update.json"

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
    return {"status": "update_available", "current": VERSION,
            "version": remote, "notes": str(manifest.get("notes", "")),
            "url": str(manifest.get("url", "")),
            "sha256": str(manifest.get("sha256", ""))}


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
        resp = requests.get(url, timeout=120, stream=True)
        resp.raise_for_status()
        tmp = Path(tempfile.mkdtemp(prefix="memo-ai-update-"))
        zip_path = tmp / "update.zip"
        sha = hashlib.sha256()
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(1024 * 64):
                if chunk:
                    sha.update(chunk)
                    f.write(chunk)
        if sha.hexdigest() != expected_sha:
            error("update", "SHA-256 mismatch - update ABORTED, nothing was changed")
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
        shutil.rmtree(tmp, ignore_errors=True)
        return True
    except Exception as exc:
        error("update", f"Install failed (nothing may have changed): {exc}")
        return False
