"""Shared data contracts; keep traceability from chunk back to source."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DocumentPage:
    document_id: str
    source: str
    page: int
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Chunk:
    chunk_id: str
    document_id: str
    source: str
    page: int
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Heading:
    label: str
    title: str
    page: int
    y0: float


@dataclass
class BodyBlock:
    page: int
    text: str
    y0: float
    y1: float = 0.0


@dataclass
class FigureUnit:
    figure_id: str
    document_id: str
    source: str
    page: int
    figure_label: str
    caption: str
    image_path: str
    section: str = ""
    bbox_json: str = ""
    ocr_text: str = ""


@dataclass
class TableUnit:
    table_id: str
    document_id: str
    source: str
    page: int
    table_label: str
    title: str
    markdown: str
    section: str = ""
    bbox_json: str = ""
    row_count: int = 0
    col_count: int = 0


@dataclass
class ParsedDocument:
    document_id: str
    source: str
    title: str
    pages: list[DocumentPage]
    blocks: list[BodyBlock]
    headings: list[Heading]
    figures: list[FigureUnit]
    tables: list[TableUnit]
    page_count: int = 0
