"""商务评分项的响应（资质证书、类似业绩、团队配置等），用企业资料逐项对照评分标准。

评分标准里点名的证书、业绩口径、承诺条款都从评分原文读取；企业资料里没有的内容标“待填”，不代填。
"""

from __future__ import annotations

import re
from collections.abc import Callable

from api.services.bidgen.business_sections import certificate_blocks
from api.services.bidgen.document_model import ComposeContext, paragraph, table
from api.services.bidgen.epc_writer import Draft
from api.services.bidgen.profile import CERT_CATEGORIES, usable_certificates

# 评分标准中的证书名称 → 企业资料证件类别
CERTIFICATE_NAMES: tuple[tuple[str, str], ...] = (
    (r"高新技术企业", "high_tech"),
    (r"信息技术服务管理体系|ISO\s*/?\s*IEC\s*20000|ISO20000", "itss"),
    (r"信息安全管理体系|ISO\s*/?\s*IEC\s*27001|ISO27001", "isms"),
    (r"质量管理体系|ISO\s*9001", "quality_system"),
    (r"环境管理体系|ISO\s*14001", "environment_system"),
    (r"职业健康安全管理体系|ISO\s*45001", "safety_system"),
    (r"能源管理体系|ISO\s*50001", "energy_system"),
    (r"能力成熟度|CMMI", "cmmi"),
    (r"专精特新", "specialized"),
    (r"安全生产许可证", "safety_license"),
)
_SCORING_LINE = re.compile(r"得\s*\d+(?:\.\d+)?\s*分|不得分|此项最高|最高得|分值|计分|[^。]{0,20}的[：:]$")


def is_scoring_text(text: str) -> bool:
    """格式模板里照抄的评分语句（“……的，得1分”），响应文件中不应出现。"""
    return bool(_SCORING_LINE.search(text))


def _p(text: str) -> dict:
    return paragraph(text)


def _validity(certificate: dict) -> str:
    if certificate.get("perpetual"):
        return "长期有效"
    return f"有效期至{certificate['validTo']}" if certificate.get("validTo") else "有效期见证书"


def write_certificates(ctx: ComposeContext, rule: str) -> Draft:
    # 逐条（“1.具有……的，得1分”）读取，避免把“供应商具有以下有效证书的：”这类引导语当成证书名
    required = re.findall(r"(?:^|\d+\s*[.．、]|[；;。])\s*具有([^；;。：:]{2,60}?)的[，,]\s*得\s*(\d+(?:\.\d+)?)\s*分", rule)
    usable = usable_certificates(ctx.profile, ctx.tender.get("bid_deadline", ""))
    rows, held = [], []
    for index, (name, score) in enumerate(required, start=1):
        category = next((code for pattern, code in CERTIFICATE_NAMES if re.search(pattern, name, re.I)), "")
        found = [c for c in usable if c["category"] == category] if category else []
        if found:
            held.append((category, name))
            rows.append([str(index), name, f"{score}分", found[0]["name"], _validity(found[0]), "扫描件附后"])
        else:
            rows.append([str(index), name, f"{score}分", ctx.need(f"{name}（企业资料中未找到，如有请上传）"), "—", "—"])
    blocks = [_p("我方对照采购文件商务评分标准中的资质证书要求，逐项提供证书如下（扫描件均加盖我方公章）：")]
    if rows:
        blocks.append(table(["序号", "评分要求", "分值", "我方证书", "有效期", "证明材料"], rows, caption="资质证书响应表",
                            widths=[1.2, 5.6, 1.4, 4.0, 2.6, 1.8]))
    for category, name in held:
        blocks += certificate_blocks(ctx, (category,), f"{name}扫描件")
    return Draft(blocks=blocks)


_PERFORMANCE_FIELDS = (
    (r"项目名称|合同名称", "project"),
    (r"委托单位|采购人|买方|发包人|业主|客户", "buyer"),
    (r"合同金额|金额", "amount"),
    (r"签订时间|签订日期|时间|年份", "year"),
    (r"产品|内容", "product"),
)


