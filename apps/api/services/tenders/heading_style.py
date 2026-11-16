"""Separate chapter heading styles from inherited cover typography."""

from __future__ import annotations

from copy import deepcopy
import re

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from docx.text.paragraph import Paragraph
from docx.text.run import Run

_HEADINGS = {'Heading 1': 'level1', 'Heading 2': 'level2'}
_NUMBERED_HEADINGS = {'Heading 1', 'Heading 2', 'Heading 9'}
_COVER_PREFIX = 'Tender Cover '
_OWN_NUMBER = re.compile(
    r'^\s*(?:'
    r'[（(][一二三四五六七八九十百千万亿零〇0-9]+[）)]'
    r'|[一二三四五六七八九十百千万亿零〇]+[、．.]'
    r'|第[一二三四五六七八九十百千万亿零〇0-9]+[章节部分]'
    r'|\d+[、．.]'
    r')'
)


def has_own_number(text: str) -> bool:
    """Return whether a heading already contains a visible number prefix."""
    return bool(_OWN_NUMBER.match(text or ''))


def _chinese_ordinal(number: int) -> str:
    """Render the small ordinal range used by tender section headings."""
    digits = '零一二三四五六七八九'
    if number < 10:
        return digits[number]
    if number < 20:
        return '十' + (digits[number - 10] if number > 10 else '')
    if number < 100:
        tens, units = divmod(number, 10)
        return digits[tens] + '十' + (digits[units] if units else '')
    hundreds, remainder = divmod(number, 100)
    if remainder == 0:
        return digits[hundreds] + '百'
    return digits[hundreds] + '百零' + _chinese_ordinal(remainder)


def _paragraphs(document):
    return [Paragraph(node, document) for node in document.element.body.iter(qn('w:p'))]


def _cover_end(paragraphs):
    # A document without a TOC has no positively identified cover region.
    return next((i for i, p in enumerate(paragraphs) if ''.join(p.text.split()) == '目录'), 0)


def _style_points(style):
    seen = set()
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id)
        if style.font.size is not None:
            return style.font.size.pt
        style = style.base_style
    return None


def resolve_heading_points(document) -> dict:
    result, sources = {}, []
    for level, names, fallback, low, high in (
        ('level1', ('Heading 5', 'Heading 6'), 16.0, 14.0, 22.0),
        ('level2', ('Heading 7', 'Heading 8'), 14.0, 12.0, 18.0),
    ):
        points, font, source = fallback, '宋体', 'fallback'
        for name in names:
            try:
                style = document.styles[name]
            except KeyError:
                continue
            if style.font.size is None:
                continue
            candidate = style.font.size.pt
            if candidate >= 30:
                break
            points, source = min(high, max(low, candidate)), name
            fonts = style.element.find('w:rPr/w:rFonts', style.element.nsmap)
            candidate_font = fonts.get(qn('w:eastAsia')) if fonts is not None else ''
            compact_font = ''.join(candidate_font.split()).casefold() if candidate_font else ''
            if (candidate_font and 'microsoftjhenghei' not in compact_font
                    and '微软正黑' not in candidate_font):
                font = candidate_font
            break
        result[level] = (float(points), font)
        sources.append(source)
    result['source'] = '/'.join(sources) if sources != ['fallback', 'fallback'] else 'fallback'
    return result


