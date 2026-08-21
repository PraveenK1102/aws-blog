"""Focused tests for the observability helper (common/tracing.py).

Stdlib unittest only — no new application framework. These test the three
guarantees in isolation (no network, no AWS): feature-control, fail-safety, and
the privacy whitelist. Run:

    PYTHONPATH=multitenant-rag/lambdas python -m unittest common.test_tracing -v
"""

import os
import unittest
from unittest import mock

from common import tracing
from common.tracing import _Span, _Tracer, _NoopTracer, _clean, get_tracer


def _fresh_tracer(**env):
    """get_tracer() is lru_cached; clear it and evaluate under a chosen env."""
    get_tracer.cache_clear()
    with mock.patch.dict(os.environ, env, clear=False):
        # ensure the flag reflects exactly what the test sets
        if "LANGSMITH_TRACING" not in env:
            os.environ.pop("LANGSMITH_TRACING", None)
        return get_tracer()


class FeatureControlTests(unittest.TestCase):
    def tearDown(self):
        get_tracer.cache_clear()

    def test_disabled_by_default(self):
        get_tracer.cache_clear()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LANGSMITH_TRACING", None)
            self.assertIsInstance(get_tracer(), _NoopTracer)

    def test_disabled_when_flag_falsey(self):
        self.assertIsInstance(_fresh_tracer(LANGSMITH_TRACING="false"), _NoopTracer)
        self.assertIsInstance(_fresh_tracer(LANGSMITH_TRACING="0"), _NoopTracer)

    def test_enabled_but_no_key_is_noop(self):
        # Flag on, but no resolvable key -> graceful no-op (never raises).
        get_tracer.cache_clear()
        with mock.patch.object(tracing, "get_langsmith_key", return_value=None), \
             mock.patch.object(tracing, "_LS_IMPORTABLE", True), \
             mock.patch.dict(os.environ, {"LANGSMITH_TRACING": "true"}, clear=False):
            self.assertIsInstance(get_tracer(), _NoopTracer)

    def test_enabled_but_sdk_missing_is_noop(self):
        get_tracer.cache_clear()
        with mock.patch.object(tracing, "_LS_IMPORTABLE", False), \
             mock.patch.dict(os.environ, {"LANGSMITH_TRACING": "true"}, clear=False):
            self.assertIsInstance(get_tracer(), _NoopTracer)

    def test_noop_span_ops_never_raise(self):
        t = _NoopTracer()
        s = t.start_root("ask_request", run_id="rid", metadata={"request_id": "rid"})
        with s.child("retrieval", run_type="retriever") as c:
            c.set(hits=3, top_dense=0.7)
            with c.child("hybrid_qdrant_search", run_type="retriever") as gc:
                gc.set(hits=3)
        s.set(result_type="answered")
        s.record_error(ValueError("x"))
        s.finish(metadata={"latency_ms": 12})  # must be safe


class PrivacyWhitelistTests(unittest.TestCase):
    def test_clean_drops_non_whitelisted_and_pii(self):
        dirty = {
            # forbidden — must all be dropped
            "question": "what is his salary?",
            "answer": "secret answer text",
            "system_prompt": "You are ...",
            "history": [{"role": "user", "content": "hi"}],
            "email": "a@b.com",
            "user_id": "user_123",
            "display_name": "Jane Doe",
            "api_key": "lsv2_secret",
            "groq_key": "gsk_secret",
            # allowed
            "request_id": "rid-1",
            "top_dense": 0.71,
            "hits": 5,
            "cache_hit": True,
            "result_type": "answered",
            "input_tokens": 100,
            "model": "llama-3.3-70b-versatile",
        }
        clean = _clean(dirty)
        for forbidden in ("question", "answer", "system_prompt", "history",
                          "email", "user_id", "display_name", "api_key", "groq_key"):
            self.assertNotIn(forbidden, clean, f"{forbidden} must never be traced")
        self.assertEqual(clean["request_id"], "rid-1")
        self.assertEqual(clean["hits"], 5)
        self.assertEqual(clean["model"], "llama-3.3-70b-versatile")
        self.assertTrue(clean["cache_hit"])

    def test_clean_coerces_nonscalar_whitelisted_values(self):
        # A whitelisted key with an odd type is coerced to str, never dropped-with-object.
        out = _clean({"result_type": ["a", "b"]})
        self.assertIsInstance(out["result_type"], str)

    def test_clean_empty(self):
        self.assertEqual(_clean(None), {})
        self.assertEqual(_clean({}), {})

    def test_clean_group_flow_keeps_counts_drops_identities(self):
        # Group/global flows: counts are safe; tenant identities/lists are not.
        out = _clean({
            "target_count": 3, "result_count": 12, "hits": 5,   # safe
            "tenant_ids": ["tenant_a", "tenant_b"],              # forbidden
            "targets": ["tenant_a"], "group_id": "grp_1",        # forbidden
        })
        self.assertEqual(out, {"target_count": 3, "result_count": 12, "hits": 5})

    def test_clean_global_search_metadata(self):
        # global_search_request: only safe operational fields survive; raw query,
        # result payloads, snippets, titles, tenant/user ids are all rejected.
        out = _clean({
            "request_id": "rid-9", "environment": "prod", "dims": 1024,
            "hits": 20, "result_count": 12, "latency_ms": 88, "error_type": "TimeoutError",
            # forbidden:
            "query": "who wrote about kubernetes?", "results": [{"post_id": "p1"}],
            "snippet": "a chunk of a post...", "title": "My Post",
            "tenant_id": "tenant_x", "user_id": "user_y", "writer": "Jane",
        })
        self.assertEqual(out, {
            "request_id": "rid-9", "environment": "prod", "dims": 1024,
            "hits": 20, "result_count": 12, "latency_ms": 88, "error_type": "TimeoutError",
        })


class _ExplodingRT:
    """A RunTree stand-in that raises on every method it exposes."""
    def add_metadata(self, *a, **k): raise RuntimeError("boom")
    def create_child(self, *a, **k): raise RuntimeError("boom")
    def end(self, *a, **k): raise RuntimeError("boom")
    def patch(self, *a, **k): raise RuntimeError("boom")
    def post(self, *a, **k): raise RuntimeError("boom")


class _ExplodingClient:
    def flush(self): raise RuntimeError("flush boom")


class FailSafetyTests(unittest.TestCase):
    def test_span_swallows_all_sdk_errors(self):
        s = _Span(_ExplodingRT(), _ExplodingClient())
        # none of these may raise
        s.set(hits=1)
        s.record_error(ValueError("x"))
        child = s.child("retrieval", run_type="retriever")  # create_child explodes -> noop child
        self.assertIsNone(child._rt)
        with s as ctx:  # __exit__ (end/patch explode) must be swallowed
            pass
        s.finish(metadata={"latency_ms": 1})  # end/patch/flush all explode -> swallowed

    def test_exit_does_not_suppress_application_exception(self):
        s = _Span(_ExplodingRT())
        with self.assertRaises(KeyError):
            with s:
                raise KeyError("app error")  # must propagate, not be swallowed

    def test_tracer_start_root_failure_returns_noop_span(self):
        # Real _Tracer but RunTree construction/post explodes -> returns safe span.
        with mock.patch.object(tracing, "_RunTree", side_effect=RuntimeError("ctor boom")):
            t = _Tracer(client=_ExplodingClient(), project="p")
            span = t.start_root("ask_request", run_id="rid")
            self.assertIsNone(span._rt)          # degraded to no-op
            span.set(hits=1)                      # still safe
            span.finish(metadata={"latency_ms": 1})


if __name__ == "__main__":
    unittest.main()
