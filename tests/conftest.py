"""tests/ 全局夹具：跑完自动收走测试写进仓库 tmp/ 的临时目录。

背景：部分用例（test_quote_ingest、test_quote_packs 等）为了用相对路径
直接在仓库 tmp/ 下建目录，但用完不删，每跑一次就留一批 quote-ingest-*、
bad-pack-*，目录越积越多。

做法：进入用例前记下 tmp/ 现有条目，用例结束后只删除「本次新增」的条目，
用例开始前就存在的目录一律不动，避免误删别人正在用的数据。
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clean_repo_tmp_scratch() -> Iterator[None]:
    root = Path("tmp")
    root.mkdir(parents=True, exist_ok=True)
    before = {entry.name for entry in root.iterdir()}

    yield

    for entry in root.iterdir():
        if entry.name in before:
            continue
        try:
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError:
            # 占用中的文件删不掉时不影响用例结果，留给下次清理
            pass
