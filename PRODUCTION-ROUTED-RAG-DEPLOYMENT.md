# Production Routed-RAG Deployment

**Status: DEPLOYED / AWAITING MANUAL AUTHENTICATED SMOKE**
**Date:** 2026-08-25 (UTC 2026-08-24T19:24Z)
**Routed RAG:** `ROUTED_RAG_ENABLED=true` — LIVE, unverified by human smoke.

> This deployment is **NOT** declared successful. Routed RAG is enabled in production but
> no authenticated end-to-end request has been made. Acceptance requires the manual smoke
> in §15.

---

## 1. Objective

Deploy the Phase-1 hardened Routed LangGraph RAG image to `multitenant-ask`, verify the
legacy path on the new image with the flag **off**, then enable the flag and stop for
manual authenticated smoke testing.

No architecture change, no percentage rollout, no test users, no DLQ work.

---

## 2. Rotation gate — SATISFIED

| Item | Status |
|---|---|
| Qdrant API key rotated | ✅ `AWSCURRENT` `59568b60-875d-47c2-8ce8-e321d6fe8d24` |
| Old Qdrant key revoked at Qdrant Cloud | ✅ |
| Local `.env` Qdrant key updated | ✅ digest differs from the exposed copy |
| Local dev `JWT_SECRET` replaced | ✅ digest differs; 64-char `token_urlsafe(48)` |
| Production `multitenant/jwt` | ✅ untouched — was never implicated |
| Behavioural verification with the rotated key | ✅ global search `result_count: 5` at 19:05:52Z; hybrid retrieval at 19:06:21Z; **0 auth errors** |
| Any exposed value still live | ✅ **NONE** |

Verification used SHA-256 digests only — no secret value was printed, logged, stored or
compared as plaintext.

---

## 3. Preflight — ALL PASS

| Check | Result |
|---|---|
| `main` clean except the known `.gitignore` change | ✅ 0 unexpected tracked changes |
| Deployment commit present, in sync with origin | ✅ `4126661` (0 ahead / 0 behind) |
| Production routed-RAG tests | ✅ **152 passed** |
| Release/security tests | ✅ **19 passed** |
| Pre-existing suite | ✅ **39 passed** |
| Release guard (forbidden paths) | ✅ CLEAN |
| Release ZIP `.env` count | ✅ **0** |
| Secret scan of extracted archive | ✅ CLEAN (121 files) |
| No evals import / no RAGAS / no DeepEval / no LangChain app import | ✅ (AST tests) |
| Production image before deploy | ✅ still `d5af30e…` |
| API Gateway `TimeoutInMillis` | ✅ **30000** (both integrations) |
| Ask Lambda timeout | ✅ **60 s** |
| Production environment captured | ✅ 14 vars preserved verbatim |

---

## 4. x86_64 build

The earlier local Colima build was **arm64 and not deployable**. The deployable image was
produced by the project's existing production build process — GitHub Actions
`build-lambdas.yml` on **native x86_64 runners** (`ubuntu-latest`, `--platform linux/amd64`,
no emulation), pushed to ECR via OIDC. This is the same Dockerfile and pipeline that built
every previously deployed image.

CI run: `32764221478`, commit `4126661`, conclusion **success**, 2026-08-24T18:45:33Z.

Validation of the actual pulled artifact:

| Check | Result |
|---|---|
| Architecture | ✅ **amd64 / linux** |
| `machine` at runtime | ✅ `x86_64` |
| `langgraph` imports | ✅ |
| FastAPI app cold-import | ✅ 36 routes |
| Routed `rag` package imports | ✅ |
| LangGraph graph compiles | ✅ 14 nodes |
| Frozen prompts assert | ✅ `763d12cd82245285` / `ae8185181e88f25f` / `8c30bb9b064e6784` |
| `GET /health` in container | ✅ `{"ok":true}` |
| `ROUTED_RAG_ENABLED` default when unset | ✅ **False** |
| Models resolved | ✅ `20b` / `20b` / `120b` |
| `REQUEST_DEADLINE_MS` | ✅ **24000** |
| Titan bounds | ✅ retrieval ≤3, semcache ≤1, **total ≤4** |
| Eval source in image | ✅ **none** |
| RAGAS / DeepEval / `langchain_community` / `langchain_openai` / `instructor` | ✅ **all absent** |
| `langchain_core` | ✅ present **only** as a LangGraph transitive dep; no top-level `langchain` |
| `/var/task/rag` contents | ✅ exactly 15 production modules, no test files |

**Caveat, stated honestly:** the amd64 image was executed on an arm64 host under QEMU for
these checks, so the observed **9.9 s cold import is emulation overhead and is not
representative of native Lambda**. The equivalent arm64 local build imported in 901 ms, and
the real deployed cold start measured **5.67 s** end-to-end including image pull (§8).

---

## 5. ECR image

```
repo:         multitenant-ask
tag:          41266619fe4b36bcccf3f0d2d6f8dc719bd8553f   (immutable, = git SHA)
also tagged:  latest, v1
digest:       sha256:3ada41fc85255adf544bb266b98ca62887b8639ea85f2b14c824005d530cfca0
architecture: amd64 / linux
size:         160,719,326 bytes
pushed:       2026-08-25T00:16:37+05:30
git commit:   41266619fe4b36bcccf3f0d2d6f8dc719bd8553f
```

