"""招标压缩包递归解包：支持 ZIP / RAR 及压缩包套压缩包，保留层级来源。"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

MAX_DEPTH = 6
MAX_FILES = 3000
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_RATIO = 300
ARCHIVE_SUFFIXES = (".zip", ".rar")
RAR_TIMEOUT_SECONDS = 300
_RAR_MAGIC = b"Rar!\x1a\x07"
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


def is_rar_file(path: Path) -> bool:
    """按文件头判断 RAR（RAR4 / RAR5 签名相同前缀），不依赖扩展名。"""
    try:
        with path.open("rb") as stream:
            return stream.read(len(_RAR_MAGIC)) == _RAR_MAGIC
    except OSError:
        return False


def find_rar_tool() -> tuple[str, str] | None:
    """查找本机可用的 RAR 解码程序，返回 (类型, 可执行文件路径)。

    依次尝试 UnRAR、7-Zip、unar、bsdtar（Windows 10+ 与 macOS 自带的 tar 即 bsdtar）。
    """
    program_dirs = [os.environ.get(key, "") for key in ("ProgramFiles", "ProgramFiles(x86)")]
    candidates: list[tuple[str, str]] = [("unrar", "unrar"), ("unrar", "UnRAR")]
    candidates += [("unrar", str(Path(folder) / "WinRAR" / "UnRAR.exe")) for folder in program_dirs if folder]
    candidates += [("7z", name) for name in ("7z", "7zz", "7za")]
    candidates += [("7z", str(Path(folder) / "7-Zip" / "7z.exe")) for folder in program_dirs if folder]
    candidates += [("unar", "unar"), ("bsdtar", "bsdtar")]
    if os.environ.get("SystemRoot"):
        candidates.append(("bsdtar", str(Path(os.environ["SystemRoot"]) / "System32" / "tar.exe")))
    for kind, name in candidates:
        executable = shutil.which(name)
        if executable:
            return kind, executable
    generic_tar = shutil.which("tar")
    if generic_tar:
        try:
            version = subprocess.run([generic_tar, "--version"], capture_output=True, timeout=10, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            version = b""
        if b"bsdtar" in version:
            return "bsdtar", generic_tar
    return None


def _rar_command(kind: str, executable: str, archive: Path, target: Path) -> list[str]:
    if kind == "unrar":
        return [executable, "x", "-o+", "-y", "-p-", "-idq", str(archive), str(target) + os.sep]
    if kind == "7z":
        return [executable, "x", "-y", "-p-", "-bd", f"-o{target}", str(archive)]
    if kind == "unar":
        return [executable, "-quiet", "-force-overwrite", "-no-directory", "-output-directory", str(target), str(archive)]
    return [executable, "-xf", str(archive), "-C", str(target)]


def _extract_rar(archive: Path, target: Path, label: str) -> list[tuple[str, Path]]:
    """把 RAR 解到 ``target``，返回 [(包内相对路径, 落盘路径)]。"""
    tool = find_rar_tool()
    if tool is None:
        raise ArchiveError(
            f"服务器缺少 RAR 解压组件，无法读取：{label}。请在服务器安装 7-Zip 或 WinRAR（UnRAR），或将该压缩包转换为 ZIP 后重新上传"
        )
    target.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    if os.name != "nt":
        # bsdtar 需要 UTF-8 区域设置才能写出中文文件名
        environment["LC_ALL"] = "en_US.UTF-8" if sys.platform == "darwin" else "C.UTF-8"
    kind, executable = tool

    def run(command: list[str]) -> int:
        return subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=RAR_TIMEOUT_SECONDS, check=False, env=environment,
        ).returncode

    def run_piped(command: list[str]) -> int:
        """经管道送入压缩包：bsdtar 无法回读文件尾，只能按文件头识别为 RAR。"""
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment)
        try:
            with archive.open("rb") as stream:
                shutil.copyfileobj(stream, process.stdin, 1 << 20)
        except OSError:
            pass  # 解码程序提前退出（如包已损坏），以退出码为准
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            return process.wait(timeout=RAR_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            raise

    try:
        if kind == "bsdtar":
            # RAR 里若原样存放着 ZIP，bsdtar 按文件读取会把整个包误判成 ZIP；管道输入可避免。
            return_code = run_piped([executable, "-xf", "-", "-C", str(target)])
            if return_code != 0:
                shutil.rmtree(target, ignore_errors=True)
                target.mkdir(parents=True)
                return_code = run(_rar_command(kind, executable, archive, target))
        else:
            return_code = run(_rar_command(kind, executable, archive, target))
    except subprocess.TimeoutExpired as exc:
        raise ArchiveError(f"RAR 压缩包解压超时：{label}，请拆分后上传") from exc
    except OSError as exc:
        raise ArchiveError(f"RAR 解压组件无法运行：{label}") from exc
    members: list[tuple[str, Path]] = []
    for path in sorted(target.rglob("*")):
        if path.is_symlink():
            raise ArchiveError(f"压缩包内不允许链接文件：{path.name}")
        if path.is_file():
            members.append((path.relative_to(target).as_posix(), path))
    if return_code != 0 or not members:
        raise ArchiveError(f"RAR 压缩包无法解压（可能已加密、已损坏或为分卷压缩）：{label}")
    return members


def unpack_archive(source: Path, destination: Path) -> list[ArchiveEntry]:
    """把 ``source`` 及其内部所有嵌套 ZIP / RAR 解到 ``destination``，返回文件树。"""
    destination.mkdir(parents=True, exist_ok=True)
    counter = {"files": 0, "bytes": 0}

    def count(name: str, size: int) -> None:
        counter["files"] += 1
        counter["bytes"] += size
        if counter["files"] > MAX_FILES or counter["bytes"] > MAX_TOTAL_BYTES:
            raise ArchiveError("压缩包文件数量或总大小超限，请拆分后上传")

    def add_entry(entries: list[ArchiveEntry], name: str, parts: tuple[str, ...], size: int, output: Path, chain: list[str], depth: int) -> None:
        entry = ArchiveEntry(
            name=parts[-1],
            logical_path="/".join([*chain, name]),
            chain=list(chain),
            depth=depth,
            suffix=Path(name).suffix.lower(),
            size=size,
            storage_path=str(output.relative_to(destination)),
        )
        if entry.suffix in ARCHIVE_SUFFIXES:
            entry.children = visit(output, output.parent / f"{output.stem}__unzipped", [*chain, name], depth + 1)
        entries.append(entry)

    def visit_rar(archive: Path, target: Path, chain: list[str], depth: int) -> list[ArchiveEntry]:
        entries: list[ArchiveEntry] = []
        for name, output in _extract_rar(archive, target, " > ".join(chain) or archive.name):
            parts = _check_member_path(name)
            size = output.stat().st_size
            count(name, size)
            add_entry(entries, name, parts, size, output, chain, depth)
        return entries

    def visit(archive: Path, target: Path, chain: list[str], depth: int) -> list[ArchiveEntry]:
        if depth > MAX_DEPTH:
            raise ArchiveError(f"压缩包嵌套超过 {MAX_DEPTH} 层：{' > '.join(chain)}")
        if is_rar_file(archive):
            return visit_rar(archive, target, chain, depth)
        if not zipfile.is_zipfile(archive):
            raise ArchiveError(f"不是有效的 ZIP / RAR 文件或文件已损坏：{chain[-1] if chain else archive.name}")
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
                    count(name, info.file_size)
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
                    add_entry(entries, name, parts, info.file_size, output, chain, depth)
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
