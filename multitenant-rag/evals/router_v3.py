"""compound-router-v3 — retrieval-PLAN routing. Offline evaluation only.

Central design change from v2: v2 asked "does this question contain multiple
information needs?", which let contrast / verification / temporal framing about a
single subject read as two needs (5 genuine false positives on the dev set). v3
instead asks "how many focused retrieval QUERIES would a good retriever actually
need?" and makes the model emit the minimal retrieval PLAN. The verdict is then a
property of the plan, not a separate judgement:

    needs_decomposition == (len(retrieval_queries) >= 2)

Exactly ONE NVIDIA 20B call per question. No planner second pass, no retrieval,
no generation, no judging. No chain-of-thought requested or stored.

Isolated: does not import or mutate router_v2.py or decomp_graph.py.
"""
import json, os, re, time

import nvidia_harness as H
import nvidia_provider as nv

ROUTER_MODEL = os.environ.get("ROUTER_V3_MODEL", H.APP_MODEL)      # NVIDIA 20B
ROUTER_TIMEOUT = int(os.environ.get("ROUTER_V3_TIMEOUT", "120"))
ROUTER_MAX_TOKENS = int(os.environ.get("ROUTER_V3_MAX_TOKENS", "1500"))
ROUTER_TEMPERATURE = 0.0
STRUCTURED_OUTPUT_VERSION = "router-v3-retrieval-plan-1"
MAX_RETRIEVAL_QUERIES = 3

COMPOUND_CODE = "multiple_independent_retrieval_targets"
# Simple codes deliberately NAME the shapes v2 got wrong, so the model has a
# category to reach for instead of defaulting to the compound code.
SIMPLE_CODES = (
    "single_retrieval_target",
    "single_entity_multi_attribute",
    "single_event_multi_consequence",
    "contrast_or_verification_one_subject",
    "temporal_update_same_topic",
    "synthesis_over_related_observations",
    "scope_or_negative_check",
)
REASON_CODES = (COMPOUND_CODE,) + SIMPLE_CODES

ROUTER_SYS = (
    "You plan document retrieval for a reader's question about a collection of blog posts.\n"
    "\n"
    "Your job: decide the MINIMUM number of focused retrieval queries a good search engine "
    "would need in order to find evidence for everything the question asks.\n"
    "\n"
    "Reply with ONLY JSON, no prose and no description of your reasoning:\n"
    '{"needs_decomposition": false, "retrieval_queries": ["..."], '
    '"reason_code": "single_retrieval_target", "rationale": "one short sentence"}\n'
    '{"needs_decomposition": true, "retrieval_queries": ["...", "..."], '
    f'"reason_code": "{COMPOUND_CODE}", "rationale": "one short sentence"}}\n'
    "\n"
    "retrieval_queries is an actual minimal plan: each entry is a search query you would run.\n"
    "Use ONE query whenever one focused search over a single topic could reasonably return "
    "evidence for every requested output.\n"
    "Use TWO or THREE queries ONLY when the evidence targets sit in genuinely separate "
    "semantic neighbourhoods, so a search for one would not be expected to surface the other.\n"
    f"Never emit more than {MAX_RETRIEVAL_QUERIES} queries. Always prefer the smallest plan "
    "that works.\n"
    "needs_decomposition MUST equal (number of retrieval_queries >= 2).\n"
    "\n"
    "Use ONE query for these shapes — each is a single retrieval target:\n"
    "- one entity with several attributes\n"
    "- one event with several consequences\n"
    "- one object contrasted with what it actually does, or a claim being verified or corrected\n"
    "- one subject before and after a change, or an update to the same underlying topic\n"
    "- one scope, applicability or negative check\n"
    "- one synthesis or summary over a related series of observations\n"
    "\n"
    "The words 'and', 'but', 'while', 'versus', 'before', 'after', two clauses, or a second "
    "question mark do NOT by themselves justify more than one query.\n"
    "\n"
    "Use two or three queries only when the question genuinely combines separate targets, for "
    "example a fact about one unrelated subject together with a fact about another, or "
    "separately documented events or measurements whose evidence would not be found together.\n"
    "\n"
    "reason_code must be exactly one of:\n"
    f"  {COMPOUND_CODE} - two or more separate retrieval targets (the ONLY code allowed with true)\n"
    "  single_retrieval_target - one target, nothing more specific applies\n"
    "  single_entity_multi_attribute - several attributes of one entity\n"
    "  single_event_multi_consequence - several consequences of one event\n"
    "  contrast_or_verification_one_subject - checking, correcting or contrasting one subject\n"
    "  temporal_update_same_topic - same topic before/after or current-versus-older\n"
    "  synthesis_over_related_observations - one summary over a related series\n"
    "  scope_or_negative_check - whether something applies, or whether it happened at all\n"
    "\n"
    "Preserve names, identifiers, time constraints and scope exactly in the queries.\n"
    "Keep rationale to one short sentence."
)


