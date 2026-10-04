# Compound Router v3 — Development Evaluation (Holdout NOT Run)

**Outcome: ROUTER V3 DEVELOPMENT FAILURE.** The development gate rejected v3 on recall, so per the
architect's §18 the frozen unseen holdout was **not** run and remains untouched. v3 was not tuned again.

Offline routing evaluation only. Zero retrieval, generation, judging or LangGraph execution.
Production unchanged.

---

## 1. Why v3 exists

Router-v2, scored against the adjudicated ground truth, had exactly one narrow defect: five false
positives in which **one** retrieval need expressed as contrast, verification or temporal framing was read
as multiple independent needs. Recall was already perfect, so the target was precision/specificity without
losing compounds.

## 2. Frozen v2 result (baseline for comparison)

Adjudicated development metrics, 50 non-ambiguous cases, predictions immutable:

| Metric | v2 |
|---|---|
| TP / FP / TN / FN | 11 / 5 / 34 / 0 |
| compound precision | 0.6875 |
| compound recall | 1.0000 |
| F1 | 0.8148 |
| simple specificity | 0.8718 |
| accuracy | 0.9000 |
| verdict | NOT ACCEPTED |

The five false positives: `case-002`, `case-003`, `case-004`, `case-056`, `case-059`.

## 3. v3 design

| Field | Value |
|---|---|
| Model | NVIDIA NIM `openai/gpt-oss-20b` |
| Temperature | 0.0 |
| Prompt SHA | `e148925f8098d9e8` |
| Structured-output version | `router-v3-retrieval-plan-1` |
| Run fingerprint | `a270bd5888461d60` |
| Max retrieval queries | 3 |
| Reason codes | 8 (1 compound + 7 simple) |
| Calls per question | **exactly 1** (no planner second pass) |
| Concurrency | 1 |

Implemented in `evals/router_v3.py` + `evals/run_router_v3.py`. AST tests confirm it imports neither
`router_v2` nor `decomp_graph`, calls only `nv.chat`, and performs no retrieval, generation or judging.
v2 and the decomposition experiment were not modified.

## 4. Retrieval-plan semantics — the central design change

v2 asked *"does this question contain multiple information needs?"* — a question about the **text**.
v3 asks *"how many focused retrieval QUERIES would a good retriever actually need?"* and makes the model
emit the minimal **plan**. The verdict is then a property of that plan, enforced by the parser:

```
needs_decomposition == (len(retrieval_queries) >= 2)
```

The prompt additionally names, as explicit one-query shapes, every failure shape v2 exhibited: one entity
with several attributes; one event with several consequences; one object contrasted with what it actually
does or a claim being verified/corrected; one subject before/after a change; one scope/negative check; one
synthesis over a related series. It states that "and", "but", "while", "versus", "before", "after", extra
clauses and extra question marks do not by themselves justify more than one query. Seven simple reason
codes give the model a category to reach for instead of defaulting to the compound code.

## 5. Development-set metrics

52 adjudicated cases classified, 50 scored (2 ambiguous excluded), `parse_ok = 50/50`, zero provider errors.

|  | predicted compound | predicted simple |
|---|---|---|
| **actually compound** (11) | **TP = 9** | **FN = 2** |
| **actually simple** (39) | **FP = 1** | **TN = 38** |

| Metric | v2 | **v3** | Δ |
|---|---|---|---|
| compound precision | 0.6875 | **0.9000** | **+0.213** ✅ |
| compound recall | 1.0000 | **0.8182** | **−0.182** ❌ |
| F1 | 0.8148 | 0.8572 | +0.042 |
| simple specificity | 0.8718 | **0.9744** | **+0.103** ✅ |
| accuracy | 0.9000 | **0.9400** | +0.040 |
| false-positive decomposition rate | 0.1282 | **0.0256** | −0.103 |
| false-negative compound rate | 0.0000 | **0.1818** | +0.182 |

Retrieval-plan sizes: 40 one-query plans, 10 two-query plans, 0 three-query plans.
Reason codes: `single_retrieval_target` 25 · `single_entity_multi_attribute` 13 ·
`multiple_independent_retrieval_targets` 10 · `single_event_multi_consequence` 2.

