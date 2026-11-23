"""v1.6.4-rc.3 - tightened system prompt (Anthropic Fable 5 habits).

The prompt got the four habits - WHY line, no-auto-fix (assessment
first, act only when told), act-once-enough (recommend instead of
surveying options), evidence-grounded device numbers - while getting
SHORTER, not longer (rc.2 token-efficiency work must not be undone).

How the "did not grow" claim is enforced here: tiktoken is NOT a
runtime dependency, so this test pins the exact character and word
counts of the rc.2 prompt (measured with keys_status() == {} and the
fixed off-state WhatsApp lines below) and asserts the current prompt
stays at or under them in every variant. The real token counts were
measured during development with tiktoken cl100k_base under the same
fixed inputs - ar tools=True 2527 -> 2305, ar tools=False 1814 ->
1743, en tools=True 1067 -> 962, en tools=False 772 -> 754 - so the
length pin below is the CI-enforceable form of the same constraint.
"""

import unittest
from unittest import mock

import brain


# rc.2 baseline prompt sizes (chars / words), measured with
# keys_status() == {} and the fixed WhatsApp lines below.
RC2_BASELINE = {
    ("ar", True): (3680, 650),
    ("ar", False): (2640, 469),
    ("en", True): (4258, 750),
    ("en", False): (2957, 517),
}

WA_AR = ("الواتساب مش مربوط لسه — فعّله من الإعدادات وامسح الـ QR من "
         "واتساب على الرقم التالت (مش الأساسي ومش رقم الشغل)، وبعدها "
         "أقدر أستقبل رسايل واتساب وأرد عليها؛ رسايل الرقم الأساسي "
         "أوامر للابتوب، وأي رقم تاني ردود شات بس.")
WA_EN = ("WhatsApp is not linked yet — enable it in Settings and scan "
         "the QR from WhatsApp on the third number (not the main one, "
         "not the work number); then I can receive and reply to "
         "WhatsApp messages. Messages from the main number are laptop "
         "commands; any other number gets chat replies only.")


class TestPromptGuardrails(unittest.TestCase):
    def _prompt(self, lang, tools):
        with mock.patch.object(brain, "keys_status", return_value={}), \
                mock.patch.object(brain.capabilities,
                                  "whatsapp_status_line",
                                  side_effect=lambda language="ar":
                                  WA_AR if language == "ar" else WA_EN):
            return brain.system_prompt("لونا", "Mohamed", lang,
                                       tools_available=tools,
                                       owner_unlocked=False)

    def test_ar_has_why_and_guardrails(self):
        p = self._prompt("ar", True)
        # 1. WHY: one line about what the user is trying to achieve.
        self.assertIn("هدفك:", p)
        # 2. Negative prompting: assessment first, no auto-fix/send/
        # edit/delete until told (reinforces the green/yellow/red tiers).
        self.assertIn("قدّم التقييم", p)
        self.assertIn("متصلّحش ولا تبعت ولا تعدّل ولا تمسح", p)
        # 3. Act once it has enough: recommend instead of surveying.
        self.assertIn("اقترح واحدة", p)
        # 4. Prove it: device numbers from measured values only.
        self.assertIn("القيم المقاسة", p)
        self.assertIn("ممنوع تخترع أرقام", p)

    def test_en_has_why_and_guardrails(self):
        p = self._prompt("en", True)
        self.assertIn("Your goal:", p)
        self.assertIn("give the assessment", p)
        self.assertIn("do not fix, send, edit, or delete", p)
        self.assertIn("recommend one", p)
        self.assertIn("measured values", p)
        self.assertIn("never invent numbers", p)

    def test_guardrails_present_without_tools_too(self):
        # The simple-chat path (tools_available=False) carries the same
        # guardrails - it is the path simple questions take.
        p_ar = self._prompt("ar", False)
        self.assertIn("هدفك:", p_ar)
        self.assertIn("ممنوع تخترع أرقام", p_ar)
        p_en = self._prompt("en", False)
        self.assertIn("Your goal:", p_en)
        self.assertIn("never invent numbers", p_en)

    def test_prompt_did_not_grow_vs_rc2(self):
        for (lang, tools), (base_chars, base_words) in \
                RC2_BASELINE.items():
            p = self._prompt(lang, tools)
            self.assertLessEqual(
                len(p), base_chars,
                f"{lang} tools={tools}: prompt grew in chars "
                f"({len(p)} > rc.2 {base_chars})")
            self.assertLessEqual(
                len(p.split()), base_words,
                f"{lang} tools={tools}: prompt grew in words "
                f"({len(p.split())} > rc.2 {base_words})")


if __name__ == "__main__":
    unittest.main()
