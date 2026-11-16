"""标书配图：用已生成的表格数据画甘特图、分层架构图、组织机构图、流程图（Pillow 绘制，不依赖 matplotlib）。

找不到中文字体时不出图（避免方框乱码），正文不受影响。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from api.services.bidgen.document_model import Section, image

_FONT_CANDIDATES = (
    "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf", "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simsun.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
)
_PRIMARY, _LIGHT, _LINE, _TEXT = (31, 78, 121), (222, 235, 247), (120, 144, 168), (33, 37, 41)
SCALE = 2  # 2 倍像素，插入 Word 后清晰


@lru_cache
def font_path() -> str | None:
    return next((path for path in _FONT_CANDIDATES if Path(path).is_file()), None)


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path(), size * SCALE)


def _text(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, size: int, color=_TEXT) -> None:
    """在矩形内居中写字，过长时缩小字号或折行。"""
    x0, y0, x1, y1 = box
    for current in range(size, 8, -1):
        font = _font(current)
        lines, line = [], ""
        for char in text:
            if draw.textlength(line + char, font=font) > (x1 - x0) - 8 * SCALE:
                lines.append(line)
                line = char
            else:
                line += char
        lines.append(line)
        # 行首不留右括号、句读：并回上一行
        for index in range(len(lines) - 1, 0, -1):
            while lines[index] and lines[index][0] in "）)》】，。、；：":
                lines[index - 1] += lines[index][0]
                lines[index] = lines[index][1:]
        lines = [item for item in lines if item]
        height = len(lines) * (current + 4) * SCALE
        if height <= (y1 - y0) - 4 * SCALE or current == 9:
            break
    top = y0 + ((y1 - y0) - height) / 2
    for index, line in enumerate(lines):
        width = draw.textlength(line, font=font)
        draw.text((x0 + ((x1 - x0) - width) / 2, top + index * (current + 4) * SCALE), line, font=font, fill=color)


def _canvas(width: int, height: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    canvas = Image.new("RGB", (width * SCALE, height * SCALE), "white")
    return canvas, ImageDraw.Draw(canvas)


def gantt(rows: list[tuple[str, int, int]], path: Path) -> Path:
    total = max(end for _name, _start, end in rows)
    label_width, chart_width, row_height = 150, 560, 30
    canvas, draw = _canvas(label_width + chart_width + 30, row_height * (len(rows) + 1) + 30)
    s = SCALE
    scale = chart_width / total
    step = next(value for value in (5, 10, 15, 30, 60, 90, 120, 180, 365) if total / value <= 12)
    for day in range(0, total + 1, step):
        x = (label_width + day * scale) * s
        draw.line([(x, row_height * s), (x, (row_height * (len(rows) + 1)) * s)], fill=(230, 230, 230), width=s)
        _text(draw, (int(x - 25 * s), 4 * s, int(x + 25 * s), (row_height - 4) * s), f"第{max(day, 1)}日", 9, _LINE)
    for index, (name, start, end) in enumerate(rows, start=1):
        top = row_height * index * s
        _text(draw, (0, top, label_width * s, top + row_height * s), name, 10)
        x0, x1 = (label_width + (start - 1) * scale) * s, (label_width + end * scale) * s
        draw.rounded_rectangle([x0, top + 7 * s, max(x1, x0 + 4 * s), top + (row_height - 7) * s], radius=3 * s, fill=_PRIMARY)
    canvas.save(path)
    return path


def layers(names: list[str], path: Path) -> Path:
    width, height = 620, 46
    canvas, draw = _canvas(width, height * len(names) + 20)
    s = SCALE
    for index, name in enumerate(reversed(names)):
        top = (10 + index * height) * s
        fill = _LIGHT if index % 2 else (198, 220, 240)
        draw.rounded_rectangle([20 * s, top, (width - 20) * s, top + (height - 8) * s], radius=6 * s, fill=fill, outline=_LINE, width=s)
        _text(draw, (20 * s, top, (width - 20) * s, top + (height - 8) * s), name, 13, _PRIMARY)
    canvas.save(path)
    return path


def org_chart(root: str, members: list[str], path: Path) -> Path:
    members = members[:10]
    box_w, box_h, gap = 92, 54, 12
    width = max(320, len(members) * (box_w + gap) + gap)
    canvas, draw = _canvas(width, 190)
    s = SCALE
    rx0 = (width / 2 - 80) * s
    draw.rounded_rectangle([rx0, 15 * s, rx0 + 160 * s, 60 * s], radius=6 * s, fill=_PRIMARY)
    _text(draw, (int(rx0), 15 * s, int(rx0 + 160 * s), 60 * s), root, 13, (255, 255, 255))
    draw.line([(width / 2 * s, 60 * s), (width / 2 * s, 90 * s)], fill=_LINE, width=2 * s)
    left = gap + box_w / 2
    right = gap + (len(members) - 1) * (box_w + gap) + box_w / 2
    draw.line([(left * s, 90 * s), (right * s, 90 * s)], fill=_LINE, width=2 * s)
    for index, member in enumerate(members):
        x0 = (gap + index * (box_w + gap)) * s
        draw.line([((x0 / s + box_w / 2) * s, 90 * s), ((x0 / s + box_w / 2) * s, 110 * s)], fill=_LINE, width=2 * s)
        draw.rounded_rectangle([x0, 110 * s, x0 + box_w * s, (110 + box_h) * s], radius=5 * s, fill=_LIGHT, outline=_LINE, width=s)
        _text(draw, (int(x0), 110 * s, int(x0 + box_w * s), (110 + box_h) * s), member, 11)
    canvas.save(path)
    return path


def flow(steps: list[str], path: Path) -> Path:
    per_row = 5
    box_w, box_h, gap = 104, 46, 26
    rows = (len(steps) + per_row - 1) // per_row
    width = per_row * box_w + (per_row - 1) * gap + 40
    canvas, draw = _canvas(width, rows * (box_h + 40) + 10)
    s = SCALE
    for index, step in enumerate(steps):
        row, col = divmod(index, per_row)
        if row % 2:
            col = per_row - 1 - col  # 蛇形排列，箭头连续
        x0, y0 = (20 + col * (box_w + gap)) * s, (10 + row * (box_h + 40)) * s
        draw.rounded_rectangle([x0, y0, x0 + box_w * s, y0 + box_h * s], radius=6 * s, fill=_LIGHT, outline=_PRIMARY, width=s)
        _text(draw, (int(x0), int(y0), int(x0 + box_w * s), int(y0 + box_h * s)), step, 11)
        if index + 1 < len(steps):
            next_row, next_col = divmod(index + 1, per_row)
            if next_row % 2:
                next_col = per_row - 1 - next_col
            if next_row == row:
                direction = 1 if next_col > col else -1
                ax = x0 + box_w * s if direction == 1 else x0
                bx = ax + direction * gap * s
                ay = y0 + box_h * s / 2
                draw.line([(ax, ay), (bx, ay)], fill=_PRIMARY, width=2 * s)
                draw.polygon([(bx, ay), (bx - direction * 7 * s, ay - 5 * s), (bx - direction * 7 * s, ay + 5 * s)], fill=_PRIMARY)
            else:
                ax, ay = x0 + box_w * s / 2, y0 + box_h * s
                draw.line([(ax, ay), (ax, ay + 40 * s)], fill=_PRIMARY, width=2 * s)
                draw.polygon([(ax, ay + 40 * s), (ax - 5 * s, ay + 33 * s), (ax + 5 * s, ay + 33 * s)], fill=_PRIMARY)
    canvas.save(path)
    return path


def _day(text: str) -> int | None:
    matched = re.search(r"第\s*(\d+)\s*日", text)
    return int(matched.group(1)) if matched else None


def _schedule_rows(block: dict) -> list[tuple[str, int, int]]:
    header = block.get("header") or []
    rows = block.get("rows") or []
    if "开始" in header and "完成" in header:
        start_col, end_col = header.index("开始"), header.index("完成")
        result = []
        for row in rows:
            start, end = _day(row[start_col]), _day(row[end_col])
            if start and end and end >= start:
                result.append((row[0], start, end))
        return result
    duration_col = next((i for i, title in enumerate(header) if re.search(r"工期（日）|持续（日）|天数", title)), None)
    if duration_col is None:
        return []
    result, cursor = [], 1
    for row in rows:
        if not re.fullmatch(r"\d+", row[duration_col].strip()):
            continue
        days = int(row[duration_col])
        result.append((row[0], cursor, cursor + days - 1))
        cursor += days
    return result


def add_charts(roots: list[Section], folder: Path) -> int:
    """在相关表格 / 流程段落之后插入配图，返回插图数量。"""
    if font_path() is None:
        return 0
    folder.mkdir(parents=True, exist_ok=True)
    count = 0

    def save(kind: str) -> Path:
        nonlocal count
        count += 1
        return folder / f"{kind}-{count}.png"

    for root in roots:
        for section in root.walk():
            blocks = []
            for block in section.blocks:
                blocks.append(block)
                caption = block.get("caption", "")
                if block["type"] == "table":
                    schedule = _schedule_rows(block) if re.search(r"进度|工期|计划", caption) else []
                    if len(schedule) >= 3:
                        blocks.append(image(str(gantt(schedule, save("gantt"))), f"{caption}（横道图）"))
                    elif "分层架构" in caption:
                        names = [row[0] for row in block["rows"] if row and row[0]]
                        if len(names) >= 3:
                            blocks.append(image(str(layers(names, save("layers"))), "系统分层架构图"))
                    elif re.search(r"机构设置|人员配置|人员配备", caption) and "设计" not in caption:
                        members = [row[1] if re.fullmatch(r"\d+", row[0]) else row[0] for row in block["rows"]]
                        members = [m for m in members if m and not re.search(r"项目经理|项目负责人", m)]
                        if len(members) >= 3:
                            blocks.append(image(str(org_chart("项目经理", members, save("org"))), "项目组织机构图"))
                elif block["type"] == "paragraph" and block["text"].count("→") >= 3:
                    steps = re.search(r"[：:]([^。]*→[^。]*)", block["text"])
                    if steps:
                        names = [step.strip() for step in steps.group(1).split("→") if step.strip()]
                        if 4 <= len(names) <= 15:
                            blocks.append(image(str(flow(names, save("flow"))), "业务流程图"))
            section.blocks = blocks
    return count
