# Compound Router v2 — Offline Routing Evaluation

Routing evaluation only. No retrieval, no fan-out, no answer generation, no judging.
Production untouched. LangGraph decomposition v1 frozen and not re-run.

---

## 1. Experiment identity

| Field | Value |
|---|---|
| Experiment ID | `compound-router-v2` |
| Run fingerprint | `cc6dfde4be4dd586` |
| Manifest fingerprint | `e9702a562a635c68` (frozen before any model call) |
| Prompt SHA | `763d12cd82245285` (one design, unchanged throughout) |
| Router model | NVIDIA NIM `openai/gpt-oss-20b` |
| Concurrency | 1 (strictly sequential) |
| Dataset | 52 generative cases from `multitenant-rag-eval-60-v1` |
| Date | 2026-08-23 |
| Repo HEAD | `3564dfb` |
| Groq calls | **0** · NVIDIA 120B calls | **0** |
| **Verdict** | **FAIL** (see §20) |

Scope declared in the fingerprint and asserted by tests: `retrieval_performed: false`,
`generation_performed: false`, `judging_performed: false`, `langgraph_used: false`.

---

## 2. Router-v1 problem

| Measurement | v1 |
|---|---|
| Known compound detected | 3/3 |
| Simple controls classified compound | **3/3** |
| Precision | 50% |
| Routing accuracy | 50% |
| Simple-branch executions | **0** |

v1's rule — "two or three things that could each be answered separately" — is satisfied by almost any
two-clause question, so it behaved indistinguishably from a constant "always compound" classifier. The
analyzer responded to **surface clause and conjunction structure** rather than asking whether separate
retrieval operations were actually necessary.

---

## 3. Router-v2 definition

v2 anchors the decision on **retrieval locality** instead of grammar. A question is COMPOUND only when
answering it requires **two or more independently retrievable information needs** — each targeting a
different subject, measurement, event or explanation, and each clearly benefiting from its own focused
search query. If every requested detail concerns the **same entity, event or state** such that one
localized passage would plausibly contain them all, it is SIMPLE.

The prompt states explicitly that grammar is not evidence, and names the shapes v1 got wrong:

- several attributes or properties of ONE thing → simple
- a yes/no check plus "what does it really do" about the SAME thing → simple
- confirming, denying, correcting or bounding ONE fact → simple
- cause and effect of the SAME event → simple

### Structured output

```json
{"needs_decomposition": true,
 "information_needs": ["...", "..."],
 "reason_code": "multiple_independent_retrieval_needs"}
```

Closed 5-value enum: `multiple_independent_retrieval_needs` (the only code permitted with `true`),
`single_retrieval_need`, `single_entity_multi_attribute`, `single_event_multi_attribute`,
`negative_or_scope_check`. The parser enforces flag/code consistency in both directions and rejects a
compound verdict carrying fewer than two needs. No chain-of-thought is requested or stored.

### Isolation

Implemented in `evals/router_v2.py` + `evals/run_router_v2.py`. Verified by AST-based tests to import
neither `decomp_graph` nor the production `app`, and to call exactly one provider function (`nv.chat`) with
no retrieval or generation calls. `decomp_graph.py` was not modified.

---

## 4. Ground-truth manifest

Frozen **before** any model call, fingerprint `e9702a562a635c68`:

- 52 generative cases (routes `single` 21, `multi` 13, `group` 18)
- 8 global-search cases excluded (`case-010/011/012/013/048/049/050/051`) — global search does not enter
  the generative RAG router
- **Positive (compound): 3** — `case-018`, `case-020`, `case-022`, the cases already identified as
  "compound multi-part" before router-v2 existed
- **Negative (simple): 49** — all other generative cases

Labels are evaluation metadata only; §12's anti-leakage tests assert the analyzer receives the question and
nothing else. Labels were **not** altered after seeing router output.

---

## 5. Six-case preflight

| case | ground truth | v1 prediction | v2 prediction | verdict |
|---|---|---|---|---|
| case-018 | compound | compound | **compound** | ok |
| case-020 | compound | compound | **compound** | ok |
| case-022 | compound | compound | **compound** | ok |
| case-001 | simple | compound | **simple** | **fixed** |
| case-002 | simple | compound | compound | miss |
| case-004 | simple | compound | compound | miss |

