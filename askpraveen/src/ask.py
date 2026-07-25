from __future__ import annotations

from typing import List, Optional

import boto3

from .config import AWS_REGION, BEDROCK_LLM_MODEL_ID, TOP_K
from .db import vector_search
from .embeddings import embed_text

_bedrock_client = None


def _bedrock():
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    return _bedrock_client


SYSTEM_PROMPT_TEMPLATE = (
    "You are AskPraveen, an assistant that answers questions about a specific user "
    "based ONLY on the retrieved excerpts from that user's blog posts. "
    "The excerpts below are from user '{user_id}'. Answer only about {user_id}, "
    "using only their content.\n\n"
    "Rules:\n"
    "- If the excerpts don't contain the answer, say plainly that {user_id}'s posts "
    "don't cover that topic. Do not invent facts. Do not answer from general knowledge.\n"
    "- Cite sources by their [N] number when you use them.\n"
    "- Be concise and direct. Prefer specifics from the excerpts over generic advice.\n"
    "- Answer in third person: '{user_id} writes about…' or 'According to {user_id}\\'s "
    "posts…'. Mirror first-person only inside direct quotes from the excerpts."
)


def _format_context(hits: list) -> tuple:
    lines: List[str] = []
    sources: List[dict] = []
    for i, row in enumerate(hits, start=1):
        (
            _id, source_type, source_url, source_path, title,
            section_path, chunk_index, content, score,
        ) = row
        header = f"[{i}] {title}"
        if section_path:
            header += f" — {section_path}"
        lines.append(f"{header}\n{content}")
        sources.append({
            "n": i,
            "title": title,
            "source_type": source_type,
            "source_url": source_url,
            "source_path": source_path,
            "section_path": section_path,
            "chunk_index": chunk_index,
            "score": round(float(score), 4),
        })
    return "\n\n---\n\n".join(lines), sources


def ask(
    question: str,
    user_id: str,
    top_k: int = TOP_K,
    model_id: Optional[str] = None,
) -> dict:
    """Multi-tenant RAG: retrieve chunks scoped to user_id, then generate an answer.

    Args:
        question: user's question.
        user_id: tenant key. ONLY chunks with this user_id in payload are searched.
        top_k: how many chunks to retrieve.
        model_id: override for BEDROCK_LLM_MODEL_ID.

    Returns dict with keys: answer, sources, usage, model_id, user_id.
    Bedrock Converse API means the same call works for Claude, Nova, Llama, Mistral.
    """
    q_vec = embed_text(question)
    hits = vector_search(q_vec, top_k, user_id=user_id)
    context, sources = _format_context(hits)

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(user_id=user_id)

    if not hits:
        # Short-circuit: no chunks matched the user_id filter. Skip the LLM call —
        # cheaper and the answer is deterministic.
        return {
            "answer": (
                f"I don't have any content from user '{user_id}' yet. "
                "Once they post, ask again."
            ),
            "sources": [],
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "model_id": model_id or BEDROCK_LLM_MODEL_ID,
            "user_id": user_id,
        }

    user_msg = (
        f"Retrieved excerpts from {user_id}'s posts:\n\n{context}\n\n"
        f"---\n\nQuestion: {question}\n\n"
        f"Answer using only the excerpts above. Cite as [N]."
    )

    mid = model_id or BEDROCK_LLM_MODEL_ID
    resp = _bedrock().converse(
        modelId=mid,
        system=[{"text": system_prompt}],
        messages=[{"role": "user", "content": [{"text": user_msg}]}],
        inferenceConfig={"maxTokens": 800, "temperature": 0.2},
    )
    answer = "".join(
        block.get("text", "")
        for block in resp["output"]["message"]["content"]
        if "text" in block
    ).strip()

    usage = resp.get("usage", {})
    return {
        "answer": answer,
        "sources": sources,
        "usage": {
            "input_tokens": usage.get("inputTokens"),
            "output_tokens": usage.get("outputTokens"),
            "total_tokens": usage.get("totalTokens"),
        },
        "model_id": mid,
        "user_id": user_id,
    }
