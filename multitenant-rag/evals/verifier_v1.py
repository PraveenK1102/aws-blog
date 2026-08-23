"""compound-router-cascade-v1 — Stage B strict compound verifier.

Architecture: Stage A is the FROZEN Router V2 (high recall, 11/11 compounds on the
adjudicated dev set, but 5 genuine false positives). Stage B is invoked ONLY on
questions Stage A called compound, and answers exactly one confirmation question:

    do V2's proposed information needs truly require SEPARATE retrieval
    neighbourhoods, or could one focused retrieval reasonably cover them?

It does not build a fresh routing plan. It cannot turn a V2 "simple" into a
compound — by construction the cascade's recall is bounded above by V2's.

Deliberately SMALL schema. v4 showed that rich nested router schemas raise
compliance risk (3/52 unscorable) and token cost for no accuracy gain, so this
returns a boolean plus a closed-enum code and an optional one-sentence reason.
No retrieval queries, no anchors, no facts arrays, no chain-of-thought.

Input is restricted to the question, V2's proposed needs, and V2's reason code.
No ground-truth label, expected answer, category, judge score, route or
"was V2 correct" signal ever reaches the model. Enforced by tests.

Isolated: imports neither router_v2/v3/v4 nor decomp_graph.
"""
import json, os, re, time

import nvidia_harness as H
import nvidia_provider as nv

VERIFIER_MODEL = os.environ.get("VERIFIER_V1_MODEL", H.APP_MODEL)   # NVIDIA 20B
VERIFIER_TIMEOUT = int(os.environ.get("VERIFIER_V1_TIMEOUT", "120"))
VERIFIER_MAX_TOKENS = int(os.environ.get("VERIFIER_V1_MAX_TOKENS", "1200"))
VERIFIER_TEMPERATURE = 0.0
STRUCTURED_OUTPUT_VERSION = "cascade-v1-verifier-1"

CONFIRM_CODE = "independent_evidence_neighborhoods"
REJECT_CODES = (
    "same_entity_state",
    "same_event_or_record",
    "same_series_synthesis",
    "same_subject_temporal_comparison",
    "same_subject_verification",
)
REASON_CODES = (CONFIRM_CODE,) + REJECT_CODES

VERIFIER_SYS = (
    "A first-pass router has proposed splitting a reader's question into several information "
    "needs. Your ONLY job is to confirm or reject that split.\n"
    "\n"
    "Answer one question: do those needs truly require SEPARATE retrieval neighbourhoods, or "
    "could ONE focused retrieval over a single topic reasonably cover them all?\n"
    "\n"
    "A retrieval neighbourhood is one localized place in a collection of blog posts - one "
    "object, state, event, record, decision or documented comparison - whose facts would be "
    "found together.\n"
    "\n"
    "Reply with ONLY JSON, no prose and no description of your reasoning:\n"
    f'{{"confirm_compound": true, "reason_code": "{CONFIRM_CODE}", "rationale": "one short sentence"}}\n'
    '{"confirm_compound": false, "reason_code": "same_entity_state", "rationale": "one short sentence"}\n'
    "\n"
    "CONFIRM the split (true) when the needs point to genuinely different neighbourhoods:\n"
    "- facts about different unrelated systems, subjects or domains\n"
    "- different places or contexts with their own independent rules or policies\n"
    "- different events whose explanations are documented independently\n"
    "- separate sibling items where an independently sourced value is required from EACH, and no "
    "single passage would reasonably carry them all\n"
    "\n"
    "REJECT the split (false) when the needs are really several attributes of ONE localized "
    "state, event or record:\n"
    "- one object's measurements and their consequences\n"
    "- a claim about a subject together with what that subject actually does\n"
    "- one property of one subject compared between its older and current values\n"
    "- several attributes of the same decision or the same documented comparison\n"
    "- one summary or conclusion drawn over a related series of observations\n"
    "\n"
    "Two things to weigh carefully, in both directions:\n"
    "- A shared word, entity or subject noun across the needs does NOT prove they share one "
    "neighbourhood. The same noun can be governed by unrelated rules in unrelated contexts; "
    "those are separate neighbourhoods.\n"
    "- Conversely, the fact that the question asks for several DIFFERENT outputs does NOT prove "
    "separate neighbourhoods. Several distinct facts about one localized thing are still one "
    "neighbourhood.\n"
    "\n"
    "Judge retrieval locality, not the number of needs, clauses or names.\n"
    "\n"
    "reason_code must be exactly one of:\n"
    f"  {CONFIRM_CODE} - the needs require separate retrieval (the ONLY code allowed with true)\n"
    "  same_entity_state - attributes of one entity's state\n"
    "  same_event_or_record - attributes or consequences of one event, decision or record\n"
    "  same_series_synthesis - one conclusion over a related series\n"
    "  same_subject_temporal_comparison - one subject's property across time\n"
    "  same_subject_verification - a claim about one subject plus its actual behaviour\n"
    "\n"
    "Keep rationale to one short sentence."
)


