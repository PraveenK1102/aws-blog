# AWS Learning — Master Knowledge Document

This is the complete knowledge from my AWS learning journey (Stages 1-4 so far), deploying a blog app on AWS to learn production cloud. Use this as full context to answer my questions.

---

## About Me

- **Background:** Developer at Zoho, zero cloud experience before this
- **Goal:** Learn AWS to qualify for senior developer roles requiring 30-40% AWS knowledge
- **Approach:** Project-based learning — deploy a real app, hit real problems, learn services that solve them
- **Style:** I want to understand WHY things work, not just copy-paste commands. Learning is the code, not the output.

---

## The Project

A blog application I'm using as a learning vehicle:

- **Frontend:** React + Vite (static build, single-page app)
- **Backend:** Express + Prisma ORM (Node.js API)
- **Database:** PostgreSQL
- **Auth:** JWT (register/login)
- **File uploads:** Multer (currently local disk on EC2)

The app itself is intentionally simple — complexity is in the AWS infrastructure.

---

## The 6-Stage Learning Plan

1. ✅ **Stage 1** — EC2 + RDS (manual deploy, networking basics)
2. ✅ **Stage 2** — S3 + CloudFront + Route 53 (static hosting, CDN)
3. ✅ **Stage 3** — ALB + VPC + CloudWatch + SNS (production-grade)
4. 🟡 **Stage 4** — CI/CD + ECR + ECS Fargate (in progress)
5. ⏳ **Stage 5** — Lambda + SQS + API Gateway (serverless)
6. ⏳ **Stage 6** — Terraform / CDK (Infrastructure as Code)

---

# STAGE 1: EC2 + RDS (✅ Complete)

## What I Built

A simple setup: backend on one EC2, database on RDS.

```
User → http://15.206.179.93:4000 → EC2 (Docker → Express) → RDS PostgreSQL
```

## Key Concepts Learned

### EC2 (Elastic Compute Cloud)
- A virtual machine in AWS
- Free tier: t2.micro for 750 hours/month (24/7) for 12 months
- I used Ubuntu 26.04 LTS

### RDS (Relational Database Service)
- Managed PostgreSQL — AWS handles backups, patches, replication
- Free tier: db.t3.micro for 750 hours/month for 12 months

### VPC (Virtual Private Cloud)
- Your own private network in AWS
- Every region has a "default VPC" — created automatically
- My VPC: `172.31.0.0/16` (~65,000 IP addresses)

### Subnets
- Slices of a VPC, each in ONE Availability Zone
- My subnets:
  - `172.31.0.0/20` in ap-south-1a
  - `172.31.16.0/20` in ap-south-1c
  - `172.31.32.0/20` in ap-south-1b
- Public subnets = can reach internet directly
- Private subnets = no direct internet access (more secure)

### Security Groups
- Stateful firewalls (return traffic auto-allowed)
- Can reference OTHER security groups, not just IPs
- My chain:
  - `blog-backend-sg`: SSH from my IP + EC2 Instance Connect, port 4000 from anywhere
  - `blog-rds-sg`: PostgreSQL (5432) from `blog-backend-sg` ONLY

### CIDR Notation
- `/16` = 65,536 addresses
- `/20` = 4,096 addresses
- `/24` = 256 addresses
- `/32` = 1 address (a single IP)
- `0.0.0.0/0` = everywhere on the internet

## Problems I Hit and Solved

1. **SSH blocked by corporate proxy** (Zoho-hardened machine routes everything through proxy)
   - Fix: Used EC2 Instance Connect (browser-based SSH via HTTPS)

2. **EC2 Instance Connect failed initially** — security group only allowed my IP
   - Fix: Added `13.233.177.0/29` (AWS EC2 Instance Connect IP range for ap-south-1)

3. **Prisma OpenSSL error in Docker Alpine**
   - Fix: Added `RUN apk add --no-cache openssl` to Dockerfile

## Pain Points That Led to Stage 2

- Frontend served from same EC2 → slow globally
- Site down whenever I redeploy
- EC2 wastes resources serving static files

---

# STAGE 2: S3 + CloudFront (✅ Complete)

