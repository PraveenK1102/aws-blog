# AWS CLI Commands — Glossary & Reference

Every command we've run, with each part explained. Use this as a cheat sheet.

---

## Common Terms (used in almost every command)

| Term | Meaning |
|------|---------|
| `aws` | The AWS Command Line Interface — sends API calls to AWS |
| `--region ap-south-1` | Which AWS region to run the command against (Mumbai) |
| `--output table` | Format output as a readable table (alternatives: `json`, `text`) |
| `--query "..."` | Filter the output using JMESPath (like jq for JSON) |
| `\` | Continue command on next line (shell line continuation) |
| `arn:aws:service:region:account:resource` | ARN — globally unique ID for any AWS resource |
| `sg-xxxxx` | Security Group ID prefix |
| `vpc-xxxxx` | VPC ID prefix |
| `subnet-xxxxx` | Subnet ID prefix |
| `i-xxxxx` | EC2 instance ID prefix |

---

## Stage 1: EC2 + RDS Setup

### IAM / Identity
```bash
aws configure
```
- `configure` — interactive setup, stores access key + secret + region in `~/.aws/credentials`

```bash
aws sts get-caller-identity
```
- `sts` — Security Token Service
- `get-caller-identity` — shows which IAM user/role is making the request (used to verify CLI auth works)

### EC2
```bash
aws ec2 describe-instances --instance-ids i-092269a5892039994
```
- `ec2` — Elastic Compute Cloud (virtual machines)
- `describe-instances` — read details about EC2 instances
- `--instance-ids` — filter to specific instances

```bash
aws ec2 authorize-security-group-ingress \
  --group-id sg-06e037bf33bab6949 \
  --protocol tcp --port 22 \
  --cidr 13.233.177.0/29
```
- `authorize-security-group-ingress` — add an inbound (ingress) rule to a security group
- `--group-id` — which security group to modify
- `--protocol tcp` — allow TCP traffic (alternatives: udp, icmp)
- `--port 22` — which port to allow (22 = SSH)
- `--cidr 13.233.177.0/29` — which IPs are allowed (CIDR notation; /29 = 8 IPs)

```bash
aws ec2 revoke-security-group-ingress \
  --group-id sg-06e037bf33bab6949 \
  --protocol tcp --port 4000 \
  --cidr 0.0.0.0/0
```
- `revoke-security-group-ingress` — REMOVE an inbound rule (opposite of authorize)

```bash
aws ec2 authorize-security-group-ingress \
  --group-id sg-06e037bf33bab6949 \
  --protocol tcp --port 4000 \
  --source-group sg-0ce737bbc8a4c6a77
```
- `--source-group` — allow traffic from anything attached to THIS security group (instead of an IP range)

```bash
aws ec2 create-security-group \
  --group-name blog-rds-sg \
  --description "PostgreSQL from EC2 only" \
  --vpc-id vpc-0afd1ce4e85f54254
```
- `create-security-group` — make a new security group
- `--group-name` — friendly name
- `--description` — required, explains purpose
- `--vpc-id` — which VPC it belongs to (security groups live inside a VPC)

```bash
aws ec2 describe-vpcs \
  --query "Vpcs[*].{VpcId:VpcId,CidrBlock:CidrBlock,IsDefault:IsDefault}"
```
- `describe-vpcs` — list all VPCs in this region
- The `--query` reshapes output into a custom table

```bash
aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=vpc-0afd1ce4e85f54254"
```
- `describe-subnets` — list subnets
- `--filters` — narrow results (here: only subnets in our VPC)

### RDS
```bash
aws rds create-db-subnet-group \
  --db-subnet-group-name blog-db-subnets \
  --db-subnet-group-description "Subnets for blog RDS" \
  --subnet-ids subnet-A subnet-B subnet-C
```
- `rds` — Relational Database Service (managed databases)
- `create-db-subnet-group` — group of subnets where RDS can place the database
- Required because RDS needs subnets in at least 2 AZs for high availability

```bash
aws rds create-db-instance \
  --db-instance-identifier blog-db \
  --db-instance-class db.t3.micro \
  --engine postgres --engine-version 16 \
  --master-username bloguser --master-user-password "$DB_PASSWORD" \   # export DB_PASSWORD first; never commit it
  --allocated-storage 20 \
  --db-name blogdb \
  --vpc-security-group-ids sg-08b5c84adf8754574 \
  --db-subnet-group-name blog-db-subnets \
  --no-publicly-accessible --no-multi-az \
  --storage-type gp2
