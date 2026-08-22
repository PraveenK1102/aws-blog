"""LangSmith application tracing for the query/RAG flows.

Currently instruments the three user-facing query routes in the ask Lambda:
  * POST /api/ask (and /ask)   — single-profile RAG (root run "ask_request")
  * POST /api/ask/group        — multi-tenant group RAG ("group_ask_request")
  * POST /api/search/global    — LLM-free discovery search ("global_search_request")
Each route creates ONE root run keyed on its existing request_id and a small tree
of child spans over the real operations it performs. CRUD/auth/chat/ingestion
routes are deliberately not traced.

Direct LangSmith SDK usage (RunTree). No LangChain, no LangGraph, no OpenAI SDK.

Three guarantees, by construction:

1. **Feature-controlled.** Tracing is OFF unless env `LANGSMITH_TRACING` is truthy
   AND an API key is resolvable. When off, `get_tracer()` returns a no-op tracer
   and the request path behaves exactly as it did before this module existed —
   no client, no key lookup, no network.

2. **Fail-safe.** Every SDK interaction is wrapped so a tracing failure (import
   missing, network error, bad key, timeout) is swallowed and logged through the
   existing structured logger. Tracing is never on the critical path of a user
   request.

3. **Privacy-safe by default.** The LangSmith Client is created with
   `hide_inputs=True` / `hide_outputs=True`, and this module attaches ONLY
   whitelisted, non-PII scalar metadata (see `_ALLOWED_META`). Raw question,
   answer, system prompt, conversation history, emails, user ids, and secrets
   are never passed to a run's name, inputs, outputs, tags, or metadata.

Correlation: the root run id is the caller's existing `request_id`, so the same
id ties together CloudWatch logs, the DynamoDB usage row, and the LangSmith
trace.

Lambda note: the execution environment freezes after the response is returned,
so the SDK's background batch thread cannot be relied upon to deliver queued
runs. `Span.finish()` (root only) calls `client.flush()` so pending runs are
delivered before the invocation ends. Callers must invoke `finish()` from a
`finally` so it runs on every branch (cache hit, empty corpus, overview,
decline, normal answer, handled LLM failure).
"""

import functools
import os

from common.logger import get_logger
from common.secrets import get_langsmith_key

log = get_logger("tracing")

# LangSmith SDK is imported defensively: if it is absent from the image the
# module still imports and tracing simply stays disabled.
try:
    from langsmith import Client as _Client
    from langsmith.run_trees import RunTree as _RunTree
    _LS_IMPORTABLE = True
except Exception:  # pragma: no cover - only when SDK not installed
    _Client = None
    _RunTree = None
    _LS_IMPORTABLE = False

_TRUTHY = {"1", "true", "yes", "on"}

# Whitelist of metadata keys allowed to leave the process. Anything not listed
# is dropped, so even a careless future edit cannot leak content through the
# metadata channel. All values must be scalars (str/int/float/bool/None).
_ALLOWED_META = {
    "request_id", "environment", "has_history", "model",
    "cache_hit", "citation_count", "top_dense", "floor", "hits",
    "input_tokens", "output_tokens", "result_type", "latency_ms",
    "dims", "reused_query_embedding", "embedded_internally",
    "error_type", "retry_count",
    # group/search flows: non-sensitive counts only (never the ids themselves)
    "target_count", "result_count",
    # generation-failure signal (boolean only; no message/provider body)
    "generation_error",
}


def _clean(md: dict | None) -> dict:
    """Drop non-whitelisted keys and coerce values to safe scalars."""
    if not md:
        return {}
    out = {}
    for k, v in md.items():
        if k not in _ALLOWED_META:
            continue
        out[k] = v if (v is None or isinstance(v, (str, int, float, bool))) else str(v)
    return out


