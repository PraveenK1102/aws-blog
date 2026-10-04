# RAGAS + DeepEval Evaluation of the Frozen Routed RAG — Calibration STOP

**Outcome: STOPPED AT CALIBRATION per §12. The full 36-record run was NOT executed.**

The independent evaluator was available and the harness was made to work with both frameworks, but
calibration exposed pathological evaluator/framework behaviour on **four** of §12's own criteria. Spending
~900 more evaluator calls on demonstrably unreliable metrics would have produced numbers that look like
evidence and are not.

Zero Titan, Qdrant, Groq and NVIDIA-120B calls. No application output regenerated. Production unchanged.

---

## 1. Objective

Evaluate the frozen routed architecture with RAGAS and DeepEval against the exact persisted contexts and
answers — no new retrieval, no regeneration, no graph execution.

## 2. Frozen application artifacts

Nothing was regenerated. Sources, all read-only: `rag-model-eval-nvidia20b-top5-v1-full.jsonl` (baseline
answers + contexts), `output/routed_live_v2_cases.jsonl` (routed answers + merged contexts),
`compound-router-groundtruth-v2.jsonl` (adjudicated labels), `output/router_v2_results.jsonl` (routes).

## 3. Evaluation dataset

**36 records = 18 baseline + 18 routed**, over the 18 V2-compound cases.
Exported to `ragas-deepeval-eval-inputs.jsonl`.

| Property | Value |
|---|---|
| cases | 18 (11 adjudicated compound, 5 simple/V2-FP, 2 ambiguous) |
| context count | exactly 5 in every record |
| baseline/routed contexts byte-identical | 2 of 18 cases |
| reference-refusal / negative-verification cases | `case-002`, `case-004`, `case-056` |
| decomposition-fallback cases | `case-041`, `case-056`, `case-059` |

## 4. Exact-context construction

Contexts are the **exact persisted strings** the application model saw — asserted, never reconstructed from
citations, post IDs, source documents or a Qdrant query. Each record carries a `context_sha256` so drift is
detectable. For routed records the LLM-visible context set is the citation-eligible set, as the graph
guarantees.

## 5. RAGAS configuration

RAGAS **0.4.3**, eval-only, never added to a Lambda requirement. In 0.4.x the metrics moved to
`ragas.metrics.collections`, scored directly rather than through `ragas.evaluate()` so every provider call is
attributable and bounded:

| Feedback key | Metric class | Inputs |
|---|---|---|
| `ragas_faithfulness` | `Faithfulness` | question, answer, contexts |
| `ragas_answer_relevancy` | `AnswerRelevancy` | question, answer (+ local embeddings) |
| `ragas_context_precision` | `ContextPrecisionWithReference` | question, reference, contexts |
| `ragas_context_recall` | `ContextRecall` | question, contexts, reference |

Embeddings are **local** (`sentence-transformers/all-MiniLM-L6-v2` via `HuggingFaceEmbeddings`) so no
embedding provider is called and no Titan call occurs.

**Two environment problems had to be solved, both documented as findings:**
1. ragas 0.4.3 imports `langchain_community.chat_models.vertexai`, removed in langchain-community 0.4.2 —
   an upstream incompatibility. Rather than downgrade inside the validated langgraph venv, the frameworks
   were installed in a **separate isolated venv** (`~/.cache/mtrag-eval-frameworks`) with
   `langchain-community<0.4`. The langgraph eval environment was left untouched and re-verified (41 routed +
   30 decomposition tests still pass).
2. The 0.4 collection metrics call `ascore()` → `agenerate()`, which requires a genuine `AsyncOpenAI`
   instance. A proxy wrapper fails ragas' `isinstance` check with *"Cannot use agenerate() with a synchronous
   client"*, so the bounded counter had to patch the **bound method on the real client object** rather than
   wrap it.

## 6. DeepEval configuration

DeepEval **4.1.10**, eval-only, never added to a Lambda requirement.
`FaithfulnessMetric`, `AnswerRelevancyMetric`, `ContextualPrecisionMetric`, `ContextualRecallMetric`, all
`async_mode=False`, driven by a custom `DeepEvalBaseLLM` pointed at the NVIDIA evaluator. Keys stored as
`deepeval_faithfulness`, `deepeval_answer_relevancy`, `deepeval_contextual_precision`,
`deepeval_contextual_recall`.

