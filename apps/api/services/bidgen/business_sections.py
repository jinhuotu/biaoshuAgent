"""商务文件各章节的内容生成规则。"""

from __future__ import annotations

import re

from api.services.bidgen.document_model import (
    ComposeContext,
    Section,
    form_table,
    image,
    paragraph,
    placeholder,
    table,
    title_line,
)
from api.services.bidgen.profile import CERT_CATEGORIES

NO_DEVIATION = "完全响应，无偏差"

# 通用资格要求 → 响应说明（按关键词匹配，未命中的走兜底句）
_QUALIFICATION_RESPONSES = (
    ("同一人或者存在控股、管理关系", "与我公司法定代表人为同一人或存在控股、管理关系的其他单位，均未参加本标包投标。"),
    ("财务状况", "我公司财务状况和商业信用良好，近年财务会计报表经会计师事务所审计，未处于被责令停业、财产被接管、冻结或破产状态。"),
    ("供应商不良行为", "我公司不存在被暂停或取消中标资格且在处理有效期内的不良行为，未触发安全及质量重大问题“熔断机制”。"),
    ("失信被执行人", "我公司未被人民法院列为失信被执行人，未被“信用中国”网站列入严重失信主体名单，查询结果附后。"),
    ("经营异常名录", "我公司未被列入严重违法失信企业名单（黑名单）或经营异常名录，国家企业信用信息公示系统查询报告附后。"),
    ("外购外协供应商或代理", "我公司所选配的外购外协供应商均不在国家电网有限公司及相关省公司不良行为处理通报的处理期内。"),
    ("缺陷责任期", "我公司未选配缺陷责任期内发生过产品质量问题并导致设备停运检修的组件材料制造商的产品。"),
    ("合并、分立、破产", "资格预审结果有效期内我公司未发生合并、分立、破产等重大变化，持续具备资格预审文件与招标文件规定的资格条件。"),
    ("贴牌代工", "我公司为投标产品制造商，具有生产投标产品所需的生产场地、生产设备、生产人员及检测能力，不存在贴牌代工、分包、转包行为。"),
    ("行贿犯罪", "我公司及法定代表人无行贿犯罪记录，严格遵守“投标函”各项承诺。"),
    ("进厂验收", "我公司建有完整的外购原材料进厂检验制度，配备相应检测手段，供应商随货提供检测合格证明，原材料组部件管理具有可追溯性。"),
    ("未因该材料原因出现过质量问题", "我公司提供的同类材料未因材料原因出现过质量问题。"),
    ("批量退货", "我公司在国内材料销售合同执行过程中，未发生因严重质量问题造成的批量退货或严重影响施工的情形。"),
    ("交付拖延", "我公司在国内材料销售合同执行过程中，未发生因货物或图纸交付拖延而严重影响施工和工程进度的情形。"),
    ("投运数量或供货数量", "我公司投标产品的供货数量及成功运行时间满足招标文件要求，业绩证明材料已在资格预审申请文件中提交。"),
    ("试验报告", "我公司投标产品已取得国家授权的专业检测机构出具的有效型式试验报告，报告结论数据满足本次招标技术规范要求。"),
    ("工艺文件、作业指导书", "我公司具有生产招标产品所需各工序的工艺文件和作业指导书，各工序按工艺文件执行并具有可追溯性。"),
    ("出厂检测能力", "我公司具有生产招标产品所需的出厂检测能力，试验场地、试验设备和试验人员配置齐全。"),
    ("中介机构", "本次投标文件由我公司自行编制，未委托中介机构或中间人编制投标文件或代办投标事宜。"),
    ("联合体", "我公司以独立法人身份参加本次投标，未组成联合体。"),
    ("转包、分包", "我公司承诺中标后不进行任何转包、违规分包，不委托其他供应商贴牌代工，也不承接其他中标人的贴牌代工。"),
    ("资格预审", "我公司所投标包对应的预审标段均已在资格预审中合格，资格预审结果通知书附于技术文件。"),
)

# 商务评分要素 → 证明材料清单
_BUSINESS_EVIDENCE = (
    ("经营状况", ["近三年经审计的财务会计报表（资产负债表、利润表、现金流量表及附注）", "银行资信证明（如有）"]),
    ("售后服务", ["售后服务网点一览表（省份、地址、负责人、电话）", "服务网点营业执照或租赁协议（如有）"]),
    ("应急保供", ["应急抢险保电物资供货证明", "项目单位出具的感谢信、表扬函"]),
    ("绿色发展规划", ["《绿色发展规划》制度文件", "能源管理体系认证证书", "质量、环境、职业健康安全管理体系认证证书"]),
    ("环境影响评价", ["ESG（环境、社会和公司治理）报告及发布途径截图", "环评 / 能评报告及审核批复", "废水 / 废气 / 废固检测报告"]),
    ("绿电绿证", ["绿色电力证书或绿电交易凭证"]),
    ("企业创新成果", ["近3年国家级 / 省级科技创新成果证书"]),
    ("研发团队", ["高级及以上职称人员、高级技师名单", "职称证书及社保缴纳证明"]),
    ("科研经费", ["近3年研发费用专项审计报告或财务报表研发费用明细"]),
    ("高新技术企业", ["高新技术企业证书"]),
    ("创新激励", ["《研发创新激励管理办法》等制度文件", "供应链保障措施及风险应对预案"]),
    ("数智化", ["智能制造示范工厂、数字化车间、智能工厂等认定文件"]),
    ("质量奖", ["国家级质量奖获奖证书（如有）"]),
    ("专精特新", ["专精特新企业认定证书或公示文件"]),
    ("实体清单", ["应对国际贸易壁垒举措说明", "财政补贴批文、税收优惠证明、融资协议等"]),
)


