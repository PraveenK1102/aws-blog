// API client for MultiTenantRAG. Uses relative /api paths (Vite proxies to the
// backend in dev; CloudFront routes them in prod). Auth via JWT bearer token.

const TOKEN_KEY = "mt_token";
export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (t) => localStorage.setItem(TOKEN_KEY, t);
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);

async function req(path, { method = "GET", body, auth = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth) {
    const t = getToken();
    if (t) headers.Authorization = `Bearer ${t}`;
  }
  const res = await fetch(path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const err = new Error((data && data.error) || res.statusText);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

export const signup = (email, password, display_name) =>
  req("/api/auth/signup", { method: "POST", auth: false, body: { email, password, display_name } });

export const login = (email, password) =>
  req("/api/auth/login", { method: "POST", auth: false, body: { email, password } });

export const me = () => req("/api/auth/me");

// Directory of all profiles (you browse these and ask their AIs).
export const listProfiles = () => req("/api/users").then((d) => d.users || []);
// One profile by user_id (for loading a /u/:userId page directly).
export const getProfile = (userId) => req(`/api/users/${encodeURIComponent(userId)}`);
// A profile's posts (any logged-in user can view).
export const listProfilePosts = (tenantId) =>
  req(`/api/tenants/${encodeURIComponent(tenantId)}/posts`).then((d) => d.posts || []);
// One post's full content (to read it).
export const getPost = (tenantId, postId) =>
  req(`/api/tenants/${encodeURIComponent(tenantId)}/posts/${encodeURIComponent(postId)}`);
// My own posts (for the Write/manage view).
export const listMyPosts = () => req("/api/posts").then((d) => d.posts || []);
export const createPost = (title, content, tags) =>
  req("/api/posts", { method: "POST",
    body: tags && tags.length ? { title, content, tags } : { title, content } });

// Saved chats (conversation memory, up to 5)
export const listChats = () => req("/api/chats").then((d) => d.chats || []);
export const listTrash = () => req("/api/chats/trash").then((d) => d.chats || []);
export const getChat = (id) => req(`/api/chats/${id}`);
export const createChat = (tenantId, profileUserId) =>
  req("/api/chats", { method: "POST", body: { tenant_id: tenantId, profile_user_id: profileUserId } });
export const deleteChat = (id) => req(`/api/chats/${id}`, { method: "DELETE" });
export const restoreChat = (id) => req(`/api/chats/${id}/restore`, { method: "POST" });
export const permanentDeleteChat = (id) => req(`/api/chats/${id}/permanent`, { method: "DELETE" });

// Ask a PROFILE's AI (tenantId = the profile you're visiting). chatId ties it to a
// saved conversation for memory. Streams NDJSON; onToken(text) per content chunk.
export async function ask(tenantId, question, onToken, chatId) {
  const res = await fetch("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` },
    body: JSON.stringify({ question, tenant_id: tenantId, chat_id: chatId || undefined }),
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(`ask failed (${res.status})`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let citations = [];
  const handleLine = (line) => {
    line = line.trim();
    if (!line) return;
    let evt;
    try { evt = JSON.parse(line); } catch { return; }
    if (evt.type === "content") onToken(evt.text);
    else if (evt.type === "done") citations = evt.citations || [];
    else if (evt.type === "error") throw new Error(evt.message || "ask error");
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) handleLine(line);
  }
  if (buffer) handleLine(buffer);
  return citations;
}

// --- Phase 1: follow / groups ---
export const followUser = (userId) => req(`/api/users/${encodeURIComponent(userId)}/follow`, { method: "POST" });
export const unfollowUser = (userId) => req(`/api/users/${encodeURIComponent(userId)}/follow`, { method: "DELETE" });
export const listFollowing = () => req("/api/me/following").then((d) => d.users || []);

export const createGroup = (name) => req("/api/groups", { method: "POST", body: { name } });
export const listGroups = () => req("/api/groups").then((d) => d.groups || []);
export const getGroup = (groupId) => req(`/api/groups/${groupId}`);
export const addGroupMember = (groupId, userId) => req(`/api/groups/${groupId}/members`, { method: "POST", body: { user_id: userId } });
export const removeGroupMember = (groupId, userId) => req(`/api/groups/${groupId}/members/${encodeURIComponent(userId)}`, { method: "DELETE" });
export const discoverGroups = () => req("/api/discover/groups").then((d) => d.groups || []);
export const subscribeGroup = (groupId) => req(`/api/groups/${groupId}/subscribe`, { method: "POST" });
export const unsubscribeGroup = (groupId) => req(`/api/groups/${groupId}/subscribe`, { method: "DELETE" });

// --- Phase 3: global discovery search (LLM-free) ---
export const globalSearch = (question) =>
  req("/api/search/global", { method: "POST", body: { question } }).then((d) => d.results || []);

// Ask a GROUP (group_id) or an explicit set of profiles (tenantIds). Streams NDJSON like ask().


// --- Profile: mutable username / email. Stable user_id + tenant_id never move.
export const getMyProfile = () => req("/api/me/profile");

export const checkUsername = (username) =>
  req(`/api/me/username-available?username=${encodeURIComponent(username)}`);

export const updateUsername = (username) =>
  req("/api/me/username", { method: "PUT", body: { username } });

export const updateEmail = (email) =>
  req("/api/me/email", { method: "PUT", body: { email } })
    .then((r) => { if (r && r.token) setToken(r.token); return r; });

/**
 * Non-streaming group/multi-user ask used by the conversational surfaces.
 * `scope_labels` is display metadata only — the backend ignores it for
 * retrieval and re-resolves authorization itself.
 */
export async function askGroup({ tenant_ids, group_id, question, chat_id, scope_labels }) {
  const res = await fetch("/api/ask/group", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` },
    body: JSON.stringify({
      question, tenant_ids, group_id: group_id || undefined,
      chat_id: chat_id || undefined, scope_labels: scope_labels || undefined,
    }),
  });
  if (!res.ok) throw new Error(`Ask failed (${res.status})`);
  const text = await res.text();
  let answer = "", citations = [];
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    let ev; try { ev = JSON.parse(line); } catch { continue; }
    if (ev.type === "content") answer += ev.text;
    else if (ev.type === "done") citations = ev.citations || [];
  }
  return { answer, citations };
}