**v3 achieved its design goal and broke something else.** It cut false positives from 5 to 1 and lifted
specificity above target, but introduced 2 false negatives where v2 had none.

## 6. The five known v2 false positives under v3

| case | v2 | **v3** | v3 reason code | plan size |
|---|---|---|---|---|
| `case-002` | compound (FP) | **simple — FIXED** | `single_entity_multi_attribute` | 1 |
| `case-003` | compound (FP) | compound — still FP | `multiple_independent_retrieval_targets` | 2 |
| `case-004` | compound (FP) | **simple — FIXED** | `single_retrieval_target` | 1 |
| `case-056` | compound (FP) | **simple — FIXED** | `single_retrieval_target` | 1 |
| `case-059` | compound (FP) | **simple — FIXED** | `single_entity_multi_attribute` | 1 |

**4 of 5 fixed.** The targeted defect was substantially addressed.

### The regressions, with the actual plans v3 produced

**`case-030` — FN. Plan-count gaming (the most important finding here).**
> Q: "How many seeds per tray were used in MS-E1, MS-E2 and MS-E3?"
> v3 plan (1 query): `["MS-E1 seeds per tray OR MS-E2 seeds per tray OR MS-E3 seeds per tray"]`
> v3 rationale: "one query covers all experiments"

v3 packed three distinct retrieval needs into a **single disjunctive string** to keep the plan length at 1.
The invariant `needs_decomposition == (len(queries) >= 2)` was satisfied while the underlying semantics
were not. **The central v3 mechanism has a loophole: plan length is gameable by OR-stuffing.** No parser
check currently detects it.

**`case-041` — FN. Contrast rule over-applied across unrelated domains.**
> Q: "Why is a blue ribbon appropriate at Sable Library but inappropriate in Copper Orchard rover zones?"
> v3 plan (1 query): `["blue ribbon Sable Library Copper Orchard rover zones policy"]`
> v3 rationale: "single query covers both contexts"

The prompt's "one object contrasted with what it actually does → one query" instruction generalised to a
contrast spanning **two unrelated domains** (a library convention and orchard rover safety). The subject
word is shared ("blue ribbon"); the evidence is not.

**`case-003` — the single remaining FP, and arguably a label question rather than a router error.**
> Q: "What are the current Quiet Bloom hours, and were they always the same?"
> v3 plan (2 queries): `["Quiet Bloom current hours", "Quiet Bloom hours history"]`

The adjudicated label is simple under rule R5 (same entity and attribute across time). v3's two-query plan
is coherent and a reasonable annotator might endorse it. Note this is the **only** R5 case in the whole dev
set, so the "R5 specificity = 0.000" figure below rests on a denominator of one and should not be read as a
category weakness.

### Per-category development results

| Category (dev decision rule) | Metric | Result | Misses |
|---|---|---|---|
| R1 two unrelated domains | recall | 2/3 = 0.667 | `case-041` |
| R2 distinct events/entities, own explanations | recall | 5/5 = **1.000** | — |
| R3 N sibling entities, own values | recall | 2/3 = 0.667 | `case-030` |
| R4 one entity, many attributes | specificity | 21/21 = **1.000** | — |
| R5 same entity+attribute across time | specificity | 0/1 = 0.000 | `case-003` (n=1) |
| R6 scope / negative check | specificity | 16/16 = **1.000** | — |
| R7 one synthesis over a series | specificity | 1/1 = **1.000** | — |

R4 and R6 — the two largest simple categories, 37 cases combined — are now perfect. That is where v2 lost
most of its precision.

## 7. Development gate

| Threshold | Required | Actual | Result |
|---|---|---|---|
| compound recall | ≥ 0.90 | **0.8182** | ❌ **FAIL** |
| simple specificity | ≥ 0.90 | 0.9744 | ✅ PASS |
| compound precision | ≥ 0.80 | 0.9000 | ✅ PASS |

> ## ROUTER V3 DEVELOPMENT FAILURE

Two of three thresholds pass and accuracy improved to 0.940, but recall of 0.8182 (9/11) misses the 0.90
bar. Losing 2 compounds is worse than v2's 5 false positives under this gate, and deliberately so: a missed
compound means the pipeline never retrieves evidence for one half of the question, whereas a false positive
merely costs an extra retrieval.

