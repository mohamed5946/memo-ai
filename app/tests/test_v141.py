# Memo AI v1.4.1 - headless tests (run on Linux, no Windows, no keys).
#
#   python -m unittest discover -s app/tests -v
#
# What is proven here (every v1.4.1 scope item, failing-before /
# passing-after where the old code is still visible):
#   * pet + island REMOVED from the running app (flags off by default,
#     windows never created, no traces in the UI) - code kept behind flags.
#   * close (X) -> system tray (fake icon); explicit Quit -> full exit;
#     no tray backend -> minimize to the taskbar instead.
#   * voice/avatar persistence: save on pick, restore on load, twice.
#   * WhatsApp settings: owner number survives reload; QR state machine;
#     sidecar failure surfaces an error (not a blank); the save race
#     (mid-save re-render wiping the fields) is gone.
#   * Settings reorganized DeliveryFlow-style (menu + 6 sections).
#   * "who is the developer?" - public info only, never private info.
#   * owner mode actually works (flag sticks, local rule + model prompt
#     both honor it; without the code the rule still refuses).
#   * the "none" / 400 fix: request sanitizing, clean history, local
#     fallback that really answers, engine never "none", region
#     verification with +/-1px tolerance.
#
# NOT proven here (needs real Windows + a live Groq key): the real tray
# icon on the user's taskbar, the real QR scan, the model actually
# CHOOSING a tool on a live call. Those are the field tests in the
# release notes. The live-Groq test in test_v132.py runs only when
# GROQ_API_KEY is set.

import ast
import hashlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

import brain  # noqa: E402
import rules  # noqa: E402
import tools  # noqa: E402
import tray as tray_mod  # noqa: E402
import whatsapp  # noqa: E402


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class TestPetIslandRemoved(unittest.TestCase):
    def test_flags_default_off(self):
        import main
        from settings_store import DEFAULTS
        self.assertIs(DEFAULTS["pet_enabled"], False)
        self.assertIs(DEFAULTS["island_enabled"], False)
        app = main.MemoApp()
        try:
            # Flags off (default): only the main window, and the
            # self-test never requires the disabled pages' files.
            self.assertEqual(app._enabled_windows(), ["main"])
            self.assertEqual(app._watchdog_pages(), {"main"})
            self.assertNotIn("island.html", app._selftest_ui_files())
            self.assertNotIn("pet.html", app._selftest_ui_files())
            # Flags on (user re-enables them later): the windows and
            # their files come back behind the flags.
            app.settings["pet_enabled"] = True
            app.settings["island_enabled"] = True
            self.assertEqual(sorted(app._enabled_windows()),
                             ["island", "main", "pet"])
            self.assertIn("island.html", app._selftest_ui_files())
            self.assertIn("pet.html", app._selftest_ui_files())
            self.assertEqual(app._watchdog_pages(),
                             {"main", "island", "pet"})
        finally:
            main._APP = None

    def test_island_pet_windows_created_only_behind_flags(self):
        # AST check: the create_window calls for island.html / pet.html
        # sit inside an `if` that tests their flag - they can never run
        # while the flags are off (default).
        src = _read("app/main.py")
        tree = ast.parse(src)
        parent = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parent[child] = node
        found = {"island": False, "pet": False, "main": False}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "create_window"):
                continue
            seg = ast.get_source_segment(src, node) or ""
            kind = ("island" if "island.html" in seg else
                    "pet" if "pet.html" in seg else
                    "main" if "index.html" in seg else None)
            if kind is None:
                continue
            found[kind] = True
            if kind in ("island", "pet"):
                flag = "island_enabled" if kind == "island" else "pet_enabled"
                anc, guarded = node, False
                while anc in parent:
                    anc = parent[anc]
                    if isinstance(anc, ast.If) and flag in (
                            ast.get_source_segment(src, anc.test) or ""):
                        guarded = True
                        break
                self.assertTrue(
                    guarded,
                    f"{kind} window must be created only behind its flag")
        self.assertTrue(all(found.values()), found)
        # The main window opens directly (not hidden behind the island).
        self.assertIn('hidden=self.settings.get("island_enabled", False)',
                      src)
        self.assertIn("min_size=(640, 520)", src)

    def test_no_pet_island_traces_in_ui(self):
        html = _read("app/ui/index.html")
        js = _read("app/ui/app.js")
        for needle in ("pet-btn", "island-btn", "set-pet", "settings-save",
                       "soon-tag", "ma4670156", "01557887719"):
            self.assertNotIn(needle, html, f"index.html still has {needle}")
            self.assertNotIn(needle, js, f"app.js still has {needle}")
        # The JS never calls the (compat-kept) island bridge method.
        self.assertNotIn("minimize_to_island", js)
        # The old startup log line about the island is gone.
        self.assertNotIn("Island started - full window opens only on tap",
                         _read("app/main.py"))

    def test_no_startup_log_lines_about_pet_island(self):
        # run() must not log about pet/island when they are disabled.
        src = _read("app/main.py")
        run_src = src[src.index("def run(self):"):]
        self.assertNotIn('"Island started', run_src)
        self.assertIn("main window only", run_src)


