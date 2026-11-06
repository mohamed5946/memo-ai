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
binaries = []
hiddenimports = [
    "clr",  # pythonnet — pywebview's WinForms/EdgeChromium backend on Windows
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "webview.platforms.mshtml",
]

# Heavy binary deps: collect the whole package (modules + native libs).
for _pkg in ("webview", "faster_whisper", "ctranslate2", "av",
             "sounddevice", "groq", "clr_loader"):
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
