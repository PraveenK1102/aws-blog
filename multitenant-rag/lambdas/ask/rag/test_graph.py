"""Production routed-RAG graph tests (§26). Offline: no AWS, Qdrant or Groq.

Run: PYTHONPATH=multitenant-rag/lambdas:multitenant-rag/lambdas/ask \
     python -m unittest rag.test_graph -v
"""
import unittest

from rag import config as rag_config
from rag import graph as G
from rag import scope as S
from rag.budget import LIMITS, BudgetExceeded, DeadlineExceeded, RequestBudget
from rag.conftest_helpers import (
    DECOMP_THREE, DECOMP_TWO, DECOMP_UNUSABLE, ROUTER_COMPOUND, ROUTER_SIMPLE,
    FakePoint, FakeProvider, make_deps, points,
)
from rag.provider import ProviderError, ProviderRateLimited


def run(question="Does X do A? And what about B?", scope=None, history=None,
        deps=None, budget=None, provider=None):
    """Execute the graph with fakes and return (final_state, provider, budget)."""
    scope = scope or S.single("t1")
    budget = budget or RequestBudget()
    deps = deps or make_deps()
    provider = provider or FakeProvider()
    with provider:
        state = G.run(request_id="req-1", question=question, scope=scope,
                      history=history or [], deps=deps, budget=budget)
    return state, provider, budget


# ------------------------------------------------------------------ routing
class RoutingTests(unittest.TestCase):
    def test_simple_router_result_skips_decomposition(self):
        st, p, _ = run(provider=FakeProvider(router=ROUTER_SIMPLE))
        self.assertEqual(st["answer_path"], "simple")
        self.assertEqual(p.kinds(), ["router", "generation"])
        self.assertNotIn("decomposition", p.kinds())
        self.assertEqual(st["router_reason_code"], "single_retrieval_need")

    def test_compound_router_result_triggers_decomposition(self):
        st, p, _ = run(provider=FakeProvider(router=ROUTER_COMPOUND))
        self.assertEqual(st["answer_path"], "compound")
        self.assertEqual(p.kinds(), ["router", "decomposition", "generation"])
        self.assertEqual(st["subquestions"], ["sub one", "sub two"])
        self.assertTrue(st["decomposition_used"])

    def test_router_information_needs_are_never_used_as_queries(self):
        """V2's needs are diagnostic only; branches use the DECOMPOSITION output."""
        calls = []
        st, _, _ = run(deps=make_deps(_calls=calls),
                       provider=FakeProvider(router=ROUTER_COMPOUND))
        queried = [c[1] for c in calls]
        self.assertEqual(sorted(queried), ["sub one", "sub two"])
        for need in st["router_information_needs"]:
            self.assertNotIn(need, queried)

    def test_unparseable_router_falls_back_to_simple(self):
        st, p, _ = run(provider=FakeProvider(router="not json at all"))
        self.assertEqual(st["answer_path"], "simple")
        self.assertFalse(st["router_parse_ok"])
        self.assertNotIn("decomposition", p.kinds())
        self.assertTrue(any(e.startswith("router:") for e in st["errors"]))

    def test_router_enum_violation_falls_back_to_simple(self):
        bad = ('{"needs_decomposition": true, "information_needs": ["a","b"], '
               '"reason_code": "single_retrieval_need"}')
        st, _, _ = run(provider=FakeProvider(router=bad))
        self.assertEqual(st["answer_path"], "simple")
        self.assertFalse(st["router_parse_ok"])


class RouterFailureFallbackTests(unittest.TestCase):
    def test_router_provider_failure_falls_back_and_still_answers(self):
        p = FakeProvider(raises={"router": ProviderError("timeout")})
        st, prov, _ = run(provider=p)
        self.assertEqual(st["answer_path"], "simple")
        self.assertTrue(st["router_failed"])
        self.assertEqual(st["result_type"], "answered")   # user still gets an answer
        self.assertIn("generation", prov.kinds())

    def test_router_429_falls_back_and_still_answers(self):
        p = FakeProvider(raises={"router": ProviderRateLimited(9000)})
        st, _, _ = run(provider=p)
        self.assertEqual(st["answer_path"], "simple")
        self.assertTrue(st["router_failed"])
        self.assertEqual(st["result_type"], "answered")