def _child(parent: Section, key: str, title: str, *blocks: dict, criteria: list[str] | None = None) -> Section:
    return parent.add(
        Section(key=f"{parent.key}.{key}", title=title, level=parent.level + 1, blocks=list(blocks), criteria=criteria or [])
    )


def _find_criteria(ctx: ComposeContext, volume: str, *keywords: str) -> list[dict]:
    return [c for c in ctx.criteria_for(volume) if any(k in c["factor"] for k in keywords)]


def _certificates(ctx: ComposeContext, *categories: str) -> list[dict]:
    return [c for c in ctx.profile["certificates"] if c["category"] in categories]


def _validity_text(certificate: dict) -> str:
    if certificate["perpetual"]:
        return "长期有效"
    return f"有效期至 {certificate['validTo']}" if certificate["validTo"] else "有效期见证书"


def certificate_blocks(ctx: ComposeContext, categories: tuple[str, ...], hint: str) -> list[dict]:
    """有扫描件就贴图，没有就留虚线粘贴框。"""
    blocks: list[dict] = []
    for certificate in _certificates(ctx, *categories):
        caption = f"{certificate['name']}（{_validity_text(certificate)}）"
        path = ctx.certificate_files.get(certificate["id"])
        if path:
            blocks.append(image(path, caption))
        else:
            blocks.append(placeholder(f"（此处粘贴：{caption} 扫描件）"))
    if not blocks:
        blocks.append(placeholder(f"（此处粘贴：{hint}）"))
    return blocks


# ---------------- 各目录项的生成器 ----------------


def build_business_deviation(ctx: ComposeContext, section: Section) -> None:
    validity = ctx.tender.get("validity_days") or 90
    rows = [
        ["1", "投标人须知前附表 3.3.1", f"投标有效期：自投标截止日起{validity}日", f"投标有效期为自投标截止日起{validity}日", NO_DEVIATION],
        ["2", "第一章 投标邀请书 第2条", "交货时间、交货地点以供货单约定为准", "按招标文件要求及供货单约定执行", NO_DEVIATION],
        ["3", "投标人须知前附表 3.2.5", "报价方式为单价报价，投标报价为固定不变价格", "按单价报价，报价为固定不变价格，包含运至指定交货地点的全部费用", NO_DEVIATION],
        ["4", "第四章 合同条款及格式", "合同通用条款、专用条款及附件", "完全接受招标文件合同条款及格式", NO_DEVIATION],
    ]
    for clause in ctx.tender.get("contract_clauses", [])[:3]:
        rows.append([str(len(rows) + 1), f"合同专用条款 {clause['clause']}".strip(" -"), clause["content"][:120], "完全接受该约定", NO_DEVIATION])
    rows.append([str(len(rows) + 1), "招标文件其余商务条款", "除上述条款外的全部商务条款", "全部接受并响应", NO_DEVIATION])
    section.blocks += [
        title_line("商务偏差表"),
        paragraph(f"项目名称：{ctx.project_name}", indent=False),
        paragraph(f"招标编号：{ctx.tender_no}　　分标名称：{ctx.lot_names}", indent=False),
        paragraph(f"分标编号：{ctx.lot_codes}　　包号：{ctx.package_no}", indent=False),
        table(["序号", "招标文件条目号", "招标文件条款", "投标文件条款", "偏差说明"], rows, widths=[1.2, 3.4, 4.6, 4.6, 2.6]),
        paragraph("投标人声明：针对本招标标的，除本表已列明偏差外，我们接受招标文件规定的其余全部商务条件，并承诺按照招标文件规定的商务条件提供对应服务。"),
        paragraph("说明：我方对招标文件的商务条款完全响应，无任何商务偏差。"),
        ctx.signature_block(),
    ]


def build_bid_bond(ctx: ComposeContext, section: Section) -> None:
    if ctx.tender.get("bid_bond_required"):
        section.blocks += [
            paragraph("我方已按招标文件要求提交投标保证金，相关凭证如下。"),
            placeholder("（此处粘贴：投标保证金汇款凭证 / 银行保函 / 投标保证保险保单扫描件）"),
            placeholder("（此处粘贴：基本账户开户许可证或《基本存款账户信息》扫描件）"),
        ]
        return
    section.not_applicable = True
    clause = ctx.clause("投标保证金")
    basis = f"投标人须知前附表第 {clause['number']} 条载明“{clause['content']}”" if clause else "招标文件明确本次招标不收取投标保证金"
    section.blocks += [
        paragraph(f"根据招标文件第一章投标邀请书“本次招标不收取投标保证金，投标保证金相关内容均不适用”之约定，以及{basis}，我方无需提交投标保证金及银行保函、投标保证保险、汇款凭证、基本账户证明和投标保证金明细表等材料。"),
        paragraph("本项及其下属各子项（投标保证保险、汇款凭证、银行保函、银行基本账户证明、投标保证金明细表）均不适用。"),
    ]


