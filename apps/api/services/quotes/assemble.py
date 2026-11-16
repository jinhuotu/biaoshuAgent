"""把读图结果摊成报价行，补规则项，对价目。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from api.services.quotes.catalog import (
    OFFICIAL_SCORE,
    CatalogItem,
    load_catalog,
    money,
    pick_catalog,
)
from api.services.quotes.library import stored_paths
from api.services.quotes.packs import load_pack


def _qty(raw: object) -> Decimal:
    try:
        n = Decimal(str(raw or "0"))
    except Exception:
        return Decimal("0")
    return n if n > 0 else Decimal("0")


def _txt(raw: object) -> str:
    return str(raw or "").strip()


def empty_line(**kwargs: Any) -> dict[str, Any]:
    row = {
        "seq": "",
        "code": "",
        "name": "",
        "spec": "",
        "unit": "项",
        "qty": Decimal("0"),
        "unitPrice": Decimal("0"),
        "costPrice": Decimal("0"),
        "sellPrice": Decimal("0"),
        "amount": Decimal("0"),
        "source": "manual",
        "matchName": "",
        "matchScore": 0.0,
        "matchStatus": "none",
        "note": "",
    }
    row.update(kwargs)
    return row


def flatten_bom(data: dict[str, Any], *, pack_id: str = "default_charger") -> tuple[list[dict[str, Any]], list[str]]:
    pack = load_pack(pack_id)
    warnings = [_txt(x) for x in (data.get("uncertainties") or []) if _txt(x)]
    lines: list[dict[str, Any]] = []
    rule_sources: dict[str, Decimal] = {}

    def resolved(row: dict[str, Any], code: str, fallback_name: str = "") -> tuple[str, str, str]:
        name = _txt(row.get("name")) or pack.display_name(code, fallback_name or code or "设备")
        unit = pack.normalize_unit(row.get("unit"))
        if not unit:
            unit = pack.normalize_unit(pack.unit_by_code.get(code)) or pack.fallback_unit()
        note = _txt(row.get("note"))
        if not _txt(row.get("unit")) and not pack.unit_by_code.get(code):
            note = f"{note}；单位待确认" if note else "单位待确认"
        return name, unit, note

    generic_items = data.get("items") or []
    for row in generic_items:
        if not isinstance(row, dict):
            continue
        name, unit, note = resolved(row, _txt(row.get("code")), "待确认项")
        qty = _qty(row.get("qty"))
        if _txt(row.get("code")):
            rule_sources[_txt(row.get("code")).lower()] = rule_sources.get(_txt(row.get("code")).lower(), Decimal("0")) + qty
        lines.append(
            empty_line(
                seq=_txt(row.get("seq")),
                code=_txt(row.get("code")),
                name=name,
                spec=_txt(row.get("spec") or row.get("specHint")),
                unit=unit,
                qty=qty,
                source="vision",
                note=note,
            )
        )

    for row in ([] if generic_items else data.get("chargers")) or []:
        if not isinstance(row, dict):
            continue
        code = _txt(row.get("code")).lower()
        qty = _qty(row.get("qty"))
        if qty <= 0:
            continue
        name, unit, note = resolved(row, code)
        rule_sources[code] = rule_sources.get(code, Decimal("0")) + qty
        lines.append(
            empty_line(
                code=code or "charger",
                name=name,
                spec=_txt(row.get("specHint") or row.get("spec")),
                unit=unit,
                qty=qty,
                source="vision",
                note=note,
            )
        )

    for row in ([] if generic_items else data.get("equipment")) or []:
        if not isinstance(row, dict):
            continue
        code = _txt(row.get("code")).lower()
        qty = _qty(row.get("qty"))
        if qty <= 0:
            continue
        name, unit, note = resolved(row, code)
        lines.append(
            empty_line(
                code=code,
                name=name,
                spec=_txt(row.get("specHint") or row.get("spec")),
                unit=unit,
                qty=qty,
                source="vision",
                note=note,
            )
        )

    for row in ([] if generic_items else data.get("extras")) or []:
        if not isinstance(row, dict):
            continue
        name = _txt(row.get("name"))
        qty = _qty(row.get("qty"))
        if not name or qty <= 0:
            continue
        lines.append(
            empty_line(
                name=name,
                spec=_txt(row.get("specHint") or row.get("spec")),
                unit=pack.normalize_unit(row.get("unit")) or pack.fallback_unit(),
                qty=qty,
                source="vision",
                note=(
                    f"{_txt(row.get('note'))}；单位待确认"
                    if not _txt(row.get("unit")) and _txt(row.get("note"))
                    else ("单位待确认" if not _txt(row.get("unit")) else _txt(row.get("note")))
                ),
            )
        )

    existing_names = {_txt(row.get("name")) for row in lines}
    for rule in pack.quantity_rules:
        if rule.item in existing_names:
            continue
        source_qty = Decimal("0")
        if rule.codes:
            source_qty = sum((rule_sources.get(code.lower(), Decimal("0")) for code in rule.codes), Decimal("0"))
        else:
            source_qty = sum(
                (_qty(row.get("qty")) for row in lines if _txt(row.get("unit")) in {pack.normalize_unit(x) for x in rule.per}),
                Decimal("0"),
            )
        qty = source_qty * rule.factor
        if qty <= 0:
            continue
        rule_code = next((code for code, aliases in pack.synonyms.items() if aliases and aliases[0] == rule.item), "")
        lines.append(
            empty_line(
                code=rule_code,
                name=rule.item,
                unit=pack.unit_by_code.get(rule_code, pack.fallback_unit()),
                qty=qty,
                source="rule",
                note=rule.note or "单位待确认",
            )
        )

    if not lines:
        warnings.append("未识别到可用工程量，请补充说明或改用更清晰的资料")
    return lines, warnings


def apply_catalog(lines: list[dict[str, Any]], catalog: list[CatalogItem], *, pack_id: str = "default_charger") -> list[str]:
    notes: list[str] = []
    if not catalog:
        notes.append("所选知识库没有可用价目 Excel（需含名称、单价列），单价留空待核价")
        return notes
    unmatched = 0
    estimates = 0
    for row in lines:
        hit, score = pick_catalog(
            name=str(row.get("name") or ""),
            spec=str(row.get("spec") or ""),
            code=str(row.get("code") or ""),
            catalog=catalog,
            pack_id=pack_id,
        )
        row["matchScore"] = round(float(score or 0), 3)
        if hit is None:
            unmatched += 1
            row["matchStatus"] = "none"
            continue
        cost = hit.cost_price if hit.cost_price > 0 else hit.unit_price
        sell = hit.sell_price if hit.sell_price > 0 else hit.unit_price
        row["costPrice"] = cost
        row["sellPrice"] = sell
        row["unitPrice"] = sell
        row["amount"] = money(_qty(row.get("qty")), sell)
        row["matchName"] = hit.name
        if hit.spec:
            row["spec"] = hit.spec
        if not str(row.get("unit") or "").strip() or str(row.get("unit") or "") == "项":
            if hit.unit:
                row["unit"] = hit.unit
        extra = "；".join(x for x in (hit.category, hit.scene) if x)
        if extra:
            old = str(row.get("note") or "").strip()
            row["note"] = f"{old}；{extra}" if old else extra
        official = score >= OFFICIAL_SCORE
        row["matchStatus"] = "official" if official else "estimate"
        row["source"] = "catalog" if official else "estimate"
        if not official:
            estimates += 1
            notes.append(f"「{row['name']}」按「{hit.name}」估价（{score:.0%}），须确认后才能作为正式价")
    if unmatched:
        notes.append(f"{unmatched} 项未匹配到价目，单价留空")
    if estimates:
        notes.append(f"{estimates} 项为低分估价，请点确认或换条后再导出")
    return notes


def is_pending_price(row: dict[str, Any]) -> bool:
    st = str(row.get("matchStatus") or "").strip().lower()
    price = _qty(row.get("sellPrice")) or _qty(row.get("unitPrice")) or _qty(row.get("costPrice"))
    if price <= 0:
        return True
    return st == "estimate"


def pending_price_count(lines: list[dict[str, Any]]) -> int:
    return sum(1 for r in lines if str(r.get("name") or "").strip() and is_pending_price(r))


def number_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, row in enumerate(lines, start=1):
        item = dict(row)
        item["seq"] = str(i)
        qty = _qty(item.get("qty"))
        price = _qty(item.get("unitPrice"))
        cost = _qty(item.get("costPrice")) or price
        sell = _qty(item.get("sellPrice")) or price
        item["qty"] = qty
        item["unitPrice"] = price
        item["costPrice"] = cost
        item["sellPrice"] = sell
        item["amount"] = money(qty, price)
        out.append(item)
    return out


def catalog_from_docs(docs: list[dict], *, pack_id: str = "default_charger") -> tuple[list[CatalogItem], list[str], dict[str, Any]]:
    paths = stored_paths(docs)
    items, errors = load_catalog(paths, pack_id=pack_id)
    meta = {
        "fileCount": len(paths),
        "itemCount": len(items),
        "files": [p.name for p in paths],
        "errors": errors,
    }
    return items, errors, meta


def _dump_line(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "seq": str(row.get("seq") or ""),
        "code": str(row.get("code") or ""),
        "name": str(row.get("name") or ""),
        "spec": str(row.get("spec") or ""),
        "unit": str(row.get("unit") or "项"),
        "qty": float(row.get("qty") or 0),
        "unitPrice": float(row.get("unitPrice") or 0),
        "costPrice": float(row.get("costPrice") or 0),
        "sellPrice": float(row.get("sellPrice") or 0),
        "amount": float(row.get("amount") or 0),
        "source": str(row.get("source") or "manual"),
        "matchName": str(row.get("matchName") or ""),
        "matchScore": float(row.get("matchScore") or 0),
        "matchStatus": str(row.get("matchStatus") or "none"),
        "note": str(row.get("note") or ""),
    }


async def recognize_from_bom(
    *,
    data: dict[str, Any],
    docs: list[dict],
    project_name: str = "",
    pack_id: str = "default_charger",
) -> dict[str, Any]:
    lines, warnings = flatten_bom(data, pack_id=pack_id)
    catalog, cat_err, meta = catalog_from_docs(docs, pack_id=pack_id)
    warnings.extend(cat_err)
    warnings.extend(apply_catalog(lines, catalog, pack_id=pack_id))
    lines = number_lines(lines)
    unmatched = pending_price_count(lines)
    title = (project_name or "").strip() or _txt(data.get("projectName"))
    return {
        "projectName": title,
        "lines": [_dump_line(r) for r in lines],
        "warnings": warnings,
        "catalogCount": len(catalog),
        "unmatched": unmatched,
        "catalog": meta,
    }
