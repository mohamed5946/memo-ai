# -*- mode: python ; coding: utf-8 -*-
# LONA v2 — PyInstaller spec (one-folder, windowed).
# Build (from the repo root, on Windows):
#   pip install -r lona2/requirements.txt
#   pip install pyinstaller pyinstaller-hooks-contrib
#   pyinstaller lona.spec
# Output: dist\LONA\LONA.exe  (with lona2\ui next to it, under _internal)
#
# The frozen layout: the bootloader puts ALL collected files under
# _internal/ next to the exe, so in the frozen install the UI lives in
# _internal/lona2/ui/ (resolved via Path(__file__).parent in
# lona2/main.py — no sys._MEIPASS handling is needed). The app data
# (settings/tasks/memories/logs) lives in %LOCALAPPDATA%\LONA, NOT in
# the bundle, so it survives reinstalls; the installer migrates the
# v1.6.3 data there (see installer/lona.iss).
#
# v2 has NO WhatsApp sidecar and NO bundled Node runtime (the Chrome
# extension re-wire is a Phase 2 item) — nothing sidecar-shaped is
# bundled, and the build must not expect it.

import os
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
LONA2 = os.path.join(ROOT, "lona2")

datas = [
    (os.path.join(LONA2, "ui"), "lona2/ui"),
]

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
    # pystray's Win32 backend is imported dynamically
    # (pystray._base -> pystray._win32) - PyInstaller misses it
    # unless named explicitly.
    "pystray._win32",
    "pystray._base",
]

# Heavy binary deps: collect the whole package (modules + native libs).
# v2's dependency list is lean on purpose (see lona2/requirements.txt):
# no groq package (the brain calls Groq over plain requests), no
# faster-whisper / sounddevice / winrt (voice is Phase 2). certifi is
# collected explicitly so frozen HTTPS calls always find their CA
# bundle.
for _pkg in ("webview", "clr_loader", "pystray", "PIL",
             "psutil", "cryptography", "requests", "certifi"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

a = Analysis(
    [os.path.join(LONA2, "main.py")],
    pathex=[LONA2],
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
    name="LONA",
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
    icon=os.path.join(ROOT, "installer", "lona.ico"),
    version=os.path.join(ROOT, "installer", "file_version_info_v2.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LONA",
)
