"""按招标文件“投标文件格式目录”编排整本投标文件。

目录条目 → 关键词匹配到对应生成器；匹配不到的条目按第六章给出的格式原文还原，
因此换一份招标文件、目录不同也能生成。
"""

from __future__ import annotations

import re
from collections.abc import Callable

from api.services.bidgen import business_sections as business
from api.services.bidgen import technical_sections as technical
from api.services.bidgen.document_model import (
    ComposeContext,
    Section,
    assign_numbers,
    paragraph,
    placeholder,
    table,
)

Builder = Callable[[ComposeContext, Section], None]

VOLUME_BUSINESS = "商务文件"
VOLUME_TECHNICAL = "技术文件"

# (目录标题关键词, 生成器)；自上而下首个命中生效
_BUSINESS_BUILDERS: tuple[tuple[str, Builder], ...] = (
    ("商务偏差", business.build_business_deviation),
    ("保证金", business.build_bid_bond),
    ("基本情况", business.build_basic_info),
    ("营业执照", business.build_business_license),
    ("税务登记", business.build_tax),
    ("查询报告", business.build_credit_reports),
    ("资格要求的证明文件", business.build_qualification),
    ("财务状况", business.build_finance),
    ("商务支撑", business.build_business_supplements),
    ("名称变更", business.build_name_change),
    ("凭证单", business.build_finance_voucher),
    ("专用章授权", business.build_seal_authorization),
    ("人员关系", business.build_relation_statement),
    ("授权委托书", business.build_authorization),
    ("其他", business.build_business_other),
)
_TECHNICAL_BUILDERS: tuple[tuple[str, Builder], ...] = (
    ("技术偏差", technical.build_technical_deviation),
    ("业绩", technical.build_performance),
    ("技术特性参数", technical.build_parameter_tables),
    ("组件材料", technical.build_component_tables),
    ("资格要求的证明文件", technical.build_technical_qualification),
    ("技术支撑", technical.build_technical_supplements),
    ("其他应提交的技术文件", technical.build_other_technical_documents),
    ("技术服务", technical.build_technical_service),
    ("售后服务", technical.build_after_sales),
    ("质量保证", technical.build_quality_assurance),
    ("承诺函", technical.build_commitment_letter),
    ("检测检验报告", technical.build_test_reports),
    ("资格预审结果", technical.build_prequalification_notice),
    ("名称变更", business.build_name_change),
    ("其他", technical.build_technical_other),
)

# 招标文件没有格式目录时的通用货物类目录
_DEFAULT_DIRECTORY = (
    (VOLUME_BUSINESS, "1", "商务偏差表"),
    (VOLUME_BUSINESS, "2", "补充文件"),
    (VOLUME_BUSINESS, "2.1", "投标保证金"),
    (VOLUME_BUSINESS, "2.2", "投标人基本情况表"),
    (VOLUME_BUSINESS, "2.3", "企业法人营业执照（扫描件）"),
    (VOLUME_BUSINESS, "2.4", "有效的税务登记证明（扫描件）"),
    (VOLUME_BUSINESS, "2.5", "符合招标文件投标人资格要求的证明文件"),
    (VOLUME_BUSINESS, "2.6", "财务状况"),
    (VOLUME_BUSINESS, "2.7", "其他"),
    (VOLUME_BUSINESS, "3", "法定代表人（单位负责人）授权委托书"),
    (VOLUME_TECHNICAL, "1", "技术偏差表"),
    (VOLUME_TECHNICAL, "2", "专项投标文件"),
    (VOLUME_TECHNICAL, "2.1", "技术特性参数表"),
    (VOLUME_TECHNICAL, "2.2", "货物组件材料配置表"),
    (VOLUME_TECHNICAL, "2.3", "技术服务，包括培训、安装指导等"),
    (VOLUME_TECHNICAL, "2.4", "售后服务保障及承诺"),
    (VOLUME_TECHNICAL, "2.5", "质量保证措施方案"),
    (VOLUME_TECHNICAL, "2.6", "其他"),
)


def _directory(ctx: ComposeContext) -> list[dict]:
    items = [i for i in ctx.tender.get("format_items", []) if i["volume"] in (VOLUME_BUSINESS, VOLUME_TECHNICAL)]
    if items:
        return items
    return [
        {"volume": volume, "number": number, "title": title, "level": number.count(".") + 1, "required": True,
         "has_format": False, "not_applicable": False, "children_required": True, "template": []}
        for volume, number, title in _DEFAULT_DIRECTORY
    ]


def _clean_title(title: str) -> str:
    """目录标题去掉“（格式）”“（如有）”等填表提示；过长的标题只保留主干。"""
    value = re.sub(r"[（(](?:格式|如有|如需|扫描件)[)）]", "", title).strip()
    value = re.sub(r"^XX年", "", value)
    if "名称变更" in value and len(value) > 30:
        return "投标人名称变更说明及证明材料"
    if len(value) > 30:
        value = re.sub(r"[（(][^（()）]*[)）]?", "", value).strip() or value
    return value[:40] + "……" if len(value) > 42 else value


def _pick_builder(volume: str, title: str) -> Builder | None:
    builders = _BUSINESS_BUILDERS if volume == VOLUME_BUSINESS else _TECHNICAL_BUILDERS
    return next((builder for keyword, builder in builders if keyword in title), None)


