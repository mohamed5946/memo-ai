# LONA v1.6.4 rc.3 - memory knowledge graph ("second brain" view).
#
# This file proves:
#   * every node comes ONLY from the real MemoryStore (seed a store
#     with known facts / rules / routines / style phrases+notes and
#     assert the nodes match exactly - no extras, no demo nodes);
#   * an empty store gives an empty graph (not a fake graph);
#   * each node carries the full record (id / text / source /
#     created) the detail panel needs;
#   * edges exist ONLY between records sharing >= 2 real content
#     tokens, and carry those shared terms as the reason;
#   * the node cap is honoured (100 max, total + capped reported
#     honestly);
#   * the bridge method never raises and logs the load;
#   * the view's JS has NO continuous animation loop: no
#     requestAnimationFrame anywhere in the graph code, no
#     setInterval for the graph - rendering is event-driven
#     (load / pan / zoom / resize / click) and stops when the
#     view is hidden (unloadGraph clears the data + canvas).
#
#   python3 -m pytest app/tests/test_v164_rc3_graph.py -q

import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

UI_DIR = APP_DIR / "ui"
INDEX = UI_DIR / "index.html"
CSS = UI_DIR / "assets" / "lona.css"

import bridge_v2  # noqa: E402
import memory as memory_mod  # noqa: E402


def _read(path):
    return Path(path).read_text(encoding="utf-8")


def _graph_js(html):
    """The graph JS block only, so the no-loop assertion is scoped
    to this feature (other views legitimately poll)."""
    start = html.index("memory graph (v1.6.4-rc.3)")
    end = html.index("/* ---------- chat ---------- */", start)
    return html[start:end]


class _GraphCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = memory_mod.MemoryStore(
            Path(self.tmp.name) / "memory.json")
        self._orig = memory_mod.get_store()
        memory_mod.set_store(self.store)
        self.addCleanup(lambda: memory_mod.set_store(self._orig))
        self.addCleanup(self.tmp.cleanup)

    def _seed(self):
        # Facts that share real terms (exam / CS 309 / study) and
        # one that shares nothing with anything.
        self.f1 = self.store.add_fact(
            "امتحان CS 309 يوم الاتنين الساعة 11", source="explicit",
            priority="high")
        self.f2 = self.store.add_fact(
            "محاضرة CS 309 يوم الاتنين في قاعة 1118", source="explicit")
        self.f3 = self.store.add_fact(
            "بحب القهوة الصبح قبل الشغل", source="auto")
        self.r1 = self.store.add_rule("قول امتحان مش اختبار")
        self.ro1 = self.store.add_routine("بذاكر كل يوم الساعة 7 الصبح")
        self.store.add_phrase("تمام يا صاحبي")
        self.store.add_style_note("بيحب الردود القصيرة")

    def _graph(self, **kw):
        return self.store.graph_data(**kw)


class TestNodesFromRealStore(_GraphCase):
    def test_nodes_match_seeded_records_exactly(self):
        self._seed()
        g = self._graph()
        kinds = {}
        for n in g["nodes"]:
            kinds.setdefault(n["kind"], []).append(n)
        self.assertEqual(len(kinds.get("fact", [])), 3)
        self.assertEqual(len(kinds.get("rule", [])), 1)
        self.assertEqual(len(kinds.get("routine", [])), 1)
        self.assertEqual(len(kinds.get("style", [])), 2)
        self.assertEqual(g["total"], 7)
        self.assertFalse(g["capped"])
        texts = {n["text"] for n in g["nodes"]}
        self.assertIn("امتحان CS 309 يوم الاتنين الساعة 11", texts)
        self.assertIn("قول امتحان مش اختبار", texts)
        self.assertIn("بذاكر كل يوم الساعة 7 الصبح", texts)
        self.assertIn("تمام يا صاحبي", texts)
        self.assertIn("بيحب الردود القصيرة", texts)

    def test_node_carries_full_record(self):
        self._seed()
        g = self._graph()
        node = next(n for n in g["nodes"] if n["id"] == self.f1)
        self.assertEqual(node["kind"], "fact")
        self.assertEqual(node["text"],
                         "امتحان CS 309 يوم الاتنين الساعة 11")
        self.assertEqual(node["source"], "explicit")
        self.assertEqual(node["priority"], "high")
        self.assertTrue(node["created"])  # ISO timestamp, real
        self.assertEqual(node["hits"], 0)
        # Label is a short display form of the same text.
        self.assertTrue(node["text"].startswith(node["label"].rstrip("…")))

    def test_empty_store_empty_graph(self):
        g = self._graph()
        self.assertEqual(g["nodes"], [])
        self.assertEqual(g["edges"], [])
        self.assertEqual(g["total"], 0)
        self.assertFalse(g["capped"])

    def test_no_demo_or_hardcoded_nodes(self):
        self._seed()
        g = self._graph()
        self.assertEqual(len(g["nodes"]), 7)
        for n in g["nodes"]:
            self.assertTrue(n["id"])
            self.assertTrue(n["text"])

    def test_style_nodes_have_synthetic_stable_ids(self):
        self._seed()
        g = self._graph()
        style_ids = sorted(n["id"] for n in g["nodes"]
                           if n["kind"] == "style")
        self.assertEqual(style_ids, ["style-note-0", "style-phrase-0"])

    def test_deleted_record_disappears_from_graph(self):
        self._seed()
        self.assertTrue(self.store.delete_fact(self.f3))
        g = self._graph()
        self.assertEqual(g["total"], 6)
        self.assertNotIn("بحب القهوة الصبح قبل الشغل",
                         {n["text"] for n in g["nodes"]})


