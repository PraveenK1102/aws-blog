"""Deterministic prose generator for the RAG stress corpus.

ANTI-LEAKAGE INVARIANT (§6): this module MUST NOT read the evaluation set. Its
only inputs are the user, post and fact manifests. Posts are therefore written
around structured facts, never around known questions. `test_corpus_generator.py`
asserts this by AST — no import, no filename, no read of the eval JSON.

FACT FIDELITY (§5/§10): every evaluation-relevant fact is rendered into exactly
one sentence that contains its `value` VERBATIM. Numbers, dates, percentages,
company names and confusable identifiers (INC-731 vs INC-713, QL-2C vs QL-2D…)
are emitted exactly as the manifest holds them and are never normalised. That
sentence is recorded in `fact_trace.json` as the verbatim evidence excerpt.

NO TEST MARKERS (§9): fact ids, post ids, question ids and evaluation labels
never appear in a post body. Metadata lives in the sidecar manifest.

Deterministic: seed 20260827, per-post RNG, no timestamps in output.
"""
import argparse
import hashlib
import json
import os
import random
import re

SEED = 20260827
HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.abspath(os.path.join(HERE, "..", "..", "rag-stress-corpus"))
OUT_ROOT = os.path.join(SPEC, "generated")

# Bodies must never contain these — asserted by the validator too.
FORBIDDEN_IN_BODY = re.compile(
    r"(FACT-U\d|POST-ID|QUESTION-ID|GROUND TRUTH|EXPECTED ANSWER|EXPECTED SOURCE"
    r"|\[simple\]|\[compound\]|\[scope\]|\[temporal\]|\[exact\]|\[overlap\]"
    r"|\[unanswerable\]|\[compare\])", re.I)


def rng(*parts):
    return random.Random(f"{SEED}|" + "|".join(str(p) for p in parts))


# --------------------------------------------------------------- section plans
# Headings matter: the production chunker is header-aware, so section structure
# is what produces multiple chunks per post rather than one long blob.
SECTIONS = {
    "job_search": ["Where things stand", "How I prepared", "The round itself",
                   "What I took from it"],
    "ai_ml_swe": ["The problem", "What I built", "What I measured",
                  "What I would change"],
    "travel_food": ["Getting there", "The place itself", "What I ate",
                    "Notes for next time"],
    "eng_notes": ["The problem", "The design", "Implementation notes",
                  "What I measured", "The lesson"],
    "adversarial": ["Context", "The reference", "Follow-up"],
    "noise": [],
}

OPENERS = {
    "job_search": [
        "Another week of applications, and a few things worth writing down.",
        "Keeping this log honest is the only way I can see my own progress.",
        "A short entry today, mostly so future me remembers the reasoning.",
        "I nearly skipped writing this one, which usually means it is worth writing.",
    ],
    "ai_ml_swe": [
        "Spent most of this week on the retrieval side of the project.",
        "Notes from a few days of experiments, written while they are still fresh.",
        "This started as a small change and turned into a proper investigation.",
        "Writing this up mostly to force myself to be precise about the numbers.",
    ],
    "travel_food": [
        "A short trip, and a long list of things I want to remember.",
        "Went back to a place I had not visited in years.",
        "This was meant to be a quiet weekend and turned into a food crawl.",
        "Notes from a day of walking more than I planned.",
    ],
    "eng_notes": [
        "A note to myself, because I will absolutely forget the details.",
        "This came out of a production incident, so the details matter.",
        "Writing down what actually happened rather than what I assumed happened.",
        "Short engineering note, mostly so the next person does not repeat it.",
    ],
    "adversarial": [
        "Filing this so the reference is somewhere I can find it again.",
        "A record of what was actually decided, since the naming is confusing.",
        "Keeping this note short and specific on purpose.",
        "Writing the identifiers down carefully because they are easy to mix up.",
    ],
    "noise": [
        "Testing the editor.", "Quick note to self.", "Nothing important today.",
        "Trying out formatting.", "Placeholder post.", "Just checking this works.",
    ],
}

