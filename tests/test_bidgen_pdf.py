"""PDF 招标文件：标题层级、目录去重、同行标题拆分、带框线表格。夹具用 PyMuPDF 现场绘制。"""

import fitz

from api.services.bidgen.archive import ArchiveEntry
from api.services.bidgen.pdf_reader import read_pdf_blocks
from api.services.bidgen.tender_parser import parse_tender_package


def make_pdf(path):
    document = fitz.open()
    page = document.new_page()
    y = 60

    def write(text, size=11):
        nonlocal y
        page.insert_text((50, y), text, fontname="china-s", fontsize=size)
        y += size + 9

    write("第三章 评审办法", 16)
    # 带框线的评分表：条款号及名称 | 评分因素 | 评分标准（4 列数据行）
    rows = [["条款号及名称", "评分因素", "", "评分标准"],
            ["9", "技术部分（10分）", "需求理解（5分）", "理解深刻"],
            ["", "", "运维保障方案（5分）", "服务及时"]]
    top, widths = y, [90, 110, 120, 170]
    for r, row in enumerate(rows):
        x = 50
        for c, text in enumerate(row):
            rect = fitz.Rect(x, top + r * 24, x + widths[c], top + (r + 1) * 24)
            page.draw_rect(rect, color=(0, 0, 0), width=0.6)
            if text:
                page.insert_text((x + 3, rect.y1 - 8), text, fontname="china-s", fontsize=9)
            x += widths[c]
    y = top + len(rows) * 24 + 30
    write("第六章 响应文件格式", 16)
    write("目录")
    for line in ("一、响应函", "二、单位负责人身份证明", "九、技术响应文件"):
        write(line)
    write("一、响应函")
    write("（采购人名称）：根据贵方采购文件，我单位自愿参加谈判。")
    write("备注：此页签字或盖章均有效。 二、单位负责人身份证明")
    write("系（供应商名称）的单位负责人。")
    write("九、技术响应文件")
    write("1.需求理解")
    write("2.运维保障方案")
    write("第七章 其他", 16)
    document.save(path)


def test_pdf_blocks_keep_headings_tables_and_split_tail_headings(tmp_path):
    path = tmp_path / "采购文件.pdf"
    make_pdf(path)
    blocks = read_pdf_blocks(path)
    titles = [(b.level, b.text) for b in blocks if b.kind == "paragraph" and b.level]
    assert (1, "第三章 评审办法") in titles and (1, "第六章 响应文件格式") in titles
    assert any(b.text == "二、单位负责人身份证明" for b in blocks)  # 与上一句挤在同一行的标题被拆出
    table = next(b for b in blocks if b.kind == "table")
    assert table.rows[1][2] == "需求理解（5分）"


def test_pdf_tender_parses_format_directory_and_scoring(tmp_path):
    path = tmp_path / "采购文件.pdf"
    make_pdf(path)
    entry = ArchiveEntry(path.name, path.name, [], 0, ".pdf", path.stat().st_size, path.name)
    info = parse_tender_package([entry], tmp_path, path.name)
    assert [(item.number, item.title) for item in info.format_items] == [
        ("1", "响应函"), ("2", "单位负责人身份证明"), ("9", "技术响应文件")]
    assert [(c.factor, c.max_score) for c in info.criteria] == [("需求理解", 5), ("运维保障方案", 5)]
