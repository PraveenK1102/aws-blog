import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ScopeSummary, UserMessage, AssistantMessage, CitationList,
         MessageList, ChatComposer, buildScope } from "../chat.jsx";

const users = (n) => Array.from({ length: n }, (_, i) => ({
  tenant_id: `t${i}`, display_name: `User ${i}` }));

describe("per-question scope line (§24)", () => {
  it("shows all names when there are 5 or fewer", () => {
    render(<ScopeSummary scope={{ kind: "users", count: 3,
      labels: ["Kavin Raj", "Divya Rajan", "Nandhini Kumar"] }} />);
    const el = screen.getByTestId("scope-summary");
    expect(el).toHaveTextContent("Asked across: Kavin Raj · Divya Rajan · Nandhini Kumar");
    expect(el).not.toHaveTextContent("more");
  });
  it("shows exactly 5 names with no overflow at the boundary", () => {
    const labels = users(5).map((u) => u.display_name);
    render(<ScopeSummary scope={{ kind: "users", count: 5, labels }} />);
    expect(screen.getByTestId("scope-summary")).not.toHaveTextContent("more");
  });
  it("shows first five then +N more when over 5", () => {
    const labels = users(9).map((u) => u.display_name);
    render(<ScopeSummary scope={{ kind: "users", count: 9, labels }} />);
    const el = screen.getByTestId("scope-summary");
    expect(el).toHaveTextContent("User 0 · User 1 · User 2 · User 3 · User 4 +4 more");
    expect(el).not.toHaveTextContent("User 5");
  });
  it("computes +N from the true count, not the truncated label list", () => {
    render(<ScopeSummary scope={{ kind: "users", count: 12,
      labels: users(12).map((u) => u.display_name) }} />);
    expect(screen.getByTestId("scope-summary")).toHaveTextContent("+7 more");
  });
  it("renders a group scope label (§32)", () => {
    render(<ScopeSummary scope={{ kind: "group", group_name: "GenAI Job Search",
      member_count: 8 }} />);
    expect(screen.getByTestId("scope-summary"))
      .toHaveTextContent("Group: GenAI Job Search · 8 members");
  });
  it("omits the member count when it is not cheaply available", () => {
    render(<ScopeSummary scope={{ kind: "group", group_name: "Team" }} />);
    expect(screen.getByTestId("scope-summary")).toHaveTextContent("Group: Team");
    expect(screen.getByTestId("scope-summary")).not.toHaveTextContent("members");
  });
  it("renders nothing for a legacy message with no scope", () => {
    const { container } = render(<ScopeSummary scope={undefined} />);
    expect(container.firstChild).toBeNull();
  });
});

describe("scope snapshot immutability (§25)", () => {
  it("buildScope captures ids, labels and count at send time", () => {
    const s = buildScope(users(3));
    expect(s).toEqual({ kind: "users", tenant_ids: ["t0", "t1", "t2"],
      labels: ["User 0", "User 1", "User 2"], count: 3 });
  });
  it("a later selector change cannot mutate an existing snapshot", () => {
    const selected = users(2);
    const snap = buildScope(selected);
    selected.push({ tenant_id: "t9", display_name: "Late Arrival" });
    expect(snap.count).toBe(2);
    expect(snap.labels).not.toContain("Late Arrival");
  });
  it("a rendered historical message keeps its own scope", () => {
    const messages = [
      { role: "user", text: "Q1", scope: { kind: "users", count: 2, labels: ["A", "B"] } },
      { role: "assistant", text: "A1", citations: [] },
      { role: "user", text: "Q2", scope: { kind: "users", count: 2, labels: ["C", "D"] } },
    ];
    render(<MessageList messages={messages} />);
    const scopes = screen.getAllByTestId("scope-summary");
    expect(scopes[0]).toHaveTextContent("A · B");
    expect(scopes[1]).toHaveTextContent("C · D");
  });
  it("legacy messages without a snapshot still render", () => {
    render(<MessageList messages={[{ role: "user", text: "old question" }]} />);
    expect(screen.getByTestId("user-message")).toHaveTextContent("old question");
    expect(screen.queryByTestId("scope-summary")).toBeNull();
  });
});

describe("assistant presentation (§30/§31)", () => {
  it("renders markdown in the answer", () => {
    const { container } = render(
      <AssistantMessage text={"# Title\n\n**bold**"} citations={[]} />);
    expect(container.querySelector("h1")).toHaveTextContent("Title");
    expect(container.querySelector("strong")).toHaveTextContent("bold");
  });
  it("renders citations below the answer", () => {
    render(<AssistantMessage text="answer" citations={[{ title: "Post A" }, { title: "Post B" }]} />);
    const c = screen.getByTestId("citations");
    expect(c).toHaveTextContent("Post A");
    expect(c).toHaveTextContent("Post B");
  });
  it("shows attributed writers when present", () => {
    render(<CitationList citations={[{ title: "P", writer: "Kavin Raj" }]} />);
    expect(screen.getByTestId("citations")).toHaveTextContent("Kavin Raj");
  });
  it("renders nothing when there are no citations", () => {
    const { container } = render(<CitationList citations={[]} />);
    expect(container.firstChild).toBeNull();
  });
  it("shows a loading state, not an endless blank", () => {
    render(<AssistantMessage pending />);
    expect(screen.getByTestId("chat-loading")).toHaveTextContent("Thinking…");
  });
  it("surfaces an error as an alert", () => {
    render(<AssistantMessage error="Provider unavailable" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Provider unavailable");
  });
});

describe("composer (§29)", () => {
  it("sends on Enter", () => {
    const onSend = vi.fn();
    render(<ChatComposer value="hi" onChange={() => {}} onSend={onSend} />);
    fireEvent.keyDown(screen.getByLabelText("Ask a question"), { key: "Enter" });
    expect(onSend).toHaveBeenCalled();
  });
  it("does not send on Shift+Enter", () => {
    const onSend = vi.fn();
    render(<ChatComposer value="hi" onChange={() => {}} onSend={onSend} />);
    fireEvent.keyDown(screen.getByLabelText("Ask a question"),
      { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });
  it("disables send for empty or whitespace input", () => {
    render(<ChatComposer value="   " onChange={() => {}} onSend={() => {}} />);
    expect(screen.getByTestId("chat-send")).toBeDisabled();
  });
  it("disables send while a request is in flight", () => {
    render(<ChatComposer value="hi" onChange={() => {}} onSend={() => {}} disabled />);
    expect(screen.getByTestId("chat-send")).toBeDisabled();
  });
  it("does not send while disabled even on Enter", () => {
    const onSend = vi.fn();
    render(<ChatComposer value="hi" onChange={() => {}} onSend={onSend} disabled />);
    fireEvent.keyDown(screen.getByLabelText("Ask a question"), { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });
  it("is a multiline textarea", () => {
    render(<ChatComposer value="" onChange={() => {}} onSend={() => {}} />);
    expect(screen.getByLabelText("Ask a question").tagName).toBe("TEXTAREA");
  });
});

describe("conversation, not comments (§26)", () => {
  it("assistant answers are not wrapped in bordered comment cards", () => {
    const { container } = render(<AssistantMessage text="answer" citations={[]} />);
    expect(container.firstChild.className).not.toMatch(/\bborder\b/);
  });
  it("does not label every message with an author name", () => {
    render(<MessageList messages={[{ role: "assistant", text: "hi", citations: [] }]} />);
    expect(screen.queryByText(/^Assistant$/)).toBeNull();
  });
});