FILLER = {
    "job_search": [
        "The hardest part is not the technical preparation, it is deciding what to leave out of an answer.",
        "I have started recording myself explaining a project out loud, and the gaps are obvious immediately.",
        "Applying broadly felt productive for a while, but the replies got noticeably better once I narrowed the target.",
        "A recruiter told me the first thirty seconds of a project explanation decide the rest of the conversation.",
        "I keep a short list of the questions that made me hesitate, and revisit it before every round.",
        "Reading other people's write-ups helps, though it is easy to mistake their confidence for my own readiness.",
        "The gap between knowing something and explaining it under time pressure is larger than I expected.",
        "I have stopped treating rejection as a verdict and started treating it as a list of specific gaps.",
        "Scheduling practice at the same time each morning removed most of the friction of starting.",
        "There is a temptation to rewrite the whole resume after every rejection, which I am trying to resist.",
    ],
    "ai_ml_swe": [
        "The first version worked on the sample data and fell apart the moment the inputs got messy.",
        "Most of the time went into building a way to measure the change rather than making the change.",
        "I learned to distrust any improvement that only shows up on the examples I chose myself.",
        "Splitting the pipeline into separately testable steps made the failures much easier to localise.",
        "Latency turned out to be dominated by a step I had assumed was cheap.",
        "Writing the evaluation harness before the optimisation saved me from at least two false wins.",
        "The interesting failures were the ones where the output looked plausible but cited the wrong source.",
        "I kept a log of every configuration I tried, which felt tedious until I needed to explain a regression.",
        "Batching helped throughput but made the tail latency harder to reason about.",
        "A surprising amount of the gain came from cleaning the inputs rather than changing the model.",
    ],
    "travel_food": [
        "The streets were quieter than I expected for that hour, which made the walk pleasant.",
        "I had planned an itinerary and abandoned it within an hour, which was the right call.",
        "Prices and timings change often enough that I would check again before relying on them.",
        "The queue moved faster than it looked, and the wait was worth it.",
        "Getting there early meant the place was still calm and the staff had time to talk.",
        "There is a particular kind of tiredness that comes from a good day of walking.",
        "I took the slower route back deliberately, and saw a whole street I had missed.",
        "Local advice was better than anything I had bookmarked in advance.",
        "The weather turned halfway through, which rearranged the afternoon entirely.",
        "I would go back, though probably on a weekday and earlier in the morning.",
    ],
    "eng_notes": [
        "The failure was intermittent, which meant every fix looked like it worked for a while.",
        "Adding a metric before adding a fix turned out to be the only reason we caught the regression.",
        "The obvious cause was not the real cause, and the logs said so if you read them carefully.",
        "Retries made the symptom disappear and the underlying problem worse.",
        "Idempotency turned out to matter far more than throughput in this path.",
        "We spent longer agreeing on what 'done' meant than on the implementation itself.",
        "A timeout that is longer than the caller's own timeout is not a timeout, it is a leak.",
        "Documenting the failure mode was more useful than documenting the happy path.",
        "The rollback plan was the part we had not rehearsed, which is always the part that matters.",
        "Small, boring changes with a measurement attached beat clever changes without one.",
    ],
    "adversarial": [
        "The naming convention is close enough between components that a careless copy causes real confusion.",
        "I have started quoting the identifier exactly rather than paraphrasing it in messages.",
        "Two of these look almost identical in a terminal, which has caused at least one wrong lookup.",
        "Keeping the reference and the description in the same place has saved time more than once.",
        "The suffix is the only thing distinguishing them, so it is worth reading twice.",
        "Search does not always help here, because the near-matches rank alongside the real one.",
        "I keep a short table of these so I do not have to trust my memory.",
        "Whenever a ticket references one of these I copy it verbatim rather than retyping.",
    ],
    "noise": [
        "Nothing much to report.", "Might delete this later.",
        "Random test words: mango, velvet bicycle, paper cup, green gate, number 731.",
        "Checking whether lists render.", "Weather was fine.",
        "Reminder to buy coffee.", "This is a short one.",
    ],
}


