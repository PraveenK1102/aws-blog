import { useEffect, useRef, useState } from "react";
import { listUsers, listPosts, createPost, ask } from "./api";

export default function App() {
  const [users, setUsers] = useState([]);
  const [current, setCurrent] = useState(null);
  const [tab, setTab] = useState("chat");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listUsers()
      .then((u) => {
        setUsers(u);
        setCurrent(u[0] || null);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">◆</span> MultiTenantRAG
        </div>
        <div className="tagline">Serverless multi-tenant RAG · each persona’s AI answers only from their own posts</div>
      </header>

      {loading ? (
        <div className="empty">Loading personas…</div>
      ) : !current ? (
        <div className="empty">No personas found. Seed some users first.</div>
      ) : (
        <div className="layout">
          <aside className="sidebar">
            <div className="side-label">Persona</div>
            {users.map((u) => (
              <button
                key={u.user_id}
                className={`persona ${current.user_id === u.user_id ? "active" : ""}`}
                onClick={() => setCurrent(u)}
              >
                <span className="persona-name">{u.display_name}</span>
                <span className="persona-domain">{u.domain}</span>
              </button>
            ))}
          </aside>

          <main className="main">
            <div className="tabs">
              {["chat", "posts", "write"].map((t) => (
                <button key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
                  {t === "chat" ? "Chat" : t === "posts" ? "Posts" : "Write"}
                </button>
              ))}
            </div>

            {tab === "chat" && <Chat user={current} />}
            {tab === "posts" && <Posts user={current} />}
            {tab === "write" && <Write user={current} onDone={() => setTab("posts")} />}
          </main>
        </div>
      )}
    </div>
  );
}

function Chat({ user }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef(null);

  useEffect(() => setMessages([]), [user.user_id]);
  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight);
  }, [messages]);

  async function send(e) {
    e.preventDefault();
    const q = input.trim();
    if (!q || busy) return;
    setInput("");
    setBusy(true);
    setMessages((m) => [...m, { role: "user", text: q }, { role: "ai", text: "", citations: null, pending: true }]);

    const queue = [];
    let reading = true;
    // Typewriter: drain tokens at a steady cadence even though the buffered
    // response arrives in one burst. (Same code path works if we flip the
    // backend to true streaming later.)
    const timer = setInterval(() => {
      if (queue.length) {
        const tok = queue.shift();
        setMessages((m) => {
          const c = [...m];
          const last = c[c.length - 1];
          c[c.length - 1] = { ...last, text: last.text + tok };
          return c;
        });
      } else if (!reading) {
        clearInterval(timer);
      }
    }, 18);

    try {
      const citations = await ask(user.user_id, q, (t) => queue.push(t));
      reading = false;
      setMessages((m) => {
        const c = [...m];
        c[c.length - 1] = { ...c[c.length - 1], citations, pending: false };
        return c;
      });
    } catch (err) {
      reading = false;
      setMessages((m) => {
        const c = [...m];
        c[c.length - 1] = { ...c[c.length - 1], text: `⚠️ ${err.message}`, pending: false };
        return c;
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="chat">
      <div className="messages" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="hint">
            Ask <b>{user.display_name}</b>’s AI something about {user.domain}. It only knows {user.display_name}’s own
            posts — ask about another persona’s topic and it will politely decline.
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="bubble">
              {m.text || (m.pending ? <span className="dots">•••</span> : "")}
              {m.citations && m.citations.length > 0 && (
                <div className="citations">
                  {m.citations.map((c, j) => (
                    <span key={j} className="cite" title={`score ${c.score}`}>
                      {c.title}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
      <form className="composer" onSubmit={send}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={`Ask ${user.display_name}’s AI…`}
          disabled={busy}
        />
        <button type="submit" disabled={busy || !input.trim()}>
          {busy ? "…" : "Send"}
        </button>
      </form>
    </div>
  );
}

function Posts({ user }) {
  const [posts, setPosts] = useState(null);
  useEffect(() => {
    setPosts(null);
    listPosts(user.tenant_id).then(setPosts).catch(() => setPosts([]));
  }, [user.tenant_id]);

  if (posts === null) return <div className="empty">Loading posts…</div>;
  if (posts.length === 0) return <div className="empty">No posts yet. Use the Write tab to add one.</div>;
  return (
    <ul className="posts">
      {posts.map((p) => (
        <li key={p.post_id} className="post">
          <span className="post-title">{p.title}</span>
          <span className={`badge ${p.status}`}>{p.status}</span>
        </li>
      ))}
    </ul>
  );
}

function Write({ user, onDone }) {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [status, setStatus] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setStatus({ kind: "busy", msg: "Publishing…" });
    try {
      const r = await createPost(user.user_id, title, content);
      setStatus({ kind: "ok", msg: `Published (${r.post_id}). Indexing runs async — check Posts in a few seconds.` });
      setTitle("");
      setContent("");
      setTimeout(onDone, 1500);
    } catch (err) {
      setStatus({ kind: "err", msg: err.message });
    }
  }

  return (
    <form className="write" onSubmit={submit}>
      <input placeholder="Post title" value={title} onChange={(e) => setTitle(e.target.value)} required />
      <textarea
        placeholder="Markdown content — use # headings; the chunker is markdown-aware."
        value={content}
        onChange={(e) => setContent(e.target.value)}
        rows={12}
        required
      />
      <div className="write-actions">
        <button type="submit" disabled={status?.kind === "busy"}>
          Publish as {user.display_name}
        </button>
        {status && <span className={`status ${status.kind}`}>{status.msg}</span>}
      </div>
    </form>
  );
}
