"""招标压缩包递归解包：支持压缩包套压缩包，保留层级来源。"""

from __future__ import annotations

import re
import stat
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

MAX_DEPTH = 6
MAX_FILES = 3000
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_RATIO = 300
_RESERVED_NAME = re.compile(r"(?i)^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$")


class ArchiveError(ValueError):
    """压缩包不可用（损坏、越界路径、超限等）。"""


@dataclass
class ArchiveEntry:
    """解包后的一个文件；``chain`` 为它所在的各层压缩包名。"""

    name: str
    logical_path: str
    chain: list[str]
    depth: int
    suffix: str
    size: int
    storage_path: str = ""
    children: list[ArchiveEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _decode_member_name(info: zipfile.ZipInfo) -> str:
    """Windows 打的包文件名是 GBK，zipfile 默认按 cp437 解出乱码。"""
    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            name = name.encode("cp437").decode("gbk")
        except (UnicodeError, LookupError):
            pass
    return name.replace("\\", "/")


def _check_member_path(name: str) -> tuple[str, ...]:
    parts = PurePosixPath(name).parts
    unsafe = (
        not parts
        or name.startswith("/")
        or any(p in ("..", ".") or ":" in p or _RESERVED_NAME.match(p) for p in parts)
    )
    if unsafe:
        raise ArchiveError(f"压缩包内存在不安全路径：{name}")
    return parts


def unpack_archive(source: Path, destination: Path) -> list[ArchiveEntry]:
    """把 ``source`` 及其内部所有嵌套 zip 解到 ``destination``，返回文件树。"""
    destination.mkdir(parents=True, exist_ok=True)
    counter = {"files": 0, "bytes": 0}

    def visit(archive: Path, target: Path, chain: list[str], depth: int) -> list[ArchiveEntry]:
        if depth > MAX_DEPTH:
            raise ArchiveError(f"压缩包嵌套超过 {MAX_DEPTH} 层：{' > '.join(chain)}")
        if not zipfile.is_zipfile(archive):
            raise ArchiveError(f"不是有效的 ZIP 文件或文件已损坏：{chain[-1] if chain else archive.name}")
        entries: list[ArchiveEntry] = []
        try:
            with zipfile.ZipFile(archive) as zip_file:
                for index, info in enumerate(zip_file.infolist()):
                    name = _decode_member_name(info)
                    if info.is_dir():
                        continue
                    parts = _check_member_path(name)
                    mode = info.external_attr >> 16
                    if stat.S_ISLNK(mode):
                        raise ArchiveError(f"压缩包内不允许链接文件：{name}")
                    if info.file_size / max(info.compress_size, 1) > MAX_RATIO:
                        raise ArchiveError(f"压缩比异常：{name}")
                    counter["files"] += 1
                    counter["bytes"] += info.file_size
                    if counter["files"] > MAX_FILES or counter["bytes"] > MAX_TOTAL_BYTES:
                        raise ArchiveError("压缩包文件数量或总大小超限，请拆分后上传")
                    output = target / f"{index:04d}" / Path(*parts)
                    if not output.resolve().is_relative_to(destination.resolve()):
                        raise ArchiveError(f"路径越界：{name}")
                    output.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        with zip_file.open(info) as member, output.open("wb") as stream:
                            while block := member.read(1 << 16):
                                stream.write(block)
                    except RuntimeError as exc:
                        raise ArchiveError(f"压缩包已加密，无法读取：{name}") from exc
                    entry = ArchiveEntry(
                        name=parts[-1],
                        logical_path="/".join([*chain, name]),
                        chain=list(chain),
                        depth=depth,
                        suffix=Path(name).suffix.lower(),
                        size=info.file_size,
                        storage_path=str(output.relative_to(destination)),
                    )
                    if entry.suffix == ".zip":
                        entry.children = visit(
                            output, output.parent / f"{output.stem}__unzipped", [*chain, name], depth + 1
                        )
                    entries.append(entry)
        except (zipfile.BadZipFile, EOFError, NotImplementedError) as exc:
            raise ArchiveError(f"压缩包损坏或压缩算法不受支持：{' > '.join(chain) or archive.name}") from exc
        return entries

    return visit(source, destination, [], 0)


def flatten_entries(entries: list[ArchiveEntry]) -> list[ArchiveEntry]:
    """按深度优先展开文件树（含压缩包自身节点）。"""
    result: list[ArchiveEntry] = []
    for entry in entries:
        result.append(entry)
        result.extend(flatten_entries(entry.children))
    return result
