"""PHASE 31 — tests for the LangGraph decomposition workflow. No provider calls (all mocked)."""
import json, os, unittest
from unittest import mock

os.environ.setdefault("TENANTS_TABLE", "t"); os.environ.setdefault("USAGE_TABLE", "u")
os.environ.setdefault("AWS_REGION", "ap-south-1")

import decomp_graph as D
import nvidia_provider as nv
import nvidia_harness as H
import app as prod


class Hit:
    def __init__(self, i, score=0.9, post=None, text=None):
        self.score = score
        self.payload = {"post_id": post or f"p{i}", "title": f"T{i}",
                        "chunk_text": text or f"body {i} " * 10,
                        "tenant_id": f"tenant_{i}", "header_path": "H"}


def hits(n, start=0, post=None):
    return [Hit(i, 0.9 - i*0.01, post=post) for i in range(start, start + n)]


class AnalyzerParsingTests(unittest.TestCase):
    def test_parses_compound(self):
        d = D.parse_analysis('{"is_compound": true, "subquestions": ["A?", "B?"]}')
        self.assertTrue(d["is_compound"]); self.assertEqual(d["subquestions"], ["A?", "B?"])

    def test_parses_simple(self):
        d = D.parse_analysis('{"is_compound": false, "subquestions": []}')
        self.assertFalse(d["is_compound"]); self.assertEqual(d["subquestions"], [])

    def test_compound_with_one_subquestion_degrades_to_simple(self):
        d = D.parse_analysis('{"is_compound": true, "subquestions": ["only one"]}')
        self.assertFalse(d["is_compound"])

    def test_caps_at_three_subquestions(self):
        d = D.parse_analysis('{"is_compound": true, "subquestions": ["a","b","c","d","e"]}')
        self.assertEqual(len(d["subquestions"]), 3)

    def test_json_in_prose(self):
        self.assertTrue(D.parse_analysis('sure:\n{"is_compound": true, "subquestions":["a","b"]}\n')["is_compound"])

    def test_rejects_non_json(self):
        with self.assertRaises(ValueError): D.parse_analysis("it is compound")


class RoutingTests(unittest.TestCase):
    def test_simple_route(self):
        self.assertEqual(D.route_question({"is_compound": False, "subquestions": []}), "simple")

    def test_compound_route(self):
        self.assertEqual(D.route_question({"is_compound": True, "subquestions": ["a","b"]}), "compound")

    def test_compound_flag_without_subquestions_is_simple(self):
        self.assertEqual(D.route_question({"is_compound": True, "subquestions": []}), "simple")


class NoLeakageTests(unittest.TestCase):
    """The analyzer must never see the reference answer or the dataset label."""
    def test_analyzer_prompt_contains_only_the_question(self):
        seen = {}
        def fake(model, msgs, **k):
            seen["sys"], seen["user"] = msgs[0]["content"], msgs[1]["content"]
            return {"content": '{"is_compound": false, "subquestions": []}', "input_tokens": 1,
                    "output_tokens": 1, "latency_ms": 1, "retry_count": 0, "rate_limited": False}
        with mock.patch.object(nv, "chat", side_effect=fake), \
             mock.patch.object(D, "_scope", return_value=(["t1"], "t1")):
            D.analyze_question({"original_question": "What is X?", "route": "single",
                                "target": "Someone", "case_id": "case-999",
                                "expected_answer": "SECRET REFERENCE", "scenario": "compound multi-part"})
        blob = seen["sys"] + seen["user"]
        self.assertIn("What is X?", blob)
        self.assertNotIn("SECRET REFERENCE", blob)
        self.assertNotIn("compound multi-part", blob)
        self.assertNotIn("case-999", blob)


class FanOutTests(unittest.TestCase):
    def _sends(self, n):
        st = {"case_id": "c", "route": "multi", "scope_tenant_ids": ["t1", "t2"],
              "scope_single_tenant": None, "subquestions": [f"Q{i}" for i in range(n)]}
        return D.fan_out(st)

    def test_two_branch_fan_out(self):
        s = self._sends(2)
        self.assertEqual(len(s), 2)
        self.assertTrue(all(x.node == "retrieve_branch" for x in s))

    def test_three_branch_fan_out(self):
        self.assertEqual(len(self._sends(3)), 3)

    def test_scope_preserved_in_every_branch(self):
        for x in self._sends(3):
            self.assertEqual(x.arg["scope_tenant_ids"], ["t1", "t2"], "tenant scope must not be widened")
            self.assertEqual(x.arg["route"], "multi")


