# LangGraph Compound-Question Decomposition Experiment

Offline research experiment. **Not deployed.** Production RAG is untouched.

---

## 1. Experiment identity

| Field | Value |
|---|---|
| Experiment ID | `rag-agentic-decomposition-nvidia20b-v1` |
| Fingerprint hash | `c56b24a3a1002366` |
| Baseline compared against | `rag-model-eval-nvidia20b-top5-v1` (not regenerated) |
| Cases | 6 (3 compound multi-part + 3 non-compound controls) |
| Date | 2026-08-21 → 2026-08-23 |
| Repo HEAD at run | `aa87814` |
| Deployed production ask image | `d5af30e` (unchanged) |
| Groq calls | **0** (guard installed and asserted) |
| Experiment status | **FROZEN** (architect decision, 2026-08-23) — graph, retrieval, merge, models locked |
| Judge completion | **1 of 6** — NVIDIA 120B endpoint outage, see §10 / §13 |

**Hypothesis under test:** the 3 compound multi-part cases scored correctness 0.000 / completeness 0.000
in the deterministic top-5 baseline. Do they fail because a *single* retrieval query cannot serve two
independent information needs at once — i.e. does query decomposition fix them?

---

## 2. LangGraph version

| Package | Version | Role |
|---|---|---|
| `langgraph` | **1.2.11** | eval-only dependency |
| `langchain-core` | 1.6.0 | **transitive dependency of langgraph only** — not used as an application abstraction |

Neither package is installed into any Lambda image. They exist only in the local eval virtualenv.

---

## 3. Graph topology

```
            START
              │
      ┌───────▼────────┐
      │ analyze_question│  NVIDIA 20B, JSON-only classifier
      └───────┬────────┘
              │  conditional edge: route_question()
      ┌───────┴─────────────────┐
   "simple"                 "compound"
      │                          │
┌─────▼────────┐         ┌───────▼──────┐
│normal_retrieve│         │  decompose   │
└─────┬────────┘         └───────┬──────┘
      │                          │  Send() fan-out (conditional edge)
┌─────▼──────┐          ┌────────▼─────────┐
│normal_answer│          │ retrieve_branch  │ × N   (parallel, semaphore=2)
└─────┬──────┘          └────────┬─────────┘
      │                          │  fan-in barrier: defer=True
      │                  ┌───────▼──────┐
      │                  │merge_evidence│  coverage-aware, cap 5
      │                  └───────┬──────┘
      │                  ┌───────▼──────┐
      │                  │ final_answer │  NVIDIA 20B
      │                  └───────┬──────┘
      └──────────┬───────────────┘
                 ▼
                END
```

7 nodes, 2 conditional edges, 1 dynamic fan-out, 1 deferred fan-in barrier, explicit `END` on both arms.

The `simple` arm is deliberately a faithful re-implementation of production behaviour (one hybrid query,
top-5, one generation) so that routing can be measured against a like-for-like path.

---

## 4. State schema

```python
class DecompState(TypedDict, total=False):
    case_id: str
    original_question: str
    route: str                       # single | multi | group
    target: str | None               # profile name(s) / group name — MUST be declared:
                                     # LangGraph strips keys absent from the state schema,
                                     # which would silently empty the tenant scope.
    scope_tenant_ids: list[str]      # resolved allowed tenants — never widened
    scope_single_tenant: str | None
    is_compound: bool
    subquestions: list[str]
    branch_results: Annotated[list[dict], operator.add]   # fan-in reducer
    merged_context: list[Any]
    merged_context_map: list[dict]
    ...
```

`branch_results` is the only reducer channel: `Annotated[list[dict], operator.add]` lets N parallel
branches append concurrently without a lock.

**Bug found and fixed during this experiment (documented because it is the most valuable finding for
anyone building on LangGraph):** `target` was initially *not* declared in `DecompState`. LangGraph
silently strips any input key absent from the state schema, so `target` arrived as `None`, tenant scope
resolved to an empty list, and every branch retrieved 0 candidates while the graph reported success.
Failure mode: silent, not an exception. Two regression tests now cover it. The invalid records were
quarantined to `output/decomp_cases.INVALID_scope_bug.jsonl` and never reused.

