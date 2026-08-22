"""Focused tests for the observability helper (common/tracing.py).

Stdlib unittest only — no new application framework. These test the three
guarantees in isolation (no network, no AWS): feature-control, fail-safety, and
the privacy whitelist. Run:

    PYTHONPATH=multitenant-rag/lambdas python -m unittest common.test_tracing -v
"""

import json
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


class ErrorSemanticsTests(unittest.TestCase):
    """PHASE 3/4: a CAUGHT provider failure must still mark the span as a LangSmith error,
    exposing only the exception CLASS NAME (never the message/provider body)."""

    class _RT:
        def __init__(self):
            self.error=None; self.meta={}; self.ended=False; self.end_error="__unset__"
        def add_metadata(self,md): self.meta.update(md)
        def end(self,*a,**k): self.ended=True; self.end_error=k.get("error", a[0] if a else None)
        def patch(self): pass
        def post(self): pass

    def test_caught_error_marks_span_error(self):
        rt=self._RT(); s=_Span(rt)
        with s:
            try:
                raise RuntimeError("Groq error 429: {secret provider body}")
            except Exception as e:
                s.record_error(e)          # app catches and falls back
        self.assertEqual(rt.error,"RuntimeError")       # span flagged as error
        self.assertEqual(rt.end_error,"RuntimeError")   # preserved through __exit__
        self.assertEqual(rt.meta.get("error_type"),"RuntimeError")

    def test_success_stays_success(self):
        rt=self._RT(); s=_Span(rt)
        with s:
            pass
        self.assertIsNone(rt.error)
        self.assertIsNone(rt.end_error)
        self.assertNotIn("error_type", rt.meta)

    def test_exception_message_cannot_leak(self):
        rt=self._RT(); s=_Span(rt)
        msg="429 body {\"key\":\"gsk_supersecret\"} question=what is X"
        try:
            raise RuntimeError(msg)
        except Exception as e:
            s.record_error(e)
        blob=json.dumps({"error":rt.error,"meta":rt.meta})
        self.assertNotIn("gsk_supersecret", blob)
        self.assertNotIn("question=", blob)
        self.assertNotIn("429", blob)

    def test_propagating_exception_still_wins(self):
        rt=self._RT(); s=_Span(rt)
        with self.assertRaises(KeyError):
            with s:
                raise KeyError("boom")
        # __exit__ passes the class name to end(error=...) (SDK stores it there)
        self.assertEqual(rt.end_error, "KeyError")
        self.assertEqual(rt.meta.get("error_type"), "KeyError")

    def test_root_generation_error_key_whitelisted(self):
        # the root signal the app sets alongside the fallback response
        self.assertEqual(_clean({"generation_error":True}), {"generation_error":True})

    def test_record_error_is_failopen_on_noop_span(self):
        _Span().record_error(RuntimeError("x"))   # must not raise


if __name__ == "__main__":
    unittest.main()


class _RecordingSpan:
    """Fake span that records everything the app sets, for endpoint-level assertions."""
    def __init__(self, store, name="root"):
        self.store = store; self.name = name
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def child(self, name, run_type="chain", metadata=None):
        self.store.setdefault("children", []).append(name)
        return _RecordingSpan(self.store, name)
    def set(self, **md):
        if self.name == "root":
            self.store.setdefault("root_meta", {}).update(md)
        else:
            self.store.setdefault(f"meta:{self.name}", {}).update(md)
    def record_error(self, exc):
        self.store.setdefault("errors", []).append((self.name, type(exc).__name__))
    def finish(self, metadata=None):
        self.store.setdefault("root_meta", {}).update(metadata or {})


class _RecordingTracer:
    def __init__(self, store): self.store = store
    def start_root(self, name, run_id, metadata=None):
        self.store["root_name"] = name
        self.store.setdefault("root_meta", {}).update(metadata or {})
        return _RecordingSpan(self.store)


