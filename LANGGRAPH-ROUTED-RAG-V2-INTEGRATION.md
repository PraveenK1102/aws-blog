# LangGraph Routed RAG v2 — Live Offline Evaluation

Experiment `rag-routed-langgraph-v2-offline`, fingerprint `90a7d26b3e6b88d6`.
**18/18 compound cases executed live through the real graph. All approved bounds respected.**
Zero Groq calls, zero NVIDIA 120B calls, zero router calls. Production unchanged, nothing deployed.

---

## 1. Objective

Run the routed architecture for real: frozen Router V2 verdicts → frozen v1 decomposition → LangGraph `Send`
fan-out → production-equivalent Titan/BM25/Qdrant retrieval → coverage-aware merge → max-5 context → one
NVIDIA 20B generation.

## 2. Router evidence

Frozen Router V2, holdout: TP 20 · FN **0** · FP 1 · TN 19 → precision 0.9524, recall **1.0000**,
specificity 0.9500. Verdicts were **reused from disk** (`cc6dfde4be4dd586`); **0 router calls** this run.

## 3. Architecture — as executed

```
question → resolve_scope → route_question (injected frozen V2 verdict, 0 calls)
             └─ compound → decompose (frozen v1 analyzer)
                            ├─ ≥2 subs → Send fan-out → retrieve_branch ×N (Semaphore(2))
                            │              → merge_evidence (defer) → final_answer → END
                            └─ <2 subs → normal_retrieve → normal_answer → END   ← fired 3×
```

Retrieval behaviour untouched: per branch, 1 Titan dense embed + 1 dense-only relevance probe + 1 hybrid RRF
query + 1 local BM25 encode. `RETRIEVAL_FLOOR`, RRF, `TOP_K`, chunking, models all unchanged.

## 4. Execution success — 18/18

Every case completed. **0 provider errors, 0 retries, 0 empty-retrieval branches, 0 graph exceptions.**
NVIDIA provider stats: 36 requests, 36 successes, 0 timeouts, 0 429s, 0 circuit events.

## 5. Actual branch counts

| Branches | Cases | Which |
|---|---|---|
| 3 | 1 | case-030 |
| 2 | 14 | case-002/003/004/005/007/008/009/018/020/022/023/025/046/047 |
| 1 (fallback) | 3 | case-041, case-056, case-059 |

**34 logical branch retrievals** in total.

## 6. Decomposition fallbacks — the run's most interesting finding

Three cases were routed compound by V2 but the **frozen v1 decomposition analyzer returned 0 subquestions**,
so the graph took its documented fallback to the simple path:

| case | adjudicated truth | rule | outcome |
|---|---|---|---|
| `case-056` | **simple** | R6 scope check | ✅ V2 false positive **rescued** — never decomposed |
| `case-059` | **simple** | R7 series synthesis | ✅ V2 false positive **rescued** — never decomposed |
| `case-041` | **compound** | R1 cross-domain contrast | ❌ **true compound not decomposed** |

So the decomposition node behaved as an unplanned second-stage filter: it removed 2 of V2's 5 false
positives at zero extra cost, and it also refused one genuine compound.

**`case-041` deserves attention.** V2 routed it compound *and* produced exactly the right diagnostic needs —
`["Why is a blue ribbon appropriate at Sable Library?", "Why is a blue ribbon inappropriate in Copper Orchard
rover zones?"]` — but the frozen v1 analyzer declined to split it, so those needs went unused (they are
diagnostic-only by design). Its context coverage stayed at baseline 2/3. This is the first concrete evidence
that V2's `information_needs` would have outperformed the v1 analyzer on a real case; acting on it would
require architect approval, since using them as subquestions is currently forbidden.

## 7. Actual provider and retrieval counts

