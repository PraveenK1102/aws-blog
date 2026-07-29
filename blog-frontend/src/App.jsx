import { useEffect, useRef, useState } from "react";
import {
  BrowserRouter, Routes, Route, NavLink, Link, useParams, useNavigate, Navigate, useSearchParams,
} from "react-router-dom";
import {
  ask, createPost, listProfiles, getProfile, listProfilePosts, listMyPosts, getPost,
  listChats, listTrash, getChat, createChat, deleteChat, restoreChat, permanentDeleteChat,
  login, signup, me, getToken, setToken, clearToken,
  followUser, unfollowUser, listFollowing,
  createGroup, listGroups, getGroup, addGroupMember, removeGroupMember,
  globalSearch, askGroup,
} from "./api";

const INPUT = "w-full bg-white border border-line rounded-lg px-3.5 py-2.5 text-[15px] outline-none focus:border-accent";
const DARK = "bg-ink text-white font-medium rounded-full hover:bg-black disabled:opacity-40 transition";
const AVATAR = "rounded-full grid place-items-center font-semibold text-white bg-gradient-to-br from-accent to-accent2 shrink-0";

export default function App() {
  const [user, setUser] = useState(null);
  const [booting, setBooting] = useState(true);
  useEffect(() => {
    if (!getToken()) { setBooting(false); return; }
    me().then((d) => setUser(d.user)).catch(() => clearToken()).finally(() => setBooting(false));
  }, []);
  if (booting) return <div className="grid place-items-center h-screen text-faint">Loading…</div>;
  if (!user) return <Auth onAuthed={(r) => { setToken(r.token); setUser(r.user); }} />;
  return (
    <BrowserRouter>
      <Shell user={user} onLogout={() => { clearToken(); setUser(null); }}>
        <Routes>
          <Route path="/" element={<Discover />} />
          <Route path="/search" element={<GlobalSearchPage />} />
          <Route path="/following" element={<Following />} />
          <Route path="/groups" element={<GroupsList />} />
          <Route path="/groups/:groupId" element={<GroupDetail />} />
          <Route path="/u/:userId" element={<ProfilePage />} />
          <Route path="/chats" element={<ChatsList />} />
          <Route path="/me" element={<MyBlog user={user} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Shell>
    </BrowserRouter>
  );
}

const fmtDate = (ts) => ts ? new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "";
const initials = (n) => (n || "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();

// ---------------------------------------------------------------------------
function Auth({ onAuthed }) {
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState(""); const [password, setPassword] = useState("");
  const [name, setName] = useState(""); const [err, setErr] = useState(null); const [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault(); setErr(null); setBusy(true);
    try {
      const r = mode === "login" ? await login(email.trim(), password) : await signup(email.trim(), password, name.trim() || undefined);
      onAuthed(r);
    } catch (ex) { setErr(ex.message || "Something went wrong"); } finally { setBusy(false); }
  }
  return (
    <div className="min-h-screen grid place-items-center px-6 bg-cream">
      <div className="w-full max-w-[380px]">
        <div className="text-center mb-8">
          <div className="text-2xl font-bold tracking-tight">Inkwell</div>
          <p className="text-soft mt-2">Read people’s writing. Ask their AI anything they’ve written about.</p>
        </div>
        <div className="bg-white border border-line rounded-2xl p-7 shadow-sm">
          <div className="flex gap-6 mb-5 text-[15px] font-medium border-b border-line">
            {["login", "signup"].map((m) => (
              <button key={m} onClick={() => setMode(m)}
                className={`pb-3 -mb-px border-b-2 ${mode === m ? "border-ink text-ink" : "border-transparent text-faint"}`}>
                {m === "login" ? "Sign in" : "Create account"}
              </button>
            ))}
          </div>
          <form onSubmit={submit} className="flex flex-col gap-3">
            {mode === "signup" && <input className={INPUT} placeholder="Display name" value={name} onChange={(e) => setName(e.target.value)} />}
            <input className={INPUT} type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            <input className={INPUT} type="password" placeholder="Password (min 8 chars)" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
            {err && <div className="text-err text-sm">{err}</div>}
            <button type="submit" disabled={busy} className={`${DARK} py-2.5 mt-1`}>{busy ? "…" : mode === "login" ? "Sign in" : "Create account"}</button>
          </form>
          <p className="text-faint text-xs text-center mt-4">No email verification — sign up freely to explore.</p>
        </div>
      </div>
    </div>
  );
}

function Shell({ user, onLogout, children }) {
  const link = ({ isActive }) => `text-[15px] ${isActive ? "text-ink font-medium" : "text-soft hover:text-ink"}`;
  return (
    <div className="min-h-screen flex flex-col">
      <header className="sticky top-0 z-30 bg-white/90 backdrop-blur border-b border-line">
        <div className="max-w-feed mx-auto px-5 h-14 flex items-center justify-between">
          <Link to="/" className="text-lg font-bold tracking-tight">Inkwell</Link>
          <nav className="flex items-center gap-5">
            <NavLink to="/" end className={link}>Discover</NavLink>
            <NavLink to="/search" className={link}>Search</NavLink>
            <NavLink to="/following" className={link}>Following</NavLink>
            <NavLink to="/groups" className={link}>Groups</NavLink>
            <NavLink to="/chats" className={link}>Chats</NavLink>
            <NavLink to="/me" className={link}>Write</NavLink>
            <span className="w-px h-5 bg-line" />
            <span className="text-faint text-sm hidden sm:block">{user.email}</span>
            <button onClick={onLogout} className="text-soft hover:text-ink text-sm">Sign out</button>
          </nav>
        </div>
      </header>
      <main className="flex-1 max-w-feed w-full mx-auto px-5 py-10">{children}</main>
    </div>
  );
}

// ---------------------------------------------------------------------------
function Discover() {
  const [profiles, setProfiles] = useState(null);
  useEffect(() => { listProfiles().then(setProfiles).catch(() => setProfiles([])); }, []);
  if (profiles === null) return <p className="text-faint">Loading writers…</p>;
  return (
    <div>
      <h1 className="text-3xl font-bold tracking-tight mb-1">Discover writers</h1>
      <p className="text-soft mb-8">Open a profile to read their posts — or ask their AI, which answers only from what they wrote.</p>
      <div className="grid gap-4 sm:grid-cols-2">
        {profiles.map((p) => (
          <Link key={p.tenant_id} to={`/u/${p.user_id}`}
            className="group flex items-center gap-4 bg-white border border-line rounded-2xl p-5 hover:shadow-md hover:-translate-y-0.5 transition">
            <div className={`${AVATAR} w-12 h-12 text-lg`}>{initials(p.display_name)}</div>
            <div className="min-w-0">
              <div className="font-semibold flex items-center gap-2 truncate">
                {p.display_name}
                {p.is_me && <span className="text-[10px] uppercase tracking-wide text-accent bg-accent/10 px-1.5 py-px rounded-full">you</span>}
              </div>
              <div className="text-soft text-sm">writes about {p.domain}</div>
            </div>
            <span className="ml-auto text-faint group-hover:text-accent transition">→</span>
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
  const openChatId = sp.get("chat");
  const [profile, setProfile] = useState(null);
  const [posts, setPosts] = useState(null);
  const [reading, setReading] = useState(null);
  const [chatOpen, setChatOpen] = useState(!!openChatId);

  useEffect(() => { setProfile(null); getProfile(userId).then(setProfile).catch(() => setProfile(false)); }, [userId]);
  useEffect(() => { if (profile) { setPosts(null); listProfilePosts(profile.tenant_id).then(setPosts).catch(() => setPosts([])); } }, [profile]);
  useEffect(() => { setChatOpen(!!openChatId); }, [openChatId]);

  if (profile === null) return <p className="text-faint">Loading…</p>;
  if (!profile) return <p className="text-faint">Profile not found. <Link to="/" className="text-accent">Back</Link></p>;

  return (
    <div className="max-w-article mx-auto">
      <button onClick={() => navigate("/")} className="text-soft hover:text-ink text-sm mb-8">← Discover</button>
      <div className="flex items-center gap-4 pb-8 border-b border-line">
        <div className={`${AVATAR} w-16 h-16 text-2xl`}>{initials(profile.display_name)}</div>
        <div>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            {profile.display_name}
            {profile.is_me && <span className="text-[10px] uppercase tracking-wide text-accent bg-accent/10 px-1.5 py-px rounded-full">you</span>}
          </h1>
          <p className="text-soft">{posts ? `${posts.length} post${posts.length === 1 ? "" : "s"}` : ""} · mainly {profile.domain}{typeof profile.follower_count === "number" ? ` · ${profile.follower_count} follower${profile.follower_count === 1 ? "" : "s"}` : ""}</p>
        </div>
        {!profile.is_me && <FollowButton userId={profile.user_id} initialFollowing={profile.is_following} />}
      </div>

      {posts === null ? <p className="text-faint py-10">Loading posts…</p>
        : posts.length === 0 ? <p className="text-faint py-10">No posts yet.</p>
        : <div className="divide-y divide-line">
            {posts.map((p) => (
              <button key={p.post_id} onClick={() => setReading(p.post_id)} className="group w-full text-left py-6 flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-xl font-bold tracking-tight group-hover:text-accent transition">{p.title}</h2>
                  <div className="text-faint text-sm mt-1">{fmtDate(p.created_at)} · {p.status === "indexed" ? "AI-searchable" : "indexing…"}</div>
                </div>
                <span className="text-faint group-hover:text-accent mt-1.5">→</span>
              </button>
            ))}
          </div>}

      {reading && <Reader tenantId={profile.tenant_id} postId={reading} onClose={() => setReading(null)} />}
      <AskWidget profile={profile} open={chatOpen} setOpen={setChatOpen} initialChatId={openChatId} />
    </div>
  );
}

function Reader({ tenantId, postId, onClose }) {
  const [post, setPost] = useState(null); const [err, setErr] = useState(null);
  useEffect(() => { getPost(tenantId, postId).then(setPost).catch((e) => setErr(e.message)); }, [tenantId, postId]);
  return (
    <div className="fixed inset-0 z-40 bg-black/30 overflow-y-auto" onClick={onClose}>
      <div className="min-h-full flex justify-center py-10 px-5">
        <div className="relative w-full max-w-article bg-white rounded-2xl shadow-xl p-8 sm:p-12 h-fit animate-popup" onClick={(e) => e.stopPropagation()}>
          <button onClick={onClose} className="absolute top-4 right-4 w-9 h-9 rounded-full grid place-items-center text-soft hover:bg-cream">✕</button>
          {err ? <p className="text-faint">{err}</p>
            : post === null ? <p className="text-faint">Loading…</p>
            : <article className="prose">
                <h1>{post.title}</h1>
                <div className="text-faint text-sm mb-8" style={{ fontFamily: "-apple-system, sans-serif" }}>{fmtDate(post.created_at)}</div>
                {renderMarkdown(post.content)}
              </article>}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Floating Ask-AI widget (bottom-right of every profile)
// ---------------------------------------------------------------------------
function AskWidget({ profile, open, setOpen, initialChatId }) {
  const first = (profile.display_name || "").split(" ")[0];
  return (
    <>
      {!open && (
        <button onClick={() => setOpen(true)}
          className="fixed bottom-6 right-6 z-40 flex items-center gap-2 pl-4 pr-5 py-3 rounded-full text-white font-medium shadow-lg bg-gradient-to-br from-accent to-accent2 hover:shadow-xl hover:-translate-y-0.5 transition">
          <span className="text-lg leading-none">✨</span> Ask {first}’s AI
        </button>
      )}
      {open && (
        <div className="fixed bottom-6 right-6 z-40 w-[min(400px,calc(100vw-2rem))] h-[min(560px,calc(100vh-3rem))] bg-white border border-line rounded-2xl shadow-2xl flex flex-col animate-popup overflow-hidden">
          <ChatPanel profile={profile} initialChatId={initialChatId} onClose={() => setOpen(false)} />
        </div>
      )}
    </>
  );
}

function ChatPanel({ profile, initialChatId, onClose }) {
  const [chatId, setChatId] = useState(null);
  const [myChats, setMyChats] = useState([]);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [menu, setMenu] = useState(false);
  const scrollRef = useRef(null);
  const first = (profile.display_name || "").split(" ")[0];

  const refresh = () => listChats().then((cs) => setMyChats(cs.filter((c) => c.tenant_id === profile.tenant_id))).catch(() => {});
  useEffect(() => { refresh(); if (initialChatId) load(initialChatId); /* eslint-disable-next-line */ }, [profile.tenant_id]);
  useEffect(() => { scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight); }, [messages]);

  async function load(id) {
    try { const c = await getChat(id); setChatId(id); setMenu(false);
      setMessages((c.messages || []).map((m) => ({ role: m.role, text: m.text, citations: m.citations || null }))); }
    catch { setErr("Could not load that chat."); }
  }
  function fresh() { setChatId(null); setMessages([]); setErr(null); setMenu(false); }
  async function remove(id, e) { e.stopPropagation(); await deleteChat(id).catch(() => {}); if (id === chatId) fresh(); refresh(); }

  async function send(e) {
    e.preventDefault();
    const q = input.trim(); if (!q || busy) return;
    setErr(null);
    let cid = chatId;
    if (!cid) { try { const c = await createChat(profile.tenant_id, profile.user_id); cid = c.chat_id; setChatId(cid); } catch (ex) { setErr(ex.message); return; } }
    setInput(""); setBusy(true);
    setMessages((m) => [...m, { role: "user", text: q }, { role: "ai", text: "", citations: null, pending: true }]);
    const queue = []; let reading = true;
    const timer = setInterval(() => {
      if (queue.length) { const t = queue.shift(); setMessages((m) => { const c = [...m]; const l = c[c.length - 1]; c[c.length - 1] = { ...l, text: l.text + t }; return c; }); }
      else if (!reading) clearInterval(timer);
    }, 14);
    try {
      const cites = await ask(profile.tenant_id, q, (t) => queue.push(t), cid);
      reading = false;
      setMessages((m) => { const c = [...m]; c[c.length - 1] = { ...c[c.length - 1], citations: cites, pending: false }; return c; });
      refresh();
    } catch (ex) {
      reading = false;
      setMessages((m) => { const c = [...m]; c[c.length - 1] = { ...c[c.length - 1], text: `⚠️ ${ex.message}`, pending: false }; return c; });
    } finally { setBusy(false); }
  }

  return (
    <>
      <div className="flex items-center gap-2 px-4 py-3 border-b border-line">
        <div className={`${AVATAR} w-8 h-8 text-xs`}>{initials(profile.display_name)}</div>
        <div className="min-w-0 flex-1">
          <div className="font-semibold text-sm leading-tight truncate">Ask {profile.display_name}’s AI</div>
          <button onClick={() => setMenu((v) => !v)} className="text-faint text-xs hover:text-accent">
            {myChats.length ? `${myChats.length}/5 chats · switch ▾` : "new conversation"}
          </button>
        </div>
        <button onClick={fresh} title="New chat" className="w-8 h-8 rounded-full grid place-items-center text-soft hover:bg-cream text-lg leading-none">＋</button>
        <button onClick={onClose} title="Close" className="w-8 h-8 rounded-full grid place-items-center text-soft hover:bg-cream">✕</button>
      </div>

      {menu && (
        <div className="border-b border-line max-h-40 overflow-y-auto bg-cream">
          {myChats.length === 0 ? <div className="px-4 py-3 text-faint text-sm">No saved chats yet.</div>
            : myChats.map((c) => (
              <div key={c.chat_id} className={`flex items-center px-4 py-2 text-sm hover:bg-white ${c.chat_id === chatId ? "text-accent" : "text-ink"}`}>
                <button onClick={() => load(c.chat_id)} className="flex-1 text-left truncate">{c.title || "New chat"}</button>
                <button onClick={(e) => remove(c.chat_id, e)} className="text-faint hover:text-err px-1">🗑</button>
              </div>
            ))}
        </div>
      )}

      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
        {messages.length === 0 && (
          <div className="text-soft text-sm leading-relaxed bg-cream rounded-xl p-4">
            Ask anything <b>{first}</b> has written about. Answers come <b>only</b> from their posts — remembers the conversation, and asks to clarify if a question is vague.
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : ""}`}>
            <div className={`max-w-[85%] px-3.5 py-2.5 rounded-2xl leading-relaxed whitespace-pre-wrap text-[14px] ${m.role === "user" ? "bg-ink text-white rounded-br-sm" : "bg-cream text-ink rounded-bl-sm"}`}>
              {m.text || (m.pending ? <span className="text-faint tracking-widest">•••</span> : "")}
              {m.citations && m.citations.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {m.citations.map((c, j) => <span key={j} title={`score ${c.score}`} className="text-[11px] text-accent bg-accent/10 px-2 py-px rounded-full">{c.title}</span>)}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
      {err && <div className="text-err text-xs px-4 pb-1">{err}</div>}
      <form onSubmit={send} className="p-3 border-t border-line flex gap-2">
        <input className={`${INPUT} rounded-full py-2`} value={input} onChange={(e) => setInput(e.target.value)} placeholder={`Ask ${first}’s AI…`} disabled={busy} />
        <button type="submit" disabled={busy || !input.trim()} className={`${DARK} px-4 shrink-0`}>{busy ? "…" : "↑"}</button>
      </form>
    </>
  );
}

// ---------------------------------------------------------------------------
function ChatsList() {
  const [active, setActive] = useState(null); const [trash, setTrash] = useState([]); const [showTrash, setShowTrash] = useState(false);
  const navigate = useNavigate();
  const refresh = () => { listChats().then(setActive).catch(() => setActive([])); listTrash().then(setTrash).catch(() => {}); };
  useEffect(refresh, []);
  if (active === null) return <p className="text-faint">Loading chats…</p>;
  return (
    <div className="max-w-article mx-auto">
      <h1 className="text-3xl font-bold tracking-tight mb-1">Your chats</h1>
      <p className="text-soft mb-8">{active.length} saved · up to 5 per writer.</p>
      {active.length === 0 ? <p className="text-faint">No chats yet. Open a writer and tap “Ask their AI”.</p>
        : <div className="divide-y divide-line border-y border-line">
            {active.map((c) => (
              <div key={c.chat_id} className="flex items-center gap-3 py-4">
                <button onClick={() => navigate(`/u/${c.profile_user_id}?chat=${c.chat_id}`)} className="flex-1 min-w-0 text-left">
                  <div className="font-medium truncate">{c.title || "New chat"}</div>
                  <div className="text-faint text-sm">with {c.profile_name} · {c.message_count} messages · {fmtDate(c.updated_at)}</div>
                </button>
                <button onClick={() => navigate(`/u/${c.profile_user_id}?chat=${c.chat_id}`)} className="text-accent text-sm">Open</button>
                <button onClick={() => deleteChat(c.chat_id).then(refresh)} className="text-faint hover:text-err text-sm">Delete</button>
              </div>
            ))}
          </div>}
      <div className="mt-6">
        <button onClick={() => setShowTrash((s) => !s)} className="text-faint text-sm hover:text-ink">{showTrash ? "▾" : "▸"} Trash ({trash.length})</button>
        {showTrash && (trash.length === 0 ? <p className="text-faint text-sm mt-2">Trash is empty.</p>
          : <div className="divide-y divide-line mt-2">
              {trash.map((c) => (
                <div key={c.chat_id} className="flex items-center gap-3 py-3 text-sm">
                  <div className="flex-1 min-w-0"><div className="truncate">{c.title || "New chat"}</div><div className="text-faint">with {c.profile_name}</div></div>
                  <button onClick={() => restoreChat(c.chat_id).then(refresh).catch((e) => alert(e.message))} className="text-accent">Restore</button>
                  <button onClick={() => { if (confirm("Permanently delete this chat?")) permanentDeleteChat(c.chat_id).then(refresh); }} className="text-err">Delete forever</button>
                </div>
              ))}
            </div>)}
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
    try { const r = await createPost(title, content); setStatus({ kind: "ok", msg: `Published. Indexing runs in the background — refresh in a few seconds.` }); setTitle(""); setContent(""); setTimeout(reload, 2000); }
    catch (err) { setStatus({ kind: "err", msg: err.message }); }
  }
  const tone = { ok: "text-ok", err: "text-err", busy: "text-faint" };
  return (
    <div className="max-w-article mx-auto">
      <h1 className="text-3xl font-bold tracking-tight mb-6">Write a post</h1>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <input className="w-full text-2xl font-bold tracking-tight outline-none placeholder:text-faint/60 py-1" placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} required />
        <textarea className="w-full outline-none resize-y text-[1.05rem] leading-8 placeholder:text-faint/60 min-h-[240px]" placeholder="Tell your story… (Markdown — use # headings)" value={content} onChange={(e) => setContent(e.target.value)} required />
        <div className="flex items-center gap-3 border-t border-line pt-4">
          <button type="submit" disabled={status?.kind === "busy"} className={`${DARK} px-5 py-2`}>Publish</button>
          {status && <span className={`text-sm ${tone[status.kind]}`}>{status.msg}</span>}
        </div>
      </form>
      <div className="mt-10">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-bold">Your posts</h2>
          <button onClick={reload} className="text-faint hover:text-ink text-sm">↻ Refresh</button>
        </div>
        {posts === null ? <p className="text-faint">Loading…</p>
          : posts.length === 0 ? <p className="text-faint">Nothing published yet.</p>
          : <div className="divide-y divide-line border-t border-line">
              {posts.map((p) => (
                <button key={p.post_id} onClick={() => setReading(p.post_id)} className="w-full text-left py-4 flex items-center justify-between group">
                  <span className="group-hover:text-accent">{p.title}</span>
                  <span className={`text-xs ${p.status === "indexed" ? "text-ok" : "text-warn"}`}>{p.status === "indexed" ? "AI-searchable" : "indexing…"}</span>
                </button>
              ))}
            </div>}
      </div>
      {reading && <Reader tenantId={user.tenant_id} postId={reading} onClose={() => setReading(null)} />}
    </div>
  );
}

function renderMarkdown(md) {
  const lines = (md || "").split("\n");
  const out = []; let para = [], list = [];
  const fp = () => { if (para.length) { out.push(<p key={out.length}>{para.join(" ")}</p>); para = []; } };
  const fl = () => { if (list.length) { out.push(<ul key={out.length}>{list.map((li, i) => <li key={i}>{li}</li>)}</ul>); list = []; } };
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^#\s+/.test(line)) { fp(); fl(); out.push(<h1 key={out.length}>{line.replace(/^#\s+/, "")}</h1>); }
    else if (/^##\s+/.test(line)) { fp(); fl(); out.push(<h2 key={out.length}>{line.replace(/^##\s+/, "")}</h2>); }
    else if (/^###\s+/.test(line)) { fp(); fl(); out.push(<h3 key={out.length}>{line.replace(/^###\s+/, "")}</h3>); }
    else if (/^[-*]\s+/.test(line)) { fp(); list.push(line.replace(/^[-*]\s+/, "")); }
    else if (line.trim() === "") { fp(); fl(); }
    else { fl(); para.push(line); }
  }
  fp(); fl();
  return out;
}

// ---------------------------------------------------------------------------
// Phase 1–3: follow button, Following, Groups, group chat, global search
// ---------------------------------------------------------------------------
function FollowButton({ userId, initialFollowing }) {
  const [following, setFollowing] = useState(!!initialFollowing);
  const [busy, setBusy] = useState(false);
  async function toggle() {
    setBusy(true);
    try {
      if (following) { await unfollowUser(userId); setFollowing(false); }
      else { await followUser(userId); setFollowing(true); }
    } catch (e) { alert(e.message); } finally { setBusy(false); }
  }
  return (
    <button onClick={toggle} disabled={busy}
      className={following
        ? "ml-auto shrink-0 border border-line rounded-full px-4 py-1.5 text-sm text-soft hover:text-ink"
        : `${DARK} ml-auto shrink-0 px-4 py-1.5 text-sm`}>
      {busy ? "…" : following ? "Following" : "Follow"}
    </button>
  );
}

function Following() {
  const [profiles, setProfiles] = useState(null);
  useEffect(() => { listFollowing().then(setProfiles).catch(() => setProfiles([])); }, []);
  if (profiles === null) return <p className="text-faint">Loading…</p>;
  return (
    <div>
      <h1 className="text-3xl font-bold tracking-tight mb-1">Following</h1>
      <p className="text-soft mb-8">Writers you follow.</p>
      {profiles.length === 0
        ? <p className="text-faint">You're not following anyone yet. <Link to="/" className="text-accent">Discover writers</Link>.</p>
        : <div className="grid gap-4 sm:grid-cols-2">
            {profiles.map((p) => (
              <Link key={p.user_id} to={`/u/${p.user_id}`} className="group flex items-center gap-4 bg-white border border-line rounded-2xl p-5 hover:shadow-md transition">
                <div className={`${AVATAR} w-12 h-12 text-lg`}>{initials(p.display_name)}</div>
                <div className="min-w-0"><div className="font-semibold truncate">{p.display_name}</div><div className="text-soft text-sm">writes about {p.domain}</div></div>
                <span className="ml-auto text-faint group-hover:text-accent">→</span>
              </Link>
            ))}
          </div>}
    </div>
  );
}

function GroupsList() {
  const [groups, setGroups] = useState(null);
  const [name, setName] = useState("");
  const navigate = useNavigate();
  const reload = () => listGroups().then(setGroups).catch(() => setGroups([]));
  useEffect(reload, []);
  async function create(e) {
    e.preventDefault(); if (!name.trim()) return;
    try { const g = await createGroup(name.trim()); setName(""); navigate(`/groups/${g.group_id}`); }
    catch (ex) { alert(ex.message); }
  }
  if (groups === null) return <p className="text-faint">Loading…</p>;
  return (
    <div className="max-w-article mx-auto">
      <h1 className="text-3xl font-bold tracking-tight mb-1">Groups</h1>
      <p className="text-soft mb-6">Bundle writers together, then ask the whole group's AIs at once.</p>
      <form onSubmit={create} className="flex gap-2 mb-8">
        <input className={INPUT} placeholder="New group name" value={name} onChange={(e) => setName(e.target.value)} />
        <button className={`${DARK} px-4 shrink-0`} type="submit">Create</button>
      </form>
      {groups.length === 0 ? <p className="text-faint">No groups yet.</p>
        : <div className="divide-y divide-line border-y border-line">
            {groups.map((g) => (
              <button key={g.group_id} onClick={() => navigate(`/groups/${g.group_id}`)} className="w-full text-left py-4 flex items-center justify-between group">
                <span className="font-medium group-hover:text-accent">{g.name}</span>
                <span className="text-faint group-hover:text-accent">→</span>
              </button>
            ))}
          </div>}
    </div>
  );
}

function MemberPicker({ existing, onAdd }) {
  const [profiles, setProfiles] = useState([]);
  useEffect(() => { listProfiles().then(setProfiles).catch(() => {}); }, []);
  const available = profiles.filter((p) => !existing.includes(p.user_id));
  return (
    <div className="border border-line rounded-xl divide-y divide-line mb-6 max-h-60 overflow-y-auto">
      {available.length === 0 ? <div className="px-4 py-3 text-faint text-sm">No more writers to add.</div>
        : available.map((p) => (
          <div key={p.user_id} className="flex items-center gap-3 px-4 py-2">
            <span className="flex-1 truncate">{p.display_name} <span className="text-faint text-sm">· {p.domain}</span></span>
            <button onClick={() => onAdd(p.user_id)} className="text-accent text-sm">Add</button>
          </div>
        ))}
    </div>
  );
}

function GroupDetail() {
  const { groupId } = useParams();
  const [group, setGroup] = useState(null);
  const [dirOpen, setDirOpen] = useState(false);
  const reload = () => getGroup(groupId).then(setGroup).catch(() => setGroup(false));
  useEffect(() => { reload(); /* eslint-disable-next-line */ }, [groupId]);
  if (group === null) return <p className="text-faint">Loading…</p>;
  if (!group) return <p className="text-faint">Group not found. <Link to="/groups" className="text-accent">Back</Link></p>;
  async function add(userId) { try { await addGroupMember(groupId, userId); reload(); } catch (e) { alert(e.message); } }
  async function remove(userId) { try { await removeGroupMember(groupId, userId); reload(); } catch (e) { alert(e.message); } }
  return (
    <div className="max-w-article mx-auto">
      <Link to="/groups" className="text-soft hover:text-ink text-sm">← Groups</Link>
      <h1 className="text-2xl font-bold tracking-tight mt-3 mb-1">{group.name}</h1>
      <p className="text-soft mb-6">{group.members.length} member{group.members.length === 1 ? "" : "s"}</p>
      <div className="flex flex-wrap gap-2 mb-4">
        {group.members.map((m) => (
          <span key={m.user_id} className="inline-flex items-center gap-2 bg-cream border border-line rounded-full pl-3 pr-2 py-1 text-sm">
            {m.display_name || m.user_id}
            {group.is_owner && <button onClick={() => remove(m.user_id)} className="text-faint hover:text-err">✕</button>}
          </span>
        ))}
      </div>
      {group.is_owner && <button onClick={() => setDirOpen((v) => !v)} className="text-accent text-sm mb-6">{dirOpen ? "Close" : "+ Add members"}</button>}
      {dirOpen && <MemberPicker existing={group.members.map((m) => m.user_id)} onAdd={add} />}
      <GroupChatPanel groupId={groupId} groupName={group.name} />
    </div>
  );
}

function GroupChatPanel({ groupId, groupName }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef(null);
  useEffect(() => { scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight); }, [messages]);
  async function send(e) {
    e.preventDefault(); const q = input.trim(); if (!q || busy) return;
    setInput(""); setBusy(true);
    setMessages((m) => [...m, { role: "user", text: q }, { role: "ai", text: "", citations: null, pending: true }]);
    const queue = []; let reading = true;
    const timer = setInterval(() => {
      if (queue.length) { const t = queue.shift(); setMessages((m) => { const c = [...m]; const l = c[c.length - 1]; c[c.length - 1] = { ...l, text: l.text + t }; return c; }); }
      else if (!reading) clearInterval(timer);
    }, 14);
    try {
      const cites = await askGroup({ groupId }, q, (t) => queue.push(t));
      reading = false;
      setMessages((m) => { const c = [...m]; c[c.length - 1] = { ...c[c.length - 1], citations: cites, pending: false }; return c; });
    } catch (ex) {
      reading = false;
      setMessages((m) => { const c = [...m]; c[c.length - 1] = { ...c[c.length - 1], text: `⚠️ ${ex.message}`, pending: false }; return c; });
    } finally { setBusy(false); }
  }
  return (
    <div className="border border-line rounded-2xl overflow-hidden">
      <div className="px-4 py-3 border-b border-line font-semibold text-sm">Ask everyone in {groupName}</div>
      <div ref={scrollRef} className="max-h-96 overflow-y-auto p-4 flex flex-col gap-3">
        {messages.length === 0 && <div className="text-soft text-sm bg-cream rounded-xl p-4">Ask a question — it searches every member's posts and tells you who wrote what.</div>}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : ""}`}>
            <div className={`max-w-[85%] px-3.5 py-2.5 rounded-2xl leading-relaxed whitespace-pre-wrap text-[14px] ${m.role === "user" ? "bg-ink text-white rounded-br-sm" : "bg-cream text-ink rounded-bl-sm"}`}>
              {m.text || (m.pending ? <span className="text-faint tracking-widest">•••</span> : "")}
              {m.citations && m.citations.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {m.citations.map((c, j) => <span key={j} title={`score ${c.score}`} className="text-[11px] text-accent bg-accent/10 px-2 py-px rounded-full">{c.writer}: {c.title}</span>)}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
      <form onSubmit={send} className="p-3 border-t border-line flex gap-2">
        <input className={`${INPUT} rounded-full py-2`} value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask the group…" disabled={busy} />
        <button type="submit" disabled={busy || !input.trim()} className={`${DARK} px-4 shrink-0`}>{busy ? "…" : "↑"}</button>
      </form>
    </div>
  );
}

function GlobalSearchPage() {
  const [q, setQ] = useState("");
  const [results, setResults] = useState(null);
  const [busy, setBusy] = useState(false);
  async function run(e) {
    e.preventDefault(); if (!q.trim()) return; setBusy(true);
    try { setResults(await globalSearch(q.trim())); } catch (ex) { alert(ex.message); setResults([]); } finally { setBusy(false); }
  }
  return (
    <div className="max-w-article mx-auto">
      <h1 className="text-3xl font-bold tracking-tight mb-1">Search everyone</h1>
      <p className="text-soft mb-6">Find writers across the whole platform who've written about a topic.</p>
      <form onSubmit={run} className="flex gap-2 mb-8">
        <input className={INPUT} placeholder="e.g. filter coffee, react hooks, marathon training" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className={`${DARK} px-5 shrink-0`} type="submit" disabled={busy}>{busy ? "…" : "Search"}</button>
      </form>
      {results === null ? null
        : results.length === 0 ? <p className="text-faint">No matches.</p>
        : <div className="divide-y divide-line border-y border-line">
            {results.map((r) => (
              <Link key={r.post_id} to={`/u/${r.user_id}`} className="block py-4 group">
                <div className="font-medium group-hover:text-accent">{r.title || "Untitled"}</div>
                <div className="text-faint text-sm">by {r.writer}</div>
                <div className="text-soft text-sm mt-1">{r.snippet}</div>
              </Link>
            ))}
          </div>}
    </div>
  );
}