## What I Built

Split the frontend out of EC2. Static files in S3, served globally through CloudFront CDN.

```
User → CloudFront (CDN) → S3 (frontend files)
```

## Key Concepts Learned

### S3 (Simple Storage Service)
- Object storage — flat key-value store, not a filesystem
- Bucket names are globally unique across all AWS accounts
- My bucket: `praveen-blog-frontend`

### CloudFront (CDN)
- Content Delivery Network with 400+ edge locations worldwide
- Caches files near users (low latency)
- Free SSL certificate for `*.cloudfront.net` domain
- My URL: `https://d261g450savmee.cloudfront.net`

### Edge Locations
- Small AWS data centers near users
- First request → CloudFront fetches from origin (S3), caches it
- Future requests → served from cache (fast)

### OAC (Origin Access Control)
- Makes S3 bucket PRIVATE
- Only CloudFront can read from it (signs every request with AWS SigV4)
- Users CAN'T bypass CloudFront and hit S3 directly
- Bucket policy uses `Condition: AWS:SourceArn` to lock to specific distribution

### Cache Invalidation
- After deploying new files, CloudFront still has OLD files cached
- Must explicitly clear: `aws cloudfront create-invalidation --paths "/*"`
- First 1000 invalidation paths per month are FREE
- `/*` counts as 1 path

### SPA Routing with CloudFront
- React Router uses client-side URLs like `/posts/5`
- S3 doesn't have a file at `/posts/5/index.html` → returns 404
- Custom error response: 404 → serve `/index.html` with status 200
- React then reads URL and renders correct page

### Why I Switched from Next.js to React + Vite
- Next.js static export does CODE SPLITTING — each page has its own HTML + JS
- Custom error response serves home page HTML for all 404s → wrong JS loads
- React + Vite generates ONE index.html with ALL JS → true SPA → works perfectly with S3+CloudFront

### Build-time vs Runtime Environment Variables
- `VITE_API_URL`, `REACT_APP_*`, `NEXT_PUBLIC_*` — all baked into JS at BUILD time
- Cannot be changed after build without rebuilding
- Production tip: use relative paths (`/api/posts`) so no env var needed when frontend and backend are on same domain

## CORS & Preflight (Important Concept)

CORS = Cross-Origin Resource Sharing — browser security rule.

When frontend and backend are on DIFFERENT domains:
- Browser sends **preflight** request (OPTIONS method) before the real request
- Backend must respond with `Access-Control-Allow-Origin: *` (or specific origin)
- Browser then sends the real request (GET/POST/etc.)
- **2 network calls per API request** instead of 1

```
1st request (PREFLIGHT — sent automatically by browser):
   OPTIONS /posts
   Origin: https://d261g450savmee.cloudfront.net
   Access-Control-Request-Method: GET
   Access-Control-Request-Headers: Authorization

2nd request (your actual fetch):
   GET /posts
   Authorization: Bearer ...
```

The browser sends the OPTIONS automatically — your code never sees it. The `cors` middleware in Express handles responding to it.

**Solution to eliminate preflight:** Put frontend and backend on the same domain (Stage 3 with CloudFront path-based routing).

## Problems I Hit and Solved

1. **Mixed content blocking** — HTTPS frontend trying to call HTTP backend
   - Browser blocks: "HTTPS page cannot call HTTP API"
   - Temporary fix: Set CloudFront ViewerProtocolPolicy to `allow-all` (serve HTTP too)
   - Real fix later in Stage 3: HTTPS everywhere via ALB

2. **HSTS browser auto-upgrade** — once a site is visited on HTTPS, browser remembers
   - Fix: Use incognito mode, or clear browser cache

3. **Cache showed old version after S3 update**
   - Fix: Must run `aws cloudfront create-invalidation` after every deploy

## Deploy Flow

```bash
cd blog-frontend
npm run build                                                # build
aws s3 sync dist/ s3://praveen-blog-frontend/ --delete       # upload
aws cloudfront create-invalidation --distribution-id X --paths "/*"  # clear cache
```

## Pain Points That Led to Stage 3

