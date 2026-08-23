"""compound-router-v4 — ATOMIC EVIDENCE-UNIT routing. Offline evaluation only.

v3 derived the verdict from len(retrieval_queries), which the model defeated by
OR-stuffing three independent needs into one query string (dev case-030), and it
over-applied "contrast -> one query" to a contrast spanning two unrelated domains
(dev case-041). A query STRING is not the semantic unit that matters.

v4 reasons in ATOMIC EVIDENCE UNITS. An evidence unit is one localized semantic
evidence neighbourhood from which one or more requested facts can reasonably be
retrieved together. The verdict is a property of the UNIT COUNT:

    needs_decomposition == (len(evidence_units) >= 2)     1..3 units

Crucially the atomicity rule is enforced in the PARSER, not only the prompt: a
unit whose retrieval_query joins independent targets with boolean syntax
(OR / AND / || / &&) is rejected, so v3's loophole cannot be re-used.

Exactly ONE NVIDIA 20B call per question. No second planner, no retrieval, no
generation, no judging. No chain-of-thought requested or stored.

Isolated: imports neither router_v2, router_v3 nor decomp_graph.
"""
import json, os, re, time

import nvidia_harness as H
import nvidia_provider as nv

ROUTER_MODEL = os.environ.get("ROUTER_V4_MODEL", H.APP_MODEL)      # NVIDIA 20B
ROUTER_TIMEOUT = int(os.environ.get("ROUTER_V4_TIMEOUT", "120"))
ROUTER_MAX_TOKENS = int(os.environ.get("ROUTER_V4_MAX_TOKENS", "1500"))
ROUTER_TEMPERATURE = 0.0
STRUCTURED_OUTPUT_VERSION = "router-v4-atomic-evidence-units-1"
MAX_EVIDENCE_UNITS = 3

COMPOUND_CODE = "multiple_evidence_neighborhoods"
SIMPLE_CODES = (
    "single_evidence_neighborhood",
    "one_entity_multi_attribute",
    "one_event_multi_consequence",
    "verification_same_subject",
    "temporal_same_attribute",
    "synthesis_related_observations",
    "scope_or_negative_check",
)
REASON_CODES = (COMPOUND_CODE,) + SIMPLE_CODES

# boolean stuffing inside ONE unit — the v3 loophole, now a parse error
_BOOLEAN_STUFF = re.compile(r"(?:\bOR\b|\bAND\b|\|\||&&)")

ROUTER_SYS = (
    "You plan document retrieval for a reader's question about a collection of blog posts.\n"
    "\n"
    "Think in EVIDENCE UNITS. An evidence unit is ONE localized evidence neighbourhood - one "
    "place in the collection - from which one or more of the requested facts can reasonably be "
    "retrieved TOGETHER.\n"
    "\n"
    "Decide the MINIMUM number of evidence units the answer genuinely requires, then output them.\n"
    "\n"
    "Reply with ONLY JSON, no prose and no description of your reasoning:\n"
    '{"evidence_units": [{"anchor": "...", "facts_needed": ["..."], "retrieval_query": "..."}], '
    '"needs_decomposition": false, "reason_code": "single_evidence_neighborhood", '
    '"rationale": "one short sentence"}\n'
    "\n"
    f"Rules:\n"
    f"- Output 1, 2 or at most {MAX_EVIDENCE_UNITS} evidence units. Prefer the fewest that are legitimate.\n"
    "- needs_decomposition MUST equal (number of evidence units >= 2).\n"
    "- Each unit must be ONE coherent retrieval neighbourhood. NEVER combine independent targets "
    "inside a single unit using OR, AND, ||, commas, slashes or lists. If focusing a search on one "
    "requested fact would substantially change the target entity, event, place or topic, that fact "
    "belongs in its OWN unit.\n"
    "- Keep anchor, facts_needed and retrieval_query short.\n"
    "\n"
    "The criterion is RETRIEVAL LOCALITY, not counting names:\n"
    "- Several attributes of one localized object, state or event = ONE unit, even if the question "
    "has several clauses.\n"
    "- Several consequences of one event = ONE unit.\n"
    "- A claim being checked, denied or corrected about the SAME subject, together with what that "
    "subject actually does = ONE unit.\n"
    "- The same entity's same attribute compared across time (current versus older) = ONE unit.\n"
    "- One summary or synthesis over a related series of observations = ONE unit.\n"
    "- A scope, applicability or did-it-happen-at-all check = ONE unit.\n"
    "- Several names can appear inside ONE unit when they belong to one decision, comparison or "
    "record. Naming two things does not by itself require two units.\n"
    "\n"
    "Use SEPARATE units when the evidence genuinely sits in different places:\n"
    "- A contrast is ONE unit only when both sides belong to the SAME object, event, experiment, "
    "decision or documented comparison. When each side belongs to a materially different context - "
    "a different place, domain or rule set - use one unit PER side. A shared word or shared subject "
    "noun does NOT prove shared retrieval locality.\n"
    "- When the answer needs an independently sourced value from EACH of several sibling items, and "
    "no single passage would reasonably carry them all, give each its own unit.\n"
    "- Facts about unrelated subjects, or separately documented events, measurements or times, "
    "belong in separate units.\n"
    "\n"
    "Ask yourself: how many independently localized pieces of evidence must be found? Never ask "
    "whether all the search words could be crammed into one string.\n"
    "\n"
    "reason_code must be exactly one of:\n"
    f"  {COMPOUND_CODE} - two or more evidence neighbourhoods (the ONLY code allowed with true)\n"
    "  single_evidence_neighborhood - one neighbourhood, nothing more specific applies\n"
    "  one_entity_multi_attribute - several attributes of one entity\n"
    "  one_event_multi_consequence - several consequences of one event\n"
    "  verification_same_subject - checking, denying or correcting one subject\n"
    "  temporal_same_attribute - one attribute of one subject across time\n"
    "  synthesis_related_observations - one summary over a related series\n"
    "  scope_or_negative_check - whether something applies or happened at all\n"
    "\n"
    "Preserve names, identifiers, time constraints and scope exactly. One short rationale sentence "
    "at most."
)


