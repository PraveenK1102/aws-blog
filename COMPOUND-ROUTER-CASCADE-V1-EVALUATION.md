# Compound Router Cascade v1 — Frozen V2 + Strict Verifier (Holdout NOT Run)

**Outcome: CASCADE V1 DEVELOPMENT FAILURE.** The cascade cleared precision and specificity but missed
compound recall by exactly one case. Per §18 the frozen holdout was **not** run and remains unspent. The
verifier was not tuned again and no further version was built.

Offline routing only. Zero retrieval, generation, judging or LangGraph execution. Production unchanged.

---

## 1. Architectural motivation

Three single-shot routers each failed the same gate from a different direction. Rather than ask one prompt
to be simultaneously sensitive and specific, this experiment separates the two jobs: a high-recall candidate
detector followed by a conditional high-specificity verifier.

## 2. Frozen V2 characteristics (Stage A)

| Metric | V2 adjudicated |
|---|---|
| compound recall | **1.0000** — found all 11 known compounds |
| compound precision | 0.6875 |
| simple specificity | 0.8718 |
| accuracy | 0.9000 |
| errors | 5 genuine false positives, **0** false negatives |

V2 is used **exactly as frozen**. Its prompt SHA `763d12cd82245285` was re-verified live against its
recorded fingerprint (`cc6dfde4be4dd586`) — identical. No file, prompt or prediction was modified.

## 3. Why single-shot V3/V4 were rejected

| | precision | recall | specificity | accuracy | verdict |
|---|---|---|---|---|---|
| V2 | 0.6875 | **1.0000** | 0.8718 | 0.9000 | NOT ACCEPTED (precision) |
| V3 | **0.9000** | 0.8182 | **0.9744** | **0.9400** | DEV FAILURE (recall) |
| V4 | 0.7500 | 0.8182 | 0.9167 | 0.8936 | DEV FAILURE (recall + 3 unscorable) |

V3 became an effective conservative classifier but dropped two genuine compounds. V4's atomic evidence-unit
representation fixed V3's two specific failures yet regressed elsewhere and added schema non-compliance.

## 4. Cascade topology

```
question
   │
   ▼
Stage A — FROZEN Router V2  (high recall)
   │
   ├── predicted simple ──────────────────────────► SIMPLE          (no Stage B call)
   │
   └── predicted compound
              │
              ▼
       Stage B — strict verifier  (1 NVIDIA 20B call)
              │
              ├── reject  → same evidence neighbourhood ──► SIMPLE
              └── confirm → independent needs ───────────► COMPOUND
```

**Stage B cannot create a compound that Stage A missed.** Cascade recall is bounded above by V2's recall —
intentional, because V2 sits at 11/11.

## 5. Stage B semantics

One confirmation question: *do V2's proposed needs truly require separate retrieval neighbourhoods, or could
one focused retrieval reasonably cover them?* It does not build a fresh plan.

| Field | Value |
|---|---|
| Model / temperature | NVIDIA `openai/gpt-oss-20b` / 0.0 |
| Verifier prompt SHA | `7a4d3859f1285c62` |
| Schema | `cascade-v1-verifier-1` |
| Cascade fingerprint | `5f8fcff597261b61` |
| Reason codes | 6 (1 confirm + 5 reject) |
| Input | question + V2's proposed needs + V2 reason code — **nothing else** |

Schema deliberately small, applying v4's lesson: a boolean, a closed-enum code, and one optional sentence.
No retrieval queries, anchors, facts arrays or nested objects. Anti-leakage is test-enforced at the call
site — the runner passes only `(question, a_needs, a_code)`; no label, expected answer, category, judge
score, route or "was V2 correct" signal can reach the model.

## 6. Development confusion matrix

Stage A reused from the persisted V2 checkpoint (**0 V2 calls**). Stage B ran on all 18 V2-compound
candidates. 50 non-ambiguous cases scored; all 50 scorable.

|  | predicted compound | predicted simple |
|---|---|---|
| **actually compound** (11) | **TP = 9** | **FN = 2** |
| **actually simple** (39) | **FP = 2** | **TN = 37** |

## 7. Development metrics

