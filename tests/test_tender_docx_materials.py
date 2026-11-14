import hashlib
import json
import random
import subprocess
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import fitz
from docx import Document
from PIL import Image

from api.services.tenders import docx_materials as materials


def test_image_docx_preserves_document_reference_and_media_order(tmp_path, monkeypatch):
    monkeypatch.setattr(materials, "_cache_dir", lambda: tmp_path / "converted")
    images = []
    for seed, size in [(1, (320, 360)), (2, (340, 320)), (3, (299, 400))]:
        buffer = BytesIO()
        Image.frombytes("RGB", size, random.Random(seed).randbytes(size[0] * size[1] * 3)).save(
            buffer, format="PNG"
        )
        images.append(buffer.getvalue())
    small = BytesIO()
    Image.new("RGB", (400, 400), "white").save(small, format="PNG")
    source = tmp_path / "scans.docx"
    # ZIP, numeric rId and document orders deliberately differ; repeated references survive.
    with zipfile.ZipFile(source, "w") as package:
        package.writestr("word/media/first.png", images[0])
        package.writestr("word/media/second.png", images[1])
        package.writestr("word/media/short.png", images[2])
        package.writestr("word/media/tiny.png", small.getvalue())
        package.writestr("word/media/unused.png", images[0])
        package.writestr("word/_rels/document.xml.rels", '''
          <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
            <Relationship Id="rId9" Target="media/first.png"/>
            <Relationship Id="rId2" Target="media/second.png"/>
            <Relationship Id="rId3" Target="media/short.png"/>
            <Relationship Id="rId4" Target="media/tiny.png"/>
          </Relationships>''')
        package.writestr("word/document.xml", '''
          <document xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
            <blip r:embed="rId2"/><blip r:embed="rId3"/><blip r:embed="rId9"/>
            <blip r:embed="rId4"/><blip r:embed="rId2"/>
          </document>''')
    def unexpected_conversion(*args, **kwargs):
        raise AssertionError("images must not invoke soffice")
    monkeypatch.setattr(materials.word_convert, "_run_converter", unexpected_conversion)
    before, after = tmp_path / "before.pdf", tmp_path / "after.jpg"
    original = {"license": [before, source, after], "empty": []}
    hydrated, reports = materials.hydrate_docx_media(original)
    assert original == {"license": [before, source, after], "empty": []}
    assert hydrated is not original and hydrated["empty"] is not original["empty"]
    assert hydrated["license"][0] == before and hydrated["license"][-1] == after
    extracted = hydrated["license"][1:-1]
    assert [p.read_bytes() for p in extracted] == [images[1], images[0], images[1]]
    assert len(reports) == 1
    assert reports[0]["mode"] == "extract-image" and reports[0]["reason"] == ""
    assert set(reports[0]) == {
        "key", "title", "mode", "sourcePath", "outputs", "elapsedMs", "reason",
    }
    assert all(isinstance(value, str) for value in reports[0].values())
    assert json.loads(reports[0]["outputs"]) == [str(p) for p in extracted]


def test_table_docx_converts_pdf_then_reuses_content_cache(tmp_path, monkeypatch):
    cache = tmp_path / "converted"
    monkeypatch.setattr(materials, "_cache_dir", lambda: cache)
    monkeypatch.setenv("SOFFICE_PATH", str(tmp_path / "custom-soffice.exe"))
    source = tmp_path / "table.docx"
    document = Document()
    document.add_table(rows=2, cols=2).cell(0, 0).text = "Audited financial information"
    document.save(source)
    calls = []
    def convert(args, timeout):
        calls.append((args, timeout))
        folder = Path(args[args.index("--outdir") + 1])
        assert folder.is_relative_to(cache)
        with fitz.open() as pdf:
            pdf.new_page().insert_text((50, 50), "Audited financial information")
            pdf.save(folder / "table.pdf")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(materials.word_convert, "_run_converter", convert)
    original = {"financial": [source]}
    hydrated, reports = materials.hydrate_docx_media(original, timeout=7.5)
    expected = cache / (hashlib.sha256(source.read_bytes()).hexdigest()[:16] + ".pdf")
    assert hydrated == {"financial": [expected]}
    assert reports[0]["mode"] == "convert-pdf" and reports[0]["reason"] == ""
    with fitz.open(expected) as pdf:
        assert pdf.page_count == 1 and "Audited financial information" in pdf[0].get_text()
    assert calls[0][0][0] == str(tmp_path / "custom-soffice.exe")
    assert calls[0][0][-1] == str(source.resolve()) and calls[0][1] == 7.5
    again, reports = materials.hydrate_docx_media(original)
    assert again == hydrated and reports[0]["mode"] == "cached-pdf" and len(calls) == 1
    assert original == {"financial": [source]}


def test_corrupt_word_is_removed_and_failure_explained(tmp_path, monkeypatch):
    monkeypatch.setattr(materials, "_cache_dir", lambda: tmp_path / "converted")
    source = tmp_path / "broken.docx"
    source.write_bytes(b"not an OPC package")
    original = {"license": [source], "untouched": [tmp_path / "scan.png"]}
    for failure, expected in [
        (2, "退出码 2"), (subprocess.TimeoutExpired("soffice", 1), "TimeoutExpired"),
    ]:
        def fail(args, timeout, failure=failure):
            if isinstance(failure, Exception):
                raise failure
            return SimpleNamespace(returncode=failure)
        monkeypatch.setattr(materials.word_convert, "_run_converter", fail)
        hydrated, reports = materials.hydrate_docx_media(original, timeout=1)
        assert hydrated == {"license": [], "untouched": original["untouched"]}
        assert reports[0]["key"] == "license" and reports[0]["title"] == "broken"
        assert reports[0]["mode"] == "failed" and reports[0]["outputs"] == "[]"
        assert reports[0]["reason"] == "未抽取到图片；soffice 转换失败：" + expected
    assert original["license"] == [source] and source.read_bytes() == b"not an OPC package"
