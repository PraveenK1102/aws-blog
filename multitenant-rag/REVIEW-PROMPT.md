# Production-Readiness & Scale Review — MultiTenantRAG

*Open a fresh Claude Code session at the repo root
(`/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog`) and paste this whole thing in.
It explains everything you need — you don't need any other context.*

---

## Who you are for this task

You are a senior engineer doing a careful, honest review of a live app before we try to grow it
to a huge number of users. Your job is to **find the problems** — the things that will break,
leak private data, get slow, or cost a fortune once the app is big. Please be blunt. Do not try
to make us feel good. If something is fine, say it's fine in one line and move on; spend your
energy on what's wrong.

**Only read and think. Do not change any code.** We want a list of problems and advice, not fixes.

## What the app is

It's a blogging site where every writer also gets their own AI chatbot. A visitor logs in,
browses a list of writers, opens a writer's page to read their posts, and can chat with that
writer's AI. The AI is only allowed to answer using **that one writer's posts** — never anyone
else's. Everything runs on AWS using "serverless" pieces (small functions that run on demand,
a managed database, a vector search service, etc.).

## Read these first, in this order

Read them to understand the app, but then **trust the actual code over the docs** — the docs can
be a little out of date.

1. `multitenant-rag/MASTER-CONTEXT.md` — short overview and the names of all the AWS pieces.
2. `multitenant-rag/ARCHITECTURE.md` — how the data is stored (it has a note at the top listing
   what changed after it was written).
3. `multitenant-rag/CODE-GUIDE.md` — a tour of the code (also has an "update" note; the older
   sections describe the first version and are stale — trust the code).
4. `multitenant-rag/STATUS.md` — what is live right now.
5. The code itself: `multitenant-rag/lambdas/**` (this is the backend), and the website in
   `blog-frontend/src/App.jsx` and `blog-frontend/src/api.js`.
6. There is **no infrastructure-as-code** (no Terraform) — the AWS setup was done by hand. The
   only automation is `.github/workflows/build-lambdas.yml`, which builds the code; putting a new
   version live is a manual command.

## The size we are testing against

Right now the app is tiny. We want you to imagine it has grown to:

- **1 million users** and **20 million blog posts** (so about 20 posts per user on average — but
  it's uneven: assume a few writers have tens of thousands of posts and are very busy, while most
  have just a handful).

Please work out for yourself what that size *means* for each part of the system, and then check
whether the current design can handle it. For example:

- Each post gets split into several small pieces ("chunks") and each piece is turned into a
  vector (a list of numbers) for search. 20 million posts easily becomes **hundreds of millions
  of vectors**. Can the vector database hold that?
- If even a small fraction of a million users are chatting at the same time, that's **thousands
  of chat requests at once**. Can the system handle that many at once?
- If a writer publishes a burst of posts, can the background "process and index" pipeline keep up?

For every part, answer plainly: **does this still work at 1 million users and 20 million posts —
and if not, exactly where and how does it fall apart?** (Does it get slow? Start rejecting
requests? Run out of space? Cost too much? Give wrong or leaked answers?)

## What to look at, and what to skip

**Please look for:**
- Bugs — anything that already gives a wrong result, crashes, or behaves incorrectly.
- Security and privacy holes — especially anything that could let one writer's data (posts,
  chats, or cached AI answers) leak to another. This is the most important thing.
- Things that won't handle the big size — parts that get slow, get overloaded, run out of room,
  or grow forever without cleanup.
- Fragile spots — places that will fail badly if a retry happens, or if an outside service is
  slow or down, or if two things happen at once.
- Money — places where the cost blows up once the app is big.
- Data cleanup — what happens when a post is edited or deleted, or when a user asks to be fully
  deleted (leftover junk, orphaned data).
- Login safety — sign-up and sign-in weaknesses.
- Being able to see what's going on in production (logs, alarms, dashboards).

**Please skip:**
- New feature ideas.
- How the website looks (colors, layout).
- Simply repeating things we already know are limitations (see the next section for how to handle
  those).

## Things we already chose on purpose — please JUDGE them, don't just repeat them

Below is a list of choices we made knowing they're not "big-scale" choices. **We already know
these are limited at 1 million users.** So please do **not** just tell us "you're using the free
version, it won't scale" — that helps nobody. Instead, for each one, dig in and tell us the
**useful** part:

