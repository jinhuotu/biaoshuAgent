from __future__ import annotations

import base64
import uuid
from decimal import Decimal
from pathlib import Path

import fitz
from docx import Document
from openpyxl import Workbook

from api.services.quotes.ingest import ingest
from api.services.quotes.ingest.drawing_parser import parse as parse_drawing


def _work() -> Path:
    path = Path("tmp") / f"quote-ingest-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _xlsx(path: Path, rows: list[list[object]], *, title: str = "清单") -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for row in rows:
        ws.append(row)
    wb.save(path)
    return path


def test_xlsx_with_header_scans_all_sheets() -> None:
    tmp_path = _work()
    path = _xlsx(tmp_path / "a.xlsx", [["名称", "规格型号", "单位", "数量"], ["地砖", "800", "㎡", 10]])
    wb = Workbook()
    ws = wb.active
    ws.title = "一"
    ws.append(["名称", "单位", "数量"])
    ws.append(["地砖", "㎡", 10])
    other = wb.create_sheet("二")
    other.append(["名称", "单位", "数量"])
    other.append(["乳胶漆", "桶", 2])
    wb.save(path)
    result = ingest([path])
    assert {line.name for line in result["lines"]} == {"地砖", "乳胶漆"}


def test_xlsx_without_header_uses_guess() -> None:
    tmp_path = _work()
    path = _xlsx(tmp_path / "a.xlsx", [[1, "木门", "M1", "樘", 2, 100], [2, "地砖", "800", "㎡", 10, 80]])
    lines = ingest([path])["lines"]
    assert lines[0].name == "木门"
    assert lines[0].qty == Decimal("2")
    assert "表头为推测" in lines[0].notes


def test_csv_is_parsed() -> None:
    tmp_path = _work()
    path = tmp_path / "a.csv"
    path.write_text("名称,单位,数量\n踢脚线,米,12\n", encoding="utf-8")
    line = ingest([path])["lines"][0]
    assert line.name == "踢脚线"
    assert line.qty == Decimal("12")


def test_docx_table_is_parsed() -> None:
    tmp_path = _work()
    path = tmp_path / "a.docx"
    doc = Document()
    table = doc.add_table(rows=2, cols=4)
    for i, row in enumerate([["名称", "规格", "单位", "数量"], ["门", "M1", "樘", "3"]]):
        for j, value in enumerate(row):
            table.cell(i, j).text = value
    doc.save(path)
    line = ingest([path])["lines"][0]
    assert line.name == "门"
    assert line.qty == Decimal("3")


def test_pdf_text_is_parsed() -> None:
    tmp_path = _work()
    path = tmp_path / "a.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Door 5 set")
    pdf.save(path)
    pdf.close()
    lines = ingest([path])["lines"]
    assert lines and lines[0].qty == Decimal("5")


def test_drawing_old_keys_are_mapped() -> None:
    tmp_path = _work()
    path = tmp_path / "drawing.png"
    path.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
    lines = parse_drawing(path, options={"vision_data": {"chargers": [{"name": "设备A", "qty": 2, "unit": "台"}]}})
    assert len(lines) == 1
    assert lines[0].name == "设备A"
    assert lines[0].source == "drawing"


def test_same_name_different_units_conflict() -> None:
    tmp_path = _work()
    first = _xlsx(tmp_path / "a.xlsx", [["名称", "单位", "数量"], ["阀门", "个", 2]])
    second = _xlsx(tmp_path / "b.xlsx", [["名称", "单位", "数量"], ["阀门", "套", 1]])
    lines = ingest([first, second])["lines"]
    assert len(lines) == 2
    assert all(line.status == "conflict" for line in lines)
    assert any("单位不一致" in note for note in lines[0].notes)


def test_missing_quantity_is_pending() -> None:
    tmp_path = _work()
    path = _xlsx(tmp_path / "a.xlsx", [["名称", "单位", "数量"], ["灯具", "盏", ""]])
    line = ingest([path])["lines"][0]
    assert line.status == "pending"
    assert "缺数量" in line.notes


def test_evidence_merge_is_capped_at_five() -> None:
    tmp_path = _work()
    path = _xlsx(tmp_path / "a.xlsx", [["名称", "单位", "数量"], ["水泥", "吨", 1]])
    line = ingest([path] * 8)["lines"][0]
    assert len(line.evidence["items"]) == 5
    assert line.qty == Decimal("8")