- Backend still HTTP only (no SSL cert on EC2)
- Different domains for frontend (CloudFront) and backend (EC2) → CORS on every call
- EC2 IP exposed to users
- No monitoring or alerting

---

# STAGE 3: ALB + CloudWatch + SNS (✅ Complete)

## What I Built

Added ALB for HTTPS + same-domain routing. Added CloudWatch for monitoring.

```
User → CloudFront → /* → S3 (frontend)
                  → /api/* → ALB → EC2 → RDS
```

Same domain for everything = no CORS, no mixed content.

## Key Concepts Learned

### ALB (Application Load Balancer)
- A type of ELBv2 (Elastic Load Balancer v2)
- Three components:
  - **Listener**: accepts traffic on a port (e.g., 80 HTTP)
  - **Target Group**: list of backend servers
  - **Health Check**: pings targets every N seconds
- Requires at least 2 subnets in different AZs
- DNS name: `blog-alb-xxx.ap-south-1.elb.amazonaws.com`
- `internet-facing` scheme = public DNS, reachable from internet

### ELB Types
- **CLB** (Classic Load Balancer) — v1, old, avoid
- **ALB** (Application Load Balancer) — v2, what I'm using
- **NLB** (Network Load Balancer) — v2, for extreme performance (TCP-level)

### ACM (AWS Certificate Manager)
- Free SSL/TLS certificates
- Auto-renewed every year
- Used by ALB, CloudFront, API Gateway
- For custom domains (e.g., `api.mysite.com`)

### Health Checks
- ALB pings `/api/health` every 30 seconds
- Mark target healthy after 2 consecutive successes
- Mark target unhealthy after 3 consecutive failures
- Auto-removes unhealthy targets from rotation

### Path-Based Routing in CloudFront
- CloudFront can have MULTIPLE origins
- Each origin has a "cache behavior" with a path pattern
- My setup:
  - `/api/*` → ALB (backend, no caching)
  - `/*` (default) → S3 (frontend, cache aggressively)

### Why Same Domain Solves Everything
- Browser sees frontend and backend as SAME origin
- No CORS check, no preflight, just 1 network call
- No mixed content (CloudFront handles all HTTPS)
- EC2 IP completely hidden from users
- Single domain to manage in production (e.g., `mysite.com`)

### Defense in Depth (Security Chain)
Each layer accepts traffic only from the previous layer's security group:

```
Internet → ALB (port 80 from 0.0.0.0/0)
        → EC2 (port 4000 from blog-alb-sg ONLY)
        → RDS (port 5432 from blog-backend-sg ONLY)
```

If any layer is compromised, the next layer still blocks unauthorized access.

### Principle of Least Privilege
Give exactly the permissions needed, nothing more.

Bad bucket policy:
```json
{ "Principal": "*", "Action": "s3:*" }
```

Good bucket policy:
```json
{
  "Principal": { "Service": "cloudfront.amazonaws.com" },
  "Action": "s3:GetObject",
  "Resource": "arn:aws:s3:::my-bucket/*",
  "Condition": { "StringEquals": { "AWS:SourceArn": "specific-cloudfront-arn" } }
}
```

### CloudWatch (3 things)
1. **Metrics** — numbers over time (auto-collected for AWS services)
   - `RequestCount`, `TargetResponseTime`, `HTTPCode_Target_5XX_Count`, `UnHealthyHostCount`
2. **Logs** — text output from apps (must configure)
3. **Alarms** — notifications when metrics cross thresholds

### Every CloudWatch alarm has 4 parts:
1. WHICH metric?
2. HOW to aggregate (Sum, Average, etc. over what window)?
3. WHAT threshold triggers it?
4. WHAT action to take (publish to SNS)?

### SNS (Simple Notification Service)
- AWS's pub/sub messaging system
- Topic = a notification channel
- Subscribers = email, SMS, Lambda, SQS, HTTPS endpoint
- One publish → all subscribers get notified
- My setup: CloudWatch alarm → SNS topic `blog-alarms` → email

### IAM (Identity and Access Management)

**IAM User** = a person (you, with access key + secret)
**IAM Role** = identity that AWS resources can assume (no password)

