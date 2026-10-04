# Compound Router v4 — Atomic Evidence-Unit Routing (Holdout NOT Run)

**Outcome: ROUTER V4 DEVELOPMENT FAILURE.** The development stage could not produce a scorable gate result,
and on the subset that did parse two of three thresholds still fail. Per §23 the frozen holdout was **not**
run and remains unspent. v4 was not modified afterwards and no v5 was built.

Offline routing evaluation only. Zero retrieval, generation, judging or LangGraph execution.
Production unchanged.

---

## 1. Why v4 exists

Router-v3 derived its verdict from `len(retrieval_queries)`. Two dev failures showed a query **string** is
not the semantic unit that matters:

- `case-030` — three independently required sibling values were **OR-stuffed into one query string**
  (`"MS-E1 seeds per tray OR MS-E2 seeds per tray OR MS-E3 seeds per tray"`), keeping the plan length at 1
  while the semantics required three.
- `case-041` — a contrast across two unrelated evidence contexts was collapsed into one query because both
  sides shared a surface noun ("blue ribbon").

v4 therefore reasons in **atomic evidence units** rather than query strings.

## 2. v3 failure analysis (baseline)

| Metric | v2 adjudicated | v3 dev |
|---|---|---|
| TP / FP / TN / FN | 11 / 5 / 34 / 0 | 9 / 1 / 38 / 2 |
| compound precision | 0.6875 | 0.9000 |
| compound recall | 1.0000 | **0.8182** ❌ |
| simple specificity | 0.8718 | 0.9744 |
| accuracy | 0.9000 | 0.9400 |
| verdict | NOT ACCEPTED | DEVELOPMENT FAILURE (recall) |

## 3. The atomic evidence-unit abstraction

An **evidence unit** is one localized semantic evidence neighbourhood — one place in the collection — from
which one or more requested facts can reasonably be retrieved *together*. The router outputs the minimum
number of legitimate units, and the verdict is a property of the **unit count**, not of any string:

```
needs_decomposition == (len(evidence_units) >= 2)        1..3 units
```

The prompt encodes retrieval locality as the criterion in both directions: several attributes of one
localized object/state/event, several consequences of one event, a claim verified/denied about the same
subject, one attribute across time, one synthesis over a series, and scope/negative checks are all **one**
unit; while a contrast is one unit *only* when both sides belong to the same object, event, experiment,
decision or documented comparison — otherwise one unit **per side**. It states explicitly that naming two
things does not by itself require two units, and that a shared noun does not prove shared retrieval
locality.

**The atomicity rule is enforced in the parser, not only the prompt.** A unit whose `retrieval_query` joins
independent targets with boolean syntax (`OR`, `AND`, `||`, `&&`) is rejected as
`unit_boolean_stuffing`. v3's exact loophole string is now a hard parse error, verified by test.

## 4. Schema

```json
{"evidence_units": [{"anchor": "...", "facts_needed": ["..."], "retrieval_query": "..."}],
 "needs_decomposition": false,
 "reason_code": "single_evidence_neighborhood",
 "rationale": "one short sentence"}
```

Parser enforces: 1–3 units; each unit has a non-empty `anchor`, a non-empty `facts_needed` list of
non-empty strings, and a non-empty `retrieval_query`; no boolean stuffing in any unit; the unit-count
invariant; and reason-code/flag consistency both ways. 8 reason codes (1 compound + 7 simple). Rationale
capped at one sentence; no chain-of-thought requested or stored.

## 5. Prompt SHA / fingerprint

| Field | Value |
|---|---|
| Prompt SHA | `e0439db6159e5d5b` |
| Run fingerprint | `978d0ae1f8a89462` |
| Model / temperature | NVIDIA `openai/gpt-oss-20b` / 0.0 |
| Structured-output version | `router-v4-atomic-evidence-units-1` |
| Max evidence units | 3 · Calls per question **1** · Concurrency 1 |

Implemented in `evals/router_v4.py` + `run_router_v4.py`. AST tests confirm it imports neither
`router_v2`, `router_v3` nor `decomp_graph`, calls only `nv.chat`, and performs no retrieval,
generation or judging. No diagnostic case ID appears in v4 logic or in the prompt.

## 6. Development confusion matrix

52 cases classified, 50 non-ambiguous, **3 UNSCORABLE**.

|  | predicted compound | predicted simple | **no verdict** |
|---|---|---|---|
| **actually compound** (11) | TP = 9 | FN = 2 | 0 |
| **actually simple** (39) | FP = 3 | TN = 33 | **3** |

The matrix covers 47 of 50 cases. **A gate result over the mandated 50-case denominator does not exist.**

### The three unscorable cases

All three failed with `unit_retrieval_query_empty` — the model emitted evidence units carrying an anchor
and `facts_needed` but with a missing or empty `retrieval_query`:

