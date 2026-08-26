# MultiTenantRAG — Current Implementation State

> Handoff document for the architect. **Code + actual AWS state are the source of truth.**
> Terms are kept distinct: implemented / committed / pushed / deployed / backend-verified.

## 1. Snapshot
- **Git branch:** `main`
- **repo_head (== `origin/main`):** `e28029b` — evaluation tooling only, **not deployed**
- **deployed_ask_sha:** `d5af30e` (with `MAX_LLM_CONTEXT_CHUNKS=5`) — the running production image
- **Date:** 2026-08-22
- **Deployment status:** **DEPLOYED to AWS** (unified `main`). Live at https://d261g450savmee.cloudfront.net.
- **Deployed ask Lambda image:** `multitenant-ask:d5af30e7f4cc679b2625d6a623d4a7857b1f8094` (digest `sha256:2791dfa4059193ad5488402eda3dcc9caa4aa51d91167fee47cfd7e6addb3e48`), verified from AWS 2026-08-23. Env includes `MAX_LLM_CONTEXT_CHUNKS=5`.
- **Rollback reference (previous rev-5 image):** `multitenant-ask:24be4488e93490e3584ee365af8025c3066d4e10`.
- **Synthetic test data (seed-20260822):** 6 profiles + 4 groups + 50 posts (~2,053 Qdrant vectors) seeded into PROD 2026-08-22 for scale/RAG testing; tagged + captured in `SEED-MANIFEST.json` for cleanup. See §15.
- **Not yet done:** authenticated functional smoke + live-LangSmith-trace verification (no smoke creds in the executor — see §13/§16).

State of each work stream:
| Work | Implemented | Committed | Pushed | Deployed (AWS) | Backend-verified |
|---|---|---|---|---|---|
| rev-5 app (single-profile ask, chats, semcache, tiering) | ✅ | ✅ | ✅ | ✅ | ✅ (pre-existing) |
| Social/search (follow, groups, group ask, global search) | ✅ | ✅ | ✅ | ✅ (routes live) | ⏳ pending authed smoke |
| LangSmith tracing — `/api/ask`, `/api/ask/group`, `/api/search/global` | ✅ | ✅ | ✅ | ✅ (enabled at init; secret resolves) | ⏳ pending live trace (no authed query yet) |

`.gitignore` has one unrelated local (uncommitted) modification, preserved throughout.

## 2. Current Architecture
100% serverless.
```
Frontend (React + Vite, blog-frontend/)  →  S3 praveen-blog-frontend
   ↓
CloudFront (EOV3277U5A8CF, d261g450savmee.cloudfront.net)
   ├── /*      → S3 (static site)
   └── /api/*  → API Gateway HTTP API (pdp1o70aug)
                    ↓
   ┌─────────── 3 Lambda container images (ap-south-1) ───────────┐
   │ multitenant-ask (FastAPI + Lambda Web Adapter, buffered)     │  ← deployed d5af30e
   │ multitenant-createpost                                       │  ← unchanged (rev-5)
   │ multitenant-ingestworker (SQS-triggered)                     │  ← unchanged (rev-5)
   └──────────────────────────────────────────────────────────────┘
        ↓            ↓             ↓              ↓
   DynamoDB      S3 (raw md)   Qdrant Cloud    Groq API
   (metadata)   praveen-...    (vectors,       (gpt-oss-120b + 20b)
                -content       external)
        ↑                          ↑
   SQS FIFO (multitenant-ingestion.fifo) ── Bedrock Titan Text V2 (dense embed)

   Observability: CloudWatch (all Lambdas) + LangSmith (ask, project multitenant-rag-prod)
```
- **AWS services:** CloudFront, API Gateway (HTTP API), Lambda (container images), DynamoDB, S3, SQS FIFO, Bedrock (Titan embeddings), Secrets Manager, CloudWatch.
- **Vector DB:** Qdrant Cloud (external). **Embeddings:** Titan Text V2 (dense) + fastembed BM25 (sparse). **LLM:** Groq `openai/gpt-oss-120b` (answers) + `openai/gpt-oss-20b` (small) — switched from Llama 3.x on 2026-08-22 (env-only) because the current Groq account lacks Llama models; see §15. **Auth:** custom JWT (bcrypt). **Cache:** Qdrant semantic cache. **Observability:** CloudWatch (deployed) + LangSmith (deployed; backend delivery pending an authed query).
- History: EC2/ECS/ALB/RDS deleted 2026-07-25. Legacy `blog-backend/`, `blog-frontend-nextjs/` are dead (excluded from the zip).

## 3. AWS Components
| Service | Purpose | Resource | State |
|---|---|---|---|
| CloudFront | CDN + routing (`/*`→S3, `/api/*`→API GW) | `EOV3277U5A8CF` | ✅ deployed; invalidated this task |
| API Gateway (HTTP API) | Sync `/api/*` | `pdp1o70aug` | ✅ |
| Lambda (container) | Query/RAG API | `multitenant-ask` | ✅ **image `d5af30e` deployed (MAX_LLM_CONTEXT_CHUNKS=5)** |
| Lambda (container) | Post create | `multitenant-createpost` | ✅ unchanged |
| Lambda (container) | Async ingest | `multitenant-ingestworker` | ✅ unchanged |
| S3 | Raw markdown | `praveen-multitenant-content` | ✅ |
| S3 | Static frontend | `praveen-blog-frontend` | ✅ new build synced this task |
| DynamoDB | users, tenants, posts, usage-logs (TTL), chats | `multitenant-*` | ✅ |
| DynamoDB | follows | `multitenant-follows` (GSI `by_followee`, KEYS_ONLY) | ✅ **created ACTIVE this task** |
| DynamoDB | groups | `multitenant-groups` | ✅ **created ACTIVE this task** |
| DynamoDB | group members | `multitenant-group-members` (GSI `by_member`, ALL) | ✅ **created ACTIVE this task** |
| SQS FIFO | Ingestion pipeline | `multitenant-ingestion.fifo` | ✅ |
| Bedrock | Dense embeddings | Titan Text V2 | ✅ |
| Secrets Manager | Groq/Qdrant/JWT | `multitenant/groq`,`/qdrant`,`/jwt` | ✅ |
| Secrets Manager | LangSmith key | `multitenant/langsmith` (ARN `…-Y96wek`) | ✅ **present (user-created); non-empty api_key verified; not overwritten** |
| IAM | ask role permissions | role `multitenant-ask-role`: inline `multitenant-ask-inline` (existing) + `multitenant-social-langsmith-access` (**added this task**) + managed `AWSLambdaBasicExecutionRole` | ✅ |
| CloudWatch | Logs/metrics | per-Lambda log groups | ✅ |

External: Qdrant Cloud, Groq, LangSmith (SaaS, project `multitenant-rag-prod`).

## 4. User-Facing Features
| Feature | Route(s) | File | Deployed | Notes |
|---|---|---|---|---|
| Signup / login / me | `/api/auth/*` | `ask/app.py` | ✅ | rev-5 |
| Write / list posts | `POST/GET /api/posts` | `ask/app.py`,`common/posts.py` | ✅ | rev-5 |
| Profile directory / page | `GET /api/users`, `/{user_id}` | `ask/app.py` | ✅ | +is_following/follower_count |
| Ask a profile's AI | `POST /api/ask` (+`/ask`) | `ask/app.py` `ask()` | ✅ | traced |
| Saved chats + memory | `/api/chats*` | `ask/app.py`,`common/chats.py` | ✅ | ≤5/profile |
| Follow / unfollow | `POST/DELETE /api/users/{id}/follow`, `GET /api/me/following` | `common/follows.py` | ✅ live (routes reach Lambda) | functional smoke pending |
| Groups | `/api/groups*`, `/api/discover/groups`, `/subscribe`, `/members*` | `common/groups.py` | ✅ live | functional smoke pending |
| Group ask | `POST /api/ask/group` | `ask/app.py` `ask_group()` | ✅ live | traced; functional smoke pending |
| Global search | `POST /api/search/global` | `ask/app.py` `global_search_ep()` | ✅ live | LLM-free; traced; functional smoke pending |

All new routes verified reachable through CloudFront→API GW→Lambda (401/422 unauth, never 404).

## 5. RAG Pipeline (single-profile `/api/ask`)
Auth/tenant resolution (JWT→context; tenant never trusted from body) → `request_id=uuid4()` (also LangSmith run id + DDB usage key) → conversation memory (chat_id) → **semantic cache** (single-turn, Titan embed once, Qdrant cosine ≥0.95) → **dense (Titan) + sparse (BM25)** → **Qdrant hybrid RRF, pre-filtered by tenant_id**, TOP_K=5 → floor 0.15 (below → 0 posts honest "nothing yet" no-LLM, or one **gpt-oss-20b** overview/decline call) → **Groq gpt-oss-120b** streamed → citations deduped (empty on refusal) → cache store (clean single-turn) → chat append → DDB usage log. Group ask uses `_hybrid_search_multi` (Qdrant MatchAny over selected tenants), attributed prompt/citations, no cache, stateless unless chat_id.

## 5b. Context budget (measured 2026-08-22)
- **`MAX_LLM_CONTEXT_CHUNKS = 5`** (env-configurable, deployed set to 5). All generative paths send
  at most **5 final ranked chunks** to the LLM: single-profile ask, group ask, multi-profile eval path.
  The title-only overview/decline prompt carries **0 chunks**. **Global search is LLM-free — not capped.**
- **Retrieval breadth is UNCHANGED and deliberately separate:** hybrid prefetch is dense 20 + sparse 20
  (single) / dense 30 + sparse 30 (group/multi), RRF-fused to `TOP_K`=5 or `TOP_K*2`=10 candidates.
  Group/multi still *retrieve and rank* 10; only the prompt is capped at 5.
  Before this change the group/multi paths sent all **10** chunks to the model (~4.9k prompt tokens vs
  ~2.9k single).
- **Citation invariant:** the same capped list feeds the prompt and the citations — the system cannot
  cite evidence the model never received.
- **Measured chunk distribution** (2,053 seed chunks, 50 posts, chars/4 estimate): mean **473** est.
  tokens, p50 489, p95 506, max 509.5; **95.9% ≥400**, **86.4% ≥475**, 16% >500. ~41 chunks/post.
  So the chunker packs against its 500 ceiling rather than sitting well below it.
- **Chunk size/overlap/splitter/embeddings UNCHANGED** (max_tokens 500, overlap 50). Verdict recorded
  as "B — likely too large, deserves an experiment"; quality impact unmeasured. See
  `CHUNK-AND-CONTEXT-ANALYSIS.md`.
- **Adaptive 5 → 10 → 15 escalation = FUTURE ONLY**, not implemented (no adaptive top-k, no query
  rewriting, no complexity classifier, no model routing).
- Safe tracing metadata added: `retrieval_candidate_count`, `llm_context_chunk_count`,
  `llm_context_estimated_tokens`, `max_llm_context_chunks` (counts/estimates only, no content).

## 6. Ingestion Pipeline
`POST /api/posts` (sync) → S3 put + DDB put (status=pending) + SQS FIFO enqueue (MessageGroupId=tenant_id) → **SQS-triggered** `ingestworker` (async): S3 read → markdown-aware chunk → Titan dense + BM25 sparse embed → Qdrant delete-then-upsert (idempotent) → DDB status=indexed → semantic-cache invalidation. Not traced (future phase).

## 7. LangSmith

LangSmith is used in **two strictly separate modes**:

### 7A. Production operational tracing — project `multitenant-rag-prod`
REDACTED. `Client(hide_inputs=True, hide_outputs=True)` + a metadata whitelist. Records only
span timing, latency, tokens, errors, cache status, model, request correlation and operational
metadata. **No** raw question/answer/prompt/history/retrieved chunks/user ids/tenant ids/emails/
secrets. `hide_inputs`/`hide_outputs` are NOT disabled anywhere globally.
**Error semantics (fixed 2026-08-22, commit `03fda30`, DEPLOYED):** a *caught* provider failure now
marks the `groq_generation` span as a real LangSmith error (exception CLASS NAME only) and the root
carries `result_type=generation_error` + `generation_error=true`. Previously such runs closed as
`success` with only `error_type` metadata.

### 7B. Offline synthetic evaluation — dataset + experiments (PRIVATE)

**Current experiment: `rag-model-eval-nvidia20b-v1` (offline model evaluation).**
| Role | Provider | Model |
|---|---|---|
| Application under test | NVIDIA | `openai/gpt-oss-20b` |
| Judge | NVIDIA | `openai/gpt-oss-120b` |
| **Production application (UNCHANGED)** | **Groq** | **`openai/gpt-oss-120b`** |

Groq receives **zero** calls during this experiment (actively guarded: the production Groq entry
points are replaced with functions that raise). Retrieval, prompts, TOP_K (5), floor (0.15), Titan
V2 embeddings, BM25 and RRF are the production ones, unchanged — the only intentional difference is
which provider/model generates the answer. Global search stays LLM-free (no generation call).
Feedback keys: `request_success`, `answer_correctness`, `answer_completeness`, `answer_groundedness`
(one structured judge call yields the three quality dimensions). Groundedness is judged against the
**actual retrieved contexts**, not the reference answer. NVIDIA throttling: concurrency 1, 6 s minimum
interval, bounded backoff, 3-consecutive-429 circuit breaker. Durable per-case checkpointing makes the
run resumable without re-spending quota.

**Earlier Groq baseline (`rag-baseline-v1*`) remains INCOMPLETE/ABORTED** — 27/60, halted by the Groq
200k tokens/day free-tier limit; preserved and annotated, never merged into any other result. A clean
Groq-faithful baseline is PAUSED because the user does not want paid inference (projected ~184k Groq
tokens for 60 cases vs a 140k cap). See `EVALUATION-BASELINE.md`.

**Superseded plan (2026-08-22, PAUSED):** a Groq-application / NVIDIA-judge baseline was designed
(application Groq `openai/gpt-oss-120b`, judge NVIDIA `openai/gpt-oss-20b`) but was **never run** —
projected Groq application-only usage ~183,788 tokens exceeded the 140,000 cap (~92% of the 200,000
daily TPD) and the user declined paid inference. **That 20B-judge configuration is NOT active.**
The active offline configuration is the one in §7B above: application NVIDIA `openai/gpt-oss-20b`,
judge NVIDIA `openai/gpt-oss-120b`.