| Metric | V2 (Stage A alone) | **Cascade v1** | Δ |
|---|---|---|---|
| TP / FP / TN / FN | 11 / 5 / 34 / 0 | **9 / 2 / 37 / 2** | −3 FP, +2 FN |
| compound precision | 0.6875 | **0.8182** | **+0.131** ✅ |
| compound recall | 1.0000 | **0.8182** | **−0.182** ❌ |
| compound F1 | 0.8148 | 0.8182 | +0.003 |
| simple specificity | 0.8718 | **0.9487** | **+0.077** ✅ |
| accuracy | 0.9000 | **0.9200** | +0.020 |
| FP decomposition rate | 0.1282 | **0.0513** | −0.077 |
| FN compound rate | 0.0000 | **0.1818** | +0.182 |

The architecture delivered its precision half: it removed **3 of the 5** V2 false positives, exactly the
target in §16. It failed the recall half: it rejected **2** true compounds where at most **1** was allowed.

Verifier reason codes used: `independent_evidence_neighborhoods` 12 · `same_event_or_record` 3 ·
`same_entity_state` 2 · `same_subject_temporal_comparison` 1.

## 8. Eleven true-compound verifier decisions

| case | verifier | reason code | correct |
|---|---|---|---|
| case-008 | CONFIRM | `independent_evidence_neighborhoods` | ✅ |
| case-009 | CONFIRM | `independent_evidence_neighborhoods` | ✅ |
| case-018 | CONFIRM | `independent_evidence_neighborhoods` | ✅ |
| case-020 | CONFIRM | `independent_evidence_neighborhoods` | ✅ |
| case-022 | CONFIRM | `independent_evidence_neighborhoods` | ✅ |
| case-023 | CONFIRM | `independent_evidence_neighborhoods` | ✅ |
| case-030 | CONFIRM | `independent_evidence_neighborhoods` | ✅ |
| case-041 | CONFIRM | `independent_evidence_neighborhoods` | ✅ |
| case-046 | CONFIRM | `independent_evidence_neighborhoods` | ✅ |
| **case-007** | **reject** | `same_entity_state` | ❌ **FN** |
| **case-047** | **reject** | `same_event_or_record` | ❌ **FN** |

**9 of 11 preserved.** Notably `case-030` and `case-041` — the two V3 lost — were both confirmed.

## 9. Five V2 false-positive verifier decisions

| case | verifier | reason code | outcome |
|---|---|---|---|
| `case-002` | **reject** | `same_entity_state` | ✅ FP removed |
| `case-003` | **reject** | `same_subject_temporal_comparison` | ✅ FP removed |
| `case-004` | **reject** | `same_event_or_record` | ✅ FP removed |
| `case-056` | CONFIRM | `independent_evidence_neighborhoods` | ❌ FP remains |
| `case-059` | CONFIRM | `independent_evidence_neighborhoods` | ❌ FP remains |

Ambiguous cases (path executed faithfully, excluded from metrics): `case-005` rejected
(`same_event_or_record`), `case-025` confirmed.

### The decisive finding: all four errors contradict the verifier's own stated rules

Every error is a case where the correct reason code existed in the enum **and** the prompt explicitly
described the correct handling — and the verifier went the other way:

| case | verifier rationale (verbatim) | which rule it violated |
|---|---|---|
| `case-007` | *"Both facts describe attributes of the same T41 box."* | The prompt states a shared noun "does not prove they share one neighbourhood". The needs are the box's *contents* and its *pre-discovery provenance* — different records that merely share the identifier `T41`. |
| `case-047` | *"Both facts describe the same Quiet Bloom window event."* | Same shared-noun collapse: a schedule window and a robot's behaviour during it are different subjects. |
| `case-056` | *"old post and current schedule are separate records"* | `same_subject_temporal_comparison` exists and the prompt names "one property of one subject compared between its older and current values" as a reject. The verifier applied that code **correctly** to `case-003` and missed it here. |
| `case-059` | *"each MS-Ex is a distinct change requiring separate evidence"* | `same_series_synthesis` exists and the question opens with "Summarize". The verifier never used that code once in 18 calls. |

The failure is **not missing instruction — it is inconsistent application of instructions the model
demonstrably understood elsewhere in the same run.** Two of six reject codes were never used at all.

## 10. case-030 — **CONFIRMED (correct)**
> "How many seeds per tray were used in MS-E1, MS-E2 and MS-E3?" → V2 compound → verifier **CONFIRM**
V3 lost this case; the cascade keeps it.

