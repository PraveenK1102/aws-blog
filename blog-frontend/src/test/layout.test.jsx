/**
 * Source-level regression tests for the layout and profile decisions.
 *
 * These assert against App.jsx's SOURCE rather than a mounted tree because the
 * app is one module with router + network side effects at import time; the
 * properties under test (no narrow max-width on the shell, a single Publish, no
 * password controls) are statically checkable and this keeps the guarantee cheap
 * and stable.
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

const read = (f) => fs.readFileSync(path.join(process.cwd(), "src", f), "utf8");
const App = read("App.jsx");

// The <main> element and the header bar, which previously carried max-w-feed.
const shell = App.slice(App.indexOf("function Shell"), App.indexOf("function Discover"));

describe("full-width desktop shell (§3)", () => {
  it("the app shell no longer applies the narrow max-w-feed cap", () => {
    // Checked against real className usage, not raw text: the source comment
    // documenting the removal legitimately mentions the old class name.
    const classNames = [...shell.matchAll(/className="([^"]*)"/g)].map((m) => m[1]);
    expect(classNames.some((c) => /\bmax-w-feed\b/.test(c))).toBe(false);
  });
  it("main uses full width with page gutters", () => {
    expect(shell).toMatch(/<main[^>]*className="[^"]*w-full[^"]*px-6/);
  });
  it("the header bar also spans the full width", () => {
    expect(shell).toMatch(/<div className="w-full px-6 xl:px-8 h-14/);
  });
  it("does not reach for unbounded viewport widths that cause overflow", () => {
    // `w-screen` and a bare `w-[100vw]` ignore the scrollbar and overflow the
    // page. A CLAMPED use such as `min(400px, calc(100vw-2rem))` is the opposite
    // — it keeps a floating panel inside the viewport — so only the unbounded
    // forms are banned.
    expect(App).not.toContain("w-screen");
    expect(App).not.toMatch(/w-\[100vw\]/);
    expect(App).not.toMatch(/width:\s*100vw/);
    for (const m of App.matchAll(/100vw/g)) {
      const ctx = App.slice(Math.max(0, m.index - 40), m.index + 10);
      expect(ctx).toMatch(/min\(|calc\(/);
    }
  });
  it("keeps a reading-width cap only where line length matters (the reader)", () => {
    const reader = App.slice(App.indexOf("function Reader"), App.indexOf("function AskWidget"));
    expect(reader).toContain("max-w-article");
  });
});

describe("write workspace (§4-§7)", () => {
  const write = App.slice(App.indexOf("function MyBlog"), App.indexOf("function Following"));
  it("is not constrained to the old article column", () => {
    expect(write).not.toContain("max-w-article");
  });
  it("lays out a main column beside a secondary rail on desktop", () => {
    expect(write).toContain("lg:flex-row");
    expect(write).toContain("flex-1 min-w-0");
  });
  it("gives the editor a tall min-height in both relative and absolute terms", () => {
    expect(write).toMatch(/min-h-\[60vh\]/);
    expect(write).toMatch(/\[min-height:560px\]/);
  });
  it("uses the shared MarkdownEditor rather than a bare textarea", () => {
    expect(write).toContain("<MarkdownEditor");
  });
  it("has exactly one Publish control", () => {
    expect(write.match(/data-testid="publish"/g)).toHaveLength(1);
  });
  it("places Publish after the tags field in the flow", () => {
    expect(write.indexOf('data-testid="post-tags"'))
      .toBeLessThan(write.indexOf('data-testid="publish"'));
  });
  it("keeps Your Posts discoverable but secondary in a rail", () => {
    expect(write).toContain('data-testid="your-posts-rail"');
    expect(write).toContain('data-testid="view-your-posts"');
  });
  it("supports tags on publish", () => {
    expect(write).toContain("createPost(title, content, tagList)");
  });
});

describe("routed ask workspace (§22)", () => {
  const ask = App.slice(App.indexOf("function AskPeople"), App.indexOf("function ProfileSettings"));
  it("puts the selector and chat side by side on desktop, each half", () => {
    expect(ask).toContain("lg:flex-row");
    expect(ask.match(/lg:w-1\/2/g).length).toBeGreaterThanOrEqual(2);
  });
  it("uses viewport height so both panes are usable", () => {
    expect(ask).toMatch(/h-\[calc\(100vh-/);
  });
  it("uses real checkboxes for multi-select", () => {
    expect(ask).toContain('type="checkbox"');
  });
  it("shows a selected count", () => {
    expect(ask).toContain('data-testid="selected-count"');
  });
  it("snapshots the scope for each question", () => {
    expect(ask).toContain("buildScope(selected)");
    expect(ask).toContain("scope }");
  });
  it("reuses the shared ChatShell rather than a bespoke thread", () => {
    expect(ask).toContain("<ChatShell");
  });
});

describe("group ask reuses the same shell (§32)", () => {
  const grp = App.slice(App.indexOf("function GroupChatPanel"));
  it("renders through ChatShell", () => {
    expect(grp.slice(0, 2500)).toContain("<ChatShell");
  });
  it("labels the scope as a group", () => {
    expect(grp.slice(0, 2500)).toContain('kind: "group"');
  });
});

describe("profile (§19-§21)", () => {
  const prof = App.slice(App.indexOf("function ProfileSettings"));
  it("lets the user edit username and email", () => {
    expect(prof).toContain('data-testid="username-input"');
    expect(prof).toContain('data-testid="email-input"');
  });
  it("shows username availability feedback", () => {
    expect(prof).toContain('data-testid="username-availability"');
  });
  it("exposes NO password controls anywhere in the profile", () => {
    for (const banned of ["Current password", "New password", "Confirm password",
                          "Change password", 'type="password"']) {
      expect(prof).not.toContain(banned);
    }
  });
  it("never sends a client-chosen user_id when mutating the profile", () => {
    expect(prof).not.toMatch(/updateUsername\([^)]*user_id/);
    expect(prof).not.toMatch(/updateEmail\([^)]*user_id/);
  });
});

describe("api client", () => {
  const api = read("api.js");
  it("targets the authenticated /api/me profile endpoints", () => {
    expect(api).toContain('"/api/me/profile"');
    expect(api).toContain('"/api/me/username"');
    expect(api).toContain('"/api/me/email"');
  });
  it("refreshes the stored token after an email change", () => {
    expect(api).toMatch(/updateEmail[\s\S]*setToken\(r\.token\)/);
  });
  it("passes scope_labels as display metadata on ask", () => {
    expect(api).toContain("scope_labels");
  });
  it("has exactly one askGroup implementation", () => {
    expect(api.match(/function askGroup/g)).toHaveLength(1);
  });
});
