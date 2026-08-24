# Production Routed-RAG Hardening — Phase 1

**Status: IMPLEMENTED AND TESTED. NOT DEPLOYED. Routed path DISABLED by default.**
**Date:** 2026-08-24
**Provider calls this task:** Groq 0 · Titan 0 · Qdrant data-plane 0 · NVIDIA 0 · RAGAS 0 · DeepEval 0
**AWS mutations:** 0

---

## 1. Objective

Port the validated `rag-routed-langgraph-v2-offline` architecture into the production
Ask codebase and harden it for deployment: bounded budgets, a request deadline inside the
API Gateway limit, explicit external timeouts, a deadline-aware 429 policy, scope-safety
enforcement, a rollback switch, and production observability.

Implementation and testing only. Nothing was deployed, no AWS resource was changed, and
the routed path cannot execute in production until `ROUTED_RAG_ENABLED` is set to true.

---

## 2. Frozen architecture — carried, not redesigned

Nothing in the accepted architecture was altered: Router V2 and its prompt/parser/reason
codes, the decomposition prompt and parser, the graph topology, dynamic `Send` fan-out,
`Semaphore(2)`, the coverage-aware merge, Titan dense + local BM25 sparse, the Qdrant
dense relevance probe and hybrid RRF, `RETRIEVAL_FLOOR`, `TOP_K`,
`MAX_LLM_CONTEXT_CHUNKS=5`, the chunker, and the citation/context invariant.

Router V2's `information_needs` are **not** used for retrieval — a test asserts the branch
queries are the decomposition subquestions and never the router's needs. `case-041` got no
special handling.

Frozen-contract identities are recorded and asserted at graph build time:

| Contract | sha256[:16] |
|---|---|
| `ROUTER_SYS` (Router V2) | `763d12cd82245285` |
| `ANALYZER_SYS` (decomposition) | `ae8185181e88f25f` |
| `GEN_SYS_COMPOUND` (compound generation) | `8c30bb9b064e6784` |

`763d12cd82245285` is the same prompt SHA the holdout-passing router ran under.

---

## 3. Production code structure

New package `multitenant-rag/lambdas/ask/rag/` — production owns production graph logic.
**No module imports anything from `evals/`**, proven by AST analysis of the shipped file
set.

| Module | Responsibility |
|---|---|
| `config.py` | Feature flag, model arrangement, deadline, per-operation timeout ceilings, token ceilings, concurrency, 429 policy |
| `budget.py` | `RequestBudget`: hard architectural bounds + monotonic deadline; thread-safe (parallel branches share it) |
| `scope.py` | Frozen `Scope` value object, branch payloads, parity assertion, fail-closed validation |
| `deps.py` | `RagDeps` — injects the EXISTING production retrieval/prompt/citation functions |
| `state.py` | `RoutedState` TypedDict with `operator.add` / dict-merge reducers |
| `prompts.py` | The three frozen prompts byte-for-byte, their hashes, the reason-code enum, `assert_frozen()` |
| `provider.py` | Deadline-aware bounded non-streaming Groq client + reactive-only 429 policy |
| `router.py` | Router V2 node: frozen prompt, ported parser, fallback-to-simple policy |
| `decomposition.py` | Decomposition node: frozen analyzer, non-raising parser, branch-room cap |
| `retrieval_nodes.py` | Simple retrieval, `Send` payload construction, per-branch retrieval with scope parity |
| `merge.py` | Coverage-aware deterministic merge, capped at 5 |
| `generation.py` | Context/prompt construction from REAL point objects, generation, citation construction |
| `observability.py` | The §20 LangSmith span tree, emitted from measured state |
| `graph.py` | `StateGraph` wiring, conditional edges, `Send` fan-out, `defer=True` fan-in, `run()` |

`app.py` changes are deliberately small: import the package, add the flag branch to
`/api/ask` and `/api/ask/group`, add `_rag_deps()` and `_routed_stream()`, and put explicit
timeouts on the boto3 and Qdrant clients.

---

## 4. Feature flag

`ROUTED_RAG_ENABLED`, **default `false`**.

* `false` — the existing production RAG path runs exactly as today. The routed graph is
  never built and `rag_graph.run` is never called (asserted by a test).
* `true` — the routed LangGraph path runs.

