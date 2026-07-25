#!/usr/bin/env python3
"""Multi-tenant isolation test.

Asks the SAME question with different user_id filters and verifies:
  - Right user  → confident answer with citations
  - Wrong user  → refusal or low-relevance sources

Strong isolation means the same question routed to different tenants
produces different, correct behavior. Weak isolation (different questions
per user) can be right by accident.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ask import ask


REFUSAL_MARKERS = (
    "don't have",
    "don't cover",
    "not cover",
    "doesn't cover",
    "no information",
    "not in the excerpts",
    "no content",
    "not covered",
    "does not appear",
    "not mention",
)

TEST_CASES = [
    {
        "name": "Engineering Q on Praveen (owner)",
        "question": "How does the Bedrock Converse API help with model portability?",
        "user_id": "praveen",
        "expect_confident": True,
    },
    {
        "name": "Engineering Q on Prakash (wrong tenant)",
        "question": "How does the Bedrock Converse API help with model portability?",
        "user_id": "prakash",
        "expect_confident": False,
    },
    {
        "name": "Reimbursement Q on Prakash (owner)",
        "question": "What are the per diem meal allowances by city tier?",
        "user_id": "prakash",
        "expect_confident": True,
    },
    {
        "name": "Reimbursement Q on Praveen (wrong tenant)",
        "question": "What are the per diem meal allowances by city tier?",
        "user_id": "praveen",
        "expect_confident": False,
    },
]


def looks_like_refusal(answer: str) -> bool:
    lower = answer.lower()
    return any(marker in lower for marker in REFUSAL_MARKERS)


def check_leaks(result: dict, expected_user_id: str) -> list:
    """Return list of source rows whose source_path shows a wrong user_id.
    (Would only happen if the filter failed silently.)
    """
    leaks = []
    for s in result.get("sources", []):
        sp = s.get("source_path", "")
        if sp and not sp.startswith(f"{expected_user_id}/"):
            leaks.append(sp)
    return leaks


def main() -> int:
    print("=" * 72)
    print("MULTI-TENANT ISOLATION TEST")
    print("=" * 72)
    passed = 0
    failed = 0

    for i, tc in enumerate(TEST_CASES, 1):
        print(f"\n[{i}/{len(TEST_CASES)}] {tc['name']}")
        print(f"    Q: {tc['question']}")
        print(f"    filter: user_id={tc['user_id']}")

        result = ask(tc["question"], user_id=tc["user_id"])
        answer = result["answer"]
        n_sources = len(result["sources"])
        top_score = result["sources"][0]["score"] if result["sources"] else None
        refusal = looks_like_refusal(answer)
        leaks = check_leaks(result, tc["user_id"])

        print(f"    -> {n_sources} sources retrieved, top score: {top_score}")
        print(f"    -> refusal detected: {refusal}")
        print(f"    -> cross-tenant leaks in sources: {len(leaks)}")
        print(f"    -> answer (first 300 chars):")
        print(f"       {answer[:300]}{'...' if len(answer) > 300 else ''}")

        # Pass/fail logic
        problems = []
        if leaks:
            problems.append(f"LEAK: sources from wrong user: {leaks}")
        if tc["expect_confident"]:
            if refusal:
                problems.append("expected confident answer, got refusal-shaped text")
            if n_sources == 0:
                problems.append("expected sources, got none")
        else:
            # Wrong-tenant case. Either the LLM refuses (best) or the retrieved
            # chunks are so off-topic that even a naive read makes the mismatch
            # obvious. Refusal is the strong signal.
            if not refusal:
                problems.append(
                    "wrong-tenant case: LLM did not refuse — check answer manually"
                )

        if problems:
            print("    FAIL:")
            for p in problems:
                print(f"       - {p}")
            failed += 1
        else:
            print("    PASS")
            passed += 1

    print("\n" + "=" * 72)
    print(f"RESULT: {passed} passed, {failed} failed / {len(TEST_CASES)} tests")
    print("=" * 72)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