def build_messages(question: str) -> list[dict]:
    """Router input. ONLY the question crosses this boundary — never a label."""
    return [{"role": "system", "content": ROUTER_SYS},
            {"role": "user", "content": f"Question: {question}"}]


def parse_router_output(raw: str) -> dict:
    """Strict parse enforcing the plan/flag invariant. Never raises."""
    out = {"needs_decomposition": None, "retrieval_queries": [], "reason_code": None,
           "rationale": None, "parse_ok": False, "parse_error": None}
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        out["parse_error"] = "no_json_object"; return out
    try:
        d = json.loads(m.group(0))
    except Exception as e:
        out["parse_error"] = f"json_decode:{type(e).__name__}"; return out
    if not isinstance(d, dict):
        out["parse_error"] = "not_an_object"; return out

    nd = d.get("needs_decomposition")
    if not isinstance(nd, bool):
        out["parse_error"] = "needs_decomposition_not_bool"; return out
    out["needs_decomposition"] = nd

    q = d.get("retrieval_queries")
    if not isinstance(q, list) or not all(isinstance(x, str) for x in q):
        out["parse_error"] = "retrieval_queries_not_string_list"; return out
    q = [x.strip() for x in q if x and x.strip()]
    out["retrieval_queries"] = q
    if not q:
        out["parse_error"] = "retrieval_queries_empty"; return out
    if len(q) > MAX_RETRIEVAL_QUERIES:
        out["parse_error"] = "too_many_retrieval_queries"; return out

    code = d.get("reason_code")
    out["reason_code"] = code
    if code not in REASON_CODES:
        out["parse_error"] = "reason_code_not_in_enum"; return out

    # the v3 invariant: the verdict must BE a property of the plan
    if nd != (len(q) >= 2):
        out["parse_error"] = "plan_flag_invariant_violated"; return out
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
    rec = {"provider_status": "ok", "latency_ms": None,
           "input_tokens": None, "output_tokens": None, "finish_reason": None}
    try:
        r = nv.chat(ROUTER_MODEL, build_messages(question),
                    max_tokens=ROUTER_MAX_TOKENS, temperature=ROUTER_TEMPERATURE,
                    timeout=ROUTER_TIMEOUT)
    except Exception as e:
        rec.update({"provider_status": f"error:{type(e).__name__}",
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                    "needs_decomposition": None, "retrieval_queries": [],
                    "reason_code": None, "rationale": None,
                    "parse_ok": False, "parse_error": "provider_error"})
        return rec
    rec.update({"latency_ms": r.get("latency_ms"), "input_tokens": r.get("input_tokens"),
                "output_tokens": r.get("output_tokens"), "finish_reason": r.get("finish_reason")})
    parsed = parse_router_output(r.get("content") or "")
    if not parsed["parse_ok"] and r.get("finish_reason") == "length":
        parsed["parse_error"] = "truncated_output_token_limit"
    rec.update(parsed)
    return rec
