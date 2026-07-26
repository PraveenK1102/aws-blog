# Architecture Learning Review — MultiTenantRAG (design it to scale to 1M users)

*Open a fresh Claude Code session at the repo root
(`/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog`) and paste this whole thing in.
It explains everything — you don't need any other context.*

---

## Why this review exists (please read this first — it changes how you should answer)

I am **not** actually going to run this app at a million users. I built it to **learn how to
design a production, enterprise-grade system**. So this is a *learning* review, not a fire drill.

What I want from you: **teach me how a senior/staff engineer would design this app so it is
*capable* of growing from about 10,000 users up to 1,000,000 users (and up to ~20,000,000 blog
posts) — and, just as important, tell me *which* design decisions matter *at which size*.**

A perfect example of the thinking I want: right now the app has no "cross-encoder reranker" (an
extra step that re-orders search results for better quality). I don't want you to just say "add
one." I want you to teach me: **do you even need a reranker at 10,000 posts? At 1 million? At 20
million? When does it start to matter, why, and what does adding it cost in latency and money?**
Apply that same "is it needed yet, and when does it become needed?" thinking to everything.

So every point you make should do two things:
1. **Point out what the current code does and where it would fall short** at bigger sizes (with
   the exact file and line).
2. **Teach me the proper way to design it** — what a real production system would do, why, and
   **at what scale that upgrade becomes necessary** (not needed yet vs. needed at 100K vs. needed
   at 20M). I'm here to learn the *reasoning*, not just get a to-do list.

Keep it honest and blunt. If a part of the current design is actually fine and doesn't need to
change until very large scale, say so clearly — that's a valuable lesson too. Over-engineering
before it's needed is a mistake I want to learn to avoid.

**Only read and think. Do not change any code.**

## What the app is

A blogging site where every writer also gets their own AI chatbot. A visitor logs in, browses a
list of writers, opens a writer's page to read their posts, and can chat with that writer's AI.
The AI may only answer using **that one writer's posts** — never anyone else's. It runs on AWS
using serverless pieces (small on-demand functions, a managed database, a vector search service,
an AI model, etc.). **Keep the product as-is: a plain text blog.** Don't suggest new features
(images, PDFs, video, etc.) — I want to learn how to scale *this*, not how to grow the product.

## Read these first, in this order (then trust the code over the docs — docs can lag)

1. `multitenant-rag/MASTER-CONTEXT.md` — short overview and the names of all the AWS pieces.
2. `multitenant-rag/ARCHITECTURE.md` — how the data is stored (note at top lists later changes).
3. `multitenant-rag/CODE-GUIDE.md` — a tour of the code (older sections are stale; trust the code).
4. `multitenant-rag/STATUS.md` — what is live right now.
5. The code: `multitenant-rag/lambdas/**` (backend), and `blog-frontend/src/App.jsx` +
   `blog-frontend/src/api.js` (website).
6. There is **no infrastructure-as-code** (no Terraform) — AWS was set up by hand. The only
   automation is `.github/workflows/build-lambdas.yml` (it builds the code); putting a new version
   live is a manual command.

## The size range we're designing for

Imagine the app has to be *capable* of these sizes. Use them as checkpoints, and tell me what
changes at each step:

- **~10,000 users** (small startup)
- **~100,000 users**
- **~1,000,000 users** and **~20,000,000 posts** (about 20 posts per user, but uneven — a few
  writers have tens of thousands of posts and are very active; most have just a handful).

Remember: each post is split into several small pieces ("chunks"), and each piece becomes a vector
(a list of numbers) for search — so 20 million posts becomes **hundreds of millions of vectors**.
And if even a small fraction of a million users chat at once, that's **thousands of AI requests at
the same time**.

## The walls I expect to hit first (please focus here — these are my real worries)

These are the limits I think matter most for a serverless AI app like this. For each, teach me
where the wall is and how a production system gets past it:

- **AI model rate limits.** The AI provider limits how many requests per second and per minute I
  can make, and how many tokens per minute. At thousands of chats at once, I'll hit this fast.
  What are the real limits, when do I hit them, and how do production systems handle it (paid
  tiers, multiple providers, queuing, batching, caching, spreading load)?
- **Storage.** Vectors, blog text, database rows, logs, cached answers — all grow with posts and
  usage. Where does storage get expensive or hit a limit, and how is it managed at scale?
- **Compute cost (Lambda) and overall AWS bill.** Serverless is cheap when small but can get
  expensive when busy. Where does the function/compute cost climb, and what's the cheaper
  production shape at high volume? Give me a rough monthly bill at each checkpoint.
