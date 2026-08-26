/**
 * Safe Markdown renderer.
 *
 * SECURITY POSTURE — unchanged and deliberate.
 * This renderer builds React ELEMENTS. It never calls dangerouslySetInnerHTML and
 * never injects an HTML string, so author content cannot introduce markup or
 * script: every span of text ends up as a React text child, which React escapes.
 *
 * UNDERLINE (§12). Underline is not CommonMark. Rather than enabling raw HTML —
 * which would hand every author an XSS primitive — exactly one marker, <u>…</u>,
 * is recognised by the inline tokenizer and emitted as a React <u> element. The
 * inner text is still escaped as a React text child. No other tag is recognised;
 * anything else stays literal text. General XSS protection is therefore not
 * weakened in any way, and no raw-HTML mode exists to be turned on.
 */

// Inline spans, longest/most-specific first so ** wins over *.
const INLINE = [
  { re: /`([^`]+)`/,                    tag: "code",   cls: "px-1.5 py-0.5 rounded bg-cream border border-line text-[0.9em] font-mono" },
  { re: /\*\*([^*]+)\*\*/,              tag: "strong", cls: "font-semibold" },
  { re: /__([^_]+)__/,                  tag: "strong", cls: "font-semibold" },
  { re: /\*([^*]+)\*/,                  tag: "em",     cls: "italic" },
  { re: /_([^_]+)_/,                    tag: "em",     cls: "italic" },
  { re: /<u>([\s\S]*?)<\/u>/,           tag: "u",      cls: "underline" },
];
const LINK = /\[([^\]]+)\]\(([^)\s]+)\)/;

// Only these schemes may become an href. `javascript:` and `data:` are rejected,
// so a crafted link cannot execute script.
const SAFE_HREF = /^(https?:\/\/|mailto:|\/|#)/i;

function safeHref(raw) {
  const href = (raw || "").trim();
  return SAFE_HREF.test(href) ? href : null;
}

/** Tokenize one line of inline Markdown into React children. */
export function renderInline(text, keyPrefix = "i") {
  const src = String(text ?? "");
  if (!src) return [src];

  // Links first: their label may itself contain inline formatting.
  const lm = src.match(LINK);
  if (lm) {
    const [full, label, url] = lm;
    const before = src.slice(0, lm.index);
    const after = src.slice(lm.index + full.length);
    const href = safeHref(url);
    const node = href ? (
      <a key={`${keyPrefix}-a`} href={href} target="_blank" rel="noopener noreferrer"
         className="text-accent underline underline-offset-2 hover:text-accent2">
        {renderInline(label, `${keyPrefix}-al`)}
      </a>
    ) : (
      // Unsafe scheme: render the original text, never an anchor.
      <span key={`${keyPrefix}-a`}>{full}</span>
    );
    return [...renderInline(before, `${keyPrefix}-b`), node,
            ...renderInline(after, `${keyPrefix}-c`)];
  }

  for (let i = 0; i < INLINE.length; i++) {
    const { re, tag: Tag, cls } = INLINE[i];
    const m = src.match(re);
    if (!m) continue;
    const before = src.slice(0, m.index);
    const after = src.slice(m.index + m[0].length);
    const inner = Tag === "code"
      ? m[1]                                   // code spans are literal
      : renderInline(m[1], `${keyPrefix}-in${i}`);
    return [
      ...renderInline(before, `${keyPrefix}-b${i}`),
      <Tag key={`${keyPrefix}-t${i}`} className={cls}>{inner}</Tag>,
      ...renderInline(after, `${keyPrefix}-a${i}`),
    ];
  }
  return [src];
}

/**
 * Block-level render. Supports H1-H3, paragraphs, bullets, numbered lists,
 * blockquotes, fenced code blocks, horizontal rules, and every inline span.
 */
export function renderMarkdown(md) {
  const lines = String(md ?? "").split("\n");
  const out = [];
  let para = [], ul = [], ol = [], quote = [], code = null, codeLang = "";

  const key = () => `b${out.length}`;
  const flushPara = () => {
    if (para.length) {
      out.push(<p key={key()}>{renderInline(para.join(" "), key())}</p>);
      para = [];
    }
  };
  const flushUl = () => {
    if (ul.length) {
      out.push(<ul key={key()}>{ul.map((li, i) =>
        <li key={i}>{renderInline(li, `${key()}-${i}`)}</li>)}</ul>);
      ul = [];
    }
  };
  const flushOl = () => {
    if (ol.length) {
      out.push(<ol key={key()} className="list-decimal pl-6 mb-5">{ol.map((li, i) =>
        <li key={i} className="mb-2">{renderInline(li, `${key()}-${i}`)}</li>)}</ol>);
      ol = [];
    }
  };
  const flushQuote = () => {
    if (quote.length) {
      out.push(
        <blockquote key={key()}
          className="border-l-4 border-line pl-4 italic text-soft my-5">
          {renderInline(quote.join(" "), key())}
        </blockquote>);
      quote = [];
    }
  };
  const flushAll = () => { flushPara(); flushUl(); flushOl(); flushQuote(); };

  for (const raw of lines) {
    const line = raw.trimEnd();

    // Fenced code block: contents are literal, never re-parsed.
    const fence = line.match(/^```\s*(\w+)?\s*$/);
    if (fence) {
      if (code === null) { flushAll(); code = []; codeLang = fence[1] || ""; }
      else {
        out.push(
          <pre key={key()}
            className="bg-cream border border-line rounded-lg p-4 overflow-x-auto my-5 text-[0.9rem]">
            <code data-lang={codeLang} className="font-mono">{code.join("\n")}</code>
          </pre>);
        code = null; codeLang = "";
      }
      continue;
    }
    if (code !== null) { code.push(raw); continue; }

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line.trim())) {
      flushAll(); out.push(<hr key={key()} className="my-8 border-line" />); continue;
    }
    if (/^#\s+/.test(line))   { flushAll(); out.push(<h1 key={key()}>{renderInline(line.replace(/^#\s+/, ""), key())}</h1>); continue; }
    if (/^##\s+/.test(line))  { flushAll(); out.push(<h2 key={key()}>{renderInline(line.replace(/^##\s+/, ""), key())}</h2>); continue; }
    if (/^###\s+/.test(line)) { flushAll(); out.push(<h3 key={key()}>{renderInline(line.replace(/^###\s+/, ""), key())}</h3>); continue; }
    if (/^>\s?/.test(line))   { flushPara(); flushUl(); flushOl(); quote.push(line.replace(/^>\s?/, "")); continue; }
    if (/^[-*]\s+/.test(line)) { flushPara(); flushOl(); flushQuote(); ul.push(line.replace(/^[-*]\s+/, "")); continue; }
    if (/^\d+\.\s+/.test(line)) { flushPara(); flushUl(); flushQuote(); ol.push(line.replace(/^\d+\.\s+/, "")); continue; }
    if (line.trim() === "")   { flushAll(); continue; }
    flushUl(); flushOl(); flushQuote(); para.push(line);
  }
  if (code !== null && code.length) {          // unterminated fence
    out.push(<pre key={key()} className="bg-cream border border-line rounded-lg p-4 overflow-x-auto my-5">
      <code className="font-mono">{code.join("\n")}</code></pre>);
  }
  flushAll();
  return out;
}

export const __test__ = { safeHref, SAFE_HREF };
