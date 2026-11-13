import type { HitRecord } from "../types";

/** Split assistant text into answer body + optional trailing References block. */
export function splitAnswerAndRefs(content: string): { body: string; refsFromText: string[] } {
  const marker = content.search(/\nReferences\s*\n/i);
  if (marker < 0) {
    return { body: content.trim(), refsFromText: [] };
  }
  const body = content.slice(0, marker).trim();
  const refsBlock = content.slice(marker).replace(/^References\s*/i, "").trim();
  const refsFromText = refsBlock
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => /^\[\d+\]/.test(l));
  return { body, refsFromText };
}

/** Render answer text with [n] markers as clickable cite chips (React nodes via callback). */
export function tokenizeCites(body: string): Array<{ type: "text" | "cite"; value: string; num?: number }> {
  const parts: Array<{ type: "text" | "cite"; value: string; num?: number }> = [];
  const re = /\[(\d+)\]/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(body)) !== null) {
    if (m.index > last) {
      parts.push({ type: "text", value: body.slice(last, m.index) });
    }
    parts.push({ type: "cite", value: m[0], num: Number(m[1]) });
    last = m.index + m[0].length;
  }
  if (last < body.length) {
    parts.push({ type: "text", value: body.slice(last) });
  }
  return parts;
}

export function hitByCite(hits: HitRecord[] | undefined, num: number): HitRecord | undefined {
  return hits?.find((h) => (h.cite_num ?? h.rank) === num);
}