| Metric | Actual | Approved limit | Status |
|---|---|---|---|
| Router V2 calls | **0** | 0 | ✅ |
| NVIDIA 20B decomposition | **18** | ≤18 | ✅ |
| NVIDIA 20B final generation | **15** | ≤18 | ✅ |
| NVIDIA 20B total | **36** | ≤36 | ✅ (at the limit) |
| **Bedrock Titan embeddings** | **34** | ≤54 | ✅ |
| Qdrant **logical** branch retrievals | **34** | ≤54 | ✅ |
| Qdrant dense relevance probes | **34** | — | component |
| Qdrant hybrid-RRF queries | **34** | — | component |
| Qdrant **physical** `query_points` | **68** | ≤108 | ✅ |
| BM25 sparse encodes (local) | 34 | — | no external cost |
| Groq | **0** | 0 | ✅ |
| NVIDIA 120B | **0** | 0 | ✅ |

`final_generation` is 15 rather than 18 because the 3 fallback cases answered via `normal_answer` instead of
`final_answer`. Total NVIDIA is 36 either way (18 decomposition + 15 final + 3 normal). Titan came in at 34,
not the 36 minimum projected, for the same reason — the fallbacks each ran one branch instead of two.

## 8. Final context and estimated tokens

**All 18 cases produced exactly 5 context chunks** — the `MAX_LLM_CONTEXT_CHUNKS` invariant held everywhere.
Estimated context tokens ranged 1,670–2,456 (case-004 lowest, case-030 highest).

## 9. Context / citation invariant

✅ Holds in every case: context chunks ≤ 5 (all exactly 5), and citations ≤ context chunks in all 18
(1–5 citations per case). The same capped list fed the prompt and the citation set.

## 10. Baseline vs routed evidence coverage — deterministic

Reference-phrase coverage in the **retrieved context**, Unicode-normalised, over the 8 compound cases with
enumerable reference facts:

| case | truth | baseline ctx | routed ctx | Δ |
|---|---|---|---|---|
| case-018 | compound | 1/6 | **6/6** | **+5** |
| case-020 | compound | 4/7 | **7/7** | **+3** |
| case-022 | compound | 3/5 | **5/5** | **+2** |
| case-023 | compound | 3/5 | **5/5** | **+2** |
| case-030 | compound | 3/4 | **4/4** | **+1** |
| case-007 | compound | 5/5 | 5/5 | 0 (already complete) |
| case-041 | compound | 2/3 | 2/3 | 0 (fallback — not decomposed) |
| case-046 | compound | 3/4 | 3/4 | 0 |
| **TOTAL** | | **24** | **37** | **+13** |

## 11. Baseline vs routed answers — deterministic

Reference-phrase coverage in the **generated answer**:

| case | baseline | routed | Δ |
|---|---|---|---|
| case-018 | 1/4 | **4/4** | **+3** |
| case-023 | 0/3 | **3/3** | **+3** |
| case-020 | 3/5 | **5/5** | **+2** |
| case-022 | 1/3 | **3/3** | **+2** |
| case-007 / case-030 / case-041 / case-046 | at ceiling | unchanged | 0 |
| **TOTAL** | **16** | **26** | **+10** |

**Zero answer-level regressions.** Every gain is a case where decomposed retrieval surfaced evidence the
single query missed, and the 20B generator then used it.

## 12. V2 false-positive regression analysis

The 5 adjudicated-simple cases that V2 routed compound:

| case | path taken | baseline ans | routed ans | verdict |
|---|---|---|---|---|
| case-002 | compound | 4/4 | 4/4 | **preserved** |
| case-003 | compound | 1/2 | 1/2 | **preserved** |
| case-004 | compound | 3/4 | 3/4 | **preserved** |
| case-056 | simple (fallback) | 2/2 | 2/2 | **preserved** |
| case-059 | simple (fallback) | 3/3 | 3/3 | **preserved** |

**No false positive caused a quality regression.** Three took the decomposition path and still answered
correctly; two never decomposed at all. The measured cost of the 3 unnecessary decompositions was 3
decomposition calls, 6 Titan embeddings, 12 physical Qdrant queries and 3 generations.

## 13. Latency and token overhead