Compounds retained **3/3**; controls corrected **1/3** (v1: 0/3). Behaviour is no longer equivalent to
"always compound", so the gate cleared the full run.

### Preflight incident — reported because it nearly produced a false pass

The first preflight attempt ran with `ROUTER_MAX_TOKENS = 400`. Two controls (`case-001`, `case-002`)
returned `output_tokens = 400` — exactly the ceiling — and their JSON was **truncated mid-object**, so they
parsed as "no JSON" and were recorded as `None`.

Two defects followed, both mine, both fixed:

1. **Harness truncation.** The ceiling was too low for a model that emits a reasoning preamble. Raised to
   1500 and truncation is now labelled `truncated_output_token_limit` rather than misreported as absent
   JSON. The four cases that did parse used 194–230 output tokens, well clear of the old cap, so raising it
   could not change them (temperature 0, identical prompt and input).
2. **A gate that passed on a technicality.** The gate tested `all(pred is True)`; two `None` values made
   that false, so it printed **PREFLIGHT PASSED with 0/3 controls actually classified simple.** The gate
   now halts as INDETERMINATE on any unparsed case and additionally fails if not one control is classified
   simple.

**The prompt was not touched** — SHA `3fc4825a89d95979` before and after the fix, verified byte-identical.
This was a harness correction, not prompt tuning against observed failures. Cost: 6 superseded NVIDIA 20B
calls under fingerprint `fab786461e289d9b`, excluded from all scoring below.

---

## 6. Full 52-case confusion matrix

|  | predicted compound | predicted simple |
|---|---|---|
| **actually compound** (3) | **TP = 3** | FN = 0 |
| **actually simple** (49) | **FP = 15** | TN = 34 |

52/52 classified, `parse_ok = 52/52`, zero provider errors, zero unscored.

Reason-code distribution: `single_retrieval_need` 22 · `multiple_independent_retrieval_needs` 18 ·
`single_entity_multi_attribute` 10 · `single_event_multi_attribute` 1 · `negative_or_scope_check` 1.

## 7. Precision
**0.167** (3 / 18)

## 8. Recall
**1.000** (3 / 3) — every known compound was detected.

## 9. F1
**0.286**

## 10. Specificity
**0.694** (34 / 49) — the binding failure against the success target.

## 11. Accuracy
**0.712** (37 / 52)

## 12. False-positive rate
**0.306** (15 / 49) — false-positive decomposition rate.

## 13. False-negative rate
**0.000** (0 / 3) — false-negative compound rate.

---

## 14. False positives

15 cases. Classified below against the architect's own §9 definition. **This does not change the verdict**
(§20 is scored strictly against the frozen labels) but it matters for deciding what to do next.

### 14a. Definitive ground-truth label defects (3)

**`case-023` — the decisive one.** Its question text is **byte-identical** to `case-022`, and its expected
answer is identical too. The two differ only in `route` (`multi` vs `group`). `case-022` is labelled
COMPOUND; `case-023` is labelled simple.

> Q (both): "What was the highest Meral Index, and why did Hollow Stair remain unverified after Zone South?"

**A router that sees only question text cannot be correct on both.** At least one error is mathematically
guaranteed, so maximum achievable specificity on this label set is bounded below 100% before the model runs.

| case | why the label is questionable |
|---|---|
| `case-023` | byte-identical question and expected answer to the labelled-compound `case-022` |
| `case-007` | "What was found inside the T41 box, and what evidence originally connected T41 to Elian Voss?" — structurally identical to the labelled-compound `case-020`, two distinct evidence targets |
| `case-046` | "What pressure threshold sends a Quill Cell to isolation, and how much korra peel is used in one ET-R3 flask?" — two entirely unrelated domains; compound under any reading of §9 |

### 14b. Genuine router errors (3)

These are one entity/event/state and the router should have said simple:

| case | question | why it is one retrieval need |
|---|---|---|
| `case-002` | "Does Pip-6 actually pollinate the flowers? What does it really do?" | one entity (Pip-6); the denial and the real function sit in the same passage — exactly the shape the prompt names as simple |
| `case-004` | "Why wasn't QL-2D approved for passenger service on the Lantern Loop? Was charging wait time the problem?" | **the architect's own canonical simple example (§10)** — one object's one state/event |
| `case-056` | "If an old post says Quiet Bloom ends at 07:00, should I use 07:00 as current?" | a single scope/recency check on one fact; `negative_or_scope_check` existed and was not used |