**Shared offline-evaluation facts (both configurations):** architect-approved to store FULL synthetic
content because the corpus is fictional/public test data — question, expected answer, generated answer,
retrieved contexts, citations, evaluator scores/reasons.
- Dataset: `multitenant-rag-eval-60-v1` (60 examples, tag `baseline-v1`, private/workspace-scoped),
  metadata `data_classification=synthetic_public`, `evaluation_type=offline`.
- Experiments write to their own LangSmith projects and never into `multitenant-rag-prod`.
- Harness: `multitenant-rag/evals/` — imports the PRODUCTION RAG functions (no re-implementation),
  app-level tracing disabled, semantic cache bypassed, and it mirrors prod model config by reading the
  deployed Lambda env. Uses the same `MAX_LLM_CONTEXT_CHUNKS=5` policy as production.

**NVIDIA 20B candidate evaluation — COMPLETE (`rag-model-eval-nvidia20b-top5-v1`, 2026-08-23):**
60/60 cases run under the top-5 policy (fingerprint `856b2bf583419c93`; the earlier 3/60 pre-cap run is
superseded and not reused). 52 generative + 8 LLM-free global. **request_success 52/52 = 100%**,
application provider errors/timeouts/429s = 0. Judge scored 52/52 (3 first-pass judge provider errors
re-judged on retry; application generation never re-run — the two-stage design preserved it).
Results: **correctness 0.740 · completeness 0.721 · groundedness 0.798**. Strong on simple factual and
negative/scope cases; **compound multi-part = 0.000 (n=3)**. Only ~5% of failures (1 of 21) had evidence
uniquely in candidates 6–10, and low top-5 source diversity did **not** predict failure — which weakens
the evidential case for adaptive 5→10 and points instead at query decomposition. Application tokens
124,478; judge tokens 100,131 (never combined). See `NVIDIA-20B-TOP5-QUALITY-BASELINE.md`.
Adaptive retrieval remains **NOT implemented**.
### 7C. Legacy detail (still accurate)
- **Implemented: YES** — direct LangSmith SDK (`RunTree`) via `common/tracing.py`. No LangChain.
- **Deployed: YES** — secret `multitenant/langsmith` present; ask role can read it; env `LANGSMITH_TRACING=true`, `LANGSMITH_PROJECT=multitenant-rag-prod`, `ENVIRONMENT=prod`; new image live. CloudWatch at cold start logged `"langsmith tracing enabled" project=multitenant-rag-prod`, which confirms the SDK imported and **the key resolved from Secrets Manager (no AccessDenied)**.
- **Backend-verified: NO — PENDING USER SMOKE QUERY.** No authenticated query has run, so no trace has been delivered to the `multitenant-rag-prod` project yet. Redaction/child-span/correlation assertions are pending that first real query (all three traced routes require a JWT).
- **Traced routes / roots:** `/api/ask`(+`/ask`) → `ask_request`; `/api/ask/group` → `group_ask_request`; `/api/search/global` → `global_search_request`. Run id = each route's `request_id`.
- **Child spans:** ask → `semantic_cache`(→`dense_embedding`), `retrieval`(→`hybrid_qdrant_search`), `groq_generation`, `completion`. group → `retrieval`(→`dense_embedding`,`hybrid_qdrant_search`), `context_preparation`, `groq_generation`, `completion`. global → `retrieval`(→`dense_embedding`,`qdrant_search`), `result_processing`.
- **Privacy:** `Client(hide_inputs=True, hide_outputs=True)` + metadata whitelist (`_ALLOWED_META`). No question/answer/prompt/history/PII/tenant+user ids/secrets. Error = class name only.
- **Fail-open + flush:** every SDK call swallowed+logged; `client.flush()` in a `finally` on every branch (Lambda freeze-safe).
- **CloudWatch relationship:** independent; same `request_id` joins them.

## 8. RAGAS
**Implemented: NO — DEFERRED** (architect decision 2026-08-22). Not installed; not in any
requirements file. A later phase will add faithfulness / answer relevancy / context precision /
context recall over this same synthetic corpus and the actual retrieved contexts, and compare
against this baseline. Note: current `ragas` (0.4.3) declares `langchain`, `langchain-core`,
`langchain-community`, `langchain_openai` as MANDATORY dependencies — the reason it was deferred.

## 8b. DeepEval
**Implemented: NO — DEFERRED** (architect decision 2026-08-22). Not installed. `deepeval` (4.1.10)
has no mandatory langchain dependency, but was deferred together with RAGAS so the first baseline is
LangSmith-native only.