---

## 5. Conditional routing

`analyze_question` calls NVIDIA 20B with a JSON-only contract and no access to the expected answer, the
dataset label, or the case's scenario category:

```
{"is_compound": true, "subquestions": ["...", "..."]}  |  {"is_compound": false, "subquestions": []}
```

Rules given to the analyzer: mark compound only for 2–3 genuinely separable information needs; produce
atomic subquestions; preserve names/identifiers/time constraints exactly; do not add facts; together they
must cover the whole original question.

**No manual routing.** No case was forced onto either arm. The analyzer's own classification was used and
recorded verbatim, including where it was wrong (§14).

---

## 6. Decomposition behavior

All 6 cases produced 2 subquestions each, syntactically clean and entity-preserving:

| Case | Subquestions produced |
|---|---|
| case-018 | "What is the highest Meral Index recorded in the network?" / "What temperature rule applies to cloud honey in Ember Tea?" |
| case-020 | "What evidence made Zone North probable?" / "What was later found inside T41?" |
| case-022 | "What was the highest Meral Index?" / "Why did Hollow Stair remain unverified after Zone South?" |
| case-002 | "Does Pip-6 actually pollinate the flowers?" / "What does Pip-6 really do?" |
| case-004 | "Why wasn't QL-2D approved for passenger service on the Lantern Loop?" / "Was charging wait time the problem?" |
| case-001 | "What time does the Morrow Bell ring?" / "What was the original purpose of ringing the Morrow Bell?" |

Decomposition *quality* was good in all 6 cases — including the 3 where decomposing was unnecessary.
There were **zero** cases of category B (bad/incorrect subquestions). Identifiers (`QL-2D`, `T41`,
`Pip-6`, `WG-03`) and scope words ("in the network", "on the Lantern Loop") survived intact.

---

## 7. Parallel fan-out / fan-in

- Fan-out via `add_conditional_edges("decompose", fan_out, ["retrieve_branch"])` returning `Send` objects,
  one per subquestion. Branch count is therefore data-driven, not hardcoded.
- Concurrency bounded by `threading.Semaphore(2)` — deliberate: retrieval hits the shared free-tier
  Qdrant cluster and Bedrock Titan embeddings, and unbounded fan-out would risk throttling.
- Fan-in via `add_node("merge_evidence", merge_evidence, defer=True)`. `defer=True` is what makes the node
  wait for *all* inbound branches instead of firing once per branch — the single most important detail in
  the graph.
- Each branch resolves its own scope from `scope_tenant_ids` / `scope_single_tenant`. Scope is **never
  widened** by decomposition: a branch cannot retrieve from a profile the original question could not see.
- Every branch used the **existing production hybrid retrieval** (Titan dense 1024-d + fastembed BM25
  sparse → Qdrant native RRF), `TOP_K=5`, `RETRIEVAL_FLOOR=0.15`. No new retrieval code.

`merge_evidence` latency was ≤ 0.1 ms in all 6 cases — merging is free; the cost is entirely in the two
LLM calls.

---

## 8. Evidence merge policy

Coverage-aware, deliberately **not** score-ranked:

1. **Pass 1 — coverage guarantee:** take the single best eligible chunk from *each* branch.
2. **Pass 2 — round-robin:** walk deeper into each branch's pool, one chunk at a time.
3. Cap at `MAX_LLM_CONTEXT_CHUNKS = 5` (identical to production).
4. Dedupe on `(post_id, chunk_text[:80])`.

Rationale: RRF scores from *different* queries are not comparable, so a global re-sort would let a
high-scoring branch monopolise all 5 slots and starve the other information need — which is precisely the
baseline failure being investigated. The policy never compares scores across branches.

Result: every case allocated slots to both branches. `merged_context_map` shows the interleave, e.g.
case-018 → `[b0r0, b1r0, b0r1, b1r1, b0r2]`. **`branch_evidence_missing = False` for all 6 cases** — no
branch came back empty, so **zero** category D (merge-drop) failures.