def build_messages(question: str, proposed_needs, v2_reason_code=None) -> list[dict]:
    """Verifier input: question + V2's proposed needs (+ optional V2 code). Nothing else."""
    needs = [str(n).strip() for n in (proposed_needs or []) if str(n).strip()]
    lines = [f"Question: {question}", "", "Proposed information needs:"]
    lines += [f"{i}. {n}" for i, n in enumerate(needs, 1)]
    if v2_reason_code:
        lines += ["", f"First-pass reason code: {v2_reason_code}"]
    return [{"role": "system", "content": VERIFIER_SYS},
            {"role": "user", "content": "\n".join(lines)}]


def parse_verifier_output(raw: str) -> dict:
    """Strict parse with flag/code consistency. Never raises."""
    out = {"confirm_compound": None, "reason_code": None, "rationale": None,
           "parse_ok": False, "parse_error": None}
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        out["parse_error"] = "no_json_object"; return out
    try:
        d = json.loads(m.group(0))
    except Exception as e:
        out["parse_error"] = f"json_decode:{type(e).__name__}"; return out
    if not isinstance(d, dict):
        out["parse_error"] = "not_an_object"; return out

    c = d.get("confirm_compound")
    if not isinstance(c, bool):
        out["parse_error"] = "confirm_compound_not_bool"; return out
    out["confirm_compound"] = c

    code = d.get("reason_code")
    out["reason_code"] = code
    if code not in REASON_CODES:
        out["parse_error"] = "reason_code_not_in_enum"; return out
    if c and code != CONFIRM_CODE:
        out["parse_error"] = "confirm_true_with_reject_reason_code"; return out
    if (not c) and code == CONFIRM_CODE:
        out["parse_error"] = "confirm_false_with_confirm_reason_code"; return out

    r = d.get("rationale")
    out["rationale"] = r.strip()[:200] if isinstance(r, str) else None
    out["parse_ok"] = True
    return out


def verify(question: str, proposed_needs, v2_reason_code=None) -> dict:
    """Exactly ONE bounded NVIDIA 20B call. Only for Stage-A-compound questions."""
    t0 = time.time()
    rec = {"provider_status": "ok", "latency_ms": None, "input_tokens": None,
           "output_tokens": None, "finish_reason": None}
    try:
        r = nv.chat(VERIFIER_MODEL, build_messages(question, proposed_needs, v2_reason_code),
                    max_tokens=VERIFIER_MAX_TOKENS, temperature=VERIFIER_TEMPERATURE,
                    timeout=VERIFIER_TIMEOUT)
    except Exception as e:
        rec.update({"provider_status": f"error:{type(e).__name__}",
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                    "confirm_compound": None, "reason_code": None, "rationale": None,
                    "parse_ok": False, "parse_error": "provider_error"})
        return rec
    rec.update({"latency_ms": r.get("latency_ms"), "input_tokens": r.get("input_tokens"),
                "output_tokens": r.get("output_tokens"), "finish_reason": r.get("finish_reason")})
    parsed = parse_verifier_output(r.get("content") or "")
    if not parsed["parse_ok"] and r.get("finish_reason") == "length":
        parsed["parse_error"] = "truncated_output_token_limit"
    rec.update(parsed)
    return rec
