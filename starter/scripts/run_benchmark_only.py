"""Run A/B/C retrieval benchmark against an existing Chroma index (no re-embed)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.db import connect, init_schema, save_traces, upsert_questions  # noqa: E402
from src.embeddings import MiniLMEmbedder  # noqa: E402
from src.evaluation import evaluate_query, load_questions, save_json, summarize  # noqa: E402
from src.store import ChromaVectorStore  # noqa: E402


def filter_for_question(question: dict, strategy: str) -> dict | None:
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
    questions_path = ROOT / "data" / "questions.json"
    sqlite_path = ROOT / "data" / "lab.db"
    chroma_path = ROOT / "data" / "chroma"
    results_dir = ROOT / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    questions = load_questions(questions_path)
    print(f"Loaded {len(questions)} questions")

    conn = connect(sqlite_path)
    init_schema(conn)
    upsert_questions(conn, questions)

    embedder = MiniLMEmbedder()
    store = ChromaVectorStore(chroma_path, dimensions=embedder.dimensions)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    traces: list[dict] = []

    for strategy in ("A", "B", "C"):
        print(f"Strategy {strategy}...")
        for q in questions:
            qvec = embedder.embed_query(q["question"])
            where = filter_for_question(q, strategy)
            hits = store.search(strategy, qvec, top_k=3, filters=where)
            trace = evaluate_query(q, hits)
            trace["strategy_id"] = strategy
            trace["run_id"] = run_id
            trace["filter_applied"] = where
            traces.append(trace)
            mark = "HIT" if trace["hit_at_3"] else "miss"
            print(f"  {q['id']} {mark} hit1={trace['hit_at_1']} filter={where}")

    save_traces(conn, run_id, traces)
    summary = summarize(traces)
    save_json(results_dir / f"traces_{run_id}.json", traces)
    save_json(results_dir / f"summary_{run_id}.json", summary)
    save_json(results_dir / "summary_latest.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"run_id={run_id}")


if __name__ == "__main__":
    main()
