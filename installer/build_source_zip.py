#!/usr/bin/env python3
"""Build the Memo AI source zip (memo-ai-vX.Y.Z.zip) from the repo root.

The zip contains a single top-level memo-ai/ folder with the files needed
to run the app from source (and for the in-app updater to extract):
  README.md, version.json, app/**, keys/README.txt, scripts/**

Excluded: .git, .github, installer, __pycache__, *.pyc, .gitignore,
keys/*.txt (real keys - never shipped), app/data/settings.json
(user-specific), app/data/logs/* (except the .gitkeep).

The build is DETERMINISTIC: entries are sorted and every file gets the
same fixed timestamp, so the zip built on this machine and the one built
by GitHub Actions are byte-identical — and the SHA-256 in version.json
matches the zip attached to the release.

Usage:
  python installer/build_source_zip.py [version] [output-dir]
Defaults: version is read from version.json, output dir is the repo root.
"""

import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Fixed timestamp for every entry (deterministic builds).
FIXED_DATE = (2026, 1, 1, 0, 0, 0)

EXCLUDE_DIRS = {".git", ".github", "installer", "__pycache__",
                "release", "dist", "build", "node_modules"}
# v1.4.0: the bundled Node runtime + the WhatsApp session are NEVER in
# the source zip. node.exe is a binary runtime (the installer bundles
# it; zip users run with node on PATH). whatsapp_auth/ holds Signal
# session keys - shipping them would hand over the linked WhatsApp
# account to anyone with the zip.
EXCLUDE_PREFIXES = (
    ("app", "whatsapp_sidecar", "node"),
    ("app", "data", "whatsapp_auth"),
)
# version.json is excluded on purpose: it carries the zip's own SHA-256,
# so including it would make the hash self-referential (chicken-and-egg).
EXCLUDE_FILES = {".gitignore", "version.json", "app/data/settings.json"}


def _include(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return False
    if rel.parts[:3] in EXCLUDE_PREFIXES:
        return False
    if rel.as_posix() in EXCLUDE_FILES:
        return False
    if path.is_file():
        if path.suffix in {".pyc", ".pyo", ".zip"}:
            return False
        if rel.parts[0] == "keys" and path.name != "README.txt":
            return False
        if rel.as_posix().startswith("app/data/logs/") and path.name != ".gitkeep":
            return False
    return True


def build(version: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"memo-ai-v{version}.zip"
    files = sorted(
        (p for p in ROOT.rglob("*") if p.is_file() and _include(p)),
        key=lambda p: p.relative_to(ROOT).as_posix(),
    )
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as zf:
        for path in files:
            rel = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(f"memo-ai/{rel}", date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    return zip_path


def main() -> int:
    version = sys.argv[1] if len(sys.argv) > 1 else None
    if not version:
        version = json.loads((ROOT / "version.json").read_text(
            encoding="utf-8"))["version"]
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT
    zip_path = build(version, out_dir)
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        names = zf.namelist()
    tops = sorted({n.split("/")[0] for n in names})
    assert bad is None, f"zip corrupt: {bad}"
    assert tops == ["memo-ai"], f"unexpected top-level entries: {tops}"
    assert not any(n.endswith(".txt") and "/keys/" in n and "README" not in n
                   for n in names), "real key file leaked into the zip"
    assert not any("__pycache__" in n or "/.git/" in n
                   or n.startswith("memo-ai/.git") for n in names), \
        "excluded files leaked into the zip"
    assert not any("node_modules" in n for n in names), \
        "node_modules leaked into the zip"
    assert not any(n.endswith("node.exe") for n in names), \
        "bundled node.exe leaked into the zip"
    assert not any("whatsapp_auth" in n for n in names), \
        "WhatsApp session leaked into the zip"
    print(f"zip: {zip_path} ({len(names)} entries, "
          f"{zip_path.stat().st_size / 1024:.0f} KB)")
    print(f"sha256: {sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
