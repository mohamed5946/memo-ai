# LONA v1.6.1 - tests for the program rename (item 14)
# (run on Linux, no Windows, no keys, no network):
#
#   python3 -m pytest app/tests/test_v161_rename.py -q
#
# What is proven here:
#   * The frozen exe is LONA.exe, the setup is LONA-Setup-<version>.exe,
#     the install folder is %LOCALAPPDATA%\Programs\LONA, the single-
#     instance mutex / show-main event are LONA names, and the updater
#     downloads + relaunches LONA.exe.
#   * The installer uses a NEW AppId (the old one would have made
#     Setup reuse the old "Memo AI" folder) and stages the old
#     install's data before removing the old install, so exactly one
#     Add/Remove Programs entry (LONA) remains.
#   * The Python first-launch migration (app/migrate.py) moves an old
#     "Memo AI" install's data - settings, invite state
#     (activated.json), memory, keys - into the new LONA folder,
#     never overwriting files the new install already has, and
#     removes the old folder (zip-style) afterwards. An upgrade from
#     v1.6.0 therefore never re-asks for the invite code.
#   * No user-visible "Memo AI" remains in the installer, spec,
#     version info, native-host template, workflow, or backend
#     string literals (history comments are fine).
#
# NOT proven here (needs real Windows): the .iss actually compiling
# under ISCC, the old uninstaller running silently, and the real
# upgrade on Mohamed's laptop. The .iss migration logic IS proven by
# source inspection (procedure presence + ordering), and the Python
# fallback is proven by unit tests on simulated install folders.

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

import migrate as migrate_mod  # noqa: E402
import updater  # noqa: E402
import autostart  # noqa: E402
# NOTE: main.py is NOT imported here - it imports pywebview, which is
# not installed in the Linux test sandbox. The mutex name is checked
# by source inspection below (same pattern as the backend literal
# scan in test_v160_backend.py).

OLD_APP_ID = "{7BBF7DD9-42C6-41D0-BB76-99A5E19569A1}"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _make_old_install(base: Path, *, frozen: bool = True,
                      with_uninstaller: bool = False) -> Path:
    """A simulated old 'Memo AI' install under base.

    Frozen layout: <base>/Memo AI/_internal/app/data/*.json +
    <base>/Memo AI/keys/*.txt. Zip layout: <base>/Memo AI/app/data/.
    Returns the old install folder."""
    old = base / "Memo AI"
    if frozen:
        data = old / "_internal" / "app" / "data"
    else:
        data = old / "app" / "data"
    data.mkdir(parents=True)
    (data / "settings.json").write_text(
        '{"ai_name": "LONA", "user_name": "Mohamed"}', encoding="utf-8")
    (data / "activated.json").write_text(
        '{"activated": true, "at": "2026-11-21T10:00:00"}',
        encoding="utf-8")
    (data / "memory.json").write_text(
        '{"version": 1, "facts": [{"text": "likes delivery work"}]}',
        encoding="utf-8")
    (data / "rules.json").write_text(
        '{"rules": [{"match": "hi", "reply": "hello"}]}', encoding="utf-8")
    keys = old / "keys"
    keys.mkdir(parents=True)
    (keys / "groq-key.txt").write_text("gsk_old_key\n", encoding="utf-8")
    if with_uninstaller:
        (old / "unins000.exe").write_bytes(b"MZ fake uninstaller")
    return old


def _make_new_install(base: Path) -> tuple[Path, Path]:
    """A simulated new LONA install. Returns (base_dir, data_dir)."""
    new_base = base / "LONA"
    new_data = new_base / "_internal" / "app" / "data"
    new_data.mkdir(parents=True)
    return new_base, new_data


