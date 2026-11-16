from __future__ import annotations

import pytest

from api.services.quotes.excel import _headers_for
from api.services.quotes.packs import QuotePack, load_pack
from api.services.quotes.quality import enforce_quality, run_quality
from common.errors import AppError


def test_priced_ratio_and_pending_count() -> None:
    pack = load_pack()
    lines = [{"name": "A", "unit": "项", "qty": 1, "unitPrice": 100}, {"name": "B", "unit": "项", "qty": 1, "unitPrice": 0, "status": "pending"}]
    quality = run_quality(lines, {}, pack)
    assert quality["pricedRatio"] == 0.5
    assert quality["pendingCount"] == 1


def test_outlier_price_is_reported() -> None:
    pack = load_pack()
    lines = [
        {"name": "A", "spec": "10kW", "unitPrice": 100},
        {"name": "A", "spec": "20kW", "unitPrice": 300},
    ]
    assert run_quality(lines, {}, pack)["outlierCount"] == 2


def test_pack_missing_columns_is_rejected() -> None:
    pack = QuotePack.model_validate({"id": "bad", "name": "bad", "units": ["项"], "columns": ["名称"]})
    with pytest.raises(AppError, match="列头缺少必要角色"):
        _headers_for("quote", pack)


def test_quality_gate_rejects_pending_without_force() -> None:
    with pytest.raises(AppError, match="待核价"):
        enforce_quality({"pricedRatio": 0.5, "pendingCount": 1, "subtotalMatch": True}, force_export=False)


def test_quality_gate_allows_force_export_with_warning() -> None:
    warnings = enforce_quality({"pricedRatio": 0.5, "pendingCount": 2, "subtotalMatch": True}, force_export=True)
    assert warnings == ["有效报价行比例低于 0.6", "含 2 项待核价"]
