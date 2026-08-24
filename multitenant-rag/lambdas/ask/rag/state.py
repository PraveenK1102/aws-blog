"""Typed LangGraph state for the production routed path.

Every key the graph reads MUST be declared. LangGraph strips keys absent from
the schema, and in the offline v1 experiment that silently emptied the tenant
scope — a security-relevant failure. The `scope` key is therefore declared
explicitly and carried as an immutable `Scope`.

Reducers:
  * branch_results   — operator.add, the fan-in accumulator for parallel Sends
  * node_latencies / tokens — dict merge
  * errors           — operator.add, error CLASS NAMES only (never messages)
"""
import operator
from typing import Annotated, Any, TypedDict

from .scope import Scope


def _merge(a: dict, b: dict) -> dict:
    return {**a, **b}


class RoutedState(TypedDict, total=False):
    # --- request identity / input ---
    request_id: str
    question: str                 # the user's question, verbatim
    retrieval_query: str          # question, or the folded follow-up form
    history: list[dict]           # prior turns for the generation call
    scope: Scope                  # RESOLVED, AUTHORIZED, immutable

    # --- semantic cache ---
    cache_eligible: bool
    cache_hit: bool
    cached_answer: str
    cached_citations: list[dict]
    query_dense: list[float] | None   # embedded once, reused for retrieval

    # --- routing (frozen Router V2) ---
    needs_decomposition: bool
    router_reason_code: str | None
    router_information_needs: list[str]   # DIAGNOSTIC ONLY — never a query
    router_parse_ok: bool
    router_failed: bool                   # provider/parse failure -> simple path

    # --- decomposition ---
    subquestions: list[str]
    decomposition_used: bool
    decomposition_unusable: bool          # fallback to normal retrieval
    decomposition_failed: bool

    # --- retrieval ---
    branch_results: Annotated[list[dict], operator.add]
    branch_count: int
    top_dense: float
    retrieval_candidate_count: int
    relevance_floor_passed: bool
    partial_branch_failure: bool
    successful_branch_count: int
    failed_branch_count: int

    # --- context / generation ---
    merged_context: list[Any]             # the FINAL capped list (<= 5)
    merged_context_map: list[dict]
    system_prompt: str
    user_prompt: str
    answer_path: str                      # "simple" | "compound" | "cache"
    final_answer: str
    citations: list[dict]
    result_type: str

    # --- observability ---
    node_latencies: Annotated[dict, _merge]
    tokens: Annotated[dict, _merge]
    errors: Annotated[list[str], operator.add]
