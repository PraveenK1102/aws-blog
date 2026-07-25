# AWS Learning Progress

Track what you've learned, what broke, and what you'd do differently.

## Overall Status

| Stage | Topic | Status | Started | Completed |
|-------|-------|--------|---------|-----------|
| 1 | EC2 + RDS (Manual Deploy) | ✅ Complete | 2026-05-31 | 2026-06-01 |
| 2 | S3 + CloudFront (Frontend) | ✅ Complete | 2026-06-02 | 2026-06-04 |
| 3 | VPC + ALB + CloudWatch (Prod-Grade) | ✅ Complete | 2026-06-05 | 2026-06-08 |
| 4 | CI/CD + ECR + ECS Fargate | ✅ Complete | 2026-06-11 | 2026-06-14 |
| 5 | Lambda + SQS + API Gateway (+ serverless RAG app) | ✅ Complete | 2026-07-25 | 2026-07-26 |
| 6 | Terraform (IaC) | Not started | - | - |

## Stage Files

- [Stage 1 — EC2 + RDS](stage-1-ec2-rds.md)
- [Stage 2 — S3 + CloudFront](stage-2-s3-cloudfront.md)
- [Stage 3 — Production Backend](stage-3-production-backend.md)
- [Stage 4 — CI/CD + ECS](stage-4-cicd-ecs.md)
- [Stage 5 — Serverless](stage-5-serverless.md)
- [Stage 6 — Terraform](stage-6-terraform.md)

## Key Gotchas & Lessons (cross-cutting)

- **LocalStack ignores IAM.** A dev env on LocalStack won't catch least-privilege gaps — they only appear on
  real AWS. When promoting, audit each handler's exact actions (get vs query vs scan). (Stage 5: `Query` on a
  table without `GetItem` made reading one item 404 in prod only.)
- **Least-privilege gaps hide as the wrong error.** An `AccessDenied` swallowed in a `try/except` looks like
  "not found". Log infra errors + return 500; don't masquerade them.
- **CloudFront SPA error responses (403/404 → /index.html) mask API errors.** A failing `/api/*` request came
  back as the app shell (`x-cache: Error from cloudfront`, `server: AmazonS3`) instead of a JSON error — check
  those headers when an API call "returns HTML".
- **Lambda memory = CPU dial.** ~1769MB ≈ 1 vCPU; sizing is about CPU for compute-bound work, not just RAM.
- **Adding a DynamoDB GSI is online but backfills** — takes minutes to go ACTIVE even on an empty table.
- **OIDC > stored keys for CI**, pinned to a branch. Native x86 GitHub runners avoid ARM→x86 cross-build pain.
- **Pure serverless idle cost ≈ $0**; a VPC/ALB/RDS/EC2 stack costs ~$10/mo even idle (public IPv4 + Fargate).

## Also To Learn

- **ECS** (Elastic Container Service) — covered in Stage 4, running Docker containers without managing servers
- **EKS** (Elastic Kubernetes Service) — Kubernetes on AWS, more complex than ECS, used by large companies. Learn after Stage 6 as a bonus.
- Compare: ECS vs EKS — when to use which, interview question
