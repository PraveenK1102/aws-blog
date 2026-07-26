import { useEffect, useRef, useState } from "react";
import {
  BrowserRouter, Routes, Route, NavLink, Link, useParams, useNavigate, Navigate, useSearchParams,
} from "react-router-dom";
import {
  ask, createPost, listProfiles, getProfile, listProfilePosts, listMyPosts, getPost,
  listChats, listTrash, getChat, createChat, deleteChat, restoreChat, permanentDeleteChat,
  login, signup, me, getToken, setToken, clearToken,
} from "./api";

const INPUT = "bg-surface2 border border-line text-ink px-3.5 py-3 rounded-lg text-sm outline-none focus:border-accent";
const PRIMARY = "bg-accent text-onaccent font-bold rounded-lg cursor-pointer disabled:opacity-50";
const AVATAR = "rounded-full grid place-items-center font-bold text-onaccent bg-gradient-to-br from-accent to-accent2";
const YOU_TAG = "text-[10px] bg-accent/15 text-accent border border-accent/35 px-1.5 py-px rounded-full uppercase tracking-wide";

export default function App() {
  const [user, setUser] = useState(null);
  const [booting, setBooting] = useState(true);
  useEffect(() => {
    if (!getToken()) { setBooting(false); return; }
    me().then((d) => setUser(d.user)).catch(() => clearToken()).finally(() => setBooting(false));
  }, []);
  if (booting) return <div className="grid place-items-center h-screen text-muted">Loading…</div>;
  if (!user) return <Auth onAuthed={(r) => { setToken(r.token); setUser(r.user); }} />;
  return (
    <BrowserRouter>
      <Shell user={user} onLogout={() => { clearToken(); setUser(null); }}>
        <Routes>
          <Route path="/" element={<Discover />} />
          <Route path="/u/:userId" element={<ProfilePage />} />
          <Route path="/chats" element={<ChatsList />} />
          <Route path="/me" element={<MyBlog user={user} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Shell>
    </BrowserRouter>
  );
}

function Brand() {
  return <span className="font-bold flex items-center gap-2 text-ink"><span className="text-accent">◆</span> MultiTenantRAG</span>;
}
function Empty({ small, children }) {
  return <div className={small ? "text-[13px] text-muted p-4" : "grid place-items-center h-[40vh] text-muted"}>{children}</div>;
}
function Label({ children }) {
  return <div className="text-muted text-xs uppercase tracking-wide mb-2.5 flex items-center gap-2">{children}</div>;
}
function initials(name) { return (name || "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase(); }

// ---------------------------------------------------------------------------
function Auth({ onAuthed }) {
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState(""); const [password, setPassword] = useState("");
  const [name, setName] = useState(""); const [err, setErr] = useState(null); const [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault(); setErr(null); setBusy(true);
    try {
      const resp = mode === "login" ? await login(email.trim(), password) : await signup(email.trim(), password, name.trim() || undefined);
      onAuthed(resp);
    } catch (ex) { setErr(ex.message || "Something went wrong"); } finally { setBusy(false); }
  }
  const toggle = (m, label) => (
    <button type="button" onClick={() => setMode(m)} className={`flex-1 rounded-lg py-2.5 font-semibold ${mode === m ? "bg-accent text-onaccent" : "text-muted"}`}>{label}</button>
  );
  return (
    <div className="grid place-items-center min-h-screen p-6">
      <div className="w-full max-w-sm bg-surface border border-line rounded-2xl p-7 shadow-[0_20px_60px_rgba(0,0,0,0.35)]">
        <div className="text-xl"><Brand /></div>
        <p className="text-muted text-sm mt-2 mb-5">Browse people’s blogs and chat with an AI that answers only from what they wrote.</p>
        <div className="flex bg-surface2 rounded-xl p-1 mb-4">{toggle("login", "Log in")}{toggle("signup", "Sign up")}</div>
        <form onSubmit={submit} className="flex flex-col gap-2.5">
          {mode === "signup" && <input className={INPUT} placeholder="Display name (optional)" value={name} onChange={(e) => setName(e.target.value)} />}
          <input className={INPUT} type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          <input className={INPUT} type="password" placeholder="Password (min 8 chars)" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
          {err && <div className="text-err text-sm">{err}</div>}
          <button type="submit" disabled={busy} className={`${PRIMARY} mt-1.5 py-3`}>{busy ? "…" : mode === "login" ? "Log in" : "Create account"}</button>
        </form>
        <div className="text-muted text-xs mt-3.5 text-center">No email verification in this build — sign up freely to test.</div>
      </div>
    </div>
  );
}

function Shell({ user, onLogout, children }) {
  const tabCls = ({ isActive }) => `px-3.5 py-2.5 rounded-t-xl font-semibold cursor-pointer border ${isActive ? "text-ink bg-surface border-line border-b-surface" : "text-muted border-transparent"}`;
  return (
    <div className="w-full min-h-screen flex flex-col">
      <header className="flex items-center justify-between px-5 py-4 border-b border-line">
        <Link to="/"><Brand /></Link>
        <div className="flex items-center gap-3">
          <span className="text-muted text-[13px]">{user.email}</span>
          <button onClick={onLogout} className="bg-surface2 border border-line text-ink px-3 py-1.5 rounded-lg cursor-pointer text-[13px]">Log out</button>
        </div>
      </header>
      <div className="flex gap-1.5 px-5 pt-3">
        <NavLink to="/" end className={tabCls}>Discover</NavLink>
        <NavLink to="/chats" className={tabCls}>Chats</NavLink>
        <NavLink to="/me" className={tabCls}>My blog</NavLink>
      </div>
      <main className="flex-1 bg-surface border border-line rounded-b-xl rounded-tr-xl mx-5 mb-5 p-[18px] flex flex-col">{children}</main>
    </div>
  );
}

// ---------------------------------------------------------------------------
function Discover() {
  const [profiles, setProfiles] = useState(null);
  useEffect(() => { listProfiles().then(setProfiles).catch(() => setProfiles([])); }, []);
  if (profiles === null) return <Empty small>Loading people…</Empty>;
  if (profiles.length === 0) return <Empty small>No profiles yet.</Empty>;
  return (
    <div>
      <div className="text-muted text-sm mb-3.5">Pick someone and ask their AI</div>
      <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(160px,1fr))]">
        {profiles.map((p) => (
          <Link key={p.tenant_id} to={`/u/${p.user_id}`} className="flex flex-col items-center gap-2 text-center bg-surface2 border border-line rounded-2xl px-3 py-5 cursor-pointer text-ink transition hover:border-accent hover:-translate-y-0.5">
            <div className={`${AVATAR} w-[46px] h-[46px] text-[15px]`}>{initials(p.display_name)}</div>
            <div className="font-semibold text-sm flex items-center gap-1.5">{p.display_name}{p.is_me && <span className={YOU_TAG}>you</span>}</div>
            <div className="text-muted text-xs">{p.domain}</div>
          </Link>
        ))}
      </div>
    </div>
  );
}

function ProfilePage() {
  const { userId } = useParams();
  const navigate = useNavigate();
  const [sp] = useSearchParams();
  const initialChatId = sp.get("chat");
  const [profile, setProfile] = useState(null);
  const [posts, setPosts] = useState(null);
  const [reading, setReading] = useState(null);
  useEffect(() => { setProfile(null); getProfile(userId).then(setProfile).catch(() => setProfile(false)); }, [userId]);
  useEffect(() => { if (profile) { setPosts(null); listProfilePosts(profile.tenant_id).then(setPosts).catch(() => setPosts([])); } }, [profile]);
  if (profile === null) return <Empty small>Loading…</Empty>;
  if (!profile) return <Empty small>Profile not found. <Link to="/" className="text-accent">Back</Link></Empty>;
  return (
    <div className="flex flex-col">
      <button onClick={() => navigate("/")} className="self-start bg-transparent border-0 text-accent cursor-pointer text-sm pb-3">← All people</button>
      <div className="flex items-center gap-3.5 pb-4 border-b border-line mb-4">
        <div className={`${AVATAR} w-[60px] h-[60px] text-xl`}>{initials(profile.display_name)}</div>
        <div>
          <div className="text-lg font-bold flex items-center gap-2">{profile.display_name}{profile.is_me && <span className={YOU_TAG}>you</span>}</div>
          <div className="text-muted text-[13px]">{posts ? `${posts.length} post${posts.length === 1 ? "" : "s"}` : ""} · mainly {profile.domain}</div>
        </div>
      </div>
      <div className="grid gap-[18px] [grid-template-columns:240px_1fr] max-[680px]:grid-cols-1">
        <div>
          <Label>Posts</Label>
          {posts === null ? <Empty small>Loading…</Empty> : posts.length === 0 ? <Empty small>No posts yet.</Empty>
            : <PostList posts={posts} onOpen={(p) => setReading(p.post_id)} />}
        </div>
        <div>
          <Label>Ask {profile.display_name}’s AI</Label>
          <Chat profile={profile} initialChatId={initialChatId} />
        </div>
      </div>
      {reading && <PostReader tenantId={profile.tenant_id} postId={reading} onClose={() => setReading(null)} />}
    </div>
  );
}

function PostList({ posts, onOpen }) {
  return (
    <ul className="list-none p-0 m-0 flex flex-col gap-2">
      {posts.map((p) => (
        <li key={p.post_id} onClick={() => onOpen(p)} className="flex items-center justify-between gap-2 bg-surface2 border border-line rounded-lg px-3.5 py-3 cursor-pointer transition hover:border-accent">
          <span className="text-sm">{p.title}</span><Badge status={p.status} />
        </li>
      ))}
    </ul>
  );
}
function Badge({ status }) {
  const tone = status === "indexed" ? "text-ok bg-ok/10 border-ok/30" : "text-warn bg-warn/10 border-warn/30";
  return <span className={`shrink-0 text-[11px] px-2.5 py-[3px] rounded-full uppercase tracking-wide font-bold border ${tone}`}>{status}</span>;
}
function PostReader({ tenantId, postId, onClose }) {
  const [post, setPost] = useState(null); const [err, setErr] = useState(null);
  useEffect(() => { getPost(tenantId, postId).then(setPost).catch((e) => setErr(e.message)); }, [tenantId, postId]);
  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm grid place-items-center p-6 z-50" onClick={onClose}>
      <div className="relative w-full max-w-[620px] max-h-[82vh] overflow-y-auto bg-surface border border-line rounded-2xl px-8 py-8 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <button onClick={onClose} className="absolute top-3.5 right-3.5 bg-surface2 border border-line text-muted w-[30px] h-[30px] rounded-lg cursor-pointer">✕</button>
        {err ? <Empty small>{err}</Empty> : post === null ? <Empty small>Loading…</Empty> : <article className="markdown leading-relaxed text-[15px] text-ink">{renderMarkdown(post.content)}</article>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chat with session memory
// ---------------------------------------------------------------------------
function Chat({ profile, initialChatId }) {
  const [chatId, setChatId] = useState(null);
  const [myChats, setMyChats] = useState([]);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const scrollRef = useRef(null);

  const refreshChats = () => listChats().then((cs) => setMyChats(cs.filter((c) => c.tenant_id === profile.tenant_id))).catch(() => {});

  useEffect(() => {
    setErr(null); refreshChats();
    if (initialChatId) loadChat(initialChatId);
    else { setChatId(null); setMessages([]); }
    // eslint-disable-next-line
  }, [profile.tenant_id, initialChatId]);
  useEffect(() => { scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight); }, [messages]);

  async function loadChat(id) {
    try {
      const c = await getChat(id);
      setChatId(id);
      setMessages((c.messages || []).map((m) => ({ role: m.role, text: m.text, citations: m.citations || null })));
    } catch { setErr("Could not load that chat."); }
  }
  function newChat() { setChatId(null); setMessages([]); setErr(null); }
  async function removeChat(id, e) { e.stopPropagation(); await deleteChat(id).catch(() => {}); if (id === chatId) newChat(); refreshChats(); }

  async function send(e) {
    e.preventDefault();
    const q = input.trim();
    if (!q || busy) return;
    setErr(null);
    let cid = chatId;
    if (!cid) {
      try { const c = await createChat(profile.tenant_id, profile.user_id); cid = c.chat_id; setChatId(cid); }
      catch (ex) { setErr(ex.message || "Could not start a chat"); return; }
    }
    setInput(""); setBusy(true);
    setMessages((m) => [...m, { role: "user", text: q }, { role: "ai", text: "", citations: null, pending: true }]);
    const queue = []; let reading = true;
    const timer = setInterval(() => {
      if (queue.length) { const t = queue.shift(); setMessages((m) => { const c = [...m]; const l = c[c.length - 1]; c[c.length - 1] = { ...l, text: l.text + t }; return c; }); }
      else if (!reading) clearInterval(timer);
    }, 16);
    try {
      const citations = await ask(profile.tenant_id, q, (t) => queue.push(t), cid);
      reading = false;
      setMessages((m) => { const c = [...m]; c[c.length - 1] = { ...c[c.length - 1], citations, pending: false }; return c; });
      refreshChats();
    } catch (ex) {
      reading = false;
      setMessages((m) => { const c = [...m]; c[c.length - 1] = { ...c[c.length - 1], text: `⚠️ ${ex.message}`, pending: false }; return c; });
    } finally { setBusy(false); }
  }

  return (
    <div className="flex flex-col h-[56vh]">
      <div className="flex flex-wrap gap-1.5 mb-2 items-center">
        {myChats.map((c) => (
          <span key={c.chat_id} className={`flex items-center rounded-full border text-[12px] ${c.chat_id === chatId ? "border-accent bg-accent/10 text-accent" : "border-line bg-surface2 text-muted"}`}>
            <button onClick={() => loadChat(c.chat_id)} className="pl-3 pr-1.5 py-1 max-w-[150px] truncate">{c.title || "New chat"}</button>
            <button onClick={(e) => removeChat(c.chat_id, e)} title="delete" className="pr-2.5 pl-0.5 py-1 opacity-60 hover:opacity-100">×</button>
          </span>
        ))}
        <button onClick={newChat} className="rounded-full border border-line bg-surface2 text-muted text-[12px] px-3 py-1 hover:border-accent hover:text-accent">＋ New chat ({myChats.length}/5)</button>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto flex flex-col gap-3 p-1.5">
        {messages.length === 0 && (
          <div className="text-muted text-sm leading-relaxed bg-surface2 border border-line rounded-xl p-4">
            Ask about anything <b>{profile.display_name}</b> has written about. The AI answers <b>only</b> from {profile.display_name}’s posts, remembers this conversation, and asks to clarify if your question is vague.
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : ""}`}>
            <div className={`max-w-[min(80%,640px)] px-3.5 py-3 rounded-2xl leading-relaxed whitespace-pre-wrap text-[14.5px] ${m.role === "user" ? "bg-gradient-to-b from-accent to-[#4f8ff0] text-onaccent rounded-br-sm" : "bg-surface2 border border-line rounded-bl-sm"}`}>
              {m.text || (m.pending ? <span className="text-muted tracking-widest">•••</span> : "")}
              {m.citations && m.citations.length > 0 && (
                <div className="mt-2.5 flex flex-wrap gap-1.5">
                  {m.citations.map((c, j) => <span key={j} title={`score ${c.score}`} className="text-[11.5px] text-accent bg-accent/10 border border-accent/30 px-2 py-[3px] rounded-full">{c.title}</span>)}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
      {err && <div className="text-err text-[13px] px-1.5 pb-1">{err}</div>}
      <form onSubmit={send} className="flex gap-2 mt-1">
        <input className={`${INPUT} flex-1`} value={input} onChange={(e) => setInput(e.target.value)} placeholder={`Ask ${profile.display_name}’s AI…`} disabled={busy} />
        <button type="submit" disabled={busy || !input.trim()} className={`${PRIMARY} px-5`}>{busy ? "…" : "Send"}</button>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chats tab — all saved chats + trash
// ---------------------------------------------------------------------------
function ChatsList() {
  const [active, setActive] = useState(null);
  const [trash, setTrash] = useState([]);
  const [showTrash, setShowTrash] = useState(false);
  const navigate = useNavigate();
  const refresh = () => { listChats().then(setActive).catch(() => setActive([])); listTrash().then(setTrash).catch(() => {}); };
  useEffect(refresh, []);

  const when = (t) => new Date(t * 1000).toLocaleDateString();

  if (active === null) return <Empty small>Loading chats…</Empty>;
  return (
    <div className="flex flex-col gap-4">
      <div>
        <Label>Your chats ({active.length}) · up to 5 per person</Label>
        {active.length === 0 ? <Empty small>No chats yet. Open someone’s profile and start one.</Empty> : (
          <ul className="list-none p-0 m-0 flex flex-col gap-2">
            {active.map((c) => (
              <li key={c.chat_id} className="flex items-center justify-between gap-2 bg-surface2 border border-line rounded-lg px-3.5 py-3">
                <button onClick={() => navigate(`/u/${c.profile_user_id}?chat=${c.chat_id}`)} className="flex-1 text-left min-w-0">
                  <div className="text-sm truncate">{c.title || "New chat"}</div>
                  <div className="text-muted text-xs">with {c.profile_name} · {c.message_count} messages · {when(c.updated_at)}</div>
                </button>
                <button onClick={() => navigate(`/u/${c.profile_user_id}?chat=${c.chat_id}`)} className="text-accent text-[13px] px-2">Open</button>
                <button onClick={() => deleteChat(c.chat_id).then(refresh)} className="text-muted text-[13px] px-2 hover:text-err">Delete</button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <button onClick={() => setShowTrash((s) => !s)} className="text-muted text-[13px]">{showTrash ? "▾" : "▸"} Trash ({trash.length})</button>
        {showTrash && (
          trash.length === 0 ? <div className="text-muted text-[13px] p-2">Trash is empty.</div> : (
            <ul className="list-none p-0 m-0 flex flex-col gap-2 mt-2">
              {trash.map((c) => (
                <li key={c.chat_id} className="flex items-center justify-between gap-2 bg-surface2 border border-line rounded-lg px-3.5 py-3 opacity-80">
                  <div className="flex-1 min-w-0">
                    <div className="text-sm truncate">{c.title || "New chat"}</div>
                    <div className="text-muted text-xs">with {c.profile_name} · {c.message_count} messages</div>
                  </div>
                  <button onClick={() => restoreChat(c.chat_id).then(refresh).catch((e) => alert(e.message))} className="text-accent text-[13px] px-2">Restore</button>
                  <button onClick={() => { if (confirm("Permanently delete this chat? This cannot be undone.")) permanentDeleteChat(c.chat_id).then(refresh); }} className="text-err text-[13px] px-2">Delete forever</button>
                </li>
              ))}
            </ul>
          )
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
function MyBlog({ user }) {
  const [posts, setPosts] = useState(null);
  const [title, setTitle] = useState(""); const [content, setContent] = useState("");
  const [status, setStatus] = useState(null); const [reading, setReading] = useState(null);
  const reload = () => listMyPosts().then(setPosts).catch(() => setPosts([]));
  useEffect(() => { reload(); }, []);
  async function submit(e) {
    e.preventDefault(); setStatus({ kind: "busy", msg: "Publishing…" });
    try {
      const r = await createPost(title, content);
      setStatus({ kind: "ok", msg: `Published (${r.post_id}). Indexing runs async — refresh in a few seconds.` });
      setTitle(""); setContent(""); setTimeout(reload, 2000);
    } catch (err) { setStatus({ kind: "err", msg: err.message }); }
  }
  const statusTone = { ok: "text-ok", err: "text-err", busy: "text-muted" };
  return (
    <div className="flex flex-col">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <Label>Write a post</Label>
        <input className={INPUT} placeholder="Post title" value={title} onChange={(e) => setTitle(e.target.value)} required />
        <textarea className={`${INPUT} resize-y leading-relaxed`} placeholder="Markdown content — use # headings; the chunker is markdown-aware." value={content} onChange={(e) => setContent(e.target.value)} rows={8} required />
        <div className="flex items-center gap-3">
          <button type="submit" disabled={status?.kind === "busy"} className={`${PRIMARY} px-5 py-2.5`}>Publish</button>
          {status && <span className={`text-[13px] ${statusTone[status.kind]}`}>{status.msg}</span>}
        </div>
      </form>
      <div className="mt-[18px]">
        <Label>My posts <button onClick={reload} className="bg-surface2 border border-line text-muted px-3 py-1.5 rounded-lg cursor-pointer text-[13px]">↻</button></Label>
        {posts === null ? <Empty small>Loading…</Empty> : posts.length === 0 ? <Empty small>No posts yet — write your first above.</Empty>
          : <PostList posts={posts} onOpen={(p) => setReading(p.post_id)} />}
      </div>
      {reading && <PostReader tenantId={user.tenant_id} postId={reading} onClose={() => setReading(null)} />}
    </div>
  );
}

function renderMarkdown(md) {
  const lines = (md || "").split("\n");
  const out = []; let para = [], list = [];
  const flushPara = () => { if (para.length) { out.push(<p key={out.length}>{para.join(" ")}</p>); para = []; } };
  const flushList = () => { if (list.length) { out.push(<ul key={out.length}>{list.map((li, i) => <li key={i}>{li}</li>)}</ul>); list = []; } };
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^#\s+/.test(line)) { flushPara(); flushList(); out.push(<h1 key={out.length}>{line.replace(/^#\s+/, "")}</h1>); }
    else if (/^##\s+/.test(line)) { flushPara(); flushList(); out.push(<h2 key={out.length}>{line.replace(/^##\s+/, "")}</h2>); }
    else if (/^###\s+/.test(line)) { flushPara(); flushList(); out.push(<h3 key={out.length}>{line.replace(/^###\s+/, "")}</h3>); }
    else if (/^[-*]\s+/.test(line)) { flushPara(); list.push(line.replace(/^[-*]\s+/, "")); }
    else if (line.trim() === "") { flushPara(); flushList(); }
    else { flushList(); para.push(line); }
  }
  flushPara(); flushList();
  return out;
}
