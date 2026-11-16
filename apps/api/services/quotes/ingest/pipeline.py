from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from . import doc_parser, drawing_parser, quote_parser, table_parser
from .types import LineCandidate


_TABLE = {".xlsx", ".xlsm", ".csv"}
_DOCUMENT = {".docx", ".pdf"}
_DRAWING = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_QUOTE = {".xlsx", ".xlsm", ".csv", ".docx", ".pdf"}


def _path(item: Any) -> Path:
    if isinstance(item, (str, Path)):
        return Path(item)
    if isinstance(item, dict):
        return Path(str(item.get("path") or item.get("file") or ""))
    return Path(str(item))


def _kind(item: Any, options: dict[str, Any]) -> str:
    if isinstance(item, dict) and item.get("kind"):
        return str(item["kind"]).strip().lower()
    explicit = str(options.get("kind") or "").strip().lower()
    if explicit:
        return explicit
    name = str(_path(item).name).lower()
    if any(token in name for token in ("报价", "历史", "quote", "quotation")):
        return "quote"
    return ""


def _evidence_list(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(evidence.get("items"), list):
        return [x for x in evidence["items"] if isinstance(x, dict)]
    return [evidence] if evidence else []


def _merge_evidence(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    items = (_evidence_list(left) + _evidence_list(right))[:5]
    if not items:
        return {}
    result = dict(items[0])
    result["items"] = items
    return result


def _merge(lines: Iterable[LineCandidate]) -> list[LineCandidate]:
    merged: list[LineCandidate] = []
    by_key: dict[tuple[str, str], list[int]] = {}
    for line in lines:
        key = (line.name.strip().casefold(), line.spec.strip().casefold())
        if not key[0]:
            merged.append(line)
            continue
        candidates = by_key.setdefault(key, [])
        same = next((merged[i] for i in candidates if merged[i].unit.strip() == line.unit.strip()), None)
        if same is not None:
            if line.status == "price-only" or same.status == "price-only":
                # Keep a price-only candidate as a separate row so it never
                # changes the engineering quantity total.
                merged.append(line)
                candidates.append(len(merged) - 1)
                continue
            same.qty += line.qty
            if same.amount <= 0 and same.unit_price > 0:
                same.amount = same.qty * same.unit_price
            same.evidence = _merge_evidence(same.evidence, line.evidence)
            same.notes.extend(x for x in line.notes if x not in same.notes)
            if same.status == "confirmed" and line.status != "confirmed":
                same.status = line.status
            continue
        if candidates:
            units = {merged[i].unit.strip() for i in candidates if merged[i].unit.strip()}
            units.add(line.unit.strip())
            message = "单位不一致：" + " / ".join(sorted(x for x in units if x))
            line.status = "conflict"
            if message not in line.notes:
                line.notes.append(message)
            for index in candidates:
                previous = merged[index]
                previous.status = "conflict"
                if message not in previous.notes:
                    previous.notes.append(message)
            merged.append(line)
            candidates.append(len(merged) - 1)
            continue
        merged.append(line)
        candidates.append(len(merged) - 1)
    for index, line in enumerate(merged, 1):
        line.seq = line.seq or str(index)
        if not line.qty and "缺数量" not in line.notes:
            line.status = "pending" if line.status not in {"conflict", "price-only"} else line.status
            line.notes.append("缺数量")
        if line.unit_price <= 0 and line.status not in {"conflict", "price-only"}:
            line.status = "estimate" if line.status == "confirmed" else line.status
    return merged


def _project_name(paths: list[Path], options: dict[str, Any]) -> tuple[str, bool]:
    explicit = str(options.get("projectName") or "").strip()
    if explicit:
        return explicit, False
    if paths:
        first = paths[0]
        if first.suffix.lower() == ".docx":
            try:
                from docx import Document

                title = next((p.text.strip() for p in Document(str(first)).paragraphs if p.text.strip()), "")
                if title:
                    return title[:120], True
            except Exception:
                pass
        if first.suffix.lower() == ".pdf":
            try:
                from pypdf import PdfReader

                text = (PdfReader(str(first)).pages[0].extract_text() or "").strip()
                title = next((line.strip() for line in text.splitlines() if line.strip()), "")
                if title:
                    return title[:120], True
            except Exception:
                pass
        if first.suffix.lower() in _TABLE:
            try:
                from openpyxl import load_workbook

                wb = load_workbook(first, read_only=True, data_only=True)
                title = str(wb.worksheets[0].title or "").strip() if wb.worksheets else ""
                wb.close()
                if title:
                    return title, True
            except Exception:
                pass
        return first.stem, True
    return "", True


def _quality(lines: list[LineCandidate]) -> dict[str, Any]:
    total = len(lines)
    named = sum(1 for x in lines if x.name.strip())
    units = sum(1 for x in lines if x.unit.strip())
    pending = sum(1 for x in lines if x.status == "pending" or "缺数量" in x.notes)
    conflicts = sum(1 for x in lines if x.status == "conflict")
    evidence = sum(1 for x in lines if x.evidence)
    return {
        "lineCount": total,
        "namedRatio": named / total if total else 0,
        "unitRatio": units / total if total else 0,
        "pendingCount": pending,
        "conflictCount": conflicts,
        "evidenceRatio": evidence / total if total else 0,
    }


def ingest(files: list[str | Path] | tuple[str | Path, ...], options: dict[str, Any] | None = None) -> dict[str, Any]:
    options = options or {}
    paths = [_path(item) for item in files]
    all_lines: list[LineCandidate] = []
    warnings: list[str] = []
    for item, path in zip(files, paths):
        ext = path.suffix.lower()
        try:
            kind = _kind(item, options)
            if kind == "quote":
                parsed = quote_parser.parse(path, options=options)
            elif ext in _DRAWING:
                parsed = drawing_parser.parse(path, options=options)
            elif ext in _TABLE:
                parsed = table_parser.parse(path, options=options)
            elif ext in _DOCUMENT:
                parsed = doc_parser.parse(path, options=options)
            else:
                warnings.append(f"{path.name}：暂不支持的格式")
                continue
            all_lines.extend(parsed)
        except Exception as exc:  # one file cannot discard other extracted rows
            warnings.append(f"{path.name}：解析失败，{exc}")
    lines = _merge(all_lines)
    project_name, inferred = _project_name(paths, options)
    quality = _quality(lines)
    missing = []
    if not options.get("legacy"):
        for key, label in (("taxRate", "税率"), ("location", "地区"), ("durationDays", "工期")):
            if options.get(key) in (None, ""):
                missing.append(f"{label}未提供，按默认口径处理")
    warnings.extend(missing)
    counts = Counter(line.source for line in lines)
    return {
        "stage": "parsed",
        "projectName": project_name,
        "projectNameInferred": inferred,
        "lines": lines,
        "quality": quality,
        "counts": {"bySource": dict(counts)},
        "warnings": warnings,
    }


__all__ = ["ingest"]
