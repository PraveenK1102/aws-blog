#!/usr/bin/env python
"""Dev end-to-end test: log in each user, ask on-topic (should answer) and
off-topic (should refuse) questions against their own workspace. Proves
retrieval + tenant isolation. Hits the running ask server (localhost:8080)."""
import json
import sys
import time
import urllib.request

SERVER = "http://localhost:8080"


def _post(path, body, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(SERVER + path, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read().decode()


def login(username):
    email = f"{username}@gmail.com"
    resp = json.loads(_post("/api/auth/login", {"email": email, "password": f"{username}@password@123"}))
    return resp["token"]


def ask(token, question):
    raw = _post("/api/ask", {"question": question}, token=token)
    answer, cites = [], []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        e = json.loads(line)
        if e.get("type") == "content":
            answer.append(e["text"])
        elif e.get("type") == "done":
            cites = e.get("citations", [])
    return "".join(answer).strip(), cites


# (username, on-topic Q [expect answer], off-topic Q [expect refusal])
MATRIX = [
    ("karthikraja",  "How should I structure my marathon training?",   "How do I make Chettinad chicken?"),
    ("anitharani",   "How do I land a back handspring?",               "What did the army teach about discipline?"),
    ("senthilkumar", "What did army training teach about discipline?", "How do Kubernetes pods and deployments work?"),
    ("divyabharathi","How do Kubernetes pods and deployments work?",   "How should I structure my marathon training?"),
    ("balamurugan",  "Explain dollar-cost averaging.",                 "How do I land a back handspring on the beam?"),
]


def main():
    for username, on_q, off_q in MATRIX:
        token = login(username)
        print("=" * 78)
        print(f"USER: {username}")
        # On-topic — expect a grounded answer + citations
        ans, cites = ask(token, on_q)
        print(f"\n  ON-TOPIC Q: {on_q}")
        print(f"  A: {ans[:200]}")
        print(f"  citations: {[c['title'] for c in cites]}")
        # Off-topic (another user's domain) — expect refusal + no citations
        ans2, cites2 = ask(token, off_q)
        refused = "hasn't written about" in ans2.lower()
        print(f"\n  OFF-TOPIC Q: {off_q}")
        print(f"  A: {ans2[:160]}")
        print(f"  citations: {len(cites2)}  ->  {'✅ ISOLATED (refused)' if refused and not cites2 else '⚠️ CHECK'}")
        print()
        time.sleep(8)  # stay under Groq free-tier TPM (llm.py also retries on 429)


if __name__ == "__main__":
    main()