def build_basic_info(ctx: ComposeContext, section: Section) -> None:
    company, legal, tech, staff = ctx.company, ctx.profile["legalRep"], ctx.profile["techLead"], ctx.profile["staff"]
    rows = [
        ["投标人名称", ctx.company_name, "统一社会信用代码", ctx.value(company["creditCode"], "统一社会信用代码")],
        ["注册地址", ctx.value(company["address"], "注册地址"), "邮政编码", ctx.value(company["postcode"], "邮政编码")],
        ["联系人", ctx.value(company["contact"], "联系人"), "电话", ctx.value(company["phone"], "联系电话")],
        ["传真", company["fax"] or "—", "网址", company["website"] or "—"],
        ["组织结构", ctx.value(company["orgStructure"], "组织结构"), "企业资质等级", company["qualificationLevel"] or "—"],
        ["法定代表人（单位负责人）", ctx.value(legal["name"], "法定代表人姓名"), "技术职称 / 电话", f"{legal['title'] or '—'} / {ctx.value(legal['phone'], '法定代表人电话')}"],
        ["技术负责人", ctx.value(tech["name"], "技术负责人姓名"), "技术职称 / 电话", f"{tech['title'] or '—'} / {ctx.value(tech['phone'], '技术负责人电话')}"],
        ["成立时间", ctx.value(company["foundedDate"], "成立时间"), "注册资金", ctx.value(company["registeredCapital"], "注册资金")],
        ["员工总人数", ctx.value(staff["total"], "员工总人数"), "其中：项目经理", staff["projectManagers"] or "—"],
        ["高级职称人员", ctx.value(staff["senior"], "高级职称人数"), "中级职称人员", ctx.value(staff["middle"], "中级职称人数")],
        ["初级职称人员", staff["junior"] or "—", "技工", staff["technicians"] or "—"],
        ["开户银行", ctx.value(company["bank"], "开户银行"), "账号", ctx.value(company["bankAccount"], "银行账号")],
        ["经营范围", ctx.value(company["businessScope"], "经营范围"), "", ""],
        ["备注", "以上信息以营业执照及最新经审计财务报表为准。", "", ""],
    ]
    table_one = _child(section, "table1", "投标人基本情况表1", form_table(rows, widths=[3.2, 5.2, 3.0, 5.0]))
    table_one.blocks.append(
        paragraph("关联情况说明：法定代表人或单位负责人为同一人或者存在控股、管理关系的其他单位，未参加本标包投标。")
    )
    shareholders = ctx.profile["shareholders"]
    share_rows = [
        [s.get("name", ""), s.get("amount", ""), s.get("ratio", ""), "—" if s.get("kind") != "自然人" else "详见附件（参股比例达三分之一及以上时提供）"]
        for s in shareholders
    ] or [[ctx.need("股东名称及出资情况"), "", "", ""]]
    share_blocks = [
        table(["股东或上级管理单位名称", "出资金额（万元）", "出资比例（%）", "身份证号（如需）"], share_rows, widths=[6.2, 3.2, 3.0, 4.0]),
        paragraph("注：股东及出资信息与国家企业信用信息公示系统（www.gsxt.gov.cn）公示信息一致，查询截图附后。"),
    ]
    if not shareholders:
        share_blocks.append(placeholder("（此处粘贴：国家企业信用信息公示系统股东及出资信息查询截图）", height=4.5))
    _child(
        section,
        "table2",
        "投标人基本情况表2（股东及出资情况）",
        *share_blocks,
    )
    summary_parts = []
    if company["foundedDate"]:
        summary_parts.append(f"成立于{company['foundedDate']}")
    if company["registeredCapital"]:
        summary_parts.append(f"注册资本{company['registeredCapital']}")
    if company["businessScope"]:
        summary_parts.append(f"经营范围为{company['businessScope']}")
    intro_text = company["intro"] or (f"{ctx.company_name}{'，'.join(summary_parts)}。" if summary_parts else "")
    intro = _child(section, "intro", "企业概况", paragraph(ctx.value(intro_text, "企业简介")))
    if company["honors"]:
        intro.blocks.append(paragraph(f"资质荣誉：{company['honors']}"))
    section.blocks.append(paragraph("我方按招标文件格式填报投标人基本情况如下，所填内容真实、准确、完整。"))
    intro.blocks.append(ctx.signature_block(dated=False))


def build_business_license(ctx: ComposeContext, section: Section) -> None:
    section.blocks += [
        paragraph(f"投标人名称：{ctx.company_name}；统一社会信用代码：{ctx.value(ctx.company['creditCode'], '统一社会信用代码')}。营业执照副本扫描件如下："),
        *certificate_blocks(ctx, ("business_license",), "企业法人营业执照副本扫描件"),
    ]


