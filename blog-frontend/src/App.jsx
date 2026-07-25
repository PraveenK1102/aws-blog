import { useEffect, useRef, useState } from "react";
import {
  ask, createPost, listProfiles, listProfilePosts, listMyPosts,
  login, signup, me, getToken, setToken, clearToken,
} from "./api";

export default function App() {
  const [user, setUser] = useState(null);
  const [booting, setBooting] = useState(true);

  useEffect(() => {
    if (!getToken()) { setBooting(false); return; }
    me().then((d) => setUser(d.user)).catch(() => clearToken()).finally(() => setBooting(false));
  }, []);

  if (booting) return <div className="empty">Loading…</div>;
  if (!user) return <Auth onAuthed={(r) => { setToken(r.token); setUser(r.user); }} />;
  return <Shell user={user} onLogout={() => { clearToken(); setUser(null); }} />;
}

// ---------------------------------------------------------------------------
// Auth gate
// ---------------------------------------------------------------------------
function Auth({ onAuthed }) {
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault(); setErr(null); setBusy(true);
    try {
      const resp = mode === "login"
        ? await login(email.trim(), password)
        : await signup(email.trim(), password, name.trim() || undefined);
      onAuthed(resp);
    } catch (ex) { setErr(ex.message || "Something went wrong"); }
    finally { setBusy(false); }
  }

  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <div className="brand"><span className="logo">◆</span> MultiTenantRAG</div>
        <p className="auth-sub">Browse people’s blogs and chat with an AI that answers only from what they wrote.</p>
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
          <button type="submit" disabled={busy}>{busy ? "…" : mode === "login" ? "Log in" : "Create account"}</button>
        </form>
        <div className="auth-hint">No email verification in this build — sign up freely to test.</div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Signed-in shell: Discover (profiles) | Write (my blog)
