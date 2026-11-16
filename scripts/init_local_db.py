"""本机轻量运行：按 ORM 模型直接建表（SQLite），并写入种子管理员。

仅用于没有 MySQL 的开发 / 演示环境；正式环境仍使用 Alembic 迁移。

用法：
  .venv/bin/python scripts/init_local_db.py
"""

from __future__ import annotations

import asyncio
import importlib
import pkgutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "apps"))
sys.path.insert(0, str(ROOT / "scripts"))

import db.models as models_package  # noqa: E402
from db.base import Base  # noqa: E402
from db.session import engine  # noqa: E402


async def create_tables() -> None:
    for module in pkgutil.iter_modules(models_package.__path__):
        importlib.import_module(f"db.models.{module.name}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    print(f"[ok] tables ready: {len(Base.metadata.tables)}")


async def main() -> None:
    await create_tables()
    from seed_admin import seed

    await seed()


if __name__ == "__main__":
    asyncio.run(main())
