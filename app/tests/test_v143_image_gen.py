# Memo AI v1.4.3 - headless tests for generate_image (item 3 only):
#
#   python -m unittest app.tests.test_v143_image_gen -v
#   python -m unittest discover -s app/tests -v   (full suite)
#
# What is proven here:
#   * the tool exists in the dispatcher + TOOL_SCHEMAS, with an
#     English-prompt instruction and the [[IMAGE:...]] marker rule.
#   * a mocked download of REAL image bytes (a PNG built with Pillow
#     in the test) is validated, re-encoded, saved under
#     app/data/images/ (DATA_DIR redirected to a temp dir), and the
#     result carries a [[IMAGE:<filename>]] marker + the tracker
#     (take_generated_images) hands the file to the chat result.
#   * failure is honest: network down / HTTP 500 / non-image reply /
#     empty prompt / over-long prompt -> a clear Arabic error, NO
#     marker, NO file, NO tracker entry (the brain can never claim
#     an image was generated when it was not).
#   * the brain's system prompt (ar + en) teaches image requests
#     (ارسم / ولّد صورة / draw / generate an image) to call
#     generate_image with an English prompt and to echo the marker.
#   * the full tool loop wires through: a fake Groq that calls
#     generate_image gets the tool result with the marker.
#   * the UI contract: app.js renders chat_result().images via the
#     get_generated_image bridge, main.py attaches images + strips
#     the marker, WhatsApp strips the marker (text only).
#
# What is NOT proven here (needs real Windows + live Groq):
#   * the LIVE Pollinations endpoint - covered by
#     test_live_pollinations_endpoint below (one real HTTP GET;
#     skipped cleanly when this machine has no network) and by the
#     manual live check done while building (HTTP 200, real JPEG,
#     512x512 in ~6s, 2026-11-07).
#   * the real Groq model CHOOSING generate_image for an Arabic
#     "ارسم لي..." request, and the picture rendering in WebView2.
#     Field test on Windows covers those.

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

