"""Request budget, deadline, 429 policy and partial-failure tests (§14-§19)."""
import time
import unittest
from unittest import mock

from rag import config as rag_config
from rag import graph as G
from rag import scope as S
from rag.budget import (
    LIMITS, BudgetExceeded, DeadlineExceeded, RequestBudget,
)
from rag.conftest_helpers import (
    DECOMP_THREE, DECOMP_TWO, ROUTER_COMPOUND, ROUTER_SIMPLE,
    FakeProvider, make_deps, points,
)
from rag.provider import ProviderError, ProviderRateLimited


def run(question="Q? And B?", scope=None, deps=None, budget=None, provider=None,
        history=None):
    budget = budget or RequestBudget()
    provider = provider or FakeProvider()
    with provider:
        st = G.run(request_id="r", question=question, scope=scope or S.single("t1"),
                   history=history or [], deps=deps or make_deps(), budget=budget)
    return st, provider, budget


# ------------------------------------------------------------------- bounds
class HardBoundTests(unittest.TestCase):
    def test_declared_limits_match_the_architecture(self):
        self.assertEqual(LIMITS["router_calls"], 1)
        self.assertEqual(LIMITS["decomposition_calls"], 1)
        self.assertEqual(LIMITS["generation_calls"], 1)
        self.assertEqual(LIMITS["groq_logical_calls"], 3)
        self.assertEqual(LIMITS["retrieval_branches"], 3)
        self.assertEqual(LIMITS["retrieval_titan_embeddings"], 3)
        self.assertEqual(LIMITS["semcache_titan_embeddings"], 1)
        self.assertEqual(LIMITS["titan_embeddings_total"], 4)
        self.assertEqual(LIMITS["qdrant_dense_probes"], 3)
        self.assertEqual(LIMITS["qdrant_hybrid_queries"], 3)
        self.assertEqual(LIMITS["qdrant_physical_queries"], 6)
        self.assertEqual(LIMITS["final_context_chunks"], 5)

    def test_compound_worst_case_stays_inside_every_bound(self):
        _, p, b = run(provider=FakeProvider(router=ROUTER_COMPOUND,
                                            decomposition=DECOMP_THREE))
        for res, limit in LIMITS.items():
            self.assertLessEqual(b.counts[res], limit, res)
        self.assertEqual(b.counts["groq_logical_calls"], 3)
        self.assertEqual(b.counts["qdrant_physical_queries"], 6)

    def test_second_router_call_is_refused(self):
        b = RequestBudget()
        b.spend_groq("router")
        with self.assertRaises(BudgetExceeded) as cm:
            b.spend_groq("router")
        self.assertEqual(cm.exception.resource, "router_calls")

    def test_three_routers_cannot_masquerade_as_the_logical_budget(self):
        b = RequestBudget()
        b.spend_groq("router")
        with self.assertRaises(BudgetExceeded):
            b.spend_groq("router")
        self.assertEqual(b.counts["groq_logical_calls"], 1)

    def test_rejected_call_leaves_no_residue(self):
        b = RequestBudget()
        b.spend_groq("router")
        try:
            b.spend_groq("router")
        except BudgetExceeded:
            pass
        self.assertEqual(b.counts["router_calls"], 1)
        self.assertEqual(b.counts["groq_logical_calls"], 1)

    def test_fourth_branch_is_refused(self):
        b = RequestBudget()
        for _ in range(3):
            b.spend_retrieval()
        with self.assertRaises(BudgetExceeded) as cm:
            b.spend_retrieval()
        self.assertIn(cm.exception.resource,
                      ("retrieval_branches", "retrieval_titan_embeddings",
                       "titan_embeddings_total", "qdrant_physical_queries"))

    def test_reused_embedding_is_not_double_counted(self):
        b = RequestBudget()
        b.spend_retrieval(embed=False)
        self.assertEqual(b.counts["retrieval_titan_embeddings"], 0)
        self.assertEqual(b.counts["titan_embeddings_total"], 0)
        self.assertEqual(b.counts["qdrant_physical_queries"], 2)

    def test_decomposition_is_capped_by_remaining_branch_room(self):
        """A model returning 3 subquestions when only 1 branch remains must be
        truncated, not allowed to overrun the branch budget."""
        from rag import decomposition as D
        b = RequestBudget()
        b.spend_retrieval(); b.spend_retrieval()          # 2 branches used
        self.assertEqual(D.budget_branch_room(b), 1)

    def test_context_bound_is_asserted_not_assumed(self):
        """build_context raises if a malformed merge ever exceeds the cap."""
        deps = make_deps(max_llm_context_chunks=99)       # deliberately too high
        with self.assertRaises(BudgetExceeded):
            G.build_context({"merged_context": points(9), "answer_path": "simple",
                             "scope": S.single("t1"), "question": "q"},
                            config={"configurable": {"deps": deps,
                                                     "budget": RequestBudget()}})

    def test_budget_exhaustion_terminates_deterministically(self):
        """A pre-exhausted generation budget must produce a controlled result."""
        b = RequestBudget()
        b.spend_groq("generation")                        # generation already used
        st, _, _ = run(budget=b, provider=FakeProvider(router=ROUTER_SIMPLE))
        self.assertEqual(st["result_type"], "generation_error")
        self.assertEqual(st["citations"], [])

    def test_snapshot_is_content_free(self):
        _, _, b = run(provider=FakeProvider(router=ROUTER_COMPOUND))
        snap = b.snapshot()
        for v in snap.values():
            self.assertIsInstance(v, (int, bool))


