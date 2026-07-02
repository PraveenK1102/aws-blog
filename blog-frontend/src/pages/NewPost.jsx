import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { getToken } from "../api";

export default function NewPost() {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [file, setFile] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const formData = new FormData();
      formData.set("title", title);
      formData.set("content", content);
      if (file) formData.set("image", file);

      const base = import.meta.env.VITE_API_URL || "";
      const token = getToken();
      const res = await fetch(`${base}/api/posts`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || res.statusText);
      }
      const post = await res.json();
      navigate(`/posts/${post.id}`);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
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
