# Lambda container image for askLambda (streaming)
# Includes fastembed for query-time BM25 sparse embedding
FROM public.ecr.aws/lambda/python:3.12

COPY ask/requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Pre-download BM25 model
ENV FASTEMBED_CACHE_DIR=/opt/fastembed_cache
RUN mkdir -p /opt/fastembed_cache && \
    python -c "from fastembed import SparseTextEmbedding; SparseTextEmbedding('Qdrant/bm25', cache_dir='/opt/fastembed_cache')"

# Copy shared common utilities
COPY common/ ${LAMBDA_TASK_ROOT}/common/

# Copy Lambda code
COPY ask/handler.py ${LAMBDA_TASK_ROOT}/
COPY ask/llm.py ${LAMBDA_TASK_ROOT}/

CMD [ "handler.handler" ]
