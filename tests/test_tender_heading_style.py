import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from api.services.tenders.assemble import _write_toc
from api.services.tenders.document import finalize_body_layout
from api.services.tenders.generate import generate_bid
from api.services.tenders.heading_style import (
    apply_heading_styles,
    normalize_heading_paragraphs,
    resolve_heading_points,
)
from api.services.tenders.schema import BidBrief, DocumentFormat, OutlineItem, VolumeFormat
from api.services.tenders.technical_render import render

POINTS = {'level1': (16.0, '宋体'), 'level2': (14.0, '宋体'), 'source': 'fallback'}


def test_missing_chapter_styles_use_16_and_14_point_fallbacks():
    document = Document()
    document.styles['Heading 5'].delete()
    for name in ['Heading 6', 'Heading 7', 'Heading 8']:
        document.styles[name].font.size = None
    assert resolve_heading_points(document) == POINTS


def test_36_point_cover_styles_are_never_used_as_chapter_sizes():
    document = Document()
    for name in ['Heading 2', 'Heading 5', 'Heading 7']:
        document.styles[name].font.size = Pt(36)
    document.styles['Heading 6'].font.size = Pt(20)
    document.styles['Heading 8'].font.size = Pt(16)
    assert resolve_heading_points(document) == POINTS


def test_heading_two_loses_indents_tabs_and_run_spacing_and_centers():
    document = Document()
    document.add_paragraph('目录')
    paragraph = document.add_paragraph('二级标题', style='Heading 2')
    paragraph.runs[0].font.size = Pt(18)
    props = paragraph._p.get_or_add_pPr()
    indent = OxmlElement('w:ind')
    indent.set(qn('w:left'), '4069')
    indent.set(qn('w:hanging'), '467')
    props.append(indent)
    props.append(OxmlElement('w:tabs'))
    spacing = OxmlElement('w:spacing')
    spacing.set(qn('w:val'), '-5')
    paragraph.runs[0]._r.get_or_add_rPr().append(spacing)
    apply_heading_styles(document, POINTS)
    changes = normalize_heading_paragraphs(document, POINTS)
    assert props.find(qn('w:ind')) is None and props.find(qn('w:tabs')) is None
    assert props.find(qn('w:jc')).get(qn('w:val')) == 'center'
    assert paragraph.runs[0]._r.rPr.find(qn('w:spacing')) is None
    assert paragraph.runs[0].font.size is None
    assert changes[1]['from_size'] == 18 and changes[1]['to_size'] == 14
    assert set(changes[1]['removed']) == {'sz', 'spacing', 'tabs', 'ind'}


def test_heading_one_removes_direct_size_and_inherits_16_points():
    document = Document()
    paragraph = document.add_paragraph('一级标题', style='Heading 1')
    size = OxmlElement('w:sz')
    size.set(qn('w:val'), '36')
    paragraph.runs[0]._r.get_or_add_rPr().append(size)
    apply_heading_styles(document, POINTS)
    normalize_heading_paragraphs(document, POINTS)
    assert paragraph.runs[0]._r.rPr.find(qn('w:sz')) is None
    assert paragraph.style.font.size.pt == POINTS['level1'][0]
    assert paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER


def test_sizes_are_clamped_and_excluded_chinese_fonts_fall_back():
    document = Document()
    document.styles['Heading 5'].font.size = Pt(10)
    document.styles['Heading 7'].font.size = Pt(25)
    for name, font in [('Heading 5', 'Microsoft JhengHei'), ('Heading 7', '楷体')]:
        document.styles[name].font.name = font
        document.styles[name].element.rPr.rFonts.set(qn('w:eastAsia'), font)
    assert resolve_heading_points(document) == {
        'level1': (14.0, '宋体'), 'level2': (18.0, '楷体'), 'source': 'Heading 5/Heading 7',
    }
    document.styles['Heading 5'].font.size = None
    document.styles['Heading 6'].font.size = Pt(19)
    document.styles['Heading 7'].font.size = None
    document.styles['Heading 8'].font.size = Pt(13)
    result = resolve_heading_points(document)
    assert result['level1'][0] == 19 and result['level2'][0] == 13
    assert result['source'] == 'Heading 6/Heading 8'