```
- `create-db-instance` — provision a new database
- `--db-instance-identifier` — unique name for the DB instance
- `--db-instance-class` — hardware size (t3.micro = free tier)
- `--engine` — which database engine (postgres, mysql, etc.)
- `--master-username/password` — root DB credentials
- `--allocated-storage` — disk size in GB
- `--db-name` — initial database to create inside Postgres
- `--vpc-security-group-ids` — which SG controls access
- `--db-subnet-group-name` — which subnet group to use
- `--no-publicly-accessible` — don't give a public IP (private network only)
- `--no-multi-az` — single AZ (cheaper, no standby replica)
- `--storage-type gp2` — general purpose SSD

```bash
aws rds describe-db-instances \
  --query "DBInstances[*].{Name:DBInstanceIdentifier,Endpoint:Endpoint.Address,Port:Endpoint.Port}"
```
- `describe-db-instances` — list databases with their connection endpoints

---

## Stage 2: S3 + CloudFront

### S3
```bash
aws s3 mb s3://praveen-blog-frontend --region ap-south-1
```
- `s3` — Simple Storage Service (object storage)
- `mb` — "make bucket" (create)
- `s3://name` — bucket URI format

```bash
aws s3 sync dist/ s3://praveen-blog-frontend/ --delete
```
- `sync` — upload/update files, only transferring what changed
- `--delete` — remove S3 files that no longer exist locally (keeps bucket clean)

```bash
aws s3 ls s3://praveen-blog-frontend/ --recursive --human-readable --summarize
```
- `ls` — list objects
- `--recursive` — include subdirectories
- `--human-readable` — show sizes as KB/MB not bytes
- `--summarize` — show total count + size at the end

```bash
aws s3api put-bucket-policy --bucket praveen-blog-frontend --policy '{...JSON...}'
```
- `s3api` — lower-level S3 commands (more granular than `s3`)
- `put-bucket-policy` — attach an access policy to a bucket
- `--policy` — the policy JSON (who can do what)

### CloudFront
```bash
aws cloudfront create-origin-access-control \
  --origin-access-control-config '{"Name":"...", "SigningProtocol":"sigv4", ...}'
```
- `cloudfront` — the CDN service
- `create-origin-access-control` (OAC) — security mechanism so only CloudFront can read S3
- `SigningProtocol: sigv4` — CloudFront signs every S3 request using AWS Signature V4
- `SigningBehavior: always` — sign every single request

```bash
aws cloudfront create-distribution --distribution-config '{...}'
```
- `create-distribution` — create the CDN itself
- `--distribution-config` — JSON config (origins, behaviors, SSL, error responses)

```bash
aws cloudfront get-distribution --id EOV3277U5A8CF
```
- `get-distribution` — read current state of a distribution

```bash
aws cloudfront get-distribution-config --id EOV3277U5A8CF
```
- `get-distribution-config` — read JUST the editable config (returns an ETag for safe updates)

```bash
aws cloudfront update-distribution \
  --id EOV3277U5A8CF \
  --if-match ETVPDKIKX0DER \
  --distribution-config file:///tmp/cf-config-update.json
```
- `update-distribution` — apply a new config
- `--if-match` — ETag check, rejects update if config changed in between (prevents overwrites)
- `file://...` — read config from local file instead of inline JSON

```bash
aws cloudfront create-invalidation \
  --distribution-id EOV3277U5A8CF \
  --paths "/*"
```
- `create-invalidation` — tell CloudFront to forget cached files
- `--paths "/*"` — clear ALL files (1 path = 1 invalidation; 1000/month free)
- Must run after uploading new files, otherwise users see old cached version

```bash
aws cloudfront list-distributions
```
- `list-distributions` — see all distributions in your account