# ------------------------------------------------------------- fact rendering
def render_fact(f, user, r):
    """One sentence carrying the fact's VALUE verbatim.

    The returned string becomes both the prose and the fact_trace evidence
    excerpt, so the two can never drift apart.
    """
    ft, subj, val = f["fact_type"], f["subject"], str(f["value"])
    name = user["name"].split()[0]
    if ft == "event_outcome":
        if f["predicate"] == "interview_outcome":
            return r.choice([
                f"The {subj} process finished with one clear result: {val}.",
                f"After everything, the outcome at {subj} was {val}.",
                f"I can finally record the {subj} result — it was {val}.",
            ])
        return r.choice([
            f"The {subj} round came back as {val}, which I had half expected.",
            f"For {subj}, that round ended up {val}.",
            f"My {subj} round result was {val}.",
        ])
    if ft == "status_change":
        when = f["date"]
        return r.choice([
            f"As of {when} I changed my {subj.replace('_', ' ')} to {val}.",
            f"On {when} the {subj.replace('_', ' ')} became {val}.",
            f"From {when} onward my {subj.replace('_', ' ')} is {val}.",
        ])
    if ft == "measurement":
        return r.choice([
            f"Measured properly, {subj} came out at {val}.",
            f"The number I got for {subj} was {val}.",
            f"{subj.capitalize()} landed at {val} once I measured it end to end.",
        ])
    if ft == "preference":
        return r.choice([
            f"The part of my stack I lean on most is {val}.",
            f"Most of this work sits on {val}.",
            f"I keep coming back to {val} for this.",
        ])
    if ft == "location_visit":
        if f["predicate"] == "mentioned_without_visiting":
            return r.choice([
                f"People kept recommending {subj}, but I did not make it there this time — {val}.",
                f"{subj} came up more than once in conversation, though I never went in ({val}).",
            ])
        return r.choice([
            f"I went to {subj} specifically for the {val}.",
            f"{subj} was the stop I had planned, and the {val} was the reason.",
            f"At {subj} I ordered the {val} and it lived up to the recommendation.",
        ])
    if ft == "identifier":
        return r.choice([
            f"For the record, {subj} refers to the {val}.",
            f"{subj} is the {val} — worth writing down precisely.",
            f"When someone says {subj}, they mean the {val}.",
        ])
    return f"{name} noted that {subj} was {val}."


