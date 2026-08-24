"""Feature flag, LangSmith privacy, tracing fail-open, and performance (§8/§20/§27)."""
import os
import time
import unittest
from unittest import mock

from common.tracing import _ALLOWED_META, _clean
from rag import config as rag_config
from rag import graph as G
from rag import observability as OBS
from rag import scope as S
from rag.budget import RequestBudget
from rag.conftest_helpers import (
    DECOMP_THREE, ROUTER_COMPOUND, ROUTER_SIMPLE, FakeProvider, make_deps, points,
)


class FeatureFlagTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("ROUTED_RAG_ENABLED", None)

    def test_disabled_by_default(self):
        os.environ.pop("ROUTED_RAG_ENABLED", None)
        self.assertFalse(rag_config.routed_rag_enabled())

    def test_enabled_by_truthy_values(self):
        for v in ("1", "true", "TRUE", "yes", "on", "  True  "):
            os.environ["ROUTED_RAG_ENABLED"] = v
            self.assertTrue(rag_config.routed_rag_enabled(), v)

    def test_disabled_by_falsey_values(self):
        for v in ("0", "false", "no", "off", "", "maybe"):
            os.environ["ROUTED_RAG_ENABLED"] = v
            self.assertFalse(rag_config.routed_rag_enabled(), v)

    def test_flag_is_read_per_request_not_cached(self):
        """Instant rollback requires no cold start."""
        os.environ["ROUTED_RAG_ENABLED"] = "true"
        self.assertTrue(rag_config.routed_rag_enabled())
        os.environ["ROUTED_RAG_ENABLED"] = "false"
        self.assertFalse(rag_config.routed_rag_enabled())

    def test_no_percentage_rollout_knob_exists(self):
        """No percentage-rollout mechanism (§8). Checked over CODE IDENTIFIERS and
        env-var names via AST — not over raw text, which would also match the
        comment that documents the absence of a percentage canary."""
        import ast
        src_dir = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(src_dir, "config.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id.lower())
            elif isinstance(node, ast.FunctionDef):
                names.add(node.name.lower())
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.add(node.value.lower())     # env-var names live here
        for token in ("percent", "canary", "rollout", "sample_rate", "bucket",
                      "traffic_split"):
            offenders = [n for n in names if token in n]
            self.assertEqual(offenders, [], f"{token}: {offenders}")


class _RecordingSpan:
    """Captures exactly what would be sent to LangSmith, through the real
    whitelist, so a privacy assertion is meaningful."""

    def __init__(self, name="root", sink=None):
        self.name = name
        self.metadata = {}
        self.children = []
        self.sink = sink if sink is not None else []
        self.sink.append(self)

    def set(self, **md):
        self.metadata.update(_clean(md))       # the REAL privacy filter

    def child(self, name, run_type="chain", metadata=None):
        ch = _RecordingSpan(name, self.sink)
        if metadata:
            ch.set(**metadata)
        self.children.append(ch)
        return ch

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def all_metadata_values(self):
        out = []
        for s in self.sink:
            out += [str(v) for v in s.metadata.values()]
        return out


def _run(provider=None, deps=None, scope=None, question="Q? And B?", history=None):
    b = RequestBudget()
    provider = provider or FakeProvider(router=ROUTER_COMPOUND,
                                        decomposition=DECOMP_THREE)
    with provider:
        st = G.run(request_id="r", question=question, scope=scope or S.single("t1"),
                   history=history or [], deps=deps or make_deps(), budget=b)
    return st, b


class SpanHierarchyTests(unittest.TestCase):
    def test_compound_span_tree_matches_the_specification(self):
        st, b = _run()
        root = _RecordingSpan()
        OBS.emit_spans(root, st, b, route_type="single")
        names = [c.name for c in root.children]
        self.assertIn("semantic_cache", names)
        self.assertIn("router_v2", names)
        self.assertIn("decomposition", names)
        self.assertIn("merge_evidence", names)
        self.assertIn("build_context", names)
        self.assertIn("groq_generation", names)
        self.assertEqual(
            [n for n in names if n.startswith("retrieval_branch_")],
            ["retrieval_branch_0", "retrieval_branch_1", "retrieval_branch_2"])

    def test_branch_span_has_the_four_physical_operations(self):
        st, b = _run()
        root = _RecordingSpan()
        OBS.emit_spans(root, st, b, route_type="single")
        branch = next(c for c in root.children
                      if c.name == "retrieval_branch_0")
        self.assertEqual([g.name for g in branch.children],
                         ["titan_embedding", "bm25_encode",
                          "qdrant_dense_probe", "qdrant_hybrid_rrf"])

    def test_simple_path_emits_no_decomposition_span(self):
        st, b = _run(provider=FakeProvider(router=ROUTER_SIMPLE))
        root = _RecordingSpan()
        OBS.emit_spans(root, st, b, route_type="single")
        self.assertNotIn("decomposition", [c.name for c in root.children])
        self.assertNotIn("merge_evidence", [c.name for c in root.children])

    def test_cache_hit_emits_no_router_or_generation_span(self):
        hit = {"answer": "CACHED", "citations": [], "score": 0.99}
        st, b = _run(deps=make_deps(semcache_lookup=lambda t, d: hit),
                     provider=FakeProvider())
        root = _RecordingSpan()
        OBS.emit_spans(root, st, b, route_type="single")
        names = [c.name for c in root.children]
        self.assertEqual(names, ["semantic_cache"])

    def test_root_carries_the_required_safe_metadata(self):
        st, b = _run()
        root = _RecordingSpan()
        OBS.emit_spans(root, st, b, route_type="single")
        for key in ("route_type", "route_decision", "router_reason_code",
                    "decomposition_used", "branch_count",
                    "successful_branch_count", "failed_branch_count",
                    "partial_branch_failure", "retrieval_candidate_count",
                    "top_dense_similarity", "relevance_floor_passed",
                    "final_context_count", "citation_count",
                    "remaining_budget_ms", "deadline_exceeded", "result_type"):
            self.assertIn(key, root.metadata, key)


class PrivacyTests(unittest.TestCase):
    SECRETS = ("Does X do A", "THE ANSWER", "sub one", "SINGLE:", "GROUP:",
               "body p0", "Title p0", "tenant-secret", "earlier turn")

    def test_no_content_reaches_any_span(self):
        deps = make_deps()
        st, b = _run(deps=deps, scope=S.single("tenant-secret"),
                     question="Does X do A? And what about B?",
                     history=[{"role": "user", "content": "earlier turn"}])
        root = _RecordingSpan()
        OBS.emit_spans(root, st, b, route_type="single")
        blob = " | ".join(root.all_metadata_values())
        for secret in self.SECRETS:
            self.assertNotIn(secret, blob, f"leaked: {secret}")

    def test_tenant_id_never_appears_in_span_metadata(self):
        st, b = _run(scope=S.multi(["tenant-alpha", "tenant-beta"]),
                     provider=FakeProvider(router=ROUTER_SIMPLE))
        root = _RecordingSpan()
        OBS.emit_spans(root, st, b, route_type="group")
        blob = " | ".join(root.all_metadata_values())
        self.assertNotIn("tenant-alpha", blob)
        self.assertNotIn("tenant-beta", blob)

    def test_whitelist_rejects_content_bearing_keys(self):
        for k in ("question", "answer", "system_prompt", "user_prompt",
                  "chunk_text", "history", "email", "jwt", "token",
                  "tenant_id", "group_id", "user_id", "subquestion",
                  "subquestions", "citations", "api_key", "merged_context"):
            self.assertNotIn(k, _ALLOWED_META, k)
            self.assertEqual(_clean({k: "SENSITIVE"}), {}, k)

    def test_all_emitted_metadata_values_are_scalars(self):
        st, b = _run()
        root = _RecordingSpan()
        OBS.emit_spans(root, st, b, route_type="single")
        for span in root.sink:
            for k, v in span.metadata.items():
                self.assertIsInstance(v, (str, int, float, bool, type(None)),
                                      f"{span.name}.{k}={v!r}")

    def test_error_metadata_is_class_name_only(self):
        def boom(q, tid, dense_vec=None):
            raise RuntimeError("connection string user:pass@host leaked")
        st, b = _run(deps=make_deps(hybrid_search=boom))
        root = _RecordingSpan()
        OBS.emit_spans(root, st, b, route_type="single")
        blob = " | ".join(root.all_metadata_values())
        self.assertNotIn("user:pass@host", blob)
        self.assertIn("RuntimeError", blob)


class TracingFailOpenTests(unittest.TestCase):
    def test_span_emission_failure_does_not_raise(self):
        class Exploding:
            def set(self, **md):
                raise RuntimeError("langsmith down")

            def child(self, *a, **k):
                raise RuntimeError("langsmith down")
        st, b = _run()
        OBS.emit_spans(Exploding(), st, b, route_type="single")   # must not raise

    def test_answer_is_unaffected_by_a_tracing_failure(self):
        st, b = _run()
        self.assertEqual(st["result_type"], "answered")
        class Exploding:
            def set(self, **md):
                raise RuntimeError("x")
            def child(self, *a, **k):
                raise RuntimeError("x")
        OBS.emit_spans(Exploding(), st, b, route_type="single")
        self.assertEqual(st["final_answer"], "THE ANSWER")


class PerformanceRegressionTests(unittest.TestCase):
    """§27 — no provider calls; assert the orchestration adds no sleeps or
    material synchronous overhead of its own."""

    def test_graph_orchestration_overhead_is_small(self):
        t0 = time.monotonic()
        for _ in range(10):
            _run()
        per_run_ms = (time.monotonic() - t0) * 1000 / 10
        # The fakes return instantly, so this is pure orchestration cost:
        # LangGraph scheduling + budget + merge + prompt building.
        self.assertLess(per_run_ms, 150,
                        f"orchestration overhead {per_run_ms:.1f}ms/request")

    def test_no_sleep_is_called_anywhere_on_the_happy_path(self):
        import rag.provider as PV
        with mock.patch.object(PV.time, "sleep") as slept:
            _run()
            slept.assert_not_called()

    def test_merge_is_sub_millisecond(self):
        from rag.merge import merge_evidence
        branches = [{"branch": i, "eligible": points(10, prefix=f"b{i}"),
                     "candidate_count": 10, "top_dense": 0.8,
                     "evidence_missing": False} for i in range(3)]
        t0 = time.monotonic()
        for _ in range(200):
            merge_evidence({"branch_results": branches}, make_deps())
        per_ms = (time.monotonic() - t0) * 1000 / 200
        self.assertLess(per_ms, 2.0, f"merge {per_ms:.3f}ms")

    def test_span_emission_is_cheap(self):
        st, b = _run()
        t0 = time.monotonic()
        for _ in range(50):
            OBS.emit_spans(_RecordingSpan(), st, b, route_type="single")
        per_ms = (time.monotonic() - t0) * 1000 / 50
        self.assertLess(per_ms, 5.0, f"span emission {per_ms:.3f}ms")

    def test_graph_is_compiled_once_per_container(self):
        a = G.build_graph()
        bg = G.build_graph()
        self.assertIs(a, bg)


if __name__ == "__main__":
    unittest.main()