class BranchRetrievalTests(unittest.TestCase):
    def test_branch_uses_existing_hybrid_retrieval_and_scope(self):
        calls = {}
        def fake_multi(q, tids, **k):
            calls["q"], calls["tids"] = q, tids
            return hits(5), 0.6
        with mock.patch.object(prod, "_hybrid_search_multi", side_effect=fake_multi):
            out = D.retrieve_branch({"branch": 0, "subquestion": "Q1", "route": "group",
                                     "scope_tenant_ids": ["t1", "t2"], "scope_single_tenant": None,
                                     "case_id": "c"})
        self.assertEqual(calls["q"], "Q1")
        self.assertEqual(calls["tids"], ["t1", "t2"])
        self.assertEqual(len(out["branch_results"]), 1)
        self.assertEqual(out["branch_results"][0]["candidate_count"], 5)

    def test_branch_below_floor_marks_evidence_missing(self):
        with mock.patch.object(prod, "_hybrid_search_multi", return_value=(hits(5), 0.01)):
            out = D.retrieve_branch({"branch": 1, "subquestion": "Q2", "route": "group",
                                     "scope_tenant_ids": ["t1"], "scope_single_tenant": None, "case_id": "c"})
        b = out["branch_results"][0]
        self.assertTrue(b["evidence_missing"]); self.assertEqual(b["eligible"], [])

    def test_branch_error_is_captured_not_raised(self):
        with mock.patch.object(prod, "_hybrid_search_multi", side_effect=RuntimeError("qdrant down")):
            out = D.retrieve_branch({"branch": 0, "subquestion": "Q", "route": "group",
                                     "scope_tenant_ids": ["t"], "scope_single_tenant": None, "case_id": "c"})
        self.assertTrue(out["branch_results"][0]["evidence_missing"])
        self.assertIn("branch0:RuntimeError", out["errors"])


class MergeTests(unittest.TestCase):
    def _state(self, pools):
        return {"branch_results": [{"branch": i, "subquestion": f"Q{i}", "eligible": p,
                                    "evidence_missing": not p, "candidate_count": len(p)}
                                   for i, p in enumerate(pools)]}

    def test_final_context_never_exceeds_cap(self):
        out = D.merge_evidence(self._state([hits(5, 0), hits(5, 10), hits(5, 20)]))
        self.assertEqual(len(out["merged_context"]), prod.MAX_LLM_CONTEXT_CHUNKS)

    def test_every_branch_gets_at_least_one_chunk_when_available(self):
        out = D.merge_evidence(self._state([hits(5, 0), hits(5, 10), hits(5, 20)]))
        idx = {m["subquestion_index"] for m in out["merged_context_map"]}
        self.assertEqual(idx, {0, 1, 2}, "coverage: each branch must contribute")

    def test_round_robin_after_coverage_pass(self):
        out = D.merge_evidence(self._state([hits(5, 0), hits(5, 10)]))
        seq = [m["subquestion_index"] for m in out["merged_context_map"]]
        self.assertEqual(seq[:2], [0, 1], "first pass takes best of each branch")
        self.assertEqual(seq[2:4], [0, 1], "then round-robin")

    def test_dedupes_identical_chunks_across_branches(self):
        shared = hits(3, 0)
        out = D.merge_evidence(self._state([shared, list(shared)]))
        keys = [(c.payload["post_id"], c.payload["chunk_text"][:80]) for c in out["merged_context"]]
        self.assertEqual(len(keys), len(set(keys)), "no duplicate chunk may reach the prompt")

    def test_branch_with_no_evidence_is_flagged_not_backfilled_with_unrelated(self):
        out = D.merge_evidence(self._state([hits(5, 0), []]))
        self.assertTrue(out["branch_evidence_missing"])
        self.assertEqual({m["subquestion_index"] for m in out["merged_context_map"]}, {0})

    def test_fewer_than_cap_is_not_padded(self):
        out = D.merge_evidence(self._state([hits(1, 0), hits(1, 10)]))
        self.assertEqual(len(out["merged_context"]), 2)


class CitationConsistencyTests(unittest.TestCase):
    def test_citations_only_from_merged_context(self):
        ctx = hits(3)
        st = {"route": "single", "merged_context": ctx, "original_question": "q",
              "subquestions": ["a", "b"], "merged_context_map": [{"subquestion_index": 0}]*3,
              "scope_single_tenant": "t1"}
        with mock.patch.object(nv, "chat", return_value={"content": "ans", "input_tokens": 5,
                "output_tokens": 5, "latency_ms": 9, "retry_count": 0, "rate_limited": False}):
            out = D.final_answer(st)
        self.assertTrue(set(out["citations"]) <= {c.payload["title"] for c in ctx})