# Combinatorial fragments. Cross-producing these yields hundreds of distinct
# sentences per category, so reaching a 1,200-word target never requires
# repeating a sentence — which is the padding §14 forbids.
FRAG = {
    "job_search": {
        "lead": ["I spent part of the week", "Most of my evenings went on",
                 "A good chunk of my time went into", "I keep returning to",
                 "This week I focused on", "Somewhat reluctantly I started"],
        "act": ["rewriting the same project summary", "practising a system-design answer out loud",
                "working through problems I had previously skipped", "revisiting fundamentals I assumed I knew",
                "tightening the story around my main project", "mapping one request end to end",
                "drilling the questions that made me hesitate", "reading through other people's write-ups"],
        "tail": ["and the difference showed almost immediately.", "though progress is slower than I would like.",
                 "which was less comfortable than it sounds.", "and I have no regrets about the time spent.",
                 "even if the payoff is not obvious yet.", "because the alternative is guessing.",
                 "and it changed how I answer the follow-up.", "which exposed a gap I had been avoiding."],
    },
    "ai_ml_swe": {
        "lead": ["The experiment started with", "I began by isolating", "Before changing anything I measured",
                 "The interesting part turned out to be", "Most of the effort went into", "I rebuilt"],
        "act": ["the retrieval step on its own", "a small evaluation harness", "the chunking behaviour",
                "the ranking of candidate passages", "the way inputs were normalised",
                "the slowest stage in the pipeline", "the fallback path nobody exercised",
                "the assumptions baked into the defaults"],
        "tail": ["and the numbers were not what I expected.", "which made the regression obvious.",
                 "before touching anything else.", "and that alone explained most of the gap.",
                 "so the comparison would actually mean something.", "which took longer than the fix itself.",
                 "and I kept the harness afterwards.", "because the earlier result was not reproducible."],
    },
    "travel_food": {
        "lead": ["The morning began with", "We ended up at", "I had planned to skip",
                 "A local suggested", "The detour led to", "Halfway through the day we found"],
        "act": ["a long walk along the shore", "a crowded tiffin counter", "a quiet street of small shops",
                "an early filter coffee", "a temple courtyard in the shade", "a bakery with no signage",
                "a stretch of road I had never taken", "a market that was winding down"],
        "tail": ["and it reset the whole day.", "which turned out to be the highlight.",
                 "though the queue was longer than expected.", "and I stayed longer than planned.",
                 "before the heat made walking unpleasant.", "which is worth the detour.",
                 "and I would happily repeat it.", "even though I nearly walked past it."],
    },
    "eng_notes": {
        "lead": ["The failure showed up as", "We eventually traced it to", "The first hypothesis was",
                 "Instrumentation revealed", "The fix came down to", "What actually broke was"],
        "act": ["a slow query behind a cache miss", "a retry storm masking the real error",
                "an index that no longer matched the access pattern", "a timeout longer than the caller's",
                "a queue consumer falling behind quietly", "a connection pool exhausted at peak",
                "a migration that had never been rehearsed", "a metric that averaged away the tail"],
        "tail": ["and it only appeared under load.", "which explained the intermittency.",
                 "so the graphs looked healthy throughout.", "and the logs had said so all along.",
                 "once we stopped trusting the average.", "which is why the rollback mattered.",
                 "and it has not recurred since.", "though the fix was less interesting than the diagnosis."],
    },
    "adversarial": {
        "lead": ["For clarity,", "Recording this precisely:", "To avoid another mix-up,",
                 "Noting it here because", "Worth writing down:", "A short reminder that"],
        "act": ["the suffix is the only distinguishing part", "these two sit next to each other in the register",
                "the reference is easy to mistype", "search returns the near-matches first",
                "the naming predates the current convention", "the label appears in two different systems"],
        "tail": ["so I copy it verbatim every time.", "and a careless paste has caused a wrong lookup before.",
                 "which is why I keep a table.", "and I no longer trust my memory for it.",
                 "so it is quoted exactly in tickets.", "which matters more than it sounds."],
    },
    # Noise stays low-signal and casual, but needs enough combinatorial range to
    # reach a 500-800 word target without repeating a sentence three times. The
    # single designed boilerplate line ("Random test words: … velvet bicycle …")
    # is kept deliberately as the accepted duplicate/exact-token noise fixture.
    "noise": {
        "lead": ["Woke up late", "Ended up walking to the shop", "Spent the evening",
                 "Forgot to charge the laptop", "Tried the new place near the office",
                 "Sat outside for a while", "Started a list", "Rearranged the desk"],
        "act": ["and did very little else", "because the weather was decent",
                "instead of doing anything useful", "which took longer than it should have",
                "and lost track of time", "while the laundry finished",
                "and then changed my mind", "before it started raining",
                "with the radio on", "and forgot what I went for"],
        "tail": ["today.", "again.", "for no particular reason.", "which was fine.",
                 "and that was the whole day.", "more or less."],
    },
}


def filler_pool(cat, r, n):
    """Deterministic, de-duplicated combinatorial sentences."""
    fr = FRAG[cat]
    seen, out = set(), []
    guard = 0
    while len(out) < n and guard < n * 40:
        guard += 1
        s = " ".join(x for x in (r.choice(fr["lead"]), r.choice(fr["act"]),
                                 r.choice(fr["tail"])) if x).strip()
        if not s.endswith((".", "!", "?")):
            s += "."
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def words(text):
    return len(text.split())


