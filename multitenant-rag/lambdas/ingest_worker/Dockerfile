# Lambda container image for ingestWorkerLambda
# Larger image because it includes fastembed (BM25 model)
FROM public.ecr.aws/lambda/python:3.12

# Install dependencies
COPY ingest_worker/requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Pre-download the BM25 model into the image so cold starts don't fetch it
# fastembed caches models under ~/.cache/fastembed by default.
ENV FASTEMBED_CACHE_DIR=/opt/fastembed_cache
RUN mkdir -p /opt/fastembed_cache && \
    python -c "from fastembed import SparseTextEmbedding; SparseTextEmbedding('Qdrant/bm25', cache_dir='/opt/fastembed_cache')"

# Copy shared common utilities
COPY common/ ${LAMBDA_TASK_ROOT}/common/

# Copy Lambda code
COPY ingest_worker/handler.py ${LAMBDA_TASK_ROOT}/
COPY ingest_worker/chunker.py ${LAMBDA_TASK_ROOT}/

CMD [ "handler.handler" ]
