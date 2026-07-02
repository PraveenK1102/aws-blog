# Stage 2: S3 + CloudFront — Frontend Hosting

**Status:** ✅ Complete
**Started:** 2026-06-02
**Completed:** 2026-06-04
**Goal:** React frontend served via S3 + CloudFront with HTTPS

---

## Checklist

- [x] S3 bucket created (`praveen-blog-frontend`)
- [x] OAC created (Origin Access Control — only CloudFront can read S3)
- [x] CloudFront distribution created (`d261g450savmee.cloudfront.net`)
- [x] S3 bucket policy set (allows only this CloudFront distribution)
- [x] Custom error responses configured (403/404 → /index.html for SPA routing)
- [x] Frontend built and uploaded to S3
- [x] Cache invalidation done after deploy
- [x] Frontend loads via CloudFront URL
- [x] Frontend calls EC2 backend API (CORS working)
- [x] Full blog working end-to-end (register, login, create, view, edit, delete posts)
- [ ] (Stage 3) Custom domain with Route 53
- [ ] (Stage 3) HTTPS everywhere (ALB for backend)

## Frontend Switch: Next.js → React + Vite

Started with Next.js static export (`output: "export"`), but hit a fundamental problem:
- Next.js generates **different HTML files per route**, each with different JS (code splitting)
- Custom error response serves `/index.html` (home page) for all 404s
- Home page HTML only has home page JavaScript — can't render other pages
- Clicking into `/posts/123` would show home page instead of post detail

**Switched to React + Vite** (true SPA):
- ONE `index.html` with ALL JavaScript in one bundle
- Custom error response serves that single `index.html` for all 404s
- React Router reads the URL and renders the correct page
- Everything works because all code is in one file

**Lesson:** Next.js static export is NOT a true SPA. If you need static hosting on S3 + CloudFront, use React + Vite (true SPA) or deploy Next.js as a server on ECS.

## Deploy Flow (do this every time you update frontend)

```bash
# 1. Build
cd blog-frontend
VITE_API_URL=http://15.206.179.93:4000 npm run build

# 2. Upload to S3 (--delete removes old files)
aws s3 sync dist/ s3://praveen-blog-frontend/ --delete

# 3. Clear CloudFront cache (MUST do this or users see old version)
aws cloudfront create-invalidation --distribution-id EOV3277U5A8CF --paths "/*"

# 4. Wait 30-60 seconds, then test
```

## Commands Used

```bash
# Create S3 bucket in Mumbai
aws s3 mb s3://praveen-blog-frontend --region ap-south-1

# Upload built files to S3
aws s3 sync dist/ s3://praveen-blog-frontend/ --delete

# Create Origin Access Control
aws cloudfront create-origin-access-control \
  --origin-access-control-config '{
    "Name": "blog-frontend-oac",
    "SigningProtocol": "sigv4",
    "SigningBehavior": "always",
    "OriginAccessControlOriginType": "s3"
  }'

# Create CloudFront distribution
aws cloudfront create-distribution --distribution-config '{...}'
# Key settings:
#   Origin: praveen-blog-frontend.s3.ap-south-1.amazonaws.com
#   OAC: E3MND7Y2HC8YS8
#   DefaultRootObject: index.html
#   ViewerProtocolPolicy: allow-all (temporary — no HTTPS on backend yet)
#   CachePolicyId: 658327ea-... (CachingOptimized managed policy)
#   Compress: true (gzip/brotli)
#   CustomErrorResponses: 403,404 → /index.html (200) — SPA routing
#   PriceClass: PriceClass_200

# Set S3 bucket policy — allow ONLY this CloudFront distribution
aws s3api put-bucket-policy --bucket praveen-blog-frontend --policy '{
  "Statement": [{
    "Principal": {"Service": "cloudfront.amazonaws.com"},
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::praveen-blog-frontend/*",
    "Condition": {"StringEquals": {
      "AWS:SourceArn": "arn:aws:cloudfront::557690605487:distribution/EOV3277U5A8CF"
    }}
  }]
}'

# Invalidate cache after deploying new files
aws cloudfront create-invalidation --distribution-id EOV3277U5A8CF --paths "/*"
```

## AWS Resources Created

| Resource | Name/ID | Details |
|----------|---------|---------|
| S3 Bucket | praveen-blog-frontend | ap-south-1, holds static frontend files |
| OAC | E3MND7Y2HC8YS8 (blog-frontend-oac) | Signs CloudFront → S3 requests |
| CloudFront Distribution | EOV3277U5A8CF | d261g450savmee.cloudfront.net |

## Things That Broke & How I Fixed Them

### 1. Mixed content blocking (HTTPS frontend → HTTP backend)
- CloudFront serves over HTTPS, EC2 backend is HTTP only
- Browser blocks: "HTTPS page cannot call HTTP API"
- **Temporary fix:** Changed CloudFront ViewerProtocolPolicy to `allow-all` (serve over HTTP too)
- **Real fix (Stage 3):** ALB with HTTPS cert for backend, then switch back to `redirect-to-https`

