from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from api.services.quotes.vision import extract_json, rasterize_upload

from .types import LineCandidate, decimal


def _items(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("items")
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    # Compatibility for historical vision output. The values are copied into
    # the generic item contract without imposing industry-specific semantics.
    out: list[dict[str, Any]] = []
    for key in ("chargers", "equipment", "extras"):
        for row in data.get(key) or []:
            if isinstance(row, dict):
                out.append(row)
    return out


def parse(path: str | Path, *, options: dict[str, Any] | None = None) -> list[LineCandidate]:
    options = options or {}
    source = Path(path)
    data = options.get("vision_data")
    if not isinstance(data, dict):
        raw = options.get("vision_json")
        if isinstance(raw, dict):
            data = raw
        elif isinstance(raw, str):
            data = extract_json(raw)
        else:
            # Rasterization remains the common drawing entry point. The model
            # call is owned by the async router and can pass its JSON result.
            rasterize_upload(source.name, None, source.read_bytes())
            data = {"items": [], "uncertainties": ["图纸尚未完成视觉解析"]}
    lines: list[LineCandidate] = []
    uncertainties = [str(x).strip() for x in (data.get("uncertainties") or []) if str(x).strip()]
    for index, row in enumerate(_items(data), 1):
        name = str(row.get("name") or "").strip()
        qty = decimal(row.get("qty"))
        notes = [str(row.get("note") or "").strip()] if str(row.get("note") or "").strip() else []
        if not qty:
            notes.append("缺数量")
        status = "pending" if not qty else "confirmed"
        lines.append(
            LineCandidate(
                seq=str(row.get("seq") or index),
                group_path=[str(row.get("group") or "").strip()] if str(row.get("group") or "").strip() else [],
                code=str(row.get("code") or "").strip(),
                name=name,
                spec=str(row.get("spec") or row.get("specHint") or "").strip(),
                unit=str(row.get("unit") or "").strip(),
                qty=qty,
                source="drawing",
                evidence={"file": source.name, "loc": f"image index {index}", "raw": json.dumps(row, ensure_ascii=False)},
                confidence=float(row.get("confidence") or (0.55 if not qty else 0.85)),
                status=status,
                notes=notes,
            )
        )
    if uncertainties and lines:
        lines[-1].notes.extend(uncertainties)
    return lines


__all__ = ["parse"]
