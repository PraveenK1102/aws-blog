"""Deterministic validator for the RAG Architecture Stress Corpus v1 spec.

FAILS CLOSED: any structural violation exits non-zero. Purely offline — it reads
the four JSON manifests and performs no AWS, network or provider call.
"""
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.abspath(os.path.join(HERE, "..", "..", "rag-stress-corpus"))

EXPECT_USERS, EXPECT_POSTS, EXPECT_CASES = 100, 1800, 240
COHORTS = ["A", "B", "C", "D"]
PER_COHORT = 25
POSTS_PER_USER = 18
COMPOSITION = {"job_search": 10, "ai_ml_swe": 5, "travel_food": 3,
               "eng_notes": 3, "noise": 2, "adversarial": 2}
EVAL_PLAN = {"simple_factual": 60, "high_overlap_discrimination": 40,
             "cross_user_comparison": 35, "compound_decomposition": 35,
             "scope_isolation": 25, "exact_token_bm25": 15,
             "unanswerable": 15, "temporal_update": 15}
# Anything resembling a real credential must never appear in a spec file.
SECRET_RE = re.compile(
    r"(gsk_[A-Za-z0-9]{20,}|nvapi-|lsv2_|AKIA[0-9A-Z]{16}|-----BEGIN|"
    r"password\s*[:=]\s*['\"][^'\"]{6,}|eyJ[A-Za-z0-9_-]{10,}\.eyJ)")


def load(name):
    with open(os.path.join(SPEC, name), encoding="utf-8") as fh:
        return json.load(fh)


