"""通用编写器：任何行业的评分点都能写出正文（不调用模型）。

1. 拆评分点：从评分标准中读出“包括但不限于A、B、C”“（1）……（2）……”等评审要点；
2. 找依据：在采购需求结构树中找与要点最相关的条款（汉字二元组相似度），逐条“采购要求→我方响应”；
3. 补方法：按要点主题从通用管理知识库取写法（质量、进度、安全、服务、培训、应急、人员、供货……），
   代入项目名称、采购人、工期、质保等已抽取事实；
4. 覆盖自检：保证评分标准列出的每个要点都有对应小节（``ensure_coverage``）。
"""

from __future__ import annotations

import re

from api.services.bidgen.document_model import ComposeContext, paragraph, table
from api.services.bidgen.epc_writer import Draft
from api.services.bidgen.requirements import node_text

_LIST_LEAD = re.compile(r"(?:包括但不限于|包含但不限于|包括|包含|涵盖|主要有|内容有|针对)[：:]?(.{4,200}?)(?:等(?:内容|方面|方案|措施)?|。|；|$)")
_ENUM_ITEM = re.compile(r"[（(]\d{1,2}[）)]\s*([^；;。（(]{2,40})")


def _p(text: str) -> dict:
    return paragraph(text)


def bigrams(text: str) -> set[str]:
    value = re.sub(r"[\s\W_]+", "", text)
    return {value[i:i + 2] for i in range(len(value) - 1)} or ({value} if value else set())


def similarity(left: str, right: str) -> float:
    a, b = bigrams(left), bigrams(right)
    return len(a & b) / max(1, min(len(a), len(b))) if a and b else 0.0


def scoring_aspects(rule: str) -> list[str]:
    """评分标准中逐项列出的评审要点（去重、保持原顺序）。"""
    aspects: list[str] = []
    for matched in _LIST_LEAD.finditer(rule):
        for part in re.split(r"[、，,；;]|以及", matched.group(1)):
            part = re.sub(r"^(?:对|的|与|和|在|系统的|项目的)", "", part.strip(" ：:（）()"))
            part = re.sub(r"(?:等|的|进行综合评定|综合评定|方面)$", "", part)
            if 2 <= len(part) <= 16 and not re.search(r"\d+\s*分|得分|评分|供应商|投标人|采购人", part):
                aspects.append(part)
    for matched in _ENUM_ITEM.finditer(rule):
        # 分档评分（“（1）方案完整、科学可行的，11分≤得分≤15分”）不是评审要点
        if re.search(r"≤|≥|得分|\d+\s*分", rule[matched.start():matched.start() + 120].split("；")[0]):
            continue
        part = re.sub(r"(?:合理|可行|完善|齐全|清晰|明确|科学)[、，,]?.*$", "", matched.group(1)).strip()
        if 2 <= len(part) <= 16 and not re.search(r"分|得分", part):
            aspects.append(part)
    return list(dict.fromkeys(aspects))


