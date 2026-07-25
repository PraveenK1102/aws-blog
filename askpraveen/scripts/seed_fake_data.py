#!/usr/bin/env python3
"""Seed Qdrant with fake multi-tenant data for Session 2 isolation testing.

Two users:
  - praveen: engineering topics (AWS, Docker, RAG, etc.)
  - prakash: reimbursements topics (expense policies, receipts, etc.)

Each post is chunked, embedded via Titan, and upserted to Qdrant with
user_id tagging. Idempotent — re-running produces the same UUIDs and
just overwrites content in place.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.chunker import chunk_markdown
from src.db import count_documents, delete_by_post, ensure_collection, upsert_chunks
from src.embeddings import embed_batch


# ---------- User: praveen (engineering) ----------
PRAVEEN_POSTS = [
    (1, "Deploying a Node.js API to AWS ECS Fargate",
"""ECS Fargate lets you run Docker containers without provisioning EC2 instances yourself.
I moved my blog backend from EC2 to Fargate to get zero-maintenance patching and native
autoscaling. The steps: push the image to ECR, define a task definition with CPU/memory
requests, create a service behind an ALB target group, and let ECS handle placement.

The trickiest part was IAM: Fargate needs both a task execution role (to pull from ECR
and write logs) and a task role (for the app's own AWS API calls). Getting them confused
is a common newbie mistake. Also, Fargate networking mode is 'awsvpc' — each task gets
its own ENI, which is cleaner than bridge mode but uses more IP addresses.
"""),

    (2, "pgvector on RDS Postgres: HNSW indexes and lessons learned",
"""pgvector adds a vector column type to Postgres, backed by HNSW (Hierarchical Navigable
Small World) for approximate nearest neighbor search. I enabled it on my existing RDS
instance to store 1024-dim Titan embeddings alongside blog post content.

The HNSW parameters matter. m=16 and ef_construction=64 are safe defaults, but for
larger corpora you'd bump both to improve recall at the cost of index build time.
Cosine distance (vector_cosine_ops) is the right operator class for Titan embeddings,
which are already L2-normalized.

One gotcha: HNSW index builds hold locks longer than you'd expect. On tables with
active writes, use CONCURRENTLY. And bump maintenance_work_mem before rebuilds.
"""),

    (3, "Why I use Docker Compose for local development",
"""Docker Compose gives me a one-command way to bring up Postgres, Redis, and my
backend all wired together. No 'works on my machine' problems. The docker-compose.yml
declares services, networks, and volumes; docker-compose up -d starts everything.

For this project I mount the sql/ directory as a docker-entrypoint-initdb.d volume,
which auto-runs migration SQL on first launch. Init scripts run only on empty data
volumes — a subtle gotcha when iterating on schema. Nuke the volume with 'docker
compose down -v' to force a re-init.
"""),

    (4, "Bedrock Converse API: one code path for every model",
"""The Bedrock Converse API is a game-changer for model portability. Instead of writing
different request/response format code for Claude vs Nova vs Llama vs Mistral, you
call converse() with a normalized schema and Bedrock handles the format translation
internally.

I use this to keep the AskPraveen Lambda model-agnostic. The model ID is a single
env var. Nova Micro during dev (cheapest, no form gating), Claude 3 Haiku for demos
(better reasoning, needs Anthropic use-case form). Swap is zero code change.
"""),

    (5, "Retrieval-augmented generation: retrieval is where most of the quality lives",
"""LLMs hallucinate. RAG is the standard mitigation: retrieve relevant chunks from your
own corpus, hand them to the LLM in the prompt, and instruct the model to answer only
from those chunks. The LLM becomes a summarizer over your data instead of a know-it-all.

But retrieval quality dominates the pipeline. If you retrieve the wrong chunks, no
amount of prompt engineering fixes the answer. Hybrid retrieval — combining vector
search (semantic) with BM25 (lexical) via RRF fusion — consistently outperforms either
alone. Reranking with a cross-encoder on top of the retrieved set adds another 10-15%
precision. HyDE (Hypothetical Document Embeddings) helps when questions and documents
use very different vocabulary.
"""),

    (6, "Load balancers, target groups, listener rules — the ALB mental model",
"""An Application Load Balancer routes traffic based on listener rules. Each rule
matches a condition (path, host, header) and forwards to a target group. Target
groups have targets — EC2 instances, IP addresses, Lambda functions, or, in my
case, ECS Fargate tasks registered dynamically by the service.

Health checks live at the target group level. If a target fails health checks,
the target group stops routing to it. Blue-green deploys work by shifting traffic
between two target groups — ECS handles this natively via CodeDeploy or via the
deployment_controller: CODE_DEPLOY setting.
"""),

    (7, "GitHub Actions with OIDC: no more long-lived AWS keys in CI",
"""Storing AWS access keys in GitHub secrets works but is a rotating headache — keys
expire, get leaked, or outlive their owners. The modern pattern is OIDC (OpenID
Connect): GitHub Actions gets a short-lived token, exchanges it for AWS credentials
via an IAM role, and never stores long-lived secrets.

Setup: create an OIDC identity provider in IAM pointing to token.actions.github.com,
then an IAM role with a trust policy that scopes the role to a specific repo and
branch. In the workflow, aws-actions/configure-aws-credentials assumes the role on
each run. Least privilege by default because you can scope the trust to individual
branches or environments.
"""),

    (8, "Terraform state: local vs remote, and why remote wins",
"""Terraform tracks the mapping between HCL code and real cloud resources in a state
file. Local state (a terraform.tfstate on your Mac) is fine for solo hobby work but
breaks the moment you're on two machines or working with a teammate. Two people
applying against local state race and corrupt each other's work.

Remote state — usually S3 bucket + DynamoDB lock table — solves both problems. S3
stores the state file; DynamoDB provides a lock so only one apply runs at a time.
This is called 'the backend'. Configure it in a backend block in your terraform.tf.
Once you flip from local to remote, terraform init migrates the state for you.
"""),

    (9, "IAM roles vs IAM users: the fundamental AWS access-control distinction",
"""An IAM user is a persistent identity with long-lived credentials (access key,
password). Human developers have IAM users. Services running inside AWS should
NOT have IAM users — they should assume IAM roles, which provide short-lived,
auto-rotated credentials.

Every EC2 instance, ECS task, Lambda function, GitHub Actions workflow (via OIDC),
or CodeBuild project should have a role, not a user. The AWS SDK inside those
services automatically picks up temporary role credentials via the instance
metadata service (IMDSv2) or environment variables. No secrets in your code.
"""),

    (10, "Session and cost tradeoffs when running RAG on Bedrock",
"""Every RAG query on Bedrock is two model calls: one embedding call for the question,
one generation call for the answer. Titan v2 embeddings are $0.02 per million tokens
(essentially free at hobby scale). Generation is where cost lives.

Claude 3 Haiku is $0.25 input / $1.25 output per million tokens. Nova Micro is
$0.035 in / $0.14 out — roughly 8x cheaper. At 2000 input + 200 output tokens per
query, Haiku costs $0.0007 per query, Nova about $0.0001. At 10k queries/month
that's $7 vs $1 respectively. The difference in answer quality is small for our
use case; I'd default to Nova and only switch to Haiku when quality complaints
show up in production.
"""),
]


# ---------- User: prakash (reimbursements) ----------
PRAKASH_POSTS = [
    (1, "Understanding the corporate reimbursement policy",
"""The reimbursement policy defines what expenses are eligible for company payment.
Categories include travel (flights, hotels, ground transportation), meals during
work travel (subject to per diem caps), client entertainment (with approval and
guest list), professional development (courses, books, conferences), and home
office supplies (with limits).

Every expense needs a receipt with the vendor, date, amount, and business purpose.
Missing receipts require a lost-receipt affidavit signed by the employee and their
manager. Personal expenses mixed with business ones (like a lunch with a friend
tagged onto a client meeting) are not reimbursable — even the business portion,
because the auditor can't verify split.
"""),

    (2, "How to submit an expense claim in Concur",
"""The expense workflow in Concur has five steps. First, create a new report and
select the associated project or client code — this determines which cost center
gets charged. Second, upload receipts either by mobile photo or forwarding email
receipts to receipts@concur. Third, categorize each expense (meals, transit,
lodging, etc.) — the category determines applicable per diem or spending limits.

Fourth, add attendee lists for meal expenses over $75 — the IRS requires attendee
tracking for tax deduction purposes. Fifth, submit for manager approval. Approved
reports are paid via direct deposit within 5-7 business days. Rejected reports
come back with a comment explaining what needs to change; fix and resubmit.
"""),

    (3, "Per diem meal allowances by city",
"""Per diem meal allowances vary by city because meal costs vary. Tier 1 cities
(San Francisco, New York, Boston, Seattle, DC): $75/day. Tier 2 (Chicago, LA,
Dallas, Austin, Denver): $60/day. Tier 3 (all other US cities): $50/day.
International cities follow the US State Department per diem tables.

Per diem covers three meals — breakfast $15, lunch $25, dinner $35 in Tier 1
proportion. If a meal is provided by the client, event, or hotel, deduct that
meal's per diem amount from your claim. Alcohol is not covered by per diem —
it's a separate line item requiring manager pre-approval and is capped at $50
per person per client dinner.
"""),

    (4, "Receipt scanning: what makes a receipt readable to OCR",
"""The Concur OCR engine reads receipts to auto-extract vendor, date, and amount.
Getting this right saves you manual entry time. Good receipts: clear photograph
in good lighting, the entire receipt visible (no cropping), taken on a plain
contrasting background (not a busy tablecloth), no glare.

Thermal receipts (the kind from most restaurants and gas stations) fade over
time — scan them within 48 hours. Handwritten receipts don't OCR well; type
the info manually. Digital email receipts (from Amazon, Uber, etc.) are the
easiest — forward directly to receipts@concur and Concur imports them without
any OCR at all.
"""),

    (5, "Corporate card vs personal card: when to use which",
"""Use the corporate card for any expense over $50. It goes straight to the
company account so you don't front the money and wait for reimbursement.
Corporate card charges auto-import into Concur; you still need to categorize
them and attach receipts, but no manual entry.

Personal card is appropriate for expenses under $50, informal team lunches
where cost-splitting is easier on personal cards, or in vendors that don't
accept corporate cards. Any expense under $10 doesn't require a receipt —
just log it manually. Cash reimbursements are strongly discouraged; use
cards whenever possible for audit trail.
"""),

    (6, "Client entertainment: rules and approval flow",
"""Entertaining clients has stricter rules than regular meals. First, you need
pre-approval from your VP for any client dinner over $500 total. Second, you
must maintain an attendee list — full names, companies, and their role in the
current or potential business relationship. Third, the venue matters: strip
clubs, casinos, and adult entertainment are never reimbursable regardless of
business purpose or clientele.

Golf outings, sporting events, and concerts are 50% deductible under IRS rules
and require attendee lists. Home entertainment (hosting clients at your house)
is reimbursable up to $200/person and requires manager approval. Alcohol at
client events is separately capped at $150 per person per event.
"""),

    (7, "Business travel policy: booking, class, and advance booking rules",
"""All flight bookings must go through the corporate travel portal (currently
BCD Travel). Domestic economy is the default class. Business class is approved
for flights over 6 hours or transatlantic/transpacific segments. First class
is never approved for reimbursement regardless of duration.

Book flights at least 14 days in advance to qualify for standard reimbursement.
Late bookings (within 7 days of travel) require a written justification and
manager approval. Hotel bookings should use preferred hotels for negotiated rates;
non-preferred hotels are approved if preferred are unavailable or over 30 minutes
from the meeting location.
"""),

    (8, "How rejections work and how to handle them",
"""Expense reports come back rejected for several reasons: missing receipt,
duplicate submission, wrong category, no business purpose, or violates policy.
The rejection email lists the specific issue and the specific line item.

To handle: open the rejected report in Concur, click the flagged line item,
address the issue (upload the missing receipt, correct the category, add the
business purpose text), and resubmit. Don't create a new report — the old
number is your audit trail. If you disagree with a rejection, comment on the
line item and re-submit; your manager can override with a note.
"""),

    (9, "Foreign currency: exchange rates and receipts in other languages",
"""When you have receipts in foreign currency, Concur converts them at the
day-of-transaction exchange rate published by OANDA. If the corporate card
was used, the actual USD amount charged by the card processor is what's
reimbursed — this may differ slightly from the OANDA rate because card
processors add a currency conversion fee (typically 3%).

Receipts in non-English languages are acceptable as long as the amount,
date, and vendor are legible. Translate the description or category
yourself when entering into Concur. Some countries (Japan, Korea) provide
translated meal receipts for foreign travelers on request — worth asking.
"""),

    (10, "Common reimbursement mistakes and how to avoid them",
"""The five most common expense errors: (1) Missing itemized receipt — you
have a total but not what was ordered. Restaurants will reprint itemized
receipts on request; hotels itemize by default. (2) Wrong project code —
double-check with your project manager before submitting. (3) Mixing
personal and business (adding a personal item to a business dinner tab).
Never allowed. (4) Late submission — reports over 60 days old need VP
approval to reimburse. (5) Duplicate submission — usually because you
resubmit a rejected report as a new one. Always resubmit the original.
"""),
]


def make_rows(user_id: str, posts: list) -> list:
    """Turn (post_id, title, content) tuples into rows ready for embedding."""
    rows: list = []
    for post_id, title, content in posts:
        # Prepend the title so it's included in the embedded content
        full_text = f"# {title}\n\n{content}"
        chunks = chunk_markdown(full_text)
        for c in chunks:
            rows.append({
                "user_id": user_id,
                "post_id": post_id,
                "chunk_index": c.chunk_index,
                "content": c.content,
                "title": title,
                "section_path": c.section_path or None,
                "source_type": "blog_post",
                "source_url": f"local://{user_id}/post-{post_id}",
            })
    return rows


def main() -> int:
    ensure_collection()

    all_rows = []
    all_rows.extend(make_rows("praveen", PRAVEEN_POSTS))
    all_rows.extend(make_rows("prakash", PRAKASH_POSTS))

    # Idempotent wipe: delete all chunks for each seed post before re-inserting.
    # Because point IDs are deterministic UUIDs, upsert alone would overwrite in
    # place — but if we ever change chunking, old chunks with higher indexes
    # would be orphaned. Delete-then-upsert is safest for a re-seedable script.
    print("Wiping existing chunks for seed users...")
    for user_id, posts in [("praveen", PRAVEEN_POSTS), ("prakash", PRAKASH_POSTS)]:
        for post_id, _, _ in posts:
            delete_by_post(user_id, post_id)

    print(f"Chunked {len(all_rows)} chunks total.")
    print("  praveen chunks:", sum(1 for r in all_rows if r["user_id"] == "praveen"))
    print("  prakash chunks:", sum(1 for r in all_rows if r["user_id"] == "prakash"))

    print(f"\nEmbedding {len(all_rows)} chunks via Titan (Bedrock)...")
    t0 = time.time()
    texts = [r["content"] for r in all_rows]
    vecs = embed_batch(texts)
    for r, v in zip(all_rows, vecs):
        r["embedding"] = v
    print(f"Embedded in {time.time() - t0:.1f}s")

    print("\nUpserting to Qdrant...")
    n = upsert_chunks(all_rows)
    print(f"Upserted {n} points.")

    print(f"\nCollection totals:")
    print(f"  praveen: {count_documents(user_id='praveen')}")
    print(f"  prakash: {count_documents(user_id='prakash')}")
    print(f"  total:   {count_documents()}")

    approx_input_chars = sum(len(t) for t in texts)
    print(f"\nApprox Titan input: {approx_input_chars} chars (~{approx_input_chars // 4} tokens)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
