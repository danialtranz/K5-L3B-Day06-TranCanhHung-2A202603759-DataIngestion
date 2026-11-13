"""Fixed-size (A) and heading/paragraph (B) chunking + shared figure/table units."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .ingestion import _nearest_section, assign_sections_to_blocks
from .models import BodyBlock, Chunk, DocumentPage, ParsedDocument

# Strategy A
A_SIZE = 500
A_OVERLAP = 80
A_MIN_LAST = 100

# Strategy B
B_MAX_SIZE = 1000
B_MIN_SIZE = 200
B_SENTENCE_OVERLAP = 100

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chunk(
    *,
    strategy_id: str,
    seq: int,
    document_id: str,
    source: str,
    page: int,
    text: str,
    chunk_type: str,
    section: str,
    figure_id: str | None = None,
    table_id: str | None = None,
    extra: dict | None = None,
) -> Chunk:
    meta = {
        "chunk_type": chunk_type,
        "section": section or "",
        "strategy_id": strategy_id,
        "created_at": _now(),
        "figure_id": figure_id or "",
        "table_id": table_id or "",
    }
    if extra:
        meta.update(extra)
    return Chunk(
        chunk_id=f"{strategy_id}_{document_id}_{seq:05d}",
        document_id=document_id,
        source=source,
        page=page,
        text=text,
        metadata=meta,
    )


def fixed_size_windows(
    text: str,
    *,
    size: int = A_SIZE,
    overlap: int = A_OVERLAP,
    min_last_chunk: int = A_MIN_LAST,
) -> list[str]:
    text = text or ""
    if not text:
        return []
    if len(text) <= size:
        return [text]
    windows: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        if end >= n:
            remaining = text[start:n]
            if windows and len(remaining) < min_last_chunk:
                windows[-1] = (windows[-1] + remaining).strip()
            else:
                windows.append(remaining)
            break
        windows.append(text[start:end])
        start = end - overlap
        if start < 0:
            start = 0
        if start >= n:
            break
    return [w for w in windows if w.strip()]


def fixed_size(page: DocumentPage, size: int = A_SIZE, overlap: int = A_OVERLAP) -> list[Chunk]:
    """Split one page into overlapping windows (legacy helper)."""
    chunks: list[Chunk] = []
    section = (page.metadata or {}).get("section", "")
    for i, piece in enumerate(fixed_size_windows(page.text, size=size, overlap=overlap)):
        chunks.append(
            _chunk(
                strategy_id="A",
                seq=i,
                document_id=page.document_id,
                source=page.source,
                page=page.page,
                text=piece,
                chunk_type="text",
                section=section,
            )
        )
    return chunks


def _split_sentences(text: str) -> list[str]:
    parts = SENTENCE_SPLIT_RE.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def _pack_sentences(text: str, max_size: int, overlap: int) -> list[str]:
    if len(text) <= max_size:
        return [text]
    sentences = _split_sentences(text)
    if not sentences:
        return fixed_size_windows(text, size=max_size, overlap=overlap, min_last_chunk=0)
    out: list[str] = []
    buf = ""
    for sent in sentences:
        candidate = sent if not buf else f"{buf} {sent}"
        if len(candidate) <= max_size:
            buf = candidate
            continue
        if buf:
            out.append(buf)
            # overlap: keep tail of previous buffer
            tail = buf[-overlap:] if overlap > 0 and len(buf) > overlap else buf
            buf = f"{tail} {sent}".strip()
            if len(buf) > max_size:
                # hard split oversized sentence
                out.extend(fixed_size_windows(buf, size=max_size, overlap=overlap, min_last_chunk=0))
                buf = ""
        else:
            out.extend(fixed_size_windows(sent, size=max_size, overlap=overlap, min_last_chunk=0))
            buf = ""
    if buf:
        out.append(buf)
    return out


def by_paragraph(page: DocumentPage, max_size: int = B_MAX_SIZE) -> list[Chunk]:
    """Legacy single-page paragraph packer."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", page.text) if p.strip()]
    chunks: list[Chunk] = []
    section = (page.metadata or {}).get("section", "")
    buf = ""
    seq = 0
    for para in paras:
        pieces = _pack_sentences(para, max_size=max_size, overlap=B_SENTENCE_OVERLAP) if len(para) > max_size else [para]
        for piece in pieces:
            candidate = piece if not buf else f"{buf}\n\n{piece}"
            if buf and len(candidate) > max_size:
                if len(buf) >= B_MIN_SIZE or not chunks:
                    chunks.append(
                        _chunk(
                            strategy_id="B",
                            seq=seq,
                            document_id=page.document_id,
                            source=page.source,
                            page=page.page,
                            text=buf,
                            chunk_type="text",
                            section=section,
                        )
                    )
                    seq += 1
                    buf = piece
                else:
                    buf = candidate
            else:
                buf = candidate
    if buf:
        chunks.append(
            _chunk(
                strategy_id="B",
                seq=seq,
                document_id=page.document_id,
                source=page.source,
                page=page.page,
                text=buf,
                chunk_type="text",
                section=section,
            )
        )
    return chunks


