const BASE = typeof window !== "undefined" ? (process.env.NEXT_PUBLIC_API_URL || "") : "";

export function getToken() {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("token");
}

export async function api(path, options = {}) {
  const url = `${BASE}${path}`;
  const headers = { ...options.headers };
  if (!headers["Content-Type"] && typeof options.body === "string") headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(url, { ...options, headers });
  const data = res.ok ? (res.status === 204 ? null : await res.json()) : null;
  if (!res.ok) {
    const err = new Error(data?.error || res.statusText);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

export function setToken(token) {
  if (typeof window !== "undefined") localStorage.setItem("token", token);
}
export function clearToken() {
  if (typeof window !== "undefined") localStorage.removeItem("token");
}