def build_post(user, post, facts, r):
    """Assemble one Markdown body plus its fact evidence map."""
    cat = post["category"]
    lo, hi = post["target_word_range"]
    # Aim for a varied point inside the band rather than its floor, so lengths
    # spread realistically and chunk density is not artificially suppressed.
    target = int(lo + r.uniform(0.20, 0.85) * (hi - lo))
    title = make_title(user, post, r)
    evidence = {}

    if cat == "noise":
        parts = [r.choice(OPENERS["noise"])]
        # A long noise post needs ~100 sentences; 40 would force repetition.
        pool = FILLER["noise"] + filler_pool("noise", r, 300)
        r.shuffle(pool)
        i = 0
        while words("\n\n".join(parts)) < target and i < len(pool) * 4:
            parts.append(pool[i % len(pool)])
            i += 1
        body = "\n\n".join(parts)
        return title, _trim(body, hi), evidence

    heads = SECTIONS[cat]
    # Spread facts across sections so evidence is not clustered in one chunk.
    #
    # The structural spec can assign a post several facts that are the SAME
    # assertion (e.g. four `interview_outcome` facts with identical subject and
    # value). Writing that sentence once per fact would be padding, and would
    # also make traceability ambiguous — four ids pointing at four identical
    # sentences. Identical assertions are therefore rendered ONCE and every
    # matching fact id maps to that single occurrence, which is the truthful
    # relationship: the same evidence supports all of them. No fact is dropped.
    buckets = {h: [] for h in heads}
    rendered = {}
    slot = 0
    for f in facts:
        key = (f["fact_type"], str(f["subject"]), str(f["predicate"]), str(f["value"]))
        if key in rendered:
            evidence[f["fact_id"]] = rendered[key]
            continue
        sent = render_fact(f, user, r)
        # A different fact must never collide onto an existing sentence.
        tries = 0
        while sent in rendered.values() and tries < 6:
            sent = render_fact(f, user, r)
            tries += 1
        rendered[key] = sent
        buckets[heads[slot % len(heads)]].append(sent)
        slot += 1
        evidence[f["fact_id"]] = sent

    parts = [r.choice(OPENERS[cat])]
    pool = list(FILLER[cat]) + filler_pool(cat, r, 260)
    r.shuffle(pool)
    fi = 0
    for h in heads:
        parts.append(f"## {h}")
        seg = list(buckets[h])
        # two filler sentences per section keeps prose natural without padding
        for _ in range(2):
            seg.append(pool[fi % len(pool)])
            fi += 1
        r.shuffle(seg)
        parts.append(" ".join(seg))

    body = "\n\n".join(parts)
    # Grow toward the target with contextual sentences, never empty repetition.
    guard = 0
    while words(body) < target and guard < 600:
        h = heads[guard % len(heads)]
        extra = pool[fi % len(pool)]
        fi += 1
        body = _append_to_section(body, h, extra)
        guard += 1
    body = _trim(body, hi)

    # Any fact sentence lost to trimming must be restored — fidelity beats length.
    for fid, sent in evidence.items():
        if sent not in body:
            body = body.rstrip() + "\n\n" + sent
    return title, body, evidence


def _append_to_section(body, head, sentence):
    marker = f"## {head}"
    if marker not in body:
        return body.rstrip() + "\n\n" + sentence
    i = body.index(marker) + len(marker)
    j = body.find("\n## ", i)
    if j == -1:
        return body.rstrip() + " " + sentence
    return body[:j].rstrip() + " " + sentence + body[j:]


def _trim(body, hi):
    """Trim to the upper word bound on paragraph boundaries."""
    if words(body) <= hi:
        return body
    out = []
    total = 0
    for para in body.split("\n\n"):
        w = words(para)
        if total + w > hi and out:
            break
        out.append(para)
        total += w
    return "\n\n".join(out)


TITLE_BANK = {
    "job_search": ["Narrowing the target", "A week of applications", "What the last round taught me",
                   "Rewriting the same paragraph", "Preparation, honestly assessed",
                   "One round closer", "Notes after a rejection", "Changing direction",
                   "The question I keep fumbling", "Small progress, recorded"],
    "ai_ml_swe": ["Measuring before optimising", "A retrieval experiment", "Where the latency went",
                  "Building the harness first", "An honest look at the numbers",
                  "What the evaluation missed", "Chunking, revisited", "The failure I did not expect"],
    "travel_food": ["A morning walk", "Back to an old favourite", "The long way round",
                    "Breakfast, properly", "A quieter afternoon", "Notes from the coast",
                    "What I would do differently", "An unplanned detour"],
    "eng_notes": ["A note on retries", "Where the timeout leaked", "Indexes and assumptions",
                  "The incident, written down", "Caching, carefully", "What the metric hid",
                  "Idempotency in practice", "A boring fix that worked"],
    "adversarial": ["Recording the reference", "Two identifiers, one letter apart",
                    "Keeping the naming straight", "A short lookup note",
                    "Why I quote these exactly", "The component register"],
    "noise": ["First post test", "Formatting test", "Quick note", "Testing",
              "Random thoughts", "Status update"],
}


def make_title(user, post, r):
    bank = TITLE_BANK[post["category"]]
    return f"{bank[(post['sequence'] - 1) % len(bank)]}"


