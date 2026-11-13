import { useMemo } from "react";
import type { HitRecord } from "../types";

type Props = {
  hit: HitRecord | null;
  pdfUrl?: string;
};

export function PdfViewer({ hit, pdfUrl = "/api/corpus.pdf" }: Props) {
  const page = hit?.metadata?.page != null ? Number(hit.metadata.page) : null;
  const section = hit?.metadata?.section || "—";
  const n = hit?.cite_num ?? hit?.rank;

  const src = useMemo(() => {
    if (page && Number.isFinite(page) && page > 0) {
      // Native browser PDF viewer jump (Chrome/Edge). Remount via key below.
      return `${pdfUrl}#page=${page}`;
    }
    return pdfUrl;
  }, [pdfUrl, page]);

  return (
    <div className="pdf-viewer">
      <header className="pdf-toolbar">
        <div>
          <strong>Corpus PDF</strong>
          {hit ? (
            <span className="muted">
              {" "}
              · cite [{n}] · page {page ?? "—"} · section {section}
            </span>
          ) : (
            <span className="muted"> · select a cited chunk to jump</span>
          )}
        </div>
        {page ? (
          <a className="pdf-open" href={src} target="_blank" rel="noreferrer">
            Open page {page}
          </a>
        ) : (
          <a className="pdf-open" href={pdfUrl} target="_blank" rel="noreferrer">
            Open PDF
          </a>
        )}
      </header>
      <iframe
        key={src}
        className="pdf-frame"
        title="Corpus PDF"
        src={src}
      />
      {hit?.text ? (
        <details className="pdf-passage">
          <summary>Chunk passage</summary>
          <pre>{hit.text}</pre>
        </details>
      ) : null}
    </div>
  );
}