def test_styles_keep_outline_and_other_heading_definitions_and_set_both_sizes():
    document = Document()
    outlines = {name: document.styles[name].element.pPr.xml for name in ['Heading 1', 'Heading 2']}
    untouched = {f'Heading {i}': document.styles[f'Heading {i}'].element.xml for i in range(3, 10)}
    report = apply_heading_styles(document, POINTS)
    assert set(report['applied']) == {'Heading 1', 'Heading 2'}
    for name, size in [('Heading 1', '32'), ('Heading 2', '28')]:
        style = document.styles[name]
        assert style.element.pPr.xml == outlines[name]
        assert style.element.rPr.rFonts.get(qn('w:eastAsia')) == '宋体'
        assert style.element.rPr.rFonts.get(qn('w:ascii')) == '宋体'
        assert style.element.rPr.find(qn('w:sz')).get(qn('w:val')) == size
        assert style.element.rPr.find(qn('w:szCs')).get(qn('w:val')) == size
    assert all(document.styles[name].element.xml == xml for name, xml in untouched.items())


def test_inherited_cover_size_is_preserved_without_run_sizes_and_is_idempotent():
    document = Document()
    document.styles['Heading 2'].font.size = Pt(36)
    cover = document.add_paragraph('投 标 文 件', style='Heading 2')
    document.add_paragraph('目 录')
    toc = document.add_paragraph('第一章\t1')
    body = document.add_paragraph('第一章', style='Heading 1')
    ordinary = document.add_paragraph('正文保持原样')
    ordinary.runs[0].font.size = Pt(12)
    toc_xml, ordinary_xml = toc._p.xml, ordinary._p.xml
    texts = [p.text for p in document.paragraphs]
    apply_heading_styles(document, POINTS)
    first = normalize_heading_paragraphs(document, POINTS)
    assert len(first) == 2
    assert cover.style.name == 'Tender Cover Heading 2' and cover.style.font.size.pt == 36
    assert cover.runs[0].font.size is None and cover.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert body.runs[0].font.size is None and body.style.font.size.pt == 16
    assert [p.text for p in document.paragraphs] == texts
    assert toc._p.xml == toc_xml and ordinary._p.xml == ordinary_xml
    once = (document.element.xml, document.styles.element.xml)
    apply_heading_styles(document, POINTS)
    assert normalize_heading_paragraphs(document, POINTS) == {}
    assert (document.element.xml, document.styles.element.xml) == once


