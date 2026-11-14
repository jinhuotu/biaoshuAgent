"""Turn legacy Word attachments into media accepted by the existing assembler."""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import shutil
import tempfile
import time
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

import fitz
from PIL import Image

from api.services import word_convert
from common.config import get_settings

_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _cache_dir() -> Path:
    return Path(get_settings().storage_root).expanduser().resolve() / "tender-assets" / "converted"


def _extract_images(source: Path, cache: Path, digest: str) -> list[Path]:
    """Read only referenced images, in document order (never ZIP/rId order)."""
    outputs = []
    with zipfile.ZipFile(source) as package:
        document = ET.fromstring(package.read("word/document.xml"))
        relationships = ET.fromstring(package.read("word/_rels/document.xml.rels"))
        targets = {
            rel.get("Id"): posixpath.normpath(
                posixpath.join("word", rel.get("Target", ""))
            ).lstrip("/")
            for rel in relationships
            if rel.get("TargetMode") != "External"
        }
        for index, node in enumerate(document.iter()):
            target = targets.get(node.get(f"{{{_REL}}}embed"), "")
            if not target.startswith("word/media/"):
                continue
            try:
                data = package.read(target)
                if len(data) < 20_000:
                    continue
                with Image.open(BytesIO(data)) as picture:
                    if min(picture.size) < 300:
                        continue
                    picture.load()
                    suffix = {"JPEG": ".jpg", "PNG": ".png"}.get(picture.format)
                    if suffix is None:
                        buffer = BytesIO()
                        picture.convert("RGB").save(buffer, format="PNG")
                        data, suffix = buffer.getvalue(), ".png"
                cache.mkdir(parents=True, exist_ok=True)
                dest = cache / f"{digest}-{index:06d}{suffix}"
                # Publish complete files so parallel generations never read partial images.
                with tempfile.NamedTemporaryFile(dir=cache, delete=False) as staged:
                    temp = Path(staged.name)
                    staged.write(data)
                try:
                    os.replace(temp, dest)
                finally:
                    temp.unlink(missing_ok=True)
                outputs.append(dest)
            except (KeyError, OSError, ValueError):
                continue
    return outputs


def _valid_pdf(path: Path) -> bool:
    try:
        with fitz.open(path) as document:
            return document.is_pdf and document.page_count > 0
    except Exception:
        return False


def _convert_pdf(source: Path, cache: Path, digest: str, timeout: float) -> tuple[Path, str]:
    dest = cache / f"{digest}.pdf"
    if _valid_pdf(dest):
        return dest, "cached-pdf"
    cache.mkdir(parents=True, exist_ok=True)
    soffice = os.environ.get("SOFFICE_PATH") or os.environ.get("LIBREOFFICE_PATH")
    if not soffice:
        installed = Path(r"C:\Program Files\LibreOffice\program\soffice.exe")
        soffice = str(installed) if installed.is_file() else (
            shutil.which("soffice") or shutil.which("soffice.exe") or str(installed)
        )
    with tempfile.TemporaryDirectory(prefix=f"{digest}-", dir=cache) as temp:
        folder = Path(temp)
        result = word_convert._run_converter([
            soffice, f"-env:UserInstallation={(folder / 'profile').as_uri()}",
            "--headless", "--convert-to", "pdf", "--outdir", str(folder), str(source.resolve()),
        ], timeout)
        if result.returncode:
            raise RuntimeError(f"退出码 {result.returncode}")
        produced = folder / f"{source.stem}.pdf"
        if not _valid_pdf(produced):
            raise RuntimeError("退出码 0，未生成有效 PDF")
        os.replace(produced, dest)
    return dest, "convert-pdf"


def hydrate_docx_media(
    media: dict[str, list[Path]], *, timeout: float = 120.0,
) -> tuple[dict[str, list[Path]], list[dict[str, str]]]:
    """Copy media, replacing each Word item in place; report failures without raising."""
    hydrated: dict[str, list[Path]] = {}
    reports: list[dict[str, str]] = []
    cache = _cache_dir()
    for key, files in media.items():
        hydrated[key] = []
        for item in files:
            source = Path(item)
            if source.suffix.lower() not in {".doc", ".docx"}:
                hydrated[key].append(item)
                continue
            started = time.perf_counter()
            outputs: list[Path] = []
            mode, reason = "failed", ""
            try:
                digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
                try:
                    outputs = _extract_images(source, cache, digest)
                except (OSError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError):
                    outputs = []
                if outputs:
                    mode = "extract-image"
                else:
                    reason = "未抽取到图片"
                    try:
                        pdf, mode = _convert_pdf(source, cache, digest, timeout)
                        outputs, reason = [pdf], ""
                    except Exception as exc:
                        detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
                        reason += f"；soffice 转换失败：{detail}"
            except Exception as exc:
                reason = f"未抽取到图片；读取 Word 原件失败：{type(exc).__name__}"
            hydrated[key].extend(outputs)
            reports.append({
                "key": key, "title": source.stem, "mode": mode, "sourcePath": str(source),
                "outputs": json.dumps([str(path) for path in outputs], ensure_ascii=False),
                "elapsedMs": str(round((time.perf_counter() - started) * 1000)), "reason": reason,
            })
    return hydrated, reports