**Trust policy** = WHO can use this role (e.g., "EC2 service can assume this")
**Permission policy** = WHAT the role can do (e.g., "write to CloudWatch")

**Instance profile** = wrapper that lets EC2 use a role

Why roles instead of access keys on EC2:
- No keys stored on the server (can't be stolen)
- Credentials auto-refresh every hour
- Can detach role instantly to revoke access

### How EC2 Gets Credentials Automatically
- Special internal URL only reachable from inside EC2: `http://169.254.169.254/`
- IAM role attached → AWS provides temporary credentials at this URL
- AWS CLI / SDK automatically discovers and uses them
- Credentials expire in ~1 hour, auto-refresh

### Docker Logging Driver Switch
- Default: Docker writes logs to `/var/lib/docker/containers/.../json.log` on EC2
- Switched to `awslogs` driver: Docker ships logs to CloudWatch in real-time
- Configured via `--log-driver=awslogs --log-opt awslogs-group=/aws/ec2/blog-backend`

## Load Test Results

```
1000 requests, 50 concurrent, hitting /api/posts (with DB query):
  Time:        4.1 sec
  Throughput:  245 RPS
  Failed:      0
  Median:      186ms
  95th:        242ms
```

Production-quality performance from a single t2.micro + db.t3.micro.

## Cost Awareness

For a small learning project, AWS Free Tier covers nearly everything:
- EC2 t2.micro, RDS db.t3.micro: FREE 12 months
- ALB: FREE 12 months (then ~$22/mo)
- S3, CloudFront: FREE under heavy usage limits
- CloudWatch: 10 metrics + 10 alarms + 1 GB logs FREE
- SNS: 1M publishes + 1000 emails FREE

DDoS protection:
- AWS Shield Standard: FREE, automatic, blocks common attacks
- CloudFront absorbs traffic at edge (hard to overwhelm)
- AWS WAF: paid, for rate limiting and custom rules

---

# STAGE 4: ECS Fargate (🟡 In Progress)

## What I'm Building

Replace the manual EC2 setup with managed container orchestration.

```
User → CloudFront → ALB → ECS Fargate Task (Express container) → RDS
```

EC2 is gone from the request path. Fargate runs containers without me managing any servers.

## Key Concepts Learned

### Why Move from EC2 to ECS

Pain points with EC2:
1. Manual deploys — SSH, git pull, docker build, stop, run with 20 env flags
2. Single point of failure — one EC2, if it dies, site is down
3. No auto-recovery — container crash = manual restart
4. Hard to scale — can't easily run 3 copies
5. Zero-downtime updates impossible — stop → delay → start

What ECS gives:
- Tell ECS "run N copies of this image" → it does it
- Container crashes → ECS restarts in seconds
- Update image → ECS rolls out one container at a time (zero downtime)
- Need more capacity? Change desired-count from 1 to N

### ECS Hierarchy (5 terms to learn)

```
Cluster      = group of services (just a folder)
Service      = "keep N tasks running of this type" (auto-restart, rolling updates)
Task Def     = recipe for a container (image, CPU, env vars)
Task         = a running container (instance of a definition)
Fargate      = AWS-managed compute that runs the tasks
```

Analogy:
```
Cluster  = the kitchen
Service  = "always have 1 dish ready"
Task Def = the recipe
Task     = a finished dish (one running container)
```

### ECS has 2 compute modes

**ECS on EC2 (the old way, still used):**
- YOU manage EC2 instances
- ECS schedules containers onto them
- Cheaper at scale (~50% vs Fargate)
- More complexity (patches, sizing, scaling EC2s)

**ECS Fargate (modern, what I use):**
- AWS manages the underlying compute
- You just describe containers (CPU, memory, image)
- Slightly more expensive per resource unit
- Zero servers to manage

I chose Fargate because the whole point was to stop managing servers.

### ECR (Elastic Container Registry)

- AWS's private Docker registry (like a private GitHub for Docker images)
- Lives inside AWS, integrated with IAM
- No rate limits (unlike Docker Hub free tier)
- 500 MB FREE storage for 12 months

Stores my image versions:
```
blog-backend:v1, v2, v3...
```

**Key insight:** ECR is smart about shared layers:
- v1 = base layers (110 MB) + my code (8 MB) = 118 MB
- v2 = base layers SHARED + new code (8 MB) = only 8 MB more
- 20 versions ≈ 280 MB total

That's why Dockerfile order matters — put expensive layers first (npm install), changing layers last (COPY .).

### ECR vs Docker Hub

| Feature | Docker Hub | ECR |
|---|---|---|
| Owned by | Docker Inc. | AWS |
| Auth | API tokens | AWS IAM (automatic) |
| Rate limits | 100 pulls/6hrs (free) | None within account |
| Privacy | 1 private repo free | All private by default |
| Speed for AWS | Slow (internet) | Fast (same network) |

In production:
- Pull base images from Docker Hub (`FROM node:20-alpine`)
- Push your built image to ECR
- ECS pulls from ECR (fast, IAM-authenticated)

### Docker Concepts

**Image** = a static recipe (read-only, layered filesystem)
**Container** = a running process based on an image (one image → many containers)
**Layer** = each Dockerfile instruction creates a layer; Docker caches them

Dockerfile order for caching:
```dockerfile
FROM node:20-alpine          # base layer (rarely changes)
RUN apk add openssl          # system deps (rarely changes)
COPY package.json ./         # dep declarations (sometimes changes)
RUN npm install              # heavy layer (cached if package.json same)
COPY . .                     # your code (changes often)
```

When code changes, only the `COPY . .` layer rebuilds. Heavy `npm install` is cached.

### Docker = One Engine, Many Containers

One Docker engine on a machine can run many containers:
- nginx + Express + PostgreSQL + Redis + Java app
- All isolated from each other
- Each has its own filesystem, network, processes
- Like `docker-compose` for a multi-service stack

### Container vs VM

- VM: full OS simulation, gigabytes, minutes to boot, ~3-5 per laptop
- Container: isolated process on shared kernel, megabytes, milliseconds to boot, 100+ per laptop

Containers use Linux features:
- **Namespaces**: isolated view of filesystem, network, processes
- **Cgroups**: resource limits (CPU, memory)
- **Union filesystem**: layered, read-only storage

### Task Definition

JSON file describing the container, like a `docker run` command but reusable:

```json
{
  "family": "blog-backend",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256",          // 0.25 vCPU
  "memory": "512",       // 512 MB
  "executionRoleArn": "...",
  "containerDefinitions": [{
    "name": "blog-backend",
    "image": "557690605487.dkr.ecr.ap-south-1.amazonaws.com/blog-backend:v1",
    "portMappings": [{ "containerPort": 4000 }],
    "environment": [...],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": { "awslogs-group": "/aws/ecs/blog-backend", ... }
    }
  }]
}
```

Each update creates a new "revision": blog-backend:1, blog-backend:2, ...

### Task vs Container vs Cost

```
Task = the unit of compute + networking (one micro-VM in Fargate)
Container = the application process inside the task

You pay per TASK (CPU + RAM allocation × time, per second)

1 task with 1 container  = $9/mo (24/7, 0.25 vCPU + 0.5 GB RAM)
1 task with 3 containers = $9/mo (same task wrapper, shared resources)
3 tasks with 1 container each = $27/mo (three separate task wrappers)
```

### Multiple Containers in Same Task

When useful:
- App + log shipper (sidecar pattern)
- App + envoy proxy
- App + secrets fetcher (init container)

Containers in same task:
- Share the same IP (the task's ENI)
- Talk via `localhost`
- Start/stop together
- Share security context (IAM role)

Containers in different tasks:
- Independent IPs
- Need real networking (DNS, service discovery)
- Can scale independently

### IAM Roles for ECS

Two types:

1. **Task Execution Role** (`ecsTaskExecutionRole`)
   - Used by Fargate BEFORE container runs
   - Permissions: pull from ECR, write to CloudWatch Logs
   - Pre-built policy: `AmazonECSTaskExecutionRolePolicy`

2. **Task Role**
   - Used by container while running
   - If your app calls AWS APIs (e.g., S3 upload)
   - We don't use this yet — our app doesn't call AWS APIs

### Why Build on EC2 (Architecture Issue)

I hit an `exec format error` because:
- My Mac is ARM64 (Apple Silicon)
- `docker build` on Mac produced ARM64 image
- Fargate (default) runs on x86-64 → can't execute ARM binaries

Fix options:
- Use `docker build --platform linux/amd64` (needs buildx — my Colima doesn't have it)
- Build on EC2 (which IS x86) — what I did

### Cost Breakdown (Current Setup)

Running 24/7:
- ALB: FREE first 12 months, then ~$22/mo
- Fargate (1 task, 0.25 vCPU + 0.5 GB): ~$9/mo (NO free tier)
- RDS db.t3.micro: FREE 12 months
- ECR: FREE under 500 MB
- CloudFront + S3: FREE under heavy limits

**Today: ~$9/month if 24/7**
**After 12 months: ~$31/month**

To minimize cost while learning:
- Set ECS desired-count to 0 when not actively learning
- Pay only when learning (a few dollars per month)

### Where Everything Lives

**NOT in any VPC (global AWS services):**
- CloudFront (edge locations worldwide)
- S3 (global object storage)
- Route 53 (global DNS)
- ECR (regional but managed)
- ECS Control Plane (the orchestrator)

**IN my VPC (172.31.0.0/16):**
- ALB (public subnets, internet-facing)
- EC2 (public subnet, locked down)
- Fargate tasks (subnets I specified, get private IPs from the subnet's CIDR)
- RDS (subnet group, ideally private subnets)

## Problems Hit in Stage 4

1. **`logs:CreateLogGroup` permission denied**
   - Task def had `awslogs-create-group: true`
   - `ecsTaskExecutionRole` doesn't have `CreateLogGroup` permission
   - Fix: Created the log group manually (`aws logs create-log-group ...`)

2. **`exec format error` — architecture mismatch**
   - Built ARM64 image on Mac, Fargate needs x86
   - Fix: Built on EC2 (which is x86 native)

3. **Docker buildx missing**
   - Colima on Mac doesn't have buildx by default
   - Workaround: Build on EC2 — cleaner than installing buildx

## Steps Completed So Far

✅ Created ECR repository  
✅ Built image with `--platform linux/amd64`  
✅ Pushed image to ECR  
✅ Created ECS cluster  
✅ Created IAM roles (Task Execution Role)  
✅ Registered Task Definition  
✅ Created new Target Group (target-type=ip for Fargate)  
✅ Created security groups (ECS allow from ALB, RDS allow from ECS)  
✅ Created ECS Service with Fargate  
✅ Switched ALB listener to ECS target group  
✅ Site now served by Fargate, not EC2  

## Still To Do in Stage 4

⏳ GitHub Actions for auto-deploy (push code → CI/CD builds → deploys)  
⏳ Use OIDC for AWS auth (no long-lived secret keys in GitHub)  
⏳ Decommission old EC2  

## How Scaling Works in ECS Fargate

**Scale UP:**
- Manual: `aws ecs update-service --desired-count 5`
- ECS asks Fargate for 4 more micro-VMs
- Each pulls image from ECR, starts container
- Auto-registers with ALB target group
- ALB starts load-balancing across all 5 within ~60 seconds

- Auto: CloudWatch alarm (CPU > 70% for 5 min) → ECS auto-adjusts desired-count

**Scale DOWN:**
- Lower desired-count (manually or auto-scaling)
- ECS picks tasks to remove → tells ALB "drain this target"
- ALB stops sending NEW requests, lets existing finish (~30s grace)
- ECS stops the task → Fargate releases the micro-VM
- Stop paying immediately (per-second billing)

---

# CORE PRINCIPLES I'VE LEARNED

## 1. Defense in Depth
Each layer trusts only the previous layer's security group, not IPs. If one layer is breached, the next still blocks unauthorized access.

## 2. Principle of Least Privilege
Give exactly the permissions needed, nothing more. Use specific IAM policies, conditional access in bucket policies.

## 3. Separation of Concerns
- Frontend (static) lives separately from backend (API)
- Each scales independently
- Connected only by API URL configuration

## 4. Observability
If you can't see it, you can't fix it. CloudWatch for metrics + logs + alarms. SNS for notifications.

## 5. Idempotency
Every deploy should produce the same result. Container images are immutable. Infrastructure as Code (Stage 6) makes this explicit.

## 6. Build Once, Deploy Anywhere
Docker images are portable artifacts. Build once, push to ECR, ECS pulls and runs identically wherever it lands.

## 7. Cost Awareness
- Track free tier limits per service
- Set budget alerts
- Stop unused resources between learning sessions

---

# COMMON TERMS GLOSSARY

| Term | Meaning |
|---|---|
| ARN | Amazon Resource Name — globally unique ID for any AWS resource |
| ETag | Version check on AWS resources (prevents accidental overwrites) |
| CIDR | IP range notation (e.g., 10.0.0.0/16) |
| AZ | Availability Zone (a physical data center within a region) |
| Region | Geographic area with multiple AZs (e.g., ap-south-1 = Mumbai) |
| OIDC | OpenID Connect — secure auth for CI/CD without storing keys |
| SDK | Software Development Kit (libraries for calling AWS APIs from code) |
| CLI | Command Line Interface (`aws ...` commands) |
| Free tier | First 12 months OR always-free quotas per service |

---

# REQUEST FLOW SUMMARY (Current Setup)

```
1. User types: https://d261g450savmee.cloudfront.net/api/posts

2. DNS resolves to CloudFront edge nearest user

3. Browser → HTTPS → CloudFront (TLS handshake)

4. CloudFront checks path:
   - /api/* → forward to ALB origin
   - /* → serve from S3 (cached)

5. CloudFront → HTTP → ALB (port 80, internal AWS network)

6. ALB picks a healthy task from target group

7. ALB → HTTP → Fargate task IP (10.X.X.X:4000) inside VPC

8. Container's network namespace → Express process

9. Express → Prisma → SQL query → RDS (port 5432, inside VPC)

10. RDS returns rows → Prisma → JSON → Express response

11. Response travels back: container → ALB → CloudFront → user

12. CloudFront does NOT cache /api/* responses (configured CachingDisabled)

13. CloudFront DOES cache static assets (HTML, JS, CSS) at edge
```

---

# WHERE I AM RIGHT NOW

- Stage 4 ECS Fargate is RUNNING and serving traffic
- Site URL: https://d261g450savmee.cloudfront.net
- All HTTPS, same domain, no CORS
- ALB → Fargate task → RDS
- Old EC2 still exists but receives no traffic (will decommission later)
- Next step: GitHub Actions auto-deploy

---

# KEY QUESTIONS I'VE ASKED (for context)

1. "What is RDS, VPC, subnet?" → Learned networking basics deeply
2. "Why ECS instead of EC2?" → Container orchestration, auto-recovery, scaling
3. "ECR vs Docker Hub?" → Both are registries; ECR is AWS-private, integrated with IAM, no rate limits
4. "What is OAC?" → Origin Access Control, makes S3 private but accessible only by CloudFront
5. "Why is there preflight on every API call?" → CORS rule for cross-origin requests with Authorization header
6. "Is preflight avoidable?" → Yes, same domain via CloudFront eliminates CORS entirely
7. "Are Docker images architecture-specific?" → Yes, must match runtime (x86 vs ARM)
8. "Does base image (node:20-alpine) get pushed to ECR?" → Yes, image is self-contained, all layers go up
9. "Can multiple containers run on one Docker?" → Yes, one Docker engine runs many containers
10. "Does a task have multiple Dockers?" → No, ONE Docker per task, MANY containers inside
11. "Are containers in same task isolated from other tasks' containers?" → Yes, different micro-VMs, separate networks
12. "How does ECS scale?" → Change desired-count manually OR auto-scale based on CloudWatch metric
13. "What's the cost model?" → Per-task (CPU + RAM × time), not per-container

---

This is the full context of my AWS learning journey so far. Use this to answer my questions accurately and pick up where I left off.
