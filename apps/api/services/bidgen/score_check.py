"""评分自查：按招标文件评分标准逐项核对生成的投标文件是否响应、能拿到哪一档。

三层判定：
  1. 定位 —— 该评分项在生成稿中是否有对应章节（回读生成的 Word 再确认标题真实存在）；
  2. 取证 —— 响应章节是否还有“待填”项、证明材料是否仍是空白粘贴框；
  3. 估分 —— 能由企业资料量化的评分项（设备数量、证书、人数、业绩等）直接套评分细则算出档位。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from api.services.bidgen.docx_reader import read_docx_blocks
from api.services.bidgen.document_model import PENDING_PREFIX, Section
from api.services.bidgen.profile import usable_certificates

STATUS_FULL = "full"
STATUS_PARTIAL = "partial"
STATUS_MISSING = "missing"
STATUS_MANUAL = "manual"
STATUS_EXTERNAL = "external"
SCORE_UNAVAILABLE_MESSAGE = "未识别到适用的评分标准，暂不可评分；请补充包含评分细则的 Word 招标文件后重新解析。"

STATUS_LABELS = {
    STATUS_FULL: "满足（可得最高档）",
    STATUS_PARTIAL: "部分满足（可提升）",
    STATUS_MISSING: "未响应 / 资料缺失",
    STATUS_MANUAL: "已响应，需人工确认档位",
    STATUS_EXTERNAL: "招标人依据系统数据评定",
}

_EXTERNAL_FACTORS = ("运行绩效", "综合绩效", "履约风险")
_EQUIPMENT_RULE = re.compile(r"([一-龥]{2,10}?(?:设备|生产线))\s*≥\s*(\d+)\s*[套条台][^【]*【\s*(\d+(?:\.\d+)?)\s*】[^【]*【\s*(\d+(?:\.\d+)?)\s*】")


def _number(value: object) -> float | None:
    matched = re.search(r"-?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(matched.group(0)) if matched else None


def _tier(count: float | None, tiers: list[tuple[float, float]], default: float) -> float | None:
    """``tiers`` 为 [(门槛, 得分)]，按门槛从高到低匹配。"""
    if count is None:
        return None
    for threshold, score in sorted(tiers, reverse=True):
        if count >= threshold:
            return score
    return default


def _certificate_count(certificates: list[dict], *categories: str) -> int:
    return len({c["category"] for c in certificates if c["category"] in categories})


def _estimate(criterion: dict, profile: dict, certificates: list[dict], lots: list[dict]) -> tuple[float | None, str, list[str]]:
    """返回 (预估得分, 判定依据, 提升建议)。无法量化时得分为 None。"""
    factor, rule = criterion["factor"], criterion["rule"]
    declarations, staff = profile["declarations"], profile["staff"]
    maximum = criterion["max_score"]
    if "售后服务" in factor:
        count = _number(profile["service"]["provinceCount"])
        if count is None:
            return None, "未登记售后服务网点覆盖省份数量", ["在企业资料“售后服务”中填写网点覆盖省份数量并附网点清单"]
        score = _tier(count, [(20, 5), (10, 3)], 1)
        return score, f"售后服务网点覆盖 {count:g} 个省份", [] if score == maximum else ["网点覆盖 20 个及以上省份可得满分 5 分"]
    if "应急保供" in factor:
        if declarations["emergencyProof"]:
            return maximum, "已声明具备应急抢险保电突出贡献证明材料", ["请确认已附供货证明 / 感谢信等原件扫描件"]
        return criterion["min_score"], "未提供应急保供突出贡献证明", ["补充应急抢险保电物资供应突出贡献证明可得 20 分"]
    if "绿色发展规划" in factor:
        score = (1 if declarations["greenPlan"] else 0) + (2 if _certificate_count(certificates, "energy_system") else 0)
        systems = _certificate_count(certificates, "quality_system", "environment_system", "safety_system")
        score += systems
        advice = []
        if not declarations["greenPlan"]:
            advice.append("建立并提交绿色发展规划制度（+1 分）")
        if not _certificate_count(certificates, "energy_system"):
            advice.append("取得能源管理体系认证证书（+2 分）")
        if systems < 3:
            advice.append(f"质量 / 环境 / 职业健康安全管理体系证书仅 {systems} 项有效，补齐 3 项可得 3 分")
        return float(score), f"绿色发展规划{'已' if declarations['greenPlan'] else '未'}建立；有效三体系证书 {systems} 项；能源管理体系证书{'有' if score - systems - (1 if declarations['greenPlan'] else 0) else '无'}", advice
    if "环境影响评价" in factor:
        count = sum(1 for key in ("esgReport", "eiaReport", "wasteReport") if declarations[key])
        score = 5.0 if count == 3 else 3.0 if count else 0.0
        return score, f"具备 {count} 类环境影响报告", [] if count == 3 else ["ESG 报告、环评 / 能评报告、废水 / 废气 / 废固报告三类齐全可得 5 分"]
    if "绿电绿证" in factor:
        held = _certificate_count(certificates, "green_power")
        return (5.0 if held else 0.0), "已登记绿色电力证书" if held else "未登记绿色电力证书", [] if held else ["取得绿色电力证书或凭证可得 5 分"]
    if "企业创新成果" in factor:
        levels = " ".join(str(a.get("level", "")) for a in profile["awards"])
        if "国家" in levels:
            return 8.0, "近 3 年有国家级科技创新成果", []
        if "省" in levels:
            return 6.0, "近 3 年有省级科技创新成果", ["取得国家级科技创新成果可得 8 分"]
        return 4.0, "未登记近 3 年科技创新成果", ["在企业资料“科技奖励 / 成果”中登记国家级或省级成果"]
    if "研发团队" in factor:
        count = _number(staff["senior"])
        if count is None:
            return None, "未登记高级及以上职称人员数量", ["在企业资料“人员”中填写高级及以上职称人员和高级技师人数"]
        score = _tier(count, [(20, 10), (5, 8)], 6)
        return score, f"高级及以上职称人员和高级技师 {count:g} 人", [] if score == maximum else ["人数达到 20 人及以上可得 10 分"]
    if "科研经费" in factor:
        ratios = [_number(f.get("rdRatio")) for f in profile["finance"]]
        ratios = [r for r in ratios if r is not None]
        if not ratios:
            return None, "未登记近 3 年研发投入占比", ["在企业资料“财务”中填写各年度研发费占比"]
        best = max(ratios)
        score = 16.0 if best >= 2 else 14.0 if best > 0 else 12.0
        return score, f"近 3 年研发投入占比最高 {best:g}%", [] if score == maximum else ["任一年研发投入占比达到 2% 以上可得 16 分"]
    if "高新技术企业" in factor:
        held = _certificate_count(certificates, "high_tech")
        return (4.0 if held else 0.0), "持有有效高新技术企业证书" if held else "无有效高新技术企业证书", [] if held else ["登记有效期内的高新技术企业证书"]
    if "创新激励" in factor:
        both = declarations["innovationIncentive"] and declarations["supplyChainMeasures"]
        return (maximum if both else criterion["min_score"]), "已建立创新激励机制与供应链保障措施" if both else "创新激励机制或供应链保障措施资料不全", [] if both else ["同时提供创新激励制度与供应链保障措施证据材料可得 6 分"]
    if "专精特新" in factor:
        held = _certificate_count(certificates, "specialized")
        return (2.0 if held else 0.0), "持有专精特新企业认定" if held else "未登记专精特新认定", [] if held else ["登记专精特新企业认定证书"]
    if "数智化" in factor or "质量奖" in factor or "实体清单" in factor:
        return None, "需依据工信部等部委认定文件 / 获奖证书人工判定", ["如有对应认定或证明文件，请在响应章节粘贴扫描件"]
    if "供货业绩" in factor:
        required = max((_number(lot.get("performance_requirement")) or 0 for lot in lots), default=0)
        total = sum(_number(p.get("quantity")) or 0 for p in profile["performances"])
        if not required or not total:
            return None, "未登记供货业绩数量，或招标文件未给出业绩门槛", ["在企业资料“供货业绩”中登记同类产品合同及数量"]
        ratio = total / required
        score = 17.0 if ratio >= 3 else 14.0 if ratio >= 2 else 11.0 if ratio >= 1.5 else 8.0 if ratio >= 1 else None
        if score is None:
            return criterion["min_score"], f"登记业绩 {total:g}，低于公告要求 {required:g}", ["业绩需至少满足资格预审公告要求"]
        return score, f"登记业绩 {total:g}，为公告要求 {required:g} 的 {ratio:.1f} 倍", [] if score == maximum else ["业绩超过公告要求 200% 及以上可得 17 分"]
    if "创新能力" in factor:
        standard_levels = " ".join(str(s.get("level", "")) for s in profile["standards"])
        standard = 4 if re.search(r"国家|行业|国标|行标", standard_levels) else 3 if "团" in standard_levels else 1 if "企业" in standard_levels else 0
        inventions = sum(1 for p in profile["patents"] if "发明" in str(p.get("kind", "")))
        award_levels = " ".join(str(a.get("level", "")) for a in profile["awards"])
        award = 4 if "国家" in award_levels else 2 if "省" in award_levels or "部" in award_levels else 0
        score = float(standard + min(inventions, 4) + award)
        advice = []
        if standard < 4:
            advice.append("登记参与制定的行业及以上标准（4 分）")
        if inventions < 4:
            advice.append(f"与所投产品相关的发明专利已登记 {inventions} 项，达到 4 项可得 4 分")
        if award < 4:
            advice.append("登记与投标产品相关的国家级 / 省部级科技奖励")
        return score, f"标准得 {standard} 分、发明专利 {inventions} 项、科技奖励得 {award} 分", advice
    if "设备工艺" in factor:
        missing = int(not declarations["processDocsComplete"]) + int(not declarations["materialDocsComplete"])
        score = max(0.0, 3.0 - missing)
        return score, "工艺文件、原材料 / 组部件管理文件" + ("齐全" if not missing else f"缺少 {missing} 类"), [] if not missing else ["补齐工艺文件与原材料 / 组部件管理文件"]
    if "生产试验设备" in factor:
        owned = {e.get("name", ""): _number(e.get("count")) for e in profile["equipment"]}
        score, details, advice, unknown = 0.0, [], [], False
        for name, threshold, high, low in _EQUIPMENT_RULE.findall(rule):
            count = next((value for key, value in owned.items() if name[:4] in key or key[:4] in name), None)
            if count is None:
                unknown = True
                advice.append(f"填写“{name}”数量（≥{threshold} 可得 {high} 分）")
                continue
            got = float(high) if count >= float(threshold) else float(low)
            score += got
            details.append(f"{name} {count:g}（门槛 {threshold}）得 {got:g} 分")
            if got < float(high):
                advice.append(f"{name}达到 {threshold} 及以上可得 {high} 分")
        if unknown and not details:
            return None, "未登记生产设备数量", advice
        return score, "；".join(details) + ("；部分设备未登记数量" if unknown else ""), advice
    if "人力资源" in factor:
        count = _number(staff["seniorEngineers"])
        if count is None:
            return None, "未登记与投标产品相关的高级工程师（含高级技师）人数", ["在企业资料“人员”中填写相关高级工程师人数"]
        score = 2.0 if count > 4 else 1.0 if count > 2 else 0.0
        return score, f"相关高级工程师（含高级技师）{count:g} 人", [] if score == maximum else ["4 人以上可得 2 分"]
    if "产品制造质量控制" in factor:
        systems = len(profile["systems"])
        software = 3.0 if systems >= 2 else 2.0 if systems == 1 else 0.0
        return software + 2.0, f"应用 {systems} 项智能制造系统软件得 {software:g} 分；质量控制措施已完整响应，按“情况良好”计 2 分", [] if systems >= 2 else ["应用 MES、智能仓储等 2 项及以上系统软件可得 3 分"]
    if "绿色低碳" in factor:
        count = _certificate_count(certificates, "green_factory", "green_supply_chain")
        if count >= 2:
            return 3.0, "取得绿色工厂、绿色供应链管理企业 2 项认证", []
        if count == 1:
            return 2.0, "取得 1 项工信部绿色认证", ["同时取得绿色工厂与绿色供应链管理企业认证可得 3 分"]
        return (1.0 if declarations["greenRecycling"] else 0.0), "未取得工信部绿色认证", ["登记绿色工厂 / 绿色供应链管理企业认证"]
    if "碳足迹" in factor:
        held = _certificate_count(certificates, "carbon_footprint")
        return (1.0 if held else 0.0), "已登记产品碳足迹证书" if held else "未登记产品碳足迹证书", [] if held else ["取得产品碳足迹证书可得 1 分"]
    if "经营状况" in factor:
        return None, "由评标委员会依据财务报表综合评价", ["确保已附近三年经审计财务报表"]
    return None, "该评分项无法由企业资料直接量化", []


def _read_back_headings(path: Path) -> set[str]:
    """回读生成的 Word，取出真实写入的全部标题文字。"""
    try:
        blocks = read_docx_blocks(path)
    except ValueError:
        return set()
    return {re.sub(r"\s+", "", b.text) for b in blocks if b.kind == "paragraph" and b.level}


def run_score_check(
    roots: list[Section],
    criteria: list[dict],
    profile: dict,
    lots: list[dict],
    bid_deadline: str,
    document_path: Path | None = None,
) -> dict:
    """生成评分自查报告。``criteria`` 为本次所投分标适用的评分项。"""
    certificates = usable_certificates(profile, bid_deadline)
    expired = [c["name"] for c in profile["certificates"] if c not in certificates]
    headings = _read_back_headings(document_path) if document_path else set()
    located: dict[str, list[Section]] = {}
    for root in roots:
        for node in root.walk():
            for criterion_id in node.criteria:
                located.setdefault(criterion_id, []).append(node)
    items = []
    for criterion in criteria:
        sections = located.get(criterion["criterion_id"], [])
        deduction = (criterion["max_score"] or 0) <= 0 and (criterion["min_score"] or 0) < 0
        review_only = bool(criterion.get("evaluation_mode"))
        external = not review_only and any(keyword in criterion["factor"] for keyword in _EXTERNAL_FACTORS)
        text = "\n".join(section.plain_text() for section in sections)
        pending = text.count(PENDING_PREFIX)
        placeholders = sum(1 for s in sections for n in s.walk() for b in n.blocks if b["type"] == "placeholder")
        written = [s for s in sections if not headings or re.sub(r"\s+", "", s.heading) in headings]
        estimated, basis, advice = (None, "", [])
        if review_only:
            basis = ("报价评分需要有效报价、评标基准价等数据，暂不可估分" if criterion["evaluation_mode"] == "price"
                     else "已识别招标原表评分标准，尚未核验投标正文和证明材料，需人工评估")
        elif deduction:
            clean = profile["declarations"]["noAdverseRecord"]
            estimated, basis = (0.0, "已出具诚信自查声明：不存在所列扣分情形") if clean else (None, "未声明无不良记录，需逐项核实")
        elif not external:
            estimated, basis, advice = _estimate(criterion, profile, certificates, lots)
        advice = list(advice)
        if review_only:
            status = STATUS_MANUAL
            advice.append("请按原表逐项核验；识别出评分条款不代表已经响应或取得相应分数")
        elif external:
            status = STATUS_EXTERNAL
            basis = basis or "该项由招标人依据供应商绩效评价 / 历史履约数据评定，投标文件无需单独响应"
        elif not written:
            status = STATUS_MISSING
            advice.insert(0, "生成稿中没有该评分项的响应章节")
        elif pending:
            status = STATUS_MISSING if estimated is None else STATUS_PARTIAL
            advice.insert(0, f"响应章节中还有 {pending} 处“待填”内容，请在企业资料中补全后重新生成")
        elif estimated is None:
            status = STATUS_MANUAL
        elif criterion["max_score"] is not None and estimated >= criterion["max_score"]:
            status = STATUS_FULL
        else:
            status = STATUS_PARTIAL
        if placeholders and status in (STATUS_FULL, STATUS_PARTIAL, STATUS_MANUAL):
            advice.append(f"响应章节预留了 {placeholders} 处证明材料粘贴位，定稿前须粘贴对应扫描件")
        items.append(
            {
                "criterionId": criterion["criterion_id"],
                "volume": criterion["volume"],
                "category": criterion["category"],
                "group": criterion["group"],
                "factor": criterion["factor"],
                "minScore": criterion["min_score"],
                "maxScore": criterion["max_score"],
                "rule": criterion["rule"],
                "deduction": deduction,
                "status": status,
                "statusLabel": "待评估" if review_only else STATUS_LABELS[status],
                "responseVerified": bool(written) and not review_only,
                "estimatedScore": estimated,
                "basis": basis,
                "sections": [{"key": s.key, "heading": s.heading} for s in written],
                "pendingCount": pending,
                "placeholderCount": placeholders,
                "advice": advice,
            }
        )
    volumes = []
    for volume, label in (("business", "商务 / 其他评分"), ("technical", "技术评分"), ("price", "报价评分")):
        scoped = [i for i in items if i["volume"] == volume]
        if not scoped:
            continue
        scorable = [i for i in scoped if not i["deduction"] and i["status"] != STATUS_EXTERNAL]
        volumes.append(
            {
                "volume": volume,
                "label": label,
                "total": len(scoped),
                "counts": {status: sum(1 for i in scoped if i["status"] == status) for status in STATUS_LABELS},
                "maxScore": sum(i["maxScore"] or 0 for i in scorable),
                "estimatedScore": (sum(i["estimatedScore"] for i in scorable if i["estimatedScore"] is not None)
                                   if any(i["estimatedScore"] is not None for i in scorable) else None),
                "unestimated": sum(1 for i in scorable if i["estimatedScore"] is None),
                "externalMaxScore": sum(i["maxScore"] or 0 for i in scoped if i["status"] == STATUS_EXTERNAL),
            }
        )
    responded = sum(1 for i in items if i["status"] != STATUS_MISSING and
                    (i["responseVerified"] or i["status"] == STATUS_EXTERNAL))
    passed = bool(items) and all(i["status"] != STATUS_MISSING for i in items) and not any(c.get("evaluation_mode") for c in criteria)
    return {
        "checkedAt": datetime.now().isoformat(timespec="seconds"),
        "items": items,
        "volumes": volumes,
        "coverage": round(100 * responded / len(items), 1) if items else None,
        "status": "blocked" if not items else "ready" if passed else "warning",
        "message": SCORE_UNAVAILABLE_MESSAGE if not items else "",
        "expiredCertificates": expired,
        "readBack": bool(headings),
        "passed": passed,
    }