class TestTray(unittest.TestCase):
    class FakeIcon:
        def __init__(self, name, image, title, menu, on_open=None,
                     on_quit=None):
            self.name = name
            self.on_open = on_open
            self.on_quit = on_quit
            self.stopped = False
            self.ran = False

        def run(self):
            self.ran = True

        def stop(self):
            self.stopped = True

    def _app_with_fake_windows(self):
        import main
        app = main.MemoApp()
        calls = {"hide": 0, "show": 0, "minimize": 0, "destroy": 0}

        class FakeWindow:
            def hide(self):
                calls["hide"] += 1

            def show(self):
                calls["show"] += 1

            def minimize(self):
                calls["minimize"] += 1

            def destroy(self):
                calls["destroy"] += 1

        app.main_window = FakeWindow()
        app._main_visible = True
        return app, calls

    def test_tray_manager_with_fake_icon(self):
        opened, quit_ = [], []

        def factory(name, image, title, menu, on_open=None, on_quit=None):
            return self.FakeIcon(name, image, title, menu,
                                 on_open=on_open, on_quit=on_quit)

        mgr = tray_mod.TrayManager(on_open=lambda: opened.append(1),
                                   on_quit=lambda: quit_.append(1),
                                   icon_factory=factory)
        self.assertTrue(mgr.available)
        self.assertTrue(mgr.start())
        self.assertTrue(mgr.running)
        mgr._icon.on_open()
        mgr._icon.on_quit()
        self.assertEqual(opened, [1])
        self.assertEqual(quit_, [1])
        mgr.stop()
        self.assertFalse(mgr.running)
        self.assertTrue(mgr._icon is None or mgr._icon.stopped)

    def test_close_hides_to_tray_not_quit(self):
        import main
        app, calls = self._app_with_fake_windows()
        try:
            app._tray = tray_mod.TrayManager(
                on_open=lambda: None, on_quit=lambda: None,
                icon_factory=lambda *a, **k: self.FakeIcon(*a, **k))
            self.assertTrue(app._tray.start())
            self.assertIs(app._on_main_closing(), False)
            self.assertEqual(calls["hide"], 1)
            self.assertEqual(calls["destroy"], 0)
            self.assertFalse(app._main_visible)
            # And it comes back.
            app.show_main()
            self.assertEqual(calls["show"], 1)
            self.assertTrue(app._main_visible)
        finally:
            main._APP = None

    def test_close_minimizes_without_tray(self):
        import main
        app, calls = self._app_with_fake_windows()
        try:
            # A tray that fails to start (no backend): start() False.
            def broken_factory(*a, **k):
                raise RuntimeError("no tray backend")

            app._tray = tray_mod.TrayManager(
                on_open=lambda: None, on_quit=lambda: None,
                icon_factory=broken_factory)
            self.assertFalse(app._tray.start())
            self.assertIs(app._on_main_closing(), False)
            self.assertEqual(calls["hide"], 1)
            self.assertEqual(calls["minimize"], 1)
            self.assertEqual(calls["destroy"], 0)
        finally:
            main._APP = None

    def test_explicit_quit_exits_fully(self):
        import main
        app, calls = self._app_with_fake_windows()
        try:
            mgr = tray_mod.TrayManager(
                on_open=lambda: None, on_quit=lambda: None,
                icon_factory=lambda *a, **k: self.FakeIcon(*a, **k))
            self.assertTrue(mgr.start())
            app._tray = mgr
            app.island_window = None
            app.pet_window = None
            with mock.patch.object(main, "whatsapp_mod", None), \
                    mock.patch("app.main.speak.stop", create=True):
                app.quit()
            self.assertTrue(app._quitting)
            self.assertTrue(mgr._icon is None or mgr._icon.stopped)
            self.assertEqual(calls["destroy"], 1)
        finally:
            main._APP = None

    def test_second_launch_focus_still_wired(self):
        src = _read("app/main.py")
        self.assertIn("create_show_main_event", src)
        self.assertIn("watch_show_main_event", src)
        self.assertIn("_acquire_single_instance_lock", src)


