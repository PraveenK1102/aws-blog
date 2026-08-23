# AWS Learning Plan — Blog Deployment

A 6-stage plan to go from zero cloud experience to production-grade AWS knowledge. Each stage builds on the previous one. Spend ~1-2 weeks per stage at 1 hour/day.

---

## Stage 1: AWS Foundations + Manual EC2 Deploy

**Timeline:** Week 1-2
**Status:** Not started

**Objective:** Get the blog backend running on EC2, connected to RDS PostgreSQL. Understand the absolute basics of AWS.

### What you'll learn
- AWS account setup, billing alerts, free tier limits
- IAM (users, groups, policies — never use root)
- EC2 (launch, SSH, security groups)
- RDS (managed PostgreSQL)
- Security Groups (inbound/outbound rules, why things can't connect)

### Steps

1. **AWS Account Setup**
   - Create AWS account (free tier eligible)
   - Set up billing alert ($5 threshold) — so you don't get a surprise bill
   - Enable MFA on root account
   - Create an IAM user with `AdministratorAccess` (never use root for daily work)
   - Install AWS CLI on your Mac, configure it with `aws configure`

2. **Launch EC2 Instance**
   - Launch Ubuntu 22.04 t2.micro (free tier) in `ap-south-1` (Mumbai, closest to you)
   - Create a key pair (.pem file) for SSH
   - Create a Security Group: allow SSH (port 22) from your IP only, allow HTTP (port 4000) from anywhere
   - SSH into the instance: `ssh -i your-key.pem ubuntu@<public-ip>`
   - Install Docker: `sudo apt update && sudo apt install -y docker.io && sudo usermod -aG docker ubuntu`

3. **Create RDS PostgreSQL**
   - Launch RDS PostgreSQL (db.t3.micro or db.t4g.micro, free tier)
   - Put it in the same VPC as your EC2
   - Create a Security Group for RDS: allow port 5432 only from your EC2's security group
   - Note the RDS endpoint: `your-db.xxxxx.ap-south-1.rds.amazonaws.com`

4. **Deploy Backend on EC2**
   - Clone your blog-backend repo on EC2 (or `scp` the code)
   - Set `DATABASE_URL=postgresql://bloguser:${DB_PASSWORD}@<rds-endpoint>:5432/blogdb` (keep the real password out of the repo)
   - Run `docker build -t blog-backend .` and `docker run -d -p 4000:4000 --env-file .env blog-backend`
   - Run Prisma migrations against RDS: `npx prisma db push`
   - Test: `curl http://<ec2-public-ip>:4000/health`

5. **Verify It Works**
   - From your local machine, hit `http://<ec2-ip>:4000/health`
   - Try creating a user and a post via the API
   - Check RDS — the data should be there (connect via `psql` from EC2)

### End Result
- Backend API running on EC2 at `http://<ec2-ip>:4000`
- PostgreSQL on RDS (managed, auto-backups)
- You understand: IAM, EC2, RDS, Security Groups, SSH

### Cost
- EC2 t2.micro: Free tier (750 hrs/month for 12 months)
- RDS db.t3.micro: Free tier (750 hrs/month for 12 months, 20GB storage)
- Estimated: **$0/month** if you stay in free tier

### Interview Talking Points
- "I deployed a Node.js backend on EC2, connected to RDS PostgreSQL"
- "I configured Security Groups to restrict database access to only the application server"
- "I used IAM to create least-privilege users instead of using root credentials"

---

## Stage 2: Frontend on S3 + CloudFront

**Timeline:** Week 2-3
**Status:** Not started

**Objective:** Host the Next.js frontend as a static site on S3 with CloudFront CDN in front. Set up a custom domain.

### What you'll learn
- S3 (bucket creation, static website hosting, bucket policies)
- CloudFront (CDN, distributions, cache invalidation)
- Route 53 (DNS, hosted zones, A/AAAA/CNAME records)
- ACM (SSL certificates for HTTPS)
- CORS in production (frontend on different domain than backend)

### Steps

1. **Build the Frontend for Static Export**
   - Configure `next.config.js` for static export (`output: 'export'`)
   - Set `NEXT_PUBLIC_API_URL` to your EC2 backend URL
   - Run `npm run build` — output goes to `out/` folder

2. **Create S3 Bucket**
   - Create bucket named `yourblog.com` (or similar)
   - Enable static website hosting
   - Set bucket policy to allow public read access
   - Upload the `out/` folder contents
   - Test: `http://yourbucket.s3-website.ap-south-1.amazonaws.com`

3. **Set Up CloudFront**
   - Create a CloudFront distribution pointing to the S3 bucket
   - Set default root object to `index.html`
   - Configure error pages (404 → index.html for client-side routing)
   - Wait for distribution to deploy (~5-10 min)
   - Test: `https://dxxxxx.cloudfront.net`

4. **Custom Domain + HTTPS**
   - Register a domain (Route 53 or any registrar — cheap .xyz domains are ~$1/year)
   - Create a hosted zone in Route 53
   - Request an SSL certificate in ACM (must be in `us-east-1` for CloudFront)
   - Validate via DNS (ACM adds a CNAME record)
   - Update CloudFront to use your domain + SSL cert
   - Create Route 53 A record (alias) pointing to CloudFront

5. **Fix CORS**
   - Your frontend is now on `https://yourblog.com`, backend on `http://<ec2-ip>:4000`
   - Update backend `CORS_ORIGIN` to your frontend domain
   - This is where you really learn CORS — it will break, and fixing it is the lesson

### End Result
- Frontend at `https://yourblog.com` via CloudFront
- Backend at `http://<ec2-ip>:4000` (upgraded to HTTPS in Stage 3)
- Full blog working: register, login, create/read/edit/delete posts

### Cost
- S3: Free tier (5GB storage, 20K GET, 2K PUT/month)
- CloudFront: Free tier (1TB transfer, 10M requests/month)
- Route 53: ~$0.50/month per hosted zone
- Domain: ~$1-12/year depending on TLD
- Estimated: **~$1/month**

### Interview Talking Points
- "I served the frontend as a static site via S3 + CloudFront for low latency"
- "I configured ACM for SSL and Route 53 for DNS management"
- "I handled CORS between the CDN-hosted frontend and the EC2 backend"

---

## Stage 3: Production-Grade Backend

**Timeline:** Week 3-4
**Status:** Not started

**Objective:** Make the backend production-ready with proper networking, HTTPS, load balancing, and observability.

### What you'll learn
- VPC (building from scratch — subnets, route tables, NAT gateway, internet gateway)
- ALB (Application Load Balancer — path-based routing, health checks, HTTPS termination)
- ACM (SSL certificate for API domain)
- S3 SDK (upload files from code, presigned URLs)
- CloudWatch (logs, metrics, alarms)
- SSM Parameter Store / Secrets Manager (don't hardcode secrets)

### Steps

1. **Build a Proper VPC**
   - Create a VPC with CIDR `10.0.0.0/16`
   - Create 2 public subnets (for EC2/ALB) in different AZs
   - Create 2 private subnets (for RDS) in different AZs
   - Create an Internet Gateway, attach to VPC
   - Create route tables: public → IGW, private → local only
   - Move your EC2 and RDS into this VPC
   - This is the hardest part — if it breaks, that's where the deepest learning happens

2. **Set Up ALB**
   - Create an Application Load Balancer in public subnets
   - Create a target group pointing to your EC2 instance (port 4000)
   - Configure health checks (`/health` endpoint)
   - Request ACM certificate for `api.yourblog.com`
   - Add HTTPS listener (443) on ALB with the cert
   - Create Route 53 A record for `api.yourblog.com` → ALB
   - Update frontend `NEXT_PUBLIC_API_URL` to `https://api.yourblog.com`

3. **Move File Uploads to S3**
   - Create an S3 bucket for blog image uploads
   - Install `@aws-sdk/client-s3` in your backend
   - Replace multer local storage with S3 upload
   - Generate presigned URLs for reading images
   - Update frontend to use S3 URLs for images

4. **Set Up CloudWatch**
   - Install CloudWatch agent on EC2
   - Configure it to ship application logs (stdout/stderr) to CloudWatch
   - Create a log group for your backend
   - Create a basic alarm: "notify me if 5xx errors > 10 in 5 minutes"
   - Learn to search and filter logs in CloudWatch console

5. **Manage Secrets Properly**
   - Store `JWT_SECRET`, `DATABASE_URL` in SSM Parameter Store (SecureString)
   - Update your app to read secrets from SSM at startup (or use environment injection)
   - Remove hardcoded secrets from docker-compose and .env files on EC2

### End Result
- Backend at `https://api.yourblog.com` via ALB with SSL
- RDS in private subnets (not publicly accessible)
- File uploads stored in S3
- Logs and metrics in CloudWatch
- Secrets in SSM Parameter Store

### Cost
- ALB: ~$16/month (this is outside free tier — the biggest cost)
- NAT Gateway: ~$32/month if you need one (avoid if possible at this stage)
- S3 uploads: negligible
- CloudWatch: free tier covers most basic usage
- Estimated: **~$16-20/month** (ALB is the main cost — consider shutting down when not learning)

### Interview Talking Points
- "I designed a VPC with public and private subnets across two AZs"
- "The database runs in private subnets accessible only from the application layer"
- "I used ALB for HTTPS termination, health checks, and path-based routing"
- "Application logs stream to CloudWatch with alarms for 5xx spikes"
- "Secrets are stored in SSM Parameter Store, not in environment files"

---

## Stage 4: CI/CD Pipeline + ECS Fargate

**Timeline:** Week 4-5
**Status:** Not started

**Objective:** Automate deployments. Push code, it goes live. Move from EC2 to ECS Fargate (serverless containers).

### What you'll learn
- ECR (Elastic Container Registry — your private Docker Hub)
- ECS (Elastic Container Service — running containers without managing servers)
- Fargate (serverless compute for containers)
- GitHub Actions for AWS CI/CD
- Task definitions, services, clusters in ECS

### Steps

1. **Set Up ECR**
   - Create an ECR repository for `blog-backend`
   - Push your Docker image: `docker build`, `docker tag`, `docker push`
   - Learn the ECR login flow: `aws ecr get-login-password | docker login`

2. **Simple CI/CD First (GitHub Actions → EC2)**
   - Create `.github/workflows/deploy.yml`
   - On push to `main`: build Docker image → push to ECR → SSH into EC2 → pull & restart
   - This is the "simple but works" approach — good to know before jumping to ECS

3. **Migrate to ECS Fargate**
   - Create an ECS Cluster
   - Create a Task Definition (your Docker image, environment variables, port mappings)
   - Create an ECS Service (desired count: 1, connected to your ALB target group)
   - ECS pulls the image from ECR and runs it — no EC2 to manage
   - Update ALB target group to point to ECS service instead of EC2

4. **Update CI/CD for ECS**
   - GitHub Actions: build → push to ECR → update ECS service (force new deployment)
   - ECS automatically does rolling updates (old container stays until new one is healthy)
   - Add a staging environment (separate ECS service, separate ALB rule)

5. **Clean Up EC2**
   - Once ECS is working, terminate the EC2 instance
   - You no longer need to manage servers

### End Result
- `git push main` → GitHub Actions → ECR → ECS Fargate → live in ~3 minutes
- No servers to manage (Fargate handles the compute)
- Rolling deployments with zero downtime

### Cost
- ECR: free tier (500MB storage)
- ECS Fargate: ~$10-15/month for a small task running 24/7
- GitHub Actions: free for public repos, 2000 min/month for private
- Estimated: **~$25-30/month** total (including ALB from Stage 3)

### Interview Talking Points
- "I built a CI/CD pipeline: GitHub Actions builds the image, pushes to ECR, and ECS does a rolling deploy"
- "I migrated from EC2 to ECS Fargate to eliminate server management overhead"
- "Deployments are zero-downtime with ECS rolling updates and ALB health checks"

---

## Stage 5: Serverless + Event-Driven Architecture

**Timeline:** Week 5-6
**Status:** Not started

**Objective:** Add serverless components alongside your container-based app. Understand when to use Lambda vs containers.

### What you'll learn
- Lambda (functions as a service)
- API Gateway (HTTP API for Lambda)
- SQS (Simple Queue Service — message queues)
- S3 event notifications (trigger Lambda on file upload)
- SNS (Simple Notification Service — pub/sub)
- When to use serverless vs containers (the real interview question)

### Steps

1. **Lambda: Image Resizer**
   - Create a Lambda function (Node.js or Python)
   - Trigger: S3 event notification when a blog image is uploaded
   - Lambda resizes the image (create a thumbnail) and saves it back to S3
   - This is the classic "what is Lambda good for?" answer

2. **SQS: Notification Queue**
   - Create an SQS queue for "new post published" events
   - Backend sends a message to SQS when a post is created
   - Create a Lambda that polls the queue and sends an email via SES (or just logs it)
   - Learn about visibility timeout, dead letter queues, retry behavior

3. **API Gateway: Contact Form**
   - Create an HTTP API in API Gateway
   - Create a Lambda behind it for a "contact form" endpoint
   - This endpoint is fully serverless — no ECS needed
   - Route 53: `api.yourblog.com/contact` → API Gateway

4. **Compare and Contrast**
   - Your blog API runs on ECS (long-running, stateful sessions)
   - Image processing runs on Lambda (short, event-triggered)
   - Notifications run via SQS + Lambda (async, decoupled)
   - Write down when you'd use each — this is the interview answer

### End Result
- Blog images are automatically thumbnailed via Lambda
- New posts trigger async notifications via SQS
- Contact form runs on API Gateway + Lambda
- You can articulate when to use containers vs serverless

### Cost
- Lambda: free tier (1M requests, 400K GB-seconds/month)
- SQS: free tier (1M requests/month)
- API Gateway: free tier (1M HTTP API calls/month)
- Estimated: **$0 additional** (all within free tier for this usage)

### Interview Talking Points
- "I use Lambda for event-driven tasks like image processing triggered by S3 uploads"
- "I implemented async processing with SQS for post notifications with DLQ for failures"
- "I chose ECS for the main API because it's long-running and needs persistent connections, but used Lambda for stateless event processing"

---

## Stage 6: Infrastructure as Code (Terraform)

**Timeline:** Week 6-7
**Status:** Not started

**Objective:** Codify everything you built by hand. Destroy it all. Recreate it with one command.

### What you'll learn
- Terraform (HCL syntax, providers, resources, state, modules)
- Or AWS CDK if you prefer TypeScript (both are valid — Terraform is more common cross-cloud)
- State management (remote state in S3 + DynamoDB locking)
- Modules (reusable infrastructure components)
- `terraform plan` / `terraform apply` / `terraform destroy`

### Steps

1. **Install and Learn Terraform Basics**
   - Install Terraform
   - Write your first `.tf` file: create an S3 bucket
   - `terraform init` → `terraform plan` → `terraform apply`
   - `terraform destroy` — see it vanish
   - Understand state: what `terraform.tfstate` is and why it matters

2. **Codify Your VPC**
   - Write Terraform for: VPC, subnets, IGW, route tables, security groups
   - This is where Terraform shines — networking is tedious by hand
   - Use variables for CIDR blocks, region, AZs

3. **Codify Everything Else**
   - RDS instance (with security group)
   - S3 buckets (frontend hosting, image uploads)
   - CloudFront distribution
   - ECR repository
   - ECS cluster, task definition, service
   - ALB, target groups, listeners
   - Route 53 records
   - IAM roles and policies

4. **Remote State**
   - Create an S3 bucket + DynamoDB table for Terraform state
   - Configure backend in Terraform
   - Now your state is safe and supports team collaboration

5. **The Ultimate Test**
   - `terraform destroy` — everything goes away
   - `terraform apply` — everything comes back in ~10 minutes
   - This is the moment you know you own this infrastructure

### End Result
- Entire infrastructure defined in ~500-800 lines of Terraform
- Can create/destroy the full stack with one command
- State stored remotely in S3

### Cost
- Terraform: free (open source)
- S3 for state: negligible
- DynamoDB for locking: free tier
- Estimated: **$0 additional**

### Interview Talking Points
- "All infrastructure is codified in Terraform — VPC, ECS, RDS, CloudFront, the full stack"
- "I use remote state in S3 with DynamoDB locking for safe state management"
- "I can destroy and recreate the entire environment in under 15 minutes"

---

## Bonus: Deploy the RAG Project (After Stage 6)

Once you've completed the core 6 stages, deploy your GenAI/RAG project on AWS:

- **Ollama** on a GPU EC2 instance (g4dn.xlarge) or use **Bedrock** for managed LLM access
- **S3** for document storage (PDFs, text files for RAG)
- **SQS** for document processing queue (upload → embed → store)
- **RDS** (or **OpenSearch**) for vector storage
- **Lambda** or **ECS** for the embedding pipeline
- **API Gateway** for the RAG query endpoint

This combines everything you learned and adds GenAI-specific AWS knowledge — a strong differentiator.

---

## AWS Services Covered (Total)

| Service | Stage | Purpose |
|---------|-------|---------|
| IAM | 1 | Users, roles, policies |
| EC2 | 1, 3 | Compute (then replaced by Fargate) |
| RDS | 1 | Managed PostgreSQL |
| Security Groups | 1, 3 | Network-level access control |
| S3 | 2, 3 | Static hosting + file storage |
| CloudFront | 2 | CDN for frontend |
| Route 53 | 2 | DNS management |
| ACM | 2, 3 | SSL certificates |
| VPC | 3 | Networking (subnets, route tables, IGW) |
| ALB | 3 | Load balancing + HTTPS termination |
| CloudWatch | 3 | Logs, metrics, alarms |
| SSM Parameter Store | 3 | Secrets management |
| ECR | 4 | Container registry |
| ECS Fargate | 4 | Serverless containers |
| Lambda | 5 | Serverless functions |
| SQS | 5 | Message queues |
| API Gateway | 5 | Serverless HTTP APIs |
| Terraform | 6 | Infrastructure as code |

That's **18 AWS services** — more than enough for any senior developer role requiring 30-40% AWS.

---

## Cost Summary

| Stage | Monthly Cost |
|-------|-------------|
| 1 | $0 (free tier) |
| 2 | ~$1 (Route 53) |
| 3 | ~$16-20 (ALB) |
| 4 | ~$25-30 (ALB + Fargate) |
| 5 | $0 additional (free tier) |
| 6 | $0 additional |
| **Total at peak** | **~$30/month** |

Shut down ALB and ECS when you're not actively learning to save costs. The free tier covers most everything else for 12 months.
