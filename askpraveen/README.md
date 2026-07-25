# AskPraveen — Cloud RAG on AWS

Public single-tenant RAG that answers questions about Praveen from his public content (blog posts, learning notes, project READMEs). Cloud-RAG sibling to the on-prem [ticket-manager](../../ticket-manager) project.

## Stack (v1)

| Layer            | Choice                          |
|------------------|---------------------------------|
| LLM              | Bedrock Claude 3 Haiku          |
| Embeddings       | Bedrock Titan Embeddings v2 (1024-dim) |
| Vector store     | pgvector on Postgres (local for dev, RDS in prod) |
| Runtime          | Python 3.9+ (Lambda-ready)      |
| API (later)      | API Gateway + Lambda            |
| Frontend (later) | React sidebar in existing blog  |

## Session 1 status

- [x] Scaffold layout
- [x] Local Docker Postgres + pgvector
- [x] documents schema with HNSW index
- [x] Ingestion script (heading-aware markdown chunker → Titan → Postgres)
- [x] `ask()` function (embed → vector search → Claude Haiku)
- [x] End-to-end tested locally
- [ ] RDS pgvector enablement (see [runbooks/rds-enable-pgvector.md](runbooks/rds-enable-pgvector.md))
- [ ] Lambda deploy (Session 3)

## Quickstart

```bash
# 1. Local Postgres + pgvector
docker compose up -d
docker compose exec db psql -U askpraveen -d askpraveen -f /docker-entrypoint-initdb.d/001_schema.sql

# 2. Python env
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# .env defaults point at the local docker Postgres; leave AWS creds to ~/.aws/credentials

# 4. Ingest local markdown content
python scripts/ingest.py

# 5. Ask
python scripts/query.py "What AWS services did Praveen use for the blog backend?"
```

## Layout

```
askpraveen/
├── README.md                       # this file
├── docker-compose.yml              # local Postgres + pgvector
├── requirements.txt                # boto3, psycopg2-binary, python-dotenv
├── .env.example                    # DATABASE_URL, AWS_REGION, model IDs
├── sql/
│   └── 001_schema.sql              # documents table + HNSW index
├── src/
│   ├── config.py                   # env + model ID resolution
│   ├── db.py                       # psycopg2 connection + query helpers
│   ├── embeddings.py               # Titan embed (single + batch)
│   ├── chunker.py                  # heading-aware markdown → chunks
│   └── ask.py                      # ask(question) -> {answer, sources}
├── scripts/
│   ├── ingest.py                   # walk repo markdown, chunk, embed, upsert
│   └── query.py                    # CLI wrapper around ask()
└── runbooks/
    └── rds-enable-pgvector.md      # steps to enable pgvector on RDS
```

## Interview narrative

Cloud-RAG mirror to on-prem ticket-manager. Same retrieval patterns (vector → hybrid BM25+vector+RRF → HyDE → reranker), different infrastructure primitives (Bedrock vs Ollama, Lambda vs FastAPI, pgvector vs ChromaDB). Chosen because the data is public and the deployment needs to be cheap+scalable.
