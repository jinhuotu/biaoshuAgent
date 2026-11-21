"""招标文件规则解析：从解包后的文件树抽取项目信息、分标、评分标准、投标文件格式与技术规范。

全部基于文档结构（标题 / 表头）与正则，不调用模型。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from api.services.bidgen.archive import ARCHIVE_SUFFIXES, ArchiveEntry, flatten_entries
from api.services.bidgen.docx_reader import DocBlock, blocks_to_text, read_docx_blocks, template_paragraph
from api.services.bidgen.pdf_reader import read_pdf_blocks
from api.services.bidgen.integrated_parser import format_chapter_bodies, is_integrated_document, parse_integrated_document
from api.services.bidgen.project_facts import extract_project_facts

ROLE_INVITATION = "invitation"
ROLE_MAIN = "main"
ROLE_TECH_SPEC = "tech_spec"
ROLE_CONTRACT = "contract"
ROLE_SIGNATURE = "signature"
ROLE_ARCHIVE = "archive"
ROLE_SCORING = "scoring"
ROLE_OTHER = "other"

ROLE_LABELS = {
    ROLE_INVITATION: "投标邀请书",
    ROLE_MAIN: "招标文件正文",
    ROLE_TECH_SPEC: "技术规范书",
    ROLE_CONTRACT: "合同文件",
    ROLE_SIGNATURE: "签名校验文件",
    ROLE_ARCHIVE: "压缩包",
    ROLE_SCORING: "评分标准附件",
    ROLE_OTHER: "其他文件",
}

_SCORE_RANGE = re.compile(r"[（(]\s*(-?\d+(?:\.\d+)?)\s*[-~－—–]\s*(-?\d+(?:\.\d+)?)\s*分?\s*[)）]")
_SCORE_NUMBER = r"-?\d+(?:\.\d+)?"
_SCORE_TIER = re.compile(
    rf"【\s*(?:(?P<zero>[^【】]*?不扣分)|(?P<deduct>扣)?\s*(?P<first>{_SCORE_NUMBER})"
    rf"(?:\s*[-~～－—–]\s*(?P<last>{_SCORE_NUMBER}))?\s*分?)\s*】(?:\s*分)?"
)
_SCORE_ATTACHMENT = re.compile(r"^(?P<label>.*?)(?P<volume>商务|技术)评分标准$")
_CATEGORY_WEIGHTS_HEADER = ("物资类别", "权重比例（技：商：价）")
_FIXED_ID = re.compile(r"(\d{4}-\d{9}-\d{5})")
_CHAPTER_HEADING = re.compile(r"^第[一二三四五六七八九十]+章")
_VOLUME_TITLES = ("价格文件", "商务文件", "技术文件")
_PLATFORM_VOLUMES = (*_VOLUME_TITLES, "资质业绩文件")
_PLATFORM_DIRECTORY_HEADER = (
    "序号", "内容", "电子商务平台", "招投标智能服务平台", "是否有格式要求", "电子商务平台离线投标工具上传端口",
)
_PLATFORM_TEMPLATE_HEADING = re.compile(r"^(?:[（(][一二三四五六七八九十\d]+[）)]|\d+[.．、]|附表\s*\d+)")
_PLATFORM_PATH_NOTE = re.compile(r"[（(](?:在投标工具|可在投标工具|投标工具路径|上传投标工具路径)")


@dataclass
class TenderLot:
    category: str
    lot_code: str
    lot_name: str
    unit: str = ""
    quantity: str = ""
    package_count: str = ""
    package_range: str = ""
    prequalification: str = ""
    tech_weight: str = ""
    business_weight: str = ""
    price_weight: str = ""
    scoring_category: str = ""
    performance_requirement: str = ""
    award_rule: str = ""


@dataclass
class ScoreCriterion:
    criterion_id: str
    volume: str  # business / technical
    category: str  # 技术评分适用的物资品类；商务为空
    group: str
    factor: str
    min_score: float | None
    max_score: float | None
    rule: str
    tiers: list[dict] = field(default_factory=list)
    source_path: str = ""


@dataclass
class FormatItem:
    """第六章“投标文件格式目录”中的一行。"""

    number: str
    title: str
    volume: str
    level: int
    required: bool
    has_format: bool
    port: str = ""
    not_applicable: bool = False
    children_required: bool = False
    template: list[dict] = field(default_factory=list)  # 第六章正文里给出的格式原文
    submission_mode: str = ""


@dataclass
class SpecTable:
    title: str
    note: str
    header: list[str]
    rows: list[list[str]]


@dataclass
class TechSpec:
    fixed_id: str
    file_name: str
    logical_path: str
    title: str
    model: str = ""
    conductor: str = ""
    core_section: str = ""
    voltage: str = ""
    tables: list[SpecTable] = field(default_factory=list)


@dataclass
class TenderInfo:
    project_name: str = ""
    tender_no: str = ""
    package_no: str = ""
    purchasers: list[str] = field(default_factory=list)
    agency: str = ""
    bid_deadline: str = ""
    validity_days: int = 0
    validity_end: str = ""
    publish_date: str = ""
    bid_bond_required: bool = True
    consortium_allowed: bool = True
    lots: list[TenderLot] = field(default_factory=list)
    general_qualifications: list[str] = field(default_factory=list)
    supplements: list[dict] = field(default_factory=list)
    key_clauses: list[dict] = field(default_factory=list)
    criteria: list[ScoreCriterion] = field(default_factory=list)
    scoring_weights: dict[str, dict[str, int]] = field(default_factory=dict)
    format_items: list[FormatItem] = field(default_factory=list)
    # "integrated"：格式目录取自整本招标文件的格式章节，按原文顺序编排，不分商务 / 技术两卷
    format_layout: str = ""
    format_source: str = ""  # "list"：须知中的组成清单；"platform"：格式章的电子商务平台目录
    format_source_path: str = ""
    cover_title: str = ""  # 格式章节封面上的文件名称，如“谈判响应文件”“投标文件”
    project_facts: dict = field(default_factory=dict)  # 整本招标文件的项目事实，供技术标编写器使用
    tech_specs: list[TechSpec] = field(default_factory=list)
    contract_clauses: list[dict] = field(default_factory=list)
    files: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_title(text: str) -> str:
    """去编号、括注和标点，便于标题间模糊匹配。"""
    value = re.sub(r"^[\s（(]*[\d一二三四五六七八九十]+(?:\.\d+)*[)）、.．\s]*", "", text.strip())
    value = re.sub(r"[（(][^（()）]{0,40}[)）]", "", value)
    return re.sub(r"[\s，,。.、:：；;‘’'\"“”/／\-—]+", "", value)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def classify_entry(entry: ArchiveEntry, blocks: list[DocBlock] | None) -> str:
    """按文件名、所在压缩包层级与正文特征判定文件角色。"""
    if entry.suffix in ARCHIVE_SUFFIXES:
        return ROLE_ARCHIVE
    if entry.suffix == ".sign":
        return ROLE_SIGNATURE
    if _SCORE_ATTACHMENT.fullmatch(Path(entry.name).stem):
        return ROLE_SCORING
    path_text = entry.logical_path
    head_text = blocks_to_text(blocks[:40]) if blocks else ""
    if "技术规范" in path_text or "技术参数特性表" in head_text or "技术规范专用部分" in head_text:
        return ROLE_TECH_SPEC
    if "合同" in path_text:
        return ROLE_CONTRACT
    if re.search(r"投标邀请书|招标公告", entry.name):
        return ROLE_INVITATION
    if re.search(r"招标文件|投标人须知|评标办法", entry.name) or "投标人须知" in head_text:
        return ROLE_MAIN
    if blocks and is_integrated_document(blocks):
        return ROLE_MAIN
    if "投标邀请书" in head_text[:200]:
        return ROLE_INVITATION
    return ROLE_OTHER


def _find_table(blocks: list[DocBlock], *keywords: str) -> DocBlock | None:
    for block in blocks:
        if block.kind != "table":
            continue
        header_text = " ".join(block.rows[0])
        if all(keyword in header_text for keyword in keywords):
            return block
    return None


def _parse_invitation(blocks: list[DocBlock], info: TenderInfo) -> None:
    text = blocks_to_text(blocks)
    for block in blocks[:6]:
        if block.kind == "paragraph" and block.text.endswith("投标邀请书") and len(block.text) > 12:
            info.project_name = block.text.removesuffix("投标邀请书").strip()
            break
    matched = re.search(r"招标编号[:：]\s*([A-Za-z0-9\-]+)", text)
    if matched:
        info.tender_no = matched.group(1)
    matched = re.search(
        r"截止时间[^\n。]{0,40}?(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2})[:：](\d{2})", text
    )
    if matched:
        year, month, day, hour, minute = (int(part) for part in matched.groups())
        info.bid_deadline = datetime(year, month, day, hour, minute).isoformat(timespec="minutes")
    dates = [b.text for b in blocks if b.kind == "paragraph" and re.fullmatch(r"\d{4}年\d{1,2}月\d{1,2}日", b.text)]
    if dates:
        info.publish_date = dates[-1]
    info.bid_bond_required = not re.search(r"不收取投标保证金|免收投标保证金", text)
    info.consortium_allowed = not re.search(r"不接受联合体投标", text)
    info.purchasers = list(dict.fromkeys(_clean(m) for m in re.findall(r"招标人\s*[:：]\s*([^\n]+)", text)))
    matched = re.search(r"招标代理机构[:：]\s*([^\n]+)", text)
    if matched:
        info.agency = _clean(matched.group(1))
    info.general_qualifications = _section_paragraphs(blocks, r"投标人资格要求", r"招标文件的获取")
    _parse_lots(blocks, info)
    _parse_supplements(blocks, info)


def _section_paragraphs(blocks: list[DocBlock], start_pattern: str, end_pattern: str) -> list[str]:
    result: list[str] = []
    active = False
    for block in blocks:
        if block.kind != "paragraph":
            continue
        if not active and block.level and re.search(start_pattern, block.text):
            active = True
            continue
        if active and block.level and re.search(end_pattern, block.text):
            break
        if active:
            result.append(block.text)
    return result


def _parse_lots(blocks: list[DocBlock], info: TenderInfo) -> None:
    table = _find_table(blocks, "分标编号", "分标名称", "采购数量")
    if table is None:
        return
    category = ""
    for row in table.rows[1:]:
        if len(row) < 6 or not re.search(r"[A-Za-z]+\d+-", row[2]):
            continue
        category = _clean(row[1]) or category
        info.lots.append(
            TenderLot(
                category=category,
                lot_code=_clean(row[2]),
                lot_name=_clean(row[3]),
                unit=_clean(row[4]),
                quantity=_clean(row[5]),
                package_count=_clean(row[6]) if len(row) > 6 else "",
                package_range=_clean(row[7]) if len(row) > 7 else "",
            )
        )
    special = _find_table(blocks, "分标编号", "分标名称", "资格预审")
    if special is None:
        return
    by_code = {lot.lot_code: lot for lot in info.lots}
    requirement = ""
    for row in special.rows[1:]:
        if len(row) < 4:
            continue
        requirement = _clean(row[3]) or requirement
        lot = by_code.get(_clean(row[0]))
        if lot:
            lot.prequalification = requirement


def _parse_supplements(blocks: list[DocBlock], info: TenderInfo) -> None:
    """投标邀请书“须在商务、技术投标文件中提供补充支持证明材料”的几张表。"""
    scope = ""
    for block in blocks:
        if block.kind == "paragraph":
            matched = re.match(r"^(商务|技术)部分[:：]\s*[（(](.+?)[)）]", block.text)
            scope = block.text if matched else scope if block.text.startswith(("商务部分", "技术部分")) else ""
            continue
        if not scope or "申请文件内容" not in " ".join(block.rows[0]):
            continue
        volume = "business" if scope.startswith("商务") else "technical"
        project = ""
        for row in block.rows[1:]:
            if len(row) < 3:
                continue
            project = _clean(row[0]) or project
            info.supplements.append(
                {"volume": volume, "scope": _clean(scope), "project": project, "item": _clean(row[1]), "note": _clean(row[2])}
            )
        scope = ""


def _parse_range(text: str) -> tuple[str, float | None, float | None]:
    matched = _SCORE_RANGE.search(text)
    if not matched:
        return _clean(text), None, None
    name = _clean(text[: matched.start()] + text[matched.end():])
    low, high = float(matched.group(1)), float(matched.group(2))
    return name, min(low, high), max(low, high)


def _parse_tiers(rule: str) -> list[dict]:
    """保留规则里的每个显式档位及原文条件，区间不取单点。"""
    tiers: list[dict] = []
    for clause in re.split(r"[。\n]", rule):
        end = 0
        for matched in _SCORE_TIER.finditer(clause):
            condition = _clean(clause[end:matched.start()]).strip("：:，,；; ")
            condition = re.sub(r"\s*(?:得|计)$", "", condition)
            end = matched.end()
            if matched["zero"]:
                in_marker = matched["zero"].removesuffix("不扣分").strip("，, ")
                condition = "，".join(part for part in (condition, in_marker) if part)
                tiers.append({"score": 0.0, "condition": condition})
                continue
            first = float(matched["first"])
            if matched["deduct"]:
                first = -abs(first)
            if matched["last"] is None:
                tiers.append({"score": first, "condition": condition})
                continue
            last = float(matched["last"])
            if matched["deduct"]:
                last = -abs(last)
            tiers.append({"min_score": min(first, last), "max_score": max(first, last), "condition": condition})
    return tiers


def _criteria_from_table(table: DocBlock, volume: str, category: str, prefix: str) -> list[ScoreCriterion]:
    result: list[ScoreCriterion] = []
    group = ""
    for row in table.rows[1:]:
        cells = [_clean(cell) for cell in row] + ["", "", ""]
        group_cell, factor_cell, rule_cell = cells[0], cells[1], cells[2]
        if not (group_cell or factor_cell or rule_cell):
            continue
        group = group_cell or group
        if not factor_cell:
            # 合并单元格的续行：并入上一评审要素
            if result and rule_cell:
                result[-1].rule = f"{result[-1].rule}\n{rule_cell}"
                result[-1].tiers = _parse_tiers(result[-1].rule)
            continue
        factor, low, high = _parse_range(factor_cell)
        result.append(
            ScoreCriterion(
                criterion_id=f"{prefix}-{len(result) + 1:02d}",
                volume=volume,
                category=category,
                group=_parse_range(group)[0],
                factor=factor,
                min_score=low,
                max_score=high,
                rule=rule_cell,
                tiers=_parse_tiers(rule_cell),
            )
        )
    return result


def _parse_scoring(blocks: list[DocBlock], info: TenderInfo) -> None:
    pending: tuple[str, str] | None = None
    technical_index = 0
    for block in blocks:
        if block.kind == "paragraph":
            if "商务评分标准" in block.text and len(block.text) < 40:
                pending = ("business", "")
            else:
                heading = re.sub(r"^\d+\s*[.．、]\s*", "", block.text)
                matched = re.match(r"(.{2,30}?)物资品类技术评分标准", heading)
                if matched and len(block.text) < 60:
                    pending = ("technical", matched.group(1).strip())
                elif block.level:
                    pending = None
            continue
        if pending is None or "评审要素" not in " ".join(block.rows[0]):
            continue
        volume, category = pending
        if volume == "technical":
            technical_index += 1
        prefix = "B" if volume == "business" else f"T{technical_index}"
        info.criteria.extend(_criteria_from_table(block, volume, category, prefix))
        pending = None

    by_name = {lot.lot_name: lot for lot in info.lots}
    weights = next(
        (b for b in blocks if b.kind == "table" and "权重" in " ".join(b.rows[0]) and "分标名称" in " ".join(b.rows[0])),
        None,
    )
    if weights:
        for row in weights.rows[1:]:
            lot = by_name.get(_clean(row[2])) if len(row) >= 6 else None
            if lot:
                lot.tech_weight, lot.business_weight, lot.price_weight = (_clean(c) for c in row[3:6])
    mapping = _find_table(blocks, "分标名称", "技术评分标准")
    if mapping:
        for row in mapping.rows[1:]:
            lot = by_name.get(_clean(row[1])) if len(row) >= 4 else None
            if lot:
                lot.scoring_category = _clean(row[2]).removesuffix("物资品类技术评分标准")
                lot.performance_requirement = _clean(row[3])
    award = next(
        (b for b in blocks if b.kind == "table" and "授标规则" in " ".join(" ".join(r) for r in b.rows[:12])),
        None,
    )
    if award:
        for row in award.rows:
            if len(row) == 3 and _clean(row[0]) in by_name:
                by_name[_clean(row[0])].award_rule = _clean(row[2])


def _parse_category_weights(blocks: list[DocBlock], info: TenderInfo) -> None:
    for block in blocks:
        if block.kind != "table" or tuple(_clean(cell) for cell in block.rows[0]) != _CATEGORY_WEIGHTS_HEADER:
            continue
        for row in block.rows[1:]:
            if len(row) != 2 or not row[0].strip():
                continue
            matched = re.fullmatch(r"\s*(\d+)\s*[:：]\s*(\d+)\s*[:：]\s*(\d+)\s*", row[1])
            if matched:
                technical, business, price = map(int, matched.groups())
                info.scoring_weights.setdefault(_clean(row[0]), {"technical": technical, "business": business, "price": price})


def _parse_scoring_attachments(
    attachments: list[tuple[ArchiveEntry, list[DocBlock] | None]], info: TenderInfo,
) -> None:
    seen = {(c.volume, c.category, c.group, c.factor, c.rule) for c in info.criteria}
    categories = set(info.scoring_weights) | {lot.scoring_category or lot.category for lot in info.lots}
    for index, (entry, blocks) in enumerate(attachments, 1):
        named = _SCORE_ATTACHMENT.fullmatch(Path(entry.name).stem)
        if named is None:
            continue
        volume = "business" if named["volume"] == "商务" else "technical"
        heading = _clean(named["label"]).removesuffix("物资品类").rstrip("-_ ")
        category = max(
            (name for name in categories if name and heading.endswith(name)), key=len, default="",
        ) if volume == "technical" else ""
        count = 0
        for block in blocks or []:
            if block.kind != "table":
                continue
            header = tuple(_clean(cell) for cell in block.rows[0])
            if header not in {("项目", "评审要素", "评审细则"), ("项目", "评审要素", "评审内容")}:
                continue
            parsed = _criteria_from_table(block, volume, category, f"S{index}-{count + 1}")
            count += len(parsed)
            for criterion in parsed:
                key = (criterion.volume, criterion.category, criterion.group, criterion.factor, criterion.rule)
                if key in seen:
                    continue
                seen.add(key)
                criterion.source_path = entry.logical_path
                info.criteria.append(criterion)
        if not count:
            info.warnings.append(f"评分标准附件“{entry.logical_path}”未提取到评分表，请核对原件或另存为可编辑的 .docx 后重新上传。")


def _parse_key_clauses(blocks: list[DocBlock], info: TenderInfo) -> None:
    table = _find_table(blocks, "条款号", "条款名称", "编列内容")
    if table is None:
        return
    for row in table.rows[1:]:
        if len(row) >= 3 and _clean(row[0]) and _clean(row[2]):
            info.key_clauses.append({"number": _clean(row[0]), "name": _clean(row[1]), "content": _clean(row[2])})
    text = " ".join(f"{c['name']} {c['content']}" for c in info.key_clauses)
    matched = re.search(r"投标有效期.{0,30}?(\d+)\s*[日天]", text)
    if matched:
        info.validity_days = int(matched.group(1))
    if re.search(r"免收投标保证金", text):
        info.bid_bond_required = False


def _parse_format_directory(blocks: list[DocBlock], info: TenderInfo) -> None:
    table = _find_table(blocks, "提交方式", "格式要求")
    if table is None:
        info.warnings.append("未在招标文件中找到“投标文件格式目录”，将按通用货物类投标文件结构编排。")
        return
    info.format_items = _format_directory_items(table)
    _attach_format_templates(blocks, info)


def _format_directory_items(table: DocBlock, *, platform: bool = False) -> list[FormatItem]:
    items: list[FormatItem] = []
    volumes = _PLATFORM_VOLUMES if platform else _VOLUME_TITLES
    volume = ""
    for row in table.rows[1:]:
        cells = [_clean(cell) for cell in row] + [""] * 6
        number, title = cells[0], cells[1]
        if not title or not number:
            continue
        volume_title = next((v for v in volumes if title.startswith(v)), "")
        if volume_title and re.fullmatch(r"[一二三四五六七八九十]+", number):
            volume = volume_title
            continue
        if not volume:
            continue
        ticks = [cell == "√" for cell in cells[2:5]]
        port = cells[5]
        mode = ""
        if platform:
            if "自动生成" in port:
                mode = "tool_generated"
            elif "无偏差" in port:
                mode = "conditional_deviation"
        items.append(
            FormatItem(
                number=number,
                title=title,
                volume=volume,
                level=number.count(".") + 1 if re.match(r"\d", number) else 1,
                required=ticks[0] or ticks[1],
                has_format=ticks[2],
                port=port,
                not_applicable=platform and cells[2:4] == ["×", "×"],
                submission_mode=mode,
            )
        )
    for index, item in enumerate(items):
        children = [
            other
            for other in items[index + 1:]
            if other.volume == item.volume and other.number.startswith(item.number + ".")
        ]
        item.children_required = any(child.required for child in children)
        if not platform:
            item.not_applicable = not item.required and not item.children_required
    return items


def _platform_title_key(title: str) -> str:
    title = re.sub(r"^附表\s*\d+\s*", "", title.strip())
    return normalize_title(_PLATFORM_PATH_NOTE.split(title, maxsplit=1)[0])


def _attach_platform_templates(blocks: list[DocBlock], items: list[FormatItem]) -> None:
    by_volume = {
        volume: [(item, _platform_title_key(item.title)) for item in items if item.volume == volume]
        for volume in _PLATFORM_VOLUMES
    }
    candidates: list[tuple[FormatItem, str]] = []
    current = None
    for block in blocks:
        if block.kind == "paragraph":
            key = _platform_title_key(block.text)
            if key in by_volume:
                candidates = by_volume[key]
                current = None
                continue
            if candidates and (block.level or _PLATFORM_TEMPLATE_HEADING.match(block.text)):
                scored = [(_title_similarity(key, title), item) for item, title in candidates]
                score, match = max(scored, key=lambda pair: pair[0])
                if score >= 0.6 and match is not current:
                    current = match
                    continue
        if current is not None:
            current.template.append(template_paragraph(block) if block.kind == "paragraph"
                                    else {"kind": "table", "rows": block.rows})


def _parse_platform_format_directory(blocks: list[DocBlock], info: TenderInfo, source_path: str) -> bool:
    for body in format_chapter_bodies(blocks):
        for index, table in enumerate(body):
            if table.kind != "table" or tuple(re.sub(r"\s+", "", cell) for cell in table.rows[0]) != _PLATFORM_DIRECTORY_HEADER:
                continue
            items = _format_directory_items(table, platform=True)
            if items:
                _attach_platform_templates(body[index + 1:], items)
                info.format_items = items
                info.format_source = "platform"
                info.format_source_path = source_path
                return True
    return False


def _attach_format_templates(blocks: list[DocBlock], info: TenderInfo) -> None:
    """把第六章正文中各小节的格式原文挂到目录项上。"""
    start = next(
        (i for i, b in enumerate(blocks) if b.kind == "table" and "提交方式" in " ".join(b.rows[0])), None
    )
    if start is None:
        return
    sections: list[tuple[str, str, list[dict]]] = []
    volume = ""
    for block in blocks[start + 1:]:
        if block.kind == "paragraph" and block.level and _CHAPTER_HEADING.match(block.text):
            break
        if block.kind == "paragraph" and block.level:
            title = block.text
            stripped = normalize_title(title)
            volume_title = next((v for v in _VOLUME_TITLES if stripped == v), "")
            if volume_title:
                volume = volume_title
                continue
            sections.append((volume, title, []))
            continue
        if not sections:
            continue
        body = sections[-1][2]
        if block.kind == "paragraph":
            body.append(template_paragraph(block))
        else:
            body.append({"kind": "table", "rows": block.rows})
    for item in info.format_items:
        target = normalize_title(item.title)
        best: tuple[int, list[dict], str] | None = None
        for volume_title, title, body in sections:
            if volume_title != item.volume:
                continue
            candidate = normalize_title(title)
            if not candidate:
                continue
            score = _title_similarity(target, candidate)
            if score >= 0.6 and (best is None or score > best[0]):
                best = (score, body, title)
        if best:
            item.template = best[1]
            if "不适用" in best[2]:
                item.not_applicable = True


def _title_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    short, long = sorted((left, right), key=len)
    if short in long and len(short) >= 4:
        return 0.6 + 0.4 * len(short) / len(long)
    common = len(set(short) & set(long))
    return 0.55 * common / len(set(long))


def _parse_tech_spec(entry: ArchiveEntry, blocks: list[DocBlock]) -> TechSpec:
    head = [b.text for b in blocks[:8] if b.kind == "paragraph"]
    matched = _FIXED_ID.search(entry.name) or _FIXED_ID.search(" ".join(head))
    spec = TechSpec(
        fixed_id=matched.group(1) if matched else Path(entry.name).stem,
        file_name=entry.name,
        logical_path=entry.logical_path,
        title=next((t for t in head if "编制" in t or "规范" in t), Path(entry.name).stem),
    )
    title, note = "", ""
    for block in blocks:
        if block.kind == "paragraph":
            if re.match(r"^\d+\s*[.．、]\s*\S", block.text) and len(block.text) < 40:
                title, note = re.sub(r"^\d+\s*[.．、]\s*", "", block.text), ""
            elif block.text.startswith("注"):
                note = block.text
            continue
        if not title or len(block.rows) < 2:
            continue
        spec.tables.append(SpecTable(title=title, note=note, header=block.rows[0], rows=block.rows[1:]))
        title = ""
    parameters = {
        _clean(row[1]).lstrip("※△"): _clean(row[3])
        for table in spec.tables[:1]
        for row in table.rows
        if len(row) >= 4
    }
    spec.model = parameters.get("产品型号规格", "")
    spec.conductor = parameters.get("导体材料", "")
    spec.core_section = next((v for k, v in parameters.items() if "芯数" in k and "截面" in k), "")
    voltage = re.search(r"(\d+(?:\.\d+)?)\s*kV", " ".join(parameters.values()), re.I)
    spec.voltage = f"{voltage.group(1)}kV" if voltage else ""
    return spec


def _parse_contract(blocks: list[DocBlock], info: TenderInfo) -> None:
    table = _find_table(blocks, "条款项号", "约定内容")
    if table is None:
        return
    for row in table.rows[1:]:
        if len(row) >= 4 and _clean(row[3]):
            info.contract_clauses.append({"scope": _clean(row[0]), "clause": _clean(row[2]), "content": _clean(row[3])})


def parse_tender_package(entries: list[ArchiveEntry], workspace: Path, archive_name: str) -> TenderInfo:
    """解析整个招标包。``workspace`` 为解包根目录。"""
    info = TenderInfo()
    matched = re.search(r"(包\s*\d+)", archive_name)
    info.package_no = matched.group(1).replace(" ", "") if matched else ""
    main_blocks: list[DocBlock] = []
    main_documents: list[tuple[str, list[DocBlock]]] = []
    scoring_attachments: list[tuple[ArchiveEntry, list[DocBlock] | None]] = []
    for entry in flatten_entries(entries):
        if entry.is_word_lock_file:
            continue
        blocks: list[DocBlock] | None = None
        note = ""
        if entry.suffix == ".docx":
            try:
                blocks = read_docx_blocks(workspace / entry.storage_path)
            except ValueError as exc:
                note = str(exc)
                info.warnings.append(note)
        elif entry.suffix == ".pdf":
            try:
                blocks = read_pdf_blocks(workspace / entry.storage_path)
                note = "PDF 文件已按文字识别解析；表格无框线时可能识别不全，请核对评分标准与格式目录。"
            except ValueError as exc:
                note = str(exc)
                info.warnings.append(note)
        elif entry.suffix in (".doc", ".wps"):
            note = "该格式暂不解析，已保留原件；如为关键文件请另存为 .docx 后重新上传。"
        role = classify_entry(entry, blocks)
        info.files.append(
            {
                "name": entry.name,
                "logicalPath": entry.logical_path,
                "chain": entry.chain,
                "depth": entry.depth,
                "suffix": entry.suffix,
                "size": entry.size,
                "role": role,
                "roleLabel": ROLE_LABELS[role],
                "note": note,
            }
        )
        if role == ROLE_SCORING:
            scoring_attachments.append((entry, blocks))
        if blocks is None:
            continue
        if role == ROLE_INVITATION:
            _parse_invitation(blocks, info)
        elif role == ROLE_MAIN:
            main_blocks.extend(blocks)
            main_documents.append((entry.logical_path, blocks))
        elif role == ROLE_TECH_SPEC:
            info.tech_specs.append(_parse_tech_spec(entry, blocks))
        elif role == ROLE_CONTRACT:
            _parse_contract(blocks, info)
    if main_blocks:
        _parse_key_clauses(main_blocks, info)
        _parse_scoring(main_blocks, info)
        _parse_category_weights(main_blocks, info)
        platform_format = any(_parse_platform_format_directory(blocks, info, path) for path, blocks in main_documents)
        legacy_format = not platform_format and _find_table(main_blocks, "提交方式", "格式要求") is not None
        if legacy_format:
            _parse_format_directory(main_blocks, info)
        parse_integrated_document(main_blocks, info, legacy_format=legacy_format)
        if info.format_layout == "integrated":
            info.project_facts = extract_project_facts(main_blocks, info)
        if not info.format_items:
            info.warnings.append("未在招标文件中找到“投标文件格式目录”，将按通用货物类投标文件结构编排。")
    else:
        info.warnings.append("未识别到招标文件正文（投标人须知 / 评标办法 / 投标文件格式），评分自查与格式编排将受限。")
    _parse_scoring_attachments(scoring_attachments, info)
    if not info.project_name:
        info.warnings.append("未识别到招标文件中的项目名称，请在项目信息中手工补充。")
    if not info.validity_days:
        # 原文写了天数却没取到，与原文根本没有规定有效期，是两回事
        stated = re.search(r"(?:投标|响应)(?:文件)?有效期[^。\n]{0,30}?\d+\s*(?:个)?(?:日历天|日|天)", blocks_to_text(main_blocks))
        info.warnings.append("未识别到投标有效期，请核对原文并手工补充。" if stated or not main_blocks
                             else "未识别到投标有效期：招标文件没有规定有效期天数，证件效期按投标截止日校验；招标方另有要求时请手工补充。")
    if info.bid_deadline and info.validity_days:
        deadline = datetime.fromisoformat(info.bid_deadline)
        info.validity_end = (deadline + timedelta(days=info.validity_days)).date().isoformat()
    info.tech_specs.sort(key=lambda spec: spec.fixed_id)
    return info
