# Minimal Blog — Stage 1 (Local)

Two repos in one workspace for learning **independent deployments**, **CORS**, and **API separation**. Later: frontend → S3 + CloudFront, backend → EC2 (then ECS), DB → RDS.

## Structure

| Repo           | Stack              | Deploy (later)     |
|----------------|--------------------|--------------------|
| **blog-frontend** | Next.js (static export) | S3 + CloudFront |
| **blog-backend**  | Node + Express, Prisma, PostgreSQL | EC2 → ECS   |

## Run locally (no AWS yet)

### 1. Backend (API + DB)

From `blog-backend/`:

**Option A — all in Docker**

```bash
cd blog-backend
docker compose up --build
```

API: http://localhost:4000  
Health: http://localhost:4000/health

**Option B — Postgres in Docker, app on host**

```bash
cd blog-backend
docker compose up postgres -d
cp .env.example .env
# In .env set: DATABASE_URL=postgresql://bloguser:${POSTGRES_PASSWORD}@localhost:5432/blogdb
#   (use the same password as POSTGRES_PASSWORD in blog-backend/docker-compose.yml)
npm install
npx prisma db push
npm run dev
```

### 2. Frontend

From `blog-frontend/`:

```bash
cd blog-frontend
npm install
npm run dev
```

Open http://localhost:3000. Frontend calls the API at `NEXT_PUBLIC_API_URL` (default `http://localhost:4000`). You’ll learn **CORS** when the browser enforces origin; backend is configured with `CORS_ORIGIN=http://localhost:3000`.

## What you have

- **Auth**: Register / Login (JWT). Token in `localStorage`; `Authorization: Bearer <token>` on API calls.
- **Posts**: List, view, create, edit, delete. Optional image upload (stored locally on backend).
- **Docker**: Backend runs in container; Postgres in container; `docker-compose` for both.

## Next (Stage 2)

- Move DB to **AWS RDS**.
- Run backend on **EC2** (Docker).
- Build frontend and upload to **S3**, put **CloudFront** in front.
- Frontend `NEXT_PUBLIC_API_URL` → your EC2/ALB URL.
