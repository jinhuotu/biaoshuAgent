"""软件 / 信息化采购技术标编写器（不调用模型）。

写法：以采购需求章节（``requirements.build_requirement_tree``）为骨架逐条响应——
需求原文照录要点并改写为响应口吻，再按条目涉及的技术点补充实现方式；工期、质保、服务时效、团队承诺等
数值全部取自采购文件，取不到就不写具体值。源代码移交等对公司有约束的承诺按最高得分情形编写，并列入待确认清单。
"""

from __future__ import annotations

import re
from collections.abc import Callable

from api.services.bidgen.document_model import ComposeContext, paragraph, table
from api.services.bidgen.epc_writer import Draft, split_days
from api.services.bidgen.requirements import children, descendants, find_node, find_nodes, node_text

# 条目涉及的技术点 → 实现方式（按出现顺序最多补两条）
_TECH_NOTES: tuple[tuple[str, str], ...] = (
    (r"甘特图|排程|排产", "排程结果以可拖拽甘特图呈现，可按设备、班组、工单多维度查看；系统依据BOM、工艺路线与设备产能自动校验约束，人工调整后即时重算并提示冲突。"),
    (r"条码|二维码|序列号|一物一码|批次码|箱码", "编码规则可配置（前缀、日期、流水号、校验位），打印模板可视化设计；扫码数据实时与工单、工序、批次、人员关联，形成可追溯的数据链。"),
    (r"流程引擎|审批|流程设计器", "流程引擎采用可视化流程设计器，节点、条件、办理人、超时规则均可配置；待办同时推送PC端与移动端，支持转办、催办与流程监控。"),
    (r"消息|预警|推送|告警|提醒", "事件规则引擎统一定义触发条件、通知对象与通知渠道（站内信、移动端推送、弹窗、短信/邮件），全程记录触发、通知、已读、处理与闭环时间。"),
    (r"调度|自动触发|定时", "调度中心支持定时、事件两类触发，任务执行状态、耗时与失败原因可视化监控，失败任务自动重试并告警。"),
    (r"ERP|金蝶|用友|SAP", "与ERP的交互通过统一接口服务实现，接口具备鉴权、幂等、失败重试与日终对账机制，确保两系统数据口径一致。"),
    (r"追溯|投料|物料使用", "追溯以批次/序列号为主线，记录每道工序的投料批次、设备、人员、时间与检验结果，支持由成品向原料的反向追溯和由原料向成品的正向追溯。"),
    (r"质检|检验|首检|巡检|不合格|缺陷", "检验方案按物料、工序、产品分别配置检验项目、标准值与上下限，录入实测值后自动判定；不合格品自动触发评审、返工或报废流程并纳入质量统计。"),
    (r"设备|点检|保养|维修|维保", "设备台账、点检保养计划、维修工单与备件消耗一体管理，计划到期自动生成任务；统计故障次数、停机时长、MTBF/MTTR与设备综合效率（OEE）。"),
    (r"看板|大屏|可视化", "看板采用组件化配置，数据源、刷新频率、布局可自定义，支持车间大屏、电视与PC端多端展示，异常数据高亮预警。"),
    (r"报表|统计分析", "内置报表设计器，支持按时间、产线、产品、班组等维度钻取分析，报表可导出Excel/PDF并按角色授权查看。"),
    (r"仓储|配送|库存|线边|发货|出入库", "仓储按仓库、库区、库位三级建模，出入库全程扫码，支持先进先出与批次管理；配送任务依据工单需求自动生成并跟踪送达签收。"),
    (r"APP|移动端|手机|扫码报工|PDA", "移动端与PC端共用同一套业务服务与数据库，兼容Android/iOS及工业手持终端；弱网环境下支持本地暂存、网络恢复后自动补传。"),
    (r"算法|智能匹配|匹配", "匹配算法以订单颜色参数与回收粉成分、色差数据为输入，按相似度打分排序给出推荐方案，并记录采纳结果持续修正匹配规则。"),
    (r"调色|配色|色差|打板", "调色过程数字化记录配方版本、色差仪检测数据与每次打板结果，形成可复用的配方知识库，打板次数自动累计用于工艺改进分析。"),
    (r"安灯|异常", "异常按类型与等级配置响应时限和升级路径，超时未处理自动逐级升级通知，处理结果需发起人确认后方可关闭，形成闭环。"),
    (r"权限|角色|授权|登录", "权限采用“用户—角色—权限”（RBAC）模型，细化到菜单、按钮与数据行/字段；登录与关键操作全程留痕，便于审计。"),
    (r"工艺|BOM|工序", "工艺路线、BOM与工艺文件实行版本管理，变更走审批流程并记录变更前后内容，工单下达时锁定当期有效版本。"),
    (r"模具|筛网|容器|耗材", "工模具、容器按唯一编码建档，记录领用、使用次数/时长、清洗保养与报废全过程，寿命到期自动预警。"),
    (r"研发|文档", "研发文档按项目、产品分类存储，支持版本控制、在线预览与借阅审批，权限按角色控制。"),
    (r"物联网|IOT|数据采集|数采", "设备数据经采集网关进入平台后完成清洗、单位换算与异常值剔除，按设备、参数建立实时与历史数据表，供生产执行、设备管理与看板调用。"),
)


def _p(text: str) -> dict:
    return paragraph(text)


def _join(items, sep: str = "、") -> str:
    return sep.join(item for item in items if item)


def respond(text: str) -> str:
    """把需求表述改写为响应表述：主体换成“我方 / 本系统”，去掉“应 / 须 / 需”等要求口吻。"""
    value = text.strip().rstrip("。；;")
    value = re.sub(r"(?:成交)?供应商(?:应|须|需|需要|必须)?", "我方", value)
    verbs = r"具备|具有|支持|提供|实现|能够|满足|采用|建立|充分考虑|考虑|保证|确保|遵循|符合|按照|根据|做到|达到"
    value = re.sub(rf"(?:应|须|需|需要|必须)(?={verbs})", "", value)
    value = value.replace("我方我方", "我方")
    return value + "。"


def tech_notes(text: str, limit: int = 2) -> list[str]:
    notes = []
    for pattern, note in _TECH_NOTES:
        if re.search(pattern, text, re.I) and note not in notes:
            notes.append(note)
        if len(notes) >= limit:
            break
    return notes


def first_sentence(text: str) -> str:
    matched = re.match(r"(.+?[。；])", text)
    return (matched.group(1) if matched else text).rstrip("；;")


