# Production Readiness Gap Matrix

Audit 2026-08-23, grounded in code and read-only AWS describes. **Zero provider/inference calls.**
No AWS mutation, nothing deployed.

Column meanings — **A** current production (live `d5af30e`), **B** offline-validated (frozen, never
deployed), **C** desired production target.

Priority: **P0** blocks enabling routed RAG · **P1** needed soon after · **P2** hardening · **P3** backlog.

---

## Matrix

| # | Capability | A — Current production | B — Offline validated | C — Desired production | Gap | Priority |
|---|---|---|---|---|---|---|
| 1 | **Router V2** | none — deterministic if/else, every query takes one path | frozen V2; holdout recall **1.000**, specificity 0.950, precision 0.952; 52-case dev routing 34 simple / 18 compound | V2 as the graph's routing node behind a switch | port to a Lambda-importable module; add prompt-SHA assertion at cold start | **P0** |
| 2 | **LangGraph** | **not present in any image**; no `langgraph` line in the three `requirements.txt` | `langgraph==1.2.11` eval-only, pinned in `evals/requirements-eval.txt`; 41 graph tests | added to the ask image; graph owns routing/retrieval/merge/generate | add dependency (+cold-start and image-size check); wire FastAPI → graph | **P0** |
| 3 | **Decomposition** | none | frozen v1 analyzer; 18/18 live; 0 malformed decompositions; 3 fallbacks to simple | compound path only, unchanged prompt | port node; keep the `<2 subquestions → simple` fallback | **P0** |
| 4 | **Scope safety** | enforced in the deterministic path (`MatchValue` / `MatchAny`) | `resolve_scope` + per-branch inheritance; tests prove single/multi/group preserved and **no branch can widen** | identical, plus a graph-level assertion | port tests as production tests; assert scope non-empty before any retrieval | **P0** |
| 5 | **Context cap** | `MAX_LLM_CONTEXT_CHUNKS=5`, live and verified | exactly 5 chunks in all 18 live cases | unchanged | none — already identical | — |
| 6 | **Citations** | `_dedupe_citations` / `_dedupe_citations_attributed` from the same capped list | invariant held in all 18 (citations ≤ chunks) | unchanged, asserted in the graph | add an explicit runtime assertion rather than relying on convention | **P2** |
| 7 | **Request budgets** | **none** — no per-request ceiling on retrievals, embeddings or LLM calls | experiment-level counters with hard caps (proven: stopped a run pre-breach) | per-request budget: ≤3 branches, ≤1 decomposition, ≤1 generation, wall-clock deadline | port the counter pattern into request scope; reject/degrade on breach | **P0** |
| 8 | **Timeouts** | Groq 60 s; **Bedrock/DynamoDB boto3 defaults**; **Qdrant client defaults**; Lambda 300 s | NVIDIA per-call timeouts (120–180 s) | explicit `botocore.Config` timeouts, explicit Qdrant timeout, request deadline < API Gateway 29 s | no `Config(` or `retries=` anywhere in `lambdas/`; Qdrant constructed with no timeout | **P0** |
| 9 | **Bounded retries** | Groq 4 attempts, **429 only** (5xx raises immediately); Bedrock/Qdrant boto3/client defaults; ingestion relies on SQS redelivery | NVIDIA 4 attempts + 30/60/120 backoff + **circuit breaker on 3 consecutive 429s** + 6 s pacing | explicit bounded retry per dependency; retry Groq 5xx too; circuit breaker on the generation provider | add `botocore.Config(retries=…)`; extend Groq retry to 5xx; port the circuit breaker | **P1** |
| 10 | **Branch partial failure** | n/a | `retrieve_branch` catches per branch, records `evidence_missing`, run continues; **0 empty branches in the live run**; `branch_evidence_missing` surfaced | same, plus explicit degradation: if all branches empty → decline rather than answer | behaviour exists; needs a production policy for "all branches empty" | **P1** |
| 11 | **DLQ** | **NONE.** `RedrivePolicy` NOT SET on `multitenant-ingestion.fifo`; no Lambda DLQ | n/a | FIFO DLQ `multitenant-ingestion-dlq.fifo`, `maxReceiveCount=5`, 14-day retention | create DLQ + redrive policy. Today a poison message retries ~1,150× over 4 days and blocks its tenant group | **P0** |
| 12 | **Redrive** | none possible (no DLQ) | n/a | manual `StartMessageMoveTask` after fix; `RedriveAllowPolicy` byQueue | depends on #11; never automatic | **P1** |
| 13 | **Ingestion backpressure** | ESM `BatchSize=1`, `MaximumConcurrency=null` (unbounded fan-out); Titan is the constrained downstream | seeding used a temporary `MaximumConcurrency=2` successfully | `MaximumConcurrency` 2–5 permanently | set it; Titan throttling was already observed during seeding | **P1** |
| 14 | **Ingestion status integrity** | `_mark_indexed` swallows `ClientError` → post stays `pending` though chunks are indexed | n/a | retry/alarm on status-update failure | add bounded retry + a reconciliation query for `pending` posts with `chunk_count>0` | **P2** |
| 15 | **LangSmith graph spans** | root + per-flow spans, redacted, `flush()` on freeze | offline experiments traced separately | one child span per graph node (route, decompose, each branch, merge, generate) with whitelisted metadata only | extend `_ALLOWED_META` for route/branch fields; wire spans into nodes | **P1** |
| 16 | **RAGAS / DeepEval** | not used | calibration **STOPPED** — evaluator pathological (see #17) | offline-only quality gate with a reliable evaluator; never a runtime component | **blocked on evaluator reliability, not architecture** | **P2** |
| 17 | **Independent evaluator** | n/a | Nemotron-30B available but unusable: RAGAS faithfulness/context_recall 0/6, DeepEval faithfulness flat 1.000, RAGAS relevancy inverted, 15% error rate | a reliable-JSON instruct evaluator, not the application model | 120B remains unavailable; needs a different free model | **P2** |
| 18 | **Feature / rollback switch** | **none** | n/a | `ROUTED_RAG_ENABLED` env var, default off; flip to roll back without redeploy | implement flag + a no-op deploy path | **P0** |
| 19 | **Manual smoke tests** | ad-hoc curl probes; authenticated functional smoke was never completed | 18/18 live graph runs offline | scripted authenticated smoke: single / multi / group / compound / refusal / cache-hit | write the script; needs a test JWT | **P0** |
| 20 | **Deployment** | CI x86 image build → ECR → Lambda update, proven | n/a | same pipeline; routed code ships disabled, then enabled by flag | no new mechanism needed | **P1** |
| 21 | **Semantic cache correctness** | separate Qdrant collection, cosine ≥ 0.95, tenant-filtered; empty/error answers no longer cached | n/a | `validate_answer` gates `write_cache` inside the graph | port the existing guard into the graph edge | **P1** |
| 22 | **Prompt-injection surface (group path)** | retrieved user content is interpolated into the group/single system prompt | unchanged (graph reuses the same prompt builders) | delimit/neutralise retrieved content; treat as data | pre-existing, unrelated to routing; **decompose widens the blast radius** (more tenants' chunks per answer) | **P1** |
| 23 | **Global search tenant filter** | `app.py:1356` queries with **no tenant filter** by design | n/a | explicit decision + documented justification | pre-existing; confirm intent | **P2** |
| 24 | **Chunking strategy** | deterministic markdown/char chunker (500/50, chars÷4) | same — unchanged across all experiments | unchanged for now | semantic chunking would invalidate every measurement and needs a separate collection + Titan budget | **P3** |
| 25 | **Git history credential** | tip clean (retired RDS password 0, old dev password 0) | n/a | history scrub if the repo is ever published | historical commits may still contain the retired credential | **P3** |
| 26 | **`case-041`** | n/a | Router V2 routes compound correctly and its `information_needs` are right, but the frozen analyzer returns zero subquestions → falls back to simple; coverage stayed 2/3 | possible future: use V2's needs when the analyzer declines | **LOW PRIORITY / BACKLOG — explicitly NOT a production blocker** | **P3** |

---

## P0 set — what actually blocks enabling routed RAG

1. **#18 feature/rollback switch** — nothing should ship without the off-switch.
2. **#2 LangGraph in the ask image** + **#1 Router V2** + **#3 decomposition** ported.
3. **#4 scope-safety assertions** carried over as production tests — the one invariant with a
   tenant-isolation consequence.
4. **#7 per-request budgets** and **#8 explicit timeouts** — the routed path multiplies work per request
   (up to 3 Titan + 6 Qdrant + 2 LLM calls versus 1 + 2 + 1), and API Gateway caps at 29 s while the
   observed offline graph mean was 18.8 s with a p95 of 32 s. **This is the most likely production failure
   mode and it is a latency/budget problem, not a quality problem.**
5. **#11 DLQ** — independent of routing, but a live queue with no DLQ and a 4-day retry loop is the largest
   standing operational risk in the system.
6. **#19 authenticated smoke tests** — still never completed end-to-end.

## Explicitly NOT blocking

- **#16/#17 RAGAS/DeepEval** — framework evaluation is blocked by **evaluator reliability, not application
  architecture**. The deterministic evidence stands on its own (below).
- **#26 case-041** — backlog.
- **#24 semantic chunking** — no evidence it is needed; changing it invalidates every baseline.

## Deterministic evidence that stands regardless of framework calibration

| Finding | Value |
|---|---|
| Live routed graph executions | **18/18 succeeded**, 0 provider errors, 0 empty branches |
| Context reference-phrase coverage | **24 → 37 (+13)** |
| Answer reference-phrase coverage | **16 → 26 (+10)** |
| Answer-level regressions | **zero** |
| Final context | exactly 5 chunks in all 18 |
| Context/citation invariant | held in all 18 |
| V2 false positives | all 5 preserved answer quality |
| Router V2 unseen holdout | recall **1.000** (20/20), specificity 0.950, precision 0.952 |

These are not weakened by the RAGAS/DeepEval calibration failure.