class DecompositionFallbackTests(unittest.TestCase):
    def test_unusable_decomposition_uses_normal_retrieval(self):
        st, p, _ = run(provider=FakeProvider(router=ROUTER_COMPOUND,
                                             decomposition=DECOMP_UNUSABLE))
        self.assertEqual(st["answer_path"], "simple")
        self.assertTrue(st["decomposition_unusable"])
        self.assertFalse(st["decomposition_used"])
        self.assertEqual(p.kinds(), ["router", "decomposition", "generation"])

    def test_single_subquestion_never_fans_out_one_branch(self):
        one = '{"is_compound": true, "subquestions": ["only one"]}'
        st, _, _ = run(provider=FakeProvider(router=ROUTER_COMPOUND,
                                             decomposition=one))
        self.assertEqual(st["answer_path"], "simple")
        self.assertEqual(st["branch_count"], 1)

    def test_decomposition_provider_failure_falls_back(self):
        p = FakeProvider(router=ROUTER_COMPOUND,
                         raises={"decomposition": ProviderError("timeout")})
        st, _, _ = run(provider=p)
        self.assertEqual(st["answer_path"], "simple")
        self.assertTrue(st["decomposition_failed"])
        self.assertEqual(st["result_type"], "answered")

    def test_decomposition_429_falls_back(self):
        p = FakeProvider(router=ROUTER_COMPOUND,
                         raises={"decomposition": ProviderRateLimited(9000)})
        st, _, _ = run(provider=p)
        self.assertEqual(st["answer_path"], "simple")
        self.assertTrue(st["decomposition_failed"])
        self.assertEqual(st["result_type"], "answered")


# ------------------------------------------------------------------ fan-out
class FanOutTests(unittest.TestCase):
    def test_send_payload_shape_and_scope(self):
        scope = S.multi(["t1", "t2"])
        from rag.retrieval_nodes import fan_out_payloads
        pls = fan_out_payloads({"scope": scope,
                                "subquestions": ["a", "b", "c"]})
        self.assertEqual(len(pls), 3)
        for i, p in enumerate(pls):
            self.assertEqual(p["branch"], i)
            self.assertEqual(p["subquestion"], ["a", "b", "c"][i])
            self.assertEqual(p["scope_payload"]["tenant_ids"], ["t1", "t2"])
            self.assertEqual(p["parent_scope_payload"], p["scope_payload"])

    def test_two_branch_merge(self):
        st, _, b = run(provider=FakeProvider(router=ROUTER_COMPOUND,
                                             decomposition=DECOMP_TWO))
        self.assertEqual(st["branch_count"], 2)
        self.assertEqual(b.counts["retrieval_branches"], 2)
        self.assertLessEqual(len(st["merged_context"]), 5)

    def test_three_branch_merge(self):
        st, _, b = run(provider=FakeProvider(router=ROUTER_COMPOUND,
                                             decomposition=DECOMP_THREE))
        self.assertEqual(st["branch_count"], 3)
        self.assertEqual(b.counts["retrieval_branches"], 3)
        self.assertEqual(b.counts["qdrant_physical_queries"], 6)
        self.assertLessEqual(len(st["merged_context"]), 5)

    def test_branches_run_independently_per_subquestion(self):
        calls = []
        run(deps=make_deps(_calls=calls),
            provider=FakeProvider(router=ROUTER_COMPOUND,
                                  decomposition=DECOMP_THREE))
        self.assertEqual(sorted(c[1] for c in calls),
                         ["sub one", "sub three", "sub two"])