## 11. case-041 — **CONFIRMED (correct)**
> "Why is a blue ribbon appropriate at Sable Library but inappropriate in Copper Orchard rover zones?"
> → V2 compound → verifier **CONFIRM** (`independent_evidence_neighborhoods`)
V3 lost this case; the cascade keeps it. The cross-domain contrast was handled correctly here — which makes
the shared-noun collapse on `case-007` and `case-047` an inconsistency rather than a blind spot.

## 12. Development gate

> ## CASCADE V1 DEVELOPMENT FAILURE

| Threshold | Required | Actual | Result |
|---|---|---|---|
| compound recall | ≥ 0.90 | **0.8182** (9/11) | ❌ FAIL |
| simple specificity | ≥ 0.90 | 0.9487 | ✅ PASS |
| compound precision | ≥ 0.80 | 0.8182 | ✅ PASS |

Thresholds were not relaxed. Recall needed 10/11 = 0.909; the cascade reached 9/11 = 0.8182. **One
additional preserved compound would have passed the gate.**

## 13. Frozen holdout integrity

| Field | Value |
|---|---|
| Stored `manifest_sha256` | `0957b7bf8db137bad6e35f1495f5953e636e87969b5173b0fd9101a1dfd233b8` |
| Recomputed from file | **identical — verified match** |
| Cases | 40 — 20 simple / 20 compound / 0 ambiguous |

Not one byte modified. The runner hard-codes the expected SHA and refuses on mismatch; it also refuses to
start without a passed development gate, so the holdout was structurally unreachable here.

## 14. Holdout execution status

> ## NOT RUN

No `cascade_v1_holdout_*` artifact exists and no `compound-router-cascade-v1-holdout-results.csv` was
produced. **The holdout remains unspent and has never been shown to any router across four experiments.**

## Sections 15–22 — HOLDOUT METRICS: NOT RUN

| Section | Status |
|---|---|
| 15. Holdout confusion matrix | **NOT RUN** |
| 16. Precision | **NOT RUN** |
| 17. Recall | **NOT RUN** |
| 18. Specificity | **NOT RUN** |
| 19. Accuracy | **NOT RUN** |
| 20. Category metrics | **NOT RUN** |
| 21. False positives | **NOT RUN** |
| 22. False negatives | **NOT RUN** |

No values are reported because none exist. Development-stage errors are in §7–§9; development per-category
results: R1 3/3, R2 3/5 (miss `case-007`, `case-047`), R3 3/3, R4 21/21, R5 1/1, R6 15/16 (miss
`case-056`), R7 0/1 (miss `case-059`).

## 23. Call efficiency

| Metric | Development |
|---|---|
| Stage A calls | **0** (persisted V2 predictions reused) |
| Stage B calls | **18** |
| Verifier invocation rate | **34.6%** (18 of 52) |
| Mean calls/question (measured) | **0.346** |
| Mean calls/question if Stage A ran live | **1.346** |

Roughly two-thirds of questions would never invoke the verifier. This is the architecture's clearest
practical advantage and it held up.

## 24. Latency / tokens

| Metric | Stage B verifier | V3 router | V4 router |
|---|---|---|---|
| mean | **2,126.0 ms** | 7,698.8 ms | 6,794.9 ms |
| p50 | **1,921.3 ms** | 5,178.2 ms | 4,677.1 ms |
| p95 | **3,202.4 ms** | 20,123.0 ms | 19,251.7 ms |
| max | **5,150.8 ms** | 23,958.2 ms | 20,415.8 ms |
| input tokens mean | 660.5 | 647.0 | 795.0 |
| output tokens mean | **220.7** | 402.6 | 428.6 |

**The small-schema decision paid off measurably**: Stage B is ~3× faster than either single-shot router at
p50 and emits about half the output tokens. Totals: 11,889 in / 3,973 out across 18 calls. Free-tier NIM
figures excluding 65.7 s of deliberate pacing — **not** production latency.

## 25. Provider reliability

| Stage | Model | Requests | Successes | Timeouts | 429s |
|---|---|---|---|---|---|
| A (dev) | — | 0 (reused) | — | — | — |
| B (dev) | 20B | 18 | 18 | 0 | 0 |
| Holdout | — | 0 (not run) | — | — | — |

**Groq calls: 0** · **NVIDIA 120B calls: 0**. Schema compliance was perfect — 18/18 parsed, no invariant
violation, no truncation. Concurrency 1, bounded timeouts, fsync'd checkpoint per call, no fallback, no paid
provider.