def build_messages(question: str) -> list[dict]:
    """Router input. ONLY the question crosses this boundary — never a label."""
    return [{"role": "system", "content": ROUTER_SYS},
            {"role": "user", "content": f"Question: {question}"}]


def _bad_unit(u) -> str | None:
    """Structural + atomicity validation of one evidence unit. Returns an error key."""
    if not isinstance(u, dict): return "unit_not_object"
    a = u.get("anchor")
    if not isinstance(a, str) or not a.strip(): return "unit_anchor_missing"
    f = u.get("facts_needed")
    if not isinstance(f, list) or not f: return "unit_facts_needed_empty"
    if not all(isinstance(x, str) and x.strip() for x in f): return "unit_facts_needed_invalid"
    q = u.get("retrieval_query")
    if not isinstance(q, str) or not q.strip(): return "unit_retrieval_query_empty"
    # the v3 loophole: independent targets crammed into one unit with boolean syntax
    if _BOOLEAN_STUFF.search(q): return "unit_boolean_stuffing"
    return None


def parse_router_output(raw: str) -> dict:
    """Strict parse. Verdict is derived from the UNIT COUNT. Never raises."""
    out = {"needs_decomposition": None, "evidence_units": [], "evidence_unit_count": 0,
           "reason_code": None, "rationale": None, "parse_ok": False, "parse_error": None}
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        out["parse_error"] = "no_json_object"; return out
    try:
        d = json.loads(m.group(0))
    except Exception as e:
        out["parse_error"] = f"json_decode:{type(e).__name__}"; return out
    if not isinstance(d, dict):
        out["parse_error"] = "not_an_object"; return out

    units = d.get("evidence_units")
    if not isinstance(units, list) or not units:
        out["parse_error"] = "evidence_units_empty"; return out
    if len(units) > MAX_EVIDENCE_UNITS:
        out["evidence_unit_count"] = len(units)
        out["parse_error"] = "too_many_evidence_units"; return out
    for u in units:
        err = _bad_unit(u)
        if err:
            out["parse_error"] = err; return out
    out["evidence_units"] = [{"anchor": u["anchor"].strip(),
                              "facts_needed": [x.strip() for x in u["facts_needed"]],
                              "retrieval_query": u["retrieval_query"].strip()} for u in units]
    out["evidence_unit_count"] = len(units)

    nd = d.get("needs_decomposition")
    if not isinstance(nd, bool):
        out["parse_error"] = "needs_decomposition_not_bool"; return out
    out["needs_decomposition"] = nd

    code = d.get("reason_code")
    out["reason_code"] = code
    if code not in REASON_CODES:
        out["parse_error"] = "reason_code_not_in_enum"; return out

    # the v4 invariant: the verdict IS the unit count
    if nd != (len(units) >= 2):
        out["parse_error"] = "unit_count_flag_invariant_violated"; return out
    if nd and code != COMPOUND_CODE:
        out["parse_error"] = "compound_flag_with_simple_reason_code"; return out
    if (not nd) and code == COMPOUND_CODE:
        out["parse_error"] = "simple_flag_with_compound_reason_code"; return out

    r = d.get("rationale")
    out["rationale"] = r.strip()[:200] if isinstance(r, str) else None
    out["parse_ok"] = True
    return out


def classify(question: str) -> dict:
    """Exactly ONE bounded NVIDIA 20B call. Concurrency is the caller's job."""
    t0 = time.time()
    rec = {"provider_status": "ok", "latency_ms": None, "input_tokens": None,
           "output_tokens": None, "finish_reason": None}
    try:
        r = nv.chat(ROUTER_MODEL, build_messages(question),
                    max_tokens=ROUTER_MAX_TOKENS, temperature=ROUTER_TEMPERATURE,
                    timeout=ROUTER_TIMEOUT)
    except Exception as e:
        rec.update({"provider_status": f"error:{type(e).__name__}",
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                    "needs_decomposition": None, "evidence_units": [],
                    "evidence_unit_count": 0, "reason_code": None, "rationale": None,
                    "parse_ok": False, "parse_error": "provider_error"})
        return rec
    rec.update({"latency_ms": r.get("latency_ms"), "input_tokens": r.get("input_tokens"),
                "output_tokens": r.get("output_tokens"), "finish_reason": r.get("finish_reason")})
    parsed = parse_router_output(r.get("content") or "")
    if not parsed["parse_ok"] and r.get("finish_reason") == "length":
        parsed["parse_error"] = "truncated_output_token_limit"
    rec.update(parsed)
    return rec
