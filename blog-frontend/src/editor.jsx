/**
 * Markdown writing surface: formatting toolbar + keyboard shortcuts.
 *
 * Markdown remains the source format (§8) — the toolbar only inserts Markdown
 * syntax into the same textarea. No rich-text document model, no editor
 * framework, and S3 continues to store exactly what the author typed.
 *
 * Every operation is selection-aware: it wraps the current selection, or inserts
 * a marker at the caret, and never rewrites unrelated text.
 */
import { useRef, useState } from "react";

/** Apply a formatting action to a textarea value. Pure, so it is directly testable. */
export function applyFormat(value, selStart, selEnd, action) {
  const v = String(value ?? "");
  const start = Math.max(0, Math.min(selStart ?? 0, v.length));
  const end = Math.max(start, Math.min(selEnd ?? start, v.length));
  const sel = v.slice(start, end);

  const wrap = (left, right = left, placeholder = "") => {
    const inner = sel || placeholder;
    const next = v.slice(0, start) + left + inner + right + v.slice(end);
    return { value: next, start: start + left.length,
             end: start + left.length + inner.length };
  };

  // Line-prefix actions operate on whole lines containing the selection.
  const linePrefix = (prefix, numbered = false) => {
    const lineStart = v.lastIndexOf("\n", start - 1) + 1;
    const lineEndIdx = v.indexOf("\n", end);
    const lineEnd = lineEndIdx === -1 ? v.length : lineEndIdx;
    const block = v.slice(lineStart, lineEnd) || "";
    const lines = block.split("\n");
    const cleaned = lines.map((l) =>
      l.replace(/^(#{1,6}\s+|[-*]\s+|\d+\.\s+|>\s?)/, ""));
    const applied = cleaned.map((l, i) => (numbered ? `${i + 1}. ` : prefix) + l);
    const nextBlock = applied.join("\n");
    const next = v.slice(0, lineStart) + nextBlock + v.slice(lineEnd);
    return { value: next, start: lineStart, end: lineStart + nextBlock.length };
  };

  switch (action) {
    case "bold":       return wrap("**", "**", "bold text");
    case "italic":     return wrap("*", "*", "italic text");
    case "underline":  return wrap("<u>", "</u>", "underlined text");
    case "code":       return wrap("`", "`", "code");
    case "paragraph":  return linePrefix("");
    case "h1":         return linePrefix("# ");
    case "h2":         return linePrefix("## ");
    case "h3":         return linePrefix("### ");
    case "bullet":     return linePrefix("- ");
    case "number":     return linePrefix("", true);
    case "quote":      return linePrefix("> ");
    case "codeblock": {
      const inner = sel || "code";
      const next = v.slice(0, start) + "```\n" + inner + "\n```" + v.slice(end);
      return { value: next, start: start + 4, end: start + 4 + inner.length };
    }
    case "link": {
      const label = sel || "link text";
      const next = v.slice(0, start) + `[${label}](url)` + v.slice(end);
      // Leave the caret on `url` so the author can type straight over it.
      const u = start + label.length + 3;
      return { value: next, start: u, end: u + 3 };
    }
    case "hr": {
      const pre = start > 0 && v[start - 1] !== "\n" ? "\n" : "";
      const ins = pre + "\n---\n\n";
      const next = v.slice(0, start) + ins + v.slice(end);
      return { value: next, start: start + ins.length, end: start + ins.length };
    }
    default:           return { value: v, start, end };
  }
}

const GROUPS = [
  [["paragraph", "¶", "Paragraph"], ["h1", "H1", "Heading 1"],
   ["h2", "H2", "Heading 2"], ["h3", "H3", "Heading 3"]],
  [["bold", "B", "Bold"], ["italic", "I", "Italic"], ["underline", "U", "Underline"]],
  [["bullet", "•", "Bulleted list"], ["number", "1.", "Numbered list"],
   ["quote", "❝", "Blockquote"]],
  [["code", "</>", "Inline code"], ["codeblock", "{ }", "Code block"],
   ["link", "🔗", "Link"], ["hr", "—", "Horizontal rule"]],
];

const SHORTCUTS = [
  ["Cmd/Ctrl + B", "Bold"], ["Cmd/Ctrl + I", "Italic"],
  ["Cmd/Ctrl + U", "Underline"], ["Cmd/Ctrl + K", "Link"],
];

/** One subtle shortcut affordance (§11) — never repeated across the UI. */
function ShortcutHelp() {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative ml-auto">
      <button type="button" onClick={() => setOpen((o) => !o)}
        aria-expanded={open} aria-haspopup="dialog"
        className="text-faint hover:text-ink text-xs px-2 py-1 rounded"
        data-testid="shortcut-help-trigger">
        ⌨ Shortcuts
      </button>
      {open && (
        <div role="dialog" aria-label="Keyboard shortcuts" data-testid="shortcut-help"
          className="absolute right-0 top-full mt-1 z-20 bg-white border border-line rounded-lg shadow-lg p-3 w-56">
          {SHORTCUTS.map(([k, l]) => (
            <div key={k} className="flex justify-between text-xs py-1">
              <span className="text-soft">{l}</span>
              <kbd className="text-faint font-mono">{k}</kbd>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function MarkdownToolbar({ onAction }) {
  return (
    <div role="toolbar" aria-label="Text formatting" data-testid="md-toolbar"
      className="flex flex-wrap items-center gap-1 border border-line rounded-lg px-2 py-1.5 overflow-x-auto">
      {GROUPS.map((group, gi) => (
        <span key={gi} className="flex items-center gap-1">
          {gi > 0 && <span aria-hidden className="w-px h-5 bg-line mx-1" />}
          {group.map(([action, label, title]) => (
            <button key={action} type="button" title={title} aria-label={title}
              data-action={action} onClick={() => onAction(action)}
              className="min-w-[30px] h-7 px-1.5 rounded text-xs text-soft hover:bg-cream hover:text-ink focus:outline-none focus:ring-2 focus:ring-accent">
              {label}
            </button>
          ))}
        </span>
      ))}
      <ShortcutHelp />
    </div>
  );
}

/** The full editor: toolbar + tall textarea, wired for selection and shortcuts. */
export function MarkdownEditor({ value, onChange, placeholder, minHeightClass }) {
  const ref = useRef(null);

  function run(action) {
    const el = ref.current;
    const res = applyFormat(value, el?.selectionStart ?? value.length,
                            el?.selectionEnd ?? value.length, action);
    onChange(res.value);
    requestAnimationFrame(() => {
      if (!el) return;
      el.focus();
      el.setSelectionRange(res.start, res.end);
    });
  }

  function keyDown(e) {
    if (!(e.metaKey || e.ctrlKey) || e.altKey) return;
    const map = { b: "bold", i: "italic", u: "underline", k: "link" };
    const action = map[e.key.toLowerCase()];
    if (!action) return;          // never hijack unrelated browser shortcuts
    e.preventDefault();
    run(action);
  }

  return (
    <div className="flex flex-col gap-3">
      <MarkdownToolbar onAction={run} />
      <label className="sr-only" htmlFor="post-body">Post content</label>
      <textarea
        id="post-body" ref={ref} value={value} data-testid="post-editor"
        onChange={(e) => onChange(e.target.value)} onKeyDown={keyDown}
        placeholder={placeholder || "Tell your story…"}
        className={`w-full outline-none resize-y text-[1.05rem] leading-8 placeholder:text-faint/60 border border-line rounded-xl p-5 focus:border-accent ${minHeightClass}`}
      />
    </div>
  );
}
