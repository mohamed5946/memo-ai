# -*- mode: python ; coding: utf-8 -*-
# Memo AI — PyInstaller spec (one-folder, windowed).
# Build (from the repo root, on Windows):
#   pip install -r app/requirements.txt
#   pip install pyinstaller pyinstaller-hooks-contrib
#   pyinstaller installer/memo-ai.spec
# Output: dist\Memo AI\Memo AI.exe  (with app\ + keys\ next to it)
#
# The frozen layout mirrors the zip layout, with one PyInstaller 6 quirk:
# the bootloader puts ALL collected files under _internal/ next to the exe,
# so in the frozen install the app files live in _internal/app/ (resolved
# via sys._MEIPASS in settings_store). keys/ is NOT bundled as data — it
# lives next to the exe (BASE_DIR/keys) and is installed by Inno Setup
# directly from the repo, so users can see and edit their key files.
# The Python modules themselves are frozen into the exe/_internal — only
# ui/, data/ and keys/ are loose files, and the app never writes code
# files at runtime.

import os
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
APP = os.path.join(ROOT, "app")

datas = [
    (os.path.join(APP, "ui"), "app/ui"),
    (os.path.join(APP, "data"), "app/data"),
]

# v1.4.0: WhatsApp sidecar (Baileys) + bundled Node runtime.
# The sidecar is REQUIRED for WhatsApp linking: fail loudly if its
# entry point is missing. The Node runtime is downloaded by CI
# (portable Node 20 LTS -> app/whatsapp_sidecar/node/node.exe); a local
# build without it fails with instructions, never silently ships a
# WhatsApp-less installer.
_wa_dir = os.path.join(APP, "whatsapp_sidecar")
if not os.path.isfile(os.path.join(_wa_dir, "sidecar.js")):
    raise RuntimeError(
        "WhatsApp sidecar.js missing — cannot build an installer "
        "without it: " + os.path.join(_wa_dir, "sidecar.js"))
if not os.path.isfile(os.path.join(_wa_dir, "node", "node.exe")):
    raise RuntimeError(
        "Bundled node.exe missing — the WhatsApp sidecar needs the "
        "Node 20 LTS runtime at app/whatsapp_sidecar/node/node.exe "
        "(CI downloads it; for a local build, download the portable "
        "Node 20 win-x64 zip from nodejs.org/dist and copy node.exe there)")

# node_modules pruning: bundle only what the sidecar can load on
# Windows. Skipped (never required at runtime): .bin symlinks, *.map /
# *.md / *.d.ts / *.ts, @types, test/tests/example dirs, the npm
# package-lock, and sharp's non-Windows native binaries (@img/sharp-*
# without 'win32' in the name - npm installs per-platform, so on the
# Windows CI runner only the win32 ones exist at all).
_WA_SKIP_DIRS = {"test", "tests", "example", "examples", ".bin", "@types"}
_WA_SKIP_SUFFIXES = (".map", ".md", ".d.ts", ".ts")
_WA_SKIP_FILES = {"package-lock.json", ".package-lock.json"}


def _wa_datas():
    out = []
    for root, dirs, files in os.walk(_wa_dir):
        rel_root = os.path.relpath(root, _wa_dir)
        # Prune sharp's platform binaries: keep win32 + colour only.
        if rel_root.startswith(os.path.join("node_modules", "@img")):
            parts = rel_root.split(os.sep)
            if len(parts) >= 3 and parts[2] != "colour" \
                    and "win32" not in parts[2]:
                dirs[:] = []
                continue
        dirs[:] = [d for d in dirs if d not in _WA_SKIP_DIRS]
        for name in files:
            if name in _WA_SKIP_FILES or name.endswith(_WA_SKIP_SUFFIXES):
                continue
            src = os.path.join(root, name)
            dest = os.path.join(
                "app", "whatsapp_sidecar",
                rel_root if rel_root != "." else "")
            out.append((src, dest))
    return out


datas += _wa_datas()

# v1.2.5: EXPLICIT webview/js bridge files. collect_all("webview") SHOULD
# include them, but the whole window.pywebview bridge (and therefore every
# button in the app) silently never exists if they are missing from the
# frozen bundle — pywebview builds the bridge from <webview package>/js
# (util.load_js_files -> get_js_dir), and a missing dir fails silently
# inside the .NET event handler. Pin the files down explicitly and fail
# the build loudly if they are not where the installed pywebview keeps
# them. (find_spec does not execute the package, so this is safe at
# spec-parse time.)
import importlib.util
_webview_spec = importlib.util.find_spec("webview")
if _webview_spec is None or not _webview_spec.origin:
    raise RuntimeError("pywebview package not found — cannot locate webview/js")
_webview_js_dir = os.path.join(os.path.dirname(_webview_spec.origin), "js")
if not os.path.isfile(os.path.join(_webview_js_dir, "api.js")):
    raise RuntimeError(
        "webview/js/api.js not found under " + _webview_js_dir +
        " — the frozen app would have no window.pywebview bridge")
datas.append((_webview_js_dir, "webview/js"))
binaries = []
hiddenimports = [
    "clr",  # pythonnet — pywebview's WinForms/EdgeChromium backend on Windows
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "webview.platforms.mshtml",
    # v1.4.1: pystray's Win32 backend is imported dynamically
    # (pystray._base -> pystray._win32) - PyInstaller misses it
    # unless named explicitly.
    "pystray._win32",
    "pystray._base",
]

# Heavy binary deps: collect the whole package (modules + native libs).
for _pkg in ("webview", "faster_whisper", "ctranslate2", "av",
             "sounddevice", "groq", "clr_loader", "pystray", "PIL",
             # v1.4.2: psutil (native _psutil_windows), ddgs (pure
             # Python, collected for safety - it is imported lazily
             # inside tools.py), winrt (Windows toast .pyd modules).
             "psutil", "ddgs", "winrt"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

a = Analysis(
    [os.path.join(APP, "main.py")],
    pathex=[APP],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Memo AI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "installer", "memo-ai.ico"),
    version=os.path.join(ROOT, "installer", "file_version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Memo AI",
)
