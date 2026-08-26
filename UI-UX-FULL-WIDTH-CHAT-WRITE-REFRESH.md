# Full-Width UI + Writing + Profile + Chat Refresh

**Status: IMPLEMENTED AND TESTED LOCALLY. NOT DEPLOYED. Username backfill NOT RUN.**
**Date:** 2026-08-26

## 1. Objective
Turn a narrow centred mobile-feeling page into a full-width desktop product: a real
writing workspace, richer Markdown tooling, an editable public username and email, and
ask surfaces that read as AI conversations rather than comment threads.

## 2. UI audit — root cause of the narrow desktop UI
`Shell` in `blog-frontend/src/App.jsx` applied Tailwind's custom `max-w-feed` (**760px**,
defined in `tailwind.config.js`) to **both** the header bar and `<main>`. Every page
therefore rendered inside a 760px column no matter the viewport, and most pages then
nested a second `max-w-article` (**680px**) inside it. The app is a single 723-line
`App.jsx` with `api.js`, `index.css` and `main.jsx`; there was **no test framework**, and
`renderMarkdown` was a hand-rolled renderer supporting only H1–H3, bullets and paragraphs.

## 3. Global layout
`Shell` now uses `w-full px-6 xl:px-8` on the header and `<main>` — full width with 24px
gutters (32px from `xl`). Fixed at the shell level, not patched per page.

* **Desktop (≥1024px `lg`)**: side-by-side workspaces, full width.
* **Tablet / mobile**: `flex-col` stacking; the toolbar wraps.
* No `w-screen`, no bare `w-[100vw]`, no `width:100vw` — a test enforces this and also
  asserts that the one remaining `100vw` is the *clamped* `min(400px, calc(100vw-2rem))`
  on the floating chat widget, which prevents overflow rather than causing it.
* A reading-width cap is deliberately kept in the article `Reader`, where line length
  matters.

## 4. Write experience
* Main column `flex-1 min-w-0` beside a `w-[260px]` secondary rail (`lg:flex-row`).
* Editor min-height `min-h-[60vh] lg:min-h-[min(65vh,720px)]` with an absolute
  `[min-height:560px]` floor.
* Title → toolbar → body → tags → **single** Publish, bottom-right, with disabled,
  loading, validation, success and error states preserved.
* Tags are sent to the API as a list; posts without tags keep working unchanged.

## 5. Markdown toolbar
`src/editor.jsx`. Paragraph, H1–H3, Bold, Italic, Underline, bulleted list, numbered
list, blockquote, inline code, code block, link, horizontal rule. Every action is
selection-aware; block actions replace an existing prefix rather than stacking (`# t` →
H2 gives `## t`, not `## # t`). `applyFormat` is a pure function, so all 16 behaviours are
unit-tested directly. Markdown remains the source format — the toolbar only inserts
Markdown, and S3 still stores exactly what the author typed.

## 6. Safe underline
Underline is not CommonMark, and raw HTML was **not** enabled. `src/markdown.jsx` builds
**React elements** and never calls `dangerouslySetInnerHTML`, so author text is always
escaped by React. Exactly one marker — `<u>…</u>` — is recognised by the inline tokenizer
and emitted as a React `<u>`; its inner text is still escaped. No other tag is recognised:
`<b>`, `<script>` and `<img onerror>` all remain literal text. Link hrefs are restricted
to `https?:`, `mailto:`, `/` and `#`, so `javascript:` and `data:` links render as plain
text instead of anchors. Six adversarial tests cover this. **No DECISION REQUIRED** — safe
underline needed no weakening of XSS protection.

## 7. Shortcut UX
Cmd/Ctrl + B / I / U / K, handled on the editor only and ignoring anything else (Cmd+S is
explicitly tested as not hijacked). Help lives in exactly **one** place: a `⌨ Shortcuts`
button on the toolbar opening a small popover. A test asserts only one such affordance
exists.

## 8. Your Posts
A right rail: heading, `View Your Posts` button with a count when already loaded, and an
expandable list. Visible but secondary — no feed competing with the editor. Collapses
above the editor on tablet/mobile.

