import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, getToken } from "../api";

export default function PostsList() {
  const [posts, setPosts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/posts")
      .then(setPosts)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <>
      <h1>Posts</h1>
      {loading && <p>Loading...</p>}
      {error && <p style={{ color: "red" }}>{error}</p>}
      {!loading && !error && (
        <ul className="post-list">
          {posts.length === 0 ? (
            <li>No posts yet.</li>
          ) : (
            posts.map((p) => (
              <li key={p.id}>
                <Link to={`/posts/${p.id}`}>{p.title}</Link>
                <div className="post-meta">
                  by {p.author?.name || p.author?.email} ·{" "}
                  {new Date(p.createdAt).toLocaleDateString()}
                </div>
              </li>
            ))
          )}
        </ul>
      )}
      {getToken() && (
        <p><Link to="/posts/new">Create post</Link></p>
      )}
    </>
  );
}
