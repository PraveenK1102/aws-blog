# Router V2 High-Recall Holdout Evaluation

**Outcome: ROUTER V2 HIGH-RECALL HOLDOUT = PASS.**
**ROUTER V2 ACCEPTED FOR OFFLINE LANGGRAPH INTEGRATION** — this is not production approval.

Router-only evaluation of the exact frozen Router V2 against the previously untouched 40-case holdout, run
exactly once. Zero retrieval, generation, judging or LangGraph execution. Production unchanged.

---

## 1. Why V2 was selected

Three successive attempts to add a specificity mechanism each cost compound recall. Since a missed compound
means an information need may never be retrieved at all, the architect returned to the simplest,
highest-recall candidate and validated it on unseen data.

## 2. Development evidence across V2 / V3 / V4 / Cascade

| Candidate | precision | recall | specificity | accuracy | dev verdict |
|---|---|---|---|---|---|
| **V2** (permissive single-shot) | 0.6875 | **1.0000** | 0.8718 | 0.9000 | not accepted (old policy) |
| V3 (retrieval-plan) | **0.9000** | 0.8182 | **0.9744** | **0.9400** | DEV FAILURE |
| V4 (atomic evidence units) | 0.7500 | 0.8182 | 0.9167 | 0.8936 | DEV FAILURE |
| Cascade (V2 + strict verifier) | 0.8182 | 0.8182 | 0.9487 | 0.9200 | DEV FAILURE |

Three architecturally different specificity mechanisms all landed on recall exactly 0.8182 (9/11). Only V2
ever kept all 11 development compounds.

## 3. Cost asymmetry

- **False positive** — a simple question routed through decomposition. Costs extra routing, embedding and
  branch work, but the question still has a strong chance of receiving sufficient evidence.
- **False negative** — a compound question routed through single retrieval. One independent evidence need may
  never be retrieved. The decomposition experiment demonstrated exactly this failure mode: case-018's
  required facts were absent from **all 10** single-query candidates.

Compound recall is therefore the primary routing objective.

## 4. Holdout integrity

Asserted **before** any provider call; the runner exits with `DECISION REQUIRED — HOLDOUT INTEGRITY FAILURE`
on any violation.

| Check | Result |
|---|---|
| `sha256` | `0957b7bf8db137bad6e35f1495f5953e636e87969b5173b0fd9101a1dfd233b8` — **match** |
| Cases | 40 |
| Balance | 20 simple / 20 compound / **0 ambiguous** |
| Unique case ids | 40 |
| Unique normalized questions | 40 (no duplicates) |
| Categories | 13 |
| Previously shown to any router | **No** — never run by V2, V3, V4 or the cascade |
| Mutated since creation | No |

## 5. V2 frozen identity

Verified live against the stored fingerprint (`cc6dfde4be4dd586`) before execution; the runner refuses on
mismatch.

| Field | Live | Stored | |
|---|---|---|---|
| prompt SHA | `763d12cd82245285` | `763d12cd82245285` | ✅ |
| model | `openai/gpt-oss-20b` | same | ✅ |
| temperature | 0.0 | 0.0 | ✅ |
| max_tokens | 1500 | 1500 | ✅ |
| timeout | 120 s | 120 s | ✅ |
| reason codes (5) | identical | identical | ✅ |
| parser | `parse_router_output`, unmodified | — | ✅ |

Experiment fingerprint `ef077ea13547d7d3`. Nothing about V2 was tuned, and no development label influenced it.

## 6. Holdout confusion matrix

|  | predicted compound | predicted simple |
|---|---|---|
| **actually compound** (20) | **TP = 20** | **FN = 0** |
| **actually simple** (20) | **FP = 1** | **TN = 19** |

`parse_ok = 40/40`. All 40 scorable, zero provider errors.

## 7. Precision
**0.9524** (20/21) — 95% Wilson CI [0.773, 0.992]

## 8. Recall
**1.0000** (20/20) — 95% Wilson CI [0.839, 1.000]. **Every unseen compound was detected.**

## 9. Specificity
**0.9500** (19/20) — 95% Wilson CI [0.764, 0.991]

## 10. Accuracy
**0.9750** (39/40) — 95% Wilson CI [0.871, 0.996] · F1 **0.9756** · FP rate 0.050 · FN rate **0.000**

## 11. Category metrics

**Compound half — 20/20, every category perfect:**