class ItWriter:
    def __init__(self, ctx: ComposeContext, facts: dict) -> None:
        self.ctx = ctx
        self.facts = facts
        self.tree: list[dict] = facts.get("requirements") or []
        self.name = facts.get("name") or ctx.project_name
        self.owner = facts.get("owner") or ctx.purchaser_names
        self.all_text = "\n".join(node_text(node) + "\n" + node["title"] for node in self.tree)
        self.criteria_text = "\n".join(c["rule"] for c in ctx.tender.get("criteria", []))
        dotnet = re.search(r"WCF|Remoting|\.NET|SQL\s*Server", self.all_text, re.I)
        self.stack = ".NET" if dotnet else "Java"

    # ---- 取需求 ----
    def node(self, pattern: str) -> dict | None:
        return find_node(self.tree, pattern)

    def first_node(self, *patterns: str) -> dict | None:
        """按给定顺序找第一个命中的节点（具体的写法排在前面）。"""
        return next((node for pattern in patterns for node in [self.node(pattern)] if node), None)

    def texts(self, pattern: str) -> list[str]:
        node = self.node(pattern)
        return (node["texts"] + [f"{i['label']}：{i['text']}" for i in node["items"]]) if node else []

    def number(self, pattern: str, text: str | None = None) -> str:
        matched = re.search(pattern, text if text is not None else self.all_text + "\n" + self.facts.get("schedule", ""))
        return matched.group(1) if matched else ""

    @property
    def modules(self) -> list[dict]:
        """功能模块：“建设详细内容”到下一个同级以上章节（如“技术要求”）之间带内容的小节。

        架构、硬件、系统集成在总体架构 / 集成方案中单独写；与其他模块同名的小节（如移动端的“生产执行”）
        加上所属分组名区分。
        """
        # 按具体程度依次找：“建设详细内容”优先于笼统的“建设内容”
        detail = next((node for pattern in (r"建设详细内容|详细建设内容", r"功能需求|功能要求|功能清单", r"^建设内容$|^项目内容$")
                       for node in [self.node(pattern)] if node), None)
        if detail is None:
            return []
        start = self.tree.index(detail)
        end = next((i for i in range(start + 1, len(self.tree)) if self.tree[i]["level"] < detail["level"]), len(self.tree))
        skip = re.compile(r"架构|硬件|网络环境|网络建设|配套技术|^\d*(?:\.\d+)*\s*系统集成|CRM|ERP")
        candidates = [node for node in self.tree[start + 1:end]
                      if (node["items"] or node["texts"]) and not skip.search(node["title"])
                      and not any(skip.search(parent) for parent in node["path"][-1:])]
        titles = [node["title"] for node in candidates]
        result = []
        for node in candidates:
            if titles.count(node["title"]) > 1 and node["path"]:
                group = re.sub(r"^\d+(?:\.\d+)*\s*|建设$|应用建设$|集成建设$", "", node["path"][-1]).strip()
                node = {**node, "title": f"{group}—{node['title']}"}
            result.append(node)
        return result

    @property
    def schedule(self) -> dict:
        text = self.all_text + "\n" + "\n".join(self.facts.get("project_terms", []))
        return {
            "entry": self.number(r"(\d+)\s*日内人员进场", text),
            "total": self.number(r"(\d+)\s*日内完成", text) or (str(self.facts.get("schedule_days")) if self.facts.get("schedule_days") else ""),
            "trial": self.number(r"试运行(?:期限)?不少于\s*(\d+)\s*个?月", text),
            "warranty": self.number(r"质保期[^\d]{0,20}?(\d+)\s*个月", text) or self.number(r"(\d+)\s*个月免费质保", self.criteria_text),
        }

    def team_terms(self) -> dict:
        text = self.criteria_text
        return {
            "size": self.number(r"核心人员数量不少于\s*(\d+)\s*人", text),
            "onsite": self.number(r"每月驻场不少于\s*(\d+)\s*个?工作日", text),
            "arrive": self.number(r"(\d+)\s*日内保证人员到位", text),
            "notice": self.number(r"提前\s*(\d+)\s*日通知", text),
        }

    def roles(self) -> list[str]:
        matched = re.search(r"针对(.+?)等(?:不同|多类)?岗位", self.all_text)
        return [r.strip() for r in re.split(r"[、，,]", matched.group(1)) if r.strip()] if matched else []

    def erp_name(self) -> str:
        matched = re.search(r"(金蝶[一-鿿A-Za-z]*?\s*ERP|用友[一-鿿A-Za-z0-9]*|SAP\s*\w*)", self.all_text)
        return re.sub(r"\s+", "", matched.group(1)) if matched else ("ERP" if "ERP" in self.all_text else "")


# ================= 需求理解 =================

