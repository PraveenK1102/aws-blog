#!/usr/bin/env python3
"""Ask a question scoped to a specific user's content.

Usage:
    python scripts/query.py <user_id> "your question"
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ask import ask


def main(argv: list) -> int:
    if len(argv) < 3:
        print("usage: python scripts/query.py <user_id> \"your question\"", file=sys.stderr)
        return 2
    user_id = argv[1]
    question = " ".join(argv[2:])
    result = ask(question, user_id=user_id)

    print(f"\n=== ANSWER (about {user_id}) ===\n")
    print(result["answer"])
    print("\n=== SOURCES ===\n")
    if not result["sources"]:
        print("(no sources — either the corpus is empty for this user, "
              "or nothing matched)")
    for s in result["sources"]:
        print(
            f"[{s['n']}] score={s['score']:.4f}  {s['title']}"
            f"{'  ' + s['section_path'] if s['section_path'] else ''}"
        )
        print(f"     {s['source_path']}")
    print(f"\n=== USAGE ===\n{json.dumps(result['usage'])}")
    print(f"model: {result['model_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
