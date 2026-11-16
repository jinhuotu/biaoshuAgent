from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from api.services.quotes.packs import QuotePack
from common.errors import AppError, ErrorCode


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def _spec_values(text: str, pack: QuotePack) -> dict[str, set[Decimal]]:
    result: dict[str, set[Decimal]] = {}
    for pattern in pack.spec_patterns:
        try:
            matches = re.findall(pattern.regex, re.sub(r"\s+", "", text or ""), re.IGNORECASE)
        except re.error:
            continue
        values: set[Decimal] = set()
        for match in matches:
            raw = match[0] if isinstance(match, tuple) else match
            try:
                values.add(Decimal(str(raw)))
            except Exception:
                continue
        if values:
            result[pattern.key] = values
    return result


def _subtotals_match(summary: dict[str, Any]) -> bool:
    tree = summary.get("subTotals")
    if not isinstance(tree, dict):
        return True
    def matches(node: dict[str, Any]) -> bool:
        children = [child for child in node.get("children") or [] if isinstance(child, dict)]
        if not children:
            return True
        total = sum((_decimal(child.get("total")) for child in children), Decimal("0"))
        return abs(_decimal(node.get("total")) - total) <= Decimal("0.01") and all(matches(child) for child in children)
    return matches(tree)


def run_quality(lines: list[dict[str, Any]], summary: dict[str, Any], pack: QuotePack) -> dict[str, Any]:
    total = len(lines)
    named = sum(1 for row in lines if str(row.get("name") or "").strip())
    units = sum(1 for row in lines if str(row.get("unit") or "").strip())
    priced = sum(1 for row in lines if _decimal(row.get("sellPrice") or row.get("unitPrice")) > 0)
    pending = sum(1 for row in lines if str(row.get("status") or "").lower() == "pending" or _decimal(row.get("sellPrice") or row.get("unitPrice")) <= 0)
    conflict = sum(1 for row in lines if str(row.get("status") or "").lower() == "conflict")
    groups: dict[tuple[str, str], list[tuple[Decimal, int]]] = {}
    for index, row in enumerate(lines):
        name = re.sub(r"\s+", "", str(row.get("name") or "").lower())
        price = _decimal(row.get("sellPrice") or row.get("unitPrice"))
        for key, values in _spec_values(f"{row.get('name') or ''} {row.get('spec') or ''}", pack).items():
            for value in values:
                groups.setdefault((name, key), []).append((value, index))
    outliers: set[int] = set()
    spec_conflict_rows: set[int] = set()
    for values in groups.values():
        if len(values) < 2:
            continue
        if len({value for value, _index in values}) > 1:
            spec_conflict_rows.update(index for _value, index in values)
        prices = sorted(_decimal(lines[index].get("sellPrice") or lines[index].get("unitPrice")) for _value, index in values)
        median = prices[len(prices) // 2] if len(prices) % 2 else (prices[len(prices) // 2 - 1] + prices[len(prices) // 2]) / Decimal("2")
        if median <= 0:
            continue
        for _value, index in values:
            price = _decimal(lines[index].get("sellPrice") or lines[index].get("unitPrice"))
            if abs(price - median) / median >= Decimal("0.5"):
                outliers.add(index)
    return {
        "lineCount": total,
        "namedRatio": named / total if total else 0,
        "unitRatio": units / total if total else 0,
        "pricedRatio": priced / total if total else 0,
        "pendingCount": pending,
        "conflictCount": max(conflict, len(spec_conflict_rows)),
        "subtotalMatch": _subtotals_match(summary),
        "outlierCount": len(outliers),
    }


def enforce_quality(quality: dict[str, Any], *, force_export: bool = False) -> list[str]:
    warnings: list[str] = []
    if float(quality.get("pricedRatio") or 0) < 0.6:
        warnings.append("有效报价行比例低于 0.6")
    pending = int(quality.get("pendingCount") or 0)
    if pending:
        warnings.append(f"含 {pending} 项待核价")
    if not quality.get("subtotalMatch", True):
        warnings.append("分组小计与明细合计不一致")
    if warnings and not force_export:
        raise AppError(ErrorCode.VALIDATION, "质量门未通过：" + "；".join(warnings), status_code=422)
    return warnings


__all__ = ["enforce_quality", "run_quality"]