## 26. Acceptance verdict

> ## CASCADE V1 = NOT ACCEPTED

Failed the development gate on recall, so it never reached the holdout. Not integrated, not deployed.

### The pattern across four experiments

| | precision | recall | specificity | accuracy |
|---|---|---|---|---|
| V2 (permissive single-shot) | 0.6875 | **1.0000** | 0.8718 | 0.9000 |
| V3 (retrieval-plan) | **0.9000** | 0.8182 | **0.9744** | **0.9400** |
| V4 (evidence-units) | 0.7500 | 0.8182 | 0.9167 | 0.8936 |
| **Cascade (V2 + verifier)** | 0.8182 | 0.8182 | 0.9487 | 0.9200 |

**Every design that adds a specificity mechanism lands on recall exactly 0.8182 (9/11) — three times, from
three architecturally different mechanisms.** V4 and the cascade lost the *identical* pair (`case-007`,
`case-047`); V3 lost a different pair (`case-030`, `case-041`). Across all attempts, 4 of the 11 compounds
have been lost by at least one design, and only V2's permissive prompt has ever kept all 11.

That convergence is the most useful result of this experiment. It suggests the binding constraint is not the
prompt, the schema, or the topology, but that a 20B model asked to apply a specificity criterion will
misclassify roughly 2 of 11 compounds — and *which* two depends on the framing rather than on capability.

## 27. Limitations

1. **Recall is quantised at n=11.** Steps of 0.091 mean one case decides the gate. The cascade missed by
   exactly one.
2. **Stage B inherits V2's needs.** The verifier judges V2's proposed split, so a poorly phrased V2 need can
   mislead it — visible in `case-007`, where V2's two needs both start with the token `T41`.
3. **Recall is bounded by V2 by construction.** Deliberate, but it means the cascade can never exceed V2's
   ceiling; every Stage B error is one-directional loss.
4. **The verifier was designed knowing V3/V4's dev failures**, so development numbers are optimistic. Hence
   the holdout — and hence not spending it.
5. **Two of six reject codes were never used** (`same_series_synthesis`, `same_subject_verification`),
   suggesting the enum is not being exercised as designed on a 18-call sample.
6. **Single run, temperature 0**, no repeat sampling; the four errors may not be stable across runs, and
   nothing here measures that.
7. **Dev labels remain single-annotator and non-blinded** — the standing ground-truth caveat applies.
8. **Retrieval locality is reasoned about, never measured.** No retrieval was executed.
9. **`case-056`/`case-059` are arguably boundary items**, as noted in the original audit; a different
   adjudication of those two would change precision and specificity, though not recall.

## 28. Future graph shape — DOCUMENTED ONLY, NOT IMPLEMENTED

If a cascade ever clears both gates, the offline graph would conceptually become:

```
question
  │
  ▼
V2 high-recall detector
  │
  ├── simple ─────────────────────────────► current top-5 RAG
  │
  └── possible compound
            │
            ▼
       strict verifier
            │
            ├── reject ─────────────────► current top-5 RAG
            │
            └── confirm
                   │
                   ▼   decomposition → parallel retrieval
                       → coverage-aware merge → final generation
```

Not implemented. Not integrated. Graph runs this task: **0**.

## 29. Production status

**Unchanged. Nothing deployed.**

| Item | State |
|---|---|
| Deployed ask image | `d5af30e` — untouched |
| Production model | Groq `openai/gpt-oss-120b` — untouched |
| `MAX_LLM_CONTEXT_CHUNKS` | 5 — unchanged |
| `TOP_K` / floor / chunk size / RRF / BM25 / Titan / Qdrant | unchanged — no retrieval executed |
| LangGraph | **OFFLINE ONLY**, decomposition v1 **FROZEN** — 0 graph runs |
| Decomposition findings | preserved: case-018 1/6→6/6, case-020 4/7→7/7, case-022 3/6→5/6 |
| Router V2 / V3 / V4 | all frozen — sources, prompts and predictions unmodified |
| Frozen holdout v1 | hash verified, **unused** |
| NVIDIA 120B judge | **1/6 scored, 5 pending** — not probed |
| Provider calls | NVIDIA 20B 18 · NVIDIA 120B 0 · Groq 0 |