- **At what point does it break?** (roughly what number of users/posts/requests)
- **How badly, and what exactly happens** when it breaks?
- **What should we switch to, and roughly what will that cost or take?**

Here's the difference, using the vector database as an example:

- ❌ Not helpful (just repeating): *"The vector database is on the free 1 GB plan — it won't hold
  20 million posts."* (We know.)
- ✅ Helpful (actually judging): *"20 million posts turn into hundreds of gigabytes of vectors,
  but the free 1 GB plan only holds roughly 250,000 of them — so you run out at around 30,000
  posts. To fix it you could shrink the vectors (a setting called quantization, 4–32× smaller),
  split each writer into their own collection, or pay for a bigger cluster (about $X/month at
  this size). Switching means re-indexing everything. My recommendation: shrink the vectors and
  move to a paid cluster, roughly $Y/month."*

The choices to judge this way:

1. Chat replies are sent as one finished block, not streamed word-by-word as they're generated.
2. The AI model (Groq) is on a free, rate-limited plan.
3. Turning text into vectors (Bedrock Titan) is done one piece at a time, one call each.
4. The vector database (Qdrant) is on the free 1 GB plan.
5. There's no "reranker" (an extra step that reorders search results for quality).
6. Sign-up doesn't verify email addresses.
7. The local test setup doesn't enforce AWS permissions, so permission mistakes only show up in
   production.
8. Secret keys are cached inside each function and only refresh when the function restarts.

## The specific places to dig (these are leads — go check the code and prove each one)

For each item below: open the code, see if the concern is real, and if it is, show us exactly
where (file and line) and describe what goes wrong at the big size.

1. **Keeping writers' data separate (most important).** The whole promise is that one writer's AI
   only ever sees that writer's stuff. Trace how the app decides "which writer is this" — it comes
   from the login token (see `common/context.py` and `common/auth.py`), and then every search must
   be locked to that writer's ID. Check that this lock is applied **everywhere**: normal search,
   the saved-answer cache, and reading a single post. Can a logged-in person ever get another
   writer's posts, chats, or **cached answers**? Also check the login token itself: how long it
   lasts, whether it can be faked, and whether it can be turned off if stolen.

2. **The main database at big size.** The posts are stored grouped by writer ID. If one writer has
   a huge number of posts, all of them land in one "bucket" in the database — one bucket getting
   hammered while others sit idle makes it slow and start rejecting requests (this is called a
   "hot partition"). There's also a secondary index organized by a field that only has 2–3
   possible values ("pending" / "indexed" / "failed") — that means almost all 20 million posts pile
   into 2–3 buckets, which will choke. Also look at the saved-chats code: to enforce "5 chats per
   writer" it loads a user's chats and counts them in memory every time — does that get expensive
   or wrong when a user has chatted with many writers? And each chat stores its whole message
   history as one blob, which has a size limit.

3. **The vector search database at big size.** Hundreds of millions of vectors versus a 1 GB free
   plan — put real numbers to the gap. Everything is in one big shared collection for all writers;
   is that the right shape, or should each writer (or group of writers) be split out? How fast is
   search when the collection is enormous? How costly is deleting a writer's vectors?

4. **The saved-answer cache.** When someone asks a question, the answer can be cached so the same
   question is instant next time (see `common/semcache.py`). Check: is the cache strictly
   per-writer, both when saving and when reading (otherwise one writer's cached answer could show
   up for another — a privacy leak)? The "expire after 24 hours" rule is only checked when reading,
   so old entries pile up forever and never get cleaned — is that a growth problem? Every time a
   writer publishes a new post, the code deletes that writer's **entire** cache — is that wasteful
   or wrong if they post a lot?

5. **The "who is this writer?" answer and malicious input.** When a search finds nothing, the app
   sends the writer's **post titles** to a small AI model to either give an overview of the writer
   or politely decline (see `_build_profile_prompt` and `_tenant_post_titles` in `ask/app.py`).
   Post titles are written by users. Could a sneaky title (e.g. one that contains instructions)
   trick the AI into misbehaving? Also, the small model is supposed to reply with an exact decline
   sentence — is it reliable? And what does this extra AI call cost when it happens a lot?

