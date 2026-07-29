# Setup — social + groups + global search (Phase 1–3)

New feature branch `feat/social-and-search`. Adds: follow/unfollow, groups, group ask
(multi-tenant), and LLM-free global discovery search. **No vector-DB migration** — group
search is a `tenant_id IN [...]` filter and global search is "no filter" on the existing
`multitenant_chunks` collection.

## 1. New DynamoDB tables (prod — real AWS, ap-south-1)

Dev/LocalStack tables are already in `local/bootstrap_dev.sh`. For prod, create:

```bash
R=ap-south-1

aws dynamodb create-table --region $R --table-name multitenant-follows \
  --attribute-definitions AttributeName=follower_id,AttributeType=S AttributeName=followee_id,AttributeType=S \
  --key-schema AttributeName=follower_id,KeyType=HASH AttributeName=followee_id,KeyType=RANGE \
  --global-secondary-indexes '[{"IndexName":"by_followee","KeySchema":[{"AttributeName":"followee_id","KeyType":"HASH"},{"AttributeName":"follower_id","KeyType":"RANGE"}],"Projection":{"ProjectionType":"KEYS_ONLY"}}]' \
  --billing-mode PAY_PER_REQUEST

aws dynamodb create-table --region $R --table-name multitenant-groups \
  --attribute-definitions AttributeName=group_id,AttributeType=S \
  --key-schema AttributeName=group_id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST

aws dynamodb create-table --region $R --table-name multitenant-group-members \
  --attribute-definitions AttributeName=group_id,AttributeType=S AttributeName=user_id,AttributeType=S \
  --key-schema AttributeName=group_id,KeyType=HASH AttributeName=user_id,KeyType=RANGE \
  --global-secondary-indexes '[{"IndexName":"by_member","KeySchema":[{"AttributeName":"user_id","KeyType":"HASH"},{"AttributeName":"group_id","KeyType":"RANGE"}],"Projection":{"ProjectionType":"ALL"}}]' \
  --billing-mode PAY_PER_REQUEST
```

## 2. IAM — widen the `ask` Lambda role
The `ask` Lambda now reads/writes the three new tables + their GSIs. Add to its role policy
(Query/GetItem/PutItem/DeleteItem) the ARNs for `multitenant-follows`, `multitenant-groups`,
`multitenant-group-members`, and their indexes (`.../index/*`). **LocalStack doesn't enforce IAM,
so this only bites in prod — audit before deploying** (this bit us once before).

## 3. Env vars (optional)
The code defaults to the table names above, so no env change is needed unless you rename them:
`FOLLOWS_TABLE`, `GROUPS_TABLE`, `GROUP_MEMBERS_TABLE`.

## 4. Deploy
Only the **ask** image changed (all new endpoints live there) + the frontend. createpost /
ingestworker are untouched. Build ask → `update-function-code` by SHA → frontend `s3 sync` +
CloudFront invalidation.

## 5. Notes / limits (v1)
- **Group ask** is **stateless** (no saved-chat memory) unless a `chat_id` is passed — kept simple
  to avoid a chats-table schema change. Add multi-target saved chats later.
- **Group search** uses `tenant_id IN [members]` (fine for small/medium groups). For very large
  groups, switch to a `group_id` payload tag on vectors (re-tag on join/leave) — deferred.
- **Global search** is LLM-free discovery (vector search only). Rate-limit it at API Gateway/WAF;
  add a reranker when result quality on the big pool needs it.
- **Isolation:** group + global cross the per-writer wall **by design** → they only ever surface
  PUBLIC posts (every post is public today). If a private/draft state is ever added, exclude it here.
