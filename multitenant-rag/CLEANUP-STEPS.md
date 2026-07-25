# Phase 0 — Cleanup Runbook

Deletes old blog infra to reduce cost before building MultiTenantRAG.

**Expected savings:** ~$10/month (primarily VPC public IPv4 charges on ALB + EC2).

**Region:** ap-south-1  
**Account:** 557690605487

---

## Order Matters

Delete in this order due to dependencies:

1. ECS service (before cluster)
2. ECS cluster
3. ALB listener (before ALB)
4. ALB (before target groups can be freed)
5. Target groups
6. EC2 instance
7. RDS instance
8. RDS subnet group (after RDS gone)
9. Security groups (after all consumers removed)
10. ECR repositories
11. CloudWatch log groups
12. IAM roles and instance profiles

---

## Steps

### 1. Delete ECS Service

```bash
aws ecs delete-service \
  --cluster blog-cluster \
  --service blog-backend-service \
  --force \
  --region ap-south-1
```

Verify:
```bash
aws ecs list-services --cluster blog-cluster --region ap-south-1
```

Should return empty.

### 2. Delete ECS Cluster

```bash
aws ecs delete-cluster --cluster blog-cluster --region ap-south-1
```

Verify:
```bash
aws ecs list-clusters --region ap-south-1
```

### 3. Delete ALB Listener

```bash
aws elbv2 delete-listener \
  --listener-arn arn:aws:elasticloadbalancing:ap-south-1:557690605487:listener/app/blog-alb/8aba6c8f4e399562/d653bbaa59ed84c9 \
  --region ap-south-1
```

### 4. Delete ALB

```bash
aws elbv2 delete-load-balancer \
  --load-balancer-arn arn:aws:elasticloadbalancing:ap-south-1:557690605487:loadbalancer/app/blog-alb/8aba6c8f4e399562 \
  --region ap-south-1
```

Wait ~30 seconds for delete to propagate before deleting target groups.

### 5. Delete Target Groups

```bash
aws elbv2 delete-target-group \
  --target-group-arn arn:aws:elasticloadbalancing:ap-south-1:557690605487:targetgroup/blog-backend-tg/96e224d1678528c6 \
  --region ap-south-1

aws elbv2 delete-target-group \
  --target-group-arn arn:aws:elasticloadbalancing:ap-south-1:557690605487:targetgroup/blog-ecs-tg/8423f7ee89908fd0 \
  --region ap-south-1
```

### 6. Terminate EC2 Instance

```bash
aws ec2 terminate-instances \
  --instance-ids i-092269a5892039994 \
  --region ap-south-1
```

Wait until state is `terminated`:
```bash
aws ec2 describe-instances \
  --instance-ids i-092269a5892039994 \
  --query "Reservations[0].Instances[0].State.Name" \
  --output text \
  --region ap-south-1
```

### 7. Delete RDS Instance

```bash
aws rds delete-db-instance \
  --db-instance-identifier blog-db \
  --skip-final-snapshot \
  --delete-automated-backups \
  --region ap-south-1
```

Wait ~5 minutes for RDS to finish deleting. Check status:
```bash
aws rds describe-db-instances \
  --db-instance-identifier blog-db \
  --region ap-south-1 2>&1
```

Should return `DBInstanceNotFound` when complete.

### 8. Delete RDS Subnet Group

Only after RDS instance is fully gone:

```bash
aws rds delete-db-subnet-group \
  --db-subnet-group-name blog-db-subnets \
  --region ap-south-1
```

### 9. Delete Security Groups

Only after all resources using them are gone:

```bash
# ALB security group
aws ec2 delete-security-group --group-id sg-0ce737bbc8a4c6a77 --region ap-south-1

# ECS security group
aws ec2 delete-security-group --group-id sg-0f1ba63ea8db9e9f2 --region ap-south-1

# RDS security group
aws ec2 delete-security-group --group-id sg-08b5c84adf8754574 --region ap-south-1

# EC2 security group (if not default)
# Find id first: aws ec2 describe-security-groups --group-names blog-backend-sg --region ap-south-1
```

If any fails with "DependencyViolation," check BOTH kinds of dependencies:

**Kind 1: Network interfaces still using the SG**
```bash
aws ec2 describe-network-interfaces \
  --filters "Name=group-id,Values=<sg-id>" \
  --region ap-south-1
```

**Kind 2: Other SGs referencing this SG in their rules** (common with chained SGs like ALB→EC2→RDS)
```bash
# Find SGs referencing the one you want to delete
aws ec2 describe-security-groups \
  --filters "Name=ip-permission.group-id,Values=<sg-id>" \
  --query "SecurityGroups[*].{Id:GroupId,Name:GroupName}" \
  --output table \
  --region ap-south-1
```

If other SGs reference it, revoke those rules first:
```bash
aws ec2 revoke-security-group-ingress \
  --group-id <referencing-sg> \
  --protocol tcp --port <port> \
  --source-group <sg-being-deleted> \
  --region ap-south-1
```