def apply_heading_styles(document, points) -> dict:
    paragraphs = _paragraphs(document)
    end = _cover_end(paragraphs)
    previous = {name: _style_points(document.styles[name]) for name in _HEADINGS}
    cover_changed = set()
    # Preserve inherited cover sizes in a separate paragraph style, never on runs.
    for paragraph in paragraphs[:end]:
        name = paragraph.style.name
        if name not in _HEADINGS:
            continue
        cover_name = _COVER_PREFIX + name
        try:
            cover_style = document.styles[cover_name]
        except KeyError:
            element = deepcopy(paragraph.style.element)
            element.set(qn('w:styleId'), cover_name.replace(' ', ''))
            element.find(qn('w:name')).set(qn('w:val'), cover_name)
            document.styles.element.append(element)
            cover_style = document.styles[cover_name]
            if cover_style.font.size is None and previous[name] is not None:
                cover_style.font.size = Pt(previous[name])
        paragraph.style = cover_style
        cover_changed.add(paragraph._p)
    document.element._heading_previous_points = previous
    document.element._heading_cover_changed = cover_changed
    applied = {}
    for name, level in _HEADINGS.items():
        size, font = points[level]
        style = document.styles[name]
        style.font.size = Pt(size)
        style.font.name = font
        props = style.element.get_or_add_rPr()
        fonts = props.find(qn('w:rFonts'))
        fonts.set(qn('w:eastAsia'), font)
        fonts.set(qn('w:ascii'), font)
        for attribute in ('eastAsiaTheme', 'asciiTheme', 'hAnsiTheme'):
            fonts.attrib.pop(qn('w:' + attribute), None)
        complex_size = props.find(qn('w:szCs'))
        if complex_size is None:
            complex_size = OxmlElement('w:szCs')
            props.append(complex_size)
        complex_size.set(qn('w:val'), str(round(size * 2)))
        applied[name] = {'size': float(size), 'font': font}
    return {'applied': applied}