def write_performance(ctx: ComposeContext, rule: str, template_tables: list[list[list[str]]]) -> Draft:
    keyword = re.search(r"([A-Za-z0-9]{2,10}|[一-鿿]{2,8}?)相关(?:系统)?(?:建设|实施|项目)", rule)
    keyword = keyword.group(1) if keyword else ""
    since = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日以来", rule)
    each = re.search(r"每(?:满足|提供)一(?:项|个)[^，。]{0,8}?得\s*(\d+(?:\.\d+)?)\s*分", rule)
    cap = re.search(r"最高得\s*(\d+(?:\.\d+)?)\s*分", rule)
    needed = int(float(cap.group(1)) / float(each.group(1))) if each and cap else 0
    since_year = int(since.group(1)) if since else 0
    matched = []
    for item in ctx.profile.get("performances", []):
        text = f"{item.get('project', '')}{item.get('product', '')}"
        year = re.search(r"(\d{4})", str(item.get("year", "")))
        if keyword and keyword.lower() not in text.lower():
            continue
        if since_year and year and int(year.group(1)) < since_year:
            continue
        matched.append(item)
    header = template_tables[0][0] if template_tables else ["序号", "项目名称", "委托单位", "合同金额（元）", "合同签订时间"]
    rows = []
    for index, item in enumerate(matched[:needed or None], start=1):
        row = []
        for title in header:
            if re.search(r"序号", title):
                row.append(str(index))
                continue
            key = next((field for pattern, field in _PERFORMANCE_FIELDS if re.search(pattern, title)), "")
            value = str(item.get(key, "") or "")
            row.append(value or ("见合同" if key in ("amount", "year") else ""))
        rows.append(row)
    scope = (f"{since.group(1)}年{since.group(2)}月{since.group(3)}日以来" if since else "") + (f"{keyword}相关系统建设或实施" if keyword else "类似项目")
    lead = f"我方{scope}的合同业绩如下" + (f"（评分最多计{needed}项）" if needed else "") + "，合同关键页扫描件附后，合同关键页均体现建设内容、合同金额、签订时间及签署页："
    if not rows:
        rows = [[ctx.need(f"{scope}合同业绩（企业资料中未找到符合条件的业绩）") if index == 0 else "" for index in range(len(header))]]
    blocks = [_p(lead), table(header, rows, caption="类似项目业绩汇总表")]
    blocks += certificate_blocks(ctx, ("performance_proof",), f"{scope}合同关键页或合同相对方出具的业绩证明扫描件")
    return Draft(blocks=blocks)


def write_team(ctx: ComposeContext, rule: str, template_tables: list[list[list[str]]]) -> Draft:
    blocks = []
    certificate_rule = re.search(r"每具有一项(.+?)的，得", rule)
    if certificate_rule:
        blocks.append(_p(f"我方拟派项目团队成员的{certificate_rule.group(1)}情况见下表，证书扫描件、近六个月内任一月我方为其缴纳社保的证明材料及个人简历附后。"))
    for rows in template_tables:
        if rows:
            width = max(len(row) for row in rows)
            body = [row + [""] * (width - len(row)) for row in rows[1:]] or [[""] * width]
            blocks.append(table(rows[0] + [""] * (width - len(rows[0])), body))
    if template_tables:
        ctx.need("项目团队成员信息（姓名、职称、执业或职业资格证书）及简历")
    promise = re.search(r"供应商自行承诺[：:]\s*(.+?)(?:有此承诺得\s*\d+\s*分|（供应商自行承诺|$)", rule, re.S)
    if promise:
        clauses = [c.strip("；;。 ") for c in re.split(r"(?=[①②③④⑤⑥])", promise.group(1)) if c.strip("；;。 ")]
        blocks.append(paragraph("团队配置承诺函", bold=True, align="center", indent=False))
        blocks.append(paragraph(f"致：{ctx.purchaser_names}", indent=False))
        blocks.append(_p(f"我方参加{ctx.project_name}采购活动，就本项目团队配置郑重承诺如下："))
        for clause in clauses:
            text = re.sub(r"供应商(?:需要|需|须|应)?", "我方", clause)
            blocks.append(_p(text + "。"))
        blocks.append(_p("上述承诺无任何附加条件，如有违反，愿意接受采购人依据合同及供应商管理办法作出的处理。"))
        blocks.append(ctx.signature_block())
    return Draft(blocks=blocks)


Topic = Callable[..., Draft]


def write_business_point(ctx: ComposeContext, factor: str, rule: str, template_tables: list[list[list[str]]]) -> Draft | None:
    if re.search(r"资质|证书|认证", factor):
        return write_certificates(ctx, rule)
    if re.search(r"业绩", factor):
        return write_performance(ctx, rule, template_tables)
    if re.search(r"团队|人员配置|项目组|人员配备", factor):
        return write_team(ctx, rule, template_tables)
    return None


__all__ = ["CERTIFICATE_NAMES", "CERT_CATEGORIES", "is_scoring_text", "write_business_point"]