| Metric | Value |
|---|---|
| End-to-end graph | mean **18,764 ms** · p50 17,152 · p95 32,019 · max 39,274 |
| `decompose` | mean 7,329 ms (n=18) |
| `final_answer` | mean 8,362 ms (n=15) |
| `normal_answer` (fallback) | mean 3,744 ms (n=3) |
| `normal_retrieve` (fallback) | mean 929 ms (n=3) |
| `merge_evidence` | mean **0.1 ms** (n=15) |
| Decomposition tokens | 235.9 in / 222.8 out mean · **8,257 total** |
| Generation tokens | 1,973.8 in / 426.3 out mean · **43,202 total** |

Orchestration is free — the merge is 0.1 ms. The overhead versus the single-query baseline is one extra LLM
call (~7.3 s) plus N−1 extra retrievals. Excludes 48.3 s of deliberate free-endpoint pacing.
**Free-tier NIM measurement — not a production latency estimate.**

## 14. Provider errors and retries

**None.** 36/36 NVIDIA successes, 0 timeouts, 0 429s, 0 retries, 0 circuit-breaker events. All 34 Titan
embeddings and 68 Qdrant queries succeeded. No case required resume.

## 15. Judge status

**NVIDIA 120B calls: 0 — not probed** (hard bound). Judge-dependent metrics remain pending:
decomposition experiment stands at **1/6 scored, 5 pending**, and the 18 new routed answers are
`pending_120b_unavailable`. No judge scores were fabricated. All application outputs are persisted, so
judging later requires no re-execution.

## 16. Cost interpretation

**Bedrock Titan query embeddings are the only AWS-billable bounded resource: 34 calls, limit 54.** Qdrant is
the existing free-tier managed cluster; raising its accounting bound from 54 to 108 was a physical-request
accounting correction, not an additional paid-provider approval. No monetary figure is quoted — no reliable
current pricing source was consulted and none was requested.

## 17. Limitations

1. **Judge scores pending for all 18 routed answers.** Correctness/completeness/groundedness are unmeasured;
   §10–§12 are deterministic phrase proxies, which under-count paraphrase and word-form numbers.
2. **Only 8 of 18 cases have enumerable reference phrases**, so the coverage totals cover less than half the
   batch.
3. **The 34 V2-simple cases were not re-executed** — their baseline artifacts are reused, so this run says
   nothing new about them.
4. **`case-041` shows the decomposition node can veto a correct route.** One true compound bypassed
   decomposition; the sample is one case.
5. **Dev labels remain single-annotator and non-blinded** (standing ground-truth caveat).
6. **Single run, no repeat sampling** — the three fallbacks may not be stable across runs.
7. **Branch cap 3** inherited from frozen v1.
8. **Latency is free-tier**, and no production SLO has been set for an agentic path.

## 18. Production status

**Unchanged. Nothing deployed.**

| Item | State |
|---|---|
| Deployed ask image | `d5af30e` — untouched |
| Production model | Groq `openai/gpt-oss-120b` — untouched |
| `MAX_LLM_CONTEXT_CHUNKS` | 5 — unchanged |
| `TOP_K` / floor / chunk / overlap / RRF / BM25 / Titan model / Qdrant / semantic cache / tracing | unchanged |
| Lambda requirements | unmodified — no langgraph, no langchain |
| LangGraph | **OFFLINE ONLY**, not deployed |
| Router V2 / V3 / V4 / verifier / decomp_graph | all frozen, sources unmodified |
| Holdout v1 | spent, manifest unmodified |
| NVIDIA 120B judge | 1/6 scored, 5 pending — not probed |

## 19. Next recommendation

Documented only; nothing actioned.

1. **Judge the 18 routed answers when the 120B endpoint recovers.** Everything is persisted; reuse baseline
   scores for the 34 unchanged simple cases → ≤18 judge calls for a full 52-case routed score.
2. **Consider letting V2's `information_needs` drive decomposition when the v1 analyzer declines.**
   `case-041` is direct evidence this would help: V2's needs were correct and went unused. This is an
   architecture change and needs approval.
3. **The decomposition node is an accidental false-positive filter** — it rescued 2 of 5. Worth measuring
   deliberately rather than relying on it.
4. **Not indicated:** router changes, a larger model, retrieval changes, or deployment.
