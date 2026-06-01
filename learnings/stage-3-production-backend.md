# Stage 3: Production-Grade Backend

**Status:** Not started
**Goal:** VPC, ALB, HTTPS on backend, S3 uploads, CloudWatch logs, secrets management

---

## Checklist

- [ ] Custom VPC created (10.0.0.0/16)
- [ ] Public subnets (2 AZs) for EC2/ALB
- [ ] Private subnets (2 AZs) for RDS
- [ ] Internet Gateway attached
- [ ] Route tables configured
- [ ] EC2 and RDS migrated to new VPC
- [ ] ALB created in public subnets
- [ ] ACM certificate for api.yourblog.com
- [ ] HTTPS listener on ALB
- [ ] Route 53 record for API domain
- [ ] File uploads moved to S3 (multer-s3 or AWS SDK)
- [ ] CloudWatch agent installed on EC2
- [ ] Application logs in CloudWatch
- [ ] Basic alarm created (5xx errors)
- [ ] Secrets moved to SSM Parameter Store
- [ ] Frontend updated to use HTTPS API URL

## Commands Used

## AWS Resources Created

## Things That Broke & How I Fixed Them

## What I Learned (In My Own Words)

## Questions That Came Up
