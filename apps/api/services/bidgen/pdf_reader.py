"""PDF 招标文件读取：转成与 DOCX 相同的段落 / 表格块，后续解析流程不变。

- 文字型 PDF：PyMuPDF 取文字与字号；表格用 PyMuPDF 的表格识别（有框线的表格）；
- 标题层级：字号明显大于正文的行，或符合“第X章 / 一、 / （一） / 1.1”编号格式的短行；
- 跨行折断的句子按标点与编号合并为段落；去掉每页重复出现的页眉页脚与页码；
- 扫描件（整页无文字）：渲染为图片后用本地 OCR 识别；识别器能给出文字坐标时按框线还原表格（见 pdf_scan），
  跨页的表格并成一张，重复扫描的页只留一份。
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from api.services.bidgen.docx_reader import DocBlock
from api.services.bidgen.pdf_scan import continue_table, read_scanned_page

logger = logging.getLogger(__name__)
MAX_OCR_PAGES = 300
_CHAPTER = re.compile(r"^第[一二三四五六七八九十百\d]+[章部分篇]")
_LEVEL2 = re.compile(r"^[一二三四五六七八九十]+[、．.]\s*\S")
_LEVEL3 = re.compile(r"^[（(][一二三四五六七八九十]+[）)]\s*\S")
_NUMBERED = re.compile(r"^\d+(?:\.\d+){1,3}\s*\S")
_TOC_LINE = re.compile(r"(?:\s|…|\.{2,}|·{2,})\d{1,4}$")
_SENTENCE_END = "。；！？：:;!?"
_HEADING_TAIL = re.compile(r"^(.*[。；])\s*((?:第[一二三四五六七八九十]+章|[一二三四五六七八九十]+、|[（(][一二三四五六七八九十]+[）)])[^。；，]{1,28})$")
_PAGE_NUMBER = re.compile(r"^(?:第\s*\d+\s*页(?:\s*共\s*\d+\s*页)?|-?\s*\d{1,4}\s*-?|\d+\s*/\s*\d+)$")


def _heading_level(text: str, size: float, body_size: float, bold: bool) -> int:
    if len(text) > 40 or text[-1:] in "。；，,;":
        return 0
    if _TOC_LINE.search(text):
        return 0  # 目录行（标题后带页码）
    bigger = size >= body_size + 1.5
    if _CHAPTER.match(text):
        return 1
    if _LEVEL2.match(text) and (bigger or bold or len(text) <= 30):
        return 2
    if _LEVEL3.match(text) and (bigger or bold or len(text) <= 24):
        return 3
    if _NUMBERED.match(text) and (bigger or bold):
        return 3 + text.split()[0].count(".") - 1 if " " in text else 3
    if bigger and len(text) <= 30:
        return 2
    return 0


def _ocr_page(page) -> list[str]:
    from api.services.knowledge.ocr.factory import get_ocr_provider

    pixmap = page.get_pixmap(dpi=200)
    text = get_ocr_provider().recognize(pixmap.tobytes("png"))
    return [line.strip() for line in text.splitlines() if line.strip()]


def read_pdf_blocks(path: Path) -> list[DocBlock]:
    """读取 PDF 招标文件。打不开时抛 ``ValueError``。"""
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise ValueError("服务器缺少 PDF 解析组件（PyMuPDF），无法读取 PDF 招标文件") from exc
    try:
        document = fitz.open(str(path))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"无法读取 PDF 文件：{path.name}") from exc

    pages: list[list[tuple[str, float, bool, float]]] = []  # (文字, 字号, 加粗, 纵坐标)
    page_tables: list[list[tuple[float, list[list[str]]]]] = []
    sizes: Counter = Counter()
    ocr_pages = 0
    scanned_pages: set[int] = set()  # 按版面识别的扫描页（页序号）
    previous_scan = ""
    for page in document:
        lines: list[tuple[str, float, bool, float]] = []
        tables: list[tuple[float, list[list[str]]]] = []
        areas = []
        try:
            for found in page.find_tables().tables:
                rows = [[re.sub(r"\s+", " ", cell or "").strip() for cell in row] for row in found.extract()]
                rows = [row for row in rows if any(row)]
                if len(rows) >= 2:
                    tables.append((found.bbox[1], rows))
                    areas.append(fitz.Rect(found.bbox))
        except Exception:  # noqa: BLE001 - 表格识别失败时按纯文本处理
            logger.debug("pdf table detection failed page=%s", page.number)
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = [span for span in line["spans"] if span["text"].strip()]
                if not spans:
                    continue
                rect = fitz.Rect(line["bbox"])
                if any(area.intersects(rect) for area in areas):
                    continue
                text = "".join(span["text"] for span in spans).strip()
                size = max(span["size"] for span in spans)
                bold = any(span["flags"] & 16 or "Bold" in span["font"] for span in spans)
                # 上一句的结尾与下一节标题挤在同一行（“……效力。 三、报价表”）：拆成两行
                tail = _HEADING_TAIL.match(text)
                if tail:
                    lines.append((tail.group(1), size, False, rect.y0))
                    lines.append((tail.group(2), size, bold, rect.y0 + 0.1))
                else:
                    lines.append((text, size, bold, rect.y0))
                sizes[round(size, 1)] += len(text)
        if not lines and not tables and ocr_pages < MAX_OCR_PAGES:
            ocr_pages += 1
            try:
                scanned = read_scanned_page(page)
                if scanned is None:
                    lines = [(text, 0.0, False, float(index)) for index, text in enumerate(_ocr_page(page))]
                else:
                    lines = [(text, 0.0, False, y) for y, text in scanned[0]]
                    tables = scanned[1]
                    scan_text = "".join(text for text, *_ in lines) + "".join(cell for _y, rows in tables for row in rows for cell in row)
                    if previous_scan and SequenceMatcher(None, previous_scan, scan_text, autojunk=False).ratio() > 0.9:
                        lines, tables = [], []  # 同一页扫了两遍
                    else:
                        previous_scan = scan_text
                        scanned_pages.add(len(pages))
            except Exception:  # noqa: BLE001 - OCR 未启用或失败：该页留空并记录
                logger.warning("pdf ocr failed page=%s", page.number, exc_info=True)
        pages.append(lines)
        page_tables.append(tables)
    if not any(pages) and not any(page_tables):
        raise ValueError(f"PDF 中没有可识别的文字：{path.name}（扫描件请确认已启用 OCR）")

    # 页眉页脚：在半数以上页面重复出现的短行
    repeated = Counter(text for lines in pages for text, *_ in {line[0]: line for line in lines}.values() if len(text) <= 40)
    threshold = max(3, len(pages) // 2)
    noise = {text for text, count in repeated.items() if count >= threshold}
    body_size = sizes.most_common(1)[0][0] if sizes else 0.0

    # 折行只发生在满行：明显短于常见行长的行是段落结尾（封面字段、“目录”、短标签各自成段）
    lengths = sorted(len(text) for lines in pages for text, *_ in lines if len(text) > 10)
    full_line = lengths[int(len(lengths) * 0.75)] if lengths else 30
    blocks: list[DocBlock] = []
    buffer = ""

    def flush() -> None:
        nonlocal buffer
        if buffer.strip():
            blocks.append(DocBlock(kind="paragraph", text=buffer.strip()))
        buffer = ""

    table_page = -1  # 最近一张表格所在页
    for page_index, (lines, tables) in enumerate(zip(pages, page_tables)):
        events = [(y, "line", line) for line in lines for y in [line[3]]] + [(y, "table", rows) for y, rows in tables]
        events.sort(key=lambda event: (event[0], event[1] != "table"))
        for _y, kind, payload in events:
            if kind == "table":
                flush()
                # 扫描件中紧接上一页表格、中间没有正文的同列数表格，是同一张表的下半部分
                continued = (page_index in scanned_pages and table_page not in (-1, page_index) and blocks
                             and blocks[-1].kind == "table" and continue_table(blocks[-1].rows, payload))
                if not continued:
                    blocks.append(DocBlock(kind="table", rows=payload))
                table_page = page_index
                continue
            text, size, bold, _ = payload
            if text in noise or _PAGE_NUMBER.match(text):
                continue
            level = _heading_level(text, size or body_size, body_size, bold)
            if level:
                flush()
                blocks.append(DocBlock(kind="paragraph", text=text, level=level))
                continue
            starts_item = bool(re.match(r"^(?:\d+[.．、)）]|[（(]\d+[）)]|[①-⑳]|[一二三四五六七八九十]+[、．.]|[（(][一二三四五六七八九十]+[）)])", text))
            if buffer and (buffer[-1] in _SENTENCE_END or starts_item or _TOC_LINE.search(text)):
                flush()
            buffer += text
            if _TOC_LINE.search(text) or len(text) < full_line * 0.6:
                flush()
    flush()
    return blocks
