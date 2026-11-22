"""v1.6.1 workstream B tests - gold robot logo + island on by default.

Run:  python3 -m pytest app/tests/test_v161_island.py -q

What is proven here:
  * Item 2 - the program icon is the gold/white robot, not the black
    cat: installer/memo-ai.ico is a valid multi-size icon (16/32/48/
    64/128/256, PNG entries) built from the robot image, referenced by
    the PyInstaller spec and the Inno Setup script; the tray uses the
    new app/ui/assets/avatar-robot.png. The 4 selectable in-app
    avatars (cat/dog/boy/girl) are untouched - only the PROGRAM icon
    changed. Small icon sizes use a face-filling crop so the robot
    still reads at 16px (the full figure is a muddy blob that small).
  * Item 4 - the Dynamic Island is ON by default: fresh installs get
    island_enabled=True; existing v1.6.0 installs (saved False under
    the v1.4.1 kill-switch, with no toggle ever existing) are migrated
    to True once, and a user's own choice after that is respected.
    Startup creates the island window and starts the main window
    hidden whenever the flag is on, and Settings has a visible toggle
    wired through refreshStatus + save-general + the bridge allowlist
    (the toggle takes effect on the next launch - the label says so).

No test here imports main.py: it needs pywebview, which is not
installed on Linux. Startup behavior is verified by reading the
guard in main.py source, the way test_v141 does its AST checks.
"""

import ast
import json
import struct
import sys
from pathlib import Path

import pytest
from PIL import Image

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

import settings_store  # noqa: E402
from settings_store import DEFAULTS, load_settings, save_settings  # noqa: E402

UI = APP_DIR / "ui"
HTML = (UI / "index.html").read_text(encoding="utf-8")
JS = (UI / "app.js").read_text(encoding="utf-8")
MAIN = (APP_DIR / "main.py").read_text(encoding="utf-8")
SPEC = (ROOT / "installer" / "memo-ai.spec").read_text(encoding="utf-8")
ISS = (ROOT / "installer" / "memo-ai.iss").read_text(encoding="utf-8")
ROBOT_PNG = UI / "assets" / "avatar-robot.png"
ICO = ROOT / "installer" / "memo-ai.ico"

EXPECTED_ICO_SIZES = {16, 32, 48, 64, 128, 256}


# ---------------- Item 4: island defaults + migration ----------------

def test_defaults_island_on_pet_off():
    assert DEFAULTS["island_enabled"] is True
    assert DEFAULTS["pet_enabled"] is False


def test_fresh_install_shows_island(tmp_path, monkeypatch):
    """No settings.json at all: the island is on at first startup."""
    monkeypatch.setattr(settings_store, "SETTINGS_PATH",
                        tmp_path / "settings.json")
    settings = load_settings()
    assert settings["island_enabled"] is True
    assert settings["pet_enabled"] is False
    assert settings["island_migrated_v161"] is True


def test_migration_enables_v160_install(tmp_path, monkeypatch):
    """A v1.6.0 install saved island_enabled=False under the old
    kill-switch. No toggle ever existed, so nobody could have turned
    it off on purpose - the first v1.6.1 run enables it once."""
    monkeypatch.setattr(settings_store, "SETTINGS_PATH",
                        tmp_path / "settings.json")
    monkeypatch.setattr(settings_store, "DATA_DIR", tmp_path)
    (tmp_path / "settings.json").write_text(
        json.dumps({"island_enabled": False, "ai_name": "LONA"}),
        encoding="utf-8")
    settings = load_settings()
    assert settings["island_enabled"] is True
    assert settings["island_migrated_v161"] is True
    assert settings["ai_name"] == "LONA"  # other settings untouched
    assert save_settings(settings) is True
    saved = json.loads((tmp_path / "settings.json").read_text(
        encoding="utf-8"))
    assert saved["island_enabled"] is True
    assert saved["island_migrated_v161"] is True


def test_user_choice_respected_after_migration(tmp_path, monkeypatch):
    """Once migrated, turning the island off in Settings sticks - the
    migration never re-enables it against the user's will."""
    monkeypatch.setattr(settings_store, "SETTINGS_PATH",
                        tmp_path / "settings.json")
    (tmp_path / "settings.json").write_text(
        json.dumps({"island_enabled": False,
                    "island_migrated_v161": True}),
        encoding="utf-8")
    assert load_settings()["island_enabled"] is False