```bash
aws cloudfront get-invalidation \
  --distribution-id EOV3277U5A8CF \
  --id IANBA1ZKBQHXAE8VEGOC9Z09B4
```
- `get-invalidation` — check status of an invalidation (InProgress → Completed)

---

## Stage 3: ALB + CloudWatch + SNS

### ALB (ELBv2)
```bash
aws ec2 create-security-group \
  --group-name blog-alb-sg \
  --description "Security group for blog ALB" \
  --vpc-id vpc-0afd1ce4e85f54254
```
- Reused from earlier — same pattern, but for the ALB

```bash
aws elbv2 create-target-group \
  --name blog-backend-tg \
  --protocol HTTP --port 4000 \
  --vpc-id vpc-0afd1ce4e85f54254 \
  --health-check-path /api/health \
  --health-check-interval-seconds 30 \
  --healthy-threshold-count 2 \
  --unhealthy-threshold-count 3 \
  --target-type instance
```
- `elbv2` — Elastic Load Balancer v2 (covers ALB + NLB; v1 was Classic)
- `create-target-group` — list of servers the load balancer can forward to
- `--protocol HTTP --port 4000` — backend listens on HTTP port 4000
- `--health-check-path /api/health` — URL ALB pings to verify the target is alive
- `--health-check-interval-seconds 30` — check every 30 seconds
- `--healthy-threshold-count 2` — mark healthy after 2 consecutive successful pings
- `--unhealthy-threshold-count 3` — mark unhealthy after 3 consecutive failed pings
- `--target-type instance` — targets are EC2 instances (alternatives: ip, lambda)

```bash
aws elbv2 register-targets \
  --target-group-arn arn:aws:elasticloadbalancing:... \
  --targets Id=i-092269a5892039994
```
- `register-targets` — add servers to a target group
- `--targets Id=...` — EC2 instance ID(s) to register

```bash
aws elbv2 create-load-balancer \
  --name blog-alb \
  --subnets subnet-A subnet-B \
  --security-groups sg-0ce737bbc8a4c6a77 \
  --scheme internet-facing \
  --type application
```
- `create-load-balancer` — create the ALB itself
- `--subnets` — at least 2 subnets in different AZs (required)
- `--scheme internet-facing` — has a public DNS name (alternative: `internal`, VPC-only)
- `--type application` — ALB (alternatives: `network` for NLB, `gateway` for GLB)

```bash
aws elbv2 create-listener \
  --load-balancer-arn arn:aws:elasticloadbalancing:...:loadbalancer/app/blog-alb/... \
  --protocol HTTP --port 80 \
  --default-actions Type=forward,TargetGroupArn=arn:...:targetgroup/blog-backend-tg/...
```
- `create-listener` — tells ALB what to do with incoming traffic
- `--protocol HTTP --port 80` — accept traffic on port 80
- `--default-actions` — what to do (here: forward to a target group)
- `Type=forward` — forward to a target group (alternatives: redirect, fixed-response, authenticate-cognito)

```bash
aws elbv2 describe-load-balancers --names blog-alb \
  --query "LoadBalancers[0].LoadBalancerArn" --output text
```
- `describe-load-balancers` — read ALB details
- `--query "...[0]..."` — pick first result and extract just the ARN

### CloudWatch
```bash
aws cloudwatch list-metrics \
  --namespace AWS/ApplicationELB \
  --dimensions Name=LoadBalancer,Value=app/blog-alb/8aba6c8f4e399562
```
- `cloudwatch` — monitoring + logging service
- `list-metrics` — see what metrics exist for a resource
- `--namespace` — which AWS service (AWS/ApplicationELB, AWS/EC2, AWS/RDS, etc.)
- `--dimensions` — filter to a specific resource (Name=key, Value=value pairs)

