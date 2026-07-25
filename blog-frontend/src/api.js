// API client for MultiTenantRAG. Same-origin (served by the same CloudFront
// distribution as /api/*), so BASE is empty and there is no CORS.
const BASE = import.meta.env.VITE_API_URL || "";

async function getJSON(path) {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export function listUsers() {
  return getJSON("/api/users").then((d) => d.users || []);
}

export function listPosts(tenantId) {
  return getJSON(`/api/tenants/${encodeURIComponent(tenantId)}/posts`).then((d) => d.posts || []);
}

export async function createPost(userId, title, content) {
  const res = await fetch(`${BASE}/api/posts`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Id": userId },
    body: JSON.stringify({ title, content }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.error || `${res.status}`);
  return data;
}

// Ask the tenant's AI. The backend returns NDJSON events (buffered today, but
// the same parser works unchanged if we flip to true streaming later):
//   {"type":"content","text":"..."}   repeated
//   {"type":"done","citations":[...]}
// onToken(text) is called per content event; resolves to the citations array.
export async function ask(userId, question, onToken) {
  const res = await fetch(`${BASE}/api/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Id": userId },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || err.error || `${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let citations = [];

  const handleLine = (line) => {
    line = line.trim();
    if (!line) return;
    let evt;
    try {
      evt = JSON.parse(line);
    } catch {
      return;
    }
    if (evt.type === "content") onToken(evt.text);
    else if (evt.type === "done") citations = evt.citations || [];
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop(); // keep any partial trailing line
    for (const line of lines) handleLine(line);
  }
  if (buffer) handleLine(buffer);
  return citations;
}
