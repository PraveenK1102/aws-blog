"""Per-request budget + monotonic deadline for the routed RAG path.

Two jobs, deliberately in one object because they are always consulted together:

1. **Hard architectural bounds.** No graph state and no malformed model output
   may cause more provider work than the architecture allows. Every counter is
   checked BEFORE the call, and exhaustion raises `BudgetExceeded` so the caller
   falls back deterministically instead of silently doing more work.

2. **Monotonic deadline.** `time.monotonic()` from request entry (never
   wall-clock, which can jump). No retry, model call, retrieval branch or
   backoff may KNOWINGLY begin if it cannot fit in what remains.

Thread-safe: compound retrieval branches run in parallel threads and all share
one budget, so every mutation is under a lock.
"""
import threading
import time

from . import config


class BudgetExceeded(RuntimeError):
    """A hard architectural bound would be crossed. Caller must fall back."""

    def __init__(self, resource: str, limit: int):
        super().__init__(f"budget exceeded: {resource} limit={limit}")
        self.resource = resource
        self.limit = limit


class DeadlineExceeded(RuntimeError):
    """Not enough remaining request budget to start this operation safely."""

    def __init__(self, operation: str, need_ms: int, remaining_ms: int):
        super().__init__(
            f"deadline: {operation} needs>={need_ms}ms, remaining={remaining_ms}ms")
        self.operation = operation
        self.need_ms = need_ms
        self.remaining_ms = remaining_ms


# Hard architectural bounds (§14). These are CEILINGS, not tuning knobs.
LIMITS = {
    "router_calls": 1,
    "decomposition_calls": 1,
    "generation_calls": 1,
    "groq_logical_calls": 3,          # router + decomposition + generation
    "retrieval_branches": 3,
    # --- Titan (Bedrock) embeddings: THREE separate bounds, deliberately ---
    #
    # READ THIS BEFORE QUOTING A NUMBER. "3 Titan calls" is the RETRIEVAL bound,
    # NOT the per-request total. A request makes at most:
    #
    #     1 semantic-cache probe embedding      (semcache_titan_embeddings)
    #   + 3 retrieval embeddings, one per branch (retrieval_titan_embeddings)
    #   = 4 Titan embeddings per request         (titan_embeddings_total)
    #
    # The maximum of 4 occurs ONLY for a cache-eligible request that probes the
    # cache, misses, routes compound, and decomposes into 3 branches. It was
    # reviewed and ACCEPTED by the architect (2026-08-24) — branches are NOT
    # capped at 2, the probe is NOT moved after the router, and the semantic
    # cache is NOT removed.
    #
    # A simple cache-eligible request costs 1 (the probe vector is REUSED for
    # retrieval, never re-embedded — see spend_retrieval(embed=False)).
    "retrieval_titan_embeddings": 3,
    "semcache_titan_embeddings": 1,
    "titan_embeddings_total": 4,      # enforced, not merely reported
    "qdrant_dense_probes": 3,
    "qdrant_hybrid_queries": 3,
    "qdrant_physical_queries": 6,     # dense probe + hybrid, per logical retrieval
    "final_context_chunks": 5,
    "semcache_queries": 1,
}