Deployed **by digest**, not by tag, so the deployed artifact is pinned even if `latest`/`v1`
later move.

---

## 6. Previous rollback image — PRESERVED

```
tag:          d5af30e7f4cc679b2625d6a623d4a7857b1f8094
digest:       sha256:2791dfa4059193ad5488402eda3dcc9caa4aa51d91167fee47cfd7e6addb3e48
LastModified: 2026-08-24T19:04:51Z
RevisionId:   86570e99-30c4-44ab-a14d-732022ae1c38
env:          14 variables (captured verbatim)
timeout/mem:  60 s / 2048 MB
```

Not deleted. This is the secondary rollback point.

---

## 7. Lambda update

| Step | Result |
|---|---|
| `update-function-code` to the new digest | ✅ `Active` / `Successful`, 19:22:17Z |
| `update-function-configuration` adding `ROUTED_RAG_ENABLED=false` | ✅ `Active` / `Successful` |
| Env preservation | ✅ **14 → 15 vars, 0 dropped** (asserted before the call) |
| Unchanged | ✅ timeout 60 s, memory 2048 MB, `x86_64`, IAM role, API Gateway, CloudFront, Secrets Manager, Qdrant config, Groq models, context cap |

The environment was rebuilt from the captured baseline plus exactly one addition, with an
assertion that no existing variable could be dropped — never a partial subset.

---

## 8. Flag-false verification

| Check | Result |
|---|---|
| `GET /health` through API Gateway → Lambda | ✅ **200** `{"ok":true}` |
| Cold start (fresh image pull) | 16.6 s wall / **5.67 s** billed Lambda duration |
| Warm `/health` × 3 | ✅ 0.394 s / 0.393 s / 0.359 s |
| Container init | ✅ uvicorn started, "Application startup complete" |
| LangSmith init | ✅ `langsmith tracing enabled project=multitenant-rag-prod` |
| Import errors / tracebacks | ✅ **0** (`ImportError`, `ModuleNotFound`, `Runtime.ImportModuleError` all absent) |
| Routed graph executed | ✅ **no** router/decomposition/routed activity in logs |
| Max memory used | 270 MB of 2048 MB |

**The langgraph-in-production import risk is retired** — the new dependency loads cleanly on
real x86_64 Lambda.

**An authenticated request was deliberately NOT made:** no credentials are available to the
executor, and creating a production user or requesting a password is prohibited. Per the
instruction, flag-false authenticated verification is covered by automated tests
(`test_endpoint_integration` asserts flag=false → legacy path and `rag_graph.run` never
called) and is otherwise left to the user's smoke.

---

## 9. Flag enable

```
update-function-configuration  ROUTED_RAG_ENABLED=true
State=Active  LastUpdateStatus=Successful  LastModified=2026-08-24T19:24:00Z
env vars: 15 (0 dropped)
post-enable GET /health: 200 in 4.25 s (re-init after config change)
```

---

## 10. Current Lambda configuration

```
Function:      multitenant-ask
Image:         ...multitenant-ask@sha256:3ada41fc85255adf544bb266b98ca62887b8639ea85f2b14c824005d530cfca0
Architecture:  x86_64
State:         Active / Successful
LastModified:  2026-08-24T19:24:00Z
Timeout:       60 s      Memory: 2048 MB
Env (15):      ROUTED_RAG_ENABLED=true
               GROQ_MODEL=openai/gpt-oss-120b
               GROQ_MODEL_SMALL=openai/gpt-oss-20b
               MAX_LLM_CONTEXT_CHUNKS=5
               RETRIEVAL_FLOOR=0.15
               LANGSMITH_PROJECT=multitenant-rag-prod
               LANGSMITH_TRACING=true
               ENVIRONMENT, LOG_LEVEL, CHATS_TABLE, POSTS_TABLE,
               TENANTS_TABLE, USERS_TABLE, USAGE_TABLE, S3_CONTENT_BUCKET
```

---

## 11. Request deadline

`REQUEST_DEADLINE_MS = 24000` (verified inside the deployed image) against API Gateway
`TimeoutInMillis = 30000` (unchanged, both integrations). 6 s of headroom for cold start,
LWA buffering, response assembly and network. API Gateway was **not** modified.

Per-call ceilings, each additionally clamped to remaining budget: router 6 s, decomposition
6 s, generation 12 s, Bedrock/DDB connect 3 s / read 8 s, Qdrant 8 s.

---

## 12. Provider configuration

| Stage | Model |
|---|---|
| Router V2 | Groq `openai/gpt-oss-20b` |
| Decomposition | Groq `openai/gpt-oss-20b` |
| Final generation | Groq `openai/gpt-oss-120b` |

No NVIDIA, no alternate provider, no paid upgrade, no second key.

**No production pacing.** The 7 s development pacing was test-only and is absent. The 429
policy is reactive and deadline-gated: retry only if the provider's own hint ≤ 2 s **and**
the remaining deadline permits, at most once, never a long in-request sleep.

