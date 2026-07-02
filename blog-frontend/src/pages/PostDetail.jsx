import { useEffect, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { api, getToken } from "../api";

export default function PostDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [post, setPost] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (!id) return;
    api(`/posts/${id}`)
      .then(setPost)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [id]);

  async function handleDelete() {
    if (!window.confirm("Delete this post?")) return;
    setDeleting(true);
    try {
      await api(`/posts/${id}`, { method: "DELETE" });
      navigate("/posts");
    } catch (err) {
      setError(err.data?.error || err.message);
    } finally {
      setDeleting(false);
    }
  }

  if (loading) return <p>Loading...</p>;
  if (error || !post) return <p style={{ color: "red" }}>{error || "Not found"}</p>;

  const baseUrl = import.meta.env.VITE_API_URL || "";
  const imageUrl = post.imagePath ? `${baseUrl}${post.imagePath}` : null;

  return (
    <article>
      <h1>{post.title}</h1>
      <div className="post-meta">
        by {post.author?.name || post.author?.email} ·{" "}
        {new Date(post.createdAt).toLocaleDateString()}
      </div>
      {imageUrl && <img src={imageUrl} alt="" />}
      <div style={{ whiteSpace: "pre-wrap", marginTop: "1rem" }}>{post.content}</div>
      {getToken() && (
        <p style={{ marginTop: "1rem" }}>
          <Link to={`/posts/${id}/edit`} className="btn btn-secondary">Edit</Link>{" "}
          <button className="btn btn-danger" onClick={handleDelete} disabled={deleting}>
            {deleting ? "..." : "Delete"}
          </button>
        </p>
      )}
    </article>
  );
}