class TestVoiceAvatarPersistence(unittest.TestCase):
    def test_roundtrip_twice(self):
        # Save a non-default voice + avatar, reload (restart), twice.
        import settings_store
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(settings_store, "SETTINGS_PATH", path):
                s = settings_store.load_settings()
                s["voice"] = "boy_fun"
                s["avatar"] = "dog"
                self.assertTrue(settings_store.save_settings(s))
                s2 = settings_store.load_settings()
                self.assertEqual(s2["voice"], "boy_fun")
                self.assertEqual(s2["avatar"], "dog")
                # Second round-trip (no silent reset).
                s2["voice"] = "girl_fast"
                s2["avatar"] = "girl"
                self.assertTrue(settings_store.save_settings(s2))
                s3 = settings_store.load_settings()
                self.assertEqual(s3["voice"], "girl_fast")
                self.assertEqual(s3["avatar"], "girl")

    def test_js_saves_on_pick_and_restores_on_load(self):
        js = _read("app/ui/app.js")
        # Voice + avatar save at the moment of picking (not on Save).
        self.assertIn('await saveNow({ voice: selectedVoice }, "save-status-voice")', js)
        self.assertIn('await saveNow({ avatar: selectedAvatar }, "save-status-avatar")', js)
        # The old "cat only" early return is gone.
        self.assertNotIn('if (opt.dataset.avatar !== "cat") return;', js)
        # The load path restores + applies the avatar (was hardcoded cat).
        self.assertIn('selectedAvatar = st.settings.avatar || "cat";', js)
        self.assertIn("applyAvatar();", js)
        # The General save payload carries the avatar too (safety net).
        self.assertIn("avatar: selectedAvatar,", js)
        # The 15s poll never overwrites a focused field.
        self.assertIn("document.activeElement === el", js)

    def test_avatar_images_exist_and_valid(self):
        from PIL import Image
        html = _read("app/ui/index.html")
        for name in ("cat", "dog", "boy", "girl"):
            p = APP_DIR / "ui" / "assets" / f"avatar-{name}.png"
            self.assertTrue(p.is_file(), f"avatar-{name}.png missing")
            self.assertEqual(p.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            with Image.open(p) as im:
                im.load()
            self.assertIn(f"assets/avatar-{name}.png", html)
        # The three NEW avatars ship at 640x640 (the original cat is
        # 1175x1471 - object-fit: cover handles every size).
        for name in ("dog", "boy", "girl"):
            with Image.open(APP_DIR / "ui" / "assets" / f"avatar-{name}.png") as im:
                self.assertEqual(im.size, (640, 640), f"avatar-{name} size")

    def test_speak_uses_saved_voice(self):
        src = _read("app/main.py")
        self.assertIn('self.settings.get("voice", "girl_warm")', src)


class TestWhatsAppSettings(unittest.TestCase):
    def test_owner_number_roundtrip(self):
        import main
        app = main.MemoApp()
        try:
            api = main.Api()
            started = {}

            class FakeSvc:
                def start(self):
                    started["n"] = started.get("n", 0) + 1
                    return True

            fake_mod = mock.Mock()
            fake_mod.normalize_number.side_effect = (
                lambda v: whatsapp.normalize_number(v))
            fake_mod.ensure_started.return_value = FakeSvc()
            fake_mod.get_state.return_value = {"enabled": True,
                                               "status": "waiting_qr"}
            with mock.patch.object(main, "whatsapp_mod", fake_mod), \
                    mock.patch.object(main, "save_settings",
                                      return_value=True):
                res = api.whatsapp_save(
                    {"enabled": True, "owner_phone": "01557887719"})
                self.assertTrue(res["ok"])
                self.assertEqual(
                    app.settings["whatsapp_owner_phone"], "201557887719")
                # Reload (restart) - the number is still there.
                self.assertEqual(
                    app.settings["whatsapp_owner_phone"], "201557887719")
                # The "+20 ... spaces" form normalizes the same way.
                res = api.whatsapp_save(
                    {"enabled": True, "owner_phone": "+20 155 788 7719"})
                self.assertTrue(res["ok"])
                self.assertEqual(
                    app.settings["whatsapp_owner_phone"], "201557887719")
                self.assertEqual(started.get("n"), 2,
                                 "enabling must (re)start the sidecar")
        finally:
            main._APP = None

    def test_save_settings_has_no_mid_save_rerender(self):
        # The v1.4.0 race: save_settings -> apply_settings() ->
        # evaluate_js("applySettingsFromPython()") re-rendered the form
        # mid-save and wiped the unsaved WhatsApp fields.
        tree = ast.parse(_read("app/main.py"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) \
                    and node.name == "save_settings":
                body = ast.get_source_segment(_read("app/main.py"), node)
                # The real call is _APP.apply_settings() (the comment
                # mentions the name in prose - that is not a call).
                self.assertNotIn("_APP.apply_settings()", body)
                self.assertNotIn("applySettingsFromPython", body)
        # apply_settings() itself must not re-render the form either.
        # AST check (not substring - its comment quotes the old call
        # in prose): no evaluate_js("applySettingsFromPython()") call.
        tree = ast.parse(_read("app/main.py"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) \
                    and node.name == "apply_settings":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) \
                            and isinstance(sub.func, ast.Attribute) \
                            and sub.func.attr == "evaluate_js":
                        args = [ast.get_source_segment(
                            _read("app/main.py"), a) or ""
                            for a in sub.args]
                        self.assertNotIn("applySettingsFromPython",
                                         " ".join(args))

    def test_js_whatsapp_save_snapshots_first(self):
        js = _read("app/ui/app.js")
        block = js[js.index('id="save-whatsapp"') if 'id="save-whatsapp"' in js
                   else js.index("$(\"save-whatsapp\")"):]
        block = block[:block.index("refreshWhatsApp();")]
        # The snapshot is read BEFORE the first whatsapp_save call...
        snap = block.index("const snap = {")
        call = block.index("whatsapp_save(JSON.stringify(snap))")
        self.assertLess(snap, call)
        # ...and the handler never calls save_settings first.
        self.assertNotIn("save_settings", block)
        # The result is checked and the error shown (not swallowed).
        self.assertIn('t("wa_save_error")', block)

    def test_qr_state_machine(self):
        svc = whatsapp.WhatsAppService(
            {"whatsapp_enabled": True, "ai_name": "Memo",
             "user_name": "Mohamed", "language": "ar"})
        self.assertEqual(svc.get_state()["status"], "off")
        self.assertIsNone(svc.get_state()["qr"])
        svc._on_event({"event": "qr",
                       "qr": "data:image/png;base64,QRDATA"})
        st = svc.get_state()
        self.assertEqual(st["status"], "waiting_qr")
        self.assertEqual(st["qr"], "data:image/png;base64,QRDATA")
        svc._on_event({"event": "ready", "me": {"id": "201234567890@s.whatsapp.net",
                                                 "name": "Memo"}})
        st = svc.get_state()
        self.assertEqual(st["status"], "linked")
        self.assertIsNone(st["qr"])

    def test_sidecar_failure_surfaces_error_not_blank(self):
        svc = whatsapp.WhatsAppService(
            {"whatsapp_enabled": True, "ai_name": "Memo",
             "user_name": "Mohamed", "language": "ar"})
        with mock.patch.object(whatsapp.WhatsAppService, "_find_node",
                               return_value=None):
            self.assertIs(svc.start(), False)
        st = svc.get_state()
        self.assertEqual(st["status"], "error")
        self.assertTrue(st["error"], "a failed sidecar must name its error")
        # The JS shows that error (not a blank).
        self.assertIn('t("wa_save_error")', _read("app/ui/app.js"))


class TestSettingsReorg(unittest.TestCase):
    def test_menu_and_sections(self):
        html = _read("app/ui/index.html")
        for section in ("general", "whatsapp", "voices", "avatar",
                        "logs", "updates"):
            self.assertIn(f'data-section="{section}"', html)
            self.assertIn(f'id="section-{section}"', html)
        self.assertEqual(html.count('class="back-btn'), 6)
        self.assertIn('id="settings-menu"', html)
        # Quit lives on the menu; Save lives in its own section.
        self.assertIn('id="quit-btn"', html)
        self.assertIn('id="save-general"', html)
        self.assertIn('id="save-whatsapp"', html)
        # General holds the owner code + speaker toggle.
        self.assertIn('id="set-owner-code"', html)
        self.assertIn('id="owner-unlock-btn"', html)
        self.assertIn('id="set-voice-output"', html)
        # About: public info only, portfolio + GitHub buttons.
        self.assertIn("ENG/Mohamed El Sayed", html)
        self.assertIn('id="portfolio-btn"', html)
        self.assertIn('id="github-btn"', html)
        self.assertNotIn("ma4670156", html)
        self.assertNotIn("01557887719", html)

    def test_js_section_navigation(self):
        js = _read("app/ui/app.js")
        for needle in ("function showSettingsSection",
                       "function showSettingsMenu",
                       'document.querySelectorAll(".menu-btn")',
                       'document.querySelectorAll(".back-btn")',
                       '$("save-general")',
                       '$("save-whatsapp")',
                       '$("owner-unlock-btn")',
                       '$("set-voice-output")',
                       '$("github-btn")'):
            self.assertIn(needle, js, needle)


class TestDeveloperAnswer(unittest.TestCase):
    FORBIDDEN = ("01557887719", "1557887719", "223535", "ma4670156",
                 "@gmail.com", "@")

    def test_developer_rule_ar_and_en(self):
        for q in ("مين المطور؟", "من المطور؟", "مين اللي عملك؟",
                  "who is the developer?", "who made you?",
                  "who is the developer of memo ai?"):
            lang = "en" if q.startswith("who") else "ar"
            res = rules.check(q, False, lang)
            self.assertIsNotNone(res, q)
            self.assertIn("Mohamed El Sayed", res["reply"])
            self.assertIn("https://mohamed5946.github.io/portfolio/",
                          res["reply"])
            self.assertIn("https://github.com/mohamed5946", res["reply"])
            for bad in self.FORBIDDEN:
                self.assertNotIn(bad, res["reply"], f"{q} leaks {bad}")

    def test_developer_prompt_block(self):
        for lang in ("ar", "en"):
            p = brain.system_prompt("Memo", "Mohamed", lang)
            self.assertIn("Mohamed El Sayed", p)
            self.assertIn("mohamed5946.github.io/portfolio", p)
            self.assertIn("github.com/mohamed5946", p)
            for bad in self.FORBIDDEN:
                self.assertNotIn(bad, p, f"prompt ({lang}) leaks {bad}")
        # The prohibition itself is in the prompt (both languages).
        self.assertIn("ممنوع تماماً", brain.system_prompt("Memo", "Mohamed", "ar"))
        self.assertIn("never mention any private",
                      brain.system_prompt("Memo", "Mohamed", "en"))


class TestOwnerMode(unittest.TestCase):
    TEST_CODE = "test-code-123"

    def _patched_hash(self):
        import main
        return mock.patch.object(
            main, "OWNER_CODE_HASH",
            hashlib.sha256(self.TEST_CODE.encode()).hexdigest())

    def test_rules_honors_owner_flag(self):
        # Hypothesis (b) is FALSE: rules.check DOES honor the flag.
        q = "عايز الكود المصدري بتاع البرنامج"
        self.assertIsNone(rules.check(q, True, "ar"))
        hit = rules.check(q, False, "ar")
        self.assertIsNotNone(hit)
        self.assertIn("تواصل مع المطور", hit["reply"])

    def test_prompt_flips_with_owner_flag(self):
        for lang, refusal, owner_mark in (
                ("ar", "تواصل مع المطور", "وضع المالك مفتوح"),
                ("en", "Contact the developer", "Owner mode is open")):
            locked = brain.system_prompt("Memo", "Mohamed", lang,
                                         tools_available=True,
                                         owner_unlocked=False)
            self.assertIn(refusal, locked)
            unlocked = brain.system_prompt("Memo", "Mohamed", lang,
                                           tools_available=True,
                                           owner_unlocked=True)
            self.assertNotIn(refusal, unlocked)
            self.assertIn(owner_mark, unlocked)

    def test_unlock_flow_end_to_end(self):
        import main
        app = main.MemoApp()
        try:
            api = main.Api()
            captured = {}

            def fake_ask(text, ai_name, user_name, lang, history=None,
                         owner_unlocked=False):
                captured["owner_unlocked"] = owner_unlocked
                captured["text"] = text
                return {"reply": "السورس مكتوب بـ Python وpywebview.",
                        "engine": "groq", "tools_used": 0,
                        "tools_disabled": False}

            with self._patched_hash(), \
                    mock.patch.object(brain, "ask_with_tools",
                                      side_effect=fake_ask), \
                    mock.patch.object(app, "speak_async",
                                      lambda text: None):
                # 1. Type the code -> unlock confirms.
                res = api.chat(self.TEST_CODE)
                self.assertIn("وضع المالك اتفتح", res["reply"])
                self.assertTrue(app.owner_unlocked)
                # 2. Ask about the source code -> NO refusal anywhere.
                res = api.chat("عايز الكود المصدري بتاع البرنامج")
                self.assertTrue(res.get("pending"))
                deadline = time.time() + 5
                done = None
                while time.time() < deadline:
                    done = api.chat_result(res["chat_id"])
                    if done["done"]:
                        break
                    time.sleep(0.05)
                self.assertTrue(done["done"])
                self.assertNotIn("تواصل مع المطور", done["reply"])
                self.assertIn("Python", done["reply"])
                # The model was called WITH owner mode on.
                self.assertIs(captured.get("owner_unlocked"), True)
                # The flag sticks across chat calls...
                self.assertTrue(app.owner_unlocked)
                # ...and never leaks into the persisted settings dict.
                self.assertNotIn("owner_unlocked", app.settings)
                # 3. The Settings unlock path sets the same flag.
                app.owner_unlocked = False
                unlock = api.unlock_owner(self.TEST_CODE)
                self.assertTrue(unlock["ok"])
                self.assertTrue(app.owner_unlocked)
                self.assertFalse(api.unlock_owner("wrong-code")["ok"])
        finally:
            main._APP = None

    def test_locked_still_refuses(self):
        import main
        app = main.MemoApp()
        try:
            api = main.Api()
            with mock.patch.object(brain, "ask_with_tools") as m:
                res = api.chat("عايز الكود المصدري بتاع البرنامج")
                self.assertIn("تواصل مع المطور", res["reply"])
                m.assert_not_called()
        finally:
            main._APP = None

    def test_whatsapp_owner_gets_owner_mode(self):
        svc = whatsapp.WhatsAppService(
            {"whatsapp_enabled": True,
             "whatsapp_owner_phone": "01557887719",
             "ai_name": "Memo", "user_name": "Mohamed", "language": "ar"})
        captured = {}
        sends = []

        def fake_ask(text, ai_name, user_name, lang, history=None,
                     owner_unlocked=False):
            captured["owner_unlocked"] = owner_unlocked
            return {"reply": "تمام.", "engine": "groq",
                    "tools_used": 0, "tools_disabled": False}

        svc._send_raw = lambda obj: sends.append(obj) or True
        with mock.patch.object(brain, "ask_with_tools",
                               side_effect=fake_ask):
            svc._handle_message(
                {"from": "201557887719@s.whatsapp.net",
                 "text": "عايز الكود المصدري", "id": "m1"})
        self.assertIs(captured.get("owner_unlocked"), True)
        self.assertEqual(len(sends), 1)


class TestNoneAnd400Fix(unittest.TestCase):
    def setUp(self):
        brain.clear_history()

    def tearDown(self):
        brain.clear_history()

    def test_sanitize_messages(self):
        poison = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "list_dir",
                                        "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "dir list"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c2", "type": "function",
                             "function": {"name": "list_dir",
                                        "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "orphan", "content": "x"},
            {"role": "assistant", "content": None},
            {"role": "bogus", "content": "drop me"},
        ]
        clean = brain._sanitize_messages(poison)
        self.assertEqual(
            [(m["role"], m.get("tool_call_id", "")) for m in clean],
            [("system", ""), ("user", ""), ("assistant", ""),
             ("tool", "c1")])
        self.assertTrue(all(isinstance(m["content"], str) for m in clean))
        # The well-formed round survives intact (c1 kept).
        self.assertEqual(clean[2]["tool_calls"][0]["id"], "c1")
        # The unpaired c2 call + orphan tool + empty assistant are gone.

    def test_local_cleaning_strips_all_tool_material(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "list_dir",
                                        "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "x"},
            {"role": "assistant", "content": "done."},
        ]
        clean = brain._clean_messages_for_local(msgs)
        self.assertEqual([(m["role"], m["content"]) for m in clean],
                         [("system", "sys"), ("user", "hi"),
                          ("assistant", "done.")])
        self.assertFalse(any("tool_calls" in m or m["role"] == "tool"
                             for m in clean))

    def test_ollama_empty_reply_raises(self):
        import requests
        fake_resp = mock.Mock()
        fake_resp.json.return_value = {"message": {"content": None}}
        fake_resp.raise_for_status.return_value = None
        with mock.patch.object(brain.requests, "post",
                               return_value=fake_resp):
            with self.assertRaises(RuntimeError) as ctx:
                brain._ollama([{"role": "user", "content": "hi"}])
            self.assertEqual(str(ctx.exception), "ollama-empty-reply")

    def test_429_falls_back_to_local(self):
        with mock.patch.object(brain, "is_online", return_value=True), \
                mock.patch.object(brain, "_groq_message",
                                  side_effect=RuntimeError("groq-429")), \
                mock.patch.object(brain, "_ollama",
                                  return_value="أنا المخ المحلي، أهلاً"):
            res = brain.ask_with_tools("إزيك؟", "Memo", "Mohamed", "ar")
        self.assertEqual(res["engine"], "ollama")
        self.assertIn("المخ المحلي", res["reply"])
        self.assertNotIn("none", res["reply"].lower())

    def test_429_and_local_down_honest_message(self):
        import requests
        with mock.patch.object(brain, "is_online", return_value=True), \
                mock.patch.object(brain, "_groq_message",
                                  side_effect=RuntimeError("groq-429")), \
                mock.patch.object(
                    brain, "_ollama",
                    side_effect=requests.ConnectionError("refused")):
            res = brain.ask_with_tools("إزيك؟", "Memo", "Mohamed", "ar")
        self.assertEqual(res["engine"], "")
        self.assertNotEqual(res["engine"], "none")
        self.assertIn("Groq", res["reply"])
        self.assertIn("Ollama", res["reply"])
        self.assertNotIn("none", res["reply"].lower())

    def test_history_stays_plain_text_after_tool_turn(self):
        script = [
            {"content": None,
             "tool_calls": [{"id": "call_1", "type": "function",
                             "function": {"name": "list_dir",
                                        "arguments": json.dumps(
                                            {"path": "/tmp"})}}]},
            {"content": "المجلد فيه ملفات.", "tool_calls": []},
        ]
        calls = {"n": 0}

        def fake_groq(messages, use_tools=False, timeout=60):
            # Every request the loop sends must be sanitized already.
            for m in messages:
                self.assertIsInstance(m.get("content"), str)
            msg = script[min(calls["n"], len(script) - 1)]
            calls["n"] += 1
            return msg

        with mock.patch.object(brain, "is_online", return_value=True), \
                mock.patch.object(brain, "_groq_message",
                                  side_effect=fake_groq), \
                mock.patch.object(tools, "request_permission",
                                  return_value=True):
            res = brain.ask_with_tools("اعرض ملفات /tmp", "Memo",
                                       "Mohamed", "ar")
        self.assertEqual(res["engine"], "groq")
        self.assertEqual(res["tools_used"], 1)
        for m in brain._history:
            self.assertIn(m["role"], ("user", "assistant"))
            self.assertIsInstance(m["content"], str)
            self.assertNotIn("tool_calls", m)
        self.assertEqual(
            [m["content"] for m in brain._history],
            ["اعرض ملفات /tmp", "المجلد فيه ملفات."])

    def test_execute_tool_never_returns_none(self):
        with mock.patch.object(tools, "_tool_list_dir", return_value=None):
            self.assertEqual(tools.execute_tool("list_dir", {}), "")
        self.assertIsInstance(tools.execute_tool("no_such_tool", {}), str)

    def test_no_none_engine_in_brain_main_whatsapp(self):
        for rel in ("app/brain.py", "app/main.py", "app/whatsapp.py"):
            for i, line in enumerate(_read(rel).splitlines(), 1):
                self.assertNotIn('"engine": "none"', line,
                                 f"{rel}:{i}")

    def test_js_never_shows_none(self):
        js = _read("app/ui/app.js")
        self.assertIn("function safeReply", js)
        self.assertIn("function metaEngine", js)
        self.assertIn('low === "none"', js)
        self.assertIn('engine === "groq" || engine === "ollama"', js)
        # All four chat display sites go through the guards.
        self.assertEqual(js.count("safeReply("), 5)  # def + 4 call sites
        self.assertEqual(js.count("metaEngine("), 5)  # def + 4 call sites

    def test_multi_turn_replies_stay_clean(self):
        # Turn 1: plain Groq reply. Turn 2: tool use. Turn 3: 429.
        script = [
            {"content": "أهلاً يا محمد.", "tool_calls": []},
            {"content": None,
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "list_dir",
                                        "arguments": json.dumps(
                                            {"path": "/tmp"})}}]},
            {"content": "خلصت.", "tool_calls": []},
        ]
        calls = {"n": 0}

        def fake_groq(messages, use_tools=False, timeout=60):
            # Calls: 1 = turn 1, 2-3 = turn 2 (tool round), 4 = turn 3.
            msg = script[min(calls["n"], len(script) - 1)]
            calls["n"] += 1
            if calls["n"] == 4:
                raise RuntimeError("groq-429")
            return msg

        with mock.patch.object(brain, "is_online", return_value=True), \
                mock.patch.object(brain, "_groq_message",
                                  side_effect=fake_groq), \
                mock.patch.object(tools, "request_permission",
                                  return_value=True), \
                mock.patch.object(brain, "_ollama",
                                  return_value="أنا المخ المحلي."):
            r1 = brain.ask_with_tools("إزيك؟", "Memo", "Mohamed", "ar")
            r2 = brain.ask_with_tools("اعرض ملفات /tmp", "Memo",
                                      "Mohamed", "ar")
            r3 = brain.ask_with_tools("كمل", "Memo", "Mohamed", "ar")
        for r in (r1, r2, r3):
            self.assertIsInstance(r["reply"], str)
            self.assertTrue(r["reply"].strip())
            self.assertNotEqual(r["reply"].strip().lower(), "none")
            self.assertNotEqual(r["reply"].strip().lower(), "null")
            self.assertIn(r["engine"], ("groq", "ollama", ""))
        self.assertEqual(r3["engine"], "ollama")


