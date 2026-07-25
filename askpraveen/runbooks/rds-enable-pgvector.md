# Runbook — Enable pgvector on existing blog RDS

Ship criterion: `CREATE EXTENSION vector` succeeds on `blog-db.cnakgsquy4bt.ap-south-1.rds.amazonaws.com` and the schema in `askpraveen/sql/001_schema.sql` applies cleanly.

## Prerequisites — verify before you start

- Blog RDS instance identifier: `blog-db`, engine `postgres`, engine version `16.x`.
- RDS is in a private subnet (no public access) per Stage 1 notes. Connection must come from inside the VPC → we go via EC2 Instance Connect on the blog EC2 (`i-092269a5892039994`).
- pgvector 0.5.0+ ships in the default `rds.extensions` allowlist for PostgreSQL 15.5+ and 16 — no parameter group changes needed.
- Instance class is `db.t3.micro` (2 vCPU, 1 GiB RAM). Corpus is ~200 chunks × 1024-dim float32 = ~800 KB raw + ~2 MB HNSW index. Well within a t3.micro. Watch for OOM only if the corpus grows past ~50k rows or the concurrent load spikes.

## Steps

### 1. Open a shell on the blog EC2

Console → EC2 → `i-092269a5892039994` → Connect → **EC2 Instance Connect** → Connect. (Direct SSH is blocked by the Zoho corporate proxy per Stage 1 notes.)

### 2. Install the Postgres client if it's not there

```bash
which psql || sudo apt-get update -qq && sudo apt-get install -y postgresql-client
psql --version   # want 14+ ideally, any works
```

### 3. Connect to RDS

```bash
export PGHOST=blog-db.cnakgsquy4bt.ap-south-1.rds.amazonaws.com
export PGPORT=5432
export PGUSER=bloguser
export PGDATABASE=blogdb
psql   # enter password when prompted
```

Sanity check:

```sql
SELECT version();
SHOW rds.extensions;   -- should include 'vector' in the list
```

### 4. Create the extension

```sql
CREATE EXTENSION IF NOT EXISTS vector;
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';
-- expected: vector | 0.5.x or newer
```

### 5. Apply the AskPraveen schema

Two options. Pick one.

**Option A — paste inline** (fastest, no file transfer):

```sql
CREATE TABLE IF NOT EXISTS documents (
    id            BIGSERIAL PRIMARY KEY,
    source_type   TEXT        NOT NULL,
    source_url    TEXT        NOT NULL,
    source_path   TEXT        NOT NULL,
    title         TEXT        NOT NULL,
    section_path  TEXT,
    chunk_index   INT         NOT NULL,
    content       TEXT        NOT NULL,
    embedding     vector(1024),
    metadata      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    content_tsv   tsvector    GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_path, chunk_index)
);

CREATE INDEX IF NOT EXISTS documents_embedding_hnsw
    ON documents USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS documents_content_tsv
    ON documents USING gin (content_tsv);

CREATE INDEX IF NOT EXISTS documents_source_type
    ON documents (source_type);
```

**Option B — copy the file to EC2 first**:

```bash
# From your local Mac (via a signed URL, S3, or git pull on the EC2)
# On EC2:
cd ~ && git pull origin main   # if the repo is already checked out
psql -f askpraveen/sql/001_schema.sql
```

### 6. Verify

```sql
\d documents
SELECT indexdef FROM pg_indexes WHERE tablename = 'documents';
```

Expected output mirrors the local install: hnsw index on `embedding`, gin index on `content_tsv`, unique on `(source_path, chunk_index)`.

### 7. Instance-class sanity check

```sql
SELECT
    pg_size_pretty(pg_database_size(current_database())) AS db_size,
    (SELECT setting FROM pg_settings WHERE name='shared_buffers') AS shared_buffers,
    (SELECT setting FROM pg_settings WHERE name='maintenance_work_mem') AS maintenance_work_mem;
```

t3.micro defaults: `shared_buffers ≈ 256 MB`, `maintenance_work_mem ≈ 64 MB`. HNSW index build on <5k rows uses well under 100 MB. No tuning needed for Session 1 scale.

If the corpus later exceeds ~10k chunks, bump the RDS parameter group's `maintenance_work_mem` to 128 MB before rebuilding the HNSW index. Rebuild command: `REINDEX INDEX documents_embedding_hnsw;`.

## Rollback

```sql
DROP TABLE IF EXISTS documents;
DROP EXTENSION IF EXISTS vector;
```

Extension drop is safe — no blog app tables use `vector`. The blog's `User` and `Post` tables are untouched.

## Wire the Lambda later (Session 3)

- Same schema, same code — set `DATABASE_URL` env var on the Lambda to the RDS endpoint.
- Lambda must be in the same VPC as RDS + attached to a security group whose ingress is allowed by `blog-rds-sg`. Reuse `blog-backend-sg` or make a new `askpraveen-lambda-sg` and add its id to `blog-rds-sg` ingress on port 5432.
- Bedrock calls from a VPC Lambda: either add a **VPC endpoint for Bedrock runtime** (`com.amazonaws.ap-south-1.bedrock-runtime`), or give the Lambda a NAT gateway. VPC endpoint is cheaper for our volume.

## Cost impact

Adding pgvector to an existing db.t3.micro RDS: **$0 incremental**. Same instance, same storage tier. Free tier stays.
