from docx import Document

from api.services.tenders.assemble import _write_toc
from api.services.tenders.schema import BidBrief, DocumentFormat, OutlineItem


def test_toc_styles_and_indents_follow_heading_levels():
    document = Document()
    brief = BidBrief(
        requirementsDriven=True,
        outlineItems=[
            OutlineItem(id='h1', title='一级标题', level=1),
            OutlineItem(id='h2', title='二级标题', level=2),
            OutlineItem(id='h9', title='三级标题', level=3),
        ],
    )

    _write_toc(document, brief.outlineItems, brief, DocumentFormat(), ['h1', 'h2', 'h9'])

    rows = document.paragraphs[1:]
    assert [(row.style.name, row.paragraph_format.left_indent.twips) for row in rows] == [
        ('TOC 1', 0),
        ('TOC 2', 420),
        ('TOC 3', 840),
    ]
