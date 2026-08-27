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
import collections
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
        "Writing this before the details blur into the previous round.",
        "Short update, mostly for my own record.",
    ],
    "ai_ml_swe": [
        "Spent most of this week on the retrieval side of the project.",
        "Notes from a few days of experiments, written while they are still fresh.",
        "This started as a small change and turned into a proper investigation.",
        "Writing this up mostly to force myself to be precise about the numbers.",
        "A short write-up while the results are still reproducible.",
    ],
    "travel_food": [
        "A short trip, and a long list of things I want to remember.",
        "Went back to a place I had not visited in years.",
        "This was meant to be a quiet weekend and turned into a food crawl.",
        "Notes from a day of walking more than I planned.",
        "Half a day out, written up before I forget the details.",
    ],
    "eng_notes": [
        "A note to myself, because I will absolutely forget the details.",
        "This came out of a production incident, so the details matter.",
        "Writing down what actually happened rather than what I assumed happened.",
        "Short engineering note, mostly so the next person does not repeat it.",
        "Filed here so the postmortem has something to point at.",
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
def render_fact(f, user, r, seen4=None):
    """One sentence carrying the fact's VALUE verbatim.

    The returned string becomes both the prose and the fact_trace evidence
    excerpt, so the two can never drift apart.
    """
    ft, subj, val = f["fact_type"], f["subject"], str(f["value"])
    name = user["name"].split()[0]

    def pick(cands):
        """Prefer a phrasing whose opening is not already spent in this post.

        A post often carries several facts of the SAME type, and a blind choice
        made all of them open identically ("The number I got for ..." x3). The
        value itself is untouched — only the sentence around it varies.
        """
        if seen4 is None:
            return r.choice(cands)
        fresh = [c for c in cands if seen4[norm_open(c, 4)] == 0]
        return r.choice(fresh) if fresh else min(
            cands, key=lambda c: (seen4[norm_open(c, 4)], cands.index(c)))
    if ft == "event_outcome":
        if f["predicate"] == "interview_outcome":
            return pick([
                f"The {subj} process finished with one clear result: {val}.",
                f"After everything, the outcome at {subj} was {val}.",
                f"I can finally record the {subj} result — it was {val}.",
            ])
        return pick([
            f"The {subj} round came back as {val}, which I had half expected.",
            f"For {subj}, that round ended up {val}.",
            f"My {subj} round result was {val}.",
        ])
    if ft == "status_change":
        when = f["date"]
        return pick([
            f"As of {when} I changed my {subj.replace('_', ' ')} to {val}.",
            f"On {when} the {subj.replace('_', ' ')} became {val}.",
            f"From {when} onward my {subj.replace('_', ' ')} is {val}.",
        ])
    if ft == "measurement":
        # A post can carry more measurement facts than a three-way bank can
        # open distinctly, so this bank is deliberately wider.
        return pick([
            f"Measured properly, {subj} came out at {val}.",
            f"The number I got for {subj} was {val}.",
            f"{subj.capitalize()} landed at {val} once I measured it end to end.",
            f"When I finally measured it, {subj} was {val}.",
            f"My recorded figure for {subj} is {val}.",
            f"Running it end to end put {subj} at {val}.",
            f"That gave {subj} of {val}, which I wrote down at the time.",
            f"On the instrumented run, {subj} showed {val}.",
        ])
    if ft == "preference":
        return pick([
            f"The part of my stack I lean on most is {val}.",
            f"Most of this work sits on {val}.",
            f"I keep coming back to {val} for this.",
        ])
    if ft == "location_visit":
        if f["predicate"] == "mentioned_without_visiting":
            return pick([
                f"People kept recommending {subj}, but I did not make it there this time — {val}.",
                f"{subj} came up more than once in conversation, though I never went in ({val}).",
            ])
        return pick([
            f"I went to {subj} specifically for the {val}.",
            f"{subj} was the stop I had planned, and the {val} was the reason.",
            f"At {subj} I ordered the {val} and it lived up to the recommendation.",
        ])
    if ft == "identifier":
        return pick([
            f"For the record, {subj} refers to the {val}.",
            f"{subj} is the {val} — worth writing down precisely.",
            f"When someone says {subj}, they mean the {val}.",
        ])
    return f"{name} noted that {subj} was {val}."


# ---------------------------------------------------------------- prose engine
# WHY THIS REPLACED THE ORIGINAL FRAGMENT BANK
# Cohort A v1 built every sentence as lead + action + tail from six "lead"
# fragments, so in a long post each lead recurred ~12 times ("The morning began
# with" x20). Unrealistic lexical repetition distorts BM25 ranking and dense
# similarity, which would distort the benchmark itself.
#
# Two changes fix that properly rather than by swapping one repeated phrase for
# another:
#   1. Sentences are CONTEXT-AWARE. They draw on the persona's own anchors —
#      their company, role, tech stack, place, venue — so two users writing about
#      the same topic produce genuinely different text instead of cycling one
#      shared vocabulary.
#   2. Sentences are built from distinct grammatical CONSTRUCTIONS (declarative,
#      contrast, cause/effect, result-first, question, retrospective, compound),
#      and assembly enforces a hard opening-prefix budget (§8) across the whole
#      post, fact sentences included. The old failure mode is now structurally
#      impossible, not merely unlikely.
# Sentences are also longer and multi-clause, so a 1,100-word post needs ~55
# sentences rather than ~120 — less filler, more coherence.

TOPIC_NOUN = {
    "job_search": ["the system-design round", "the coding round", "the recruiter screen",
                   "the take-home", "my project write-up", "the behavioural round",
                   "the panel discussion", "my resume", "the follow-up thread"],
    "ai_ml_swe": ["the retrieval step", "the evaluation harness", "the chunking logic",
                  "the reranking pass", "the ingestion path", "the embedding cache",
                  "the fallback branch", "the scoring function"],
    "travel_food": ["the walk from the station", "the queue outside", "the early light",
                    "the side street", "the counter", "the ride back", "the courtyard"],
    "eng_notes": ["the connection pool", "the retry path", "the cache layer",
                  "the migration", "the queue consumer", "the timeout budget", "the index"],
    "adversarial": ["the register entry", "the ticket reference", "the component label",
                    "the lookup table", "the changelog line"],
    "noise": ["the kettle", "the laundry", "the shopping list", "the desk", "the radio"],
}


def ctx(user, post, r):
    """Per-persona anchors, so content differs between users writing on one topic."""
    tech = user.get("tech") or ["Python"]
    return {
        "co": user.get("primary_company") or "the company",
        "role": user.get("role") or "the role",
        # t1/t2 fill contrasting slots ("depth in {t1} vs breadth across {t2}"),
        # so they must never collide or the sentence stops making sense.
        **dict(zip(("t1", "t2"),
                   r.sample(tech, 2) if len(tech) > 1 else (tech[0], tech[0]))),
        "place": user.get("place") or "the coast",
        "venue": user.get("venue") or "the tiffin place",
        "noun": r.choice(TOPIC_NOUN[post["category"]]),
        "month": {"03": "March", "04": "April", "05": "May",
                  "06": "June", "07": "July", "08": "August"}[post["date"][5:7]],
    }


PATTERNS = {
    "job_search": [
        "I spent most of {month} on {t1}, and it changed how I answer questions about {noun}.",
        "{noun} at {co} was harder than the practice problems suggested, mainly because I had rehearsed the happy path.",
        "Because I could not explain my {t1} choices cleanly, I rewrote that section of my resume twice.",
        "Preparing for {co} meant choosing between depth in {t1} and breadth across {t2}; I chose depth.",
        "Something I keep getting wrong: I describe what I built before explaining why it needed building.",
        "Did the {role} framing help? Slightly — recruiters asked better questions once I stopped hedging.",
        "Looking back at {month}, the useful hours were the ones I spent explaining {t2} out loud.",
        "There is a difference between knowing {t1} and defending a decision about {t1} under time pressure.",
        "After {noun}, I wrote down the two moments where my answer thinned out rather than rewriting everything.",
        "The {co} process moved faster than I expected, which left less room to recover from a weak answer.",
        "I have started treating each rejection as a list of gaps instead of a verdict on the {role} track.",
        "My preparation now splits evenly between {t1} fundamentals and talking through the project end to end.",
        "Two rounds in, the pattern is clear: I over-prepare for {t1} and under-prepare for follow-ups.",
        "A recruiter told me the first thirty seconds of a project explanation decide the rest of the conversation.",
        "I removed the tool list from my resume and replaced it with three decisions I could defend.",
        "Practising with a timer changed my answers more than any amount of reading about {t2} did.",
        "The gap between {month} and now is mostly confidence rather than knowledge.",
        "Narrowing to {role} roles felt like giving something up until the replies started improving.",
        "One question about {t1} exposed that I had memorised the shape of an answer without the reasoning.",
        "Scheduling practice at the same hour each morning removed most of the friction of starting.",
        "The prep that actually moved the needle was explaining one design decision without notes.",
        "I stopped applying broadly in {month}; the reply rate went up almost immediately.",
        "Reading other people's interview write-ups helps, though their confidence is easy to mistake for my own readiness.",
        "When they asked why {t1} and not something simpler, I did not have a crisp answer ready.",
        "Rejection stings less when it arrives with a specific reason attached, and this one did.",
        "Half my notes from {month} are about communication rather than anything technical.",
        "I rehearsed the {t2} portion so heavily that the unscripted questions caught me flat.",
        "The strongest answer I gave all week was the one where I admitted a trade-off I had got wrong.",
        "Keeping a list of the questions that made me pause has been more useful than any course.",
        "There was a long gap before the {co} reply, which I have learned not to read anything into.",
    ],
    "ai_ml_swe": [
        "The first version worked on the sample set and fell apart the moment real {t1} inputs arrived.",
        "Most of the week went into {noun} rather than the change I had actually planned to make.",
        "Because the harness came before the optimisation, I caught a regression that looked like a win.",
        "I do not trust an improvement that only appears on examples I picked myself.",
        "Splitting the pipeline around {noun} made the failures much easier to localise.",
        "Latency turned out to be dominated by {noun}, which I had assumed was cheap.",
        "The interesting failures were the ones where the output read well but cited the wrong source.",
        "Was the gain from the model or from cleaning the {t1} inputs? Mostly the inputs.",
        "Once I logged every configuration, explaining the regression to myself took minutes rather than a day.",
        "Batching improved throughput and made the tail latency considerably harder to reason about.",
        "I rebuilt {noun} so the before-and-after comparison would actually mean something.",
        "Reproducibility mattered more here than cleverness, which is not how I would have ranked them in {month}.",
        "Two runs disagreed by enough that I stopped trusting the setup and rebuilt the measurement first.",
        "Cleaning the {t1} inputs produced a larger gain than any change to the model did.",
        "A plausible answer citing the wrong passage is worse than an obvious failure, and much harder to catch.",
        "In {month} I assumed the bottleneck was compute; it was almost entirely I/O.",
        "Keeping a log of every configuration felt tedious until I needed to explain a regression.",
        "The fallback path had never been exercised, so nobody knew it returned an empty result.",
        "Tuning {t2} helped marginally; fixing how documents were split helped substantially.",
        "I now write the evaluation before the change, which has saved me from at least two false wins.",
        "The result held up in staging and quietly regressed the moment traffic patterns changed.",
        "I spent an afternoon convinced the model was wrong before finding a bug in the loader.",
        "Every improvement I could not reproduce twice turned out not to be an improvement.",
        "Instrumenting {noun} cost a day and saved considerably more than that later.",
        "Comparing against a fixed baseline made the {month} numbers finally interpretable.",
        "The tempting change was the model; the effective change was how documents were split.",
        "I keep a short list of results I do not believe yet, and revisit it weekly.",
        "Caching {t1} lookups helped until invalidation became the more interesting problem.",
        "Two configurations differed by one parameter and by far more than one parameter's worth of output.",
        "Writing the failure cases down first made the design conversation much shorter.",
    ],
    "travel_food": [
        "We got to {place} early enough that {noun} was still quiet, which made the whole morning easier.",
        "The plan lasted about an hour before {noun} sent us down a different street entirely.",
        "{venue} was busier than I expected, though the queue moved faster than it looked.",
        "Prices and opening hours shift often enough around {place} that I would check again before relying on them.",
        "Because the heat arrived earlier than forecast, we rearranged the afternoon around shade rather than sights.",
        "Local advice beat everything I had bookmarked, particularly about when to arrive at {venue}.",
        "Walking back the long way through {place} showed me a stretch of road I had completely missed.",
        "Was it worth the detour? Easily — {noun} alone justified the extra half hour.",
        "There is a particular tiredness that comes from a good day of walking, and this was that kind of day.",
        "I would come back to {place} on a weekday, earlier, and with fewer plans.",
        "Arriving before seven meant {venue} was still calm and the staff had time to talk.",
        "The weather turned halfway through and rearranged the afternoon completely.",
        "I nearly walked past the entrance twice; there is no signage worth mentioning.",
        "Everyone recommends {venue}, and for once the recommendation held up.",
        "Getting around {place} on foot was slower and considerably better than the alternative.",
        "By the time we reached the far end, the light had gone soft and the crowds had thinned.",
        "I had planned an itinerary and abandoned it within the hour, which was the right call.",
        "Breakfast took longer than planned and was the best decision of the day.",
        "I asked at the counter rather than trusting the map, and got better directions.",
        "The {place} traffic made the short route slower than the long one.",
        "There was a queue by eight, so the early start justified itself.",
        "Someone at the next table recommended a dish I would never have ordered.",
        "The return journey was quieter and gave me time to write most of this.",
        "I had three places on the list and reached one of them properly.",
        "Prices had changed since the last time, which surprised nobody but me.",
        "A short walk turned into a long one because the street kept being interesting.",
        "The afternoon heat decided the rest of the itinerary for us.",
    ],
    "eng_notes": [
        "The failure was intermittent, so every fix looked correct for a while before {noun} broke again.",
        "Adding a metric to {noun} before adding a fix is the only reason we caught the regression at all.",
        "The obvious cause was not the real cause, and the logs had said so if you read them carefully.",
        "Retries around {noun} made the symptom disappear and the underlying problem considerably worse.",
        "Idempotency mattered far more than throughput on this path, which inverted our original priorities.",
        "Because a timeout longer than the caller's is not a timeout but a leak, we shortened it deliberately.",
        "We spent longer agreeing what done meant for {noun} than on the implementation itself.",
        "Documenting the failure mode turned out more valuable than documenting the happy path.",
        "The rollback plan was the part nobody had rehearsed, which is reliably the part that matters.",
        "Small boring changes with a measurement attached beat clever changes without one, every time.",
        "{noun} only misbehaved at peak, so the averages stayed healthy right through the incident.",
        "Three deploys in, the pattern was obvious: {noun} degraded before anything else did.",
        "We added one metric, waited a week, and the cause became embarrassingly clear.",
        "An average hid a tail that was doing all the damage.",
        "Nobody had rehearsed the rollback, so we rehearsed it afterwards and wrote it down.",
        "The fix was four lines; the diagnosis took two days.",
        "Because the consumer fell behind quietly, the first symptom was a customer report.",
        "I no longer trust a health check that only proves the process is running.",
        "We shipped the metric first and the fix a week later, in that deliberate order.",
        "The alert fired correctly and nobody could act on it, which is its own kind of failure.",
        "Under normal load {noun} behaved; under peak it did not, and only peak mattered.",
        "The postmortem was more useful than the fix because it changed how we test.",
        "I assumed the database was the bottleneck for two days longer than the evidence supported.",
        "Every retry we added made the graphs look better and the system behave worse.",
        "The config that caused it had been in place for months without incident.",
        "Making the failure reproducible locally took longer than repairing it.",
        "We now write down what we expect to see before deploying, which catches surprises early.",
        "One badly scoped timeout propagated into three services before anyone noticed.",
    ],
    "adversarial": [
        "The suffix is the only thing distinguishing these, so I read it twice before acting on {noun}.",
        "Search is not much help here because the near-matches rank alongside the one I actually want.",
        "Because a careless paste caused a wrong lookup once already, I now quote {noun} verbatim in tickets.",
        "These two sit next to each other in the register, which is exactly how the mix-up happened.",
        "I keep a short table of these rather than trusting my memory, and it has paid for itself.",
        "The naming predates the current convention, so {noun} does not follow the pattern you would expect.",
        "Worth noting that the same label appears in two different systems with different meanings.",
        "In {month} someone referenced the wrong one in a ticket and it took an hour to unpick.",
        "I copy these rather than retype them, having learned that lesson properly.",
        "Two of them differ by a single transposed digit, which no amount of care fully solves.",
        "The register lists them adjacently, which is convenient and dangerous in equal measure.",
        "When the label appears without context I now ask rather than assume.",
        "It reads unambiguously here and ambiguously everywhere else, so context goes in the note.",
        "I checked the register twice and still had to check a third time.",
        "The difference matters because they point at completely different systems.",
        "A colleague pasted the wrong one into a ticket last {month}, which is why this note exists.",
        "Reading it aloud helps more than reading it on screen.",
        "Autocomplete offers the wrong one first, which is not helpful.",
        "I have started including the full reference rather than an abbreviation.",
        "They were allocated at different times, which explains the inconsistent format.",
        "The safest habit is to copy from the source rather than from a message.",
        "Getting this wrong costs half an hour of confusion, reliably.",
        "It looks obvious written down and much less obvious at speed.",
    ],
    "noise": [
        "Woke up late and did very little about it.",
        "The kettle finally gave up this morning.",
        "Meant to go out, then did not.",
        "Testing whether lists render properly here.",
        "Bought coffee, forgot the milk.",
        "Rearranged the desk instead of working.",
        "Rain all afternoon, so nothing much happened.",
        "Short one today.",
        "Random test words: mango, velvet bicycle, paper cup, green gate, number 731.",
        "Might delete this later.",
        "Forgot to charge the laptop again.",
        "The radio was on all morning.",
        "Nothing worth writing about.",
        "Tried a new place, it was fine.",
        "Sat outside for a bit.",
        "Lost track of the afternoon.",
        "Checking whether headings work.",
        "Reminder: buy milk.",
        "Went for a walk, came back.",
        "The bus was late again.",
        "Made tea, forgot about it.",
        "Half-finished note.",
        "Nothing to see here.",
        "Trying a longer post for no reason.",
        "The neighbours were loud.",
        "Slept badly, functioning anyway.",
        "Bought a plant, we will see.",
        "Cleared the inbox, briefly.",
    ],
}


def norm_open(sent, n):
    """Normalized n-word opening prefix, used by the repetition budget."""
    w = re.sub(r"[^a-z0-9 ]", " ", sent.lower()).split()
    return " ".join(w[:n])


def make_sentences(user, post, r, count, budget4=2, budget3=4, seen4=None, seen3=None):
    """Context-aware sentences under opening-prefix and per-pattern budgets.

    Two passes. The strict pass enforces the tight budgets (4-word opening <=2,
    3-word <=4, each template <=3 times). If that cannot deliver enough sentences
    to reach the post's assigned word range, a bounded relaxed pass tops up with
    slightly looser budgets — because falling outside the frozen word range is a
    harder failure than a fourth reuse of a template. Repetition stays bounded
    either way; the old unbounded "x20" behaviour remains impossible.

    `seen4`/`seen3` are shared with the fact sentences, so the budget covers the
    whole post rather than only the filler.
    """
    cat = post["category"]
    pats = PATTERNS[cat]
    seen4 = seen4 if seen4 is not None else collections.Counter()
    seen3 = seen3 if seen3 is not None else collections.Counter()
    out, used = [], collections.Counter()
    # Two sentences from the SAME template differ only by slot fill ("Retries
    # around the cache layer..." / "Retries around the migration..."), so
    # landing them close together reads as generated text even when neither
    # budget is breached. Keep siblings apart.
    last_at = {}

    def fill(per_pattern, b4, b3, budget_iters, spacing=8):
        guard = 0
        while len(out) < count and guard < budget_iters:
            guard += 1
            pi = r.randrange(len(pats))
            if used[pi] >= per_pattern:
                continue
            if pi in last_at and len(out) - last_at[pi] < spacing:
                continue
            sent = pats[pi].format(**ctx(user, post, r))
            sent = sent[0].upper() + sent[1:] if sent else sent
            o4, o3 = norm_open(sent, 4), norm_open(sent, 3)
            if seen4[o4] >= b4 or seen3[o3] >= b3 or sent in out:
                continue
            used[pi] += 1
            last_at[pi] = len(out)
            seen4[o4] += 1
            seen3[o3] += 1
            out.append(sent)

    fill(3, budget4, budget3, count * 40)
    if len(out) < count:                       # bounded relaxation
        fill(5, budget4 + 1, budget3 + 3, count * 40, spacing=4)
    return out


def words(text):
    return len(text.split())


def build_post(user, post, facts, r):
    """Assemble one Markdown body plus its fact evidence map.

    Sentences are grouped into PARAGRAPHS (3-5 each) rather than one wall of text
    per section, and the opening-prefix budget spans the whole post — fact
    sentences included — so no construction can dominate.
    """
    cat = post["category"]
    lo, hi = post["target_word_range"]
    target = int(lo + r.uniform(0.25, 0.85) * (hi - lo))
    title = make_title(user, post, r)
    evidence = {}
    seen4, seen3 = collections.Counter(), collections.Counter()

    if cat == "noise":
        pool = make_sentences(user, post, r, max(30, int(target / 9)),
                              budget4=3, budget3=6, seen4=seen4, seen3=seen3)
        parts, i = [], 0
        while words("\n\n".join(parts)) < target:
            if i >= len(pool):
                more = make_sentences(user, post, r, 40, budget4=6, budget3=12,
                                      seen4=seen4, seen3=seen3)
                if more:
                    pool.extend(more)
                else:
                    # Noise is DESIGNED to be low-signal and repetitive (§11/§27
                    # permit exact boilerplate here), and its bank is small by
                    # intent. Recycling is acceptable for noise only — never for
                    # the signal-bearing categories, which top up with fresh
                    # sentences above.
                    pool.extend(pool[:40] or ["Nothing much today."])
            take = pool[i:i + r.randint(1, 3)]
            i += max(1, len(take))
            if take:
                parts.append(" ".join(take))
        body = _trim("\n\n".join(parts), hi)
        return title, body, evidence

    heads = SECTIONS[cat]
    # Identical assertions render ONCE; every matching fact id maps to that
    # occurrence. Duplicating the sentence would be padding and would make
    # traceability ambiguous.
    buckets = {h: [] for h in heads}
    rendered = {}
    slot = 0
    for f in facts:
        key = (f["fact_type"], str(f["subject"]), str(f["predicate"]), str(f["value"]))
        if key in rendered:
            evidence[f["fact_id"]] = rendered[key]
            continue
        sent = render_fact(f, user, r, seen4=seen4)
        tries = 0
        while sent in rendered.values() and tries < 6:
            sent = render_fact(f, user, r, seen4=seen4)
            tries += 1
        rendered[key] = sent
        seen4[norm_open(sent, 4)] += 1
        seen3[norm_open(sent, 3)] += 1
        buckets[heads[slot % len(heads)]].append(sent)
        slot += 1
        evidence[f["fact_id"]] = sent

    # Ask for enough sentences to reach the target with headroom (~16 words each).
    need = max(24, int(target / 12))
    pool = make_sentences(user, post, r, need, seen4=seen4, seen3=seen3)
    pi = 0
    parts = []
    for h in heads:
        parts.append(f"## {h}")
        seg = list(buckets[h])
        for _ in range(r.randint(2, 4)):
            if pi < len(pool):
                seg.append(pool[pi]); pi += 1
        r.shuffle(seg)
        parts.append(_paragraphs(seg, r))
    body = "\n\n".join(parts)

    # Top the pool up on demand rather than failing short: the assigned word
    # range is frozen, so running out of sentences must never push a post
    # outside it. Repetition stays bounded by the budgets inside make_sentences.
    guard = 0
    while words(body) < target and guard < 400:
        if pi >= len(pool):
            more = make_sentences(user, post, r, 40, seen4=seen4, seen3=seen3)
            if not more:
                break
            pool.extend(more)
        h = heads[guard % len(heads)]
        body = _append_to_section(body, h, pool[pi]); pi += 1
        guard += 1
    body = _trim(body, hi)

    for fid, sent in evidence.items():
        if sent not in body:
            body = body.rstrip() + "\n\n" + sent
    return title, body, evidence


def _paragraphs(sentences, r):
    """Group sentences into paragraphs of 3-5 rather than one long block."""
    out, i = [], 0
    while i < len(sentences):
        n = r.randint(3, 5)
        out.append(" ".join(sentences[i:i + n]))
        i += n
    return "\n\n".join(out)


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
    """Cycle the bank, qualifying repeats so one author never reuses a title.

    Each user has 18 posts and the banks are smaller, so the raw cycle produced
    two identical titles per author. The qualifier is drawn from the post's own
    frozen metadata, so it stays deterministic and never invents a fact.
    """
    bank = TITLE_BANK[post["category"]]
    i = post["sequence"] - 1
    title = bank[i % len(bank)]
    if i // len(bank):
        month = {"03": "March", "04": "April", "05": "May", "06": "June",
                 "07": "July", "08": "August"}[post["date"][5:7]]
        title = f"{title} ({month})"
    return title


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