## 8c. NVIDIA (provider)
**Validated and used as the OFFLINE evaluation provider only. Production integration: NOT IMPLEMENTED.** Auth PASS, `GET /v1/models` → 200 with 102 models; exact
accessible IDs `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `meta/llama-3.1-8b-instruct`,
`nvidia/nvidia-nemotron-nano-9b-v2`, `nvidia/nemotron-3-nano-30b-a3b` (all smoke-PASS; streaming
verified compatible with the existing `llm.py` parser). Secret `multitenant/nvidia` exists in
ap-south-1 with **no** `multitenant-ask-role` IAM grant — production cannot read it. Used **only** as
the offline evaluation judge. Production provider remains Groq. See `NVIDIA-MODEL-INVENTORY.md`.
Free-tier endpoint exposes no rate-limit headers; a successful offline run proves compatibility, not
production throughput/SLA/quota.

## 9. LangChain
**NOT IMPLEMENTED AS AN APPLICATION ABSTRACTION.**

- No LangChain anywhere in the request path. The Groq client is a hand-rolled `requests.post` with SSE
  parsing (`ask/llm.py`); retrieval, prompting and citation assembly are all direct code.
- `langchain-core` 1.6.0 **is** present in the local **eval** virtualenv, but **only as a transitive
  dependency of `langgraph`** (§9b). It is not imported by any application or eval module we wrote, and it
  is **not installed in any Lambda image**. This is a dependency-graph artifact, not application-level
  LangChain usage.
- `ragas` (0.4.3) would pull `langchain` / `langchain-core` / `langchain-community` / `langchain_openai` as
  mandatory deps — one reason it stays deferred (§8).

## 9b. LangGraph
**IMPLEMENTED FOR OFFLINE RESEARCH / EVALUATION. NOT deployed to production.**

`langgraph` 1.2.11, eval virtualenv only — **not in any Lambda image**, not in the request path, and not a
production dependency. Lives in `multitenant-rag/evals/decomp_graph.py`, driven by
`evals/run_decomp_experiment.py`. Production `/api/ask` remains the deterministic if/else DAG.

What the graph does (7 nodes, 2 conditional edges, dynamic fan-out, deferred fan-in):

- **Runtime question analysis** — `analyze_question` calls NVIDIA 20B with a JSON-only contract to decide
  whether the question holds more than one independent information need. No access to the expected answer
  or any dataset label.
- **Conditional simple-vs-compound route** — `add_conditional_edges` sends simple questions down a
  faithful re-implementation of the production path (one hybrid query → top-5 → one generation), and
  compound questions to decomposition. Routing is never manually forced.
- **Parallel subquery retrieval** — `Send`-based fan-out, one `retrieve_branch` per subquestion, bounded by
  `threading.Semaphore(2)`. Each branch reuses the **existing production hybrid retrieval** (Titan dense
  1024-d + fastembed BM25 → Qdrant native RRF, `TOP_K=5`, `RETRIEVAL_FLOOR=0.15`). Tenant scope is
  inherited per branch and **never widened** by decomposition.
- **Fan-in** — `add_node("merge_evidence", merge_evidence, defer=True)`; `defer=True` makes the node wait
  for all branches instead of firing per branch. Branch results accumulate through an
  `Annotated[list[dict], operator.add]` reducer channel.
- **Coverage-aware evidence merge** — pass 1 takes the best chunk from *each* branch (coverage guarantee),
  pass 2 round-robins deeper, capped at `MAX_LLM_CONTEXT_CHUNKS = 5`, deduped on
  `(post_id, chunk_text[:80])`. Deliberately **never** re-sorts by score across branches: RRF scores from
  different queries are not comparable, and a global sort lets one branch starve the other information need.
- **Final synthesis** — one NVIDIA 20B call over the merged context, instructed to address every part and
  to say plainly when a part is uncovered.

**Experiment v1 is FROZEN** (architect decision 2026-08-23): topology, fan-out, `Semaphore(2)`, merge
policy, max-5 context, citation policy, Titan/BM25/RRF, `TOP_K`, floor, chunk size and models are all
locked. **The current router is NOT production ready** — it classified all 6 test cases compound, including
3/3 simple controls (compound recall 100%, precision 50%, routing accuracy 50%, zero simple-branch
executions). Router improvement is a separate future experiment (`compound-router-v2`, documented only —
not started). A larger application model is **NOT justified yet**.

Judge scores are **1 of 6 complete** — the NVIDIA 120B judge endpoint is down (11/12 timeouts, plus two
single bounded probes that both timed out). The graph checkpoint is durable and fingerprint-matched, so the
judge stage resumes with **zero** graph reruns when the endpoint recovers.

Results, limitations and the failure-mode classification: **`LANGGRAPH-DECOMPOSITION-EXPERIMENT.md`**
(experiment `rag-agentic-decomposition-nvidia20b-v1`, 6 cases, fingerprint `c56b24a3a1002366`).
Headline: compound recall 3/3 and decomposed retrieval surfaced evidence absent from **all 10**
single-query candidates in 2 of 3 compound cases; but routing precision was 3/6 (all three controls
over-decomposed), and 5 of 6 LLM judge scores are missing due to a NVIDIA 120B endpoint outage.
Production deterministic RAG remains unchanged.

## 9c. Compound Router v2 — OFFLINE ROUTER EXPERIMENT

**Not a production implementation. Not integrated into any graph. Not deployed.**

`evals/router_v2.py` + `evals/run_router_v2.py` — an isolated offline experiment that evaluates ONLY question
routing: does a question need decomposition into multiple retrieval queries? It performs no retrieval, no
fan-out, no answer generation and no judging, and imports neither `decomp_graph` nor the production `app`
(asserted by AST-based tests). Frozen v1 was not modified.

Design: NVIDIA 20B with a strict structured contract (`needs_decomposition`, `information_needs`,
`reason_code` from a closed 5-value enum) that anchors the decision on **retrieval locality** rather than
grammar — compound only when two or more information needs are independently retrievable. No
chain-of-thought requested or stored. The router receives the question and nothing else; anti-leakage tests
assert no label, expected answer, score or case category can reach it.

**Router-v2 execution: COMPLETE / OFFLINE.** 52 generative cases classified, 58 NVIDIA 20B calls
(52 scored + 6 superseded), 0 errors, concurrency 1, Groq 0, NVIDIA 120B 0. Router source and prompt are
now frozen and unmodified.

**Router-v2 original scoring: SUPERSEDED due to inconsistent ground truth.** The original label policy
("case-018/020/022 compound, all other 49 simple") was an assumption, never adjudicated, and was internally
invalid: `case-022` and `case-023` have byte-identical question text and identical expected answers but
carried opposite labels, so no text-only classifier could be correct on both. Superseded figures — precision
0.167, recall 1.000, specificity 0.694, accuracy 0.712 — are preserved in
`output/router_v2_metrics.json` and `compound-router-v2-metrics.csv`, not overwritten.

**Router-v2 adjudicated scoring: NOT ACCEPTED.** A full semantic ground-truth audit
(`independent-retrieval-needs-v1`, manifest sha256 `8b1111bc…`, 39 simple / 11 compound / 2 ambiguous,
48 unique questions, 4 duplicate groups) was frozen before any rescore, then the **same immutable 52
predictions** were re-scored at **zero** provider cost:

| Metric | Original | Adjudicated | Threshold |
|---|---|---|---|
| Compound recall | 1.0000 | **1.0000** | ≥0.90 ✅ |
| Simple specificity | 0.6939 | **0.8718** | ≥0.90 ❌ |
| Compound precision | 0.1667 | **0.6875** | ≥0.80 ❌ |
| F1 / accuracy | 0.286 / 0.712 | **0.815 / 0.900** | — |
| TP / FP / TN / FN | 3/15/34/0 | **11/5/34/0** | — |

Of the original 15 false positives: **8 were label defects** (router was right), **5 were genuine router
errors**, 2 ambiguous. So ~53% of the apparent failure was measurement error — but the verdict is still
**NOT ACCEPTED**, failing 2 of 3 thresholds. The real positive class is **11, not 3** (3.7× larger).
Router-v2 does beat a naive "and"-detector on every metric (0.688/1.000/0.872/0.900 vs
0.476/0.909/0.718/0.760), so it learned more than conjunction-spotting. All 5 remaining errors share one
shape: a contrast, verification or temporal framing about **one** subject read as two needs.

Not integrated, not deployed. An **unseen holdout is mandatory** before integration regardless — this was a
post-hoc rescore of the same dataset, and the annotation was not blinded (the annotator had prior exposure
to the predictions). Next step recommended: independent blinded re-annotation, then a narrowly targeted
router-v3. No router-v3 designed or built.

Details: **`ROUTER-GROUNDTRUTH-AUDIT-V2.md`** (22 sections) and
**`COMPOUND-ROUTER-V2-EVALUATION.md`** (23 sections, original scoring).

## 9d. Compound Router v3 — OFFLINE ROUTER EXPERIMENT, DEVELOPMENT ONLY

**NOT ACCEPTED. Not integrated into LangGraph. Not deployed. Holdout not run.**

`evals/router_v3.py` + `evals/run_router_v3.py`, isolated from v2 and the decomposition graph (AST-tested).
Model NVIDIA 20B, temperature 0.0, prompt SHA `e148925f8098d9e8`, schema `router-v3-retrieval-plan-1`,
fingerprint `a270bd5888461d60`, one call per question, concurrency 1. **57 tests.**

Design change from v2: instead of asking "does this contain multiple information needs?", v3 asks how many
focused retrieval **queries** a retriever would need and emits the minimal plan; the verdict is a parser-
enforced property of that plan (`needs_decomposition == (len(retrieval_queries) >= 2)`, max 3 queries).
Seven simple reason codes explicitly name the shapes v2 mis-handled.

**Development result (52 adjudicated cases, 50 scored) — gate FAILED on recall:**

| Metric | v2 | v3 | Threshold |
|---|---|---|---|
| compound precision | 0.6875 | **0.9000** | ≥0.80 ✅ |
| compound recall | 1.0000 | **0.8182** | ≥0.90 ❌ |
| simple specificity | 0.8718 | **0.9744** | ≥0.90 ✅ |
| accuracy | 0.9000 | **0.9400** | — |
| TP/FP/TN/FN | 11/5/34/0 | **9/1/38/2** | — |

v3 fixed **4 of the 5** v2 false positives (`case-002`, `case-004`, `case-056`, `case-059`) and made the two
largest simple categories perfect (21/21 and 16/16), but introduced 2 false negatives, so the runner's gate
refused the holdout stage automatically. v3 was not tuned again.

Two findings worth carrying forward: (1) **the plan-length invariant is gameable** — on `case-030` the model
OR-stuffed three needs into one query string ("MS-E1 … OR MS-E2 … OR MS-E3 …") to keep the count at 1, which
the parser does not detect; (2) the "contrast about one subject → one query" instruction over-fired on
`case-041`, where the contrast spans two unrelated domains.

**Holdout v1: BUILT, FROZEN, UNUSED.** `compound-router-holdout-v1.jsonl` — 40 new cases (20 simple / 20
compound, 0 ambiguous), sha256 `0957b7bf…`, authored before any v3 call, zero exact overlap with the dev set
(max Jaccard 0.50), and cue-balanced so a naive "and"-detector reaches only 0.750 accuracy. It remains
genuinely unseen and reusable.

Details: **`COMPOUND-ROUTER-V3-HOLDOUT-EVALUATION.md`** (23 sections).

## 9e. Compound Router v4 — OFFLINE ROUTER EXPERIMENT, DEVELOPMENT ONLY

**NOT ACCEPTED. Not integrated. Not deployed. Holdout NOT run — still unspent.**

`evals/router_v4.py` + `run_router_v4.py`, isolated from v2/v3 and the graph (AST-tested). Model NVIDIA 20B,
temperature 0.0, prompt SHA `e0439db6159e5d5b`, schema `router-v4-atomic-evidence-units-1`, fingerprint
`978d0ae1f8a89462`, one call per question, concurrency 1. **55 tests.**

Design change from v3: v3 derived the verdict from `len(retrieval_queries)`, which the model defeated by
OR-stuffing three needs into one string. v4 reasons in **atomic evidence units** — one localized evidence
neighbourhood each — and the verdict is a property of the unit count
(`needs_decomposition == (len(evidence_units) >= 2)`, 1–3 units). **The atomicity rule is enforced in the
parser**: a unit whose `retrieval_query` joins independent targets with `OR`/`AND`/`||`/`&&` is rejected, so
v3's loophole is permanently closed.

**Development result — FAILURE, gate not computable:**

3 of 50 non-ambiguous cases produced **no verdict** (`case-002`, `case-038`, `case-040`, all
`unit_retrieval_query_empty` — the model supplied `anchor` and `facts_needed` but no query). A non-empty
`retrieval_query` per unit is mandated schema, so this is a genuine finding: the richer three-field schema is
one the 20B model fails on 5.8% of questions. Provider health was perfect (52/52, 0 timeouts).

On the 47 parseable cases (**not** a valid gate result): TP/FP/TN/FN 9/3/33/2, precision **0.7500**, recall
**0.8182**, specificity 0.9167, accuracy 0.8936 — recall and precision both still miss threshold, so v4
fails on two independent counts. The runner exited before writing a gate file, which structurally prevents
the holdout stage from starting.

**What v4 did fix:** both targeted v3 failures — `case-030` (3 evidence units, was OR-stuffed into 1) and
`case-041` (2 units, was collapsed to 1). Categories R1 and R3 went 0.667 → **1.000**. It also recovered
`case-003`, which neither v2 nor v3 got right. **What it broke:** `case-007` and `case-047` became false
negatives (R2, both correct under v3), `case-056` and `case-059` became false positives, and `case-002`
became unscorable.

Across three attempts no version has cleared all three thresholds — v2 maximised recall (1.000), v3
precision/specificity/accuracy (0.900/0.974/0.940), v4 fixed v3's representation flaws but regressed
elsewhere. **Each redesign moved the errors rather than removing them.**

**Frozen holdout v1: still BUILT, VERIFIED, UNUSED.** sha256
`0957b7bf8db137bad6e35f1495f5953e636e87969b5173b0fd9101a1dfd233b8` — recomputed and matched; no question,
label or category edited. It has never been shown to any router.

Details: **`COMPOUND-ROUTER-V4-ATOMIC-EVIDENCE-EVALUATION.md`** (27 sections; holdout sections marked NOT RUN).

## 9f. Compound Router Cascade v1 — OFFLINE ROUTING EXPERIMENT, DEVELOPMENT ONLY

**NOT ACCEPTED. Not integrated. Not deployed. Holdout NOT run — still unspent.**

`evals/verifier_v1.py` + `run_cascade_v1.py`. Two-stage architecture instead of another single-shot router:
**Stage A = frozen Router V2** (high recall, used byte-identical — prompt SHA `763d12cd82245285` re-verified
live against its recorded fingerprint), **Stage B = new strict verifier** invoked only on V2-compound
candidates. Cascade fingerprint `5f8fcff597261b61`, verifier prompt SHA `7a4d3859f1285c62`, schema
`cascade-v1-verifier-1`, NVIDIA 20B at temperature 0, concurrency 1. **53 tests.**

Stage B answers one confirmation question — do V2's proposed needs need separate retrieval neighbourhoods? —
with a deliberately small schema (boolean + closed 6-code enum + one sentence), applying v4's lesson that
rich router schemas cost compliance. By construction Stage B cannot create a compound V2 missed.

**Development result — FAILURE on recall only:**

| Metric | V2 alone | Cascade v1 | Threshold |
|---|---|---|---|
| compound precision | 0.6875 | **0.8182** | ≥0.80 ✅ |
| compound recall | 1.0000 | **0.8182** | ≥0.90 ❌ |
| simple specificity | 0.8718 | **0.9487** | ≥0.90 ✅ |
| accuracy | 0.9000 | **0.9200** | — |
| TP/FP/TN/FN | 11/5/34/0 | **9/2/37/2** | — |

The architecture delivered its precision half — it removed **3 of the 5** V2 false positives (`case-002`,
`case-003`, `case-004`) — but rejected **2** true compounds (`case-007`, `case-047`) where at most one was
allowed. One more preserved compound would have passed.

**Notable:** all four errors contradict rules the verifier's own prompt states and codes it used correctly
elsewhere in the same run — `case-007`/`case-047` collapsed on a shared noun the prompt explicitly warns
about; `case-056`/`case-059` were confirmed although `same_subject_temporal_comparison` and
`same_series_synthesis` exist and the former was applied correctly to `case-003`. The failure is
inconsistent application, not missing instruction.

**Call efficiency and cost — the architecture's clearest win:** verifier invoked on only **34.6%** of
questions (18/52); 1.346 calls/question if Stage A ran live; Stage B p50 **1,921 ms** and 220.7 output tokens
— roughly 3× faster and half the output of either single-shot router.

**Cross-experiment pattern:** V3, V4 and the cascade all land on recall **exactly 0.8182 (9/11)** from three
architecturally different specificity mechanisms. V4 and the cascade lost the identical pair; V3 lost a
different pair. Only V2's permissive prompt has ever kept all 11 compounds.

**Frozen holdout v1: still BUILT, VERIFIED, UNUSED** — sha256
`0957b7bf8db137bad6e35f1495f5953e636e87969b5173b0fd9101a1dfd233b8`, recomputed and matched. Never shown to
any router across four experiments.

Details: **`COMPOUND-ROUTER-CASCADE-V1-EVALUATION.md`** (29 sections; holdout sections marked NOT RUN).

## 9g. Router V2 High-Recall Holdout Validation — **PASS**

**ROUTER V2 ACCEPTED FOR OFFLINE LANGGRAPH INTEGRATION. Not production-ready. Not integrated.**

`evals/run_v2_holdout.py` + `test_v2_holdout.py` (**28 tests**). Experiment
`compound-router-v2-high-recall-holdout-v1`, fingerprint `ef077ea13547d7d3`. The candidate is the **exact
frozen Router V2**, verified live against its stored fingerprint before execution (prompt SHA
`763d12cd82245285`, `openai/gpt-oss-20b`, temperature 0, max_tokens 1500, 5 reason codes, parser unmodified).
No verifier, no V3, no V4, no cascade Stage B, no retrieval, no generation, no judging, no LangGraph.

Acceptance policy was fixed **before** the untouched holdout ran and recorded in the fingerprint: compound
recall ≥0.95, simple specificity ≥0.80, compound precision ≥0.80, plus category guards
compound-without-"and" recall ≥0.75 and contrast/verification specificity ≥0.75.

**Result on the 40-case unseen holdout (20 simple / 20 compound, sha256 `0957b7bf…` verified):**

| Metric | Value | Threshold |
|---|---|---|
| compound recall | **1.0000** (20/20) | ≥0.95 ✅ |
| simple specificity | **0.9500** (19/20) | ≥0.80 ✅ |
| compound precision | **0.9524** (20/21) | ≥0.80 ✅ |
| accuracy / F1 | **0.9750 / 0.9756** | — |
| TP/FP/TN/FN | **20/1/19/0** | — |
| compound-without-"and" recall | **1.0000** | ≥0.75 ✅ |
| contrast/verification specificity | **1.0000** | ≥0.75 ✅ |

**Zero false negatives** across all 20 compounds and all six compound categories. The single error is
`hold-001` ("What does the Forty-Minute Rule require and which Quill Cell version introduced it?") — a
same-entity multi-attribute question masked by an "and" plus a second named entity.

**The result reframes the earlier arc:** the same frozen V2 scored *better on unseen data than on
development* — precision 0.6875 → **0.9524**, specificity 0.8718 → **0.9500**, accuracy 0.900 → **0.975**,
recall 1.000 → **1.000**. V2 was never the weak router; the 5 development false positives that motivated
three redesigns did not generalise into a systematic specificity problem, and V3/V4/Cascade each traded away
real recall to fix something narrower than it appeared.

**Honest caveat:** with 20 compounds, recall 20/20 carries a 95% Wilson lower bound of 0.839 — below the
0.95 threshold it nominally clears. The point estimate passes; the interval does not establish ≥0.95 with
confidence. The holdout was also authored by the same agent that analysed V2's failure modes. A larger,
independently annotated holdout is the recommended next validation.

**Holdout v1 is now SPENT** (manifest unmodified, hash verified). Any future router candidate needs a new
unseen set. Provider: NVIDIA 20B **40** calls, 40/40 successes, 0 timeouts; NVIDIA 120B 0; Groq 0.

Details: **`ROUTER-V2-HIGH-RECALL-HOLDOUT-EVALUATION.md`** (20 sections).

## 9h. Routed LangGraph v2 — IMPLEMENTED OFFLINE, LIVE COMPOUND RUN COMPLETE

**18/18 compound cases executed live. The 34 simple cases reuse baseline artifacts. Judge scores pending. Not deployed.**

`evals/routed_graph_v2.py` + `run_routed_graph_v2.py` + `test_routed_graph_v2.py` (**41 tests**).
Experiment `rag-routed-langgraph-v2-offline`. Combines the two validated pieces: **frozen Router V2** as the
routing node (prompt SHA `763d12cd82245285` asserted at runtime; the graph refuses to run against a changed
prompt) and the **validated decomposition/fan-out/fan-in mechanics**.

Topology (8 nodes, 2 conditional edges, dynamic `Send` fan-out, deferred fan-in):
`START → resolve_scope → route_question →` simple: `normal_retrieve → normal_answer → END`; compound:
`decompose → Send fan-out → retrieve_branch ×N (Semaphore(2)) → merge_evidence (defer) → final_answer → END`,
with an explicit fallback to the simple path when decomposition cannot yield ≥2 subquestions.

**Fidelity by reuse:** graph v2 *imports* the frozen `decomp_graph` and calls its nodes unmodified
(`_scope`, `_retrieve`, `ANALYZER_SYS`, `parse_analysis`, `normal_retrieve`, `retrieve_branch`,
`merge_evidence`, `final_answer`, `normal_answer`, `fan_out`). Routing is the only change from v1.
Router V2's `information_needs` are **diagnostic metadata only** — never subquestions or retrieval queries
(test injects sentinels and asserts they never reach a query).

**Two defects found and fixed in the new graph during implementation:**
1. The compound conditional edge originally returned a string label, so LangGraph delivered the whole state
   to `retrieve_branch` → `KeyError: 'branch'` on every compound case. Fixed to return `list[Send]` with the
   edge in list form; regression test added.
2. `get_graph()` renders `normal_answer -> merge_evidence` and omits `normal_answer -> __end__` — a drawing
   artifact of the `defer=True` node. Verified functionally that `merge_evidence` does **not** run on the
   simple path; a behavioural test now pins this instead of trusting the drawn edges.

**Phase A (zero provider cost — NVIDIA 0, Groq 0, Titan 0, Qdrant 0):** six-case **artifact replay** (not a
graph execution) using persisted V2 verdicts plus existing baseline/decomposition artifacts. Routes read from
storage: `case-001` simple → baseline artifact; `case-002/004/018/020/022` compound → decomposition-v1
artifacts. All respect the ≤5 context cap.

**Full-52 routing distribution** (persisted V2, no new call): **34 simple / 18 compound**; both
ambiguous-ground-truth cases route compound. Cross-tab: compound→compound 11, simple→simple 34,
simple→compound 5, ambiguous→compound 2.

**Prospective live budget (NOT executed):** NVIDIA 20B **36** calls (18 decomposition + 18 final generation);
**Bedrock Titan embeddings 36–54** (AWS-billable, the only paid item); Qdrant branch searches 36–54; Router
V2 0; Groq 0; NVIDIA 120B 0. Simple-path retrievals and generations are 0 — baseline artifacts are reused in
the harness only, never in graph runtime.

**Eval environment restored:** `langgraph==1.2.11` reinstalled into the persistent repo venv
`multitenant-rag/.venv` after the ephemeral `/tmp` venv was lost, pinned in `evals/requirements-eval.txt`
(marked OFFLINE EVALUATION ONLY, never added to a Lambda image). **The 30 frozen decomposition-v1 tests that
could not execute last task now pass** — 346 tests green across the whole eval suite.

**LIVE RUN COMPLETE 2026-08-23 — 18/18, all approved bounds respected.** Experiment fingerprint
`90a7d26b3e6b88d6`. Router calls **0** (persisted verdicts injected). Actual usage against approved limits:
NVIDIA 20B decomposition **18**/18, final generation **15**/18, total **36**/36; **Bedrock Titan embeddings
34**/54 (the only AWS-billable item); Qdrant logical branches **34**/54, physical `query_points` **68**/108;
Groq **0**; NVIDIA 120B **0**. Zero provider errors, zero retries, zero empty-retrieval branches.

Branch counts: 14 cases × 2 branches, 1 × 3 (case-030), 3 × 1 (fallback). **All 18 produced exactly 5
context chunks**, citations ≤ chunks in every case.

**Deterministic quality, baseline → routed:** context reference-phrase coverage **24 → 37 (+13)** and answer
coverage **16 → 26 (+10)** across the 8 cases with enumerable reference facts, with **zero regressions**.
Largest gains: case-018 context 1/6 → 6/6 and answer 1/4 → 4/4; case-023 answer 0/3 → 3/3.

**All 5 V2 false positives preserved quality** — 3 decomposed and still answered correctly, 2 were rescued.

**Notable finding:** the frozen v1 decomposition analyzer returned 0 subquestions for 3 V2-compound cases,
triggering the documented fallback to the simple path. This rescued 2 of V2's 5 false positives
(`case-056`, `case-059`) at no cost, but also declined one genuine compound (`case-041`) — where V2's own
diagnostic `information_needs` were correct and went unused, since they are diagnostic-only by design.

Latency: end-to-end mean 18,764 ms (p50 17,152, p95 32,019); `decompose` 7,329 ms, `final_answer` 8,362 ms,
`merge_evidence` **0.1 ms**. Tokens: decomposition 8,257, generation 43,202. Free-tier NIM, not production
latency.

**Judge: NVIDIA 120B not probed (bound 0).** All 18 routed answers are `pending_120b_unavailable`; the
decomposition experiment stays 1/6 scored, 5 pending. No scores fabricated; everything persisted so judging
needs no re-execution.

Details: **`LANGGRAPH-ROUTED-RAG-V2-INTEGRATION.md`** (26 sections).

## 9i. RAGAS + DeepEval framework evaluation — STOPPED AT CALIBRATION

**Full 36-record run NOT executed. Framework scores are NOT usable as quality evidence.**

`evals/run_framework_eval.py`. RAGAS **0.4.3** + DeepEval **4.1.10**, both eval-only and never added to a
Lambda requirement, installed in an **isolated venv** (`~/.cache/mtrag-eval-frameworks`,
`langchain-community<0.4`) because ragas 0.4.3 imports a module removed in langchain-community 0.4.2 — the
validated langgraph venv was left untouched and re-verified (41 routed + 30 decomposition tests still pass).

Evaluator: **`nvidia/nemotron-3-nano-30b-a3b`** — deliberately not the GPT-OSS-20B application model.
Exactly **one** availability probe (OK, 0.8 s). NVIDIA 120B not probed. AnswerRelevancy embeddings are
**local** (sentence-transformers), so no embedding provider and no Titan call.

Input set frozen to **36 records** (18 baseline + 18 routed) built from the exact persisted contexts, with a
`context_sha256` per record — never rebuilt from citations, IDs or a Qdrant query.

**Calibration on 6 diagnostic records stopped the run.** Four of the architect's own pathology criteria were
met: (1) `ragas_faithfulness` and `ragas_context_recall` were **0/6 usable**
(`IncompleteOutputException`/`InstructorRetryException`); (2) `deepeval_faithfulness` returned **1.000 on all
six**, including a demonstrably false baseline answer — no discrimination; (3) `ragas_answer_relevancy`
**inverted**, rating the weak baseline 0.660 above the strong routed answer 0.592 against a 1/4 → 4/4 fact
improvement; (4) **15% provider error rate** (23/150 calls). Only **10 of 24** metric pairs were comparable,
with **mean absolute disagreement 0.3769**.

Measured cost was **25.0 evaluator calls per record** (RAGAS 18, DeepEval 7) → the full run would have been
**≈900 calls**, 6.25× the naive 144 estimate.

**One metric tracked reality:** `deepeval_contextual_recall` moved **0.000 → 1.000** on case-018
baseline→routed, matching the deterministic finding exactly.

**The deterministic findings remain first-class and unchanged:** context coverage 24→37 (+13), answer
coverage 16→26 (+10), zero regressions, 18/18 success, exactly 5 context chunks everywhere, context/citation
invariant held, all 5 V2 false positives preserved. Where the two disagree the deterministic measurement is
the one with a verifiable basis.

Recommendation recorded: change the **evaluator**, not the harness — a reasoning-style model driving
instructor-style structured extraction is the blocking issue. Details:
**`RAGAS-DEEPEVAL-ROUTED-RAG-EVALUATION.md`** (28 sections).

## 9j. Groq production-provider observability + free-tier feasibility

**Development/provider-feasibility experiment. Nothing deployed.** Experiment
`groq-routed-rag-observability-v1`, fingerprint `9584e29a26b57de5`, LangSmith dev project
**`multitenant-rag-dev-groq-observability-v1`** (production project untouched, received no traffic).

Provider swap only — frozen Router V2 and decomposition prompts/parsers imported and reused byte-identical:
Router **Groq 20B**, decomposition **Groq 20B**, generation **Groq 120B**.

**API timeout boundary (verified read-only):** API Gateway HTTP API `AWS_PROXY` `TimeoutInMillis = 30000` —
the binding external deadline. The ask Lambda (60 s) and the Groq client timeout (60 s) both sit *above* it,
so a slow generation hits the gateway first. Recommended internal safety deadline **24,000 ms**.

**Phases A/B/C executed; D and E NOT run** (Groq ceiling: 93 of 100 physical requests used).

| Result | Value |
|---|---|
| Trace completeness | **100%** — 221 spans, 76 roots, 0 errors, 0 orphans; simple roots carry no decomposition span |
| Router equivalence (52 cases) | **identical aggregate metrics to NVIDIA**: TP 11 / FP 5 / TN 34 / **FN 0**, recall **1.0000**, specificity 0.8718. Agreement 50/52; the 2 disagreements cancel |
| Decomposition equivalence (18) | 15/18 usable, **the same 3 fallbacks** as NVIDIA, 17/18 same subquestion count, identifiers 4/4 |
| Router latency | **864.9 ms** mean / 1,338.6 p95 — vs NVIDIA 4,567.9 / 13,624.2 (**5–10× faster**) |
| Decomposition latency | **662.8 ms** mean — vs NVIDIA 7,329.2 (**11× faster**) |
| Generation latency (120B) | **1,210.8 ms** mean / 1,742.8 p95 — model differs from NVIDIA 20B |
| Projected compound critical path | **3,529.8 ms mean / 4,681.7 ms p95** (parallel branch = slowest, not sum) |
| Observed Phase A compound | 18,432 ms mean — the ~15 s gap is **rate-limit backoff, not compute** |

**Latency verdict: YELLOW.** The architecture is comfortably GREEN (4.7 s projected p95 vs a 30 s deadline);
the **free plan** makes it yellow — 429 backoff inflated observed p95 to 23.8 s and max to 25.3 s, above the
24 s safety line but inside the 30 s hard deadline. For contrast, NVIDIA's routed p95 of 32.0 s was already
*above* the hard deadline, so Groq moves this from infeasible to feasible-with-a-quota-caveat.

**Groq free-plan capacity (from actual response headers, none invented):** `x-ratelimit-limit-requests` 1000
(≈920 remaining after the whole run) but `x-ratelimit-limit-tokens` **8000** — **TPM is the binding
constraint, not RPM.** All 4 × 429 occurred in Phase A at ~1 s pacing; **zero** 429s across 70 consecutive
20B calls once paced at 7 s. Concurrency 2 healthy (4/4 in 1.93 s wall, no 429, latency unchanged). At
~3,760 tokens per compound request the free plan supports roughly **2 compound requests/minute**.

**Observability finding:** LangSmith span wall-duration for phases B/C **encloses the deliberate 7 s pacing
sleep**, and Phase A `generation` wall encloses 429 backoff — so span duration ≠ provider latency. Provider
latency is the `latency_ms` metadata around the HTTP call. Also newly visible: `qdrant_dense_probe`
(526 ms) is **3× `qdrant_hybrid_rrf`** (169 ms).

**NVIDIA observability gap CONFIRMED** — the NVIDIA live run never separately measured Titan, BM25, the two
Qdrant calls, `retrieval_branch`, `resolve_scope` or `build_context`. Closing it would need ~15 NVIDIA 20B
calls on 6 mirrored cases. **No NVIDIA call was made.** RAGAS/DeepEval/judge: 0 calls, still blocked on
evaluator reliability.

Details: **`GROQ-LANGSMITH-PRODUCTION-FEASIBILITY.md`** (27 sections).

## 9k. Groq 120B routed generation validation — COMPLETE, ALL 18 CASES

**Offline generation-quality validation. Nothing deployed.** Experiment
`groq120b-routed-generation-validation-v1`, fingerprint `d98f753ce3029b4a`, LangSmith dev project
`multitenant-rag-dev-groq-observability-v1`.

Closes the gap left by §9j, which measured Groq 120B generation on only **6 of 18** routed cases.

**Generation only.** Router V2, decomposition, LangGraph, Titan, BM25, Qdrant, NVIDIA, RAGAS and DeepEval:
**0 calls each**. All 18 evidence sets replayed from the persisted routed artifact and verified
**18/18 by `context_sha256`**. Every case carried exactly 5 chunks (the `MAX_LLM_CONTEXT_CHUNKS` cap).

**The model under test is the deployed production model** — read-only config check confirms
`GROQ_MODEL=openai/gpt-oss-120b`.

| Result | Value |
|---|---|
| Execution | 18 logical / **18 physical** (ceiling 22); 18/18 first-attempt success; **0** 429/5xx/timeouts/retries |
| Generation latency (inference only) | **1,475.7 ms** mean / 1,432 median / 1,891 p95 / **2,614 max** |
| vs NVIDIA 20B gen node | **5.14× mean**, 3.34× median, **11.7× at max** (NVIDIA max 30,524 ms — over the 30 s gateway deadline by itself) |
| Frozen reference-phrase coverage | **18/19 both models — identical** (covers only 3 of 18 cases) |
| Deterministic fact-atom coverage (all 18) | Groq **139/213 (0.653)** vs NVIDIA **136/213 (0.638)**; per-case 5W/4L/9T |
| Refusal markers | 0 for both models |
| Trace completeness | **18/18** roots each with a `generation` child, status success, 0 errors; server duration agrees with local provider latency to **18.1 ms mean** |

**Quality verdict: EQUIVALENT, not better.** 1.5 points across 213 atoms with a 5W/4L/9T per-case record is
noise. The defensible claim is that Groq 120B is **quality-equivalent to NVIDIA 20B on identical routed
evidence while being 3.3–5.1× faster** — so the fast production provider costs nothing measurable in quality.

**Free-tier economics refined:** mean **2,401 tokens per generation call** against
`x-ratelimit-limit-tokens: 8000` ⇒ **3.33 calls/min sustainable**. TPM confirmed again as the binding
constraint (RPM ended 982/1000). **92% of this experiment's wall clock (305.6 s of 332.2 s) was deliberate
rate-limit pacing, not model work** — pacing is recorded in its own field and never added to
`provider_latency_ms`.

**Router V2 false positives cost verbosity, not correctness.** All 5 over-decomposed cases
(002/003/004/056/059) produced correct answers; the only effect was longer, section-headed output. This
supports keeping the high-recall V2 policy.

**case-041 fails identically under both models on the same context** (6/13 atoms each) — the library half of
the answer (Founders' Week ceremonial ribbon on the bell clapper) is missing. A stronger model on the same
evidence does not fix it, confirming this is a **retrieval defect, not a generation defect**. Stays LOW
PRIORITY / BACKLOG.

**Two honest caveats, both in the report:** the prompt **scaffolding is reconstructed** for all 18 (only the
evidence text is byte-verified — the live run built prompts from Qdrant point objects that no longer exist),
and **3 of 18 fallback cases used a substituted system prompt** because the live one needs a DynamoDB tenant
lookup the artifact does not carry.

**Scorer defect found and fixed:** case-030 initially scored 0/2 for *both* models — both answers were fully
correct, but the corpus spells integers out ("eighteen") while the reference uses digits. Added digit↔word
equivalence (same class as the earlier U+2011 hyphen defect). NVIDIA 133→136, Groq 135→139.

**NVIDIA observability gap: still DEFERRED** (no NVIDIA call made). RAGAS/DeepEval still blocked on evaluator
reliability.

Details: **`GROQ120B-ROUTED-GENERATION-VALIDATION.md`** (20 sections).

## 9l. Production routed-RAG hardening (Phase 1) — IMPLEMENTED + TESTED, **NOT DEPLOYED**

**The validated routed architecture now exists in production code, disabled by default.**
Feature flag `ROUTED_RAG_ENABLED` (default **false**) — with it off, `/api/ask` and
`/api/ask/group` behave byte-for-byte as today and the graph is never built.

New package **`lambdas/ask/rag/`** (15 modules) owns the production graph logic. It imports
**nothing** from `evals/` — proven by AST analysis over the shipped file set, and the Dockerfile
never copies `evals/`. Production functions reach the graph through `RagDeps` injection, which also
avoids a circular import with `app.py` and keeps every node testable without AWS.

**Frozen contracts carried byte-for-byte and hash-locked:** `ROUTER_SYS` `763d12cd82245285`
(the holdout-passing identity), `ANALYZER_SYS` `ae8185181e88f25f`, `GEN_SYS_COMPOUND`
`8c30bb9b064e6784`. `assert_frozen()` runs at graph build; a cross-check test re-derives all three
from the frozen eval modules and compares byte-for-byte.

**Graph:** `resolve_scope → load_history → fold_followup → semantic_cache_check →`
(hit → END) `→ route_question →` simple/compound `→ [decompose → Send fan-out → retrieve_branch xN
(Semaphore(2)) → merge_evidence (defer=True)] → build_context → generate → finalize → END`.

| Hardening | Implementation |
|---|---|
| Models | Router + decomposition **Groq 20B**, generation **Groq 120B**. No NVIDIA, no second provider |
| Request budget | `RequestBudget`: ≤1 router / ≤1 decomposition / ≤1 generation / ≤3 logical Groq / ≤3 branches / **Titan ≤4 total (1 cache probe + ≤3 retrieval)** / ≤6 physical Qdrant / ≤5 context chunks. Checked **before** each call, thread-safe, no residue on reject |
| Deadline | `REQUEST_DEADLINE_MS=24000`, `time.monotonic()` from entry; every call timeout is `min(ceiling, remaining − 1500 ms reserve)`; a branch that cannot finish is not started |
| Timeouts | **Fixed two audit gaps**: Bedrock/DDB now `botocore.Config` (connect 3 s / read 8 s / 2 attempts), Qdrant now `timeout=8` (it had **none**). Groq 6/6/12 s, each clamped to remaining budget |
| 429 policy | **No proactive pacing** — the 7 s experiment pacing was NOT carried over (`time.sleep` never called on the happy path, asserted). Retry only if the provider's own hint ≤2 s **and** the budget affords wait+call; ≤1 retry. Router/decomposition failure → fall back to normal RAG and still answer; generation failure → controlled error contract |
| Scope safety | Frozen immutable `Scope`; every `Send` carries the exact parent scope and `assert_parity()` runs before retrieval; empty/blank/widened scope **fails closed** |
| Partial failure | Survivor branches still answer; **all** branches empty → `empty_context`, no generation call; scope never widens |
| Semantic cache | Probe runs **before** routing, so a hit spends **zero** Groq calls (asserted). Eligibility unchanged: single route + no history only; group route uncached |
| Citation invariant | One capped list feeds both prompt and citations; the ≤5 cap is **asserted**, not assumed |
| Observability | §20 span tree (`semantic_cache`, `router_v2`, `decomposition`, `retrieval_branch_N` with its 4 physical ops, `merge_evidence`, `build_context`, `groq_generation`), emitted from **measured** node latencies after the answer — `RunTree.create_child` is not documented thread-safe and parallel branches must not put that assumption on the request path |
| Privacy | Whitelist extended with counts/booleans/enums/timings only. No question, answer, prompt, chunk text, sub-question, history, tenant id, group id, email, JWT or secret can pass — tested adversarially, including that an exception message is reduced to its class name |

**Closes both offline replay caveats:** the compound prompt is rendered from **real Qdrant point
objects** (no reconstruction), and the simple/fallback path uses the **existing production prompt
builders** (`_build_system_prompt` / `_build_group_system_prompt`) instead of a substituted compound
prompt.

**One production hardening beyond the offline graph, flagged:** compound routing now additionally
requires `parse_ok`. The frozen parser can return `needs_decomposition=True` with `parse_ok=False`
(e.g. `compound_flag_with_simple_reason_code`); the offline graph never exercised that path because
every holdout case parsed cleanly. No holdout metric changes.

**Tests: 140 new, all passing; 39 pre-existing still passing (verified identical at HEAD) = 179.**
`rag/test_frozen_parity.py` (14), `test_graph.py` (47), `test_budget_deadline.py` (32),
`test_dependency_boundary.py` (11), `test_flag_and_observability.py` (22),
`test_endpoint_integration.py` (14).

**Local container (arm64 via colima; production ships x86_64 from CI):** builds at 155.7 MB,
cold-imports `app` + `rag` in **901 ms**, compiles the 14-node graph, serves `GET /health`
`{"ok":true}`, flag reads `False`. No `ragas`/`deepeval`/`langchain_community`/`langchain_openai`/
`instructor`, no eval source anywhere in the image, no top-level `langchain`; `langchain_core` 1.6.0
present **only** as a LangGraph transitive dependency. A new `lambdas/.dockerignore` keeps test
files out of the runtime image.

**Provider calls this task: Groq 0, Titan 0, Qdrant data-plane 0, NVIDIA 0, RAGAS 0, DeepEval 0.**
**AWS mutations: 0. Nothing deployed.** The ingestion **DLQ is still absent** and is now the top
open P0. `case-041` unchanged (backlog).

Details: **`PRODUCTION-ROUTED-RAG-HARDENING.md`** (30 sections).

## 9m. Titan budget accepted + release-security hardening (2026-08-25)

**Titan accounting — ARCHITECT-ACCEPTED, option (a).** The per-request maximum is **4**, not 3:

```
  semcache_titan_embeddings    <= 1     semantic-cache probe
