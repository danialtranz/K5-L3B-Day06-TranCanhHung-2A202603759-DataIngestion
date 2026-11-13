import { useEffect, useMemo, useRef, useState } from "react";
import { getHealth, getMeta, postChatStream } from "./api";
import { ChatPanel } from "./components/ChatPanel";
import { HitList } from "./components/HitList";
import { PdfViewer } from "./components/PdfViewer";
import type {
  ChatMessage,
  HealthResponse,
  HitRecord,
  MetaResponse,
  StrategyId,
} from "./types";

function uid() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [meta, setMeta] = useState<MetaResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [strategy, setStrategy] = useState<StrategyId>("B");
  const [section, setSection] = useState("");
  const [topK, setTopK] = useState(3);
  const [busy, setBusy] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [activeHits, setActiveHits] = useState<HitRecord[]>([]);
  const [selectedHit, setSelectedHit] = useState<HitRecord | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    Promise.all([getHealth(), getMeta()])
      .then(([h, m]) => {
        setHealth(h);
        setMeta(m);
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  const countsLabel = useMemo(() => {
    if (!health) return "…";
    return Object.entries(health.collection_counts)
      .map(([k, v]) => `${k}:${v}`)
      .join(" · ");
  }, [health]);

  function selectHit(hit: HitRecord) {
    setSelectedHit(hit);
    setActiveHits((prev) => (prev.some((h) => h.chunk_id === hit.chunk_id) ? prev : [hit, ...prev]));
  }

  async function handleSend(question: string) {
    setError(null);
    setBusy(true);
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;

    const userId = uid();
    const assistantId = uid();
    setMessages((prev) => [
      ...prev,
      { id: userId, role: "user", content: question },
      {
        id: assistantId,
        role: "assistant",
        content: "",
        strategy,
        hits: [],
        provider: "streaming…",
      },
    ]);

    try {
      await postChatStream(
        {
          question,
          strategy,
          top_k: topK,
          section: strategy === "C" ? section || undefined : undefined,
          generate: true,
        },
        {
          onMeta: (m) => {
            setActiveHits(m.hits);
            setSelectedHit(m.hits[0] ?? null);
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantId
                  ? {
                      ...msg,
                      strategy: m.strategy,
                      hits: m.hits,
                      references: m.references,
                      trace: m.trace,
                    }
                  : msg,
              ),
            );
          },
          onToken: (text) => {
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantId ? { ...msg, content: msg.content + text } : msg,
              ),
            );
          },
          onDone: (payload) => {
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantId
                  ? {
                      ...msg,
                      content: payload.answer || msg.content,
                      provider: payload.provider,
                    }
                  : msg,
              ),
            );
            if (payload.error) {
              setError(`LLM stream note: ${payload.error}`);
            }
          },
          onError: (message) => setError(message),
        },
        ac.signal,
      );
    } catch (err) {
      if ((err as Error)?.name === "AbortError") return;
      setError(err instanceof Error ? err.message : String(err));
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === assistantId && !msg.content
            ? { ...msg, content: "(stream failed — see error banner)", provider: "error" }
            : msg,
        ),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app-shell wide">
      <header className="app-header">
        <div>
          <p className="eyebrow">Day 06 · Wiley DDBMS Ch.5</p>
          <h1>Grounded chat</h1>
          <p className="subhead">Streaming RAG · IEEE citations · PDF page jump</p>
        </div>
        <div className="status">
          <span className={health?.llm_configured ? "ok" : undefined}>
            {health?.llm_configured ? `LLM · ${health.chat_model || "on"}` : "retrieval-only"}
          </span>
          <span>{meta?.citation_style || "IEEE"}</span>
          <span>{meta?.pdf_available ? "PDF ready" : "PDF missing"}</span>
          <span>{countsLabel}</span>
        </div>
      </header>

      {error ? <div className="banner error">{error}</div> : null}

      <main className="layout three">
        <ChatPanel
          messages={messages}
          strategy={strategy}
          section={section}
          topK={topK}
          busy={busy}
          onStrategy={setStrategy}
          onSection={setSection}
          onTopK={setTopK}
          onSend={handleSend}
          onSelectMessageHits={(msg) => {
            setActiveHits(msg.hits || []);
            setSelectedHit(msg.hits?.[0] ?? null);
          }}
          onCiteClick={selectHit}
        />

        <aside className="side">
          <div className="side-block sources">
            <h2>Cited sources</h2>
            <HitList
              hits={activeHits}
              selectedId={selectedHit?.chunk_id ?? null}
              onSelect={selectHit}
            />
          </div>
          <div className="side-block grow pdf-block">
            <PdfViewer hit={selectedHit} pdfUrl={meta?.pdf_url || "/api/corpus.pdf"} />
          </div>
        </aside>
      </main>
    </div>
  );
}
