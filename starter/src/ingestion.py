"""Read and normalize Wiley DDBMS Ch.5 PDF: body, figures, tables, headings."""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
import pymupdf

from .models import (
    BodyBlock,
    DocumentPage,
    FigureUnit,
    Heading,
    ParsedDocument,
    TableUnit,
)

logger = logging.getLogger(__name__)

HEADING_LABEL_RE = re.compile(r"^5(?:\.\d+){1,3}$")
FIGURE_LABEL_RE = re.compile(r"^Figure\s+5\.\d+$", re.IGNORECASE)
TABLE_LABEL_RE = re.compile(r"^TABLE\s+5\.\d+", re.IGNORECASE)
EXERCISE_START_RE = re.compile(
    r"^(Assume|What|Why|How|Explain|Describe|Discuss|Compare|List|"
    r"Calculate|Show|Prove|Determine|Define|Identify|Give|Write|State)\b",
    re.IGNORECASE,
)
BOLD_HINT_RE = re.compile(r"bold", re.IGNORECASE)


@dataclass
class _Line:
    text: str
    page: int
    y0: float
    y1: float
    x0: float
    x1: float
    fonts: list[str]
    sizes: list[float]
    bbox: tuple[float, float, float, float]


def normalize_text(text: str) -> str:
    """NFKC normalize, then tidy whitespace without discarding paragraph breaks."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _is_boldish(fonts: list[str]) -> bool:
    return any(BOLD_HINT_RE.search(f or "") for f in fonts)


def _size_near_10(sizes: list[float], lo: float = 8.5, hi: float = 12.0) -> bool:
    if not sizes:
        return False
    avg = sum(sizes) / len(sizes)
    return lo <= avg <= hi


def _strip_run_in_title(title: str) -> str:
    title = normalize_text(title)
    if not title:
        return title
    # Heading title then run-in body after sentence period.
    m = re.match(r"^(.+?\.)\s+([A-Z].{40,})$", title)
    if m:
        return m.group(1).rstrip(".")
    return title


def _overlap_x(a: tuple[float, float], b: tuple[float, float]) -> float:
    left = max(a[0], b[0])
    right = min(a[1], b[1])
    return max(0.0, right - left)


def _extract_lines(page: pymupdf.Page, page_number: int) -> list[_Line]:
    data = page.get_text("dict")
    lines: list[_Line] = []
    for block in data.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            raw = "".join(s.get("text", "") for s in spans)
            text = normalize_text(raw)
            if not text:
                continue
            bbox = line.get("bbox") or spans[0].get("bbox")
            x0, y0, x1, y1 = bbox
            lines.append(
                _Line(
                    text=text,
                    page=page_number,
                    y0=float(y0),
                    y1=float(y1),
                    x0=float(x0),
                    x1=float(x1),
                    fonts=[s.get("font", "") for s in spans],
                    sizes=[float(s.get("size", 0)) for s in spans],
                    bbox=(float(x0), float(y0), float(x1), float(y1)),
                )
            )
    lines.sort(key=lambda ln: (ln.y0, ln.x0))
    return lines


def _detect_headings(lines: list[_Line]) -> list[Heading]:
    headings: list[Heading] = []
    i = 0
    while i < len(lines) - 1:
        label_ln = lines[i]
        title_ln = lines[i + 1]
        label = label_ln.text.strip()
        if not HEADING_LABEL_RE.match(label):
            i += 1
            continue
        prefer = _is_boldish(label_ln.fonts) and _size_near_10(label_ln.sizes)
        # Accept non-preferred matches only if next line does not look like body garbage.
        title = _strip_run_in_title(title_ln.text)
        if not title or EXERCISE_START_RE.match(title):
            i += 1
            continue
        if not prefer and not _is_boldish(title_ln.fonts):
            i += 1
            continue
        headings.append(Heading(label=label, title=title, page=label_ln.page, y0=label_ln.y0))
        i += 2
    return headings


def _nearest_section(headings: list[Heading], page: int, y0: float) -> str:
    best = ""
    best_key = (-1, -1.0)
    for h in headings:
        key = (h.page, h.y0)
        if h.page < page or (h.page == page and h.y0 <= y0 + 1e-3):
            if key >= best_key:
                best_key = key
                best = h.label
    return best


def _looks_like_page_number(text: str) -> bool:
    return bool(re.fullmatch(r"\d{1,4}", (text or "").strip()))


def _append_wrap_line(parts: list[str], lines: list[_Line], base_idx: int, base: _Line) -> list[int]:
    """Optionally append one wrapped caption continuation; return consumed indices."""
    consumed: list[int] = []
    # Wiley captions are usually one short sentence ending with '.'.
    if parts and parts[-1].rstrip().endswith((".", "!", "?")):
        return consumed
    if base_idx + 1 >= len(lines):
        return consumed
    nxt = lines[base_idx + 1]
    if not (0 <= nxt.y0 - base.y1 <= 20):
        return consumed
    if FIGURE_LABEL_RE.match(nxt.text) or TABLE_LABEL_RE.match(nxt.text) or HEADING_LABEL_RE.match(nxt.text):
        return consumed
    if _looks_like_page_number(nxt.text):
        return consumed
    # Body paragraphs after a caption often start lowercase mid-flow; skip those.
    if nxt.text and nxt.text[0].islower():
        return consumed
    parts.append(nxt.text)
    consumed.append(base_idx + 1)
    return consumed


def _find_caption_after(
    lines: list[_Line], idx: int, label_bbox: tuple[float, float, float, float]
) -> tuple[str, list[int]]:
    """Locate caption for a Figure label.

    Wiley Ch.5 usually places caption on the *same baseline to the right* of
    ``Figure 5.x``. Some figures use rotated text with caption *above* the label.
    Returns ``(caption, consumed_line_indices)``.
    """
    label_x0, label_y0, label_x1, label_y1 = label_bbox
    label_width = max(label_x1 - label_x0, 1.0)

    # 1) Same-line caption to the right (dominant layout in this corpus).
    if idx + 1 < len(lines):
        nxt = lines[idx + 1]
        same_baseline = abs(nxt.y0 - label_y0) <= 10
        to_the_right = nxt.x0 >= label_x1 - 5
        if same_baseline and to_the_right and not _looks_like_page_number(nxt.text):
            if not FIGURE_LABEL_RE.match(nxt.text) and not TABLE_LABEL_RE.match(nxt.text):
                parts = [nxt.text]
                consumed = [idx + 1]
                consumed.extend(_append_wrap_line(parts, lines, idx + 1, nxt))
                return normalize_text(" ".join(parts)), consumed

    # 2) Caption immediately below, same column (classic layout).
    if idx + 1 < len(lines):
        nxt = lines[idx + 1]
        below = nxt.y0 >= label_y1 - 2 and (nxt.y0 - label_y1) <= 80
        if below and not _looks_like_page_number(nxt.text):
            ov = _overlap_x((label_x0, label_x1), (nxt.x0, nxt.x1))
            if ov / label_width >= 0.3 or ov / max(nxt.x1 - nxt.x0, 1.0) >= 0.3:
                parts = [nxt.text]
                consumed = [idx + 1]
                consumed.extend(_append_wrap_line(parts, lines, idx + 1, nxt))
                return normalize_text(" ".join(parts)), consumed

    # 3) Caption above label (rotated / sideways figure labels).
    if idx > 0:
        prev = lines[idx - 1]
        if (
            prev.y1 <= label_y0 + 5
            and label_y0 - prev.y1 <= 200
            and not _looks_like_page_number(prev.text)
            and not FIGURE_LABEL_RE.match(prev.text)
            and not TABLE_LABEL_RE.match(prev.text)
            and not HEADING_LABEL_RE.match(prev.text)
        ):
            ov = _overlap_x((label_x0, label_x1), (prev.x0, prev.x1))
            prev_width = max(prev.x1 - prev.x0, 1.0)
            # Rotated labels are narrow; require strong horizontal alignment.
            if ov / label_width >= 0.5 or ov / prev_width >= 0.5:
                return normalize_text(prev.text), [idx - 1]

    return "", []


def _save_nearest_image(
    page: pymupdf.Page,
    page_number: int,
    label_y0: float,
    out_dir: Path,
    figure_id: str,
) -> tuple[str, str]:
    """Crop image whose bottom is closest above the figure label."""
    out_dir.mkdir(parents=True, exist_ok=True)
    images = page.get_images(full=True)
    best = None
    best_dist = 1e18
    for img in images:
        xref = img[0]
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            continue
        for rect in rects:
            if rect.y1 > label_y0 + 5:
                continue
            dist = label_y0 - rect.y1
            if 0 <= dist < best_dist:
                best_dist = dist
                best = (xref, rect)
    if best is None:
        # Fallback: full-page render band above label.
        path = out_dir / f"{figure_id}.png"
        clip = pymupdf.Rect(0, max(0, label_y0 - 320), page.rect.width, label_y0)
        pix = page.get_pixmap(clip=clip, dpi=144)
        pix.save(str(path))
        return str(path), json.dumps([clip.x0, clip.y0, clip.x1, clip.y1])

    xref, rect = best
    path = out_dir / f"{figure_id}.png"
    try:
        pix = page.get_pixmap(clip=rect, dpi=144)
        pix.save(str(path))
        return str(path), json.dumps([rect.x0, rect.y0, rect.x1, rect.y1])
    except Exception:
        # Extract raw image bytes if clip fails.
        try:
            info = page.parent.extract_image(xref)
            path.write_bytes(info["image"])
            return str(path), json.dumps([rect.x0, rect.y0, rect.x1, rect.y1])
        except Exception as exc:
            logger.warning("Could not save figure %s: %s", figure_id, exc)
            return "", json.dumps([rect.x0, rect.y0, rect.x1, rect.y1])


def _rows_to_markdown(header: list[str] | None, rows: list[list[str]], title: str, label: str) -> str:
    lines = [normalize_text(f"{label} {title}".strip())]
    if not rows and not header:
        return lines[0]
    data = rows
    cols = header
    if cols is None and data:
        cols = [f"col_{i+1}" for i in range(len(data[0]))]
    if cols:
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
    for row in data:
        cells = [normalize_text(c.replace("\n", " ")) for c in row]
        if cols and len(cells) < len(cols):
            cells += [""] * (len(cols) - len(cells))
        lines.append("| " + " | ".join(cells[: len(cols) if cols else None]) + " |")
    return "\n".join(lines)


def _extract_tables_for_page(
    page: pymupdf.Page,
    page_number: int,
    lines: list[_Line],
    document_id: str,
    source: str,
    headings: list[Heading],
) -> tuple[list[TableUnit], set[int]]:
    """Return table units and line indices consumed (label/title/body region approx)."""
    units: list[TableUnit] = []
    consumed: set[int] = set()
    finder = None
    try:
        finder = page.find_tables()
    except Exception as exc:
        logger.warning("find_tables failed on page %s: %s", page_number, exc)

    table_objs = list(getattr(finder, "tables", []) or [])

    for idx, ln in enumerate(lines):
        m = TABLE_LABEL_RE.match(ln.text)
        if not m:
            continue
        label = normalize_text(m.group(0))
        # Title often remains on same line after label, or next line.
        rest = normalize_text(TABLE_LABEL_RE.sub("", ln.text).strip(" :-"))
        title = rest
        title_idx = idx
        if not title and idx + 1 < len(lines):
            title = lines[idx + 1].text
            title_idx = idx + 1
            if HEADING_LABEL_RE.match(title) or FIGURE_LABEL_RE.match(title) or TABLE_LABEL_RE.match(title):
                title = ""
                title_idx = idx
        consumed.add(idx)
        if title_idx != idx:
            consumed.add(title_idx)

        best_table = None
        best_dist = 1e18
        for t in table_objs:
            bbox = getattr(t, "bbox", None)
            if not bbox:
                continue
            y0 = float(bbox[1])
            if y0 + 5 < ln.y0:
                continue
            dist = y0 - ln.y0
            if dist < best_dist:
                best_dist = dist
                best_table = t

        markdown = ""
        row_count = 0
        col_count = 0
        bbox_json = ""
        if best_table is not None:
            try:
                extracted = best_table.extract()
                rows = [[normalize_text(str(c or "")) for c in row] for row in extracted]
                header = rows[0] if rows else None
                body = rows[1:] if rows else []
                if header:
                    col_count = len(header)
                row_count = len(rows)
                markdown = _rows_to_markdown(header, body, title, label)
                bbox_json = json.dumps(list(best_table.bbox))
            except Exception as exc:
                logger.warning("table extract failed page %s: %s", page_number, exc)

        if not markdown:
            # Fallback: collect following lines until blank-ish gap / next structural label.
            buf = [normalize_text(f"{label} {title}".strip())]
            j = title_idx + 1
            while j < len(lines):
                t = lines[j].text
                if FIGURE_LABEL_RE.match(t) or TABLE_LABEL_RE.match(t) or HEADING_LABEL_RE.match(t):
                    break
                if lines[j].y0 - lines[j - 1].y1 > 40 and j > title_idx + 1:
                    break
                buf.append(t)
                consumed.add(j)
                j += 1
                if j - title_idx > 40:
                    break
            markdown = "\n".join(buf)

        safe_label = re.sub(r"[^\w]+", "_", label).strip("_").lower()
        table_id = f"{document_id}_{safe_label}_p{page_number}"
        units.append(
            TableUnit(
                table_id=table_id,
                document_id=document_id,
                source=source,
                page=page_number,
                table_label=label,
                title=title,
                markdown=markdown,
                section=_nearest_section(headings, page_number, ln.y0),
                bbox_json=bbox_json,
                row_count=row_count,
                col_count=col_count,
            )
        )
    return units, consumed


def load_pdf(
    path: Path,
    document_id: str,
    source: str,
    *,
    figures_dir: Path | None = None,
    title: str = "",
) -> ParsedDocument:
    """Parse PDF into body pages/blocks, headings, figure units, table units."""
    path = Path(path)
    figures_dir = figures_dir or (path.parent / "figures")
    doc = pymupdf.open(path)

    all_lines: list[_Line] = []
    page_lines: dict[int, list[_Line]] = {}
    for i in range(doc.page_count):
        page_number = i + 1
        page = doc.load_page(i)
        lines = _extract_lines(page, page_number)
        page_lines[page_number] = lines
        all_lines.extend(lines)

    headings = _detect_headings(all_lines)
    logger.info("Detected %d headings", len(headings))

    figures: list[FigureUnit] = []
    tables: list[TableUnit] = []
    consumed_global: dict[int, set[int]] = {p: set() for p in page_lines}

    # Pass 1: tables (so body can drop table lines)
    for page_number, lines in page_lines.items():
        page = doc.load_page(page_number - 1)
        units, consumed = _extract_tables_for_page(
            page, page_number, lines, document_id, source, headings
        )
        tables.extend(units)
        consumed_global[page_number].update(consumed)

    # Pass 2: figures
    for page_number, lines in page_lines.items():
        page = doc.load_page(page_number - 1)
        for idx, ln in enumerate(lines):
            if not FIGURE_LABEL_RE.match(ln.text):
                continue
            label = ln.text.strip()
            caption, caption_idxs = _find_caption_after(lines, idx, ln.bbox)
            if not caption:
                caption = f"[uncaptioned figure on page {page_number}]"
                logger.warning("Uncaptioned figure %s on page %s", label, page_number)
            consumed_global[page_number].add(idx)
            consumed_global[page_number].update(caption_idxs)
            safe = re.sub(r"[^\w]+", "_", label).strip("_").lower()
            figure_id = f"{document_id}_{safe}_p{page_number}"
            image_path, bbox_json = _save_nearest_image(
                page, page_number, ln.y0, figures_dir, figure_id
            )
            figures.append(
                FigureUnit(
                    figure_id=figure_id,
                    document_id=document_id,
                    source=source,
                    page=page_number,
                    figure_label=label,
                    caption=caption,
                    image_path=image_path,
                    section=_nearest_section(headings, page_number, ln.y0),
                    bbox_json=bbox_json,
                )
            )

    # Also consume heading label+title lines from body text stream
    for i, ln in enumerate(all_lines):
        if HEADING_LABEL_RE.match(ln.text.strip()):
            # mark in page index space
            local_lines = page_lines[ln.page]
            for j, cand in enumerate(local_lines):
                if cand is ln or (cand.y0 == ln.y0 and cand.text == ln.text):
                    consumed_global[ln.page].add(j)
                    if j + 1 < len(local_lines):
                        consumed_global[ln.page].add(j + 1)
                    break

    blocks: list[BodyBlock] = []
    pages: list[DocumentPage] = []
    empty_pages: list[int] = []

    for page_number in sorted(page_lines):
        lines = page_lines[page_number]
        kept: list[_Line] = [
            ln for j, ln in enumerate(lines) if j not in consumed_global[page_number]
        ]
        # Merge into blocks by vertical proximity (PyMuPDF block-ish).
        page_blocks: list[BodyBlock] = []
        buf: list[str] = []
        y0 = 0.0
        y1 = 0.0
        for ln in kept:
            if not buf:
                buf = [ln.text]
                y0, y1 = ln.y0, ln.y1
                continue
            if ln.y0 - y1 <= 14:
                buf.append(ln.text)
                y1 = ln.y1
            else:
                text = normalize_text(" ".join(buf))
                if text:
                    page_blocks.append(BodyBlock(page=page_number, text=text, y0=y0, y1=y1))
                buf = [ln.text]
                y0, y1 = ln.y0, ln.y1
        if buf:
            text = normalize_text(" ".join(buf))
            if text:
                page_blocks.append(BodyBlock(page=page_number, text=text, y0=y0, y1=y1))

        page_text = normalize_text("\n\n".join(b.text for b in page_blocks))
        if not page_text:
            empty_pages.append(page_number)
        blocks.extend(page_blocks)
        pages.append(
            DocumentPage(
                document_id=document_id,
                source=source,
                page=page_number,
                text=page_text,
                metadata={"empty": not bool(page_text), "block_count": len(page_blocks)},
            )
        )

    if empty_pages:
        logger.warning("Pages with little/no body text: %s", empty_pages)

    doc.close()
    return ParsedDocument(
        document_id=document_id,
        source=source,
        title=title or path.stem,
        pages=pages,
        blocks=blocks,
        headings=headings,
        figures=figures,
        tables=tables,
        page_count=len(pages),
    )


def assign_sections_to_blocks(doc: ParsedDocument) -> None:
    """Attach nearest prior heading label onto each body block (in-place via side map)."""
    # Stored on pages metadata for convenience.
    by_page: dict[int, list[str]] = {}
    for b in doc.blocks:
        sec = _nearest_section(doc.headings, b.page, b.y0)
        by_page.setdefault(b.page, [])
        # monkey-patch attribute for chunking
        setattr(b, "section", sec)
