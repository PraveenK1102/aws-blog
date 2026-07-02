import Link from "next/link";

export default function Home() {
  return (
    <>
      <nav>
        <Link href="/">Home</Link>
        <Link href="/posts/">Posts</Link>
        <Link href="/login/">Login</Link>
        <Link href="/register/">Register</Link>
      </nav>
      <h1>Minimal Blog</h1>
      <p>Stage 1 — local. Frontend: Next.js (static export). Backend: Express API. Deploy later: S3 + CloudFront (frontend), EC2 (backend).</p>
      <p><Link href="/posts/">View posts →</Link></p>
    </>
  );
}