import brain  # noqa: E402
import settings_store  # noqa: E402
import tools  # noqa: E402


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _make_png_bytes(width: int = 200, height: int = 100) -> bytes:
    """Real PNG bytes (red rectangle) built with Pillow."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, status=200, content=b"",
                 content_type="image/png"):
        self.status_code = status
        self.content = content
        self.headers = {"content-type": content_type}


class TestGenerateImageTool(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._patcher = mock.patch.object(settings_store, "DATA_DIR",
                                          self.tmp)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        tools.take_generated_images()  # drain any leftover tracker state

    def test_schema_present_with_english_prompt_rule(self):
        schemas = {s["function"]["name"]: s["function"]
                   for s in tools.TOOL_SCHEMAS}
        self.assertIn("generate_image", schemas)
        fn = schemas["generate_image"]
        self.assertIn("prompt", fn["parameters"]["required"])
        self.assertIn("ENGLISH", fn["description"])
        self.assertIn("[[IMAGE:", fn["description"])

    def test_real_image_bytes_saved_and_marker_returned(self):
        png = _make_png_bytes()
        with mock.patch("requests.get",
                        return_value=_FakeResponse(200, png)):
            out = tools.execute_tool(
                "generate_image", {"prompt": "a small red cat"})
        self.assertIn("[[IMAGE:", out)
        clean, names = tools.extract_image_markers(out)
        self.assertEqual(len(names), 1)
        saved = self.tmp / "images" / names[0]
        self.assertTrue(saved.exists())
        self.assertGreater(saved.stat().st_size, 0)
        from PIL import Image
        with Image.open(saved) as im:
            im.verify()
        self.assertEqual(tools.take_generated_images(), names)
        self.assertEqual(tools.take_generated_images(), [])  # drains

    def test_default_size_and_explicit_portrait(self):
        seen = {}

        def fake_get(url, timeout=None):
            seen["url"] = url
            return _FakeResponse(200, _make_png_bytes())

        with mock.patch("requests.get", side_effect=fake_get):
            tools.execute_tool("generate_image", {"prompt": "a cat"})
            self.assertIn("width=1024&height=1024", seen["url"])
            tools.execute_tool("generate_image",
                               {"prompt": "a cat", "size": "768x1024"})
            self.assertIn("width=768&height=1024", seen["url"])

    def test_failure_network_is_honest_no_marker_no_file(self):
        with mock.patch("requests.get",
                        side_effect=ConnectionError("network down")):
            out = tools.execute_tool("generate_image",
                                     {"prompt": "a cat"})
        self.assertIn("مقدرتش", out)
        self.assertNotIn("[[IMAGE:", out)
        self.assertEqual(tools.take_generated_images(), [])
        self.assertFalse((self.tmp / "images").exists())

    def test_failure_http_500_is_honest(self):
        with mock.patch("requests.get",
                        return_value=_FakeResponse(500, b"oops",
                                                   "text/plain")):
            out = tools.execute_tool("generate_image",
                                     {"prompt": "a cat"})
        self.assertIn("مقدرتش", out)
        self.assertIn("500", out)
        self.assertNotIn("[[IMAGE:", out)

    def test_failure_non_image_reply_is_honest(self):
        with mock.patch("requests.get",
                        return_value=_FakeResponse(
                            200, b"<html>not an image</html> * 100",
                            "text/html")):
            out = tools.execute_tool("generate_image",
                                     {"prompt": "a cat"})
        self.assertIn("مقدرتش", out)
        self.assertNotIn("[[IMAGE:", out)

    def test_failure_garbage_bytes_is_honest(self):
        with mock.patch("requests.get",
                        return_value=_FakeResponse(
                            200, b"x" * 5000, "image/png")):
            out = tools.execute_tool("generate_image",
                                     {"prompt": "a cat"})
        self.assertIn("مقدرتش", out)
        self.assertNotIn("[[IMAGE:", out)

    def test_empty_prompt_never_touches_network(self):
        with mock.patch("requests.get",
                        side_effect=AssertionError("must not be called")):
            out = tools.execute_tool("generate_image", {"prompt": "   "})
        self.assertIn("الوصف فاضي", out)

    def test_overlong_prompt_rejected(self):
        out = tools.execute_tool("generate_image",
                                 {"prompt": "x" * 501})
        self.assertIn("طويل", out)
        self.assertNotIn("[[IMAGE:", out)

    def test_size_parsing(self):
        self.assertEqual(tools._parse_image_size("768x1024"), (768, 1024))
        self.assertEqual(tools._parse_image_size("garbage"), (1024, 1024))
        self.assertEqual(tools._parse_image_size("5000x10"), (1280, 256))
        self.assertEqual(tools._parse_image_size(""), (1024, 1024))

    def test_marker_helpers(self):
        text = "ولّدت الصورة ✅\n[[IMAGE:img-1.jpg]]\nخلصت\n[[IMAGE:img-2.jpg]]"
        clean, names = tools.extract_image_markers(text)
        self.assertEqual(names, ["img-1.jpg", "img-2.jpg"])
        self.assertNotIn("[[IMAGE:", clean)
        self.assertIn("ولّدت الصورة", clean)
        self.assertEqual(tools.extract_image_markers("عادي"),
                         ("عادي", []))


class TestBrainKnowsImageGen(unittest.TestCase):
    def test_system_prompt_ar_teaches_image_tool(self):
        prompt = brain.system_prompt("Memo", "Mohamed", "ar",
                                     tools_available=True)
        self.assertIn("generate_image", prompt)
        self.assertIn("[[IMAGE:", prompt)
        self.assertIn("ارسم", prompt)
        self.assertIn("متولدتش", prompt)

    def test_system_prompt_en_teaches_image_tool(self):
        prompt = brain.system_prompt("Memo", "Mohamed", "en",
                                     tools_available=True)
        self.assertIn("generate_image", prompt)
        self.assertIn("[[IMAGE:", prompt)
        self.assertIn("ENGLISH", prompt)

    def test_capability_block_mentions_image_generation(self):
        self.assertIn("تولّد صور",
                      brain._capability_block("ar"))
        self.assertIn("generate images",
                      brain._capability_block("en"))

    def test_draw_request_looks_like_action(self):
        self.assertTrue(brain._looks_like_action("ارسم لي قطة"))
        self.assertTrue(brain._looks_like_action("ولّد صورة لغروب"))
        self.assertTrue(brain._looks_like_action(
            "generate an image of a cat"))

    def test_brain_tool_loop_calls_generate_image(self):
        script = [
            {"content": "", "tool_calls": [
                {"id": "call_1", "function": {
                    "name": "generate_image",
                    "arguments": json.dumps(
                        {"prompt": "a small red cat on a rug"})}}]},
            {"content": "خلصت، دي القطة اللي طلبتها."},
        ]

        def fake_groq(messages, use_tools=False, timeout=60):
            return script.pop(0)

        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(settings_store, "DATA_DIR", tmp), \
                mock.patch.object(brain, "is_online", return_value=True), \
                mock.patch.object(brain, "_groq_message",
                                  side_effect=fake_groq), \
                mock.patch("requests.get",
                           return_value=_FakeResponse(
                               200, _make_png_bytes())):
            tools.take_generated_images()
            result = brain.ask_with_tools(
                "ارسم لي قطة حمرا صغيرة", "Memo", "Mohamed", "ar")
        self.assertEqual(result["tools_used"], 1)
        self.assertIn("القطة", result["reply"])
        # The tool really ran and saved a file this turn.
        self.assertEqual(len(tools.take_generated_images()), 1)


class TestUiContract(unittest.TestCase):
    """Source-level contract (main.py needs pywebview, so the bridge
    itself is verified by parsing + the field test, not imported here):
    the JS asks the bridge for chat images, main.py attaches them and
    strips the marker, WhatsApp strips the marker (text only)."""

    def test_js_renders_images_from_chat_result(self):
        js = _read("app/ui/app.js")
        self.assertIn("addGeneratedImages", js)
        self.assertIn("get_generated_image", js)
        self.assertIn("done.images", js)
        self.assertIn("gen-image", js)

    def test_main_attaches_images_and_strips_marker(self):
        main = _read("app/main.py")
        self.assertIn("def get_generated_image", main)
        self.assertIn("extract_image_markers", main)
        self.assertIn("take_generated_images", main)
        self.assertIn('"images": entry.get("images", [])', main)
        self.assertIn("DATA_DIR / \"images\"", main)
        # Path traversal is rejected by the bridge.
        self.assertIn('".." in name', main)

    def test_whatsapp_strips_marker(self):
        wa = _read("app/whatsapp.py")
        self.assertIn("extract_image_markers", wa)
        self.assertIn("take_generated_images", wa)

    def test_css_styles_generated_image(self):
        css = _read("app/ui/styles.css")
        self.assertIn(".msg.gen-image img", css)

    def test_images_live_under_app_data(self):
        tools_src = _read("app/tools.py")
        self.assertIn('_IMAGE_DIRNAME = "images"', tools_src)
        self.assertIn("DATA_DIR / _IMAGE_DIRNAME", tools_src)


class TestLivePollinationsEndpoint(unittest.TestCase):
    """ONE real HTTP GET against the chosen free service. Skipped
    cleanly when this machine has no network - the manual live check
    (HTTP 200, real JPEG, 2026-11-07) is the evidence in that case."""

    def test_live_endpoint_returns_real_image(self):
        import requests
        from PIL import Image
        try:
            resp = requests.get(
                "https://image.pollinations.ai/prompt/"
                "a%20small%20red%20cat%20on%20a%20rug"
                "?width=256&height=256&model=flux&nologo=true&seed=7",
                timeout=90)
        except Exception as exc:
            self.skipTest(f"no network to Pollinations: {exc}")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            (resp.headers.get("content-type") or "").startswith("image/"))
        self.assertGreater(len(resp.content or b""), 1024)
        with Image.open(io.BytesIO(resp.content)) as im:
            im.verify()
            self.assertEqual(im.size, (256, 256))


if __name__ == "__main__":
    unittest.main()