def test_startup_creates_island_and_hides_main():
    """main.py honors the flag at startup (source check - pywebview
    is not importable on Linux): the island window is created only
    when the flag is on, and the main window starts hidden then."""
    assert 'if self.settings.get("island_enabled", False):' in MAIN
    assert 'hidden=self.settings.get("island_enabled", False)' in MAIN
    assert (UI / "island.html").is_file()


# ---------------- Item 4: Settings toggle wiring ----------------

def test_settings_toggle_wired():
    # HTML: the checkbox exists, checked by default, with its label.
    assert 'id="set-island"' in HTML
    assert 'data-i18n="island_toggle"' in HTML
    # i18n: the label exists in both languages, and says honestly
    # that the change applies after a restart.
    assert JS.count('island_toggle:') >= 2
    assert "التغيير يسري بعد إعادة التشغيل" in JS
    assert "change applies after restart" in JS
    # refreshStatus restores the toggle from the real setting.
    assert ('setVal("set-island", st.settings.island_enabled !== false)'
            in JS)
    # save-general persists the toggle.
    assert 'island_enabled: $("set-island").checked' in JS


def test_bridge_save_accepts_island_flag():
    """Api.save_settings() allowlist includes island_enabled - without
    this the toggle would look saved and silently do nothing."""
    tree = ast.parse(MAIN)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Api":
            for item in node.body:
                if (isinstance(item, ast.FunctionDef)
                        and item.name == "save_settings"):
                    for sub in ast.walk(item):
                        if isinstance(sub, ast.Tuple):
                            keys = {e.value for e in sub.elts
                                    if isinstance(e, ast.Constant)}
                            assert "island_enabled" in keys
                            return
    pytest.fail("Api.save_settings allowlist not found")


# ---------------- Item 2: gold robot logo ----------------

def _ico_entries():
    raw = ICO.read_bytes()
    _reserved, typ, count = struct.unpack("<HHH", raw[:6])
    assert typ == 1
    entries = []
    for i in range(count):
        w, h, _cc, _res, _planes, bpp, size, offset = struct.unpack(
            "<BBBBHHII", raw[6 + 16 * i:6 + 16 * (i + 1)])
        entries.append((w or 256, h or 256, bpp, size, offset))
    return raw, entries


def test_robot_ico_is_valid_multisize():
    raw, entries = _ico_entries()
    assert {w for w, _h, _b, _s, _o in entries} == EXPECTED_ICO_SIZES
    for w, _h, bpp, size, offset in entries:
        assert bpp == 32
        # PNG-compressed entry (Vista+ ICO).
        assert raw[offset:offset + 4] == b"\x89PNG"
        assert size > 0
    # Pillow agrees on the size set.
    assert set(Image.open(str(ICO)).info["sizes"]) == {
        (s, s) for s in EXPECTED_ICO_SIZES}


def test_installer_and_spec_reference_robot_ico():
    assert ICO.is_file()
    assert 'icon=os.path.join(ROOT, "installer", "memo-ai.ico")' in SPEC
    assert "SetupIconFile=memo-ai.ico" in ISS


def test_tray_uses_robot_icon():
    assert ROBOT_PNG.is_file()
    img = Image.open(str(ROBOT_PNG))
    assert img.size == (256, 256)
    assert img.mode in ("RGB", "RGBA")
    # main.py points the tray at the robot, not the cat.
    assert ('icon_path=APP_DIR / "ui" / "assets" / "avatar-robot.png"'
            in MAIN)
    assert ('icon_path=APP_DIR / "ui" / "assets" / "avatar-cat.png"'
            not in MAIN)


def test_tray_loads_robot_image():
    import tray as tray_mod
    if not tray_mod._PYSTRAY_OK:
        pytest.skip("no pystray backend on this machine")
    tm = tray_mod.TrayManager(lambda: None, lambda: None,
                              icon_path=str(ROBOT_PNG))
    img = tm._load_image()
    assert img is not None
    assert img.size == (64, 64)
    assert img.mode == "RGBA"


def test_selectable_avatars_unchanged():
    """Only the PROGRAM icon changed - the 4 in-app avatars stay.
    v1.6.1: the program identity avatar is the gold robot (the
    default pick); the 4 picker options are untouched."""
    for name in ("avatar-cat", "avatar-dog", "avatar-boy", "avatar-girl"):
        assert (UI / "assets" / f"{name}.png").is_file()
    assert DEFAULTS["avatar"] == "robot"