# ----------------------------------------------------------------- deadline
class DeadlineTests(unittest.TestCase):
    def test_default_deadline_is_24s(self):
        self.assertEqual(rag_config.REQUEST_DEADLINE_MS, 24000)

    def test_deadline_is_below_the_api_gateway_timeout(self):
        self.assertLess(rag_config.REQUEST_DEADLINE_MS, 30000)

    def test_remaining_shrinks_monotonically(self):
        b = RequestBudget()
        first = b.remaining_ms()
        time.sleep(0.01)
        self.assertLessEqual(b.remaining_ms(), first)

    def test_timeout_is_clamped_to_remaining_budget(self):
        b = RequestBudget(deadline_ms=4000)
        t = b.timeout_for("generation", rag_config.GENERATION_TIMEOUT_MS)
        self.assertLessEqual(t, 4000 - rag_config.TAIL_RESERVE_MS)
        self.assertLess(t, rag_config.GENERATION_TIMEOUT_MS)

    def test_timeout_never_exceeds_its_own_ceiling(self):
        b = RequestBudget(deadline_ms=600000)
        self.assertEqual(b.timeout_for("router", rag_config.ROUTER_TIMEOUT_MS),
                         rag_config.ROUTER_TIMEOUT_MS)

    def test_exhausted_deadline_refuses_to_start_a_call(self):
        b = RequestBudget(deadline_ms=100)
        with self.assertRaises(DeadlineExceeded):
            b.timeout_for("generation", rag_config.GENERATION_TIMEOUT_MS)
        self.assertTrue(b.deadline_exceeded)

    def test_deadline_exhaustion_produces_controlled_result(self):
        b = RequestBudget(deadline_ms=1)
        st, p, _ = run(budget=b, provider=FakeProvider(router=ROUTER_SIMPLE))
        self.assertIn(st["result_type"],
                      ("generation_error", "empty_context", "provider_unavailable"))
        self.assertEqual(st["citations"], [])
        self.assertTrue(b.deadline_exceeded)

    def test_branch_not_started_when_it_cannot_finish(self):
        from rag.retrieval_nodes import retrieve_branch
        sc = S.single("t1")
        b = RequestBudget(deadline_ms=1)
        out = retrieve_branch({"branch": 0, "subquestion": "q",
                               "scope_payload": sc.for_branch(),
                               "parent_scope_payload": sc.for_branch()},
                              make_deps(), b)
        self.assertEqual(out["branch_results"][0]["error"], "DeadlineExceeded")

    def test_snapshot_exposes_deadline_metadata(self):
        _, _, b = run(provider=FakeProvider(router=ROUTER_SIMPLE))
        snap = b.snapshot()
        self.assertEqual(snap["request_deadline_ms"], 24000)
        self.assertIn("remaining_budget_ms", snap)
        self.assertIn("deadline_exceeded", snap)