Final context was exactly 5 chunks in all 6 cases; estimated context tokens 1,670–2,432.

---

## 9. Models

| Role | Model | Provider | Calls per case |
|---|---|---|---|
| Question analyzer | `openai/gpt-oss-20b` | NVIDIA NIM | 1 |
| Final synthesis | `openai/gpt-oss-20b` | NVIDIA NIM | 1 |
| Judge | `openai/gpt-oss-120b` | NVIDIA NIM | 1 (per §23 of the spec) |
| Embeddings | Amazon Titan Text V2 (1024-d) | AWS Bedrock | reused, unchanged |
| Sparse | fastembed BM25 | local | reused, unchanged |

Groq — the production provider — was **never called**: `install_groq_guard()` replaces
`prod.stream_answer`, `llm.stream_answer` and `llm._post_with_retry` with functions that raise.

Application model held at 20B **on purpose**: this experiment isolates the retrieval/orchestration
variable. Changing model size and topology together would confound the result.

---

## 10. Provider usage

| Stage | NVIDIA requests | Successes | Timeouts | 429s | Groq |
|---|---|---|---|---|---|
| Graph (6 cases × analyzer + generator) | 12 | 12 | 0 | 0 | 0 |
| Judge (120B) | 12 | **1** | **11** | 0 | 0 |

The graph stage was clean. The judge stage hit a **NVIDIA 120B endpoint outage** (§20) — 11 of 12
requests timed out.

Two availability probes have been run, each a single bounded request, never a retry loop:

| Probe | When | Prompt | Result |
|---|---|---|---|
| 1 | 2026-08-23 (initial) | 8 tokens | no response within 75 s |
| 2 | 2026-08-23 (architect-authorised, `MAX_ATTEMPTS=1`) | 8 tokens | **timeout at 60.3 s** — 1 request, 1 timeout, 0 successes |

The endpoint, not the harness, is at fault: the same client, key and code path served 12/12 NVIDIA 20B
requests without error. Per the standing cost rule the run stays checkpointed — **not** switched to Groq,
**not** escalated to a paid tier, and **not** re-probed in a loop.

---

## 11. Three compound cases

### case-018 — multi-profile (`Tara Moss, Kian Rao`)

> "What is the highest Meral Index recorded in the network, and what temperature rule applies to cloud honey in Ember Tea?"
> Expected: *11.4 at WG-03 North Fen on 16 January; honey below 70 °C.*

| Question | Answer |
|---|---|
| Analyzer detected compound? | **YES** |
| Subquestions | 2, both atomic and correct |
| Did each branch retrieve evidence? | YES — 10 candidates each |
| Evidence for each expected fact found? | **YES — 6/6 key phrases in final context** (was 1/6) |
| Necessary evidence absent from original top-10? | **YES — absent from ALL 10 single-query candidates** |
| Final merged chunk count | 5 (map `[b0r0, b1r0, b0r1, b1r1, b0r2]`) |

Baseline answer: *"No one in this selection has written about the highest Meral Index…"* — a confident
false negative. Baseline judge: *"Answer omits 11.4 Meral Index and gives incorrect claim of no data."*

Decomposition answer: *"…the highest Meral Index recorded in the network is **11.4**, observed by station
**WG-03** at **North Fen** on **16 January**… cloud honey must be added **below 70 degrees**."*
All 6 reference phrases present. **This is the cleanest confirmation of the hypothesis in the experiment.**

### case-020 — multi-profile (`Nila Roy, Kian Rao`)

> "What evidence made Zone North probable, and what was later found inside T41?"

| Question | Answer |
|---|---|
| Analyzer detected compound? | **YES** |
| Subquestions | 2, both atomic and correct |
| Did each branch retrieve evidence? | YES — 10 candidates each |
| Evidence for each expected fact found? | **YES — 7/7 key phrases in final context** (was 4/7) |
| Necessary evidence absent from original top-10? | **NO — it sat at ranks 6–10 of the single query** |
| Final merged chunk count | 5 |

