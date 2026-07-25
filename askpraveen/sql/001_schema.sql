CREATE EXTENSION IF NOT EXISTS vector;

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