def write_understanding(w: ItWriter) -> Draft:
    draft = Draft()
    background = w.texts(r"项目背景|背景")
    purpose = next((t for t in reversed(background) if re.search(r"本项目|旨在|目的", t)), background[-1] if background else "")
    blocks = []
    if background:
        owner_intro = first_sentence(background[0])
        blocks.append(_p(f"{w.owner}是本项目采购人。根据采购文件，{owner_intro.rstrip('。')}。"))
    if purpose:
        blocks.append(_p(f"我方理解，{purpose}"))
    pains = re.findall(r"解决(?:当前)?([^。；]{4,40}?)的问题", "\n".join(background))
    if pains:
        blocks.append(_p(f"项目要解决的核心问题是{_join(pains)}。这决定了本项目不是单纯的软件采购，而是以数据贯通为主线、以业务流程重塑为目标的数字化工程：系统上线后，生产端与管理端应基于同一套实时数据协同，管理决策由“事后统计”转为“过程管控”。"))
    draft.children.append(("项目背景理解", blocks or [_p(f"我方已认真研读{w.name}采购文件，充分理解采购人的建设意图。")]))

    goals = w.texts(r"建设目标")
    goal_blocks = []
    if goals:
        goal_text = goals[0]
        goal_blocks.append(_p(f"采购文件明确：{goal_text}"))
        scope = re.search(r"覆盖(.+?)等(?:完整)?业务领域", goal_text)
        domains = [d.strip() for d in scope.group(1).split("、")] if scope else []
        if domains:
            goal_blocks.append(_p(f"我方将上述目标分解为{len(domains)}个业务领域逐一落地，每个领域均对应明确的功能模块与验收口径（见总体设计方案），确保“目标—功能—验收”一一对应。"))
    main = w.first_node(r"建设主要内容|主要建设内容", r"^建设内容$|^项目内容$")
    rows = []
    if main:
        for index, child in enumerate(children(w.tree, main), start=1):
            summary = first_sentence(child["texts"][0]) if child["texts"] else ""
            rows.append([str(index), child["title"], summary, "完整纳入本次建设范围"])
    if rows:
        goal_blocks.append(table(["序号", "建设内容", "采购需求要点", "我方理解与响应"], rows, caption="建设内容理解与响应", widths=[1.2, 3.6, 8.6, 3.0]))
    draft.children.append(("建设目标与建设内容理解", goal_blocks or [_p("我方完整理解采购文件规定的建设目标与建设内容。")]))

    roles = w.roles()
    scenario_rows = []
    role_scenes = (("生产|操作", "生产执行", "接收任务、扫码报工、查看工艺要求", "报工实时入库，减少手工统计"),
                   ("设备|维护", "设备管理", "点检保养、故障报修、维保记录", "维保有据可查，设备状态透明"),
                   ("质量|质检|检验", "质量管理", "检验任务、实测数据录入、判定与留证", "质量数据可追溯，缺陷可分析"),
                   ("现场|管理|班组", "现场管理", "异常发起与处置、进度监控、看板查看", "异常闭环，现场管控实时化"),
                   ("计划|调度", "计划排程", "订单承接、排程调整、派工下达", "计划与执行联动"),
                   ("仓|物料|配送", "仓储物流", "备料、配送、出入库扫码", "物料齐套、库存准确"))
    for role in roles:
        scene = next((s for s in role_scenes if re.search(s[0], role)), None)
        if scene:
            scenario_rows.append([role, scene[1], scene[2], scene[3]])
    if scenario_rows:
        draft.children.append(("应用场景分析", [
            _p("结合采购文件对一线岗位的描述，本项目的主要使用者和应用场景如下，系统设计以“岗位化、场景化”为原则，确保每个岗位打开系统即可看到自己的任务："),
            table(["岗位", "应用场景", "主要操作", "预期效果"], scenario_rows, caption="主要岗位应用场景", widths=[3.0, 2.6, 6.0, 4.8]),
        ]))

    points = []
    erp = w.erp_name()
    if erp:
        points.append((f"与{erp}的双向集成", f"基础数据、订单、计划由{erp}下发，领料、完工、废品、工时等实绩回传，任一方向数据不一致都会造成账实不符、成本失真",
                       f"按“主数据以{erp}为准、业务数据按场景双向同步”的原则设计接口，接口具备鉴权、幂等、重试与日终对账；上线前用真实单据开展端到端联调"))
    if re.search(r"IOT|物联网|数据采集", w.all_text, re.I):
        points.append(("设备数据采集与实时展示", "设备品牌、协议各异，采集数据存在缺失、跳变等质量问题，直接使用会误导生产判断",
                       "与采购人确定的物联网平台约定数据接口与点位表，平台侧完成清洗、换算与异常剔除后再供业务模块使用，看板数据与业务数据同源"))
    industry = [node["title"] for node in w.tree if re.search(r"个性化|行业", node["title"])]
    if industry:
        names = _join([re.sub(r"[（(].*", "", title) for title in industry])
        points.append(("行业特色业务的数字化", f"{names}属于行业个性化需求，通用MES产品没有现成功能，需要把老师傅的经验转化为规则与数据",
                       "安排业务顾问驻场跟班调研，先梳理配方、色差、成分等关键数据标准，再以“规则+算法”方式固化，并预留人工确认环节逐步提高自动化程度"))
    if re.search(r"追溯|一物一码", w.all_text):
        points.append(("全链路追溯", "追溯要求从原料投料到成品出库每一环节都有记录，任何一个工序漏扫都会导致追溯链断裂", "以批次/序列号为主线设计数据模型，关键工序设置强制扫码校验，漏扫不能进入下道工序"))
    schedule = w.schedule
    if schedule["total"]:
        points.append(("工期紧、范围广", f"合同签订后{schedule['total']}日内需完成需求分析、设计、开发、测试、部署、上线与试运行等全部工作，模块多、集成多",
                       "以成熟产品为基础配置化实施，定制开发集中在行业特色模块；调研、开发、测试分模块并行推进，关键路径每周跟踪"))
    if points:
        draft.children.append(("建设重难点分析与对策", [
            _p("我方结合采购需求与同类项目实施经验，识别本项目建设重难点如下："),
            table(["序号", "重难点", "难点分析", "我方对策"], [[str(i), *row] for i, row in enumerate(points, start=1)],
                  caption="建设重难点分析与对策", widths=[1.2, 3.0, 6.0, 6.2]),
        ]))

    departments = _join([s[1] for s in role_scenes if any(re.search(s[0], role) for role in roles)]) or "生产、设备、质量、仓储等业务部门"
    research = [
        ["调研准备", "项目领导小组、信息部门", "启动会、调研计划与问卷下发", "调研计划、调研问卷"],
        ["现状调研", departments, "跟班观察、访谈、单据与报表收集", "业务现状记录、单据与报表清单"],
        ["需求分析", "各业务部门关键用户", "流程梳理研讨、差异分析", "需求规格说明书、业务流程图"],
        ["方案设计", "采购人项目组", "蓝图评审、原型演示确认", "系统实施方案、原型确认单"],
        ["需求确认", "采购人项目负责人", "逐项签字确认、变更登记", "需求确认书、需求跟踪矩阵"],
    ]
    draft.children.append(("需求调研与设计规划", [
        _p("需求调研遵循“先业务后系统、先现状后蓝图、先确认后开发”的原则，调研成果全部书面化并经采购人确认，作为开发、测试与验收的依据："),
        table(["阶段", "调研对象", "调研方式", "阶段成果"], research, caption="需求调研与设计规划", widths=[2.2, 4.4, 5.0, 4.8]),
    ]))
    return draft


# ================= 总体设计方案 =================

def _module_blocks(w: ItWriter, module: dict) -> list[dict]:
    blocks = []
    if module["texts"]:
        blocks.append(_p(respond(module["texts"][0])))
    for index, item in enumerate(module["items"], start=1):
        blocks.append(_p(f"（{index}）{item['label']}。{respond(item['text'])}"))
    for text in module["texts"][1:]:
        numbered = re.match(r"^\s*(\d+)[)）]\s*([^：:]{2,30})[：:]\s*(.+)$", text)
        if numbered:
            blocks.append(_p(f"（{numbered.group(1)}）{numbered.group(2)}。{respond(numbered.group(3))}"))
        else:
            blocks.append(_p(respond(text)))
    notes = tech_notes(node_text(module) + module["title"])
    if notes:
        blocks.append(_p("实现要点：" + "".join(notes)))
    return blocks


