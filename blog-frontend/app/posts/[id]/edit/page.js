"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";

export default function EditPostPage() {
  const params = useParams();
  const router = useRouter();
  const id = params.id;
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [file, setFile] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [fetching, setFetching] = useState(true);

  useEffect(() => {
    if (!id) return;
    api(`/posts/${id}`)
      .then((p) => {
        setTitle(p.title);
        setContent(p.content || "");
      })
      .catch((err) => setError(err.message))
      .finally(() => setFetching(false));
  }, [id]);

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
      const res = await fetch(`${base}/posts/${id}`, {
        method: "PATCH",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || res.statusText);
      }
      router.push(`/posts/${id}/`);
      router.refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  if (fetching) return <><nav><Link href="/">Home</Link> <Link href="/posts/">Posts</Link></nav><p>Loading…</p></>;

  return (
    <>
      <nav>
        <Link href="/">Home</Link>
        <Link href="/posts/">Posts</Link>
        <Link href={`/posts/${id}/`}>View post</Link>
      </nav>
      <h1>Edit post</h1>
      <form onSubmit={handleSubmit}>
        {error && <p style={{ color: "red" }}>{error}</p>}
        <label>Title</label>
        <input value={title} onChange={(e) => setTitle(e.target.value)} required />
        <label>Content</label>
        <textarea value={content} onChange={(e) => setContent(e.target.value)} />
        <label>New image (optional, replaces current)</label>
        <input type="file" accept="image/*" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        <button type="submit" disabled={loading}>{loading ? "..." : "Update"}</button>
      </form>
    </>
  );
}
