# Memo AI v1.3.2 - headless tests (run on Linux, no Windows, no keys).
#
#   python -m unittest discover -s app/tests -v
#
# What is proven here (all the v1.3.2 field failures, reproduced as
# failing-before / passing-after checks):
#   * name bug: the system prompt states the user's name explicitly.
#   * tools refusal: ask_with_tools really calls open_app / create_file
#     (mocked Groq + mocked permission + mocked launch), result feeds back.
#   * persona: the prompt forbids flat refusals and carries the real
#     capability state (WhatsApp building, screen translation not built).
#   * hang: no .native/.Handle access anywhere in main.py; the morph
#     loop is bounded; min_size lets it shrink without .NET juggling.
#   * black box: move_pet never moves a hidden pet; pet.html only roams
#     when Settings confirm visible; suspendRoaming exists.
#   * pet drag: the drag handlers + instrumentation exist in pet.html.
#   * cat fly + bigger chat: app.js/styles.css hooks exist.
#   * installer: AppMutex + CloseApplications (no more install-over-running).
#   * self-test: READY markers on all three pages + watchdog in main.py.
#   * chroma-key stays deleted.
#
# NOT proven here (needs real Windows + a live Groq key): the model
# actually CHOOSING the tool on a live call, the real click/transparency
# on the user's desktop, the installer refusing a running instance.
# Those are the field tests in the release notes. The live-Groq test
# below (test_live_groq_open_vscode) runs only when GROQ_API_KEY is set.

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

import brain  # noqa: E402
import capabilities  # noqa: E402
import tools  # noqa: E402
import winshape  # noqa: E402


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class TestSystemPromptName(unittest.TestCase):
    def test_name_stated_explicitly_ar(self):
        p = brain.system_prompt("Memo", "Mohamed", "ar")
        self.assertIn("اسم صاحبك هو Mohamed", p)
        self.assertIn("اسمي إيه؟", p)

    def test_name_stated_explicitly_en(self):
        p = brain.system_prompt("Memo", "Mohamed", "en")
        self.assertIn("Your friend's name is Mohamed", p)

    def test_old_prompt_never_said_the_name(self):
        # Regression guard: the v1.3.1 wording ("on Mohamed's laptop")
        # is exactly what made the brain answer "I don't know your name".
        p = brain.system_prompt("Memo", "Mohamed", "ar")
        self.assertNotIn("على لابتوب Mohamed", p)

    def test_missing_name_asks_once(self):
        p = brain.system_prompt("Memo", "", "ar")
        self.assertIn("اسمك إيه؟", p)
        self.assertNotIn("قوله غير معروف", p)


class TestPersonaAndCapabilities(unittest.TestCase):
    def test_no_flat_refusal_phrasing_in_prompt(self):
        # The prompt QUOTES the flat refusal once, inside the rule that
        # FORBIDS it - that is the point. It must never appear as
        # something the brain may say.
        p_ar = brain.system_prompt("Memo", "Mohamed", "ar")
        self.assertEqual(p_ar.count("مش هقدر أساعدك"), 1)
        self.assertIn("ممنوعة", p_ar)
        p_en = brain.system_prompt("Memo", "Mohamed", "en")
        self.assertEqual(p_en.count("I can't, sorry I can't help"), 1)
        self.assertIn("forbidden", p_en)

    def test_refusal_rules_present(self):
        p = brain.system_prompt("Memo", "Mohamed", "ar")
        self.assertIn("ممنوع ترفض رفض جاف", p)
        self.assertIn("ليه؟", p)
        self.assertIn("قدّم بديل", p)

    def test_capability_block_present(self):
        p = brain.system_prompt("Memo", "Mohamed", "ar")
        self.assertIn("قدراتك الحقيقية", p)
        self.assertIn("الأدوات على اللابتوب", p)
        self.assertIn("الواتساب", p)
        self.assertIn("ترجمة الشاشة", p)

    def test_whatsapp_state_is_building_not_linked(self):
        self.assertEqual(capabilities.WHATSAPP, "building")
        self.assertFalse(capabilities.WHATSAPP_LINKED)
        line = capabilities.whatsapp_status_line("ar")
        self.assertIn("مش مربوط", line)
        self.assertIn("QR", line)
        # The brain must never claim WhatsApp works yet.
        self.assertNotIn("مربوط ✅", line)

    def test_tools_directive_calls_tool_directly(self):
        p = brain.system_prompt("Memo", "Mohamed", "ar", tools_available=True)
        self.assertIn("نادي الأداة", p)
        self.assertIn("مش قادر أفتح البرامج", p)  # as a FORBIDDEN sentence
        # Old v1.3.0 phrasing taught the model to ASK instead of calling.
        self.assertNotIn("استنى الإذن", p)

    def test_human_persona(self):
        p = brain.system_prompt("Memo", "Mohamed", "ar")
        self.assertIn("زي إنسان وصاحب مش زي آلة", p)


