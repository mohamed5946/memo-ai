# LONA v1.6.1 - tests for the identity migration (item 19)
# (run on Linux, no Windows, no keys, no network):
#
#   python3 -m pytest app/tests/test_v161_identity.py -q
#
# What is proven here:
#   * Upgrade migration: a stored ai_name "Memo" (or empty) becomes
#     "LONA" AND the avatar becomes the gold robot - the old default
#     identity migrates as ONE unit. A custom name is left alone,
#     avatar included. A stored "LONA" keeps its own avatar.
#   * The canned self-introductions in qa.json / rules.json move with
#     it ("أنا Memo" -> "أنا LONA"); unrelated user text is untouched.
#   * The program identity is the robot by default (DEFAULTS, JS
#     defaults, header + hero images); the 4 picker avatars stay.
#   * The tray icon is the robot (item 2, re-asserted here because
#     the program identity is the robot).
#   * No user-visible "Memo" remains in backend string literals,
#     the WhatsApp default, the sidecar browser label, the pet alt
#     text, or the bundled Q&A answers. (History comments and the
#     old-install identifiers needed by the migration itself -
#     migrate.OLD_APP_DIR_NAME / autostart._OLD_VALUE_NAME, both
#     exactly "Memo AI" - are allowlisted with the reason.)

import ast
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

import settings_store as settings_mod  # noqa: E402

HTML = (APP_DIR / "ui" / "index.html").read_text(encoding="utf-8")
JS = (APP_DIR / "ui" / "app.js").read_text(encoding="utf-8")