| case | ground truth | question |
|---|---|---|
| `case-002` | simple | "Does Pip-6 actually pollinate the flowers? What does it really do?" |
| `case-038` | simple | "Can Orrin Bluff Rillback thresholds automatically be applied to every Whisperglass station?" |
| `case-040` | simple | "Does the 16:20 Morrow Bell tell visitors Sable Library is closing?" |

This is **not** a harness defect to be papered over: a non-empty `retrieval_query` per unit is part of the
mandated §3 schema and §34 explicitly requires "empty query rejected". The finding is that the richer v4
schema — three fields per unit instead of one query string — is one the 20B model fails to satisfy on
**3 of 52 questions (5.8%)**. Provider health was perfect (52/52 successes, 0 timeouts, 0 429s), so this is
schema compliance, not endpoint trouble.

## 7. Development metrics

Computed over the **47 parseable** cases only. These are **not** a valid gate result — reported because
they are informative, and clearly labelled:

| Metric | v2 adj. | v3 dev | **v4 dev (parseable subset)** |
|---|---|---|---|
| scored | 50 | 50 | **47 of 50** |
| TP / FP / TN / FN | 11/5/34/0 | 9/1/38/2 | **9/3/33/2** |
| compound precision | 0.6875 | 0.9000 | **0.7500** ❌ |
| compound recall | 1.0000 | 0.8182 | **0.8182** ❌ |
| compound F1 | 0.8148 | 0.8572 | **0.7826** |
| simple specificity | 0.8718 | 0.9744 | **0.9167** ✅ |
| accuracy | 0.9000 | 0.9400 | **0.8936** |
| FP decomposition rate | 0.1282 | 0.0256 | **0.0833** |
| FN compound rate | 0.0000 | 0.1818 | **0.1818** |

Even ignoring the three unscorable cases, **recall (0.8182 < 0.90) and precision (0.7500 < 0.80) both miss
threshold**. v4 fails the gate on two independent counts.

Evidence-unit counts emitted: 35×1 unit, 10×2 units, 2×3 units, 3×unparsed.
Reason codes: `single_evidence_neighborhood` 30 · `multiple_evidence_neighborhoods` 12 ·
`one_entity_multi_attribute` 2 · `verification_same_subject` 2 · `one_event_multi_consequence` 1.

### Per-category development results

| Category | Metric | v4 result | Misses |
|---|---|---|---|
| R1 two unrelated domains | recall | **3/3 = 1.000** | — (v3: 0.667) |
| R2 distinct events/entities | recall | 3/5 = 0.600 | `case-007`, `case-047` (v3: 1.000) |
| R3 N sibling entities, own values | recall | **3/3 = 1.000** | — (v3: 0.667) |
| R4 one entity, many attributes | specificity | 20/21 = 0.952 | `case-058` |
| R5 same entity+attribute over time | specificity | 1/1 = 1.000 | — |
| R6 scope / negative check | specificity | 15/16 = 0.938 | `case-056` |
| R7 one synthesis over a series | specificity | 0/1 = 0.000 | `case-059` (n=1) |

## 8. case-030 result — **FIXED**

> Q: "How many seeds per tray were used in MS-E1, MS-E2 and MS-E3?"
> v4: **compound**, **3 evidence units**, `multiple_evidence_neighborhoods`

v3 collapsed this into one OR-stuffed query. v4 produced one unit per sibling experiment. The combination of
the evidence-unit abstraction and the parser-level boolean-stuffing rejection resolved the exact failure it
was designed for.

## 9. case-041 result — **FIXED**

> Q: "Why is a blue ribbon appropriate at Sable Library but inappropriate in Copper Orchard rover zones?"
> v4: **compound**, **2 evidence units**, `multiple_evidence_neighborhoods`

v3 said "single query covers both contexts". v4 correctly split the two contexts. The sharpened contrast
rule — one unit only when both sides share an object/event/decision, otherwise one per side — worked.

**Both targeted v3 failures were fixed, and the R1/R3 categories that housed them went from 0.667 to
1.000.** The abstraction change did what it was meant to do.

## 10. Five v2 false-positive regression checks

| case | v2 | v3 | **v4** | v4 status |
|---|---|---|---|---|
| `case-002` | compound (FP) | simple ✅ | **UNPARSED** | **regression — unscorable** |
| `case-003` | compound (FP) | compound (FP) | **simple** ✅ | **improved over v3** |
| `case-004` | compound (FP) | simple ✅ | **simple** ✅ | held |
| `case-056` | compound (FP) | simple ✅ | **compound (FP)** | **regression** |
| `case-059` | compound (FP) | simple ✅ | **compound (FP)** | **regression** |

