"""Write manual-audit label files from adjudicated scores (run_id=20261113T212140Z)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results"

# Coherence: fixed pages chosen before scoring; chunk_ids from lab.db samples.
COHERENCE = [
    # strategy, page, chunk_id, score, reason
    ("A", 1, "A_wiley_ddbms_ch5_00000", 1, "Chapter intro readable but fixed window cuts mid-sentence."),
    ("A", 2, "A_wiley_ddbms_ch5_00005", 0, "Starts mid-sentence ('network databases...'); subject unclear without prior chunk."),
    ("A", 6, "A_wiley_ddbms_ch5_00038", 0, "Starts mid-word/phrase ('f the transaction'); idea fragmented."),
    ("A", 7, "A_wiley_ddbms_ch5_00039", 1, "OLTP focus understandable; ends mid-sentence / partial context."),
    ("A", 9, "A_wiley_ddbms_ch5_00047", 2, "Conflict definition + matrix context self-contained."),
    ("A", 10, "A_wiley_ddbms_ch5_00054", 1, "Cascading-abort idea present but opens mid-discourse."),
    ("A", 14, "A_wiley_ddbms_ch5_00085", 2, "Global serializability definition intact."),
    ("A", 19, "A_wiley_ddbms_ch5_00127", 2, "Locking vs timestamping approaches introduced clearly."),
    ("A", 25, "A_wiley_ddbms_ch5_00165", 2, "Phantom definition present and readable."),
    ("A", 27, "A_wiley_ddbms_ch5_00175", 2, "TO intro keeps subject and purpose."),
    ("B", 1, "B_wiley_ddbms_ch5_00000", 2, "Longer heading-aware intro keeps chapter roadmap intact."),
    ("B", 2, "B_wiley_ddbms_ch5_00003", 1, "Still mid-sentence start; fuller examples make meaning recoverable."),
    ("B", 6, "B_wiley_ddbms_ch5_00016", 2, "Isolation interleaving example stands alone."),
    ("B", 7, "B_wiley_ddbms_ch5_00023", 2, "Schedule / serial / parallel definitions complete."),
    ("B", 9, "B_wiley_ddbms_ch5_00026", 2, "Conflict definition + isolation need coherent."),
    ("B", 10, "B_wiley_ddbms_ch5_00030", 2, "Cascading abort explanation complete despite soft boundary."),
    ("B", 14, "B_wiley_ddbms_ch5_00048", 2, "Global serializability + LA/NY example coherent."),
    ("B", 19, "B_wiley_ddbms_ch5_00071", 2, "Centralized locking/timestamping overview self-contained."),
    ("B", 25, "B_wiley_ddbms_ch5_00091", 1, "Starts mid-word ('nflict'); ANSI/phantom remedies still usable."),
    ("B", 27, "B_wiley_ddbms_ch5_00096", 2, "TO definition and timestamp assignment clear."),
    ("C", 1, "C_wiley_ddbms_ch5_00000", 2, "Same chunk text as B (shared packing)."),
    ("C", 2, "C_wiley_ddbms_ch5_00003", 1, "Same as B."),
    ("C", 6, "C_wiley_ddbms_ch5_00016", 2, "Same as B."),
    ("C", 7, "C_wiley_ddbms_ch5_00023", 2, "Same as B."),
    ("C", 9, "C_wiley_ddbms_ch5_00026", 2, "Same as B."),
    ("C", 10, "C_wiley_ddbms_ch5_00030", 2, "Same as B."),
    ("C", 14, "C_wiley_ddbms_ch5_00048", 2, "Same as B."),
    ("C", 19, "C_wiley_ddbms_ch5_00071", 2, "Same as B."),
    ("C", 25, "C_wiley_ddbms_ch5_00091", 1, "Same as B."),
    ("C", 27, "C_wiley_ddbms_ch5_00096", 2, "Same as B."),
]

# Metadata: 5 test x top-3; fields source/page/document_id/section present & consistent with chunk text.
# Manual review of traces_20261113T212140Z.json — all 15 records per strategy OK.
METADATA_ISSUES: dict[tuple[str, str, int], str] = {
    # rare notes (still ok=1)
}


def _hit_meta(h: dict) -> dict:
    meta = h.get("metadata") or {}
    return {
        "source": meta.get("source") if meta.get("source") is not None else h.get("source"),
        "page": meta.get("page") if meta.get("page") is not None else h.get("page"),
        "document_id": meta.get("document_id") or h.get("document_id"),
        "section": meta.get("section") if meta.get("section") is not None else h.get("section"),
    }


def _meta_rows(traces: list[dict]) -> list[dict]:
    rows = []
    for t in traces:
        if t.get("split") != "test":
            continue
        for h in t.get("hits") or []:
            m = _hit_meta(h)
            meta_ok = True
            issues = []
            if not m.get("source"):
                meta_ok = False
                issues.append("missing_source")
            if m.get("page") is None:
                meta_ok = False
                issues.append("missing_page")
            if not m.get("document_id"):
                meta_ok = False
                issues.append("missing_document_id")
            # section may be empty in corpus; for these test hits all non-empty
            if m.get("section") in (None, ""):
                meta_ok = False
                issues.append("missing_section")
            key = (t["strategy_id"], t["question_id"], int(h["rank"]))
            note = METADATA_ISSUES.get(key)
            if note:
                issues.append(note)
            rows.append(
                {
                    "strategy": t["strategy_id"],
                    "question_id": t["question_id"],
                    "rank": h["rank"],
                    "chunk_id": h.get("chunk_id"),
                    "ok": 1 if meta_ok else 0,
                    "page": m.get("page"),
                    "section": m.get("section"),
                    "issues": ";".join(issues) if issues else "",
                }
            )
    return rows


FILTER_IDS = ["q06", "q15", "q17", "q18", "q24", "q25"]

GROUNDING = [
    # strategy, qid, score, reason
    ("A", "q21", 1, "Top-3 mentions phantom/locking periphery but misses full gold definition in one chunk."),
    ("A", "q22", 1, "TO/age coordination appears partially; full 'main philosophy' wording not intact in top-3."),
    ("A", "q23", 2, "Top-1 states EP/VP/CP life-cycle phases; sourceable."),
    ("A", "q24", 2, "Top-1 states Oracle exclusive and shared lock modes."),
    ("A", "q25", 2, "Evidence for centralized 2PL/CLM present in top-3 (rank 2)."),
    ("B", "q21", 2, "Rank 3 contains phantom definition with predicate P."),
    ("B", "q22", 1, "TO introduced with age/timestamp but philosophy sentence incomplete vs gold."),
    ("B", "q23", 2, "Top-1 includes EP/VP/CP phases."),
    ("B", "q24", 2, "Rank 3 states exclusive and shared lock modes."),
    ("B", "q25", 2, "Top-1 covers centralized 2PL / CLM."),
    ("C", "q21", 2, "Same B packing; phantom definition in top-3."),
    ("C", "q22", 1, "Same miss on full TO philosophy wording."),
    ("C", "q23", 2, "EP/VP/CP in top-1."),
    ("C", "q24", 2, "Exclusive/shared modes in top-1 under section filter."),
    ("C", "q25", 2, "Centralized 2PL/CLM in top-1 under section filter."),
]


def avg(scores: list[int]) -> float:
    return round(sum(scores) / len(scores), 2) if scores else 0.0


def main() -> None:
    traces = json.loads((OUT / "traces_20261113T212140Z.json").read_text(encoding="utf-8"))

    # coherence CSV
    coh_path = OUT / "audit_coherence.csv"
    with coh_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["strategy", "page", "chunk_id", "score", "reason"]
        )
        w.writeheader()
        for strategy, page, cid, score, reason in COHERENCE:
            w.writerow(
                {
                    "strategy": strategy,
                    "page": page,
                    "chunk_id": cid,
                    "score": score,
                    "reason": reason,
                }
            )

    # metadata CSV
    meta_rows = _meta_rows(traces)
    meta_path = OUT / "audit_metadata.csv"
    with meta_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "strategy",
                "question_id",
                "rank",
                "chunk_id",
                "ok",
                "page",
                "section",
                "issues",
            ],
        )
        w.writeheader()
        w.writerows(meta_rows)

    # filter CSV
    filt_path = OUT / "audit_filter.csv"
    filter_rows = []
    for strategy in ("A", "B", "C"):
        for qid in FILTER_IDS:
            t = next(
                x
                for x in traces
                if x["strategy_id"] == strategy and x["question_id"] == qid
            )
            wanted = (t.get("filter") or {}).get("section")
            applied = t.get("filter_applied")
            hits = t.get("hits") or []
            if strategy in ("A", "B"):
                filter_rows.append(
                    {
                        "strategy": strategy,
                        "question_id": qid,
                        "wanted_section": wanted,
                        "filter_applied": json.dumps(applied),
                        "compliant": "N/A",
                        "n_hits": len(hits),
                        "detail": "Filter not applied for this strategy.",
                    }
                )
                continue
            # C: exact section equality as store.search where clause
            sections = [_hit_meta(h).get("section") for h in hits]
            match = bool(hits) and all(s == wanted for s in sections)
            filter_rows.append(
                {
                    "strategy": strategy,
                    "question_id": qid,
                    "wanted_section": wanted,
                    "filter_applied": json.dumps(applied),
                    "compliant": "1" if match else "0",
                    "n_hits": len(hits),
                    "detail": f"sections={sections}",
                }
            )

    with filt_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "strategy",
                "question_id",
                "wanted_section",
                "filter_applied",
                "compliant",
                "n_hits",
                "detail",
            ],
        )
        w.writeheader()
        w.writerows(filter_rows)

    # grounding CSV
    ground_path = OUT / "audit_grounding.csv"
    with ground_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["strategy", "question_id", "score", "reason"]
        )
        w.writeheader()
        for strategy, qid, score, reason in GROUNDING:
            w.writerow(
                {
                    "strategy": strategy,
                    "question_id": qid,
                    "score": score,
                    "reason": reason,
                }
            )

    # summary
    summary: dict = {
        "run_id": "20261113T212140Z",
        "method": {
            "grader": "author",
            "author_share_approx": "70-80%",
            "ai_draft_share_approx": "20-30%",
            "bias_note": "Author is grader of record; self-grading bias possible.",
            "coherence_pages": [1, 2, 6, 7, 9, 10, 14, 19, 25, 27],
            "test_questions": ["q21", "q22", "q23", "q24", "q25"],
            "filter_questions": FILTER_IDS,
        },
        "by_strategy": {},
    }
    for strategy in ("A", "B", "C"):
        coh = [s for st, _, _, s, _ in COHERENCE if st == strategy]
        meta = [r for r in meta_rows if r["strategy"] == strategy]
        meta_ok = sum(r["ok"] for r in meta)
        ground = [s for st, _, s, _ in GROUNDING if st == strategy]
        g0 = sum(1 for s in ground if s == 0)
        g1 = sum(1 for s in ground if s == 1)
        g2 = sum(1 for s in ground if s == 2)
        if strategy == "C":
            filt = [r for r in filter_rows if r["strategy"] == "C"]
            filt_ok = sum(1 for r in filt if r["compliant"] == "1")
            filt_str = f"{filt_ok}/{len(filt)}"
        else:
            filt_str = "N/A"
        summary["by_strategy"][strategy] = {
            "coherence_avg_over_2": avg(coh),
            "coherence_n": len(coh),
            "coherence_scores": coh,
            "metadata_ok": f"{meta_ok}/{len(meta)}",
            "filter_compliant": filt_str,
            "grounding_counts_0_1_2": {"0": g0, "1": g1, "2": g2},
            "grounding_avg_over_2": avg(ground),
            "grounding_n": len(ground),
        }

    sum_path = OUT / "audit_summary.json"
    sum_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Wrote", coh_path.name, meta_path.name, filt_path.name, ground_path.name, sum_path.name)
    print(json.dumps(summary["by_strategy"], indent=2))


if __name__ == "__main__":
    main()