- **Correctness and privacy at scale** — because none of the above matters if writers' data leaks
  into each other or the answers are wrong. Treat any cross-writer data leak as the top priority.

## Things we chose on purpose — JUDGE and TEACH, don't just repeat

Below are choices I made knowing they're not "big-scale" choices. I already know they're limited.
So do **not** just say "the free tier won't scale." Instead, for each, teach me: **at what size
does it break, what exactly happens, what's the proper production replacement, and roughly what
that costs or takes.** Show me the upgrade path and the reasoning.

1. Chat replies come back as one finished block, not streamed word-by-word.
2. The AI model (Groq) is on a free, rate-limited plan.
3. Turning text into vectors (Bedrock Titan) is done one piece at a time, one call each.
4. The vector database (Qdrant) is on the free 1 GB plan.
5. There is no reranker (the search-result re-ordering step) — my example above; teach the "when."
6. Sign-up doesn't verify email addresses.
7. The local test setup doesn't enforce AWS permissions, so permission mistakes only show up live.
8. Secret keys are cached inside each function until it restarts.

Example of the depth I want (vector database):
- ❌ Not helpful: *"Free 1 GB won't hold 20 million posts."* (I know.)
- ✅ Helpful: *"Hundreds of GB of vectors vs a 1 GB free plan — you run out around 30,000 posts. The
  production options are: shrink the vectors (quantization, 4–32× smaller), give big writers their
  own collections, or a paid cluster (~$X/mo at this size). Re-indexing is required. For a text
  blog at 1M users I'd do quantization + a paid cluster, ~$Y/mo — and I would NOT bother until past
  ~100K posts."*

## The places to dig — for each, tell me (a) where the current code falls short, and (b) the proper design + when it's needed

Prove each from the code (file and line). For each, teach me the production way and the scale at
which it starts to matter.

1. **Keeping writers' data separate (top priority).** Trace how "which writer is this" is decided
   (from the login token — `common/context.py`, `common/auth.py`) and confirm every search is
   locked to that writer's ID **everywhere**: normal search, the saved-answer cache, and reading a
   single post. Can anyone ever get another writer's posts, chats, or **cached answers**? Teach me
   how big multi-tenant systems guarantee isolation, and whether one shared store or per-tenant
   stores is the right call at each size. Also: token lifetime, faking, and revoking a stolen token.

2. **The main database (DynamoDB) at each size.** Posts are grouped by writer ID — if one writer
   has huge numbers of posts, they all land in one "bucket" that gets hammered while others sit
   idle (a "hot partition"), which throttles and slows things. A secondary index is organized by a
   field with only 2–3 possible values ("pending"/"indexed"/"failed"), so almost all 20M posts pile
   into 2–3 buckets — that will choke. The saved-chats code counts a user's chats in memory to
   enforce "5 per writer" — does that get slow/wrong at scale? Teach me the right key design and
   access patterns for this data, and when the current design starts to hurt.

3. **The vector search database at each size.** Hundreds of millions of vectors vs a 1 GB free
   plan — put real numbers on it. One shared collection for all writers vs per-writer collections
   vs sharding — teach me the tradeoffs and which fits at 10K / 100K / 1M. How fast is search when
   the collection is huge, and what does quantization / a paid tier / a different engine buy me?

