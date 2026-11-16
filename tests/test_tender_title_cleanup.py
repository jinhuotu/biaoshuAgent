from copy import deepcopy
from hashlib import md5
from types import SimpleNamespace

import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

from api.services.tenders.title_cleanup import (
    is_section_heading,
    strip_title_tail,
    typeset_body,
)


@pytest.mark.parametrize(('original', 'expected'), [
    ('主要施工方案与技术措施；', '主要施工方案与技术措施'),
    ('招标文件规定的其他内容 o', '招标文件规定的其他内容'),
    ('近年财务状况表。', '近年财务状况表'),
    ('投标文件', '投标文件'),
    ('BIM 技术应用；', 'BIM 技术应用'),
    ('  方案；中间，内容；。）  ', '方案；中间，内容'),
    ('招标文件规定的其他内容O', '招标文件规定的其他内容'),
    ('招标文件规定的其他内容〇', '招标文件规定的其他内容'),
    ('BIM Studio', 'BIM Studio'),
    ('中文方案FOO', '中文方案FOO'),
    ('方案o', '方案o'),
    ('质量保证承诺书', '质量保证承诺书'),
    ('企业资质证', '企业资质证'),
])
def test_strip_title_tail_changes_only_authorized_suffixes(original, expected):
    assert strip_title_tail(original) == expected
    assert strip_title_tail(expected) == expected


def test_section_prefixes_are_not_body_decimal_numbers():
    for title in ['（1）施工方案', '(13)附表', '2.质量措施', '（十三）附表']:
        assert is_section_heading(title)
    for title in ['3.14倍', '正文（1）', '普通正文']:
        assert not is_section_heading(title)


def test_body_splits_preserve_text_runs_bookmarks_and_indentation():
    doc = Document()
    paragraph = doc.add_paragraph()
    sentences = ['甲' * 85 + '。', '乙' * 85 + '；', '丙' * 85 + '。', '丁' * 85 + '；']
    for sentence in sentences:
        run = paragraph.add_run(sentence)
        run.bold = sentence.startswith('甲')
        run.font.size = Pt(12)
    paragraph.paragraph_format.line_spacing = Pt(24)
    paragraph.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    bookmark = OxmlElement('w:bookmarkStart')
    bookmark.set(qn('w:id'), '7')
    bookmark.set(qn('w:name'), 'body_anchor')
    paragraph._p.insert(1, bookmark)
    end = OxmlElement('w:bookmarkEnd')
    end.set(qn('w:id'), '7')
    paragraph._p.append(end)
    old = paragraph.text
    old_properties = [r._r.rPr.xml for r in paragraph.runs]
    report = typeset_body(doc, SimpleNamespace(bodySizePt=12))
    assert report['split'] == 1 and report['indented'] == 2
    assert [p.text for p in doc.paragraphs] == [''.join(sentences[:2]), ''.join(sentences[2:])]
    after = ''.join(p.text for p in doc.paragraphs)
    assert md5(after.encode()).digest() == md5(old.encode()).digest()
    assert [r._r.rPr.xml for p in doc.paragraphs for r in p.runs] == old_properties
    assert len(list(doc.element.body.iter(qn('w:bookmarkStart')))) == 1
    assert len(list(doc.element.body.iter(qn('w:bookmarkEnd')))) == 1
    for p in doc.paragraphs:
        assert p._p.pPr.find(qn('w:ind')).get(qn('w:firstLineChars')) == '200'
        assert p._p.pPr.find(qn('w:ind')).get(qn('w:firstLine')) is None
        assert p.alignment == WD_ALIGN_PARAGRAPH.JUSTIFY
        assert p.paragraph_format.line_spacing.pt == 24


def test_tables_pictures_and_toc_regions_remain_byte_for_byte_untouched():
    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    table.cell(0, 0).text = '表内资料' * 100
    picture = doc.add_paragraph('图像说明' * 100)
    picture.runs[0]._r.append(OxmlElement('w:drawing'))
    begin = doc.add_paragraph()
    fields = [('fldChar', 'begin'), ('instrText', ' TOC \\o "1-3" '), ('fldChar', 'separate')]
    for tag, value in fields:
        node = OxmlElement('w:' + tag)
        if tag == 'fldChar':
            node.set(qn('w:fldCharType'), value)
        else:
            node.text = value
        begin.add_run()._r.append(node)
    row = doc.add_paragraph('目录缓存' * 100)
    finish = doc.add_paragraph()
    field_end = OxmlElement('w:fldChar')
    field_end.set(qn('w:fldCharType'), 'end')
    finish.add_run()._r.append(field_end)
    originals = [e.xml for e in [table._tbl, picture._p, begin._p, row._p, finish._p]]
    report = typeset_body(doc, SimpleNamespace(bodySizePt=12))
    assert report['indented'] == report['split'] == 0
    assert [e.xml for e in [table._tbl, picture._p, begin._p, row._p, finish._p]] == originals