# --------------------------------------------------------------- scope safety
class ScopeSafetyTests(unittest.TestCase):
    def test_single_scope_retrieves_only_that_tenant(self):
        calls = []
        run(deps=make_deps(_calls=calls),
            scope=S.single("t-only"),
            provider=FakeProvider(router=ROUTER_COMPOUND))
        self.assertTrue(calls)
        for c in calls:
            self.assertEqual(c[0], "hybrid_search")
            self.assertEqual(c[2], "t-only")

    def test_multi_scope_passes_exact_allowed_set_to_every_branch(self):
        calls = []
        run(deps=make_deps(_calls=calls), scope=S.multi(["a", "b", "c"]),
            provider=FakeProvider(router=ROUTER_COMPOUND,
                                  decomposition=DECOMP_THREE))
        self.assertEqual(len(calls), 3)
        for c in calls:
            self.assertEqual(c[0], "hybrid_search_multi")
            self.assertEqual(c[2], ("a", "b", "c"))

    def test_group_scope_kind_preserved(self):
        calls = []
        run(deps=make_deps(_calls=calls), scope=S.multi(["g1", "g2"], kind="group"),
            provider=FakeProvider(router=ROUTER_COMPOUND))
        for c in calls:
            self.assertEqual(c[2], ("g1", "g2"))

    def test_branch_scope_parity_every_branch(self):
        seen = []

        def hsm(q, tids, dense_vec=None):
            seen.append(tuple(tids))
            return points(2), 0.8
        run(deps=make_deps(hybrid_search_multi=hsm), scope=S.multi(["x", "y"]),
            provider=FakeProvider(router=ROUTER_COMPOUND,
                                  decomposition=DECOMP_THREE))
        self.assertEqual(len(set(seen)), 1, "branches disagreed on scope")
        self.assertEqual(seen[0], ("x", "y"))

    def test_fallback_scope_parity(self):
        """The decomposition fallback must retrieve in the SAME scope."""
        calls = []
        run(deps=make_deps(_calls=calls), scope=S.multi(["p", "q"]),
            provider=FakeProvider(router=ROUTER_COMPOUND,
                                  decomposition=DECOMP_UNUSABLE))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2], ("p", "q"))

    def test_widened_branch_scope_is_rejected(self):
        """A tampered Send payload must fail closed, not search wider."""
        from rag.retrieval_nodes import retrieve_branch
        parent = S.single("t1")
        widened = S.multi(["t1", "t2"])
        out = retrieve_branch({"branch": 0, "subquestion": "q",
                               "scope_payload": widened.for_branch(),
                               "parent_scope_payload": parent.for_branch()},
                              make_deps(), RequestBudget())
        self.assertEqual(out["branch_results"][0]["error"], "ScopeError")
        self.assertTrue(out["branch_results"][0]["evidence_missing"])

    def test_empty_scope_fails_closed(self):
        with self.assertRaises(S.ScopeError):
            S.multi([])
        with self.assertRaises(S.ScopeError):
            S.single("")

    def test_scope_is_immutable(self):
        sc = S.single("t1")
        with self.assertRaises(Exception):
            sc.tenant_ids = ("other",)
        self.assertIsInstance(sc.tenant_ids, tuple)

    def test_scope_metadata_never_contains_tenant_ids(self):
        md = S.multi(["secret-a", "secret-b"]).as_metadata()
        self.assertEqual(md, {"scope_kind": "multi", "scope_tenant_count": 2})
        self.assertNotIn("secret-a", str(md))

    def test_graph_without_scope_raises(self):
        with self.assertRaises(Exception):
            G.build_graph().invoke(
                {"question": "q", "retrieval_query": "q", "history": [],
                 "branch_results": []},
                config={"configurable": {"deps": make_deps(),
                                         "budget": RequestBudget()}})


# ------------------------------------------------------------ semantic cache
class SemanticCacheTests(unittest.TestCase):
    def test_cache_hit_spends_no_router_call(self):
        hit = {"answer": "CACHED", "citations": [{"post_id": "p1", "title": "T"}],
               "score": 0.99}
        st, p, b = run(deps=make_deps(semcache_lookup=lambda tid, d: hit),
                       provider=FakeProvider())
        self.assertTrue(st["cache_hit"])
        self.assertEqual(st["final_answer"], "CACHED")
        self.assertEqual(p.kinds(), [])                    # ZERO Groq calls
        self.assertEqual(b.counts["router_calls"], 0)
        self.assertEqual(b.counts["groq_logical_calls"], 0)

    def test_cache_miss_proceeds_to_routing(self):
        st, p, _ = run(provider=FakeProvider(router=ROUTER_SIMPLE))
        self.assertFalse(st["cache_hit"])
        self.assertIn("router", p.kinds())

    def test_group_route_is_not_cache_eligible(self):
        probed = []
        run(deps=make_deps(semcache_lookup=lambda tid, d: probed.append(tid)),
            scope=S.multi(["a", "b"]), provider=FakeProvider())
        self.assertEqual(probed, [], "group route must not probe the cache")

    def test_history_makes_request_cache_ineligible(self):
        probed = []
        st, _, _ = run(history=[{"role": "user", "content": "earlier"}],
                       deps=make_deps(
                           semcache_lookup=lambda tid, d: probed.append(tid)),
                       provider=FakeProvider())
        self.assertEqual(probed, [])
        self.assertFalse(st["cache_eligible"])

    def test_cache_failure_degrades_to_normal_query(self):
        def boom(tid, d):
            raise RuntimeError("qdrant down")
        st, p, _ = run(deps=make_deps(semcache_lookup=boom),
                       provider=FakeProvider(router=ROUTER_SIMPLE))
        self.assertFalse(st["cache_hit"])
        self.assertEqual(st["result_type"], "answered")
        self.assertIn("router", p.kinds())

    def test_embed_failure_degrades_to_normal_query(self):
        def boom(t):
            raise RuntimeError("bedrock down")
        st, _, _ = run(deps=make_deps(embed_dense=boom),
                       provider=FakeProvider(router=ROUTER_SIMPLE))
        self.assertFalse(st["cache_hit"])
        self.assertEqual(st["result_type"], "answered")


