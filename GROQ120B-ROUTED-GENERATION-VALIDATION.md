# Groq `openai/gpt-oss-120b` — Routed Generation Validation

**Experiment tag:** `groq120b-routed-generation-validation-v1`
**LangSmith project:** `multitenant-rag-dev-groq-observability-v1`
**Fingerprint:** `d98f753ce3029b4a`
**Date:** 2026-08-24
**Scope:** GENERATION ONLY on all 18 persisted routed LangGraph v2 final contexts.

---

## 1. Objective

The previous task measured Groq 120B generation on **6 of 18** routed cases. That was
enough to show the architecture is latency-feasible but **not** enough to claim the
production generation model produces acceptable answers on the routed evidence. This
task closes that gap: run the deployed production generation model on **all 18**
persisted routed final contexts and compare, deterministically, against the NVIDIA 20B
answers that were generated from the *same* contexts.

**This is a generation-quality and provider-behaviour validation, not a RAG evaluation.**
Retrieval is frozen and replayed; nothing about retrieval is being tested here.

---

## 2. What was NOT re-run

Zero calls were made to any of the following. All were replayed from persisted artifacts:

| Component | Calls this task |
|---|---|
| Router V2 | 0 |
| Decomposition (20B) | 0 |
| LangGraph (any node) | 0 |
| Titan embeddings | 0 |
| BM25 / fastembed | 0 |
| Qdrant `query_points` | 0 |
| NVIDIA NIM (any model) | 0 |
| RAGAS / DeepEval | 0 |
| AWS mutations | 0 |

The only network calls were 18 Groq generation requests and the LangSmith trace
writes/reads.

---

## 3. The model under test is the deployed production model

Read-only check of the live function config:

```
GROQ_MODEL        = openai/gpt-oss-120b
GROQ_MODEL_SMALL  = openai/gpt-oss-20b
MAX_LLM_CONTEXT_CHUNKS = 5
```

The model validated here is **byte-identical in name to the one production serves**.
This is not a proxy or a stand-in.

---

## 4. Input integrity — `context_sha256` verified

Each generation input was hashed and compared against the persisted routed artifact
(`output/routed_live_v2_cases.jsonl`):

```
context_sha256 verified: 18/18 match the persisted routed contexts
distinct sha256 prefixes in LangSmith export: 18/18
```

All 18 evidence sets are **byte-identical** to what the live routed run assembled.
Every case carried exactly 5 context chunks (the `MAX_LLM_CONTEXT_CHUNKS` cap), so the
capped-context condition is the one under test.

---

## 5. Honest disclosure — the prompt scaffolding is RECONSTRUCTED

This is the most important methodological caveat in this report and it is stated
before any result.

The persisted artifact stores contexts in the **harness** form:

```python
# nvidia_harness.py:110
out.append(f"[{p.get('title','')}] {p.get('chunk_text','')}")
```

What the live run actually sent the model was the **graph** form:

```python
# decomp_graph.py:251-260
label = f"[for sub-question {si+1}] " if isinstance(si, int) and subs else ""
out.append(f"{label}[Source: {p.get('title','')}]\n{p.get('chunk_text','')}")
# joined by "\n\n---\n\n"
```

The live run built its prompt from **live Qdrant point objects**, which no longer exist.
So the prompt had to be rebuilt by parsing `title` and `chunk_text` back out of each
persisted string (`^\[(.*?)\]\s(.*)$`) and re-emitting the `_blocks` form, using the
persisted `merged_context_map` for the sub-question labels.

**What this means precisely:**

- The **evidence text is byte-identical** — verified by sha256 (§4).
- The **surrounding scaffolding is reconstructed**, not replayed. It reproduces the
  `_blocks` shape, but it is a rebuild.

A side effect visible in the data: titles appear doubled —
`"[Copper Orchard's Smallest Robot] [Copper Orchard's Smallest Robot]\n…"` — because the
chunker already prepends `[header_path]` and `_contexts_from` prepended `[title]` again.
That doubling was present in the persisted artifact and is faithfully preserved rather
than cleaned up, because cleaning it would change the input.

---

## 6. Second disclosure — 3 of 18 cases used a substituted system prompt

15 of 18 cases took the compound path and were re-run with `GEN_SYS_COMPOUND`, the exact
system prompt the live run used.

3 cases (`case-041`, `case-056`, `case-059`) took the **decomposition-fallback simple
path** (`normal_answer`). Their live system prompt came from
`app._build_system_prompt` / `_build_group_system_prompt`, which require a **DynamoDB
tenant lookup** and live point objects to render writer display names. The persisted
artifact does not carry tenant identity, so that prompt **cannot be faithfully
reconstructed**.

