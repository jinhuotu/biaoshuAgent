"""扫描版 PDF 页面的版面识别：按文字位置还原行，按表格框线还原单元格。

只在本地 OCR（RapidOCR，能给出文字框坐标）可用时启用；其他识别器只返回纯文本，由调用方按原方式处理。
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)
SCAN_DPI = 200
_MIN_SCORE = 0.5
# 扫描 App 在页脚加的水印
_WATERMARK = re.compile(r"扫描全能王|都在用的扫描\s*App", re.I)
_SENTENCE_END = "。；！？.;!?"
Box = tuple[float, float, float, float, str]  # 左、上、右、下、文字
TableRows = list[list[str]]


def _engine():
    from api.services.knowledge.ocr.factory import get_ocr_provider

    return getattr(get_ocr_provider(), "_engine", None)


def _page_image(page):
    import numpy

    pixmap = page.get_pixmap(dpi=SCAN_DPI, alpha=False)
    image = numpy.frombuffer(pixmap.samples, dtype=numpy.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)[:, :, :3].copy()
    # 红色印章盖在文字上会被识别成乱字：识别前抹成白色，黑色文字不受影响
    red, green, blue = (image[:, :, channel].astype(int) for channel in range(3))
    image[(red > 140) & (red - green > 60) & (red - blue > 60)] = 255
    return image


def _text_boxes(engine, image) -> list[Box]:
    # RapidOCR 按 BGR 读取数组。页面都是正向的：方向分类器偶尔把整行误判成倒置而识别成乱字，这里不用
    result = engine(image[:, :, ::-1], use_cls=False)
    boxes, texts, scores = (getattr(result, name, None) for name in ("boxes", "txts", "scores"))
    if boxes is None or texts is None:
        return []
    found: list[Box] = []
    for index, (points, text) in enumerate(zip(boxes, texts)):
        text = str(text).strip()
        if not text or _WATERMARK.search(text) or (scores is not None and scores[index] < _MIN_SCORE):
            continue
        xs, ys = [float(point[0]) for point in points], [float(point[1]) for point in points]
        if max(ys) - min(ys) > (max(xs) - min(xs)) * 1.8:
            continue  # 竖长的框是页边印章残迹，正文都是横排
        found.append((min(xs), min(ys), max(xs), max(ys), text))
    return found


def _join_lines(boxes: list[Box]) -> list[tuple[float, str]]:
    """同一水平线上的文字框自左向右拼成一行，返回 (纵坐标, 文字)。"""
    lines: list[list[Box]] = []
    for box in sorted(boxes, key=lambda item: (item[1] + item[3]) / 2):
        center = (box[1] + box[3]) / 2
        last = lines[-1] if lines else None
        if last and abs(center - (last[0][1] + last[0][3]) / 2) < (last[0][3] - last[0][1]) * 0.6:
            last.append(box)
        else:
            lines.append([box])
    return [(line[0][1], "".join(box[4] for box in sorted(line, key=lambda item: item[0]))) for line in lines]


def _line_positions(mask) -> list[int]:
    """框线掩膜沿一个方向的投影 → 各条线的位置（相邻像素归为同一条线）。"""
    positions: list[list[int]] = []
    for position, hit in enumerate(mask):
        if not hit:
            continue
        if positions and position - positions[-1][-1] <= 6:
            positions[-1].append(position)
        else:
            positions.append([position])
    return [sum(group) // len(group) for group in positions]


def _band(lines: list[int], value: float) -> int:
    """坐标落在第几个格（相邻两条线之间）；不在表格内返回 -1。"""
    return next((index for index in range(len(lines) - 1) if lines[index] <= value < lines[index + 1]), -1)


def _tables(image, boxes: list[Box]) -> list[tuple[tuple[int, int, int, int], TableRows]]:
    """有框线的表格：返回 ((左, 上, 右, 下), 行)。

    行列按框线位置划分；两格之间没有框线即为合并单元格，文字记在合并区域的左上格，其余留空，与 DOCX 读取结果一致。
    """
    import cv2

    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, width // 30), 1)))
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(30, height // 60))))
    grid = cv2.dilate(cv2.bitwise_or(horizontal, vertical), cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    outlines, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    tables = []
    for outline in outlines:
        left, top, table_width, table_height = cv2.boundingRect(outline)
        if table_width < width * 0.25 or table_height < 40:
            continue
        across = horizontal[top:top + table_height, left:left + table_width] > 0
        down = vertical[top:top + table_height, left:left + table_width] > 0
        row_lines = _line_positions(across.any(axis=1))
        column_lines = _line_positions(down.any(axis=0))
        if len(row_lines) < 2 or len(column_lines) < 2:
            continue  # 文字下划线、分隔线不是表格

        def owner(row: int, column: int) -> tuple[int, int]:
            """所在合并区域的左上格：上方 / 左方没有框线就继续并过去。"""
            while row > 0 and across[max(row_lines[row] - 4, 0):row_lines[row] + 5,
                                     column_lines[column] + 6:column_lines[column + 1] - 5].any(axis=0).mean() < 0.5:
                row -= 1
            while column > 0 and down[row_lines[row] + 6:row_lines[row + 1] - 5,
                                      max(column_lines[column] - 4, 0):column_lines[column] + 5].any(axis=1).mean() < 0.5:
                column -= 1
            return row, column

        cells: dict[tuple[int, int], list[Box]] = {}
        for box in boxes:
            row = _band(row_lines, (box[1] + box[3]) / 2 - top)
            column = _band(column_lines, (box[0] + box[2]) / 2 - left)
            if row >= 0 and column >= 0:
                cells.setdefault(owner(row, column), []).append(box)
        if not cells:
            continue
        rows = [["".join(text for _y, text in _join_lines(cells.get((row, column), [])))
                 for column in range(len(column_lines) - 1)] for row in range(len(row_lines) - 1)]
        tables.append(((left, top, left + table_width, top + table_height), rows))
    return sorted(tables, key=lambda table: table[0][1])


def read_scanned_page(page) -> tuple[list[tuple[float, str]], list[tuple[float, TableRows]]] | None:
    """扫描页 → (正文行, 表格)，均带纵坐标；当前识别器不提供文字坐标时返回 None。"""
    engine = _engine()
    if engine is None:
        return None
    image = _page_image(page)
    boxes = _text_boxes(engine, image)
    tables = _tables(image, boxes)
    outside = [box for box in boxes if not any(
        left <= (box[0] + box[2]) / 2 <= right and top <= (box[1] + box[3]) / 2 <= bottom
        for (left, top, right, bottom), _rows in tables)]
    return _join_lines(outside), [(float(area[1]), rows) for area, rows in tables]


def _same_row(left: list[str], right: list[str]) -> bool:
    return [re.sub(r"\s", "", cell) for cell in left] == [re.sub(r"\s", "", cell) for cell in right]


def continue_table(previous: TableRows, following: TableRows) -> bool:
    """跨页表格的下半部分并回上半部分；不是同一张表时返回 False。

    下页重复的表头去掉；被分页线截断的一行（上下两半都有字的单元格，上半句没有说完）拼回一行。
    """
    if not previous or not following or len(previous[0]) != len(following[0]):
        return False
    rows = following[1:] if _same_row(previous[0], following[0]) else following
    if rows:
        last, first = previous[-1], rows[0]
        shared = [index for index, (above, below) in enumerate(zip(last, first)) if above and below]
        if shared and all(len(last[index]) >= 8 and last[index][-1] not in _SENTENCE_END for index in shared):
            previous[-1] = [above + below for above, below in zip(last, first)]
            rows = rows[1:]
    previous.extend(rows)
    return True