Then retry the delete. Delete the ALB SG first (it's the parent of the chain), then the ones that were referencing it.

### 10. Delete ECR Repository

```bash
aws ecr delete-repository \
  --repository-name blog-backend \
  --force \
  --region ap-south-1
```

### 11. Delete CloudWatch Log Groups

```bash
aws logs delete-log-group --log-group-name /aws/ec2/blog-backend --region ap-south-1
aws logs delete-log-group --log-group-name /aws/ecs/blog-backend --region ap-south-1
```

Also check for any Lambda log groups from earlier experiments:
```bash
aws logs describe-log-groups --log-group-name-prefix /aws/lambda/blog --region ap-south-1
```

Delete any found:
```bash
aws logs delete-log-group --log-group-name <name> --region ap-south-1
```

### 12. Delete IAM Roles and Instance Profiles

**Task Execution Role:**
```bash
aws iam detach-role-policy \
  --role-name ecsTaskExecutionRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy

aws iam delete-role --role-name ecsTaskExecutionRole
```

**Task Role (blog-backend):**
```bash
# List inline policies attached
aws iam list-role-policies --role-name blog-backend-task-role

# Delete each inline policy
aws iam delete-role-policy \
  --role-name blog-backend-task-role \
  --policy-name blog-backend-s3-uploads-policy

aws iam delete-role --role-name blog-backend-task-role
```

**EC2 CloudWatch Role:**
```bash
aws iam detach-role-policy \
  --role-name blog-ec2-cloudwatch-role \
  --policy-arn arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy

aws iam detach-role-policy \
  --role-name blog-ec2-cloudwatch-role \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser

# Instance profile name may differ from role name (Console-created profiles
# often match role name exactly). Find the actual name first:
aws iam list-instance-profiles-for-role \
  --role-name blog-ec2-cloudwatch-role \
  --query "InstanceProfiles[*].InstanceProfileName" --output text

# Then use that name in the next commands (example uses role name):
aws iam remove-role-from-instance-profile \
  --instance-profile-name blog-ec2-cloudwatch-role \
  --role-name blog-ec2-cloudwatch-role

aws iam delete-instance-profile --instance-profile-name blog-ec2-cloudwatch-role

aws iam delete-role --role-name blog-ec2-cloudwatch-role
```

---

## Preserve (Do NOT Delete)

- CloudFront distribution `EOV3277U5A8CF` (will reuse and reconfigure)
- S3 bucket `praveen-blog-frontend` (will overwrite content with new UI)
- OIDC provider `arn:aws:iam::557690605487:oidc-provider/token.actions.githubusercontent.com`
- IAM role `github-actions-deploy-role` (still needed for CI/CD)
- AWS Budgets and billing alerts

---

## Verification

After all steps, verify with:

```bash
# No EC2 instances
aws ec2 describe-instances \
  --query "Reservations[*].Instances[*].{Id:InstanceId,State:State.Name}" \
  --region ap-south-1

# No RDS instances
aws rds describe-db-instances \
  --query "DBInstances[*].DBInstanceIdentifier" \
  --region ap-south-1

# No ECS clusters
aws ecs list-clusters --region ap-south-1

# No ALBs
aws elbv2 describe-load-balancers --region ap-south-1

# No target groups
aws elbv2 describe-target-groups --region ap-south-1

# CloudFront still exists
aws cloudfront list-distributions \
  --query "DistributionList.Items[*].{Id:Id,Status:Status,Comment:Comment}"

# Frontend bucket still exists
aws s3 ls | grep blog-frontend
```

---

## Handling Common Errors

### "InvalidParameterCombination" on RDS delete

Cause: RDS Multi-AZ or Read Replica dependencies. For our db.t3.micro Single-AZ, shouldn't happen.

If it does:
```bash
aws rds modify-db-instance \
  --db-instance-identifier blog-db \
  --deletion-protection false \
  --apply-immediately \
  --region ap-south-1
```

Then retry delete.

### "DependencyViolation" on Security Group delete

A resource still uses the SG. Find it:
```bash
aws ec2 describe-network-interfaces \
  --filters "Name=group-id,Values=<sg-id>" \
  --region ap-south-1
```

Delete or detach the resource, then retry.

### "ResourceInUseException" on ECS delete

Service still has running tasks. Force it:
```bash
aws ecs update-service \
  --cluster blog-cluster \
  --service blog-backend-service \
  --desired-count 0 \
  --region ap-south-1

# Wait 30 seconds

aws ecs delete-service \
  --cluster blog-cluster \
  --service blog-backend-service \
  --force \
  --region ap-south-1
```

### "ListenerNotFound" on delete-listener

Already deleted or never existed. Skip and continue.

### "CannotDelete" on IAM role

Role has attached policies. List and detach:
```bash
aws iam list-attached-role-policies --role-name <role-name>
aws iam list-role-policies --role-name <role-name>
```

Detach managed policies, delete inline policies, then delete role.

---

## After Cleanup

Immediate next steps (Phase 1):
1. Sign up for Qdrant Cloud (free tier)
2. Sign up for Groq (free tier)
3. Enable Bedrock Titan V2 model access in AWS Console
4. Create Secrets Manager entries
5. Create DynamoDB tables (see ARCHITECTURE.md)
6. Create S3 content bucket
7. Create SQS FIFO queue
8. Initialize Qdrant collection with hybrid search config

Cost check after cleanup:
```bash
aws ce get-cost-and-usage \
  --time-period Start=$(date -u +%Y-%m-01),End=$(date -u +%Y-%m-%d) \
  --granularity DAILY \
  --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=SERVICE \
  --output table \
  --region us-east-1
```

Expected daily cost after cleanup: near-zero (only tiny CloudFront + S3 charges).