| Category | Metric | Result |
|---|---|---|
| compound_without_and | recall | **4/4 = 1.000** |
| unrelated_domain | recall | 4/4 = 1.000 |
| different_entity | recall | 4/4 = 1.000 |
| different_event | recall | 3/3 = 1.000 |
| different_time | recall | 3/3 = 1.000 |
| single_clause_compound | recall | 2/2 = 1.000 |

**Simple half — 19/20:**

| Category | Metric | Result |
|---|---|---|
| single_entity_multi_attribute | specificity | 4/4 = 1.000 |
| contrast_verification | specificity | **3/3 = 1.000** |
| temporal_before_after | specificity | 3/3 = 1.000 |
| negative_scope | specificity | 2/2 = 1.000 |
| same_event_multi_consequence | specificity | 2/2 = 1.000 |
| series_synthesis | specificity | 2/2 = 1.000 |
| **simple_with_and** | specificity | **3/4 = 0.750** — miss `hold-001` |

The two categories that mattered most are the two that answer the benchmark's original design flaw:
**compound-without-"and" recall 1.000** (V2 is not a conjunction detector) and **contrast/verification
specificity 1.000** (the shape that defeated V2 on development, and that the cascade verifier also
mishandled).

Reason codes used: `multiple_independent_retrieval_needs` 21 · `single_entity_multi_attribute` 9 ·
`single_retrieval_need` 9 · `single_event_multi_attribute` 1.

## 12. False positives

Exactly one.

**`hold-001`** — category `simple_with_and`, code `multiple_independent_retrieval_needs`
> Q: "What does the Forty-Minute Rule require and which Quill Cell version introduced it?"
> V2 needs: `["What does the Forty-Minute Rule require?", "Which Quill Cell version introduced the Forty-Minute Rule?"]`

Descriptive classification against known development failure patterns: this is a **same-entity
multi-attribute** case — one rule, its requirement and its originating version. It resembles the
`case-002`-type pattern (attributes of one subject read as separate needs). Notably V2 got all four
*explicitly labelled* `single_entity_multi_attribute` cases right and failed only this one, where the
multi-attribute structure is masked by an "and" plus a second named entity ("Quill Cell version").
No label was changed.

## 13. False negatives

**NONE.** Zero across all 20 compound cases and all six compound categories — including all four
compound-without-"and" questions and both single-clause compounds.

## 14. Latency / tokens

| Metric | Value |
|---|---|
| mean | 4,217.4 ms |
| p50 | 3,519.8 ms |
| p95 | 8,520.7 ms |
| max | 16,996.9 ms |
| input tokens mean / total | 517.8 / 20,712 |
| output tokens mean / total | 255.3 / 10,213 |

Excludes 90.9 s of deliberate free-endpoint pacing. V2 is also the lightest router measured (V3 647/402.6,
V4 795/428.6 mean in/out). **Offline free-endpoint measurement only — not production latency.**

## 15. Provider reliability

| Model | Requests | Successes | Timeouts | 429s | Retries |
|---|---|---|---|---|---|
| NVIDIA `openai/gpt-oss-20b` | **40** | 40 | 0 | 0 | 0 |
| NVIDIA 120B | **0** | — | — | — | — |
| Groq | **0** | — | — | — | — |

One call per question, no bounded-retry requests needed. Concurrency 1, bounded timeout, durable fsync'd
checkpoint per case, no fallback, no paid provider.

## 16. Acceptance thresholds

Fixed **before** the untouched holdout ran and recorded in the experiment fingerprint:

| Requirement | Threshold | Actual | Result |
|---|---|---|---|
| compound recall | ≥ 0.95 (≥19/20) | **1.0000 (20/20)** | ✅ PASS |
| simple specificity | ≥ 0.80 (≥16/20) | **0.9500 (19/20)** | ✅ PASS |
| compound precision | ≥ 0.80 | **0.9524** | ✅ PASS |
| compound-without-"and" recall | ≥ 0.75 | **1.0000** | ✅ PASS |
| contrast/verification specificity | ≥ 0.75 | **1.0000** | ✅ PASS |

Not changed after execution.

## 17. Verdict

> ## ROUTER V2 HIGH-RECALL HOLDOUT = PASS
> ## ROUTER V2 ACCEPTED FOR OFFLINE LANGGRAPH INTEGRATION

