"""Evaluate retrieved evidence and preserve per-query traces."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", _nfkc(text)).strip().lower()


def evidence_in_text(evidence: str, text: str) -> bool:
    """True if gold evidence (or a close collapsed form) appears in chunk text."""
    ev = _collapse(evidence)
    body = _collapse(text)
    if not ev:
        return False
    if ev in body:
        return True
    # tolerate minor whitespace / hyphenation differences
    ev2 = ev.replace("- ", "").replace(" -", "-")
    body2 = body.replace("- ", "").replace(" -", "-")
    return ev2 in body2


def _page_match(gold_page: int | list[int] | None, hit_page: Any) -> bool:
    if gold_page is None:
        return True
    try:
        hp = int(hit_page)
    except (TypeError, ValueError):
        return False
    if isinstance(gold_page, list):
        return hp in {int(p) for p in gold_page}
    return hp == int(gold_page)


def _source_match(gold_source: str | None, hit_meta: dict[str, Any]) -> bool:
    if not gold_source:
        return True
    doc_id = str(hit_meta.get("document_id") or "")
    source = str(hit_meta.get("source") or "")
    return gold_source in (doc_id, source) or gold_source in source or doc_id == gold_source


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    """Check source/page and enough gold evidence; keep hit@1 and hit@3 trace."""
    gold_source = question.get("gold_source_id")
    gold_page = question.get("gold_page")
    evidence = question.get("evidence_quote") or ""

    annotated = []
    for hit in hits:
        meta = hit.get("metadata") or {}
        page = meta.get("page")
        is_hit = (
            _source_match(gold_source, meta)
            and _page_match(gold_page, page)
            and evidence_in_text(evidence, hit.get("text") or "")
        )
        row = dict(hit)
        row["is_hit"] = bool(is_hit)
        annotated.append(row)

    hit_at_1 = bool(annotated) and bool(annotated[0].get("is_hit"))
    hit_at_3 = any(h.get("is_hit") for h in annotated[:3])

    filt = question.get("filter") or question.get("filter_json")
    if isinstance(filt, str) and filt.strip():
        try:
            filt = json.loads(filt)
        except json.JSONDecodeError:
            filt = None

    return {
        "question_id": question.get("id"),
        "split": question.get("split"),
        "question": question.get("question"),
        "filter": filt,
        "hit_at_1": hit_at_1,
        "hit_at_3": hit_at_3,
        "hits": annotated,
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate Hit@1 and Evidence Hit@3 by strategy and dev/test split."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for t in traces:
        key = (str(t.get("strategy_id")), str(t.get("split") or "unknown"))
        groups.setdefault(key, []).append(t)

    summary: dict[str, Any] = {"by_strategy_split": {}, "overall": {}}
    for (strategy, split), rows in sorted(groups.items()):
        n = len(rows)
        h1 = sum(1 for r in rows if r.get("hit_at_1"))
        h3 = sum(1 for r in rows if r.get("hit_at_3"))
        summary["by_strategy_split"][f"{strategy}:{split}"] = {
            "n": n,
            "hit_at_1": f"{h1}/{n}",
            "hit_at_3": f"{h3}/{n}",
            "hit_at_1_rate": (h1 / n) if n else 0.0,
            "hit_at_3_rate": (h3 / n) if n else 0.0,
        }

    n = len(traces)
    h1 = sum(1 for r in traces if r.get("hit_at_1"))
    h3 = sum(1 for r in traces if r.get("hit_at_3"))
    summary["overall"] = {
        "n": n,
        "hit_at_1": f"{h1}/{n}",
        "hit_at_3": f"{h3}/{n}",
    }
    return summary


def load_questions(path: Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "questions" in data:
        return list(data["questions"])
    if isinstance(data, list):
        return data
    raise ValueError(f"Unsupported questions format: {path}")


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