## 9. Existing identity model
`user_id` (PK), `email` (GSI `by_email`), `password_hash`, `tenant_id`, `display_name`,
`active`, `created_at`. JWT carries `sub = user_id`, `tenant_id` and `email`; **identity
is `sub`**, so email is never used for authorization.

## 10. New username model
`username` is an optional, mutable, **public profile attribute**. It is never a primary
key, tenant identity, Qdrant scope, post ownership or JWT subject. Renaming moves no
posts, rewrites no S3 paths, touches no Qdrant points, and leaves `user_id`/`tenant_id`
untouched — asserted by tests.

## 11. Username uniqueness design
A read-then-write over the `by_email` GSI is **not** race-safe (GSIs are eventually
consistent), so uniqueness uses a **reservation item in the existing users table**:

```
user_id = "USERNAME#<normalized>"  ->  { owner_user_id, reservation_kind, claimed_at }
```

Claiming is a conditional `PutItem` on `attribute_not_exists(user_id)` — an atomic
compare-and-set. **No new table, no new GSI, no key-schema change, no infrastructure
mutation**, so no DECISION REQUIRED was triggered.

* Normalisation is lowercase, so case can never create duplicates.
* Format: `^[a-z0-9._]{3,30}$`, no leading/trailing dot, no consecutive dots — corpus
  usernames such as `kavin.raj25` and `priyadharshini.m25` fit.
* **Rename claims the new name first and releases the old one only after the new claim
  commits.** A mid-flight failure can leave a harmless orphaned reservation; it can never
  leave two users sharing a username.
* Reservation rows carry no `tenant_id`, and `GET /api/users` already skips items whose
  tenant does not resolve — so reservations are invisible in the directory. A test pins
  that behaviour so it cannot silently regress.

## 12. Email update design
`PUT /api/me/email` validates syntax, checks the `by_email` GSI, and additionally makes an
atomic `EMAIL#<normalized>` reservation claim. `user_id`/`tenant_id` and every post, chat,
follow, group and Qdrant point keep their owner. Login with the new address works and the
old address stops resolving. Because the JWT carries `email` as a claim, a **refreshed
token is issued** and stored by the client, so no stale claim lingers.

> **Known limitation, stated rather than glossed:** signup does not create EMAIL#
> reservations, so a change racing a brand-new signup keeps the same window that exists
> today. Two concurrent *changes* are fully race-safe. Closing the remaining window means
> adding reservations to signup plus a backfill for existing users — out of scope here.

## 13. Password policy
No password UI in Profile: no current/new/confirm fields, no change-password control, and
no `type="password"` input anywhere in the profile — enforced by test. Backend password
code is untouched.

## 14. Routed Ask layout
Desktop `lg:flex-row` with two `lg:w-1/2` panes: left a searchable, scrollable checkbox
list with a selected count; right the conversation. Height is
`h-[calc(100vh-11rem)] min-h-[520px]`, each pane scrolling independently with the composer
pinned at the bottom. Stacks on tablet/mobile.

## 15. Per-message scope snapshot
`buildScope(selected)` captures `{kind, tenant_ids, labels, count}` at send time and stores
it **on the user message**, so a later selector change can never rewrite an earlier turn —
tested by rendering two turns with different scopes and by mutating the selection array
after snapshotting.

Backend: `chats.append_turn(..., scope=None)` persists it on the user message, additively.
`_scope_snapshot()` builds it from the authoritative resolved `targets`. **Messages written
before this feature have no `scope` key and render unchanged** (tested).
`scope_labels` on the request is **display metadata only** — retrieval and authorization
are unchanged and still resolve scope server-side.

Display rule (§24): ≤5 → all names; >5 → first five then `+N more`, where N is computed
from the true count, not the truncated label list.

## 16. Shared chat shell
`src/chat.jsx` provides `ChatShell`, `MessageList`, `UserMessage`, `AssistantMessage`,
`ScopeSummary`, `CitationList`, `ChatComposer` and `buildScope`. User turns are compact and
right-aligned; assistant turns are unbordered and spacious with citations beneath;
composer at the bottom with Enter-to-send and Shift+Enter for a newline. Tests assert the
assistant bubble has no comment-card border and that no per-message author labels appear.

