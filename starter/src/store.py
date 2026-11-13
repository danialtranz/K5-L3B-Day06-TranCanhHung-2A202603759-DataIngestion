"""Persistent Chroma vector store (embeddings supplied externally)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings

from .models import Chunk


def _meta_for_chroma(chunk: Chunk) -> dict[str, Any]:
    meta = chunk.metadata or {}
    # Chroma metadata values must be str|int|float|bool
    out: dict[str, Any] = {
        "document_id": chunk.document_id,
        "source": chunk.source,
        "page": int(chunk.page),
        "section": str(meta.get("section") or ""),
        "chunk_type": str(meta.get("chunk_type") or "text"),
        "strategy_id": str(meta.get("strategy_id") or ""),
        "figure_id": str(meta.get("figure_id") or ""),
        "table_id": str(meta.get("table_id") or ""),
    }
    if meta.get("image_path"):
        out["image_path"] = str(meta["image_path"])
    return out


class ChromaVectorStore:
    def __init__(self, persist_dir: str | Path, dimensions: int) -> None:
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.dimensions = dimensions
        self.client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=Settings(anonymized_telemetry=False),
        )

    def _collection_name(self, strategy: str) -> str:
        return f"chunks_{strategy.upper()}"

    def get_or_create_collection(self, strategy: str):
        return self.client.get_or_create_collection(
            name=self._collection_name(strategy),
            metadata={"hnsw:space": "cosine"},
        )

    def reset_strategy(self, strategy: str) -> None:
        name = self._collection_name(strategy)
        try:
            self.client.delete_collection(name)
        except Exception:
            pass
        self.get_or_create_collection(strategy)

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError(f"chunks ({len(chunks)}) != vectors ({len(vectors)})")
        for v in vectors:
            if len(v) != self.dimensions:
                raise ValueError(f"expected dim {self.dimensions}, got {len(v)}")

        self.reset_strategy(strategy)
        collection = self.get_or_create_collection(strategy)

        batch = 100
        for i in range(0, len(chunks), batch):
            part_c = chunks[i : i + batch]
            part_v = vectors[i : i + batch]
            collection.upsert(
                ids=[c.chunk_id for c in part_c],
                embeddings=part_v,
                documents=[c.text for c in part_c],
                metadatas=[_meta_for_chroma(c) for c in part_c],
            )

    def search(
        self,
        strategy: str,
        query_vector: list[float],
        top_k: int = 3,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if len(query_vector) != self.dimensions:
            raise ValueError(f"query dim {len(query_vector)} != {self.dimensions}")

        collection = self.get_or_create_collection(strategy)
        where = None
        if filters:
            # Only allow known metadata keys
            allowed = {}
            for key in ("section", "chunk_type", "document_id", "page"):
                if key in filters and filters[key] not in (None, ""):
                    allowed[key] = filters[key]
            if len(allowed) == 1:
                where = allowed
            elif len(allowed) > 1:
                where = {"$and": [{k: v} for k, v in allowed.items()]}

        result = collection.query(
            query_embeddings=[query_vector],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]

        hits: list[dict[str, Any]] = []
        for rank, (cid, doc, meta, dist) in enumerate(zip(ids, docs, metas, dists), start=1):
            # cosine distance in Chroma: score = 1 - distance (similarity)
            score = 1.0 - float(dist) if dist is not None else None
            hits.append(
                {
                    "rank": rank,
                    "chunk_id": cid,
                    "text": doc,
                    "score": score,
                    "metadata": meta or {},
                }
            )
        return hits


# Alias kept for older imports / README wording
PgVectorStore = ChromaVectorStore