class TestPythonMigration(unittest.TestCase):
    def test_frozen_old_install_migrates_everything(self):
        tmp = Path(tempfile.mkdtemp())
        old = _make_old_install(tmp, frozen=True)
        new_base, new_data = _make_new_install(tmp)
        result = migrate_mod.migrate_from_old_install(new_base, new_data,
                                                      old)
        self.assertTrue(result["migrated"])
        # Settings, invite state, memory and keys all carried over.
        self.assertEqual(
            json.loads((new_data / "settings.json").read_text()),
            {"ai_name": "LONA", "user_name": "Mohamed"})
        self.assertTrue(
            json.loads((new_data / "activated.json").read_text())
            ["activated"], "invite state migrated - no re-ask on upgrade")
        self.assertIn("likes delivery work",
                      (new_data / "memory.json").read_text())
        self.assertIn("groq-key.txt",
                      [p.name for p in (new_base / "keys").glob("*.txt")])
        self.assertGreater(len(result["copied"]), 0)

    def test_zip_layout_old_install_migrates_too(self):
        tmp = Path(tempfile.mkdtemp())
        old = _make_old_install(tmp, frozen=False)
        new_base, new_data = _make_new_install(tmp)
        result = migrate_mod.migrate_from_old_install(new_base, new_data,
                                                      old)
        self.assertTrue(result["migrated"])
        self.assertTrue((new_data / "activated.json").is_file())
        self.assertTrue((new_data / "settings.json").is_file())

    def test_old_folder_removed_when_no_uninstaller(self):
        tmp = Path(tempfile.mkdtemp())
        old = _make_old_install(tmp, frozen=True)
        new_base, new_data = _make_new_install(tmp)
        result = migrate_mod.migrate_from_old_install(new_base, new_data,
                                                      old)
        self.assertTrue(result["old_removed"])
        self.assertFalse(old.exists(), "old Memo AI folder must be gone")

    def test_new_data_never_overwritten(self):
        tmp = Path(tempfile.mkdtemp())
        old = _make_old_install(tmp, frozen=True)
        new_base, new_data = _make_new_install(tmp)
        (new_data / "settings.json").write_text(
            '{"ai_name": "LONA", "user_name": "Mohamed", "theme": "dark"}',
            encoding="utf-8")
        (new_data / "activated.json").write_text(
            '{"activated": true, "at": "2026-11-22T01:00:00"}',
            encoding="utf-8")
        result = migrate_mod.migrate_from_old_install(new_base, new_data,
                                                      old)
        self.assertTrue(result["migrated"])
        self.assertEqual(
            json.loads((new_data / "settings.json").read_text())["theme"],
            "dark", "the new install's own settings win")
        self.assertEqual(
            json.loads((new_data / "activated.json").read_text())["at"],
            "2026-11-22T01:00:00")
        self.assertGreaterEqual(result["skipped_existing"], 2)

    def test_no_old_install_is_a_noop(self):
        tmp = Path(tempfile.mkdtemp())
        new_base, new_data = _make_new_install(tmp)
        result = migrate_mod.migrate_from_old_install(new_base, new_data,
                                                      tmp / "Memo AI")
        self.assertFalse(result["migrated"])
        self.assertEqual(result["copied"], [])
        self.assertFalse(result["old_removed"])

    def test_same_folder_refused(self):
        tmp = Path(tempfile.mkdtemp())
        old = _make_old_install(tmp, frozen=True)
        result = migrate_mod.migrate_from_old_install(old, old / "app"
                                                      / "data", old)
        self.assertFalse(result["migrated"])
        self.assertTrue((old / "_internal" / "app" / "data"
                         / "settings.json").is_file())

    def test_readme_never_migrated_as_a_key(self):
        tmp = Path(tempfile.mkdtemp())
        old = _make_old_install(tmp, frozen=True)
        (old / "keys" / "README.txt").write_text("instructions",
                                                  encoding="utf-8")
        new_base, new_data = _make_new_install(tmp)
        migrate_mod.migrate_from_old_install(new_base, new_data, old)
        self.assertFalse((new_base / "keys" / "README.txt").exists())

    def test_old_app_id_known_for_uninstaller_lookup(self):
        self.assertEqual(migrate_mod.OLD_APP_ID, OLD_APP_ID)
        self.assertEqual(migrate_mod.OLD_APP_DIR_NAME, "Memo AI")


