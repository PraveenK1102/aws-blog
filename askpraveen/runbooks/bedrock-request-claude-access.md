# Runbook — Request Claude Haiku access on Bedrock

## Why this matters

Ingestion (Titan) works out of the box. Generation via `anthropic.claude-3-haiku-20240307-v1:0` fails with:

> Model use case details have not been submitted for this account. Fill out the Anthropic use case details form before using the model.

Anthropic requires all AWS accounts to submit a one-time use-case form before their models on Bedrock become invokable. This is per-account, not per-model.

Until the form is approved, `apac.amazon.amazon.nova-micro-v1:0` is the default LLM (see `.env.example`).

## Steps

1. Console → **Amazon Bedrock** → region **Mumbai (ap-south-1)** → left sidebar **Model access**.
2. Click **Manage model access**.
3. Under **Anthropic**, tick **Claude 3 Haiku** (and optionally Claude 3.5 Haiku, Claude Sonnet — same form covers them all).
4. Click **Next**. Fill the use-case form:
   - **Company name**: Personal / your name.
   - **Company website**: your GitHub or blog URL is fine.
   - **Intended users**: `Personal portfolio project — public Q&A over my own blog content.`
   - **Use case description**: `Retrieval-augmented Q&A. User asks a question about my public blog posts and learning notes; the app retrieves relevant chunks from a pgvector store and prompts Claude Haiku to synthesize an answer with citations. No PII, no third-party data — only my own published content.`
   - **Traffic estimate**: `< 1000 requests/month`.
   - **Countries of operation**: India.
5. Submit. Anthropic auto-approves personal-scope requests, typically within 15 minutes but sometimes a few hours.

## Verify

```bash
cd askpraveen
source .venv/bin/activate
python3 -c "
import boto3
c = boto3.client('bedrock-runtime', region_name='ap-south-1')
r = c.converse(
    modelId='anthropic.claude-3-haiku-20240307-v1:0',
    messages=[{'role':'user','content':[{'text':'Say hi in 4 words.'}]}],
    inferenceConfig={'maxTokens':30}
)
print(r['output']['message']['content'][0]['text'])
"
```

If this prints text, access is live.

## Swap the app to Claude

Edit `.env`:

```
BEDROCK_LLM_MODEL_ID=anthropic.claude-3-haiku-20240307-v1:0
```

Re-run the sample queries in `scripts/query.py` and compare answers to the Nova Micro baseline captured in `runbooks/session-1-sample-queries.md`.