Structured output needed hardening for a reasoning-style evaluator: the model emits chain-of-thought around
its JSON, so a greedy `{.*}` regex either swallowed prose or stopped at the wrong brace. The adapter now
extracts **balanced** JSON objects and validates longest-first, appends the schema to the prompt, and
**raises rather than fabricating an empty schema instance** — an empty instance would have surfaced as a real
score.

## 7. Evaluator model

`nvidia/nemotron-3-nano-30b-a3b`, used **only as evaluator**, never as an application-model upgrade.
Deliberately different from the GPT-OSS-20B application model, so the judge is independent of the system
judged. NVIDIA 120B was **not** probed.

**Exactly one availability probe** was made, as instructed: **OK in 0.8 s**. Note the probe already showed
the model emitting a reasoning preamble rather than answering directly — the first hint of the structured
output problems that followed.

## 8. Calibration

Six records chosen to span the required properties, using existing artifacts only:

| record | deterministic expectation |
|---|---|
| `case-018::baseline` | **weak** — answer 1/4, context 1/6 |
| `case-018::routed` | **strong** — answer 4/4, context 6/6 |
| `case-007::routed` | unchanged, at ceiling 4/4 |
| `case-002::routed` | refusal-style reference |
| `case-056::routed` | refusal-style reference + fallback path |
| `case-041::routed` | partial — context 2/3, true compound not decomposed |

### Results

| record | framework | faithfulness | answer relevancy | context precision | context recall |
|---|---|---|---|---|---|
| case-018::**baseline** (weak) | ragas | **nan** | **0.660** | 0.000 | ERR |
| | deepeval | **1.000** | 1.000 | **1.000** | 0.000 |
| case-018::**routed** (strong) | ragas | ERR | **0.592** | ERR | nan |
| | deepeval | **1.000** | 1.000 | 1.000 | **1.000** |
| case-007::routed (unchanged) | ragas | ERR | 0.919 | 0.806 | ERR |
| | deepeval | 1.000 | **0.500** | 1.000 | 1.000 |
| case-002::routed (refusal) | ragas | ERR | 0.576 | 0.700 | nan |
| | deepeval | 1.000 | 1.000 | 0.833 | **0.000** |
| case-056::routed (refusal+fallback) | ragas | nan | 0.641 | 0.700 | ERR |
| | deepeval | 1.000 | 1.000 | 0.804 | 1.000 |
| case-041::routed (partial) | ragas | ERR | 0.612 | ERR | ERR |
| | deepeval | 1.000 | 1.000 | 1.000 | 1.000 |

### Reliability

| metric | usable / 6 | errors | nan |
|---|---|---|---|
| `ragas_faithfulness` | **0/6** | 4 | 2 |
| `ragas_context_recall` | **0/6** | 4 | 2 |
| `ragas_context_precision` | 4/6 | 2 | 0 |
| `ragas_answer_relevancy` | 6/6 | 0 | 0 |
| `deepeval_faithfulness` | 6/6 | 0 | 0 |
| `deepeval_answer_relevancy` | 6/6 | 0 | 0 |
| `deepeval_contextual_precision` | 6/6 | 0 | 0 |
| `deepeval_contextual_recall` | 6/6 | 0 | 0 |

RAGAS failure modes: `IncompleteOutputException` (the reasoning model exhausts even an 8,000-token ceiling
before emitting structured output) and `InstructorRetryException` (schema validation on partial JSON, e.g.
`StatementGeneratorOutput.statements` / `ContextRecallOutput.classifications` missing).

### Calibration verdict — STOP

Four of §12's pathological criteria are met:

1. **"Framework repeatedly fails to parse."** RAGAS `faithfulness` and `context_recall` are **0/6 usable**.
2. **"Clearly unsupported answer receives perfect faithfulness."** DeepEval scored `faithfulness = 1.000` on
   **all six** records, including `case-018::baseline`, whose answer asserts *"No one in this selection has
   written about the highest Meral Index"* — a confident false negative. The metric shows **no
   discrimination whatsoever**.
3. **"Clearly improved complete answer scores materially worse."** RAGAS `answer_relevancy` rates the weak
   baseline **0.660** above the strong routed answer **0.592** — inverted against a 1/4 → 4/4 fact-coverage
   improvement.
4. **"Provider reliability is poor."** 23 errors across 150 calls = **15%**.

Additional evidence of unreliability: DeepEval `contextual_precision = 1.000` for the `case-018` baseline
context, which deterministically contained **1 of 6** reference facts.

