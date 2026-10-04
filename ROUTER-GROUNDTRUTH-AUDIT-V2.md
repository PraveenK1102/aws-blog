# Router Ground-Truth Audit v2 + Zero-Cost Rescore

Dataset and evaluation-policy work only. **Zero** NVIDIA, Groq, judge, retrieval and LangGraph calls.
Router-v2 was not modified and its 52 predictions were not re-run.

---

## 1. Why the audit was required

The original label policy was an assumption, not an adjudication:

> `case-018`/`case-020`/`case-022` = compound; **all other 49 = simple**

The 49 negatives were never individually reviewed. That produced an internally **invalid** manifest, proved
by one case: `case-022` and `case-023` have **byte-identical question text** (same normalized hash
`beb43143e30e81c3`) and identical expected answers, yet carried **opposite** labels.

A router that sees only question text cannot classify identical strings differently. At least one error was
therefore **guaranteed before the model ran**, so the reported figures could not be treated as router
quality:

| Superseded metric | Value |
|---|---|
| precision | 0.167 |
| recall | 1.000 |
| specificity | 0.694 |
| accuracy | 0.712 |

---

## 2. Annotation policy

`annotation_policy_version = independent-retrieval-needs-v1`

**COMPOUND** — answering requires **two or more independently retrievable information needs**, such that a
focused retrieval query for one need would not reasonably be expected to retrieve sufficient evidence for
the other.

**SIMPLE** — all requested facts belong to **one** localized information need / entity / event / state, and
a single focused query could reasonably retrieve the evidence for the whole answer.

**AMBIGUOUS** — reasonable annotators could genuinely disagree under the definition above. Not used for
annotation convenience.

Grammar does not determine the label. None of the following alone makes a question compound: the word
"and", multiple clauses, two requested attributes, a contrast, a yes/no plus explanation, or several
requested fields about one localized event/object/state.

### Procedure (applied to every question)

- **Step A** — identify the atomic outputs the question asks for.
- **Step B** — group those outputs by the evidence target each requires.
- **Step C** — ask whether one focused query over a localized topic/entity/event would reasonably retrieve
  evidence for all of them. Yes → SIMPLE. No, and two or more focused retrievals are needed → COMPOUND.

### Decision rules, fixed before annotating

To keep 52 judgements consistent rather than case-by-case intuition, each label cites one rule:

| Rule | Condition | Label |
|---|---|---|
| R1 | two unrelated topical domains | COMPOUND |
| R2 | two distinct events/entities each needing its own explanation, from different narrative threads | COMPOUND |
| R3 | N distinct named sibling entities, each contributing its **own** required value/explanation | COMPOUND (N needs) |
| R4 | one entity/event/state, several attributes of it | SIMPLE |
| R5 | same entity **and** same attribute across time (current vs historical) | SIMPLE |
| R6 | scope / recency / negative / applicability check on one fact | SIMPLE |
| R7 | one synthesis output spanning a series (one output, not N values) | SIMPLE |

