"""统一报价输入解析。"""

from .pipeline import ingest
from .types import EvidenceDoc, LineCandidate

__all__ = ["EvidenceDoc", "LineCandidate", "ingest"]