+ retrieval_titan_embeddings   <= 3     one per retrieval branch
= titan_embeddings_total       <= 4     ENFORCED per-request ceiling
```

"3 Titan calls" is the **retrieval** bound alone. The maximum of 4 arises only for a
cache-eligible request that probes, misses, routes compound and decomposes into 3 branches.
Branches were **not** capped at 2, the probe was **not** moved after Router V2, and the
semantic cache was **not** removed. The ambiguous `titan_embeddings` counter name was
**removed outright** (asserted by a test) so it cannot be misread as the request total; all
three figures appear in `budget.snapshot()` and the trace whitelist.

Measured, each pinned by its own test: simple cache-eligible **1** · 2-branch compound
**3** · 3-branch compound **4** · group route (never cache-eligible) **3**.

**Real inefficiency found and fixed while pinning those numbers:** the routed simple path
was re-embedding the question instead of reusing the semantic-cache probe vector — **2**
Titan calls where the existing production path costs 1. `normal_retrieve` now passes the
probe vector through, gated on the probe text being **exactly** the retrieval text (compared,
not assumed), so folded follow-ups and every branch still embed their own text.

**Release-security hardening — two independent fail-closed controls:**

| Control | Type | Rule |
|---|---|---|
| `tools/release_guard.py` | **PRIMARY, path-based** | **No `.env` may enter a release archive at all** — any depth, any variant (`.env.*`, `*.env`), plus `*.pem/.key/.p12/.pfx/.keystore/.jks`, `id_rsa*`, `credentials*`, `.netrc`, `.pgpass`, `.npmrc`, `.kdbx`. Allowlist holds exactly **one** entry (`local/.env.example`) |
| `tools/secret_scan.py` | SECONDARY, content | Runs on the **extracted** archive. Known prefixes (Groq/NVIDIA/LangSmith/OpenAI/GitHub/Slack), AWS key ids, JWTs, `Authorization: Bearer\|Basic\|Token` headers, and credential assignments by entropy — **unquoted `NAME=VALUE` in any file type** (the form originally missed) and quoted literals. Reports path/line/pattern-class only, **never the value** |

`tools/build_release_archive.sh` runs both and **deletes the archive** if either trips —
verified end-to-end with a planted secret. A bug in the guard was caught by its own tests:
`lstrip("./")` strips *characters*, so a root-level `.env` became `env` and passed; fixed
and pinned by a regression test. Deliberate suppressions use the greppable
`# pragma: allowlist secret` marker (synthetic test fixtures only).