R3 vs R7 is the distinction that separates `case-030` ("how many seeds per tray in MS-E1, MS-E2 **and**
MS-E3" — three required values → compound) from `case-059` ("**summarize** how MS-E1, MS-E2 and MS-E3
changed what the team was testing" — one synthesis → simple). Both name three entities; only one asks for
three answers.

---

## 3. Data isolation

The primary annotation pass consumed a purpose-built input containing **only** `case_id`, `question` and
`normalized_question_hash` (`output/router_gt_annotation_input.json`). Deliberately absent: router-v2
predictions, router reason codes, the previous labels, baseline scores, judge scores, generated answers and
expected answers.

`route` was **not** used. Route cannot change how many independent retrieval needs a question has —
`route_used_for_labels: false` is recorded in the manifest metadata and asserted by a test.

**Stated limitation, because it affects how much this audit is worth (see §19.1):** the annotator here is
the same agent that reported router-v2's results in a previous step, so it had prior exposure to those
predictions. This annotation is therefore **not blinded**. The mitigations are structural, not
psychological: labels were derived from the seven rules above fixed in advance, each case records its rule
and a concise reason, and duplicate consistency is enforced mechanically. An independent blinded annotator
remains the correct way to establish this ground truth.

---

## 4. Duplicate-question analysis

Normalization: Unicode NFKC, strip, collapse internal whitespace. No semantic rewriting.

**48 unique questions across 52 cases — 4 duplicate groups:**

| Group | Cases | Question | Shared label |
|---|---|---|---|
| `dup-01` | case-015, case-016 | "What caused the Ternlink outage at North Fen, and how many records were recovered?" | simple |
| `dup-02` | case-054, case-055 | "What did the T41 box contain?" | simple |
| `dup-03` | case-052, case-053 | "How many ORIOLE-K crates reached Kestrel Station Two?" | simple |
| `dup-04` | **case-022, case-023** | "What was the highest Meral Index, and why did Hollow Stair remain unverified after Zone South?" | **compound** |

Only `dup-04` was mislabelled under the old policy. The other three groups happened to agree because all
their members fell in the "all other 49" bucket.

A validation assertion now makes a conflicting duplicate label **impossible**: the audit computes label
sets per normalized hash and aborts with `DUPLICATE LABEL CONFLICT` before any rescore.

---

## 5. Corrected class distribution

| Label | Count | Share |
|---|---|---|
| simple | **39** | 75.0% |
| compound | **11** | 21.2% |
| ambiguous | **2** | 3.8% |
| **total** | **52** | |

**Compound cases (11):** `case-007`, `case-008`, `case-009`, `case-018`, `case-020`, `case-022`,
`case-023`, `case-030`, `case-041`, `case-046`, `case-047`.

The positive class is **11, not 3** — see §17.

---

## 6. Ambiguous cases

Two, both excluded from hard metrics:

| case | question | why ambiguous |
|---|---|---|
| `case-005` | "Why was the Hollow Stair still considered unverified after Zone South was surveyed, and what evidence supported each candidate?" | "each candidate" may require two zones' evidence, or may be the same verification narrative restated |
| `case-025` | "Which battery is currently used by the Lantern Loop fleet, and why isn't QL-2D used despite its energy advantage?" | arguably one battery-selection decision; arguably two separate lookups |

Both remain in the audit and in all exports, flagged `EXCLUDED_AMBIGUOUS`.

---

## 7. Validation

All invariants asserted **before** any rescore; failure aborts with exit 1:

- ✅ 52 generative case IDs represented
- ✅ zero global-search cases (8 excluded: `case-010/011/012/013/048/049/050/051`)
- ✅ all case IDs unique
- ✅ every normalized-duplicate group shares exactly one label **and** one need count
- ✅ every label ∈ {simple, compound, ambiguous}
- ✅ every compound has `independent_retrieval_need_count ≥ 2` and ≥2 atomic needs
- ✅ every simple has `independent_retrieval_need_count = 1`
- ✅ every case carries a concise reason (<200 chars, no chain-of-thought) and a cited rule

Result: `validation: OK (52 cases, 4 duplicate groups, no label conflicts)`.

Covered by **31 tests** in `evals/test_gt_audit.py`, including an AST check that the audit script imports
no provider, harness, router or boto3 module.

---

## 8. Manifest freeze

| Field | Value |
|---|---|
| `annotation_policy_version` | `independent-retrieval-needs-v1` |
| `manifest_sha256` | `8b1111bce456a156cd0c580da548dafa…` (full value in `output/router_gt_v2_meta.json`) |
| unique questions | 48 |
| duplicate groups | 4 |
| simple / compound / ambiguous | 39 / 11 / 2 |
| supersedes | manifest fingerprint `e9702a562a635c68` |

Labels were frozen before predictions were loaded and were **not** edited afterwards.

---

## 9. Original router-v2 metrics (SUPERSEDED, preserved)

Retained in `output/router_v2_metrics.json` and `compound-router-v2-metrics.csv`; nothing was overwritten.

| Metric | Value |
|---|---|
| positives / negatives / ambiguous | 3 / 49 / 0 |
| TP / FP / TN / FN | 3 / 15 / 34 / 0 |
| precision | 0.1667 |
| recall | 1.0000 |
| F1 | 0.2857 |
| specificity | 0.6939 |
| accuracy | 0.7115 |
| verdict | FAIL |

## 10. Adjudicated router-v2 metrics + confusion matrix

Same 52 immutable predictions, adjudicated labels, ambiguous excluded.

|  | predicted compound | predicted simple |
|---|---|---|
| **actually compound** (11) | **TP = 11** | FN = 0 |
| **actually simple** (39) | **FP = 5** | TN = 34 |

Scored cases 50; ambiguous excluded 2.

| Metric | Original | **Adjudicated** | Δ |
|---|---|---|---|
| positives / negatives / ambiguous | 3 / 49 / 0 | **11 / 39 / 2** | — |
| TP / FP / TN / FN | 3 / 15 / 34 / 0 | **11 / 5 / 34 / 0** | −10 FP |
| **precision** (§13) | 0.1667 | **0.6875** | **+0.521** |
| **recall** (§14) | 1.0000 | **1.0000** | 0.000 |
| F1 | 0.2857 | **0.8148** | +0.529 |
| **specificity** (§15) | 0.6939 | **0.8718** | **+0.178** |
| **accuracy** (§16) | 0.7115 | **0.9000** | **+0.189** |
| false-positive decomposition rate | 0.3061 | **0.1282** | −0.178 |
| false-negative compound rate | 0.0000 | **0.0000** | 0.000 |

---

## 11. Original 15 false positives — disposition

Derived **after** the manifest was frozen; these categories did not influence any label.

### A. LABEL WAS WRONG — router prediction is correct under adjudicated ground truth — **8**

`case-007`, `case-008`, `case-009`, `case-023`, `case-030`, `case-041`, `case-046`, `case-047`

| case | rule | why it is genuinely compound |
|---|---|---|
| case-007 | R2 | T41 contents vs pre-discovery Elian Voss provenance — different evidence targets |
| case-008 | R3 | separate rejection explanation per sibling product (QL-2C, QL-2D) |
| case-009 | R3 | "respectively" demands one charging rule per version |
| case-023 | R2 | byte-identical to `case-022`, which was always labelled compound |
| case-030 | R3 | three required values, one per MS-E experiment |
| case-041 | R1 | Sable Library convention vs Copper Orchard rover safety — unrelated domains |
| case-046 | R1 | Quill Cell pressure threshold vs ET-R3 korra peel quantity — unrelated domains |
| case-047 | R2 | a schedule window vs a robot's function — different entities |

### B. ROUTER REALLY WRONG — ground truth remains simple — **5**

| case | rule | why it is one retrieval need |
|---|---|---|
| `case-002` | R4 | one entity (Pip-6); the denial and the real function are the same localized fact |
| `case-003` | R5 | same entity and attribute across time — one topical cluster |
| `case-004` | R4 | one object's single approval decision; the second clause tests the same cause |
| `case-056` | R6 | a single recency check on one fact |
| `case-059` | R7 | one synthesis output, not three values |

Shared defect: the router treats a **contrast, verification or temporal framing about one subject** as two
needs. `case-002`/`case-004`/`case-056` all have a `negative_or_scope_check` or
`single_entity_multi_attribute` code available and did not use it — it used
`multiple_independent_retrieval_needs` for all five.

### C. AMBIGUOUS — excluded from hard scoring — **2**

`case-005`, `case-025`

**Totals: A = 8, B = 5, C = 2 (= 15).**

---

## 12. case-022 / case-023 correction

| | case-022 | case-023 |
|---|---|---|
| normalized question hash | `beb43143e30e81c3` | `beb43143e30e81c3` (**identical**) |
| route | `group` | `multi` |
| expected answer | identical | identical |
| **old label** | compound | **simple** ← the defect |
| **new shared label** | **compound** | **compound** |
| need count | 2 | 2 |
| duplicate group | `dup-04` | `dup-04` |

The question requires two independently retrievable needs — the highest Meral Index measurement, and the
reasoning for the Hollow Stair remaining unverified — which target different evidence. That is the
architect's own canonical compound example.

**Route difference does not alter the semantic number of independent retrieval needs.** Route determines
*which tenants' documents* are searched, not *how many distinct evidence targets* the question has. A
question needing two focused retrievals needs two whether it is scoped to one profile, several profiles, or
a group. Labelling by route is what produced the contradiction.

---

## 13. Precision
**0.6875** (11 / 16) — up from 0.1667.

## 14. Recall
**1.0000** (11 / 11) — unchanged. Every adjudicated compound was detected, including all 8 that the old
labels had counted as false positives. Zero false negatives.

## 15. Specificity
**0.8718** (34 / 39) — up from 0.6939.

## 16. Accuracy
**0.9000** (45 / 50) — up from 0.7115.

---

## 17. Diagnostic threshold result

| Threshold | Required | Actual | Result |
|---|---|---|---|
| compound recall | ≥ 0.90 | **1.0000** | ✅ PASS |
| simple specificity | ≥ 0.90 | **0.8718** | ❌ FAIL |
| compound precision | ≥ 0.80 | **0.6875** | ❌ FAIL |

> ## Verdict: NOT ACCEPTED

Two of three thresholds fail. Specificity misses by 0.028 (one more true negative — 35/39 = 0.897 — would
still miss it; it needs 2 fewer false positives). Precision misses by 0.113 (it needs 2 fewer false
positives to reach 13/13+3 = 0.8125).

**Both failing thresholds are limited by the same 5 false positives**, all genuine router errors of one
recognisable shape (§11B). Not a broad failure — a specific, narrow, fixable defect.

### Positive-class size

The dataset contains **11 genuinely compound questions**, not 3. The previous count came from an assumption
that only the three already-flagged cases were compound; the other 49 were never individually reviewed.
The real positive class is **3.7× larger**, which is why precision moved so far.

---

## 18. Measurement error vs router error

Of the 15 originally reported false positives:

| Source | Count | Share |
|---|---|---|
| **Ground-truth measurement error** (label was wrong) | **8** | **53%** |
| **True routing error** (router genuinely wrong) | **5** | **33%** |
| Ambiguous (excluded) | 2 | 13% |

**A majority of router-v2's apparent failure was measurement error, not router error.** Concretely:

- Reported specificity was **0.694**; adjudicated is **0.8718**. About **75%** of the specificity gap to
  the 0.90 target (0.206 of the original 0.306 shortfall) was the label set, not the router.
- Reported precision was **0.167**; adjudicated is **0.6875** — a **4.1×** change from re-labelling alone,
  with the predictions byte-identical.
- Recall was never affected: 1.000 under both label sets.

### Sanity check: did v2 actually learn the distinction, or just detect "and"?

v1's diagnosed defect was keying on surface conjunctions. The adjudicated label set can test whether v2
escaped that, because it contains **10 compound questions with "and", 1 without, and 11 simple questions
with "and"** — so a conjunction detector is separable from a semantic router here.

| | precision | recall | specificity | accuracy |
|---|---|---|---|---|
| Naive "and"-detector | 0.4762 | 0.9091 | 0.7179 | 0.7600 |
| **Router-v2 (adjudicated)** | **0.6875** | **1.0000** | **0.8718** | **0.9000** |

Router-v2 beats the naive detector on **every** metric, and notably gets `case-041` right — the one compound
question with no "and" — which the detector misses. It also correctly calls 6 of the 11 simple-with-"and"
questions simple, which v1 could not do at all. **v2 did learn something beyond grammar**; it simply did not
learn enough to clear the thresholds.

**But the corrected verdict is still NOT ACCEPTED.** The 5 remaining false positives are real, they are the
binding constraint on both failing thresholds, and correcting the ground truth did not make router-v2 good
enough. The honest summary: the original scoring **overstated** the router's failure by roughly a factor of
three, and the router **still** falls short.

---

## 19. Limitations

1. **The annotation is not blinded.** The annotator had prior exposure to router-v2's predictions from an
   earlier step (§3). Mitigations were structural — rules fixed in advance, a cited rule and concise reason
   per case, mechanical duplicate enforcement — but a genuinely independent annotator is required before
   these labels should be treated as authoritative. This is the single biggest caveat on §17's verdict.
2. **This is a post-hoc rescore of the same dataset.** Ground truth was corrected *after* the predictions
   existed. Even a passing result could not have established generalisation — which is why §20's holdout is
   mandatory regardless of outcome.
3. **Single annotator, no inter-annotator agreement.** No κ statistic exists. The 2 ambiguous cases are
   almost certainly an undercount of genuine disagreement.
4. **11 positives is still small.** Recall of 1.000 over 11 cases is encouraging but not a production claim.
5. **Retrieval locality was reasoned about, not measured.** Step C asks whether one focused query *would
   reasonably* retrieve all needs. No retrieval was executed (by instruction), so the labels encode a
   judgement about corpus structure rather than measured retrieval behaviour. A future audit could validate
   labels empirically by running each atomic need as its own query.
6. **Expected answers were deliberately not used** for the primary pass (§9 of the brief). The consistency
   audit they would enable has not been performed, so no case has been flagged for architect review on that
   basis.
7. **Synthetic corpus** with clean, unambiguous facts.
8. **Grammar bias in the benchmark is real but partial.** 10 of the 11 compound questions contain "and",
   so the positive class is conjunction-heavy. It is not purely grammar-separable — 11 simple questions also
   contain "and", which is what makes the §18 naive-detector comparison meaningful — but only **one**
   compound question lacks "and", so the benchmark barely tests compound-detection in the absence of a
   conjunction. §20's holdout must fix this.

---

## 20. Holdout requirement

**Mandatory before router-v2 could ever be integrated, regardless of this rescore.** Documented only —
not created and not run in this task.

A future router-only holdout must be **unseen** and should deliberately include:

- simple questions containing "and"
- single-entity multi-attribute questions
- single-event multi-attribute questions
- negative / scope checks
- contrast questions
- two unrelated facts
- different entities
- different events
- different times requiring independent retrieval
- **compound questions without the word "and"**

The last item and the first are the load-bearing ones: §19.8 shows 10/11 current positives contain "and",
so the present benchmark cannot distinguish a genuine router from an "and"-detector. It should also carry
substantially more compound examples than 11.

---

## 21. Production status

**Unchanged. Nothing deployed.**

| Item | State |
|---|---|
| Deployed ask image | `d5af30e` — untouched |
| Production model | Groq `openai/gpt-oss-120b` — untouched |
| `MAX_LLM_CONTEXT_CHUNKS` | 5 — unchanged |
| `TOP_K` / floor / chunk size / RRF / BM25 / Titan / Qdrant | unchanged — no retrieval executed |
| LangGraph | **OFFLINE ONLY**, decomposition v1 **FROZEN** — 0 graph runs |
| Decomposition findings | preserved unchanged: case-018 1/6→6/6, case-020 4/7→7/7, case-022 3/6→5/6 |
| NVIDIA 120B judge | **1/6 scored, 5 pending** — not probed |
| Router-v2 | offline evaluation only; **NOT ACCEPTED**, not integrated, source and prompt unmodified |
| Provider calls this task | NVIDIA 20B **0** · NVIDIA 120B **0** · Groq **0** |

---

## 22. Next recommendation

Options with evidence. **Sequencing is the architect's decision — nothing below was actioned, and no
router-v3 was designed or built.**

1. **Independent re-annotation of the 52 questions (highest value, no model cost).** §19.1 is the weakest
   link in this audit: the labels that produced a 4.1× precision change were set by an annotator with prior
   exposure to the predictions. A blinded second annotator, scoring inter-annotator agreement against
   `compound-router-groundtruth-v2.jsonl`, would either harden these labels or expose bias. The rescore
   re-runs at zero cost against any corrected manifest.
2. **Then a router-v3 experiment targeting the single observed defect.** All 5 genuine errors share one
   shape: contrast, verification or temporal framing about **one** subject read as two needs. That is far
   narrower than v1's failure and the enum already contains the right codes
   (`negative_or_scope_check`, `single_entity_multi_attribute`) — the router simply did not reach for them.
3. **Build the §20 holdout before, not after, any v3 scoring** — and make sure it contains compound
   questions without "and", plus simple questions with it. Scoring v3 on this same 52 would measure
   overfitting.
4. **Not indicated:** integrating router-v2 (fails 2 of 3 thresholds), a larger router or application model
   (recall is already 1.000; the failures are definitional, not capacity), retrieval changes, re-running
   decomposition, or probing the 120B judge.
