# Project: AWS Learning via Blog Deployment

## Goal

Learn AWS at a production-grade level by deploying this blog application (Next.js frontend + Express/Prisma/PostgreSQL backend) across real AWS services. The end goal is to confidently handle AWS responsibilities in senior developer roles that require 30-40% AWS knowledge.

## What this project is

A blog app built specifically as a **learning vehicle** for AWS. The app itself is intentionally simple — the complexity is in the infrastructure. Don't over-engineer the app code; focus on AWS infrastructure, deployment patterns, and production readiness.

## Stack

- **Frontend:** Next.js 14 (static export) — deploys to S3 + CloudFront
- **Backend:** Express + Prisma ORM + PostgreSQL — deploys to EC2, then ECS Fargate
- **Database:** PostgreSQL (local Docker → AWS RDS)
- **Auth:** JWT (register/login)
- **File uploads:** Multer (local disk → S3)
- **Containerization:** Docker + Docker Compose

## AWS Learning Path

The full plan is in [AWS-LEARNING-PLAN.md](./AWS-LEARNING-PLAN.md). There are 6 stages, each building on the last:

1. **Stage 1** — EC2 + RDS (manual deploy, networking basics)
2. **Stage 2** — S3 + CloudFront + Route 53 (static hosting, CDN, DNS)
3. **Stage 3** — ALB + VPC + CloudWatch + S3 uploads (production-grade)
4. **Stage 4** — CI/CD with ECR + ECS Fargate (automated deploys)
5. **Stage 5** — Lambda + SQS + API Gateway (serverless, event-driven)
6. **Stage 6** — Terraform/CDK (infrastructure as code)

## Tracking Progress

All learnings, notes, gotchas, and commands are recorded in the [learnings/](./learnings/) folder. Each stage has its own file. There's also a [learnings/INDEX.md](./learnings/INDEX.md) that tracks overall progress.

## Conventions

- When working on AWS infrastructure, always document what was done and what was learned in the corresponding `learnings/stage-X-*.md` file.
- Record actual AWS CLI commands and console steps taken — not just theory.
- Note any costs incurred and how to stay within free tier.
- When something breaks (it will), document the error and the fix — these are the most valuable learnings.

## Related Projects

- **GenAI/RAG project** — Built with Ollama, embeddings, SQL storage, Ragas testing. Can be deployed on AWS later as an advanced exercise (GPU EC2, S3 for docs, SQS for processing).
