"""招标文件分析报告与投标文件废标风险自查（不调用模型）。

分析：从解析结果整理项目要素、资格条件、分值构成、否决 / 实质性条款、需提交材料清单与关键时间。
自查：把生成的投标文件与招标要求、企业资料逐项对照，列出可能导致废标或失分的问题及处理建议。
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

from api.services.bidgen.document_model import PENDING_PREFIX, Section

LEVEL_HIGH, LEVEL_MEDIUM, LEVEL_LOW = "高", "中", "低"


def _clause(tender: dict, *names: str) -> str:
    for clause in tender.get("key_clauses", []):
        if any(name in re.sub(r"\s", "", clause["name"]) for name in names):
            return re.sub(r"\s+", " ", clause["content"]).strip()
    return ""


def _money(text: str, *labels: str) -> str:
    for label in labels:
        matched = re.search(rf"{label}[^\d]{{0,12}}([\d,]+(?:\.\d+)?)\s*(万?元)", text)
        if matched:
            value = float(matched.group(1).replace(",", ""))
            return f"{value / 10000:g}万元" if matched.group(2) == "元" and value >= 10000 else f"{value:g}{matched.group(2)}"
    return ""


def analyze_tender(tender: dict) -> dict:
    facts = tender.get("project_facts") or {}
    bond_text = _clause(tender, "投标保证金", "响应保证金", "保证金")
    price_text = _clause(tender, "最高投标限价", "采购限价", "最高限价", "招标控制价")
    def term(word: str) -> str:
        found = next((t for t in facts.get("project_terms", []) if word in t), "")
        return re.sub(r"^[\d.\s]*[^：:]{0,8}[：:]\s*", "", found)

    basics = [
        ("项目名称", tender.get("project_name", "")),
        ("项目编号", tender.get("tender_no", "")),
        ("招标（采购）人", "、".join(tender.get("purchasers", []))),
        ("代理机构", tender.get("agency", "")),
        ("投标（响应）截止时间", (tender.get("bid_deadline") or "").replace("T", " ")),
        ("有效期", f"{tender['validity_days']}日历天" if tender.get("validity_days") else ""),
        ("工期 / 服务期", facts.get("schedule") or term("工期")),
        ("质保期", facts.get("warranty") or term("质保")),
        ("保证金", ("需要" + (f"，金额{_money(bond_text, '金额')}" if _money(bond_text, '金额') else "")) if tender.get("bid_bond_required") else "不需要"),
        ("最高限价", "；".join(filter(None, [f"设计费{facts['design_price_cap']}" if facts.get("design_price_cap") else "",
                                        f"工程费{facts['engineering_price_cap']}" if facts.get("engineering_price_cap") else "",
                                        _money(price_text, "最高限价", "限价")]))),
        ("联合体", "接受" if tender.get("consortium_allowed") else "不接受"),
        ("分包", {True: "允许", False: "不允许"}.get(facts.get("subcontract_allowed"), "")),
    ]
    groups: dict[str, float] = {}
    for criterion in tender.get("criteria", []):
        groups[criterion["group"] or criterion["volume"]] = groups.get(criterion["group"] or criterion["volume"], 0) + (criterion["max_score"] or 0)
    materials = []
    for item in tender.get("format_items", []):
        text = " ".join(block.get("text", "") for block in item.get("template", []))
        needs = re.findall(r"(?:附|提供)([^，。；]{2,30}?(?:扫描件|复印件|证明|截图|凭证|保函))", text)
        materials.append({"item": f"{item['number']} {item['title']}", "attachments": list(dict.fromkeys(needs))[:6]})
    return {
        "basics": [{"label": label, "value": value} for label, value in basics if value],
        "qualifications": tender.get("general_qualifications", [])[:20],
        "scoreGroups": [{"group": group, "maxScore": score} for group, score in groups.items()],
        "criteria": [{"group": c["group"], "factor": c["factor"], "maxScore": c["max_score"]} for c in tender.get("criteria", [])],
        "vetoClauses": facts.get("veto_clauses", []),
        "materials": materials,
    }


def risk_check(tender: dict, profile: dict, roots: list[Section], ctx, certificate_check: dict) -> list[dict]:
    """返回 [{level, item, detail, suggestion}]，按风险等级排序。"""
    risks: list[dict] = []

    def add(level: str, item: str, detail: str, suggestion: str) -> None:
        risks.append({"level": level, "item": item, "detail": detail, "suggestion": suggestion})

    deadline = tender.get("bid_deadline")
    if deadline and datetime.fromisoformat(deadline).date() < date.today():
        add(LEVEL_HIGH, "截止时间", f"投标（响应）截止时间 {deadline.replace('T', ' ')} 已过", "确认是否为本次采购的最新文件或澄清修改后的时间")
    company = profile["company"]
    if not company.get("name"):
        add(LEVEL_HIGH, "投标人名称", "企业资料未填写投标人名称，文件中多处签章与表单无法填写", "在“企业资料”中填写与营业执照一致的名称")
    if not profile["legalRep"].get("name"):
        add(LEVEL_HIGH, "法定代表人", "未填写法定代表人姓名，身份证明、授权委托书等签署页不完整", "在“企业资料”中补充法定代表人信息")
    text = "\n".join(root.plain_text() for root in roots)
    if any("授权委托书" in root.title for root in roots) and not profile["agent"].get("name"):
        add(LEVEL_MEDIUM, "委托代理人", "文件含授权委托书，但未填写委托代理人", "如由委托代理人签署，请补充其姓名与身份证号；法定代表人亲自签署时可删除该页")
    if tender.get("bid_bond_required") and not any(c["category"] == "bid_bond" for c in profile.get("certificates", [])):
        add(LEVEL_HIGH, "保证金", "招标文件要求提交保证金，企业资料中没有保证金缴纳凭证或保函", "缴纳后上传汇款凭证 / 保函扫描件")
    for item in certificate_check.get("items", []):
        if item.get("status") == "expired":
            add(LEVEL_HIGH, "证件过期", f"{item.get('categoryLabel', '')}“{item.get('name', '')}”：{item.get('message', '')}", "更换为有效证件后重新生成")
    for missing in certificate_check.get("missing", []):
        add(LEVEL_HIGH, "必备证件缺失", f"缺少{missing.get('categoryLabel', '')}", "在“企业资料”中上传")
    pending_hits = text.count(PENDING_PREFIX)
    if pending_hits:
        hints = list(dict.fromkeys(p["hint"] for p in ctx.pending if "请确认" not in p["hint"]))
        add(LEVEL_MEDIUM, "待填内容", f"正文中仍有 {pending_hits} 处【待填】标记，涉及：{'、'.join(hints[:8])}{'等' if len(hints) > 8 else ''}",
            "补全企业资料后重新生成，或在 Word 中逐处补写并删除标记")
    for entry in ctx.pending:
        if "请确认" in entry["hint"]:
            add(LEVEL_MEDIUM, "承诺确认", entry["hint"], "确认公司可以作出该承诺；不能承诺的请改写或删除，避免履约风险")
    for row in _missing_scores(roots):
        add(LEVEL_MEDIUM, "可能失分", row, "补充证明材料可争取该项分值")
    if any(re.search(r"报价", item["title"]) for item in tender.get("format_items", [])):
        add(LEVEL_HIGH, "报价", "报价表金额需由投标人填写，系统不代填", "填写后核对大写与小写一致、不超过最高限价，分项合计等于总价")
    name = tender.get("project_name", "")
    if name and text.count(name) < 1:
        add(LEVEL_MEDIUM, "项目名称", "正文中未出现与招标文件完全一致的项目名称", "核对封面与各签署页的项目名称")
    if tender.get("format_items") and not tender.get("criteria"):
        add(LEVEL_LOW, "评分标准", "未识别到评分标准，技术部分无法按评分点逐项响应", "核对招标文件评标办法章节")
    if (tender.get("project_facts") or {}).get("veto_clauses"):
        add(LEVEL_LOW, "否决条款", f"招标文件含 {len(tender['project_facts']['veto_clauses'])} 条否决 / 实质性条款，见分析报告", "逐条对照投标文件确认已满足")
    order = {LEVEL_HIGH: 0, LEVEL_MEDIUM: 1, LEVEL_LOW: 2}
    return sorted(risks, key=lambda risk: order[risk["level"]])


def _missing_scores(roots: list[Section]) -> list[str]:
    rows = []
    for root in roots:
        for node in root.walk():
            for block in node.blocks:
                if block["type"] != "table" or block.get("caption") not in ("资质证书响应表", "类似项目业绩汇总表"):
                    continue
                for row in block["rows"]:
                    if any(PENDING_PREFIX in cell for cell in row):
                        score = next((cell for cell in row if re.fullmatch(r"\d+(?:\.\d+)?分", cell)), "")
                        label = row[1] if len(row) > 1 and row[1] else row[0]
                        rows.append(f"{node.title}：{re.sub(r'【待填：|（企业资料中.*$|】', '', label)[:40]}{'（' + score + '）' if score else ''}")
    return rows


def _font(run, size: float, bold: bool = False) -> None:
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = "宋体"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")


def write_report(path: Path, tender: dict, analysis: dict, risks: list[dict], coverage: list[dict]) -> Path:
    document = Document()

    def heading(text: str, size: float = 14) -> None:
        paragraph = document.add_paragraph()
        _font(paragraph.add_run(text), size, bold=True)

    def line(text: str) -> None:
        _font(document.add_paragraph().add_run(text), 10.5)

    def grid(header: list[str], rows: list[list[str]]) -> None:
        table = document.add_table(rows=1, cols=len(header))
        table.style = "Table Grid"
        for cell, text in zip(table.rows[0].cells, header):
            _font(cell.paragraphs[0].add_run(text), 10, bold=True)
        for row in rows:
            cells = table.add_row().cells
            for cell, text in zip(cells, row):
                _font(cell.paragraphs[0].add_run(str(text)), 10)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _font(title.add_run("招标文件分析与投标文件风险自查报告"), 18, bold=True)
    line(f"项目：{tender.get('project_name', '')}　　生成时间：{datetime.now():%Y-%m-%d %H:%M}")
    heading("一、风险自查（交标前逐项处理）")
    grid(["等级", "检查项", "问题", "处理建议"], [[r["level"], r["item"], r["detail"], r["suggestion"]] for r in risks] or [["—", "—", "未发现问题", "—"]])
    heading("二、项目要素")
    grid(["要素", "内容"], [[b["label"], b["value"]] for b in analysis["basics"]])
    if analysis["scoreGroups"]:
        heading("三、分值构成")
        grid(["评审部分", "评审要素", "分值"], [[c["group"], c["factor"], f"{c['maxScore']:g}" if c["maxScore"] is not None else "—"] for c in analysis["criteria"]])
    if coverage:
        heading("四、评分点覆盖自检")
        grid(["评分要素", "评审要点", "自检补写"], [[c["factor"], "、".join(c["aspects"]) or "—", "、".join(c["added"]) or "无"] for c in coverage])
    if analysis["qualifications"]:
        heading("五、资格条件")
        for text in analysis["qualifications"]:
            line(text)
    if analysis["vetoClauses"]:
        heading("六、否决与实质性条款（须逐条满足）")
        for index, text in enumerate(analysis["vetoClauses"], start=1):
            line(f"{index}. {text}")
    heading("七、需提交材料清单")
    grid(["格式条目", "需附材料"], [[m["item"], "、".join(m["attachments"]) or "按格式填写"] for m in analysis["materials"]])
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(path))
    return path
