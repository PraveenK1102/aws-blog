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
# Edit .env: DATABASE_URL=postgresql://bloguser:localdev_only@localhost:5432/blogdb
npm install
npx prisma db push
npm run dev
```

## Env

See `.env.example`. Key vars: `DATABASE_URL`, `JWT_SECRET`, `CORS_ORIGIN`, `PORT`, `UPLOAD_DIR`.

### `POSTGRES_PASSWORD` (local development only)

`POSTGRES_PASSWORD` is **optional**. If it is not set, `docker-compose.yml` substitutes the explicitly
non-secret development default `localdev_only`, so `docker compose up postgres` works with zero config.

To use your own value, put it in an untracked `.env` next to `docker-compose.yml`:

```bash
POSTGRES_PASSWORD=<your-local-value>
```

Compose applies it to both the Postgres container and the app's `DATABASE_URL` from that single variable.

This default is a disposable local fixture and is **not suitable for production**. It has no relationship to
any deployed environment: the AWS deployment runs on unrelated infrastructure and reads every secret from
AWS Secrets Manager, so it is unaffected by this setting.

## Endpoints

- `POST /auth/register` — body: `{ email, password, name? }`
- `POST /auth/login` — body: `{ email, password }`
- `GET /posts` — list posts
- `GET /posts/:id` — get one post
- `POST /posts` — create (auth, multipart: title, content, image?)
- `PATCH /posts/:id` — update (auth, author only)
- `DELETE /posts/:id` — delete (auth, author only)
