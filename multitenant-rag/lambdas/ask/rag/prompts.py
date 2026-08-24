"""FROZEN model contracts, carried into production byte-for-byte.

These strings were validated offline and are now ACCEPTED architecture:

  * ROUTER_SYS       — Router V2, passed the 40-case untouched holdout
                       (recall 1.000, specificity 0.950) and the final
                       high-recall holdout gate.
  * ANALYZER_SYS     — the decomposition prompt from the validated routed graph.
  * GEN_SYS_COMPOUND — the compound generation system prompt.

WHY THEY LIVE HERE AND NOT IN evals/
------------------------------------
Production must not import eval modules at runtime, so the text is duplicated
rather than imported. Duplication invites drift, so the exact hashes below are
asserted by `rag/test_frozen_parity.py`, which ALSO re-derives the strings from
the frozen eval modules when those are present and fails on any byte difference.
Changing a prompt now requires changing a hash, which is a reviewable diff.

DO NOT EDIT the strings. A reworded prompt is a new experiment that must be
re-validated against a holdout before it may ship.
"""
import hashlib

# Router V2 system prompt — FROZEN.
# sha256[:16] = 763d12cd82245285
ROUTER_SYS = 'You decide whether answering a reader\'s question about a collection of blog posts requires MORE THAN ONE separate document-retrieval query.\n\nReply with ONLY JSON, no prose and no description of your reasoning:\n{"needs_decomposition": true, "information_needs": ["...", "..."], "reason_code": "multiple_independent_retrieval_needs"}\n{"needs_decomposition": false, "information_needs": ["..."], "reason_code": "single_retrieval_need"}\n\nThe test is retrieval locality, not grammar. Ask yourself: would the facts needed to answer this come from DIFFERENT places in the collection, each needing its own focused search?\n\nAnswer true ONLY when the question requires two or more information needs that are INDEPENDENTLY RETRIEVABLE — each targets a different subject, measurement, event or explanation, and each would clearly benefit from its own separate search query.\n\nAnswer false when every requested detail concerns the SAME entity, event or state, so one localized passage would plausibly contain them all.\n\nGrammar is NOT evidence. Do NOT answer true merely because the question contains \'and\', \'or\', \'but\', several clauses, more than one question mark, or asks for several attributes. Specifically:\n- several attributes or properties of ONE thing is false\n- a yes/no check plus \'what does it really do\' about the SAME thing is false\n- confirming, denying, correcting or bounding ONE fact is false\n- cause and effect of the SAME event is false\n\nreason_code must be exactly one of:\n  multiple_independent_retrieval_needs - two or more separate retrieval targets (the only code allowed with true)\n  single_retrieval_need - one information need\n  single_entity_multi_attribute - several attributes of one entity\n  single_event_multi_attribute - several attributes of one event or state\n  negative_or_scope_check - verifying, denying or bounding one fact\n\ninformation_needs: when true, give 2-3 atomic needs, each ONE information need, preserving names, identifiers, time constraints and scope EXACTLY as written. When false, give exactly one entry restating the single focused need.'

# Decomposition analyzer system prompt — FROZEN.
# sha256[:16] = ae8185181e88f25f
ANALYZER_SYS = 'You analyse a reader\'s question about a set of blog posts and decide whether it contains MORE THAN ONE independent information need.\nReply with ONLY JSON, no prose:\n{"is_compound": true, "subquestions": ["...", "..."]}\nor {"is_compound": false, "subquestions": []}\nRules: mark is_compound true only when the question asks for two or three things that could each be answered separately. Produce 2-3 atomic subquestions, each ONE information need. Preserve the original names, entities, identifiers and time constraints exactly. Do not change the intent, do not add facts, and together they must cover the whole original question. A single question with one need is NOT compound.'

# Compound generation system prompt — FROZEN.
# sha256[:16] = 8c30bb9b064e6784
GEN_SYS_COMPOUND = "You answer a reader's question using ONLY the evidence excerpts provided.\nThe question has several parts. You MUST address EVERY part.\nThe excerpts are grouped by the sub-question they were retrieved for.\nIf the evidence for one part is missing, say plainly that it is not covered by the available posts — never silently skip a part and never invent facts.\nCite sources by post title in normal prose. Do not output your reasoning steps."

# Recorded identities of the frozen contracts (§5 parity record).
FROZEN_HASHES = {
    "ROUTER_SYS": "763d12cd82245285",
    "ANALYZER_SYS": "ae8185181e88f25f",
    "GEN_SYS_COMPOUND": "8c30bb9b064e6784",
}

# Closed reason-code enum. `multiple_independent_retrieval_needs` is the ONLY
# code permitted alongside needs_decomposition=true.
COMPOUND_CODE = "multiple_independent_retrieval_needs"
SIMPLE_CODES = (
    "single_retrieval_need",
    "single_entity_multi_attribute",
    "single_event_multi_attribute",
    "negative_or_scope_check",
)
REASON_CODES = (COMPOUND_CODE,) + SIMPLE_CODES

# Expected router JSON schema, recorded so a contract change is explicit.
ROUTER_SCHEMA = {
    "needs_decomposition": "bool (required)",
    "information_needs": "list[str] (>=2 when needs_decomposition is true)",
    "reason_code": f"one of {REASON_CODES}",
}

# Expected decomposition JSON schema.
DECOMPOSITION_SCHEMA = {
    "is_compound": "bool",
    "subquestions": "list[str] (2-3 atomic needs when is_compound)",
}


def prompt_sha(text: str) -> str:
    """First 16 hex chars of the sha256 — the identity form used everywhere."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def live_hashes() -> dict:
    """Hashes of the strings actually loaded in this process."""
    return {
        "ROUTER_SYS": prompt_sha(ROUTER_SYS),
        "ANALYZER_SYS": prompt_sha(ANALYZER_SYS),
        "GEN_SYS_COMPOUND": prompt_sha(GEN_SYS_COMPOUND),
    }


def assert_frozen() -> None:
    """Fail closed if any frozen prompt drifted. Called at graph build time."""
    live = live_hashes()
    for name, expected in FROZEN_HASHES.items():
        if live[name] != expected:
            raise RuntimeError(
                f"FROZEN PROMPT DRIFT: {name} is {live[name]}, expected {expected}. "
                "A reworded prompt is a new experiment and must be re-validated.")
