"""FastAPI server: retrieval chat over indexed Chroma collections."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .embeddings import DEFAULT_MODEL, MiniLMEmbedder
from .store import ChromaVectorStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("app")

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
load_dotenv(ROOT / ".env")

SQLITE_PATH = Path(os.getenv("SQLITE_PATH", str(ROOT / "data" / "lab.db")))
CHROMA_PATH = Path(os.getenv("CHROMA_PATH", str(ROOT / "data" / "chroma")))
FIGURES_DIR = Path(os.getenv("FIGURES_DIR", str(ROOT / "data" / "figures")))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL)
WEB_DIST = Path(__file__).resolve().parent / "web" / "dist"
DOC_IEEE_SHORT = os.getenv(
    "DOC_IEEE_SHORT",
    "S. Rahimi and F. Haug, Distributed Database Management Systems: A Practical Approach, Wiley-IEEE, 2010",
)

STATE: dict[str, Any] = {}


def _resolve_pdf_path() -> Path | None:
    raw = os.getenv("PDF_PATH", "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = (ROOT / p).resolve()
        if p.is_file():
            return p
    assets = REPO_ROOT / "assets"
    if assets.is_dir():
        candidates = sorted(assets.glob("*.pdf"))
        if candidates:
            return candidates[0]
    fallback = ROOT / "data" / "corpus.pdf"
    return fallback if fallback.is_file() else None


def _sse(event: str, data: dict[str, Any] | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    strategy: str = Field(default="B", pattern="^[ABCabc]$")
    top_k: int = Field(default=3, ge=1, le=10)
    section: str | None = None
    generate: bool = True


class SearchRequest(BaseModel):
    question: str = Field(..., min_length=1)
    strategy: str = Field(default="B", pattern="^[ABCabc]$")
    top_k: int = Field(default=3, ge=1, le=10)
    section: str | None = None


def _filters(section: str | None, strategy: str) -> dict[str, Any] | None:
    if strategy.upper() != "C":
        return None
    if section and section.strip():
        return {"section": section.strip()}
    return None


def _figure_image_url(image_path: str | None, figure_id: str | None) -> str | None:
    """Map stored figure path / id to a public /api/figures/<file> URL."""
    candidates: list[Path] = []
    if image_path:
        p = Path(image_path)
        candidates.append(p)
        candidates.append(FIGURES_DIR / p.name)
    if figure_id:
        candidates.append(FIGURES_DIR / f"{figure_id}.png")
    for cand in candidates:
        try:
            if cand.is_file():
                return f"/api/figures/{cand.name}"
        except OSError:
            continue
    # Fallback: search by figure_id prefix
    if figure_id and FIGURES_DIR.exists():
        for f in FIGURES_DIR.glob(f"{figure_id}*.png"):
            return f"/api/figures/{f.name}"
    return None


def _ieee_citation(n: int, meta: dict[str, Any], text: str) -> str:
    """Build an IEEE-style numbered reference for one retrieved chunk."""
    page = meta.get("page")
    section = (meta.get("section") or "").strip()
    chunk_type = (meta.get("chunk_type") or "text").strip()
    locator_parts: list[str] = []

    if chunk_type == "figure":
        label = ""
        # text often starts with "Figure 5.10. ..."
        head = (text or "").strip().split("\n", 1)[0]
        if head.lower().startswith("figure"):
            label = head.split(".", 1)[0].strip()
        locator_parts.append(label or "Fig.")
    elif chunk_type == "table":
        head = (text or "").strip().split("\n", 1)[0]
        if head.upper().startswith("TABLE"):
            locator_parts.append(head.split(",", 1)[0].strip()[:40])
        else:
            locator_parts.append("Table")
    elif section:
        locator_parts.append(f"sec. {section}")

    if page not in (None, ""):
        locator_parts.append(f"p. {page}")

    locator = ", ".join(locator_parts) if locator_parts else "retrieved chunk"
    return f"[{n}] {DOC_IEEE_SHORT}, {locator}."


def _enrich_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach SQLite fields, IEEE citation, and optional figure image URL."""
    conn = None
    if SQLITE_PATH.exists():
        conn = sqlite3.connect(str(SQLITE_PATH))
        conn.row_factory = sqlite3.Row
    out: list[dict[str, Any]] = []
    try:
        for h in hits:
            row = dict(h)
            meta = dict(row.get("metadata") or {})
            cid = h.get("chunk_id")
            image_path = meta.get("image_path") or ""
            figure_id = meta.get("figure_id") or ""

            if conn and cid:
                cur = conn.execute(
                    """
                    SELECT c.chunk_id, c.strategy_id, c.document_id, c.source, c.page, c.text,
                           c.chunk_type, c.section, c.figure_id, c.table_id, c.char_count,
                           f.image_path AS figure_image_path, f.figure_label, f.caption
                    FROM chunks c
                    LEFT JOIN figures f ON f.figure_id = c.figure_id
                    WHERE c.chunk_id = ?
                    """,
                    (cid,),
                )
                db_row = cur.fetchone()
                if db_row:
                    payload = dict(db_row)
                    row["sqlite"] = payload
                    if payload.get("figure_image_path"):
                        image_path = str(payload["figure_image_path"])
                    if payload.get("figure_id"):
                        figure_id = str(payload["figure_id"])
                    if payload.get("chunk_type"):
                        meta["chunk_type"] = payload["chunk_type"]
                    if payload.get("section") is not None:
                        meta["section"] = payload["section"]
                    if payload.get("page") is not None:
                        meta["page"] = payload["page"]
                    if payload.get("figure_label"):
                        meta["figure_label"] = payload["figure_label"]

            n = int(row.get("rank") or len(out) + 1)
            citation = _ieee_citation(n, meta, row.get("text") or "")
            image_url = _figure_image_url(str(image_path) if image_path else None, str(figure_id) if figure_id else None)

            row["metadata"] = meta
            row["cite_num"] = n
            row["citation_ieee"] = citation
            row["image_url"] = image_url
            row["image_ref"] = image_url  # alias for FE
            out.append(row)
    finally:
        if conn is not None:
            conn.close()
    return out