Read **per request**, not cached at import, so flipping the Lambda env var takes effect on
the next invocation with no redeploy and no cold start — that is what makes rollback
instant. This is a deploy/rollback switch only: a test proves no percentage/canary/bucket
mechanism exists (checked over AST identifiers and env-var names, not raw text).

---

## 5. Request lifecycle

```
HTTP (owned by FastAPI/Lambda — §6)
  JWT validation -> request validation -> tenant authorization -> Scope construction
    |
    v  routed graph (only if ROUTED_RAG_ENABLED)
  resolve_scope -> load_history -> fold_followup -> semantic_cache_check
      |- hit  -> END
      `- miss -> route_question (Groq 20B)
                   |- simple   -> normal_retrieve ------------.
                   `- compound -> decompose (Groq 20B)         |
                                    |- unusable -> normal_retrieve
                                    `- usable   -> Send fan-out
                                                     retrieve_branch xN (Semaphore(2))
                                                   -> merge_evidence (defer=True)
                                                                       |
                   .--------------------------------------------------`
                   v
                build_context -> generate (Groq 120B) -> finalize -> END
    |
    v  HTTP again
  NDJSON assembly -> semantic-cache write -> chat append -> usage row
```

**Boundary decision (§10 asked for this explicitly).** Persistence — semantic-cache write,
`chatstore.append_turn`, and the DynamoDB usage row — is deliberately **outside** the
graph, in `_routed_stream`, so a persistence failure cannot invalidate an answer the user
already received. A test asserts a `chatstore` exception still yields the full answer and
a well-formed `done` event.

**Response-granularity consequence, stated plainly.** Routed generation is a single
non-streaming Groq call, so the answer is emitted as **one** `content` event rather than
per-token events. The event *schema* is unchanged (`content`* then one `done` with
`citations` and `cache_hit`). This is not user-visible in the deployed configuration
because the Lambda Web Adapter runs in **buffered** mode
(`AWS_LWA_INVOKE_MODE=buffered`): LWA collects the whole `StreamingResponse` into one HTTP
response before the client sees any of it, and the frontend typewriter-animates what it
received. Flagged here rather than hidden; it is listed again in §26.

---

## 6. LangGraph state

`RoutedState` (`state.py`) declares **every** key the graph reads. This matters: LangGraph
strips keys absent from the schema, and in the offline v1 experiment that silently emptied
the tenant scope. `scope` is therefore declared explicitly and carried as an immutable
`Scope`.

Reducers: `branch_results` uses `operator.add` (the fan-in accumulator), `node_latencies`
and `tokens` use dict-merge, `errors` uses `operator.add` and holds **exception class
names only** — never messages.

---

## 7. Router contract

Model Groq `openai/gpt-oss-20b`, temperature 0.0, `max_tokens` 1500, frozen prompt.

Expected JSON: `needs_decomposition` (bool), `information_needs` (list[str], ≥2 when
true), `reason_code` (closed 5-value enum, with `multiple_independent_retrieval_needs` the
only code allowed with `true`). The parser is a byte-for-byte port including every
`parse_error` string and both enum/flag consistency rules.

**One deliberate production hardening, flagged for review.** The graph routes compound
only when `parse_ok` is **true** *and* `needs_decomposition` is true. The parser can return
`needs_decomposition=True` together with `parse_ok=False` — for example
`compound_flag_with_simple_reason_code`, or fewer than two `information_needs`. The offline
graph did not gate on `parse_ok` because every holdout case parsed cleanly, so this error
path was never exercised there. Trusting a verdict that violated its own schema would
spend a decomposition call and up to three retrieval branches on invalid output, so
production gates it and falls back to the simple path. **No holdout metric changes**,
because no holdout case took this path.

---

## 8. Decomposition contract

Model Groq `openai/gpt-oss-20b`, `max_tokens` 700, frozen analyzer prompt, user turn
prefixed `"Question: "` (the accepted v2 form). Expected JSON: `is_compound` (bool),
`subquestions` (2–3 atomic needs). Capped at 3 and further capped by remaining branch
budget, so a malformed output cannot fan out more work than the budget allows.

`< 2` usable subquestions → **unusable** → normal retrieval. A single subquestion never
fans out one degenerate branch (asserted).

---

## 9. Scope security

Authorization happens in the HTTP layer **before** the graph starts. The graph carries a
frozen `Scope` and enforces it positively rather than by convention:

* `Scope` is a frozen dataclass with a tuple of tenant ids — it cannot be mutated in place.
* Construction validates: unknown kind, empty tenant set, blank id, or a `single` scope
  inconsistent with its `tenant_ids` all raise `ScopeError` → **fail closed**, never a
  wider search.
* Every `Send` payload is built by `Scope.for_branch()` and carries the parent scope
  alongside it; `retrieve_branch` rebuilds both and calls `assert_parity()` **before**
  retrieving. A tampered/widened branch payload is rejected as `ScopeError`.
* Trace metadata exposes `scope_kind` and `scope_tenant_count` only — never tenant ids.

Tested: single tenant, multi-tenant allowed set, group scope, branch parity across 3
branches, fallback-path parity, widened-payload rejection, empty scope, immutability,
history-cannot-alter-scope, graph-without-scope, and no scope widening during partial
failure.

---

## 10. Retrieval

Unchanged semantics via `RagDeps` → the existing `_hybrid_search` /
`_hybrid_search_multi`. One logical retrieval is 1 Titan query embedding + 1 local BM25
query encoding + 1 Qdrant dense top-1 relevance probe + 1 Qdrant hybrid RRF query. No new
retriever, no prefetch or `TOP_K` change. Relevance is gated on the **absolute dense
cosine**, never the RRF fused score. Branch concurrency `Semaphore(2)`.

---

## 11. Merge

The validated coverage-aware merge: dedupe on `(post_id, chunk_text[:80])`; pass 1 takes
the best unseen chunk from **each** branch so every sub-question is represented; pass 2
round-robins the next ranked chunk per branch; capped at
`min(MAX_LLM_CONTEXT_CHUNKS, LIMITS["final_context_chunks"])` = 5. It **never** compares
RRF scores across independent branch searches — those are per-branch rank numbers and the
top hit of every branch is ≈1.0.

---

## 12. Context / citation invariant

`state["merged_context"]` is the single capped list. `build_context` renders the prompt
from it; `build_citations` derives citations from it. There is no path that shows the model
a chunk which is not citation-eligible, or cites a chunk the model never received. The cap
is **asserted**, not assumed — `build_context` raises `BudgetExceeded` if the list ever
exceeds 5. Refusals suppress citations entirely.

Tested: cap ≤5 with 40 candidates × 3 branches, citations ⊆ visible chunks, every visible
post citation-eligible, prompt text contains exactly the merged chunks, refusal → no
citations, empty retrieval → no citations and **no generation call**.

---

## 13. Model configuration

| Stage | Model | Temp | max_tokens |
|---|---|---|---|
| Router V2 | Groq `openai/gpt-oss-20b` | 0.0 | 1500 |
| Decomposition | Groq `openai/gpt-oss-20b` | 0.0 | 700 |
| Final generation | Groq `openai/gpt-oss-120b` | 0.3 | 1024 |

No NVIDIA, no second provider, no paid tier, no provider fallback. Generation temperature
0.3 matches the existing production path.

---

## 14. Request budget

`RequestBudget` — one per request, thread-safe, checked **before** every call so exhaustion
never happens mid-flight.

| Resource | Bound | Compound worst case |
|---|---|---|
| Router calls | 1 | 1 |
| Decomposition calls | 1 | 1 |
| Generation calls | 1 | 1 |
| Groq logical calls | 3 | 3 |
| Retrieval branches | 3 | 3 |
| Titan embeddings (retrieval) | 3 | 3 |
| Qdrant dense probes | 3 | 3 |
| Qdrant hybrid queries | 3 | 3 |
| Qdrant physical `query_points` | 6 | 6 |
| Final context chunks | 5 | 5 |

A Groq call consumes both its specific counter **and** the shared logical counter, so three
routers cannot masquerade as router+decomposition+generation. A rejected spend leaves no
residue. Exhaustion raises `BudgetExceeded` and the caller falls back deterministically; a
pre-exhausted generation budget yields a controlled `generation_error`, not a crash.

**One accounting decision, flagged in §26:** the semantic-cache probe is also a Titan
embedding. It is counted under a separate `semcache_embeddings` bound (1) so the
architectural retrieval bound of 3 stays exactly enforceable. Worst case per request is
therefore **1 probe embed + 3 branch embeds = 4 Titan calls**, which exceeds a literal
reading of "Titan embeddings ≤ 3". Reused embeddings are never double-counted
(`spend_retrieval(embed=False)`).