def write_overall_design(w: ItWriter) -> Draft:
    draft = Draft()
    general = w.texts(r"^总体要求$")
    principles = w.node(r"设计原则")
    idea = [_p(f"本项目总体设计以采购文件“第五章 项目采购需求”为依据，坚持“业务驱动、数据贯通、平台支撑、分步实施”的思路：以统一平台承载全部业务模块，以统一数据模型打通计划、执行、质量、设备、仓储等环节，以标准接口连接{_join([w.erp_name(), 'IOT平台' if re.search('IOT|物联网', w.all_text, re.I) else ''])}等外部系统。")]
    if general:
        idea.append(_p("对采购文件提出的总体要求，我方方案的响应是：系统设计坚持国内领先、适度超前，采用模块化、可插拔的平台架构，满足先进性、开放性、稳定性与可扩展性要求，新增业务以配置或插件方式扩展，最大程度保护采购人已有投资；方案中凡与采购需求存在差异之处，均以书面方式说明并提出更优替代方案。"))
    rows = []
    if principles:
        for node in [principles, *descendants(w.tree, principles)]:
            for item in node["items"]:
                rows.append([item["label"], item["text"], "满足。" + _principle_measure(item["label"] + item["text"])])
            if node is not principles and node["texts"]:
                rows.append([node["title"], node["texts"][0], "满足。" + _principle_measure(node["title"] + node["texts"][0])])
    if rows:
        idea.append(table(["设计原则", "采购要求", "我方响应"], rows, caption="设计原则响应", widths=[3.0, 7.4, 6.0]))
    draft.children.append(("总体设计思路与原则", idea))

    architecture = w.node(r"技术架构|系统架构|总体架构")
    arch_text = node_text(architecture) if architecture else ""
    layers_match = re.search(r"分为(.+?)(?:[五六七四三]大|等)?核心层级", arch_text) or re.search(r"分为(.+?)[等。]", arch_text)
    layers = [layer.strip() for layer in re.split(r"[、，]", layers_match.group(1))] if layers_match else ["基础层", "数据层", "平台层", "应用层", "接入层"]
    layer_rows = []
    described_text = arch_text[layers_match.end():] if layers_match else arch_text
    for layer in layers:
        described = re.search(rf"{re.escape(layer)}(?:作为|承担|为|是)?([^；。]{{6,120}})", described_text)
        layer_rows.append([layer, (described.group(1).strip("，") if described else ""), _LAYER_DESIGN.get(layer[:2], "按采购要求建设")])
    draft.children.append(("总体架构设计", [
        _p(f"系统采用分层架构，自下而上分为{_join(layers)}，各层职责清晰、接口标准，单层升级不影响其他层，满足采购文件对先进性、开放性、稳定性与可扩展性的要求。"),
        table(["层级", "采购要求", "我方设计"], layer_rows, caption="系统分层架构", widths=[2.2, 7.0, 7.2]),
    ]))

    modules = w.modules
    if modules:
        module_rows = [[str(i), m["title"], _join([item["label"] for item in m["items"]][:8]) or first_sentence(m["texts"][0] if m["texts"] else ""), "满足"]
                       for i, m in enumerate(modules, start=1)]
        draft.children.append(("功能架构与需求响应总表", [
            _p(f"按采购需求，本系统共规划{len(modules)}个功能模块（含移动端与集成部分），全部纳入本次建设范围，逐项响应如下，各模块详细设计见后续小节："),
            table(["序号", "功能模块", "主要功能点（采购需求）", "响应情况"], module_rows, caption="功能需求响应总表", widths=[1.2, 3.8, 9.6, 1.8]),
        ]))
    flow = [name for name, pattern in (("订单接收", "订单"), ("计划排程", "排程|计划"), ("派工下达", "派工"), ("生产执行与报工", "报工|生产执行"),
                                       ("物料配送与投料", "配送|投料"), ("质量检验", "检验|质检"), ("完工入库", "入库"), ("数据回传ERP", "ERP"))
            if re.search(pattern, w.all_text)]
    if len(flow) >= 4:
        draft.children.append(("核心业务流程设计", [
            _p("系统以订单为起点、以完工入库和实绩回传为终点组织核心业务流程：" + "→".join(flow) + "。每个环节的输出即下一环节的输入，单据自动流转、状态实时可见，任一环节异常即触发预警与处理流程，避免信息在部门之间断档。"),
            _p("流程中所有节点均记录处理人、处理时间与处理结果，配合消息与调度引擎实现“到点自动推送、超时自动提醒、完成自动流转”，确保业务流程在线闭环。"),
        ]))
    for module in modules:
        draft.children.append((f"{module['title']}设计", _module_blocks(w, module)))
    integration = w.node(r"系统集成建设|集成建设|系统集成$")
    if integration:
        blocks = [_p(respond(text)) for text in integration["texts"][:2]]
        for item in integration["items"]:
            blocks.append(_p(f"{item['label']}：{respond(item['text'])}"))
        lists = re.findall(r"(?:具体涵盖|主要包含|包含)(.{6,160}?)等", node_text(integration))
        rows = []
        for index, found in enumerate(lists[:4]):
            direction = "外部系统→本系统" if index == 0 else "本系统→外部系统"
            rows.append([direction, found.strip("：:"), "RESTful API，定时+事件触发", "鉴权、幂等、重试、对账"])
        if rows:
            blocks.append(table(["数据方向", "数据内容（采购需求）", "交互方式", "保障机制"], rows, caption="系统集成数据交互设计", widths=[2.6, 7.4, 3.4, 3.0]))
        for child in children(w.tree, integration):
            blocks.append(_p(f"{child['title']}：" + "".join(respond(text) for text in child["texts"])))
        draft.children.append(("系统集成方案设计", blocks))
    draft.children.append(("主要界面原型说明", [
        _p("为便于评审直观了解系统功能实现与逻辑关系，主要界面原型设计如下（实施阶段将在调研后制作可交互原型，经采购人确认后开发）："),
        table(["界面", "主要元素", "核心操作与逻辑"], _prototype_rows(w), caption="主要界面原型说明", widths=[3.0, 6.0, 7.4]),
    ]))
    return draft


_LAYER_DESIGN = {
    "基础": "服务器、存储、网络与操作系统、数据库由采购人提供，我方提出资源配置建议并完成软件部署与调优",
    "数据": "统一主数据模型与业务数据库，建立数据字典、编码规范与数据交换服务，保障数据口径一致",
    "平台": "提供流程、消息、调度、权限、报表等公共引擎，业务模块按需调用，避免重复开发",
    "应用": "承载全部业务模块与移动端功能，模块化部署，可按场景独立升级",
    "接入": "统一接入PC浏览器、手机APP、工控机与手持终端，对外提供标准接口与集成服务",
    "展现": "统一门户与看板，按角色呈现个性化工作台",
}


def _principle_measure(text: str) -> str:
    for pattern, measure in (
        (r"兼容", "采用主流成熟技术，接口遵循RESTful/JSON等开放标准，数据库、中间件选型开放，支持与采购人现有及未来系统对接。"),
        (r"稳定", "模块间通过标准接口解耦，接口版本化管理；单个模块升级不改动上下游接口，升级前在测试环境回归验证，不影响业务运转。"),
        (r"扩展", "采用模块化、插件化架构，新业务以配置或插件方式扩展；外部集成采用适配器模式，对已有业务系统的改动降到最少。"),
        (r"维护", "功能参数与权限支持集中维护与按业务线独立维护两种模式，按组织隔离配置，互不影响。"),
        (r"前瞻|实用", "架构采用分层与服务化设计，适配移动互联与物联网接入；功能以采购人现有业务为出发点，确保上线即可用。"),
        (r"开放|灵活", "平台对外开放标准API，模块支持按场景独立部署；第三方程序可通过接口服务快速集成。"),
        (r"安全", "按“身份可信、权限最小、操作留痕、数据加密”原则设计安全体系。"),
    ):
        if re.search(pattern, text):
            return measure
    return "按此原则开展设计、开发与测试。"