def build_tax(ctx: ComposeContext, section: Section) -> None:
    section.blocks += [
        paragraph(f"我公司已办理“多证合一”登记，营业执照载明的统一社会信用代码（{ctx.value(ctx.company['creditCode'], '统一社会信用代码')}）即为纳税人识别号，我公司为增值税一般纳税人。按招标文件格式说明，提供“多证合一”后的营业执照副本扫描件的，可不再另行提供税务登记证。"),
        *certificate_blocks(ctx, ("tax", "business_license"), "有效的税务登记证明或“多证合一”营业执照副本扫描件"),
    ]


def build_qualification(ctx: ComposeContext, section: Section) -> None:
    section.blocks.append(paragraph("我方对照招标文件第一章投标邀请书“投标人资格要求”逐条自查，全部满足，并按要求提交下列证明文件。"))
    requirements = [q for q in ctx.tender.get("general_qualifications", []) if len(q) > 12]
    rows = []
    for index, requirement in enumerate(requirements, start=1):
        response = next((text for keyword, text in _QUALIFICATION_RESPONSES if keyword in requirement), "我公司完全满足本条要求，并承诺如有不实愿承担由此产生的一切责任。")
        brief = re.sub(r"^[（(]?\d+(?:\.\d+)*[)）]?\s*", "", requirement)
        rows.append([str(index), brief if len(brief) <= 130 else brief[:130] + "……", response, "满足"])
    if rows:
        _child(section, "response", "投标人资格要求逐条响应表", table(["序号", "招标文件资格要求（摘要）", "投标人响应情况", "结论"], rows, widths=[1.1, 7.2, 6.6, 1.5]))
    special = [lot for lot in ctx.lots if lot.get("prequalification")]
    if special:
        _child(
            section,
            "special",
            "专用资格条件响应",
            table(
                ["分标编号", "分标名称", "应通过资格预审标段及其他要求", "响应情况"],
                [[lot["lot_code"], lot["lot_name"], lot["prequalification"], "已通过对应预审标段资格预审，满足要求"] for lot in special],
                widths=[3.6, 3.2, 6.6, 3.0],
            ),
        )
    held = [c for c in ctx.profile["certificates"] if c["category"] not in ("legal_id", "agent_id", "business_license", "tax")]
    if held:
        _child(
            section,
            "certs",
            "资质证书一览表",
            table(
                ["序号", "证书名称", "证书编号", "发证机构", "有效期"],
                [[str(i), c["name"], c["number"] or "见证书", c["issuer"] or "见证书", _validity_text(c)] for i, c in enumerate(held, start=1)],
                widths=[1.2, 6.4, 3.2, 3.2, 2.6],
            ),
            paragraph("注：以上证书原件扫描件随本投标文件一并提交，资格预审申请文件中已提交且在有效期内的证书不再重复提供。"),
        )


def build_credit_reports(ctx: ComposeContext, section: Section) -> None:
    window = ctx.query_window()
    section.blocks += [
        paragraph(f"按招标文件要求，我方提供在“国家企业信用信息公示系统”（www.gsxt.gov.cn）查询并由网站自动生成的企业信用信息公示报告（PDF 电子版），报告包含“营业执照信息”“列入严重违法失信企业名单（黑名单）信息”“列入经营异常名录信息”“行政处罚信息”“清算信息”。查询日期应在投标截止日前20天内，即 {window} 之间。"),
        placeholder(f"（此处粘贴：国家企业信用信息公示系统——企业信用信息公示报告；查询日期须在 {window} 之间）", height=7),
        paragraph("同时提供“信用中国”网站（www.creditchina.gov.cn）查询的信用报告，证明我公司未被列入失信被执行人、严重失信主体名单。"),
        placeholder("（此处粘贴：“信用中国”信用信息报告及失信被执行人查询截图）", height=7),
    ]