@pytest.mark.parametrize('heading_name', ['heading 1', 'Heading 1'])
def test_generate_from_word_preserves_original_heading_format(tmp_path, monkeypatch, heading_name):
    source = Document()
    style = source.styles['Heading 1']
    style.font.name = '楷体'
    style.font.size = Pt(27)
    style.font.color.rgb = RGBColor.from_string('294B83')
    source.styles['Heading 2'].delete()

    title = '货物交付及售后服务能力总体说明'
    heading = source.add_paragraph(title, style)
    heading.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    heading.paragraph_format.left_indent = Pt(12)
    heading.paragraph_format.tab_stops.add_tab_stop(Pt(48))
    heading.paragraph_format.space_before = Pt(9)
    heading._p.pPr.get_or_add_numPr().get_or_add_numId().val = 5
    run = heading.runs[0]
    run.font.name = '黑体'
    run.font.size = Pt(23)
    run.font.color.rgb = RGBColor.from_string('782458')
    run.bold = True
    style.element.find(qn('w:name')).set(qn('w:val'), heading_name)

    body_text = '本项目各项服务将按招标文件的约定执行。'
    source.add_paragraph(body_text)
    source_path = tmp_path / 'source.format.docx'
    source.save(source_path)
    source_path.with_suffix('.docx.source').write_text('docx-raw', encoding='utf-8')
    original_bytes = source_path.read_bytes()
    monkeypatch.setattr('api.services.tenders.pdf_format._word_format_file', lambda _: source_path)
    monkeypatch.setattr('api.services.tenders.generate.tenders_output_dir', lambda: tmp_path)

    brief = BidBrief(
        layoutMode='outline', invitationId='abc123def456', generateVolume='business',
        documentFormat=DocumentFormat(businessFormat=VolumeFormat(
            fontSet=True, fontName='宋体', bodySizeSet=True, bodySizePt=12,
        )),
    )
    result = generate_bid(brief, catalog_slots=[], catalog_media={}, catalog_facts=[])
    output = Document(tmp_path / result['docxFile'])
    generated = next(p for p in output.paragraphs if p.text == title)

    assert generated.style.style_id == 'Heading1'
    assert generated.style.element.xml == style.element.xml
    # TOC pagination may add a page break before the first body heading.
    for name in ('pStyle', 'jc', 'ind', 'tabs', 'spacing', 'numPr', 'outlineLvl'):
        expected = heading._p.pPr.find(qn(f'w:{name}'))
        actual = generated._p.pPr.find(qn(f'w:{name}'))
        if expected is None:
            assert actual is None
        else:
            assert actual.xml == expected.xml
    assert generated.runs[0]._r.rPr.xml == run._r.rPr.xml
    assert output.part.numbering_part.element.xml == source.part.numbering_part.element.xml
    assert next(p for p in output.paragraphs if p.text == body_text).runs[0].font.size.pt == 12
    assert result['headingStyleReport']['source'] == 'original'
    assert source_path.read_bytes() == original_bytes


def test_technical_toc_does_not_require_standard_heading_style_names():
    document = Document()
    style = document.styles['Heading 1']
    style.element.find(qn('w:name')).set(qn('w:val'), 'Heading 1')
    document.styles['Heading 2'].delete()
    title = '施工组织设计'
    original = document.add_paragraph(title, style)
    original_xml = original._p.xml
    brief = BidBrief(generateVolume='technical', layoutMode='outline')
    items = [OutlineItem(id='design', title=title, rawTitle=title, kind='tech_chapter')]

    _write_toc(document, items, brief, brief.documentFormat, ['toc_design'], subset=True)

    assert original._p.xml == original_xml
    assert sum(title in p.text for p in document.paragraphs) == 2


def test_technical_content_respects_numbering_and_typography_of_source_heading():
    document = Document()
    title = '质量保障措施'
    heading = document.add_paragraph(title, 'Heading 2')
    heading.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    heading.paragraph_format.left_indent = Pt(18)
    heading._p.pPr.get_or_add_numPr().get_or_add_numId().val = 5
    heading.runs[0].font.size = Pt(20)
    original_paragraph_format = heading._p.pPr.xml
    original_run_format = heading.runs[0]._r.rPr.xml
    text = '验收前核对批次资料与实物标识，逐项记录核验结果。'
    brief = BidBrief(technicalPackage={
        'sections': [{'id': 'quality', 'title': title, 'body': text}],
        'tables': [], 'figures': [],
    })

    render(document, brief, preserve_headings=True)

    assert heading.text == title
    assert heading._p.pPr.xml == original_paragraph_format
    assert heading.runs[0]._r.rPr.xml == original_run_format
    assert document.paragraphs[1].text == text


def test_source_heading_style_keeps_its_indent_in_a_signoff_block():
    document = Document()
    signing = document.add_paragraph('投标人（加盖公章）：', 'Heading 6')
    date = document.add_paragraph('日期：2026年11月21日')
    date.paragraph_format.left_indent = Pt(57.7)
    original = signing._p.pPr.xml

    finalize_body_layout(document, preserve_headings=True)

    assert signing._p.pPr.xml == original
    assert date.paragraph_format.left_indent == Pt(57.7)
