"""Strict parser for the curated 25-user blog corpus.

SOURCE BOUNDARY IS ABSOLUTE (§1): only the text between `<!-- BEGIN_INGEST -->`
and `<!-- END_INGEST -->` may become a user or a post. Everything else in the
file — the title, the test-data note, "Research Basis", "Short Claude Ingestion
Prompt" — is documentation and is discarded before parsing begins.

FIDELITY (§5): titles, dates, bodies, Markdown and tags are captured verbatim.
The parser never rewrites, summarises, spell-corrects, normalises or reorders
anything. It only splits and validates. If the corpus does not match the
expected shape, the parser REPORTS the discrepancy — it never repairs it.
"""
import hashlib
import re

BEGIN = "<!-- BEGIN_INGEST -->"
END = "<!-- END_INGEST -->"

USER_RE = re.compile(r"^# User (\d+)\s+—\s+(.+?)\s*$")
FIELD_RE = re.compile(r"^-\s+\*\*(.+?):\*\*\s*(.*?)\s*$")
POSTS_HDR_RE = re.compile(r"^## Posts\s*$")
POST_RE = re.compile(r"^### (\d+)\.\s+(.+?)\s*$")
DATE_RE = re.compile(r"^\*\*Date:\*\*\s*(.+?)\s*$")
TAGS_RE = re.compile(r"^\*\*Tags:\*\*\s*(.+?)\s*$")


class CorpusError(Exception):
    """Structural problem in the corpus. Never auto-repaired."""


def extract_ingest_region(text: str) -> str:
    """Return ONLY the ingestable region: FIRST BEGIN .. FIRST END after it.

    The corpus file mentions both marker strings a second time, in prose inside a
    fenced ```text block in the trailing "Short Claude Ingestion Prompt"
    documentation section ("Use only content between ... and ..."). That mention
    sits AFTER the real END marker, so first-BEGIN/first-END is unambiguous and
    the documentation tail is still fully excluded.

    The genuinely dangerous case — a second BEGIN *inside* the region, which would
    make the boundary ambiguous — is rejected rather than guessed.
    """
    if BEGIN not in text:
        raise CorpusError(f"missing {BEGIN}")
    start = text.index(BEGIN) + len(BEGIN)
    if END not in text[start:]:
        raise CorpusError(f"missing {END} after {BEGIN}")
    stop = start + text[start:].index(END)
    region = text[start:stop]
    if BEGIN in region:
        raise CorpusError("a second BEGIN_INGEST occurs INSIDE the region — "
                          "boundary is ambiguous, refusing to guess")
    return region


def _strip_code_ticks(v: str) -> str:
    return v.strip().strip("`").strip()


def parse(text: str) -> dict:
    """Parse the ingest region into {directory, users}. Verbatim content."""
    region = extract_ingest_region(text)
    lines = region.split("\n")

    directory = _parse_directory(lines)
    users = _parse_users(lines)
    return {"directory": directory, "users": users,
            "ingest_region": region,
            "ingest_sha256": hashlib.sha256(region.encode("utf-8")).hexdigest()}


def _parse_directory(lines) -> list[dict]:
    """The numbered User Directory list. Used as an independent cross-check
    against the per-user sections — two sources that must agree."""
    out, in_dir = [], False
    pat = re.compile(
        r"^\d+\.\s+\*\*(.+?)\*\*\s+\(`(.+?)`\)\s+—\s+Age\s+(\d+)\s+—\s+(.+?)\s+—\s+(.+?)\s+—\s+(\d+)\s+posts\s*$")
    alt = re.compile(
        r"^\d+\.\s+\*\*(.+?)\*\*\s+\(`(.+?)`\)\s+—\s+Age\s+(\d+)\s+—\s+(.+?)\s+—\s+(\d+)\s+posts\s*$")
    for ln in lines:
        if ln.startswith("## User Directory"):
            in_dir = True
            continue
        if in_dir:
            if ln.startswith("#") or ln.strip() == "---":
                break
            s = ln.strip()
            if not s:
                continue
            m = pat.match(s)
            if m:
                out.append({"display_name": m.group(1), "username": m.group(2),
                            "age": int(m.group(3)), "origin": m.group(4),
                            "content_type": m.group(5), "post_count": int(m.group(6))})
                continue
            m = alt.match(s)
            if m:
                out.append({"display_name": m.group(1), "username": m.group(2),
                            "age": int(m.group(3)), "origin": m.group(4),
                            "content_type": "", "post_count": int(m.group(5))})
                continue
            if s.startswith(tuple(f"{i}." for i in range(1, 100))):
                raise CorpusError(f"unparsed directory row: {s[:90]}")
    return out


def _parse_users(lines) -> list[dict]:
    """Per-user sections. Body text is captured VERBATIM between the Date line
    and the Tags line — no stripping beyond the leading/trailing blank lines that
    the Markdown separators themselves introduce."""
    users, i, n = [], 0, len(lines)
    while i < n:
        m = USER_RE.match(lines[i])
        if not m:
            i += 1
            continue
        user = {"index": int(m.group(1)), "display_name": m.group(2),
                "fields": {}, "posts": []}
        i += 1
        # metadata bullets until "## Posts"
        while i < n and not POSTS_HDR_RE.match(lines[i]) and not USER_RE.match(lines[i]):
            fm = FIELD_RE.match(lines[i])
            if fm:
                user["fields"][fm.group(1).strip()] = _strip_code_ticks(fm.group(2))
            i += 1
        if i < n and POSTS_HDR_RE.match(lines[i]):
            i += 1
            i = _parse_posts(lines, i, n, user)
        users.append(user)
    return users


