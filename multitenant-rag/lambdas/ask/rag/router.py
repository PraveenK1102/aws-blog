"""Router V2 in production — Groq 20B, frozen prompt, frozen parser.

The parser is a byte-for-byte port of the frozen `router_v2.parse_router_output`
(held to parity by test_frozen_parity.py). It never raises: every malformed
output becomes a structured verdict with `parse_ok=False`, and the graph then
takes the SIMPLE path, which is the safe direction — a missed decomposition
degrades answer coverage, whereas a spurious one spends budget.

Failure policy (§17): router provider error / timeout / unretryable 429 does NOT
fail the request. It falls back to normal RAG, because routing is an optimisation
and its failure must not cost the user their answer.
"""
import json
import re
import time

from common.logger import get_logger

from . import config, prompts
from .budget import BudgetExceeded, DeadlineExceeded
from .provider import ProviderError, chat

log = get_logger("rag.router")


def build_messages(question: str) -> list[dict]:
    """Router input. ONLY the question crosses this boundary."""
    return [{"role": "system", "content": prompts.ROUTER_SYS},
            {"role": "user", "content": f"Question: {question}"}]


def parse_router_output(raw: str) -> dict:
    """Strict structured parse. Never raises.

    Byte-for-byte port of the frozen router_v2 parser, including the
    enum/flag consistency rules and every parse_error string.
    """
    out = {"needs_decomposition": None, "information_needs": [],
           "reason_code": None, "parse_ok": False, "parse_error": None}
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        out["parse_error"] = "no_json_object"
        return out
    try:
        d = json.loads(m.group(0))
    except Exception as e:
        out["parse_error"] = f"json_decode:{type(e).__name__}"
        return out
    if not isinstance(d, dict):
        out["parse_error"] = "not_an_object"
        return out

    nd = d.get("needs_decomposition")
    if not isinstance(nd, bool):
        out["parse_error"] = "needs_decomposition_not_bool"
        return out

    code = d.get("reason_code")
    if code not in prompts.REASON_CODES:
        out["parse_error"] = "reason_code_not_in_enum"
        out["needs_decomposition"] = nd
        return out
    # enum/flag consistency: the compound code is exclusive to true, and vice versa
    if nd and code != prompts.COMPOUND_CODE:
        out["parse_error"] = "compound_flag_with_simple_reason_code"
        out["needs_decomposition"] = nd
        out["reason_code"] = code
        return out
    if (not nd) and code == prompts.COMPOUND_CODE:
        out["parse_error"] = "simple_flag_with_compound_reason_code"
        out["needs_decomposition"] = nd
        out["reason_code"] = code
        return out

    needs = d.get("information_needs")
    if not isinstance(needs, list) or not all(isinstance(x, str) for x in needs):
        out["parse_error"] = "information_needs_not_string_list"
        out["needs_decomposition"] = nd
        out["reason_code"] = code
        return out
    needs = [x.strip() for x in needs if x and x.strip()]
    if nd and len(needs) < 2:
        out["parse_error"] = "compound_needs_fewer_than_two"
        out["needs_decomposition"] = nd
        out["reason_code"] = code
        out["information_needs"] = needs
        return out

    out.update({"needs_decomposition": nd, "information_needs": needs,
                "reason_code": code, "parse_ok": True})
    return out


def classify(question: str, budget) -> dict:
    """One bounded Groq 20B router call. Returns a verdict; never raises."""
    t0 = time.monotonic()
    try:
        r = chat(config.ROUTER_MODEL, build_messages(question),
                 budget=budget, kind="router",
                 max_tokens=config.ROUTER_MAX_TOKENS,
                 ceiling_ms=config.ROUTER_TIMEOUT_MS,
                 temperature=0.0)
    except (ProviderError, BudgetExceeded, DeadlineExceeded) as e:
        return {"needs_decomposition": None, "information_needs": [],
                "reason_code": None, "parse_ok": False,
                "parse_error": f"provider:{type(e).__name__}",
                "failed": True,
                "latency_ms": round((time.monotonic() - t0) * 1000, 1)}
    parsed = parse_router_output(r["content"])
    if not parsed["parse_ok"] and r.get("finish_reason") == "length":
        parsed["parse_error"] = "truncated_output_token_limit"
    parsed.update({"failed": False, "latency_ms": r["latency_ms"],
                   "input_tokens": r["input_tokens"],
                   "output_tokens": r["output_tokens"]})
    return parsed


# ---------------------------------------------------------------- graph node
def route_question(state, budget):
    """Node: decide simple vs compound. Any failure -> simple (safe direction)."""
    res = classify(state["question"], budget)
    nd = res.get("needs_decomposition")
    parse_ok = bool(res.get("parse_ok"))
    # Route compound ONLY on a verdict that passed its own schema validation.
    # The parser can return needs_decomposition=True together with parse_ok=False
    # (e.g. reason_code "compound_flag_with_simple_reason_code", or fewer than two
    # information_needs). Trusting that verdict would spend a decomposition call
    # and up to 3 retrieval branches on output that violated the frozen contract.
    # The offline graph did not gate on parse_ok because every holdout case parsed
    # cleanly, so this error path was never exercised there; production gates it.
    compound = parse_ok and nd is True
    out = {"needs_decomposition": compound,
           "router_reason_code": res.get("reason_code"),
           "router_information_needs": list(res.get("information_needs") or []),
           "router_parse_ok": parse_ok,
           "router_failed": bool(res.get("failed")),
           "tokens": {"router_in": res.get("input_tokens"),
                      "router_out": res.get("output_tokens")},
           "node_latencies": {"route_question_ms": res.get("latency_ms")}}
    if not parse_ok:
        # Unparseable, contract-violating, or provider failure -> simple path.
        out["errors"] = [f"router:{res.get('parse_error')}"]
        log.info("router fell back to simple path",
                 reason=str(res.get("parse_error")))
    return out


def router_edge(state) -> str:
    return "compound" if state.get("needs_decomposition") else "simple"