**Decision taken and its consequence:** those 3 cases were run with `GEN_SYS_COMPOUND`
(with no sub-questions, so it degrades to question + evidence). This keeps the yardstick
uniform across all 18, but it means **3 of 18 cases used a system prompt that differs
from the live run**. Their results are still comparable to each other and to NVIDIA
(both models are compared on the same reconstruction), but they are **not** a replay of
the live prompt. They are flagged as `decomposition_fallback=true` in every CSV.

---

## 7. Execution — bounds respected

| Bound | Limit | Actual |
|---|---|---|
| Logical generation calls | 18 | **18** |
| Physical HTTP requests | 22 | **18** |
| Concurrency | 1 | 1 |
| Max attempts per logical call | 2 | 1 (no retries needed) |

```
logical_calls=18  physical_requests=18  successes=18
http_429=0  http_5xx=0  timeouts=0  retries=0  backoff_seconds=0.0
```

Every one of the 18 logical calls succeeded on its **first** physical attempt. The
4-request headroom between the logical and physical ceiling was never touched.

---

## 8. Token-aware pacing — recorded SEPARATELY from inference latency

Groq's free plan reports `x-ratelimit-limit-tokens: 8000` (per minute). Mean cost per
generation call was **2,401 tokens**, so the sustainable rate is:

```
8000 / 2401 = 3.33 calls per minute
```

TPM — not RPM — is the binding constraint. RPM ended at `982/1000` remaining; the token
window is what forced waiting.

The runner consulted the returned `x-ratelimit-remaining-tokens` /
`x-ratelimit-reset-tokens` before each call and slept only when headroom was
insufficient:

| Metric | Value |
|---|---|
| Cases requiring a deliberate wait | 6 / 18 |
| Mean wait when waiting | 50.9 s |
| Total deliberate pacing | 305.6 s |
| Total provider inference time | 26.6 s |
| Total wall clock | 332.2 s |
| **Inference share of wall clock** | **8.0 %** |

**92 % of the wall-clock time of this experiment was free-tier rate-limit waiting, not
model work.** Pacing is recorded in its own field (`deliberate_pacing_s`) on both the
CSV rows and the LangSmith root spans and is **never** added to
`provider_latency_ms`. Any latency number in this report is inference only.

---

## 9. Generation latency — inference only

| Statistic | Groq 120B (ms) |
|---|---|
| n | 18 |
| min | 892.4 |
| median | 1,432.3 |
| mean | 1,475.7 |
| p95 | 1,891.0 |
| max | 2,613.7 |
| stdev | 379.9 |

Per routed path:

| Path | n | mean latency | mean input tok | mean output tok |
|---|---|---|---|---|
| compound | 15 | 1,418 ms | 1,958 | 450 |
| simple (fallback) | 3 | 1,765 ms | 1,809 | 557 |

Tight distribution — max is under 3 s, stdev under 400 ms.

---

## 10. Latency vs the NVIDIA 20B routed run

The NVIDIA figures are the **generation-node latency** from the persisted live routed
run (`final_answer_ms` on the compound path, `normal_answer_ms` on the fallback path).

| Statistic | NVIDIA 20B gen node | Groq 120B gen | Ratio |
|---|---|---|---|
| mean | 7,592 ms | 1,476 ms | **5.14×** |
| median | 4,784 ms | 1,432 ms | **3.34×** |
| min | 2,075 ms | 892 ms | 2.33× |
| max | 30,524 ms | 2,614 ms | **11.7×** |

The `max` column is the operationally decisive one. NVIDIA's worst generation took
**30,524 ms**, which exceeds the API Gateway `TimeoutInMillis = 30000` on its own —
before routing, decomposition, embedding, or retrieval. Groq's worst was 2,614 ms.

Note this is a **larger** model (120B vs 20B) that is also faster here, and it produced
comparable output length (mean 468 vs 426 output tokens) — so the speed is not bought
by generating less.

---

## 11. Quality method 1 — frozen reference-phrase set

The frozen phrase set (`output/decomp_phrases.json`) built and audited during the
decomposition experiment was reused **unchanged**. No phrases were added, so Groq is
measured on exactly the yardstick NVIDIA was measured on.

| case | ref phrases | NVIDIA 20B | Groq 120B | delta |
|---|---|---|---|---|
| case-018 | 6 | 6 | 6 | 0 |
| case-020 | 7 | 7 | 7 | 0 |
| case-022 | 6 | 5 | 5 | 0 |
| **total** | **19** | **18** | **18** | **0** |

**Identical.** But this set only covers **3 of 18** cases, which is too thin to support
a verdict — hence method 2.

---

## 12. Quality method 2 — deterministic fact-atom comparison, all 18