class GraphTopologyTests(unittest.TestCase):
    def test_graph_compiles_with_expected_nodes(self):
        g = D.build_graph()
        nodes = set(g.get_graph().nodes)
        for n in ("analyze_question", "normal_retrieve", "normal_answer", "decompose",
                  "retrieve_branch", "merge_evidence", "final_answer"):
            self.assertIn(n, nodes)

    def test_end_to_end_compound_path_without_provider(self):
        """Full graph run: analyzer + generation mocked, retrieval mocked."""
        def fake_chat(model, msgs, **k):
            if "analyse" in msgs[0]["content"][:40].lower() or "is_compound" in msgs[0]["content"]:
                return {"content": '{"is_compound": true, "subquestions": ["Q1?", "Q2?"]}',
                        "input_tokens": 10, "output_tokens": 5, "latency_ms": 5,
                        "retry_count": 0, "rate_limited": False}
            return {"content": "final synthesised answer", "input_tokens": 20, "output_tokens": 8,
                    "latency_ms": 7, "retry_count": 0, "rate_limited": False}
        with mock.patch.object(nv, "chat", side_effect=fake_chat), \
             mock.patch.object(D, "_scope", return_value=(["t1", "t2"], None)), \
             mock.patch.object(prod, "_hybrid_search_multi", return_value=(hits(5), 0.7)):
            g = D.build_graph()
            out = g.invoke({"case_id": "c1", "original_question": "What is X and why is Y?",
                            "route": "multi", "target": "A,B"})
        self.assertTrue(out["is_compound"])
        self.assertEqual(len(out["subquestions"]), 2)
        self.assertEqual(len(out["branch_results"]), 2, "fan-in must collect BOTH branches")
        self.assertLessEqual(len(out["merged_context"]), prod.MAX_LLM_CONTEXT_CHUNKS)
        self.assertEqual(out["final_answer"], "final synthesised answer")

    def test_end_to_end_simple_path_without_provider(self):
        def fake_chat(model, msgs, **k):
            if "is_compound" in msgs[0]["content"]:
                return {"content": '{"is_compound": false, "subquestions": []}', "input_tokens": 5,
                        "output_tokens": 2, "latency_ms": 3, "retry_count": 0, "rate_limited": False}
            return {"content": "simple answer", "input_tokens": 9, "output_tokens": 3,
                    "latency_ms": 4, "retry_count": 0, "rate_limited": False}
        with mock.patch.object(nv, "chat", side_effect=fake_chat), \
             mock.patch.object(D, "_scope", return_value=(["t1"], "t1")), \
             mock.patch.object(prod, "_hybrid_search", return_value=(hits(5), 0.7)), \
             mock.patch.object(prod, "_get_tenant", return_value={"display_name": "D", "domain": "x"}):
            out = D.build_graph().invoke({"case_id": "c2", "original_question": "What is X?",
                                          "route": "single", "target": "Someone"})
        self.assertFalse(out["is_compound"])
        self.assertEqual(out["final_answer"], "simple answer")
        self.assertLessEqual(len(out["merged_context"]), prod.MAX_LLM_CONTEXT_CHUNKS)


class GuardTests(unittest.TestCase):
    def test_groq_zero_call_guard(self):
        H.install_groq_guard()
        with self.assertRaises(H.GroqCallForbidden):
            prod.stream_answer("s", "q")

    def test_langchain_only_transitive(self):
        """LangGraph pulls langchain-core; we must not import LangChain app abstractions."""
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "decomp_graph.py")).read()
        self.assertNotIn("from langchain.", src)
        self.assertNotIn("import langchain\n", src)
        self.assertIn("from langgraph.graph import", src)


class StateSchemaTests(unittest.TestCase):
    """Regression: every key the runner passes in MUST be declared on DecompState, or
    LangGraph silently drops it (this emptied the tenant scope and broke retrieval)."""

    def test_runner_input_keys_are_all_declared(self):
        declared = set(D.DecompState.__annotations__)
        for k in ("case_id", "original_question", "route", "target"):
            self.assertIn(k, declared, f"{k} must be in DecompState or LangGraph will drop it")

    def test_target_survives_into_the_graph(self):
        seen = {}
        def fake_scope(route, target):
            seen["target"] = target
            return (["t1"], "t1")
        def fake_chat(model, msgs, **k):
            return {"content": '{"is_compound": false, "subquestions": []}', "input_tokens": 1,
                    "output_tokens": 1, "latency_ms": 1, "retry_count": 0, "rate_limited": False}
        with mock.patch.object(D, "_scope", side_effect=fake_scope), \
             mock.patch.object(nv, "chat", side_effect=fake_chat), \
             mock.patch.object(prod, "_hybrid_search", return_value=(hits(3), 0.7)), \
             mock.patch.object(prod, "_get_tenant", return_value={"display_name": "D", "domain": "x"}):
            D.build_graph().invoke({"case_id": "c", "original_question": "q",
                                    "route": "single", "target": "Someone Name"})
        self.assertEqual(seen.get("target"), "Someone Name", "target must reach _scope through graph state")


if __name__ == "__main__":
    unittest.main()