**Archive verified by extraction** (not the source tree): 133 entries / 121 files,
**0 `.env` files**, both controls CLEAN. A coarse 14-character prefix check initially
appeared to match the old Qdrant key; that was a **false alarm** — Qdrant Cloud keys are
JWTs, so the prefix was the universal `{"alg":"HS256","typ":"JWT"}` header shared with a
synthetic test fixture. The key-specific 60-character slice and payload segment are both
absent.

**DEPLOYMENT GATE: BLOCKED.** No deployment until the user confirms the Qdrant Cloud API key
is rotated **and the old key revoked**, and the local dev `JWT_SECRET` replaced. Production
`multitenant/jwt` was **not** implicated and must not be modified. Git history is **not**
being rewritten (no force push, no scrub) — rotation/revocation is the mitigation.

**Tests: 281 passing** — 171 production routed-RAG (incl. 19 release/secret-scan) + 39
pre-existing + 71 eval-side frozen. Provider calls: Groq 0, Titan 0, Qdrant data-plane 0,
NVIDIA 0, RAGAS 0, DeepEval 0. AWS mutations 0. Production UNCHANGED.

Details: **`PRODUCTION-ROUTED-RAG-HARDENING.md`** (31 sections).

## 9n. ROUTED RAG DEPLOYED TO PRODUCTION (2026-08-24T19:24Z) — AWAITING MANUAL SMOKE

**Status: DEPLOYED / AWAITING MANUAL AUTHENTICATED SMOKE. Not declared successful.**

Routed LangGraph RAG is **live in production** with `ROUTED_RAG_ENABLED=true`, but no
authenticated end-to-end request has been made, so routed behaviour is **unverified**.

| | |
|---|---|
| Image | `multitenant-ask@sha256:3ada41fc85255adf544bb266b98ca62887b8639ea85f2b14c824005d530cfca0` |
| Tag / commit | `41266619fe4b36bcccf3f0d2d6f8dc719bd8553f` (immutable, = git SHA) |
| Architecture | **x86_64** — built by GitHub Actions `build-lambdas.yml` on native amd64 runners (CI run `32764221478`), NOT the earlier arm64 local build |
| Lambda | `Active` / `LastUpdateStatus: Successful`, 60 s / 2048 MB, deployed **by digest** |
| Env | 15 vars — the 14 captured baseline vars preserved verbatim + `ROUTED_RAG_ENABLED` |
| Rollback | flag→false (instant, no redeploy); then image `d5af30e…` digest `sha256:2791dfa4…` (preserved) |

**Flag-false verification passed before enabling:** `/health` 200 through API Gateway; cold
start 5.67 s billed / 16.6 s wall including image pull; warm `/health` ~0.37 s; uvicorn
started; `langsmith tracing enabled project=multitenant-rag-prod`; **0 import errors or
tracebacks**; no routed execution. The langgraph-in-production import risk is retired.

Verified inside the deployed image: `REQUEST_DEADLINE_MS=24000` (vs API Gateway 30,000 ms,
unchanged), models `20b`/`20b`/`120b`, Titan retrieval ≤3 + semcache ≤1 = **total ≤4**,
frozen prompt hashes intact, 15 rag modules and **no** eval source, RAGAS, DeepEval,
`langchain_community`, `langchain_openai` or top-level `langchain`.

**No authenticated request was made** — no credentials available, and creating a production
user or requesting a password is prohibited. Automated tests cover flag=false → legacy path;
the rest is the user's smoke.

**DLQ deliberately NOT part of this deployment** (independent failure domains; keeps rollback
attribution clear). It remains the **top open ingestion P0**.

Details: **`PRODUCTION-ROUTED-RAG-DEPLOYMENT.md`** (20 sections).

## 9o. Curated 25-user / 268-post seeded corpus (2026-08-26) — SYNTHETIC DEMO DATA

**Data classification: curated seeded demo users. NOT real users, customers or organic
traffic.** Realistic synthetic personas whose purpose is stable, heterogeneous content for
hybrid-retrieval, tenant/scope, simple/compound-query, group-RAG, global-search, semantic-cache
and noise-robustness testing, and for evaluation-dataset development.

| | |
|---|---|
| Corpus | 25 personas / 268 posts; source sha256 `6e2f76b7…`, ingest-region sha256 `ed8ecb66…` |
| Composition | 15 job-search (6 Full Stack, 4 ML, 5 GenAI), 1 Rameswaram travel (18), 1 Chennai food (28), 1 casual diary (10), 5 noise/test (6 each), 2 engineering notes (16 each) |
| Ingestion | **268/268 created and indexed**, 0 conflicts, 0 failures |
| Fidelity | `created_at` = corpus date @ 00:00 UTC **268/268**; tags exact **268/268**; S3 body SHA-256 matches corpus **268/268**; Qdrant points **268/268** |
| Identity | `email = <corpus-username>@example.com`; passwords derived via HMAC-SHA256 from a master secret **in memory only** — never stored anywhere. No username schema field was added (deferred to the profile/UI task) |
| Metadata | `tags` added as an optional DynamoDB list attribute; no key/GSI/LSI/IAM/API-GW/Qdrant schema change |
| Smoke | 7/7 retrieval checks pass via the production hybrid path (Groq 0) |

**Legacy cleanup:** the `seed-20260822` demo population was removed — 6 users, 6 tenants,
50 posts, 50 S3 objects, 12 group memberships, all its Qdrant points. Zero residue, zero
errors, no protected record touched. Five accounts of unproven provenance were classified
UNKNOWN_REVIEW and left **completely untouched** by explicit decision.

**Final population: 30 users / 30 tenants / 271 posts** — 25 corpus personas (268 posts)
plus 5 retained accounts (3 posts). The system does *not* contain only 25 users.

**Operational note:** ingestion triggered 8 Titan `ThrottlingException` events that all
self-recovered via FIFO redelivery (0 permanent failures, 0 poison messages). No queue
configuration was changed. `RedrivePolicy` on `multitenant-ingestion.fifo` is still **null** —
the ingestion DLQ remains the **top open P0**.

Details: **`CURATED-CORPUS-INGESTION-AND-CLEANUP.md`**.

## 9p. Full-width UI + writing workspace + profile identity + conversational chat (2026-08-26) — IMPLEMENTED, NOT DEPLOYED

**Frontend + narrowly-scoped backend. Nothing deployed; username backfill NOT run.**

**Root cause of the narrow desktop UI:** `Shell` applied `max-w-feed` (760px) to both the
header and `<main>`, so every page rendered in a 760px column regardless of viewport. Now
`w-full px-6 xl:px-8`. Reading width is still capped in the article `Reader` only.

| Area | Change |
|---|---|
| Write | Main column + `w-[260px]` "Your Posts" rail; editor `min-h-[60vh]` with a 560px floor; single Publish at the bottom after tags |
| Markdown | New `src/markdown.jsx` — H1–H3, paragraphs, bold, italic, underline, bullets, numbered lists, blockquote, inline code, fenced code, links, `hr` |
| Safe underline | Renderer emits **React elements**, never `dangerouslySetInnerHTML`. Exactly one marker `<u>…</u>` is recognised; `<b>`/`<script>`/`<img onerror>` stay literal text; link hrefs allow-listed so `javascript:`/`data:` cannot become anchors. **Sanitisation strengthened, not weakened** |
| Toolbar | `src/editor.jsx` — 14 selection-aware actions; Cmd/Ctrl+B/I/U/K; one `⌨ Shortcuts` popover |
| Profile | New `username` (optional, mutable, public) + editable email. **No password UI** |
| Chat | New `src/chat.jsx` shared by routed ask, group ask and saved chats — one implementation instead of three |
| Routed Ask | Desktop 50/50 selector + conversation, viewport height, independent scroll |
| Scope | Per-question snapshot stored on the user message; ≤5 names shown, >5 → first five `+N more` |