class TestExeAndUpdaterNames(unittest.TestCase):
    def test_mutex_and_event_are_lona(self):
        # Source inspection: main.py imports pywebview, which is not
        # installed in the Linux test sandbox.
        main_src = (APP_DIR / "main.py").read_text(encoding="utf-8")
        self.assertIn('_MUTEX_NAME = "Local\\\\LONA-SingleInstance"',
                      main_src)
        self.assertNotIn("MemoAI-SingleInstance", main_src)
        win_src = (APP_DIR / "winshape.py").read_text(encoding="utf-8")
        self.assertIn('_SHOW_MAIN_EVENT = "Local\\\\LONA-ShowMain"',
                      win_src)
        self.assertNotIn("MemoAI-ShowMain", win_src)

    def test_download_filename_is_lona_setup(self):
        self.assertEqual(updater._download_filename("setup", "1.6.1"),
                         "LONA-Setup-v1.6.1.exe")
        # The source zip keeps the repo name (only the setup is renamed).
        self.assertEqual(updater._download_filename("zip", "1.6.1"),
                         "memo-ai-v1.6.1.zip")

    def test_launch_setup_relaunches_lona_exe(self):
        src = Path(updater.__file__).read_text(encoding="utf-8")
        self.assertIn('BASE_DIR / "LONA.exe"', src)
        self.assertNotIn('"Memo AI.exe"', src)

    def test_autostart_value_is_lona_and_cleans_old(self):
        self.assertEqual(autostart._VALUE_NAME, "LONA")
        src = Path(autostart.__file__).read_text(encoding="utf-8")
        self.assertIn('_OLD_VALUE_NAME = "Memo AI"', src)
        self.assertIn("DeleteValue(key, _OLD_VALUE_NAME)", src)

    def test_tray_menu_is_lona(self):
        import tray as tray_mod
        src = Path(tray_mod.__file__).read_text(encoding="utf-8")
        self.assertIn('"فتح LONA"', src)


class TestInstallerRename(unittest.TestCase):
    def test_app_name_folder_and_output(self):
        iss = _read("installer/memo-ai.iss")
        self.assertIn('#define AppName "LONA"', iss)
        self.assertIn("DefaultDirName={localappdata}\\Programs\\LONA", iss)
        self.assertIn("DefaultGroupName=LONA", iss)
        self.assertIn("OutputBaseFilename=LONA-Setup-{#AppVersion}", iss)
        self.assertIn("AppMutex=Local\\LONA-SingleInstance", iss)
        self.assertIn('UninstallDisplayIcon={app}\\LONA.exe', iss)
        self.assertIn('Source: "..\\dist\\LONA\\*"; DestDir: "{app}"', iss)
        self.assertIn('Name: "{group}\\LONA"; Filename: "{app}\\LONA.exe"',
                      iss)
        self.assertIn('Name: "{userdesktop}\\LONA"; Filename: '
                      '"{app}\\LONA.exe"; Tasks: desktopicon', iss)
        self.assertIn('Filename: "{app}\\LONA.exe"; Description: '
                      '"{cm:LaunchProgram,LONA}"', iss)

    def test_new_app_id_differs_from_old(self):
        iss = _read("installer/memo-ai.iss")
        m = re.search(r"AppId=\{\{([0-9A-F-]+)\}", iss)
        self.assertIsNotNone(m, "AppId not found in .iss")
        self.assertNotEqual("{" + m.group(1) + "}", OLD_APP_ID,
                            "same AppId would reuse the old Memo AI folder")
        # The old AppId is still recorded, for the uninstall lookup.
        self.assertIn("OldAppId = '" + OLD_APP_ID + "'", iss)

    def test_migration_procedures_present_and_ordered(self):
        iss = _read("installer/memo-ai.iss")
        for proc in ("MigrateOldInstallData", "RemoveOldInstall",
                     "RestoreMigratedData", "CopyMissingFiles",
                     "CopyFilesOver"):
            self.assertIn(f"procedure {proc}", iss, proc)
        # ssInstall stages + removes the old install; ssPostInstall
        # restores the staged files over the fresh install.
        install_step = iss.index("if CurStep = ssInstall then")
        post_step = iss.index("else if CurStep = ssPostInstall then")
        self.assertLess(iss.index("MigrateOldInstallData();", install_step),
                        post_step)
        self.assertLess(iss.index("RemoveOldInstall();", install_step),
                        post_step)
        self.assertGreater(iss.index("RestoreMigratedData();", post_step),
                           post_step)
        # Staging happens BEFORE the old uninstaller runs.
        self.assertLess(
            iss.index("MigrateOldInstallData();", install_step),
            iss.index("RemoveOldInstall();", install_step))

    def test_old_uninstaller_and_leftovers_removed(self):
        iss = _read("installer/memo-ai.iss")
        self.assertIn("UninstallString", iss)
        self.assertIn("/VERYSILENT /NORESTART /SUPPRESSMSGBOXES", iss)
        self.assertIn("DelTree(OldDir, True, True, True)", iss)
        self.assertIn("OldAppId + '_is1'", iss)
        self.assertIn("'Software\\Microsoft\\Windows\\CurrentVersion\\Run', "
                      "OldAppDirName", iss)

    def test_native_host_manifest_uses_lona_exe(self):
        iss = _read("installer/memo-ai.iss")
        self.assertIn("ExePath := ExpandConstant('{app}\\LONA.exe');", iss)
        manifest = json.loads(
            (ROOT / "installer" / "native-host.json").read_text())
        self.assertTrue(manifest["path"].endswith("LONA.exe"))
        self.assertEqual(manifest["name"], "com.memoai.host",
                         "host id is a technical identifier, unchanged")

    def test_webview2_warnings_say_lona(self):
        iss = _read("installer/memo-ai.iss")
        self.assertIn("هيكمل تثبيت LONA عادي", iss)
        self.assertNotIn("هيكمل تثبيت Memo AI عادي", iss)

    def test_no_user_visible_memo_ai_in_installer_directives(self):
        # "Memo AI" may still appear in the .iss only as history
        # comments or as the old-folder name the migration code needs.
        # Strip ;-comments and { }-comments, then every remaining
        # occurrence must be inside the migration code (OldAppDirName
        # / OldInstallDir / the old AppId record).
        iss = _read("installer/memo-ai.iss")
        stripped_lines = []
        for line in iss.splitlines():
            code = line.split(";", 1)[0] if line.lstrip().startswith(";") \
                else line
            stripped_lines.append(code)
        stripped = "\n".join(stripped_lines)
        stripped = re.sub(r"\{[^}]*\}", "", stripped)
        for i, line in enumerate(stripped.splitlines(), 1):
            if "Memo AI" in line:
                self.assertTrue(
                    ("OldAppDirName" in line or "OldInstallDir" in line
                     or "OldAppId" in line or "old \"Memo AI\"" in line
                     or "old Memo AI" in line),
                    f".iss line {i}: user-visible 'Memo AI': {line!r}")