class TestToolLoop(unittest.TestCase):
    def setUp(self):
        brain.clear_history()

    def tearDown(self):
        brain.clear_history()

    def _mock_groq(self, script):
        """script: list of messages returned by successive Groq calls."""
        calls = {"n": 0}

        def fake(messages, use_tools=False, timeout=60):
            self.assertTrue(use_tools, "tool loop must send tools to Groq")
            msg = script[min(calls["n"], len(script) - 1)]
            calls["n"] += 1
            return msg

        return fake

    def test_open_vscode_calls_open_app(self):
        # Field failure: "ممكن تفتح VS Code؟" got a text refusal.
        # The tool loop must produce a real open_app call instead.
        launched = {}
        script = [
            {"content": "", "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "open_app",
                              "arguments": json.dumps({"app": "vscode"})}}]},
            {"content": "فتحت VS Code يا محمد ✅", "tool_calls": []},
        ]
        with mock.patch.object(brain, "is_online", return_value=True), \
                mock.patch.object(brain, "_groq_message",
                                  side_effect=self._mock_groq(script)), \
                mock.patch.object(tools, "request_permission",
                                  return_value=True), \
                mock.patch.object(tools, "_find_app_exe",
                                  return_value=r"C:\VS Code\Code.exe"), \
                mock.patch.object(tools, "_launch",
                                  side_effect=lambda p: launched.update(path=p)):
            res = brain.ask_with_tools("ممكن تفتح VS Code؟", "Memo",
                                       "Mohamed", "ar")
        self.assertEqual(res["tools_used"], 1)
        self.assertEqual(res["engine"], "groq")
        self.assertIn("VS Code", res["reply"])
        self.assertEqual(launched.get("path"), r"C:\VS Code\Code.exe")

    def test_create_file_executes_and_feeds_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "notes.txt")
            script = [
                {"content": "", "tool_calls": [
                    {"id": "call_1", "type": "function",
                     "function": {"name": "create_file",
                                  "arguments": json.dumps(
                                      {"path": target,
                                       "content": "hello memo"})}}]},
                {"content": "عملت الملف يا محمد ✅", "tool_calls": []},
            ]
            with mock.patch.object(brain, "is_online", return_value=True), \
                    mock.patch.object(brain, "_groq_message",
                                      side_effect=self._mock_groq(script)), \
                    mock.patch.object(tools, "request_permission",
                                      return_value=True):
                res = brain.ask_with_tools(
                    "اعمل ملف notes.txt واكتب فيه hello memo",
                    "Memo", "Mohamed", "ar")
            self.assertEqual(res["tools_used"], 1)
            self.assertEqual(Path(target).read_text(encoding="utf-8"),
                             "hello memo")
            self.assertIn("الملف", res["reply"])

    def test_permission_denied_means_nothing_runs(self):
        launched = {}
        script = [
            {"content": "", "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "open_app",
                              "arguments": json.dumps({"app": "vscode"})}}]},
            {"content": "تمام، مش هفتحه.", "tool_calls": []},
        ]
        with mock.patch.object(brain, "is_online", return_value=True), \
                mock.patch.object(brain, "_groq_message",
                                  side_effect=self._mock_groq(script)), \
                mock.patch.object(tools, "request_permission",
                                  return_value=False), \
                mock.patch.object(tools, "_find_app_exe",
                                  return_value=r"C:\VS Code\Code.exe"), \
                mock.patch.object(tools, "_launch",
                                  side_effect=lambda p: launched.update(path=p)):
            res = brain.ask_with_tools("افتح VS Code", "Memo", "Mohamed", "ar")
        self.assertEqual(res["tools_used"], 1)
        self.assertNotIn("path", launched)