---

## 15. Deadline design

`REQUEST_DEADLINE_MS = 24000`, tracked from entry with `time.monotonic()` (never
wall-clock, which can jump). API Gateway `TimeoutInMillis` is verified at 30,000 ms, so
24 s leaves 6 s for cold start, LWA buffering, response assembly and network.

`timeout_for(op, ceiling)` returns `min(ceiling, remaining - TAIL_RESERVE_MS)` and raises
`DeadlineExceeded` when what remains cannot fit a useful call
(`MIN_CALL_HEADROOM_MS = 1200`). `TAIL_RESERVE_MS = 1500` keeps room for citations, the
cache write and NDJSON assembly, so the last provider call cannot consume the whole
deadline. A retrieval branch that cannot finish is **not started**.

Exposed safely: `request_deadline_ms`, `remaining_budget_ms`, `deadline_exceeded`.

---

## 16. External timeouts

The audit found Titan on boto3 defaults, Qdrant with no timeout, and Groq at 60 s — all
incompatible with a 24 s deadline.

| Dependency | Ceiling | Also clamped to remaining budget |
|---|---|---|
| Groq router | 6,000 ms | yes |
| Groq decomposition | 6,000 ms | yes |
| Groq generation | 12,000 ms | yes |
| Bedrock/Titan + DynamoDB | connect 3 s / read 8 s, 2 attempts (`botocore.Config`) | client-level ceiling |
| Qdrant | 8 s client timeout | client-level ceiling |

The boto3 and Qdrant ceilings apply to **both** paths (they were unbounded before); the
per-call clamp to `remaining_ms - reserve` applies to the routed path. No unrelated
AWS-wide configuration was touched. A test asserts the timeout actually sent to
`requests.post` is ≤ the remaining budget even when the ceiling is far larger.

---

## 17. 429 behaviour

Production **never paces proactively.** The 7 s / token-aware experiment pacing is
deliberately absent from the graph; free-tier TPM is an environment limit, not graph logic.
A test asserts `time.sleep` is never called on the happy path.

* **Router** failure / timeout / unretryable 429 → fall back to normal RAG and still
  answer. Routing is an optimisation; its failure must not cost the user their answer.
* **Decomposition** failure / timeout / unusable output / 429 → fall back to normal
  retrieval.
* **Generation** failure → the existing controlled contract
  (`provider_unavailable` / `generation_error`), never long rate-limit sleeps.

A 429 retry is permitted only when **all** hold: the provider's own suggested wait ≤
`MAX_RATE_LIMIT_WAIT_MS` (2,000 ms); attempts remain (`MAX_RATE_LIMIT_RETRIES` = 1); and
`can_afford_ms(wait + headroom)` is true. After the sleep the timeout is re-derived because
the sleep consumed real budget. Tested: a 45 s hint is never retried and never slept on; a
0.4 s hint is retried exactly once; a 1.5 s hint is refused when the budget cannot afford
wait+call.

---

## 18. Partial branch failure

A branch failure is isolated to that branch. If any branch produced usable evidence, merge
and generation proceed. Recorded: `partial_branch_failure`, `failed_branch_count`,
`successful_branch_count`, `branch_count`.

* 1 of 3 branches fails → 2 successful, `partial_branch_failure=true`, answer produced.
* 2 of 3 fail → answers from the survivor.
* **All** branches fail → `merged_context` empty, `result_type=empty_context`, **no
  generation call**, and `partial_branch_failure=false` (total, not partial). The HTTP
  layer returns the honest no-content answer.
* Scope never widens during any fallback (asserted).

---

## 19. Semantic cache

Position: `semantic_cache_check` runs **before** `route_question`, so a hit spends **zero**
Groq calls — asserted directly (`provider.kinds() == []` and `router_calls == 0`).

Route eligibility, taken from the current code rather than invented:

| Route | Cached? | Why (existing code) |
|---|---|---|
| `/api/ask` single-turn | **yes** | `if not history:` gates the probe; per-tenant filter inside `semcache.lookup`; cosine ≥ 0.95; 24 h TTL; invalidated on tenant write |
| `/api/ask` with history | no | a cached answer ignores conversation context and would be wrong for follow-ups |
| `/api/ask/group` | no | cross-tenant by design; the existing endpoint docstring states "No semantic cache (cross-tenant)" |
| `/api/search/global` | no | LLM-free discovery search |

