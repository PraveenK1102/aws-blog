# blog-frontend

Minimal blog UI. **Next.js** with static export. Deploy to **S3 + CloudFront** (later).

## Local dev

```bash
cp .env.example .env.local
# Ensure NEXT_PUBLIC_API_URL=http://localhost:4000 (default)
npm install
npm run dev
```

Open http://localhost:3000. The app calls the API at `NEXT_PUBLIC_API_URL`. Start the backend (see `blog-backend`) first.

## Build for deploy (static export)

```bash
npm run build
```

Output is in `out/`. For AWS you’ll upload `out/` to S3 and put CloudFront in front. Set `NEXT_PUBLIC_API_URL` to your API URL before building.
