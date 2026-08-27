"""Deterministic generator for the RAG Architecture Stress Corpus v1 SPEC.

DESIGN ONLY. Emits structural manifests and the golden evaluation set — never
prose, never AWS calls, never provider calls. Re-running with the same SEED
reproduces byte-identical output.

Ordering principle: structured ground truth FIRST (stable user/post/fact IDs),
prose later, so a generation batch can only fill in text around facts it is
handed and can never invent or move one.
"""
import json
import os
import random

SEED = 20260827
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(HERE, "..", "..", "rag-stress-corpus"))

COHORTS = ["A", "B", "C", "D"]
USERS_PER_COHORT = 25
POSTS_PER_USER = 18

# Per-cohort composition — IDENTICAL in every cohort so growth comparisons are
# meaningful and difficulty is not back-loaded into later cohorts.
COMPOSITION = [
    ("job_search", 10), ("ai_ml_swe", 5), ("travel_food", 3),
    ("eng_notes", 3), ("noise", 2), ("adversarial", 2),
]

COMPANIES = ["Amazon", "Microsoft", "NVIDIA", "Google", "Adobe", "Atlassian",
             "Zoho", "Freshworks", "Razorpay", "Hexline Labs", "Northwind Systems"]
# Deliberately overlapping outcomes: the SAME company recurs with DIFFERENT
# results so retrieval must find the person-specific fact (§5).
OUTCOMES = ["offer", "rejected_system_design", "rejected_dsa",
            "rejected_behavioral", "withdrew", "ghosted",
            "passed_all_rounds", "rejected_hiring_manager"]
ROLES = ["Full Stack", "Backend", "ML Engineer", "Generative AI Engineer",
         "Data Engineer", "Platform / DevOps"]
TECH = ["Python", "Java", "FastAPI", "Spring", "React", "PostgreSQL", "Redis",
        "AWS", "Docker", "Kubernetes", "PyTorch", "RAG", "LangGraph", "Qdrant",
        "vector search"]
PLACES = ["Chennai", "Rameswaram", "Bangalore", "Coimbatore", "Hyderabad"]
VENUES = ["Rayar's Mess", "Murugan Idli Shop", "Brew Lane", "Kadal Café",
          "Anna Tiffin Centre"]
ENG_TOPICS = ["caching", "Redis", "database indexes", "transactions", "queues",
              "Docker", "AWS", "rate limiting", "timeouts", "retries",
              "CI/CD", "React", "API design"]

# §9 confusable families — near-identical tokens that separate dense retrieval
# from BM25 exact-token retrieval.
CONFUSABLE_FAMILIES = [
    ["Project Bluefin", "Project Bluefire", "Project Bluebird"],
    ["INC-731", "INC-713", "INC-137"],
    ["QL-2C", "QL-2D", "QL-2B"],
    ["Model R14", "Model R41", "Model R144"],
    ["WG-03", "WG-30", "WG-003"],
    ["batch-9812", "batch-9821", "batch-9182"],
    ["SKU-44A1", "SKU-44AI", "SKU-441A"],
    ["runbook-7", "runbook-77", "runbook-07"],
]

FIRST = ["Kavin", "Divya", "Nandhini", "Vignesh", "Aishwarya", "Karthik",
         "Swathi", "Naveen", "Priya", "Arjun", "Meera", "Rohit", "Anusha",
         "Vikram", "Sneha", "Dinesh", "Lakshmi", "Rahul", "Deepa", "Sanjay",
         "Nithya", "Manoj", "Kavya", "Sudhir", "Ramya"]
LAST = ["Raj", "Kumar", "Menon", "Iyer", "Rao", "Nair", "Pillai", "Reddy",
        "Sharma", "Varma", "Bose", "Chandran", "Selvan", "Mohan", "Prasad",
        "Krishnan", "Ravi", "Balan", "Gupta", "Shetty", "Naidu", "Joshi",
        "Verma", "Anand", "Dutta"]