def _figure_table_units(doc: ParsedDocument, strategy_id: str, start_seq: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    seq = start_seq
    for fig in doc.figures:
        text = f"{fig.figure_label}. {fig.caption}".strip()
        if fig.ocr_text:
            text = f"{text}\n{fig.ocr_text}"
        chunks.append(
            _chunk(
                strategy_id=strategy_id,
                seq=seq,
                document_id=doc.document_id,
                source=doc.source,
                page=fig.page,
                text=text,
                chunk_type="figure",
                section=fig.section,
                figure_id=fig.figure_id,
                extra={"image_path": fig.image_path},
            )
        )
        seq += 1
    for tbl in doc.tables:
        text = tbl.markdown
        chunks.append(
            _chunk(
                strategy_id=strategy_id,
                seq=seq,
                document_id=doc.document_id,
                source=doc.source,
                page=tbl.page,
                text=text,
                chunk_type="table",
                section=tbl.section,
                table_id=tbl.table_id,
                extra={"table_label": tbl.table_label, "table_title": tbl.title},
            )
        )
        seq += 1
    return chunks


def build_strategy_a(doc: ParsedDocument) -> list[Chunk]:
    """A: fixed-size over concatenated body with page/section tracking."""
    assign_sections_to_blocks(doc)
    chunks: list[Chunk] = []
    seq = 0
    # Walk pages in order; window within each page to keep page metadata accurate.
    for page in doc.pages:
        if not page.text.strip():
            continue
        page_blocks = [b for b in doc.blocks if b.page == page.page]
        section = _nearest_section(doc.headings, page.page, 0.0)
        if page_blocks:
            section = getattr(page_blocks[0], "section", section) or section

        windows = fixed_size_windows(
            page.text, size=A_SIZE, overlap=A_OVERLAP, min_last_chunk=A_MIN_LAST
        )
        # Approximate section per window by char offset → block
        offset = 0
        for piece in windows:
            local_section = section
            if page_blocks:
                # map midpoint of window to block
                mid = offset + len(piece) // 2
                # reconstruct offsets roughly via joined text
                joined = page.text
                # find block by searching piece location
                pos = joined.find(piece[:40]) if len(piece) >= 40 else joined.find(piece)
                if pos < 0:
                    pos = offset
                acc = 0
                for b in page_blocks:
                    nxt = acc + len(b.text) + 2
                    if acc <= pos < nxt:
                        local_section = getattr(b, "section", local_section) or local_section
                        break
                    acc = nxt
            chunks.append(
                _chunk(
                    strategy_id="A",
                    seq=seq,
                    document_id=doc.document_id,
                    source=doc.source,
                    page=page.page,
                    text=piece,
                    chunk_type="text",
                    section=local_section,
                )
            )
            seq += 1
            offset += max(1, len(piece) - A_OVERLAP)

    chunks.extend(_figure_table_units(doc, "A", seq))
    return chunks


def _iter_section_spans(doc: ParsedDocument) -> list[tuple[str, list[BodyBlock]]]:
    """Group body blocks by heading label spans."""
    assign_sections_to_blocks(doc)
    if not doc.blocks:
        return []
    spans: list[tuple[str, list[BodyBlock]]] = []
    current_label = getattr(doc.blocks[0], "section", "") or ""
    buf: list[BodyBlock] = []
    for b in doc.blocks:
        label = getattr(b, "section", "") or ""
        if buf and label != current_label:
            spans.append((current_label, buf))
            buf = [b]
            current_label = label
        else:
            buf.append(b)
            current_label = label
    if buf:
        spans.append((current_label, buf))
    return spans


def build_strategy_b(doc: ParsedDocument) -> list[Chunk]:
    """B: split by heading, pack paragraph blocks to max 1000 / min 200."""
    chunks: list[Chunk] = []
    seq = 0
    for section, blocks in _iter_section_spans(doc):
        buf = ""
        buf_page = blocks[0].page if blocks else 1
        for block in blocks:
            pieces = (
                _pack_sentences(block.text, max_size=B_MAX_SIZE, overlap=B_SENTENCE_OVERLAP)
                if len(block.text) > B_MAX_SIZE
                else [block.text]
            )
            for piece in pieces:
                candidate = piece if not buf else f"{buf}\n\n{piece}"
                if buf and len(candidate) > B_MAX_SIZE:
                    if len(buf) >= B_MIN_SIZE:
                        chunks.append(
                            _chunk(
                                strategy_id="B",
                                seq=seq,
                                document_id=doc.document_id,
                                source=doc.source,
                                page=buf_page,
                                text=buf,
                                chunk_type="text",
                                section=section,
                            )
                        )
                        seq += 1
                        buf = piece
                        buf_page = block.page
                    else:
                        # below min_size: keep merging even if slightly over max
                        buf = candidate
                        buf_page = block.page
                else:
                    if not buf:
                        buf_page = block.page
                    buf = candidate
        if buf:
            chunks.append(
                _chunk(
                    strategy_id="B",
                    seq=seq,
                    document_id=doc.document_id,
                    source=doc.source,
                    page=buf_page,
                    text=buf,
                    chunk_type="text",
                    section=section,
                )
            )
            seq += 1

    chunks.extend(_figure_table_units(doc, "B", seq))
    return chunks


def build_strategy_c_from_b(b_chunks: list[Chunk]) -> list[Chunk]:
    """C reuses B chunk texts/metadata; new IDs under strategy C."""
    out: list[Chunk] = []
    for i, c in enumerate(b_chunks):
        meta = dict(c.metadata or {})
        meta["strategy_id"] = "C"
        meta["created_at"] = _now()
        out.append(
            Chunk(
                chunk_id=f"C_{c.document_id}_{i:05d}",
                document_id=c.document_id,
                source=c.source,
                page=c.page,
                text=c.text,
                metadata=meta,
            )
        )
    return out
