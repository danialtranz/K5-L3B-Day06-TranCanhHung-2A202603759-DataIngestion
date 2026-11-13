import { hitByCite, splitAnswerAndRefs, tokenizeCites } from "../lib/citations";
import type { HitRecord } from "../types";

type Props = {
  content: string;
  hits?: HitRecord[];
  references?: string[];
  onCiteClick?: (hit: HitRecord) => void;
};

export function CitedAnswer({ content, hits, references, onCiteClick }: Props) {
  const { body, refsFromText } = splitAnswerAndRefs(content);
  const tokens = tokenizeCites(body);
  const refs =
    references && references.length > 0
      ? references
      : refsFromText.length > 0
        ? refsFromText
        : (hits || []).map((h) => h.citation_ieee).filter(Boolean) as string[];

  return (
    <div className="cited-answer">
      <div className="answer-body">
        {tokens.map((t, i) => {
          if (t.type === "text") {
            return <span key={i}>{t.value}</span>;
          }
          const hit = t.num != null ? hitByCite(hits, t.num) : undefined;
          return (
            <button
              key={i}
              type="button"
              className="cite-chip"
              title={hit?.citation_ieee || `Citation [${t.num}]`}
              onClick={() => hit && onCiteClick?.(hit)}
            >
              [{t.num}]
            </button>
          );
        })}
      </div>

      {refs.length > 0 ? (
        <div className="references">
          <h4>References</h4>
          <ol className="ref-list">
            {refs.map((ref, i) => {
              const numMatch = ref.match(/^\[(\d+)\]/);
              const num = numMatch ? Number(numMatch[1]) : i + 1;
              const hit = hitByCite(hits, num);
              return (
                <li key={`${num}-${i}`}>
                  <button
                    type="button"
                    className="ref-item"
                    onClick={() => hit && onCiteClick?.(hit)}
                  >
                    {ref}
                  </button>
                  {hit?.image_url || hit?.image_ref ? (
                    <a
                      className="image-ref-link"
                      href={hit.image_url || hit.image_ref || "#"}
                      target="_blank"
                      rel="noreferrer"
                    >
                      [image]
                    </a>
                  ) : null}
                </li>
              );
            })}
          </ol>
        </div>
      ) : null}
    </div>
  );
}
