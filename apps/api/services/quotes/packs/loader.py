from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from common.errors import AppError, ErrorCode

from .schema import QuotePack

_PACK_DIR = Path(__file__).resolve().parent


def _path(pack_id: str) -> Path:
    value = str(pack_id or "").strip()
    if not value or Path(value).name != value or Path(value).suffix:
        raise AppError(ErrorCode.VALIDATION, f"行业知识包标识非法：{value or '<empty>'}", status_code=422)
    return _PACK_DIR / f"{value}.json"


def load_pack(pack_id: str = "default_charger") -> QuotePack:
    path = _path(pack_id)
    if not path.is_file():
        raise AppError(ErrorCode.VALIDATION, f"行业知识包不存在：{pack_id}", status_code=422)
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AppError(ErrorCode.VALIDATION, f"行业知识包无法读取：{pack_id}（{exc}）", status_code=422) from exc
    try:
        pack = QuotePack.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(x) for x in error['loc'])}: {error['msg']}" for error in exc.errors()
        )
        raise AppError(ErrorCode.VALIDATION, f"行业知识包校验失败：{details}", status_code=422) from exc
    if pack.id != str(pack_id).strip():
        raise AppError(ErrorCode.VALIDATION, f"行业知识包字段 id 与文件名不一致：{pack.id}", status_code=422)
    invalid_units = [unit for unit in pack.units if not str(unit).strip()]
    if invalid_units:
        raise AppError(ErrorCode.VALIDATION, "行业知识包字段 units 包含空值", status_code=422)
    return pack


def list_packs() -> list[QuotePack]:
    packs: list[QuotePack] = []
    for path in sorted(_PACK_DIR.glob("*.json")):
        packs.append(load_pack(path.stem))
    return packs


__all__ = ["list_packs", "load_pack"]