The runner enforced the gate automatically and exited before the holdout stage. v3 was **not** tuned again.

---

## Sections 8–17 — HOLDOUT: NOT RUN

The frozen holdout was **not** executed, because §18 requires stopping at a development failure. It remains
genuinely unseen and reusable for a future architect-approved router experiment — which is worth more than
a single spent evaluation.

| Section | Status |
|---|---|
| 8. Holdout manifest fingerprint | Built and frozen — see below. Not evaluated. |
| 9. Holdout category balance | Built and frozen — see below. Not evaluated. |
| 10. Holdout confusion matrix | **NOT RUN** |
| 11. Precision | **NOT RUN** |
| 12. Recall | **NOT RUN** |
| 13. Specificity | **NOT RUN** |
| 14. Accuracy | **NOT RUN** |
| 15. Category metrics | **NOT RUN** |
| 16. False positives | **NOT RUN** |
| 17. False negatives | **NOT RUN** |

No `compound-router-v3-holdout-results.csv` exists, and
`output/router_v3_holdout_results.jsonl` was never created — verified absent.

### Holdout construction (complete, frozen, unused)

Built **before** any router-v3 call, as required.

| Field | Value |
|---|---|
| Manifest | `compound-router-holdout-v1.jsonl` / `.csv` |
| `manifest_sha256` | `0957b7bf8db137bad6e35f1495f5953e…` |
| Policy | `independent-retrieval-needs-v1` (identical to the dev audit) |
| Cases | **40** — 20 simple / 20 compound, **0 ambiguous** |
| Unique questions | 40 (no internal duplicates) |
| Exact overlap with the 52-case dev set | **0** |
| Near-duplicate reject threshold | Jaccard 0.60; highest observed 0.50 (`hold-037` vs `case-001`) |

Category distribution (per the design matrix):

| Simple half | n | | Compound half | n |
|---|---|---|---|---|
| simple_with_and | 4 | | compound_without_and | 4 |
| single_entity_multi_attribute | 4 | | unrelated_domain | 4 |
| contrast_verification | 3 | | different_entity | 4 |
| temporal_before_after | 3 | | different_event | 3 |
| negative_scope | 2 | | different_time | 3 |
| same_event_multi_consequence | 2 | | single_clause_compound | 2 |
| series_synthesis | 2 | | | |

**Anti-cue verification.** 4 simple questions contain "and"; **6** compound questions contain no "and". A
naive "and"-detector scores only precision 0.778 / recall 0.700 / specificity 0.800 / **accuracy 0.750** on
this holdout, so it is not solvable by conjunction-spotting — the failure mode that made the original
benchmark misleading.

---

## 18. Latency / tokens

Development stage, 52 calls:

| Metric | v3 | v2 (reference) |
|---|---|---|
| latency mean | 7,698.8 ms | 4,567.9 ms |
| p50 | 5,178.2 ms | 2,840.9 ms |
| p95 | 20,123.0 ms | 13,624.2 ms |
| max | 23,958.2 ms | 16,010.4 ms |
| input tokens mean / total | 647.0 / 32,352 | 518.3 / 26,951 |
| output tokens mean / total | 402.6 / 20,128 | 286.6 / 14,905 |

v3 is descriptively slower and more token-hungry than v2 — a longer prompt (8 reason codes, 6 named simple
shapes) and a larger output (the retrieval plan plus a rationale). Latency excludes 75.3 s of deliberate
free-endpoint pacing. **These are free-tier NIM figures and are not production latency estimates.**

## 19. Provider reliability

| Stage | Model | Requests | Successes | Timeouts | 429s |
|---|---|---|---|---|---|
| Development | 20B | 52 | 52 | 0 | 0 |
| Holdout | — | **0** (not run) | — | — | — |

**Groq calls: 0** (guard installed; any Groq use raises). **NVIDIA 120B calls: 0** — not probed.
`parse_ok = 50/50` on scored cases; the plan/flag invariant was never violated by the model. Concurrency 1,
bounded timeouts, durable fsync'd checkpoint per classification, no fallback, no paid provider.

## 20. Acceptance verdict

> ## ROUTER V3 = NOT ACCEPTED

Failed the **development** gate on recall (0.8182 < 0.90), so it never reached the holdout. Not integrated
into LangGraph, not deployed.