# --------------------------------------------------- context / citations
class ContextCitationInvariantTests(unittest.TestCase):
    def test_context_never_exceeds_five(self):
        many = lambda q, tid, dense_vec=None: (points(40), 0.9)          # noqa: E731
        st, _, _ = run(deps=make_deps(hybrid_search=many),
                       provider=FakeProvider(router=ROUTER_COMPOUND,
                                             decomposition=DECOMP_THREE))
        self.assertLessEqual(len(st["merged_context"]), 5)

    def test_citations_are_a_subset_of_llm_visible_chunks(self):
        st, _, _ = run(provider=FakeProvider(router=ROUTER_COMPOUND,
                                             decomposition=DECOMP_THREE))
        visible = {c.payload["post_id"] for c in st["merged_context"]}
        cited = {c["post_id"] for c in st["citations"]}
        self.assertTrue(cited.issubset(visible),
                        f"cited {cited - visible} the model never saw")

    def test_every_context_post_is_citation_eligible(self):
        st, _, _ = run(provider=FakeProvider(router=ROUTER_SIMPLE))
        visible = {c.payload["post_id"] for c in st["merged_context"]}
        cited = {c["post_id"] for c in st["citations"]}
        self.assertEqual(cited, visible)

    def test_prompt_is_built_from_the_same_capped_context(self):
        st, p, _ = run(provider=FakeProvider(router=ROUTER_COMPOUND,
                                             decomposition=DECOMP_TWO))
        gen = [c for c in p.calls if c["kind"] == "generation"][0]
        user = gen["messages"][-1]["content"]
        for c in st["merged_context"]:
            self.assertIn(c.payload["chunk_text"], user)

    def test_refusal_suppresses_citations(self):
        st, _, _ = run(provider=FakeProvider(
            router=ROUTER_SIMPLE, generation="Dave hasn't written about this."))
        self.assertEqual(st["citations"], [])
        self.assertEqual(st["result_type"], "refused")

    def test_empty_retrieval_yields_no_citations(self):
        below = lambda q, tid, dense_vec=None: (points(3), 0.01)          # noqa: E731
        st, p, _ = run(deps=make_deps(hybrid_search=below),
                       provider=FakeProvider(router=ROUTER_SIMPLE))
        self.assertEqual(st["merged_context"], [])
        self.assertEqual(st["citations"], [])
        self.assertEqual(st["result_type"], "empty_context")
        self.assertNotIn("generation", p.kinds())   # no LLM call without evidence


