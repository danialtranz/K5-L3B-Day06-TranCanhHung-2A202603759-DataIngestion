import { useEffect, useRef, type FormEvent } from "react";
import type { ChatMessage, HitRecord, StrategyId } from "../types";
import { CitedAnswer } from "./CitedAnswer";

type Props = {
  messages: ChatMessage[];
  strategy: StrategyId;
  section: string;
  topK: number;
  busy: boolean;
  onStrategy: (s: StrategyId) => void;
  onSection: (s: string) => void;
  onTopK: (n: number) => void;
  onSend: (question: string) => void;
  onSelectMessageHits: (msg: ChatMessage) => void;
  onCiteClick: (hit: HitRecord) => void;
};

export function ChatPanel({
  messages,
  strategy,
  section,
  topK,
  busy,
  onStrategy,
  onSection,
  onTopK,
  onSend,
  onSelectMessageHits,
  onCiteClick,
}: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const q = inputRef.current?.value.trim() || "";
    if (!q || busy) return;
    onSend(q);
    if (inputRef.current) inputRef.current.value = "";
  }

  return (
    <section className="chat-panel">
      <header className="toolbar">
        <label>
          Strategy
          <select
            value={strategy}
            onChange={(e) => onStrategy(e.target.value as StrategyId)}
            disabled={busy}
          >
            <option value="A">A · fixed 500/80</option>
            <option value="B">B · heading/paragraph</option>
            <option value="C">C · B + section filter</option>
          </select>
        </label>
        <label>
          Section (C only)
          <input
            value={section}
            onChange={(e) => onSection(e.target.value)}
            placeholder="e.g. 5.3.1"
            disabled={busy || strategy !== "C"}
          />
        </label>
        <label>
          Top-k
          <input
            type="number"
            min={1}
            max={10}
            value={topK}
            onChange={(e) => onTopK(Number(e.target.value) || 3)}
            disabled={busy}
          />
        </label>
      </header>

      <div className="messages">
        {messages.length === 0 ? (
          <div className="intro-card">
            <p className="muted intro">
              Grounded RAG: retrieve top-k chunks, then LLM answers <strong>only</strong> from
              those passages with IEEE citations <span className="badge cite">[1]</span>. No
              session save.
            </p>
            <p className="hint">Try: “What are the ACID properties of a transaction?”</p>
          </div>
        ) : null}
        {messages.map((m) => (
          <article
            key={m.id}
            className={`bubble ${m.role}`}
            onClick={() => m.role === "assistant" && m.hits && onSelectMessageHits(m)}
          >
            <div className="bubble-meta">
              {m.role === "assistant" ? (
                <>
                  <span>{m.provider || "assistant"}</span>
                  {m.strategy ? <span>strategy {m.strategy}</span> : null}
                  {m.hits ? <span>{m.hits.length} sources</span> : null}
                  <span>IEEE</span>
                </>
              ) : (
                <span>you</span>
              )}
            </div>
            {m.role === "assistant" ? (
              <CitedAnswer
                content={m.content}
                hits={m.hits}
                references={m.references}
                onCiteClick={onCiteClick}
              />
            ) : (
              <pre className="bubble-body">{m.content}</pre>
            )}
          </article>
        ))}
        {busy ? <p className="muted">Retrieving chunks · streaming grounded answer…</p> : null}
        <div ref={bottomRef} />
      </div>

      <form className="composer" onSubmit={handleSubmit}>
        <textarea
          ref={inputRef}
          rows={3}
          placeholder="Ask the textbook… (Enter to send, Shift+Enter for newline)"
          disabled={busy}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSubmit(e);
            }
          }}
        />
        <button type="submit" disabled={busy}>
          Send
        </button>
      </form>
    </section>
  );
}
