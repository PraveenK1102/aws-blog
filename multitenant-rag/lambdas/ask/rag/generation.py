"""Context building, generation, and citation construction.

THIS MODULE CLOSES THE TWO REPLAY CAVEATS from the offline validation
--------------------------------------------------------------------
The generation-only validation had to (a) RECONSTRUCT the prompt scaffolding
from persisted context strings and (b) SUBSTITUTE the compound system prompt on
three fallback cases whose real prompt needed a DynamoDB tenant lookup.

Neither approximation exists here:
  * `build_context` renders evidence blocks from the REAL Qdrant point objects,
    so the scaffolding is the genuine article, not a rebuild; and
  * the simple/fallback path calls the EXISTING production prompt builders
    (`_build_system_prompt` / `_build_group_system_prompt` via RagDeps), which
    do the tenant lookup and writer attribution properly.

CITATION INVARIANT
------------------
`state["merged_context"]` is the ONE capped list. `build_context` renders the
prompt from it and `build_citations` derives citations from it. There is no path
by which the model sees a chunk that is not citation-eligible, or a citation is
emitted for a chunk the model never received.
"""
import time

from common.logger import get_logger

from . import config, prompts
from .budget import BudgetExceeded, DeadlineExceeded
from .provider import ProviderError, ProviderRateLimited, chat

log = get_logger("rag.generation")

# Returned when generation cannot run at all (deadline/budget/provider).
UNAVAILABLE_TEXT = "\n\n[Error while generating response]"


def _blocks(state) -> str:
    """Evidence blocks for the COMPOUND prompt, from real point objects.

    Identical rendering to the validated compound graph: each block is labelled
    with the sub-question it was retrieved for, so the model can see coverage.
    """
    out = []
    cmap = state.get("merged_context_map") or []
    subs = state.get("subquestions") or []
    for i, c in enumerate(state["merged_context"]):
        p = getattr(c, "payload", {}) or {}
        si = (cmap[i].get("subquestion_index") if i < len(cmap) else None)
        label = f"[for sub-question {si + 1}] " if isinstance(si, int) and subs else ""
        out.append(f"{label}[Source: {p.get('title', '')}]\n{p.get('chunk_text', '')}")
    return "\n\n---\n\n".join(out)


def build_context(state, deps):
    """Node: turn the merged context into the exact prompts the model will get.

    Compound  -> frozen GEN_SYS_COMPOUND + question + sub-questions + evidence.
    Simple    -> the EXISTING production system prompt builders (§22), whose
                 prompt already embeds the evidence, so the user turn is the
                 plain question exactly as the current production path sends it.
    """
    ctx = state.get("merged_context") or []
    if not ctx:
        return {"system_prompt": "", "user_prompt": "", "citations": []}

    scope = state["scope"]
    if state.get("answer_path") == "compound":
        subs = "\n".join(f"{i + 1}. {s}"
                         for i, s in enumerate(state.get("subquestions") or []))
        user = (f"QUESTION:\n{state['question']}\n\n"
                f"SUB-QUESTIONS TO COVER:\n{subs}\n\n"
                f"EVIDENCE:\n{_blocks(state)}")
        system = prompts.GEN_SYS_COMPOUND
    elif scope.kind in ("multi", "group"):
        system = deps.build_group_system_prompt(ctx)
        user = state["question"]
    else:
        tenant = deps.get_tenant(scope.single_tenant) or {
            "display_name": "the author", "domain": "general"}
        system = deps.build_system_prompt(tenant, ctx)
        user = state["question"]

    return {"system_prompt": system, "user_prompt": user,
            "estimated_context_tokens": deps.context_est_tokens(ctx)}


def build_citations(state, deps) -> list[dict]:
    """Citations from the SAME capped list the model received. Never wider."""
    ctx = state.get("merged_context") or []
    if not ctx:
        return []
    if state["scope"].kind in ("multi", "group"):
        return deps.dedupe_citations_attributed(ctx)
    return deps.dedupe_citations(ctx)


def generate(state, budget, history: list[dict] | None = None):
    """Node: ONE bounded Groq 120B generation call for the whole question.

    Failure policy (§17): a generation failure does NOT retry through long
    rate-limit sleeps. It returns the existing controlled error contract so the
    HTTP layer can respond inside the deadline.
    """
    t0 = time.monotonic()
    if not state.get("merged_context"):
        return {"final_answer": "", "result_type": "empty_context",
                "node_latencies": {"generate_ms": 0.0}}

    messages = [{"role": "system", "content": state["system_prompt"]}]
    for turn in (history or []):
        role = turn.get("role")
        content = turn.get("content") or turn.get("text") or ""
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": state["user_prompt"]})

    try:
        r = chat(config.GENERATION_MODEL, messages, budget=budget, kind="generation",
                 max_tokens=config.GENERATION_MAX_TOKENS,
                 ceiling_ms=config.GENERATION_TIMEOUT_MS,
                 temperature=0.3)          # parity with the production path
    except ProviderRateLimited as e:
        log.warning("generation rate limited beyond deadline",
                    suggested_wait_ms=e.suggested_wait_ms,
                    remaining_budget_ms=budget.remaining_ms())
        return {"final_answer": "", "result_type": "provider_unavailable",
                "errors": ["generate:ProviderRateLimited"],
                "node_latencies": {
                    "generate_ms": round((time.monotonic() - t0) * 1000, 1)}}
    except (ProviderError, BudgetExceeded, DeadlineExceeded) as e:
        log.warning("generation failed", error_type=type(e).__name__,
                    remaining_budget_ms=budget.remaining_ms())
        return {"final_answer": "", "result_type": "generation_error",
                "errors": [f"generate:{type(e).__name__}"],
                "node_latencies": {
                    "generate_ms": round((time.monotonic() - t0) * 1000, 1)}}

    text = (r["content"] or "").strip()
    return {"final_answer": text,
            "result_type": "answered" if text else "generation_error",
            "tokens": {"gen_in": r["input_tokens"], "gen_out": r["output_tokens"]},
            "node_latencies": {"generate_ms": r["latency_ms"]}}
