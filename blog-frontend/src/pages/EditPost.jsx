import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api, getToken } from "../api";

export default function EditPost() {
  const { id } = useParams();
  const navigate = useNavigate();
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

      const base = import.meta.env.VITE_API_URL || "";
      const token = getToken();
      const res = await fetch(`${base}/api/posts/${id}`, {
        method: "PATCH",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || res.statusText);
      }
      navigate(`/posts/${id}`);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  if (fetching) return <p>Loading...</p>;

  return (
    <>
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
