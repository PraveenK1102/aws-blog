"""compound-router-v2 — offline ROUTING evaluation only.

Decides whether a reader's question needs decomposition into multiple retrieval
queries. This module performs NO retrieval, NO fan-out, NO answer generation and
NO judging — it calls the NVIDIA 20B analyzer and returns a structured verdict.

Isolated by design: it does NOT import or mutate decomp_graph.py. Experiment
rag-agentic-decomposition-nvidia20b-v1 is frozen.

Anti-leakage invariant: build_messages() receives ONLY the question text. No
scenario label, case_id category, expected answer, baseline score, judge score
or known compound status ever reaches the model. Enforced by tests.
"""
import json, os, re, time

import nvidia_harness as H
import nvidia_provider as nv

ROUTER_MODEL = os.environ.get("ROUTER_V2_MODEL", H.APP_MODEL)     # NVIDIA 20B
ROUTER_TIMEOUT = int(os.environ.get("ROUTER_V2_TIMEOUT", "120"))
ROUTER_MAX_TOKENS = int(os.environ.get("ROUTER_V2_MAX_TOKENS", "1500"))

# Small closed enum. `multiple_independent_retrieval_needs` is the ONLY code
# permitted alongside needs_decomposition=true.
COMPOUND_CODE = "multiple_independent_retrieval_needs"
SIMPLE_CODES = (
    "single_retrieval_need",
    "single_entity_multi_attribute",
    "single_event_multi_attribute",
    "negative_or_scope_check",
)
REASON_CODES = (COMPOUND_CODE,) + SIMPLE_CODES

# ---------------------------------------------------------------------------
# The ONE v2 design under evaluation. Not tuned against the 52-case set.
#
# v1 failed because its rule ("two things that could each be answered
# separately") is satisfied by almost any two-clause question, so it classified
# 6/6 cases compound including 3/3 simple controls. v2 therefore anchors the
# decision on RETRIEVAL LOCALITY — would the facts come from different places,
# needing separate focused searches — and explicitly rejects grammar as evidence.
# ---------------------------------------------------------------------------
ROUTER_SYS = (
    "You decide whether answering a reader's question about a collection of blog posts "
    "requires MORE THAN ONE separate document-retrieval query.\n"
    "\n"
    "Reply with ONLY JSON, no prose and no description of your reasoning:\n"
    '{"needs_decomposition": true, "information_needs": ["...", "..."], '
    f'"reason_code": "{COMPOUND_CODE}"}}\n'
    '{"needs_decomposition": false, "information_needs": ["..."], '
    '"reason_code": "single_retrieval_need"}\n'
    "\n"
    "The test is retrieval locality, not grammar. Ask yourself: would the facts needed to "
    "answer this come from DIFFERENT places in the collection, each needing its own focused "
    "search?\n"
    "\n"
    "Answer true ONLY when the question requires two or more information needs that are "
    "INDEPENDENTLY RETRIEVABLE — each targets a different subject, measurement, event or "
    "explanation, and each would clearly benefit from its own separate search query.\n"
    "\n"
    "Answer false when every requested detail concerns the SAME entity, event or state, so "
    "one localized passage would plausibly contain them all.\n"
    "\n"
    "Grammar is NOT evidence. Do NOT answer true merely because the question contains "
    "'and', 'or', 'but', several clauses, more than one question mark, or asks for several "
    "attributes. Specifically:\n"
    "- several attributes or properties of ONE thing is false\n"
    "- a yes/no check plus 'what does it really do' about the SAME thing is false\n"
    "- confirming, denying, correcting or bounding ONE fact is false\n"
    "- cause and effect of the SAME event is false\n"
    "\n"
    "reason_code must be exactly one of:\n"
    f"  {COMPOUND_CODE} - two or more separate retrieval targets (the only code allowed with true)\n"
    "  single_retrieval_need - one information need\n"
    "  single_entity_multi_attribute - several attributes of one entity\n"
    "  single_event_multi_attribute - several attributes of one event or state\n"
    "  negative_or_scope_check - verifying, denying or bounding one fact\n"
    "\n"
    "information_needs: when true, give 2-3 atomic needs, each ONE information need, "
    "preserving names, identifiers, time constraints and scope EXACTLY as written. When "
    "false, give exactly one entry restating the single focused need."
)


def build_messages(question: str) -> list[dict]:
    """Router input. ONLY the question crosses this boundary — never a label."""
    return [{"role": "system", "content": ROUTER_SYS},
            {"role": "user", "content": f"Question: {question}"}]


def parse_router_output(raw: str) -> dict:
    """Strict structured parse. Returns {needs_decomposition, information_needs,
    reason_code, parse_ok, parse_error}. Never raises."""
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
    if code not in REASON_CODES:
        out["parse_error"] = f"reason_code_not_in_enum"
        out["needs_decomposition"] = nd
        return out
    # enum/flag consistency: the compound code is exclusive to true, and vice versa
    if nd and code != COMPOUND_CODE:
        out["parse_error"] = "compound_flag_with_simple_reason_code"
        out["needs_decomposition"] = nd
        out["reason_code"] = code
        return out
    if (not nd) and code == COMPOUND_CODE:
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


def classify(question: str) -> dict:
    """One bounded NVIDIA 20B router call. Concurrency is the caller's job."""
    t0 = time.time()
    rec = {"provider_status": "ok", "latency_ms": None,
           "input_tokens": None, "output_tokens": None}
    try:
        r = nv.chat(ROUTER_MODEL, build_messages(question),
                    max_tokens=ROUTER_MAX_TOKENS, temperature=0.0,
                    timeout=ROUTER_TIMEOUT)
    except Exception as e:
        rec.update({"provider_status": f"error:{type(e).__name__}",
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                    "needs_decomposition": None, "information_needs": [],
                    "reason_code": None, "parse_ok": False,
                    "parse_error": "provider_error"})
        return rec
    rec.update({"latency_ms": r.get("latency_ms"),
                "input_tokens": r.get("input_tokens"),
                "output_tokens": r.get("output_tokens")})
    parsed = parse_router_output(r.get("content") or "")
    if not parsed["parse_ok"] and r.get("finish_reason") == "length":
        parsed["parse_error"] = "truncated_output_token_limit"
    rec["finish_reason"] = r.get("finish_reason")
    rec.update(parsed)
    return rec