```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/ApplicationELB \
  --metric-name RequestCount \
  --dimensions Name=LoadBalancer,Value=app/blog-alb/... \
  --start-time 2026-06-06T00:00:00 \
  --end-time 2026-06-06T01:00:00 \
  --period 300 \
  --statistics Sum
```
- `get-metric-statistics` — fetch actual metric values for a time range
- `--metric-name` — which metric (RequestCount, CPUUtilization, etc.)
- `--start-time / --end-time` — ISO 8601 timestamps (UTC)
- `--period 300` — bucket size in seconds (300 = 5 minutes)
- `--statistics Sum` — how to aggregate (Sum, Average, Maximum, Minimum, SampleCount)

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "blog-backend-5xx-errors" \
  --alarm-description "Alert when backend returns 5xx errors" \
  --metric-name HTTPCode_Target_5XX_Count \
  --namespace AWS/ApplicationELB \
  --statistic Sum \
  --period 300 \
  --threshold 5 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 1 \
  --dimensions Name=LoadBalancer,Value=app/blog-alb/... \
  --alarm-actions arn:aws:sns:ap-south-1:...:blog-alarms
```
- `put-metric-alarm` — create/update an alarm
- `--alarm-name` — unique name for the alarm
- `--metric-name` — which metric to watch
- `--statistic Sum` — aggregation method per period
- `--period 300` — evaluation window (5 minutes)
- `--threshold 5` — trigger when value crosses this number
- `--comparison-operator GreaterThanThreshold` — direction (alternatives: LessThan, GreaterThanOrEqualTo, etc.)
- `--evaluation-periods 1` — number of consecutive periods crossing threshold before alarm fires
- `--alarm-actions` — what to do when alarm fires (SNS topic ARN here)

### SNS
```bash
aws sns create-topic --name blog-alarms
```
- `sns` — Simple Notification Service (pub/sub messaging)
- `create-topic` — make a new notification channel
- Returns a TopicArn — used to publish to or subscribe to the topic

```bash
aws sns subscribe \
  --topic-arn arn:aws:sns:ap-south-1:...:blog-alarms \
  --protocol email \
  --notification-endpoint mrmaniacpersonal@gmail.com
```
- `subscribe` — add a receiver to the topic
- `--topic-arn` — which topic to subscribe to
- `--protocol email` — delivery method (alternatives: sms, https, lambda, sqs)
- `--notification-endpoint` — the destination (email address, phone, URL, ARN)
- After running: AWS sends a confirmation email; you MUST click the link before notifications work

---

## Docker Commands (on EC2)

| Command | What it does |
|---------|--------------|
| `docker ps` | List running containers |
| `docker ps -a` | List ALL containers (including stopped/exited) |
| `docker logs blog-app` | Show stdout/stderr from a container |
| `docker stop blog-app` | Stop a running container |
| `docker rm blog-app` | Delete a stopped container |
| `docker build -t blog-backend .` | Build image, tag as "blog-backend", from current dir (`.`) |
| `docker run -d --name blog-app -p 4000:4000 -e KEY=value blog-backend` | Run container in background, map port, set env var |
| `docker inspect blog-app --format='...'` | Show internal config (env vars, mounts, network) |

Flags:
- `-t name` — tag/name the image
- `-d` — detached (run in background)
- `--name X` — give container a name
- `-p HOST:CONTAINER` — map host port to container port
- `-e KEY=value` — set environment variable
- `.` — build context (must be at the end of `docker build`)

---

## Quick Patterns

### Always-required pieces
- ARNs to reference: VPC, Security Group, Subnet, Target Group, ALB, Distribution, Topic
- `--region ap-south-1` — every command (or set default via `aws configure`)

### Deploy flow (frontend)
```bash
npm run build                                                          # build
aws s3 sync dist/ s3://praveen-blog-frontend/ --delete                # upload
aws cloudfront create-invalidation --distribution-id ... --paths "/*" # clear cache
```

### Deploy flow (backend on EC2)
```bash
# On local machine
git push

# On EC2 (via Instance Connect)
cd ~/aws-blog && git pull
cd blog-backend && docker build -t blog-backend .
docker stop blog-app && docker rm blog-app
docker run -d --name blog-app -p 4000:4000 -e DATABASE_URL=... -e JWT_SECRET=... blog-backend
```

### Security group chain (defense in depth)
```
Internet → ALB (port 80, from 0.0.0.0/0)
         → EC2 (port 4000, from blog-alb-sg only)
         → RDS (port 5432, from blog-backend-sg only)
```

Each layer only accepts traffic from the previous layer's security group.