The baseline answered part 2 well (compass ring, waterproof cloth, `V-31`) but omitted part 1's evidence.
Baseline judge: *"Answer omits lantern brackets and mineral lines, contradicting reference."*
The decomposition answer supplies exactly that: *"a second and third rusted lantern bracket became
visible, and the third bracket was found beside the west rock face with mineral lines."*

**Important caveat, stated because it weakens the case for decomposition specifically:** this evidence was
present in the original query's ranks 6–10. A cheaper intervention — raising top-K or improving
allocation — would plausibly also have fixed case-020. Decomposition is *sufficient* here, not *necessary*.

### case-022 — group (`Field Circle`)

> "What was the highest Meral Index, and why did Hollow Stair remain unverified after Zone South?"

| Question | Answer |
|---|---|
| Analyzer detected compound? | **YES** |
| Subquestions | 2, both atomic and correct |
| Did each branch retrieve evidence? | YES — 10 candidates each |
| Evidence for each expected fact found? | **PARTIAL — 5/6 key phrases in final context** (was 3/6) |
| Necessary evidence absent from original top-10? | **YES for the Meral Index facts** — absent from all 10 |
| Final merged chunk count | 5 |

Part 1 fixed: baseline said *"The excerpts don't mention a Meral Index at all"*; decomposition retrieved
and answered **11.4 at WG-03**. Residual gap: the "neither clue conclusive" nuance for part 2 was never
retrieved by *either* arm, and "Zone North" dropped out of the final answer that the baseline had included
— a mild completeness trade on part 2 against a large correctness gain on part 1.

---

## 12. Three controls

Selection rule, **declared before running** and persisted to `output/decomp_selection.json`: one case per
predetermined category, choosing the **lowest stable `case_id`** in each — never chosen by baseline score.

| Case | Category | Route | Runtime classification | Human expectation | Over-decomposed? |
|---|---|---|---|---|---|
| case-002 | single-profile factual | single | **compound** | simple | **YES** |
| case-004 | non-compound multi/group | multi | **compound** | simple | **YES** |
| case-001 | negative / exact-fact | single | **compound** | simple | **YES** |

**All three controls were misrouted onto the compound path.** This is the experiment's main negative
result and it is a routing-*precision* failure, not a quality failure — quality held everywhere it could
be measured:

- **case-004** — the one case the judge scored before the outage: **1.000 / 1.000 / 1.000, identical to
  baseline.** Zero regression despite unnecessary decomposition.
- **case-001** — both reference facts preserved (`16:20`, final manuscript courier crossing the northern lock).
- **case-002** — answer correct: *"No"* + the correct actual function (flags blossoms unvisited by orchard
  moths for more than two days).