# 通用管理知识库：(主题正则, [(小节标题, 正文模板)])；模板可用 {project} {owner} {period} {warranty}
_KNOWLEDGE: tuple[tuple[str, list[tuple[str, str]]], ...] = (
    (r"质量", [
        ("质量目标与组织", "我方以“一次做对、持续改进”为质量方针，为{project}设定明确的质量目标：交付成果一次验收合格，用户问题闭环率100%。项目实行项目负责人质量第一责任制，设专职质量管理人员，按照ISO 9001质量管理体系要求编制《项目质量计划》，明确各阶段质量目标、检查点与验收标准。"),
        ("过程质量控制", "质量控制贯穿策划、实施、检查、交付全过程：开工（服务）前对人员进行质量交底；过程中执行自检、互检、专检“三检制”，关键环节设置质量控制点，未经检查确认不得进入下一环节；每周开展质量检查，发现问题登记整改并复查闭环。"),
        ("质量问题处理与持续改进", "建立质量问题台账，对每一项问题记录原因、责任人、整改措施与完成时间；重大质量问题二十四小时内书面报告{owner}并提出处理方案；每月进行质量分析，针对重复发生的问题制定预防措施，持续提升质量水平。"),
    ]),
    (r"进度|工期|供货期|交货期|时间安排|计划", [
        ("进度目标", "我方严格按照采购文件约定的{period}完成全部工作，并在此基础上预留合理机动时间，确保关键节点按期实现。"),
        ("进度计划编制", "中标后立即编制总体进度计划与阶段工作计划，将全部工作分解到周，明确每项任务的开始时间、完成时间、责任人与交付成果；计划经{owner}确认后执行，并作为进度考核依据。"),
        ("进度保证措施", "进度控制实行“周计划、周检查、周纠偏”：每周召开进度例会，对比计划与实际完成情况，滞后任务分析原因并采取增加人员、调整工序、延长工作时间等措施追回；涉及外部配合的事项提前沟通、专人跟踪，确保不因我方原因延误。"),
    ]),
    (r"安全|文明", [
        ("安全管理体系", "我方建立以项目负责人为第一责任人的安全管理体系，落实“一岗双责”，配备专职安全管理人员；全部人员上岗前接受安全教育并考核合格，特种作业人员持证上岗。"),
        ("安全措施", "开展危险源辨识与风险评估，针对主要风险制定控制措施并进行安全技术交底；作业现场设置安全警示标识，配备必要的防护用品与消防器材；每周开展安全检查，隐患定人、定时、定措施整改到位。"),
        ("文明作业", "作业现场保持整洁有序，材料分类堆放、标识清楚，作业结束工完场清；尊重{owner}管理制度，服从现场管理，维护良好的工作秩序与对外形象。"),
    ]),
    (r"环保|环境|绿色|节能", [
        ("环境保护措施", "我方严格遵守国家及地方环境保护法律法规，识别作业过程中的噪声、扬尘、废弃物等环境因素并制定控制措施；废弃物分类收集、合规处置，优先选用节能环保的材料与设备，减少对周边环境的影响。"),
    ]),
    (r"应急|突发|预案|故障处理", [
        ("应急组织与预案", "我方成立由项目负责人任组长的应急处置小组，针对设备故障、人员伤害、自然灾害、公共卫生事件、信息安全事件等可能情形编制专项应急预案，明确报告程序、处置流程、职责分工与联系方式。"),
        ("应急响应与演练", "突发事件发生后，现场人员立即报告并采取先期处置，应急小组接报后第一时间到位指挥；事件处置完毕后形成书面报告，分析原因并完善预案。项目期间定期组织应急演练，确保人员熟悉流程、物资随时可用。"),
    ]),
    (r"售后|运维|维保|维护|服务承诺|服务保障|质保", [
        ("服务承诺", "我方承诺按采购文件要求提供{warranty}的质保及售后服务，质保期内因我方原因产生的问题免费处理。"),
        ("服务响应机制", "设立服务热线与专属服务群，建立“受理—派单—处理—回访”服务闭环：接到报修后快速响应，一般问题远程或电话指导解决，需要现场处理的及时安排人员到场；处理完成后回访确认，服务记录全部归档。"),
        ("服务保障措施", "安排熟悉本项目的专职服务人员，关键岗位设置AB角；储备常用备品备件与工具；定期回访与巡检，主动发现并消除隐患；质保期满后继续提供优惠的长期服务。"),
    ]),
    (r"培训", [
        ("培训方案", "我方为{owner}相关人员提供系统、实用的培训，培训内容涵盖操作使用、日常维护、常见问题处理与安全注意事项；培训采用集中授课、现场实操与答疑相结合的方式，提供培训教材，培训后进行考核，确保参训人员能够独立操作与维护。"),
    ]),
    (r"人员|团队|组织|机构|配备|人力", [
        ("组织机构", "我方为{project}组建专门项目组，实行项目负责人负责制，下设技术、实施（服务）、质量安全、综合保障等岗位，职责明确、分工到人，确保各项工作有人抓、有人管、有人负责。"),
        ("人员配备与管理", "项目组人员均具备相应的专业能力与从业经验，主要人员保持稳定，未经{owner}同意不得更换；实行考勤与绩效考核制度，人员不足或能力不匹配时及时调配补充，保障项目顺利实施。"),
    ]),
    (r"重点|难点|重难点", [
        ("重点难点分析与对策", "我方结合采购需求与同类项目经验，对本项目的重点难点逐项分析：对进度要求高的环节提前准备、并行推进；对质量要求高的环节设置专项控制措施；对需要多方协调的事项指定专人对接。各项对策在实施中动态跟踪，确保落实到位。"),
    ]),
    (r"理解|认识|分析|调研", [
        ("项目理解", "我方认真研读了{project}采购文件，充分理解{owner}的建设（采购）目的与具体要求。本项目的核心是按质、按期、按约完成全部采购内容，并为{owner}提供持续、可靠的后续服务，我方将以此为目标组织全部工作。"),
    ]),
    (r"供货|物流|运输|包装|配送|交货", [
        ("供货组织", "我方根据交货期要求制定供货计划，提前备货并做好出厂检验；货物采用符合标准的包装，防潮、防震、防锈，外包装标明名称、规格、数量及收货信息。"),
        ("运输与交付", "选择可靠的运输方式与承运单位，全程跟踪货物状态；货物到达后会同{owner}开箱验收，核对数量、规格与随货资料，验收发现的问题及时更换或补齐。"),
    ]),
    (r"验收|交付|移交", [
        ("验收与交付", "我方按采购文件与合同约定的标准组织自检，自检合格后书面申请验收，配合{owner}完成验收工作；验收中发现的问题限期整改并复验，交付时同步移交全部技术资料与相关文件。"),
    ]),
    (r"保密|信息安全|数据安全", [
        ("保密措施", "我方与项目人员签订保密协议，对{owner}提供的资料、数据及项目信息严格保密，不向任何第三方泄露；资料按权限管理，项目结束后全部移交或按要求销毁。"),
    ]),
    (r"风险", [
        ("风险管理", "我方在项目启动时识别进度、质量、安全、协调等方面的主要风险，评估发生可能性与影响程度，逐项制定预防与应对措施并指定责任人；项目实施中定期复核风险清单，风险发生时按预案处置并及时报告{owner}。"),
    ]),
    (r"建议|创新|优化", [
        ("合理化建议", "我方结合同类项目经验，在保证满足采购文件全部要求的前提下，就实施方式优化、管理效率提升与后续运行维护提出合理化建议，供{owner}决策参考；所有建议均不改变采购范围、不增加采购人费用。"),
    ]),
    (r"制度|管理措施|内部管理|管理体系", [
        ("管理制度", "我方建立健全项目管理制度，包括岗位责任制、例会制度、请示报告制度、档案管理制度与考核奖惩制度，各项制度在项目启动时向全体人员交底并严格执行。"),
    ]),
    (r"方案|实施|措施|服务内容|工作内容", [
        ("总体思路", "我方以采购文件为依据，坚持“需求导向、计划先行、过程受控、结果可验”的原则组织{project}的实施：先充分理解需求并制定详细计划，再按计划分阶段推进，每个阶段设置检查点与交付成果，确保各项工作按要求完成。"),
        ("实施步骤", "实施分为准备、实施、检查验收三个阶段：准备阶段完成人员、物资、技术准备与计划确认；实施阶段按计划开展各项工作并做好过程记录；检查验收阶段自检合格后配合{owner}验收并完成资料移交。"),
    ]),
)


