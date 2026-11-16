from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any


def decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    text = str(value).strip().replace(",", "").replace("￥", "").replace("¥", "").replace("元", "")
    if not text or text.startswith("="):
        return Decimal("0")
    try:
        return Decimal(text.replace("%", ""))
    except (InvalidOperation, ValueError):
        return Decimal("0")


@dataclass
class LineCandidate:
    seq: str = ""
    group_path: list[str] = field(default_factory=list)
    code: str = ""
    name: str = ""
    spec: str = ""
    unit: str = ""
    qty: Decimal = Decimal("0")
    unit_price: Decimal = Decimal("0")
    cost_price: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    source: str = "manual"
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    status: str = "confirmed"
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.qty = decimal(self.qty)
        self.unit_price = decimal(self.unit_price)
        self.cost_price = decimal(self.cost_price)
        self.amount = decimal(self.amount)
        self.confidence = max(0.0, min(1.0, float(self.confidence or 0)))
        self.group_path = [str(x).strip() for x in self.group_path if str(x).strip()]
        self.notes = [str(x).strip() for x in self.notes if str(x).strip()]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceDoc:
    file_name: str
    kind: str
    sheets: list[str] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    text: str = ""
    tables: list[list[list[str]]] = field(default_factory=list)
    images: int = 0


def line_from_mapping(row: dict[str, Any], *, source: str, evidence: dict[str, Any], status: str = "confirmed") -> LineCandidate:
    qty = decimal(row.get("qty"))
    unit_price = decimal(row.get("unit_price", row.get("unitPrice", row.get("price"))))
    cost_price = decimal(row.get("cost_price", row.get("costPrice")))
    amount = decimal(row.get("amount"))
    if amount <= 0 and qty and unit_price:
        amount = qty * unit_price
    notes = row.get("notes") if isinstance(row.get("notes"), list) else []
    return LineCandidate(
        seq=str(row.get("seq") or ""),
        group_path=list(row.get("group_path") or row.get("groupPath") or []),
        code=str(row.get("code") or "").strip(),
        name=str(row.get("name") or "").strip(),
        spec=str(row.get("spec") or row.get("specHint") or "").strip(),
        unit=str(row.get("unit") or "").strip(),
        qty=qty,
        unit_price=unit_price,
        cost_price=cost_price,
        amount=amount,
        source=source,
        evidence=evidence,
        confidence=float(row.get("confidence") or 1),
        status=str(row.get("status") or status),
        notes=notes,
    )
