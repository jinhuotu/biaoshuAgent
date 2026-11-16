from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from api.services.tenders.heading_style import has_own_number, normalize_heading_paragraphs


POINTS = {'level1': (16.0, '宋体'), 'level2': (14.0, '宋体')}


def _num_pr(paragraph, num_id):
    props = paragraph._p.get_or_add_pPr()
    num_pr = OxmlElement('w:numPr')
    ilvl = OxmlElement('w:ilvl')
    ilvl.set(qn('w:val'), '0')
    num = OxmlElement('w:numId')
    num.set(qn('w:val'), str(num_id))
    num_pr.extend([ilvl, num])
    props.append(num_pr)


def test_heading_numbering_is_textual_and_list_paragraphs_are_untouched():
    document = Document()
    first = document.add_paragraph('项目管理机构组成表', style='Heading 2')
    second = document.add_paragraph('投标人基本情况表', style='Heading 2')
    already = document.add_paragraph('（一）已有编号标题', style='Heading 2')
    ordinary = document.add_paragraph('正文列表', style='List Paragraph')
    for paragraph in (first, second, already):
        _num_pr(paragraph, 185)
    _num_pr(ordinary, 185)

    changes = normalize_heading_paragraphs(document, POINTS)

    assert first.text.startswith('（一）')
    assert second.text.startswith('（二）')
    assert already.text == '（一）已有编号标题'
    assert changes[first._p.getparent().index(first._p)]['n'] == 1
    assert changes[second._p.getparent().index(second._p)]['n'] == 2
    assert first._p.pPr.find(qn('w:numPr')) is None
    assert second._p.pPr.find(qn('w:numPr')) is None
    assert ordinary._p.pPr.find(qn('w:numPr')) is not None


def test_existing_numbered_heading_is_not_prefixed_and_plain_numbered_heading_is():
    document = Document()
    own = document.add_paragraph('一、投标函', style='Heading 1')
    generated = document.add_paragraph('项目管理机构组成表', style='Heading 2')
    plain = document.add_paragraph('一、投标函', style='Heading 1')
    _num_pr(own, 185)
    _num_pr(generated, 186)

    normalize_heading_paragraphs(document, POINTS)

    assert own.text == '一、投标函'
    assert generated.text == '（一）项目管理机构组成表'
    assert plain.text == '一、投标函'
    assert own._p.pPr.find(qn('w:numPr')) is None
    assert generated._p.pPr.find(qn('w:numPr')) is None
    assert plain._p.pPr.find(qn('w:numPr')) is None


def test_has_own_number_covers_supported_prefixes():
    numbered = (
        '（一）标题', '(二)标题', '十一、标题', '十二．标题',
        '第十章 标题', '1.标题', '2、标题', '3．标题',
    )
    assert all(has_own_number(text) for text in numbered)
    assert not has_own_number('项目管理机构组成表')


def test_numbering_group_uses_first_heading_as_the_group_decision():
    document = Document()
    first = document.add_paragraph('一、根标题', style='Heading 1')
    later = document.add_paragraph('子标题', style='Heading 2')
    _num_pr(first, 190)
    _num_pr(later, 190)

    normalize_heading_paragraphs(document, POINTS)

    assert first.text == '一、根标题'
    assert later.text == '子标题'