6. **Handling many requests at once, and slow starts.** These functions have limits on how many
   can run at the same time. With thousands of chats at once, do we hit those limits? When a
   function starts fresh ("cold start") it loads a search library that takes a couple of seconds —
   does that slow down chats? Also, the background pipeline that indexes new posts processes each
   writer's posts strictly one at a time (in order) — so a single very active writer can only index
   so fast. Is that a bottleneck? And turning text into vectors is one call per piece — is that too
   slow for lots of posts?

7. **What happens when things go wrong.** If indexing a post fails, is there a safety net (a
   "dead-letter queue") so it isn't lost? If the same post gets processed twice, does that cause
   duplicates? When creating a post, if saving the file works but saving the record fails, what
   state are we left in? There are several places that quietly ignore errors ("best-effort") — do
   those hide real failures? What happens if an outside service (the AI, the vector DB, the
   embedding service) is slow or down in the middle of a request?

8. **Login safety.** There's no email verification, so people (or bots) could create tons of fake
   accounts — is there anything stopping that? Is there any limit on how many times someone can try
   to log in (to stop password guessing)? Are passwords stored safely? Do the error messages
   accidentally reveal whether an email is registered?

9. **The money at big size.** Do a rough monthly cost estimate at 1 million users / 20 million
   posts across all the paid pieces (the AI model, the embedding service, the functions, the
   database, the vector DB, the CDN, storage, logs). The docs claim about $1–3/month today —
   where does that number break, and roughly what does it become?

10. **Editing and deleting data.** When a post is edited or deleted, does the app clean up the old
    search vectors, the stored file, and any cached answers — or does junk get left behind? Is
    there even a way to delete a post? If a user asks to be fully deleted (a legal requirement in
    many places), can we actually erase everything about them across all the different stores? What
    about posts stuck in "pending" or "failed" forever?

11. **The website side.** When a post is shown, its text is turned into HTML — could a malicious
    post inject harmful content into the page (an attack called XSS)? Check how `renderMarkdown` in
    `App.jsx` works. Also, the CDN is set up so that any "not found" or "forbidden" response shows
    the main page instead — this can **hide real API errors** (it already caused a bug where a
    server error looked like "not found"). And the login token is kept in the browser's local
    storage — any risk there?

12. **Being able to run it in production.** Right now there's basically one cost alarm and some
    logs. Can the team actually *see* trouble — requests being rejected, error spikes, the failure
    queue growing, how often the cache helps, the slowest requests, cost per writer? Putting a new
    version live is a manual command with no easy undo or gradual rollout — is that risky?

## How to do the review

- **Prove everything from the code.** For each problem, point to the exact file and line. Don't
  guess or invent — if you can't point to it in the code, don't claim it.
- **Give a concrete example of the failure.** Not "this might be slow," but "when a writer has
  50,000 posts, listing their posts does X, which does Y, and that's when it slows down / breaks."
- **Rate each problem:** how serious (Critical / High / Medium / Low), and what kind (privacy,
  wrong-result, won't-scale, reliability, cost, data-cleanup, monitoring, or general good-practice).
- **Separate three things:** (a) broken right now, (b) works now but won't survive the big size,
  (c) just a good-practice improvement.
- Spend more effort proving the serious ones than listing lots of small ones.

## What to hand back

1. **A short summary** (a few lines): overall, is this ready to grow to a million users? What are
   the biggest worries?
2. **A scorecard**: a simple table rating each area (data separation, database, vector search,
   reliability, cost, data cleanup, login, monitoring, website) as Red / Yellow / Green, with a
   one-line reason each.
3. **The full list of problems**, most serious first. For each: a short title, how serious it is,
   what kind, where it is (file and line), a concrete example of the failure, why it matters at the
   big size, the suggested fix, and a rough effort (small / medium / large).
4. **The top 5 things to fix before growing.**
5. **Quick wins** (optional): easy fixes that are worth a lot.

## Optional: split the work across several helpers

If you can run several sub-agents at once, consider giving each one a different area (data
separation, database size, vector search, reliability, cost, data cleanup, website), and then have
a second agent try hard to **disprove** each reported problem before it goes in the final list —
so only problems that truly hold up in the code make it in. If you can't do that, just work through
it yourself, but keep the same rule: prove it in the code before you report it.