## 17. Group Ask
`GroupChatPanel` was rewritten to render through the same `ChatShell`, so there is one chat
implementation instead of three. It shows `Group: <name> · <n> members` (count omitted when
not cheaply available) rather than listing members under every question. Group retrieval
and authorization are untouched — the backend still resolves membership itself.

## 18. Accessibility
`role="toolbar"` with `aria-label`, an `aria-label` on every formatting button, real
`<input type="checkbox">` with `<label>` wrappers for multi-select, `sr-only` labels for
title/body/composer, `aria-expanded`/`aria-haspopup` on the shortcut popover,
`role="status"` for save feedback, `role="alert"` for chat errors, and visible
`focus:ring-2` states.

## 19. Security
Profile mutations take identity from the authenticated JWT only; a client-supplied
`user_id` is never accepted (tested). Username/email changes cannot alter `user_id` or
`tenant_id`. Frontend selection remains a *requested* scope — backend authorization is
unchanged and authoritative. Markdown sanitisation was strengthened, never weakened: link
schemes are now allow-listed, which the previous renderer did not do because it had no
links at all.

## 20. Tests
**364 total, all passing.**

| Suite | Tests |
|---|---|
| `blog-frontend` markdown (incl. 6 XSS cases) | 18 |
| `blog-frontend` editor/toolbar/shortcuts | 27 |
| `blog-frontend` chat/scope/composer | 25 |
| `blog-frontend` layout/profile/api regression | 29 |
| `common.test_profile` (username/email identity) | 24 |
| Existing backend (RAG, tracing, release, corpus) | 241 |

Vitest is configured with esbuild's automatic JSX rather than `@vitejs/plugin-react`,
because the app is pinned to Vite 3 / plugin-react 2 while Vitest bundles Vite 5 and mixing
them fails with "can't detect preamble". The app build still uses the plugin and is
unchanged.

## 21. Responsive verification
Verified by build + layout assertions rather than a live browser: `lg:` (1024px) switches
both workspaces to side-by-side, so 1440/1280/1024 all get the desktop layout and below
1024 stacks. No `w-screen`/unbounded `100vw`, and `min-w-0` on flex children prevents the
classic flexbox overflow. **A human visual pass at 1440/1280/1024/mobile is still
outstanding** — see Known limitations.

## 22. Username backfill plan
`tools/backfill_usernames.py`, **dry-run by default**, `--apply` required. Maps each
manifest persona's email local part to its username (`kavin.raj25@example.com` →
`kavin.raj25`), verified against the manifest so the two can never disagree. Idempotent
(already-correct users are a no-op), conditional (claims go through the same atomic
reservation as the UI), and it **STOPS** if a target username is already claimed by a
different user rather than overwriting. The five UNKNOWN_REVIEW accounts are not in the
manifest and are therefore never touched — no username is ever invented for them, and the
UI falls back to `display_name`. **NOT RUN against AWS.**

## 23. Known limitations
1. **No human visual review yet** at 1440/1280/1024/mobile — automated checks cover
   structure, not appearance.
2. **Email-change vs concurrent-signup race** remains (§12).
3. Ask surfaces now use a **non-streaming** request and render the answer once complete,
   rather than the previous token-by-token typewriter. Under LWA buffered mode the client
   already received the whole body at once, so this is a code simplification rather than a
   change in what the user perceives — but it is a real difference.
4. `App.jsx` is still one large module; chat/editor/markdown were extracted, other pages
   were not.
5. Tags are stored and sent but the post **read** API does not return them yet, so they are
   not rendered on published posts.
6. Layout assertions are source-level, which pins the decisions but would not catch a
   purely visual CSS regression.

## 24. Deployment state
**NOT DEPLOYED.** No S3 upload, no CloudFront invalidation, no Lambda deploy, no ECR push,
no DynamoDB backfill, no API Gateway or infrastructure mutation. RAG semantics untouched —
frozen prompt hashes still `763d12cd82245285` / `ae8185181e88f25f` / `8c30bb9b064e6784`.
