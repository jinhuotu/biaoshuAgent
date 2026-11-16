"""AI 标书生成：RAR 压缩包（含 RAR 套 ZIP）上传解包。"""

import struct
import zipfile
import zlib
from io import BytesIO

import pytest

from api.services.bidgen import archive as archive_module
from api.services.bidgen.archive import ArchiveError, find_rar_tool, flatten_entries, is_rar_file, unpack_archive

needs_rar_tool = pytest.mark.skipif(find_rar_tool() is None, reason="本机没有可用的 RAR 解码程序")


def _vint(value: int) -> bytes:
    encoded = bytearray()
    while True:
        low_bits = value & 0x7F
        value >>= 7
        encoded.append(low_bits | (0x80 if value else 0))
        if not value:
            return bytes(encoded)


def _rar_header(body: bytes) -> bytes:
    sized = _vint(len(body)) + body
    return struct.pack("<I", zlib.crc32(sized)) + sized


def build_rar(files: dict[str, bytes]) -> bytes:
    """按 RAR 5.0 格式手工写出“仅存储、不压缩”的归档（测试机没有 rar 压缩程序）。"""
    archive = bytearray(b"Rar!\x1a\x07\x01\x00")
    archive += _rar_header(_vint(1) + _vint(0) + _vint(0))
    for name, content in files.items():
        raw_name = name.encode("utf-8")
        archive += _rar_header(
            _vint(2) + _vint(0x02) + _vint(len(content)) + _vint(0x04) + _vint(len(content)) + _vint(0x20)
            + struct.pack("<I", zlib.crc32(content)) + _vint(0) + _vint(0) + _vint(len(raw_name)) + raw_name
        ) + content
    archive += _rar_header(_vint(5) + _vint(0) + _vint(0))
    return bytes(archive)


def build_zip(files: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        for name, content in files.items():
            package.writestr(name, content)
    return buffer.getvalue()


def test_rar_is_detected_by_signature_not_extension(tmp_path):
    disguised = tmp_path / "source.zip"
    disguised.write_bytes(build_rar({"a.txt": b"A"}))
    plain_zip = tmp_path / "real.zip"
    plain_zip.write_bytes(build_zip({"a.txt": b"A"}))
    assert is_rar_file(disguised)
    assert not is_rar_file(plain_zip)


@needs_rar_tool
def test_rar_with_chinese_names_and_nested_zip_is_unpacked(tmp_path):
    inner_zip = build_zip({"技术规范/规范书.txt": "十千伏电缆".encode()})
    source = tmp_path / "招标文件.rar"
    source.write_bytes(build_rar({"招标文件/投标邀请书.txt": "邀请书正文".encode(), "招标文件/技术.zip": inner_zip}))
    workspace = tmp_path / "workspace"
    entries = unpack_archive(source, workspace)
    by_path = {entry.logical_path: entry for entry in flatten_entries(entries)}
    assert set(by_path) == {"招标文件/投标邀请书.txt", "招标文件/技术.zip", "招标文件/技术.zip/技术规范/规范书.txt"}
    invitation = by_path["招标文件/投标邀请书.txt"]
    assert (workspace / invitation.storage_path).read_text(encoding="utf-8") == "邀请书正文"
    nested = by_path["招标文件/技术.zip/技术规范/规范书.txt"]
    assert nested.depth == 1 and nested.chain == ["招标文件/技术.zip"]
    assert (workspace / nested.storage_path).read_text(encoding="utf-8") == "十千伏电缆"


@needs_rar_tool
def test_rar_nested_inside_zip_is_unpacked(tmp_path):
    inner_rar = build_rar({"评分标准.txt": "商务评分".encode()})
    source = tmp_path / "包1.zip"
    source.write_bytes(build_zip({"外层/说明.txt": b"readme", "外层/内层.rar": inner_rar}))
    entries = flatten_entries(unpack_archive(source, tmp_path / "workspace"))
    nested = next(entry for entry in entries if entry.name == "评分标准.txt")
    assert nested.logical_path == "外层/内层.rar/评分标准.txt"
    assert (tmp_path / "workspace" / nested.storage_path).read_text(encoding="utf-8") == "商务评分"


@needs_rar_tool
def test_damaged_rar_is_rejected_with_readable_message(tmp_path):
    source = tmp_path / "坏包.rar"
    source.write_bytes(build_rar({"a.txt": b"content"})[:-12] + b"\x00" * 40)
    with pytest.raises(ArchiveError, match="RAR 压缩包无法解压"):
        unpack_archive(source, tmp_path / "workspace")


def test_missing_rar_decoder_blocks_with_install_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(archive_module, "find_rar_tool", lambda: None)
    source = tmp_path / "招标文件.rar"
    source.write_bytes(build_rar({"a.txt": b"A"}))
    with pytest.raises(ArchiveError, match="缺少 RAR 解压组件.*7-Zip"):
        unpack_archive(source, tmp_path / "workspace")