All five gates cleared, with margin on every one. This means V2 becomes the router candidate for the next
**offline** end-to-end LangGraph evaluation. It is **not** production approval and V2 was not integrated.

### The result that reframes the whole arc

The same frozen V2, unchanged, scored **better on unseen data than on development**:

| Metric | dev (52-case) | **holdout (40-case)** |
|---|---|---|
| recall | 1.0000 | **1.0000** |
| precision | 0.6875 | **0.9524** |
| specificity | 0.8718 | **0.9500** |
| accuracy | 0.9000 | **0.9750** |

V2 was never the weak router. The 5 development false positives that drove three redesigns were
concentrated in a handful of genuinely borderline development items — two of which the ground-truth audit
itself flagged as ambiguous-adjacent — and did not generalise into a systematic specificity problem. The
three "improvements" built to fix that apparent problem each traded away real recall to solve something
substantially narrower than it looked.

## 18. Limitations

1. **Small sample; wide intervals.** 20 compounds and 20 simple. Recall 20/20 has a 95% Wilson lower bound
   of **0.839**, below the 0.95 threshold it nominally clears. The point estimate passes; the interval does
   not establish ≥0.95 with confidence. A larger holdout is the only fix.
2. **I authored the holdout.** The same agent that later analysed V2's failure modes wrote these questions.
   The categories were chosen to be adversarial *for the failure modes then known*, and questions were
   written to be hard — but they may still be more prototypical, and therefore easier, than organically
   generated ones. This is the single biggest caveat on §17.
3. **Development labels were also single-annotator and non-blinded** (standing caveat from the ground-truth
   audit). The dev-vs-holdout comparison in §17 therefore compares two label sets of the same provenance.
4. **The dev/holdout gap is unexplained by design alone.** Plausible contributors: smaller simple sample
   (20 vs 39), cleaner category exemplars, and the two dev borderline items. Not disentangled here.
5. **Single run, temperature 0**, no repeat sampling; run-to-run stability of the single false positive is
   unmeasured.
6. **Router-only.** Whether this routing quality improves end-to-end answers is untested — the emitted
   information needs were not used for retrieval.
7. **This holdout is now spent.** Any future router candidate needs a new unseen set.
8. **Free-endpoint latency** is not a production estimate, and no cost/latency budget has been set for an
   agentic production path.

## 19. Next recommendation

Documented only — nothing actioned, no integration performed.

**The architect's §21 next step is now unlocked:** integrate frozen V2 with the already-validated
decomposition graph, **offline**, and evaluate end-to-end quality on a mixed dataset.

```
question → V2 ─┬─ simple ──► normal top-5 RAG
               └─ compound ─► decomposition → parallel retrieval
                              → coverage-aware max-5 merge → final generation
```

Sequencing notes grounded in what this evaluation showed:

1. **Build a new, larger unseen holdout before the next router claim.** This one is spent, and §18.1 shows
   40 cases cannot establish ≥0.95 recall with confidence.
2. **An independent annotator would materially strengthen every number here** (§18.2), and costs no model
   calls.
3. **A larger router model is NOT justified** — see §15 of the final report. V2 achieved recall 1.000 on
   unseen data at 20B with zero false negatives across all six compound categories.
4. **The 5 pending decomposition judge scores remain the other open thread**, unrelated to routing and still
   blocked on the NVIDIA 120B endpoint.

## 20. Production status

**Unchanged. Nothing deployed.**

| Item | State |
|---|---|
| Deployed ask image | `d5af30e` — untouched |
| Production model | Groq `openai/gpt-oss-120b` — untouched |
| `MAX_LLM_CONTEXT_CHUNKS` | 5 — unchanged |
| `TOP_K` / floor / chunk size / RRF / BM25 / Titan / Qdrant | unchanged — no retrieval executed |
| LangGraph | **OFFLINE ONLY**, decomposition v1 **FROZEN** — 0 graph runs |
| Decomposition findings | preserved: case-018 1/6→6/6, case-020 4/7→7/7, case-022 3/6→5/6 |
| Router V2 | frozen, unmodified; now **accepted for offline graph integration only** |
| Router V3 / V4 / Cascade verifier | frozen, all NOT ACCEPTED |
| Holdout v1 | **spent** on this evaluation; manifest unmodified, hash verified |
| NVIDIA 120B judge | **1/6 scored, 5 pending** — not probed |
| Provider calls | NVIDIA 20B 40 · NVIDIA 120B 0 · Groq 0 |
