#!/usr/bin/env bash
# Create the dev AWS resources inside LocalStack (SQS + DynamoDB + S3).
# Mirrors production resource names, but lives only in LocalStack (isolated).
# NO mock/seed data — users sign up. Idempotent-ish: ignores "already exists".
set -uo pipefail

LS="--endpoint-url=http://localhost:4566"
export AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID:-test}
export AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY:-test}
export AWS_DEFAULT_REGION=ap-south-1

echo "=== DynamoDB: multitenant-users (PK user_id) + GSI by_email (for login) ==="
aws $LS dynamodb create-table --table-name multitenant-users \
  --attribute-definitions AttributeName=user_id,AttributeType=S AttributeName=email,AttributeType=S \
  --key-schema AttributeName=user_id,KeyType=HASH \
  --global-secondary-indexes '[{"IndexName":"by_email","KeySchema":[{"AttributeName":"email","KeyType":"HASH"}],"Projection":{"ProjectionType":"ALL"}}]' \
  --billing-mode PAY_PER_REQUEST --query 'TableDescription.TableStatus' --output text 2>&1

echo "=== DynamoDB: multitenant-tenants (PK tenant_id) ==="
aws $LS dynamodb create-table --table-name multitenant-tenants \
  --attribute-definitions AttributeName=tenant_id,AttributeType=S \
  --key-schema AttributeName=tenant_id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST --query 'TableDescription.TableStatus' --output text 2>&1

echo "=== DynamoDB: multitenant-posts (PK tenant_id, SK post_id) + GSI by_status ==="
aws $LS dynamodb create-table --table-name multitenant-posts \
  --attribute-definitions AttributeName=tenant_id,AttributeType=S AttributeName=post_id,AttributeType=S AttributeName=ingestion_status,AttributeType=S AttributeName=updated_at,AttributeType=N \
  --key-schema AttributeName=tenant_id,KeyType=HASH AttributeName=post_id,KeyType=RANGE \
  --global-secondary-indexes '[{"IndexName":"by_status","KeySchema":[{"AttributeName":"ingestion_status","KeyType":"HASH"},{"AttributeName":"updated_at","KeyType":"RANGE"}],"Projection":{"ProjectionType":"ALL"}}]' \
  --billing-mode PAY_PER_REQUEST --query 'TableDescription.TableStatus' --output text 2>&1

echo "=== DynamoDB: multitenant-usage-logs (PK tenant_date, SK timestamp_req) + TTL ==="
aws $LS dynamodb create-table --table-name multitenant-usage-logs \
  --attribute-definitions AttributeName=tenant_date,AttributeType=S AttributeName=timestamp_req,AttributeType=S \
  --key-schema AttributeName=tenant_date,KeyType=HASH AttributeName=timestamp_req,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST --query 'TableDescription.TableStatus' --output text 2>&1
aws $LS dynamodb update-time-to-live --table-name multitenant-usage-logs \
  --time-to-live-specification "Enabled=true,AttributeName=expires_at" --query 'TimeToLiveSpecification.TimeToLiveStatus' --output text 2>&1

echo "=== S3: praveen-multitenant-content ==="
aws $LS s3 mb s3://praveen-multitenant-content 2>&1

echo "=== SQS: multitenant-ingestion.fifo ==="
aws $LS sqs create-queue --queue-name multitenant-ingestion.fifo \
  --attributes FifoQueue=true,ContentBasedDeduplication=true --query 'QueueUrl' --output text 2>&1

echo ""
echo "=== dev resources present in LocalStack ==="
aws $LS dynamodb list-tables --query 'TableNames' --output text 2>&1
aws $LS s3 ls 2>&1
aws $LS sqs list-queues --query 'QueueUrls' --output text 2>&1
echo ""
echo "✅ dev bootstrap complete (NO mock data — users sign up)"

# (appended) chats table — saved conversations
aws --endpoint-url=http://localhost:4566 dynamodb create-table --table-name multitenant-chats \
  --attribute-definitions AttributeName=user_id,AttributeType=S AttributeName=chat_id,AttributeType=S \
  --key-schema AttributeName=user_id,KeyType=HASH AttributeName=chat_id,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST --query 'TableDescription.TableStatus' --output text 2>/dev/null || true
