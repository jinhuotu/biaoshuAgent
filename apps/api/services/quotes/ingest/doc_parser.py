from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .table_parser import _rows_to_lines
from .types import LineCandidate, decimal

_SECTION = re.compile(r"工程量清单|分部分项|材料表|设备表|技术参数|报价清单")
_BODY = re.compile(
    r"(?P<name>[\u4e00-\u9fffA-Za-z][^\n，,;；]{1,80}?)\s+(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>[\u4e00-\u9fffA-Za-z%㎡m²³/]{1,8})"
)


def _pdf_text(path: Path) -> tuple[str, list[int]]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "") for page in reader.pages]
        return "\n".join(pages), list(range(1, len(pages) + 1))
    except Exception:
        try:
            import fitz

            doc = fitz.open(str(path))
            pages = [page.get_text() or "" for page in doc]
            doc.close()
            return "\n".join(pages), list(range(1, len(pages) + 1))
        except Exception:
            return "", []


def _docx(path: Path) -> tuple[str, list[list[list[str]]]]:
    from docx import Document

    doc = Document(str(path))
    text = "\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())
    tables: list[list[list[str]]] = []
    for table in doc.tables:
        tables.append([[cell.text.strip() for cell in row.cells] for row in table.rows])
    return text, tables


def _body_lines(text: str, path: Path) -> list[LineCandidate]:
    lines: list[LineCandidate] = []
    section = ""
    for index, raw in enumerate((text or "").splitlines(), 1):
        if _SECTION.search(raw.strip()) and len(raw.strip()) <= 80:
            section = raw.strip()
        match = _BODY.search(raw.strip())
        if not match:
            continue
        name = match.group("name").strip(" ：:.-")
        qty = decimal(match.group("qty"))
        unit = match.group("unit")
        if not name or not qty:
            continue
        lines.append(
            LineCandidate(
                seq=str(len(lines) + 1),
                group_path=[section] if section else [],
                name=name,
                unit=unit,
                qty=qty,
                source="document",
                evidence={"file": path.name, "loc": f"page:{index}", "raw": raw.strip()},
                confidence=0.7,
                status="confirmed",
            )
        )
    return lines


def parse(path: str | Path, *, options: dict[str, Any] | None = None) -> list[LineCandidate]:
    options = options or {}
    source = Path(path)
    text = ""
    tables: list[list[list[str]]] = []
    pages: list[int] = []
    if source.suffix.lower() == ".docx":
        text, tables = _docx(source)
    elif source.suffix.lower() == ".pdf":
        text, pages = _pdf_text(source)
    else:
        return []
    if text:
        try:
            from api.services.tenders.document_parser import parse_document_structure

            text = parse_document_structure(text).full_text
        except Exception:
            pass
    lines: list[LineCandidate] = []
    for table_index, rows in enumerate(tables, 1):
        # Section headings are retained as group context by the shared row parser.
        lines.extend(_rows_to_lines(rows, file_name=source.name, sheet_name=f"table{table_index}", options=options))
    if source.suffix.lower() == ".pdf":
        pipe_rows = [[cell.strip() for cell in raw.strip().strip("|").split("|")] for raw in text.splitlines() if raw.count("|") >= 2]
        if pipe_rows:
            lines.extend(_rows_to_lines(pipe_rows, file_name=source.name, sheet_name="page1", options=options))
    lines.extend(_body_lines(text, source))
    return lines


__all__ = ["parse"]