For the record, v3 is the better classifier on 4 of 5 aggregate measures (precision, specificity, F1,
accuracy) and fixed 4 of the 5 defects it was built to fix. It fails on the one axis the architect weighted
hardest, and correctly so: a missed compound costs the pipeline half its evidence.

## 21. Limitations

1. **11 positives on the dev set.** Recall moves in increments of 0.091, so a single case decides the gate.
   Losing 2 compounds drops recall to 0.818 with no possible value between 0.909 and 0.818. The gate is
   effectively "lose at most one compound".
2. **The plan-length invariant is gameable.** `case-030` shows a model can OR-stuff several needs into one
   query string. The parser does not detect this, so the v3 mechanism is weaker than its design implies.
3. **`case-003` may be a label artifact, not a router error** (§6), and it is the sole R5 case — so v3's
   only false positive sits on the dev set's thinnest category.
4. **The dev set is not blinded relative to v3.** v3 was designed from v2's observed errors on these same
   52 cases, so the development metrics are optimistic by construction. That is exactly why the holdout
   exists — and exactly why not spending it on a failing candidate matters.
5. **Single run, temperature 0**, no repeat sampling; run-to-run stability unmeasured.
6. **Retrieval locality is still reasoned about, never measured.** No retrieval was executed, so both the
   labels and the router's plans encode judgements about corpus structure rather than observed retrieval.
7. **Dev-set labels themselves are not independently adjudicated** — the standing caveat from the
   ground-truth audit (single non-blinded annotator) applies unchanged to every figure here.
8. **Holdout labels are also single-annotator and unblinded to the policy**, though they were authored
   before any v3 call and are cue-balanced.

## 22. Next recommendation

Options with evidence. **Nothing below was actioned. No router-v4 was designed or built, no prompt was
tuned after the gate, and the holdout was not touched.**

1. **The holdout is preserved unspent — keep it that way.** It is frozen, cue-balanced, has zero overlap
   with development, and has never been shown to a model. It should be spent only on a candidate that has
   already cleared the development gate.
2. **Any future candidate needs a defence against plan-count gaming.** `case-030` is the actionable finding:
   a plan of one query containing " OR " (or several distinct named entities) should be rejected or
   re-counted rather than accepted at face value. This is a parser/validation change, not a prompt change,
   and it is checkable offline against the 52 predictions already stored.
3. **The contrast instruction needs a scope qualifier.** `case-041` shows "contrast about one subject → one
   query" firing when the contrast spans two unrelated domains. The distinction is whether the contrasted
   *contexts* share an evidence neighbourhood, not whether they share a noun.
4. **Consider whether the dev-set gate is the right instrument at n=11 positives.** With recall quantised
   to 0.091 steps, the gate admits no partial credit. A larger positive class in development — separate
   from the holdout — would make the gate less brittle.
5. **Not indicated:** integrating v3, tuning v3 further in place (explicitly forbidden and would corrupt
   the comparison), a larger router or application model (v2 already achieved recall 1.000 at 20B, so this
   is a semantics problem), retrieval changes, re-running decomposition, or probing the 120B judge.

## 23. Production status

**Unchanged. Nothing deployed.**

| Item | State |
|---|---|
| Deployed ask image | `d5af30e` — untouched |
| Production model | Groq `openai/gpt-oss-120b` — untouched |
| `MAX_LLM_CONTEXT_CHUNKS` | 5 — unchanged |
| `TOP_K` / floor / chunk size / RRF / BM25 / Titan / Qdrant | unchanged — no retrieval executed |
| LangGraph | **OFFLINE ONLY**, decomposition v1 **FROZEN** — 0 graph runs |
| Decomposition findings | preserved: case-018 1/6→6/6, case-020 4/7→7/7, case-022 3/6→5/6 |
| Router-v2 | frozen; source, prompt, predictions and adjudicated labels unmodified |
| Router-v3 | development only, **NOT ACCEPTED**, not integrated |
| Holdout v1 | frozen, **unused** |
| NVIDIA 120B judge | **1/6 scored, 5 pending** — not probed |
| Provider calls | NVIDIA 20B 52 · NVIDIA 120B 0 · Groq 0 |
