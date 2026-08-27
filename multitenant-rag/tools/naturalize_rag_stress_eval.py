"""Rewrite golden-question WORDING only. All metadata is preserved byte-for-byte.

Runs AFTER corpus generation and is never imported by the generator, so natural
phrasings cannot leak into the prose. Only `question` changes; the original is
kept as `question_template`. Category prefixes such as `[simple]` are stripped —
they are metadata and must never reach retrieval.
"""
import json
import os
import random
import re

SEED = 20260827
HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.abspath(os.path.join(HERE, "..", "..", "rag-stress-corpus"))
PREFIX_RE = re.compile(r"^\s*\[[a-z-]+\]\s*", re.I)

# Rare identifiers must survive naturalisation untouched (§28).
TOKEN_RE = re.compile(r"\b(INC-\d+|QL-\d[A-Z]|WG-\d+|SKU-[0-9A-Za-z]+|batch-\d+|"
                      r"runbook-\d+|Model R\d+|Project Blue[a-z]+)\b")


def rng(qid):
    return random.Random(f"{SEED}|nat|{qid}")


def names_from(q):
    """Recover the person/subject wording the template already used."""
    return q


def naturalize(c):
    r = rng(c["question_id"])
    q = PREFIX_RE.sub("", c["question"]).strip()
    t = c["query_type"]

    if t == "simple_factual":
        m = re.match(r"What did (.+?) report about (.+?)\?$", q)
        if m:
            who, what = m.groups()
            return r.choice([
                f"What did {who} say about {what}?",
                f"According to {who}, what happened with {what}?",
                f"What does {who} write about {what}?",
                f"Can you tell me what {who} reported regarding {what}?",
            ])
    elif t == "high_overlap_discrimination":
        m = re.match(r"What was (.+?)'s outcome at (.+?)\?$", q)
        if m:
            who, co = m.groups()
            return r.choice([
                f"How did {who}'s {co} interview end?",
                f"What was the result of {who}'s interview at {co}?",
                f"Did {who} get an offer from {co}, or what happened?",
                f"What happened with {who} at {co}?",
            ])
    elif t == "cross_user_comparison":
        m = re.match(r"Compare the interview outcomes of (.+?)\.$", q)
        if m:
            who = m.group(1)
            return r.choice([
                f"How did the interview outcomes compare for {who}?",
                f"What happened in the interviews for {who}?",
                f"Compare how {who} each did in their interviews.",
            ])
    elif t == "compound_decomposition":
        parts = [p.strip() for p in re.split(r"\s+And\s+", q) if p.strip()]
        cleaned = []
        for p in parts:
            p = p.rstrip("?").strip()
            m = re.match(r"what did (.+?) report about (.+)$", p, re.I)
            cleaned.append(f"what {m.group(1)} said about {m.group(2)}" if m else p)
        if cleaned:
            # Every independent need is preserved — never collapsed into one.
            return "Could you tell me " + ", and ".join(cleaned) + "?"
    elif t == "scope_isolation":
        m = re.match(r"Within this selection, what was said about (.+?) interviews\?$", q)
        if m:
            co = m.group(1)
            return r.choice([
                f"Based only on the people I've selected, what do they say about interviewing at {co}?",
                f"Do any of these selected writers discuss {co} interviews?",
                f"From this selection alone, what is said about {co} interviews?",
            ])
    elif t == "exact_token_bm25":
        m = re.match(r"What does (.+?) refer to\?$", q)
        if m:
            tok = m.group(1)
            return r.choice([
                f"What is {tok}?",
                f"What does {tok} actually refer to?",
                f"Can you tell me what {tok} is?",
            ])
    elif t == "unanswerable":
        m = re.match(r"What is (.+?)'s (.+?)\?$", q)
        if m:
            who, thing = m.groups()
            return r.choice([
                f"What is {who}'s {thing}?",
                f"Do you know {who}'s {thing}?",
                f"Can you tell me {who}'s {thing}?",
            ])
    elif t == "temporal_update":
        m = re.match(r"What is (.+?) targeting now\?$", q)
        if m:
            return r.choice([f"What role is {m.group(1)} going for now?",
                             f"What is {m.group(1)} currently targeting?"])
        m = re.match(r"What was (.+?) originally targeting\?$", q)
        if m:
            return r.choice([f"What role was {m.group(1)} aiming for at the start?",
                             f"What was {m.group(1)} originally going for?"])
    return q


def main():
    src = json.load(open(os.path.join(SPEC, "rag_stress_eval_v1.json"), encoding="utf-8"))
    out = []
    for c in src["cases"]:
        n = dict(c)
        n["question_template"] = c["question"]
        n["question"] = naturalize(c)
        out.append(n)
    payload = {"meta": {**src["meta"], "naturalized": True, "seed": SEED},
               "cases": out}
    with open(os.path.join(SPEC, "rag_stress_eval_v1_naturalized.json"), "w",
              encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    # Cohort-A applicable view, decided mechanically from minimum_cohort.
    applicable = [c for c in out if c["minimum_cohort"] == "A"]
    gen = os.path.join(SPEC, "generated", "cohort-a")
    with open(os.path.join(gen, "eval_applicable.json"), "w", encoding="utf-8") as fh:
        json.dump({"meta": {"cohort": "A", "selected_by": "minimum_cohort == 'A'",
                            "total_cases": len(out), "applicable": len(applicable)},
                   "cases": applicable}, fh, indent=2, ensure_ascii=False)
    print(f"  naturalized {len(out)} cases; Cohort-A applicable {len(applicable)}")


if __name__ == "__main__":
    main()
