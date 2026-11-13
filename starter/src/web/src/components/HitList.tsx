import type { HitRecord } from "../types";

type Props = {
  hits: HitRecord[];
  selectedId: string | null;
  onSelect: (hit: HitRecord) => void;
};

export function HitList({ hits, selectedId, onSelect }: Props) {
  if (!hits.length) {
    return <p className="muted">Ask a question to see IEEE-cited retrieval hits.</p>;
  }

  return (
    <ul className="hit-list">
      {hits.map((hit) => {
        const meta = hit.metadata || {};
        const active = hit.chunk_id === selectedId;
        const n = hit.cite_num ?? hit.rank;
        const score = typeof hit.score === "number" ? hit.score.toFixed(3) : "—";
        const img = hit.image_url || hit.image_ref;
        return (
          <li key={hit.chunk_id}>
            <button
              type="button"
              className={`hit-card ${active ? "active" : ""}`}
              onClick={() => onSelect(hit)}
            >
              <div className="hit-card-top">
                <span className="badge cite">[{n}]</span>
                <span className="badge soft">{meta.chunk_type || "text"}</span>
                <span className="score">{score}</span>
              </div>
              <div className="hit-meta">
                page {meta.page ?? "—"} · section {meta.section || "—"}
              </div>
              <p className="hit-cite">{hit.citation_ieee}</p>
              <p className="hit-preview">{hit.text || ""}</p>
              {img ? (
                <img className="hit-thumb" src={img} alt={meta.figure_label || `figure ${n}`} />
              ) : null}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
