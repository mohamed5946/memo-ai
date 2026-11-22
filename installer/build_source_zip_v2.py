#!/usr/bin/env python3
"""Build the LONA v2 source zip (lona-v2.0.0.zip) from the repo root.

The zip contains a single top-level lona/ folder with the files needed
to run v2 from source (and for a manual update):
  README.md, lona.spec, lona2/**, installer/lona.iss, installer/lona.ico,
  installer/file_version_info_v2.txt, installer/build_source_zip_v2.py,
  scripts/security_check_v2.py, keys/README.txt

Excluded on purpose: the v1 app/ tree (dead weight in the v2 branch —
it is never built from and never runs), the Phase 2 extension/,
.github, .git, installer v1 files, __pycache__, *.pyc, .gitignore,
keys/*.txt (real keys - never shipped), version.json (it carries the
zip's own SHA-256, so including it would make the hash self-referential
— chicken-and-egg).

The build is DETERMINISTIC: entries are sorted and every file gets the
same fixed timestamp, so the zip built on this machine and the one built
by GitHub Actions are byte-identical — and the SHA-256 in version.json
matches the zip attached to the release.

Usage:
  python installer/build_source_zip_v2.py [version] [output-dir]
Defaults: version is read from lona2/__init__.py, output dir is the
repo root.
"""

import hashlib
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Fixed timestamp for every entry (deterministic builds).
FIXED_DATE = (2026, 1, 1, 0, 0, 0)

# Only these top-level entries go in the zip (everything else in the
# repo is v1 leftovers, CI config, or build output).
INCLUDE_TOP = {"README.md", "lona.spec", "lona2", "scripts", "installer",
               "keys"}
INCLUDE_FILES = {
    "installer/lona.iss",
    "installer/lona.ico",
    "installer/file_version_info_v2.txt",
    "installer/build_source_zip_v2.py",
    "scripts/security_check_v2.py",
    "keys/README.txt",
}
EXCLUDE_DIRS = {".git", ".github", "__pycache__", "release", "dist",
                "build", "node_modules"}
# version.json is excluded on purpose: it carries the zip's own SHA-256.
EXCLUDE_FILES = {".gitignore", "version.json"}


def _version() -> str:
    text = (ROOT / "lona2" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        raise RuntimeError("VERSION not found in lona2/__init__.py")
    return match.group(1)


def _include(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if rel.parts[0] not in INCLUDE_TOP:
        return False
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return False
    if rel.as_posix() in EXCLUDE_FILES:
        return False
    if not path.is_file():
        return False
    if path.suffix in {".pyc", ".pyo", ".zip"}:
        return False
    top = rel.parts[0]
    if top in ("installer", "scripts", "keys"):
        return rel.as_posix() in INCLUDE_FILES
    return True


def build(version: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"lona-v{version}.zip"
    files = sorted(
        (p for p in ROOT.rglob("*") if p.is_file() and _include(p)),
        key=lambda p: p.relative_to(ROOT).as_posix(),
    )
    if not any(p.name == "main.py" for p in files):
        raise RuntimeError("lona2/main.py missing from the zip file list")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as zf:
        for path in files:
            rel = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(f"lona/{rel}", date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    return zip_path


def main() -> int:
    version = sys.argv[1] if len(sys.argv) > 1 else None
    if not version:
        version = _version()
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT
    zip_path = build(version, out_dir)
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        names = zf.namelist()
    tops = sorted({n.split("/")[0] for n in names})
    assert bad is None, f"zip corrupt: {bad}"
    assert tops == ["lona"], f"unexpected top-level entries: {tops}"
    assert not any(n.endswith(".txt") and "/keys/" in n and "README" not in n
                   for n in names), "real key file leaked into the zip"
    assert not any("__pycache__" in n or "/.git/" in n
                   or n.startswith("lona/.git") for n in names), \
        "excluded files leaked into the zip"
    assert not any(n.startswith("lona/app/") for n in names), \
        "v1 app/ tree leaked into the v2 zip"
    assert not any(n.startswith("lona/extension/") for n in names), \
        "Phase 2 extension/ leaked into the v2 zip"
    assert not any(n.endswith("version.json") for n in names), \
        "version.json leaked into the zip (self-referential hash)"
    print(f"zip: {zip_path} ({len(names)} entries, "
          f"{zip_path.stat().st_size / 1024:.0f} KB)")
    print(f"sha256: {sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