def _references_block(hits: list[dict[str, Any]]) -> str:
    lines = [str(h.get("citation_ieee") or "") for h in hits if h.get("citation_ieee")]
    if not lines:
        return ""
    return "References\n" + "\n".join(lines)


def _context_answer(question: str, hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "No relevant passages were retrieved for this question."
    parts = [
        f"Based on the retrieved evidence for: {question}",
        "",
    ]
    for h in hits:
        n = h.get("cite_num") or h.get("rank")
        # Full chunk text (no ellipsis truncation) for demo / evidence inspection
        excerpt = (h.get("text") or "").strip().replace("\n", " ")
        parts.append(f"[{n}] {excerpt}")
        if h.get("image_url"):
            parts.append(f"    Image ref: {h['image_url']}")
        parts.append("")
    refs = _references_block(hits)
    if refs:
        parts.append(refs)
    return "\n".join(parts).strip()


GROUNDING_SYSTEM_PROMPT = """You are a grounded tutor for a distributed databases textbook (Rahimi & Haug, Ch.5).

Hard rules:
1. Answer ONLY from the retrieved passages in the user message. Those passages are your entire knowledge base for this turn.
2. Do NOT use outside knowledge, memorized facts, or speculation. If the passages do not contain enough evidence, say clearly that the retrieved evidence is insufficient and what is missing.
3. Do NOT invent page numbers, section IDs, definitions, figures, or citations.
4. Cite supporting passages with IEEE-style numeric markers [1], [2], … matching the passage numbers given. Place citations immediately after the claims they support.
5. Prefer concise, direct answers. Quote short phrases from the passages when helpful.
6. Reply in the same language as the question (English or Vietnamese).
7. End with a References section that lists the provided IEEE reference lines UNCHANGED (do not rewrite titles).

If no passages are provided, refuse to answer and ask the user to retrieve again."""


def _build_grounded_user_prompt(question: str, hits: list[dict[str, Any]]) -> str:
    if not hits:
        return (
            f"Question:\n{question}\n\n"
            "Retrieved passages:\n(none)\n\n"
            "Instructions: Refuse — no knowledge-base evidence was retrieved."
        )

    blocks: list[str] = []
    for h in hits:
        meta = h.get("metadata") or {}
        n = h.get("cite_num") or h.get("rank")
        blocks.append(
            f"Passage [{n}]\n"
            f"citation: {h.get('citation_ieee')}\n"
            f"page: {meta.get('page')} | section: {meta.get('section') or '—'} | "
            f"type: {meta.get('chunk_type') or 'text'}\n"
            f"text:\n{(h.get('text') or '').strip()}"
        )
    context = "\n\n---\n\n".join(blocks)
    refs = _references_block(hits)
    return (
        f"Question:\n{question}\n\n"
        "Retrieved passages (knowledge base — use ONLY these):\n"
        f"{context}\n\n"
        f"{refs}\n\n"
        "Write a grounded answer with [n] citations, then the References block."
    )


async def _llm_answer(question: str, hits: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (answer, provider). Uses OpenRouter or OpenAI if key present."""
    # Re-read .env each call so key/model edits apply without relying on stale process env
    load_dotenv(ROOT / ".env", override=True)
    openrouter = os.getenv("OPENROUTER_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("CHAT_MODEL", "").strip()

    system = GROUNDING_SYSTEM_PROMPT
    user = _build_grounded_user_prompt(question, hits)

    if openrouter:
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {openrouter}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model or "openai/gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
        }
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"], f"openrouter:{payload['model']}"

    if openai_key:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {openai_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model or "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
        }
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"], f"openai:{payload['model']}"

    logger.warning("No OPENAI_API_KEY / OPENROUTER_API_KEY — retrieval-only fallback")
    return _context_answer(question, hits), "retrieval_only"


async def _llm_stream_tokens(question: str, hits: list[dict[str, Any]]) -> AsyncIterator[tuple[str, str]]:
    """Yield ('token', text) deltas, then ('done', provider). Falls back to one-shot text."""
    load_dotenv(ROOT / ".env", override=True)
    openrouter = os.getenv("OPENROUTER_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("CHAT_MODEL", "").strip()
    system = GROUNDING_SYSTEM_PROMPT
    user = _build_grounded_user_prompt(question, hits)

    if openrouter:
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {openrouter}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model or "openai/gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "stream": True,
        }
        provider = f"openrouter:{payload['model']}"
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                        delta = (((obj.get("choices") or [{}])[0]).get("delta") or {}).get("content")
                    except json.JSONDecodeError:
                        continue
                    if delta:
                        yield "token", delta
        yield "done", provider
        return

    if openai_key:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {openai_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model or "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "stream": True,
        }
        provider = f"openai:{payload['model']}"
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                        delta = (((obj.get("choices") or [{}])[0]).get("delta") or {}).get("content")
                    except json.JSONDecodeError:
                        continue
                    if delta:
                        yield "token", delta
        yield "done", provider
        return

    # No API key: stream retrieval-only answer in one chunk
    text = _context_answer(question, hits)
    yield "token", text
    yield "done", "retrieval_only"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Loading embedder: %s", EMBEDDING_MODEL)
    embedder = MiniLMEmbedder(EMBEDDING_MODEL)
    store = ChromaVectorStore(CHROMA_PATH, dimensions=embedder.dimensions)
    STATE["embedder"] = embedder
    STATE["store"] = store
    logger.info("Ready. chroma=%s sqlite=%s", CHROMA_PATH, SQLITE_PATH)
    yield
    STATE.clear()


app = FastAPI(title="Day06 RAG Chat", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, Any]:
    load_dotenv(ROOT / ".env", override=True)
    store: ChromaVectorStore = STATE["store"]
    counts = {}
    for s in ("A", "B", "C"):
        try:
            counts[s] = store.get_or_create_collection(s).count()
        except Exception as exc:
            counts[s] = f"error: {exc}"
    return {
        "ok": True,
        "embedding_model": EMBEDDING_MODEL,
        "dimensions": STATE["embedder"].dimensions,
        "chroma_path": str(CHROMA_PATH),
        "sqlite_path": str(SQLITE_PATH),
        "collection_counts": counts,
        "llm_configured": bool(
            os.getenv("OPENROUTER_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
        ),
        "chat_model": os.getenv("CHAT_MODEL", "").strip() or None,
    }


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    """Schema hints for future hit visualization."""
    return {
        "strategies": [
            {"id": "A", "label": "Fixed-size 500/80", "filter": False},
            {"id": "B", "label": "Heading + paragraph pack", "filter": False},
            {"id": "C", "label": "B + section filter", "filter": True},
        ],
        "hit_fields": [
            "rank",
            "cite_num",
            "citation_ieee",
            "chunk_id",
            "score",
            "text",
            "image_url",
            "metadata.page",
            "metadata.section",
            "metadata.chunk_type",
            "metadata.figure_id",
            "metadata.table_id",
            "sqlite",
        ],
        "citation_style": "IEEE",
        "viz_ready": True,
        "pdf_url": "/api/corpus.pdf",
        "stream_url": "/api/chat/stream",
        "pdf_available": _resolve_pdf_path() is not None,
    }


@app.get("/api/figures/{filename}")
def get_figure(filename: str) -> FileResponse:
    """Serve cropped figure PNGs for chat image refs."""
    safe = Path(filename).name
    path = FIGURES_DIR / safe
    if not path.is_file():
        raise HTTPException(404, f"figure not found: {safe}")
    return FileResponse(path, media_type="image/png")


@app.get("/api/corpus.pdf")
def get_corpus_pdf() -> FileResponse:
    """Serve the lab corpus PDF for the right-panel viewer (#page=N)."""
    path = _resolve_pdf_path()
    if path is None:
        raise HTTPException(404, "corpus PDF not found (set PDF_PATH or place a PDF under assets/)")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
        headers={"Content-Disposition": f'inline; filename="{path.name}"'},
    )


@app.post("/api/search")
def search(req: SearchRequest) -> dict[str, Any]:
    embedder: MiniLMEmbedder = STATE["embedder"]
    store: ChromaVectorStore = STATE["store"]
    strategy = req.strategy.upper()
    qvec = embedder.embed_query(req.question)
    hits = store.search(strategy, qvec, top_k=req.top_k, filters=_filters(req.section, strategy))
    hits = _enrich_hits(hits)
    return {
        "question": req.question,
        "strategy": strategy,
        "section_filter": req.section,
        "top_k": req.top_k,
        "hits": hits,
        "references": [h.get("citation_ieee") for h in hits if h.get("citation_ieee")],
    }


@app.post("/api/chat")
async def chat(req: ChatRequest) -> dict[str, Any]:
    embedder: MiniLMEmbedder = STATE["embedder"]
    store: ChromaVectorStore = STATE["store"]
    strategy = req.strategy.upper()
    if not req.question.strip():
        raise HTTPException(400, "question is empty")

    qvec = embedder.embed_query(req.question)
    hits = store.search(strategy, qvec, top_k=req.top_k, filters=_filters(req.section, strategy))
    hits = _enrich_hits(hits)

    provider = "retrieval_only"
    answer = _context_answer(req.question, hits)
    if req.generate:
        try:
            answer, provider = await _llm_answer(req.question, hits)
        except Exception as exc:
            logger.exception("LLM failed; falling back to retrieval-only")
            answer = _context_answer(req.question, hits) + f"\n\n(LLM error: {exc})"
            provider = "retrieval_only_fallback"

    return {
        "question": req.question,
        "strategy": strategy,
        "section_filter": req.section,
        "top_k": req.top_k,
        "answer": answer,
        "provider": provider,
        "hits": hits,
        "references": [h.get("citation_ieee") for h in hits if h.get("citation_ieee")],
        "trace": {
            "stages": ["embed_query", "vector_search", "enrich_sqlite", "generate"],
            "hit_count": len(hits),
        },
    }


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """SSE: meta (hits) → token* → done|error. FE streams answer while citing chunks."""
    embedder: MiniLMEmbedder = STATE["embedder"]
    store: ChromaVectorStore = STATE["store"]
    strategy = req.strategy.upper()
    if not req.question.strip():
        raise HTTPException(400, "question is empty")

    qvec = embedder.embed_query(req.question)
    hits = store.search(strategy, qvec, top_k=req.top_k, filters=_filters(req.section, strategy))
    hits = _enrich_hits(hits)
    references = [h.get("citation_ieee") for h in hits if h.get("citation_ieee")]

    async def event_gen() -> AsyncIterator[str]:
        yield _sse(
            "meta",
            {
                "question": req.question,
                "strategy": strategy,
                "section_filter": req.section,
                "top_k": req.top_k,
                "hits": hits,
                "references": references,
                "trace": {
                    "stages": ["embed_query", "vector_search", "enrich_sqlite", "generate_stream"],
                    "hit_count": len(hits),
                },
            },
        )
        if not req.generate:
            text = _context_answer(req.question, hits)
            yield _sse("token", {"text": text})
            yield _sse("done", {"provider": "retrieval_only", "answer": text})
            return

        answer_parts: list[str] = []
        provider = "retrieval_only"
        try:
            async for kind, value in _llm_stream_tokens(req.question, hits):
                if kind == "token":
                    answer_parts.append(value)
                    yield _sse("token", {"text": value})
                elif kind == "done":
                    provider = value
            answer = "".join(answer_parts)
            yield _sse("done", {"provider": provider, "answer": answer})
        except Exception as exc:
            logger.exception("LLM stream failed; falling back")
            fallback = _context_answer(req.question, hits) + f"\n\n(LLM error: {exc})"
            # If nothing streamed yet, send fallback as tokens
            if not answer_parts:
                yield _sse("token", {"text": fallback})
            yield _sse(
                "done",
                {
                    "provider": "retrieval_only_fallback",
                    "answer": "".join(answer_parts) if answer_parts else fallback,
                    "error": str(exc),
                },
            )

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


if WEB_DIST.exists():
    assets = WEB_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIST / "index.html")


def main() -> None:
    import uvicorn

    uvicorn.run("src.app:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    main()