def main():
    problems = []

    def check(cond, msg):
        if not cond:
            problems.append(msg)

    users = load("rag_stress_users_v1.json")["users"]
    posts = load("rag_stress_posts_v1.json")["posts"]
    facts = load("rag_stress_facts_v1.json")["facts"]
    cases = load("rag_stress_eval_v1.json")["cases"]

    # ---- users -----------------------------------------------------------
    check(len(users) == EXPECT_USERS, f"users: {len(users)} != {EXPECT_USERS}")
    uids = [u["user_id"] for u in users]
    check(len(set(uids)) == len(uids), "duplicate user_id")
    check(len({u["username"] for u in users}) == len(users), "duplicate username")
    by_cohort = collections.Counter(u["cohort"] for u in users)
    for c in COHORTS:
        check(by_cohort[c] == PER_COHORT, f"cohort {c}: {by_cohort[c]} != {PER_COHORT}")
        comp = collections.Counter(u["category"] for u in users if u["cohort"] == c)
        for cat, n in COMPOSITION.items():
            check(comp[cat] == n,
                  f"cohort {c} {cat}: {comp[cat]} != {n} (cohorts must match in difficulty)")
    check(all(u.get("synthetic") is True for u in users), "user not marked synthetic")

    # ---- posts -----------------------------------------------------------
    check(len(posts) == EXPECT_POSTS, f"posts: {len(posts)} != {EXPECT_POSTS}")
    pids = [p["post_id"] for p in posts]
    check(len(set(pids)) == len(pids), "duplicate post_id")
    per_user = collections.Counter(p["user_id"] for p in posts)
    for u in users:
        check(per_user[u["user_id"]] == POSTS_PER_USER,
              f"{u['user_id']}: {per_user[u['user_id']]} posts != {POSTS_PER_USER}")
    check(set(per_user) <= set(uids), "post references an unknown user")
    for p in posts:
        check(len(p["target_word_range"]) == 2
              and p["target_word_range"][0] < p["target_word_range"][1],
              f"{p['post_id']}: bad target_word_range")
    mix = collections.Counter(p["size_class"] for p in posts)
    for cls, lo, hi in (("medium", 0.55, 0.65), ("long", 0.25, 0.35), ("short", 0.05, 0.15)):
        frac = mix[cls] / len(posts)
        check(lo <= frac <= hi, f"size mix {cls} {frac:.2f} outside [{lo},{hi}]")

    # ---- facts -----------------------------------------------------------
    fids = [f["fact_id"] for f in facts]
    check(len(set(fids)) == len(fids), "duplicate fact_id")
    fset, pset = set(fids), set(pids)
    for f in facts:
        check(f["post_id"] in pset, f"{f['fact_id']}: unknown post {f['post_id']}")
        check(f["user_id"] in set(uids), f"{f['fact_id']}: unknown user")
    declared = {fid for p in posts for fid in p["fact_ids"]}
    check(declared == fset,
          f"post fact_ids and fact records disagree "
          f"(only-in-posts={len(declared - fset)}, only-in-facts={len(fset - declared)})")
    pmap = {p["post_id"]: p for p in posts}
    for f in facts:
        check(f["fact_id"] in pmap[f["post_id"]]["fact_ids"],
              f"{f['fact_id']} not listed on its own post")
    # noise posts must carry no evaluation-relevant facts
    for p in posts:
        if p["category"] == "noise":
            check(not p["fact_ids"], f"{p['post_id']}: noise post has facts")
    # substantial posts should carry 3-6 facts
    for p in posts:
        if p["category"] != "noise":
            check(3 <= len(p["fact_ids"]) <= 6,
                  f"{p['post_id']}: {len(p['fact_ids'])} facts outside 3-6")
    check(any(f["temporal_status"] != "current" for f in facts), "no temporal facts")
    check(any(f["confusable_with"] for f in facts), "no confusable facts")
    check(any(f["rare_tokens"] for f in facts), "no rare-token facts")

    # ---- eval ------------------------------------------------------------
    check(len(cases) == EXPECT_CASES, f"eval cases: {len(cases)} != {EXPECT_CASES}")
    qids = [c["question_id"] for c in cases]
    check(len(set(qids)) == len(qids), "duplicate question_id")
    got = collections.Counter(c["query_type"] for c in cases)
    for k, n in EVAL_PLAN.items():
        check(got[k] == n, f"eval {k}: {got[k]} != {n}")
    for c in cases:
        for pid in c["expected_post_ids"] + c["expected_citation_post_ids"]:
            check(pid in pset, f"{c['question_id']}: unknown expected post {pid}")
        for pid in c["forbidden_post_ids"]:
            check(pid in pset, f"{c['question_id']}: unknown forbidden post {pid}")
        for uid in c["expected_user_ids"] + c["forbidden_user_ids"] + c["scope_user_ids"]:
            check(uid in set(uids), f"{c['question_id']}: unknown user {uid}")
        for fid in c["expected_fact_ids"]:
            check(fid in fset, f"{c['question_id']}: unknown fact {fid}")
        # an answerable case must name evidence; an unanswerable one must not
        if c["answerable"]:
            check(c["expected_fact_ids"] and c["expected_post_ids"],
                  f"{c['question_id']}: answerable but no evidence")
        else:
            check(not c["expected_fact_ids"] and not c["expected_post_ids"]
                  and not c["expected_citation_post_ids"],
                  f"{c['question_id']}: unanswerable but names evidence")
        # expected and forbidden evidence must never overlap
        check(not (set(c["expected_post_ids"]) & set(c["forbidden_post_ids"])),
              f"{c['question_id']}: post is both expected and forbidden")
        check(not (set(c["expected_user_ids"]) & set(c["forbidden_user_ids"])),
              f"{c['question_id']}: user is both expected and forbidden")
        check(c["scale_stability"] in ("fixed_ground_truth", "scale_dependent"),
              f"{c['question_id']}: bad scale_stability")
        check(c["minimum_cohort"] in COHORTS, f"{c['question_id']}: bad minimum_cohort")
        if c["should_decompose"]:
            check(c["expected_router_class"] == "compound",
                  f"{c['question_id']}: decompose without compound router class")

    # a fixed_ground_truth case must be answerable from Cohort A alone
    cohort = {u["user_id"]: u["cohort"] for u in users}
    for c in cases:
        if c["scale_stability"] == "fixed_ground_truth" and c["expected_user_ids"]:
            check(all(cohort[u] == "A" for u in c["expected_user_ids"]),
                  f"{c['question_id']}: fixed_ground_truth but expects a non-Cohort-A user")
    check(sum(1 for c in cases if c["scale_stability"] == "scale_dependent") > 0,
          "no scale_dependent cases (§21 requires a subset)")
    # context-cap pressure must span 2..6+
    need = {c["required_fact_count"] for c in cases
            if c["query_type"] == "compound_decomposition"}
    for n in (2, 3, 4, 5):
        check(n in need, f"no compound case requiring {n} facts")
    check(any(n >= 6 for n in need), "no compound case beyond the 5-chunk context cap")

    # ---- safety ----------------------------------------------------------
    for name in ("rag_stress_users_v1.json", "rag_stress_posts_v1.json",
                 "rag_stress_facts_v1.json", "rag_stress_eval_v1.json"):
        with open(os.path.join(SPEC, name), encoding="utf-8") as fh:
            blob = fh.read()
        check(not SECRET_RE.search(blob), f"{name}: possible credential material")
        check("password" not in blob.lower() or "GitHub password" in blob,
              f"{name}: unexpected password field")

    print(f"  users={len(users)} posts={len(posts)} facts={len(facts)} cases={len(cases)}")
    print(f"  cohorts={dict(by_cohort)}")
    print(f"  size mix={dict(mix)}")
    print(f"  eval mix={dict(got)}")
    print(f"  stability={dict(collections.Counter(c['scale_stability'] for c in cases))}")
    print(f"  compound fact-count classes={sorted(need)}")
    if problems:
        print(f"\n  *** {len(problems)} PROBLEM(S) ***")
        for p in problems[:40]:
            print(f"    - {p}")
        return 1
    print("\n  RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
