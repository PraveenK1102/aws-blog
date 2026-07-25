#!/usr/bin/env python
"""Ad-hoc dev question tool — log in as a seeded user and ask anything.

    .venv/bin/python local/dev_ask.py <username> "your question here"

Seeded usernames: karthikraja, anitharani, senthilkumar, divyabharathi, balamurugan
(email <username>@gmail.com, password <username>@password@123)
"""
import json
import sys
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


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    username, question = sys.argv[1], " ".join(sys.argv[2:])
    auth = json.loads(_post("/api/auth/login",
                            {"email": f"{username}@gmail.com", "password": f"{username}@password@123"}))
    token = auth["token"]
    print(f">>> {username}: {question}\n")
    raw = _post("/api/ask", {"question": question}, token=token)
    cites = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        e = json.loads(line)
        if e.get("type") == "content":
            sys.stdout.write(e["text"]); sys.stdout.flush()
        elif e.get("type") == "done":
            cites = e.get("citations", [])
    print("\n\n--- citations ---")
    for c in cites:
        print(f"  • {c['title']}  ({c['score']})")
    if not cites:
        print("  (none)")


if __name__ == "__main__":
    main()
