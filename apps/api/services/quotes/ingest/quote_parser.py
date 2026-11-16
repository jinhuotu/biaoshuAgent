from __future__ import annotations

from pathlib import Path
from typing import Any

from api.services.tenders.quote import parse_quote_from_text, parse_quote_path

from .table_parser import parse as parse_table
from .types import LineCandidate


def _from_sheet(path: Path) -> list[LineCandidate]:
    sheet = parse_quote_path(path)
    if sheet is None:
        return []
    return [
        LineCandidate(
            seq=ln.seq,
            group_path=list(ln.groups),
            name=ln.name,
            spec=ln.spec,
            unit=ln.unit,
            qty=ln.qty,
            unit_price=ln.unit_price,
            amount=ln.amount,
            source="quote",
            evidence={"file": path.name, "loc": f"row:{ln.seq}", "raw": ln.name},
            confidence=0.95,
            status="price-only",
            notes=["仅价格候选，不参与工程量合计"],
        )
        for ln in sheet.lines
    ]


def _from_text(path: Path) -> list[LineCandidate]:
    text = ""
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader

            text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        except Exception:
            try:
                import fitz

                doc = fitz.open(str(path))
                text = "\n".join(page.get_text() or "" for page in doc)
                doc.close()
            except Exception:
                text = ""
    if not text:
        return []
    sheet = parse_quote_from_text(text, title_hint=path.stem)
    if sheet is None:
        return []
    return [
        LineCandidate(
            seq=ln.seq,
            group_path=list(ln.groups),
            name=ln.name,
            spec=ln.spec,
            unit=ln.unit,
            qty=ln.qty,
            unit_price=ln.unit_price,
            amount=ln.amount,
            source="quote",
            evidence={"file": path.name, "loc": "text", "raw": ln.name},
            confidence=0.7,
            status="price-only",
            notes=["仅价格候选，不参与工程量合计"],
        )
        for ln in sheet.lines
    ]


def parse(path: str | Path, *, options: dict[str, Any] | None = None) -> list[LineCandidate]:
    del options
    source = Path(path)
    if source.suffix.lower() in {".xlsx", ".xlsm", ".csv", ".docx"}:
        rows = _from_sheet(source)
        if rows:
            return rows
        if source.suffix.lower() in {".xlsx", ".xlsm", ".csv"}:
            result = []
            for row in parse_table(source):
                if not row.name:
                    continue
                row.source = "quote"
                row.status = "price-only"
                if "仅价格候选，不参与工程量合计" not in row.notes:
                    row.notes.append("仅价格候选，不参与工程量合计")
                result.append(row)
            return result
        return []
    if source.suffix.lower() == ".pdf":
        return _from_text(source)
    return []


__all__ = ["parse"]
