# Stage 1: EC2 + RDS — Manual Deploy

**Status:** ✅ Complete (2026-06-01)
**Goal:** Backend API running on EC2, connected to RDS PostgreSQL

---

## Checklist

- [x] AWS account created
- [x] Billing alert set ($5 monthly + zero-spend budget)
- [x] MFA enabled on root (Google Authenticator)
- [x] IAM user created (praveen-admin, AdministratorAccess)
- [x] AWS CLI installed and configured (aws-cli/2.34.57, region: ap-south-1)
- [x] EC2 instance launched (i-092269a5892039994, t2.micro, Ubuntu 26.04, ap-south-1b)
- [x] Key pair created and saved (blog-key.pem → ~/.ssh/blog-key.pem)
- [x] Security Group configured (blog-backend-sg: SSH from My IP + EC2 Instance Connect, port 4000 from anywhere)
- [x] SSH into EC2 working (via EC2 Instance Connect — direct SSH blocked by corporate proxy)
- [x] Docker installed on EC2 (Docker 29.1.3)
- [x] RDS PostgreSQL created (blog-db, db.t3.micro, PostgreSQL 16, 20GB gp2)
- [x] RDS Security Group allows EC2 only (blog-rds-sg: port 5432 from blog-backend-sg)
- [x] Backend deployed on EC2 (Docker image: blog-backend, container: blog-app)
- [x] DATABASE_URL pointing to RDS (blog-db.cnakgsquy4bt.ap-south-1.rds.amazonaws.com)
- [x] Prisma migrations run against RDS (auto via CMD in Dockerfile)
- [x] API health check works from browser (http://15.206.179.93:4000/health)
- [x] CRUD operations work (register, login, create post, list posts — all tested)

## Commands Used

_Record every command you run — future you will thank present you._

```bash
# AWS CLI setup
brew install awscli
aws configure   # entered access key, secret, region=ap-south-1, output=json
aws sts get-caller-identity   # verified: user=praveen-admin, account=557690605487

# EC2 — launched via console, then verified via CLI
aws ec2 describe-instances --instance-ids i-092269a5892039994

# EC2 SSH — direct SSH blocked by corporate proxy, used EC2 Instance Connect instead
# Had to add EC2 Instance Connect IP range to security group:
aws ec2 authorize-security-group-ingress --group-id sg-06e037bf33bab6949 --protocol tcp --port 22 --cidr 13.233.177.0/29

# Docker on EC2
sudo apt update && sudo apt install -y docker.io
sudo usermod -aG docker ubuntu   # add ubuntu user to docker group

# RDS setup
# export DB_PASSWORD='<choose-a-strong-password>'   # set in your shell; never commit the real value
aws ec2 create-security-group --group-name blog-rds-sg --description "PostgreSQL from EC2 only" --vpc-id vpc-0afd1ce4e85f54254
aws ec2 authorize-security-group-ingress --group-id sg-08b5c84adf8754574 --protocol tcp --port 5432 --source-group sg-06e037bf33bab6949
aws rds create-db-subnet-group --db-subnet-group-name blog-db-subnets --db-subnet-group-description "Subnets for blog RDS" --subnet-ids subnet-0e708c31627f75e88 subnet-0509f2074e5604061 subnet-06b31780d679f2a1d
aws rds create-db-instance --db-instance-identifier blog-db --db-instance-class db.t3.micro --engine postgres --engine-version 16 --master-username bloguser --master-user-password "$DB_PASSWORD" --allocated-storage 20 --db-name blogdb --vpc-security-group-ids sg-08b5c84adf8754574 --db-subnet-group-name blog-db-subnets --no-publicly-accessible --no-multi-az --storage-type gp2

# Deploy backend on EC2 (run these in EC2 Instance Connect terminal)
git clone https://github.com/PraveenK1102/aws-blog.git
cd aws-blog/blog-backend
docker build -t blog-backend .
docker run -d --name blog-app -p 4000:4000 \
  -e DATABASE_URL="postgresql://bloguser:${DB_PASSWORD}@blog-db.cnakgsquy4bt.ap-south-1.rds.amazonaws.com:5432/blogdb" \
  -e JWT_SECRET="super-secret-jwt-key-change-later" \
  -e PORT=4000 \
  -e CORS_ORIGIN="*" \
  blog-backend

# Verify
curl http://localhost:4000/health   # {"ok":true,"service":"blog-backend"}
```

## AWS Resources Created

| Resource | Details | Region |
|----------|---------|--------|
| IAM User | praveen-admin (AdministratorAccess) | Global |
| EC2 Instance | i-092269a5892039994, t2.micro, Ubuntu 26.04, IP: 15.206.179.93 | ap-south-1b |
| Key Pair | blog-key (RSA, .pem) | ap-south-1 |
| Security Group (EC2) | blog-backend-sg (sg-06e037bf33bab6949): SSH(22) from My IP + EC2 IC, TCP(4000) from anywhere | ap-south-1 |
| Security Group (RDS) | blog-rds-sg (sg-08b5c84adf8754574): PostgreSQL(5432) from blog-backend-sg only | ap-south-1 |
| DB Subnet Group | blog-db-subnets (3 subnets across 3 AZs) | ap-south-1 |
| RDS Instance | blog-db, db.t3.micro, PostgreSQL 16, 20GB, no public access | ap-south-1 |

## Things That Broke & How I Fixed Them

1. **SSH blocked by corporate proxy** — Machine (Zoho-hardened) routes all traffic through proxy at 127.0.0.1:3128. SSH on port 22 blocked. Fix: Used EC2 Instance Connect (browser-based SSH over HTTPS).

2. **EC2 Instance Connect failed** — Security group only allowed SSH from personal IP. EC2 Instance Connect connects from AWS's IP range, not user's IP. Fix: Added `13.233.177.0/29` (AWS EC2 Instance Connect range for ap-south-1) to security group.

3. **Prisma OpenSSL error in Docker** — Alpine Linux image didn't have OpenSSL. Prisma couldn't parse the schema engine response. Fix: Added `RUN apk add --no-cache openssl` to the Dockerfile.

4. **Docker Compose not installed on Mac** — Had `docker-compose` (hyphen version) not `docker compose` (plugin version). Fix: `brew install docker-compose`. Also needed to start Colima first (`colima start`).

## What I Learned (In My Own Words)

_After completing this stage, write 3-5 bullet points about what clicked for you._

## Questions That Came Up

_Write down things you didn't understand — even if you figured them out later._
