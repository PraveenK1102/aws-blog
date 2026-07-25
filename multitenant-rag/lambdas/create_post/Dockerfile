# Lambda container image for createPostLambda
FROM public.ecr.aws/lambda/python:3.12

# Install Python dependencies
COPY create_post/requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Copy shared common utilities
COPY common/ ${LAMBDA_TASK_ROOT}/common/

# Copy Lambda handler
COPY create_post/handler.py ${LAMBDA_TASK_ROOT}/

# Lambda entry point
CMD [ "handler.handler" ]
