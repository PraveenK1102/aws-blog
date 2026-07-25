import { useEffect, useRef, useState } from "react";
import {
  ask, createPost, listPosts, login, signup, me,
  getToken, setToken, clearToken,
} from "./api";

export default function App() {
  const [user, setUser] = useState(null);
  const [booting, setBooting] = useState(true);

  // On load, if a token exists, validate it.
  useEffect(() => {
    if (!getToken()) { setBooting(false); return; }
    me().then((d) => setUser(d.user)).catch(() => clearToken()).finally(() => setBooting(false));
  }, []);

  function onAuthed(resp) {
    setToken(resp.token);
    setUser(resp.user);
  }
  function logout() {
    clearToken();
    setUser(null);
  }

  if (booting) return <div className="empty">Loading…</div>;
  if (!user) return <Auth onAuthed={onAuthed} />;
  return <Shell user={user} onLogout={logout} />;
}

// ---------------------------------------------------------------------------
// Auth gate — nothing else is reachable until signed in.
// ---------------------------------------------------------------------------
function Auth({ onAuthed }) {
  const [mode, setMode] = useState("login"); // "login" | "signup"
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setErr(null); setBusy(true);
    try {
      const resp = mode === "login"
        ? await login(email.trim(), password)
        : await signup(email.trim(), password, name.trim() || undefined);
      onAuthed(resp);
    } catch (ex) {
      setErr(ex.message || "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <div className="brand"><span className="logo">◆</span> MultiTenantRAG</div>
        <p className="auth-sub">Your personal blog with an AI that answers only from what you write.</p>

        <div className="auth-toggle">
          <button className={mode === "login" ? "active" : ""} onClick={() => setMode("login")}>Log in</button>
          <button className={mode === "signup" ? "active" : ""} onClick={() => setMode("signup")}>Sign up</button>
        </div>

        <form onSubmit={submit} className="auth-form">
          {mode === "signup" && (
            <input placeholder="Display name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
          )}
          <input type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          <input type="password" placeholder="Password (min 8 chars)" value={password}
                 onChange={(e) => setPassword(e.target.value)} required minLength={8} />
          {err && <div className="auth-err">{err}</div>}
          <button type="submit" disabled={busy}>
            {busy ? "…" : mode === "login" ? "Log in" : "Create account"}
          </button>
        </form>
        <div className="auth-hint">No email verification in this build — sign up freely to test.</div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Signed-in shell
// ---------------------------------------------------------------------------
function Shell({ user, onLogout }) {
  const [tab, setTab] = useState("chat");
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand"><span className="logo">◆</span> MultiTenantRAG</div>
        <div className="userbox">
          <span className="uemail">{user.email}</span>
          <button className="logout" onClick={onLogout}>Log out</button>
        </div>
      </header>
      <div className="tabs">
        {["chat", "posts", "write"].map((t) => (
          <button key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {t === "chat" ? "Ask my AI" : t === "posts" ? "My posts" : "Write"}
          </button>
        ))}
      </div>
      <main className="main">
        {tab === "chat" && <Chat user={user} />}
        {tab === "posts" && <Posts />}
        {tab === "write" && <Write onDone={() => setTab("posts")} />}
      </main>
    </div>
  );
}

function Chat({ user }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef(null);

  useEffect(() => { scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight); }, [messages]);

  async function send(e) {
    e.preventDefault();
    const q = input.trim();
    if (!q || busy) return;
    setInput(""); setBusy(true);
    setMessages((m) => [...m, { role: "user", text: q }, { role: "ai", text: "", citations: null, pending: true }]);

    const queue = [];
    let reading = true;
    // Typewriter cadence — works whether tokens stream in (dev) or arrive together (prod).
    const timer = setInterval(() => {
      if (queue.length) {
        const tok = queue.shift();
        setMessages((m) => {
          const c = [...m]; const last = c[c.length - 1];
          c[c.length - 1] = { ...last, text: last.text + tok };
          return c;
        });
      } else if (!reading) clearInterval(timer);
    }, 16);

    try {
      const citations = await ask(q, (t) => queue.push(t));
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
            Ask your AI anything about what you’ve written. It answers <b>only</b> from your own posts —
            ask about something you haven’t written and it will politely decline.
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="bubble">
              {m.text || (m.pending ? <span className="dots">•••</span> : "")}
              {m.citations && m.citations.length > 0 && (
                <div className="citations">
                  {m.citations.map((c, j) => (
                    <span key={j} className="cite" title={`score ${c.score}`}>{c.title}</span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
      <form className="composer" onSubmit={send}>
        <input value={input} onChange={(e) => setInput(e.target.value)}
               placeholder="Ask your AI…" disabled={busy} />
        <button type="submit" disabled={busy || !input.trim()}>{busy ? "…" : "Send"}</button>
      </form>
    </div>
  );
}

function Posts() {
  const [posts, setPosts] = useState(null);
  const reload = () => { setPosts(null); listPosts().then(setPosts).catch(() => setPosts([])); };
  useEffect(reload, []);

  if (posts === null) return <div className="empty">Loading posts…</div>;
  if (posts.length === 0) return <div className="empty">No posts yet. Use the Write tab to add one.</div>;
  return (
    <div className="posts-wrap">
      <button className="refresh" onClick={reload}>↻ Refresh</button>
      <ul className="posts">
        {posts.map((p) => (
          <li key={p.post_id} className="post">
            <span className="post-title">{p.title}</span>
            <span className={`badge ${p.status}`}>{p.status}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Write({ onDone }) {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [status, setStatus] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setStatus({ kind: "busy", msg: "Publishing…" });
    try {
      const r = await createPost(title, content);
      setStatus({ kind: "ok", msg: `Published (${r.post_id}). Indexing runs async — check My posts in a few seconds.` });
      setTitle(""); setContent("");
      setTimeout(onDone, 1500);
    } catch (err) {
      setStatus({ kind: "err", msg: err.message });
    }
  }

  return (
    <form className="write" onSubmit={submit}>
      <input placeholder="Post title" value={title} onChange={(e) => setTitle(e.target.value)} required />
      <textarea placeholder="Markdown content — use # headings; the chunker is markdown-aware."
                value={content} onChange={(e) => setContent(e.target.value)} rows={12} required />
      <div className="write-actions">
        <button type="submit" disabled={status?.kind === "busy"}>Publish</button>
        {status && <span className={`status ${status.kind}`}>{status.msg}</span>}
      </div>
    </form>
  );
}
