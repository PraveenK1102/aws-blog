# blog-backend

REST API for the minimal blog. Deploy to **EC2** (later ECS).

## Local dev

### Option A: All in Docker

```bash
docker compose up --build
```

API: http://localhost:4000  
Health: http://localhost:4000/health

### Option B: Postgres in Docker, app on host

```bash
docker compose up postgres -d
cp .env.example .env
# Edit .env: DATABASE_URL=postgresql://bloguser:${POSTGRES_PASSWORD}@localhost:5432/blogdb
npm install
npx prisma db push
npm run dev
```

## Env

See `.env.example`. Key vars: `DATABASE_URL`, `JWT_SECRET`, `CORS_ORIGIN`, `PORT`, `UPLOAD_DIR`.

## Endpoints

- `POST /auth/register` — body: `{ email, password, name? }`
- `POST /auth/login` — body: `{ email, password }`
- `GET /posts` — list posts
- `GET /posts/:id` — get one post
- `POST /posts` — create (auth, multipart: title, content, image?)
- `PATCH /posts/:id` — update (auth, author only)
- `DELETE /posts/:id` — delete (auth, author only)
