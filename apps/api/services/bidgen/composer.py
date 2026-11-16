"""按招标文件“投标文件格式目录”编排整本投标文件。

目录条目 → 关键词匹配到对应生成器；匹配不到的条目按第六章给出的格式原文还原，
因此换一份招标文件、目录不同也能生成。
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable

from api.services.bidgen import business_sections as business
from api.services.bidgen import technical_sections as technical
from api.services.bidgen.document_model import (
    UNDERLINE_END,
    UNDERLINE_START,
    ComposeContext,
    Section,
    assign_numbers,
    chinese_number,
    grid_table,
    label_cell,
    paragraph,
    placeholder,
    strip_underline_marks,
    table,
    underline,
    value_cell,
)
from api.services.bidgen import business_response, generic_writer, it_writer, topic_writer
from api.services.bidgen.corpus import adapt
from api.services.bidgen.epc_writer import Draft, write_score_point
from api.services.bidgen.variation import vary_document

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


LAYOUT_INTEGRATED = "integrated"
_VOLUME_KEYS = {VOLUME_BUSINESS: "business", VOLUME_TECHNICAL: "technical"}
WRITER_DOMAINS = ("charging", "construction", "it")
# 格式章节中“根据实施方案评分标准进行编制”这类编写指引
_CRITERIA_REFERENCE = re.compile(r"根据(.{2,20}?)评分标准")
_WRITING_HINT = re.compile(r"根据.{2,20}?评分标准进行编制|^[（(]?\s*(?:格式自拟|自拟格式)\s*[）)]?[。.]?$")
_SCORE_POINT = re.compile(r"^(.*?)[。；;，,\s]*(\d+(?:\.\d+)?)\s*[-~～－—–]\s*(\d+(?:\.\d+)?)\s*分$")
_POINT_NUMBER = re.compile(r"^\s*(?:[（(]\s*\d+\s*[）)]|\d+\s*[、.．])\s*")
# 评审要点里的评价用语，作小节标题时去掉（“风险防范措施合理可行” → “风险防范措施”）
_JUDGEMENT_SUFFIX = re.compile(r"(?:[，,]\s*且措施)?(?:合理[、，,]?\s*可行的?|等方面有针对性表述)$")
_CJK_GAP = re.compile(r"(?<=[一-鿿，、。；：（）])\s+(?=[一-鿿，、。；：（）])")
# (标题关键词, 证件类别, 无原件时的粘贴提示)；自上而下首个命中生效
_INTEGRATED_CERTIFICATES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("授权委托书", ("legal_id", "agent_id"), "法定代表人及委托代理人身份证正、反面扫描件"),
    ("法定代表人身份证明", ("legal_id",), "法定代表人身份证正、反面扫描件"),
    ("营业执照", ("business_license", "bank_permit", "qualification"), "营业执照、开户许可证、资质认证证书扫描件"),
    ("保证金", ("bid_bond", "bank_permit"), "投标保证金缴纳凭证及基本账户证明扫描件"),
    ("基本情况", ("business_license", "qualification", "safety_license"), "营业执照、资质证书、安全生产许可证扫描件"),
    ("财务", ("finance_report",), "近年财务审计报告扫描件"),
    ("税收", ("tax", "social_insurance"), "依法缴纳税收和社会保障资金证明材料"),
    ("类似项目", ("performance_proof",), "类似项目合同及发票扫描件"),
)


def _join_cjk(text: str) -> str:
    """Word 转换带进来的中文字间断行空格（如“业主资 金”）。"""
    return _CJK_GAP.sub("", text.strip())


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


def _render_template(ctx: ComposeContext, template: list[dict], *, fill_parties: bool = False) -> list[dict]:
    """格式原文 → 正文块：填入投标人 / 项目 / 招标人名称，表单表格按企业资料填报。"""
    blocks: list[dict] = []
    for block in template:
        if block["kind"] == "paragraph":
            if fill_parties and _WRITING_HINT.search(block["text"]):
                continue
            # 带横线标记的原文优先：源稿填空横线保留，填入的值也加横线
            text = block.get("form") or block["text"]
            pairs = [("（投标人名称）", ctx.company_name), ("（供应商名称）", ctx.company_name)]
            if fill_parties:
                pairs += [("（项目名称）", ctx.project_name), ("（招标人名称）", ctx.purchaser_names), ("（采购人名称）", ctx.purchaser_names)]
            for label, value in pairs:
                # 原文已在标签前写好名称（“××项目（项目名称）”）时只去掉标签，避免名称重复
                if value:
                    text = re.sub(
                        rf"({re.escape(value)})([\s{UNDERLINE_START}{UNDERLINE_END}]*){re.escape(label)}",
                        lambda m: m.group(1) + re.sub(r"\s", "", m.group(2)),
                        text,
                    )
                text = text.replace(label, underline(value))
                text = _fill_label_line(ctx, text)
            if "粘帖" in text or "粘贴" in text:
                blocks.append(placeholder(strip_underline_marks(text)))
            else:
                blocks.append(paragraph(text))
        elif block["rows"]:
            width = max(len(row) for row in block["rows"])
            rows = [row + [""] * (width - len(row)) for row in block["rows"]]
            if fill_parties and _is_form_table(rows):
                blocks.append(_form_grid(ctx, rows, width))
            else:
                body = _prefill_rows(ctx, rows[0], rows[1:]) if fill_parties else rows[1:]
                blocks.append(table(rows[0], body or [[""] * width]))
    return blocks


def _blank(rows: list[list[str]]) -> bool:
    return all(not cell.strip() or re.fullmatch(r"\d{1,2}|…+|\.+", cell.strip()) for row in rows for cell in row)


def _prefill_rows(ctx: ComposeContext, header: list[str], rows: list[list[str]]) -> list[list[str]]:
    """空白的偏差表、分项报价表按采购需求预填：偏差表逐条列“无偏差”，报价表列出功能 / 内容名称（金额由投标人填写）。"""
    # 两列“标签｜内容”表：内容为空且标签可对应企业资料时填写（如报价表中的“供应商名称”）
    if len(header) == 2 and not _blank([header]) and rows:
        filled = []
        for row in [header, *rows]:
            if len(row) == 2 and row[0] and not row[1].strip():
                value = _profile_value(ctx, row[0])
                row = [row[0], value or ""]
            filled.append(row)
        if filled[0] != header:
            header[:] = filled[0]
        rows = filled[1:]
    footer = [row for row in rows if re.search(r"合计|总价|总计", "".join(row)) and sum(1 for c in row if c.strip()) <= 2]
    rows_body = [row for row in rows if row not in footer]
    if not _blank(rows_body):
        return rows
    facts = ctx.tender.get("project_facts") or {}
    tree = facts.get("requirements") or []
    joined = "".join(header)
    if "偏差" in joined or "偏离" in joined:
        sections = [node for node in tree if node["level"] == 2 and not re.search(r"概述|其他", node["title"])] or \
            [node for node in tree if node["level"] <= 3][:8]
        if not sections:
            return rows
        result = []
        for index, node in enumerate(sections, start=1):
            row = []
            for title in header:
                if "序号" in title:
                    row.append(str(index))
                elif re.search(r"采购文件|招标文件", title):
                    row.append(f"采购需求“{node['title']}”")
                elif re.search(r"响应文件|投标文件", title):
                    row.append("技术响应文件相应章节")
                elif re.search(r"偏差|偏离|说明", title):
                    row.append("无偏差")
                else:
                    row.append("")
            result.append(row)
        return result
    if "单价" in joined and re.search(r"功能|名称|内容|货物|服务|项目", joined):
        from api.services.bidgen.it_writer import ItWriter, first_sentence

        items = ItWriter(ctx, facts).modules if tree else []
        items = items or [node for node in tree if (node["texts"] or node["items"]) and node["level"] <= 4][:12]
        if not items:
            return rows
        ctx.need("分项报价金额（单价、总价、税率）")
        result = []
        for node in items:
            description = first_sentence(node["texts"][0]) if node["texts"] else "、".join(i["label"] for i in node["items"][:6])
            row = []
            for title in header:
                if re.search(r"功能|名称|内容|货物|服务|项目", title) and not row:
                    row.append(node["title"])
                elif re.search(r"描述|说明|参数|规格", title):
                    clauses = re.split(r"(?<=[，。；])", description)
                    brief = ""
                    for clause in clauses:
                        if len(brief) + len(clause) > 60 and brief:
                            break
                        brief += clause
                    row.append(brief.rstrip("，；") + ("" if brief.endswith("。") else "。"))
                elif "数量" in title:
                    row.append("1")
                elif "单位" in title:
                    row.append("项")
                else:
                    row.append("")
            result.append(row)
        return result + footer
    return rows


def _fallback_from_template(ctx: ComposeContext, section: Section, item: dict, *, fill_parties: bool = False) -> None:
    """没有专用生成器的条目：还原招标文件给出的格式，并留出粘贴位。

    ``fill_parties`` 为真时同时填入项目名称、招标人名称（整本招标文件的格式章节以此为待填项）。
    """
    if item["not_applicable"]:
        section.not_applicable = True
        section.blocks.append(paragraph("招标文件投标文件格式目录未要求提交本项内容，本项不适用。"))
        return
    section.fixed = True  # 招标文件给定的格式原文，不参与措辞变化
    section.blocks += _render_template(ctx, item.get("template", [])[:40], fill_parties=fill_parties)
    if not section.blocks:
        section.blocks += [
            paragraph(f"我方按招标文件要求提交“{section.title}”相关材料如下。"),
            placeholder(f"（此处粘贴：{section.title}相关证明材料）"),
        ]


# 格式表格 / 段落中的“标签 → 企业资料”映射：(标签正则, 取值路径, 缺失时的处理："need" 待填 / "dash" 填“—” / "blank" 留空)
_PROFILE_LABELS: tuple[tuple[str, str, str], ...] = (
    (r"投标人名称|单位名称|投标人全称|供应商名称|供应商全称", "company.name", "need"),
    (r"注册地址|地址", "company.address", "need"),
    (r"邮政编码|邮编", "company.postcode", "need"),
    (r"联系人", "company.contact", "need"),
    (r"电话|联系电话|固定电话", "company.phone", "need"),
    (r"传真", "company.fax", "dash"),
    (r"网址", "company.website", "dash"),
    (r"电子邮件|电子邮箱|邮箱", "company.email", "dash"),
    (r"组织结构", "company.orgStructure", "need"),
    (r"成立时间", "company.foundedDate", "need"),
    (r"营业执照号|统一社会信用代码", "company.creditCode", "need"),
    (r"注册资金|注册资本", "company.registeredCapital", "need"),
    (r"开户银行", "company.bank", "need"),
    (r"账号|银行账号", "company.bankAccount", "need"),
    (r"经营范围", "company.businessScope", "need"),
    (r"企业资质等级|资质等级", "company.qualificationLevel", "dash"),
    (r"员工总人数", "staff.total", "need"),
    (r"项目经理", "staff.projectManagers", "dash"),
    (r"高级职称人员", "staff.senior", "dash"),
    (r"中级职称人员", "staff.middle", "dash"),
    (r"初级职称人员", "staff.junior", "dash"),
    (r"技工", "staff.technicians", "dash"),
)
_PERSON_ROWS = {"法定代表人": "legalRep", "技术负责人": "techLead", "委托代理人": "agent"}
_PERSON_LABELS = {"姓名": "name", "技术职称": "title", "职称": "title", "职务": "title", "电话": "phone", "身份证号码": "idNumber"}
_SIGN_WORDS = re.compile(r"签章|盖章|签字|盖单位章|（签名）")


def _profile_value(ctx: ComposeContext, label: str, person: str = "") -> str | None:
    """格式标签对应的企业资料值；不认识的标签返回 None（保持原样）。"""
    key = re.sub(r"[\s：:]", "", label)
    if not key or _SIGN_WORDS.search(label):
        return None
    if person and key in _PERSON_LABELS:
        value = str(ctx.profile.get(person, {}).get(_PERSON_LABELS[key]) or "").strip()
        return value or ctx.need(f"{'法定代表人' if person == 'legalRep' else '技术负责人' if person == 'techLead' else '委托代理人'}{key}")
    for pattern, path, missing in _PROFILE_LABELS:
        if re.fullmatch(pattern, key):
            group, field_name = path.split(".")
            value = str(ctx.profile.get(group, {}).get(field_name) or "").strip()
            if value:
                return value
            return ctx.need(key) if missing == "need" else "—" if missing == "dash" else ""
    return None


def _fill_label_line(ctx: ComposeContext, text: str) -> str:
    """“投标人名称：”这类只有标签的行补上企业资料；签章行、含多个标签的行不动。"""
    # 标签后可带源稿的空白横线（“供应商名称：＿＿＿＿”），填值时替换该横线
    matched = re.fullmatch(rf"\s*([\u4e00-\u9fff ]{{2,10}})[：:]\s*(?:{UNDERLINE_START}\s*{UNDERLINE_END})?\s*", text)
    if not matched:
        return text
    value = _profile_value(ctx, matched.group(1))
    return f"{matched.group(1).strip()}：{underline(value)}" if value else text


def _is_form_table(rows: list[list[str]]) -> bool:
    """表单式表格（标签与空白交错，如投标人基本情况表）；首行全是表头的清单表不算。"""
    first = rows[0]
    return sum(1 for cell in first if not cell.strip()) >= len(first) / 2 and any(cell.strip() for cell in first)


def _form_grid(ctx: ComposeContext, rows: list[list[str]], width: int):
    """按标签填入企业资料，标签后的连续空格合并为一个填写格，版式与招标文件一致。"""
    cell_rows = []
    for row in rows:
        cells = [cell.strip() for cell in row] + [""] * (width - len(row))
        person = next((value for key, value in _PERSON_ROWS.items() if cells[0].startswith(key)), "")
        out, index = [], 0
        while index < width:
            text = cells[index]
            span = 1
            while index + span < width and not cells[index + span]:
                span += 1
            if not text:
                out.append(value_cell("", colspan=span))
                index += span
                continue
            out.append(label_cell(text))
            blanks = span - 1
            if blanks:
                value = _profile_value(ctx, text, person)
                out.append(value_cell("" if value is None else value, colspan=blanks))
            index += span
        cell_rows.append(out)
    return grid_table(cell_rows, columns=width)


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
        title = _clean_title(item["title"])
        if stack and title == parent.title and not parent.blocks and not parent.children:
            # 目录中父子条目同名（如 2.5 与 2.5.1）：正文直接写在父标题下，不再重复一层标题
            section = parent
        else:
            section = parent.add(
                Section(
                    key=f"{key}.{item['number']}",
                    title=title,
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


def _listed_title(text: str) -> str:
    """“1.需求理解”→“需求理解”；不是编号列表行返回空串。"""
    value = text.strip()
    matched = re.match(r"^\s*(?:\d{1,2}\s*[.．、]|[（(][一二三四五六七八九十]{1,2}[）)])\s*(\S.{0,30})$", value)
    if matched:
        return re.sub(r"\s", "", matched.group(1))
    # 不带编号的短标题行（如“资质证书”）：只用于与评分要素同名比对
    return re.sub(r"\s", "", value) if 2 <= len(value) <= 12 and not re.search(r"[，。；：:,、（）()]", value) else ""


def _split_by_listed(template: list[dict], factors: set[str]) -> tuple[list[dict], dict[str, list[dict]]]:
    """格式原文按与评分要素同名的小标题切段：返回 (首个小标题之前的部分, {要素: 该段原文})。"""
    prefix: list[dict] = []
    segments: dict[str, list[dict]] = {}
    current: list[dict] | None = None
    for block in template:
        name = _listed_title(block.get("text", "")) if block["kind"] == "paragraph" else ""
        if name in factors:
            current = segments.setdefault(name, [])
            continue
        (prefix if current is None else current).append(block)
    return prefix, segments


def _item_criteria(ctx: ComposeContext, item: dict) -> list[dict]:
    """格式条目对应的评分项：按条目标题或“根据××评分标准进行编制”指引匹配评审项目 / 要素名称。"""
    hint_text = " ".join(block.get("text", "") for block in item.get("template", []))
    criteria = ctx.criteria_for("business") + ctx.criteria_for("technical")
    # 格式原文逐行列出编写内容（“1.需求理解 2.总体设计方案……”）且与评分要素同名时，按原文顺序对应
    listed = [_listed_title(block.get("text", "")) for block in item.get("template", []) if block["kind"] == "paragraph"]
    by_factor = {re.sub(r"\s", "", c["factor"]): c for c in criteria}
    in_order = [by_factor[name] for name in listed if name in by_factor]
    if len(in_order) >= 2:
        return in_order
    reference = _CRITERIA_REFERENCE.search(hint_text)
    names = [re.sub(r"\s", "", name) for name in (item["title"], reference[1] if reference else "") if name]
    found = []
    for criterion in criteria:
        group, factor = re.sub(r"\s", "", criterion["group"]), re.sub(r"\s", "", criterion["factor"])
        if any(group.startswith(name) or factor.startswith(name) for name in names):
            found.append(criterion)
    return found


def _score_points(criterion: dict) -> list[tuple[str, str]]:
    """把评分项拆成 (小节标题, 评审要点)：评审标准逐行列出分值时每行一节，否则整项一节。"""
    lines = [_join_cjk(line) for line in criterion["rule"].split("\n") if line.strip()]
    matches = [_SCORE_POINT.match(line) for line in lines]
    if len(lines) >= 2 and all(matches):
        points = []
        for match, line in zip(matches, lines):
            title = _POINT_NUMBER.sub("", match[1]).rstrip("。；;，, ")
            points.append((_JUDGEMENT_SUFFIX.sub("", title) or title, line))
        return points
    return [(_join_cjk(criterion["factor"]), _join_cjk(criterion["rule"]))]


def _add_criteria_sections(ctx: ComposeContext, section: Section, criteria: list[dict], number_path: str,
                           segments: dict[str, list[dict]] | None = None) -> None:
    """按评分标准逐项列出小节（如 6.1、6.2）：有项目事实时由编写器按评审要点写出正文，否则留编写框。

    ``segments`` 为格式原文中各评分要素名下的原文段落（如“（二）业绩要求”下的业绩表），放进对应小节，
    其中照抄的评分语句不进入响应文件。
    """
    facts = ctx.tender.get("project_facts") or {}
    index = 0
    for criterion in criteria:
        segment = (segments or {}).get(re.sub(r"\s", "", criterion["factor"]), [])
        segment_tables = [block["rows"] for block in segment if block["kind"] == "table"]
        for title, rule in _score_points(criterion):
            index += 1
            draft = None
            if criterion["volume"] == "business":
                draft = business_response.write_business_point(ctx, criterion["factor"], criterion["rule"], segment_tables)
            if draft is None and facts:
                domain = facts.get("domain")
                if domain == "it":
                    draft = it_writer.write_score_point(ctx, facts, title, criterion["group"])
                elif domain in ("charging", "construction"):
                    draft = write_score_point(ctx, facts, title, criterion["group"])
                if draft is None and not re.search(r"报价|价格", criterion["group"] + criterion["factor"]):
                    # 没有专门写法的行业 / 评分点：通用编写器按评分要点与采购需求逐条响应
                    draft = generic_writer.write_score_point(ctx, facts, title, rule)
            if draft is not None and criterion["volume"] != "business":
                record = generic_writer.ensure_coverage(ctx, facts, draft, rule)
                ctx.coverage.append({"factor": title, **record})
                _add_corpus_experience(ctx, draft, title, rule)
            if segment:
                kept = [block for block in segment if block["kind"] == "paragraph" and not business_response.is_scoring_text(block["text"])
                        and not re.match(r"^\s*(?:\d+[.．、]|备注|注[:：])", block["text"])]
                if draft is None or criterion["volume"] != "business":
                    kept = [block for block in segment if not (block["kind"] == "paragraph" and business_response.is_scoring_text(block["text"]))]
                notes = _render_template(ctx, kept, fill_parties=True)
                if draft is not None and criterion["volume"] == "business":
                    # 商务评分项：先按评分标准响应，格式原文中的说明（如业绩表注）放在响应之后
                    draft = Draft(blocks=draft.blocks + notes, children=draft.children, title=draft.title)
                else:
                    draft = Draft(blocks=notes + (draft.blocks if draft else []), children=draft.children if draft else [],
                                  title=draft.title if draft else "")
            heading = draft.title if draft and draft.title and len(title) > 18 else title
            child = section.add(Section(
                key=f"{section.key}.c{index}", title=_clean_title(heading), level=section.level + 1,
                number=f"{number_path}.{index} ", criteria=[criterion["criterion_id"]],
            ))
            if draft is None:
                child.fixed = True
                child.blocks.append(placeholder(f"（此处编写：{title}。评审要点：{rule}）", height=8.0))
                continue
            child.blocks += draft.blocks
            for sub_index, (sub_title, blocks) in enumerate(draft.children, start=1):
                grandchild = child.add(Section(
                    key=f"{child.key}.{sub_index}", title=sub_title, level=child.level + 1,
                    number=f"{number_path}.{index}.{sub_index} ",
                ))
                grandchild.blocks += blocks


def _add_corpus_experience(ctx: ComposeContext, draft: Draft, title: str, rule: str) -> None:
    """历史标书段落库中有同类章节时，作为“结合我方同类项目经验”补充（替换为本项目名称），与已写内容重复的不再加入。"""
    index = ctx.options.get("_corpus")
    if index is None:
        return
    section = index.best_for(title, rule)
    if section is None:
        return
    values = {"project": ctx.project_name, "owner": ctx.purchaser_names, "company": ctx.company_name}
    existing = generic_writer._draft_text(draft)
    paragraphs = [text for text in adapt(section, index.sources.get(section["source"]), values)
                  if generic_writer.similarity(text, existing) < 0.8][:8]
    if paragraphs:
        draft.children.append(("结合我方同类项目经验", [paragraph(text) for text in paragraphs]))
        ctx.corpus_hits.append({"factor": title, "section": section["title"], "source": index.sources.get(section["source"], {}).get("name", "")})


def _add_integrated_certificates(ctx: ComposeContext, section: Section, title: str) -> None:
    for keyword, categories, hint in _INTEGRATED_CERTIFICATES:
        if keyword in title:
            section.blocks += business.certificate_blocks(ctx, categories, hint)
            return


def _build_integrated(ctx: ComposeContext) -> list[Section]:
    """整本招标文件的格式章节：按原文顺序、原编号层级编排，每个一级条目一章。

    一级条目仍按商务 / 技术归属打 key 前缀（``business.1``、``technical.6``），供分卷输出与自查使用。
    """
    roots: list[Section] = []
    items = _directory(ctx)
    used_keys: set[str] = set()
    for item in items:
        if item["level"] != 1:
            continue
        # 编号照招标文件原文（如两个“二、”是二选一的格式），原文编号不是数字时按顺序编号
        top_index = int(item["number"]) if item["number"].isdigit() else len(roots) + 1
        key = f"{_VOLUME_KEYS[item['volume']]}.{item['number']}"
        if key in used_keys:
            key = f"{key}-{len(roots) + 1}"
        used_keys.add(key)
        root = Section(
            key=key, title=_clean_title(item["title"]), level=1,
            number=f"{chinese_number(top_index)}、", source=item["number"], page_break=True,
        )
        roots.append(root)
        children = [other for other in items if other["level"] == 2 and other["number"].startswith(item["number"] + ".")]
        targets = [(root, item, str(top_index))] if not children else []
        for child_index, child_item in enumerate(children, start=1):
            child = root.add(Section(
                key=f"{root.key}.{child_index}", title=_clean_title(child_item["title"]), level=2,
                number=f"（{chinese_number(child_index)}）", source=child_item["number"], page_break=True,
            ))
            targets.append((child, child_item, f"{top_index}.{child_index}"))
        for section, source_item, number_path in targets:
            ctx.within_section(section.key)
            criteria = _item_criteria(ctx, source_item)
            _fallback_from_template(ctx, section, source_item, fill_parties=True)
            if criteria:
                # 格式原文只有编写指引时，兜底的“提交材料 + 粘贴框”由评分小节取代
                fallback_texts = {f"我方按招标文件要求提交“{section.title}”相关材料如下。",
                                  f"（此处粘贴：{section.title}相关证明材料）"}
                factors = {re.sub(r"\s", "", c["factor"]) for c in criteria}
                prefix, segments = _split_by_listed(source_item.get("template", []), factors)
                if segments:
                    # 格式原文按评分要素分段：各段放到对应评分小节下，本节只保留首段说明
                    section.blocks = [block for block in _render_template(ctx, prefix, fill_parties=True)
                                      if not business_response.is_scoring_text(block.get("text", ""))]
                section.blocks = [block for block in section.blocks if block.get("text") not in fallback_texts
                                  and _listed_title(block.get("text", "")) not in factors]
                if "承诺" in source_item["title"] and (ctx.tender.get("project_facts") or {}).get("domain") in ("charging", "construction"):
                    section.blocks.insert(0, paragraph("为充分响应招标文件要求，我方就本项目作出如下服务承诺。以下承诺均为我方投标文件的实质性内容，中标后与合同具有同等法律效力。"))
                _add_criteria_sections(ctx, section, criteria, number_path, segments)
            _add_integrated_certificates(ctx, section, source_item["title"])
    return roots


def _fill_listed(ctx: ComposeContext, writer: topic_writer.ListedWriter, section: Section, item: dict, criteria: list[dict]) -> None:
    """清单条目正文：有对应写法的按写法编写，招标文件给了格式原文的照原文，其余留粘贴位。"""
    ctx.within_section(section.key)
    section.criteria = [criterion["criterion_id"] for criterion in criteria]
    draft = topic_writer.write_item(writer, item, criteria)
    if draft is None and not item.get("template") and item["volume"] == VOLUME_TECHNICAL:
        draft = generic_writer.write_score_point(ctx, writer.facts, item["title"], item["title"])
    if draft is None:
        _fallback_from_template(ctx, section, item, fill_parties=True)
        return
    section.blocks += draft.blocks
    for index, (title, blocks) in enumerate(draft.children, start=1):
        child = section.add(Section(key=f"{section.key}.{index}", title=title, level=section.level + 1, blocks=list(blocks)))
        child.fixed = any(block["type"] == "signature" for block in blocks)  # 签署页是固定格式，措辞不变
    section.fixed = any(block["type"] == "signature" for block in section.blocks)


def _build_listed(ctx: ComposeContext) -> list[Section]:
    """清单式目录（“商务标书 / 技术标书应包括下列内容”）：两卷各自按清单原顺序、原编号编排。

    两卷的条目都从 1 编号，所以各成一章（``business`` / ``technical``），完整版里编号不会重复。
    """
    items = _directory(ctx)
    writer = topic_writer.ListedWriter(ctx, ctx.tender.get("project_facts") or {})
    owned = topic_writer.assign_criteria(items, ctx.criteria_for("business") + ctx.criteria_for("technical"))
    roots: list[Section] = []
    for volume, key, title in ((VOLUME_BUSINESS, "business", "商务标书"), (VOLUME_TECHNICAL, "technical", "技术标书")):
        entries = [item for item in items if item["volume"] == volume]
        if not entries:
            continue
        root = Section(key=key, title=title, level=1, page_break=True)
        roots.append(root)
        for item in entries:
            if item["level"] != 1:
                continue
            section = root.add(Section(key=f"{key}.{item['number']}", title=_clean_title(item["title"]), level=2,
                                       source=item["number"], page_break=True))
            children = [other for other in entries if other["level"] == 2 and other["number"].startswith(item["number"] + ".")]
            if not children:
                _fill_listed(ctx, writer, section, item, owned.get((volume, item["number"]), []))
            for child_item in children:
                child = section.add(Section(key=f"{key}.{child_item['number']}", title=_clean_title(child_item["title"]), level=3,
                                            source=child_item["number"]))
                _fill_listed(ctx, writer, child, child_item, owned.get((volume, child_item["number"]), []))
    return roots


def _score_range_text(min_score: float | None, max_score: float | None) -> str:
    """分值范围文本；整本招标文件中“（6分）”这类定额评分项只有上限，没有下限。"""
    if max_score is None:
        return ""
    if min_score is None:
        return f"{max_score:g}"
    return f"{min_score:g}～{max_score:g}"


def _build_score_index(ctx: ComposeContext, roots: list[Section]) -> Section:
    """附录：评分标准响应索引表，评审专家可按评分项直接定位响应章节。"""
    section = Section(key="index", title="附录  评分标准响应索引", level=1, page_break=True)
    chapter = "" if ctx.tender.get("format_source") == "list" else "第三章"  # 清单式招标文件不分章
    section.blocks.append(paragraph(f"为便于评标委员会评审，现将招标文件{chapter}评标办法中的商务、技术评审要素与本投标文件的响应章节对照如下。"))
    located: dict[str, list[str]] = {}
    for root in roots:
        for node in root.walk():
            for criterion_id in node.criteria:
                located.setdefault(criterion_id, []).append(f"{root.title} {node.heading}".strip())
    for volume, label in (("business", "商务评分标准响应索引"), ("technical", "技术评分标准响应索引")):
        rows = []
        for index, criterion in enumerate(ctx.criteria_for(volume), start=1):
            score = _score_range_text(criterion["min_score"], criterion["max_score"])
            where = "；".join(dict.fromkeys(located.get(criterion["criterion_id"], []))) or "由招标人依据系统数据评定，无需投标文件响应"
            rows.append([str(index), criterion["group"], criterion["factor"], score, where])
        if rows:
            child = section.add(Section(key=f"index.{volume}", title=label, level=2))
            child.blocks.append(table(["序号", "评审项目", "评审要素", "分值范围", "响应章节"], rows, widths=[1.2, 3.4, 4.4, 2.0, 5.4]))
    return section


def volume_of(root: Section) -> str:
    """一级章节所属分卷：business / technical / index（整本格式下 key 形如 ``business.1``）。"""
    return root.key.split(".", 1)[0]


def compose_document(ctx: ComposeContext, seed: int | None = None) -> list[Section]:
    """生成整本投标文件的章节树（商务文件、技术文件、评分索引）。

    ``seed`` 决定叙述性文字的措辞：不传时使用默认表述，传入不同的值得到说法不同、事实一致的文稿。
    """
    ctx.rng = random.Random(seed) if seed is not None else None
    if ctx.tender.get("format_layout") == LAYOUT_INTEGRATED and ctx.tender.get("format_items"):
        roots = _build_listed(ctx) if ctx.tender.get("format_source") == "list" else _build_integrated(ctx)
    else:
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
    if ctx.rng:
        vary_document(roots, ctx.rng)
    ctx.options = {k: v for k, v in ctx.options.items() if not k.startswith("_")}
    return roots
