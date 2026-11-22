#!/usr/bin/env python3
"""LONA v2 — Automated security check (the checkable subset of the
27-item security list, scoped to the v2 tree).

Runs before every v2 release (CI wires it in — this script itself
never touches .github/workflows):

  1. Secret scan: API-key patterns (sk-, gsk_, AIza, xoxb-...), private
     keys (BEGIN PRIVATE KEY), and high-entropy strings in lona2/ and
     the v2 installer/workflow files. Known hash constants
     (INVITE_CODE_HASH, OWNER_CODE_HASH — hashes, not keys) are
     allowlisted by name. The v1 app/ tree is NOT scanned: it is dead
     weight in the branch, never built and never shipped in v2.
  2. Invite gate: the invite code VALUE never appears anywhere in the
     v2 tree — only the SHA-256 hash lives in source (checked: the
     hash constant equals the expected hash, and no 6-digit string
     literal sits in lona2/invite.py).
  3. requirements: lona2/requirements.txt — every dependency pinned
     (==, >=, ~= with an explicit version); a bare package name fails.
  4. Config: no debug flags in lona2/ source, no plain-http:// URLs
     outside localhost / 127.0.0.1, HTTPS-only URLs in version.json.
  5. UI: no CDN links (Google Fonts, any cdn), no Math.random, no
     emoji in lona2/ui/**; the bundled fonts (8 woff2) and the mascot
     must exist.

Usage:  python scripts/security_check_v2.py [--root <repo root>]
Exit 0 = all checks pass; exit 1 = findings (listed, one per line).

Human review + pen-testing stay manual - this script proves the
checkable subset only, it does not certify the app as secure.
"""

import argparse
import json
import math
import re
import sys
from pathlib import Path

# A long base64-ish token with Shannon entropy above this is worth
# flagging. Hex hashes top out at 4.0 bits/char, so they never trip it.
ENTROPY_THRESHOLD = 4.5
MIN_TOKEN_LEN = 32

SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{10,}"), "possible OpenAI-style key"),
    (re.compile(r"sk-proj-[A-Za-z0-9_\-]{10,}"), "possible OpenAI proj key"),
    (re.compile(r"gsk_[A-Za-z0-9]{10,}"), "possible Groq key"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "possible Google API key"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"), "possible Slack token"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
     "private key material"),
    (re.compile(r"(?i)(api[_-]?key|secret|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]"),
     "possible hardcoded secret"),
]

# Lines naming a hash constant are allowlisted: a SHA-256 hex of a code
# is not a key (it cannot be used as one), and the plaintext code is
# nowhere in the repo by design.
HASH_ALLOWLIST = ("INVITE_CODE_HASH", "OWNER_CODE_HASH")

# The SHA-256 of the 6-digit trial code. The hash itself is public by
# design (it is the only code artifact in source); the code value is
# never written anywhere.
EXPECTED_INVITE_HASH = (
    "ce0e90a75246a53c921aaf19e18f179e2f243c6442214b34ffb6091cc5e6bf03")

SCAN_EXTS = {".py", ".js", ".iss", ".json", ".md", ".txt", ".bat",
            ".yml", ".yaml", ".html", ".css", ".spec"}
SCAN_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
                  "tests", "fixtures"}
TOKEN_RE = re.compile(r"[A-Za-z0-9_\-+/=]{32,}")

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    "\U0000FE0F]")

# The v2 tree only: lona2/ + the v2 installer/workflow/script files.
# The v1 app/ tree, extension/ (Phase 2) and .github v1 workflow are
# not v2 code and are not scanned here.
V2_SCAN_PATHS = [
    "lona2",
    "lona.spec",
    "installer/lona.iss",
    "installer/file_version_info_v2.txt",
    "installer/build_source_zip_v2.py",
    "scripts/security_check_v2.py",
    ".github/workflows/build-v2.yml",
    "README.md",
]


def _shannon_entropy(text: str) -> float:
    if not text:
        return 0.0
    counts = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    total = len(text)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def _v2_files(root: Path):
    out = []
    for rel in V2_SCAN_PATHS:
        path = root / rel
        if path.is_file():
            out.append(path)
        elif path.is_dir():
            for p in sorted(path.rglob("*")):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in SCAN_EXTS:
                    continue
                if any(part in SCAN_SKIP_DIRS for part in
                       p.relative_to(root).parts):
                    continue
                out.append(p)
    return out


def _scan_secrets(root: Path) -> list:
    findings = []
    for path in _v2_files(root):
        rel = path.relative_to(root)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if any(name in line for name in HASH_ALLOWLIST):
                continue
            for pattern, label in SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        f"{rel}:{i}: {label}: {line.strip()[:120]}")
                    break
            else:
                for token in TOKEN_RE.findall(line):
                    if len(token) >= MIN_TOKEN_LEN and \
                            _shannon_entropy(token) > ENTROPY_THRESHOLD:
                        findings.append(
                            f"{rel}:{i}: high-entropy string "
                            f"({len(token)} chars, entropy "
                            f"{_shannon_entropy(token):.2f}): "
                            f"{token[:24]}...")
                        break
    return findings