Semantics were not expanded. The cache **write** stays outside the graph and only for
`result_type == "answered"`, single route, no history — preserving the existing guard that
refusals and empty answers are never cached. A cache or embed failure degrades to a normal
query and never fails the request.

---

## 20. Chat history

Unchanged: the same last-8-turn window, the same fold rule (≤ 4 words **and** a prior user
turn exists), folded into the **retrieval query only** — `state["question"]` is never
rewritten. Conversation memory was not expanded. Malformed entries are dropped. A test
asserts history cannot alter tenant scope even when the history text names another tenant.

---

## 21. LangSmith spans

Hierarchy, into `multitenant-rag-prod` for future deployed requests (no development trace
was sent there during this task):

```
ask_request / group_ask_request
├── semantic_cache                (when eligible)
├── router_v2
├── decomposition                 (conditional)
├── retrieval_branch_N            (one per branch)
│   ├── titan_embedding
│   ├── bm25_encode
│   ├── qdrant_dense_probe
│   └── qdrant_hybrid_rrf
├── merge_evidence                (compound only)
├── build_context
└── groq_generation
```

**Why spans are emitted after the graph, not during it.** Compound branches run in parallel
threads and `RunTree.create_child()` is not documented thread-safe; putting an unproven
concurrency assumption on the request path for telemetry is the wrong trade. The graph
records real measured per-node durations in `node_latencies` and per-branch facts in
`branch_results`, and `observability.emit_spans` replays them into the tree once the answer
is already produced. Durations are therefore measured, not guessed, and tracing cannot
slow or break generation. Emission is wrapped so any tracing failure is swallowed —
asserted with a span object that raises on every call.

A cache hit emits only `semantic_cache`; the simple path emits no `decomposition` or
`merge_evidence` span.

---

## 22. Privacy

The existing boundary is unweakened: `hide_inputs=True` / `hide_outputs=True`, whitelist
enforcement in `_clean`, fail-open tracing.

