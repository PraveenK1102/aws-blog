from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
CODE_FENCE_RE = re.compile(r"^```", re.MULTILINE)

# Target ~500 tokens per chunk assuming ~4 chars/token -> ~2000 chars.
# Overlap ~100 tokens -> ~400 chars.
MAX_CHARS = 2000
OVERLAP_CHARS = 400


@dataclass
class Chunk:
    section_path: str
    chunk_index: int
    content: str


def _mask_code_fences(text: str) -> List[tuple]:
    """Return list of (start, end) ranges that are inside fenced code blocks.
    Used to avoid splitting inside a code fence.
    """
    fences = [m.start() for m in CODE_FENCE_RE.finditer(text)]
    ranges = []
    for i in range(0, len(fences) - 1, 2):
        ranges.append((fences[i], fences[i + 1]))
    return ranges


def _in_code_fence(pos: int, ranges: List[tuple]) -> bool:
    for start, end in ranges:
        if start <= pos <= end:
            return True
    return False


def _split_by_headings(text: str) -> List[tuple]:
    """Return list of (section_path, body_text) tuples.
    section_path is 'H1 > H2 > H3' style joined heading trail.
    """
    code_ranges = _mask_code_fences(text)
    matches = [
        m for m in HEADING_RE.finditer(text)
        if not _in_code_fence(m.start(), code_ranges)
    ]

    if not matches:
        return [("", text.strip())]

    sections: List[tuple] = []
    heading_stack: List[tuple] = []  # (level, title)

    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.append(("", preamble))

    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        heading_stack = [(lvl, t) for lvl, t in heading_stack if lvl < level]
        heading_stack.append((level, title))

        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        section_path = " > ".join(t for _, t in heading_stack)
        if body:
            sections.append((section_path, body))
    return sections


def _window_split(body: str, max_chars: int, overlap: int) -> List[str]:
    if len(body) <= max_chars:
        return [body]
    windows = []
    start = 0
    while start < len(body):
        end = min(start + max_chars, len(body))
        # Try to end at a paragraph break for readability
        if end < len(body):
            nl = body.rfind("\n\n", start + max_chars // 2, end)
            if nl != -1 and nl > start:
                end = nl
        windows.append(body[start:end].strip())
        if end == len(body):
            break
        start = max(end - overlap, start + 1)
    return [w for w in windows if w]


def chunk_markdown(text: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP_CHARS) -> List[Chunk]:
    chunks: List[Chunk] = []
    idx = 0
    for section_path, body in _split_by_headings(text):
        for window in _window_split(body, max_chars, overlap):
            chunks.append(Chunk(section_path=section_path, chunk_index=idx, content=window))
            idx += 1
    return chunks