def _prototype_rows(w: ItWriter) -> list[list[str]]:
    rows = [["个人工作台", "待办任务、消息提醒、常用功能、关键指标卡片", "登录后按角色展示待办，点击直达处理页面"]]
    for pattern, row in (
        (r"排程|甘特", ["计划排程", "订单池、甘特图、产能负荷图、齐套检查结果", "一键排程后可拖拽调整，冲突实时提示，确认后下发派工"]),
        (r"报工|生产执行", ["工位报工（移动端）", "任务列表、扫码区、合格/次品/报废数量录入", "扫码自动带出工单与工序，提交后实时更新进度"]),
        (r"质检|检验", ["质量检验", "检验任务、检验项目与标准值、实测值录入、照片上传", "录入后自动判定，不合格自动发起处理流程"]),
        (r"追溯", ["追溯查询", "条码输入框、追溯树、工序时间轴", "输入任一批次/序列号即可正反向展开全链路信息"]),
        (r"设备", ["设备管理", "设备台账、点检保养日历、维修工单、设备状态图", "到期任务自动生成，扫码签到后逐项填报"]),
        (r"看板|大屏", ["车间大屏", "设备状态、产量达成、质量指标、异常滚动", "按设定频率自动刷新，异常高亮"]),
    ):
        if re.search(pattern, w.all_text):
            rows.append(row)
    return rows


# ================= 技术方案 =================

_PERFORMANCE_MEASURES = (
    (r"并发", "应用层集群部署+负载均衡，无状态服务可横向扩容；会话集中管理"),
    (r"响应时间", "热点数据缓存、数据库索引优化、复杂计算异步处理，关键页面分页与按需加载"),
    (r"数据吞吐量", "SSD阵列存储，大批量导入导出采用分批流式处理"),
    (r"吞吐", "64位运行环境，连接池与批量处理优化，消息队列削峰"),
    (r"IOPS", "数据库与日志分盘部署于SSD存储，冷热数据分离"),
    (r"缓存", "本地缓存+分布式缓存两级缓存，缓存更新与数据库变更联动"),
    (r"通信", "按要求采用指定通信技术，同时提供RESTful标准接口供外部系统调用"),
)


def write_technical(w: ItWriter) -> Draft:
    draft = Draft()
    basic = w.texts(r"基本技术要求")
    stack = (["开发框架", ".NET 技术栈，分层服务架构"], ["服务通信", "Remoting/WCF 内部服务通信 + RESTful 对外接口"]) if w.stack == ".NET" else \
        (["开发框架", "Java（Spring Boot）微服务架构"], ["服务通信", "RESTful API + 消息队列"])
    stack_rows = [list(row) for row in stack]
    os_db = re.search(r"支持(Windows[^，。,]+?)操作系统", "\n".join(basic))
    db = re.search(r"数据库支持([^，。]+)", "\n".join(basic))
    if os_db:
        stack_rows.append(["操作系统", f"满足采购要求：{os_db.group(1)}"])
    if db:
        stack_rows.append(["数据库", f"满足采购要求：{db.group(1)}"])
    stack_rows += [["前端", "B/S架构，主流浏览器访问；移动端兼容Android/iOS及工业手持终端"], ["缓存与消息", "多级缓存、消息队列"], ["报表与可视化", "内置报表设计器与可视化看板组件"]]
    route = [_p(f"我方技术方案遵循采购人现有平台技术栈规则，" + ("采用与采购需求一致的 .NET/SQL Server 技术路线，" if w.stack == ".NET" else "") + "选用成熟、稳定、主流的技术组件，确保系统安全可靠、便于后续运维与二次开发。")]
    for text in basic:
        route.append(_p("采购要求：" + text.rstrip("。") + "。我方响应：满足，" + respond(re.sub(r"^[（(]\d+[）)]", "", text)).replace("系统", "本系统", 1)))
    route.append(table(["技术项", "我方选型"], stack_rows, caption="技术路线与技术栈", widths=[3.6, 12.8]))
    draft.children.append(("技术路线与技术栈", route))

    perf = w.node(r"性能要求|性能指标")
    if perf and perf["tables"]:
        rows = []
        for row in perf["tables"][0][1:]:
            if len(row) < 2:
                continue
            measure = next((m for p, m in _PERFORMANCE_MEASURES if re.search(p, row[0], re.I)), "按采购要求设计并在压力测试中验证")
            rows.append([row[0], row[1], "满足", measure])
        draft.children.append(("性能指标响应与保障", [
            _p("对采购文件提出的性能指标逐项响应如下，上线前通过压力测试验证并出具压力测试报告："),
            table(["指标项", "采购要求", "响应", "保障措施"], rows, caption="性能指标响应", widths=[2.6, 5.0, 1.4, 7.4]),
        ]))

    deploy = w.texts(r"部署要求")
    deploy_blocks = [_p("采购要求：" + text) for text in deploy]
    deploy_blocks.append(_p("我方响应：系统支持私有云及物理服务器部署，兼容X86、ARM架构；按正式环境、测试环境分别部署，两套环境配置一致、数据隔离，所有变更先在测试环境验证后再发布到正式环境。系统容量不足时可通过增加服务器节点横向扩展，无需修改程序。"
                         if any("私有云" in t or "ARM" in t for t in deploy) else "我方响应：按采购人环境要求完成部署，正式环境与测试环境分离。"))
    deploy_blocks.append(table(["服务器角色", "数量（建议）", "建议配置", "说明"], [
        ["应用服务器", "2台", "8核CPU／32GB内存／200GB SSD", "集群部署，承载业务服务与接口服务"],
        ["数据库服务器", "1～2台", "16核CPU／64GB内存／SSD阵列", "主从或高可用部署，满足IOPS与吞吐要求"],
        ["文件/报表服务器", "1台", "8核CPU／16GB内存／1TB存储", "附件、图纸、报表与日志存储"],
        ["测试环境", "1套", "按正式环境50%配置", "功能验证、升级预演"],
    ], caption="部署计算资源建议（由采购人按此配置）", widths=[3.0, 2.4, 5.2, 5.8]))
    draft.children.append(("部署方案与计算资源需求", deploy_blocks))

    interface = w.texts(r"接口规范") + w.texts(r"^系统集成要求$")
    systems = re.search(r"包括[:：]?(.+?)等", " ".join(w.texts(r"^系统集成要求$")))
    blocks = [_p("采购要求：" + text) for text in interface]
    blocks.append(_p("我方响应：建立统一接口服务，对内对外提供标准化的RESTful接口与文件、消息两类交互方式；接口按“一个接口一份规范”管理，包含地址、报文格式、字段说明、错误码与示例，接口调用全程记录日志并可视化监控。新增系统或物联网设备接入时，通过配置接口映射与转换规则即可完成，无需修改核心程序。"))
    if systems:
        names = [s.strip() for s in re.split(r"[、，]", systems.group(1)) if s.strip()]
        blocks.append(table(["对接系统", "集成方式", "交互内容"], [[name, "接口服务／数据交换", "按采购人业务需要实现联机交互、文件交互与数据交换"] for name in names],
                            caption="外部系统集成清单", widths=[4.0, 4.0, 8.4]))
    draft.children.append(("接口规范与技术对接", blocks))

    security = w.node(r"安全性要求|安全要求")
    rows = []
    if security:
        for child in children(w.tree, security):
            requirement = " ".join(child["texts"])
            rows.append([child["title"], requirement, _security_design(child["title"] + requirement)])
    draft.children.append(("安全设计", [
        _p("系统按“身份可信、权限最小、操作留痕、数据加密”的原则设计安全体系，对采购文件的安全要求逐项响应如下："),
        table(["安全项", "采购要求", "我方设计"], rows or [["访问控制", "", _security_design("访问控制")]], caption="安全设计响应", widths=[2.6, 6.6, 7.2]),
    ]))
    draft.children.append(("权限设计", [
        _p("权限体系采用“组织—用户组—角色—用户”多层级模型，权限细化到功能菜单、按钮操作与数据（表、字段、记录）三级：功能权限控制可访问的菜单与按钮，数据权限按组织、产线、仓库、产品类别等维度控制可读、可写、可查询范围。"),
        _p("授权支持有效期起止时间设置，到期自动失效；重要权限变更走审批流程。系统管理员仅拥有权限配置、角色维护与组织架构调整能力，不具备生产业务操作权限，实现“管理员不碰业务、业务员不改权限”的三权分立。"),
    ]))
    ha = w.texts(r"高可用") + w.texts(r"运行监控")
    draft.children.append(("高可用与运行监控", [_p("采购要求：" + text) for text in ha] + [
        _p("我方响应：应用服务集群部署并通过负载均衡分发请求，单节点故障自动摘除；数据库采用主从或高可用部署，定时全量+增量备份并定期恢复演练；重要服务（接口、调度、消息）支持分布式部署。"),
        _p("监控方面提供系统级（服务器资源、数据库、服务进程）与应用级（接口调用、作业执行、业务异常）两级监控，统一日志中心可按时间、模块、用户检索，错误自动归类并列出原因，异常通过短信、邮件、站内消息多渠道预警；提供数据归档、备份、清理与恢复方案及在线查错工具。"),
    ]))
    usability = w.texts(r"易用性")
    if usability:
        draft.children.append(("易用性与可扩展性设计", [_p("采购要求：" + text) for text in usability] + [
            _p("我方响应：界面按岗位定制工作台，常用操作不超过三次点击；表单支持扫码自动带出、默认值与批量操作；平台提供表单、流程、报表、看板的配置工具，业务变化时多数需求通过配置完成，二次开发提供标准开发规范与示例，系统升级不影响已配置内容。"),
        ]))
    return draft