**Username identity model.** `username` is a public profile attribute only — never a
primary key, tenant identity, Qdrant scope, post ownership or JWT subject (`sub` is
`user_id`). Uniqueness uses a **reservation item in the existing users table**
(`user_id = "USERNAME#<normalized>"`) claimed by a conditional `PutItem` on
`attribute_not_exists` — atomic, and needing **no new table, no new GSI, no key-schema
change**. Rename claims the new name before releasing the old, so two users can never share
one. Reservation rows carry no `tenant_id` and are already filtered out of `GET /api/users`.

**Email change** keeps `user_id`/`tenant_id` and all content ownership, and issues a
refreshed JWT so the `email` claim is not stale. *Known limitation:* signup creates no
EMAIL# reservation, so a change racing a brand-new signup keeps today's window.

**Backend touched only where required:** `common/profile.py` (new), four `/api/me/*`
endpoints, and an optional `scope` argument on `chats.append_turn`. **RAG is untouched** —
frozen prompt hashes still `763d12cd82245285` / `ae8185181e88f25f` / `8c30bb9b064e6784`.

**Tests: 364 passing** — 99 new frontend (Vitest, newly introduced) + 24 new backend
profile + 241 existing.

**Backfill prepared, NOT run:** `tools/backfill_usernames.py`, dry-run by default,
idempotent, conditional, and it stops rather than overwriting a claimed username. The five
UNKNOWN_REVIEW accounts are never given an invented username.

Details: **`UI-UX-FULL-WIDTH-CHAT-WRITE-REFRESH.md`** (24 sections).

## 10. Observability
- **CloudWatch (deployed):** structured JSON logs (`common/logger.py`); per-query `relevance` lines; `usage-logs` DDB table; new logs "global search start/done" (request_id, result_count, latency_ms — never the query).
- **LangSmith (deployed; backend delivery pending an authed query):** per-request traces for the three query flows (project `multitenant-rag-prod`), as in §7.
- **Other:** AWS Budget alarm `multitenant-monthly` ($5 email alerts).

## 11. Security
- **Auth:** custom JWT (HS256) + bcrypt; identity from token.
- **Tenant isolation:** server-side context; Qdrant pre-filter by tenant_id; body tenant_id never trusted for isolation.
- **Secret management:** Secrets Manager (groq/qdrant/jwt/langsmith). **LangSmith key is NOT a Lambda env var** — read from Secrets Manager at runtime. IAM `secretsmanager:GetSecretValue` scoped to the exact langsmith ARN (no wildcard).
- **Least privilege:** new inline policy limited to the 3 social tables (+indexes) and the one secret ARN; existing policies untouched; createpost/ingestworker IAM unchanged.
- **Trace privacy:** whitelist + hide_inputs/outputs (§7).
- **Committed-credential cleanup (2026-08-23, commit `85463b0`, pushed):** a secret scan found two
  distinct literal credentials in tracked docs. The **retired RDS master password** (3 occurrences, 2
  files) is now at **0 occurrences** — redacted to `"$DB_PASSWORD"` / `${DB_PASSWORD}`. A **separate local
  Docker dev password** was redacted from all docs and from `blog-backend/.env.example`. Credentialed
  connection strings became environment-variable examples rather than being deleted, so the walkthroughs
  stay usable. No rotation needed for the retired credential — that RDS instance was torn down 2026-07-25
  and `describe-db-instances` in ap-south-1 returns none.
  - **Still present by decision:** `blog-backend/docker-compose.yml` (2 occurrences of the local dev
    password). It is functional infra, not documentation — `POSTGRES_PASSWORD` there provisions the local
    container, and making it a required env var would break `docker compose up postgres` for anyone whose
    untracked local `.env` predates the change. Local-only; absent from all Lambda configuration.
  - **Limitation:** current tip is redacted, but **historical Git commits may still contain the retired
    credential.** No history rewrite performed — a separate explicit task if the repo is ever made public.
  - Also noted, unmodified: `learnings/stage-1-ec2-rds.md` documents a literal `JWT_SECRET` for the same
    torn-down EC2 instance, self-labelled a change-later placeholder.
- **Audit clarifications (2026-08-23, code-grounded — see `CURRENT-SYSTEM-LOW-LEVEL-DESIGN.md`):**
  - **No DLQ exists.** `multitenant-ingestion.fifo` has `RedrivePolicy` NOT SET and no Lambda DLQ. A poison
    message is redelivered until the 4-day retention expires (~1,150 attempts at a 300 s visibility timeout)
    and blocks its own tenant's `MessageGroupId` for that period. Earlier wording calling this "indefinite"
    was wrong: it is bounded by retention, not infinite.
  - **No explicit boto3/Qdrant timeouts or retry config anywhere in `lambdas/`** — no `botocore.Config(`,
    no `retries=`; `QdrantClient(url, api_key)` is constructed with client defaults. Groq retries 4× on
    **429 only**; any 5xx raises immediately.
  - **Each logical retrieval issues TWO physical Qdrant `query_points` calls** — a dense-only `limit=1`
    cosine probe for the `RETRIEVAL_FLOOR` gate plus the hybrid RRF query. `RETRIEVAL_FLOOR` is applied
    **only** to the probe's cosine, never to an RRF score.
  - **Retrieval breadth differs by route**: single uses prefetch 20/20 and `limit=TOP_K`(5); multi/group uses
    prefetch 30/30 and `limit=TOP_K*2`(10). Both are then capped to 5 by `_llm_context`.
  - **The chunker's horizontal-rule pattern `HR_RE` is declared but never used**, and there is no final hard
    character split — `max_tokens` is a soft ceiling. It is deterministic markdown-structure chunking, not
    semantic and not LangChain-style recursive splitting.
  - **`_mark_indexed` swallows `ClientError`**, so a post can remain `ingestion_status="pending"` while its
    chunks are already indexed in Qdrant.
  - **The post body lives only in S3** (`tenants/{tenant_id}/posts/{post_id}.md`). DynamoDB holds metadata
    plus `s3_key`; Qdrant holds vectors plus a denormalised `chunk_text` copy. Qdrant is rebuildable from
    S3 + DynamoDB; the reverse is not true.
  - **Ingestion idempotency** is delete-by-`post_id`-filter then upsert, with deterministic
    `sha256(chunk_id)`-derived point IDs as a second line of defence. The pair is **not atomic**.
- Note (pre-existing, unchanged): retrieved user content is interpolated into the group/single system prompt — a known cross-tenant prompt-injection surface for the group path; out of scope.