_KNOWLEDGE_BY_KEY = {
    "理解": "我方认为，{project}的关键在于准确把握{owner}的真实需求，按质、按期、按约完成全部内容，并在实施后提供持续可靠的服务。我方将以采购需求为基线逐条落实，每一项要求均明确责任人、实施措施与检查标准。",
    "重难点": "结合采购需求，本项目的重点在于需求的完整落实与关键节点的按期完成，难点在于多项工作并行时的统筹协调。对策是：实施前将需求逐条分解为任务清单并经{owner}确认；实施中按周跟踪、按节点检查；涉及{owner}配合的事项提前书面沟通并指定专人对接，确保问题当日发现、当日协调。",
    "方案": "我方以采购文件为依据，坚持“需求导向、计划先行、过程受控、结果可验”的原则组织{project}的实施。下面按采购需求的主要条款逐项说明我方的响应与实施措施。",
}


class GenericWriter:
    def __init__(self, ctx: ComposeContext, facts: dict) -> None:
        self.ctx = ctx
        self.facts = facts or {}
        self.tree = self.facts.get("requirements") or []
        warranty = self.facts.get("warranty") or ""
        months = re.search(r"(\d+)\s*个月", " ".join(self.facts.get("project_terms", [])) + warranty)
        self.values = {
            "project": self.facts.get("name") or ctx.project_name,
            "owner": self.facts.get("owner") or ctx.purchaser_names,
            "period": self.facts.get("schedule") or "工期（服务期）",
            "warranty": f"{months.group(1)}个月" if months else (warranty or "合同约定期限"),
        }

    def fill(self, template: str) -> str:
        return template.format(**self.values)

    def related(self, aspect: str, limit: int = 2) -> list[dict]:
        scored = []
        for node in self.tree:
            score = similarity(aspect, node["title"]) * 2 + similarity(aspect, node_text(node)[:200]) * 0.5
            if score >= 1.0 and (node["texts"] or node["items"]):
                scored.append((score, node))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [node for _score, node in scored[:limit]]

    def knowledge(self, aspect: str) -> list[tuple[str, str]]:
        return next((entries for pattern, entries in _KNOWLEDGE if re.search(pattern, aspect)), [])

    def aspect_blocks(self, aspect: str, nodes: list[dict] | None = None) -> list[dict]:
        from api.services.bidgen.it_writer import respond

        blocks = []
        for node in nodes if nodes is not None else self.related(aspect):
            requirement = "；".join(node["texts"][:2] + [f"{i['label']}：{i['text']}" for i in node["items"][:3]])
            blocks.append(_p(f"采购要求（{node['title']}）：{requirement.rstrip('。')}。"))
            blocks.append(_p("我方响应：" + respond(requirement)))
        for _title, template in self.knowledge(aspect):
            blocks.append(_p(self.fill(template)))
        if not blocks:
            blocks.append(_p(self.fill(f"针对“{aspect}”，我方将严格按照采购文件要求制定具体工作措施，明确责任人、工作标准与完成时限，"
                                       "实施过程中定期检查落实情况并向{owner}汇报，确保该项要求全面响应、落实到位。")))
        return blocks

    def overview_nodes(self) -> list[dict]:
        """需求树中可作为章节骨架的主要条款（有正文、非概述类，按原文顺序）。"""
        nodes = [n for n in self.tree if (n["texts"] or n["items"] or n["tables"]) and n["level"] <= 4
                 and not re.search(r"概述|背景|目标|说明|其他|附件", n["title"])]
        return nodes[:12]

    def understanding(self) -> Draft:
        draft = Draft()
        intro = []
        for pattern in (r"背景|概况|概述|简介", r"目标|目的", r"范围|内容"):
            node = next((n for n in self.tree if re.search(pattern, n["title"]) and n["texts"]), None)
            if node:
                intro.append(_p(f"采购文件明确（{node['title']}）：{node['texts'][0].rstrip('。')}。"))
        overview = self.facts.get("overview")
        if overview and not intro:
            intro.append(_p(f"采购文件明确：{overview.rstrip('。')}。"))
        draft.children.append(("采购需求理解", intro + [_p(self.fill(_KNOWLEDGE_BY_KEY["理解"]))]))
        nodes = self.overview_nodes()
        if nodes:
            rows = [[str(i), n["title"], (n["texts"][0] if n["texts"] else "；".join(x["label"] for x in n["items"]))[:80], "全面响应"]
                    for i, n in enumerate(nodes, start=1)]
            draft.children.append(("需求要点梳理", [
                _p("我方对采购需求逐条梳理，形成需求要点清单，作为实施、检查与验收的依据："),
                table(["序号", "需求条款", "要点", "响应"], rows, caption="需求要点梳理", widths=[1.2, 3.6, 9.6, 2.0])]))
        draft.children.append(("重点难点分析与对策", [_p(self.fill(_KNOWLEDGE_BY_KEY["重难点"]))]))
        return draft

    def plan_by_requirements(self, title: str) -> Draft:
        """方案类评分点：以采购需求主要条款为骨架逐条写实施措施。"""
        draft = Draft()
        draft.blocks.append(_p(self.fill(_KNOWLEDGE_BY_KEY["方案"])))
        for node in self.overview_nodes():
            draft.children.append((f"{node['title']}的实施措施", self.aspect_blocks(node["title"], nodes=[node])))
        return draft

    def write(self, title: str, rule: str) -> Draft:
        if re.search(r"承诺", title) and re.search(r"情形|承诺", rule):
            from api.services.bidgen.it_writer import ItWriter, write_commitment

            return write_commitment(ItWriter(self.ctx, self.facts))
        aspects = scoring_aspects(rule)
        if len(aspects) < 2 and re.search(r"理解|认识|需求分析", title):
            return self.understanding()
        if len(aspects) < 2 and re.search(r"方案|实施|服务内容|工作内容", title) and self.overview_nodes():
            return self.plan_by_requirements(title)
        draft = Draft()
        draft.blocks.append(_p(self.fill(f"我方对照采购文件评分标准中“{title}”的评审要求，结合{{project}}的实际情况，逐项响应如下。")))
        if len(aspects) >= 2:
            for aspect in aspects:
                draft.children.append((aspect, self.aspect_blocks(aspect)))
            return draft
        entries = self.knowledge(title)
        if entries:
            for subtitle, template in entries:
                draft.children.append((subtitle, [_p(self.fill(template))]))
            for node in self.related(title):
                draft.children.append((f"{node['title']}要求的响应", self.aspect_blocks(node["title"])))
        else:
            draft.blocks += self.aspect_blocks(title)
        return draft