class TestHangFixes(unittest.TestCase):
    def test_no_native_handle_access_in_main(self):
        # Real code-level check via AST: no attribute access named
        # "native" or "Handle" anywhere (docstrings/comments may still
        # mention them as history). Any such access from a background
        # thread could steal WinForms handle creation and freeze the UI.
        import ast
        tree = ast.parse(_read("app/main.py"))
        bad = [n.attr for n in ast.walk(tree)
               if isinstance(n, ast.Attribute)
               and n.attr in ("native", "Handle")]
        self.assertEqual(bad, [])
        self.assertNotIn("def _window_hwnd", _read("app/main.py"))

    def test_morph_loop_is_bounded(self):
        src = _read("app/main.py")
        self.assertIn("steps = max(1, int(duration_ms / 16))", src)
        self.assertIn("for i in range(1, steps + 1):", src)

    def test_morph_never_touches_min_size(self):
        src = _read("app/main.py")
        self.assertNotIn("clear_min_size(native)", src)
        self.assertNotIn("restore_min_size(native", src)

    def test_main_window_min_size_allows_pill_morph(self):
        src = _read("app/main.py")
        self.assertIn("min_size=(190, 52)", src)

    def test_morph_math_endpoints(self):
        self.assertEqual(winshape.ease_in_out_cubic(0), 0)
        self.assertEqual(winshape.ease_in_out_cubic(1), 1)
        self.assertAlmostEqual(winshape.ease_in_out_cubic(0.5), 0.5)
        self.assertEqual(winshape.lerp_rect((0, 0, 100, 100),
                                            (200, 200, 50, 50), 1),
                         (200, 200, 50, 50))

    def test_hwnd_lookup_is_enumwindows_only(self):
        src = _read("app/main.py")
        self.assertIn("EnumWindows", src)
        self.assertIn("def _find_hwnd_by_title", src)
        self.assertIn("def _find_main_hwnd", src)


class TestBlackBoxAndPet(unittest.TestCase):
    def test_move_pet_never_moves_hidden_pet(self):
        import main
        app = main.MemoApp()
        try:
            calls = []

            class FakeWindow:
                def move(self, x, y):
                    calls.append((x, y))

            app.pet_window = FakeWindow()
            api = main.Api()
            app.settings["pet_visible"] = False
            api.move_pet(100, 200)
            self.assertEqual(calls, [], "hidden pet must never be moved")
            app.settings["pet_visible"] = True
            api.move_pet(100, 200)
            self.assertEqual(calls, [(100, 200)])
        finally:
            main._APP = None

    def test_pet_html_roam_guards(self):
        html = _read("app/ui/pet.html")
        self.assertIn("roamingAllowed", html)
        self.assertIn("window.suspendRoaming", html)
        self.assertIn("window.resumeRoaming", html)
        # The v1.3.1 unconditional fallbacks must be gone.
        self.assertNotIn(".catch(() => { setTimeout(scheduleRoam", html)
        self.assertNotIn("let the window settle, then start roaming", html)
        # Roam only when Settings explicitly say visible.
        self.assertIn("s.pet_visible === true", html)

    def test_pet_region_rects_build(self):
        rects = winshape.build_pet_region_rects(
            APP_DIR / "ui" / "assets" / "avatar-cat.png")
        self.assertIsNotNone(rects)
        self.assertGreater(len(rects), 10)

    def test_pet_drag_handlers_and_logging(self):
        html = _read("app/ui/pet.html")
        self.assertIn('pet.addEventListener("pointerdown"', html)
        self.assertIn('pet.addEventListener("pointermove"', html)
        self.assertIn('pet.addEventListener("pointerup"', html)
        self.assertIn("Pet drag move", html)
        self.assertIn("Pet drag ended at", html)
        self.assertIn("api().move_pet(", html)