Titan budget: semantic-cache ≤1 + retrieval ≤3 = **total ≤4**, enforced.

---

## 13. LangSmith configuration

`LANGSMITH_TRACING=true`, `LANGSMITH_PROJECT=multitenant-rag-prod` — confirmed active from
the deployed container's own startup log. No synthetic development traffic was sent.

Expected hierarchies once real traffic flows (actual implementation wins if it differs):

```
compound: ask_request -> semantic_cache, router_v2, decomposition,
                         retrieval_branch_N{titan_embedding, bm25_encode,
                         qdrant_dense_probe, qdrant_hybrid_rrf},
                         merge_evidence, build_context, groq_generation
simple:   ask_request -> semantic_cache, router_v2, retrieval_branch_0,
                         build_context, groq_generation
cache hit: ask_request -> semantic_cache            (NO router_v2 Groq call)
```

Privacy boundary unchanged: `hide_inputs=true`, `hide_outputs=true`, whitelist-only
metadata, fail-open. No question, answer, prompt, chunk, sub-question, history, email, JWT,
tenant id, secret or exception **message** can reach LangSmith.

---

## 14. CloudWatch verification plan (inspect AFTER the smoke)

Log group `/aws/lambda/multitenant-ask`. The `relevance` line carries the routed fields.

| Field | Meaning |
|---|---|
| `request_id` | correlates CloudWatch ↔ DynamoDB usage row ↔ LangSmith run id |
| `routed` | `true` when the routed graph ran |
| `answer_path` | `simple` / `compound` / `cache` |
| `result_type` | `answered` / `refused` / `cache_hit` / `empty_context` / `generation_error` / `provider_unavailable` |
| `branch_count` | retrieval branches (≤3) |
| `top_dense`, `floor` | absolute cosine vs `RETRIEVAL_FLOOR` |
| `hits` | final context chunk count (≤5) |
| `remaining_budget_ms` | deadline headroom left |

Useful queries:

```
fields @timestamp, request_id, routed, answer_path, result_type, branch_count, hits, top_dense, remaining_budget_ms
| filter msg="relevance" | sort @timestamp desc | limit 50
```

```
fields @timestamp, @message | filter @message like /429|rate limited|ProviderRateLimited|DeadlineExceeded|BudgetExceeded/ | sort @timestamp desc
```

No secrets or raw protected content appear in these logs by construction.

---

## 15. Manual smoke checklist — USER ACTION REQUIRED

See the response accompanying this document. Eight steps: simple ask, compound ask,
cache-hit repeat, group ask, citations, off-topic, saved-chat follow-up, basic UI.

---

## 16. Rollback procedure

**Primary — instant, no redeploy** (the flag is read per request):

```
aws lambda update-function-configuration --function-name multitenant-ask --region ap-south-1 \
  --environment file://<env-flag-false.json>
```

**Secondary — previous image:**

```
aws lambda update-function-code --function-name multitenant-ask --region ap-south-1 \
  --image-uri 557690605487.dkr.ecr.ap-south-1.amazonaws.com/multitenant-ask@sha256:2791dfa4059193ad5488402eda3dcc9caa4aa51d91167fee47cfd7e6addb3e48
```

Blast radius while enabled: only `/api/ask` and `/api/ask/group`. Global search, auth,
posts, chats, follows, groups and ingestion are untouched. No schema change, no new table,
no new queue, no new IAM permission — rollback needs no data migration.

---

## 17. DLQ — DEFERRED (architect decision)

The ingestion DLQ is **not** part of this deployment and was not created. Routed Ask
deployment and ingestion redrive are independent failure domains; combining them would
enlarge the blast radius and blur rollback attribution.

Order: routed deployment → manual smoke → CloudWatch/LangSmith inspection → keep or roll
back → **then** SQS FIFO DLQ + redrive + alarms as a separate task.

It remains the **top open ingestion P0**: `RedrivePolicy` is still NOT set on
`multitenant-ingestion.fifo`, so a poison message retries for the 4-day retention (~1,150
attempts) and blocks its tenant's FIFO group.

---

## 18. Security status

Rotation gate satisfied (§2). Release controls hardened and fail-closed: a path-based guard
(no `.env` may enter an archive at any depth) plus a content scan of the **extracted**
archive. Current archive: 133 entries / 121 files, **0 `.env`**, both controls CLEAN.

No history rewrite, no force push. The old credentials remain in public history at
`60fe1e3` and are inert (revoked/replaced). `AWSPREVIOUS` cleanup is explicitly **not** a
deployment blocker. No secret value was printed or inspected at any point.

---

## 19. Production status

**DEPLOYED / AWAITING MANUAL AUTHENTICATED SMOKE.**

Routed RAG is enabled and the function is healthy, but **no authenticated end-to-end
request has been made**, so routed behaviour is unverified in production. This is
explicitly **not** a success declaration.

---

## 20. User action required

Complete the manual authenticated smoke (§15). Report which steps passed/failed, noticeable
latency, any UI error, and request IDs if surfaced — **never credentials**. Post-smoke trace
inspection is deliberately deferred until those results arrive.