def _security_design(text: str) -> str:
    for pattern, design in (
        (r"访问控制|SSL|登录方式", "全站HTTPS（SSL/TLS）加密传输；用户名+口令登录，口令复杂度、有效期与连续失败锁定策略可配置；支持统一身份认证"),
        (r"授权|权限", "功能、数据（表/字段/记录）权限可灵活定义，支持用户组、角色、用户多层级授权、数据权限审批与授权有效期"),
        (r"变更记录|数据变更", "对数据模板及表、字段的增删改自动记录变更日志，包含日期、时间、用户、原值与新值，支持查询与导出"),
        (r"登录记录|使用日志", "记录用户名、客户端IP与机器名、登录时间、活动时间、操作功能与操作数据内容"),
        (r"文件存储|配置文件|传输文件", "配置文件敏感项加密存储，接口传输文件签名校验与加密传输，文件访问按权限控制"),
        (r"日志", "统一日志中心记录登录、错误、操作、接口集成、工作流、数据流向等日志，按保存期限归档"),
    ):
        if re.search(pattern, text):
            return design
    return "按采购要求落实相应安全措施"


# ================= 计划与实施方案 =================

def write_implementation(w: ItWriter) -> Draft:
    draft = Draft()
    s = w.schedule
    total = int(s["total"]) if s["total"] else 90
    trial = int(s["trial"]) * 30 if s["trial"] else 0
    entry = int(s["entry"]) if s["entry"] else 10
    build = max(30, total - trial - entry)
    names = ["需求调研", "方案设计与原型确认", "开发与配置", "集成联调与测试", "部署上线与培训"]
    days = split_days(build, [15, 10, 35, 15, 8])
    rows, start = [["项目启动与人员进场", "第1日", f"第{entry}日", "启动会、团队进场、调研计划", "项目启动报告、人员到位表"]], entry + 1
    outputs = ["调研记录、需求规格说明书", "系统实施方案、原型确认单", "可运行系统、二次开发功能", "测试用例、测试报告、压力测试报告", "上线方案、培训记录、上线确认单"]
    works = ["分部门跟班调研、需求确认", "总体设计、功能设计、接口设计、原型评审", "基础数据配置、功能开发、行业特色模块开发", f"与{w.erp_name() or '外部系统'}等集成联调、功能/性能/安全测试", "正式环境部署、数据初始化、分角色培训、切换上线"]
    for name, day, work, output in zip(names, days, works, outputs):
        rows.append([name, f"第{start}日", f"第{start + day - 1}日", work, output])
        start += day
    if trial:
        rows.append(["试运行与优化", f"第{start}日", f"第{total}日", f"系统试运行不少于{s['trial']}个月，问题跟踪与优化", "试运行报告、问题清单闭环"])
    rows.append(["初步验收与最终验收", "试运行结束后", "系统稳定运行无问题后", "提交验收申请、配合验收", "初验意见、《项目验收报告》"])
    lead = "我方严格按采购文件工期要求组织实施：" + (f"合同签订之日起{s['entry']}日内人员进场，" if s["entry"] else "") + \
        (f"{s['total']}日内完成系统需求分析、设计、开发、测试、部署、优化、上线、试运行等工作" if s["total"] else "按合同工期完成全部工作") + \
        (f"，试运行期限不少于{s['trial']}个月" if s["trial"] else "") + "，系统稳定运行后申请验收。"
    draft.children.append(("项目进度计划", [_p(lead), table(["阶段", "开始", "完成", "主要工作", "里程碑成果"], rows,
                                                         caption="项目实施进度计划", widths=[3.0, 1.8, 2.6, 5.2, 3.8])]))

    team = w.team_terms()
    size = int(team["size"]) if team["size"] else 4
    members = [["项目经理", "1", "全面负责项目计划、资源协调、进度质量控制与对接采购人", "PMP或高级信息系统项目管理师，具备MES项目管理经验"],
               ["实施顾问（业务）", "1", "需求调研、业务流程设计、系统配置、用户培训", "制造业MES实施经验"],
               ["开发工程师", str(max(1, size - 3)), "功能开发、行业特色模块与接口开发", f"熟悉{w.stack}技术栈与数据库"],
               ["测试/集成工程师", "1", "测试用例编写、集成联调、性能测试", "具备自动化测试与接口测试经验"]]
    onsite = f"项目经理与实施顾问阶段性驻场，每月驻场不少于{team['onsite']}个工作日；" if team["onsite"] else "项目经理与实施顾问在关键阶段驻场；"
    arrive = f"项目启动后{team['arrive']}日内保证人员到位并接受采购方面试，不符合要求的人员{team['arrive']}日内补充到位；" if team["arrive"] else ""
    notice = f"项目人员原则上不调整，特殊情况需调整的提前{team['notice']}日通知采购方并取得同意，在保障项目进度的前提下进行调整。" if team["notice"] else "项目人员保持稳定。"
    draft.children.append(("组织人员保障", [
        _p(f"我方成立专项项目组，核心人员不少于{size}人（项目经理1名，其他项目组成员不少于{size - 1}人），实行项目经理负责制，设立由我方分管领导牵头的项目领导小组负责重大事项决策与资源保障。"),
        table(["岗位", "人数", "主要职责", "能力要求"], members, caption="项目组人员配置", widths=[3.0, 1.2, 6.8, 5.4]),
        _p(onsite + arrive + notice),
    ]))
    draft.children.append(("部署实施", [
        _p("部署实施按“测试环境验证—正式环境部署—数据初始化—切换上线”四步推进：先在测试环境完成全部功能与集成验证，再按部署方案在正式环境安装配置；基础数据（组织、人员、物料、工艺、设备等）按模板整理、校验后导入，与ERP同步的数据以ERP为准；上线采用分模块、分产线逐步切换，上线首周安排专人现场值守，问题当日响应、当日处理。"),
    ]))
    quality = w.texts(r"质量控制") + w.texts(r"测试要求")
    coverage = w.number(r"覆盖率不得低于\s*(\d+%)")
    draft.children.append(("质量保证措施", [_p("采购要求：" + text) for text in quality] + [
        _p("我方响应：建立“过程评审+阶段测试+里程碑确认”的质量保证体系。需求、设计、开发、测试每个阶段结束均组织评审，阶段成果经采购人确认后进入下一阶段；代码实行同行评审与版本管理" + (f"，单元测试覆盖率不低于{coverage}" if coverage else "") + "；交付系统测试用例、压力测试报告、集成测试报告等测试文档，缺陷全部登记跟踪至关闭。"),
    ]))
    progress = w.texts(r"进度控制")
    draft.children.append(("进度控制措施", [_p("采购要求：" + text) for text in progress[1:2] or progress[:1]] + [
        _p("我方响应：编制总体进度计划与阶段工作计划，按周跟踪、按月总结；每周召开项目例会并提交周报，每个里程碑提交进度报告；建立项目变更流程，所有需求与计划变更登记、评估、审批后执行；关键路径任务延误超过2个工作日即启动纠偏，通过增加资源、调整并行度等措施追回进度。"),
    ]))
    risks = [["需求变更频繁", "范围蔓延、工期延误", "需求书面确认，变更走评审审批流程，评估对工期与成本的影响后执行"],
             ["外部系统接口延迟", "集成联调受阻", "提前确定接口规范与联调计划，接口未就绪时用模拟服务先行开发"],
             ["基础数据质量不高", "上线后数据错误", "提供数据模板与校验规则，上线前组织数据核对"],
             ["用户使用习惯转变难", "系统用不起来", "分角色培训、上线值守、关键用户带动，管理制度配套"],
             ["工期紧张", "里程碑延误", "成熟产品配置化实施，模块并行推进，关键路径每周跟踪"]]
    draft.children.append(("风险管理", [table(["风险", "影响", "应对措施"], risks, caption="项目风险及应对措施", widths=[3.4, 3.6, 9.4])]))
    return draft


