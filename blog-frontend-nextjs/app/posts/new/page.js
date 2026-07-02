"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";

export default function NewPostPage() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [file, setFile] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [hasToken, setHasToken] = useState(false);

  useEffect(() => {
    setHasToken(!!localStorage.getItem("token"));
  }, []);

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const formData = new FormData();
      formData.set("title", title);
      formData.set("content", content);
      if (file) formData.set("image", file);
      const base = process.env.NEXT_PUBLIC_API_URL || "";
      const token = localStorage.getItem("token");
      const res = await fetch(`${base}/posts`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || res.statusText);
      }
      const post = await res.json();
      router.push(`/posts/${post.id}/`);
      router.refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  if (!hasToken && typeof window !== "undefined") {
    return (
      <>
        <nav><Link href="/">Home</Link> <Link href="/posts/">Posts</Link> <Link href="/login/">Login</Link></nav>
        <p>You must be logged in to create a post.</p>
      </>
    );
  }

  return (
    <>
      <nav>
        <Link href="/">Home</Link>
        <Link href="/posts/">Posts</Link>
        <Link href="/posts/new/">New post</Link>
      </nav>
      <h1>New post</h1>
      <form onSubmit={handleSubmit}>
        {error && <p style={{ color: "red" }}>{error}</p>}
        <label>Title</label>
        <input value={title} onChange={(e) => setTitle(e.target.value)} required />
        <label>Content</label>
        <textarea value={content} onChange={(e) => setContent(e.target.value)} />
        <label>Image (optional)</label>
        <input type="file" accept="image/*" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        <button type="submit" disabled={loading}>{loading ? "..." : "Create"}</button>
      </form>
    </>
  );
}
