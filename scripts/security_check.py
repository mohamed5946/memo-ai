#!/usr/bin/env python3
"""LONA - Automated security check (scope item 11, v1.6.0).

The CHECKABLE subset of the 27-item security list, runnable before
every release (the integrator wires it into CI - this script itself
never touches .github/workflows):

  1. Secret scan: API-key patterns (sk-, gsk_, AIza, xoxb-...), private
     keys (BEGIN PRIVATE KEY), and high-entropy strings in app/ + the
     installer script. Known hash constants (INVITE_CODE_HASH,
     OWNER_CODE_HASH - hashes, not keys) are allowlisted by name.
  2. requirements.txt: every dependency pinned (==, >=, ~= with an
     explicit version); a bare package name fails.
  3. Config: no debug flags in source (debug=True / DEBUG=True), no
     plaintext keys in source, HTTPS-only URLs in the updater manifest
     (app/data/update.json manifest_url) and no plain-http:// URLs in
     source outside localhost / 127.0.0.1.

Usage:  python scripts/security_check.py [--root <repo root>]
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

SCAN_EXTS = {".py", ".js", ".iss", ".json", ".md", ".txt", ".bat"}
SCAN_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
                  "tests", "fixtures", "whatsapp_sidecar"}
TOKEN_RE = re.compile(r"[A-Za-z0-9_\-+/=]{32,}")


def _shannon_entropy(text: str) -> float:
    if not text:
        return 0.0
    counts = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    total = len(text)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def _scan_secrets(root: Path) -> list:
    findings = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SCAN_EXTS:
            continue
        rel = path.relative_to(root)
        if any(part in SCAN_SKIP_DIRS for part in rel.parts):
            continue
        if rel.parts and rel.parts[0] == "keys":
            continue  # keys/ holds the user's own key FILES by design
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


def _check_requirements(root: Path) -> list:
    findings = []
    req = root / "app" / "requirements.txt"
    if not req.is_file():
        return [f"missing requirements file: {req}"]
    for i, raw in enumerate(req.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[<>=~!;\s]", line, maxsplit=1)[0]
        if not re.search(r"(==|>=|<=|~=|!=)\s*\d", line):
            findings.append(
                f"app/requirements.txt:{i}: unpinned dependency: {name}")
    return findings


def _check_config(root: Path) -> list:
    findings = []
    # No debug flags in app source (comments stripped by a light pass:
    # only code before a '#' on the same line counts).
    for path in sorted((root / "app").rglob("*.py")):
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
    # Updater manifest: HTTPS-only (or unconfigured = empty).
    manifest_path = root / "app" / "data" / "update.json"
    if manifest_path.is_file():
        try:
            cfg = json.loads(manifest_path.read_text(encoding="utf-8"))
            url = str((cfg or {}).get("manifest_url", "") or "").strip()
            if url and not url.startswith("https://"):
                findings.append(
                    f"app/data/update.json: manifest_url is not HTTPS: "
                    f"{url[:100]}")
        except Exception as exc:
            findings.append(f"app/data/update.json: unreadable: {exc}")
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
    findings += _check_requirements(root)
    findings += _check_config(root)
    if findings:
        print(f"SECURITY CHECK FAILED: {len(findings)} finding(s)")
        for f in findings:
            print(f"  - {f}")
        return 1
    print("SECURITY CHECK PASSED: no secrets, pinned requirements, "
          "no debug flags, HTTPS-only updater manifest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