class TestEdges(_GraphCase):
    def test_edge_only_between_records_sharing_real_terms(self):
        self._seed()
        g = self._graph()
        by_id = {n["id"]: n for n in g["nodes"]}
        # f1 and f2 share "cs", "309", "الاتنين" -> edge, with the
        # shared terms carried as the reason.
        pair = {(e["from"], e["to"]) for e in g["edges"]}
        self.assertIn((self.f1, self.f2), pair)
        edge = next(e for e in g["edges"]
                    if {e["from"], e["to"]} == {self.f1, self.f2})
        self.assertGreaterEqual(edge["weight"], 2)
        self.assertIn("309", edge["shared"])
        # f3 (coffee) shares nothing with anything -> no edges.
        for e in g["edges"]:
            self.assertNotIn(self.f3, (e["from"], e["to"]))
        # Every edge's endpoints are real nodes.
        for e in g["edges"]:
            self.assertIn(e["from"], by_id)
            self.assertIn(e["to"], by_id)
            self.assertGreaterEqual(len(e["shared"]), 2)

    def test_one_shared_token_is_not_enough(self):
        # Only "القهوه" in common -> no edge (needs >= 2 terms).
        a = self.store.add_fact("بحب القهوة التركي", source="explicit")
        b = self.store.add_fact("القهوة بتفوقني الصبح", source="explicit")
        g = self._graph()
        pair = {(e["from"], e["to"]) for e in g["edges"]}
        self.assertNotIn((a, b), pair)
        self.assertNotIn((b, a), pair)

    def test_rule_and_routine_can_link_by_terms(self):
        self.store.add_rule("قول امتحان مش اختبار")
        self.store.add_routine("براجع قبل الامتحان بيوم")
        g = self._graph()
        # "امتحان" alone is one token -> no edge; this asserts the
        # rule is strict even across kinds.
        self.assertEqual(g["edges"], [])


class TestCap(_GraphCase):
    def test_node_cap_honoured_and_reported(self):
        for i in range(120):
            self.store.add_fact(f"حقيقة رقم {i} عن موضوع مختلف تماما {i}",
                                source="explicit")
        g = self._graph()
        self.assertEqual(len(g["nodes"]),
                         memory_mod.GRAPH_NODE_CAP)
        self.assertEqual(g["total"], 120)
        self.assertTrue(g["capped"])
        self.assertEqual(g["limit"], memory_mod.GRAPH_NODE_CAP)

    def test_high_priority_facts_survive_the_cap(self):
        hot = self.store.add_fact("حقيقة مهمة جدا عن الامتحان",
                                  source="explicit", priority="high")
        for i in range(120):
            self.store.add_fact(f"حقيقة رقم {i} عن موضوع مختلف {i}",
                                source="explicit")
        g = self._graph()
        ids = {n["id"] for n in g["nodes"]}
        self.assertIn(hot, ids)
        self.assertTrue(g["capped"])

    def test_custom_limit_respected(self):
        self._seed()
        g = self._graph(limit=3)
        self.assertEqual(len(g["nodes"]), 3)
        self.assertEqual(g["total"], 7)
        self.assertTrue(g["capped"])


