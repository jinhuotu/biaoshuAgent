"""标书填空横线：源稿下划线保留、已填值加下划线、落款空位画横线。"""

from __future__ import annotations

from docx import Document

from api.services.bidgen.document_model import paragraph, signature, underline
from api.services.bidgen.docx_reader import read_docx_blocks, template_paragraph
from api.services.bidgen.word_writer import _add_blocks, _signature_form

START, END = "", ""


def _underlined_runs(para) -> list[tuple[str, bool]]:
    return [(run.text, bool(run.font.underline)) for run in para.runs if run.text]


def test_reader_keeps_source_blank_underlines(tmp_path):
    source = Document()
    line = source.add_paragraph()
    line.add_run("姓名：")
    line.add_run("      ").underline = True
    line.add_run("性别：")
    line.add_run("    ").underline = True
    source.add_paragraph("特此证明。")
    path = tmp_path / "form.docx"
    source.save(path)

    blocks = read_docx_blocks(path)

    assert blocks[0].text == "姓名：      性别："
    assert blocks[0].form == f"姓名：{START}      {END}性别：{START}    {END}"
    assert template_paragraph(blocks[0]) == {"kind": "paragraph", "text": blocks[0].text, "form": blocks[0].form}
    assert blocks[1].form == ""
    assert template_paragraph(blocks[1]) == {"kind": "paragraph", "text": "特此证明。"}


def test_filled_value_replaces_blank_and_text_stays_plain():
    block = paragraph(f"系{START}      {END}{underline('甲公司')}的单位负责人。")

    assert block["text"] == "系甲公司的单位负责人。"
    assert block["form"] == f"系{START}甲公司{END}的单位负责人。"


def test_empty_value_leaves_blank_line():
    block = paragraph(f"姓名：{underline('')}")

    assert block["text"] == "姓名：" + "　" * 8
    assert block["form"] == f"姓名：{START}{chr(0x3000) * 8}{END}"


def test_signature_line_underlines_value_or_blank():
    assert _signature_form("投标人：甲公司（盖单位章）") == f"投标人：{START}甲公司{END}（盖单位章）"
    assert _signature_form("日期：2026年11月14日") == f"日期：{START}2026年11月14日{END}"
    assert _signature_form("被授权人签名：＿＿＿＿") == f"被授权人签名：{START}{chr(0x3000) * 8}{END}"
    assert _signature_form("特此证明。") == "特此证明。"


def test_writer_renders_underlined_runs():
    document = Document()
    _add_blocks(
        document,
        [paragraph(f"供应商名称：{underline('甲公司')}"), signature(["供应商：甲公司（盖单位章）"])],
        [0],
    )
    form_line, sign_line = document.paragraphs[-2:]

    assert _underlined_runs(form_line) == [("供应商名称：", False), ("甲公司", True)]
    assert _underlined_runs(sign_line) == [("供应商：", False), ("甲公司", True), ("（盖单位章）", False)]


def test_writer_ignores_stale_form_after_text_rewrite():
    block = paragraph(f"供应商名称：{underline('甲公司')}")
    block["text"] = "供应商名称：乙公司"  # 生成后被改写
    document = Document()
    _add_blocks(document, [block], [0])

    assert _underlined_runs(document.paragraphs[-1]) == [("供应商名称：乙公司", False)]
