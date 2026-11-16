"""可插拔计价模型。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable

from api.services.quotes.catalog import money
from api.services.quotes.contract import merge_rates
from api.services.quotes.packs import QuotePack, load_pack
from common.errors import AppError, ErrorCode

_TWO = Decimal("0.01")
_ACTIVE_MODEL: ContextVar[str] = ContextVar("quote_costing_model", default="cost_plus")


@contextmanager
def activate_model(model: str):
    token = _ACTIVE_MODEL.set(str(model or "cost_plus"))
    try:
        yield
    finally:
        _ACTIVE_MODEL.reset(token)


def _q(raw: object) -> Decimal:
    try:
        return Decimal(str(raw or "0"))
    except Exception:
        return Decimal("0")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_TWO, rounding=ROUND_HALF_UP)


def _normalize_lines(lines: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Decimal]:
    out: list[dict[str, Any]] = []
    material = Decimal("0")
    for row in lines:
        item = dict(row)
        qty = _q(item.get("qty"))
        cost = _q(item.get("costPrice"))
        sell = _q(item.get("sellPrice"))
        unit = _q(item.get("unitPrice"))
        if cost <= 0 and unit > 0:
            cost = unit
        if sell <= 0 and unit > 0:
            sell = unit
        if sell <= 0 and cost > 0:
            sell = cost
        if cost <= 0 and sell > 0:
            cost = sell
        item["costPrice"] = cost
        item["sellPrice"] = sell
        item["unitPrice"] = sell if sell > 0 else cost
        item["amount"] = money(qty, item["unitPrice"])
        item["costAmount"] = money(qty, cost)
        material += item["costAmount"]
        out.append(item)
    return out, material


def _subtotals(lines: list[dict[str, Any]], total: Decimal) -> dict[str, Any]:
    grouped: dict[tuple[str, ...], Decimal] = {}
    base = sum((_q(row.get("costAmount")) for row in lines), Decimal("0"))
    for row in lines:
        path = tuple(str(x).strip() for x in (row.get("group_path") or row.get("groupPath") or []) if str(x).strip()) or ("未分组",)
        grouped[path] = grouped.get(path, Decimal("0")) + _q(row.get("costAmount"))
    scale = total / base if base > 0 else Decimal("0")
    root = {"name": "总计", "total": float(_money(total)), "children": []}
    nodes: dict[tuple[str, ...], dict[str, Any]] = {(): root}
    for path in sorted(grouped):
        current = root
        prefix: tuple[str, ...] = ()
        for name in path:
            prefix += (name,)
            node = nodes.get(prefix)
            if node is None:
                node = {"name": name, "total": 0.0, "children": [], "_direct": 0.0}
                current["children"].append(node)
                nodes[prefix] = node
            current = node
        current["_direct"] = float(_money(_q(current.get("_direct")) + grouped[path] * scale))
    def rollup(node: dict[str, Any]) -> Decimal:
        children = node.get("children") or []
        if children:
            direct = _q(node.get("_direct"))
            if direct > 0:
                children.insert(0, {"name": "本级", "total": float(_money(direct)), "children": [], "_direct": float(_money(direct))})
            total_value = sum((rollup(child) for child in children), Decimal("0"))
            node["total"] = float(_money(total_value))
        else:
            node["total"] = float(_money(_q(node.get("_direct"))))
        node.pop("_direct", None)
        return _q(node.get("total"))
    for child in root["children"]:
        rollup(child)
    child_total = sum((Decimal(str(x["total"])) for x in root["children"]), Decimal("0"))
    if root["children"] and child_total != total:
        root["children"][-1]["total"] = float(_money(Decimal(str(root["children"][-1]["total"])) + total - child_total))
    return root


def _with_common_summary(summary: dict[str, Any], lines: list[dict[str, Any]], total: Decimal) -> dict[str, Any]:
    summary["subTotals"] = _subtotals(lines, total)
    summary["total"] = float(_money(total))
    return summary


def validate_subtotals(summary: dict[str, Any]) -> None:
    tree = summary.get("subTotals") or {}
    def check(node: dict[str, Any]) -> bool:
        children = [child for child in node.get("children") or [] if isinstance(child, dict)]
        if not children:
            return True
        return abs(_q(node.get("total")) - sum((_q(child.get("total")) for child in children), Decimal("0"))) <= Decimal("0.01") and all(check(child) for child in children)
    if not check(tree):
        raise AppError(ErrorCode.VALIDATION, "分组小计与总计偏差超过 0.01 元", status_code=422)


def cost_plus(lines: list[dict[str, Any]], rates: dict[str, Decimal], *, pack: QuotePack, options: dict[str, Any] | None = None):
    del pack, options
    out, material = _normalize_lines(lines)
    labor = _money(material * rates["laborCoef"])
    base = material + labor
    measure = _money(base * rates["measureRate"])
    manage = _money((base + measure) * rates["manageRate"])
    cost_total = _money(base + measure + manage)
    profit = _money(cost_total * rates["profitRate"])
    quote_ex = _money(cost_total + profit)
    contingency = _money(cost_total * rates["contingencyRate"])
    budget_ex = _money((cost_total + contingency) * rates["budgetCoef"])
    tax_rate = rates["taxRate"]
    summary = {
        "materialCost": float(material), "laborCost": float(labor), "measureFee": float(measure),
        "manageFee": float(manage), "costExTax": float(cost_total), "profit": float(profit),
        "quoteExTax": float(quote_ex), "contingency": float(contingency), "budgetExTax": float(budget_ex),
        "taxRate": float(tax_rate), "costIncTax": float(_money(cost_total * (1 + tax_rate))),
        "quoteIncTax": float(_money(quote_ex * (1 + tax_rate))),
        "budgetIncTax": float(_money(budget_ex * (1 + tax_rate))),
        "rates": {k: float(v) for k, v in rates.items()},
    }
    return out, _with_common_summary(summary, out, cost_total)


def market_down(lines: list[dict[str, Any]], rates: dict[str, Decimal], *, pack: QuotePack, options: dict[str, Any] | None = None):
    del pack
    options = options or {}
    out, material = _normalize_lines(lines)
    reference = _q(options.get("referenceTotal"))
    if reference <= 0:
        reference = sum((_q(row.get("qty")) * (_q(row.get("sellPrice")) or _q(row.get("unitPrice"))) for row in out), Decimal("0"))
    down_rate = _q(options.get("downRate", rates.get("downRate", Decimal("0"))))
    if down_rate > 1:
        down_rate /= Decimal("100")
    down_rate = max(Decimal("0"), min(Decimal("1"), down_rate))
    quote_ex = _money(reference * (Decimal("1") - down_rate))
    margin = _money((quote_ex - material) / quote_ex) if quote_ex > 0 else Decimal("0")
    tax = rates["taxRate"]
    summary = {
        "referenceTotal": float(_money(reference)), "downRate": float(down_rate), "margin": float(margin),
        "materialCost": float(material), "costExTax": float(material), "quoteExTax": float(quote_ex),
        "taxRate": float(tax), "quoteIncTax": float(_money(quote_ex * (1 + tax))),
        "costIncTax": float(_money(material * (1 + tax))), "rates": {k: float(v) for k, v in rates.items()},
    }
    return out, _with_common_summary(summary, out, quote_ex)


def quota(lines: list[dict[str, Any]], rates: dict[str, Decimal], *, pack: QuotePack, options: dict[str, Any] | None = None):
    del options
    out, _material = _normalize_lines(lines)
    labor_rate = _q(getattr(pack.fee_defaults, "laborRate", None))
    material_rate = _q(getattr(pack.fee_defaults, "materialRate", None))
    machine_rate = _q(getattr(pack.fee_defaults, "machineRate", None))
    split_total = labor_rate + material_rate + machine_rate
    if split_total <= 0:
        labor_rate, material_rate, machine_rate, split_total = Decimal("0.2"), Decimal("0.7"), Decimal("0.1"), Decimal("1")
    direct = labor = material_part = machine = Decimal("0")
    for row in out:
        base = _q(row.get("costAmount"))
        row_labor, row_material, row_machine = _q(row.get("labor")), _q(row.get("material")), _q(row.get("machine"))
        if row_labor + row_material + row_machine <= 0:
            row_labor = _money(base * labor_rate / split_total)
            row_material = _money(base * material_rate / split_total)
            row_machine = _money(base - row_labor - row_material)
            old = str(row.get("note") or "").strip()
            row["note"] = f"{old}；按包比例分摊，标记 estimate" if old else "按包比例分摊，标记 estimate"
            row["status"] = "estimate"
        row["labor"], row["material"], row["machine"] = row_labor, row_material, row_machine
        labor, material_part, machine = labor + row_labor, material_part + row_material, machine + row_machine
        direct += row_labor + row_material + row_machine
    measure = _money(direct * rates["measureRate"])
    manage = _money((direct + measure) * rates["manageRate"])
    profit = _money((direct + measure + manage) * rates["profitRate"])
    cost_total, quote_ex, tax = _money(direct + measure + manage), _money(direct + measure + manage + profit), rates["taxRate"]
    summary = {
        "directFee": float(direct), "laborCost": float(_money(labor)), "materialCost": float(_money(material_part)),
        "machineCost": float(_money(machine)), "measure": float(measure), "manage": float(manage),
        "profit": float(profit), "tax": float(_money(quote_ex * tax)), "costExTax": float(cost_total),
        "quoteExTax": float(quote_ex), "taxRate": float(tax), "quoteIncTax": float(_money(quote_ex * (1 + tax))),
        "costIncTax": float(_money(cost_total * (1 + tax))), "rates": {k: float(v) for k, v in rates.items()},
    }
    return out, _with_common_summary(summary, out, cost_total)


MODELS: dict[str, Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]]] = {
    "cost_plus": cost_plus, "market_down": market_down, "quota": quota,
}


def apply_costing(lines: list[dict[str, Any]], rates_raw: dict[str, Any] | None = None, *, model: str | None = None, pack: QuotePack | None = None, pack_id: str = "default_charger", options: dict[str, Any] | None = None):
    fn = MODELS.get(str(model or _ACTIVE_MODEL.get()).strip())
    if fn is None:
        raise AppError(ErrorCode.VALIDATION, f"未知计价模型：{model}", status_code=422)
    selected_pack = pack or load_pack(pack_id)
    rates = merge_rates(rates_raw, pack_id=selected_pack.id)
    result = fn(lines, rates, pack=selected_pack, options=options or {})
    validate_subtotals(result[1])
    return result


__all__ = ["MODELS", "activate_model", "apply_costing", "cost_plus", "market_down", "quota", "validate_subtotals"]
