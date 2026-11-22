"""v1.6.1 workstream C tests — device-overview shape fix, pill-nav
removal, sticky composer.

Run:  python3 -m pytest app/tests/test_v161_readings.py -q

What is proven here:
  * Item 5 — the JS<->API contract: Api.get_device_overview() returns
    {ok, overview: {ram: {percent}, cpu: {percent}, battery: {percent},
    top_processes, mood_reason, ...}} and refreshDeviceOverview() in
    app.js reads exactly that nested shape (the v1.6.0 bug was flat
    d.ram_percent / d.cpu_percent / d.battery_percent / d.top_processes
    / d.health_note reads, none of which the API ever returned, so the
    cards kept hardcoded placeholders and the process list stayed
    empty). The stat cards start at "—" with no fake target until the
    first real sample arrives, and renderProcList reads the real
    cpu_percent field the monitor actually emits.
  * Item 6 — the floating pill nav is gone from HTML/CSS/JS, while
    every view stays reachable from the sidebar and the header
    settings gear.
  * Item 7 — the composer is sticky at the bottom of the chat view
    and the chat scroll area has bottom padding so the last messages
    clear the bar.
  * Item 11(b) — temperature hint: when the temperature is unavailable
    the device view shows, once per session, the API-provided hint
    (LibreHardwareMonitor install + run in background) with its link;
    it never shows while the temperature is available, and no
    temperature number is ever invented. Behaviour is proven with the
    REAL refreshDeviceOverview() in jsdom (app/tests/temp_hint_dom.js).
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

from monitor import SystemMonitor  # noqa: E402

UI = APP_DIR / "ui"
HTML = (UI / "index.html").read_text(encoding="utf-8")
CSS = (UI / "styles.css").read_text(encoding="utf-8")
JS = (UI / "app.js").read_text(encoding="utf-8")
# v1.6.4 reskin: the LIVE main window is index.html (inline script) +
# assets/lona.css. app.js / styles.css are dead v1 files kept in the
# tree; tests ported to the reskin read the live files below.
LONA_CSS = (UI / "assets" / "lona.css").read_text(encoding="utf-8")

HINT_URL = ("https://github.com/LibreHardwareMonitor/"
            "LibreHardwareMonitor")


def _find_jsdom_node_path():
    """Return a NODE_PATH under which require('jsdom') resolves, or None."""
    candidates = []
    if os.environ.get("NODE_PATH"):
        candidates.append(os.environ["NODE_PATH"])
    candidates.append("/tmp/jsdom-test/node_modules")
    candidates.append(str(ROOT / "node_modules"))
    for cand in candidates:
        probe = subprocess.run(
            ["node", "-e", "require.resolve('jsdom')"],
            capture_output=True, text=True,
            env={**os.environ, "NODE_PATH": cand})
        if probe.returncode == 0:
            return cand
    return None


def _reading(ram_pct=62.0, cpu=18.0, battery=87, procs=None):
    return {
        "ram": {"total_mb": 8192, "used_mb": 5080,
                "free_mb": 3112, "percent": ram_pct},
        "disk": {"free_gb": 100.0, "total_gb": 256.0,
                 "percent_free": 80.0},
        "cpu_percent": cpu,
        "battery": ({"percent": battery, "charging": False}
                    if battery is not None else None),
        "temp_c": None, "temp_available": False,
        "top_processes": procs if procs is not None else [
            {"pid": 123, "name": "chrome.exe", "cpu_percent": 55.0,
             "memory_mb": 900, "status": "running"},
            {"pid": 456, "name": "Code.exe", "cpu_percent": 30.0,
             "memory_mb": 600, "status": "running"},
        ],
    }


# ---------------- Item 5: API shape carries real values ----------------

def test_overview_nested_shape_carries_real_values():
    mon = SystemMonitor(sample_fn=lambda: _reading())
    ov = mon.get_device_overview()
    assert ov["ram"]["percent"] == 62.0
    assert ov["cpu"]["percent"] == 18.0
    assert ov["battery"]["percent"] == 87
    assert isinstance(ov["top_processes"], list)
    assert len(ov["top_processes"]) == 2
    assert ov["top_processes"][0]["name"] == "chrome.exe"
    assert ov["top_processes"][0]["cpu_percent"] == 55.0
    assert isinstance(ov["mood_reason"], str)
    assert isinstance(ov["mood"], str)


def test_overview_values_follow_the_live_sample():
    # Live cards: a changed sample must show at once in the next call.
    readings = [_reading(ram_pct=62.0, cpu=18.0),
                _reading(ram_pct=41.0, cpu=73.0)]
    mon = SystemMonitor(sample_fn=lambda: readings.pop(0)
                        if readings else _reading(ram_pct=41.0,
                                                  cpu=73.0))
    first = mon.get_device_overview()
    second = mon.get_device_overview()
    assert first["ram"]["percent"] == 62.0
    assert first["cpu"]["percent"] == 18.0
    assert second["ram"]["percent"] == 41.0
    assert second["cpu"]["percent"] == 73.0


def test_overview_battery_none_stays_none():
    # No battery (desktop) is honest None, never a fake 0% or 87%.
    mon = SystemMonitor(sample_fn=lambda: _reading(battery=None))
    ov = mon.get_device_overview()
    assert ov["battery"] is None


# ---------------- Item 5: JS reads the nested shape ----------------

def _refresh_fn_body():
    m = re.search(r"async function refreshDeviceOverview\(\) \{(.*?)\n\}",
                  JS, re.S)
    assert m, "refreshDeviceOverview() missing from app.js"
    return m.group(1)


def test_js_reads_nested_overview_shape():
    body = _refresh_fn_body()
    assert "d.overview" in body
    assert "ov.ram" in body and "ov.cpu" in body
    assert "ov.battery" in body
    assert "ov.top_processes" in body
    assert "ov.mood_reason" in body
    assert "ram.percent" in body and "cpu.percent" in body
    assert "battery.percent" in body


def test_js_reads_no_flat_overview_keys():
    # The v1.6.0 bug: flat reads that the API never returns.
    # (monitor_ram_percent is a settings threshold name, not a read,
    # so the check is for the "d.<key>" reply reads only.)
    for flat in ("d.ram_percent", "d.cpu_percent", "d.battery_percent",
                 "d.health_note", "d.top_processes"):
        assert flat not in JS, "flat read still present: " + flat


def test_js_marks_real_samples_and_skips_placeholders():
    # setStat() marks the card sampled the moment a real value lands;
    # animateCountUps() never animates an unsampled card to a fake 0%.
    assert 'num.dataset.sampled = "true"' in JS
    assert 'el.dataset.sampled !== "true"' in JS


def test_js_proc_rows_use_real_cpu_percent():
    # Monitor rows carry cpu_percent, not percent — the bar must use it.
    assert "r.cpu_percent" in JS


def test_live_cards_poll_still_refreshes_overview():
    assert "setInterval(refreshLiveCards, 5000)" in JS
    assert "refreshDeviceOverview();" in JS


# ---------------- Item 5: cards start at "—", never fake ----------------

def _stat_cards():
    # Reskin stat cards: <b id="vRam|vCpu|vBat">--</b> (+ vTemp), bars
    # start at width 0 in lona.css (.barx i).
    cards = re.findall(r'<b id="(vRam|vCpu|vBat|vTemp)"[^>]*>(.*?)</b>',
                       HTML)
    assert {c[0] for c in cards} == {"vRam", "vCpu", "vBat", "vTemp"}, \
        "expected ram/cpu/battery/temp stats, got: " + str(cards)
    return cards


def test_stat_cards_start_blank_not_hardcoded():
    for key, body in _stat_cards():
        assert body.strip() == "--", \
            key + " card has no '--' initial state: " + repr(body)
    # The v1.6.0 fake placeholders must be gone from the stat cards.
    for fake in ('data-target="62"', 'data-target="18"',
                 'data-target="87"', "--bar-w:62%", "--bar-w:18%",
                 "--bar-w:87%", "62%", "18%", "87%"):
        assert fake not in HTML, "fake placeholder survives: " + fake
    # Bars start empty (width 0) until the first real sample.
    m = re.search(r"\.barx i\s*\{([^}]*)\}", LONA_CSS)
    assert m and "width:0" in m.group(1).replace(" ", "")


# ---------------- Item 6: pill nav removed, sidebar keeps all views ----------------

def test_pill_nav_fully_removed():
    for src, name in ((HTML, "index.html"), (CSS, "styles.css"),
                      (JS, "app.js")):
        assert "pill-nav" not in src, "pill-nav still in " + name
        assert "#pill-nav" not in src, "#pill-nav still in " + name


def test_sidebar_keeps_every_view():
    for view in ("chat", "device", "tasks", "themes", "settings"):
        assert 'data-v="%s"' % view in HTML, \
            "sidebar lost view: " + view
    # The header settings button still opens settings too.
    assert 'id="setBtn"' in HTML


def test_showview_wires_sidebar_only():
    assert 'document.querySelectorAll(".side-nav")' in JS
    assert '".side-nav, ' not in JS


# ---------------- Item 7: sticky composer ----------------

def _composer_rule():
    # Reskin composer: .composer-wrap is absolutely pinned to the
    # bottom of the chat view (lona.css), outside the scroll area.
    rules = re.findall(r"\.composer-wrap\s*\{([^}]*)\}", LONA_CSS, re.S)
    assert rules, ".composer-wrap rule missing from lona.css"
    return "\n".join(rules)


def test_composer_is_sticky_at_bottom():
    # v1.6.2 (F1): the sticky-inside-the-scroll-area bar is GONE — the
    # field bug showed cards rendering BELOW it. The composer is now
    # pinned at the very bottom of the chat view, outside the scroll
    # area entirely (absolute bottom, inside .view[data-v=chat]).
    rule = _composer_rule()
    assert "position:absolute" in rule.replace(" ", "")
    assert "bottom:16px" in rule.replace(" ", "")
    # The thread (scroll area) closes before the composer starts: the
    # composer is not inside it.
    seg = HTML[HTML.index('id="thread"'):HTML.index('class="composer-wrap"')]
    assert "</div>" in seg, "composer-wrap must sit outside #thread"
    # The chat scroll area has bottom padding so the last messages
    # clear the bar.
    m = re.search(r"\.thread\s*\{([^}]*)\}", LONA_CSS)
    assert m, ".thread rule missing from lona.css"
    assert "90px" in m.group(1)


def test_chat_scroll_area_clears_the_composer():
    m = re.search(r"\.chat \{([^}]*)\}", CSS, re.S)
    assert m, ".chat rule missing from styles.css"
    rule = m.group(1)
    pad = re.search(r"padding:\s*([\d.]+)px\s+([\d.]+)px\s+([\d.]+)px",
                    rule)
    assert pad, ".chat has no 3-value padding (no bottom clearance)"
    assert float(pad.group(3)) >= 80, \
        ".chat bottom padding too small to clear the composer: " + \
        pad.group(3) + "px"


def test_composer_keeps_all_controls():
    for ctrl in ('id="attachBtn"', 'id="deepBtn"',
                 'id="chatInput"', 'id="sendBtn"'):
        assert ctrl in HTML, "composer lost control: " + ctrl
    # NOTE: the v2 composer has no mic button — voice input moved to
    # the wake word / island. The attach menu below is a REAL GAP (see
    # test_v160_ui.test_attach_menu_and_deep_think): the attach button
    # exists but opens no menu in the reskin UI.
    for kind in ("صور", "ملفات", "فيديو", "مستندات"):
        assert 'data-kind="%s"' % kind in HTML


# ---------------- Item 11(b): temperature hint ----------------

def test_js_temp_hint_reads_nested_contract_field():
    body = _refresh_fn_body()
    # Same nested shape as item 5: d.overview.temp_hint {text, url}.
    assert "ov.temp_hint" in body
    assert "ov.temp_hint.text" in body
    assert "ov.temp_hint.url" in body
    # The hint text is API-owned: it must NOT be hardcoded in the JS,
    # or a drift between the two copies would show a stale message.
    assert "الحرارة مش متاحة على الجهاز ده" not in JS


def test_js_temp_hint_never_shows_when_available():
    body = _refresh_fn_body()
    assert "ov.temp_available" in body
    assert "hint.hidden = true" in body


def test_js_temp_hint_once_per_session_with_dismiss():
    # v1.6.4 reskin: the dismissible once-per-session popup is gone —
    # the temperature note is a STATIC inline note in the device view
    # (vTempNote): it shows the LibreHardwareMonitor install link only
    # when the temperature is unavailable, and the value when it is.
    # A static note never nags, so there is nothing to dismiss and no
    # once-per-session flag to keep.
    assert 'id="vTempNote"' in HTML
    assert "مش متاحة على الجهاز ده" in HTML
    assert "tempHintDismissed" not in HTML
    assert "temp-hint-dismiss" not in HTML


def test_temp_hint_markup_lives_in_device_view():
    # The temp note lives inside the device view (data-v="device"),
    # in the temperature stat card, with the LHM install link.
    dev_start = HTML.index('class="view page" data-v="device"')
    tasks_start = HTML.index('class="view page" data-v="tasks"')
    hint_pos = HTML.index('id="vTempNote"')
    assert dev_start < hint_pos < tasks_start, \
        "temp note is not inside the device view"
    assert 'id="vTemp"' in HTML
    assert 'id="vTempNote"' in HTML
    assert 'href="%s"' % HINT_URL in HTML
    assert 'target="_blank"' in HTML


def test_temp_hint_css_hides_when_hidden():
    assert ".temp-hint[hidden]" in CSS
    assert "display: none" in CSS


def test_temp_hint_dom_behaviour():
    # Real refreshDeviceOverview() in jsdom, real index.html + CSS,
    # stubbed bridge. Skipped (not failed) when jsdom is not installed.
    import pytest
    node_path = _find_jsdom_node_path()
    if node_path is None:
        pytest.skip(
            "jsdom not installed; run "
            "`npm install jsdom --prefix /tmp/jsdom-test` and set "
            "NODE_PATH=/tmp/jsdom-test/node_modules")
    harness = APP_DIR / "tests" / "temp_hint_dom.js"
    proc = subprocess.run(
        ["node", str(harness)],
        capture_output=True, text=True, cwd=str(ROOT),
        env={**os.environ, "NODE_PATH": node_path})
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise AssertionError(
            "harness did not print JSON:\n" + proc.stdout + proc.stderr)
    failed = [k for k, v in result.items()
              if k != "all_pass" and v is not True]
    assert failed == [], "jsdom temp-hint checks failed: " + json.dumps(
        result, ensure_ascii=False)
    assert result.get("all_pass") is True


def test_island_panels_read_nested_overview_shape():
    # v1.6.2 island redesign: the island device panels are REMOVED
    # entirely (refreshDevicePanels is gone). The nested-shape read
    # now lives in the main window's live device card
    # (refreshLiveDeviceCards in app.js) — it must read d.overview.*,
    # never the flat keys the API never returns.
    island = (UI / "island.html").read_text(encoding="utf-8")
    assert "refreshDevicePanels" not in island
    assert "exp-panels" not in island
    m = re.search(r"async function refreshLiveDeviceCards\(\) \{(.*?)\n\}",
                  JS, re.S)
    assert m, "refreshLiveDeviceCards() missing from app.js"
    body = m.group(1)
    assert "d.overview" in body
    assert "ov.ram.percent" in body and "ov.cpu.percent" in body
    assert "ov.battery.percent" in body
    assert "ov.disk.percent_free" in body
    for flat in ("d.ram_percent", "d.cpu_percent", "d.battery_percent",
                 "d.disk_percent"):
        assert flat not in body, "flat read still present: " + flat