class TestCatFlyAndChat(unittest.TestCase):
    def test_fly_hooks_present(self):
        js = _read("app/ui/app.js")
        self.assertIn("flyHeroToHeader", js)
        self.assertIn("heroFlown", js)
        self.assertIn("--fly-dx", js)
        self.assertIn("flown", js)
        css = _read("app/ui/styles.css")
        self.assertIn(".avatar-frame.flying", css)
        self.assertIn(".avatar-wrap.flown", css)
        self.assertIn("fly-land", css)
        html = _read("app/ui/index.html")
        self.assertIn('id="avatar-frame"', html)
        self.assertIn('class="logo"', html)

    def test_fly_triggered_on_first_message(self):
        js = _read("app/ui/app.js")
        # sendText and voiceChat both fly the hero on the first message.
        self.assertGreater(js.count("flyHeroToHeader()"), 1)


class TestSelfTestAndInstaller(unittest.TestCase):
    def test_ready_markers_on_all_pages(self):
        self.assertIn("READY main", _read("app/ui/app.js"))
        self.assertIn("READY island", _read("app/ui/island.html"))
        self.assertIn("READY pet", _read("app/ui/pet.html"))
        src = _read("app/main.py")
        self.assertIn("_bridge_ready_watchdog", src)
        self.assertIn("def _check_bridge_files", src)
        self.assertIn("def _webview2_version", src)

    def test_installer_refuses_running_instance(self):
        iss = _read("installer/memo-ai.iss")
        self.assertIn("AppMutex=Local\\MemoAI-SingleInstance", iss)
        self.assertIn("CloseApplications=yes", iss)

    def test_chroma_key_stays_deleted(self):
        for rel in ("app/main.py", "app/winshape.py", "app/ui/island.html",
                    "app/ui/pet.html", "app/ui/app.js", "app/ui/styles.css"):
            src = _read(rel)
            self.assertNotIn("#FF00FF", src, rel)
        for rel in ("app/main.py", "app/winshape.py"):
            self.assertNotIn("SetLayeredWindowAttributes(", _read(rel), rel)

    def test_version_consistency(self):
        import main
        self.assertEqual(main.VERSION, "1.3.2")
        self.assertIn("v1.3.2", _read("app/ui/index.html"))
        self.assertIn("1, 3, 2, 0",
                      _read("installer/file_version_info.txt"))
        # version.json is updated BY THE CI after the tag build (it
        # carries the real release hashes) - it must NOT be hand-edited
        # here, so it still says 1.3.1 until the tag build lands.
        manifest = json.loads(_read("version.json"))
        self.assertEqual(manifest["version"], "1.3.1")


class TestLiveGroqToolCall(unittest.TestCase):
    """LIVE test: does Groq actually CHOOSE the tool for 'افتح VS Code'?
    Runs ONLY when GROQ_API_KEY is set in the environment (never in CI
    without a key). This is the one thing mocks cannot prove."""

    @unittest.skipUnless(os.environ.get("GROQ_API_KEY"),
                         "no GROQ_API_KEY - live model choice untested")
    def test_live_groq_open_vscode(self):
        import requests
        key = os.environ["GROQ_API_KEY"]
        system = brain.system_prompt("Memo", "Mohamed", "ar",
                                     tools_available=True)
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={"model": brain.GROQ_MODEL,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user",
                                "content": "ممكن تفتح VS Code؟"}],
                  "tools": tools.TOOL_SCHEMAS, "tool_choice": "auto"},
            timeout=60)
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        self.assertTrue(
            calls, f"Groq answered in text instead of calling a tool: "
                    f"{msg.get('content')!r}")
        self.assertEqual(calls[0]["function"]["name"], "open_app")


if __name__ == "__main__":
    unittest.main()
