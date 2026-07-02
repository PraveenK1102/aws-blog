# Stage 3: Production-Grade Backend

**Status:** ✅ Complete
**Started:** 2026-06-05
**Completed:** 2026-06-08
**Goal:** ALB, HTTPS, same-domain routing, health checks, CloudWatch

---

## Checklist

- [x] Security Group created for ALB (blog-alb-sg, port 80 from anywhere)
- [x] Target Group created (blog-backend-tg, health check: /api/health every 30s)
- [x] EC2 registered in Target Group
- [x] ALB created (blog-alb, internet-facing, 2 AZs)
- [x] Listener created (port 80 → forward to target group)
- [x] ALB tested — /api/health works through ALB
- [x] EC2 security group locked down (port 4000 from ALB only, not 0.0.0.0/0)
- [x] CloudFront updated — ALB added as second origin
- [x] Path-based routing: /api/* → ALB, /* → S3
- [x] HTTPS restored (redirect-to-https, no more allow-all workaround)
- [x] Frontend rebuilt with relative paths (VITE_API_URL="", fetch("/api/posts"))
- [x] Full blog working end-to-end over HTTPS, same domain, no CORS
- [x] CloudWatch metrics explored (auto-collected for ALB)
- [x] SNS topic created for alarms (blog-alarms, email subscription confirmed)
- [x] CloudWatch alarm: 5xx errors (HTTPCode_Target_5XX_Count > 5 in 5 min)
- [x] CloudWatch alarm: unhealthy hosts (UnHealthyHostCount > 0 for 2 min)
- [x] Alarm tested — stopped container, alarm fired, got email, restarted, alarm cleared
- [x] IAM role created for EC2 to write to CloudWatch (CloudWatchAgentServerPolicy)
- [x] Docker container restarted with awslogs driver (logs streaming to CloudWatch)
- [x] Load test passed — 1000 req @ 50 concurrent, 0 errors, ~245 RPS with DB queries

## Commands Used

```bash
# Create ALB security group
aws ec2 create-security-group \
  --group-name blog-alb-sg \
  --description "Security group for blog ALB" \
  --vpc-id vpc-0afd1ce4e85f54254

# Allow HTTP from anywhere (CloudFront connects from many IPs)
aws ec2 authorize-security-group-ingress \
  --group-id sg-0ce737bbc8a4c6a77 \
  --protocol tcp --port 80 --cidr 0.0.0.0/0

# Create target group with health check
aws elbv2 create-target-group \
  --name blog-backend-tg \
  --protocol HTTP --port 4000 \
  --vpc-id vpc-0afd1ce4e85f54254 \
  --health-check-path /api/health \
  --health-check-interval-seconds 30 \
  --healthy-threshold-count 2 \
  --unhealthy-threshold-count 3 \
  --target-type instance

# Register EC2 in target group
aws elbv2 register-targets \
  --target-group-arn arn:aws:elasticloadbalancing:ap-south-1:557690605487:targetgroup/blog-backend-tg/96e224d1678528c6 \
  --targets Id=i-092269a5892039994

# Create ALB (requires 2+ subnets in different AZs)
aws elbv2 create-load-balancer \
  --name blog-alb \
  --subnets subnet-0509f2074e5604061 subnet-0e708c31627f75e88 \
  --security-groups sg-0ce737bbc8a4c6a77 \
  --scheme internet-facing --type application

# Create listener (connects ALB to target group)
aws elbv2 create-listener \
  --load-balancer-arn arn:aws:elasticloadbalancing:ap-south-1:557690605487:loadbalancer/app/blog-alb/8aba6c8f4e399562 \
  --protocol HTTP --port 80 \
  --default-actions Type=forward,TargetGroupArn=arn:aws:elasticloadbalancing:ap-south-1:557690605487:targetgroup/blog-backend-tg/96e224d1678528c6

# Lock down EC2 — remove public access, allow ALB only
aws ec2 revoke-security-group-ingress \
  --group-id sg-06e037bf33bab6949 \
  --protocol tcp --port 4000 --cidr 0.0.0.0/0

aws ec2 authorize-security-group-ingress \
  --group-id sg-06e037bf33bab6949 \
  --protocol tcp --port 4000 --source-group sg-0ce737bbc8a4c6a77

# CloudFront — added ALB as second origin + /api/* behavior
# Updated via: aws cloudfront update-distribution (config file with Python script)

# Frontend deploy (relative paths, no VITE_API_URL needed)
cd blog-frontend
npm run build
aws s3 sync dist/ s3://praveen-blog-frontend/ --delete
aws cloudfront create-invalidation --distribution-id EOV3277U5A8CF --paths "/*"
```

## AWS Resources Created

| Resource | Name/ID | Details |
|----------|---------|---------|
| Security Group (ALB) | blog-alb-sg (sg-0ce737bbc8a4c6a77) | Port 80 from anywhere |
| Target Group | blog-backend-tg (96e224d1678528c6) | HTTP:4000, health check /api/health |
| ALB | blog-alb (8aba6c8f4e399562) | blog-alb-1510810204.ap-south-1.elb.amazonaws.com |
| Listener | On blog-alb | Port 80 → blog-backend-tg |
| SNS Topic | blog-alarms | arn:aws:sns:ap-south-1:557690605487:blog-alarms |
| SNS Subscription | mrmaniacpersonal@gmail.com | Email, confirmed |
| CloudWatch Alarm | blog-backend-5xx-errors | Triggers if 5xx > 5 in 5 min |
| CloudWatch Alarm | blog-backend-unhealthy | Triggers if UnHealthyHostCount > 0 for 2 min |
| IAM Role | blog-ec2-cloudwatch-role | EC2 → CloudWatch Logs (CloudWatchAgentServerPolicy) |
| Instance Profile | blog-ec2-cloudwatch-profile | Wraps role, attached to EC2 |
| Log Group | /aws/ec2/blog-backend | 7-day retention, awslogs driver |

## Load Test Results

```
Test 1: /api/health (1000 req, 50 concurrent)
  Time:        3.3 sec
  Throughput:  300 RPS
  Failed:      0
  Median:      149ms
  95th:        216ms

Test 2: /api/posts (1000 req, 50 concurrent, hits DB)
  Time:        4.1 sec
  Throughput:  245 RPS
  Failed:      0
  Median:      186ms (~37ms slower due to DB query)
  95th:        242ms
```

## Things That Broke & How I Fixed Them

### 1. Frontend calling localhost after build
- Built with `npm run build` without `VITE_API_URL`
- Default fallback was `"http://localhost:4000"` — baked into JS bundle
- **Fix:** Changed default in api.js from `"http://localhost:4000"` to `""` (empty = relative path)

### 2. EC2 container crashed — missing DATABASE_URL
- Rebuilt Docker image, started container without env vars
- `.env` file not in git repo (correctly in .gitignore)
- **Fix:** Passed env vars via `-e` flags in `docker run`, found values in Stage 1 learnings

## What I Learned

### ALB (Application Load Balancer)
- ALB = a type of ELB (Elastic Load Balancer). ELBv2 has ALB and NLB.
- Three components: Listener (accepts traffic on a port), Target Group (list of servers), Health Check (monitors servers)
- ALB needs at least 2 subnets in different AZs
- `internet-facing` = has public DNS name, reachable from internet
- DNS name: blog-alb-xxxxx.ap-south-1.elb.amazonaws.com (AWS-managed, stable)
- ALB doesn't know about target group until you create a listener connecting them

### Security: Defense in Depth
- Three security groups chained: ALB-sg → EC2-sg → RDS-sg
- Each layer only accepts traffic from the previous one
- EC2 locked down: revoked 0.0.0.0/0, added ALB security group as source
- Security group referencing: allow by security group ID, not by IP
- Direct access to EC2 (http://15.206.179.93:4000) now blocked — hangs and times out

### Same Domain Architecture
- CloudFront supports multiple origins with path-based routing
- Origin 1: S3 (for /*) — frontend static files
- Origin 2: ALB (for /api/*) — backend API
- Cache behavior: /api/* has CachingDisabled (API responses never cached)
- Same domain = no CORS, no preflight, no mixed content — all problems gone
- Frontend uses relative paths: fetch("/api/posts") — no env variable needed

### Protocol Flow
- User → CloudFront: HTTPS (CloudFront has free *.cloudfront.net cert)
- CloudFront → S3: HTTPS (OAC signed requests)
- CloudFront → ALB: HTTP (configured as http-only, safe within AWS)
- ALB → EC2: HTTP (inside VPC, private)
- EC2 → RDS: PostgreSQL protocol (inside VPC)

### Where Services Live
- NOT in VPC: CloudFront (global edge), S3 (global), Route 53 (global DNS)
- IN VPC: ALB (public subnets, internet-facing), EC2 (public subnet, locked down), RDS (private subnet)
- CloudFront reaches ALB via its public DNS name — ALB is internet-facing

### ARN (Amazon Resource Name)
- Unique ID for every AWS resource: arn:aws:service:region:account:resource
- Like a full postal address — globally unique
- Used to reference resources in CLI commands and policies

### ETag (Version Check)
- CloudFront returns ETag when you read config
- Must pass it back with --if-match when updating
- Prevents accidental overwrites if someone else changed config between your read and write

### DDoS Protection
- AWS Shield Standard: free, automatic, protects against common network attacks
- CloudFront absorbs traffic at 400+ edge locations — hard to overwhelm
- S3 behind CloudFront: cached at edge, attackers never reach S3
- API (/api/*): not cached, every request hits ALB/EC2 — more vulnerable
- AWS WAF: paid, rate limiting, blocks abusive IPs ($5+/month)
- Budget alerts catch unexpected cost spikes

### Environment Variables in Frontend
- All frontend frameworks bake env vars at BUILD time (not runtime)
- VITE_API_URL, REACT_APP_*, NEXT_PUBLIC_* — all build-time only
- Same domain setup eliminates the need: relative paths ("/api/posts") just work
- For local dev: VITE_API_URL=http://localhost:4000 npm run dev

## Questions That Came Up

- Can anyone hit ALB unlimited times? → AWS Shield Standard (free) handles common attacks. WAF for rate limiting.
- Is CloudFront → ALB over HTTP safe? → Traffic stays within AWS infrastructure. For production, add HTTPS on ALB too.
- What is ELBv2? → Version 2 of Elastic Load Balancer service, covers ALB and NLB. V1 was Classic (old).
- What is an ARN? → Amazon Resource Name, globally unique ID for any AWS resource.
- What is an ETag? → Version check to prevent overwriting someone else's changes.