def _check_invite(root: Path) -> list:
    findings = []
    invite_path = root / "lona2" / "invite.py"
    if not invite_path.is_file():
        return ["lona2/invite.py is missing"]
    text = invite_path.read_text(encoding="utf-8")
    # The INVITE_CODE_HASH assignment spans two string literals; they
    # must join to exactly the expected hash (and nothing else in the
    # file may carry a 6-digit literal).
    match = re.search(r'INVITE_CODE_HASH\s*=\s*\((.*?)\)', text, re.S)
    if not match:
        findings.append("lona2/invite.py: INVITE_CODE_HASH assignment "
                        "not found")
    else:
        joined = "".join(re.findall(r'"([0-9a-f]+)"', match.group(1)))
        if joined != EXPECTED_INVITE_HASH:
            findings.append(
                "lona2/invite.py: INVITE_CODE_HASH does not match the "
                "expected hash")
    if re.search(r'"\d{6}"', text) or re.search(r"'\d{6}'", text):
        findings.append(
            "lona2/invite.py: a 6-digit string literal sits in source "
            "(a plaintext invite code must never be committed)")
    return findings


def _check_requirements(root: Path) -> list:
    findings = []
    req = root / "lona2" / "requirements.txt"
    if not req.is_file():
        return [f"missing requirements file: {req}"]
    for i, raw in enumerate(req.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[<>=~!;\s]", line, maxsplit=1)[0]
        if not re.search(r"(==|>=|<=|~=|!=)\s*\d", line):
            findings.append(
                f"lona2/requirements.txt:{i}: unpinned dependency: {name}")
    return findings


def _check_config(root: Path) -> list:
    findings = []
    # No debug flags in lona2 source (comments stripped by a light pass:
    # only code before a '#' on the same line counts).
    for path in sorted((root / "lona2").rglob("*.py")):
        if "tests" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"\bdebug\s*=\s*True\b", code, re.I) or \
                    re.search(r"\bDEBUG\s*=\s*True\b", code):
                findings.append(f"{path.relative_to(root)}:{i}: "
                                "debug flag enabled in source")
            for m in re.finditer(r"https?://[A-Za-z0-9\-\.]+"
                                 r"\.[A-Za-z]{2,}[^\s'\"<>]*", code):
                url = m.group(0)
                if url.startswith("http://") and not (
                        url.startswith("http://localhost") or
                        url.startswith("http://127.0.0.1")):
                    findings.append(
                        f"{path.relative_to(root)}:{i}: plain-http URL: "
                        f"{url[:100]}")
    # Updater manifest: HTTPS-only URLs.
    manifest_path = root / "version.json"
    if manifest_path.is_file():
        try:
            cfg = json.loads(manifest_path.read_text(encoding="utf-8"))
            for key in ("url", "setup_url"):
                url = str((cfg or {}).get(key, "") or "").strip()
                if url and not url.startswith("https://"):
                    findings.append(
                        f"version.json: {key} is not HTTPS: {url[:100]}")
        except Exception as exc:
            findings.append(f"version.json: unreadable: {exc}")
    return findings


def _check_ui(root: Path) -> list:
    findings = []
    ui_dir = root / "lona2" / "ui"
    if not ui_dir.is_dir():
        return ["lona2/ui is missing"]
    for path in sorted(ui_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in \
                {".html", ".js", ".css"}:
            continue
        rel = path.relative_to(root)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "fonts.googleapis" in text or "fonts.gstatic" in text:
            findings.append(f"{rel}: Google Fonts CDN link (must be bundled)")
        if "http://cdn" in text or "https://cdn" in text:
            findings.append(f"{rel}: CDN link (must be bundled)")
        if "Math.random" in text:
            findings.append(f"{rel}: Math.random (demo values must come "
                            "from the bridge)")
        found = _EMOJI_RE.findall(text)
        if found:
            findings.append(f"{rel}: emoji found: {found[:5]}")
    fonts = ui_dir / "assets" / "fonts"
    woff2 = sorted(fonts.glob("*.woff2")) if fonts.is_dir() else []
    if len(woff2) < 8:
        findings.append(f"lona2/ui/assets/fonts: expected 8 woff2 files, "
                        f"got {len(woff2)}")
    mascot = ui_dir / "assets" / "mascot.webp"
    if not mascot.is_file() or mascot.stat().st_size < 10000:
        findings.append("lona2/ui/assets/mascot.webp is missing or too small")
    return findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None,
                        help="repo root (default: parent of scripts/)")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else \
        Path(__file__).resolve().parent.parent
    findings = []
    findings += _scan_secrets(root)
    findings += _check_invite(root)
    findings += _check_requirements(root)
    findings += _check_config(root)
    findings += _check_ui(root)
    if findings:
        print(f"SECURITY CHECK FAILED: {len(findings)} finding(s)")
        for f in findings:
            print(f"  - {f}")
        return 1
    print("SECURITY CHECK PASSED (v2): no secrets, invite hash only, "
          "pinned requirements, no debug flags, HTTPS-only manifest, "
          "clean UI (no CDN / Math.random / emoji), bundled fonts + mascot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