# ---------------------------------------------------------- prompt selection
class PromptSelectionTests(unittest.TestCase):
    def test_compound_path_uses_frozen_compound_prompt(self):
        from rag import prompts
        st, p, _ = run(provider=FakeProvider(router=ROUTER_COMPOUND,
                                             decomposition=DECOMP_TWO))
        gen = [c for c in p.calls if c["kind"] == "generation"][0]
        self.assertEqual(gen["messages"][0]["content"], prompts.GEN_SYS_COMPOUND)
        self.assertIn("SUB-QUESTIONS TO COVER", gen["messages"][-1]["content"])

    def test_simple_path_uses_production_single_prompt_builder(self):
        st, p, _ = run(provider=FakeProvider(router=ROUTER_SIMPLE))
        gen = [c for c in p.calls if c["kind"] == "generation"][0]
        self.assertTrue(gen["messages"][0]["content"].startswith("SINGLE:Dave:"))

    def test_group_path_uses_production_group_prompt_builder(self):
        st, p, _ = run(scope=S.multi(["a", "b"]),
                       provider=FakeProvider(router=ROUTER_SIMPLE))
        gen = [c for c in p.calls if c["kind"] == "generation"][0]
        self.assertTrue(gen["messages"][0]["content"].startswith("GROUP:"))

    def test_fallback_path_uses_production_builder_not_compound_prompt(self):
        """Closes the offline replay caveat: the 3 fallback cases previously used
        a SUBSTITUTED compound prompt. In production they must use the real one."""
        from rag import prompts
        st, p, _ = run(provider=FakeProvider(router=ROUTER_COMPOUND,
                                             decomposition=DECOMP_UNUSABLE))
        gen = [c for c in p.calls if c["kind"] == "generation"][0]
        self.assertNotEqual(gen["messages"][0]["content"], prompts.GEN_SYS_COMPOUND)
        self.assertTrue(gen["messages"][0]["content"].startswith("SINGLE:Dave:"))

    def test_generation_model_is_120b_and_router_is_20b(self):
        st, p, _ = run(provider=FakeProvider(router=ROUTER_COMPOUND,
                                             decomposition=DECOMP_TWO))
        by_kind = {c["kind"]: c["model"] for c in p.calls}
        self.assertEqual(by_kind["router"], rag_config.ROUTER_MODEL)
        self.assertEqual(by_kind["decomposition"], rag_config.DECOMPOSE_MODEL)
        self.assertEqual(by_kind["generation"], rag_config.GENERATION_MODEL)
        self.assertIn("20b", by_kind["router"])
        self.assertIn("20b", by_kind["decomposition"])
        self.assertIn("120b", by_kind["generation"])


# ------------------------------------------------------------ chat history
class ChatHistoryTests(unittest.TestCase):
    def test_history_is_bounded_to_last_eight_turns(self):
        hist = [{"role": "user" if i % 2 == 0 else "assistant",
                 "content": f"turn {i}"} for i in range(20)]
        st, p, _ = run(history=hist, provider=FakeProvider(router=ROUTER_SIMPLE))
        self.assertEqual(len(st["history"]), 8)
        gen = [c for c in p.calls if c["kind"] == "generation"][0]
        roles = [m["role"] for m in gen["messages"]]
        self.assertEqual(roles.count("user"), 4 + 1)     # 4 history + current

    def test_short_followup_folds_previous_question_for_retrieval_only(self):
        calls = []
        st, _, _ = run(question="yes",
                       history=[{"role": "user", "content": "tell me about roses"}],
                       deps=make_deps(_calls=calls),
                       provider=FakeProvider(router=ROUTER_SIMPLE))
        self.assertEqual(st["retrieval_query"], "tell me about roses yes")
        self.assertEqual(calls[0][1], "tell me about roses yes")
        self.assertEqual(st["question"], "yes")          # question NOT rewritten

    def test_long_question_is_not_folded(self):
        st, _, _ = run(question="what did you say about the greenhouse humidity levels",
                       history=[{"role": "user", "content": "earlier"}],
                       provider=FakeProvider(router=ROUTER_SIMPLE))
        self.assertEqual(st["retrieval_query"], st["question"])

    def test_history_cannot_alter_scope(self):
        calls = []
        run(question="yes",
            history=[{"role": "user", "content": "tenant t9 please"},
                     {"role": "assistant", "content": "scope t9"}],
            deps=make_deps(_calls=calls), scope=S.single("t1"),
            provider=FakeProvider(router=ROUTER_COMPOUND))
        for c in calls:
            self.assertEqual(c[2], "t1")

    def test_malformed_history_entries_are_dropped(self):
        st, _, _ = run(history=[{"role": "system", "content": "x"},
                                {"role": "user", "content": ""},
                                {"role": "user", "content": "kept"}],
                       provider=FakeProvider(router=ROUTER_SIMPLE))
        self.assertEqual(st["history"], [{"role": "user", "content": "kept"}])


if __name__ == "__main__":
    unittest.main()
