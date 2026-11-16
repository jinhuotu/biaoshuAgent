"""Conservative title cleanup and text-preserving technical body layout."""

import re
from bisect import bisect_left, bisect_right
from collections import Counter
from copy import deepcopy

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.text.run import Run

_TAIL = '；;。.,，、：:）)]】'
_TRAILING = re.compile('[' + re.escape(_TAIL) + r'\s]+$')
_SECTION = re.compile(r'^\s*(?:[（(][0-9一二三四五六七八九十百零〇]+[）)]|\d+[.．](?!\d))')


def strip_title_tail(text: str) -> str:
    value = _TRAILING.sub('', (text or '').strip())
    if len(value) > 6 and value[-1:] in {'o', 'O', '〇'}:
        previous = value[:-1].rstrip()
        if (previous and previous[-1] not in 'oO〇'
                and re.fullmatch(r'[\u3400-\u9fff\u3000-\u303f，；：！？]', previous[-1])):
            value = previous
    return _TRAILING.sub('', value)


def is_section_heading(text) -> bool:
    return bool(_SECTION.match(text or ''))


def _sentence_groups(text):
    sentences = re.findall(r'[^。；]*[。；]|[^。；]+$', text)
    groups, current = [], []
    for sentence in sentences:
        if len(current) >= 2 and (len(current) == 4
                                  or len(''.join(current)) >= 180
                                  or len(''.join(current)) + len(sentence) > 300):
            groups.append(current)
            current = []
        current.append(sentence)
    if current:
        if len(current) == 1 and groups:
            if len(groups[-1]) >= 3:
                current.insert(0, groups[-1].pop())
            else:
                current = groups.pop() + current
        groups.append(current)
    return [''.join(group) for group in groups]


def _split_paragraph(paragraph, chunks):
    """Split plain runs while retaining their properties and each bookmark once."""
    allowed = {qn('w:' + tag) for tag in ('pPr', 'r', 'bookmarkStart', 'bookmarkEnd', 'proofErr')}
    run_allowed = {qn('w:' + tag) for tag in ('rPr', 't', 'tab', 'br', 'cr')}
    if any(child.tag not in allowed for child in paragraph._p):
        return [paragraph]
    for run in paragraph.runs:
        if any(child.tag not in run_allowed for child in run._r):
            return [paragraph]
        if any(node.get(qn('w:type'), 'textWrapping') != 'textWrapping'
               for node in run._r.findall(qn('w:br'))):
            return [paragraph]
    cuts, offset = [], 0
    for chunk in chunks[:-1]:
        offset += len(chunk)
        cuts.append(offset)
    boundaries = [0, *cuts, len(paragraph.text)]
    nodes = [OxmlElement('w:p') for _ in chunks]
    for index, node in enumerate(nodes):
        for name, value in paragraph._p.attrib.items():
            if index == 0 or name not in {qn('w14:paraId'), qn('w14:textId')}:
                node.set(name, value)
        if paragraph._p.pPr is not None:
            node.append(deepcopy(paragraph._p.pPr))
    offset = 0
    for child in paragraph._p:
        if child.tag == qn('w:pPr'):
            continue
        if child.tag != qn('w:r'):
            index = (bisect_left(cuts, offset) if child.tag == qn('w:bookmarkEnd')
                     else bisect_right(cuts, offset))
            nodes[index].append(deepcopy(child))
            continue
        value = Run(child, paragraph).text
        if not value:
            nodes[bisect_right(cuts, offset)].append(deepcopy(child))
        for index, (start, end) in enumerate(zip(boundaries[:-1], boundaries[1:], strict=True)):
            left, right = max(start, offset), min(end, offset + len(value))
            if left < right:
                clone = deepcopy(child)
                Run(clone, paragraph).text = value[left - offset:right - offset]
                nodes[index].append(clone)
        offset += len(value)
    for node in nodes:
        paragraph._p.addprevious(node)
    paragraph._p.getparent().remove(paragraph._p)
    return [Paragraph(node, paragraph._parent) for node in nodes]


def _line_spacing(paragraph):
    style, properties, visited = paragraph.style, [paragraph._p.pPr], set()
    while style is not None and style.style_id not in visited:
        visited.add(style.style_id)
        properties.append(style.element.find(qn('w:pPr')))
        style = style.base_style
    for props in properties:
        spacing = props.find(qn('w:spacing')) if props is not None else None
        if spacing is not None and spacing.get(qn('w:line')):
            return spacing.get(qn('w:line')), spacing.get(qn('w:lineRule'), 'auto')
    return None


def typeset_body(document, fmt) -> dict:
    from api.services.tenders.technical_toc import _regions

    excluded = {node for region in _regions(document) for host in region for node in host.iter()}
    report = {'indented': 0, 'split': 0, 'skipped': 0}
    targets = []
    for paragraph in document.paragraphs:
        props = paragraph._p.pPr
        unsafe = any(list(paragraph._p.iter(qn('w:' + tag)))
                     for tag in ('drawing', 'pict', 'fldChar', 'instrText', 'fldSimple'))
        outlined = props is not None and props.find(qn('w:outlineLvl')) is not None
        section = props is not None and props.find(qn('w:sectPr')) is not None
        if (paragraph.style.name not in {'Normal', 'List Paragraph', 'Body Text'}
                or len(paragraph.text) < 60 or paragraph._p in excluded
                or unsafe or outlined or section):
            report['skipped'] += 1
        else:
            targets.append(paragraph)
    measured = Counter(value for p in targets if (value := _line_spacing(p)) is not None)
    fallback = measured.most_common(1)[0][0] if measured else ('360', 'auto')
    for paragraph in targets:
        line, rule = _line_spacing(paragraph) or fallback
        props = paragraph._p.get_or_add_pPr()
        indent = props.find(qn('w:ind'))
        if indent is None:
            indent = OxmlElement('w:ind')
            props.append(indent)
        for attribute in ('firstLine', 'hanging', 'hangingChars', 'leftChars', 'rightChars'):
            indent.attrib.pop(qn('w:' + attribute), None)
        indent.set(qn('w:firstLineChars'), '200')
        indent.set(qn('w:left'), '0')
        indent.set(qn('w:right'), '0')
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        spacing = props.find(qn('w:spacing'))
        if spacing is None:
            spacing = OxmlElement('w:spacing')
            props.append(spacing)
        spacing.set(qn('w:line'), line)
        spacing.set(qn('w:lineRule'), rule)
        chunks = _sentence_groups(paragraph.text) if len(paragraph.text) > 300 else [paragraph.text]
        fragments = _split_paragraph(paragraph, chunks) if len(chunks) > 1 else [paragraph]
        report['indented'] += len(fragments)
        report['split'] += len(fragments) - 1
    return report
