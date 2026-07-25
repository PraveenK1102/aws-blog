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
export const listPosts = () => req("/api/posts").then((d) => d.posts || []);
export const createPost = (title, content) =>
  req("/api/posts", { method: "POST", body: { title, content } });

// Ask the user's own AI. Streams NDJSON events; onToken(text) per content chunk.
// Same parser works whether the response streams (dev) or arrives buffered (prod).
export async function ask(question, onToken) {
  const res = await fetch("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` },
    body: JSON.stringify({ question }),
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