def build_finance(ctx: ComposeContext, section: Section) -> None:
    section.not_applicable = False
    finance = ctx.profile["finance"]
    criteria = _find_criteria(ctx, "business", "经营状况", "科研经费")
    section.criteria += [c["criterion_id"] for c in criteria]
    section.blocks.append(paragraph("我公司财务状况良好、资产运营能力与履约能力强。现按招标文件格式补充财务信息、股权结构说明书及支持证明材料如下。"))
    indicators = (
        ("规模", "主营业务收入净额（万元）", "revenue"),
        ("规模", "实收资本（万元）", "paidCapital"),
        ("风险", "资产负债率", "debtRatio"),
        ("风险", "流动比率", "currentRatio"),
        ("盈利", "净资产收益率", "roe"),
        ("履约能力", "现金比率", "cashRatio"),
        ("科研经费", "研究与开发费（万元）", "rdExpense"),
        ("科研经费", "研发费占比", "rdRatio"),
    )
    years = [str(f.get("year", "")) for f in finance] or ["近三年"]
    rows = [
        [group, label, *([str(f.get(field) or ctx.need(f"{f.get('year', '')}年{label}")) for f in finance] or [ctx.need(f"近三年{label}")])]
        for group, label, field in indicators
    ]
    _child(
        section,
        "info",
        "财务信息表",
        table(["财务状况", "事项", *[f"{y}年" if y.isdigit() else y for y in years]], rows),
        paragraph("注：上表数据取自经会计师事务所审计的财务会计报表，审计报告及报表复印件附后。"),
        placeholder("（此处粘贴：近三年经审计的财务会计报表——资产负债表、利润表、现金流量表及财务报表附注）", height=7),
    )
    shareholders = ctx.profile["shareholders"]
    share_rows = [[str(i), s.get("name", ""), s.get("amount", ""), s.get("ratio", "")] for i, s in enumerate(shareholders, start=1)]
    if shareholders:
        total = sum(float(s["amount"]) for s in shareholders if re.fullmatch(r"\d+(?:\.\d+)?", str(s.get("amount", ""))))
        share_rows.append(["合计", "—", f"{total:g}", "100.00"])
    else:
        share_rows.append(["1", ctx.need("股东名称及出资情况"), "", ""])
    largest = max(shareholders, key=lambda s: float(s.get("ratio") or 0), default=None)
    _child(
        section,
        "equity",
        "股权结构说明书",
        paragraph(f"{ctx.company_name}股权结构如下："),
        table(["序号", "股东名称", "出资金额（万元）", "出资比例（%）"], share_rows, widths=[1.4, 8.0, 3.6, 3.4]),
        paragraph(
            (f"第一大股东为{largest['name']}，持股比例{largest['ratio']}%。" if largest else "")
            + "我公司股权结构清晰，不存在代持、信托持股等特殊安排。"
        ),
        ctx.signature_block(),
    )


def build_business_supplements(ctx: ComposeContext, section: Section) -> None:
    supplements = _applicable_supplements(ctx, "business")
    section.blocks.append(paragraph("按照招标文件第一章投标邀请书关于“投标人须在商务投标文件中提供补充支持证明材料”的要求，我方逐项提交如下："))
    if not supplements:
        section.blocks.append(paragraph("招标文件未对所投分标提出需补充的商务支撑证明材料，本项无补充内容。"))
        return
    for index, item in enumerate(supplements, start=1):
        criteria = _find_criteria(ctx, "business", *_keywords(item["item"]))
        child = _child(section, f"s{index}", f"{item['item']}", criteria=[c["criterion_id"] for c in criteria])
        child.blocks.append(paragraph(f"招标文件要求：{item['project']}——{item['item']}。{item['note']}。"))
        child.blocks += _business_factor_response(ctx, item["item"])
        child.blocks += _evidence_placeholders(item["item"])


def _applicable_supplements(ctx: ComposeContext, volume: str) -> list[dict]:
    result, seen = [], set()
    for item in ctx.tender.get("supplements", []):
        if item["volume"] != volume or item["item"] in seen:
            continue
        if ctx.categories and not any(category in item["scope"] for category in ctx.categories):
            continue
        seen.add(item["item"])
        result.append(item)
    return result


def _keywords(text: str) -> list[str]:
    return [part for part in re.split(r"[、，,及和/]", text) if len(part) >= 2] or [text]


def _evidence_placeholders(factor: str) -> list[dict]:
    materials = next((items for keyword, items in _BUSINESS_EVIDENCE if keyword in factor), ["相关证明材料"])
    return [placeholder(f"（此处粘贴：{material}）", height=4.5) for material in materials]


def _has_certificate(ctx: ComposeContext, *categories: str) -> list[dict]:
    from api.services.bidgen.profile import usable_certificates

    return [c for c in usable_certificates(ctx.profile, ctx.tender.get("bid_deadline", "")) if c["category"] in categories]


