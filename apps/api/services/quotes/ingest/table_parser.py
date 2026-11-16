from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from api.services.tenders.tables import quote_role

from .types import LineCandidate, decimal


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value).strip()


def _mapping(row: list[Any]) -> dict[str, int] | None:
    result: dict[str, int] = {}
    for index, value in enumerate(row):
        role = _role(value)
        if role and role not in result:
            result[role] = index
    if "name" in result and any(k in result for k in ("qty", "price", "amount", "cost_price", "sell_price")):
        return result
    return None


def _role(value: Any) -> str | None:
    role = quote_role(value)
    if role:
        return role
    compact = _text(value).replace(" ", "").replace("　", "")
    english = compact.lower()
    if english in {"name", "item", "description", "material"}:
        return "name"
    if english in {"unit", "uom"}:
        return "unit"
    if english in {"qty", "quantity", "amountofwork"}:
        return "qty"
    if english in {"price", "unitprice", "costprice"}:
        return "price"
    if english in {"total", "amount", "totalamount"}:
        return "amount"
    if english in {"no", "number", "seq"}:
        return "seq"
    if english in {"spec", "model", "specification"}:
        return "spec"
    if any(token in compact for token in ("材料名称", "物品名称", "清单名称", "项目名称")):
        return "name"
    if compact in {"材料", "品名", "名称"}:
        return "name"
    return None


def _guess_mapping(rows: list[list[Any]], header_index: int) -> dict[str, int]:
    sample = rows[header_index] if rows else []
    width = max((len(row) for row in rows), default=len(sample))
    numeric: list[int] = []
    text_score: dict[int, int] = {}
    for index in range(width):
        values = [_text(row[index]) if index < len(row) else "" for row in rows[header_index:]]
        numeric_hits = sum(1 for value in values if value and (decimal(value) != 0 or value in {"0", "0.0"}))
        nonnumeric_hits = sum(1 for value in values if value and decimal(value) == 0 and value not in {"0", "0.0"})
        nonempty_count = len([x for x in values if x])
        threshold = 1 if nonempty_count <= 1 else max(2, (nonempty_count + 1) // 2)
        if numeric_hits >= threshold:
            numeric.append(index)
        text_score[index] = nonnumeric_hits
    name_index = max((i for i in text_score if i not in numeric), key=lambda i: text_score[i], default=0)
    mapping = {"name": name_index}
    numeric_data = list(numeric)
    if numeric_data and numeric_data[0] < name_index:
        mapping["seq"] = numeric_data.pop(0)
    if numeric_data:
        mapping["qty"] = numeric_data[0]
    if len(numeric_data) > 1:
        mapping["price"] = numeric_data[1]
    if len(numeric_data) > 2:
        mapping["amount"] = numeric_data[2]
    if width > name_index + 1 and "spec" not in mapping:
        for index in range(name_index + 1, width):
            if index not in numeric and _text(sample[index]):
                mapping["spec"] = index
                break
    if "unit" not in mapping:
        for index in range(name_index + 1, width):
            if index in numeric or index == mapping.get("spec"):
                continue
            values = [_text(row[index]) if index < len(row) else "" for row in rows[header_index:]]
            if any(value for value in values) and all(len(value) <= 8 for value in values if value):
                mapping["unit"] = index
                break
    return mapping


def _cell(row: list[Any], mapping: dict[str, int], role: str) -> Any:
    index = mapping.get(role)
    return row[index] if index is not None and index < len(row) else ""


def _rows_to_lines(rows: list[list[Any]], *, file_name: str, sheet_name: str, options: dict[str, Any]) -> list[LineCandidate]:
    nonempty = [i for i, row in enumerate(rows) if any(_text(x) for x in row)]
    if not nonempty:
        return []
    header_index = next((i for i in nonempty if _mapping(rows[i]) is not None), None)
    guessed = header_index is None
    if header_index is None:
        header_index = nonempty[0]
        mapping = _guess_mapping(rows, header_index)
    else:
        mapping = _mapping(rows[header_index]) or {}
    lines: list[LineCandidate] = []
    group: list[str] = []
    start = header_index + 1 if not guessed else header_index
    for row_index in range(start, len(rows)):
        row = rows[row_index]
        if not any(_text(x) for x in row):
            continue
        name = _text(_cell(row, mapping, "name"))
        spec = _text(_cell(row, mapping, "spec"))
        unit = _text(_cell(row, mapping, "unit"))
        qty = decimal(_cell(row, mapping, "qty"))
        price = decimal(_cell(row, mapping, "price"))
        cost = decimal(_cell(row, mapping, "cost_price"))
        sell = decimal(_cell(row, mapping, "sell_price"))
        amount = decimal(_cell(row, mapping, "amount"))
        if amount <= 0 and qty and (sell or price):
            amount = qty * (sell or price)
        if not name and "group" in mapping:
            candidate_group = _text(_cell(row, mapping, "group"))
            if candidate_group:
                group = [candidate_group]
        if not name:
            name = next((_text(value) for value in row if _text(value)), "待确认项")
        notes: list[str] = []
        if guessed:
            notes.append("表头为推测")
        if not qty:
            notes.append("缺数量")
        status = "pending" if not qty else ("estimate" if not (price or cost or sell) else "confirmed")
        evidence = {"file": file_name, "loc": f"{sheet_name}!{row_index + 1}", "raw": " | ".join(_text(x) for x in row)}
        lines.append(
            LineCandidate(
                seq=_text(_cell(row, mapping, "seq")) or str(len(lines) + 1),
                group_path=list(group),
                code=_text(_cell(row, mapping, "code")),
                name=name,
                spec=spec,
                unit=unit,
                qty=qty,
                unit_price=sell or price,
                cost_price=cost,
                amount=amount,
                source="table",
                evidence=evidence,
                confidence=0.55 if guessed else 0.95,
                status=status,
                notes=notes,
            )
        )
    return lines


def _parse_csv(path: Path, options: dict[str, Any]) -> list[LineCandidate]:
    raw = path.read_bytes()
    text = ""
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    rows = [list(row) for row in csv.reader(io.StringIO(text))]
    return _rows_to_lines(rows, file_name=path.name, sheet_name="Sheet1", options=options)


def _parse_workbook(path: Path, options: dict[str, Any]) -> list[LineCandidate]:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(path, data_only=False, read_only=True)
    except TypeError:
        wb = load_workbook(path, data_only=False, read_only=True)
    lines: list[LineCandidate] = []
    try:
        for ws in wb.worksheets:
            rows = [list(row) for row in ws.iter_rows(values_only=True)]
            lines.extend(_rows_to_lines(rows, file_name=path.name, sheet_name=ws.title or "Sheet1", options=options))
    finally:
        wb.close()
    return lines


def parse(path: str | Path, *, options: dict[str, Any] | None = None) -> list[LineCandidate]:
    options = options or {}
    source = Path(path)
    if source.suffix.lower() == ".csv":
        return _parse_csv(source, options)
    if source.suffix.lower() in {".xlsx", ".xlsm"}:
        return _parse_workbook(source, options)
    return []


__all__ = ["parse"]
