# LONA v2 — personal AI assistant for your laptop

A from-scratch rebuild of LONA (Python + pywebview/WebView2 on Windows).
The online brain is Groq (gpt-oss-120b) with a Gemini 2.5 Flash fallback;
API keys are entered by hand and stored in Windows Credential Manager
(DPAPI) — the program ships with no key. The local Ollama fallback is
optional and disabled until Ollama is installed.

v1.6.3 stays the stable release until this rebuild is proven in the
field. v2 releases are prereleases by design.

## What is in v2 (Phase 1)

- Frameless main window (1280x860) with working minimize / maximize /
  close traffic lights and header drag. Window buttons fetch the bridge
  fresh on every click and surface errors instead of swallowing them.
- Dynamic Island as its OWN frameless window (380x60 collapsed,
  720x400 expanded, no idle shrink). Transparency is SetWindowRgn,
  applied to the hidden window and verified on read-back before the
  window is shown — no white/black rectangle.
- Chat: Groq → Gemini fallback, Deep Think, model switcher (unavailable
  options are disabled with a reason), engine badge under every reply.
- Device page with live bridge data. CPU temperature prefers
  LibreHardwareMonitor CPU Package / Core sensors BY NAME; when only a
  thermal-zone value answers, the UI says so honestly.
- Tasks (persisted), 5 themes (Liquid Glass default; the old theme is
  never deleted), full 13-section settings — every control wired to
  real storage. Wake-word listening is OFF by default (continuous
  listening heated the laptop to 99C in the field).
- Invite gate: 6-box code entry, checked locally against a SHA-256 hash
  only (the code value never appears in source). 5 wrong attempts lock
  the gate for 60 seconds; a failed state write never fails a correct
  code.
- Owner code (PBKDF2, salted), encrypted backup (scrypt + AES-GCM),
  diagnostics log from day one (in-app viewer, secrets redacted —
  a Bearer token is removed whole, not just the word "Bearer").
- In-app updater rules: background download with progress, the setup
  file is never deleted before install, relaunch only after a
  successful install.

## What is deferred (Phase 2)

Deep-files permission flow (path optional), cache cleanup with Undo,
morning brief, WhatsApp text-only, Chrome extension re-wire, voice +
wake word, Ollama local model. The bridge already carries the methods;
the UI wires them when the features land.

## Layout

```
memo-ai/
├── lona2/                  # the v2 program (one module, one job each)
│   ├── main.py             # entry: main window + separate island window
│   ├── bridge.py           # the Api object the UI calls
│   ├── brain.py            # Groq -> Gemini, Deep Think, model switcher
│   ├── monitor.py          # device overview, cache-first, LHM by name
│   ├── winshape.py         # SetWindowRgn island clip, single instance
│   ├── webview2.py         # WebView2 detection incl. WOW6432Node
│   ├── invite.py           # invite gate (hash only)
│   ├── owner.py            # owner code (PBKDF2)
│   ├── apikeys.py          # DPAPI key store (+ v1.6.3 key migration)
│   ├── backup.py           # encrypted backup (scrypt + AES-GCM)
│   ├── stores.py           # settings / tasks / memories (JSON)
│   ├── health.py           # threshold alerts
│   ├── updater.py          # in-app update check / download / install
│   ├── logger.py           # diagnostics log with redaction
│   ├── ui/                 # Liquid Glass UI (index / island / invite)
│   │   └── assets/         # bridge.js, lona.css, fonts, mascot.webp
│   ├── tests/              # pytest suite (bridge, security, UI)
│   └── requirements.txt
├── lona.spec               # PyInstaller spec (one-folder, windowed)
├── installer/
│   ├── lona.iss            # Inno Setup: per-user, no admin, Arabic
│   ├── lona.ico            # mascot icon, 16–256px
│   ├── file_version_info_v2.txt
│   └── build_source_zip_v2.py
├── scripts/
│   └── security_check_v2.py
├── .github/workflows/
│   ├── build-v2.yml        # Windows CI for v2.* tags (prerelease)
│   └── build-installer.yml # v1 workflow — v1.* tags only
├── version.json            # updater manifest (updated by CI per release)
├── app/                    # the OLD v1.6.3 code. Untouched, never built
│                           # from, never shipped in v2. Kept in the
│                           # branch only so the history stays intact.
└── extension/              # Chrome extension source (Phase 2)
```

## Build

On Windows, from the repo root:

```
pip install -r lona2/requirements.txt
pip install pyinstaller pyinstaller-hooks-contrib
pyinstaller lona.spec
ISCC /DAppVersion=2.0.0 installer\lona.iss
```

Output: `dist\LONA\LONA.exe` and `release\LONA-Setup-2.0.0.exe`.
CI does the same on every `v2*` tag and publishes a prerelease with
the setup.exe + the source zip (`lona-v2.0.0.zip`).

## Tests

```
python -m pytest lona2/tests/ -q
python scripts/security_check_v2.py
```

The suite covers the bridge contract, the security subset (redaction,
invite gate, owner code, backup) and the UI static checks (every UI
call maps to a real bridge method; no CDN links, no Math.random, no
emoji; fonts bundled, mascot present).

## Upgrade from v1.6.3

v2 installs over v1.6.3 in place: same AppId, same folder
(%LOCALAPPDATA%\Programs\LONA), one Add/Remove Programs entry. The
installer migrates the v1.6.3 settings + invite state into the new
data folder (%LOCALAPPDATA%\LONA), and the app migrates the three
saved API keys from the old Credential Manager targets on first
launch — so the upgrade never asks for the invite code again and
never loses a key or a setting.

## The old code

`app/` is the v1.6.3 program. It stays in this branch untouched so the
v1 history is preserved, but v2 never imports it, never builds from
it, and never ships it. Do not "fix" v2 by copying v1 code into
`lona2/` — the rebuild exists precisely because the old code carried
the old bugs.
