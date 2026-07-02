const BASE = import.meta.env.VITE_API_URL || "";

export function getToken() {
  return localStorage.getItem("token");
}

export function saveToken(token) {
  localStorage.setItem("token", token);
}

export function removeToken() {
  localStorage.removeItem("token");
}

export async function api(path, opts = {}) {
  const headers = { ...opts.headers };
  if (!headers["Content-Type"] && typeof opts.body === "string") {
    headers["Content-Type"] = "application/json";
  }
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${BASE}/api${path}`, { ...opts, headers });
  const data = res.ok ? (res.status === 204 ? null : await res.json()) : null;
  if (!res.ok) {
    const err = new Error(data?.error || res.statusText);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}