def _business_factor_response(ctx: ComposeContext, factor: str) -> list[dict]:
    """各商务评审要素的响应正文。"""
    declarations, service, staff = ctx.profile["declarations"], ctx.profile["service"], ctx.profile["staff"]
    name = ctx.company_name
    if "应急保供" in factor:
        return [
            paragraph(f"{name}高度重视电网应急抢险保电物资供应保障工作，建立了“统一指挥、分级负责、快速响应”的应急保供机制，设立 7×24 小时应急联络值班制度，对电网抢险、迎峰度夏（冬）、重大活动保电等紧急需求实行“优先排产、优先检验、专车直发”。"),
            paragraph("应急保供措施包括：（1）常备库存——对常用规格的导体、绝缘料、护套料及半成品保持安全库存，紧急订单可在最短周期内组织生产；（2）绿色通道——应急订单直接进入生产调度绿色通道，由生产副总统一协调设备与人员；（3）运输保障——与多家物流单位签订应急运输协议，省内24小时、跨省48小时送达；（4）现场服务——随车派出技术服务人员，配合项目单位完成到货验收与敷设指导。"),
            paragraph("我公司近年在电网应急抢修、灾后重建及重大保电任务的物资供应中作出突出贡献，相关供货证明、感谢信等证明材料附后。" if declarations["emergencyProof"] else f"应急抢险保电物资供应证明材料：{ctx.need('应急保供突出贡献证明材料')}"),
        ]
    if "绿色发展规划" in factor:
        systems = _has_certificate(ctx, "quality_system", "environment_system", "safety_system")
        energy = _has_certificate(ctx, "energy_system")
        return [
            paragraph(f"{name}已制定《绿色发展规划》及配套的绿色制造管理制度，明确了绿色低碳生产、绿色供应链管理、资源综合利用和绿色回收的目标、指标与责任部门，并纳入公司年度经营计划考核。" if declarations["greenPlan"] else f"绿色发展规划制度：{ctx.need('绿色发展规划制度文件')}"),
            paragraph(f"管理体系认证方面，我公司已取得经 CNCA 批准的认证机构颁发的{'、'.join(c['name'] for c in systems)}，共 {len(systems)} 项。" if systems else f"质量、环境、职业健康安全管理体系认证：{ctx.need('三体系认证证书')}"),
            paragraph(f"我公司已取得{energy[0]['name']}。" if energy else "能源管理体系认证：我公司正按 GB/T 23331 推进能源管理体系建设（如已取证请在企业资料中登记并附证书）。"),
        ]
    if "创新激励" in factor or "供应链保障" in factor:
        return [
            paragraph(f"{name}制定有《研发创新激励管理办法》，对技术攻关、专利产出、标准制定、工艺改进和合理化建议等给予专项奖励，并设立技术创新专项基金，形成“立项—攻关—转化—奖励”的闭环激励机制。" if declarations["innovationIncentive"] else f"创新激励政策和机制：{ctx.need('创新激励制度文件')}"),
            paragraph("供应链保障方面：（1）关键原材料实行“双供应商 + 战略协议”采购策略，对铜、铝、绝缘料等大宗材料建立安全库存和价格联动机制；（2）建立供应商分级评价与动态准入退出机制，定期开展二方审核；（3）制定《供应链风险应对预案》，对原材料涨价、断供、物流中断等外部系统性风险明确预警阈值和处置流程；（4）积极响应并落实国家及地方产业激励扶持政策。" if declarations["supplyChainMeasures"] else f"供应链保障措施：{ctx.need('供应链保障措施及证明材料')}"),
        ]
    if "售后服务" in factor:
        count = service["provinceCount"]
        return [
            paragraph(f"{name}建立了覆盖全国的售后服务网络，服务网点覆盖 {ctx.value(count, '售后服务网点覆盖省份数量')} 个省（自治区、直辖市）" + (f"，包括：{service['provinces']}。" if service["provinces"] else "，网点清单见附表。")),
            paragraph(f"各服务网点配备专职服务工程师和常用检测工具，实行 7×24 小时响应：接到报修后 {service['responseHours'] or '2'} 小时内响应，需现场处理的 {service['arrivalHours'] or '24'} 小时内到达现场。"),
        ]
    if "经营状况" in factor:
        return [paragraph(f"{name}资产运营能力与履约能力强，整体经营状况优良，近三年主营业务收入稳定增长，资产负债率保持在合理水平，详见“财务信息、股权结构说明书及支持证明材料”及所附审计报告。")]
    if "环境影响评价" in factor:
        held = [label for key, label in (("esgReport", "ESG 报告"), ("eiaReport", "环评 / 能评报告"), ("wasteReport", "废水 / 废气 / 废固报告")) if declarations[key]]
        return [paragraph(f"我公司具备以下环境影响报告：{'、'.join(held)}，共 {len(held)} 类，报告均由具有资质能力的机构出具（发布），证明材料附后。" if held else f"环境影响报告（ESG 报告、环评 / 能评报告、废水 / 废气 / 废固报告）：{ctx.need('三类环境影响报告持有情况')}")]
    if "绿电绿证" in factor:
        held = _has_certificate(ctx, "green_power")
        return [paragraph(f"我公司已取得{held[0]['name']}，证书附后。" if held else "我公司积极推进绿色电力消费（如已取得绿色电力证书或绿电交易凭证，请在企业资料中登记并附证明材料）。")]
    if "企业创新成果" in factor:
        awards = ctx.profile["awards"]
        if awards:
            return [table(["序号", "成果 / 奖项名称", "级别", "获得年份"], [[str(i), a.get("name", ""), a.get("level", ""), str(a.get("year", ""))] for i, a in enumerate(awards, start=1)])]
        return [paragraph(f"近3年科技创新成果：{ctx.need('近3年国家级/省级科技创新成果')}")]
    if "研发团队" in factor:
        return [paragraph(f"{name}拥有高级及以上职称人员和高级技师共 {ctx.value(staff['senior'], '高级及以上职称人员和高级技师人数')} 人，人员名单、职称证书及社保缴纳证明附后。")]
    if "科研经费" in factor:
        finance = [f for f in ctx.profile["finance"] if f.get("rdRatio")]
        if finance:
            return [paragraph("我公司近3年持续投入研发：" + "；".join(f"{f['year']}年研发投入占主营业务收入 {f['rdRatio']}" for f in finance) + "。研发费用专项审计报告附后。")]
        return [paragraph(f"近3年研发投入占比：{ctx.need('近3年研发费用及占比')}")]
    if "高新技术企业" in factor:
        held = _has_certificate(ctx, "high_tech")
        return [paragraph(f"我公司持有有效的{held[0]['name']}（{_validity_text(held[0])}），证书附后。" if held else f"高新技术企业证书：{ctx.need('有效的高新技术企业证书')}")]
    if "数智化" in factor:
        systems = ctx.profile["systems"]
        return [paragraph("我公司持续推进数字化、智能化转型，已建成并应用" + ("、".join(systems) if systems else ctx.need("数智化系统")) + "等信息化系统；如已取得工信部等部委“智能制造示范工厂”“数字化车间”“智能工厂”等认定，认定文件附后。")]
    if "质量奖" in factor:
        return [paragraph("我公司坚持“质量第一”的经营方针，推行卓越绩效管理模式（如已获得国家级质量奖，请附获奖证书）。")]
    if "专精特新" in factor:
        held = _has_certificate(ctx, "specialized")
        return [paragraph(f"我公司已被认定为专精特新企业，持有{held[0]['name']}，证书附后。" if held else f"专精特新企业认定：{ctx.need('专精特新企业认定证书')}")]
    if "实体清单" in factor:
        return [paragraph("我公司积极应对国际贸易壁垒，推进关键原材料国产化替代与多元化采购（如已获得财政补贴、税收优惠、融资支持等，请附补贴批文、税收优惠证明、融资协议等证明材料）。")]
    return [paragraph(f"我方对“{factor}”作出如下响应：我公司满足该项要求，相关证明材料附后。")]