class RequestBudget:
    """One instance per routed request. Never shared across requests."""

    def __init__(self, deadline_ms: int | None = None, now: float | None = None):
        self._lock = threading.Lock()
        self._t0 = now if now is not None else time.monotonic()
        self.deadline_ms = (config.REQUEST_DEADLINE_MS if deadline_ms is None
                            else int(deadline_ms))
        self.counts: dict[str, int] = {k: 0 for k in LIMITS}
        # Observability only — never used to make control decisions.
        self.input_tokens = 0
        self.output_tokens = 0
        self.rate_limit_events = 0
        self.retry_count = 0
        self.deadline_exceeded = False

    # ---------------------------------------------------------------- time
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000)

    def remaining_ms(self) -> int:
        return max(0, self.deadline_ms - self.elapsed_ms())

    def expired(self) -> bool:
        return self.remaining_ms() <= 0

    def timeout_for(self, operation: str, ceiling_ms: int,
                    reserve_ms: int | None = None) -> int:
        """Bounded timeout for one external call: min(ceiling, remaining - reserve).

        Raises DeadlineExceeded when what remains cannot fit a useful call, so no
        external operation can ever block past the request deadline.
        """
        reserve = config.TAIL_RESERVE_MS if reserve_ms is None else reserve_ms
        usable = self.remaining_ms() - reserve
        if usable < config.MIN_CALL_HEADROOM_MS:
            with self._lock:
                self.deadline_exceeded = True
            raise DeadlineExceeded(operation, config.MIN_CALL_HEADROOM_MS,
                                   self.remaining_ms())
        return max(config.MIN_CALL_HEADROOM_MS, min(int(ceiling_ms), usable))

    def can_afford_ms(self, need_ms: int, reserve_ms: int | None = None) -> bool:
        """True when `need_ms` still fits. Used to gate a 429 retry."""
        reserve = config.TAIL_RESERVE_MS if reserve_ms is None else reserve_ms
        return (self.remaining_ms() - reserve) >= need_ms

    # -------------------------------------------------------------- counters
    def spend(self, resource: str, n: int = 1) -> int:
        """Consume budget for `resource`. Raises BEFORE the call on exhaustion."""
        with self._lock:
            limit = LIMITS[resource]
            if self.counts[resource] + n > limit:
                raise BudgetExceeded(resource, limit)
            self.counts[resource] += n
            return self.counts[resource]

    def would_exceed(self, resource: str, n: int = 1) -> bool:
        with self._lock:
            return self.counts[resource] + n > LIMITS[resource]

    def spend_groq(self, kind: str) -> None:
        """A Groq call consumes BOTH its specific counter and the shared logical
        counter, so three routers can never masquerade as router+decomp+gen."""
        specific = {"router": "router_calls",
                    "decomposition": "decomposition_calls",
                    "generation": "generation_calls"}[kind]
        # Check both before mutating either, so a rejected call leaves no residue.
        with self._lock:
            for res in (specific, "groq_logical_calls"):
                if self.counts[res] + 1 > LIMITS[res]:
                    raise BudgetExceeded(res, LIMITS[res])
            self.counts[specific] += 1
            self.counts["groq_logical_calls"] += 1

    def spend_retrieval(self, embed: bool = True) -> None:
        """One LOGICAL retrieval = 1 Titan embed + 1 BM25 encode + 2 physical
        Qdrant query_points (dense top-1 probe + hybrid RRF).

        `embed=False` when the caller reuses an already-computed dense vector
        (the existing single-turn path reuses the semantic-cache probe embedding),
        so a reused embedding is never double-counted.
        """
        with self._lock:
            need = {"retrieval_branches": 1,
                    "qdrant_dense_probes": 1, "qdrant_hybrid_queries": 1,
                    "qdrant_physical_queries": 2}
            if embed:
                # A retrieval embedding consumes BOTH the retrieval bound and the
                # per-request total, so the two can never disagree.
                need["retrieval_titan_embeddings"] = 1
                need["titan_embeddings_total"] = 1
            for res, n in need.items():
                if self.counts[res] + n > LIMITS[res]:
                    raise BudgetExceeded(res, LIMITS[res])
            for res, n in need.items():
                self.counts[res] += n

    def spend_semcache_embedding(self) -> None:
        """The semantic-cache probe embedding.

        Consumes its own bound AND the per-request Titan total, so the total is a
        genuinely enforced ceiling rather than a derived report. Checked before
        mutating either counter, so a rejected spend leaves no residue.
        """
        with self._lock:
            for res in ("semcache_titan_embeddings", "titan_embeddings_total"):
                if self.counts[res] + 1 > LIMITS[res]:
                    raise BudgetExceeded(res, LIMITS[res])
            self.counts["semcache_titan_embeddings"] += 1
            self.counts["titan_embeddings_total"] += 1

    def record_tokens(self, tin: int | None, tout: int | None) -> None:
        with self._lock:
            self.input_tokens += int(tin or 0)
            self.output_tokens += int(tout or 0)

    def record_rate_limit(self) -> None:
        with self._lock:
            self.rate_limit_events += 1

    def record_retry(self) -> None:
        with self._lock:
            self.retry_count += 1

    # ------------------------------------------------------------ reporting
    def snapshot(self) -> dict:
        """Safe, content-free metadata. No question, answer, prompt or chunk."""
        with self._lock:
            return {
                "request_deadline_ms": self.deadline_ms,
                "remaining_budget_ms": self.remaining_ms(),
                "deadline_exceeded": self.deadline_exceeded,
                "router_calls": self.counts["router_calls"],
                "decomposition_calls": self.counts["decomposition_calls"],
                "generation_calls": self.counts["generation_calls"],
                "groq_logical_calls": self.counts["groq_logical_calls"],
                "retrieval_branches": self.counts["retrieval_branches"],
                # All three Titan figures are exposed so no reader has to infer
                # the total from the retrieval bound (see LIMITS for why).
                "semcache_titan_embeddings": self.counts["semcache_titan_embeddings"],
                "retrieval_titan_embeddings": self.counts["retrieval_titan_embeddings"],
                "titan_embeddings_total": self.counts["titan_embeddings_total"],
                "qdrant_dense_probes": self.counts["qdrant_dense_probes"],
                "qdrant_hybrid_queries": self.counts["qdrant_hybrid_queries"],
                "qdrant_physical_queries": self.counts["qdrant_physical_queries"],
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.input_tokens + self.output_tokens,
                "rate_limit_events": self.rate_limit_events,
                "retry_count": self.retry_count,
            }