LEN_MEDIUM, LEN_LONG, LEN_SHORT = (500, 800), (900, 1200), (150, 350)


def _rng(*parts):
    """Stable per-entity RNG so a change in one user cannot shift another."""
    return random.Random(f"{SEED}|" + "|".join(str(p) for p in parts))


def build_users():
    users, n = [], 0
    for ci, cohort in enumerate(COHORTS):
        slots = []
        for cat, count in COMPOSITION:
            slots += [cat] * count
        assert len(slots) == USERS_PER_COHORT
        for si, cat in enumerate(slots):
            n += 1
            r = _rng("user", n)
            first = FIRST[(n * 7 + ci) % len(FIRST)]
            last = LAST[(n * 13 + si) % len(LAST)]
            uid = f"U{n:03d}"
            users.append({
                "user_id": uid,
                "name": f"{first} {last}",
                "username": f"{first.lower()}.{last.lower()}{n:03d}",
                "cohort": cohort,
                "cohort_index": si + 1,
                "category": cat,
                "role": r.choice(ROLES) if cat in ("job_search", "ai_ml_swe") else None,
                "primary_company": r.choice(COMPANIES) if cat == "job_search" else None,
                "outcome": OUTCOMES[(n + si) % len(OUTCOMES)] if cat == "job_search" else None,
                "tech": sorted(r.sample(TECH, 5)),
                "place": r.choice(PLACES) if cat == "travel_food" else None,
                "venue": r.choice(VENUES) if cat == "travel_food" else None,
                "confusable_family": (CONFUSABLE_FAMILIES[(n // 25) % len(CONFUSABLE_FAMILIES)]
                                      if cat == "adversarial" else None),
                "expected_posts": POSTS_PER_USER,
                "synthetic": True,
            })
    assert len(users) == 100
    return users


# Length plan per user: 60% medium / 30% long / 10% short overall, with noise
# users deliberately skewed short.
def _length_plan(cat, r):
    if cat == "noise":
        plan = ["short"] * 10 + ["medium"] * 7 + ["long"] * 1
    else:
        plan = ["medium"] * 11 + ["long"] * 6 + ["short"] * 1
    r.shuffle(plan)
    return plan


def build_posts(users):
    posts = []
    for u in users:
        r = _rng("posts", u["user_id"])
        plan = _length_plan(u["category"], r)
        for pi in range(1, POSTS_PER_USER + 1):
            size = plan[pi - 1]
            wr = {"medium": LEN_MEDIUM, "long": LEN_LONG, "short": LEN_SHORT}[size]
            month = 3 + ((pi - 1) // 3)                # Mar..Aug 2026
            day = 1 + ((pi - 1) * 5) % 27
            posts.append({
                "post_id": f"{u['user_id']}-P{pi:02d}",
                "user_id": u["user_id"],
                "username": u["username"],
                "cohort": u["cohort"],
                "category": u["category"],
                "sequence": pi,
                "title_slug": f"{u['category']}-{pi:02d}",
                "date": f"2026-{month:02d}-{day:02d}",
                "topic": _topic(u, pi, r),
                "tags": _tags(u, pi),
                "size_class": size,
                "target_word_range": list(wr),
                "fact_ids": [],
                "noise_fact_ids": [],
                "confusable_post_ids": [],
            })
    assert len(posts) == 1800
    return posts


def _topic(u, pi, r):
    c = u["category"]
    if c == "job_search":
        return ["application", "interview_round", "outcome", "prep", "role_change"][pi % 5]
    if c == "ai_ml_swe":
        return ["model_work", "rag_project", "evaluation", "infra", "writeup"][pi % 5]
    if c == "travel_food":
        return ["visit", "food", "travel_note", "place_review"][pi % 4]
    if c == "eng_notes":
        return ENG_TOPICS[pi % len(ENG_TOPICS)]
    if c == "adversarial":
        return ["identifier_note", "incident", "component", "measurement"][pi % 4]
    return ["diary", "formatting_test", "random", "status"][pi % 4]


def _tags(u, pi):
    base = {"job_search": ["job-search", "interview"],
            "ai_ml_swe": ["ml", "engineering"],
            "travel_food": ["travel", "food"],
            "eng_notes": ["engineering", "notes"],
            "adversarial": ["reference", "identifier"],
            "noise": ["personal"]}[u["category"]]
    return base + [u["cohort"].lower()]


# ---------------------------------------------------------------- facts
FACT_TYPES = ["event_outcome", "measurement", "identifier", "preference",
              "decision", "location_visit", "status_change"]


def build_facts(users, posts):
    """3-6 evaluation-relevant facts per substantial post; 0 for noise posts.

    Every fact is attributable to exactly one (user, post) so ground truth stays
    unambiguous even where users legitimately contradict one another (§7).
    """
    by_user = {u["user_id"]: u for u in users}
    facts, seq = [], {}
    for p in posts:
        u = by_user[p["user_id"]]
        r = _rng("facts", p["post_id"])
        if u["category"] == "noise":
            # Noise carries keyword overlap but no evaluation-relevant fact.
            p["noise_fact_ids"] = [f"NOISE-{p['post_id']}-{i:02d}" for i in range(1, 3)]
            continue
        n = 3 if p["size_class"] == "short" else r.randint(3, 6)
        for i in range(1, n + 1):
            seq[p["post_id"]] = seq.get(p["post_id"], 0) + 1
            fid = f"FACT-{u['user_id']}-P{p['sequence']:02d}-{seq[p['post_id']]:03d}"
            fact = _make_fact(fid, u, p, i, r)
            facts.append(fact)
            p["fact_ids"].append(fid)
    return facts


def _make_fact(fid, u, p, i, r):
    cat, topic = u["category"], p["topic"]
    base = {"fact_id": fid, "user_id": u["user_id"], "post_id": p["post_id"],
            "topic": topic, "date": p["date"], "temporal_status": "current",
            "confusable_with": [], "rare_tokens": [], "importance": "normal"}

    if cat == "job_search":
        if topic == "outcome":
            base.update(fact_type="event_outcome", subject=u["primary_company"],
                        predicate="interview_outcome", value=u["outcome"],
                        importance="high",
                        expected_evidence=f"{u['name']} {u['primary_company']} {u['outcome']}")
        elif topic == "role_change":
            base.update(fact_type="status_change", subject="target_role",
                        predicate="changed_to", value=r.choice(ROLES),
                        temporal_status="superseded" if p["sequence"] < 10 else "latest",
                        importance="high",
                        expected_evidence=f"{u['name']} target role")
        elif topic == "interview_round":
            base.update(fact_type="event_outcome", subject=u["primary_company"],
                        predicate="round_result",
                        value=r.choice(["passed", "failed", "rescheduled"]),
                        expected_evidence=f"{u['name']} {u['primary_company']} round")
        else:
            base.update(fact_type="preference", subject="stack",
                        predicate="uses", value=r.choice(u["tech"]),
                        expected_evidence=f"{u['name']} stack")
    elif cat == "ai_ml_swe":
        base.update(fact_type="measurement", subject=r.choice(["retrieval", "latency", "accuracy"]),
                    predicate="measured", value=f"{r.randint(5, 60)}%",
                    expected_evidence=f"{u['name']} measurement")
    elif cat == "travel_food":
        base.update(fact_type="location_visit", subject=u["venue"],
                    predicate=r.choice(["visited_for", "mentioned_without_visiting"]),
                    value=r.choice(["dosa", "filter coffee", "idli", "no visit"]),
                    expected_evidence=f"{u['name']} {u['venue']}")
    elif cat == "eng_notes":
        base.update(fact_type="measurement", subject=topic,
                    predicate="result", value=f"{r.randint(3, 45)}% change",
                    expected_evidence=f"{u['name']} {topic}")
    elif cat == "adversarial":
        fam = u["confusable_family"]
        token = fam[(p["sequence"] + i) % len(fam)]
        base.update(fact_type="identifier", subject=token,
                    predicate="refers_to",
                    value=r.choice(["incident", "component", "batch", "model"]),
                    rare_tokens=[token],
                    confusable_with=[t for t in fam if t != token],
                    importance="high",
                    expected_evidence=f"{u['name']} {token}")
    return base


def link_confusables(users, posts, facts):
    """Cross-link posts that share a confusable token family, so a golden case
    can name the posts a retriever must NOT return."""
    by_token = {}
    for f in facts:
        for t in f["rare_tokens"]:
            by_token.setdefault(t, []).append(f["post_id"])
    pmap = {p["post_id"]: p for p in posts}
    for fam in CONFUSABLE_FAMILIES:
        pids = sorted({pid for t in fam for pid in by_token.get(t, [])})
        for pid in pids:
            pmap[pid]["confusable_post_ids"] = [x for x in pids if x != pid][:6]
    return posts


# ------------------------------------------------------- golden evaluation
EVAL_PLAN = [
    ("simple_factual", 60), ("high_overlap_discrimination", 40),
    ("cross_user_comparison", 35), ("compound_decomposition", 35),
    ("scope_isolation", 25), ("exact_token_bm25", 15),
    ("unanswerable", 15), ("temporal_update", 15),
]
assert sum(n for _, n in EVAL_PLAN) == 240


def build_eval(users, posts, facts):
    """240 golden cases.

    SCALE STABILITY (§20/§21) is the load-bearing idea: a case is
    `fixed_ground_truth` only when every expected user lives in COHORT A, so the
    answer is already valid at 25 users and later cohorts can add distractors
    without changing it. Anything whose answer legitimately shifts as cohorts
    arrive is marked `scale_dependent` and is measured separately.
    """
    by_user = {u["user_id"]: u for u in users}
    fbyu = {}
    for f in facts:
        fbyu.setdefault(f["user_id"], []).append(f)
    cohortA = [u for u in users if u["cohort"] == "A"]
    js_A = [u for u in cohortA if u["category"] == "job_search"]
    adv_all = [u for u in users if u["category"] == "adversarial"]
    adv_A = [u for u in adv_all if u["cohort"] == "A"]

    cases, qn = [], 0

    def add(**kw):
        nonlocal qn
        qn += 1
        base = {"question_id": f"Q{qn:03d}", "scope_group_ids": [],
                "forbidden_user_ids": [], "forbidden_post_ids": [],
                "required_fact_count": 1, "should_decompose": False,
                "expected_router_class": "simple",
                "expected_router_reason": "single_retrieval_need",
                "scale_stability": "fixed_ground_truth", "minimum_cohort": "A",
                "minimum_user_count": 25, "notes": ""}
        base.update(kw)
        base["expected_citation_post_ids"] = base.get(
            "expected_citation_post_ids", base["expected_post_ids"])
        cases.append(base)

    def pick(u, pred, k=1):
        got = [f for f in fbyu.get(u["user_id"], []) if pred(f)]
        return got[:k]

    # --- A. simple factual (60) — all Cohort A, fixed ground truth ---------
    pool = [u for u in cohortA if u["category"] != "noise"]
    for i in range(60):
        u = pool[i % len(pool)]
        fs = fbyu[u["user_id"]]
        f = fs[(i * 3) % len(fs)]
        add(question=f"[simple] What did {u['name']} report about {f['subject']}?",
            query_type="simple_factual", scope_type="single_user",
            scope_user_ids=[u["user_id"]], answerable=True,
            expected_user_ids=[u["user_id"]], expected_post_ids=[f["post_id"]],
            expected_fact_ids=[f["fact_id"]],
            expected_answer_facts=[f"{f['subject']} {f['predicate']} {f['value']}"],
            notes="Single authoritative source; MRR is meaningful here.")

    # --- B. high-overlap discrimination (40) — many users share the company -
    js_all = [u for u in users if u["category"] == "job_search"]
    js_later = [u for u in js_all if u["cohort"] != "A"]
    for i in range(40):
        # 24 anchored in Cohort A (fixed ground truth as distractors accumulate)
        # + 16 whose authoritative source only EXISTS from a later cohort, so the
        # case is scale_dependent and cannot be scored before that scale.
        u = js_A[i % len(js_A)] if i < 24 else js_later[(i - 24) % len(js_later)]
        f = (pick(u, lambda x: x["topic"] == "outcome") or fbyu[u["user_id"]])[0]
        others = [o["user_id"] for o in users
                  if o["category"] == "job_search"
                  and o["primary_company"] == u["primary_company"]
                  and o["user_id"] != u["user_id"]][:8]
        add(question=f"[overlap] What was {u['name']}'s outcome at {u['primary_company']}?",
            query_type="high_overlap_discrimination", scope_type="single_user",
            scope_user_ids=[u["user_id"]], answerable=True,
            expected_user_ids=[u["user_id"]], expected_post_ids=[f["post_id"]],
            expected_fact_ids=[f["fact_id"]],
            expected_answer_facts=[f"{u['primary_company']}: {u['outcome']}"],
            forbidden_user_ids=others,
            notes="Many users discuss the same company; only this source is correct. "
                  "Distractor count grows with cohort — measures wrong-user retrieval.")

    # --- C. cross-user comparison (35) — 2..5 user scope --------------------
    for i in range(35):
        k = 2 + (i % 4)                                   # 2,3,4,5 user scope
        # Two thirds stay inside Cohort A; one third deliberately spans cohorts
        # so the comparison only becomes answerable at a larger scale.
        src = js_A if (i % 3) else js_all
        grp = [src[(i + j * 3) % len(src)] for j in range(k)]
        seen, grp2 = set(), []
        for g in grp:
            if g["user_id"] not in seen:
                seen.add(g["user_id"]); grp2.append(g)
        j = 0
        while len(grp2) < k:
            cand = src[(i + 7 + j) % len(src)]; j += 1
            if cand["user_id"] not in seen:
                seen.add(cand["user_id"]); grp2.append(cand)
        grp = grp2
        ids = [g["user_id"] for g in grp]
        fs = [(pick(g, lambda x: x["topic"] == "outcome") or fbyu[g["user_id"]])[0] for g in grp]
        add(question="[compare] Compare the interview outcomes of "
                     + ", ".join(g["name"] for g in grp) + ".",
            query_type="cross_user_comparison", scope_type=f"{k}_user",
            scope_user_ids=ids, answerable=True, expected_user_ids=ids,
            expected_post_ids=[f["post_id"] for f in fs],
            expected_fact_ids=[f["fact_id"] for f in fs],
            expected_answer_facts=[f"{g['name']}: {g['outcome']}" for g in grp],
            required_fact_count=k, should_decompose=(k >= 3),
            expected_router_class="compound" if k >= 3 else "simple",
            expected_router_reason=("multiple_independent_retrieval_needs" if k >= 3
                                    else "single_entity_multi_attribute"),
            notes=f"{k}-user comparison; each user contributes one fact.")

    # --- D. compound / decomposition (35) — 2..6+ independent facts (§25) ---
    need_plan = [2] * 8 + [3] * 8 + [4] * 7 + [5] * 6 + [6] * 6      # 35
    for i, need in enumerate(need_plan):
        grp = [pool[(i * 3 + j) % len(pool)] for j in range(need)]
        seen, uniq = set(), []
        for g in grp:
            if g["user_id"] not in seen:
                seen.add(g["user_id"]); uniq.append(g)
        while len(uniq) < need:
            cand = pool[(i + len(uniq) * 5) % len(pool)]
            if cand["user_id"] not in seen:
                seen.add(cand["user_id"]); uniq.append(cand)
        fs = [fbyu[g["user_id"]][0] for g in uniq]
        add(question="[compound] " + " And ".join(
                f"what did {g['name']} report about {f['subject']}?"
                for g, f in zip(uniq, fs)),
            query_type="compound_decomposition", scope_type=f"{need}_user",
            scope_user_ids=[g["user_id"] for g in uniq], answerable=True,
            expected_user_ids=[g["user_id"] for g in uniq],
            expected_post_ids=[f["post_id"] for f in fs],
            expected_fact_ids=[f["fact_id"] for f in fs],
            expected_answer_facts=[f"{f['subject']} = {f['value']}" for f in fs],
            required_fact_count=need, should_decompose=True,
            expected_router_class="compound",
            expected_router_reason="multiple_independent_retrieval_needs",
            notes=("Requires %d independent facts against MAX_LLM_CONTEXT_CHUNKS=5. "
                   "%s" % (need, "BEYOND the context budget — expected to expose the "
                           "context cap, not retrieval, as the bottleneck."
                           if need >= 6 else "Within the context budget.")))

    # --- E. scope isolation (25) — the answer exists OUTSIDE the scope ------
    for i in range(25):
        target = js_A[i % len(js_A)]
        outsiders = [o for o in users
                     if o["category"] == "job_search"
                     and o["primary_company"] == target["primary_company"]
                     and o["cohort"] != "A"][:6]
        scope = [u for u in cohortA if u["category"] == "eng_notes"][:2]
        add(question=f"[scope] Within this selection, what was said about "
                     f"{target['primary_company']} interviews?",
            query_type="scope_isolation",
            scope_type=f"{len(scope)}_user",
            scope_user_ids=[s["user_id"] for s in scope],
            answerable=False, expected_user_ids=[], expected_post_ids=[],
            expected_fact_ids=[], expected_answer_facts=[],
            expected_citation_post_ids=[],
            forbidden_user_ids=[target["user_id"]] + [o["user_id"] for o in outsiders],
            forbidden_post_ids=[f["post_id"] for f in
                                (pick(target, lambda x: x["topic"] == "outcome") or [])],
            notes="Terminology exists outside the selected scope. Expected: zero "
                  "cross-scope evidence and an honest decline.",
            scale_stability="fixed_ground_truth")

    # --- F. exact-token / BM25 (15) — rare identifiers, confusable siblings --
    for i in range(15):
        # Adversarial users from every cohort, so exact-token difficulty
        # scales with the number of confusable siblings in the index.
        u = adv_all[i % len(adv_all)]
        idf = [f for f in fbyu[u["user_id"]] if f["rare_tokens"]]
        f = idf[i % len(idf)]
        tok = f["rare_tokens"][0]
        sibs = f["confusable_with"]
        bad = sorted({g["post_id"] for g in facts
                      if any(t in sibs for t in g["rare_tokens"])
                      and g["post_id"] != f["post_id"]})[:8]
        add(question=f"[exact] What does {tok} refer to?",
            query_type="exact_token_bm25", scope_type="global",
            scope_user_ids=[], answerable=True,
            expected_user_ids=[u["user_id"]], expected_post_ids=[f["post_id"]],
            expected_fact_ids=[f["fact_id"]],
            expected_answer_facts=[f"{tok} -> {f['value']}"],
            forbidden_post_ids=bad,
            notes=f"Exact rare token. Confusable siblings {sibs} must NOT be "
                  "returned. Separates BM25 exact matching from dense similarity.")

    # --- G. unanswerable (15) — the corpus deliberately lacks the fact ------
    missing = ["salary band at Amazon", "their manager's name",
               "the office postcode", "their GitHub password",
               "the exact interview panel size"]
    for i in range(15):
        u = pool[i % len(pool)]
        add(question=f"[unanswerable] What is {u['name']}'s "
                     f"{missing[i % len(missing)]}?",
            query_type="unanswerable", scope_type="single_user",
            scope_user_ids=[u["user_id"]], answerable=False,
            expected_user_ids=[], expected_post_ids=[], expected_fact_ids=[],
            expected_answer_facts=[], expected_citation_post_ids=[],
            notes="Corpus intentionally lacks this fact. Expected: honest decline, "
                  "no unsupported answer, no citation.")

    # --- H. temporal / update (15) — superseded vs latest -------------------
    tpool = [u for u in js_A]
    for i in range(15):
        u = tpool[i % len(tpool)]
        ch = [f for f in fbyu[u["user_id"]] if f["fact_type"] == "status_change"]
        if len(ch) < 2:
            ch = fbyu[u["user_id"]][:2]
        old, new = ch[0], ch[-1]
        latest = (i % 2 == 0)
        f = new if latest else old
        add(question=(f"[temporal] What is {u['name']} targeting now?" if latest
                      else f"[temporal] What was {u['name']} originally targeting?"),
            query_type="temporal_update", scope_type="single_user",
            scope_user_ids=[u["user_id"]], answerable=True,
            expected_user_ids=[u["user_id"]], expected_post_ids=[f["post_id"]],
            expected_fact_ids=[f["fact_id"]],
            expected_answer_facts=[f"{f['subject']} = {f['value']}"],
            forbidden_post_ids=[(old if latest else new)["post_id"]],
            notes=("Latest value must win over the superseded one." if latest
                   else "Superseded value must be retrievable as history."))
    return cases


def mark_scale_dependence(cases, users):
    """A case is fixed_ground_truth only when EVERY expected user is in Cohort A
    (so it is already valid at 25 users and cannot be re-answered by later
    cohorts). Anything else is scale_dependent and is scored separately."""
    cohort = {u["user_id"]: u["cohort"] for u in users}
    for c in cases:
        exp = c["expected_user_ids"]
        if exp and not all(cohort.get(x) == "A" for x in exp):
            c["scale_stability"] = "scale_dependent"
            latest = max(COHORTS.index(cohort[x]) for x in exp)
            c["minimum_cohort"] = COHORTS[latest]
            c["minimum_user_count"] = (latest + 1) * USERS_PER_COHORT
            c["notes"] = (c["notes"] + " SCALE-DEPENDENT: the authoritative source "
                          f"first exists at cohort {c['minimum_cohort']}; do not score "
                          "this case below that scale.").strip()
        # exact-token cases draw on adversarial users from any cohort
        if c["query_type"] == "exact_token_bm25" and exp:
            latest = max(COHORTS.index(cohort[x]) for x in exp)
            c["minimum_cohort"] = COHORTS[latest]
            c["minimum_user_count"] = (latest + 1) * USERS_PER_COHORT
    return cases


def main():
    os.makedirs(OUT, exist_ok=True)
    users = build_users()
    posts = build_posts(users)
    facts = build_facts(users, posts)
    posts = link_confusables(users, posts, facts)
    cases = mark_scale_dependence(build_eval(users, posts, facts), users)

    meta = {"spec_version": "v1", "seed": SEED, "synthetic": True,
            "data_classification": "synthetic stress-test corpus specification — "
                                   "design only, not real users, not seeded",
            "users": len(users), "posts": len(posts), "facts": len(facts),
            "eval_cases": len(cases)}
    for name, payload in [
        ("rag_stress_users_v1.json", {"meta": meta, "users": users}),
        ("rag_stress_posts_v1.json", {"meta": meta, "posts": posts}),
        ("rag_stress_facts_v1.json", {"meta": meta, "facts": facts}),
        ("rag_stress_eval_v1.json", {"meta": meta, "cases": cases}),
    ]:
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        print(f"  wrote {name}")

    import collections
    print(f"\n  users={len(users)} posts={len(posts)} facts={len(facts)} cases={len(cases)}")
    print(f"  size mix: {dict(collections.Counter(p['size_class'] for p in posts))}")
    print(f"  eval mix: {dict(collections.Counter(c['query_type'] for c in cases))}")
    print(f"  stability: {dict(collections.Counter(c['scale_stability'] for c in cases))}")


if __name__ == "__main__":
    main()
