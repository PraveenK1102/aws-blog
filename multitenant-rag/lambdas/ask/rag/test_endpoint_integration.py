"""Endpoint-level tests: the feature flag through the REAL FastAPI app (§26).

`app.py` is imported with every external dependency stubbed, so this exercises
the actual routing decision, the actual NDJSON contract and the actual
persistence boundary — with no AWS, Qdrant or Groq.
"""
import json
import os
import sys
import types
import unittest
from unittest import mock

# --- stub the environment app.py requires at import time --------------------
os.environ.setdefault("TENANTS_TABLE", "t-tenants")
os.environ.setdefault("USAGE_TABLE", "t-usage")
os.environ.setdefault("POSTS_TABLE", "t-posts")
os.environ.setdefault("USERS_TABLE", "t-users")
os.environ.setdefault("AWS_REGION", "ap-south-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")

# fastembed downloads a model on construction; app.py only constructs it lazily,
# but the import must still succeed without the wheel's runtime.
if "fastembed" not in sys.modules:
    fe = types.ModuleType("fastembed")
    fe.SparseTextEmbedding = lambda *a, **k: None
    sys.modules["fastembed"] = fe

from fastapi.testclient import TestClient          # noqa: E402

import app as prod                                  # noqa: E402
from rag import scope as S                          # noqa: E402
from rag.conftest_helpers import (                  # noqa: E402
    DECOMP_TWO, ROUTER_COMPOUND, ROUTER_SIMPLE, FakeProvider, points,
)

TENANT = {"tenant_id": "t1", "display_name": "Dave", "domain": "gardening"}


def _events(resp):
    return [json.loads(l) for l in resp.text.strip().splitlines() if l.strip()]


class _Base(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(prod.app)
        self.appended = []
        self.usage = []
        self.stored = []
        self.stack = [
            mock.patch.object(prod, "get_context_from_headers",
                              return_value=("asker-1", "t1", None)),
            mock.patch.object(prod, "_get_tenant", return_value=dict(TENANT)),
            mock.patch.object(prod, "_embed_dense", return_value=[0.01] * 1024),
            mock.patch.object(prod, "_hybrid_search",
                              return_value=(points(3), 0.8)),
            mock.patch.object(prod, "_hybrid_search_multi",
                              return_value=(points(3), 0.8)),
            mock.patch.object(prod, "_tenant_post_titles", return_value=["A"]),
            mock.patch.object(prod, "_log_usage",
                              side_effect=lambda *a, **k: self.usage.append(a)),
            mock.patch.object(prod.semcache, "lookup", return_value=None),
            mock.patch.object(prod.semcache, "store",
                              side_effect=lambda *a, **k: self.stored.append(a)),
            mock.patch.object(prod.chatstore, "append_turn",
                              side_effect=lambda *a, **k: self.appended.append(a)),
        ]
        for p in self.stack:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.stack])
        os.environ.pop("ROUTED_RAG_ENABLED", None)
        self.addCleanup(lambda: os.environ.pop("ROUTED_RAG_ENABLED", None))

    def ask(self, question="Does X do A? And B?", **body):
        payload = {"tenant_id": "t1", "question": question}
        payload.update(body)
        return self.client.post("/api/ask", json=payload)


class FlagOffTests(_Base):
    def test_flag_off_uses_the_existing_streaming_path(self):
        """The old path must still stream token-by-token via llm.stream_answer."""
        def fake_stream(system, user, history=None, model=None):
            yield {"type": "content", "text": "tok1 "}
            yield {"type": "content", "text": "tok2"}
            yield {"type": "usage", "input_tokens": 5, "output_tokens": 2}
        with mock.patch.object(prod, "stream_answer", side_effect=fake_stream) as sa:
            r = self.ask()
        self.assertEqual(r.status_code, 200)
        ev = _events(r)
        content = [e for e in ev if e["type"] == "content"]
        self.assertEqual(len(content), 2, "existing path must stream per token")
        self.assertEqual(ev[-1]["type"], "done")
        sa.assert_called_once()

    def test_flag_off_never_builds_the_routed_graph(self):
        def fake_stream(system, user, history=None, model=None):
            yield {"type": "content", "text": "x"}
            yield {"type": "usage", "input_tokens": 1, "output_tokens": 1}
        with mock.patch.object(prod, "stream_answer", side_effect=fake_stream), \
             mock.patch.object(prod.rag_graph, "run") as gr:
            self.ask()
            gr.assert_not_called()