To cover all 18 cases with one mechanical rule, *fact atoms* are extracted from the
reference `expected_answer` (numerics with units, capitalised identifiers such as
`QL-2D` / `MS-E1`, and salient content words after stop-word removal) and tested for
literal presence in each model's answer.

The extractor never sees a model output when deciding what the atoms are — it reads only
the reference — and it is applied **identically** to both answers. Any difference in the
resulting counts is therefore attributable to the answers, not the yardstick.

| Metric | NVIDIA 20B | Groq 120B |
|---|---|---|
| Fact atoms present | 136 / 213 | **139 / 213** |
| Coverage rate | 0.638 | **0.653** |
| Per-case higher | 4 | 5 |
| Per-case equal | — | 9 |
| Refusal markers emitted | 0 | 0 |

Per-case deltas (`groq − nvidia`): `+2` on case-023 and case-059; `+1` on case-003,
case-022, case-047; `−1` on case-002, case-004, case-009, case-020; `0` on the other 9.

---

## 13. Verdict on quality — equivalent, not better

A 1.5-percentage-point difference (0.653 vs 0.638) across 213 atoms, with the per-case
record at 5 wins / 4 losses / 9 ties, is **not** evidence of a quality improvement.

**The correct reading: on identical routed evidence, Groq 120B is quality-equivalent to
NVIDIA 20B while being 3.3–5.1× faster.** That is the finding. Claiming Groq is "better"
would over-read the noise, and claiming it is "worse" would too.

Neither model emitted a refusal or insufficient-evidence marker on any of the 18 cases.

---

## 14. Scorer defect found and fixed during this task

The all-18 comparison initially scored **case-030 at 0/2 atoms for both models**. Both
answers were then read in full and both are **completely correct** (18, 18, 16 seeds per
tray). The corpus spells small integers out — "eighteen", "sixteen" — while the reference
answer uses digits, so a literal matcher missed every atom.

A digit↔word equivalence rule for bare small integers was added and applied identically
to both models. case-030 then scored 2/2 for both. Aggregate effect: NVIDIA 133→136,
Groq 135→139.

This is the same class of defect as the earlier U+2011 non-breaking-hyphen miss in
`WG-03`. Both are now covered by the normalisation in `score_groq120b_gen.py::norm`
(NFKC + dash unification + quote unification) and `atom_present` (digit↔word). **Any
deterministic string-match scorer over prose should be assumed to have more defects of
this kind until each zero-score is manually read.**

---

## 15. V2 false-positive routes — generation quality on over-decomposed questions

Five cases are adjudicated `simple` but Router V2 emitted
`multiple_independent_retrieval_needs`. The high-recall V2 acceptance policy knowingly
trades specificity for recall, so these are expected — the question is whether the
*answer* degrades when a simple question is decomposed anyway.

| case | routed path | atom delta | assessment |
|---|---|---|---|
| case-002 | compound | −1 | Correct ("No" + what Pip-6 actually does). Sectioned into 2 headed parts; slightly verbose for a simple question. |
| case-003 | compound | +1 | Correct — current 04:30–06:30, originally 04:30–07:00, with the reason. Decomposition did not hurt. |
| case-004 | compound | −1 | Correct on the real blocker (enclosure 11 cm taller, blocks manual brake-release lever). |
| case-056 | simple (fallback) | 0 | Correct — refuses the stale 07:00 and gives 06:30 with supersession reasoning. |
| case-059 | simple (fallback) | +2 | Correct and the largest single Groq gain (+2 atoms, 872 output tokens). |

**Conclusion: unnecessary decomposition costs verbosity and structure (numbered
sections), not correctness.** No false-positive route produced a wrong answer. This
supports keeping the high-recall V2 policy: its failure mode is a longer answer, not a
worse one.

---

## 16. Decomposition-fallback cases

3 cases had `decomposition_unusable=true` and fell back to the simple path:

- **case-056** (gt simple) — correct, no penalty from the fallback.
- **case-059** (gt simple) — correct, best Groq delta in the set.
- **case-041** (gt **compound**) — the one substantive content gap. Expected answer
  requires two distinct facts: library = Founders' Week ceremonial ribbon on the bell
  clapper; orchard = ribbons confused Pip-6, replaced by white triangle tags. Groq got
  the orchard half (white triangle tags) but the library half is vague — "authorizes the
  use of a blue ribbon for certain procedures" — with no mention of Founders' Week or the
  bell clapper. Atom coverage 6/13, identical to NVIDIA's 6/13.

**case-041 fails identically under both models on the same context.** That is
consistent with the previously logged retrieval-locality issue for case-041 and confirms
it is a **retrieval/decomposition problem, not a generation problem** — a stronger model
on the same evidence does not fix it. case-041 remains LOW PRIORITY / BACKLOG as
previously classified.

---

## 17. LangSmith observability — read-back verified

