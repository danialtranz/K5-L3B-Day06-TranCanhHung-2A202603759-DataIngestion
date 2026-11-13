"""Day 06 pipeline: ingest → chunk A/B/C → embed → Chroma → optional benchmark."""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .chunking import build_strategy_a, build_strategy_b, build_strategy_c_from_b
from .db import connect, init_schema, replace_chunks, save_parsed_document, save_traces, upsert_questions
from .embeddings import DEFAULT_MODEL, MiniLMEmbedder
from .evaluation import evaluate_query, load_questions, save_json, summarize
from .ingestion import load_pdf
from .store import ChromaVectorStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("run")

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent


def _default_pdf() -> Path:
    assets = REPO_ROOT / "assets"
    candidates = sorted(assets.glob("*.pdf")) if assets.exists() else []
    if candidates:
        return candidates[0]
    return ROOT / "data" / "corpus.pdf"


def _env_path(key: str, default: Path | str) -> Path:
    """Read path from env; treat missing/blank as unset (Path('') == '.')."""
    raw = (os.getenv(key) or "").strip()
    return Path(raw) if raw else Path(default)


def parse_args() -> argparse.Namespace:
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser(description="Day 06 data ingestion + retrieval pipeline")
    p.add_argument("--pdf", type=Path, default=_env_path("PDF_PATH", _default_pdf()))
    p.add_argument("--document-id", default=(os.getenv("DOCUMENT_ID") or "wiley_ddbms_ch5").strip() or "wiley_ddbms_ch5")
    p.add_argument("--sqlite", type=Path, default=_env_path("SQLITE_PATH", ROOT / "data" / "lab.db"))
    p.add_argument("--chroma", type=Path, default=_env_path("CHROMA_PATH", ROOT / "data" / "chroma"))
    p.add_argument("--figures-dir", type=Path, default=_env_path("FIGURES_DIR", ROOT / "data" / "figures"))
    p.add_argument("--results", type=Path, default=_env_path("RESULTS_DIR", ROOT / "results"))
    p.add_argument("--questions", type=Path, default=_env_path("QUESTIONS_PATH", ROOT / "data" / "questions.json"))
    p.add_argument("--model", default=(os.getenv("EMBEDDING_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL)
    p.add_argument("--skip-benchmark", action="store_true", help="Only ingest + index")
    p.add_argument("--top-k", type=int, default=3)
    return p.parse_args()


def _embed_and_store(
    store: ChromaVectorStore,
    embedder: MiniLMEmbedder,
    strategy: str,
    chunks: list,
) -> float:
    t0 = time.perf_counter()
    vectors = embedder.embed_documents([c.text for c in chunks])
    store.upsert(strategy, chunks, vectors)
    return time.perf_counter() - t0


def _filter_for_question(question: dict, strategy: str) -> dict | None:
    if strategy != "C":
        return None
    filt = question.get("filter") or question.get("filter_json")
    if isinstance(filt, str) and filt.strip():
        try:
            filt = json.loads(filt)
        except json.JSONDecodeError:
            return None
    if isinstance(filt, dict) and filt.get("section"):
        return {"section": filt["section"]}
    return None


def main() -> None:
    args = parse_args()
    pdf_path = args.pdf
    if not pdf_path.is_file():
        raise FileNotFoundError(
            f"PDF not found: {pdf_path} "
            f"(set PDF_PATH in .env or pass --pdf; leave PDF_PATH blank to auto-pick assets/*.pdf)"
        )

    args.results.mkdir(parents=True, exist_ok=True)
    args.figures_dir.mkdir(parents=True, exist_ok=True)

    logger.info("PDF: %s", pdf_path)
    logger.info("SQLite: %s | Chroma: %s", args.sqlite, args.chroma)

    conn = connect(args.sqlite)
    init_schema(conn)

    t_ingest = time.perf_counter()
    parsed = load_pdf(
        pdf_path,
        document_id=args.document_id,
        source=str(pdf_path.as_posix()),
        figures_dir=args.figures_dir,
        title="Wiley DDBMS Chapter 5",
    )
    ingest_s = time.perf_counter() - t_ingest
    logger.info(
        "Parsed pages=%d headings=%d figures=%d tables=%d (%.1fs)",
        parsed.page_count,
        len(parsed.headings),
        len(parsed.figures),
        len(parsed.tables),
        ingest_s,
    )
    if parsed.page_count < 30:
        logger.warning("Fewer than 30 pages (%d). Lab requires >= 30 content pages.", parsed.page_count)

    save_parsed_document(
        conn,
        parsed,
        license_text="check source license",
        accessed_at=datetime.now(timezone.utc).date().isoformat(),
    )

    chunks_a = build_strategy_a(parsed)
    chunks_b = build_strategy_b(parsed)
    chunks_c = build_strategy_c_from_b(chunks_b)
    logger.info("Chunks A=%d B=%d C=%d", len(chunks_a), len(chunks_b), len(chunks_c))

    replace_chunks(conn, "A", chunks_a)
    replace_chunks(conn, "B", chunks_b)
    replace_chunks(conn, "C", chunks_c)

    save_json(
        args.results / "chunk_stats.json",
        {
            "document_id": parsed.document_id,
            "page_count": parsed.page_count,
            "headings": [{"label": h.label, "title": h.title, "page": h.page} for h in parsed.headings],
            "figures": len(parsed.figures),
            "tables": len(parsed.tables),
            "strategies": {
                "A": {"n": len(chunks_a), "params": {"size": 500, "overlap": 80, "min_last": 100}},
                "B": {"n": len(chunks_b), "params": {"max_size": 1000, "min_size": 200, "sentence_overlap": 100}},
                "C": {"n": len(chunks_c), "note": "B chunks + query filter on metadata.section"},
            },
            "ingest_seconds": ingest_s,
        },
    )

    logger.info("Loading embedder: %s", args.model)
    embedder = MiniLMEmbedder(args.model)
    store = ChromaVectorStore(args.chroma, dimensions=embedder.dimensions)

    timings = {}
    logger.info("Embedding + upsert strategy A (%d chunks)...", len(chunks_a))
    timings["A"] = _embed_and_store(store, embedder, "A", chunks_a)
    logger.info("Strategy A indexed in %.1fs", timings["A"])

    logger.info("Embedding + upsert strategy B (%d chunks)...", len(chunks_b))
    t0 = time.perf_counter()
    vectors_b = embedder.embed_documents([c.text for c in chunks_b])
    store.upsert("B", chunks_b, vectors_b)
    timings["B"] = time.perf_counter() - t0
    logger.info("Strategy B indexed in %.1fs", timings["B"])

    # C reuses B vectors (same texts); only IDs/metadata strategy differ.
    logger.info("Upsert strategy C from B vectors (%d chunks)...", len(chunks_c))
    t0 = time.perf_counter()
    store.upsert("C", chunks_c, vectors_b)
    timings["C"] = time.perf_counter() - t0
    logger.info("Strategy C indexed in %.1fs", timings["C"])

    save_json(args.results / "index_timings.json", timings)

    if args.skip_benchmark or not args.questions.exists():
        if not args.questions.exists():
            logger.warning("No questions file at %s — skip benchmark. Create data/questions.json later.", args.questions)
        logger.info("Pipeline ingest/index complete.")
        return

    questions = load_questions(args.questions)
    upsert_questions(conn, questions)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    traces: list[dict] = []

    for strategy in ("A", "B", "C"):
        for q in questions:
            qvec = embedder.embed_query(q["question"])
            where = _filter_for_question(q, strategy)
            hits = store.search(strategy, qvec, top_k=args.top_k, filters=where)
            trace = evaluate_query(q, hits)
            trace["strategy_id"] = strategy
            trace["run_id"] = run_id
            trace["filter_applied"] = where
            traces.append(trace)

    save_traces(conn, run_id, traces)
    summary = summarize(traces)
    save_json(args.results / f"traces_{run_id}.json", traces)
    save_json(args.results / f"summary_{run_id}.json", summary)
    save_json(args.results / "summary_latest.json", summary)
    logger.info("Benchmark summary: %s", json.dumps(summary, ensure_ascii=False))
    logger.info("Done. Results in %s", args.results)


if __name__ == "__main__":
    main()