### 14c. Arguable — two plausibly separate retrieval targets, labelled simple by default (9)

`case-003` (current vs historical Quiet Bloom hours) · `case-005` (unverified status + evidence for each
candidate) · `case-008` (QL-2C unsuitability + QL-2D approval failure) · `case-009` (charging rule for
QL-2C and for QL-2D) · `case-025` (battery in use + why not QL-2D) · `case-030` (seeds per tray across
MS-E1/E2/E3) · `case-041` (blue ribbon at Sable Library vs Copper Orchard rover zones) · `case-047` (Quiet
Bloom window + Pip-6 behaviour during it) · `case-059` (how MS-E1/E2/E3 each changed the testing).

Most span two different objects, places or time points. They became negatives only because the manifest
assigned "all other 49 cases" to the simple class — not because anyone judged them individually.

**Summary: of 15 false positives, 3 are definitive label defects, 3 are unambiguous router errors, and 9
are genuinely arguable.** Under the strictest reading only 3/49 negatives are clear router mistakes; under
the frozen labels, 15/49 are counted.

---

## 15. False negatives

**None.** All three known compounds were detected. Recall is 3/3.

---

## 16. True-compound decomposition output

Information needs generated for the three true compounds, checked for preservation of entities,
identifiers, time constraints and scope:

| case | information needs | preservation |
|---|---|---|
| case-018 | "highest Meral Index recorded in the network" / "temperature rule that applies to cloud honey in Ember Tea" | ✅ entities, scope ("in the network"), product names intact |
| case-020 | "What evidence made Zone North probable" / "what was later found inside T41" | ✅ `Zone North`, identifier `T41` intact |
| case-022 | "highest Meral Index" / "why Hollow Stair remain unverified after Zone South" | ✅ `Hollow Stair`, `Zone South` intact |

All three split into exactly 2 atomic needs, each a single information need, with no invented facts. For
simple questions the router returned exactly one focused need in every case. **These needs were not used
for retrieval** — this task is router evaluation only.

---

## 17. Latency

| Metric | Value |
|---|---|
| mean | 4,567.9 ms |
| p50 | 2,840.9 ms |
| p95 | 13,624.2 ms |
| max | 16,010.4 ms |

Excludes scheduler pacing (110.8 s of deliberate free-endpoint spacing across the 46-case stage). The
long p95 tail reflects the free NIM endpoint, not router work.

## 18. Tokens

| Metric | Value |
|---|---|
| input mean / total | 518.3 / 26,951 |
| output mean / total | 286.6 / 14,905 |

### Cost/benefit versus v1

v1 decomposed every control it saw (3/3), i.e. it would decompose all 49 negatives. v2 decomposes 15.