**One metric behaved correctly**: DeepEval `contextual_recall` moved **0.000 → 1.000** across
`case-018` baseline → routed, matching the deterministic finding exactly. It is the only signal here that
independently reproduces the measured improvement.

## 9. Provider-call budget

Measured, not assumed — the architect's warning against `36 × 4 = 144` was well founded:

| | calls per record | measured |
|---|---|---|
| RAGAS (4 metrics) | **18.0** | 108 / 6 records |
| DeepEval (4 metrics) | **7.0** | 42 / 6 records |
| **Combined** | **25.0** | 150 / 6 records |

Projected full 36-record run: **≈900 evaluator calls** — 6.25× the naive 144 estimate. Every call passed
through one bounded counter that raises `BudgetExceeded` before breaching the cap; calibration ran under a
cap of 250 and used 150.

## 10. Reliability

150 calls, **23 errors (15%)**, 191,364 input / 116,884 output tokens. No retries beyond framework-internal
instructor retries. No paid fallback, no Groq, no NVIDIA 120B.

## Sections 11–16 — FULL-RUN METRICS: NOT RUN

| Section | Status |
|---|---|
| 11. Baseline RAGAS metrics | **NOT RUN** |
| 12. Routed RAGAS metrics | **NOT RUN** |
| 13. RAGAS deltas | **NOT RUN** |
| 14. Baseline DeepEval metrics | **NOT RUN** |
| 15. Routed DeepEval metrics | **NOT RUN** |
| 16. DeepEval deltas | **NOT RUN** |

No baseline-vs-routed framework means or deltas exist. Reporting any would be fabrication. The only
framework numbers that exist are the 6 calibration records in §8, exported to `ragas-routed-results.csv` and
`deepeval-routed-results.csv` and clearly stamped `stage=calibration`.

## 17. Framework disagreement

Computed on calibration data only, reported independently — **never averaged across frameworks**, per §9.

- **Comparable metric pairs: 10 of 24** (58% unusable because a RAGAS side errored or returned `nan`).
- **Mean absolute disagreement on comparable pairs: 0.3769** on a 0–1 scale.

A ~0.38 mean gap between two frameworks nominally measuring the same construct is itself a substantive
finding: on this evaluator these metrics are not interchangeable, and neither is trustworthy without
per-metric validation.

## 18. True-compound analysis
**NOT AVAILABLE** — requires the full run. Calibration covered `case-018` (compound, strong improvement) and
`case-007`/`case-041` (compound); no aggregate is defensible from 6 records with 58% unusable pairs.

## 19. V2 false-positive analysis
**NOT AVAILABLE** from framework metrics. The deterministic finding stands unchanged: **all 5 V2 false
positives preserved answer quality** (§23).

## 20. Fallback analysis
Calibration included two of the three fallback cases. `case-056::routed` (refusal + fallback) scored DeepEval
faithfulness 1.000 / relevancy 1.000 / precision 0.804 / recall 1.000, and RAGAS returned `nan` faithfulness.
No conclusion is drawn from two records.

## 21. case-041 — KNOWN LIMITATION / FUTURE HYPOTHESIS

Recorded, **no architecture change made**:
- Router V2 correctly routed it **compound**;
- V2's `information_needs` were semantically useful and correct;
- the frozen decomposition analyzer produced **zero** subquestions;
- the graph fell back to simple retrieval;
- context coverage remained **2/3**.

Framework scores for it were uniformly 1.000 on DeepEval and mostly ERR on RAGAS — i.e. the frameworks did
**not** detect the known shortfall, which is further evidence against relying on them here.

## 22. Refusal-policy analysis

Three records have refusal / negative-verification references: `case-002`, `case-004`, `case-056`.
Per §15 the raw framework metric is reported separately from expected-behaviour correctness, and **no
framework score was rewritten**:

| case | raw `deepeval_contextual_recall` | deterministic refusal correctness |
|---|---|---|
| `case-002` | **0.000** | **correct** — answer says "No" and gives the real function (4/4 reference facts) |
| `case-056` | 1.000 | correct — answer declines the stale 07:00 value (2/2) |

`case-002` is exactly the edge case §15 anticipated: a correct refusal-style answer receiving 0.000 on a
generic recall metric. The raw score stands as reported; the deterministic verdict is recorded beside it.

## 23. Deterministic vs LLM metric comparison

The deterministic findings remain **first-class and unchanged** — framework metrics were additional evidence
that did not survive calibration:

