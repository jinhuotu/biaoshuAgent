"""整本招标文件的结构补充解析；只照录原文，不补造评分或项目事实。"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime
from typing import TYPE_CHECKING

from api.services.bidgen.docx_reader import DocBlock, template_paragraph

if TYPE_CHECKING:
    from api.services.bidgen.tender_parser import TenderInfo

_CHAPTER = re.compile(r"^第[一二三四五六七八九十百\d]+章")
# 招标文件叫“投标文件格式”；竞争性谈判 / 磋商 / 询比等采购文件叫“响应文件格式”
_FORMAT_CHAPTER = re.compile(r"^第[一二三四五六七八九十百\d]+章\s*(?:投标|响应|磋商响应|谈判响应|磋商|谈判|应答|申请)文件格式(?:要求)?\s*$")
# 须知 / 评审办法 / 文件格式三类章节的同义写法
_DOCUMENT_KEYWORDS = (
    ("投标人须知", "供应商须知", "磋商须知", "谈判须知", "响应人须知"),
    ("评标办法", "评审办法", "评分办法", "磋商办法"),
    ("投标文件格式", "响应文件格式", "磋商文件格式", "谈判文件格式"),
)
# 一级格式条目中属于技术部分的标题
_TECHNICAL_TITLE = re.compile(r"承包人建议书|设计组织技术措施|承包人实施计划|服务承诺|技术响应|技术方案|技术部分|技术文件|实施方案|服务方案")
_NUMBER = r"[一二三四五六七八九十百\d]+"
_TOP_ITEM = re.compile(rf"^({_NUMBER})[、．.]\s*(.+)$")
_CHILD_ITEM = re.compile(rf"^[（(]({_NUMBER})[）)]\s*(.+)$")
_RANGE = re.compile(r"(?<![\d.])(-?\d+(?:\.\d+)?)\s*[-~～－—–]\s*(-?\d+(?:\.\d+)?)\s*分")
_POINTS = re.compile(r"[（(]\s*(\d+(?:\.\d+)?)\s*分\s*[）)]")
_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(上午|下午)?\s*(\d{1,2})\s*[:：时]\s*(\d{1,2})\s*分?")
# 企业自主招标文件没有“投标文件格式”章节，而是在须知里写“商务标书应包括下列内容：”后逐条列出
_COMPOSITION_HEAD = re.compile(r"^(商务|技术)(?:标书?|文件|部分)(?:应当?|须)?(?:包括|包含)(?:以下|下列|如下)?(?:内容|文件|资料|材料)?[:：]?$")
_LIST_ITEM = re.compile(r"^(\d+)\s*[.．、]\s*(.+)$")
_ATTACHMENT_HEAD = re.compile(rf"^附件?\s*{_NUMBER}\s*[:：、.．]")
# 只有标题以“模板 / 格式”结尾的附件才是投标文件的格式原文（“附二：功能需求清单”不是）
_ATTACHMENT_TEMPLATE = re.compile(rf"^附件?\s*{_NUMBER}\s*[:：、.．]\s*(.+?)(?:模板|格式)$")
_REVIEW_HEADER = ("评审项目", "评审分类", "评审内容", "分配分数")
# 清单条目括注的编写内容（“技术方案（内容包括：总体设计、平台设计……）”）：每项内容是该条目下的一节
_LISTED_CONTENTS = re.compile(r"^(.{2,40}?)[（(]\s*(?:内容)?(?:包括|包含)[:：]\s*(.+?)[）)]?$")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def is_integrated_document(blocks: list[DocBlock]) -> bool:
    """未知文件名时以正文中的须知、评标、格式章节组合识别。"""
    headings = [_compact(block.text) for block in blocks if block.kind == "paragraph" and len(block.text) < 90]
    return sum(any(keyword in title for title in headings for keyword in group) for group in _DOCUMENT_KEYWORDS) >= 2


def _front_clauses(blocks: list[DocBlock]) -> list[dict]:
    clauses: list[dict] = []
    active = False
    for block in blocks:
        if block.kind != "table":
            if active and block.text.strip() not in ("续表", "投标人须知前附表（续）"):
                active = False
            continue
        header = _compact("".join(block.rows[0]))
        has_header = all(word in header for word in ("条款号", "条款名称")) and "内容" in header
        if has_header:
            active = True
        if not active:
            continue
        if any(len(row) > 3 for row in block.rows):
            active = False
            continue
        for row in block.rows[1:] if has_header else block.rows:
            cells = [_clean(cell) for cell in row]
            if len(cells) < 2:
                continue
            number, name, content = (cells + [""])[:3] if len(cells) >= 3 else (cells[0], "", cells[1])
            numbered = re.match(r"^(\d+(?:\.\d+)*(?:\s*[（(]\d+[）)])?)\s*(.*)$", number)
            if numbered:
                clauses.append({"number": _compact(numbered[1]), "name": _compact(name or numbered[2]), "content": content})
            elif clauses:
                if number:  # 合并为两列的补充条款仍保留小标题。
                    content = f"{number}：{content}"
                if name and not clauses[-1]["name"]:
                    clauses[-1]["name"] = _compact(name)
                if content:
                    clauses[-1]["content"] = "\n".join(filter(None, (clauses[-1]["content"], content)))
    return clauses


def _read_deadline(text: str, info: TenderInfo) -> None:
    matched = _DATE.search(text)
    if not matched:
        return
    year, month, day, half_day, hour, minute = matched.groups()
    try:
        value = datetime(int(year), int(month), int(day), int(hour) + (12 if half_day == "下午" and int(hour) < 12 else 0),
                         int(minute))
    except ValueError:
        warning = "招标文件中的投标截止时间不是有效日期，请核对原文并手工补充。"
        if warning not in info.warnings:
            info.warnings.append(warning)
        return
    info.bid_deadline = value.isoformat(timespec="minutes")


def _party_name(content: str) -> str:
    name = re.sub(r"^名称\s*[:：]\s*", "", content)
    return re.split(r"\s+(?:地\s*址|联系人|项目负责人|电话|电\s*话)\s*[:：]", name)[0].strip()


def _read_main_info(blocks: list[DocBlock], info: TenderInfo) -> None:
    # 格式章节包含待填写的项目名称、招标人和截止日期，不能作为项目事实。
    end = next((i for i, block in enumerate(blocks) if _FORMAT_CHAPTER.fullmatch(block.text)), len(blocks))
    paragraphs = [block.text for block in blocks[:end] if block.kind == "paragraph"]
    text = "\n".join(paragraphs)
    for field_name, pattern in (
        ("project_name", r"(?:^|\n)\s*(?:\d+(?:\.\d+)*[.．、]?|[一二三四五六七八九十]+、)?\s*(?:项目名称|招标项目)\s*[:：]\s*([^\n]+)"),
        ("tender_no", r"(?:招标编号|项目编号)\s*[:：]\s*([A-Za-z0-9][A-Za-z0-9\-]*)"),
        ("agency", r"(?:^|\n)\s*(?:招标|采购)代理机构\s*[:：]\s*([^\n]+)"),
    ):
        match = re.search(pattern, text)
        if match and not getattr(info, field_name):
            setattr(info, field_name, _clean(match[1]))
    if not info.purchasers:
        info.purchasers = list(dict.fromkeys(_clean(value) for value in re.findall(
            r"(?:^|\n)\s*(?:[一二三四五六七八九十]+、)?\s*(?:招标人|采购人|招标方)(?:[（(][^）)]+[）)])?\s*[:：]\s*([^\n]+)", text)))
    if not info.bid_deadline:
        for line in paragraphs:
            if re.search(r"(?:投标|递交).*截止时间", line):
                _read_deadline(line, info)
                if info.bid_deadline:
                    break
    if not info.general_qualifications:
        active = False
        for line in paragraphs:
            if re.match(r"^\s*\d+(?:\.\d+)?[.．、]?\s*(?:投标人|供应商|申请人|响应人)的?资格要求[：:]?\s*$", line):
                active = True
            elif active and ("招标文件的获取" in line or _CHAPTER.match(line)):
                break
            elif active:
                info.general_qualifications.append(line)
    if re.search(r"不接受联合体|不允许联合体|拒绝联合体", text):
        info.consortium_allowed = False
    for clause in info.key_clauses:
        name, content = _compact(clause["name"]), clause["content"]
        if name == "项目名称" and not info.project_name:
            info.project_name = _compact(content)
        elif name == "招标人" and not info.purchasers:
            info.purchasers = [_party_name(content)]
        elif name == "招标代理机构" and not info.agency:
            info.agency = _party_name(content)
        elif name in ("投标截止时间", "投标文件递交截止时间"):
            _read_deadline(content, info)
        elif name in ("投标有效期", "响应文件有效期", "响应有效期"):
            match = re.search(r"(\d+)\s*(?:个\s*)?(?:日历天|日|天)", content)
            if match:
                info.validity_days = int(match[1])
        elif name == "是否接受联合体投标":
            if re.search(r"不接受|不允许|[☑√■]\s*不", content):
                info.consortium_allowed = False
            elif "接受联合体" in content:
                info.consortium_allowed = True
        elif name == "投标保证金":
            # 附条件的中小企业或信用等级减免不能否定已勾选的保证金要求。
            if re.search(r"[☑√■]\s*要求", content):
                info.bid_bond_required = True
            elif re.search(r"[☑√■]\s*不要求|^\s*(?:不要求|不收取|免收)", content):
                info.bid_bond_required = False


def _score_bounds(factor: str, rule: str) -> tuple[float | None, float | None]:
    fixed = _POINTS.search(factor)
    if fixed:
        return None, float(fixed[1])
    match = _RANGE.search(factor)
    if not match:
        ranges = list(_RANGE.finditer(rule))
        match = ranges[0] if len(ranges) == 1 else None
    if match:
        return float(match[1]), float(match[2])
    return None, None


def _scoring(blocks: list[DocBlock], info: TenderInfo) -> None:
    from api.services.bidgen.tender_parser import ScoreCriterion

    active = False
    group = ""
    for block in blocks:
        if block.kind != "table":
            if active and block.text.strip() != "续表":
                active, group = False, ""
            continue
        for row in block.rows:
            if len(row) != 4:
                # 表头合并成 3 格（如“条款号及名称 | 评分因素 | 评分标准”）时也作为评分表开始
                if _compact(row[0]).startswith("条款号") and any(
                        word in _compact("".join(row)) for word in ("评分因素", "评分标准")):
                    active = True
                continue
            number, group_cell, factor, rule = [_clean(cell) for cell in row]
            if _compact(number).startswith("条款号"):
                if any(word in _compact("".join(row)) for word in ("评审因素", "评分因素", "评分标准")):
                    active = True
                continue
            if not active or number.startswith("注"):
                continue
            if group_cell:
                group = _compact(group_cell)
            if "评分标准" not in group and not re.search(r"(?:报价|商务|技术|服务|价格|其他)部分", group):
                continue
            if not factor:
                if info.criteria and rule:
                    info.criteria[-1].rule += "\n" + rule
                continue
            low, high = _score_bounds(factor, rule)
            if high is None:
                continue  # 初步评审及没有明确分值的项目不编造分数。
            label = _compact(_POINTS.sub("", _RANGE.sub("", factor)))
            # 去掉分值后残留的空括号；不能整体 strip 括号，否则“（含设计）”会丢掉右括号。
            label = re.sub(r"^\d+[、.．]", "", re.sub(r"[（(]\s*[)）]", "", label)).strip()
            technical = bool(re.search(r"建议书|设计组织|实施方案|施工方案|技术|服务承诺", group + label))
            info.criteria.append(ScoreCriterion(
                criterion_id=f"I-{len(info.criteria) + 1:02d}", volume="technical" if technical else "business",
                category="", group=_POINTS.sub("", group), factor=label,
                min_score=low, max_score=high, rule=rule or factor,
            ))


def _review_scoring(blocks: list[DocBlock], info: TenderInfo) -> None:
    """企业自主招标的评审表：评审项目 | 评审分类 | 评审内容 | 分配分数，纵向合并的单元格沿用上一行。"""
    from api.services.bidgen.tender_parser import ScoreCriterion

    for block in blocks:
        if block.kind != "table" or tuple(_compact(cell) for cell in block.rows[0]) != _REVIEW_HEADER:
            continue
        rows = [[_clean(cell) for cell in row] for row in block.rows[1:] if len(row) == 4]
        group = category = ""
        for index, (group_cell, category_cell, content, score) in enumerate(rows):
            if group_cell:
                group = re.sub(r"[（(]总分[^）)]*[）)]", "", _compact(group_cell))
            if category_cell:
                category = category_cell
            try:
                max_score = float(score)
            except ValueError:
                continue  # 没有明确分值的行不编造分数
            following = rows[index + 1] if index + 1 < len(rows) else None
            # 一个评审分类下有多行评审内容时，每行内容是一个评分要素
            shared = not category_cell or (following is not None and not following[0] and not following[1])
            info.criteria.append(ScoreCriterion(
                criterion_id=f"I-{len(info.criteria) + 1:02d}",
                volume="business" if re.search(r"商务|价格|报价", group) else "technical",
                category="", group=group, factor=content if shared else category,
                min_score=None, max_score=max_score, rule=f"{category}：{content}" if shared else content,
            ))


def _ordinal(number: str) -> str:
    if number.isdigit():
        return number
    digits = {name: value for value, name in enumerate("零一二三四五六七八九")}
    if "百" in number:
        hundreds, number = number.split("百", 1)
        return str(digits.get(hundreds, 1) * 100 + int(_ordinal(number or "零")))
    if "十" in number:
        tens, ones = number.split("十", 1)
        return str(digits.get(tens, 1) * 10 + digits.get(ones, 0))
    return str(digits.get(number, 0))


def _title_key(text: str) -> str:
    """去编号、括注后的标题，用于目录条目与正文标题对照。"""
    value = re.sub(rf"^\s*(?:{_NUMBER}[、．.]|[（(]{_NUMBER}[）)])\s*", "", text.strip())
    return re.sub(r"[（(][^（()）]*[）)]|\s", "", value)


def format_chapter_bodies(blocks: list[DocBlock]) -> Iterator[list[DocBlock]]:
    """取格式章正文，跳过总目录中连续排列的章名。"""
    start = None
    for index, block in enumerate(blocks):
        if block.kind != "paragraph" or not _CHAPTER.match(block.text):
            continue
        if start is not None and start < index:
            yield blocks[start:index]
        start = index + 1 if _FORMAT_CHAPTER.fullmatch(block.text) else None
    if start is not None and start < len(blocks):
        yield blocks[start:]


def _formats(blocks: list[DocBlock], info: TenderInfo) -> None:
    from api.services.bidgen.tender_parser import FormatItem

    body = next(format_chapter_bodies(blocks), [])
    if not body:
        _composition_formats(blocks, info)
        _finish_formats(info)
        return
    # 格式章节开头的“目录”逐行列出一级条目：记下标题，正文里没设标题样式的同名条目也能认出，目录本身不当正文
    toc: list[str] = []
    toc_end = 0
    toc_start = next((i for i, b in enumerate(body[:40]) if b.kind == "paragraph" and _compact(b.text) == "目录"), None)
    if toc_start is not None:
        heading_run = False
        for index in range(toc_start + 1, len(body)):
            block = body[index]
            if block.kind != "paragraph":
                toc_end = index
                break
            if block.level:
                # PDF 中目录行也可能被识别成标题：连续的编号标题（下一行仍是编号条目）视为目录；
                # 否则（标题后接正文或子标题）说明正文已开始
                following = body[index + 1] if index + 1 < len(body) else None
                listed = following is not None and following.kind == "paragraph" and bool(_TOP_ITEM.match(following.text))
                if not (_TOP_ITEM.match(block.text) and (listed or heading_run)):
                    toc_end = index
                    break
                heading_run = True
            if not _TOP_ITEM.match(block.text):
                if toc:
                    toc_end = index + 1
                    continue
                break
            key = _title_key(block.text)
            if key in toc:  # 目录结束、正文开始（第一个条目再次出现）
                toc_end = index
                break
            toc.append(key)
            toc_end = index + 1
    for block in body[:toc_start if toc_start is not None else 12]:
        cover = re.sub(r"\s+", "", block.text) if block.kind == "paragraph" else ""
        if re.fullmatch(r"(?:竞争性)?(?:谈判|磋商)?(?:投标|响应|应答|申请)文件", cover):
            info.cover_title = cover
            break
    parent = None
    current = None
    for block in body[toc_end:]:
        if block.kind == "paragraph":
            text = block.text
            if "\n" in text and block.level:
                # 标题段落前面粘着上一节的备注（“备注：……\n三、报价表”）：备注归上一节，末行作标题
                *head, text = text.split("\n")
                if current and any(line.strip() for line in head):
                    current.template.append({"kind": "paragraph", "text": "\n".join(head).strip()})
            top = _TOP_ITEM.match(text)
            child = _CHILD_ITEM.match(text) if parent else None
            # 同一段含多个编号是目录串，不是正文标题。
            is_title = len(text) < 85 and len(re.findall(rf"{_NUMBER}[、．.]", text)) <= 1
            listed = bool(top) and _title_key(text) in toc
            if (is_title and top and current is not None and current.level == 1 and not current.template
                    and _ordinal(top[1]) == current.number):
                # 同号标题紧接上一条且上一条尚无内容：是上一条的副标题（如“二、法定代表人身份证明（适用……）”）
                current.template.append({"kind": "paragraph", "text": text})
                continue
            if is_title and (block.level or listed) and (top or child):
                match = top or child
                number = _ordinal(match[1])
                if child and not top:
                    number = parent.number + "." + number
                title = match[2].strip()
                volume = ("技术文件" if _TECHNICAL_TITLE.search(title) else "商务文件") if top else parent.volume
                current = FormatItem(number=number, title=title, volume=volume, level=1 if top else 2,
                                     required=True, has_format=False)
                if top:
                    parent = current
                else:
                    parent.children_required = True
                info.format_items.append(current)
                continue
        if current:
            current.template.append(template_paragraph(block) if block.kind == "paragraph"
                                    else {"kind": "table", "rows": block.rows})
    _finish_formats(info)


def _composition_formats(blocks: list[DocBlock], info: TenderInfo) -> None:
    """没有格式章节时，按须知中“商务标书 / 技术标书应包括下列内容”的清单建目录，附件模板作格式原文。"""
    from api.services.bidgen.tender_parser import FormatItem

    volume = ""
    for block in blocks:
        text = block.text.strip() if block.kind == "paragraph" else ""
        head = _COMPOSITION_HEAD.match(_compact(text))
        listed = _LIST_ITEM.match(text) if volume and not head else None
        if head:
            volume = head[1] + "文件"
        elif listed:
            title = listed[2].strip().rstrip("。；;")
            contents = _LISTED_CONTENTS.match(title)
            topics = [topic.strip() for topic in contents[2].split("、") if topic.strip()] if contents else []
            if len(topics) >= 2:
                title = contents[1].strip()
            info.format_items.append(FormatItem(number=listed[1], title=title, volume=volume, level=1, required=True,
                                                has_format=False, children_required=len(topics) >= 2))
            if len(topics) >= 2:
                info.format_items += [FormatItem(number=f"{listed[1]}.{index}", title=topic, volume=volume, level=2,
                                                 required=True, has_format=False) for index, topic in enumerate(topics, start=1)]
        else:
            volume = ""
    if not info.format_items:
        return
    info.format_source = "list"
    for index, block in enumerate(blocks):
        matched = _ATTACHMENT_TEMPLATE.match(block.text.strip()) if block.kind == "paragraph" else None
        if not matched:
            continue
        name = matched[1].strip()
        keyword = re.sub(r"^投标", "", name)[:2]  # “报价单” 对应清单里的 “投标报价表”
        entries = [item for item in info.format_items if item.level == 1]
        target = next((item for item in entries if name in item.title), None) or next(
            (item for item in entries if len(keyword) == 2 and keyword in item.title), None)
        if target is None or target.template:
            continue
        for following in blocks[index + 1:]:
            if following.kind != "paragraph":
                target.template.append({"kind": "table", "rows": following.rows})
            elif _ATTACHMENT_HEAD.match(following.text):
                break
            elif target.template or _compact(following.text) != _compact(name):  # 模板首行重复的标题不要
                target.template.append(template_paragraph(following))


def _finish_formats(info: TenderInfo) -> None:
    if info.format_items:
        info.format_layout = "integrated"
    for item in info.format_items:
        item.has_format = any(block["kind"] == "table" or not re.fullmatch(
            r"[（(]?\s*(?:格式自拟|自拟格式)\s*[）)]?[。.]?", block.get("text", "")) for block in item.template)


def parse_integrated_document(blocks: list[DocBlock], info: TenderInfo, *, legacy_format: bool = False) -> None:
    """既有行业解析结果优先；通用正文仅补充缺失字段。"""
    if not legacy_format:
        clauses = _front_clauses(blocks)
        if clauses:
            info.key_clauses = clauses
    _read_main_info(blocks, info)
    if not info.criteria:
        _scoring(blocks, info)
    if not info.criteria:
        _review_scoring(blocks, info)
    if not legacy_format and not info.format_items:
        _formats(blocks, info)