def write_score_point(ctx: ComposeContext, facts: dict, title: str, rule: str) -> Draft:
    return GenericWriter(ctx, facts).write(title, rule)


def _draft_text(draft: Draft) -> str:
    parts = [block.get("text", "") for block in draft.blocks]
    for title, blocks in draft.children:
        parts.append(title)
        parts += [block.get("text", "") for block in blocks]
        parts += [" ".join(" ".join(row) for row in block.get("rows", [])) for block in blocks if block.get("type") == "table"]
    parts += [" ".join(" ".join(row) for row in block.get("rows", [])) for block in draft.blocks if block.get("type") == "table"]
    return re.sub(r"\s", "", "".join(parts))


def ensure_coverage(ctx: ComposeContext, facts: dict, draft: Draft, rule: str) -> dict:
    """评分点覆盖自检：评分标准列出的要点在正文中找不到时补写对应小节。返回 {aspects, added}。"""
    aspects = scoring_aspects(rule)
    text = _draft_text(draft)
    titles = [re.sub(r"\s", "", title) for title, _blocks in draft.children]
    added = []
    writer = GenericWriter(ctx, facts)
    for aspect in aspects:
        key = re.sub(r"\s", "", aspect)
        covered = key in text or any(similarity(key, title) >= 0.6 for title in titles)
        if not covered:
            draft.children.append((aspect, writer.aspect_blocks(aspect)))
            added.append(aspect)
    return {"aspects": aspects, "added": added}


def coverage_table(records: list[dict]) -> dict | None:
    rows = [[r["factor"], "、".join(r["aspects"]) or "—", "、".join(r["added"]) or "无"] for r in records if r["aspects"]]
    return table(["评分要素", "评审要点", "自检补写"], rows) if rows else None