| Deterministic finding | Status |
|---|---|
| Context reference-phrase coverage 24 → 37 (**+13**) | unchanged, first-class |
| Answer reference-phrase coverage 16 → 26 (**+10**) | unchanged, first-class |
| Zero answer-level regressions | unchanged |
| 18/18 execution success, 0 provider errors | unchanged |
| Final context = exactly 5 chunks, all 18 | unchanged |
| Context/citation invariant held, all 18 | unchanged |
| All 5 V2 false positives preserved quality | unchanged |

Where the two disagree, the deterministic measurement is the one with a verifiable basis: it checks whether
named reference facts are literally present. On `case-018` the deterministic method found the baseline
answer missing 3 of 4 reference facts while DeepEval gave it perfect faithfulness and perfect contextual
precision.

## 24. Limitations

1. **The full 36-record evaluation was not run**, so there are no framework baseline-vs-routed deltas.
2. **Calibration is 6 records** — enough to detect pathology, not to characterise it.
3. **A different evaluator might behave better.** These findings indict *this evaluator with these
   frameworks*, not RAGAS or DeepEval in general. Nemotron-30B's reasoning-style output is a poor fit for
   instructor-style structured extraction.
4. **The harness needed three rounds of fixes** (async client, schema extraction, token ceiling). More
   tuning might raise RAGAS's parse rate, but §12 directs stopping on pathological behaviour rather than
   iterating, and the two clearest pathologies (flat DeepEval faithfulness, inverted RAGAS relevancy) are
   evaluator-judgement failures, not parse failures.
5. **RAGAS required an isolated venv** with `langchain-community<0.4`; results are tied to that pin.
6. **Deterministic phrase coverage under-counts paraphrase** and word-form numbers, as previously recorded.
7. **No LangSmith feedback was attached** — see §26.

## 25. Recommendation

Options with evidence. Nothing actioned; no architecture change, no deployment.

1. **Do not use these framework scores as quality evidence for the routed architecture.** The deterministic
   results (+13 context, +10 answer, zero regressions) remain the defensible finding.
2. **If framework evaluation is still wanted, change the evaluator, not the harness.** The blocking issue is
   a reasoning-style model driving instructor-style structured extraction. A non-reasoning instruct model
   with reliable JSON would be the single highest-value change. NVIDIA 120B remains the natural candidate
   and remains unavailable.
3. **`deepeval_contextual_recall` is the one metric that tracked reality** (0.000 → 1.000 on case-018). If a
   narrow framework signal is wanted cheaply, that single metric is the candidate — at ~2 calls/record
   instead of 25.
4. **Treat refusal cases explicitly** in any future framework run: §22 shows a correct refusal scoring 0.000
   on generic recall.
5. **Not indicated:** averaging framework scores (explicitly forbidden and, at 0.38 mean disagreement,
   meaningless), running the full 36 on this evaluator, or changing the application architecture.

## 26. LangSmith

**No feedback attached.** Attaching framework scores to LangSmith examples would have required either
rerunning application inference to create matching runs, or creating new examples not tied to the persisted
artifacts. §19 directs skipping in that case and keeping local exports, so all results stay in the local
CSV/JSONL exports. Production tracing was not touched.

## 27. LangChain status

**NOT application usage.** RAGAS pulls `langchain`, `langchain-core`, `langchain-community` and
`langchain-openai` as its own internal dependencies, inside an isolated eval venv. No application or eval
module we wrote imports a LangChain API. Application LangChain integration remains **NOT IMPLEMENTED**.

## 28. Production status

**Unchanged. Nothing deployed.**

| Item | State |
|---|---|
| Deployed ask image | `d5af30e` — untouched |
| Production model | Groq `openai/gpt-oss-120b` — untouched |
| `MAX_LLM_CONTEXT_CHUNKS` | 5 — unchanged |
| Retrieval stack (Titan / BM25 / Qdrant / RRF / TOP_K / chunking / cache / tracing) | unchanged; **no call made** |
| Lambda requirements | unmodified — no ragas, deepeval, langgraph or langchain |
| Routed LangGraph v2 | **FROZEN**, offline only, not deployed |
| Router V2 / V3 / V4 / verifier / decomp_graph | frozen, sources unmodified |
| NVIDIA 120B judge | 1/6 scored, 5 pending — **not probed** |
| Provider usage this task | evaluator **151** calls (1 probe + 150 calibration) · Titan 0 · Qdrant 0 · Groq 0 · NVIDIA 120B 0 |
