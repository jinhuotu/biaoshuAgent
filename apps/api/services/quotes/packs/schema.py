from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SpecPattern(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    regex: str = Field(min_length=1)
    normalize: str = ""


class QuantityRule(BaseModel):
    model_config = ConfigDict(extra="allow")

    item: str = Field(min_length=1)
    per: list[str] = Field(default_factory=list)
    factor: Decimal = Decimal("1")
    note: str = ""
    codes: list[str] = Field(default_factory=list)


class FeeDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measureRate: Decimal | None = None
    manageRate: Decimal | None = None
    profitRate: Decimal | None = None
    taxRate: Decimal | None = None
    contingencyRate: Decimal | None = None
    laborCoef: Decimal | None = None
    budgetCoef: Decimal | None = None
    laborRate: Decimal | None = None
    materialRate: Decimal | None = None
    machineRate: Decimal | None = None


class QuotePack(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1)
    units: list[str] = Field(min_length=1)
    unit_aliases: dict[str, str] = Field(default_factory=dict, alias="unitAliases")
    synonyms: dict[str, list[str]] = Field(default_factory=dict)
    spec_patterns: list[SpecPattern] = Field(default_factory=list, alias="specPatterns")
    quantity_rules: list[QuantityRule] = Field(default_factory=list, alias="quantityRules")
    fee_defaults: FeeDefaults = Field(default_factory=FeeDefaults, alias="feeDefaults")
    columns: list[str] = Field(default_factory=list)
    output_titles: dict[str, str] = Field(default_factory=dict, alias="outputTitles")
    unit_by_code: dict[str, str] = Field(default_factory=dict, alias="unitByCode")

    def normalize_unit(self, value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        return self.unit_aliases.get(raw, raw)

    def fallback_unit(self) -> str:
        if "项" in self.units:
            return "项"
        return self.units[0] if self.units else "项"

    def display_name(self, code: str, fallback: str = "") -> str:
        aliases = self.synonyms.get(str(code or "").strip().lower()) or []
        return str(aliases[0]).strip() if aliases and str(aliases[0]).strip() else str(fallback or "").strip()


__all__ = ["FeeDefaults", "QuantityRule", "QuotePack", "SpecPattern"]
