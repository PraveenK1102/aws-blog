"""
Initialize Qdrant collection for MultiTenantRAG.

Creates the multitenant_chunks collection with:
  - Dense vectors (Titan V2, 1024 dims, cosine)
  - Sparse vectors (BM25 via fastembed, IDF modifier)
  - Payload indexes for tenant_id, user_id, post_id

Reads credentials from AWS Secrets Manager, so no keys in code.

Run once. Idempotent (safe to re-run — will skip if collection exists).

Usage:
  cd multitenant-rag
  python3 -m venv .venv
  source .venv/bin/activate
  pip install qdrant-client boto3
  python scripts/init_qdrant.py
"""

import json
import sys

try:
    import boto3
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        VectorParams,
        SparseVectorParams,
        SparseIndexParams,
        Distance,
        PayloadSchemaType,
        Modifier,
    )
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: pip install qdrant-client boto3")
    sys.exit(1)


COLLECTION_NAME = "multitenant_chunks"
DENSE_DIMS = 1024  # Titan Text Embeddings V2 default


def get_qdrant_creds():
    """Fetch Qdrant URL + API key from AWS Secrets Manager."""
    sm = boto3.client("secretsmanager", region_name="ap-south-1")
    resp = sm.get_secret_value(SecretId="multitenant/qdrant")
    creds = json.loads(resp["SecretString"])
    return creds["url"], creds["api_key"]


def main():
    print("=" * 50)
    print("Qdrant Collection Initialization")
    print("=" * 50)

    print("\n1. Fetching Qdrant credentials from Secrets Manager...")
    url, api_key = get_qdrant_creds()
    print(f"   Cluster URL: {url}")

    print("\n2. Connecting to Qdrant Cloud...")
    client = QdrantClient(url=url, api_key=api_key)

    try:
        collections = client.get_collections()
        print(
            f"   Connected. Existing collections: {[c.name for c in collections.collections]}"
        )
    except Exception as e:
        print(f"   ERROR connecting: {e}")
        sys.exit(1)

    print(f"\n3. Checking if collection '{COLLECTION_NAME}' exists...")
    existing = [c.name for c in collections.collections]
    if COLLECTION_NAME in existing:
        print(f"   Collection '{COLLECTION_NAME}' already exists.")
        print(f"   Details:")
        info = client.get_collection(COLLECTION_NAME)
        print(f"     Vector count: {info.points_count}")
        print(f"     Status: {info.status}")
        confirm = input("\n   Recreate collection? This will DELETE all data. (yes/NO): ")
        if confirm.lower() == "yes":
            client.delete_collection(COLLECTION_NAME)
            print(f"   Deleted existing collection.")
        else:
            print("   Skipping. Collection preserved.")
            return

    print(f"\n4. Creating collection '{COLLECTION_NAME}' with hybrid config...")
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config={
            "dense": VectorParams(size=DENSE_DIMS, distance=Distance.COSINE)
        },
        sparse_vectors_config={
            "sparse": SparseVectorParams(
                index=SparseIndexParams(),
                modifier=Modifier.IDF,
            )
        },
    )
    print(f"   Collection created.")

    print(f"\n5. Creating payload indexes for fast filtering...")
    for field in ["tenant_id", "user_id", "post_id"]:
        client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name=field,
            field_schema=PayloadSchemaType.KEYWORD,
        )
        print(f"   Indexed: {field}")

    print(f"\n6. Verifying...")
    info = client.get_collection(COLLECTION_NAME)
    print(f"   Status: {info.status}")
    print(f"   Points: {info.points_count}")
    print(f"   Vectors config: {info.config.params.vectors}")
    print(f"   Sparse config: {info.config.params.sparse_vectors}")

    print("\n✅ Qdrant collection initialized successfully.")
    print("\nNext steps:")
    print("  1. Enable Bedrock Titan V2 in AWS Console (if not done)")
    print("  2. Move to Phase 2: write the 3 Lambda functions")


if __name__ == "__main__":
    main()