# ================= 成果移交与培训方案 =================

def write_handover(w: ItWriter) -> Draft:
    draft = Draft()
    rows = []
    content = " ".join(w.texts(r"^交付内容$"))
    listed = re.search(r"交付(.+?)等项目", content)
    for name in ([x.strip() for x in listed.group(1).split("、")] if listed else []):
        rows.append(["实施成果", name, "实施过程中按阶段交付"])
    for item in (w.node(r"技术文档") or {"items": []})["items"]:
        rows.append([item["label"], item["text"].rstrip("。等 "), "项目结束前全部移交"])
    for text in w.texts(r"产品程序及组件"):
        rows.append(["产品程序及组件", text.rstrip("。"), "上线前交付并验证与环境一致"])
    scoring = next((c["rule"] for c in w.ctx.tender.get("criteria", []) if re.search(r"移交|培训", c["factor"])), "")
    extra = re.search(r"包含但不限于(.+?)，以及", scoring)
    if extra:
        rows.append(["知识产权与其他", extra.group(1), "随最终验收一并移交"])
    draft.children.append(("交付成果与知识转移", [
        _p("我方建立完善的文档管理体系（文件命名规范、目录规范、文件传递规则），按阶段向采购人提交项目文档，项目结束后全部成果移交采购人。交付成果清单如下："),
        table(["类别", "交付内容", "交付时点"], rows or [["技术文档", "需求、设计、测试、操作手册等", "项目结束前"]], caption="交付成果清单", widths=[3.2, 9.4, 3.8]),
        _p("知识转移采用“文档移交+讲解答疑+跟岗操作”三步法：每份文档移交时安排编写人讲解；源代码移交时同步提供编译环境说明与部署脚本，现场演示从源码编译到部署运行全过程；第三方插件逐一列明名称、版本与授权情况，确保采购人能够独立维护。"),
    ]))
    accept = [_p(respond(text) if not text.startswith("项目验收") else text) for text in
              w.texts(r"验收组织") + w.texts(r"验收标准") + w.texts(r"验收流程")]
    accept.append(table(["验收阶段", "前提条件", "验收内容", "成果"], [
        ["初步验收", "完成全部建设内容并上线试运行", "功能、性能、集成、文档完整性", "初步验收意见、问题整改清单"],
        ["最终验收", "试运行期满、系统稳定运行、问题全部整改", "全部合同内容与交付物", "《项目验收报告》，质保期起算"],
    ], caption="验收方案", widths=[2.4, 4.6, 5.0, 4.4]))
    draft.children.append(("验收方案", accept))
    training = re.search(r"以及(.+?)等", scoring)
    kinds = [k.strip() for k in training.group(1).split("、")] if training else ["系统操作培训", "系统维护培训"]
    targets = {"源代码": ("信息部门技术人员", "产品架构、关键逻辑、源码结构、开发工具与二次开发规范", "讲授+上机实操"),
               "操作": ("各岗位最终用户", "按岗位讲解系统操作、移动端使用", "分岗位上机实操"),
               "业务应用": ("关键用户、部门负责人", "业务流程、单据流转、报表与看板使用", "场景化演练"),
               "流程配置": ("系统管理员、关键用户", "流程、表单、权限、报表、看板配置", "讲授+配置实操"),
               "维护": ("信息部门运维人员", "部署架构、备份恢复、日志监控、故障处理", "讲授+演练")}
    rows = []
    for kind in kinds:
        target = next((value for key, value in targets.items() if key in kind), ("相关人员", kind, "讲授+实操"))
        rows.append([kind, *target])
    requirement = w.texts(r"培训要求")
    draft.children.append(("培训方案", [_p("采购要求：" + text) for text in requirement[:2]] + [
        _p("我方响应：按“分层分类、学以致用”的原则组织培训，培训后采购人人员能够独立进行管理、配置开发、维护测试与故障处理。培训安排如下："),
        table(["培训类别", "培训对象", "培训内容", "培训方式"], rows, caption="培训计划", widths=[3.2, 3.6, 6.4, 3.2]),
        _p("每类培训提供培训教材与操作手册，培训后组织考核，考核不合格的人员安排补训；质保期内对新增人员持续提供培训。"),
    ]))
    return draft


# ================= 供应商承诺方案 =================