class GenerationErrorEndpointTests(unittest.TestCase):
    """PHASE 3/4 at the endpoint level: a caught Groq failure must (a) keep the exact
    NDJSON fallback, (b) report result_type=generation_error, (c) mark the llm span as
    an error, and (d) NOT write the empty answer into the semantic cache."""

    def _run_ask(self, raise_exc):
        import asyncio, json as _json, os as _os
        _os.environ.setdefault("TENANTS_TABLE", "t"); _os.environ.setdefault("USAGE_TABLE", "u")
        import app
        store = {}
        fake_hit = type("R", (), {"payload": {"title": "T", "chunk_text": "c"}, "score": 0.5})()

        def boom(*a, **k):
            if raise_exc: raise RuntimeError("Groq error 429: {\"key\":\"gsk_secret\"} q=leak")
            yield {"type": "content", "text": "real answer"}
            yield {"type": "usage", "input_tokens": 10, "output_tokens": 5}

        with mock.patch.object(app, "tracer", _RecordingTracer(store)), \
             mock.patch.object(app, "get_context_from_headers", return_value=("asker", "tn", None)), \
             mock.patch.object(app, "_get_tenant", return_value={"display_name": "D", "domain": "x"}), \
             mock.patch.object(app, "_embed_dense", return_value=[0.0]*1024), \
             mock.patch.object(app, "_hybrid_search", return_value=([fake_hit], 0.5)), \
             mock.patch.object(app, "_build_system_prompt", return_value="sys"), \
             mock.patch.object(app, "_dedupe_citations", return_value=[{"title": "T"}]), \
             mock.patch.object(app, "_log_usage") as log_usage, \
             mock.patch.object(app, "stream_answer", side_effect=boom), \
             mock.patch.object(app.semcache, "lookup", return_value=None), \
             mock.patch.object(app.semcache, "store") as store_fn:
            req = app.AskRequest(question="q", tenant_id="tn")
            http = type("Rq", (), {"headers": {}})()

            async def _collect():
                resp = await app.ask(req, http)
                out = []
                # Starlette wraps a sync generator in a threadpool async iterator
                async for chunk in resp.body_iterator:
                    out.append(chunk if isinstance(chunk, bytes) else str(chunk).encode())
                return out

            loop = asyncio.new_event_loop()
            try:
                chunks = loop.run_until_complete(_collect())
            finally:
                loop.close()
            events = [_json.loads(l) for l in b"".join(chunks).decode().splitlines() if l.strip()]
        return store, events, log_usage, store_fn

    def test_generation_failure_semantics(self):
        store, events, log_usage, store_fn = self._run_ask(raise_exc=True)
        texts = "".join(e.get("text", "") for e in events if e["type"] == "content")
        done = [e for e in events if e["type"] == "done"]
        # (a) API contract preserved: fallback text + a done event that still carries citations
        self.assertIn("[Error while generating response]", texts)
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["citations"], [{"title": "T"}])
        # (b) root reports the generation error
        self.assertEqual(store["root_meta"].get("result_type"), "generation_error")
        self.assertTrue(store["root_meta"].get("generation_error"))
        self.assertEqual(store["root_meta"].get("error_type"), "RuntimeError")
        # (c) the llm span itself was marked errored
        self.assertIn(("groq_generation", "RuntimeError"), store.get("errors", []))
        # (d) NO cache poisoning: the empty answer must not be stored
        store_fn.assert_not_called()
        # usage log agrees with the trace
        self.assertEqual(log_usage.call_args[0][6], "generation_error")
        # no leak of the provider body / secret anywhere in trace metadata
        blob = json.dumps(store, default=str)
        self.assertNotIn("gsk_secret", blob); self.assertNotIn("q=leak", blob); self.assertNotIn("429", blob)

    def test_successful_generation_unchanged(self):
        store, events, log_usage, store_fn = self._run_ask(raise_exc=False)
        self.assertEqual(store["root_meta"].get("result_type"), "answered")
        self.assertNotIn("generation_error", store["root_meta"])
        self.assertEqual(store.get("errors", []), [])
        store_fn.assert_called_once()          # healthy answers still cached
        self.assertEqual(log_usage.call_args[0][6], "answered")
