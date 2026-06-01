"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, getToken } from "@/lib/api";

export default function PostsListPage() {
  const [posts, setPosts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [hasToken, setHasToken] = useState(false);
  useEffect(() => setHasToken(!!getToken()), []);

  useEffect(() => {
    api("/posts")
      .then(setPosts)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <>
      <nav>
        <Link href="/">Home</Link>
        <Link href="/posts/">Posts</Link>
        {hasToken ? (
          <>
            <Link href="/posts/new/">New post</Link>
            <a href="/" onClick={() => { localStorage.removeItem("token"); window.location.href = "/"; }}>Logout</a>
          </>
        ) : (
          <>
            <Link href="/login/">Login</Link>
            <Link href="/register/">Register</Link>
          </>
        )}
      </nav>
      <h1>Posts</h1>
      {loading && <p>Loading…</p>}
      {error && <p style={{ color: "red" }}>{error}</p>}
      {!loading && !error && (
        <ul className="post-list">
          {posts.length === 0 ? (
            <li>No posts yet.</li>
          ) : (
            posts.map((p) => (
              <li key={p.id}>
                <Link href={`/posts/${p.id}/`}>{p.title}</Link>
                <div className="post-meta">by {p.author?.name || p.author?.email} · {new Date(p.createdAt).toLocaleDateString()}</div>
              </li>
            ))
          )}
        </ul>
      )}
      {hasToken && <p><Link href="/posts/new/">Create post</Link></p>}
    </>
  );
}
