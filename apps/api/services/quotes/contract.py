"""造价 / 报价 / 预算：purpose、阶段与默认费率约定。"""

from __future__ import annotations

from decimal import Decimal
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from api.services.quotes.packs import load_pack

PURPOSES = ("cost", "quote", "budget")
STAGES = ("parsed", "verified", "costed", "quoted")

PURPOSE_LABEL = {
    "cost": "AI造价智能体",
    "quote": "AI报价智能体",
    "budget": "AI预算智能体",
}

# purpose → 生成时推进到的最终阶段
PURPOSE_STOP = {
    "cost": "costed",
    "budget": "costed",
    "quote": "quoted",
}

DEFAULT_RATES: dict[str, Decimal] = {
    "laborCoef": Decimal("0"),  # 人工费系数（相对材料成本）
    "measureRate": Decimal("0.03"),  # 措施费
    "manageRate": Decimal("0.05"),  # 管理费
    "profitRate": Decimal("0.08"),  # 利润（报价侧）
    "taxRate": Decimal("0.13"),
    "budgetCoef": Decimal("1.05"),  # 预算相对造价
    "contingencyRate": Decimal("0.03"),  # 不可预见费
}
_ACTIVE_PACK_ID: ContextVar[str] = ContextVar("quote_pack_id", default="default_charger")

# 价目表头约定：名称必填；单价列可为「成本价 / 指导售价 / 单价」
CATALOG_HEADERS = (
    "名称（设备名称/产品名称）",
    "规格型号",
    "单位",
    "成本价（元）",
    "指导售价（元）",
    "单价（缺成本/售价时降级使用）",
)

EXPORT_SHEETS = {
    "cost": "造价测算表",
    "quote": "投标报价单",
    "budget": "控制预算表",
}


def normalize_purpose(raw: str | None) -> str:
    p = (raw or "quote").strip().lower()
    return p if p in PURPOSES else "quote"


def normalize_stage(raw: str | None) -> str:
    s = (raw or "").strip().lower()
    return s if s in STAGES else "parsed"


def current_pack_id() -> str:
    return _ACTIVE_PACK_ID.get()


@contextmanager
def activate_pack(pack_id: str):
    token = _ACTIVE_PACK_ID.set(str(pack_id or "default_charger"))
    try:
        yield
    finally:
        _ACTIVE_PACK_ID.reset(token)


def _pack_rates(pack_id: str | None, warnings: list[str] | None = None) -> dict[str, Decimal]:
    pack_id = pack_id or current_pack_id()
    try:
        pack = load_pack(pack_id)
    except Exception as exc:
        if warnings is not None:
            warnings.append(f"行业知识包 {pack_id} 不可用，已回落现有默认费率：{exc}")
        return dict(DEFAULT_RATES)
    rates = dict(DEFAULT_RATES)
    for key in rates:
        value = getattr(pack.fee_defaults, key, None)
        if value is not None and value >= 0:
            rates[key] = value
    return rates


def default_rates_dict(pack_id: str | None = None, warnings: list[str] | None = None) -> dict[str, float]:
    return {k: float(v) for k, v in _pack_rates(pack_id, warnings).items()}


def merge_rates(raw: dict[str, Any] | None, *, pack_id: str | None = None, warnings: list[str] | None = None) -> dict[str, Decimal]:
    out = _pack_rates(pack_id, warnings)
    if not isinstance(raw, dict):
        return out
    for key in out:
        if key not in raw:
            continue
        try:
            n = Decimal(str(raw[key]))
        except Exception:
            continue
        if n < 0:
            continue
        if key.endswith("Rate") or key == "taxRate":
            if n > 1:
                n = n / Decimal("100")
            if n > 1:
                n = Decimal("1")
        out[key] = n
    return out