class FlagOnTests(_Base):
    def test_flag_on_uses_the_routed_graph(self):
        os.environ["ROUTED_RAG_ENABLED"] = "true"
        with FakeProvider(router=ROUTER_COMPOUND, decomposition=DECOMP_TWO), \
             mock.patch.object(prod, "stream_answer") as old_path:
            r = self.ask()
        self.assertEqual(r.status_code, 200)
        ev = _events(r)
        self.assertEqual(ev[0]["type"], "content")
        self.assertEqual(ev[0]["text"], "THE ANSWER")
        self.assertEqual(ev[-1]["type"], "done")
        self.assertTrue(ev[-1]["citations"])
        old_path.assert_not_called()             # old generator never used

    def test_routed_response_keeps_the_same_event_schema(self):
        os.environ["ROUTED_RAG_ENABLED"] = "true"
        with FakeProvider(router=ROUTER_SIMPLE):
            ev = _events(self.ask())
        self.assertTrue(all("type" in e for e in ev))
        done = ev[-1]
        self.assertEqual(set(done), {"type", "citations", "cache_hit"})
        self.assertIsInstance(done["citations"], list)
        self.assertIsInstance(done["cache_hit"], bool)

    def test_routed_path_persists_chat_and_usage_outside_the_graph(self):
        os.environ["ROUTED_RAG_ENABLED"] = "true"
        with FakeProvider(router=ROUTER_SIMPLE):
            self.ask(chat_id="c1")
        self.assertEqual(len(self.appended), 1)
        self.assertEqual(len(self.usage), 1)

    def test_persistence_failure_does_not_break_the_answer(self):
        os.environ["ROUTED_RAG_ENABLED"] = "true"
        with mock.patch.object(prod.chatstore, "append_turn",
                               side_effect=RuntimeError("ddb down")), \
             FakeProvider(router=ROUTER_SIMPLE):
            r = self.ask(chat_id="c1")
        ev = _events(r)
        self.assertEqual(ev[0]["text"], "THE ANSWER")
        self.assertEqual(ev[-1]["type"], "done")

    def test_routed_cache_hit_short_circuits_with_cache_hit_true(self):
        os.environ["ROUTED_RAG_ENABLED"] = "true"
        hit = {"answer": "CACHED", "citations": [{"post_id": "p", "title": "T"}],
               "score": 0.99}
        with mock.patch.object(prod.semcache, "lookup", return_value=hit), \
             FakeProvider() as fp:
            ev = _events(self.ask())
        self.assertEqual(ev[0]["text"], "CACHED")
        self.assertTrue(ev[-1]["cache_hit"])
        self.assertEqual(fp.kinds(), [])          # zero Groq calls

    def test_routed_answer_is_written_to_the_cache(self):
        os.environ["ROUTED_RAG_ENABLED"] = "true"
        with FakeProvider(router=ROUTER_SIMPLE):
            self.ask()
        self.assertEqual(len(self.stored), 1)
        self.assertEqual(self.stored[0][0], "t1")     # per-tenant key

    def test_graph_failure_degrades_to_controlled_error(self):
        os.environ["ROUTED_RAG_ENABLED"] = "true"
        with mock.patch.object(prod.rag_graph, "run",
                               side_effect=RuntimeError("graph exploded")):
            r = self.ask()
        self.assertEqual(r.status_code, 200)
        ev = _events(r)
        self.assertIn("Error while generating", ev[0]["text"])
        self.assertEqual(ev[-1]["citations"], [])

    def test_empty_retrieval_returns_honest_no_content(self):
        os.environ["ROUTED_RAG_ENABLED"] = "true"
        with mock.patch.object(prod, "_hybrid_search",
                               return_value=(points(3), 0.01)), \
             FakeProvider(router=ROUTER_SIMPLE) as fp:
            ev = _events(self.ask())
        self.assertIn("No relevant content", ev[0]["text"])
        self.assertEqual(ev[-1]["citations"], [])
        self.assertNotIn("generation", fp.kinds())


class GroupRouteTests(_Base):
    def _group_ask(self):
        return self.client.post("/api/ask/group",
                                json={"tenant_ids": ["t1", "t2"],
                                      "question": "Q? And B?"})

    def test_group_route_flag_on_uses_routed_graph(self):
        os.environ["ROUTED_RAG_ENABLED"] = "true"
        with FakeProvider(router=ROUTER_COMPOUND, decomposition=DECOMP_TWO), \
             mock.patch.object(prod, "_tenant_name", return_value="W"):
            r = self._group_ask()
        ev = _events(r)
        self.assertEqual(ev[0]["text"], "THE ANSWER")
        self.assertEqual(ev[-1]["type"], "done")

    def test_group_route_does_not_probe_the_semantic_cache(self):
        os.environ["ROUTED_RAG_ENABLED"] = "true"
        with mock.patch.object(prod.semcache, "lookup") as lk, \
             FakeProvider(router=ROUTER_SIMPLE), \
             mock.patch.object(prod, "_tenant_name", return_value="W"):
            self._group_ask()
            lk.assert_not_called()

    def test_group_route_flag_off_unchanged(self):
        def fake_stream(system, user, history=None, model=None):
            yield {"type": "content", "text": "g1 "}
            yield {"type": "usage", "input_tokens": 1, "output_tokens": 1}
        with mock.patch.object(prod, "stream_answer", side_effect=fake_stream), \
             mock.patch.object(prod, "_tenant_name", return_value="W"), \
             mock.patch.object(prod.rag_graph, "run") as gr:
            r = self._group_ask()
            gr.assert_not_called()
        self.assertEqual(_events(r)[0]["text"], "g1 ")


class GlobalSearchUnchangedTests(_Base):
    def test_global_search_never_touches_the_routed_graph(self):
        """§23 — global search stays LLM-free and Qdrant-based."""
        fake = types.SimpleNamespace(points=[])
        with mock.patch.object(prod, "_get_qdrant_client") as qc, \
             mock.patch.object(prod.rag_graph, "run") as gr, \
             mock.patch.object(prod, "stream_answer") as sa:
            qc.return_value.query_points.return_value = fake
            for flag in ("false", "true"):
                os.environ["ROUTED_RAG_ENABLED"] = flag
                r = self.client.post("/api/search/global",
                                     json={"question": "roses"})
                self.assertEqual(r.status_code, 200, flag)
            gr.assert_not_called()
            sa.assert_not_called()


if __name__ == "__main__":
    unittest.main()