def test_missing_line_spacing_falls_back_to_one_and_a_half():
    doc = Document()
    paragraph = doc.add_paragraph('正文内容' * 20)
    report = typeset_body(doc, SimpleNamespace(bodySizePt=12))
    assert report['indented'] == 1
    assert paragraph.paragraph_format.line_spacing == 1.5


@pytest.mark.parametrize('numbered', [True, False])
def test_render_preserves_numbering_or_restores_data_order(monkeypatch, numbered):
    from api.services.tenders import pdf_format, technical_render
    from api.services.tenders.schema import BidBrief

    monkeypatch.setattr(pdf_format, '_word_format_file', lambda _: None)
    doc = Document()
    heading = doc.add_paragraph('施工方案；', style='List Paragraph')
    original_number = None
    if numbered:
        num = OxmlElement('w:numPr')
        num.append(OxmlElement('w:ilvl'))
        num[0].set(qn('w:val'), '0')
        num.append(OxmlElement('w:numId'))
        num[1].set(qn('w:val'), '42')
        heading._p.get_or_add_pPr().append(num)
        original_number = deepcopy(num)
    brief = BidBrief(bidderName='测试公司', technicalPackage={
        'sections': [{'id': 'p1', 'title': '施工方案；', 'body': '按原文执行施工。'}],
        'tables': [], 'figures': [],
    })
    technical_render.render(doc, brief)
    assert heading.text == ('施工方案' if numbered else '（1）施工方案')
    if numbered:
        actual = heading._p.pPr.find(qn('w:numPr'))
        assert [(node.tag, dict(node.attrib)) for node in actual] == [
            (node.tag, dict(node.attrib)) for node in original_number
        ]
    assert all(r.bold and r.font.size.pt == 14 for r in heading.runs)
    assert heading.alignment == WD_ALIGN_PARAGRAPH.LEFT
    assert heading._p.pPr.find(qn('w:outlineLvl')).get(qn('w:val')) == '1'
    assert heading._p.pPr.find(qn('w:ind')).get(qn('w:firstLineChars')) == '0'
    assert heading.paragraph_format.space_before.pt == heading.paragraph_format.space_after.pt == 6


def test_long_source_paragraph_is_not_upgraded_as_a_heading(monkeypatch):
    from api.services.tenders import pdf_format, technical_render
    from api.services.tenders.schema import BidBrief

    monkeypatch.setattr(pdf_format, '_word_format_file', lambda _: None)
    original = '本段为原版正文需要保留原样' * 4
    doc = Document()
    heading = doc.add_paragraph(original)
    brief = BidBrief(bidderName='测试公司', technicalPackage={
        'sections': [{'id': 'p1', 'title': original, 'body': '按原文执行。'}],
        'tables': [], 'figures': [],
    })
    notes = technical_render.render(doc, brief)
    assert heading.text == original and heading.runs[0].bold is None
    assert any('超过40字' in note for note in notes)


def test_final_layout_retains_section_left_and_body_justified(monkeypatch):
    from api.services.tenders import pdf_format, technical_render
    from api.services.tenders.document import finalize_body_layout
    from api.services.tenders.schema import BidBrief

    monkeypatch.setattr(pdf_format, '_word_format_file', lambda _: None)
    doc = Document()
    heading = doc.add_paragraph('施工方案；', style='List Paragraph')
    brief = BidBrief(bidderName='测试公司', technicalPackage={
        'sections': [{'id': 'p1', 'title': '施工方案；', 'body': '按原文核对现场条件。' * 10}],
        'tables': [], 'figures': [],
    })
    technical_render.render(doc, brief)
    finalize_body_layout(doc, brief.documentFormat)
    assert heading.alignment == WD_ALIGN_PARAGRAPH.LEFT
    assert heading.style.name == 'Technical Section Heading'
    body = next(p for p in doc.paragraphs if len(p.text) >= 60)
    assert body.alignment == WD_ALIGN_PARAGRAPH.JUSTIFY
    assert body._p.pPr.find(qn('w:ind')).get(qn('w:firstLineChars')) == '200'
