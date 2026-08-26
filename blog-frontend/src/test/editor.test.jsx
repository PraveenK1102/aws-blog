import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { applyFormat, MarkdownToolbar, MarkdownEditor } from "../editor.jsx";

const f = (value, start, end, action) => applyFormat(value, start, end, action).value;

describe("formatting actions (§9)", () => {
  it("wraps a selection in bold without touching neighbours", () => {
    expect(f("keep this keep", 5, 9, "bold")).toBe("keep **this** keep");
  });
  it("wraps italic", () => expect(f("a b", 2, 3, "italic")).toBe("a *b*"));
  it("wraps underline with the narrow marker", () =>
    expect(f("a b", 2, 3, "underline")).toBe("a <u>b</u>"));
  it("wraps inline code", () => expect(f("a b", 2, 3, "code")).toBe("a `b`"));
  it("applies H1, H2, H3 as line prefixes", () => {
    expect(f("title", 0, 5, "h1")).toBe("# title");
    expect(f("title", 0, 5, "h2")).toBe("## title");
    expect(f("title", 0, 5, "h3")).toBe("### title");
  });
  it("replaces an existing block prefix rather than stacking it", () => {
    expect(f("# title", 0, 7, "h2")).toBe("## title");
    expect(f("- item", 0, 6, "number")).toBe("1. item");
  });
  it("returns to plain paragraph", () =>
    expect(f("## title", 0, 8, "paragraph")).toBe("title"));
  it("makes bulleted lists across multiple lines", () =>
    expect(f("one\ntwo", 0, 7, "bullet")).toBe("- one\n- two"));
  it("numbers lists sequentially", () =>
    expect(f("one\ntwo\nthree", 0, 13, "number")).toBe("1. one\n2. two\n3. three"));
  it("applies blockquote", () => expect(f("said", 0, 4, "quote")).toBe("> said"));
  it("creates a fenced code block", () =>
    expect(f("x = 1", 0, 5, "codeblock")).toBe("```\nx = 1\n```"));
  it("creates a link with the caret left on the url", () => {
    const r = applyFormat("label", 0, 5, "link");
    expect(r.value).toBe("[label](url)");
    expect(r.value.slice(r.start, r.end)).toBe("url");
  });
  it("inserts a horizontal rule", () =>
    expect(f("a", 1, 1, "hr")).toContain("---"));
  it("inserts placeholders when nothing is selected", () =>
    expect(f("", 0, 0, "bold")).toBe("**bold text**"));
  it("is a no-op for an unknown action", () =>
    expect(f("same", 0, 4, "nope")).toBe("same"));
  it("clamps out-of-range selections", () =>
    expect(f("ab", 99, 99, "bold")).toBe("ab**bold text**"));
});

describe("toolbar (§9/§37)", () => {
  it("exposes every required control with an accessible label", () => {
    render(<MarkdownToolbar onAction={() => {}} />);
    for (const label of ["Paragraph", "Heading 1", "Heading 2", "Heading 3",
      "Bold", "Italic", "Underline", "Bulleted list", "Numbered list",
      "Blockquote", "Inline code", "Code block", "Link", "Horizontal rule"]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
  });
  it("is exposed as a toolbar to assistive tech", () => {
    render(<MarkdownToolbar onAction={() => {}} />);
    expect(screen.getByRole("toolbar", { name: /formatting/i })).toBeInTheDocument();
  });
  it("fires the action for a clicked button", () => {
    const onAction = vi.fn();
    render(<MarkdownToolbar onAction={onAction} />);
    fireEvent.click(screen.getByLabelText("Bold"));
    expect(onAction).toHaveBeenCalledWith("bold");
  });
});

describe("shortcut help lives in exactly one place (§11)", () => {
  it("is collapsed by default and opens on click", () => {
    render(<MarkdownToolbar onAction={() => {}} />);
    expect(screen.queryByTestId("shortcut-help")).toBeNull();
    fireEvent.click(screen.getByTestId("shortcut-help-trigger"));
    expect(screen.getByTestId("shortcut-help")).toBeInTheDocument();
  });
  it("renders only one shortcut affordance", () => {
    render(<MarkdownToolbar onAction={() => {}} />);
    expect(screen.getAllByTestId("shortcut-help-trigger")).toHaveLength(1);
  });
});

describe("keyboard shortcuts (§10)", () => {
  function Harness() {
    const [v, setV] = require("react").useState("hello");
    return <MarkdownEditor value={v} onChange={setV} minHeightClass="min-h-[10px]" />;
  }
  it("Cmd/Ctrl+B bolds the selection", () => {
    const onChange = vi.fn();
    render(<MarkdownEditor value="hello" onChange={onChange} minHeightClass="" />);
    const ta = screen.getByTestId("post-editor");
    ta.setSelectionRange(0, 5);
    fireEvent.keyDown(ta, { key: "b", ctrlKey: true });
    expect(onChange).toHaveBeenCalledWith("**hello**");
  });
  it("Cmd/Ctrl+I italicises", () => {
    const onChange = vi.fn();
    render(<MarkdownEditor value="hi" onChange={onChange} minHeightClass="" />);
    const ta = screen.getByTestId("post-editor");
    ta.setSelectionRange(0, 2);
    fireEvent.keyDown(ta, { key: "i", metaKey: true });
    expect(onChange).toHaveBeenCalledWith("*hi*");
  });
  it("Cmd/Ctrl+U underlines", () => {
    const onChange = vi.fn();
    render(<MarkdownEditor value="hi" onChange={onChange} minHeightClass="" />);
    const ta = screen.getByTestId("post-editor");
    ta.setSelectionRange(0, 2);
    fireEvent.keyDown(ta, { key: "u", ctrlKey: true });
    expect(onChange).toHaveBeenCalledWith("<u>hi</u>");
  });
  it("Cmd/Ctrl+K links", () => {
    const onChange = vi.fn();
    render(<MarkdownEditor value="hi" onChange={onChange} minHeightClass="" />);
    const ta = screen.getByTestId("post-editor");
    ta.setSelectionRange(0, 2);
    fireEvent.keyDown(ta, { key: "k", ctrlKey: true });
    expect(onChange).toHaveBeenCalledWith("[hi](url)");
  });
  it("does not hijack unrelated shortcuts such as Cmd+S", () => {
    const onChange = vi.fn();
    render(<MarkdownEditor value="hi" onChange={onChange} minHeightClass="" />);
    fireEvent.keyDown(screen.getByTestId("post-editor"), { key: "s", metaKey: true });
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("editor sizing (§5)", () => {
  it("applies the tall min-height class it is given", () => {
    render(<MarkdownEditor value="" onChange={() => {}}
      minHeightClass="min-h-[60vh] [min-height:560px]" />);
    const ta = screen.getByTestId("post-editor");
    expect(ta.className).toContain("min-h-[60vh]");
    expect(ta.className).toContain("[min-height:560px]");
  });
});