| | v1 (projected) | v2 (measured) |
|---|---|---|
| Unnecessary decompositions over 49 negatives | 49 | **15** |
| Avoided | — | **34** |
| Analyzer overhead avoided (at v1's measured +2.6 s / +462 tok) | — | **≈88.4 s, ≈15,708 tokens** |

Excludes the +(N−1) retrieval operations each unnecessary decomposition would additionally cause. No
monetary cost is claimed.

---

## 19. Provider reliability

| Stage | Model | Requests | Successes | Timeouts | 429s |
|---|---|---|---|---|---|
| Superseded preflight (truncated, unscored) | 20B | 6 | 6 | 0 | 0 |
| Preflight (scored) | 20B | 6 | 6 | 0 | 0 |
| Full run | 20B | 46 | 46 | 0 | 0 |
| **Total** | **20B** | **58** | **58** | **0** | **0** |

**Groq calls: 0** (guard installed; any Groq usage raises). **NVIDIA 120B calls: 0** — not probed in this
task. Concurrency 1, bounded timeouts, durable checkpoint after every single classification, no fallback,
no paid provider. Zero parse failures and zero provider errors in the scored run.

---

## 20. Success-target verdict

> ## FAIL

| Requirement | Target | Actual | Result |
|---|---|---|---|
| Compound recall | 3/3 | **3/3** | ✅ PASS |
| Simple specificity | ≥ 90% | **69.4%** | ❌ FAIL |
| False positives | ≤ 4 of 49 | **15 of 49** | ❌ FAIL |

Recall is met; specificity misses by a wide margin — 15 false positives against a ceiling of 4. Router-v2
is **NOT ACCEPTED** and must not be integrated into a LangGraph v2 experiment.

Stated plainly: v2 is a real improvement over v1 — v1's specificity on its controls was 0%, v2 reaches
69.4% and avoids 34 of 49 unnecessary decompositions — but improvement is not the bar. The bar was ≥90%,
and v2 does not clear it.

**No prompt was rewritten in response to these results.** One design was evaluated once, as instructed.

---

## 21. Limitations

1. **Only 3 positive cases.** This experiment gives strong evidence about false-positive rate and simple
   specificity (49 negatives) but only **directional** evidence about compound recall. 3/3 recall on three
   cases is not a production-ready recall claim, and no such claim is made.
2. **The label set is internally inconsistent.** `case-022` and `case-023` carry byte-identical questions
   and expected answers with opposite labels (§14a), so 100% accuracy is unreachable by construction. Two
   further negatives are structurally identical to labelled positives.
3. **Negatives were assigned wholesale.** All 49 came from "everything that is not one of the 3 known
   compounds", not from per-case adjudication. §14c shows at least 9 are genuinely arguable, so the true
   false-positive count is somewhere between 3 and 15 and the reported 69.4% is a lower bound on
   specificity.
4. **Synthetic corpus** of clean generated blog posts; real user questions are messier.
5. **Router-only evaluation.** Whether better routing improves end-to-end answer quality is untested here —
   the generated information needs were deliberately not used for retrieval.
6. **Single run, temperature 0**, but no repeat-sampling was done, so run-to-run stability is unmeasured.
7. **Free-endpoint latency** (p95 13.6 s) is not a usable production latency estimate.
8. **The preflight incident** (§5) means the first six classifications were discarded; had the gate bug not
   been caught, this experiment would have proceeded on a false pass.

---

## 22. Recommendation

Presented as options with evidence. **Sequencing and go/no-go are the architect's decisions — none of this
has been actioned, and no router-v3 was built.**

1. **Fix the ground truth before iterating on the router.** This is now the blocking item, not the prompt.
   `case-022`/`case-023` are byte-identical with opposite labels; the label set cannot support a ≥90%
   specificity target while that holds. Recommend per-case adjudication of all 52 against the §9 definition
   (independently retrievable needs), by a human or by an offline process that is *not* the router under
   test, before any router-v3 is scored.
2. **Re-score v2 unchanged against the corrected labels.** Zero new model calls: all 52 predictions are
   durably checkpointed with their reason codes. If v2's specificity clears 90% against adjudicated labels,
   the router may already be adequate and the v2 "failure" was substantially a measurement artifact. This
   is the cheapest next step by a wide margin and should precede any prompt work.
3. **Only then consider router-v3.** The three unambiguous errors (§14b) share a shape: the router treats a
   *contrast or verification framing* about one object ("does it do X? what does it really do", "was Y the
   problem?", "should I use the old value?") as two needs. That is a narrower defect than v1's, and worth a
   targeted design — but scoring it against the current labels would be measuring noise.
4. **Enlarge the positive class.** Three positives cannot establish recall. Any v3 evaluation needs
   materially more compound cases, ideally authored to span different compound shapes.
5. **Not indicated:** a larger router or application model (recall is already 3/3 and the failures are
   definitional, not capacity-driven), retrieval changes, or integrating v2 into the graph.

---

## 23. Production status

**Unchanged. Nothing deployed.**

| Item | State |
|---|---|
| Deployed ask image | `d5af30e` — untouched |
| Production model | Groq `openai/gpt-oss-120b` — untouched |
| `MAX_LLM_CONTEXT_CHUNKS` | 5 — unchanged |
| `TOP_K` / floor / chunk size / overlap / RRF / BM25 / Titan / Qdrant | unchanged — this task executed no retrieval |
| Lambda dependencies | unchanged (no `langgraph`, no `langchain-core`, no router code in any image) |
| LangGraph decomposition v1 | **OFFLINE / FROZEN** — not re-run; checkpoints and quarantine intact |
| NVIDIA 120B judge | **1/6 scored, 5 pending** — not probed in this task |
| Router-v2 | offline router experiment only; **NOT ACCEPTED**, not integrated |
