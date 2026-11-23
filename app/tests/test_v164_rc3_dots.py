"""v1.6.4-rc.3 tests - mic/camera in-use dots hidden by default (Item 5).

Run:  python3 -m pytest app/tests/test_v164_rc3_dots.py -q

What is proven here:
  * Item 5 - the .mdots element is HIDDEN by default and appears ONLY
    when the backend reports mic/camera in use. Root cause (caught in
    a headless render of the real island.html): the `hidden` attribute
    IS set on #mediaDots, but the CSS rule `.mdots{display:flex}`
    beats the UA's `[hidden]{display:none}`, so the attribute had no
    effect and the dots rendered in the collapsed pill even when
    nothing used the mic/camera - a fake in-use indicator.
  * The fix is a `.mdots[hidden]{display:none}` rule in island.html,
    which outranks `.mdots` (class + attribute beats class alone),
    so the dots stay hidden while `hidden` is set - and the show path
    (JS sets dots.hidden=false, then toggles each dot's display from
    the backend result) keeps working, because removing the attribute
    restores the flex layout.
  * The markup still carries the hidden attribute on #mediaDots, and
    the show path is still present and unchanged.

No test here renders the page: the headless-render verification (both
states, dots hidden by default / dots visible after the show path)
lives outside the suite, in the rc.3 delivery notes, with screenshots
under ~/workspace/.tmp/v1642-verify/ (dots-fixed-hidden.png,
dots-fixed-shown.png).
"""

import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
UI = APP_DIR / "ui"
ISLAND = (UI / "island.html").read_text(encoding="utf-8")


def test_mdots_hidden_rule_present():
    """The [hidden] override exists and hides the dots (display:none)."""
    m = re.search(r"\.mdots\[hidden\]\s*\{\s*display\s*:\s*none\s*;?\s*\}", ISLAND)
    assert m, ".mdots[hidden]{display:none} rule missing from island.html"


def test_mdots_hidden_rule_comes_after_flex_rule():
    """The override is declared after .mdots{display:flex} so it wins
    on equal specificity too (it also outranks it: class+attribute)."""
    flex = ISLAND.index(".mdots{display:flex")
    hidden = ISLAND.index(".mdots[hidden]")
    assert hidden > flex, ".mdots[hidden] must come after .mdots{display:flex}"


def test_media_dots_markup_still_hidden_by_default():
    """#mediaDots still carries the hidden attribute in the markup."""
    m = re.search(
        r'<span[^>]*class="mdots"[^>]*id="mediaDots"[^>]*hidden[^>]*>',
        ISLAND,
    ) or re.search(
        r'<span[^>]*id="mediaDots"[^>]*hidden[^>]*>',
        ISLAND,
    )
    assert m, "#mediaDots is missing its hidden attribute"


def test_show_path_intact():
    """The backend show path is untouched: on island_media_indicators
    it clears dots.hidden and toggles each dot from the backend result."""
    assert "island_media_indicators" in ISLAND
    assert "dots.hidden = false" in ISLAND
    assert "r.mic_in_use" in ISLAND
    assert "r.camera_in_use" in ISLAND
    assert "querySelector('.mic')" in ISLAND
    assert "querySelector('.cam')" in ISLAND


def test_no_other_hidden_override():
    """No blanket rule re-shows hidden elements (e.g. a [hidden] rule
    with display:flex/block would undo the fix elsewhere)."""
    for m in re.finditer(r"\[hidden\]\s*\{([^}]*)\}", ISLAND):
        body = m.group(1)
        assert "display:none" in body.replace(" ", ""), (
            "a [hidden] rule without display:none found: " + m.group(0)
        )
