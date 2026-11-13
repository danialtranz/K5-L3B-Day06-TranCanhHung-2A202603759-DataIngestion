"""SQLite schema and helpers (source of truth for docs/chunks/traces)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .models import Chunk, FigureUnit, ParsedDocument, TableUnit

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS documents (
  document_id   TEXT PRIMARY KEY,
  title         TEXT NOT NULL,
  source_path   TEXT NOT NULL,
  license       TEXT,
  accessed_at   TEXT,
  page_count    INTEGER,
  language      TEXT DEFAULT 'en'
);

CREATE TABLE IF NOT EXISTS pages (
  page_id       TEXT PRIMARY KEY,
  document_id   TEXT NOT NULL REFERENCES documents(document_id),
  page_number   INTEGER NOT NULL,
  text          TEXT NOT NULL,
  char_count    INTEGER,
  UNIQUE(document_id, page_number)
);

CREATE TABLE IF NOT EXISTS figures (
  figure_id     TEXT PRIMARY KEY,
  document_id   TEXT NOT NULL REFERENCES documents(document_id),
  page_number   INTEGER NOT NULL,
  figure_label  TEXT,
  caption       TEXT NOT NULL,
  image_path    TEXT NOT NULL,
  bbox_json     TEXT,
  ocr_text      TEXT
);

CREATE TABLE IF NOT EXISTS tables (
  table_id      TEXT PRIMARY KEY,
  document_id   TEXT NOT NULL REFERENCES documents(document_id),
  page_number   INTEGER NOT NULL,
  table_label   TEXT,
  title         TEXT,
  markdown      TEXT NOT NULL,
  bbox_json     TEXT,
  row_count     INTEGER,
  col_count     INTEGER
);

CREATE TABLE IF NOT EXISTS chunks (
  chunk_id      TEXT PRIMARY KEY,
  strategy_id   TEXT NOT NULL,
  document_id   TEXT NOT NULL,
  source        TEXT NOT NULL,
  page          INTEGER NOT NULL,
  text          TEXT NOT NULL,
  chunk_type    TEXT NOT NULL,
  section       TEXT,
  figure_id     TEXT REFERENCES figures(figure_id),
  table_id      TEXT REFERENCES tables(table_id),
  char_count    INTEGER,
  created_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_chunks_strategy ON chunks(strategy_id);
CREATE INDEX IF NOT EXISTS idx_chunks_section  ON chunks(strategy_id, section);
CREATE INDEX IF NOT EXISTS idx_chunks_type     ON chunks(strategy_id, chunk_type);

CREATE TABLE IF NOT EXISTS questions (
  id               TEXT PRIMARY KEY,
  split            TEXT NOT NULL,
  question         TEXT NOT NULL,
  expected_answer  TEXT NOT NULL,
  gold_source_id   TEXT NOT NULL,
  gold_page        INTEGER NOT NULL,
  evidence_quote   TEXT NOT NULL,
  filter_json      TEXT,
  question_type    TEXT
);

CREATE TABLE IF NOT EXISTS retrieval_traces (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        TEXT NOT NULL,
  strategy_id   TEXT NOT NULL,
  question_id   TEXT NOT NULL,
  rank          INTEGER NOT NULL,
  chunk_id      TEXT NOT NULL,
  score         REAL,
  hit           INTEGER,
  UNIQUE(run_id, strategy_id, question_id, rank)
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def save_parsed_document(
    conn: sqlite3.Connection,
    doc: ParsedDocument,
    *,
    license_text: str = "",
    accessed_at: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO documents(document_id, title, source_path, license, accessed_at, page_count, language)
        VALUES (?, ?, ?, ?, ?, ?, 'en')
        ON CONFLICT(document_id) DO UPDATE SET
          title=excluded.title,
          source_path=excluded.source_path,
          license=excluded.license,
          accessed_at=excluded.accessed_at,
          page_count=excluded.page_count
        """,
        (doc.document_id, doc.title, doc.source, license_text, accessed_at, doc.page_count),
    )
    # chunks.figure_id / table_id FK → must clear chunk rows before figures/tables
    conn.execute("DELETE FROM chunks WHERE document_id = ?", (doc.document_id,))
    conn.execute("DELETE FROM pages WHERE document_id = ?", (doc.document_id,))
    conn.execute("DELETE FROM figures WHERE document_id = ?", (doc.document_id,))
    conn.execute("DELETE FROM tables WHERE document_id = ?", (doc.document_id,))

    for page in doc.pages:
        page_id = f"{doc.document_id}_p{page.page}"
        conn.execute(
            """
            INSERT INTO pages(page_id, document_id, page_number, text, char_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            (page_id, doc.document_id, page.page, page.text, len(page.text)),
        )

    for fig in doc.figures:
        conn.execute(
            """
            INSERT INTO figures(figure_id, document_id, page_number, figure_label, caption,
                                image_path, bbox_json, ocr_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fig.figure_id,
                fig.document_id,
                fig.page,
                fig.figure_label,
                fig.caption,
                fig.image_path,
                fig.bbox_json,
                fig.ocr_text,
            ),
        )

    for tbl in doc.tables:
        conn.execute(
            """
            INSERT INTO tables(table_id, document_id, page_number, table_label, title,
                               markdown, bbox_json, row_count, col_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tbl.table_id,
                tbl.document_id,
                tbl.page,
                tbl.table_label,
                tbl.title,
                tbl.markdown,
                tbl.bbox_json,
                tbl.row_count,
                tbl.col_count,
            ),
        )
    conn.commit()


def replace_chunks(conn: sqlite3.Connection, strategy_id: str, chunks: Iterable[Chunk]) -> None:
    conn.execute("DELETE FROM chunks WHERE strategy_id = ?", (strategy_id,))
    rows = []
    for c in chunks:
        meta = c.metadata or {}
        rows.append(
            (
                c.chunk_id,
                strategy_id,
                c.document_id,
                c.source,
                c.page,
                c.text,
                meta.get("chunk_type", "text"),
                meta.get("section", ""),
                meta.get("figure_id") or None,
                meta.get("table_id") or None,
                len(c.text),
                meta.get("created_at", ""),
            )
        )
    conn.executemany(
        """
        INSERT INTO chunks(chunk_id, strategy_id, document_id, source, page, text,
                           chunk_type, section, figure_id, table_id, char_count, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


def upsert_questions(conn: sqlite3.Connection, questions: list[dict[str, Any]]) -> None:
    for q in questions:
        filt = q.get("filter") or q.get("filter_json")
        if isinstance(filt, dict):
            filt = json.dumps(filt, ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO questions(id, split, question, expected_answer, gold_source_id,
                                  gold_page, evidence_quote, filter_json, question_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              split=excluded.split,
              question=excluded.question,
              expected_answer=excluded.expected_answer,
              gold_source_id=excluded.gold_source_id,
              gold_page=excluded.gold_page,
              evidence_quote=excluded.evidence_quote,
              filter_json=excluded.filter_json,
              question_type=excluded.question_type
            """,
            (
                q["id"],
                q.get("split", "dev"),
                q["question"],
                q.get("expected_answer", ""),
                q.get("gold_source_id", ""),
                int(q.get("gold_page", 0)),
                q.get("evidence_quote", ""),
                filt,
                q.get("question_type") or q.get("type"),
            ),
        )
    conn.commit()


def save_traces(conn: sqlite3.Connection, run_id: str, traces: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM retrieval_traces WHERE run_id = ?", (run_id,))
    rows = []
    for t in traces:
        for hit in t.get("hits", []):
            rows.append(
                (
                    run_id,
                    t["strategy_id"],
                    t["question_id"],
                    int(hit["rank"]),
                    hit.get("chunk_id", ""),
                    hit.get("score"),
                    1 if hit.get("is_hit") else 0,
                )
            )
    conn.executemany(
        """
        INSERT INTO retrieval_traces(run_id, strategy_id, question_id, rank, chunk_id, score, hit)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