def build_name_change(ctx: ComposeContext, section: Section) -> None:
    if ctx.profile["declarations"]["nameChanged"]:
        section.blocks += [
            paragraph("我公司在资格预审后发生企业名称变更，变更原因说明及证明材料如下。"),
            placeholder("（此处粘贴：企业名称变更原因说明、市场监督管理部门出具的“名称变更证明”、有权机构出具的“资格资质变更证明”）", height=7),
        ]
        return
    section.not_applicable = True
    section.blocks.append(paragraph(f"{ctx.company_name}自参加资格预审至本次投标期间，企业名称未发生变更，营业执照、资质证书、资格预审结果通知书载明的名称一致，本项不适用。"))


def build_finance_voucher(ctx: ComposeContext, section: Section) -> None:
    section.not_applicable = True
    section.blocks.append(paragraph("根据招标文件第一章投标邀请书“本项目不接受资质业绩凭证单，投标人应按照招标文件要求提供对应支持证明材料，无须上传资质业绩凭证单，上传的也不作评审”之规定，我方未使用财务信息审核结果凭证单，相关财务信息以本文件所附证明材料为准，本项不适用。"))


def build_seal_authorization(ctx: ComposeContext, section: Section) -> None:
    section.blocks += [
        title_line("公章对投标专用章授权书"),
        paragraph("致招标人：", indent=False),
        paragraph(f"很荣幸能参与{ctx.project_name}（招标编号：{ctx.tender_no}），分标编号：{ctx.lot_codes}，分标名称：{ctx.lot_names}，包号：{ctx.package_no} 的投标。"),
        paragraph(f"我代表{ctx.company_name}（投标人），在此作如下说明：在此次投标中，所用“投标专用章”与我公司公章具有同等的效力。特此说明。"),
        ctx.signature_block(),
        form_table([["投标人单位公章印模", "投标人投标专用章印模"], ["\n\n\n\n", "\n\n\n\n"]], widths=[8.2, 8.2]),
    ]


def build_relation_statement(ctx: ComposeContext, section: Section) -> None:
    section.blocks += [
        title_line("投标人与国家电网公司系统人员关系说明"),
        paragraph(f"致：{ctx.purchaser_names}：", indent=False),
    ]
    if ctx.profile["declarations"]["noStateGridRelation"]:
        section.blocks.append(paragraph("截至投标截止日，本企业法定代表人（或单位负责人）、出资人、高管中，不存在国家电网公司系统在职、离职、退休人员及上述人员亲属（配偶、子女及其配偶）担任职务或持有股份的情形，不存在招标文件所要求说明的关系。"))
    else:
        section.blocks.append(paragraph(f"截至投标截止日，本企业存在需说明的人员关系，具体情况见下表：{ctx.need('与国家电网公司系统人员关系明细')}"))
    section.blocks += [
        paragraph("《本企业员工与国家电网公司系统人员关系说明表》", bold=True, align="center", indent=False),
        table(
            ["本企业人员姓名", "职务", "与国网公司系统人员关系", "国网系统人员姓名", "（曾）任职单位", "任职状态", "离职 / 退休时间"],
            [["无", "—", "—", "—", "—", "—", "—"]],
        ),
        paragraph("我公司承诺以上说明真实、完整；若经查实存在未如实申报情形，愿意承担由此产生的一切责任及后果。"),
        ctx.signature_block(),
    ]