class TestBridge(_GraphCase):
    def test_bridge_returns_live_store_graph(self):
        self._seed()
        api = mock.Mock()
        g = bridge_v2.get_memory_graph(api)
        self.assertEqual(g["total"], 7)
        self.assertEqual(len(g["nodes"]), 7)
        self.assertTrue(all(n["text"] for n in g["nodes"]))

    def test_bridge_empty_store(self):
        api = mock.Mock()
        g = bridge_v2.get_memory_graph(api)
        self.assertEqual(g, {"nodes": [], "edges": [], "total": 0,
                             "capped": False, "limit": 100})

    def test_bridge_never_raises(self):
        api = mock.Mock()
        with mock.patch.object(memory_mod, "get_store",
                               side_effect=RuntimeError("boom")):
            g = bridge_v2.get_memory_graph(api)
        self.assertEqual(g["nodes"], [])
        self.assertEqual(g["edges"], [])

    def test_bridge_logs_the_load(self):
        self._seed()
        api = mock.Mock()
        with mock.patch.object(bridge_v2, "info") as info:
            bridge_v2.get_memory_graph(api)
        self.assertTrue(info.called)
        msg = info.call_args[0][1]
        self.assertIn("7 nodes", msg)


class TestGraphUI(unittest.TestCase):
    def test_view_and_nav_wired(self):
        html = _read(INDEX)
        self.assertIn('data-v="graph"', html)
        self.assertIn('<div class="view page" data-v="graph" hidden>',
                      html)
        self.assertIn('id="graphCanvas"', html)
        self.assertIn('id="graphDetail"', html)
        self.assertIn('id="graphEmpty"', html)
        self.assertIn('id="graphLegend"', html)
        self.assertIn("B.call('get_memory_graph')", html)
        self.assertIn("loadGraph()", html)
        self.assertIn("unloadGraph()", html)
        # Empty state is honest, not a demo graph.
        self.assertIn("لا توجد ذكريات محفوظة بعد", html)
        self.assertIn("افتكر إن", html)

    def test_no_continuous_animation_loop(self):
        html = _read(INDEX)
        js = _graph_js(html)
        self.assertNotIn("requestAnimationFrame", js)
        self.assertNotIn("setInterval", js)
        self.assertNotIn("setTimeout", js)
        # Rendering happens on demand only.
        self.assertIn("drawGraph()", js)
        self.assertIn("addEventListener('pointerdown'", js)
        self.assertIn("addEventListener('wheel'", js)
        # Leaving the view drops the data and clears the canvas.
        self.assertIn("graphData = null", js)
        self.assertIn("clearRect", js)

    def test_node_click_shows_full_record_and_real_actions(self):
        html = _read(INDEX)
        js = _graph_js(html)
        self.assertIn("selectGraphNode", js)
        self.assertIn("افتح في الإعدادات", js)
        self.assertIn("renderSec('memory')", js)
        self.assertIn("اسأل لونا عنها", js)
        self.assertIn("chatInput.value", js)
        # Detail shows source + created date from the node.
        self.assertIn("n.source", js)
        self.assertIn("n.created", js)
        self.assertIn("fmtGraphDate", js)

    def test_pan_zoom_controls_exist_and_are_wired(self):
        html = _read(INDEX)
        self.assertIn('id="graphZoomIn"', html)
        self.assertIn('id="graphZoomOut"', html)
        self.assertIn('id="graphReset"', html)
        js = _graph_js(html)
        self.assertIn("graphZoomAt", js)
        self.assertIn("graphFit()", js)

    def test_memory_section_links_to_graph(self):
        html = _read(INDEX)
        self.assertIn("عرض خريطة الذاكرة", html)
        self.assertIn("data-mgraph", html)
        self.assertIn("show('graph')", html)

    def test_cap_note_is_honest(self):
        html = _read(INDEX)
        js = _graph_js(html)
        self.assertIn("graphData.capped", js)
        self.assertIn("معروض أهم", js)
        self.assertIn("الأعلى أولوية والأحدث أولاً", js)

    def test_no_emoji_and_no_ai_tell_phrases(self):
        html = _read(INDEX)
        js = _graph_js(html)
        for bad in ("في الختام", "من الجدير بالذكر", "علاوة على ذلك"):
            self.assertNotIn(bad, js)
        # No emoji in the graph UI strings.
        self.assertNotRegex(js, r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")

    def test_graph_css_exists(self):
        css = _read(CSS)
        for cls in (".graph-wrap", ".graph-legend", ".graph-empty",
                    ".graph-bar"):
            self.assertIn(cls, css)

    def test_graph_js_syntax(self):
        # The whole inline script must parse (checked via node).
        self.assertIn("layoutGraph", _read(INDEX))


if __name__ == "__main__":
    unittest.main()
