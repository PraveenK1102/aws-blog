import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { renderMarkdown, renderInline } from "../markdown.jsx";

const R = (md) => render(<div data-testid="out">{renderMarkdown(md)}</div>);

describe("markdown blocks (§13)", () => {
  it("renders H1, H2, H3", () => {
    R("# One\n\n## Two\n\n### Three");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("One");
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Two");
    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent("Three");
  });
  it("renders paragraphs", () => {
    const { container } = R("first para\n\nsecond para");
    expect(container.querySelectorAll("p")).toHaveLength(2);
  });
  it("renders bulleted lists", () => {
    const { container } = R("- alpha\n- beta");
    expect(container.querySelectorAll("ul li")).toHaveLength(2);
  });
  it("renders numbered lists", () => {
    const { container } = R("1. alpha\n2. beta\n3. gamma");
    expect(container.querySelectorAll("ol li")).toHaveLength(3);
  });
  it("renders blockquotes", () => {
    const { container } = R("> quoted wisdom");
    expect(container.querySelector("blockquote")).toHaveTextContent("quoted wisdom");
  });
  it("renders code blocks literally", () => {
    const { container } = R("```js\nconst a = **not bold**;\n```");
    const code = container.querySelector("pre code");
    expect(code.textContent).toContain("**not bold**");
  });
  it("renders horizontal rules", () => {
    const { container } = R("above\n\n---\n\nbelow");
    expect(container.querySelector("hr")).toBeTruthy();
  });
});

describe("markdown inline (§13)", () => {
  it("renders bold, italic and inline code", () => {
    const { container } = R("**bold** and *italic* and `code`");
    expect(container.querySelector("strong")).toHaveTextContent("bold");
    expect(container.querySelector("em")).toHaveTextContent("italic");
    expect(container.querySelector("code")).toHaveTextContent("code");
  });
  it("renders underline via the narrow <u> marker", () => {
    const { container } = R("plain <u>underlined</u> tail");
    expect(container.querySelector("u")).toHaveTextContent("underlined");
  });
  it("renders safe links", () => {
    const { container } = R("[label](https://example.com)");
    const a = container.querySelector("a");
    expect(a).toHaveAttribute("href", "https://example.com");
    expect(a).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });
});

describe("markdown safety (§12/§35)", () => {
  it("never injects raw HTML — script tags stay literal text", () => {
    const { container } = R("<script>window.__pwned=1</script>");
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("<script>");
    expect(window.__pwned).toBeUndefined();
  });
  it("does not honour arbitrary tags such as <img onerror>", () => {
    const { container } = R('<img src=x onerror="window.__pwned=1">');
    expect(container.querySelector("img")).toBeNull();
    expect(window.__pwned).toBeUndefined();
  });
  it("rejects javascript: links", () => {
    const { container } = R("[click](javascript:alert(1))");
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toContain("javascript:alert(1)");
  });
  it("rejects data: links", () => {
    const { container } = R("[x](data:text/html;base64,PHNjcmlwdD4=)");
    expect(container.querySelector("a")).toBeNull();
  });
  it("allows only <u> and never a second tag such as <b>", () => {
    const { container } = R("<b>not bold</b> <u>yes</u>");
    expect(container.querySelector("b")).toBeNull();
    expect(container.querySelector("u")).toBeTruthy();
    expect(container.textContent).toContain("<b>");
  });
  it("escapes html inside an underline marker", () => {
    const { container } = R("<u><script>x</script></u>");
    expect(container.querySelector("script")).toBeNull();
  });
});

describe("renderInline", () => {
  it("returns the raw string when nothing matches", () => {
    expect(renderInline("plain")).toEqual(["plain"]);
  });
  it("handles empty input", () => {
    expect(renderInline("")).toEqual([""]);
  });
});
