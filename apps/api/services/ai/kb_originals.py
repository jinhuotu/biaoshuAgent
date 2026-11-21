"""对话命中投标资料库时，抽出扫描件给模型看。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.services.knowledge.access import is_company_tender_library_id, is_tender_library_id
from api.services.knowledge.rerank import tender_hit_matches_query
from api.services.tenders.library_kb import open_library_file
from common.errors import AppError

_IMAGE = frozenset({"png", "jpg", "jpeg", "webp", "gif", "bmp"})
_MAX_VISION = 4


def preview_kind(file_type: str | None, *, has_file: bool) -> str:
    if not has_file:
        return ""
    ext = (file_type or "").lower().lstrip(".")
    if ext == "pdf":
        return "pdf"
    if ext in _IMAGE:
        return "image"
    return "file"


def _kb_id(row: dict[str, Any]) -> str:
    return str(row.get("kb_id") or row.get("kbId") or "").strip()


def unique_tender_docs(
    chunks: list[dict[str, Any]], *, query: str = ""
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    q = (query or "").strip()
    for row in chunks:
        if not is_tender_library_id(_kb_id(row)):
            continue
        pid = str(row.get("doc_id") or "").strip()
        if not pid or pid in seen:
            continue
        if row.get("has_file") is False:
            continue
        kind = str(row.get("preview_kind") or "image")
        if kind not in {"image", "pdf"}:
            continue
        if q and not tender_hit_matches_query(q, row):
            continue
        seen.add(pid)
        out.append(row)
    return out


async def load_tender_original_blobs(
    db: AsyncSession,
    chunks: list[dict[str, Any]],
    *,
    query: str = "",
    limit: int = _MAX_VISION,
) -> list[tuple[str, bytes]]:
    n = max(0, min(int(limit), _MAX_VISION))
    blobs: list[tuple[str, bytes]] = []
    for row in unique_tender_docs(chunks, query=query):
        if len(blobs) >= n:
            break
        kind = str(row.get("preview_kind") or "image")
        if kind not in {"image", "pdf"}:
            continue
        pid = str(row.get("doc_id") or "").strip()
        try:
            base_id = _kb_id(row)
            if is_company_tender_library_id(base_id):
                data, _name, media = await open_library_file(
                    db, pid, thumb=True, base_public_id=base_id
                )
            else:
                data, _name, media = await open_library_file(db, pid, thumb=True)
        except AppError:
            continue
        mime = (media or "image/jpeg").split(";", 1)[0].strip() or "image/jpeg"
        if not mime.startswith("image/"):
            mime = "image/jpeg"
        blobs.append((mime, data))
    return blobs