class _Span:
    """A single run node. `_rt is None` means no-op (tracing disabled/failed)."""

    __slots__ = ("_rt", "_client", "_errored")

    def __init__(self, rt=None, client=None):
        self._rt = rt
        self._client = client
        self._errored = None

    # -- context manager: used for child spans (with span.child(...) as s:) ----
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._rt is not None:
                # A propagating exception wins; otherwise preserve an error that
                # record_error() captured from a CAUGHT provider failure.
                err = exc_type.__name__ if exc_type is not None else self._errored
                if err:
                    self._rt.add_metadata({"error_type": err})
                self._rt.end(error=err)
                self._rt.patch()
        except Exception as e:  # tracing must never raise
            log.warning("trace span close failed", error_type=type(e).__name__)
        return False  # never suppress an application exception

    def child(self, name: str, run_type: str = "chain", metadata: dict | None = None) -> "_Span":
        if self._rt is None:
            return _Span()
        try:
            ch = self._rt.create_child(name=name, run_type=run_type, inputs={})
            md = _clean(metadata)
            if md:
                ch.add_metadata(md)
            ch.post()
            return _Span(ch, self._client)
        except Exception as e:
            log.warning("trace child failed", error_type=type(e).__name__)
            return _Span()

    def set(self, **metadata) -> None:
        if self._rt is None:
            return
        try:
            md = _clean(metadata)
            if md:
                self._rt.add_metadata(md)
        except Exception as e:
            log.warning("trace set failed", error_type=type(e).__name__)

    def record_error(self, exc: BaseException) -> None:
        """Mark this span as a genuine LangSmith ERROR (not just metadata).

        Used when the application CATCHES a provider failure and returns its
        fallback response: the exception never propagates through __exit__, so
        without this the run would close as `success` and error telemetry would
        be untrustworthy. Sets RunTree.error to the exception CLASS NAME ONLY —
        never the exception message or the provider response body, which could
        carry request content. `_errored` makes __exit__ preserve the error
        status when the block exits normally.
        """
        if self._rt is None:
            return
        try:
            err = type(exc).__name__
            self._rt.add_metadata({"error_type": err})
            self._rt.error = err          # what makes LangSmith show status=error
            self._errored = err
        except Exception:
            pass

    def finish(self, metadata: dict | None = None) -> None:
        """Root only: attach final metadata, end the run, and flush the client.

        Always safe to call; swallows every error. Flush is what makes tracing
        reliable under Lambda freeze — call this from a `finally`.
        """
        if self._rt is None:
            return
        try:
            md = _clean(metadata)
            if md:
                self._rt.add_metadata(md)
            self._rt.end()
            self._rt.patch()
        except Exception as e:
            log.warning("trace root end failed", error_type=type(e).__name__)
        try:
            if self._client is not None:
                self._client.flush()
        except Exception as e:
            log.warning("langsmith flush failed", error_type=type(e).__name__)


class _Tracer:
    """Enabled tracer: mints request-scoped root spans bound to one Client."""

    __slots__ = ("_client", "_project")

    def __init__(self, client, project: str):
        self._client = client
        self._project = project

    def start_root(self, name: str, run_id: str, metadata: dict | None = None) -> _Span:
        try:
            rt = _RunTree(
                name=name, run_type="chain", id=run_id, inputs={},
                client=self._client, project_name=self._project,
            )
            md = _clean(metadata)
            if md:
                rt.add_metadata(md)
            rt.post()
            return _Span(rt, self._client)
        except Exception as e:
            log.warning("trace root start failed", error_type=type(e).__name__)
            return _Span()


class _NoopTracer:
    """Disabled tracer: every call is a no-op with zero overhead."""

    def start_root(self, name: str, run_id: str, metadata: dict | None = None) -> _Span:
        return _Span()


@functools.lru_cache(maxsize=1)
def get_tracer():
    """Return the process-wide tracer (cached for the container lifetime).

    Returns a no-op tracer unless tracing is explicitly enabled, the SDK is
    importable, and an API key is resolvable — so the default posture is exactly
    today's behaviour.
    """
    if os.environ.get("LANGSMITH_TRACING", "").strip().lower() not in _TRUTHY:
        return _NoopTracer()
    if not _LS_IMPORTABLE:
        log.warning("LANGSMITH_TRACING set but langsmith SDK not importable; tracing disabled")
        return _NoopTracer()
    key = get_langsmith_key()
    if not key:
        log.warning("LANGSMITH_TRACING set but no API key resolvable; tracing disabled")
        return _NoopTracer()

    project = os.environ.get("LANGSMITH_PROJECT", "multitenant-rag-dev")
    endpoint = os.environ.get("LANGSMITH_ENDPOINT")  # optional (e.g. EU region)
    try:
        kwargs = {"api_key": key, "hide_inputs": True, "hide_outputs": True}
        if endpoint:
            kwargs["api_url"] = endpoint
        client = _Client(**kwargs)
        log.info("langsmith tracing enabled", project=project)
        return _Tracer(client, project)
    except Exception as e:
        log.warning("langsmith client init failed; tracing disabled", error_type=type(e).__name__)
        return _NoopTracer()