// ---------------------------------------------------------------------------
function Shell({ user, onLogout }) {
  const [tab, setTab] = useState("discover");
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
        <button className={`tab ${tab === "discover" ? "active" : ""}`} onClick={() => setTab("discover")}>Discover</button>
        <button className={`tab ${tab === "write" ? "active" : ""}`} onClick={() => setTab("write")}>My blog</button>
      </div>
      <main className="main">
        {tab === "discover" ? <Discover /> : <MyBlog />}
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Discover: a directory of profiles → click one → their page + Ask their AI
// ---------------------------------------------------------------------------
function Discover() {
  const [profiles, setProfiles] = useState(null);
  const [selected, setSelected] = useState(null);

  useEffect(() => { listProfiles().then(setProfiles).catch(() => setProfiles([])); }, []);

  if (selected) return <ProfilePage profile={selected} onBack={() => setSelected(null)} />;
  if (profiles === null) return <div className="empty">Loading people…</div>;
  if (profiles.length === 0) return <div className="empty">No profiles yet.</div>;

  return (
    <div className="discover">
      <div className="discover-head">Pick someone and ask their AI</div>
      <div className="profile-grid">
        {profiles.map((p) => (
          <button key={p.tenant_id} className="profile-card" onClick={() => setSelected(p)}>
            <div className="avatar">{initials(p.display_name)}</div>
            <div className="pc-name">{p.display_name}{p.is_me && <span className="you-tag">you</span>}</div>
            <div className="pc-domain">{p.domain}</div>
          </button>
        ))}
      </div>
    </div>
  );
}

function ProfilePage({ profile, onBack }) {
  const [posts, setPosts] = useState(null);
  useEffect(() => { listProfilePosts(profile.tenant_id).then(setPosts).catch(() => setPosts([])); }, [profile.tenant_id]);

  return (
    <div className="profile-page">
      <button className="back" onClick={onBack}>← All people</button>
      <div className="profile-hero">
        <div className="avatar lg">{initials(profile.display_name)}</div>
        <div>
          <div className="ph-name">{profile.display_name}{profile.is_me && <span className="you-tag">you</span>}</div>
          <div className="ph-domain">writes about {profile.domain}</div>
        </div>
      </div>

      <div className="profile-cols">
        <div className="col-posts">
          <div className="col-label">Posts</div>
          {posts === null ? <div className="empty small">Loading…</div>
            : posts.length === 0 ? <div className="empty small">No posts yet.</div>
            : <ul className="posts">
                {posts.map((p) => (
                  <li key={p.post_id} className="post">
                    <span className="post-title">{p.title}</span>
                    <span className={`badge ${p.status}`}>{p.status}</span>
                  </li>
                ))}
              </ul>}
        </div>
        <div className="col-chat">
          <div className="col-label">Ask {profile.display_name}’s AI</div>
          <Chat profile={profile} />
        </div>
      </div>
    </div>
  );
}

function Chat({ profile }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef(null);
  useEffect(() => setMessages([]), [profile.tenant_id]);
  useEffect(() => { scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight); }, [messages]);

  async function send(e) {
    e.preventDefault();
    const q = input.trim();
    if (!q || busy) return;
    setInput(""); setBusy(true);
    setMessages((m) => [...m, { role: "user", text: q }, { role: "ai", text: "", citations: null, pending: true }]);
    const queue = []; let reading = true;
    const timer = setInterval(() => {
      if (queue.length) {
        const tok = queue.shift();
        setMessages((m) => { const c = [...m]; const l = c[c.length - 1]; c[c.length - 1] = { ...l, text: l.text + tok }; return c; });
      } else if (!reading) clearInterval(timer);
    }, 16);
    try {
      const citations = await ask(profile.tenant_id, q, (t) => queue.push(t));
      reading = false;
      setMessages((m) => { const c = [...m]; c[c.length - 1] = { ...c[c.length - 1], citations, pending: false }; return c; });
    } catch (err) {
      reading = false;
      setMessages((m) => { const c = [...m]; c[c.length - 1] = { ...c[c.length - 1], text: `⚠️ ${err.message}`, pending: false }; return c; });
    } finally { setBusy(false); }
  }

  return (
    <div className="chat">
      <div className="messages" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="hint">
            Ask about anything <b>{profile.display_name}</b> has written ({profile.domain}). The AI answers
            <b> only</b> from {profile.display_name}’s posts — ask about something else and it politely declines.
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="bubble">
              {m.text || (m.pending ? <span className="dots">•••</span> : "")}
              {m.citations && m.citations.length > 0 && (
                <div className="citations">
                  {m.citations.map((c, j) => <span key={j} className="cite" title={`score ${c.score}`}>{c.title}</span>)}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
      <form className="composer" onSubmit={send}>
        <input value={input} onChange={(e) => setInput(e.target.value)}
               placeholder={`Ask ${profile.display_name}’s AI…`} disabled={busy} />
        <button type="submit" disabled={busy || !input.trim()}>{busy ? "…" : "Send"}</button>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// My blog: write posts + see my own posts
// ---------------------------------------------------------------------------
function MyBlog() {
  const [posts, setPosts] = useState(null);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [status, setStatus] = useState(null);
  const reload = () => listMyPosts().then(setPosts).catch(() => setPosts([]));
  useEffect(() => { reload(); }, []);

  async function submit(e) {
    e.preventDefault();
    setStatus({ kind: "busy", msg: "Publishing…" });
    try {
      const r = await createPost(title, content);
      setStatus({ kind: "ok", msg: `Published (${r.post_id}). Indexing runs async — refresh in a few seconds.` });
      setTitle(""); setContent("");
      setTimeout(reload, 2000);
    } catch (err) { setStatus({ kind: "err", msg: err.message }); }
  }

  return (
    <div className="myblog">
      <form className="write" onSubmit={submit}>
        <div className="col-label">Write a post</div>
        <input placeholder="Post title" value={title} onChange={(e) => setTitle(e.target.value)} required />
        <textarea placeholder="Markdown content — use # headings; the chunker is markdown-aware."
                  value={content} onChange={(e) => setContent(e.target.value)} rows={8} required />
        <div className="write-actions">
          <button type="submit" disabled={status?.kind === "busy"}>Publish</button>
          {status && <span className={`status ${status.kind}`}>{status.msg}</span>}
        </div>
      </form>

      <div className="col-label" style={{ marginTop: 18 }}>My posts <button className="refresh" onClick={reload}>↻</button></div>
      {posts === null ? <div className="empty small">Loading…</div>
        : posts.length === 0 ? <div className="empty small">No posts yet — write your first above.</div>
        : <ul className="posts">
            {posts.map((p) => (
              <li key={p.post_id} className="post">
                <span className="post-title">{p.title}</span>
                <span className={`badge ${p.status}`}>{p.status}</span>
              </li>
            ))}
          </ul>}
    </div>
  );
}

function initials(name) {
  return (name || "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
}