Traces were written to `multitenant-rag-dev-groq-observability-v1` with hierarchy
`generation_validation_request` → `generation`, then **read back through the LangSmith
SDK** to prove server-side queryability rather than local emission.

```
read-back: total runs matched=36  roots=18  generation spans=18
trace completeness: 18/18 roots have a generation child AND status=success
errors: none
distinct span names: ['generation', 'generation_validation_request']
cases in export: 18
```

Server-recorded root duration agrees with locally measured provider latency to a
**mean of 18.1 ms** (max 264.3 ms), so the traces are not merely present but
numerically consistent with local measurement.

Two read-back defects were hit and fixed:

1. `limit=600` → `400 Bad Request: Limit exceeds maximum allowed value of 100`. Fixed by
   letting the generator paginate internally instead of passing an oversized cap.
2. Filtering the whole project list on `experiment` metadata returned **18 roots and 0
   child spans**, because child spans inherit trace context but **not** metadata. A
   completeness check built that way would have reported a fully broken hierarchy on a
   perfectly good trace tree. Fixed by fetching children via `parent_run_id`.

Server-side metadata filters (`eq(metadata.fingerprint, …)`) were rejected with
`Attribute metadata.fingerprint not accepted`, so root selection is client-side over
paginated results.

---

## 18. Artifacts

| File | Records | Contents |
|---|---|---|
| `groq120b-routed-generation-full.csv` | 18 | Full per-case results, both models' answers, tokens, latency, sha256 |
| `groq120b-vs-nvidia20b-quality.csv` | 18 | Frozen-phrase coverage comparison |
| `groq120b-deterministic-fact-comparison.csv` | 18 | All-18 fact-atom comparison + refusal flags |
| `groq120b-generation-langsmith-export.csv` | 18 | Server-side root spans read back via SDK |
| `groq120b-generation-span-metrics.csv` | 36 | Every span (18 roots + 18 generation children) |

Code: `multitenant-rag/evals/run_groq120b_generation.py`,
`score_groq120b_gen.py`, `readback_groq120b_gen.py`.
Raw: `output/groq120b_gen_validation.jsonl`, `output/groq120b_gen_stats.json`,
`output/groq120b_gen_scoring.json`, `output/groq120b_readback.json`.

---

## 19. Production state — UNCHANGED

Read-only verification, no mutations:

```
multitenant-ask  LastModified = 2026-08-22T18:27:50.000+0000   (unchanged)
                 Timeout = 60s   Memory = 2048MB
                 Image   = d5af30e7f4cc679b2625d6a623d4a7857b1f8094  (unchanged)
                 MAX_LLM_CONTEXT_CHUNKS = 5
                 GROQ_MODEL = openai/gpt-oss-120b
```

No AWS mutation, no deployment, no config change. This was an offline validation of the
deployed model against frozen evidence.

---

## 20. What this does and does not establish

**Established:**

1. The deployed production generation model produces correct answers on **all 18**
   routed contexts — closing the 6/18 gap from the previous task.
2. Quality is **equivalent** to NVIDIA 20B on identical evidence (0.653 vs 0.638 atom
   coverage; 5W/4L/9T), so switching to the fast production provider costs nothing
   measurable in answer quality.
3. Generation latency is 892–2,614 ms — comfortably inside the 30,000 ms API Gateway
   budget, where NVIDIA's worst case (30,524 ms) alone exceeded it.
4. Router V2 false positives cost **verbosity, not correctness**, which supports keeping
   the high-recall policy.
5. case-041 fails identically under both models — it is a retrieval defect, not a
   generation defect.
6. The free-tier binding constraint is **TPM (8,000/min ⇒ 3.33 calls/min)**, not RPM.

**NOT established:**

1. **This is not a RAG quality evaluation.** Retrieval was replayed, not tested. Atom
   coverage of 0.653 is a *reference-phrase presence* metric, not faithfulness,
   correctness, or answer relevancy.
2. **3 of 18 cases used a substituted system prompt** (§6) — their numbers are not a
   replay of the live prompt.
3. **The prompt scaffolding is reconstructed** for all 18 (§5); only the evidence text
   is byte-verified.
4. **n=18 with 213 atoms cannot resolve a 1.5-point difference.** The equivalence claim
   is defensible; a ranking claim is not.
5. **Single run, temperature 0, no repeats** — no variance estimate for either model.
6. **The frozen phrase set covers only 3 of 18 cases**; the all-18 method is a
   string-matching proxy with at least two now-fixed defect classes (§14) and probably
   more.
7. **No concurrency was tested.** All 18 ran at concurrency 1. Production behaviour
   under parallel load is unmeasured.
8. The **NVIDIA observability gap remains DEFERRED** — NVIDIA was not re-run and no
   NVIDIA spans were emitted.