def write_commitment(w: ItWriter) -> Draft:
    rule = next((c["rule"] for c in w.ctx.tender.get("criteria", []) if re.search(r"承诺", c["factor"])), "")
    options = re.findall(r"(情形[一二三四])[：:](.+?)(?=情形[一二三四][：:]|（供应商|注[：:]|$)", rule)
    blocks = []
    if options:
        name, text = options[0]
        text = re.sub(r"以上承诺得\d+分。?|根据.+?打分。?", "", text).strip()
        commitment = respond(text).replace("供应商承诺", "我方承诺").replace("为采购人", "为采购人")
        blocks.append(_p(f"我方对照采购文件“供应商承诺方案”评分标准，按{name}作出如下承诺："))
        blocks.append(_p(commitment if commitment.startswith("我方承诺") else "我方承诺：" + commitment))
        w.ctx.pending.append({"section": w.ctx._section_key, "hint": f"供应商承诺方案按“{name}”编写（{first_sentence(text)[:40]}……），该承诺对公司有约束力，请确认可以作出后再提交"})
    else:
        blocks.append(_p("我方承诺按采购文件要求移交本项目二次开发源代码及相关技术文档，并提供源代码开发培训。"))
    blocks.append(_p("为保证承诺可兑现，我方在项目实施中执行以下措施：源代码与可执行程序同源构建，每次发布同步归档对应版本源代码；移交时提供编译环境说明、依赖清单与部署脚本，并现场演示编译、部署与运行；对第三方组件逐一列明来源与授权，确保无技术限制。"))
    blocks.append(_p(f"本承诺自响应文件提交之日起生效，成交后作为合同组成部分。"))
    blocks.append(w.ctx.signature_block())
    return Draft(blocks=blocks)


# ================= 运维保障方案 =================

def write_operation(w: ItWriter) -> Draft:
    draft = Draft()
    s = w.schedule
    months = s["warranty"] or "12"
    scope = w.texts(r"质量保证期要求|质保期")
    duty = next((item.split("：", 1)[1] for item in scope if "负责但不限于" in item), "")
    blocks = [_p(f"我方自项目最终验收通过之日起提供{months}个月免费质保及运维保障服务" + (f"，质保期内负责{duty.rstrip('。')}" if duty else "") + "。")]
    sla_rows = []
    for text in scope:
        for sentence in re.split(r"[；;]", text):
            if "故障" not in sentence:
                continue
            level = re.search(r"对(.+?)的软件故障", sentence)
            response = re.search(r"([^，。]{1,6})内响应", sentence)
            plan = re.search(r"([^，。]{1,6})内提供补救方案", sentence)
            recover = re.search(r"([^，。]{1,6})内恢复", sentence)
            service = re.search(r"(7\s*[*×]\s*24)", sentence)
            if level:
                sla_rows.append([level.group(1), service.group(1).replace(" ", "") + "服务" if service else "—",
                                 response.group(1) if response else "—", plan.group(1) if plan else "—", recover.group(1) if recover else "—"])
    if sla_rows:
        blocks.append(table(["故障等级", "服务时间", "响应时限", "补救方案", "恢复时限"], sla_rows, caption="服务时效承诺（按采购要求）", widths=[4.6, 2.6, 2.4, 3.0, 3.8]))
    after = w.texts(r"售后服务")
    for text in after:
        blocks.append(_p("对采购文件售后服务要求，" + respond(text)))
    draft.children.append(("运维保障范围与服务时效", blocks))
    rule = next((c["rule"] for c in w.ctx.tender.get("criteria", []) if re.search(r"运维|售后|服务", c["factor"])), "")
    aspects = re.search(r"针对(.+?)等方面", rule)
    names = [a.strip() for a in re.split(r"、", aspects.group(1))] if aspects else []
    content = {
        "响应流程": "建立“受理—分级—派单—处理—验证—回访”六步响应流程：问题经热线、即时通讯或工单系统受理后，按影响程度定级并派给对应工程师，处理完成经用户确认后关闭，重大问题处理后提交分析报告。",
        "问题反馈": "提供服务热线、专属即时通讯群、在线工单三种反馈渠道，所有问题统一登记编号，用户可随时查询处理进度；每月提交运维报告，汇总问题数量、类型、处理时长与改进建议。",
        "服务时效": "严格执行上表时效承诺，对影响生产的问题实行“先恢复、后分析”；采购人电话求助享有最高优先级，直至问题解决并经采购人满意确认。",
        "服务方式": "以远程支持为主、现场服务为辅：一般问题远程处理，影响生产或远程无法解决的问题立即安排工程师到现场；系统升级、重大变更前提交方案并在非生产时段实施。",
        "服务地点": "现场服务地点为采购人项目现场；远程服务由我方技术支持中心提供。",
        "本地化": "安排熟悉本项目的实施人员作为本地化服务工程师，质保期内保证能够及时到达现场；关键岗位配置AB角，避免人员变动影响服务。",
        "重大疑难": "重大疑难问题启动升级机制：由项目经理牵头，研发、测试、数据库专家组成攻关小组，24小时内给出处理方案；必要时由我方研发中心直接介入，处理完成后提交根因分析与预防措施。",
        "人员": "质保期内配置运维工程师不少于2名（其中1名为本项目实施核心成员），并由研发团队提供二线支持；运维人员熟悉系统架构与业务流程，具备独立处理问题的能力。",
        "优惠": "质保期内免费提供系统小版本升级、功能优化与新增报表配置；免费为新入职人员提供操作培训；质保期满后以优惠价格提供续保服务，对系统缺陷与安全漏洞提供终生免费修复。",
        "质量管理": "运维过程纳入质量管理：每个问题记录根因并纳入知识库，重复问题专项治理；每季度开展一次系统健康检查（性能、日志、备份、安全），出具检查报告并提出优化建议。",
    }
    for index, name in enumerate(names or ["响应流程", "服务方式", "重大疑难问题解决方式", "服务人员数量及能力水平", "其他优惠服务措施"], start=1):
        text = next((value for key, value in content.items() if key in name), "我方按采购要求提供相应服务。")
        heading = re.sub(r"^(?:企业提供|出现的)", "", name)
        draft.children.append((heading, [_p(text)]))
    if "质量管理" in rule:
        draft.children.append(("运维质量管理措施", [_p(content["质量管理"])]))
    return draft


Topic = Callable[[ItWriter], Draft]
_TOPICS: tuple[tuple[str, Topic], ...] = (
    (r"需求理解|项目理解|需求分析|项目认识", write_understanding),
    (r"总体设计|总体方案|整体方案|系统设计|功能设计", write_overall_design),
    (r"技术方案|技术架构|技术路线", write_technical),
    (r"实施方案|实施计划|项目管理|进度计划", write_implementation),
    (r"移交|培训|交付|验收", write_handover),
    (r"承诺", write_commitment),
    (r"运维|售后|质保|服务保障", write_operation),
)


def write_score_point(ctx: ComposeContext, facts: dict, title: str, group: str) -> Draft | None:
    if not facts.get("requirements"):
        return None
    for pattern, topic in _TOPICS:
        if re.search(pattern, title):
            return topic(ItWriter(ctx, facts))
    return None