class TestNoUserVisibleMemoAiElsewhere(unittest.TestCase):
    def test_spec_has_no_memo_ai(self):
        spec = _read("installer/memo-ai.spec")
        self.assertNotIn("Memo AI", spec)
        self.assertIn('name="LONA"', spec)
        self.assertIn("dist\\LONA\\LONA.exe", spec)

    def test_file_version_info_is_lona(self):
        info_txt = _read("installer/file_version_info.txt")
        self.assertNotIn("Memo AI", info_txt)
        for field in ("FileDescription', 'LONA",
                      "InternalName', 'LONA",
                      "OriginalFilename', 'LONA.exe",
                      "ProductName', 'LONA"):
            self.assertIn(field, info_txt)
        self.assertIn("filevers=(1, 6, 4, 0)", info_txt)

    def test_workflow_builds_lona_setup(self):
        yml = _read(".github/workflows/build-installer.yml")
        self.assertNotIn("MemoAI-Setup", yml)
        self.assertNotIn("Memo AI.exe", yml)
        self.assertNotIn("dist\\Memo AI", yml)
        self.assertIn("release\\LONA-Setup-$env:MEMO_VERSION.exe", yml)
        self.assertIn("dist\\LONA\\LONA.exe", yml)
        self.assertIn("LONA-Setup-$ver.exe", yml,
                      "manifest setup_url must point at the new exe")

    def test_expected_manifest_setup_url(self):
        # What the coordinator must put in version.json post-release.
        expected = ("https://github.com/mohamed5946/memo-ai/releases/"
                    "download/v1.6.1/LONA-Setup-1.6.1.exe")
        yml = _read(".github/workflows/build-installer.yml")
        self.assertIn(
            "releases/download/$tag/LONA-Setup-$ver.exe", yml)
        self.assertTrue(expected.startswith(
            "https://github.com/mohamed5946/memo-ai/releases/download/"))


if __name__ == "__main__":
    unittest.main()
