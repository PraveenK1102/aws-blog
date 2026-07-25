"""Markdown-aware structural chunker.

Strategy (priority order for chunk boundaries):
  1. Markdown headers (H1 → H6)
  2. Horizontal rules (---)
  3. Paragraph breaks (blank line)
  4. Sentence boundaries (fallback within long paragraph)

Each chunk includes the header hierarchy (e.g., "H1 / H2 / H3") prepended
so chunks are self-contained context units.

Token count is approximate — we use char/4 as a fast heuristic. Real token
counting would require the tokenizer, adding complexity without much gain
here.
"""

import re
from dataclasses import dataclass


HEADER_RE = re.compile(r"^(#{1,6})\s+(.+)$")
HR_RE = re.compile(r"^-{3,}\s*$")
SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


@dataclass
class Chunk:
    text: str          # chunk content (with header path prepended)
    header_path: str   # "H1 / H2 / H3" or "" for no headers
    char_count: int


def chunk_markdown(content: str, max_tokens: int = 500, overlap_tokens: int = 50) -> list[Chunk]:
    """
    Split markdown content into semantically coherent chunks.

    Args:
        content: raw markdown text
        max_tokens: soft ceiling for chunk size (approximated as chars/4)
        overlap_tokens: overlap between adjacent chunks in the same section

    Returns:
        List of Chunk objects, in document order.
    """
    max_chars = max_tokens * 4
    overlap_chars = overlap_tokens * 4

    sections = _split_by_headers(content)
    chunks: list[Chunk] = []

    for header_path, section_text in sections:
        chunks.extend(_chunk_section(header_path, section_text, max_chars, overlap_chars))

    # Filter out empty chunks (rare, but possible if a section has only a header)
    return [c for c in chunks if c.text.strip()]


def _split_by_headers(content: str) -> list[tuple[str, str]]:
    """
    Split content by markdown headers, tracking the header hierarchy.

    Returns list of (header_path, section_body) where header_path is a
    " / "-joined string of active headers ("" if no headers seen yet).
    """
    lines = content.splitlines()
    sections: list[tuple[str, str]] = []
    header_stack: list[tuple[int, str]] = []  # [(level, title), ...]
    current_body: list[str] = []

    def flush():
        if current_body:
            path = " / ".join(title for _, title in header_stack)
            sections.append((path, "\n".join(current_body).strip()))

    for line in lines:
        m = HEADER_RE.match(line)
        if m:
            # New header — flush current section
            flush()
            current_body = []

            level = len(m.group(1))
            title = m.group(2).strip()

            # Pop deeper-or-equal levels from stack, then push new
            while header_stack and header_stack[-1][0] >= level:
                header_stack.pop()
            header_stack.append((level, title))
            continue

        current_body.append(line)

    flush()

    # If no headers at all, treat whole doc as one section with empty path
    if not sections:
        sections.append(("", content.strip()))

    return sections


def _chunk_section(header_path: str, section: str, max_chars: int, overlap_chars: int) -> list[Chunk]:
    """
    Chunk a single section by paragraphs, respecting size ceiling.

    If a single paragraph exceeds max_chars, split by sentences.
    """
    if not section.strip():
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", section) if p.strip()]
    chunks: list[Chunk] = []

    buffer: list[str] = []
    buffer_chars = 0

    def emit():
        if not buffer:
            return
        text = "\n\n".join(buffer)
        # Prepend header path for context
        full_text = f"[{header_path}]\n{text}" if header_path else text
        chunks.append(Chunk(text=full_text, header_path=header_path, char_count=len(text)))

    for para in paragraphs:
        para_chars = len(para)

        # Paragraph itself too big — split by sentences
        if para_chars > max_chars:
            if buffer:
                emit()
                buffer, buffer_chars = _get_overlap(buffer, overlap_chars), 0
                buffer_chars = sum(len(p) + 2 for p in buffer)
            # Split oversized paragraph by sentences
            for sent_chunk in _chunk_by_sentences(para, max_chars, overlap_chars):
                text = sent_chunk
                full_text = f"[{header_path}]\n{text}" if header_path else text
                chunks.append(Chunk(text=full_text, header_path=header_path, char_count=len(text)))
            continue

        # Would this paragraph overflow the buffer?
        if buffer and buffer_chars + para_chars + 2 > max_chars:
            emit()
            buffer = _get_overlap(buffer, overlap_chars)
            buffer_chars = sum(len(p) + 2 for p in buffer)

        buffer.append(para)
        buffer_chars += para_chars + 2  # +2 for "\n\n" separator

    emit()

    return chunks


def _chunk_by_sentences(paragraph: str, max_chars: int, overlap_chars: int) -> list[str]:
    """Fallback for oversized paragraphs — split by sentence."""
    sentences = SENTENCE_END_RE.split(paragraph)
    chunks: list[str] = []
    buffer: list[str] = []
    buffer_chars = 0

    for sent in sentences:
        sent_chars = len(sent)
        if buffer and buffer_chars + sent_chars + 1 > max_chars:
            chunks.append(" ".join(buffer))
            buffer = _get_overlap(buffer, overlap_chars)
            buffer_chars = sum(len(s) + 1 for s in buffer)
        buffer.append(sent)
        buffer_chars += sent_chars + 1

    if buffer:
        chunks.append(" ".join(buffer))

    return chunks


def _get_overlap(items: list[str], target_chars: int) -> list[str]:
    """Return trailing items from list, up to target_chars total."""
    if not items or target_chars <= 0:
        return []

    result: list[str] = []
    total = 0
    for item in reversed(items):
        item_chars = len(item)
        if total + item_chars > target_chars and result:
            break
        result.insert(0, item)
        total += item_chars
    return result