v4 recovered `case-003` — the boundary case neither v2 nor v3 got right — but lost `case-056` and
`case-059`, and `case-002` became unscorable. Net on these five: v3 held 4, v4 holds 2.

New compound losses versus v3: `case-007` and `case-047` (both R2, both TP under v3) are now false
negatives. So v4 did not simply improve on v3 — it **moved** the errors: it fixed the sibling-value and
cross-domain-contrast failures while losing two different-event/entity compounds and over-splitting two
simple cases.

## 11. Development gate

> ## ROUTER V4 DEVELOPMENT FAILURE

| Threshold | Required | Actual | Result |
|---|---|---|---|
| compound recall | ≥ 0.90 | 0.8182 (parseable subset) | ❌ FAIL |
| simple specificity | ≥ 0.90 | 0.9167 (parseable subset) | ✅ PASS |
| compound precision | ≥ 0.80 | 0.7500 (parseable subset) | ❌ FAIL |
| all 50 cases scorable | required | **47 of 50** | ❌ FAIL |

Thresholds were **not** relaxed. Two of three fail on the parseable subset, and the full-denominator gate is
not computable at all. The runner exited at the unscorable check **before** writing a gate file, so
`output/router_v4_dev_gate.json` does not exist — which structurally prevents the holdout stage from
starting, since it refuses to run without a passed gate.

## 12. Holdout integrity / fingerprint

The frozen holdout was **verified and left untouched**:

| Field | Value |
|---|---|
| Manifest | `compound-router-holdout-v1.jsonl` / `.csv` |
| Stored `manifest_sha256` | `0957b7bf8db137bad6e35f1495f5953e636e87969b5173b0fd9101a1dfd233b8` |
| Recomputed from file | **identical — verified match** |
| Cases | 40 — 20 simple / 20 compound / 0 ambiguous |
| Categories | 13 |
| Naive "and"-detector accuracy | 0.750 (not cue-separable) |
| Exact overlap with dev set | 0 (re-asserted by test) |

No question, label, category or case was edited, replaced or regenerated. The runner hard-codes the expected
SHA and refuses to proceed on mismatch.

## 13. Holdout execution status

> ## NOT RUN

`output/router_v4_holdout_results.jsonl` does not exist, and no
`compound-router-v4-holdout-results.csv` was produced. **The holdout remains unspent and genuinely
unseen** — it has never been shown to any router.

## Sections 14–21 — HOLDOUT METRICS: NOT RUN

| Section | Status |
|---|---|
| 14. Holdout confusion matrix | **NOT RUN** |
| 15. Holdout precision | **NOT RUN** |
| 16. Holdout recall | **NOT RUN** |
| 17. Holdout specificity | **NOT RUN** |
| 18. Holdout accuracy | **NOT RUN** |
| 19. Category metrics | **NOT RUN** |
| 20. False positives | **NOT RUN** |
| 21. False negatives | **NOT RUN** |

No values are reported for these because none exist. Development-stage false positives
(`case-056`, `case-058`, `case-059`) and false negatives (`case-007`, `case-047`) are in §7 and §10.

## 22. Latency / tokens

Development stage, 52 calls:

| Metric | v2 | v3 | **v4** |
|---|---|---|---|
| latency mean | 4,567.9 ms | 7,698.8 ms | **6,794.9 ms** |
| p50 | 2,840.9 ms | 5,178.2 ms | **4,677.1 ms** |
| p95 | 13,624.2 ms | 20,123.0 ms | **19,251.7 ms** |
| max | 16,010.4 ms | 23,958.2 ms | **20,415.8 ms** |
| input tokens mean / total | 518.3 / 26,951 | 647.0 / 32,352 | **795.0 / 39,752** |
| output tokens mean / total | 286.6 / 14,905 | 402.6 / 20,128 | **428.6 / 21,430** |

v4 is marginally faster than v3 but the **heaviest on input tokens** (+23% over v3, +53% over v2) — the
richer three-field-per-unit schema and the longer locality rules. Latency excludes 72.9 s of deliberate
pacing. Free-tier NIM figures; **not** production latency estimates.

## 23. Provider reliability

| Stage | Model | Requests | Successes | Timeouts | 429s |
|---|---|---|---|---|---|
| Development | 20B | 52 | 52 | 0 | 0 |
| Holdout | — | **0** (not run) | — | — | — |

**Groq calls: 0** (guard installed; any Groq use raises). **NVIDIA 120B calls: 0** — not probed.
Concurrency 1, bounded timeouts, durable fsync'd checkpoint per classification, no fallback, no paid
provider. Schema compliance `parse_ok = 47/50`; the unit-count invariant and the boolean-stuffing rule
were never violated by the model — the only failures were empty `retrieval_query` fields.

## 24. Acceptance verdict

> ## ROUTER V4 = NOT ACCEPTED