### 2. HSTS browser auto-upgrade
- After visiting site on HTTPS, browser remembers and auto-upgrades HTTP → HTTPS
- **Fix:** Use incognito window (no HSTS memory) or clear browser cache

### 3. Next.js static export routing failure
- Clicking a post link showed home page instead of post detail
- Root cause: Next.js code splitting — each HTML file loads different JS chunks
- Custom error response serves home page HTML for all 404s — wrong JS loads
- **Fix:** Switched to React + Vite (true SPA, one index.html with all code)

### 4. Old files showing after S3 update
- Uploaded new files to S3 but CloudFront still served old cached version
- **Fix:** Must run `aws cloudfront create-invalidation --paths "/*"` after every deploy

## What I Learned

### S3
- Object storage — flat key-value store, not a filesystem
- Good for static files (HTML, JS, CSS, images)
- Bucket names are globally unique across all AWS accounts
- Bucket policies control WHO can access WHAT with WHICH conditions

### CloudFront
- CDN with 400+ edge locations worldwide — caches files close to users
- HTTPS with free SSL certificates via ACM (automatic for *.cloudfront.net domains)
- Custom error responses enable SPA client-side routing (404 → serve index.html)
- OAC (Origin Access Control) keeps S3 private — signs requests with SigV4
- Cache invalidation: must explicitly clear after deploying new files (`/*` counts as 1 path, 1000/month free)
- PriceClass controls which edge locations to use (cost vs coverage)
- Compress: true enables gzip/brotli — reduces file sizes ~60%
- Supports multiple origins with path-based routing (/* → S3, /api/* → ALB)

### DNS (Route 53)
- DNS is a phone book: name → address, nothing more
- www.mysite.com → CNAME → CloudFront URL (for frontend)
- api.mysite.com → CNAME → ALB URL (for backend)
- ALIAS record = AWS-specific, works for naked domains (mysite.com)
- The browser makes separate DNS lookups — Route 53 doesn't "route" traffic

### CORS & Preflight
- Cross-origin requests (different domains) trigger CORS checks
- Preflight (OPTIONS) is a real network call — sent automatically by browser before every non-simple request
- Authorization header makes every API call "non-simple" → always preflighted
- 2 network calls per API request (OPTIONS + actual request)
- **Production fix:** Same domain via CloudFront path-based routing (no CORS needed)

### Security: Principle of Least Privilege
- Bucket policy: WHO (CloudFront only), WHAT (GetObject only), WHERE (this bucket), WHICH (this distribution)
- Never use `*` for principal or action in production
- Layer restrictions — if any check fails, access denied

### Environment Variables & Secrets
- Frontend env vars (VITE_API_URL, REACT_APP_*, NEXT_PUBLIC_*) are baked into JS at build time — not runtime
- Same-domain setup eliminates the need for API URL env vars (relative paths: `/api/posts`)
- Never put `.env` with secrets in git repo (.gitignore should exclude it)
- Production: use AWS Secrets Manager or SSM Parameter Store (encrypted, auditable)
- CI/CD (Stage 4): pipeline injects secrets at deploy time

### Architecture
- Frontend (static) and Backend (API) are completely separate infrastructure
- Connected only by VITE_API_URL environment variable at build time (or relative paths with same domain)
- This separation lets them scale independently
- Production: same domain via CloudFront with multiple origins eliminates CORS
- API prefix convention: `/api/*` for backend, everything else for frontend — industry standard

### Next.js vs React+Vite for Static Hosting
- Next.js static export generates different HTML per route with code splitting — NOT a true SPA
- Custom error response (serve index.html for 404s) fails because each page has different JS
- React+Vite generates ONE index.html with ALL JS — true SPA, works perfectly with S3+CloudFront
- Use Next.js as a server (ECS) or use Vite for static S3 hosting

### Docker Deployment Flow (EC2)
- git pull → docker build → docker stop/rm → docker run (with env vars)
- Container ID (hash) printed on success = container started
- `docker ps` shows running containers, `docker logs <name>` shows output
- `-e` flag passes env vars, but `.env` file in build context also works via COPY

## Questions That Came Up

- How does the browser know api vs www goes to different places? → DNS records, two separate lookups
- Why ECS instead of EC2 in production? → ECS automates container management (restart, scale, deploy)
- What is OAC? → Signs CloudFront requests to S3, proving identity — keeps S3 private
- Is preflight avoidable? → Yes, same domain (CloudFront path-based routing) eliminates CORS entirely
- Why not Next.js static export? → Code splitting breaks SPA routing on S3. Use Vite for static hosting or deploy Next.js as a server.
- Does CloudFront have SSL by default? → Yes, *.cloudfront.net gets a free cert automatically. Custom domains need ACM.
- What if a frontend page starts with /api? → /api/* rule needs the slash, so /api-docs goes to S3, /api/posts goes to ALB. No conflict.
- How are secrets managed in production? → AWS Secrets Manager / SSM Parameter Store, never in git.
