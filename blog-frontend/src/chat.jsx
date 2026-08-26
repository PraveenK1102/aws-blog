/**
 * Shared conversational primitives used by EVERY ask surface — routed multi-user
 * ask, group ask and single-profile ask — so there is one chat implementation
 * rather than three.
 *
 * Design intent (§26): a conversation, not a comment thread. User turns are
 * compact and right-aligned; assistant turns are spacious, unbordered and full
 * width; citations hang quietly beneath the answer; the composer sits at the
 * bottom of the panel.
 */
import { useEffect, useRef } from "react";
import { renderMarkdown } from "./markdown.jsx";

/**
 * Per-question scope line (§24).
 * <=5 scope entries  -> show them all
 *  >5 scope entries  -> show the first five, then "+N more" where N = count-5
 */
export function ScopeSummary({ scope }) {
  if (!scope) return null;                       // legacy message: render nothing
  if (scope.kind === "group") {
    return (
      <div className="text-[11px] text-faint mb-1" data-testid="scope-summary">
        Group: {scope.group_name || scope.group_id}
        {typeof scope.member_count === "number" ? ` · ${scope.member_count} members` : ""}
      </div>
    );
  }
  const labels = scope.labels || [];
  const total = typeof scope.count === "number" ? scope.count : labels.length;
  if (!total) return null;
  const shown = labels.slice(0, 5);
  const extra = total - shown.length;
  return (
    <div className="text-[11px] text-faint mb-1" data-testid="scope-summary">
      Asked across: {shown.join(" · ")}{extra > 0 ? ` +${extra} more` : ""}
    </div>
  );
}

export function UserMessage({ text, scope }) {
  return (
    <div className="flex flex-col items-end mb-6" data-testid="user-message">
      <ScopeSummary scope={scope} />
      <div className="max-w-[80%] bg-cream border border-line rounded-2xl rounded-br-sm px-4 py-2.5 text-[15px] whitespace-pre-wrap">
        {text}
      </div>
    </div>
  );
}

export function CitationList({ citations }) {
  if (!citations || citations.length === 0) return null;
  return (
    <div className="mt-4 pt-3 border-t border-line" data-testid="citations">
      <div className="text-[11px] uppercase tracking-wide text-faint mb-2">
        Sources
      </div>
      <ul className="flex flex-col gap-1">
        {citations.map((c, i) => {
          const label = typeof c === "string" ? c : (c.title || c.post_id || "Source");
          const writer = typeof c === "object" && c.writer ? ` · ${c.writer}` : "";
          return (
            <li key={i} className="text-[13px] text-soft">
              <span className="text-faint mr-1.5">{i + 1}.</span>{label}
              <span className="text-faint">{writer}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function AssistantMessage({ text, citations, pending, error }) {
  return (
    <div className="mb-8" data-testid="assistant-message">
      {pending ? (
        <div className="flex items-center gap-2 text-faint text-sm" data-testid="chat-loading">
          <span className="w-1.5 h-1.5 rounded-full bg-faint animate-pulse" />
          Thinking…
        </div>
      ) : error ? (
        <div className="text-err text-sm" role="alert">{error}</div>
      ) : (
        <>
          <div className="prose prose-chat max-w-none">{renderMarkdown(text)}</div>
          <CitationList citations={citations} />
        </>
      )}
    </div>
  );
}

export function MessageList({ messages, pending, error }) {
  const endRef = useRef(null);
  useEffect(() => {
    // Guarded: scrollIntoView is missing in some environments (jsdom), and a
    // telemetry-free convenience must never break the transcript.
    const el = endRef.current;
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ block: "end" });
    }
  }, [messages.length, pending]);
  return (
    <div className="flex-1 overflow-y-auto px-5 sm:px-8 py-6" data-testid="message-list">
      {messages.length === 0 && !pending && (
        <div className="h-full grid place-items-center text-faint text-sm">
          Ask anything about what these writers have published.
        </div>
      )}
      {messages.map((m, i) =>
        m.role === "user"
          ? <UserMessage key={i} text={m.text} scope={m.scope} />
          : <AssistantMessage key={i} text={m.text} citations={m.citations} />
      )}
      {pending && <AssistantMessage pending />}
      {error && !pending && <AssistantMessage error={error} />}
      <div ref={endRef} />
    </div>
  );
}

/**
 * Composer (§29). Multiline, sticky at the bottom of the panel.
 * Enter sends, Shift+Enter inserts a newline — matching the prior single-line
 * Enter-to-send behaviour while now allowing real multi-line questions.
 */
export function ChatComposer({ value, onChange, onSend, disabled, placeholder }) {
  const ref = useRef(null);
  function keyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!disabled && value.trim()) onSend();
    }
  }
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, [value]);
  return (
    <div className="border-t border-line bg-white px-4 sm:px-6 py-3">
      <div className="flex items-end gap-2">
        <label className="sr-only" htmlFor="chat-composer">Ask a question</label>
        <textarea
          id="chat-composer" ref={ref} rows={1} value={value}
          onChange={(e) => onChange(e.target.value)} onKeyDown={keyDown}
          placeholder={placeholder || "Ask anything…"}
          className="flex-1 resize-none outline-none border border-line rounded-2xl px-4 py-3 text-[15px] leading-6 max-h-[200px] focus:border-accent"
        />
        <button
          type="button" onClick={onSend} disabled={disabled || !value.trim()}
          data-testid="chat-send"
          className="bg-ink text-white font-medium rounded-full px-5 py-3 hover:bg-black disabled:opacity-40 transition shrink-0"
        >
          {disabled ? "…" : "Send"}
        </button>
      </div>
      <div className="text-[11px] text-faint mt-1.5 px-1">
        Enter to send · Shift+Enter for a new line
      </div>
    </div>
  );
}

/**
 * The conversation surface. Fills the height it is given and scrolls internally,
 * so the composer stays reachable without covering the transcript.
 */
export function ChatShell({ header, messages, pending, error, input, setInput,
                           onSend, placeholder }) {
  return (
    <section className="flex flex-col h-full min-h-0 bg-white" data-testid="chat-shell">
      {header && (
        <div className="border-b border-line px-5 sm:px-8 py-3 shrink-0">{header}</div>
      )}
      <MessageList messages={messages} pending={pending} error={error} />
      <ChatComposer value={input} onChange={setInput} onSend={onSend}
                    disabled={pending} placeholder={placeholder} />
    </section>
  );
}

/** Build the display scope snapshot sent with a question and shown above it. */
export function buildScope(selected) {
  return {
    kind: "users",
    tenant_ids: selected.map((s) => s.tenant_id),
    labels: selected.map((s) => s.display_name),
    count: selected.length,
  };
}
