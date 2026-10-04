# LangSmith Export Summary

- **Export timestamp (UTC):** 2026-08-22T13:59:21.403994+00:00
- **Project:** multitenant-rag-prod
- **Total traces (root runs):** 130
- **Total spans/runs exported:** 792
- **Earliest run start:** 2026-08-21T16:54:07.750654+00:00
- **Latest run start:** 2026-08-22T10:31:49.904780+00:00
- **Errored runs:** 8

## Count by run name
- dense_embedding: 127
- retrieval: 113
- hybrid_qdrant_search: 98
- groq_generation: 94
- completion: 88
- context_preparation: 65
- group_ask_request: 65
- ask_request: 50
- semantic_cache: 47
- result_processing: 15
- qdrant_search: 15
- global_search_request: 15

## Safety
Exported: only whitelisted operational/timing/token fields (trace_id, run_id, parent_run_id, name,
run_type, status, start/end_time, latency_ms, error, error_type, model, input/output/total_tokens,
result_type, cache_hit, hits, top_dense, citation_count, environment, has_history, target_count,
result_count, dims, floor, reused_query_embedding, embedded_internally, request_id, retry_count).
EXCLUDED entirely: inputs, outputs, questions, answers, prompts, history, retrieved chunks/post text,
user IDs, tenant IDs, emails, JWTs, API keys, secrets. request_id is a random UUID (non-PII).