`_ALLOWED_META` gained only counts, booleans, enums, timings and token totals (§21's list).
Deliberately absent and rejected by the whitelist: question, answer, any prompt, retrieved
chunk text, sub-question text, history, tenant ids, group ids, emails, user ids, JWTs,
secrets.

Tested: no question/answer/prompt/chunk/sub-question/tenant substring appears in any
emitted metadata; tenant ids never appear even on the group route; every content-bearing
key is rejected individually; all emitted values are scalars; and an exception message
containing a fake connection string is reduced to the class name only.

---

## 23. Dependency boundary

* `langgraph==1.2.11` pinned in `lambdas/ask/requirements.txt` — the exact validated
  version.
* `langchain-core` is a **LangGraph transitive dependency only**. No application module
  imports a LangChain API; an AST test over the shipped file set forbids any
  `import langchain*` and asserts `langgraph` is imported by `graph.py` alone.
* RAGAS and DeepEval are absent from all three Lambda `requirements.txt` files and are
  imported nowhere in shipped code.
* No shipped module imports any eval module, and the Dockerfile never copies `evals/`.
* No `rag/` module imports `app` at module scope — production functions arrive through
  `RagDeps`, which also keeps every node testable without AWS.

---

## 24. Test coverage

**140 new tests, all passing. 39 pre-existing tests still passing (verified identical at
HEAD). Total 179.**

| File | Tests | Covers |
|---|---|---|
| `rag/test_frozen_parity.py` | 14 | Hash lock, drift detection, reason-code enum, schemas, byte-identity cross-check against the frozen eval modules |
| `rag/test_graph.py` | 47 | Routing, router/decomposition fallbacks, `Send` payloads, 2- and 3-branch merge, scope safety, semantic cache, context/citation invariant, prompt selection, chat history |
| `rag/test_budget_deadline.py` | 32 | Every hard bound, budget exhaustion, deadline clamping, 429 policy against a stubbed transport, partial branch failure |
| `rag/test_dependency_boundary.py` | 11 | No eval imports, no LangChain application import, no RAGAS/DeepEval, pinned LangGraph, shipped file set |
| `rag/test_flag_and_observability.py` | 22 | Flag on/off/per-request, no rollout knob, span hierarchy, privacy, tracing fail-open, performance regression |
| `rag/test_endpoint_integration.py` | 14 | Flag off → old streaming path untouched; flag on → routed path through the real FastAPI app; group route; global search unchanged |

Every §26 item is covered. Performance (§27, no provider calls): orchestration < 150 ms per
request with instant fakes, merge < 2 ms over 200 iterations, span emission < 5 ms,
`time.sleep` never called on the happy path, graph compiled once per container.

Run:

```bash
cd multitenant-rag && PYTHONPATH=lambdas:lambdas/ask .venv/bin/python -m unittest rag.test_frozen_parity rag.test_graph rag.test_budget_deadline rag.test_dependency_boundary rag.test_flag_and_observability rag.test_endpoint_integration
```

---

## 25. Local container verification

Built locally with `docker build -f ask/Dockerfile -t multitenant-ask:routed-local .`
from `multitenant-rag/lambdas`. **Local build only — nothing was pushed to ECR and no
Lambda was updated.**

| Check | Result |
|---|---|
| Image builds | **PASS** — `multitenant-ask:routed-local`, 155,721,141 bytes |
| `langgraph` imports | **PASS** — 1.2.11 |
| Cold import succeeds | **PASS** — `import app` + `rag` package, **901 ms** total |
| FastAPI app imports | **PASS** — 36 routes registered |
| Graph compiles in-image | **PASS** — 14 nodes |
| Frozen prompts assert | **PASS** — `763d12cd82245285` / `ae8185181e88f25f` / `8c30bb9b064e6784` |
| Container starts and serves | **PASS** — uvicorn up, `GET /health` → `{"ok":true}` in 2 s |
| Flag default in-image | **PASS** — `routed_rag_enabled() == False` |
| Models resolved in-image | **PASS** — router/decomp `openai/gpt-oss-20b`, generation `openai/gpt-oss-120b`, deadline 24000 ms, `Semaphore(2)` |
| Production deps resolve | **PASS** |
| Eval-only deps absent | **PASS** — `ragas`, `deepeval`, `langchain_community`, `langchain_openai`, `instructor` all absent |
| Eval source absent | **PASS** — no `nvidia_harness.py` / `decomp_graph.py` / `router_v2.py` / `routed_graph_v2.py` / `groq_provider_obs.py` anywhere in the image |
| LangChain posture | **PASS** — `langchain_core` 1.6.0 present as a LangGraph transitive dep; **top-level `langchain` package absent** |
| Runtime file set | `app.py`, `llm.py`, `common/`, `rag/` (15 production modules), `requirements.txt` |

A `lambdas/.dockerignore` was added so `test_*.py`, `conftest_helpers.py` and
`__pycache__` are not shipped: the tests must live beside the code they test for
`python -m unittest rag.test_graph` to work from the repo, but the runtime image should not
carry them (and `test_endpoint_integration` imports `fastapi.testclient`/`httpx`, which the
image deliberately does not install). Verified: `/var/task/rag` contains exactly the 15
production modules.

**Architecture caveat, stated honestly.** The local Docker host is `colima` on Apple
silicon, so this image is **arm64**. Production is built by CI as **x86_64**. This build
therefore proves dependency resolution, cold import, graph compilation and container
startup — it is **not** the artifact that would be deployed. Step 1 of the deployment plan
builds the x86_64 image in CI.

---

## 26. Remaining production gaps

1. **Titan calls can reach 4 per request** (1 semantic-cache probe + 3 branch embeds),
   above a literal reading of the ≤3 bound. Counted separately and observable; needs an
   architect ruling — see the DECISION REQUIRED in the response.
2. **Response granularity on the routed path** is one `content` event, not per-token
   (§5). Invisible under LWA `buffered` mode, but it is a real difference from the
   streaming path.
3. **Empty-retrieval behaviour differs by design.** The routed path returns an honest
   no-content answer; it does not reimplement the non-routed path's profile-overview /
   decline LLM call. That behaviour remains available whenever the flag is off.
4. **Ingestion DLQ is still absent** — `RedrivePolicy` is NOT set on
   `multitenant-ingestion.fifo`. A poison message retries for the 4-day retention (~1,150
   attempts) and blocks its tenant's FIFO group. **This is the next ingestion P0** and was
   explicitly out of scope here.
5. **Prompt-injection surface** — retrieved user content is still interpolated into the
   system prompt, and decomposition widens the blast radius (more tenants' chunks per
   answer). Pre-existing, unrelated to routing.
6. **No load/concurrency testing.** All validation was single-request.
7. **Free-plan TPM ≈ 8,000** with generation averaging ~2,401 tokens/call ⇒ roughly 3
   requests/minute; a compound request costs more. Recorded, not solved: no DynamoDB/Redis
   quota coordination, no admission control, no sleeps (§28).
8. **Local build architecture** — see §25/Build result.
9. **`case-041`** remains LOW PRIORITY / BACKLOG with no code change.

---

## 27. Deployment plan (for the NEXT architect-approved task — NOT executed here)

1. Build and tag the final **x86_64** image in CI from the merged `main` commit.
2. Push to ECR (`multitenant-ask:<sha>`), record the immutable digest.
3. Deploy that digest to the `multitenant-ask` Lambda.
4. Set `ROUTED_RAG_ENABLED=false` in the same env update (explicitly, not by omission).
5. Verify the **old** path: cold start clean, one authenticated single ask, one group ask,
   CloudWatch shows no `routed=true`, LangSmith shows the existing span shape.
6. Set `ROUTED_RAG_ENABLED=true`.
7. User performs the manual authenticated UI smoke in §29.
8. Inspect CloudWatch (`relevance` lines with `routed=true`, `answer_path`, `branch_count`,
   `remaining_budget_ms`) and LangSmith (`multitenant-rag-prod`, the §21 hierarchy).
9. If broken → set `ROUTED_RAG_ENABLED=false` (takes effect on the next invocation, no
   redeploy). If the image itself is bad → redeploy the previous digest `d5af30e…`.
10. If healthy → leave routed RAG enabled.

No percentage rollout. Recommended env additions at step 3: `ROUTED_RAG_ENABLED=false`,
and optionally `REQUEST_DEADLINE_MS`, `AWS_CONNECT_TIMEOUT_S`, `AWS_READ_TIMEOUT_S`,
`QDRANT_TIMEOUT_S` if the defaults need tuning.

---

## 28. Rollback

* **Primary:** `ROUTED_RAG_ENABLED=false`. The flag is read per request, so the next
  invocation uses the existing path. No redeploy, no cold start required.
* **Secondary:** redeploy the previous image digest
  `d5af30e7f4cc679b2625d6a623d4a7857b1f8094`.
* **Blast radius while enabled:** only `/api/ask` and `/api/ask/group`. Global search,
  auth, posts, chats, follows, groups and ingestion are untouched.
* The routed path adds no schema change, no new table, no new queue and no new IAM
  permission, so rollback needs no data migration.

---

## 29. Manual smoke checklist (for the user, after enablement)

No test user was created and no password was requested or inspected. Sign in with your own
account in the UI.

| # | Action | Expected response | CloudWatch evidence | LangSmith evidence |
|---|---|---|---|---|
| 1 | **Simple ask** on a profile with posts — a single-topic question | one answer + citations | `relevance` with `routed=true`, `answer_path=simple`, `result_type=answered` | `ask_request` → `semantic_cache`, `router_v2`, `retrieval_branch_0`, `build_context`, `groq_generation`; **no** `decomposition` |
| 2 | **Compound ask** — two genuinely independent needs ("What did X say about A, and what happened with B?") | one answer addressing **both** parts | `answer_path=compound`, `branch_count` 2–3 | `decomposition` and `merge_evidence` present, `retrieval_branch_0..N`, `router_reason_code=multiple_independent_retrieval_needs` |
| 3 | **Repeat #1 verbatim** | same answer, returned faster | `result_type=cache_hit` | only `semantic_cache` with `cache_hit=true`; **no** `router_v2` |
| 4 | **Group ask** (if you have a group) | attributed answer naming writers | `group_ask_request`, `routed=true` | span tree present; **no** `semantic_cache` span |
| 5 | **Citation check** on #2 | every cited post is real and its content actually supports the claim | `citation_count` ≤ `final_context_count` | `final_context_count` ≤ 5 |
| 6 | **Off-topic ask** | the decline line, **no** citations | `result_type` `refused` or `empty_context` | `citations` empty |
| 7 | **Saved chat** — ask, then a short follow-up ("yes") | follow-up answered in context | two turns appended | `has_history=true`; **no** `semantic_cache` probe |
| 8 | **Rollback drill** — set the flag false, repeat #1 | normal answer, per-token render | no `routed=true` line | existing span shape |

Stop and report if: any answer cites a post from a profile you did not ask, a group answer
includes a writer outside the group, latency approaches 30 s, or you see a gateway timeout.

---

## 29b. Credential exposure found during this task — ROTATION REQUIRED

While rebuilding the release archive I found that **`multitenant-rag-current.zip` as committed
and pushed in `60fe1e3` (earlier in this same session) contains `multitenant-rag/local/.env`**,
which holds a **real Qdrant API key** and the **dev JWT signing secret**. The repository
`PraveenK1102/aws-blog` is **PUBLIC**, so both values must be considered disclosed.

**Cause — mine, two compounding mistakes:**
1. The `zip -x` exclusion list covered `.venv`, `__pycache__`, `node_modules`, `.git`, `output`
   and caches, but **not `.env`**. The file is correctly gitignored, so `git status` never showed
   it — the archive bypassed that protection entirely.
2. The ad-hoc secret scan reported **"SECRET SCAN CLEAN"** because its credential-assignment
   pattern required a **quote** after `=` and its other patterns only matched known key prefixes
   (`gsk_`, `nvapi-`, `lsv2_`, `sk-`, `AKIA`, JWT). A `.env` line is unquoted and a Qdrant key
   has no distinctive prefix, so the value was never examined. **That CLEAN report was wrong.**

**Fixed forward in this commit:**
* `.env`, `.env.*`, `*.pem`, `*.key`, `credentials*`, `*.p12`, `id_rsa*`, `*.keystore` are now
  excluded from the archive; `.env.example` (placeholders only) is re-added deliberately.
* Archive absence of both live values verified two independent ways: the pattern scan, and a
  direct match of each real value's first 12 characters against the archive contents.
* `multitenant-rag/tools/secret_scan.py` — a precise scanner that examines **unquoted** bare
  `NAME=VALUE` lines in **any** file type, plus quoted literals in source, using entropy and a
  placeholder/safe-name allowlist. Self-tested against the exact miss.
* `multitenant-rag/tools/build_release_archive.sh` — builds and scans in one command and
  **deletes the archive** if anything is found. Verified fail-closed with a planted secret.
* Building that fail-closed check exposed a **second** scanner gap (an unquoted secret in a
  `.txt` file was skipped because only config-style extensions got the unquoted rule). Fixed:
  the unquoted rule now applies to every file, while code expressions
  (`os.environ.get(...)`) are correctly ignored.

**What I did NOT do:** rewrite history or force-push — both are explicitly forbidden. The values
therefore **remain in the public git history at `60fe1e3`**, and rotation is the only effective
mitigation.

### USER ACTION REQUIRED — ROTATE TWO CREDENTIALS

1. **Qdrant Cloud API key** — create a new key, update the `multitenant/qdrant` secret in AWS
   Secrets Manager and your local `multitenant-rag/local/.env`, then **revoke the old key**.
2. **Dev JWT signing secret** (`JWT_SECRET` in `local/.env`) — replace it. Local dev only;
   production signs with Secrets Manager `multitenant/jwt`, which was **not** in the archive and
   is unaffected. Rotating invalidates existing local dev tokens, which is expected.

I did not rotate anything myself: both are outward-facing credential operations, and I never
print or handle secret values.

If you also want the values scrubbed from git history, that is a **history rewrite + force push**
and needs your explicit approval as a separate task. Rotation makes the disclosed values useless
and is the higher-value step regardless.

---

## 30. Production status

**UNCHANGED. NOT DEPLOYED.**

```
multitenant-ask  LastModified 2026-08-22T18:27:50Z   (unchanged)
                 Image d5af30e7f4cc679b2625d6a623d4a7857b1f8094  (unchanged)
                 Timeout 60s  Memory 2048MB  MAX_LLM_CONTEXT_CHUNKS 5
                 GROQ_MODEL openai/gpt-oss-120b  GROQ_MODEL_SMALL openai/gpt-oss-20b
```

No ECR push, no Lambda update, no env change, no AWS mutation of any kind. The routed code
exists in the repository and in a locally built image only, and is disabled by default.
