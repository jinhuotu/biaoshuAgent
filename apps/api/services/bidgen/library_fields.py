"""将资料库已识别的明确字段、表头映射到四个企业资料页签。"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from api.services.bidgen.profile import normalize_date

# 只认明确字段，不从招标要求、文件名或模糊叙述猜测企业事实。
FIELD_ALIASES = {
    "company": {
        "name": "企业名称|公司名称|企业全称|单位名称|投标人名称|供应商名称|主体名称|签约主体",
        "creditCode": "统一社会信用代码|社会信用代码|营业执照号|营业执照号码",
        "address": "注册地址|企业地址|公司地址|住所|地址",
        "postcode": "邮政编码|邮编", "contact": "联系人|联系人员",
        "phone": "联系电话|公司电话|企业电话|电话", "fax": "传真|传真号码",
        "website": "网址|公司网址|企业网址", "email": "电子邮箱|邮箱|电子邮件",
        "foundedDate": "成立时间|成立日期", "registeredCapital": "注册资本|注册资金",
        "bank": "开户银行|开户行", "bankAccount": "银行账号|银行帐号|开户账号|账号",
        "orgStructure": "组织结构|组织机构|企业类型|公司类型",
        "businessScope": "经营范围", "intro": "企业简介|公司简介",
        "honors": "资质荣誉|企业荣誉", "qualificationLevel": "资质等级|企业资质等级",
    },
    "staff": {
        "total": "员工总人数|职工总人数|员工人数|职工人数|人员总数",
        "senior": "高级职称人数|高级及以上职称及高级技师|高级及以上职称+高级技师",
        "seniorEngineers": "高级工程师人数|与投标产品相关的高级工程师",
        "middle": "中级职称人数|中级职称", "junior": "初级职称人数|初级职称",
        "technicians": "技工人数|技工", "projectManagers": "项目经理人数",
    },
    "service": {
        "provinceCount": "售后网点覆盖省份数|覆盖省份数", "provinces": "覆盖省份清单|覆盖省份",
        "hotline": "服务热线|售后服务热线|服务电话",
        "responseHours": "响应时限|响应时间", "arrivalHours": "到场时限|到场时间",
    },
    "legalRep": {"name": "法定代表人|法人代表|法定代表人姓名"},
    "agent": {"name": "委托代理人|被授权人|授权代表|委托代理人姓名"},
    "techLead": {"name": "技术负责人|技术负责人姓名"},
    "declarations": {
        "greenPlan": "绿色发展规划制度|已建立绿色发展规划制度",
        "innovationIncentive": "创新激励政策和机制|已制定创新激励政策和机制",
        "supplyChainMeasures": "供应链保障措施|已制定供应链保障措施",
        "emergencyProof": "应急抢险保电突出贡献证明|有应急抢险保电突出贡献证明",
        "greenRecycling": "绿色生产和绿色回收制度|已建立绿色生产和绿色回收制度",
        "esgReport": "ESG报告|已发布ESG报告", "eiaReport": "环评报告|有环评/能评报告",
        "wasteReport": "废水废气废固报告|有废水/废气/废固报告",
        "processDocsComplete": "主要工序工艺文件齐全",
        "materialDocsComplete": "原材料/组部件管理文件齐全",
        "noAdverseRecord": "无不良行为、失信及抽检扣分情形",
        "noStateGridRelation": "与国网系统人员无需说明的关系",
        "nameChanged": "资格预审后发生过企业名称变更",
    },
}
PERSON_ALIASES = {"name": "姓名", "title": "职务|职称|职务/职称", "phone": "电话|联系电话", "idNumber": "身份证号|身份证号码|公民身份号码"}
ROW_ALIASES = {
    "shareholders": {"name": "股东名称|股东姓名|股东", "amount": "出资金额|出资额", "ratio": "出资比例|持股比例", "kind": "股东类型"},
    "finance": {"year": "年度|年份", "revenue": "主营收入|主营业务收入|营业收入", "paidCapital": "实收资本", "debtRatio": "资产负债率", "currentRatio": "流动比率", "roe": "净资产收益率", "cashRatio": "现金比率", "rdExpense": "研发费|研发费用|研发经费", "rdRatio": "研发费占比|研发费用占比|研发经费占比"},
    "equipment": {"name": "设备类别|设备名称|生产设备名称|主要生产设备", "count": "数量|设备数量", "unit": "单位"},
    "testEquipment": {"name": "试验设备名称|检测设备名称|设备名称", "count": "数量|设备数量", "usage": "用途|设备用途"},
    "performances": {"project": "项目名称|合同名称|项目/合同名称", "buyer": "买方|发包人|客户名称|采购人", "product": "产品|产品名称|规格", "quantity": "数量|供货数量", "unit": "单位", "year": "年份|年度|供货年份"},
    "patents": {"name": "专利名称", "number": "专利号|专利编号", "kind": "类型|专利类型"},
    "standards": {"name": "标准名称|标准名称/编号|标准编号", "level": "级别|标准级别"},
    "awards": {"name": "科技成果/奖励名称|科技成果名称|奖励名称|获奖名称|成果名称", "level": "级别|奖励级别", "year": "年份|年度|获奖年份"},
}
ROW_KEYS = {group: ("year" if group == "finance" else "project" if group == "performances" else "name") for group in ROW_ALIASES}
ROW_HINTS = {
    "shareholders": "股东|出资", "finance": "财务|审计|资产负债|利润",
    "testEquipment": "试验设备|检测设备|检验设备", "equipment": "生产设备|设备清单",
    "performances": "业绩|合同", "patents": "专利", "standards": "标准", "awards": "奖励|成果|获奖",
}

_YEAR_CELL = re.compile(r"^(?:19|20)\d{2}(?:年|年度|年末)?$")
CERTIFICATE_SLOTS = {
    "license": "business_license", "business_license": "business_license",
    "legal_id": "legal_id", "agent_id": "agent_id", "id_legal": "legal_id", "id_agent": "agent_id", "tax": "tax",
    "iso9001": "quality_system", "iso14001": "environment_system", "iso45001": "safety_system",
    "quality": "quality_system", "environment": "environment_system", "safety": "safety_system",
}
CERTIFICATE_PATTERNS = {
    "business_license": "营业执照", "legal_id": "法定代表人身份证|法人身份证",
    "agent_id": "委托代理人身份证|授权代理人身份证|被授权人身份证|授权代表身份证|委托人身份证",
    "tax": "税务登记", "quality_system": "质量管理体系|ISO.?9001",
    "environment_system": "环境管理体系|ISO.?14001",
    "safety_system": "职业健康安全|ISO.?45001", "energy_system": "能源管理体系|ISO.?50001",
    "high_tech": "高新技术企业", "specialized": "专精特新|小巨人",
    "green_factory": "绿色工厂", "green_supply_chain": "绿色供应链",
    "carbon_footprint": "碳足迹", "green_power": "绿色电力证书|绿电证书",
    "production_license": "生产许可证", "ccc": "CCC|强制性产品认证|3C认证",
    "type_test": "型式试验|型式检验", "prequalification": "资格预审.*通知",
}


def label_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value)).strip(" :：|\t")
    return re.sub(r"\s+|\([^)]*\)", "", value).lower()


_FINANCE_ROW_LABELS = {
    label_key(alias): key
    for key, aliases in ROW_ALIASES["finance"].items()
    for alias in aliases.split("|")
}


def alias_map(fields: dict) -> dict[str, str]:
    return {label_key(alias): key for key, aliases in fields.items() for alias in aliases.split("|")}


def certificate_category(slot: str, title: str) -> str:
    if slot in CERTIFICATE_SLOTS:
        return CERTIFICATE_SLOTS[slot]
    return next((key for key, pattern in CERTIFICATE_PATTERNS.items() if re.search(pattern, title, re.I)), "")


def person_group(slot: str, title: str, role: str) -> str:
    if slot in {"agent_id", "id_agent"} or re.search("委托|被授权|授权代表|授权代理", title):
        return "agent"
    if "技术负责人" in title:
        return "techLead"
    if slot in {"legal_id", "id_legal"} or "法定代表人" in title or role == "person/legal":
        return "legalRep"
    return ""


def _value(value: object) -> str:
    text = str(value).strip()
    return "" if text in {"", "-", "—", "/", "待填写", "未填写", "未知", "未提供", "暂无", "null"} else text


def _money_value(value: str, label: str) -> str:
    """表单金额为万元；只在来源明确给出单位时换算，使用 Decimal 保留精度。"""
    unit = re.search(r"亿元|万元|元", value) or re.search(r"亿元|万元|元", label)
    if not unit:
        return value
    raw = re.sub(r"亿元|万元|元|人民币|[,，\s]", "", value)
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        return value
    if not amount.is_finite():
        return ""
    multiplier = {"亿元": Decimal("10000"), "万元": Decimal("1"), "元": Decimal("0.0001")}[unit.group()]
    return format(amount * multiplier, "f")


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in re.split(r"\t|\s*\|\s*| {2,}", line.strip().strip("|"))]


def _finance_transposed_rows(lines: list[str], title: str) -> list[dict]:
    """读取“项目 | 2023年 | 2024年 | 2025年”这类纵向财务表。"""
    values: dict[tuple[str, str], tuple[int, str]] = {}
    index = 0
    while index < len(lines):
        headers = _cells(lines[index])
        years = [cell for cell in headers[1:] if _YEAR_CELL.fullmatch(cell.replace(" ", ""))]
        if len(headers) < 3 or len(years) < 2 or not re.search(r"项目|科目|指标", headers[0]):
            index += 1
            continue
        year_columns = headers[1 : len(years) + 1]
        index += 1
        while index < len(lines):
            cells = _cells(lines[index])
            if len(cells) < len(year_columns) + 1:
                break
            label = re.sub(r"^[（(]?(?:其中|其中：|其中:)\s*", "", cells[0]).strip(" ：:")
            field = _FINANCE_ROW_LABELS.get(label_key(label))
            if field and field != "year":
                for year, value in zip(year_columns, cells[1:]):
                    cleaned = _value(value)
                    if cleaned:
                        normalized_year = re.sub(r"年(?:度|末)?$", "", year)
                        priority = 1 if field == "revenue" and "主营" in label else 0
                        key = (normalized_year, field)
                        previous = values.get(key)
                        if previous is None or priority >= previous[0]:
                            values[key] = (priority, _money_value(cleaned, label))
            index += 1
        continue
    return [{"year": year, field: value} for (year, field), (_priority, value) in values.items()]


def _declaration_title_patch(title: str) -> dict:
    """只根据资料项标题识别明确的制度或说明文件，不从正文推断企业事实。"""
    normalized = re.sub(r"\s+", "", title or "")
    mapping = {
        "greenPlan": ("绿色发展规划", "绿色低碳发展规划"),
        "innovationIncentive": ("创新激励", "研发创新激励"),
        "supplyChainMeasures": ("供应链保障", "供应链风险应对"),
        "greenRecycling": ("绿色回收", "绿色生产和绿色回收"),
        "processDocsComplete": ("工艺文件", "工艺管理"),
        "materialDocsComplete": ("原材料", "组部件管理"),
        "noStateGridRelation": ("系统人员关系", "国网系统人员关系"),
        "esgReport": ("ESG报告",),
        "eiaReport": ("环评报告", "能评报告"),
        "wasteReport": ("废水废气废固", "废水、废气、废固"),
    }
    return {key: True for key, marks in mapping.items() if any(mark in normalized for mark in marks)}


def _service_table_patch(lines: list[str], title: str) -> dict:
    """读取售后服务网点表，避免把多列表头误当成标签和值。"""
    for index, line in enumerate(lines):
        headers = _cells(line)
        normalized = [label_key(cell) for cell in headers]
        if len(headers) < 3 or not any("服务网点" in cell or "覆盖区域" in cell for cell in normalized):
            continue
        region_index = next((i for i, cell in enumerate(normalized) if "覆盖区域" in cell or "省份区域" in cell), None)
        phone_index = next((i for i, cell in enumerate(normalized) if "服务电话" in cell or "联系电话" in cell), None)
        if region_index is None and phone_index is None:
            continue
        regions, phones = [], []
        cursor = index + 1
        while cursor < len(lines):
            cells = _cells(lines[cursor])
            if len(cells) < len(headers) or not re.match(r"^\d+$", cells[0]):
                break
            if region_index is not None and cells[region_index]:
                regions.append(cells[region_index])
            if phone_index is not None and cells[phone_index]:
                phones.append(cells[phone_index])
            cursor += 1
        response = re.search(r"(\d+)\s*小时内响应", "\n".join(lines))
        arrival = re.search(r"(\d+)\s*小时内到达", "\n".join(lines))
        patch = {}
        if regions:
            patch["provinces"] = "、".join(dict.fromkeys(regions))
            patch["provinceCount"] = str(len(dict.fromkeys(regions)))
        if phones:
            patch["hotline"] = "、".join(dict.fromkeys(phones))
        if response:
            patch["responseHours"] = response.group(1)
        if arrival:
            patch["arrivalHours"] = arrival.group(1)
        return patch
    return {}


def _table_group(headers: list[str], title: str) -> tuple[str, dict[int, str]]:
    choices = []
    for group, fields in ROW_ALIASES.items():
        aliases = alias_map(fields)
        mapping = {index: aliases[label_key(label)] for index, label in enumerate(headers) if label_key(label) in aliases}
        if len(mapping) >= 2 and ROW_KEYS[group] in mapping.values():
            score = len(mapping) + (3 if re.search(ROW_HINTS[group], title) else 0)
            choices.append((score, group, mapping))
    if not choices:
        return "", {}
    choices.sort(key=lambda item: item[0], reverse=True)
    if len(choices) > 1 and choices[0][0] == choices[1][0]:
        return "", {}
    return choices[0][1], choices[0][2]


def extract_profile_fields(*, fields: list[dict], text: str, slot: str, title: str, role: str) -> dict:
    """返回稀疏资料补丁；无法确定的值不进入补丁。"""
    patch: dict = {}
    declarations = _declaration_title_patch(title)
    if declarations:
        patch["declarations"] = declarations
    targets = {alias: (group, key) for group, definitions in FIELD_ALIASES.items() for alias, key in alias_map(definitions).items()}
    person = person_group(slot, title, role)
    if person:
        # 身份证中的住址与电话不属于企业字段。
        targets = {alias: target for alias, target in targets.items() if target[0] != "company"}
        targets.update({alias: (person, key) for alias, key in alias_map(PERSON_ALIASES).items()})
    elif slot in {"license", "business_license"}:
        targets["名称"] = ("company", "name")

    def add(label: str, raw: object) -> None:
        target = targets.get(label_key(label))
        value = _value(raw)
        if not target or not value:
            return
        group, key = target
        if key == "foundedDate":
            value = normalize_date(value)
        if group == "declarations":
            if value.lower() in {"是", "有", "已建立", "已制定", "已发布", "已具备", "true", "1"}:
                value = True
            elif value.lower() in {"否", "无", "未建立", "未制定", "未发布", "false", "0"}:
                value = False
            else:
                return
        if value != "":
            patch.setdefault(group, {}).setdefault(key, value)

    for field in fields:
        confidence = field.get("conf", 1)
        try:
            if float(confidence) < 0.8:
                continue
        except (ValueError, TypeError):
            continue
        add(str(field.get("name", "")), field.get("value", ""))

    # 结构化字段也可构成一行股东、财务、设备、专利或业绩。
    reliable = [field for field in fields if isinstance(field.get("conf", 1), (int, float)) and field.get("conf", 1) >= 0.8]
    headers = [str(field.get("name", "")) for field in reliable]
    group, mapping = _table_group(headers, title)
    if group:
        row = {key: _value(reliable[index].get("value", "")) for index, key in mapping.items()}
        if row.get(ROW_KEYS[group]):
            for index, key in mapping.items():
                if (group == "finance" and key in {"revenue", "paidCapital", "rdExpense"}) or (group == "shareholders" and key == "amount"):
                    row[key] = _money_value(row[key], headers[index])
            patch.setdefault(group, []).append(row)

    lines = text.splitlines()
    transposed_finance = _finance_transposed_rows(lines, title)
    if transposed_finance:
        patch["finance"] = transposed_finance
    service = _service_table_patch(lines, title)
    if service:
        patch["service"] = service
    table_group, table_mapping, table_headers = "", {}, []
    for index, line in enumerate(lines):
        cells = _cells(line)
        group, mapping = _table_group(cells, title)
        if group:
            table_group, table_mapping, table_headers = group, mapping, cells
            continue
        if table_group and len(cells) >= len(table_headers):
            row = {key: _value(cells[column]) for column, key in table_mapping.items()}
            identity = row.get(ROW_KEYS[table_group], "")
            if identity and not re.fullmatch(r"[-: ]+", identity) and identity not in {"合计", "总计"}:
                for column, key in table_mapping.items():
                    if (table_group == "finance" and key in {"revenue", "paidCapital", "rdExpense"}) or (table_group == "shareholders" and key == "amount"):
                        row[key] = _money_value(row[key], table_headers[column])
                patch.setdefault(table_group, []).append(row)
            continue
        table_group = ""
        # OCR 常见的“标签：值”、两列表格以及标签/值各占一行。
        pairs = re.split(r"[：:]", line, maxsplit=1)
        if len(pairs) == 2:
            add(pairs[0], pairs[1])
        if len(cells) % 2 == 0 and len(cells) <= 6:
            labels = [label_key(cells[column]) for column in range(0, len(cells), 2)]
            if all(label in targets for label in labels):
                for column in range(0, len(cells), 2):
                    add(cells[column], cells[column + 1])
        if len(cells) == 1 and label_key(line) in targets and index + 1 < len(lines):
            following = lines[index + 1].strip()
            if label_key(following) not in targets and not re.search(r"[:：\t|]", following):
                add(line, following)
        if len(pairs) == 2 and label_key(pairs[0]) in {"智能制造系统", "已应用的智能制造系统软件", "信息化系统"}:
            patch["systems"] = [value.strip() for value in re.split(r"[、,，;；]", pairs[1]) if value.strip()]
    return patch