# ------------------------------------------------------------------- driver
def load_spec():
    def L(n):
        with open(os.path.join(SPEC, n), encoding="utf-8") as fh:
            return json.load(fh)
    return (L("rag_stress_users_v1.json")["users"],
            L("rag_stress_posts_v1.json")["posts"],
            L("rag_stress_facts_v1.json")["facts"])


def generate(cohort="A", out_dir=None, users_slice=None):
    users_all, posts_all, facts_all = load_spec()
    users = [u for u in users_all if u["cohort"] == cohort]
    if users_slice:
        users = [u for u in users if u["user_id"] in users_slice]
    uids = {u["user_id"] for u in users}
    posts = [p for p in posts_all if p["user_id"] in uids]
    facts_by_post = {}
    for f in facts_all:
        if f["user_id"] in uids:
            facts_by_post.setdefault(f["post_id"], []).append(f)

    out = out_dir or os.path.join(OUT_ROOT, f"cohort-{cohort.lower()}")
    os.makedirs(os.path.join(out, "users"), exist_ok=True)

    manifest, trace = [], []
    umap = {u["user_id"]: u for u in users}
    for p in sorted(posts, key=lambda x: (x["user_id"], x["sequence"])):
        u = umap[p["user_id"]]
        r = rng("prose", p["post_id"])
        pf = sorted(facts_by_post.get(p["post_id"], []), key=lambda f: f["fact_id"])
        title, body, evidence = build_post(u, p, pf, r)

        if FORBIDDEN_IN_BODY.search(body):
            raise SystemExit(f"{p['post_id']}: test marker leaked into body")

        d = os.path.join(out, "users", u["user_id"])
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"P{p['sequence']:02d}.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body if body.endswith("\n") else body + "\n")

        manifest.append({
            "post_id": p["post_id"], "user_id": u["user_id"],
            "username": u["username"], "category": p["category"],
            "title": title, "date": p["date"], "topic": p["topic"],
            "tags": p["tags"], "size_class": p["size_class"],
            "target_word_range": p["target_word_range"],
            "actual_word_count": words(body),
            "path": os.path.relpath(path, out).replace("\\", "/"),
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "fact_ids": sorted(evidence),
        })
        for fid, sent in sorted(evidence.items()):
            trace.append({
                "fact_id": fid, "post_id": p["post_id"], "user_id": u["user_id"],
                "evidence_excerpt": sent,
                "evidence_sha256": hashlib.sha256(sent.encode("utf-8")).hexdigest(),
                "char_offset": body.index(sent) if sent in body else -1,
            })
    return out, manifest, trace


def write_sidecars(out, manifest, trace, cohort):
    meta = {"cohort": cohort, "seed": SEED, "synthetic": True,
            "data_classification": "synthetic RAG benchmark corpus — offline, not ingested",
            "posts": len(manifest), "facts_traced": len(trace)}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"meta": meta, "posts": manifest}, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(out, "fact_trace.json"), "w", encoding="utf-8") as fh:
        json.dump({"meta": meta, "traces": trace}, fh, indent=2, ensure_ascii=False)


def corpus_fingerprint(out):
    """Order-independent hash over every generated body."""
    h = hashlib.sha256()
    for root, _, files in os.walk(os.path.join(out, "users")):
        for fn in sorted(files):
            if fn.endswith(".md"):
                p = os.path.join(root, fn)
                h.update(os.path.relpath(p, out).replace("\\", "/").encode())
                with open(p, "rb") as fh:
                    h.update(fh.read())
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description="Generate stress-corpus prose (offline).")
    ap.add_argument("--cohort", default="A")
    ap.add_argument("--out", default=None)
    ap.add_argument("--users", default=None, help="comma-separated user ids (batch mode)")
    a = ap.parse_args()
    sl = set(a.users.split(",")) if a.users else None
    out, manifest, trace = generate(a.cohort, a.out, sl)
    write_sidecars(out, manifest, trace, a.cohort)
    wc = [m["actual_word_count"] for m in manifest]
    print(f"  cohort {a.cohort}: {len(manifest)} posts -> {out}")
    print(f"  words min={min(wc)} mean={sum(wc)//len(wc)} max={max(wc)}")
    print(f"  facts traced: {len(trace)}")
    print(f"  fingerprint: {corpus_fingerprint(out)[:32]}")


if __name__ == "__main__":
    main()
