"""核算校验：数量、合价、重名、规格冲突。"""

from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from api.services.quotes.catalog import money
from api.services.quotes.contract import current_pack_id
from api.services.quotes.packs import QuotePack, load_pack

_TWO = Decimal("0.01")
def _qty(raw: object) -> Decimal:
    try:
        n = Decimal(str(raw or "0"))
    except Exception:
        return Decimal("0")
    return n


def _norm_name(name: str) -> str:
    return re.sub(r"[\s\-_/／（）()【】\[\]]+", "", (name or "").lower())


def _spec_values(text: str, pack: QuotePack) -> dict[str, set[Decimal]]:
    values: dict[str, set[Decimal]] = {}
    normalized = re.sub(r"\s+", "", text or "")
    for pattern in pack.spec_patterns:
        try:
            matches = re.findall(pattern.regex, normalized, re.IGNORECASE)
        except re.error:
            continue
        found: set[Decimal] = set()
        for match in matches:
            raw = match[0] if isinstance(match, tuple) else match
            try:
                found.add(Decimal(str(raw)))
            except Exception:
                continue
        if found:
            values[pattern.key] = found
    return values


def verify_lines(lines: list[dict[str, Any]], *, pack_id: str | None = None) -> dict[str, Any]:
    pack = load_pack(pack_id or current_pack_id())
    issues: list[dict[str, Any]] = []
    by_name: dict[str, list[int]] = defaultdict(list)

    for i, row in enumerate(lines):
        name = str(row.get("name") or "").strip()
        if not name:
            issues.append(
                {
                    "code": "empty_name",
                    "level": "error",
                    "row": i + 1,
                    "message": f"第 {i + 1} 行名称为空",
                }
            )
            continue
        by_name[_norm_name(name)].append(i + 1)
        qty = _qty(row.get("qty"))
        if qty <= 0:
            issues.append(
                {
                    "code": "empty_qty",
                    "level": "error",
                    "row": i + 1,
                    "message": f"「{name}」数量为空或 ≤0",
                }
            )
        cost = _qty(row.get("costPrice") if row.get("costPrice") is not None else row.get("unitPrice"))
        sell = _qty(row.get("sellPrice") if row.get("sellPrice") is not None else row.get("unitPrice"))
        price = sell if sell > 0 else cost
        amount = _qty(row.get("amount"))
        expect = money(qty, price) if qty > 0 and price > 0 else Decimal("0")
        if expect > 0 and amount > 0:
            diff = abs(amount - expect)
            if diff > _TWO:
                issues.append(
                    {
                        "code": "amount_mismatch",
                        "level": "warn",
                        "row": i + 1,
                        "message": f"「{name}」合价 {amount} ≠ 数量×单价 {expect}",
                        "fix": float(expect),
                    }
                )
        if cost > 0 and sell > 0 and sell < cost:
            issues.append(
                {
                    "code": "sell_below_cost",
                    "level": "warn",
                    "row": i + 1,
                    "message": f"「{name}」售价低于成本价",
                }
            )

    for key, idxs in by_name.items():
        if len(idxs) < 2 or not key:
            continue
        issues.append(
            {
                "code": "duplicate_name",
                "level": "warn",
                "row": idxs[0],
                "rows": idxs,
                "message": f"疑似重复项：行 {', '.join(str(x) for x in idxs)}",
            }
        )

    # 同名同规格模式出现多个数值时报告冲突。
    spec_by_base: dict[tuple[str, str], set[Decimal]] = defaultdict(set)
    for row in lines:
        name = str(row.get("name") or "")
        specs = _spec_values(f"{name} {row.get('spec') or ''}", pack)
        base = _norm_name(name)
        for key, values in specs.items():
            if base:
                spec_by_base[(base, key)] |= values
    for (base, key), values in spec_by_base.items():
        if len(values) >= 2:
            issues.append(
                {
                    "code": "spec_conflict",
                    "level": "warn",
                    "row": 0,
                    "message": f"同名项的规格模式 {key} 出现多个数值：{', '.join(str(value) for value in sorted(values))}",
                }
            )

    errors = sum(1 for x in issues if x.get("level") == "error")
    warns = sum(1 for x in issues if x.get("level") == "warn")
    return {
        "ok": errors == 0,
        "errorCount": errors,
        "warnCount": warns,
        "issues": issues,
        "lineCount": len([r for r in lines if str(r.get("name") or "").strip()]),
    }


def apply_amount_fixes(lines: list[dict[str, Any]], report: dict[str, Any]) -> list[dict[str, Any]]:
    """按核算报告把 amount_mismatch 行合价改回数量×单价。"""
    fixes = {
        int(it["row"])
        for it in (report.get("issues") or [])
        if it.get("code") == "amount_mismatch" and it.get("fix") is not None
    }
    out: list[dict[str, Any]] = []
    for i, row in enumerate(lines):
        item = dict(row)
        if (i + 1) in fixes:
            qty = _qty(item.get("qty"))
            sell = _qty(item.get("sellPrice") if item.get("sellPrice") is not None else item.get("unitPrice"))
            cost = _qty(item.get("costPrice") if item.get("costPrice") is not None else item.get("unitPrice"))
            price = sell if sell > 0 else cost
            item["amount"] = money(qty, price)
            item["unitPrice"] = price
        out.append(item)
    return out