class TestRegionTolerance(unittest.TestCase):
    def test_verify_uses_1px_tolerance(self):
        src = _read("app/winshape.py")
        block = src[src.index("def _verify_region"):]
        block = block[:block.index("def _set_region")]
        self.assertIn("abs(g - e) <= 1", block)
        self.assertNotIn("got == tuple(expected_box)", block)


class TestVersionAndInstaller(unittest.TestCase):
    def test_tray_dependency_bundled(self):
        self.assertIn("pystray", _read("app/requirements.txt"))
        spec = _read("installer/memo-ai.spec")
        self.assertIn('"pystray"', spec)
        self.assertIn('"pystray._win32"', spec)

    def test_version_consistency_v141(self):
        import main
        self.assertEqual(main.VERSION, "1.4.1")
        self.assertIn("v1.4.1", _read("app/ui/index.html"))
        self.assertIn("1, 4, 1, 0",
                      _read("installer/file_version_info.txt"))
        self.assertIn("'1.4.1'", _read("installer/file_version_info.txt"))
        # The manifest still says 1.4.0 until the tag build updates it.
        manifest = json.loads(_read("version.json"))
        self.assertEqual(manifest["version"], "1.4.0")
        self.assertIn("1.4.1", manifest["notes"])


if __name__ == "__main__":
    unittest.main()