def _load_with_saved(tmp: Path, saved: dict, data_files: dict | None = None):
    """load_settings() with DATA_DIR + SETTINGS_PATH redirected."""
    data_dir = tmp / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    if data_files:
        for name, text in data_files.items():
            (data_dir / name).write_text(text, encoding="utf-8")
    with mock.patch.object(settings_mod, "DATA_DIR", data_dir), \
            mock.patch.object(settings_mod, "SETTINGS_PATH",
                              data_dir / "settings.json"):
        (data_dir / "settings.json").write_text(
            json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        return settings_mod.load_settings()


class TestIdentityMigration(unittest.TestCase):
    def test_memo_name_becomes_lona_and_avatar_robot(self):
        tmp = Path(tempfile.mkdtemp())
        st = _load_with_saved(tmp, {"ai_name": "Memo", "avatar": "boy"})
        self.assertEqual(st["ai_name"], "LONA")
        self.assertEqual(st["avatar"], "robot")
        self.assertTrue(st["identity_migrated_v161"])

    def test_empty_name_becomes_lona_and_avatar_robot(self):
        tmp = Path(tempfile.mkdtemp())
        st = _load_with_saved(tmp, {"ai_name": "", "avatar": "cat"})
        self.assertEqual(st["ai_name"], "LONA")
        self.assertEqual(st["avatar"], "robot")

    def test_custom_name_keeps_its_avatar(self):
        tmp = Path(tempfile.mkdtemp())
        st = _load_with_saved(tmp, {"ai_name": "سارة", "avatar": "girl"})
        self.assertEqual(st["ai_name"], "سارة")
        self.assertEqual(st["avatar"], "girl")

    def test_lona_name_keeps_its_avatar(self):
        tmp = Path(tempfile.mkdtemp())
        st = _load_with_saved(tmp, {"ai_name": "LONA", "avatar": "cat"})
        self.assertEqual(st["ai_name"], "LONA")
        self.assertEqual(st["avatar"], "cat")

    def test_migration_runs_once(self):
        tmp = Path(tempfile.mkdtemp())
        st = _load_with_saved(tmp, {"ai_name": "Memo", "avatar": "boy"})
        self.assertEqual(st["avatar"], "robot")
        # The user then picks the cat; a later load must not force
        # the robot back on them.
        st2 = _load_with_saved(
            tmp, {"ai_name": "LONA", "avatar": "cat",
                  "identity_migrated_v161": True})
        self.assertEqual(st2["avatar"], "cat")

    def test_identity_answers_rewritten(self):
        tmp = Path(tempfile.mkdtemp())
        qa = ('{"questions": [{"q": "ايه اسمك", '
              '"a": "أنا Memo، مساعدك الذكي على اللابتوب."}, '
              '{"q": "what is your name", '
              '"a": "I\'m Memo, your smart assistant on this laptop."}]}')
        rules = ('{"rules": [{"match": "انت مين", "reply": "أنا Memo، '
                 'مساعدك الذكي."}, {"match": "قابلت Memo امبارح", '
                 '"reply": "تمام"}]}')
        st = _load_with_saved(tmp, {"ai_name": "Memo", "avatar": "boy"},
                              {"qa.json": qa, "rules.json": rules})
        self.assertEqual(st["ai_name"], "LONA")
        data_dir = tmp / "data"
        # encoding="utf-8": on Windows read_text() defaults to
        # cp1252, which mojibakes the Arabic answers (the CI failure
        # was "أنا LONA،" not found in mojibake).
        new_qa = json.loads((data_dir / "qa.json").read_text(
            encoding="utf-8"))
        self.assertIn("أنا LONA،", new_qa["questions"][0]["a"])
        self.assertIn("I'm LONA,", new_qa["questions"][1]["a"])
        new_rules = json.loads((data_dir / "rules.json").read_text(
            encoding="utf-8"))
        self.assertIn("أنا LONA،", new_rules["rules"][0]["reply"])
        # Unrelated user text mentioning Memo is NOT rewritten.
        self.assertIn("Memo", new_rules["rules"][1]["match"])

    def test_identity_answers_skipped_for_custom_name(self):
        tmp = Path(tempfile.mkdtemp())
        qa = '{"questions": [{"q": "ايه اسمك", "a": "أنا Memo."}]}'
        _load_with_saved(tmp, {"ai_name": "سارة", "avatar": "girl"},
                         {"qa.json": qa})
        self.assertIn("أنا Memo.",
                      (tmp / "data" / "qa.json").read_text(
                          encoding="utf-8"))


class TestRobotIsTheDefaultIdentity(unittest.TestCase):
    def test_defaults_are_lona_and_robot(self):
        self.assertEqual(settings_mod.DEFAULTS["ai_name"], "LONA")
        self.assertEqual(settings_mod.DEFAULTS["avatar"], "robot")

    def test_js_avatar_map_has_robot(self):
        # v1.6.4 reskin: there is no avatar image map in the UI JS —
        # the robot IS the mascot image (hero), and the picker offers
        # only the 4 letter options. The default identity stays the
        # robot: settings DEFAULTS avatar is "robot" (see
        # test_defaults_are_lona_and_robot) and the hero shows it.
        self.assertIn('<img src="assets/mascot.webp" alt="لونا">', HTML)
        self.assertNotIn("avatar-robot.png", HTML)

    def test_header_and_hero_show_robot(self):
        self.assertIn('<img src="assets/mascot.webp" alt="لونا">',
                      HTML)

    def test_picker_keeps_exactly_four_options(self):
        # v2 design: 4 gold-circle Arabic-letter options, no robot
        # option (the robot is the identity, not a picker choice).
        for entry in ("['cat', 'ق', 'قطة']", "['dog', 'ك', 'كلب']",
                      "['boy', 'و', 'ولد']", "['girl', 'ب', 'بنت']"):
            self.assertIn(entry, HTML, "avatar option missing: " + entry)
        avatar = HTML.split("avatar: function ()")[1].split("log: function")[0]
        self.assertNotIn("robot", avatar)

    def test_tray_icon_is_robot(self):
        main = (APP_DIR / "main.py").read_text(encoding="utf-8")
        # The tray icon is the robot mascot (mascot.webp), not the old
        # avatar-robot.png.
        self.assertIn('icon_path=APP_DIR / "ui" / "assets" / "mascot.webp"',
                      main)
        self.assertNotIn('icon_path=APP_DIR / "ui" / "assets" / "avatar-robot.png"',
                         main)


class TestNoUserVisibleMemo(unittest.TestCase):
    def test_no_memo_in_backend_literals(self):
        # String literals only (comments/docstrings excluded). The
        # ONLY allowed "Memo" literals are the old-install identifiers
        # the migration/removal code must name: exactly "Memo AI" and
        # "Memo AI.exe".
        modules = ["main.py", "brain.py", "tools.py", "monitor.py",
                   "settings_store.py", "updater.py", "capabilities.py",
                   "invite.py", "apikeys.py", "browser_control.py",
                   "fixer.py", "tempwatch.py", "selfview.py",
                   "companion.py", "image_understanding.py", "backup.py",
                   "wakeword.py", "tray.py", "rules.py", "whatsapp.py",
                   "autostart.py", "migrate.py", "winshape.py",
                   "notifications.py", "weather.py"]
        allowed = {"Memo AI", "Memo AI.exe"}
        # Per-module: settings_store must name the old default
        # ai_name "Memo" to detect it for the migration, and the old
        # canned answers ("أنا Memo،" / "I'm Memo,") to rewrite them.
        per_module_allowed = {"settings_store.py": {
            "Memo", "أنا Memo،", "I'm Memo,"}}
        for name in modules:
            path = APP_DIR / name
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = set()

            def _collect(node):
                for child in ast.walk(node):
                    if isinstance(child, (ast.Module, ast.FunctionDef,
                                          ast.AsyncFunctionDef, ast.ClassDef)):
                        body = child.body
                        if body and isinstance(body[0], ast.Expr) and \
                                isinstance(body[0].value, ast.Constant) and \
                                isinstance(body[0].value.value, str):
                            docstrings.add(id(body[0].value))
            _collect(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and \
                        isinstance(node.value, str):
                    if id(node) in docstrings:
                        continue
                    if re.search(r"\bMemo\b", node.value) and \
                            node.value.strip() not in allowed and \
                            node.value.strip() not in \
                            per_module_allowed.get(name, set()):
                        self.fail(f"{name}: user-visible 'Memo' in "
                                  f"literal: {node.value[:80]!r}")

    def test_whatsapp_default_is_lona(self):
        src = (APP_DIR / "whatsapp.py").read_text(encoding="utf-8")
        self.assertIn('settings.get("ai_name", "LONA")', src)
        self.assertNotIn('"Memo"', src)

    def test_sidecar_browser_label_is_lona(self):
        src = (ROOT / "app" / "whatsapp_sidecar" / "sidecar.js").read_text(
            encoding="utf-8")
        self.assertIn('browser: ["LONA", "Chrome", "1.0.0"]', src)

    def test_pet_alt_is_lona(self):
        pet = (APP_DIR / "ui" / "pet.html").read_text(encoding="utf-8")
        self.assertIn('alt="LONA pet"', pet)
        self.assertNotIn("Memo", pet)

    def test_bundled_qa_answers_are_lona(self):
        # encoding="utf-8" is required: on Windows read_text()
        # defaults to cp1252, which cannot decode the Arabic answers
        # (UnicodeDecodeError on CI) and mojibakes them on read.
        qa = json.loads((APP_DIR / "data" / "qa.json").read_text(
            encoding="utf-8"))
        rules = json.loads((APP_DIR / "data" / "rules.json").read_text(
            encoding="utf-8"))
        blob = json.dumps([qa, rules], ensure_ascii=False)
        self.assertNotIn("Memo", blob)
        self.assertIn("LONA", blob)


if __name__ == "__main__":
    unittest.main()
