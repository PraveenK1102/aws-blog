"""Fail-closed validator for a generated stress-corpus cohort (§23)."""
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
SPEC = os.path.join(REPO, "rag-stress-corpus")

FORBIDDEN_BODY = re.compile(
    r"(FACT-U\d|POST-ID|QUESTION-ID|GROUND TRUTH|EXPECTED ANSWER|EXPECTED SOURCE"
    r"|\[simple\]|\[compound\]|\[scope\]|\[temporal\]|\[exact\]|\[overlap\])", re.I)
SECRET = re.compile(r"(gsk_[A-Za-z0-9]{20,}|nvapi-|lsv2_|AKIA[0-9A-Z]{16}|-----BEGIN"
                    r"|password\s*[:=]\s*\S{6,})", re.I)
COMPOSITION = {"job_search": 10, "ai_ml_swe": 5, "travel_food": 3,
               "eng_notes": 3, "noise": 2, "adversarial": 2}


def main(cohort="A"):
    gen = os.path.join(SPEC, "generated", f"cohort-{cohort.lower()}")
    problems = []

    def check(c, m):
        if not c:
            problems.append(m)

    users = [u for u in json.load(open(f"{SPEC}/rag_stress_users_v1.json",
                                       encoding="utf-8"))["users"] if u["cohort"] == cohort]
    spec_posts = [p for p in json.load(open(f"{SPEC}/rag_stress_posts_v1.json",
                                            encoding="utf-8"))["posts"]
                  if p["user_id"] in {u["user_id"] for u in users}]
    spec_facts = [f for f in json.load(open(f"{SPEC}/rag_stress_facts_v1.json",
                                            encoding="utf-8"))["facts"]
                  if f["user_id"] in {u["user_id"] for u in users}]
    man = json.load(open(f"{gen}/manifest.json", encoding="utf-8"))["posts"]
    trace = json.load(open(f"{gen}/fact_trace.json", encoding="utf-8"))["traces"]

    check(len(users) == 25, f"users {len(users)} != 25")
    check(len(man) == 450, f"posts {len(man)} != 450")
    per_user = collections.Counter(p["user_id"] for p in man)
    for u in users:
        check(per_user[u["user_id"]] == 18,
              f"{u['user_id']}: {per_user[u['user_id']]} posts != 18")
    comp = collections.Counter(u["category"] for u in users)
    for k, n in COMPOSITION.items():
        check(comp[k] == n, f"composition {k}: {comp[k]} != {n}")

    ids = [p["post_id"] for p in man]
    check(len(set(ids)) == len(ids), "duplicate post_id")
    check(set(ids) == {p["post_id"] for p in spec_posts},
          "generated post ids differ from the structural spec")

    bodies = {}
    for p in man:
        path = os.path.join(gen, p["path"])
        check(os.path.exists(path), f"{p['post_id']}: missing file")
        if not os.path.exists(path):
            continue
        body = open(path, encoding="utf-8").read()
        bodies[p["post_id"]] = body
        check(not FORBIDDEN_BODY.search(body), f"{p['post_id']}: test marker in body")
        check(not SECRET.search(body), f"{p['post_id']}: possible credential in body")
        lo, hi = p["target_word_range"]
        w = len(body.split())
        check(lo <= w <= hi, f"{p['post_id']}: {w} words outside {lo}-{hi}")
        check(bool(p["title"]), f"{p['post_id']}: no title")
        check(bool(p["date"]), f"{p['post_id']}: no date")
        check(bool(p["tags"]), f"{p['post_id']}: no tags")

    # every structural fact traced to the RIGHT post, evidence verbatim
    tmap = {t["fact_id"]: t for t in trace}
    check(len(tmap) == len(spec_facts),
          f"traced {len(tmap)} facts != {len(spec_facts)} expected")
    owner = {p["post_id"]: p["user_id"] for p in man}
    for f in spec_facts:
        t = tmap.get(f["fact_id"])
        if not t:
            problems.append(f"{f['fact_id']}: not traced")
            continue
        check(t["post_id"] == f["post_id"], f"{f['fact_id']}: wrong post")
        check(t["user_id"] == f["user_id"], f"{f['fact_id']}: wrong user")
        check(owner.get(t["post_id"]) == f["user_id"],
              f"{f['fact_id']}: evidence sits in another user's post")
        body = bodies.get(t["post_id"], "")
        check(t["evidence_excerpt"] in body,
              f"{f['fact_id']}: evidence excerpt not verbatim in post")
        # rare identifiers and numeric values must survive exactly
        for tok in f.get("rare_tokens", []):
            check(tok in body, f"{f['fact_id']}: rare token {tok} missing from body")
        val = str(f["value"])
        check(val in t["evidence_excerpt"], f"{f['fact_id']}: value {val!r} not in evidence")

    # unexpected exact-duplicate bodies (noise may legitimately repeat)
    seen = collections.defaultdict(list)
    for p in man:
        seen[p["body_sha256"]].append(p)
    for h, ps in seen.items():
        if len(ps) > 1 and any(x["category"] != "noise" for x in ps):
            problems.append(f"unexpected duplicate body across {[x['post_id'] for x in ps]}")

    # generation must never have consumed the evaluation set
    src = open(os.path.join(HERE, "generate_rag_stress_corpus.py"), encoding="utf-8").read()
    check("rag_stress_eval" not in src, "generator references the evaluation set")

    print(f"  cohort={cohort} users={len(users)} posts={len(man)} facts_traced={len(tmap)}")
    print(f"  problems={len(problems)}")
    for p in problems[:25]:
        print(f"    - {p}")
    print(f"  RESULT: {'PASS' if not problems else 'FAIL'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "A"))
