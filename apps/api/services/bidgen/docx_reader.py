"""DOCX 结构化读取：只依赖标准库，按正文顺序输出段落 / 表格块。"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from api.services.bidgen.document_model import UNDERLINE_END, UNDERLINE_START

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING_STYLE = re.compile(r"(?i)^(?:heading|标题)\s*(\d)$")


@dataclass
class DocBlock:
    """正文中的一个块：段落（kind=paragraph）或表格（kind=table）。"""

    kind: str
    text: str = ""
    style: str = ""
    level: int = 0  # 0 = 普通正文；1..9 = 标题层级
    rows: list[list[str]] = field(default_factory=list)
    form: str = ""  # 段落含下划线时：带横线标记的原文（见 document_model.UNDERLINE_START）；否则为空


def _run_underlined(run: ElementTree.Element) -> bool:
    line = run.find(f"{_W}rPr/{_W}u")
    return line is not None and line.get(f"{_W}val", "single") not in ("none", "0", "false", "off")


def _paragraph_pieces(paragraph: ElementTree.Element) -> list[tuple[str, bool]]:
    """段落文字片段及其是否带下划线（填空横线）。"""
    pieces: list[tuple[str, bool]] = []
    underlined = False
    for node in paragraph.iter():
        if node.tag == f"{_W}r":
            underlined = _run_underlined(node)
        elif node.tag == f"{_W}t":
            pieces.append((node.text or "", underlined))
        elif node.tag == f"{_W}tab":
            pieces.append((" ", underlined))
        elif node.tag in (f"{_W}br", f"{_W}cr"):
            pieces.append(("\n", False))
    return pieces


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    return "".join(text for text, _ in _paragraph_pieces(paragraph))


def _paragraph_form(paragraph: ElementTree.Element) -> str:
    """带横线标记的段落原文；整段没有下划线时返回空串。"""
    pieces = _paragraph_pieces(paragraph)
    if not any(underlined and text for text, underlined in pieces):
        return ""
    return "".join(f"{UNDERLINE_START}{text}{UNDERLINE_END}" if underlined else text for text, underlined in pieces).strip()


def _read_style_names(package: zipfile.ZipFile) -> tuple[dict[str, str], dict[str, int]]:
    """返回 样式ID→样式名、样式ID→大纲级别（1 起）。"""
    names: dict[str, str] = {}
    levels: dict[str, int] = {}
    try:
        root = ElementTree.fromstring(package.read("word/styles.xml"))
    except KeyError:
        return names, levels
    for style in root.iter(f"{_W}style"):
        style_id = style.get(f"{_W}styleId") or ""
        name_node = style.find(f"{_W}name")
        name = name_node.get(f"{_W}val", "") if name_node is not None else ""
        names[style_id] = name
        matched = _HEADING_STYLE.match(name)
        if matched:
            levels[style_id] = int(matched.group(1))
            continue
        outline = style.find(f"{_W}pPr/{_W}outlineLvl")
        if outline is not None:
            value = int(outline.get(f"{_W}val", "9"))
            if value < 9:
                levels[style_id] = value + 1
    return names, levels


def _cell_text(cell: ElementTree.Element) -> str:
    lines = [_paragraph_text(p).strip() for p in cell.iter(f"{_W}p")]
    return " ".join(line for line in lines if line)


def _table_rows(table: ElementTree.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.findall(f"{_W}tr"):
        cells = [_cell_text(cell) for cell in row.findall(f"{_W}tc")]
        if cells:
            rows.append(cells)
    return rows


def read_docx_blocks(path: Path) -> list[DocBlock]:
    """读取 DOCX 正文。损坏文件抛 ``ValueError``。"""
    try:
        with zipfile.ZipFile(path) as package:
            style_names, style_levels = _read_style_names(package)
            body = ElementTree.fromstring(package.read("word/document.xml")).find(f"{_W}body")
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise ValueError(f"无法读取 Word 文件：{path.name}") from exc
    blocks: list[DocBlock] = []
    if body is None:
        return blocks
    for element in body:
        if element.tag == f"{_W}p":
            text = _paragraph_text(element).strip()
            if not text:
                continue
            style_node = element.find(f"{_W}pPr/{_W}pStyle")
            style_id = style_node.get(f"{_W}val", "") if style_node is not None else ""
            level = style_levels.get(style_id, 0)
            outline = element.find(f"{_W}pPr/{_W}outlineLvl")
            if outline is not None:
                value = int(outline.get(f"{_W}val", "9"))
                level = value + 1 if value < 9 else 0
            blocks.append(
                DocBlock(kind="paragraph", text=text, style=style_names.get(style_id, style_id), level=level,
                         form=_paragraph_form(element))
            )
        elif element.tag == f"{_W}tbl":
            rows = _table_rows(element)
            if rows:
                blocks.append(DocBlock(kind="table", rows=rows))
    return blocks


def template_paragraph(block: DocBlock) -> dict:
    """格式原文段落 → 模板项；源稿有填空横线时一并带上 form。"""
    item = {"kind": "paragraph", "text": block.text}
    if block.form:
        item["form"] = block.form
    return item


def blocks_to_text(blocks: list[DocBlock]) -> str:
    """把块压平成文本（表格行用 ``|`` 连接），供正则抽取使用。"""
    lines: list[str] = []
    for block in blocks:
        if block.kind == "paragraph":
            lines.append(block.text)
        else:
            lines.extend(" | ".join(row) for row in block.rows)
    return "\n".join(lines)