Failed the **development** gate, so it never reached the holdout. Not integrated into LangGraph, not
deployed.

The honest summary across three attempts on this dev set:

| | v2 | v3 | v4 |
|---|---|---|---|
| precision | 0.6875 | **0.9000** | 0.7500 |
| recall | **1.0000** | 0.8182 | 0.8182 |
| specificity | 0.8718 | **0.9744** | 0.9167 |
| accuracy | 0.9000 | **0.9400** | 0.8936 |
| all cases scorable | ✅ | ✅ | ❌ (47/50) |

No version has cleared all three thresholds. v2 maximised recall, v3 maximised precision/specificity/
accuracy, v4 fixed the two specific representation failures v3 had but regressed elsewhere and added a
schema-compliance failure. **Each redesign moved the errors rather than removing them.**

## 25. Limitations

1. **The gate was not computable on the mandated denominator.** 3 of 50 cases produced no verdict, so the
   headline numbers are over 47 cases and are not a valid gate result. I have not substituted them for one.
2. **11 dev positives.** Recall moves in 0.091 steps, so the gate is effectively "lose at most one
   compound". v4 lost two.
3. **The richer schema costs reliability.** v3's single-string plan parsed 50/50; v4's three-field units
   parsed 47/50. Expressive power and compliance traded against each other, and the architect-specified
   "empty query rejected" rule turns non-compliance into an unscorable case rather than a wrong answer.
4. **v4 was designed from v3's observed dev errors**, so dev metrics are optimistic by construction — the
   reason the holdout exists and the reason not spending it on a failing candidate matters.
5. **R5 and R7 have n=1.** "R7 specificity 0.000" is one case (`case-059`); no category conclusion follows.
6. **Single run, temperature 0**, no repeat sampling; run-to-run stability unmeasured.
7. **Retrieval locality is still reasoned about, never measured** — no retrieval was executed, so both the
   labels and the router's units encode judgements about corpus structure.
8. **Dev labels remain single-annotator and non-blinded** — the standing ground-truth caveat applies to
   every figure here.

## 26. Next recommendation

Options with evidence. **Nothing below was actioned: no v5 was designed or built, v4 was not modified after
the gate, and the holdout was not touched.**

1. **The holdout is still unspent — keep it that way.** Three router generations have now been evaluated
   without spending it. It stays valid for the first candidate that clears development.
2. **The schema-compliance failure is the cheapest thing to fix and needs no new design.** The three
   failures were all a missing `retrieval_query` while `anchor` and `facts_needed` were present. A
   future candidate could derive the query from the anchor when absent, or make `retrieval_query` optional
   — but note that is a **schema decision for the architect**, since §3/§34 currently mandate it as
   required and rejecting an empty one is specified behaviour.
3. **The evidence-unit abstraction itself is worth keeping.** It is the only change that fixed the
   sibling-value and cross-domain-contrast failures (R1 and R3 both 1.000, up from 0.667), and the
   parser-level boolean-stuffing rule closed v3's loophole permanently. The regressions were in R2, a
   different category — suggesting refinement of the "separately documented events" guidance rather than
   abandoning the abstraction.
4. **Consider whether the three-threshold gate at n=11 positives is measuring signal or noise.** Across
   v2/v3/v4 the same dev set produced three different error profiles at similar aggregate accuracy
   (0.90 / 0.94 / 0.89). A larger development positive class — kept separate from the holdout — would make
   the comparison meaningful.
5. **Not indicated:** integrating v4, tuning v4 in place, building v5 before the above is settled, a larger
   router or application model (v2 hit recall 1.000 at 20B, so this is representation not capacity),
   retrieval changes, re-running decomposition, or probing the 120B judge.

## 27. Production status

**Unchanged. Nothing deployed.**

| Item | State |
|---|---|
| Deployed ask image | `d5af30e` — untouched |
| Production model | Groq `openai/gpt-oss-120b` — untouched |
| `MAX_LLM_CONTEXT_CHUNKS` | 5 — unchanged |
| `TOP_K` / floor / chunk size / RRF / BM25 / Titan / Qdrant | unchanged — no retrieval executed |
| LangGraph | **OFFLINE ONLY**, decomposition v1 **FROZEN** — 0 graph runs |
| Decomposition findings | preserved: case-018 1/6→6/6, case-020 4/7→7/7, case-022 3/6→5/6 |
| Router-v2 | frozen — source, predictions, adjudicated labels unmodified |
| Router-v3 | frozen — source and dev results unmodified; its holdout still never run |
| Router-v4 | development only, **NOT ACCEPTED**, not integrated |
| Frozen holdout v1 | verified by hash, **unused** |
| NVIDIA 120B judge | **1/6 scored, 5 pending** — not probed |
| Provider calls | NVIDIA 20B 52 · NVIDIA 120B 0 · Groq 0 |
