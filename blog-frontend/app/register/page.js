"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, setToken } from "@/lib/api";

export default function RegisterPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const { token } = await api("/auth/register", {
        method: "POST",
        body: JSON.stringify({ email, password, name: name || undefined }),
      });
      setToken(token);
      router.push("/posts/");
      router.refresh();
    } catch (err) {
      setError(err.data?.error || err.message || "Registration failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <nav>
        <Link href="/">Home</Link>
        <Link href="/posts/">Posts</Link>
        <Link href="/login/">Login</Link>
        <Link href="/register/">Register</Link>
      </nav>
      <h1>Register</h1>
      <form onSubmit={handleSubmit}>
        {error && <p style={{ color: "red" }}>{error}</p>}
        <label>Email</label>
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <label>Password</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        <label>Name (optional)</label>
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
        <button type="submit" disabled={loading}>{loading ? "..." : "Register"}</button>
      </form>
      <p><Link href="/login/">Login instead</Link></p>
    </>
  );
}
