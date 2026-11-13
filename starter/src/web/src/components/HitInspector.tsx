import type { HitRecord } from "../types";

type Props = {
  hit: HitRecord | null;
};

export function HitInspector({ hit }: Props) {
  if (!hit) {
    return (
      <div className="inspector empty">
        <p className="muted">Select a cited hit to inspect passage / figure.</p>
      </div>
    );
  }

  const meta = hit.metadata || {};
  const img = hit.image_url || hit.image_ref;
  const n = hit.cite_num ?? hit.rank;

  return (
    <div className="inspector">
      <header className="inspector-header">
        <h3>
          Source <span className="badge cite">[{n}]</span>
        </h3>
        <code>{hit.chunk_id}</code>
      </header>

      <p className="ieee-line">{hit.citation_ieee}</p>

      <div className="kv-grid">
        <span>Score</span>
        <strong>{typeof hit.score === "number" ? hit.score.toFixed(4) : "—"}</strong>
        <span>Page</span>
        <strong>{meta.page ?? "—"}</strong>
        <span>Section</span>
        <strong>{meta.section || "—"}</strong>
        <span>Type</span>
        <strong>{meta.chunk_type || "text"}</strong>
      </div>

      {img ? (
        <figure className="figure-preview">
          <img src={img} alt={meta.figure_label || hit.citation_ieee || "figure"} />
          <figcaption>
            Image ref: <a href={img} target="_blank" rel="noreferrer">{img}</a>
          </figcaption>
        </figure>
      ) : (
        <div className="viz-slot">
          <span className="muted">No image for this chunk</span>
        </div>
      )}

      <h4>Passage</h4>
      <pre className="passage">{hit.text}</pre>
    </div>
  );
}
