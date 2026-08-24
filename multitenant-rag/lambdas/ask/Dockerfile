# askLambda (streaming) — FastAPI served by uvicorn, bridged to Lambda by the
# Lambda Web Adapter. Plain Python base works: the Runtime Interface Client
# ships inside the LWA binary.
FROM public.ecr.aws/docker/library/python:3.12-slim

# Lambda Web Adapter as an extension. Buffered mode so the function works behind
# API Gateway (CloudFront -> API Gateway -> here). LWA collects the FastAPI
# StreamingResponse into one response; the frontend parses the NDJSON events and
# typewriter-animates them. Flip to response_stream + a RESPONSE_STREAM Function
# URL to get true edge streaming (proven working; see Phase 3D notes).
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:1.0.1 /lambda-adapter /opt/extensions/lambda-adapter
ENV AWS_LWA_INVOKE_MODE=buffered
ENV AWS_LWA_PORT=8080

WORKDIR /var/task

COPY ask/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the BM25 model into the image so cold starts don't fetch it.
ENV FASTEMBED_CACHE_DIR=/opt/fastembed_cache
RUN mkdir -p /opt/fastembed_cache && \
    python -c "from fastembed import SparseTextEmbedding; SparseTextEmbedding('Qdrant/bm25', cache_dir='/opt/fastembed_cache')"

COPY common/ ./common/
COPY ask/app.py ask/llm.py ./
# Routed RAG package (LangGraph). Production graph logic lives here; it
# never imports evals/, and evals/ is never copied into the image.
COPY ask/rag/ ./rag/

# uvicorn serves the FastAPI app on the port LWA proxies to.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
