"""从整本招标文件抽取编写技术标所需的项目事实。

只照录招标文件原文中能定位到的事实（工期、范围、设备参数、规范、管理要求等），抽不到的字段留空，
由编写器按“不写具体值”的通用表述处理，不猜测、不补造。
"""

from __future__ import annotations

import re

from api.services.bidgen.docx_reader import DocBlock
from api.services.bidgen.integrated_parser import _FORMAT_CHAPTER
from api.services.bidgen.requirements import build_requirement_tree, node_text

_CHAPTER = re.compile(r"^第[一二三四五六七八九十百\d]+章")
_STANDARD = re.compile(r"《([^》]{4,40})》\s*[（(]\s*([A-Z]{2,4}(?:/[A-Z])?\s*[A-Z]*\s*\d[\w.\-—－–]*)\s*[)）]")
_CJK_GAP = re.compile(r"(?<=[一-鿿，、。；：（）%])[ \t　]+(?=[一-鿿，、。；：（）\d])")

# 关键设施 / 工作内容 → 招标文件中出现即视为本项目包含（用于编写器按需展开章节）
FEATURE_PATTERNS = {
    "transformer": r"箱变|箱式变电站|变压器",
    "dc_charger": r"直流充电|直流充\s*电机|超充|快充",
    "ac_charger": r"交流充电|慢充",
    "barrier": r"道闸|挡车器|车牌识别",
    "parking_lock": r"车位锁|地锁",
    "camera": r"摄像机|视频监控",
    "lighting": r"庭院灯|照明",
    "network": r"交换机|弱电",
    "duct": r"排管|电缆通道|电缆沟",
    "drainage": r"排水",
    "site_work": r"场地整理|场地平整|路面",
    "platform": r"运营平台|指定平台",
    "power_supply": r"供电公司|供电答复|报装",
    "photovoltaic": r"光伏",
    "storage": r"储能",
}


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _tidy(text: str) -> str:
    """去掉 Word 转换带入的字间空格，保留中英文之间的必要空格。"""
    return _CJK_GAP.sub("", re.sub(r"[ \t　]+", " ", (text or "").strip()))


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[。；;])", _tidy(text)) if part.strip()]


def _wan(raw: str) -> str:
    """“36570000.00” → “3657万元”。"""
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return ""
    return f"{value / 10000:g}万元"


def _clause(info, *names: str) -> str:
    for clause in info.key_clauses:
        name = _compact(clause["name"])
        if any(name == target or name.startswith(target) for target in names):
            return _tidy(clause["content"])
    return ""


def _search(pattern: str, text: str, group: int = 1) -> str:
    matched = re.search(pattern, text)
    return _tidy(matched.group(group)) if matched else ""


def _chapter(blocks: list[DocBlock], keyword: str) -> list[DocBlock]:
    start = next((i for i, b in enumerate(blocks) if b.kind == "paragraph" and b.level and _CHAPTER.match(b.text)
                  and keyword in b.text), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(blocks)) if blocks[i].kind == "paragraph" and blocks[i].level
                and _CHAPTER.match(blocks[i].text)), len(blocks))
    return blocks[start + 1:end]


def _equipment(blocks: list[DocBlock]) -> list[dict]:
    """“发包人要求”中的设备表：名称、规格参数、参考品牌。"""
    items: list[dict] = []
    category, category_brands = "", ""
    for block in blocks:
        if block.kind == "paragraph":
            # 设备分类小标题，如“2.道闸（科拓、富士、道尓、海康）”
            matched = re.match(r"^\d+\s*[.．、]\s*([^（(]{2,16})(?:[（(]([^）)]*)[）)])?$", _tidy(block.text))
            if matched:
                category = re.sub(r"序.*$", "", matched.group(1)).strip()  # 表头被并入标题行时去掉
                category_brands = (matched.group(2) or "").strip()
            continue
        for row in block.rows:
            cells = [_tidy(cell) for cell in row]
            if len(cells) < 3 or not re.fullmatch(r"\d{1,3}", cells[0]) or not cells[1]:
                continue
            name = _compact(cells[1])
            spec = cells[2] if len(cells) >= 5 else (cells[3] if len(cells) >= 4 else "")
            brands = _search(r"品牌[：:]\s*([^）)]+)", cells[-1]) or _search(r"[（(]品牌[：:]([^）)]+)[）)]", spec)
            if not brands and category and len(cells) == 3:
                brands = cells[2]
            items.append({"category": category or name, "name": name, "spec": spec, "brands": brands or category_brands})
    return items