def _fallback_from_template(ctx: ComposeContext, section: Section, item: dict) -> None:
    """没有专用生成器的条目：还原招标文件给出的格式，并留出粘贴位。"""
    if item["not_applicable"]:
        section.not_applicable = True
        section.blocks.append(paragraph("招标文件投标文件格式目录未要求提交本项内容，本项不适用。"))
        return
    for block in item.get("template", [])[:40]:
        if block["kind"] == "paragraph":
            text = block["text"].replace("（投标人名称）", ctx.company_name)
            section.blocks.append(placeholder(text) if "粘帖" in text or "粘贴" in text else paragraph(text))
        elif block["rows"]:
            width = max(len(row) for row in block["rows"])
            rows = [row + [""] * (width - len(row)) for row in block["rows"]]
            section.blocks.append(table(rows[0], rows[1:] or [[""] * width]))
    if not section.blocks:
        section.blocks += [
            paragraph(f"我方按招标文件要求提交“{section.title}”相关材料如下。"),
            placeholder(f"（此处粘贴：{section.title}相关证明材料）"),
        ]


def _build_volume(ctx: ComposeContext, volume: str, key: str, title: str) -> Section:
    root = Section(key=key, title=title, level=1, page_break=True, number="")
    root.blocks += [
        paragraph(f"招标编号：{ctx.tender_no}", indent=False),
        paragraph(f"所投分标：{ctx.lot_names}", indent=False),
        paragraph(f"投标人：{ctx.company_name}（盖单位章）", indent=False),
    ]
    ctx.options[f"_{key}_root"] = root
    stack: list[tuple[int, Section]] = []
    deferred: list[tuple[Builder, Section]] = []
    skipped_prefixes: list[str] = []
    items = [i for i in _directory(ctx) if i["volume"] == volume]
    for item in items:
        if item["number"].startswith(tuple(skipped_prefixes)) and skipped_prefixes:
            continue
        level = min(item["level"], 3)
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = stack[-1][1] if stack else root
        section = parent.add(
            Section(
                key=f"{key}.{item['number']}",
                title=_clean_title(item["title"]),
                level=parent.level + 1,
                source=item["number"],
                page_break=level == 1,
            )
        )
        stack.append((level, section))
        has_children = any(other["number"].startswith(item["number"] + ".") for other in items)
        builder = _pick_builder(volume, item["title"])
        ctx.within_section(section.key)
        if has_children and builder is business.build_bid_bond:
            builder(ctx, section)
            if section.not_applicable:
                skipped_prefixes.append(item["number"] + ".")  # 保证金不适用时子项不再逐个展开
        elif has_children:
            continue  # 容器条目（补充文件 / 专项投标文件等）：正文由子项承担
        elif builder is None:
            _fallback_from_template(ctx, section, item)
        elif builder in (business.build_business_other, technical.build_technical_other):
            deferred.append((builder, section))  # 依赖前面各节已响应的评分项，最后生成
        else:
            builder(ctx, section)
    for builder, section in deferred:
        ctx.within_section(section.key)
        builder(ctx, section)
    return root


def _build_score_index(ctx: ComposeContext, roots: list[Section]) -> Section:
    """附录：评分标准响应索引表，评审专家可按评分项直接定位响应章节。"""
    section = Section(key="index", title="附录  评分标准响应索引", level=1, page_break=True)
    section.blocks.append(paragraph("为便于评标委员会评审，现将招标文件第三章评标办法中的商务、技术评审要素与本投标文件的响应章节对照如下。"))
    located: dict[str, list[str]] = {}
    for root in roots:
        for node in root.walk():
            for criterion_id in node.criteria:
                located.setdefault(criterion_id, []).append(f"{root.title} {node.heading}".strip())
    for volume, label in (("business", "商务评分标准响应索引"), ("technical", "技术评分标准响应索引")):
        rows = []
        for index, criterion in enumerate(ctx.criteria_for(volume), start=1):
            score = "" if criterion["max_score"] is None else f"{criterion['min_score']:g}～{criterion['max_score']:g}"
            where = "；".join(dict.fromkeys(located.get(criterion["criterion_id"], []))) or "由招标人依据系统数据评定，无需投标文件响应"
            rows.append([str(index), criterion["group"], criterion["factor"], score, where])
        if rows:
            child = section.add(Section(key=f"index.{volume}", title=label, level=2))
            child.blocks.append(table(["序号", "评审项目", "评审要素", "分值范围", "响应章节"], rows, widths=[1.2, 3.4, 4.4, 2.0, 5.4]))
    return section


def compose_document(ctx: ComposeContext) -> list[Section]:
    """生成整本投标文件的章节树（商务文件、技术文件、评分索引）。"""
    roots = [
        _build_volume(ctx, VOLUME_BUSINESS, "business", "第一部分  商务文件"),
        _build_volume(ctx, VOLUME_TECHNICAL, "technical", "第二部分  技术文件"),
    ]
    if ctx.tender.get("criteria") and ctx.options.get("includeScoreIndex", True):
        roots.append(_build_score_index(ctx, roots))
    for root in roots:
        for node in root.walk():
            if node.not_applicable and "不适用" not in node.title:
                node.title += "（不适用）"
        assign_numbers(root.children)
    ctx.options = {k: v for k, v in ctx.options.items() if not k.startswith("_")}
    return roots