4. **The saved-answer cache** (`common/semcache.py`). Is it strictly per-writer on both save and
   read (or could one writer's cached answer leak to another)? The "expire after 24h" is only
   checked on read, so old entries pile up forever — is that a growth/cost problem, and how should
   expiry really be done? Publishing a post deletes that writer's **entire** cache — wasteful at
   high posting rates? Teach me how caching is done well in a big system, and whether a cache is
   even worth it at each size.

5. **The "who is this writer?" answer and untrusted input.** When search finds nothing, the app
   sends the writer's **post titles** to a small AI model to give an overview or decline
   (`_build_profile_prompt`, `_tenant_post_titles` in `ask/app.py`). Titles are user-written —
   could a sneaky title contain instructions that trick the AI (a "prompt injection")? Teach me how
   production systems defend against that. Also: the extra AI call's cost at volume.

6. **Do we even need a reranker? (my headline learning question.)** There's no reranker today.
   Teach me: at 10K posts, does hybrid search alone give good enough results, or do we need a
   reranker? At 1M/20M? What does a reranker improve, what does it cost (latency + money + another
   service), and what are the options (a hosted reranker, a small cross-encoder, or none)? Give me
   a clear "use it when ___" rule.

7. **Handling thousands of requests at once, and slow starts.** These functions cap how many run at
   once — with thousands of chats at once, do we hit the cap, and how do production systems handle
   spikes (concurrency limits, provisioned capacity, queues)? A search library adds a couple of
   seconds on a "cold start" — does that hurt chat latency, and how is it avoided? The background
   indexing processes each writer's posts strictly one-at-a-time — is that a bottleneck for a very
   active writer, and what's the right design?

8. **What happens when things go wrong.** If indexing a post fails, is there a safety net (a
   "dead-letter queue") so it isn't lost? If a post is processed twice, do we get duplicates? If
   saving the file works but saving the record fails, what state are we in? Several places quietly
   ignore errors — do those hide real failures? What happens if the AI, the vector DB, or the
   embedding service is slow or down mid-request? Teach me the production patterns for
   retries, idempotency, and graceful failure.

9. **Login safety.** No email verification means bots can make endless fake accounts — what stops
   that at scale? Any limit on repeated login attempts (to stop password guessing)? Are passwords
   stored safely? Do error messages reveal whether an email is registered? Teach me the standard
   production auth hardening.

10. **The money at each size.** Give me a rough monthly cost at 10K, 100K, and 1M users across all
    paid pieces (AI model, embeddings, functions, database, vector DB, CDN, storage, logs). Show me
    where the cost curve bends and what the cheaper production shape is at high volume. This is a
    core thing I want to learn: how to keep an AI app affordable as it grows.

11. **Editing and deleting data.** When a post is edited or deleted, are old vectors, the stored
    file, and cached answers cleaned up, or is junk left behind? Is there even a delete-post path?
    If a user asks to be fully deleted (a legal requirement in many countries), can we erase
    everything about them across all stores? Teach me how data lifecycle and "right to be
    forgotten" are handled in production.

12. **The website side.** When a post is shown, its text becomes HTML — could a malicious post
    inject harmful content into the page (an "XSS" attack)? Check `renderMarkdown` in `App.jsx`.
    Also, the CDN turns any "not found"/"forbidden" into the main page, which can **hide real API
    errors** (already caused a bug once). And the login token is kept in browser local storage —
    any risk?

13. **Running it in production (operations).** Today there's basically one cost alarm and some
    logs. At scale, can the team actually *see* trouble — rejected requests, error spikes, a growing
    failure queue, how often the cache helps, the slowest requests, cost per writer? Putting a new
    version live is a manual command with no easy undo or gradual rollout. Teach me what real
    production monitoring and safe deployment look like, and what's worth adding at each size.

## The one artifact I most want: a "what you need at each size" table

Please give me a table with a row per part of the system (AI model, embeddings, vector DB, main
database, cache, functions/compute, auth, monitoring, deployment) and a column for **~10K users**,
**~100K users**, and **~1M users / 20M posts**. In each cell, say what the *right* choice is at
that size (e.g. "keep as-is", "add a reranker", "move to a paid vector cluster + quantization",
"switch to provisioned concurrency", "add a dead-letter queue"). This is the map I want to learn
from — it should make clear what to do *now* vs. what to add only when I actually get bigger.

## How to do the review

- **Prove everything from the code** — exact file and line. If you can't point to it, don't claim
  it. No guessing, no invented function names.
- **Teach the reasoning**, not just the verdict — I'm here to learn *why*, and *at what size* each
  decision flips from "not needed" to "needed."
- **Give concrete examples** of failures ("when a writer has 50,000 posts, X happens because Y").
- **Rate each item**: how serious (Critical / High / Medium / Low) and what kind (privacy,
  wrong-result, won't-scale, reliability, cost, data-cleanup, monitoring, good-practice).
- **Separate three things**: (a) broken now, (b) fine now but won't survive the big size, (c)
  good-practice improvement. And for the scale ones, always say *at which size* it starts to bite.
- Don't over-engineer: if something is fine until very large scale, say so — that's a real lesson.

## What to hand back

1. **A short summary**: is the current design a *good foundation* to grow on? Biggest lessons.
2. **The "what you need at each size" table** (described above) — the main deliverable.
3. **A scorecard**: each area (data separation, database, vector search, cache, AI/rate-limits,
   reliability, cost, auth, data-cleanup, monitoring, website) rated Red/Yellow/Green with a
   one-line reason.
4. **The full list of findings**, most serious first. For each: title, seriousness, kind, location
   (file:line), a concrete failure example, why it matters at the big size, the proper production
   design, **the scale at which it becomes necessary**, and rough effort (small/medium/large).
5. **The top 5 things to learn/fix first** to make this a solid, scalable foundation.

## Optional: split the work across several helpers

If you can run several sub-agents at once, give each a different area, and have a second agent try
hard to **disprove** each finding against the code before it makes the final list — so only
findings that truly hold up get reported. Otherwise do it yourself, same rule: prove it in the
code first.