def build_business_other(ctx: ComposeContext, section: Section) -> None:
    """“其他”项：对未在前文专门响应的商务评分要素逐项作答，并附诚信声明。"""
    covered = {cid for node in ctx.options["_business_root"].walk() for cid in node.criteria}
    positive = [c for c in ctx.criteria_for("business") if c["criterion_id"] not in covered and (c["max_score"] or 0) > 0]
    negative = [c for c in ctx.criteria_for("business") if (c["max_score"] or 0) <= 0 and (c["min_score"] or 0) < 0]
    section.blocks.append(paragraph("为便于评标委员会评审，我方对照招标文件第三章“商务评分标准”，对前述章节未专门说明的评审要素逐项响应如下。"))
    for index, criterion in enumerate(positive, start=1):
        child = _child(section, f"f{index}", f"{criterion['factor']}响应说明", criteria=[criterion["criterion_id"]])
        child.blocks.append(paragraph("投标人响应：", bold=True))
        child.blocks += _business_factor_response(ctx, criterion["factor"])
        child.blocks += _evidence_placeholders(criterion["factor"])
    if negative:
        child = _child(section, "integrity", "诚信评价自查声明", criteria=[c["criterion_id"] for c in negative])
        child.blocks.append(paragraph(f"{ctx.company_name}对照招标文件诚信评价各扣分情形逐项自查，结果如下："))
        clean = ctx.profile["declarations"]["noAdverseRecord"]
        child.blocks.append(
            table(
                ["序号", "评审要素", "扣分情形（摘要）", "自查结果"],
                [
                    [str(i), c["factor"], c["rule"][:90] + ("……" if len(c["rule"]) > 90 else ""), "经自查不存在所列扣分情形" if clean else ctx.need(f"{c['factor']}自查情况")]
                    for i, c in enumerate(negative, start=1)
                ],
                widths=[1.2, 4.2, 7.6, 3.4],
            )
        )
        child.blocks += [
            paragraph("我公司郑重承诺：上述自查结果真实有效，如有虚假，愿意接受招标人按照《国家电网有限公司供应商关系管理办法》作出的相应处理。"),
            ctx.signature_block(),
        ]


def build_authorization(ctx: ComposeContext, section: Section) -> None:
    legal, agent = ctx.profile["legalRep"], ctx.profile["agent"]
    letter = _child(
        section,
        "letter",
        "法定代表人（单位负责人）授权委托书",
        title_line("法定代表人（单位负责人）授权委托书"),
        paragraph("致招标人：", indent=False),
        paragraph(f"{ctx.company_name}为中华人民共和国合法企业，法定地址：{ctx.value(ctx.company['address'], '注册地址')}。"),
        paragraph(f"{ctx.value(legal['name'], '法定代表人姓名')}（营业执照法定代表人（单位负责人））特授权 {ctx.value(agent['name'], '委托代理人姓名')}（身份证号：{ctx.value(agent['idNumber'], '委托代理人身份证号')}，职务：{agent['title'] or '—'}）代表我方全权办理{ctx.project_name}（招标编号：{ctx.tender_no}）（分标编号及名称：{ctx.lot_codes} {ctx.lot_names}）（包号：{ctx.package_no}）项目的投标、谈判、签约、执行等具体工作，并签署全部有关的文件、协议及合同。"),
        paragraph("我方对被授权人签署的所有文件、协议及合同负全部责任。"),
        paragraph("在招标人或招标代理机构收到撤销本授权的通知以前，本授权书一直有效。被授权人签署的所有文件、协议和合同（在本授权书有效期内签署的）不因授权的撤销而失效。"),
        paragraph("被授权人不得转授权。"),
        ctx.signature_block(f"授权人（法定代表人（单位负责人））签名（或签章）：＿＿＿＿＿＿＿＿", f"职务：{legal['title'] or '法定代表人'}", "被授权人签名：＿＿＿＿＿＿＿＿"),
        paragraph("注：本授权委托书须由法定代表人（单位负责人）手写签字或加盖签名章，并加盖公司公章，不得以“投标专用章”或其他印章代替；同时附法定代表人和被授权人身份证正、反两面扫描件。"),
    )
    letter.page_break = False
    _child(
        section,
        "identity",
        "法定代表人（单位负责人）身份证明",
        title_line("法定代表人（单位负责人）身份证明"),
        paragraph(f"投标人名称：{ctx.company_name}", indent=False),
        paragraph(f"单位性质：{ctx.company['orgStructure'].split('，')[0] if ctx.company['orgStructure'] else ctx.need('单位性质')}", indent=False),
        paragraph(f"地址：{ctx.value(ctx.company['address'], '注册地址')}", indent=False),
        paragraph(f"成立时间：{ctx.value(ctx.company['foundedDate'], '成立时间')}", indent=False),
        paragraph(f"姓名：{ctx.value(legal['name'], '法定代表人姓名')}　　职务：{legal['title'] or '法定代表人'}　　系{ctx.company_name}的法定代表人（单位负责人）。"),
        paragraph("特此证明。"),
        ctx.signature_block(),
    )
    _child(section, "legal_id", "法定代表人身份证扫描件", *certificate_blocks(ctx, ("legal_id",), "法定代表人身份证正、反面扫描件"))
    _child(section, "agent_id", "被授权人身份证扫描件", *certificate_blocks(ctx, ("agent_id",), "被授权人身份证正、反面扫描件"))


def certificate_category_label(category: str) -> str:
    return CERT_CATEGORIES.get(category, CERT_CATEGORIES["other"])[0]
