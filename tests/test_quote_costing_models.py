from __future__ import annotations

from decimal import Decimal

import pytest

from api.services.quotes.costing import apply_costing, validate_subtotals
from api.services.quotes.packs import load_pack
from common.errors import AppError


def _lines():
    return [
        {"name": "A", "unit": "项", "qty": Decimal("2"), "costPrice": Decimal("100"), "sellPrice": Decimal("120"), "group_path": ["主体"]},
        {"name": "B", "unit": "项", "qty": Decimal("1"), "costPrice": Decimal("50"), "sellPrice": Decimal("60"), "group_path": ["主体", "设备"]},
    ]


def test_cost_plus_regression_summary() -> None:
    _, summary = apply_costing(_lines(), {"laborCoef": 0, "measureRate": Decimal("0.03"), "manageRate": Decimal("0.05"), "profitRate": Decimal("0.08"), "taxRate": Decimal("0.13"), "budgetCoef": Decimal("1.05"), "contingencyRate": Decimal("0.03")})
    assert summary["materialCost"] == 250.0
    assert summary["measureFee"] == 7.5
    assert summary["manageFee"] == 12.88
    assert summary["costExTax"] == 270.38
    assert summary["quoteExTax"] == 292.01
    assert summary["budgetExTax"] == 292.41


def test_market_down_reference_and_margin() -> None:
    _, summary = apply_costing(_lines(), model="market_down", options={"downRate": Decimal("0.1")})
    assert summary["referenceTotal"] == 300.0
    assert summary["downRate"] == 0.1
    assert summary["quoteExTax"] == 270.0
    assert summary["margin"] == 0.07


def test_quota_split_sums_to_direct_fee() -> None:
    rows, summary = apply_costing(_lines(), model="quota")
    assert sum((row["labor"] + row["material"] + row["machine"] for row in rows), Decimal("0")) == Decimal(str(summary["directFee"]))


def test_group_subtotals_sum_to_total() -> None:
    _, summary = apply_costing(_lines())
    root = summary["subTotals"]
    assert sum(child["total"] for child in root["children"]) == root["total"]
    validate_subtotals(summary)


def test_subtotal_deviation_over_cent_is_rejected() -> None:
    _, summary = apply_costing(_lines())
    summary["subTotals"]["children"][0]["total"] += 0.02
    with pytest.raises(AppError, match="0.01"):
        validate_subtotals(summary)


def test_unknown_model_is_rejected() -> None:
    with pytest.raises(AppError, match="未知计价模型"):
        apply_costing(_lines(), model="unknown")