def _standards(text: str) -> list[dict]:
    """技术标准（GB、NB、DL、JGJ 等）；定额等计价依据不算。同号的 GB 与 GB/T 只留一条，优先带 /T 的写法。"""
    found: dict[str, dict] = {}
    for matched in _STANDARD.finditer(text):
        name, code = matched.group(1).strip(), re.sub(r"\s+", "", matched.group(2)).replace("—", "-").replace("－", "-")
        if "定额" in name or re.match(r"HA\d", code):
            continue
        key = re.sub(r"\D", "", code)
        if key not in found or "/" in code:
            found[key] = {"name": name, "code": code}
    return list(found.values())


def _deliverables(blocks: list[DocBlock]) -> list[dict]:
    for block in blocks:
        if block.kind != "table" or not any("份数" in _compact(cell) for cell in block.rows[0]):
            continue
        rows = []
        for row in block.rows[1:]:
            cells = [_tidy(cell) for cell in row] + ["", "", "", ""]
            copies = next((cell for cell in cells[2:5] if re.fullmatch(r"\d+", cell)), "")
            if cells[1] and copies:
                rows.append({"name": re.sub(r"[（(].*$", "", cells[1]), "copies": copies, "note": cells[4]})
        if rows:
            return rows
    return []


def extract_project_facts(blocks: list[DocBlock], info) -> dict:
    """``blocks`` 为招标文件正文块（不含格式章节以外的约束），``info`` 为已解析的 TenderInfo。"""
    format_start = next((i for i, b in enumerate(blocks) if b.kind == "paragraph" and b.level
                         and _FORMAT_CHAPTER.fullmatch(b.text)), len(blocks))
    body = blocks[:format_start]
    lines = []
    for block in body:
        if block.kind == "paragraph":
            lines.append(block.text)
        else:
            lines.extend(" | ".join(row) for row in block.rows)
    text = _tidy("\n".join(lines))
    requirement_blocks = _chapter(body, "发包人要求")
    scope = _clause(info, "招标范围")
    overview = _clause(info, "工程概况") or _search(r"项目概况[：:]([^\n]+)", text)
    schedule = _clause(info, "计划工期") or _search(r"计划工期[：:]\s*([^\n]+)", text) or _search(r"交付时间[：:]\s*([^\n]+)", text)
    quality = _clause(info, "质量标准", "质量要求")
    price_clause = _clause(info, "最高投标限价")
    equipment = _equipment(requirement_blocks)
    equipment_text = " ".join(f"{item['name']} {item['spec']}" for item in equipment)
    scope_text = " ".join((overview, scope, equipment_text))
    dust_items = _search(r"8个100%标准[（(]即([^）)]+)[）)]", text)
    facts = {
        "name": info.project_name,
        "owner": info.purchasers[0] if info.purchasers else "",
        "location": _clause(info, "建设地点") or _search(r"项目地址[：:]\s*([^\n]+)", text),
        "funding": _search(r"资金来源[：:]\s*(.+?)\s*(?:比例|$)", _clause(info, "资金来源及比例", "资金来源")),
        "funding_ratio": _search(r"比例[：:]\s*(\d+%)", _clause(info, "资金来源及比例", "资金来源")),
        "estimated_price": _search(r"合同估算价约?\s*([\d.]+\s*(?:万元|亿元))", text),
        "schedule": schedule,
        "schedule_days": int(_search(r"(\d+)\s*(?:个)?(?:日历天|自然日)", schedule) or 0),
        "schedule_unit": "单个场站" if "单个场站" in schedule else "",
        "quality": quality,
        "construction_quality": _search(r"施工[：:]\s*([^\n。]*合格[^\n。]*)", quality),
        "safety": _clause(info, "安全要求"),
        "overview": overview,
        "design_scope": _search(r"设计招标范围[：:]\s*([^\n]+?)(?=施工招标范围|$)", scope),
        "construction_scope": _search(r"施工招标范围[：:]\s*([^\n]+)", scope),
        "special_requirements": [s for s in _sentences(scope) if re.search(r"特别强调|无条件|免费|须同步|必须", s)],
        "subcontract_allowed": None if not _clause(info, "投标人拟分包的工作")
        else "不允许" not in _clause(info, "投标人拟分包的工作"),
        "design_price_cap": _wan(_search(r"设计费最高投标限价[：:]\s*([\d,.]+)\s*元", price_clause)),
        "engineering_price_cap": _wan(_search(r"工程费最高投标限价[：:]\s*([\d,.]+)\s*元", price_clause)),
        "full_cost_note": _search(r"((?:该|本)[^。\n|（）()]{0,12}全费用清单[^\n|]*?。)", text)
        or _search(r"([^。\n|（）()]{0,12}全费用清单[^\n|]*?。)", text),
        "tax_rate": _search(r"税率为\s*(\d+%)", text),
        "pm_requirement": _search(r"项目经理资格要求[：:]\s*([^\n]+?)(?:若为联合体|$)", text)
        or _search(r"([^\n。]*项目经理[^\n。]*建造师[^\n。]*)", text),
        "design_lead_requirement": _search(r"设计负责人资质要求[：:]\s*([^\n。]+)", text),
        "attendance_days": _search(r"每月在现场工作时间不少于\s*(\d+)\s*天", text),
        "dust_standard": [item.strip() for item in re.split(r"、(?=[^、]{2,40}100%)", dust_items) if item.strip()],
        "dust_regulations": list(dict.fromkeys(re.findall(r"《([^》]*扬尘[^》]*)》", text)))[:4],
        "safety_standardization": _search(r"创建([^，。\n]{4,30}标准化工地)", text),
        "wage_system": re.sub(r"^.*?(?:按照|依照|执行|关于)", "", _search(r"([\u4e00-\u9fff]{2,8}[“\"]一金三制[”\"])", text)),
        "wage_account": "农民工工资专用账户" in text or "工资专用账户" in text,
        "warranty": _search(r"其他项目保修期限约定如下[：:]\s*([^。\n]+)", text),
        "defect_period": _search(r"工程缺陷责任期为\s*([^\n，。]{1,12})", text),
        "deliverables": _deliverables(body),
        "as_built_copies": _search(r"竣工图([一二三四五六七八九十\d]+)份", text),
        "as_built_days": _search(r"竣工资料移交时间[：:]\s*竣工后\s*(\d+)\s*日内", text),
        "equipment": equipment,
        "standards": _standards(text),
        "dc_powers": sorted({int(v) for v in re.findall(r"装机功率[:：]\s*(\d+)\s*kW", equipment_text)}),
        "terminal_currents": sorted({int(v) for v in re.findall(r"(\d+)\s*A直流充电终端", equipment_text)}),
        "gun_length": _search(r"枪线长度[^\d]{0,20}不少于\s*(\d+\s*米)", equipment_text),
        "ac_power": _search(r"(\d+\s*kW)交流充电桩", equipment_text),
        "dc_charger_types": sum(1 for item in equipment if "直流" in item["name"] and "充电" in item["name"]),
        "features": sorted(key for key, pattern in FEATURE_PATTERNS.items() if re.search(pattern, scope_text)),
    }
    # 编写器只为工程类项目写正文：充电站专项写法，其他工程总承包 / 施工项目通用写法；软件、服务采购不套用
    if re.search(r"充电桩|充电站|充电设施", facts["name"] + overview):
        facts["domain"] = "charging"
    elif requirement_blocks or re.search(r"EPC|工程总承包|施工总承包|施工招标|施工图", facts["name"] + scope + overview):
        facts["domain"] = "construction"
    else:
        facts["domain"] = "other"
    # “付款方式：”标题下到下一个大标题之前的付款节点原文
    facts["payment_terms"] = _search(r"付款方式[：:]\s*\n((?:(?![一二三四五六七八九十]+、)[^\n]+\n?)+)", text).replace("\n", "")
    facts["requirements"] = build_requirement_tree(body)
    requirement_text = "\n".join(node_text(node) for node in facts["requirements"])
    software_terms = len(re.findall(r"系统|软件|平台|APP|数据库|接口|信息化|数字化|模块", requirement_text, re.I))
    system_project = re.search(r"MES|ERP|系统|软件|平台|信息化|数字化|智慧", facts["name"] + requirement_text[:600], re.I)
    if facts["domain"] == "other" and (software_terms >= 30 or (software_terms >= 6 and system_project)):
        facts["domain"] = "it"  # 软件 / 信息化采购：需求章节以系统功能、接口、数据为主
    # 否决 / 无效 / 实质性条款：交标前必须逐条满足
    veto = []
    for sentence in re.split(r"(?<=[。；;])|\n", text):
        sentence = sentence.strip(" |")
        if 10 <= len(sentence) <= 160 and re.search(r"否决|无效投标|无效响应|视为无效|废标|不予受理|★|实质性(?:要求|条款|响应)", sentence) \
                and not re.search(r"^第.+章|目录|\.{4,}", sentence):
            veto.append(sentence)
    facts["veto_clauses"] = list(dict.fromkeys(veto))[:30]
    # 采购公告里的工期、质保等条款（采购文件不一定有前附表“计划工期”）
    facts["project_terms"] = [_tidy(line) for line in text.split("\n")
                              if re.search(r"工期要求|质保期|服务期|交付期", line) and len(line) < 300][:6]
    return facts
