# Stage 6: Infrastructure as Code — Terraform

**Status:** Not started
**Goal:** Codify everything in Terraform. Destroy and recreate the full stack with one command.

---

## Checklist

- [ ] Terraform installed
- [ ] First .tf file (S3 bucket) — init/plan/apply/destroy cycle
- [ ] VPC + networking in Terraform
- [ ] RDS in Terraform
- [ ] S3 buckets in Terraform
- [ ] CloudFront in Terraform
- [ ] ECR + ECS in Terraform
- [ ] ALB + listeners in Terraform
- [ ] Route 53 records in Terraform
- [ ] IAM roles/policies in Terraform
- [ ] Remote state (S3 + DynamoDB) configured
- [ ] Full destroy + apply test passed

## Commands Used

## Terraform Files Structure

```
terraform/
  main.tf
  variables.tf
  outputs.tf
  modules/
    vpc/
    ecs/
    rds/
    ...
```

## Things That Broke & How I Fixed Them

## What I Learned (In My Own Words)

## Questions That Came Up
