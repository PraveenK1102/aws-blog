"""Decomposition in production — Groq 20B, frozen analyzer prompt and parser.

Router V2's `information_needs` are DELIBERATELY NOT used as subquestions. V2
was validated for ROUTING, not as a retrieval-query generator; the validated
subquestions come from this separate analyzer call. Using V2's needs directly
would ship an unvalidated retrieval behaviour.

Failure policy (§17): provider error / timeout / unretryable 429 / unusable
output all fall back to normal retrieval. A decomposition that cannot yield
>= 2 atomic subquestions is unusable — fanning out one degenerate branch would
just be the simple path with extra cost.
"""
import json
import re
import time

from common.logger import get_logger

from . import config, prompts
from .budget import BudgetExceeded, DeadlineExceeded
from .provider import ProviderError, chat

log = get_logger("rag.decomposition")

MAX_SUBQUESTIONS = 3


def parse_analysis(raw: str) -> dict:
    """Frozen v1 parser semantics, made non-raising for production.

    The offline version raised on missing JSON; here a parse failure is just an
    unusable decomposition, which the graph already handles as a fallback.
    """
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return {"is_compound": False, "subquestions": [],
                "parse_error": "no_json_object"}
    try:
        d = json.loads(m.group(0))
    except Exception as e:
        return {"is_compound": False, "subquestions": [],
                "parse_error": f"json_decode:{type(e).__name__}"}
    if not isinstance(d, dict):
        return {"is_compound": False, "subquestions": [],
                "parse_error": "not_an_object"}
    is_c = bool(d.get("is_compound"))
    subs = [str(s).strip() for s in (d.get("subquestions") or []) if str(s).strip()]
    if is_c and len(subs) < 2:
        is_c = False
        subs = []                                  # unusable -> treat as simple
    if is_c:
        subs = subs[:MAX_SUBQUESTIONS]             # cap at 3 atomic subquestions
    return {"is_compound": is_c, "subquestions": subs if is_c else [],
            "parse_error": None}


def build_messages(question: str) -> list[dict]:
    """Parity with the validated routed graph: the user turn is prefixed
    'Question: ' (v1's own analyze_question node did not, the accepted v2 node
    did — v2 is what was validated)."""
    return [{"role": "system", "content": prompts.ANALYZER_SYS},
            {"role": "user", "content": f"Question: {question}"}]


# ---------------------------------------------------------------- graph node
def decompose(state, budget):
    """Node: produce 2-3 atomic subquestions, or mark the decomposition unusable."""
    t0 = time.monotonic()
    try:
        r = chat(config.DECOMPOSE_MODEL, build_messages(state["question"]),
                 budget=budget, kind="decomposition",
                 max_tokens=config.DECOMPOSE_MAX_TOKENS,
                 ceiling_ms=config.DECOMPOSE_TIMEOUT_MS,
                 temperature=0.0)
    except (ProviderError, BudgetExceeded, DeadlineExceeded) as e:
        log.info("decomposition fell back to normal retrieval",
                 error_type=type(e).__name__)
        return {"subquestions": [], "decomposition_used": False,
                "decomposition_unusable": True, "decomposition_failed": True,
                "errors": [f"decompose:{type(e).__name__}"],
                "node_latencies": {
                    "decompose_ms": round((time.monotonic() - t0) * 1000, 1)}}

    parsed = parse_analysis(r["content"])
    subs = parsed["subquestions"]
    # Bound the branch count by the architectural retrieval-branch limit, so a
    # malformed model output can never fan out more work than the budget allows.
    subs = subs[:min(MAX_SUBQUESTIONS, budget_branch_room(budget))]
    unusable = len(subs) < 2
    if unusable:
        log.info("decomposition unusable; using normal retrieval",
                 subquestion_count=len(subs),
                 parse_error=str(parsed.get("parse_error")))
    return {"subquestions": subs,
            "decomposition_used": not unusable,
            "decomposition_unusable": unusable,
            "decomposition_failed": False,
            "tokens": {"decompose_in": r["input_tokens"],
                       "decompose_out": r["output_tokens"]},
            "node_latencies": {"decompose_ms": r["latency_ms"]}}


def budget_branch_room(budget) -> int:
    """How many retrieval branches the budget can still afford."""
    from .budget import LIMITS
    used = budget.counts["retrieval_branches"]
    return max(0, LIMITS["retrieval_branches"] - used)