def _parse_posts(lines, i, n, user) -> int:
    while i < n:
        if USER_RE.match(lines[i]):
            break
        pm = POST_RE.match(lines[i])
        if not pm:
            i += 1
            continue
        post = {"index": int(pm.group(1)), "title": pm.group(2),
                "date": None, "body": None, "tags": []}
        i += 1
        body_lines, date_seen, tags_seen = [], False, False
        while i < n:
            if USER_RE.match(lines[i]) or POST_RE.match(lines[i]):
                break
            dm = DATE_RE.match(lines[i])
            if dm and not date_seen:
                post["date"] = dm.group(1)
                date_seen = True
                i += 1
                continue
            tm = TAGS_RE.match(lines[i])
            if tm:
                post["tags"] = [t.strip() for t in tm.group(1).split(",") if t.strip()]
                tags_seen = True
                i += 1
                continue
            if lines[i].strip() == "---":
                i += 1
                continue
            if date_seen and not tags_seen:
                body_lines.append(lines[i])
            i += 1
        post["body"] = "\n".join(body_lines).strip("\n")
        post["body_sha256"] = hashlib.sha256(
            (post["body"] or "").encode("utf-8")).hexdigest()
        user["posts"].append(post)
    return i


# ------------------------------------------------------------------ validation
EXPECTED_COUNTS = {
    "kavin.raj25": 10, "vignesh.k25": 10, "gokul.krishnan25": 10,
    "janani.raman25": 10, "aishwarya.selvam25": 10, "naveen.kumar25": 10,
    "divya.rajan25": 10, "nithin.k25": 10, "karthik.raj25": 10,
    "saikiran.reddy25": 10, "nandhini.k25": 10, "pavithra.selvan25": 10,
    "madhan.kumar25": 10, "deepika.chandran25": 10, "anusha.reddy25": 10,
    "vishnu.priyan25": 18, "priyadharshini.m25": 28, "swathi.raj25": 10,
    "ashwin.raj25": 6, "dinesh.k25": 6, "yogesh.k25": 6,
    "meena.lakshmi25": 6, "dharani.vel25": 6,
    "aravind.k25": 16, "abinaya.raj25": 16,
}
EXPECTED_USERS = 25
EXPECTED_POSTS = 268


def validate(parsed: dict) -> list[str]:
    """Return a list of problems. Empty list == corpus matches expectations."""
    problems = []
    users = parsed["users"]
    directory = parsed["directory"]

    if len(users) != EXPECTED_USERS:
        problems.append(f"user sections: {len(users)} != {EXPECTED_USERS}")
    if len(directory) != EXPECTED_USERS:
        problems.append(f"directory rows: {len(directory)} != {EXPECTED_USERS}")

    unames = []
    for u in users:
        un = u["fields"].get("Username")
        if not un:
            problems.append(f"user {u['index']} ({u['display_name']}): no Username field")
            continue
        unames.append(un)
        age = u["fields"].get("Age")
        if age != "25":
            problems.append(f"{un}: Age is {age!r}, expected '25'")
        declared = u["fields"].get("Post Count")
        if declared is not None and declared.isdigit() and int(declared) != len(u["posts"]):
            problems.append(f"{un}: declared Post Count {declared} != parsed {len(u['posts'])}")

    dupes = {x for x in unames if unames.count(x) > 1}
    if dupes:
        problems.append(f"duplicate usernames: {sorted(dupes)}")

    if set(unames) != set(EXPECTED_COUNTS):
        missing = sorted(set(EXPECTED_COUNTS) - set(unames))
        extra = sorted(set(unames) - set(EXPECTED_COUNTS))
        if missing:
            problems.append(f"missing expected usernames: {missing}")
        if extra:
            problems.append(f"unexpected usernames: {extra}")

    # directory must agree with the per-user sections (two independent sources)
    dmap = {d["username"]: d for d in directory}
    for u in users:
        un = u["fields"].get("Username")
        if un in dmap:
            if dmap[un]["post_count"] != len(u["posts"]):
                problems.append(
                    f"{un}: directory says {dmap[un]['post_count']} posts, section has {len(u['posts'])}")
            if dmap[un]["age"] != 25:
                problems.append(f"{un}: directory age {dmap[un]['age']} != 25")

    total = 0
    for u in users:
        un = u["fields"].get("Username")
        got = len(u["posts"])
        total += got
        want = EXPECTED_COUNTS.get(un)
        if want is not None and got != want:
            problems.append(f"{un}: {got} posts, expected {want}")
        for p in u["posts"]:
            who = f"{un} #{p['index']} {p['title'][:40]!r}"
            if not p["title"]:
                problems.append(f"{who}: empty title")
            if not p["date"]:
                problems.append(f"{who}: missing Date")
            elif not re.match(r"^\d{4}-\d{2}-\d{2}$", p["date"]):
                problems.append(f"{who}: date {p['date']!r} not YYYY-MM-DD")
            if not p["body"]:
                problems.append(f"{who}: empty body")
            if not p["tags"]:
                problems.append(f"{who}: missing Tags")

    if total != EXPECTED_POSTS:
        problems.append(f"total posts: {total} != {EXPECTED_POSTS}")
    return problems