## 12. Tests and Verification
- **Local (this task):** `python -m py_compile` (app.py, llm.py, all common/*) → OK; `python -m unittest common.test_tracing` → **13 passed**; ask import (tracing disabled, offline) → `_NoopTracer`; **frontend `vite build`** → OK (dist/); **ask Docker sanity build** → OK; **CI x86 build** (GitHub Actions run 32503979072) → success.
- **AWS smoke (this task):** cold-start of the new image logged `langsmith tracing enabled`; routing probes through CloudFront for all rev-5 + new endpoints → 401/422 (reach Lambda, never 404); `GET /` → 200; CloudFront invalidation → Completed; CloudWatch scan → **no AccessDenied / ResourceNotFound / ImportError / tracing failures**.
- **Not performed (no creds):** authenticated functional smoke (login → ask / search / follow / group / group-ask) and live-LangSmith-trace + CloudWatch↔LangSmith correlation. See §13/§16.

## 13. Deployment State

> **2026-08-24:** `multitenant-ask` now runs image `sha256:3ada41fc…` (commit `4126661`) with
> **`ROUTED_RAG_ENABLED=true`** — DEPLOYED / AWAITING MANUAL AUTHENTICATED SMOKE. Rollback:
> flag→false (instant), then image `sha256:2791dfa4…` (`d5af30e`). See
> `PRODUCTION-ROUTED-RAG-DEPLOYMENT.md`.
**Currently running in AWS (verified 2026-08-23):** `main` @ `d5af30e`. ask Lambda on image `d5af30e…` with `MAX_LLM_CONTEXT_CHUNKS=5`; social DynamoDB tables ACTIVE; scoped IAM applied; LangSmith env + secret wired and tracing enabled at init; new frontend live behind CloudFront. All rev-5 + social/search + global-search routes reachable; no init/IAM/import errors in logs.

**Verified but only at routing/infra level (functional behaviour pending authed smoke):** follow/unfollow, groups, group ask, global search read/write against the new tables under the new IAM.

**PENDING USER SMOKE QUERY:** LangSmith backend actually receiving traces for `ask_request` / `group_ask_request` / `global_search_request`; redaction-in-practice; request_id correlation across CloudWatch + LangSmith (+ DDB usage for /api/ask).

## 14. Known Limitations
- Authenticated functional smoke + live-trace verification not done in-session (no `MTR_SMOKE_EMAIL`/`MTR_SMOKE_PASSWORD`).
- Group ask stateless unless `chat_id`; no semantic cache (cross-tenant by design).
- No vector migration for social (group=MatchAny, global=no filter on existing collection).
- `retry_count` (Groq 429) not surfaced to traces.
- Trace-shape note: `/api/ask` `dense_embedding` sits under `semantic_cache` (query embedded once, reused) — forced by no-behaviour-change.
- ingest_worker not traced (future phase).
- LangSmith may show Groq cost as $0 (its price table).
- **Bedrock Titan quota is low for burst ingestion.** The 50-post seed (~2,053 chunks) threw `ThrottlingException` on `InvokeModel` even at 2 concurrent workers; all throttles self-recovered via SQS retry (idempotent upsert), but ingestion was slow. See §15.
- **Confirmed follow-ups (NOT implemented — do not treat as done):**
  1. **SQS DLQ + redrive policy** on `multitenant-ingestion.fifo` (currently none; failed messages retry only until the 4-day `MessageRetentionPeriod`).
  2. **Bedrock-aware ingestion backpressure / concurrency control** (self-throttle to the Titan quota instead of relying on SQS retry).
  3. **Batch the embeddings** — `ingest_worker._embed_dense_batch` currently makes one `InvokeModel` call per chunk (~40/post); investigate batching to cut request volume.

## 15. Recent Changes

### 2026-08-26 — Full-width desktop UI, writing workspace, profile identity, conversational chat
- Fixed the narrow desktop shell at its root: `max-w-feed` (760px) removed from the header
  and `<main>`; now full width with gutters.
- Write page rebuilt: tall editor (`min-h-[60vh]`, 560px floor), 14-action Markdown toolbar,
  Cmd/Ctrl shortcuts, one Publish at the end of the flow, secondary "Your Posts" rail, tags.
- New safe Markdown renderer supporting the full element set. Underline via a single narrow
  `<u>` marker with **no raw HTML enabled**; link schemes allow-listed — sanitisation
  strengthened, not weakened.
- Added optional public `username` + editable email. Uniqueness via atomic reservation
  items in the existing users table — no new table/GSI/key change. `user_id`/`tenant_id`
  never move; posts, chats, follows, groups, S3 paths and Qdrant points are unaffected.
  No password UI in Profile.
- Routed Ask is now a 50/50 desktop workspace; group ask reuses the same chat shell.
  Per-question scope is snapshotted onto the user message so history never re-reads the
  live selector; legacy messages without a snapshot still render.
- **RAG untouched** (frozen prompt hashes verified). Backend changes limited to
  profile + chat scope metadata.
- 364 tests pass (99 new frontend via newly-added Vitest, 24 new backend, 241 existing).
- **NOT DEPLOYED**; username backfill prepared but NOT run.

### 2026-08-26 — Curated 25-user / 268-post seeded corpus ingested; legacy seed-20260822 removed
- Ingested the curated corpus through the supported application path: **25 personas, 268 posts,
  268/268 indexed**, 0 conflicts, 0 failures. Fidelity verified: corpus dates as `created_at`
  @ 00:00 UTC 268/268, exact tags 268/268, S3 body SHA-256 268/268, Qdrant points 268/268.
- Identity: `<corpus-username>@example.com` + HMAC-derived per-persona passwords held in memory
  only. **No username schema field added** (profile/UI task). `tags` added as an optional
  DynamoDB attribute — no key/GSI/LSI/IAM/API-GW/Qdrant change.
- S3 markdown is the **exact** corpus body — no Date/Tags/marker injection.
- Removed the legacy `seed-20260822` population on positive provenance: 6 users, 6 tenants,
  50 posts, 50 S3 objects, 12 memberships, all Qdrant points. Zero residue, zero errors.
- **5 UNKNOWN_REVIEW accounts left untouched** and hard-blocked from the delete set in code.
- Titan throttled 8× during ingest and self-recovered; no queue config changed; **DLQ still
  absent and still the top open P0**.
- Tooling: `tools/{corpus_parser,corpus_identity,corpus_dates,seed_curated_blog_corpus,
  cleanup_legacy_seed}.py` + 31 tests. Cleanup is dry-run by default.
- These are **curated seeded demo users**, not real users.

### 2026-08-24 — Routed LangGraph RAG DEPLOYED to production (awaiting manual smoke)
- Deployed x86_64 image `sha256:3ada41fc…` (tag/commit `4126661`, CI run `32764221478` on native
  amd64 GitHub runners) to `multitenant-ask` **by digest**. Old image `d5af30e…`
  (`sha256:2791dfa4…`) preserved as rollback.
- Two-stage: deployed with `ROUTED_RAG_ENABLED=false`, verified the legacy path healthy
  (`/health` 200, warm ~0.37 s, 0 import errors, LangSmith → `multitenant-rag-prod`), then set
  `ROUTED_RAG_ENABLED=true`. `Active`/`Successful` at 19:24:00Z.
- Env handled by rebuilding from a captured baseline + exactly one addition, asserted so no
  existing variable could be dropped: **14 → 15 vars, 0 dropped**. Timeout, memory, IAM role,
  API Gateway, CloudFront, Secrets Manager, Qdrant config, Groq models and context cap all
  unchanged.
- Preflight all-pass: 210 tests (152 routed + 19 release/security + 39 pre-existing), release
  guard clean, archive 0 `.env`, secret scan clean, image `d5af30e` confirmed pre-deploy, API
  Gateway 30,000 ms, ask timeout 60 s.
- **NOT declared successful** — no authenticated request made. Awaiting the user's manual smoke.
- DLQ deferred by architect decision; still the top open ingestion P0.
- Report: `PRODUCTION-ROUTED-RAG-DEPLOYMENT.md` (20 sections).

### 2026-08-25 — Titan budget accepted (total ≤4) + release-security hardening; DEPLOYMENT GATED
- **Titan total ≤4 per request accepted** = 1 semantic-cache probe + ≤3 retrieval embeddings.
  `titan_embeddings_total` is now an ENFORCED bound; ambiguous `titan_embeddings` name removed.
  Scenarios pinned by tests: simple **1**, 2-branch compound **3**, 3-branch compound **4**,
  group **3**.
- **Fixed a real inefficiency**: the routed simple path re-embedded instead of reusing the cache
  probe vector (2 Titan calls vs production's 1). Now reused, gated on exact text equality so
  folded follow-ups and branches still embed their own text.
- **Release security hardened, fail-closed, two controls**: `tools/release_guard.py` (PATH rule —
  no `.env` may enter an archive at any depth; one-entry allowlist) and a hardened
  `tools/secret_scan.py` (unquoted `NAME=VALUE` in any file type, Authorization headers, more AWS
  and provider shapes, entropy; never prints values). `tools/build_release_archive.sh` deletes the
  archive if either trips — verified with a planted secret.
- Caught a bug in my own guard via its tests: `lstrip("./")` strips CHARACTERS, so root-level
  `.env` became `env` and passed. Fixed + regression test.
- Archive rebuilt and verified **by extraction**: 133 entries / 121 files, **0 `.env`**, both
  controls CLEAN. An apparent old-key match was a false alarm (shared JWT header prefix).
- **DEPLOYMENT BLOCKED** pending user confirmation of Qdrant key rotation+revocation and local dev
  `JWT_SECRET` replacement. Production `multitenant/jwt` NOT implicated. No history rewrite.
- **281 tests pass.** Groq/Titan/Qdrant/NVIDIA/RAGAS/DeepEval calls: 0. AWS mutations: 0.
  Production unchanged (`LastModified 2026-08-22T18:27:50Z`, image `d5af30e`).

### 2026-08-24 — SECURITY: real `.env` was included in the committed release archive (rotate)
- **`multitenant-rag-current.zip` in commit `60fe1e3` contains `multitenant-rag/local/.env`**, which
  holds a **real Qdrant API key** and the **dev JWT secret**. Repo is **public** ⇒ treat both as
  disclosed. The file is gitignored, so `git status` never flagged it; the `zip -x` list simply did
  not exclude `.env`, and the ad-hoc scan reported CLEAN because it only matched *quoted*
  assignments and known key prefixes.
- **Fixed forward:** archive now excludes `.env`/`.env.*`/`*.pem`/`*.key`/`credentials*`/`*.p12`/
  `id_rsa*`/`*.keystore`; absence of both live values verified by direct match. Added
  `multitenant-rag/tools/secret_scan.py` (examines **unquoted** `NAME=VALUE` in any file type +
  quoted literals, entropy + placeholder/safe-name allowlist) and
  `tools/build_release_archive.sh`, which **deletes the archive** if the scan finds anything.
- **No history rewrite, no force push** (forbidden) ⇒ the values remain in public history at
  `60fe1e3`. **USER ACTION REQUIRED: rotate the Qdrant API key and the dev `JWT_SECRET`.**
  Production JWT signing (Secrets Manager `multitenant/jwt`) was NOT in the archive.

### 2026-08-24 — Production routed-RAG hardening Phase 1 (implemented + tested, NOT deployed)
- Ported the validated routed LangGraph architecture into **`lambdas/ask/rag/`** (15 modules) behind
  **`ROUTED_RAG_ENABLED` (default false)**. Flag is read per request, so rollback needs no redeploy.
- Production code imports **nothing** from `evals/` (AST-asserted); frozen prompts carried
  byte-for-byte and hash-locked (`763d12cd82245285` / `ae8185181e88f25f` / `8c30bb9b064e6784`).
- Added `RequestBudget` (10 hard bounds, pre-call, thread-safe) and a **24 s monotonic deadline**
  inside the verified 30 s API Gateway limit.
- **Fixed two real audit gaps**: Bedrock/DynamoDB had boto3 DEFAULT timeouts and the Qdrant client had
  **none**. Both now bounded; Groq gets per-call ceilings clamped to remaining budget.
- 429 policy is reactive-only and deadline-gated; the 7 s experiment pacing was deliberately NOT
  carried into production.
- Scope hardened: immutable `Scope`, per-branch `assert_parity()`, fail-closed on empty/widened scope.
- Closed both offline replay caveats — real point objects for the compound prompt, real production
  prompt builders on the simple/fallback path.
- Found and fixed a routing defect: compound routing now requires `parse_ok`, so a verdict that
  violated the frozen schema can no longer spend a decomposition call and 3 branches.
- **140 new tests pass; 39 pre-existing still pass (identical at HEAD).** Local arm64 image builds,
  cold-imports in 901 ms, compiles the 14-node graph and serves `/health`.
- Groq/Titan/Qdrant/NVIDIA/RAGAS/DeepEval calls: **0**. AWS mutations: **0**. Production unchanged
  (`LastModified 2026-08-22T18:27:50Z`, image `d5af30e`).
- Reports: `PRODUCTION-ROUTED-RAG-HARDENING.md` (30 sections); `PRODUCTION-READINESS-GAP-MATRIX.md`
  updated (12 rows closed in code; **ingestion DLQ is now the top open P0**).

### 2026-08-24 — Groq 120B routed generation validation (all 18 cases) — offline, nothing deployed
- Ran the **deployed production generation model** (`openai/gpt-oss-120b`) on **all 18** persisted routed
  final contexts. Generation only: 0 calls to Router V2 / decomposition / LangGraph / Titan / BM25 / Qdrant /
  NVIDIA / RAGAS / DeepEval. Contexts verified **18/18 by `context_sha256`**.
- **18 logical / 18 physical** requests (ceiling 22), 18/18 first-attempt success, **0** 429/5xx/timeouts/retries.
- **Quality equivalent to NVIDIA 20B**: fact-atom coverage 0.653 vs 0.638 (5W/4L/9T); frozen reference-phrase
  coverage identical at 18/19. **Latency 3.3–5.1× faster** (1,476 ms mean vs 7,592 ms; 2,614 ms max vs 30,524 ms).
- **TPM economics**: 2,401 tokens/call vs an 8,000 TPM cap ⇒ 3.33 calls/min. 92% of wall clock was deliberate
  pacing, recorded separately and never counted as inference latency.
- Router V2 false positives (002/003/004/056/059) cost **verbosity, not correctness** — supports the
  high-recall policy. **case-041 fails identically under both models** ⇒ retrieval defect, not generation.
- Fixed a scorer defect: case-030 scored 0/2 for both models purely because the corpus spells integers out;
  added digit↔word equivalence (same class as the earlier U+2011 hyphen defect).
- Caveats stated in the report: prompt **scaffolding reconstructed** for all 18 (evidence text byte-verified);
  **3 fallback cases used a substituted system prompt**. NVIDIA observability gap still DEFERRED.
- Report: `GROQ120B-ROUTED-GENERATION-VALIDATION.md` (20 sections). Production **unchanged**
  (`LastModified 2026-08-22T18:27:50Z`, image `d5af30e`).

### 2026-08-23 — Groq production-provider observability: routed architecture is latency-feasible
- New dev LangSmith project `multitenant-rag-dev-groq-observability-v1` (prod untouched). Provider swap only;
  frozen prompts reused byte-identical. Phases A/B/C run; D/E not (93/100 Groq requests used).
- **Trace completeness 100%** (221 spans, 76 roots, 0 errors). Router equivalence: **identical aggregate
  metrics to NVIDIA** (FN 0, recall 1.000), 50/52 agreement. Decomposition: same 3 fallbacks, 17/18 same count.
- Groq is **5–11× faster** than NVIDIA per stage. Projected compound critical path **3.5 s mean / 4.7 s p95**
  vs the verified **30 s** API Gateway deadline → architecture GREEN.
- **Verdict YELLOW because of the free plan**: 8,000 **TPM** (not RPM) is binding; 429 backoff pushed observed
  p95 to 23.8 s. Zero 429s once paced at 7 s. Concurrency 2 healthy.
- NVIDIA observability gap confirmed (Titan/BM25/Qdrant/branch never separately measured). No NVIDIA calls.
- No deployment, no architecture change, no router tuning.

### 2026-08-23 — Code-grounded LLD + production-readiness audit (no provider calls)
- Produced `CURRENT-SYSTEM-LOW-LEVEL-DESIGN.md` (14 sections) and `PRODUCTION-READINESS-GAP-MATRIX.md`
  (26 capabilities), grounded in source and read-only AWS describes. **Zero inference/data-plane calls.**
- Verified the live-run accounting from artifacts rather than assuming: 18 decomposition + 15 `final_answer`
  + 3 `normal_answer` = **18 generations, 36 NVIDIA total**; 34 logical branches → 34 Titan / 34 BM25 /
  34 dense probes / 34 hybrid queries / **68 physical Qdrant `query_points`**. All counters matched.
- Confirmed Router V2's reason-code enum is exactly the 5 values in code.
- Recorded the audit clarifications in §11 above (no DLQ, no explicit timeouts/retries, two Qdrant calls per
  branch, route-dependent prefetch breadth, unused `HR_RE`, swallowed status-update error, store ownership).
- P0 gaps identified: feature/rollback switch, LangGraph+Router+decomposition port, scope-safety assertions,
  per-request budgets and explicit timeouts (graph p95 32 s vs API Gateway 29 s), DLQ, authenticated smoke.
- `case-041` recorded **LOW PRIORITY / BACKLOG**, explicitly not a production blocker. No deployment.

### 2026-08-23 — RAGAS + DeepEval evaluation STOPPED at calibration (pathological evaluator)
- Built the frozen 36-record eval set (18 baseline + 18 routed) from exact persisted contexts; evaluator
  `nemotron-3-nano-30b-a3b` available on one probe. Frameworks isolated in their own venv so the validated
  langgraph environment was untouched.
- Calibration on 6 diagnostic records met **four** pathology criteria: RAGAS faithfulness and context_recall
  0/6 usable; DeepEval faithfulness flat at 1.000 including on a false answer; RAGAS answer_relevancy
  inverted (weak 0.660 > strong 0.592); 15% provider error rate. Only 10/24 pairs comparable, mean
  disagreement 0.3769.
- **Full 36-record run NOT executed** (would have been ≈900 calls at 25/record). No framework deltas exist;
  none fabricated.
- `deepeval_contextual_recall` was the one metric that tracked reality (0.000→1.000 on case-018).
- Deterministic findings remain first-class. Provider: evaluator 151 calls, Titan 0, Qdrant 0, Groq 0,
  120B 0. No production change, no deployment.

### 2026-08-23 — Live routed LangGraph v2 run COMPLETE: 18/18, +13 context / +10 answer coverage
- Ran the real routed graph over all 18 persisted V2-compound cases. **All approved bounds respected**:
  Titan **34**/54, Qdrant physical **68**/108, NVIDIA 20B **36**/36, router 0, Groq 0, 120B 0.
- 0 provider errors, 0 retries, 0 empty branches. All 18 produced exactly 5 context chunks.
- Deterministic coverage baseline→routed: context **24→37 (+13)**, answer **16→26 (+10)**, zero regressions.
- All 5 V2 false positives preserved quality; 2 were rescued by a decomposition fallback.
- Frozen v1 analyzer declined 3 V2-compound cases → fallback to simple path. Rescued `case-056`/`case-059`
  but declined true compound `case-041`, where V2's diagnostic needs were correct and unused.
- Judge not probed (bound 0); 18 routed answers pending. No production change, no deployment.

### 2026-08-23 — Live routed run APPROVED but BLOCKED on a budget correction (my under-count)
- Pre-flight against the approved bounds found my Phase A Qdrant estimate was **2x too low**: each hybrid
  search issues **two** `qdrant.query_points` calls per branch (dense-only relevance probe + hybrid RRF), not
  one. Measured from `lambdas/ask/app.py`, not assumed.
- 18 compound cases at 2–3 branches → Titan **36–54** (≤54 ✅, the only billable item), NVIDIA 20B 18+18
  (✅), Qdrant physical **72–108** vs stated ≤54 (❌ under a literal reading).
- **Stopped before issuing any call.** Provider calls this task: NVIDIA 0, Groq 0, Titan 0, Qdrant 0.
  `DECISION REQUIRED` raised; corrected budget persisted. No production change, no deployment.

### 2026-08-23 — Routed LangGraph v2 implemented offline; live run blocked on Bedrock approval
- New graph (`routed_graph_v2.py`, `run_routed_graph_v2.py`, `test_routed_graph_v2.py` — **41 tests**)
  wiring frozen Router V2 to the validated decomposition mechanics. Frozen v1 nodes reused by import, not
  copied; `decomp_graph`/`router_v2..v4`/`verifier_v1` all unmodified.
- Fixed two defects in the new graph: a conditional edge returning a label instead of `list[Send]`
  (`KeyError: 'branch'` on every compound case), and a `get_graph()` rendering artifact that made the simple
  arm look like it flowed into the compound merge — verified functionally that it does not.
- **Zero provider calls**: NVIDIA 0, Groq 0, Titan 0, Qdrant 0. Six-case **artifact replay** only.
- Routing over 52: **34 simple / 18 compound**. Live budget: NVIDIA 20B 36, **Titan 36–54 (billable)**,
  Qdrant 36–54, Router V2 0.
- **STOPPED at the paid boundary: USER ACTION REQUIRED — APPROVE LIVE BEDROCK RETRIEVAL BATCH.**
- Eval env restored (`langgraph==1.2.11`, pinned in `requirements-eval.txt`); the 30 previously blocked
  decomposition tests now pass. 346 tests green. No production change, no deployment.

### 2026-08-23 — Router V2 high-recall holdout: **PASS** — accepted for offline graph integration
- Ran the exact frozen V2 once over the previously untouched 40-case holdout (hash verified pre-run; V2
  identity verified against its stored fingerprint). **28 tests.**
- **recall 1.0000 (20/20, zero false negatives), specificity 0.9500, precision 0.9524, accuracy 0.9750.**
  All five pre-registered gates passed, including compound-without-"and" recall 1.000 and
  contrast/verification specificity 1.000. Single error: `hold-001`.
- Same frozen V2 scored **better on unseen data than on development** (precision 0.6875→0.9524,
  specificity 0.8718→0.9500) — the redesign programme was chasing a problem that did not generalise.
- Caveat recorded: 95% Wilson lower bound on recall is 0.839; holdout authored by the same agent.
- **Holdout v1 now spent.** Larger model **NOT justified** — V2 hit recall 1.000 unseen at 20B.
- Provider: NVIDIA 20B 40, NVIDIA 120B 0, Groq 0, 40/40 parsed. No integration, no deployment.

### 2026-08-23 — Router cascade v1 (frozen V2 + strict verifier): DEVELOPMENT FAILURE
- New two-stage architecture (`verifier_v1.py`, `run_cascade_v1.py`, `test_cascade_v1.py` — **53 tests**).
  Stage A frozen V2 reused from checkpoint (**0 V2 calls** in dev); Stage B verifier on 18 candidates only.
- Precision 0.6875→**0.8182** ✅, specificity 0.8718→**0.9487** ✅, accuracy 0.900→**0.920**, but recall
  1.000→**0.8182** ❌ → **gate FAILED**; holdout not run and still unspent.
- Removed 3 of 5 V2 false positives; lost 2 true compounds (`case-007`, `case-047`) where 1 was allowed.
- All four errors contradict the verifier's own stated rules and codes it used correctly elsewhere —
  inconsistent application, not missing instruction. 2 of 6 reject codes never used.
- Verifier invoked on only 34.6% of questions; Stage B p50 1,921 ms, 220.7 output tokens — the small-schema
  choice measurably beat v3/v4 on both.
- Provider: NVIDIA 20B 18, NVIDIA 120B 0, Groq 0, 18/18 parsed, 0 errors.
- **Note:** the ephemeral eval virtualenv in `/tmp` was wiped mid-session by the OS; work continued in
  `multitenant-rag/.venv`. All checkpoints and manifests live in the repo and were unaffected (holdout hash
  re-verified). That venv lacks `langgraph`, so the 30 decomposition tests were **not** re-run this task.

### 2026-08-23 — Router v4 atomic evidence units: DEVELOPMENT FAILURE, holdout still unspent
- New isolated experiment (`router_v4.py`, `run_router_v4.py`, `test_router_v4.py` — **55 tests**).
  Verdict derived from evidence-unit count; boolean OR-stuffing now a parser-level rejection.
- **Gate not computable**: 3/50 cases returned no verdict (`unit_retrieval_query_empty`). On the 47
  parseable: precision **0.750**, recall **0.8182**, specificity 0.9167, accuracy 0.8936 → fails recall and
  precision anyway. No gate file written, so the holdout stage could not start.
- Fixed both targeted v3 failures (`case-030` → 3 units, `case-041` → 2 units); R1 and R3 categories
  0.667 → 1.000; also recovered `case-003`. Regressed `case-007`, `case-047` (FN) and `case-056`,
  `case-059` (FP); `case-002` unscorable.
- **Holdout NOT run and still unspent**, hash re-verified unchanged. v4 not modified after the gate, no v5.
- Provider: NVIDIA 20B 52, NVIDIA 120B 0, Groq 0, 0 provider errors, parse_ok 47/50.

### 2026-08-23 — Router v3 + frozen holdout: DEVELOPMENT FAILURE, holdout preserved unused
- New isolated experiment (`router_v3.py`, `run_router_v3.py`, `test_router_v3.py` — **57 tests**), plus a
  frozen 40-case unseen holdout (`build_holdout_v1.py`, `holdout_v1_cases.py`) authored BEFORE any v3 call.
- Retrieval-plan semantics: verdict is a parser-enforced property of the emitted minimal plan.
- Dev (50 scored): precision 0.6875→**0.9000**, specificity 0.8718→**0.9744**, accuracy 0.900→**0.940**,
  but recall 1.000→**0.8182** → **gate FAILED**; the runner refused the holdout automatically.
- Fixed 4 of 5 v2 false positives. New regressions: `case-030` (plan-count gaming via OR-stuffing — a
  design-level loophole) and `case-041` (contrast rule over-applied across unrelated domains).
- **Holdout NOT run and still unspent** — no `router_v3_holdout_results.jsonl` exists. v3 not tuned again,
  no router-v4 built, not integrated, not deployed.
- Provider: NVIDIA 20B 52, NVIDIA 120B 0, Groq 0, 0 errors, `parse_ok` 50/50.

### 2026-08-23 — Router ground-truth audit + zero-cost rescore: still NOT ACCEPTED
- Full semantic audit of all 52 generative questions under policy `independent-retrieval-needs-v1`; manifest
  frozen (sha256 `8b1111bc…`) BEFORE loading predictions. 39 simple / 11 compound / 2 ambiguous; 48 unique
  questions; 4 duplicate groups; validation gate aborts on any duplicate-label conflict. **31 tests.**
- **Zero provider calls** — NVIDIA 20B 0, NVIDIA 120B 0, Groq 0, retrieval 0, LangGraph 0. The 52 router
  predictions were re-scored, never re-run, and never mutated.
- `case-022`/`case-023` now share label `compound` (route does not change independent-need count).
- Precision 0.167→**0.6875**, specificity 0.694→**0.8718**, accuracy 0.712→**0.900**, recall **1.000**
  unchanged. Verdict **NOT ACCEPTED** (2 of 3 thresholds fail). Original scoring preserved, not overwritten.
- Of the original 15 FPs: 8 label defects, 5 genuine router errors, 2 ambiguous → ~53% of the apparent
  failure was measurement error. Real positive class is 11, not 3.
- Limitation recorded: the annotation was **not blinded**. Independent re-annotation is the recommended
  next step, then an unseen holdout, then a targeted router-v3. None built.

### 2026-08-23 — Compound Router v2 offline experiment: original scoring (SUPERSEDED)
- New isolated experiment (`evals/router_v2.py`, `run_router_v2.py`, `test_router_v2.py` — **51 tests
  passing**). 52 cases, recall 3/3, specificity 0.694 vs ≥0.90 target → **FAIL**, not integrated.
- Found the ground-truth label set is internally inconsistent (`case-022`/`case-023` byte-identical
  questions, opposite labels) — 100% accuracy unreachable by construction. See §9c.
- Preflight caught two of my own defects: a `max_tokens=400` truncation misreported as "no JSON", and a gate
  that printed PASSED with 0/3 controls actually fixed. Both fixed with regression tests; **the prompt was
  verified byte-identical before and after** (no tuning against observed failures).
- Frozen v1 untouched; 120B judge not probed (still 1/6). Groq 0. No production change.

### 2026-08-23 — Security: local dev credential removed from docker-compose (commit `3564dfb`, pushed)
- `blog-backend/docker-compose.yml` now derives both `POSTGRES_PASSWORD` and the app's `DATABASE_URL` from a
  single `${POSTGRES_PASSWORD:-localdev_only}` substitution — zero-config local dev still works, and a
  developer can override from an untracked `.env`. Verified both paths by implementing Compose's
  `${VAR:-default}` semantics (Docker is not installed on this host). README documents it as a disposable
  local fixture, not production-suitable. Scan after change: retired RDS password 0, previous local dev
  password 0.

### 2026-08-23 — Security: retired credential redacted from tracked docs (commit `85463b0`, pushed)
- Redacted 6 files; retired RDS password now **0 occurrences** in the tracked worktree. Examples converted
  to env-var form, not deleted. `blog-backend/docker-compose.yml` deliberately untouched (functional infra).
  No history rewrite. Documentation-only change — **no AWS deployment required**. See §11.

### 2026-08-23 — LangGraph experiment v1 frozen; judge still blocked
- Experiment v1 **FROZEN**; **graph reruns: 0** (durable fingerprint-matched checkpoint reused).
- Judge remains **1/6**: one architect-authorised bounded probe timed out at 60.3 s (1 request, 1 timeout).
  Not resumed, not re-probed, no Groq, no paid tier.
- Router verdict recorded without softening: **NOT production ready** (3/3 controls over-decomposed).
- Larger application model: **NOT justified yet**. Next experiment `compound-router-v2` — documented only.

### 2026-08-23 — LangGraph decomposition experiment (offline research only)
- Added `evals/decomp_graph.py` (LangGraph 1.2.11 StateGraph), `evals/run_decomp_experiment.py`
  (two-stage graph/judge, durable checkpoints), `evals/test_decomp_graph.py` (**30 tests, all passing**).
- Ran experiment `rag-agentic-decomposition-nvidia20b-v1` on 6 cases (3 compound + 3 controls) —
  graph stage 6/6 complete, 12 NVIDIA 20B requests, 0 errors, **Groq calls = 0**.
- Judge stage blocked at 1/6 by a NVIDIA 120B endpoint outage (11/12 timeouts; an 8-token probe did not
  return in 75 s). Checkpointed and resumable — no paid tier, no provider fallback.
- Bug worth remembering: a state key **not declared in the `TypedDict` state schema is silently stripped
  by LangGraph** — `target` went missing, tenant scope resolved empty, and every branch retrieved 0
  candidates while the graph reported success. Silent, not an exception. Two regression tests added;
  invalid records quarantined to `output/decomp_cases.INVALID_scope_bug.jsonl` and never reused.
- **No production change.** No deploy, no Lambda dependency change, no Qdrant/chunking/embedding change.

### 2026-08-22 — Groq model recovery (env-only)
- Incident: `/api/ask` failed after a period of disuse. Root causes (both external, not code/deploy): Qdrant Cloud free cluster had paused (all-paths 404 → 500) — user resumed it (v1.18.2, data intact); then the Groq key had gone invalid (401) — user rotated it. The new Groq account has **no Llama models**, so `llama-3.3-70b-versatile`/`llama-3.1-8b-instant` returned `model_not_found`.
- Fix (architect-approved, env-only, no code): `GROQ_MODEL=openai/gpt-oss-120b`, `GROQ_MODEL_SMALL=openai/gpt-oss-20b`. Compatibility pre-verified against the exact `stream_answer` parser (streaming OK; answer on `delta.content`; reasoning in a separate field, not leaked). Verified in prod: answered path on gpt-oss-120b (request 388b8d41: trace + citation + DDB usage, 3-way correlated); small path on gpt-oss-20b. Backend-verified for ask_request; group/global roots pending a successful browser action.

### 2026-08-22 — PROD synthetic-data seed (50 posts) via the real ingestion pipeline
- **Seeded 6 profiles + 4 groups (12 memberships) + 50 posts** into prod, all tagged `seed-20260822` (display names `"<Author> (seed-20260822)"`; emails `testuserpk1..6@gmail.com`, password==email). Source: user-supplied `synthetic_rag_50_blogs_500_lines_each.md` (6 authors, 4 groups: Orchid Lab / Helix Society / Harbor Guild / Field Circle, with cross-membership overlaps for group/global RAG testing).
- **Real pipeline exercised** (no shortcuts): posts via `common.posts.create_post` → S3 → DDB → SQS FIFO → `ingest_worker` → Titan dense + BM25 → Qdrant. Users/tenants seeded direct-to-DDB (verified schema, `attribute_not_exists` guards); groups via `groups.create_group`/`add_member`.
- **Result:** 50/50 `indexed`, 0 permanent failures; **~2,053 chunks** (~41/post) → **Qdrant `multitenant_chunks` grew 4 → 2,057 points**; per-tenant DDB `chunk_count` == Qdrant point count (consistent). No vectors inserted directly (architect requirement met).
- **Operational finding — Bedrock throttling.** Unconstrained tenant-parallel ingestion (6 FIFO groups → 6 concurrent workers × ~40 sequential `InvokeModel`) caused `ThrottlingException`. Per architect approval, temporarily set the ingest_worker SQS ESM `MaximumConcurrency=2` (baseline was **unset**; ESM `60e4e50a-…`). Throttling **reduced but not eliminated** (dropped to 0 briefly, then intermittent 2–4 log-lines/2min — evidence the account's Titan quota is low). All throttles self-recovered; **0 permanent failures**. Total ~60 throttle log-lines. Seed wall-clock **~48 min** (10:16→11:04), ~1 post/min effective.
- **Config restored:** ESM `ScalingConfig` set back to **unset** (verified null, BatchSize 1, Enabled) — matches captured baseline. No code, ingestion-algorithm, reserved-concurrency, or Bedrock changes.
- **Cleanup artifact:** `SEED-MANIFEST.json` (repo root) lists every synthetic user/tenant/post/group/member id + S3 key for exact deletion.
- Follow-ups recorded in §14 (DLQ, quota-aware backpressure, embedding batching) — **not implemented**.

### 2026-08-21 — Deployment (prior task)
- **Deployed unified `main` (951a6ae) to AWS**, resuming after the credential blocker was resolved (user created `multitenant/langsmith`).
- PHASE 2: created `multitenant-follows`, `multitenant-groups`, `multitenant-group-members` (schemas/GSIs verified ACTIVE). PHASE 4: added scoped inline IAM `multitenant-social-langsmith-access` to `multitenant-ask-role`. PHASE 6: pushed `main`→`origin/main`. PHASE 7: CI built + pushed `multitenant-ask:951a6ae…` to ECR. PHASE 8: deployed that immutable image (digest `sha256:99d8146f…`). PHASE 9: merged env (added `LANGSMITH_TRACING`/`LANGSMITH_PROJECT`/`ENVIRONMENT`; preserved all 10 existing; no key as env var). PHASE 10: cold-start clean, tracing enabled. PHASE 11: frontend synced + CloudFront invalidated (Completed). PHASE 12/16: routing + regression probes pass; logs clean.
- No code changed in this task (deployment only). No LangChain/LangGraph/OTel/X-Ray/WAF/Terraform added.

## 16. Decisions Pending
None. (Outstanding items are **USER ACTION**, not decisions: run an authenticated smoke query so LangSmith backend delivery + CloudWatch correlation can be verified — see the deployment report's USER ACTION REQUIRED.)
