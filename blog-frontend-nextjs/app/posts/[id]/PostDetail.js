"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";

function hasToken() {
  if (typeof window === "undefined") return false;
  return !!localStorage.getItem("token");
}

export default function PostDetail() {
  const params = useParams();
  const router = useRouter();
  const [post, setPost] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState(false);
  const id = params.id;
  const isAuthor = post && hasToken() && post.author?.id; // simplistic: real check would need current user id from API

  useEffect(() => {
    if (!id) return;
    api(`/posts/${id}`)
      .then(setPost)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [id]);

  async function handleDelete() {
    if (!confirm("Delete this post?")) return;
    setDeleting(true);
    try {
      await api(`/posts/${id}`, { method: "DELETE" });
      router.push("/posts/");
      router.refresh();
    } catch (err) {
      setError(err.data?.error || err.message);
    } finally {
      setDeleting(false);
    }
  }

  if (loading) return <><nav><Link href="/">Home</Link> <Link href="/posts/">Posts</Link></nav><p>Loading…</p></>;
  if (error || !post) return <><nav><Link href="/">Home</Link> <Link href="/posts/">Posts</Link></nav><p style={{ color: "red" }}>{error || "Not found"}</p></>;

  const baseUrl = typeof window !== "undefined" ? (process.env.NEXT_PUBLIC_API_URL || "") : "";
  const imageUrl = post.imagePath ? `${baseUrl}${post.imagePath}` : null;

  return (
    <>
      <nav>
        <Link href="/">Home</Link>
        <Link href="/posts/">Posts</Link>
      </nav>
      <article>
        <h1>{post.title}</h1>
        <div className="post-meta">by {post.author?.name || post.author?.email} · {new Date(post.createdAt).toLocaleDateString()}</div>
        {imageUrl && <img src={imageUrl} alt="" />}
        <div style={{ whiteSpace: "pre-wrap", marginTop: "1rem" }}>{post.content}</div>
        {hasToken() && (
          <p style={{ marginTop: "1rem" }}>
            <a href={`/posts/${id}/edit/`} className="btn btn-secondary">Edit</a>
            {" "}
            <button type="button" className="btn btn-danger" onClick={handleDelete} disabled={deleting}>{deleting ? "..." : "Delete"}</button>
          </p>
        )}
      </article>
    </>
  );
}