def normalize_heading_paragraphs(document, points) -> dict:
    paragraphs = _paragraphs(document)
    end = _cover_end(paragraphs)
    previous = getattr(document.element, '_heading_previous_points', {})
    cover_changed = getattr(document.element, '_heading_cover_changed', set())
    changes = {}
    numbering_counts = {}
    numbering_groups = {}
    for paragraph in paragraphs:
        if paragraph.style.name not in _NUMBERED_HEADINGS:
            continue
        props = paragraph._p.pPr
        numbering = props.find(qn('w:numPr')) if props is not None else None
        if numbering is None:
            continue
        num_id_node = numbering.find(qn('w:numId'))
        num_id = num_id_node.get(qn('w:val')) if num_id_node is not None else None
        if num_id is not None and num_id not in numbering_groups:
            numbering_groups[num_id] = has_own_number(paragraph.text)

    def resolved_heading9_prefix(numbering, num_id, n):
        root = document.part.numbering_part.element
        num = root.find(f'w:num[@w:numId="{num_id}"]', root.nsmap)
        if num is None:
            return ''
        abstract_id = num.find(qn('w:abstractNumId'))
        if abstract_id is None:
            return ''
        abstract = root.find(
            f'w:abstractNum[@w:abstractNumId="{abstract_id.get(qn("w:val"))}"]',
            root.nsmap,
        )
        if abstract is None:
            return ''
        ilvl = numbering.find(qn('w:ilvl'))
        level = ilvl.get(qn('w:val')) if ilvl is not None else '0'
        level_node = abstract.find(f'w:lvl[@w:ilvl="{level}"]', root.nsmap)
        if level_node is None:
            return ''
        fmt_node = level_node.find(qn('w:numFmt'))
        text_node = level_node.find(qn('w:lvlText'))
        pattern = text_node.get(qn('w:val')) if text_node is not None else ''
        if not pattern or '%1' not in pattern:
            return ''
        number_format = fmt_node.get(qn('w:val')) if fmt_node is not None else 'decimal'
        if number_format in {'chineseCounting', 'chineseCountingThousand'}:
            value = _chinese_ordinal(n)
        elif number_format in {'decimal', 'decimalZero'}:
            value = str(n)
        else:
            return ''
        shown = pattern.replace('%1', value)
        return shown if shown != value else f'{shown} '

    def remove_numbering(paragraph, props, *, name, index):
        numbering = props.find(qn('w:numPr')) if props is not None else None
        if numbering is None:
            return None, []
        num_id_node = numbering.find(qn('w:numId'))
        num_id = num_id_node.get(qn('w:val')) if num_id_node is not None else None
        if num_id is None:
            props.remove(numbering)
            return None, ['numPr']
        numbering_counts[num_id] = numbering_counts.get(num_id, 0) + 1
        n = numbering_counts[num_id]
        own_number = has_own_number(paragraph.text)
        group_own_number = numbering_groups.get(num_id, own_number)
        prefix = (
            resolved_heading9_prefix(numbering, num_id, n)
            if name == 'Heading 9'
            else f'（{_chinese_ordinal(n)}）'
        )
        record = {
            'index': index,
            'numId': num_id,
            'n': n,
            'prefix': prefix,
            'ownNumber': own_number,
            'groupOwnNumber': group_own_number,
        }
        if prefix and not group_own_number and not own_number:
            runs = paragraph.runs
            if runs:
                runs[0].text = prefix + runs[0].text
            else:
                paragraph.add_run(prefix)
        props.remove(numbering)
        return record, ['numPr']

    for index, paragraph in enumerate(paragraphs):
        name = paragraph.style.name
        if name not in _NUMBERED_HEADINGS and not name.startswith(_COVER_PREFIX):
            continue
        props = paragraph._p.pPr
        if index < end:
            if name == 'Heading 9':
                continue
            changed = paragraph._p in cover_changed
            if props is None:
                props = paragraph._p.get_or_add_pPr()
            if props.find(qn('w:jc')) is None:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                changed = True
            if changed:
                size = _style_points(paragraph.style)
                changes[index] = dict(index=index, text=paragraph.text[:24], from_size=size,
                                      to_size=size, removed=[], cover=True)
            continue
        if name == 'Heading 9':
            numbering_record, removed = remove_numbering(
                paragraph, props, name=name, index=index,
            )
            if numbering_record is not None:
                changes[index] = {
                    'index': index,
                    'text': paragraph.text[:24],
                    'from_size': None,
                    'to_size': None,
                    'removed': removed,
                    'numbering': numbering_record,
                    **numbering_record,
                }
            continue
        if name not in _HEADINGS:
            continue
        if props is None:
            props = paragraph._p.get_or_add_pPr()
        size = float(points[_HEADINGS[name]][0])
        from_sizes, removed = [], []
        numbering_record, numbering_removed = remove_numbering(
            paragraph, props, name=name, index=index,
        )
        removed.extend(numbering_removed)
        for element in paragraph._p.iter(qn('w:r')):
            if next(element.iterancestors(qn('w:p')), None) is not paragraph._p:
                continue
            run = Run(element, paragraph)
            if run.text.strip():
                from_sizes.append(run.font.size.pt if run.font.size is not None
                                  else previous.get(name, _style_points(paragraph.style)))
            rprops = element.find(qn('w:rPr'))
            if rprops is not None:
                for tag in ('sz', 'szCs', 'spacing'):
                    for node in list(rprops.findall(qn('w:' + tag))):
                        rprops.remove(node)
                        if tag not in removed:
                            removed.append(tag)
        for tag in ('tabs', 'ind'):
            for node in list(props.findall(qn('w:' + tag))):
                props.remove(node)
                removed.append(tag)
        alignment_changed = paragraph.alignment != WD_ALIGN_PARAGRAPH.CENTER
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        sizes = list(dict.fromkeys(from_sizes))
        if removed or alignment_changed or any(old != size for old in sizes):
            changes[index] = dict(index=index, text=paragraph.text[:24],
                                  from_size=sizes[0] if len(sizes) == 1 else sizes,
                                  to_size=size, removed=removed)
        if numbering_record is not None:
            changes.setdefault(index, dict(index=index, text=paragraph.text[:24],
                                           from_size=sizes[0] if len(sizes) == 1 else sizes,
                                           to_size=size, removed=removed))
            changes[index]['numbering'] = numbering_record
            if numbering_record is not None:
                changes[index].update(numbering_record)
    document.element._heading_previous_points = {}
    document.element._heading_cover_changed = set()
    return changes
