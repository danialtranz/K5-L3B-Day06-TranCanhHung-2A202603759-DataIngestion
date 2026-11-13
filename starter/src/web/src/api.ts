import type { ChatResponse, HealthResponse, HitRecord, MetaResponse, StrategyId } from "./types";

async function parseJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(path, init);
  } catch (err) {
    const hint =
      "Cannot reach API. Open http://127.0.0.1:5173 (with Vite) while FastAPI runs on :8000, " +
      "or use http://127.0.0.1:8000 after npm run build.";
    throw new Error(`${err instanceof Error ? err.message : String(err)}. ${hint}`);
  }
}

export function getHealth(): Promise<HealthResponse> {
  return apiFetch("/api/health").then((r) => parseJson<HealthResponse>(r));
}

export function getMeta(): Promise<MetaResponse> {
  return apiFetch("/api/meta").then((r) => parseJson<MetaResponse>(r));
}

export function postChat(body: {
  question: string;
  strategy: StrategyId;
  top_k?: number;
  section?: string;
  generate?: boolean;
}): Promise<ChatResponse> {
  return apiFetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => parseJson<ChatResponse>(r));
}

export type StreamHandlers = {
  onMeta: (meta: {
    question: string;
    strategy: StrategyId;
    section_filter: string | null;
    top_k: number;
    hits: HitRecord[];
    references?: string[];
    trace?: ChatResponse["trace"];
  }) => void;
  onToken: (text: string) => void;
  onDone: (payload: { provider: string; answer: string; error?: string }) => void;
  onError?: (message: string) => void;
};

/** Consume SSE from /api/chat/stream (meta → token* → done). */
export async function postChatStream(
  body: {
    question: string;
    strategy: StrategyId;
    top_k?: number;
    section?: string;
    generate?: boolean;
  },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await apiFetch("/api/chat/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({ generate: true, ...body }),
    signal,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  if (!res.body) {
    throw new Error("Streaming not supported by this browser/response");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let currentEvent = "message";

  const flushBlock = (block: string) => {
    const lines = block.split("\n");
    let event = currentEvent;
    const dataLines: string[] = [];
    for (const line of lines) {
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
    if (!dataLines.length) return;
    const raw = dataLines.join("\n");
    try {
      const data = JSON.parse(raw);
      if (event === "meta") handlers.onMeta(data);
      else if (event === "token") handlers.onToken(String(data.text ?? ""));
      else if (event === "done") handlers.onDone(data);
      else if (event === "error") handlers.onError?.(String(data.message ?? raw));
    } catch {
      handlers.onError?.(`Bad SSE payload: ${raw.slice(0, 200)}`);
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE events separated by blank line
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      if (block.trim()) flushBlock(block);
      currentEvent = "message";
    }
  }
  if (buffer.trim()) flushBlock(buffer);
}