# ------------------------------------------------------------- 429 behaviour
class RateLimitPolicyTests(unittest.TestCase):
    """The provider is exercised directly with a stubbed requests.post."""

    def _resp(self, status, body="", headers=None, json_body=None):
        m = mock.Mock()
        m.status_code = status
        m.text = body
        m.headers = headers or {}
        m.json = mock.Mock(return_value=json_body or {})
        return m

    def test_no_proactive_pacing_exists_in_production_config(self):
        """The 7s experiment pacing must NOT have been carried into production."""
        self.assertLessEqual(rag_config.MAX_RATE_LIMIT_WAIT_MS, 2000)
        self.assertLessEqual(rag_config.MAX_RATE_LIMIT_RETRIES, 1)

    def test_long_suggested_wait_is_not_retried(self):
        from rag import provider as PV
        ok = self._resp(200, json_body={
            "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
        r429 = self._resp(429, "Rate limit reached, try again in 45.0s")
        with mock.patch.object(PV.requests, "post", side_effect=[r429, ok]) as post, \
             mock.patch.object(PV, "get_groq_key", return_value="k"), \
             mock.patch.object(PV.time, "sleep") as slept:
            b = RequestBudget()
            with self.assertRaises(ProviderRateLimited):
                PV.chat("m", [], budget=b, kind="generation", max_tokens=10,
                        ceiling_ms=5000)
            self.assertEqual(post.call_count, 1, "must not retry a 45s wait")
            slept.assert_not_called()
            self.assertEqual(b.rate_limit_events, 1)

    def test_short_suggested_wait_is_retried_once(self):
        from rag import provider as PV
        ok = self._resp(200, json_body={
            "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
        r429 = self._resp(429, "Rate limit reached, try again in 0.4s")
        with mock.patch.object(PV.requests, "post", side_effect=[r429, ok]) as post, \
             mock.patch.object(PV, "get_groq_key", return_value="k"), \
             mock.patch.object(PV.time, "sleep") as slept:
            b = RequestBudget()
            out = PV.chat("m", [], budget=b, kind="generation", max_tokens=10,
                          ceiling_ms=5000)
            self.assertEqual(out["content"], "x")
            self.assertEqual(post.call_count, 2)
            slept.assert_called_once_with(0.4)
            self.assertEqual(b.retry_count, 1)

    def test_short_wait_is_refused_when_budget_cannot_afford_it(self):
        from rag import provider as PV
        r429 = self._resp(429, "Rate limit reached, try again in 1.5s")
        with mock.patch.object(PV.requests, "post", return_value=r429) as post, \
             mock.patch.object(PV, "get_groq_key", return_value="k"), \
             mock.patch.object(PV.time, "sleep") as slept:
            # Sized so the FIRST call fits (remaining-TAIL_RESERVE >= HEADROOM)
            # but wait+another call does not (remaining-TAIL_RESERVE < 1500+1200).
            b = RequestBudget(deadline_ms=3500)
            with self.assertRaises(ProviderRateLimited):
                PV.chat("m", [], budget=b, kind="generation", max_tokens=10,
                        ceiling_ms=5000)
            self.assertEqual(post.call_count, 1)
            slept.assert_not_called()

    def test_timeout_is_derived_from_remaining_budget(self):
        from rag import provider as PV
        ok = self._resp(200, json_body={
            "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
            "usage": {}})
        with mock.patch.object(PV.requests, "post", return_value=ok) as post, \
             mock.patch.object(PV, "get_groq_key", return_value="k"):
            b = RequestBudget(deadline_ms=5000)
            PV.chat("m", [], budget=b, kind="generation", max_tokens=10,
                    ceiling_ms=60000)          # ceiling far above the deadline
            sent = post.call_args.kwargs["timeout"]
            self.assertLessEqual(sent * 1000, 5000)

    def test_generation_rate_limit_returns_controlled_contract(self):
        st, _, _ = run(provider=FakeProvider(
            router=ROUTER_SIMPLE,
            raises={"generation": ProviderRateLimited(45000)}))
        self.assertEqual(st["result_type"], "provider_unavailable")
        self.assertEqual(st["citations"], [])

    def test_generation_error_returns_controlled_contract(self):
        st, _, _ = run(provider=FakeProvider(
            router=ROUTER_SIMPLE, raises={"generation": ProviderError("timeout")}))
        self.assertEqual(st["result_type"], "generation_error")
        self.assertEqual(st["citations"], [])


# ------------------------------------------------------- partial branch failure
class PartialBranchFailureTests(unittest.TestCase):
    def _flaky(self, fail_on):
        state = {"n": 0}

        def hs(q, tid, dense_vec=None):
            state["n"] += 1
            if q in fail_on:
                raise RuntimeError("qdrant blew up")
            return points(3), 0.8
        return hs

    def test_one_branch_fails_others_still_answer(self):
        st, p, _ = run(deps=make_deps(hybrid_search=self._flaky({"sub two"})),
                       provider=FakeProvider(router=ROUTER_COMPOUND,
                                             decomposition=DECOMP_THREE))
        self.assertTrue(st["partial_branch_failure"])
        self.assertEqual(st["failed_branch_count"], 1)
        self.assertEqual(st["successful_branch_count"], 2)
        self.assertEqual(st["result_type"], "answered")
        self.assertTrue(st["merged_context"])
        self.assertIn("generation", p.kinds())

    def test_multiple_branch_failures_still_answer_from_the_survivor(self):
        st, _, _ = run(
            deps=make_deps(hybrid_search=self._flaky({"sub one", "sub two"})),
            provider=FakeProvider(router=ROUTER_COMPOUND,
                                  decomposition=DECOMP_THREE))
        self.assertEqual(st["failed_branch_count"], 2)
        self.assertEqual(st["successful_branch_count"], 1)
        self.assertTrue(st["partial_branch_failure"])
        self.assertEqual(st["result_type"], "answered")

    def test_all_branches_fail_produces_empty_context_not_a_crash(self):
        st, p, _ = run(
            deps=make_deps(
                hybrid_search=self._flaky({"sub one", "sub two", "sub three"})),
            provider=FakeProvider(router=ROUTER_COMPOUND,
                                  decomposition=DECOMP_THREE))
        self.assertEqual(st["successful_branch_count"], 0)
        self.assertEqual(st["merged_context"], [])
        self.assertEqual(st["result_type"], "empty_context")
        self.assertNotIn("generation", p.kinds())
        self.assertFalse(st["partial_branch_failure"])   # total, not partial

    def test_failure_never_widens_scope(self):
        seen = []

        def hsm(q, tids, dense_vec=None):
            seen.append(tuple(tids))
            if q == "sub one":
                raise RuntimeError("boom")
            return points(2), 0.8
        run(deps=make_deps(hybrid_search_multi=hsm), scope=S.multi(["a", "b"]),
            provider=FakeProvider(router=ROUTER_COMPOUND,
                                  decomposition=DECOMP_THREE))
        self.assertEqual(set(seen), {("a", "b")})

    def test_partial_failure_is_recorded_for_observability(self):
        st, _, _ = run(deps=make_deps(hybrid_search=self._flaky({"sub two"})),
                       provider=FakeProvider(router=ROUTER_COMPOUND,
                                             decomposition=DECOMP_THREE))
        for k in ("partial_branch_failure", "failed_branch_count",
                  "successful_branch_count", "branch_count"):
            self.assertIn(k, st)


class TitanBudgetScenarioTests(unittest.TestCase):
    """ARCHITECT-ACCEPTED Titan accounting (2026-08-24), option (a).

    The per-request TOTAL maximum is 4, not 3:
        1 semantic-cache probe embedding + up to 3 retrieval embeddings.
    "3" is the RETRIEVAL bound alone. These tests pin all three scenarios so a
    future reader cannot mistake the retrieval bound for the request total.
    """

    def test_simple_cache_eligible_request_uses_one_titan_call(self):
        """The probe vector is REUSED for retrieval, never re-embedded."""
        embeds = []
        deps = make_deps(embed_dense=lambda t: embeds.append(t) or [0.01] * 1024)
        st, _, b = run(deps=deps, provider=FakeProvider(router=ROUTER_SIMPLE))
        self.assertEqual(st["answer_path"], "simple")
        self.assertEqual(b.counts["semcache_titan_embeddings"], 1)
        self.assertEqual(b.counts["titan_embeddings_total"], 1)
        self.assertLessEqual(b.snapshot()["titan_embeddings_total"], 1)

    def test_two_branch_compound_after_cache_miss_uses_three_titan_calls(self):
        st, _, b = run(provider=FakeProvider(router=ROUTER_COMPOUND,
                                             decomposition=DECOMP_TWO))
        self.assertEqual(st["answer_path"], "compound")
        self.assertEqual(st["branch_count"], 2)
        self.assertEqual(b.counts["semcache_titan_embeddings"], 1)
        self.assertEqual(b.counts["retrieval_titan_embeddings"], 2)
        self.assertEqual(b.counts["titan_embeddings_total"], 3)
        self.assertLessEqual(b.snapshot()["titan_embeddings_total"], 3)

    def test_three_branch_compound_after_cache_miss_uses_four_titan_calls(self):
        st, _, b = run(provider=FakeProvider(router=ROUTER_COMPOUND,
                                             decomposition=DECOMP_THREE))
        self.assertEqual(st["answer_path"], "compound")
        self.assertEqual(st["branch_count"], 3)
        self.assertEqual(b.counts["semcache_titan_embeddings"], 1)
        self.assertEqual(b.counts["retrieval_titan_embeddings"], 3)
        self.assertEqual(b.counts["titan_embeddings_total"], 4)
        self.assertLessEqual(b.snapshot()["titan_embeddings_total"],
                             LIMITS["titan_embeddings_total"])

    def test_group_route_is_not_cache_eligible_so_costs_no_probe_embedding(self):
        st, _, b = run(scope=S.multi(["a", "b"]),
                       provider=FakeProvider(router=ROUTER_COMPOUND,
                                             decomposition=DECOMP_THREE))
        self.assertEqual(b.counts["semcache_titan_embeddings"], 0)
        self.assertEqual(b.counts["retrieval_titan_embeddings"], 3)
        self.assertEqual(b.counts["titan_embeddings_total"], 3)

    def test_total_is_an_enforced_bound_not_a_derived_report(self):
        b = RequestBudget()
        b.spend_semcache_embedding()
        for _ in range(3):
            b.spend_retrieval()
        self.assertEqual(b.counts["titan_embeddings_total"], 4)
        with self.assertRaises(BudgetExceeded):
            b.spend_retrieval()          # would be the 5th Titan call

    def test_second_semcache_probe_is_refused(self):
        b = RequestBudget()
        b.spend_semcache_embedding()
        with self.assertRaises(BudgetExceeded) as cm:
            b.spend_semcache_embedding()
        self.assertEqual(cm.exception.resource, "semcache_titan_embeddings")

    def test_rejected_probe_leaves_no_residue_in_the_total(self):
        b = RequestBudget()
        b.spend_semcache_embedding()
        try:
            b.spend_semcache_embedding()
        except BudgetExceeded:
            pass
        self.assertEqual(b.counts["titan_embeddings_total"], 1)

    def test_snapshot_exposes_all_three_titan_figures(self):
        _, _, b = run(provider=FakeProvider(router=ROUTER_COMPOUND,
                                            decomposition=DECOMP_THREE))
        snap = b.snapshot()
        for k in ("semcache_titan_embeddings", "retrieval_titan_embeddings",
                  "titan_embeddings_total"):
            self.assertIn(k, snap, k)
        self.assertEqual(snap["titan_embeddings_total"],
                         snap["semcache_titan_embeddings"]
                         + snap["retrieval_titan_embeddings"])

    def test_probe_vector_is_actually_passed_to_retrieval(self):
        """Cheaper AND correct: the reused vector must reach the retriever."""
        seen = {}

        def hs(q, tid, dense_vec=None):
            seen["q"] = q
            seen["dense"] = dense_vec
            return points(3), 0.8
        run(deps=make_deps(hybrid_search=hs),
            provider=FakeProvider(router=ROUTER_SIMPLE))
        self.assertIsNotNone(seen["dense"], "probe vector was not reused")
        self.assertEqual(len(seen["dense"]), 1024)

    def test_folded_followup_must_not_reuse_the_probe_vector(self):
        """A folded follow-up retrieves on DIFFERENT text, so reusing the probe
        embedding would search against the wrong query."""
        seen = {}

        def hs(q, tid, dense_vec=None):
            seen["q"] = q
            seen["dense"] = dense_vec
            return points(3), 0.8
        st, _, b = run(question="yes",
                       history=[{"role": "user", "content": "tell me about roses"}],
                       deps=make_deps(hybrid_search=hs),
                       provider=FakeProvider(router=ROUTER_SIMPLE))
        self.assertEqual(seen["q"], "tell me about roses yes")
        self.assertIsNone(seen["dense"], "reused a vector for different text")
        self.assertEqual(b.counts["retrieval_titan_embeddings"], 1)

    def test_branches_never_reuse_the_probe_vector(self):
        """Each branch retrieves on its own subquestion text."""
        seen = []

        def hs(q, tid, dense_vec=None):
            seen.append((q, dense_vec))
            return points(3), 0.8
        run(deps=make_deps(hybrid_search=hs),
            provider=FakeProvider(router=ROUTER_COMPOUND,
                                  decomposition=DECOMP_THREE))
        self.assertEqual(len(seen), 3)
        for q, dv in seen:
            self.assertIsNone(dv, f"branch {q!r} reused the probe vector")

    def test_no_ambiguous_titan_counter_name_survives(self):
        """A bare `titan_embeddings` key would be read as the request total."""
        self.assertNotIn("titan_embeddings", LIMITS)
        self.assertNotIn("titan_embeddings", RequestBudget().snapshot())


if __name__ == "__main__":
    unittest.main()
