# Stage 4: CI/CD + ECR + ECS Fargate

**Status:** Not started
**Goal:** Automated deployments — git push to production via GitHub Actions + ECS Fargate

---

## Checklist

- [ ] ECR repository created
- [ ] Docker image pushed to ECR
- [ ] GitHub Actions workflow: build + push to ECR
- [ ] Simple deploy: GitHub Actions → SSH to EC2 → pull image
- [ ] ECS Cluster created
- [ ] Task Definition created
- [ ] ECS Service created (connected to ALB)
- [ ] GitHub Actions updated for ECS deploy
- [ ] Rolling deployment working
- [ ] EC2 instance terminated (Fargate handles compute)

## Commands Used

## AWS Resources Created

## Things That Broke & How I Fixed Them

## What I Learned (In My Own Words)

## Questions That Came Up
