"""把章节树渲染为标准 Word（.docx / OOXML）文件。

使用 Word 内置标题样式（Heading 1~5），目录、导航窗格、页码均可在 Microsoft Word 中正常使用。
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_COLOR_INDEX, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from api.services.bidgen.document_model import PENDING_PREFIX, ComposeContext, Section

FONT_BODY = "宋体"
FONT_HEADING = "黑体"
FONT_LATIN = "Times New Roman"
BODY_SIZE = 12  # 小四
TABLE_SIZE = 10.5  # 五号
PAGE_WIDTH_CM = 21.0
PAGE_MARGIN_CM = 2.6
CONTENT_WIDTH_CM = PAGE_WIDTH_CM - 2 * PAGE_MARGIN_CM
HEADING_SIZES = {1: 22, 2: 16, 3: 15, 4: 14, 5: 12}
MAX_IMAGE_WIDTH_CM = 14.0
MAX_IMAGE_HEIGHT_CM = 19.0
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".gif")

_PENDING_SPLIT = re.compile(rf"({re.escape(PENDING_PREFIX)}[^】]*】)")
_ALIGNMENTS = {"center": WD_ALIGN_PARAGRAPH.CENTER, "right": WD_ALIGN_PARAGRAPH.RIGHT, "left": WD_ALIGN_PARAGRAPH.LEFT}

_APP_PROPERTIES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
    'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
    "<Template>Normal.dotm</Template><Application>Microsoft Office Word</Application>"
    "<DocSecurity>0</DocSecurity><ScaleCrop>false</ScaleCrop><SharedDoc>false</SharedDoc>"
    "<HyperlinksChanged>false</HyperlinksChanged><AppVersion>16.0000</AppVersion></Properties>"
)


def _set_fonts(run_or_style, east_asia: str, size: float | None = None, bold: bool | None = None) -> None:
    font = run_or_style.font
    font.name = FONT_LATIN
    properties = run_or_style.element.get_or_add_rPr()
    fonts = properties.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        properties.append(fonts)
    fonts.set(qn("w:eastAsia"), east_asia)
    fonts.set(qn("w:ascii"), FONT_LATIN)
    fonts.set(qn("w:hAnsi"), FONT_LATIN)
    for attribute in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        if fonts.get(qn(attribute)) is not None:
            del fonts.attrib[qn(attribute)]
    if size is not None:
        font.size = Pt(size)
    if bold is not None:
        font.bold = bold
    font.color.rgb = RGBColor(0, 0, 0)


def _configure_styles(document: Document) -> None:
    normal = document.styles["Normal"]
    _set_fonts(normal, FONT_BODY, BODY_SIZE)
    normal.paragraph_format.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
    normal.paragraph_format.space_after = Pt(0)
    for level, size in HEADING_SIZES.items():
        style = document.styles[f"Heading {level}"]
        _set_fonts(style, FONT_HEADING if level <= 3 else FONT_BODY, size, True)
        style.font.italic = False
        paragraph_format = style.paragraph_format
        paragraph_format.keep_with_next = True
        paragraph_format.space_before = Pt(18 if level <= 2 else 10)
        paragraph_format.space_after = Pt(12 if level <= 2 else 6)
        paragraph_format.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
        paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER if level == 1 else WD_ALIGN_PARAGRAPH.LEFT
    for name in ("TOC 1", "TOC 2", "TOC 3"):
        try:
            _set_fonts(document.styles[name], FONT_BODY, BODY_SIZE)
        except KeyError:
            continue


def _configure_page(section) -> None:
    section.page_width, section.page_height = Cm(PAGE_WIDTH_CM), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2.54)
    section.left_margin = section.right_margin = Cm(PAGE_MARGIN_CM)
    section.header_distance, section.footer_distance = Cm(1.5), Cm(1.5)


def _configure_settings(document: Document) -> None:
    """按 Word 2013+ 文档模式保存，并让 Word 打开时自动更新目录页码。"""
    settings = document.settings.element
    update = OxmlElement("w:updateFields")
    update.set(qn("w:val"), "true")
    settings.append(update)
    compat = settings.find(qn("w:compat"))
    if compat is None:
        compat = OxmlElement("w:compat")
        settings.append(compat)
    for item in compat.findall(qn("w:compatSetting")):
        compat.remove(item)
    mode = OxmlElement("w:compatSetting")
    mode.set(qn("w:name"), "compatibilityMode")
    mode.set(qn("w:uri"), "http://schemas.microsoft.com/office/word")
    mode.set(qn("w:val"), "15")
    compat.append(mode)


def _add_field(paragraph, instruction: str, cached: str = "") -> None:
    """插入域代码（PAGE、TOC 等）；``cached`` 为未更新前显示的内容。"""
    for kind in ("begin", "separate", "end"):
        if kind == "separate":
            code = paragraph.add_run()
            text = OxmlElement("w:instrText")
            text.set(qn("xml:space"), "preserve")
            text.text = instruction
            code._r.append(text)
        run = paragraph.add_run()
        mark = OxmlElement("w:fldChar")
        mark.set(qn("w:fldCharType"), kind)
        run._r.append(mark)
        if kind == "separate" and cached:
            paragraph.add_run(cached)


def _add_text(paragraph, text: str, *, bold: bool = False, size: float | None = None, east_asia: str = FONT_BODY) -> None:
    """写入文字；“【待填：…】”黄色高亮，便于定稿前逐个处理。"""
    for part in _PENDING_SPLIT.split(text):
        if not part:
            continue
        lines = part.split("\n")
        for index, line in enumerate(lines):
            run = paragraph.add_run(line)
            _set_fonts(run, east_asia, size, bold or None)
            if part.startswith(PENDING_PREFIX):
                run.font.highlight_color = WD_COLOR_INDEX.YELLOW
            if index < len(lines) - 1:
                run.add_break(WD_BREAK.LINE)


def _add_cover(document: Document, ctx: ComposeContext, volume_label: str) -> None:
    def line(text: str, size: float, *, bold: bool = True, before: float = 0, east_asia: str = FONT_HEADING) -> None:
        paragraph = document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.space_before = Pt(before)
        _add_text(paragraph, text, bold=bold, size=size, east_asia=east_asia)

    line(ctx.project_name, 18, before=48)
    line("投  标  文  件", 36, before=60)
    line(f"（{volume_label}）", 18, before=12)
    legal = ctx.profile["legalRep"]["name"]
    rows = [
        ("招 标 编 号", ctx.tender_no),
        ("所 投 分 标", ctx.lot_names),
        ("分 标 编 号", ctx.lot_codes),
        ("包　　　号", ctx.package_no),
        ("投　标　人", f"{ctx.company_name}（盖单位章）"),
        ("法定代表人或委托代理人", f"{legal or ''}（签字）＿＿＿＿＿＿＿＿" if legal else "（签字）＿＿＿＿＿＿＿＿"),
        ("投 标 日 期", ctx.sign_date),
    ]
    spacer = document.add_paragraph()
    spacer.paragraph_format.space_before = Pt(70)
    cover = document.add_table(rows=len(rows), cols=2)
    cover.alignment = WD_TABLE_ALIGNMENT.CENTER
    for row, (label, value) in zip(cover.rows, rows, strict=True):
        row.cells[0].width, row.cells[1].width = Cm(5.2), Cm(10.4)
        _add_text(row.cells[0].paragraphs[0], f"{label}：", bold=True, size=14)
        row.cells[0].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.DISTRIBUTE
        _add_text(row.cells[1].paragraphs[0], value, size=14)
        for cell in row.cells:
            cell.paragraphs[0].paragraph_format.space_before = Pt(6)
            cell.paragraphs[0].paragraph_format.space_after = Pt(6)


def _add_table_of_contents(document: Document, roots: list[Section]) -> None:
    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_after = Pt(12)
    _add_text(title, "目    录", bold=True, size=18, east_asia=FONT_HEADING)
    entries = [node for root in roots for node in root.walk() if node.level <= 3]
    holder = document.add_paragraph()
    run = holder.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    begin.set(qn("w:dirty"), "true")
    run._r.append(begin)
    code = holder.add_run()
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = 'TOC \\o "1-3" \\h \\z \\u'
    code._r.append(instruction)
    separate = holder.add_run()
    mark = OxmlElement("w:fldChar")
    mark.set(qn("w:fldCharType"), "separate")
    separate._r.append(mark)
    # 域结果先写入静态目录，Word 打开后更新域即带出页码
    current = holder
    for index, node in enumerate(entries):
        if index:
            current = document.add_paragraph()
        current.paragraph_format.left_indent = Cm(0.74 * (node.level - 1))
        current.paragraph_format.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
        _add_text(current, node.heading, bold=node.level == 1, size=BODY_SIZE)
    end = current.add_run()
    mark = OxmlElement("w:fldChar")
    mark.set(qn("w:fldCharType"), "end")
    end._r.append(mark)


def _add_header_footer(section, ctx: ComposeContext, volume_label: str) -> None:
    section.different_first_page_header_footer = True
    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _add_text(header, f"{ctx.company_name}　{ctx.tender_no} 投标文件（{volume_label}）", size=9)
    border = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for key, value in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", "000000")):
        bottom.set(qn(key), value)
    border.append(bottom)
    header._p.get_or_add_pPr().append(border)
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _add_text(footer, "第 ", size=9)
    _add_field(footer, "PAGE", "1")
    _add_text(footer, " 页", size=9)


def _shade(cell, color: str) -> None:
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), color)
    cell._tc.get_or_add_tcPr().append(shading)


def _repeat_header(row) -> None:
    marker = OxmlElement("w:tblHeader")
    marker.set(qn("w:val"), "true")
    row._tr.get_or_add_trPr().append(marker)


def _keep_row_together(row) -> None:
    marker = OxmlElement("w:cantSplit")
    marker.set(qn("w:val"), "true")
    row._tr.get_or_add_trPr().append(marker)


def _column_widths(block: dict, columns: int) -> list[float]:
    widths = [float(w) for w in block.get("widths") or []]
    if len(widths) != columns:
        widths = [1.0] * columns
        for row in [block["header"], *block["rows"][:30]]:
            for index, cell in enumerate(row[:columns]):
                widths[index] = max(widths[index], min(len(str(cell)), 28) ** 0.6)
    total = sum(widths)
    return [CONTENT_WIDTH_CM * width / total for width in widths]


def _write_cell(cell, text: str, *, bold: bool = False, center: bool = False) -> None:
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
    paragraph.paragraph_format.space_before = paragraph.paragraph_format.space_after = Pt(2)
    if center:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _add_text(paragraph, str(text), bold=bold, size=TABLE_SIZE)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def _add_table(document: Document, block: dict, counter: list[int]) -> None:
    header, rows = block["header"], block["rows"]
    columns = max([len(header), *(len(row) for row in rows)] or [1])
    if block.get("caption"):
        counter[0] += 1
        caption = document.add_paragraph()
        caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
        caption.paragraph_format.keep_with_next = True
        caption.paragraph_format.space_before = Pt(6)
        _add_text(caption, f"表 {counter[0]}  {block['caption']}", bold=True, size=TABLE_SIZE)
    word_table = document.add_table(rows=(1 if header else 0) + len(rows), cols=columns)
    word_table.style = "Table Grid"
    word_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    word_table.autofit = False
    widths = _column_widths(block, columns)
    offset = 0
    if header:
        for index in range(columns):
            cell = word_table.rows[0].cells[index]
            _write_cell(cell, header[index] if index < len(header) else "", bold=True, center=True)
            _shade(cell, "D9D9D9")
        _repeat_header(word_table.rows[0])
        offset = 1
    is_form = bool(block.get("form"))
    for row_index, row in enumerate(rows):
        word_row = word_table.rows[row_index + offset]
        _keep_row_together(word_row)
        cells = list(row) + [""] * (columns - len(row))
        for index, text in enumerate(cells):
            label = is_form and index % 2 == 0 and bool(text)
            _write_cell(word_row.cells[index], text, bold=label, center=(index == 0 and not is_form) or len(str(text)) <= 6)
            if label:
                _shade(word_row.cells[index], "F2F2F2")
        # 表单行尾部为空的单元格并入前一格（如“经营范围”整行）
        if is_form and len(cells) >= 4 and cells[1] and not any(cells[2:]):
            merged = word_row.cells[1].merge(word_row.cells[columns - 1])
            merged.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.LEFT
    for row in word_table.rows:
        for index, width in enumerate(widths):
            if index < len(row.cells):
                row.cells[index].width = Cm(width)
    spacer = document.add_paragraph()
    spacer.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE


def _add_placeholder(document: Document, block: dict) -> None:
    """扫描件粘贴处：虚线框。"""
    frame = document.add_table(rows=1, cols=1)
    frame.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = frame.rows[0].cells[0]
    cell.width = Cm(CONTENT_WIDTH_CM)
    borders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        element = OxmlElement(f"w:{edge}")
        for key, value in (("w:val", "dashed"), ("w:sz", "8"), ("w:space", "0"), ("w:color", "7F7F7F")):
            element.set(qn(key), value)
        borders.append(element)
    cell._tc.get_or_add_tcPr().append(borders)
    height = OxmlElement("w:trHeight")
    height.set(qn("w:val"), str(int(float(block.get("height", 6)) * 567)))
    height.set(qn("w:hRule"), "atLeast")
    frame.rows[0]._tr.get_or_add_trPr().append(height)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    paragraph = cell.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run(block["text"])
    _set_fonts(run, "楷体", 11)
    run.font.color.rgb = RGBColor(0x59, 0x59, 0x59)
    document.add_paragraph().paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE


def _add_image(document: Document, block: dict) -> None:
    path = Path(block["path"])
    caption = block.get("caption", "")
    if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
        _add_placeholder(document, {"text": f"（{caption}：原件为 {path.suffix.upper().lstrip('.') or '非图片'} 文件，请在此处粘贴对应页面）", "height": 6})
        return
    try:
        from PIL import Image

        with Image.open(path) as picture:
            ratio = picture.height / max(picture.width, 1)
    except Exception:  # noqa: BLE001 - 图片损坏时退回占位框，不中断整本生成
        _add_placeholder(document, {"text": f"（{caption}：图片无法读取，请重新上传或在此处粘贴）", "height": 6})
        return
    width = min(MAX_IMAGE_WIDTH_CM, MAX_IMAGE_HEIGHT_CM / max(ratio, 0.01))
    holder = document.add_paragraph()
    holder.alignment = WD_ALIGN_PARAGRAPH.CENTER
    holder.add_run().add_picture(str(path), width=Cm(width))
    if caption:
        label = document.add_paragraph()
        label.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _add_text(label, caption, size=TABLE_SIZE)


def _add_blocks(document: Document, blocks: list[dict], counter: list[int]) -> None:
    for block in blocks:
        kind = block["type"]
        if kind == "paragraph":
            paragraph = document.add_paragraph()
            if block.get("align") in _ALIGNMENTS:
                paragraph.alignment = _ALIGNMENTS[block["align"]]
            elif block.get("indent", True):
                paragraph.paragraph_format.first_line_indent = Pt(BODY_SIZE * 2)
                paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            if block.get("size"):
                paragraph.paragraph_format.space_before = paragraph.paragraph_format.space_after = Pt(8)
            _add_text(paragraph, block["text"], bold=bool(block.get("bold")), size=block.get("size"))
        elif kind == "table":
            _add_table(document, block, counter)
        elif kind == "placeholder":
            _add_placeholder(document, block)
        elif kind == "image":
            _add_image(document, block)
        elif kind == "signature":
            for index, line in enumerate(block["lines"]):
                paragraph = document.add_paragraph()
                paragraph.paragraph_format.left_indent = Cm(6.2)
                paragraph.paragraph_format.space_before = Pt(18 if index == 0 else 6)
                paragraph.paragraph_format.keep_together = True
                _add_text(paragraph, line)


def _add_section(document: Document, section: Section, counter: list[int], first: bool) -> None:
    if section.page_break and not first:
        document.add_page_break()
    heading = document.add_heading(level=min(section.level, 5))
    _add_text(heading, section.heading, bold=True, size=HEADING_SIZES[min(section.level, 5)], east_asia=FONT_HEADING if section.level <= 3 else FONT_BODY)
    _add_blocks(document, section.blocks, counter)
    for child in section.children:
        _add_section(document, child, counter, first=False)


def _stamp_word_properties(path: Path) -> None:
    """重写 docProps/app.xml，声明为 Microsoft Office Word 文档。"""
    temporary = path.with_suffix(".tmp")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = _APP_PROPERTIES.encode("utf-8") if item.filename == "docProps/app.xml" else source.read(item.filename)
            target.writestr(item, data)
    temporary.replace(path)


def render_document(roots: list[Section], ctx: ComposeContext, path: Path, volume_label: str) -> Path:
    """渲染一本 Word 文件。``roots`` 为要输出的一级章节。"""
    document = Document()
    _configure_styles(document)
    _configure_settings(document)
    _configure_page(document.sections[0])
    _add_cover(document, ctx, volume_label)
    body = document.add_section(WD_SECTION.NEW_PAGE)
    _configure_page(body)
    body.header.is_linked_to_previous = body.footer.is_linked_to_previous = False
    _add_header_footer(body, ctx, volume_label)
    body.different_first_page_header_footer = False
    page_number = OxmlElement("w:pgNumType")
    page_number.set(qn("w:start"), "1")
    body._sectPr.append(page_number)
    _add_table_of_contents(document, roots)
    counter = [0]
    for root in roots:
        document.add_page_break()
        _add_section(document, root, counter, first=True)
    properties = document.core_properties
    properties.author = properties.last_modified_by = ctx.company["name"] or "投标人"
    properties.title = f"{ctx.tender_no} 投标文件（{volume_label}）"
    properties.subject = ctx.tender.get("project_name", "")
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(path))
    _stamp_word_properties(path)
    return path
