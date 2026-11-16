"""投标文件的中间结构：章节树 + 内容块，供 Word 渲染、评分自查与前端大纲共用。"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

PENDING_PREFIX = "【待填"
_CHINESE_DIGITS = "零一二三四五六七八九十"


def chinese_number(value: int) -> str:
    """1 → 一，12 → 十二，23 → 二十三（目录编号够用即可，支持到 99）。"""
    if value <= 10:
        return _CHINESE_DIGITS[value]
    tens, ones = divmod(value, 10)
    head = "十" if tens == 1 else f"{_CHINESE_DIGITS[tens]}十"
    return head + (_CHINESE_DIGITS[ones] if ones else "")


def numbering_label(level: int, index: int) -> str:
    """各级标题编号：一、 / （一） / 1. / （1）。"""
    if level == 2:
        return f"{chinese_number(index)}、"
    if level == 3:
        return f"（{chinese_number(index)}）"
    if level == 4:
        return f"{index}."
    if level == 5:
        return f"（{index}）"
    return ""


# 填空横线标记：私用区字符包住需加下划线的片段（源稿横线、已填入的值）。
# 段落块里 text 永远是去掉标记的纯文字，带标记的原文另存于 form，仅供 Word 渲染使用。
UNDERLINE_START = ""
UNDERLINE_END = ""
BLANK_UNDERLINE_WIDTH = 8  # 空白填空默认留出的全角空格数
_UNDERLINE_MARKS = re.compile(f"[{UNDERLINE_START}{UNDERLINE_END}]")
_ADJACENT_UNDERLINES = re.compile(rf"{UNDERLINE_END}(\s*){UNDERLINE_START}")
_FILLED_UNDERLINE = re.compile(
    rf"{UNDERLINE_START}\s*([^{UNDERLINE_START}{UNDERLINE_END}]*?\S)\s*{UNDERLINE_END}"
)


def underline(value: str) -> str:
    """填空值加横线标记；值为空时留一段空白横线，等待手工补充。"""
    text = str(value or "").strip()
    return f"{UNDERLINE_START}{text or chr(0x3000) * BLANK_UNDERLINE_WIDTH}{UNDERLINE_END}"


def strip_underline_marks(text: str) -> str:
    return _UNDERLINE_MARKS.sub("", text)


def tidy_underlines(form: str) -> str:
    """相邻横线合并为一段；横线内已有文字时去掉原空白，横线只压在文字下。"""
    merged = _ADJACENT_UNDERLINES.sub(r"\1", form)
    return _FILLED_UNDERLINE.sub(lambda m: f"{UNDERLINE_START}{m.group(1).strip()}{UNDERLINE_END}", merged)


def paragraph(text: str, *, bold: bool = False, align: str = "", indent: bool = True) -> dict:
    block = {"type": "paragraph", "text": text, "bold": bold, "align": align, "indent": indent}
    if _UNDERLINE_MARKS.search(text):
        block["form"] = tidy_underlines(text)
        block["text"] = strip_underline_marks(block["form"])
    return block


def table(header: list[str], rows: list[list[str]], *, caption: str = "", widths: list[float] | None = None) -> dict:
    return {"type": "table", "header": header, "rows": rows, "caption": caption, "widths": widths or []}


def form_table(rows: list[list[str]], *, caption: str = "", widths: list[float] | None = None) -> dict:
    """无表头的表单式表格（如基本情况表）；首列按标签加粗。"""
    return {"type": "table", "header": [], "rows": rows, "caption": caption, "widths": widths or [], "form": True}


def label_cell(text: str, *, colspan: int = 1, rowspan: int = 1) -> tuple[str, int, int, bool]:
    """合并表格中的标签单元格（加粗、底纹）。"""
    return (text, colspan, rowspan, True)


def value_cell(text: str, *, colspan: int = 1, rowspan: int = 1) -> tuple[str, int, int, bool]:
    """合并表格中的内容单元格。"""
    return (text, colspan, rowspan, False)


def grid_table(cell_rows: list[list[tuple[str, int, int, bool]]], *, columns: int, widths: list[float] | None = None) -> dict:
    """带跨行 / 跨列合并的表单表格，用于还原招标文件给定的表格版式。

    ``cell_rows`` 每行从左到右列出未被上方跨行单元格占用的单元格。
    """
    rows = [[""] * columns for _ in cell_rows]
    occupied = [[False] * columns for _ in cell_rows]
    merges: list[list[int]] = []
    labels: list[list[int]] = []
    for row_index, cells in enumerate(cell_rows):
        column = 0
        for text, colspan, rowspan, is_label in cells:
            while column < columns and occupied[row_index][column]:
                column += 1
            if column + colspan > columns or row_index + rowspan > len(cell_rows):
                raise ValueError(f"合并表格第 {row_index + 1} 行超出表格范围")
            for covered_row in range(row_index, row_index + rowspan):
                for covered_column in range(column, column + colspan):
                    occupied[covered_row][covered_column] = True
            rows[row_index][column] = text
            if is_label:
                labels.append([row_index, column])
            if colspan > 1 or rowspan > 1:
                merges.append([row_index, column, row_index + rowspan - 1, column + colspan - 1])
            column += colspan
    return {"type": "table", "header": [], "rows": rows, "caption": "", "widths": widths or [],
            "merges": merges, "labels": labels}


def placeholder(text: str, *, height: float = 6.0) -> dict:
    """扫描件 / 证明材料粘贴处：Word 中渲染成虚线框。"""
    return {"type": "placeholder", "text": text, "height": height}


def image(path: str, caption: str = "") -> dict:
    return {"type": "image", "path": path, "caption": caption}


def signature(lines: list[str]) -> dict:
    return {"type": "signature", "lines": lines}


def title_line(text: str) -> dict:
    """章节内的居中小标题（如“承 诺 函”）。"""
    return {"type": "paragraph", "text": text, "bold": True, "align": "center", "indent": False, "size": 15}


@dataclass
class Section:
    key: str
    title: str
    level: int
    blocks: list[dict] = field(default_factory=list)
    children: list[Section] = field(default_factory=list)
    criteria: list[str] = field(default_factory=list)
    not_applicable: bool = False
    page_break: bool = False
    number: str = ""
    source: str = ""  # 对应招标文件格式目录的条目号
    fixed: bool = False  # 招标文件给定的固定格式，多次生成时措辞不得变化

    @property
    def heading(self) -> str:
        return f"{self.number}{self.title}"

    def add(self, child: Section) -> Section:
        self.children.append(child)
        return child

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def plain_text(self) -> str:
        """本节（含子节）的全部文字，用于评分自查取证。"""
        parts: list[str] = []
        for node in self.walk():
            parts.append(node.heading)
            for block in node.blocks:
                if block["type"] in ("paragraph", "placeholder"):
                    parts.append(block["text"])
                elif block["type"] == "table":
                    parts.append(" ".join(block["header"]))
                    parts.extend(" ".join(row) for row in block["rows"])
                elif block["type"] == "signature":
                    parts.extend(block["lines"])
        return "\n".join(parts)

    def to_outline(self) -> dict:
        text = self.plain_text()
        return {
            "key": self.key,
            "title": self.title,
            "heading": self.heading,
            "level": self.level,
            "source": self.source,
            "notApplicable": self.not_applicable,
            "criteria": self.criteria,
            "charCount": len(re.sub(r"\s", "", text)) if not self.children else None,
            "pendingCount": sum(b.get("text", "").count(PENDING_PREFIX) for b in self.blocks)
            + sum(cell.count(PENDING_PREFIX) for b in self.blocks if b["type"] == "table" for row in b["rows"] for cell in row),
            "placeholderCount": sum(1 for b in self.blocks if b["type"] == "placeholder"),
            "children": [child.to_outline() for child in self.children],
        }


def assign_numbers(sections: list[Section]) -> None:
    """按同级顺序回填标题编号。"""
    for index, section in enumerate(sections, start=1):
        if not section.number:
            section.number = numbering_label(section.level, index)
            if section.number and not section.number.endswith(("、", "）")):
                section.number += " "
        assign_numbers(section.children)


@dataclass
class ComposeContext:
    """一次生成所需的全部输入。"""

    tender: dict
    profile: dict
    lots: list[dict]
    options: dict
    certificate_files: dict[str, str] = field(default_factory=dict)  # 证件 id → 图片 / PDF 原件路径
    certificate_check: dict = field(default_factory=dict)
    pending: list[dict] = field(default_factory=list)
    review_items: list[dict] = field(default_factory=list)
    coverage: list[dict] = field(default_factory=list)  # 评分点覆盖自检记录
    corpus_hits: list[dict] = field(default_factory=list)  # 复用的历史标书章节
    rng: random.Random | None = None  # 措辞随机源；为空时始终取第一种表述
    _section_key: str = ""

    # ---- 取值辅助 ----
    @property
    def company(self) -> dict:
        return self.profile["company"]

    @property
    def company_name(self) -> str:
        return self.company["name"] or self.need("投标人名称")

    @property
    def project_name(self) -> str:
        return self.tender.get("project_name") or self.need("项目名称")

    @property
    def tender_no(self) -> str:
        return self.tender.get("tender_no") or self.need("招标编号")

    @property
    def deadline(self) -> datetime | None:
        value = self.tender.get("bid_deadline")
        return datetime.fromisoformat(value) if value else None

    @property
    def sign_date(self) -> str:
        custom = str(self.options.get("signDate") or "")
        try:
            chosen = date.fromisoformat(custom) if custom else None
        except ValueError:
            chosen = None
        if chosen is None:
            today = date.today()
            chosen = self.deadline.date() if self.deadline and today > self.deadline.date() else today
        return f"{chosen.year}年{chosen.month}月{chosen.day}日"

    @property
    def lot_names(self) -> str:
        return "、".join(lot["lot_name"] for lot in self.lots) or self.need("所投分标")

    @property
    def lot_codes(self) -> str:
        return "、".join(lot["lot_code"] for lot in self.lots) or self.need("分标编号")

    @property
    def categories(self) -> list[str]:
        return list(dict.fromkeys(lot["category"] for lot in self.lots if lot.get("category")))

    @property
    def package_no(self) -> str:
        return str(self.options.get("packageNo") or self.tender.get("package_no") or "").strip() or self.need("包号")

    @property
    def purchaser_names(self) -> str:
        return "、".join(self.tender.get("purchasers") or []) or "招标人"

    @property
    def is_cable(self) -> bool:
        return any("电缆" in name for name in (*self.categories, *(s.get("model", "") for s in self.tender.get("tech_specs", []))))\
            or any(re.match(r"(?i)YJ|VV|VLV", s.get("model", "")) for s in self.tender.get("tech_specs", []))

    def pick(self, *variants: str) -> str:
        """同一含义的多种表述中取一种，使同一招标文件多次生成的正文不雷同。"""
        return self.rng.choice(variants) if self.rng else variants[0]

    def enumerate_clauses(self, clauses: list[str], *, shuffle: bool = True) -> str:
        """把并列措施写成“（1）…；（2）…。”；并列项之间无先后关系时可打乱顺序。"""
        ordered = list(clauses)
        if shuffle and self.rng:
            self.rng.shuffle(ordered)
        return "；".join(f"（{index}）{clause}" for index, clause in enumerate(ordered, start=1)) + "。"

    def within_section(self, key: str) -> None:
        self._section_key = key

    def need(self, hint: str) -> str:
        """缺失资料：正文留出醒目的待填标记，并记入待补充清单。"""
        entry = {"section": self._section_key, "hint": hint}
        if entry not in self.pending:
            self.pending.append(entry)
        return f"{PENDING_PREFIX}：{hint}】"

    def value(self, raw: object, hint: str) -> str:
        text = str(raw or "").strip()
        return text or self.need(hint)

    def clause(self, *keywords: str) -> dict | None:
        """按条款名称关键词查投标人须知前附表。"""
        for item in self.tender.get("key_clauses", []):
            if all(keyword in item["name"] for keyword in keywords):
                return item
        return None

    def criteria_for(self, volume: str) -> list[dict]:
        """本次所投分标适用的评分项。"""
        categories = {lot.get("scoring_category") or lot.get("category") for lot in self.lots}
        result = []
        for criterion in self.tender.get("criteria", []):
            if criterion["volume"] != volume:
                continue
            if volume == "technical" and categories and criterion["category"] not in categories:
                continue
            result.append(criterion)
        return result

    def query_window(self, days: int = 20) -> str:
        """信用报告查询日期要求：投标截止日前 N 天内。"""
        if not self.deadline:
            return f"投标截止日前{days}天内"
        earliest = self.deadline.date() - timedelta(days=days)
        latest = self.deadline.date()
        return f"{earliest.year}年{earliest.month}月{earliest.day}日至{latest.year}年{latest.month}月{latest.day}日"

    def signature_block(self, *extra: str, dated: bool = True) -> dict:
        bidder = "供应商" if re.search(r"响应|磋商|谈判", self.tender.get("cover_title") or "") else "投标人"
        lines = [f"{bidder}：{self.company_name}（盖单位章）", *extra]
        if dated:
            lines.append(f"日期：{self.sign_date}")
        return signature(lines)