Honest reading of *why* the analyzer over-triggers: all three controls are **two-clause questions**
("Does X…? What does it really do?", "Why wasn't X approved? Was Y the problem?", "What time…, and what
was the original purpose…?"). They have two *surface* clauses but one underlying information need. The
analyzer keys on surface structure. That is a fixable prompt/threshold problem, not evidence that routing
cannot work — but it is unfixed today.

**Analyzer overhead on ordinary questions:** +1 LLM call, mean **2,603.8 ms** and ~462 tokens
(231.7 in / 229.7 out) per question, plus a second retrieval round-trip. On a question that did not need
it, that is pure latency cost.

---

## 13. Before / after quality

**The LLM-judged before/after table is incomplete — 1 of 6 cases scored.** The 120B judge outage (§10,
§20) blocked the other 5. Reported honestly rather than filled in.

**Status as of 2026-08-23:** still incomplete. A second single bounded probe timed out at 60.3 s (§10), so
per the standing rule the judge stage was **not** resumed and no further probing was done. The five pending
scores remain pending — they are not estimated, inferred, or substituted with the proxy below.

| Case | Type | Correctness | Completeness | Groundedness | Judge status |
|---|---|---|---|---|---|
| case-018 | compound | 0.000 → *pending* | 0.000 → *pending* | 0.500 → *pending* | provider outage |
| case-020 | compound | 0.000 → *pending* | 0.000 → *pending* | 0.000 → *pending* | provider outage |
| case-022 | compound | 0.000 → *pending* | 0.000 → *pending* | 1.000 → *pending* | provider outage |
| case-002 | control | 1.000 → *pending* | 1.000 → *pending* | 1.000 → *pending* | provider outage |
| **case-004** | control | **1.000 → 1.000** | **1.000 → 1.000** | **1.000 → 1.000** | **scored** |
| case-001 | control | 1.000 → *pending* | 1.000 → *pending* | 1.000 → *pending* | provider outage |

### Interim deterministic proxy (NOT a judge score)

To avoid reporting nothing, a deterministic reference-phrase coverage metric was computed — string
containment of the reference facts, Unicode-dash-normalised, in the retrieved context and in the answer:

| Case | Type | Phrases in CONTEXT (before → after) | Phrases in ANSWER (before → after) |
|---|---|---|---|
| case-018 | compound | **1/6 → 6/6** | **2/6 → 6/6** |
| case-020 | compound | **4/7 → 7/7** | **4/7 → 7/7** |
| case-022 | compound | **3/6 → 5/6** | **3/6 → 4/6** |

**Two stated limitations of this proxy, both discovered while running it:**

1. It under-counts numbers written as words. case-004 scores 1/3 on it, yet the LLM judge scored that same
   answer 1.000/1.000/1.000 — the answer says "eleven centimeters" and "twelve-minute", not "11"/"12".
   The proxy is a coarse floor, not a score.
2. An earlier version of this metric produced a false negative on `WG-03` because the 20B output uses
   U+2011 NON-BREAKING HYPHEN. Normalisation was added; the corrected case-018 answer coverage is 6/6,
   not the 5/6 an unnormalised metric reports.

Direction of travel is nonetheless unambiguous on the three compound cases, and it aligns exactly with
the reasons the baseline judge gave for failing them.

---

## 14. Analyzer accuracy on the six cases

| | Predicted compound | Predicted simple |
|---|---|---|
| **Actually compound** (3) | 3 | 0 |
| **Actually simple** (3) | 3 | 0 |

- Recall on compound questions: **3/3 = 100%**
- Precision: **3/6 = 50%**
- Overall routing accuracy: **3/6 = 50%**
- Category B (bad subquestions): **0/6**

The analyzer never missed a genuine compound question and never produced a malformed decomposition, but it
classified **everything** as compound. On this 6-case sample it has not been shown to discriminate — a
constant "always compound" classifier would score identically. **This is the single most important
limitation of the experiment** and it is a direct consequence of the 6-case sample: with no true-negative
ever produced, the routing arm is untested.

### Verdict

- true compound detected: **3/3**
- simple controls incorrectly classified compound: **3/3**
- precision: **50%**
- routing accuracy: **50%**
- simple-branch executions: **0**

> ## CURRENT ROUTER NOT PRODUCTION READY.

Stated without qualification. A router that decomposes every question imposes the full agentic cost
(+1 LLM call, +(N−1) retrievals, added latency and tokens) on traffic that does not need it, while
providing no measurable routing benefit on this sample.

---

## 15. Retrieval evidence findings

The strongest finding, from checking whether the missing evidence merely sat deeper in the *same* ranked
list (positions 6–10, captured in the baseline's runtime signals) or was absent from it entirely:

| Case | Evidence in baseline top-5 | Evidence at ranks 6–10 | Found by decomposed retrieval | Interpretation |
|---|---|---|---|---|
| case-018 | NO | **NO** | **YES** | Only decomposition finds it. A top-K raise would **not** have fixed this. |
| case-020 | NO | **YES** | YES | Decomposition sufficient but not necessary — a top-K raise would likely also fix it. |
| case-022 | NO | **NO** | YES (part 1) | Only decomposition finds it. A top-K raise would **not** have fixed this. |

**In 2 of 3 compound cases the evidence was unreachable by the single query at any depth of the top-10.**
A single embedding of a two-need question lands between the two topics and retrieves neither well. This is
a genuine retrieval-formulation limit, not a ranking-depth limit — and it is the substantive justification
for decomposition surviving this experiment.

---

## 16. Latency / token overhead

| Metric | Mean | Min | Max |
|---|---|---|---|
| `analyze_question` | 2,603.8 ms | 1,278.6 | 3,904.2 |
| `decompose` | 0.0 ms | — | — |
| `merge_evidence` | 0.1 ms | — | 0.1 |
| `final_answer` | 5,250.4 ms | 2,118.6 | 9,343.4 |
| **Graph total** | **11,498.3 ms** | 8,273.8 | 15,505.3 |

| Token metric | Mean per case |
|---|---|
| Analyzer in / out | 231.7 / 229.7 |
| Generator in / out | 1,911.8 / 412.8 |
| Final context (estimated) | 1,670–2,432 |

Overhead of the agentic path vs the deterministic baseline: **+1 LLM call (~2.6 s, ~462 tokens)** and
**+N−1 retrieval round-trips**, at 2-way branch concurrency. Orchestration itself (`decompose`, fan-out,
`merge_evidence`) is effectively free — the entire cost is the extra analyzer call and the extra retrievals.
Final context size is unchanged at 5 chunks, so generation cost does **not** grow.

---

## 17. Failure-mode classification

Against the mandated taxonomy:

| Category | Count | Cases |
|---|---|---|
| **A. ROUTING FAILURE** (compound not detected) | **0** | — |
| **A′. ROUTING OVER-TRIGGER** (simple decomposed) | **3** | case-002, case-004, case-001 |
| **B. DECOMPOSITION FAILURE** (bad subquestions) | **0** | — |
| **C. RETRIEVAL FAILURE** (good subquestion, evidence still missing) | **1 partial** | case-022 (part-2 nuance only) |
| **D. EVIDENCE-MERGE FAILURE** (branch found it, allocator dropped it) | **0** | — |
| **E. GENERATION/REASONING FAILURE** (evidence reached prompt, 20B still wrong) | **0** | — |

**A′ is a category the mandated A–E taxonomy does not cover** — it is the mirror of A (over-decomposition
of simple questions rather than under-detection of compound ones). Flagging it rather than forcing it into
an existing code, since it is this experiment's dominant negative signal. Naming is the architect's call.

Notably **E = 0**: in every case where the necessary evidence reached the final prompt, NVIDIA 20B used it
correctly. Category D = 0 confirms the coverage-aware allocator does its job — no branch was starved.

---

## 18. Is a larger model justified?

**NO — not justified by this experiment's evidence.**

The mandated rule: a larger application model is justified next *iff* the necessary evidence **is** present
in the final merged prompt **and** 20B still fails correctness/completeness.

The second condition is not met. In all 3 compound cases the evidence reached the final prompt after
decomposition, and 20B **used it correctly** — every reference phrase that reached the context also
appeared in the answer (case-018 6/6, case-020 7/7, case-022 4/6 with the 2 misses being facts never
retrieved by either arm). Category E = 0. The observed bottleneck was **retrieval formulation**, and
decomposition addressed it without changing model size.

The one residual gap (case-022's "neither clue conclusive" nuance) is a **retrieval** gap — that evidence
was never in the prompt. Per the rule, a bigger model cannot reliably answer evidence it never received,
so model size is explicitly *not* the indicated fix there either.

**Confidence caveat, stated plainly:** 5 of 6 LLM judge scores are still missing due to the 120B outage
(two bounded probes, both timed out — §10), so this
answer rests on the deterministic proxy plus the reasons the baseline judge gave. It should be re-confirmed
once the judge stage completes. If LLM re-scoring later shows 20B failing *despite* complete evidence, the
rule would flip to YES — the resume command in §21 settles it at zero additional graph cost.

No larger model was run.

### Recorded decision rule for future experiments (architect, 2026-08-23)

> **IF** all facts needed to answer the question are present in the final merged prompt
> **AND** NVIDIA 20B still produces an incorrect or incomplete answer
> **THEN** model capacity / synthesis becomes a plausible bottleneck, and only then should an
> intermediate or larger application model be tested.

Corollary, which is what this experiment actually hit: **missing evidence → retrieval/decomposition
problem, NOT a larger-model problem.** A bigger model cannot answer evidence it never received.
Do not assume a 70B model is available; verify the free inventory at that time.

---

## 19. Production status

**Unchanged. Nothing was deployed.**

| Item | State |
|---|---|
| Deployed ask image | `d5af30e` — untouched |
| Production model | Groq `openai/gpt-oss-120b` — untouched |
| `MAX_LLM_CONTEXT_CHUNKS` | 5 — unchanged |
| Lambda dependencies | unchanged (no `langgraph`, no `langchain-core` in any image) |
| Qdrant collections | unchanged — no re-chunk, no re-embed |
| Retrieval config | `TOP_K=5`, `RETRIEVAL_FLOOR=0.15` — unchanged |
| API Gateway / CloudFront / DynamoDB / S3 / SQS | unchanged |
| LangSmith production project | `multitenant-rag-prod` — remains redacted; no experiment traces written to it |

The LangGraph code lives only in `multitenant-rag/evals/decomp_graph.py`, imported by an offline runner.

---

## 20. Limitations

1. **5 of 6 LLM judge scores are missing.** The NVIDIA 120B endpoint timed out on 11 of 12 judge requests;
   an 8-token probe did not return in 75 s. Only case-004 is LLM-scored. The compound-case before/after
   quality table is therefore **not** LLM-confirmed — §13's deterministic proxy is a substitute, and a
   coarse one.
2. **6 cases is a very small sample.** No statistical claim is made or implied.
3. **The routing arm is untested.** The analyzer classified all 6 cases compound, so the `simple` path
   never executed in this experiment. A constant classifier would have scored identically (§14).
4. **The deterministic proxy is coarse** — it under-counts word-form numbers (demonstrated on case-004,
   which it scores 1/3 while the LLM judge scores 1.000) and required Unicode-dash normalisation to avoid
   false negatives.
5. **case-020 does not isolate decomposition** — its missing evidence was retrievable at ranks 6–10, so a
   top-K raise is an untested cheaper alternative for that case.
6. **Synthetic corpus.** These are generated blog posts with clean, unambiguous facts; real user content is
   messier.
7. **Analyzer and generator are the same model (20B)**, so analyzer weaknesses and generator weaknesses are
   not independently attributable.
8. **Branch concurrency capped at 2**, so measured latency is not the floor for this topology.
9. **No cost/latency budget was set** for an agentic production path; the +2.6 s analyzer overhead is
   measured but not evaluated against any SLO.
10. **Git history still contains a retired credential.** The current tip is redacted (§23), but
    **historical Git commits may still contain the retired credential.** No history rewrite was performed
    (no `filter-repo`, no BFG, no force push) — that is a separate, explicit security task if this
    repository is ever prepared for public release.

---

## 21. Next experiment recommendation — `compound-router-v2` (DOCUMENTED ONLY, NOT EXECUTED)

The architect has selected **routing** as the next experiment. Nothing below has been executed, and the
router was deliberately **not** touched in this task: no analyzer-prompt change, no deterministic
heuristics, no classifier, and no use of dataset scenario labels at runtime.

### Goal

Distinguish **multiple independent information needs** from **one information need expressed with multiple
clauses**. The router must reason about the number of *independently retrievable* information needs — not
about conjunctions or clause count, which is exactly what the v1 analyzer latched onto.

| | Example | Why |
|---|---|---|
| Compound | "What was the highest Meral Index **and** why did the Hollow Stair remain unverified?" | two independent facts, two separate evidence needs |
| **Not** necessarily compound | "Did QL-2D satisfy X **and** remain blocked by Y?" | may be one object/state question despite the "and" |

All three v1 controls were of the second kind, and all three were misrouted — the concrete evidence
motivating this experiment.

### Offline evaluation design

- Use the existing evaluation corpus as **offline classifier ground truth**.
- Runtime analyzer input must contain **only** the question plus minimal scope metadata. It must **never**
  receive the scenario label, expected answer, baseline score, or judge score.
- Offline evaluation *may* compare predictions against human/existing labels — that asymmetry is the point.
- Evaluate on **substantially more than six questions**; six cannot measure specificity at all.

### Metrics

compound precision · compound recall · **simple specificity** · overall routing accuracy ·
**false-positive decomposition rate**.

False positives matter because unnecessary decomposition costs **+1 NVIDIA analyzer call, +(N−1) retrieval
operations, and added latency/tokens** — measured in §16 at ~2.6 s and ~462 tokens per question.

### Sequencing note

Two cheaper controls are worth considering alongside, both grounded in §15:

1. **Raise top-K on the deterministic baseline.** case-020's evidence sat at ranks 6-10, so part of the
   observed gain may be reachable with no agentic path at all. This is the honest null hypothesis for 1 of
   3 compound cases. It would **not** have fixed case-018 or case-022.
2. **Only after routing discriminates** is a wider run informative — running all 60 now would mostly
   re-measure a constant classifier.

Explicitly **not** indicated by current evidence: a larger application model (§18), chunk-size changes,
re-embedding, or deploying LangGraph.

---

## 22. Experiment v1 status — FROZEN

Frozen by architect decision (2026-08-23). Unchanged and not to be modified: LangGraph topology, `Send`
fan-out, parallel retrieval, `Semaphore(2)`, coverage-aware merge, final max-5 context, citation policy,
Titan embeddings, BM25, RRF, `TOP_K`, retrieval floor, chunk size, and models.

**Graph reruns performed after the initial run: 0.** The six graph executions are durable and
fingerprint-matched (`c56b24a3a1002366`); `analyze_question`, decomposition, branch retrieval, merge and
final generation were **not** re-executed for any case. Re-running them would have wasted NVIDIA 20B calls
and contaminated a controlled experiment.

The quarantined scope-bug records (`output/decomp_cases.INVALID_scope_bug.jsonl`) remain quarantined and are
**excluded from every statistic in this report**. The `target` / state-schema regression tests remain in
place — see §4 and §17.

---

## 23. Security cleanup state

Triggered by the secret scan for the handoff zip; resolved under architect decision (2026-08-23) in commit
**`85463b0`** on `main` (pushed).

The scan found **two distinct** credentials, not one — correcting an earlier report of mine that treated
them as the same reused value:

| Credential | Occurrences before | After | Disposition |
|---|---|---|---|
| Retired RDS master password | 3 (in 2 files) | **0** | Redacted to `"$DB_PASSWORD"` / `${DB_PASSWORD}` |
| Separate local Docker dev password | 7 (in 5 files) | 2 (in 1 file) | Redacted in all docs and in `.env.example`; left in `docker-compose.yml` |

Files redacted (6): `learnings/stage-1-ec2-rds.md`, `learnings/aws-cli-commands-glossary.md`,
`AWS-LEARNING-PLAN.md`, `README.md`, `blog-backend/.env.example`, `blog-backend/README.md`.

Credentialed connection strings were converted to **environment-variable examples** rather than deleted, so
the CLI and deployment walkthroughs remain useful for learning.

**No rotation required for the retired credential:** the RDS instance was torn down 2026-07-25 and
`aws rds describe-db-instances --region ap-south-1` now returns no instances. It is used by no active system.

**Deliberately not changed — `blog-backend/docker-compose.yml` (2 occurrences).** This is functional infra,
not documentation: `POSTGRES_PASSWORD` there actually provisions the local container. Replacing it with a
required env var would break `docker compose up postgres` for anyone whose local untracked `.env` predates
the change. Flagged for a separate decision rather than silently altered. It is a local-only dev credential
— AWS production reads all secrets from Secrets Manager (`CURRENT-IMPLEMENTATION.md` §11) and this value
appears nowhere in the Lambda configuration.

**Limitation:** current tip is redacted; **historical Git commits may still contain the retired
credential.** No history rewrite was performed (§20.10).

Also observed, out of scope and left untouched: `learnings/stage-1-ec2-rds.md` documents a literal
`JWT_SECRET` for the same torn-down EC2 instance, self-labelled as a change-later placeholder. Reported,
not modified.
