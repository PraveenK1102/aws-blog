#!/usr/bin/env python3
"""Walk the blog repo, chunk markdown, embed via Titan, upsert into pgvector.

Content sources (configurable via CONTENT_ROOT env var):
- learnings/*.md          -> source_type=learning_note
- AWS-LEARNING-PLAN.md    -> source_type=learning_plan
- CLAUDE.md               -> source_type=project_doc
- README.md               -> source_type=readme
- blog-backend/README.md  -> source_type=readme
- blog-frontend*/README.md-> source_type=readme
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# Allow `python scripts/ingest.py` from the askpraveen dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.chunker import chunk_markdown
from src.config import CONTENT_ROOT
from src.db import count_documents, upsert_chunks
from src.embeddings import embed_batch


INCLUDES = [
    ("learnings/**/*.md", "learning_note"),
    ("AWS-LEARNING-PLAN.md", "learning_plan"),
    ("CLAUDE.md", "project_doc"),
    ("README.md", "readme"),
    ("blog-backend/README.md", "readme"),
    ("blog-frontend-nextjs/README.md", "readme"),
]

EXCLUDE_SUBSTRINGS = ("node_modules", ".git/", "askpraveen/")


def discover_files() -> list:
    root = Path(CONTENT_ROOT)
    found: list = []
    seen: set = set()
    for pattern, source_type in INCLUDES:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            spath = str(path)
            if any(x in spath for x in EXCLUDE_SUBSTRINGS):
                continue
            if spath in seen:
                continue
            seen.add(spath)
            found.append((path, source_type))
    return found


def derive_title(path: Path, text: str) -> str:
    for line in text.splitlines()[:20]:
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem.replace("-", " ").replace("_", " ").title()


def source_url_for(path: Path) -> str:
    rel = path.relative_to(CONTENT_ROOT)
    return f"local://{rel}"


def main() -> int:
    files = discover_files()
    if not files:
        print(f"No content files found under {CONTENT_ROOT}")
        return 1

    print(f"Content root : {CONTENT_ROOT}")
    print(f"Files to ingest: {len(files)}")
    for p, st in files:
        print(f"  [{st:14}] {p.relative_to(CONTENT_ROOT)}")

    all_rows: list = []
    total_chunks = 0
    t_chunk_start = time.time()
    for path, source_type in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        title = derive_title(path, text)
        chunks = chunk_markdown(text)
        total_chunks += len(chunks)
        for c in chunks:
            all_rows.append({
                "source_type": source_type,
                "source_url": source_url_for(path),
                "source_path": str(path.relative_to(CONTENT_ROOT)),
                "title": title,
                "section_path": c.section_path or None,
                "chunk_index": c.chunk_index,
                "content": c.content,
                "metadata": {
                    "chars": len(c.content),
                    "bytes": len(c.content.encode("utf-8")),
                },
            })
    t_chunk = time.time() - t_chunk_start
    print(f"\nChunked {total_chunks} chunks from {len(files)} files in {t_chunk:.2f}s")

    print(f"\nEmbedding {len(all_rows)} chunks via Titan...")
    t_embed = time.time()
    texts = [r["content"] for r in all_rows]
    vecs = embed_batch(texts)
    for r, v in zip(all_rows, vecs):
        r["embedding"] = v
    dt_embed = time.time() - t_embed
    print(f"Embedded in {dt_embed:.2f}s ({dt_embed / max(len(vecs), 1):.3f}s/chunk avg)")

    print("\nUpserting to Postgres...")
    n = upsert_chunks(all_rows)
    total = count_documents()
    print(f"Upserted {n} rows. Total rows in documents: {total}")

    approx_input_chars = sum(len(t) for t in texts)
    print(f"\nApprox Titan input chars: {approx_input_chars} (~{approx_input_chars // 4} tokens)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
