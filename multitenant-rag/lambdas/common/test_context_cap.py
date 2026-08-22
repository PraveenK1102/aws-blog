"""PHASE 16 — tests for MAX_LLM_CONTEXT_CHUNKS (default 5).

Run:
    TENANTS_TABLE=t USAGE_TABLE=u AWS_REGION=ap-south-1 \
    PYTHONPATH=multitenant-rag/lambdas/ask:multitenant-rag/lambdas \
    python -m unittest common.test_context_cap -v

No network, no LLM: retrieval results are fabricated stand-ins and only the
pure context-selection / prompt / citation functions are exercised.
"""
import importlib
import os
import unittest

os.environ.setdefault("TENANTS_TABLE", "t")
os.environ.setdefault("USAGE_TABLE", "u")
os.environ.setdefault("AWS_REGION", "ap-south-1")

import app as prod


class Hit:
    """Minimal stand-in for a Qdrant scored point."""
    def __init__(self, i, score, post=None, text=None):
        self.score = score
        self.payload = {
            "post_id": post or f"post_{i}",
            "title": f"Title {i}",
            "chunk_text": text if text is not None else f"chunk body {i} " * 20,
            "tenant_id": f"tenant_{i}",
            "header_path": f"H{i}",
        }


def hits(n, start=0):
    """n hits with strictly DESCENDING score, so rank order is unambiguous."""
    return [Hit(i, round(1.0 - i * 0.01, 4)) for i in range(start, start + n)]


TENANT = {"display_name": "D", "domain": "x"}


class ConfigTests(unittest.TestCase):
    def test_default_is_five(self):
        self.assertEqual(prod.MAX_LLM_CONTEXT_CHUNKS, 5)

    def test_env_override_is_respected(self):
        os.environ["MAX_LLM_CONTEXT_CHUNKS"] = "3"
        try:
            m = importlib.reload(prod)
            self.assertEqual(m.MAX_LLM_CONTEXT_CHUNKS, 3)
            self.assertEqual(len(m._llm_context(hits(9))), 3)
        finally:
            os.environ.pop("MAX_LLM_CONTEXT_CHUNKS", None)
            importlib.reload(prod)          # restore default for the rest of the suite
        self.assertEqual(prod.MAX_LLM_CONTEXT_CHUNKS, 5)

    def test_top_k_and_floor_unchanged(self):
        self.assertEqual(prod.TOP_K, 5)
        self.assertEqual(prod.RETRIEVAL_FLOOR, 0.15)


class CapTests(unittest.TestCase):
    def test_single_ask_eight_candidates_gives_five(self):
        self.assertEqual(len(prod._llm_context(hits(8))), 5)

    def test_group_ask_ten_candidates_gives_five(self):
        self.assertEqual(len(prod._llm_context(hits(10))), 5)

    def test_multi_twelve_candidates_gives_five(self):
        self.assertEqual(len(prod._llm_context(hits(12))), 5)

    def test_fewer_than_cap_is_not_padded(self):
        self.assertEqual(len(prod._llm_context(hits(3))), 3)
        self.assertEqual(len(prod._llm_context(hits(1))), 1)
        self.assertEqual(len(prod._llm_context([])), 0)

    def test_top_ranked_order_preserved(self):
        src = hits(10)
        ctx = prod._llm_context(src)
        self.assertEqual([h.payload["post_id"] for h in ctx],
                         [h.payload["post_id"] for h in src[:5]])
        self.assertEqual([h.score for h in ctx], sorted([h.score for h in ctx], reverse=True))

    def test_returns_a_copy_not_a_view(self):
        src = hits(10)
        ctx = prod._llm_context(src)
        ctx.append("x")
        self.assertEqual(len(src), 10, "must not mutate the caller's candidate list")


class PromptContainsOnlyCappedChunks(unittest.TestCase):
    def test_single_profile_prompt_has_five_source_blocks(self):
        ctx = prod._llm_context(hits(10))
        p = prod._build_system_prompt(TENANT, ctx)
        # NB: the template itself mentions "[Source: ...]" in its instructions, so
        # assert on which chunk TITLES are present rather than counting that token.
        for i in range(5):
            self.assertIn(f"Title {i}", p, f"capped chunk {i} must be in the prompt")
        for i in range(5, 10):
            self.assertNotIn(f"Title {i}", p, f"chunk {i} is beyond the cap and must NOT reach the prompt")

    def test_group_prompt_has_five_source_blocks(self):
        ctx = prod._llm_context(hits(12))
        p = prod._build_group_system_prompt(ctx)
        self.assertEqual(p.count("[From "), 5)
        self.assertNotIn("Title 9", p)

    def test_overview_decline_prompt_carries_no_chunks(self):
        """The small-model overview/decline path is title-only by design."""
        p = prod._build_profile_prompt(TENANT, ["T1", "T2"])
        self.assertNotIn("chunk body", p)
        self.assertNotIn("[Source:", p)


class CitationConsistencyTests(unittest.TestCase):
    """Invariant: citation-eligible chunks == chunks the model received."""

    def test_single_citations_come_only_from_capped_context(self):
        src = hits(10)
        ctx = prod._llm_context(src)
        cites = prod._dedupe_citations(ctx)
        titles = {c["title"] for c in cites}
        self.assertLessEqual(len(cites), 5)
        self.assertTrue(titles <= {h.payload["title"] for h in ctx})
        self.assertNotIn("Title 8", titles, "must not cite evidence the model never saw")

    def test_group_attributed_citations_come_only_from_capped_context(self):
        src = hits(12)
        ctx = prod._llm_context(src)
        cites = prod._dedupe_citations_attributed(ctx)
        self.assertLessEqual(len(cites), 5)
        for c in cites:
            self.assertIn(c.get("title"), {h.payload["title"] for h in ctx})

    def test_dedupe_collapses_same_post_within_context(self):
        # two chunks of the SAME post inside the cap -> one citation, best score
        src = [Hit(0, 0.9, post="p1"), Hit(1, 0.8, post="p1"), Hit(2, 0.7, post="p2")]
        cites = prod._dedupe_citations(prod._llm_context(src))
        self.assertEqual(len(cites), 2)
        self.assertEqual(cites[0]["score"], 0.9)


class SafeMetadataTests(unittest.TestCase):
    def test_context_token_estimate_is_a_count_only(self):
        est = prod._context_est_tokens(hits(5))
        self.assertIsInstance(est, int)
        self.assertGreater(est, 0)

    def test_new_metadata_keys_are_whitelisted_and_content_free(self):
        from common.tracing import _clean
        out = _clean({"retrieval_candidate_count": 10, "llm_context_chunk_count": 5,
                      "llm_context_estimated_tokens": 2366, "max_llm_context_chunks": 5,
                      # forbidden neighbours must still be dropped
                      "chunk_text": "secret body", "question": "q", "tenant_id": "tenant_x"})
        self.assertEqual(out, {"retrieval_candidate_count": 10, "llm_context_chunk_count": 5,
                               "llm_context_estimated_tokens": 2366, "max_llm_context_chunks": 5})

    def test_global_search_is_unaffected(self):
        """global search is LLM-free: no cap is applied to it anywhere."""
        import inspect
        src = inspect.getsource(prod.global_search_ep)
        self.assertNotIn("_llm_context", src)
        self.assertNotIn("MAX_LLM_CONTEXT_CHUNKS", src)


if __name__ == "__main__":
    unittest.main()
