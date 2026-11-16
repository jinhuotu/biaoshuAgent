"""EPC 技术标正文编写器（不调用模型）。

按评分要点的标题路由到专题写法，用 ``project_facts`` 抽出的项目事实（范围、工期、设备参数、规范、
管理要求等）组织正文：招标文件有的事实照录，设备 / 工作内容出现才展开对应段落，进度表按实际工期分配。
抽不到的事实不写具体数值，不补造招标文件没有的项目信息；工期目标、响应时限等属于投标人承诺的内容按行业惯例给出。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from api.services.bidgen.document_model import ComposeContext, paragraph, table


@dataclass
class Draft:
    """一个评分要点的正文：直接正文块 + 三级小节 (标题, 正文块)。"""

    blocks: list[dict] = field(default_factory=list)
    children: list[tuple[str, list[dict]]] = field(default_factory=list)
    title: str = ""  # 评分要点原文过长时使用的小节标题


def _p(text: str) -> dict:
    return paragraph(text)


def _join(items: list[str], sep: str = "、") -> str:
    return sep.join(item for item in items if item)


def split_days(total: int, weights: list[float]) -> list[int]:
    """按权重把总工期分到各阶段，取整后合计仍等于总工期（最大余数法）。"""
    if total <= 0:
        return [0] * len(weights)
    raw = [total * weight / sum(weights) for weight in weights]
    days = [max(1, int(value)) for value in raw]
    order = sorted(range(len(raw)), key=lambda index: raw[index] - int(raw[index]), reverse=True)
    index = 0
    while sum(days) < total:
        days[order[index % len(order)]] += 1
        index += 1
    while sum(days) > total:
        largest = max(range(len(days)), key=lambda i: days[i])
        days[largest] -= 1
    return days


class Writer:
    """编写上下文：项目事实的取用与常用表述。"""

    def __init__(self, ctx: ComposeContext, facts: dict) -> None:
        self.ctx = ctx
        self.facts = facts
        self.name = facts.get("name") or ctx.project_name
        self.owner = facts.get("owner") or ctx.purchaser_names
        self.days = int(facts.get("schedule_days") or 0)
        self.charging = facts.get("domain") == "charging"
        self.unit = "单个场站" if facts.get("schedule_unit") else "本工程"
        self.short_unit = "单站" if facts.get("schedule_unit") else "本工程"

    # ---- 事实 ----
    def has(self, *features: str) -> bool:
        return any(feature in self.facts.get("features", []) for feature in features)

    @property
    def period(self) -> str:
        return self.facts.get("schedule") or "合同约定工期"

    @property
    def location(self) -> str:
        return self.facts.get("location") or "项目所在地"

    @property
    def city(self) -> str:
        matched = re.match(r"(.{2,6}?[市州])", self.location)
        return matched.group(1) if matched else self.location

    def equipment(self, *keywords: str) -> list[dict]:
        return [item for item in self.facts.get("equipment", []) if any(k in item["name"] for k in keywords)]

    @property
    def dc_desc(self) -> str:
        powers = self.facts.get("dc_powers") or []
        currents = self.facts.get("terminal_currents") or []
        text = "分体式直流充电机" if self.equipment("分体式") else "直流充电设备"
        if powers:
            text += f"（装机功率{powers[0]}kW～{powers[-1]}kW" if len(powers) > 1 else f"（装机功率{powers[0]}kW"
            text += f"，共{self.facts.get('dc_charger_types')}种配置）" if self.facts.get("dc_charger_types", 0) > 1 else "）"
        if currents:
            text += "及" + "／".join(f"{c}A" for c in currents) + "直流充电终端"
        return text

    @property
    def currents_text(self) -> str:
        currents = self.facts.get("terminal_currents") or []
        return "／".join(f"{c}A" for c in currents) or "各电流等级"

    @property
    def gun_clause(self) -> str:
        length = self.facts.get("gun_length")
        return f"，枪线长度不少于{length}" if length else ""

    @property
    def ac_desc(self) -> str:
        return f"{self.facts['ac_power']}交流充电桩" if self.facts.get("ac_power") else "交流充电桩"

    def standards(self, *keywords: str, exclude: tuple[str, ...] = ()) -> str:
        items = [s for s in self.facts.get("standards", [])
                 if (not keywords or any(k in s["name"] for k in keywords)) and not any(k in s["name"] for k in exclude)]
        return "、".join(f"《{s['name']}》（{s['code']}）" for s in items)

    def scope_layers(self) -> list[str]:
        """按“设计 / 土建 / 电气设备 / 智慧运营”归纳的工作界面。"""
        layers = []
        design = self.facts.get("design_scope")
        if design:
            layers.append("设计层面，包括" + design.rstrip("。").removeprefix("本项目的"))
        civil = []
        if self.has("site_work"):
            civil.append("场地整理与路面拆除恢复")
        civil.append("土方开挖回填")
        if self.has("transformer"):
            civil.append("箱变基础")
        if self.has("dc_charger", "ac_charger"):
            civil.append("充电设备基础")
        if self.has("duct"):
            civil.append("电缆排管与市政排管通管")
        if self.has("drainage"):
            civil.append("场站排水")
        if self.charging:
            civil.append("车位地坪、标线、车挡与标识等场站标准化设施")
        layers.append("土建与场站层面，包括" + _join(civil))
        electrical = []
        if self.has("transformer"):
            electrical.append("10kV进线及箱式变电站（变压器、开关、多功能仪表、补偿电容SVG）")
        electrical.append("成套配电箱及电缆、电缆头、接地装置")
        if self.has("dc_charger"):
            electrical.append(self.dc_desc)
        if self.has("ac_charger"):
            electrical.append(self.ac_desc)
        electrical.append("电气试验与系统调试")
        layers.append("电气与设备层面，包括" + _join(electrical))
        smart = []
        if self.has("barrier"):
            smart.append("出入口道闸与车牌识别")
        if self.has("parking_lock"):
            smart.append("4G智能车位锁")
        if self.has("camera"):
            smart.append("网络摄像机")
        if self.has("network"):
            smart.append("网络交换机")
        if self.has("lighting"):
            smart.append("庭院灯")
        if smart or self.has("platform"):
            text = "智慧运营层面，包括" + _join(smart)
            if self.has("platform"):
                text += ("及" if smart else "") + "全部设备接入招标人指定运营平台"
            layers.append(text)
        return layers

    def construction_phases(self) -> list[tuple[str, str, float, str]]:
        """(阶段, 主要工作, 权重, 关键控制点)。"""
        if self.charging:
            return [
                ("施工准备", "场地移交、围挡搭设、临水临电、测量放线、管线探测、材料报审", 8, "场地移交单、探测成果、临电验收"),
                ("土建与基础", _join(["路面拆除" if self.has("site_work") else "", "土方开挖", "箱变基础" if self.has("transformer") else "",
                                    "充电设备基础", "电缆排管" if self.has("duct") else "", "回填"]), 32, "基础轴线与标高、混凝土强度、排管通球"),
                ("电缆通道与敷设", "电缆保护管、电缆敷设、电缆头制作、接地装置", 20, "电缆绝缘、接头压接、接地电阻"),
                ("设备安装", _join(["箱变就位" if self.has("transformer") else "", "配电箱安装", "充电机与终端安装",
                                  "道闸地锁监控安装" if self.has("barrier", "parking_lock", "camera") else ""]), 22, "设备就位精度、螺栓力矩、接线正确性"),
                ("调试并网", _join(["单体调试", "系统联调", "保护整定", "送电", "平台接入" if self.has("platform") else "", "充电试验"]), 20,
                 "绝缘耐压、保护动作、平台在线率"),
                ("收尾与验收", "路面恢复、标线标识、场地清理、资料整理、预验收与整改、竣工验收", 26, "观感质量、资料完整性、验收结论"),
            ]
        return [
            ("施工准备", "场地移交、临时设施、测量放线、图纸会审、材料报审", 8, "开工条件确认"),
            ("基础与土建", "土方、基础及主体结构施工", 40, "轴线标高、混凝土强度、隐蔽验收"),
            ("安装工程", "设备与管线安装", 25, "安装精度、接线正确性"),
            ("调试试运行", "单机调试、系统联调、试运行", 12, "调试记录、试运行报告"),
            ("收尾与验收", "收尾整改、资料整理、竣工验收", 15, "验收结论"),
        ]


# ================= 六、承包人建议书 =================

def write_overview(w: Writer) -> Draft:
    f = w.facts
    draft = Draft()
    intro = [f"{w.name}位于{w.location}"]
    if w.owner:
        intro.append(f"由{w.owner}投资建设")
    if f.get("funding"):
        ratio = f"，出资比例{f['funding_ratio']}" if f.get("funding_ratio") else ""
        intro.append(f"资金来源为{f['funding']}{ratio}")
    text = "，".join(intro) + "。本项目采用EPC工程总承包方式发包"
    if f.get("estimated_price"):
        text += f"，合同估算价约{f['estimated_price']}"
    if f.get("schedule"):
        text += f"，计划工期为{f['schedule']}"
    understanding = [_p(text + "。")]
    if f.get("overview"):
        overview = re.sub(r"本项目合同估算价[^。]*。?", "", f["overview"]).rstrip("。") if f.get("estimated_price") else f["overview"].rstrip("。")
        understanding.append(_p(f"根据招标文件，本项目工程概况为：{overview}。"))
    layers = w.scope_layers()
    if layers:
        numerals = "一二三四五六"
        detail = "；".join(f"{numerals[i]}是{layer}" for i, layer in enumerate(layers))
        kind = "分布式能源基础设施系统工程" if w.charging else "设计、采购、施工一体化的系统工程"
        understanding.append(_p(f"我方理解，本项目的建设内容并非单一的设备采购安装，而是一个{kind}，其工作界面可归纳为{len(layers)}个层面：{detail}。"))
    special = f.get("special_requirements") or []
    if special or f.get("full_cost_note"):
        items = [re.sub(r"^特别强调[：:]", "", s).rstrip("；;。") for s in special]
        if f.get("full_cost_note"):
            items.append(f["full_cost_note"].rstrip("。"))
        numbered = "；".join(f"其{'一二三四五六'[i]}，{item}" for i, item in enumerate(items[:6]))
        understanding.append(_p(f"我方充分注意到招标文件中{len(items[:6])}项具有约束力的特别要求，并将其作为方案编制的强制条件：{numbered}。我方投标报价的编制、施工组织与成本控制均以此为基础。"))
    if w.charging:
        understanding.append(_p(f"基于上述理解，我方将本项目定位为“标准化设计、工厂化预制、装配化施工、信息化管理”的分布式充电场站集群工程，以单站为最小管理单元，以标准图集为技术载体，以批次滚动为组织方式，实现多场站并行推进、{w.short_unit}{w.days or ''}{'天' if w.days else '按期'}闭环交付。"))
    draft.children.append(("项目概况与建设内容理解", understanding))

    points = []
    if w.charging:
        powers = f.get("dc_powers") or []
        span = f"充电桩功率配置从单台{powers[0]}kW到{powers[-1]}kW不等，" if len(powers) > 1 else ""
        points.append(f"场站点多面广、单点规模差异大。各场站受停车位数量、可用容量、供电电源点位置、场地权属与既有地下管线的制约，{span}箱变容量与进出线方式各异。若按场站逐一设计，将造成设计周期长、图纸质量不稳定、施工交底成本高。我方拟采用“标准图＋参数化调整”的设计策略，将箱变基础、终端基础、排管断面、接地网、配电系统主接线固化为标准模块，仅对容量、回路数、平面布置做参数化调整，既保证合规性，又大幅压缩设计周期。")
        if w.has("site_work"):
            points.append("既有场地改造，地下条件不确定。项目多为既有停车场或道路附属场地改造，存在原路面结构层厚度不明、雨污水与通信管线走向不明、既有电缆路径不明等风险。我方将在设计阶段组织物探与管线探测，施工阶段执行“先探后挖、人工开挖验证”的原则，对开孔打洞、原有排管疏通等工序单独编制专项方案，避免破坏既有管线造成停工与索赔。")
        if w.has("power_supply", "transformer"):
            points.append(f"供电接入是本项目的关键路径。高压部分涉及箱变、10kV电缆、电缆头、接地、高压装置系统调试及供电公司投资界面协调，供电答复单的取得、供电方案的批复、市政排管通管、计量装置安装与送电，任一环节滞后都将直接侵蚀{w.days or '合同'}{'天的' if w.days else ''}总工期。我方将供电报装与协调工作前置至设计启动阶段，设置专职外协工程师驻点对接，把“等供电”变为“跟供电、催供电、配合供电”。")
        if f.get("terminal_currents"):
            top = max(f["terminal_currents"])
            points.append(f"大电流直流终端的安装质量直接决定运营体验。{top}A终端与小电流终端对电缆截面、接头压接、散热空间、枪线弯曲半径的要求差异明显{w.gun_clause}，安装标高与防撞、防水、防尘等级必须严格匹配。我方将编制《直流充电终端安装作业指导书》，对压接模具、压接顺序、绝缘电阻、接地连续性逐台记录，杜绝“装得上、用得差”。")
        if w.has("platform"):
            points.append("全部设备须接入招标人指定运营平台。道闸、车位锁、摄像机、充电设备品牌与协议各异，数据传输稳定性与功能完整性直接影响后期运营。我方在设计阶段即确定通信方式、协议与点位表，设备采购合同中约定厂家协议开放与联调义务，平台联调与设备安装同步推进。")
    else:
        points.append("设计、采购、施工交叉作业多，接口复杂。我方以设计为龙头统筹采购与施工，设计成果按施工顺序分批交付，长周期设备在初步设计阶段锁定参数与供货周期。")
        points.append("工期紧、质量要求高。我方编制三级进度计划，关键线路设置里程碑节点，质量实行样板引路与关键工序旁站。")
    if f.get("dust_standard"):
        points.append(f"扬尘治理与文明施工标准高。项目位于{w.location}，须严格落实{w.city}扬尘污染防治管理规定和施工工地扬尘治理“8个100%”要求，土方作业、路面切割、材料运输均受时段与作业方式限制。我方将把扬尘治理设施、雾炮与喷淋、车辆冲洗、在线监测纳入施工总平面布置，按“一站一策”编制环保专项方案。")
    draft.children.append(("项目特点、难点与建设条件分析", [_p(f"（{i}）{text}") for i, text in enumerate(points, start=1)]))

    plan = [_p("我方按“设计为龙头、计划为主线、成本为核心、接口为重点”的思路构建EPC总承包管理体系，实行项目经理负责制下的矩阵式管理：纵向按“设计—采购—施工—调试—移交”五个阶段设置阶段负责人，横向按“质量、安全、进度、造价、合同、信息”六条线设置职能主管，阶段与职能交叉形成责任矩阵，避免多头指挥与责任真空。")]
    plan.append(_p("（1）设计牵头。中标后3日内成立项目设计组进场，7日内完成现场踏勘与资料收集" + ("，同步启动供电报装" if w.has("power_supply", "transformer") else "") + "；以标准图集为骨架开展施工图设计，设计成果直接服务于采购与施工，做到“图到、料定、工序清”。设计人员全程驻场服务，重大工序设计交底到场，实现设计与施工的双向校核。"))
    long_lead = _join([n for n, k in (("箱式变电站", "transformer"), ("直流充电设备", "dc_charger"), ("交流充电桩", "ac_charger")) if w.has(k)] + ["成套配电箱", "电缆"])
    plan.append(_p(f"（2）采购前置。对{long_lead}等长周期设备材料，在设计阶段即锁定技术参数与供货周期；按招标文件要求，主要材料采购前向发包人提供供应商资料、报价与检测报告，设备采购前提供参数与指标，经发包人认可后确定。主要设备进场计划与施工进度计划实行“双周滚动”匹配。"))
    plan.append(_p("（3）施工流水。按" + ("场站" if w.charging else "施工区段") + "划分作业面，组建土建、电气、设备安装三个专业作业队，以“基础—管线—电缆—设备—调试”为工序主线组织流水施工，相邻作业面错峰开工，形成“一批设计、一批采购、一批施工、一批调试”的滚动节奏。"))
    plan.append(_p("（4）调试与移交一体化。设备安装完成后即启动单体调试与系统联调" + ("，同步开展平台接入与数据核对" if w.has("platform") else "") + "，把调试发现的问题在移交前闭环，避免“带病移交”。"))
    parties = _join(["招标人", "供电公司" if w.has("power_supply") else "", "场地产权单位", "运营平台服务商" if w.has("platform") else "", "市政管线单位" if w.has("duct") else "", "监理单位"])
    plan.append(_p(f"（5）接口管理。设立接口管理台账，明确与{parties}之间的接口事项、责任人、时限与交付物，每周更新、每周通报。"))
    draft.children.append(("总体管理方案", plan))

    quality = f.get("construction_quality") or "工程质量达到合格标准"
    schedule_goal = f"{w.unit}{w.days}日历天完成设计、施工、安装调试" + ("、并网" if w.charging else "") + "及验收移交" if w.days else "按合同工期完成全部工作"
    safety = (f.get("safety") or "不发生安全生产事故").rstrip("。")
    rows = [
        ["1", "质量目标", f"{quality}；分部分项工程一次验收合格率100%" + ("；平台接入成功率100%" if w.has("platform") else "") + "；无质量事故与质量投诉",
         "三级检验、样板引路、关键工序旁站、设备开箱验收、隐蔽工程影像留证"],
        ["2", "进度目标", schedule_goal + "；按招标人下达的批次计划节点交付，节点履约率100%", "标准图集、长周期设备前置、流水施工、周计划纠偏" + ("、供电协调专班" if w.has("power_supply") else "")],
        ["3", "安全目标", safety + "；不发生火灾、触电、坍塌、高处坠落、机械伤害事故", "安全生产责任制、危大工程专项方案、临电三级配电两级保护、班前教育与隐患排查"],
        ["4", "投资目标", "结算金额不超过合同价，设计变更与现场签证引起的费用增加严格受控", "限额设计、方案比选、变更事前审批、月度成本分析、材料价格锁定"],
    ]
    if f.get("dust_standard"):
        rows.append(["5", "环保目标", "扬尘治理“8个100%”全面达标，噪声、污水、固废排放符合规定，不因环保问题被通报或处罚", "一站一策环保方案、雾炮喷淋、车辆冲洗、在线监测、渣土密闭运输"])
    if f.get("safety_standardization"):
        rows.append([str(len(rows) + 1), "文明施工目标", f"创建{f['safety_standardization']}", "按标准化指南布置现场、定期自评与整改"])
    rows.append([str(len(rows) + 1), "服务目标", "招标人满意度不低于95%，质保期内2小时响应、24小时到场", "驻场服务、备品备件库、运维培训、回访制度"])
    draft.children.append(("总体管理目标", [_p("我方对本项目作出如下可考核的管理承诺："),
                                            table(["序号", "目标类别", "具体目标值", "保证措施要点"], rows, caption="总体管理目标", widths=[1.2, 2.2, 7.0, 6.0])]))
    return draft


def write_organization(w: Writer) -> Draft:
    f = w.facts
    draft = Draft()
    pm = f.get("pm_requirement", "")
    pm = re.sub(r"^[\d.]+\s*", "", pm)
    pm = re.sub(r"^工程总承包项目经理资质要求[：:]\s*", "", pm).rstrip("；;。")
    design_lead = (f.get("design_lead_requirement") or "").rstrip("。")
    org = [_p(f"本项目设“{w.name}项目部”，实行项目经理负责制。项目部下设设计组、工程管理部、采购部、质量安全部、造价合同部、综合协调部六个职能部门，并在现场设施工班组与调试小组。")]
    lead = "项目经理" + (f"按招标文件要求配备：{pm}" if pm else "由具备相应注册执业资格与安全生产考核合格证书的人员担任")
    if f.get("attendance_days"):
        lead += f"；项目经理常驻施工现场，每月在现场工作时间不少于{f['attendance_days']}天"
    lead += "，对设计、采购、施工、调试、移交全过程负总责。"
    lead += f"设计负责人按招标文件要求配备（{design_lead}），" if design_lead else "设计负责人由具备相应注册执业资格的人员担任，"
    lead += "对设计成果的技术可靠性、经济合理性与合规性负责，并保证设计人员驻场服务；技术负责人由具备高级工程师职称的人员担任，负责技术方案、施工组织设计与技术交底的审定。"
    org.append(_p(lead))
    rows = [
        ["1", "项目经理", "1", "全面负责合同履约，主持项目策划、资源配置、重大方案决策与对外协调；对质量、安全、进度、造价负总责"],
        ["2", "设计负责人", "1", "主持方案、初设、施工图设计，组织专业会签与图纸会审；负责限额设计指标控制与设计变更技术把关"],
        ["3", "技术负责人", "1", "负责施工组织设计、专项施工方案、技术交底与技术难题处理；组织竣工资料编制"],
        ["4", "设计组", "6", ("电气一次、电气二次、土建结构、总图排水、概预算、图纸校审" if w.charging else "建筑、结构、给排水、电气、暖通、概预算") + "各专业人员配置，驻场配合施工"],
        ["5", "工程管理部", "4", "施工计划编制与执行、作业面调度、工序交接、班组管理、进度统计"],
        ["6", "采购部", "3", "设备材料询价比价、供应商资料报审、采购合同、到货验收、仓储与配送"],
        ["7", "质量安全部", "3", "质量计划与检验批划分、隐蔽验收、安全巡查、危大工程旁站、扬尘治理、应急管理"],
        ["8", "造价合同部", "2", "成本测算与控制、变更签证、计量支付、结算编制、合同管理"],
        ["9", "综合协调部", "2", _join(["供电报装与并网协调" if w.has("power_supply") else "", "场地权属与市政对接", "资料档案", "后勤保障"])],
    ]
    org.append(table(["序号", "部门／岗位", "人数", "主要职责"], rows, caption="项目管理机构设置与职责分工", widths=[1.2, 2.8, 1.4, 11.0]))
    draft.children.append(("项目管理机构设置", org))

    if w.charging:
        design_rows = [
            ["1", "电气（一次）", "2", "供电方案、负荷计算、主接线、箱变与配电箱选型、电缆选型与敷设设计", "方案至竣工图"],
            ["2", "电气（二次）", "1", "计量与保护配置、充电设施通信" + ("与平台接入" if w.has("platform") else "") + ("、监控与道闸系统设计" if w.has("camera", "barrier") else ""), "初设至施工图"],
            ["3", "土建／结构", "1", "箱变基础、设备基础、设备支架、场地硬化设计", "初设至施工图"],
            ["4", "总图／给排水", "1", "场站平面布置、交通组织、排水与管线综合", "方案至施工图"],
            ["5", "概预算", "1", "限额指标分解、施工图预算、清单与设备材料表编制", "初设至结算"],
        ]
        lead_major = "电气专业为主，土建结构与总图排水为辅"
    else:
        design_rows = [
            ["1", "建筑", "1", "总平面与建筑方案、施工图", "方案至竣工图"],
            ["2", "结构", "1", "结构计算与施工图", "初设至施工图"],
            ["3", "给排水／暖通", "1", "给排水、消防与暖通设计", "初设至施工图"],
            ["4", "电气", "2", "供配电、照明、防雷接地与弱电设计", "方案至竣工图"],
            ["5", "概预算", "1", "限额指标分解、施工图预算", "初设至结算"],
        ]
        lead_major = "各专业协同"
    draft.children.append(("设计人员配备计划", [
        _p(f"设计组按“{lead_major}”配置，概预算人员全程参与限额设计与造价控制。设计高峰期投入不少于6人，施工期保留不少于2人驻场，负责设计交底、图纸答疑与竣工图编制。"),
        table(["序号", "专业", "人数", "主要工作内容", "投入阶段"], design_rows, caption="设计人员配备计划", widths=[1.2, 2.6, 1.2, 8.4, 3.0]),
    ]))
    attendance = f"项目经理及主要管理人员每月在现场工作时间不少于{f['attendance_days']}天，" if f.get("attendance_days") else ""
    draft.children.append(("人员到场与动态调配", [
        _p("中标通知书发出后3日内，项目经理、设计负责人、技术负责人到场；7日内项目部主要管理人员到位率不低于80%；开工前全部管理人员到位。施工高峰期按作业面数量动态增配作业人员，每个开工作业面配置不少于1名施工员、1名安全员、1名质检员，电工作业人员持特种作业操作证上岗，专职安全生产管理人员持有效安全生产考核合格证书。"),
        _p(f"项目部实行“人员到岗考勤＋履职记录”双轨考核，{attendance}主要管理人员不得兼职，离开现场须书面报招标人同意；未经招标人同意不得更换项目经理、设计负责人和技术负责人，确需更换的，替换人员资格不低于原人员并提前报审。"),
    ]))
    return draft


def write_controls(w: Writer) -> Draft:
    f = w.facts
    draft = Draft()
    quality = f.get("construction_quality") or "合格"
    check = []
    if w.has("transformer"):
        check.append("箱变基础浇筑")
    check += ["接地装置埋设", "电缆头制作", "设备就位与接线", "系统调试"]
    device = [_p(f"质量目标为{quality}。我方按“策划—实施—检查—改进”闭环建立质量保证体系，开工前编制《项目质量计划》，明确检验批划分、控制点设置、见证取样计划与验收标准；施工中执行“三检制”（自检、互检、专检）与工序交接检查，上道工序不合格不得进入下道工序；对{_join(check)}等关键工序实行旁站，旁站记录与影像资料同步归档。")]
    if w.charging:
        keys = ["装机功率", "终端数量与电流等级（" + w.currents_text + "）"]
        if f.get("gun_length"):
            keys.append(f"枪线长度不少于{f['gun_length']}")
        device.append(_p(f"设备质量控制从源头抓起：主要设备进场时核对品牌、型号、参数与招标文件“发包人要求”的一致性，查验出厂合格证、型式试验报告及充电设施相关检测报告；充电设备须核对{_join(keys)}等关键参数" + ("；箱变须核对变压器容量、型号与开关、仪表、补偿装置品牌" if w.has("transformer") else "") + "。材料设备未经报验合格不得使用，经检验不合格的一律退场并记录。"))
    draft.children.append(("质量管理与控制", device))
    days_text = f"以{w.unit}{w.days}日历天为总控目标" if w.days else "以合同工期为总控目标"
    critical = _join(["设计出图", "设备到货", "供电送电" if w.has("power_supply", "transformer") else "", "验收移交"])
    draft.children.append(("进度管理与控制", [
        _p(f"{days_text}，编制三级计划：一级为项目总进度计划（按批次与{'场站' if w.charging else '区段'}分解），二级为月度施工计划，三级为周作业计划。总进度计划采用横道图与网络图双表达，识别{critical}等关键线路，设置里程碑节点与预警阈值。"),
        _p("进度控制采取“周检查、月考核、偏差即纠”的机制：每周召开生产例会，对比计划与实际完成量，对滞后超过3天的工序启动纠偏，纠偏措施包括增加作业面、延长作业时间、调整工序搭接、增配资源；每月向招标人报送进度月报，说明完成情况、存在问题与下月安排。" + ("对供电报装、市政排管通管等外部依赖事项，提前介入、专人跟办，把外部不确定性对工期的影响降到最低。" if w.has("power_supply", "duct") else "")),
    ]))
    cap = f"以工程费最高投标限价{f['engineering_price_cap']}为上限，" if f.get("engineering_price_cap") else ""
    cost = [_p(f"投资控制贯穿设计与施工全过程。设计阶段推行限额设计，{cap}将工程费按专业与{'场站' if w.charging else '单位工程'}分解，作为方案比选与图纸审核的硬约束；施工阶段执行“先算后干、边干边算”，每月进行成本分析，对比预算成本、实际成本与目标成本，偏差超过1%即分析原因并采取措施。")]
    cost.append(_p("变更与签证实行事前审批：任何引起费用增减的设计变更、现场签证，均须先提交技术经济比较说明，经招标人及相关部门审核同意后实施；未经批准的变更不得施工，不得进入结算。按招标文件要求，总承包人开展工作前提供造价依据报发包人及相关部门审核；主要材料设备采购前报送供应商资料与报价，经发包人同意后采购。"))
    if f.get("full_cost_note"):
        cost.append(_p(f"我方已充分理解：{f['full_cost_note']}我方报价已足额考虑上述费用，施工期间不以此为由另行计取。"))
    draft.children.append(("投资（造价）管理与控制", cost))
    contract = [_p("项目部设合同管理员，开工前完成合同交底，将合同价、工期、质量标准、付款节点、违约责任、索赔时限等关键条款转化为项目部的管理指标与岗位责任。合同履约过程中建立合同台账，记录履约节点、付款申请、变更签证、往来函件；对招标人指令、现场条件变化、外部单位干扰等可能引起工期与费用变化的事件，在合同约定期限内以书面形式提出，做到“事前有函、事中有据、事后有账”。")]
    if f.get("subcontract_allowed") is False:
        contract.append(_p("本项目不允许分包，我方承诺以自有队伍完成全部设计、采购与施工内容，不转包、不违法分包；确需设备厂家参与调试的，由我方统一组织、统一管理并承担全部责任，不改变合同主体责任。"))
    draft.children.append(("合同管理与控制", contract))
    draft.children.append(("信息管理与控制", [
        _p("建立以“一库一表一平台”为核心的信息管理体系：一库为项目文档库，按" + ("场站" if w.charging else "单位工程") + "、专业、工序分类存储设计图纸、施工记录、检验资料、影像资料与验收文件，实行电子化归档与权限管理；一表为进度—质量—安全—造价四维周报，每周更新并向招标人报送；一平台为" + ("设备接入与调试数据平台，用于设备通信参数配置、平台联调与数据核对" if w.has("platform") else "项目协同管理平台，用于任务派发、问题闭环与资料流转") + "。"),
        _p("项目全过程推行移动端巡检与影像留证，隐蔽工程、关键工序、设备开箱、调试数据均留存带时间水印的照片与视频，作为验收与结算的原始依据；竣工时同步提交电子档案，确保资料与实体一致、可追溯。"),
    ]))
    return draft


def write_handover(w: Writer) -> Draft:
    f = w.facts
    draft = Draft()
    acceptance_codes = w.standards("验收", "检验")
    special = _join(["供电公司送电验收" if w.has("transformer", "power_supply") else "", "防雷接地检测", "消防器材配置检查", "排水系统通水试验" if w.has("drainage") else ""])
    accept = [_p(f"验收按“分项—分部—单位工程—专项—竣工验收”五级组织。分项工程由项目部自检合格后报监理验收；分部工程由总监理工程师组织验收；单位工程完工后组织预验收，整改闭环后申请竣工验收。专项验收同步推进，包括{special}" + (f"，以及依据{acceptance_codes}开展的竣工验收" if acceptance_codes else "") + "。")]
    if w.charging:
        accept.append(_p("充电设施调试与验收内容涵盖：绝缘电阻与接地电阻测试、耐压试验、充电控制导引与通信协议（GB/T 27930）一致性验证、充电功率与计量精度校验、急停与保护功能试验" + ("、平台接入与数据上传连续性测试" if w.has("platform") else "") + "。所有试验数据形成记录表，作为竣工验收资料的组成部分。"))
    rows = [["1", "隐蔽工程验收", "监理／项目部", "隐蔽验收记录、影像资料、材料合格证"]]
    if w.has("transformer"):
        rows.append(["2", "箱变及高压系统送电验收", "供电公司／项目部", "送电方案、试验报告、送电记录"])
    rows.append([str(len(rows) + 1), "设备单机与系统调试", "项目部／设备厂家", "调试记录" + ("、平台联调记录" if w.has("platform") else "") + "、参数配置表"])
    rows.append([str(len(rows) + 1), "单位工程预验收", "监理／项目部", "预验收报告、整改闭环记录"])
    rows.append([str(len(rows) + 1), "竣工验收", "招标人组织", "竣工验收报告、竣工图、设备清单、操作手册"])
    rows.append([str(len(rows) + 1), "移交与培训", "项目部", "资产清册、备品备件清单、培训记录、移交确认单"])
    accept.append(table(["序号", "验收／移交节点", "组织方", "主要交付成果"], rows, caption="主要验收节点与交付成果", widths=[1.2, 4.6, 3.4, 7.2]))
    draft.children.append(("验收管理", accept))
    draft.children.append(("结算管理", [_p("实行过程结算与竣工结算相结合。每月按实际完成且验收合格的工程量计量，经监理与招标人确认后作为进度款支付依据；变更、签证按月汇总，随进度款一并报审，避免竣工时集中爆发争议。竣工结算在竣工验收合格后按合同约定期限编制完成，结算资料包括结算书、工程量计算书、变更签证台账、材料价格确认单、设备发票与验收记录，积极配合招标人及其委托的造价咨询、审计单位完成审核。")]))
    deliver = []
    for item in f.get("deliverables", []):
        deliver.append(f"{item['name']}{item['copies']}份" + (f"（{item['note']}）" if item.get("note") else ""))
    as_built = f"竣工资料在竣工后{f['as_built_days']}日内移交，" if f.get("as_built_days") else ""
    handover = [_p("移交内容包括实体工程、技术资料、备品备件与运营能力四个方面。实体工程移交前完成清洁、标识、锁具与钥匙交接；技术资料按招标人要求提交" + (_join(deliver, "、") + "，并提供不加密的电子文件；" if deliver else "设计成果与竣工资料，并提供电子文件；") + as_built + "备品备件按设备厂家标准配置并列出清单；运营能力移交包括对招标人运营人员开展设备操作、日常维护、故障判别与应急处置培训，培训后经考核确认。")]
    if w.has("power_supply"):
        handover.append(_p("依据供电答复单由供电公司投资建设的内容，由我方免费无条件协调建设完成，外网市政电力排管完成通管并满足供电公司各项技术规范要求，移交本工程时该部分同步具备正式使用条件。"))
    if w.has("platform"):
        handover.append(_p("移交时同步提交《设备接入运营平台确认单》，逐站逐台核对设备编号、通信状态、数据上传完整率，确保移交时全部设备在线、数据稳定、功能满足招标人运营管理要求。"))
    draft.children.append(("移交管理", handover))
    return draft


# ================= 七、设计组织技术措施 =================

def write_design_quality(w: Writer) -> Draft:
    codes = w.standards("设计", "技术标准", "规范", exclude=("验收", "检验"))
    inputs = _join(["场地实测图", "既有管线资料", "供电答复单" if w.has("power_supply") else "", "地质与水文条件", "招标人运营要求", "设备厂家技术资料"])
    count = len(inputs.split("、"))
    compare = "供电电源点与箱变位置、充电功率配置、电缆路径与敷设方式" if w.charging else "总平面布置、结构选型、主要设备选型"
    return Draft(blocks=[
        _p("我方按ISO 9001质量管理体系要求建立设计质量控制流程，实行“设计人自校—专业校核—专业审核—项目审定”四级校审制度，各级校审均填写校审记录单，未通过校审的图纸不得出图、不得交付施工。设计成果实行专业会签，各专业在图纸交付前完成接口会签，重点核对设备基础与设备尺寸、电缆路径与土建预留、负荷计算与配电容量、清单工程量与图纸数量的一致性。"),
        _p(f"（1）源头控制。设计输入实行清单化管理，收集并确认{inputs}等{count}类输入，输入不完整不开工；对输入资料存在矛盾或缺失的，以书面形式向招标人提出澄清，不以假设代替事实。"),
        _p(f"（2）过程控制。方案设计阶段开展不少于两个方案的技术经济比选（{compare}），比选结论经招标人确认后进入初步设计；施工图阶段执行“一图一校一卡”，每张图纸附设计要点卡，明确执行规范条文、强制性条文核查项与施工注意事项。"),
        _p("（3）合规性控制。设计严格执行" + (codes or "国家及地方现行设计规范") + "等现行标准" + (f"，满足招标文件“{w.facts['quality'].split('设备')[0].removeprefix('设计：').rstrip('。')}”的要求" if w.facts.get("quality", "").startswith("设计") else "") + "，逐条对照工程建设强制性条文开展专项自查，形成强条执行检查表，随图纸一并归档。"),
        _p("（4）服务控制。设计人员实行驻场服务，施工前完成设计交底与图纸会审，会审意见形成纪要并落实到图纸修改；施工过程中对图纸疑问在24小时内答复，重大技术问题48小时内提出处理方案；竣工图随施工进度同步绘制，确保与实体一致。"),
    ])


def write_design_risk(w: Writer) -> Draft:
    rows = [["1", "场地及地下管线资料不全", "开挖破坏管线、设计返工、工期延误", "设计前组织物探与管线探测，重要部位人工开挖验证；图纸标注“先探后挖”与保护范围；预留设计变更通道"]]
    if w.has("power_supply", "transformer"):
        rows.append([str(len(rows) + 1), "供电方案与答复单不确定", "高压系统设计无法定案、送电滞后", "设计启动即报装，明确容量、电源点、计量方式；按答复单分阶段出图，先出土建与低压部分，高压部分随答复单固化"])
    rows.append([str(len(rows) + 1), "设备参数与品牌未最终确定", "基础尺寸不符、接口不匹配", "以招标文件“发包人要求”参数为设计基准，基础与预留按上限尺寸设计，设备经发包人认可后复核确认；建立设备参数确认单"])
    if w.charging:
        rows.append([str(len(rows) + 1), "分散场站设计质量不稳定", "图纸错漏碰缺、施工返工", "推行标准图集与模块化设计，统一做法与节点；设置专职校审人，按场站抽检不少于30%的图纸"])
    rows.append([str(len(rows) + 1), "图审与专项审查意见集中", "出图延误", "出图前开展内部预审与强条自查；预留图审修改期；与图审机构提前沟通关键技术处理原则"])
    rows.append([str(len(rows) + 1), "限额指标被突破", "造价超限、方案反复", "限额指标分解到专业与" + ("场站" if w.charging else "单位工程") + "，超限即启动方案比选；概预算人员参与每一轮图纸评审"])
    if w.has("platform"):
        rows.append([str(len(rows) + 1), "设备通信协议不一致", "平台接入受阻、数据不稳定", "设计阶段确定通信接口、协议与点位表，采购合同约定厂家开放协议并配合联调"])
    return Draft(blocks=[
        _p("针对本项目设计阶段可能出现的风险，我方逐项制定防范与应对措施："),
        table(["序号", "风险事项", "可能影响", "防范与应对措施"], rows, caption="设计风险识别与防范措施", widths=[1.2, 3.8, 3.6, 7.8]),
    ])


def _design_days(w: Writer) -> list[int]:
    total = min(60, max(20, round(w.days * 0.27))) if w.days else 40
    return split_days(total, [5, 7, 11, 12, 4, 6])


def write_design_milestones(w: Writer) -> Draft:
    prep, scheme, prelim, drawing, review, audit = _design_days(w)
    marks = [prep, prep + scheme, prep + scheme + prelim, prep + scheme + prelim + drawing]
    marks += [marks[-1] + review, marks[-1] + review + audit]
    rows = [["1", "现场踏勘与设计输入确认", f"第{marks[0]}日", "踏勘记录、管线探测成果、设计输入清单", "设计负责人"]]
    if w.has("power_supply", "transformer"):
        rows.append(["2", "供电报装与方案报批", f"第{prep + 2}日启动", "报装资料、供电答复单、计量方案", "综合协调部"])
    for name, mark, output, owner in (
        ("方案设计", marks[1], "总平面布置、主接线方案、设备配置表、方案比选说明", "电气（一次）" if w.charging else "设计负责人"),
        ("初步设计", marks[2], "初步设计文件、负荷计算书、设备材料清单、概算", "设计负责人"),
        ("施工图设计", marks[3], "施工图、结构计算书、防雷接地、给排水、系统图", "各专业"),
        ("内部校审与专业会签", marks[4], "校审记录、会签单、强条检查表", "校核／审核人"),
        ("图纸报审与修改", marks[5], "图审意见回复、修改图纸", "设计负责人"),
    ):
        rows.append([str(len(rows) + 1), name, f"第{mark}日", output, owner])
    copies = next((item["copies"] for item in w.facts.get("deliverables", []) if "竣工图" in item["name"]), "")
    rows += [
        [str(len(rows) + 1), "设计交底与图纸会审", "开工前3日", "交底记录、会审纪要", "设计负责人"],
        [str(len(rows) + 2), "驻场配合与变更处理", "施工全过程", "设计变更单、技术核定单", "驻场设计代表"],
        [str(len(rows) + 3), "竣工图编制", "竣工验收前", f"竣工图{copies}份及电子版" if copies else "竣工图及电子版", "设计组"],
    ]
    return Draft(blocks=[
        _p("设计工作按“方案—初设—施工图—图审—交底—竣工图”六个节点组织，每个节点明确交付物、责任人与完成时限，并与采购、施工计划咬合。"),
        table(["序号", "设计节点", "完成时限（自设计启动起）", "主要交付物", "责任人"], rows,
              caption=f"重要设计节点安排（以{w.short_unit}为单元）", widths=[1.2, 3.8, 3.2, 5.8, 2.4]),
        _p("注：" + ("多场站并行时，各场站设计启动时间按开工顺序错开5～7天，形成设计流水，" if w.charging else "") + "设计资源始终保有10%的机动余量。"),
    ])


def write_design_schedule(w: Writer) -> Draft:
    days = _design_days(w)
    total = sum(days)
    stages = [
        ("准备", "踏勘、资料收集" + ("、供电报装" if w.has("power_supply") else "") + "、设计输入确认", "同步开展场地移交与临时设施搭设"),
        ("方案", "平面布置、" + ("供电方案、设备配置" if w.charging else "功能布局、结构选型") + "、方案比选", "确定主要设备位置与进线路径，指导场地平整"),
        ("初设", "负荷计算、主接线、设备选型、概算" if w.charging else "初步设计文件、设备选型、概算", "启动长周期设备询价与订货"),
        ("施工图", "电气、土建、总图、给排水施工图" if w.charging else "各专业施工图", "基础图先出，满足基础施工"),
        ("校审会签", "四级校审、专业会签、强条检查", "图纸内部冻结，避免边施工边改图"),
        ("图审", "报审、意见回复、修改", "图审期间同步进行材料报审与样板制作"),
    ]
    rows = [[name, work, str(day), relation] for (name, work, relation), day in zip(stages, days)]
    schedule = [
        _p(f"{w.short_unit}设计周期控制在{total}日历天内完成（不含招标人确认与图审机构审查时间），与施工准备、设备采购并行推进；设计与施工搭接按“先土建后电气、先低压后高压”的顺序组织，土建基础图先行出图，保证开工即有图可依。"),
        table(["阶段", "工作内容", "工期（日）", "与施工的搭接关系"], rows, caption=f"{w.short_unit}设计进度计划", widths=[2.0, 6.6, 1.8, 6.0]),
    ]
    measures = [
        _p("（1）组织保证。设计组实行“专人专站”与“专业共享”相结合，每个" + ("场站" if w.charging else "单位工程") + "指定一名设计主办人，专业设计人员跨作业面共享，避免资源闲置与忙闲不均；设设计计划管理员，按周检查出图计划执行情况。"),
        _p("（2）技术保证。以标准图集与参数化模板为基础压缩重复性设计工作量；建立常用设备族库与节点详图库，新" + ("场站" if w.charging else "单元") + "设计以“套图＋调整”为主，把设计力量集中用于场地条件特殊、供电方案复杂的部位。"),
        _p("（3）接口保证。设计出图计划与采购计划、施工计划实行“三计合一”周协调，设备参数确认单未闭环不出施工图，避免因设备变更导致返工" + ("；与供电公司建立固定沟通渠道，答复单取得时间纳入周报考核" if w.has("power_supply") else "") + "。"),
        _p("（4）应急保证。设计进度滞后超过3天时，启动应急增援，从公司设计中心调配人员；对确因外部原因无法按期取得审查意见的，先出具“白图”供施工准备与材料报审使用，正式图纸到位后替换，确保现场不停工。"),
    ]
    return Draft(children=[("设计进度计划", schedule), ("进度保证措施", measures)])


def write_cost_limit(w: Writer) -> Draft:
    f = w.facts
    cap = f.get("engineering_price_cap")
    lead = f"限额设计是本项目投资控制的核心手段。我方以工程费最高投标限价{cap}为总限额" if cap else "限额设计是本项目投资控制的核心手段。我方以合同价为总限额"
    lead += "，按专业与" + ("场站" if w.charging else "单位工程") + "分解设计限额，形成“总限额—分部限额—专业限额—" + ("场站" if w.charging else "单位工程") + "限额”四级指标体系，作为方案选择与图纸审核的硬约束。"
    rows = [
        ["一级", "工程费总额", "不突破投标报价，结算不超合同价", "总限额下达，月度对比分析"],
        ["二级", "分部工程", "高压、低压、场站标准化等分部各自限额" if w.charging else "各分部工程各自限额", "分部限额随设计任务书下达，超限需专项论证"],
        ["三级", "专业", "电气、土建、总图、给排水专业造价指标", "按单位工程量指标（元／座、元／米、元／平方米）控制"],
        ["四级", "场站" if w.charging else "单位工程", "单站造价指标与单枪造价指标" if w.charging else "单位工程造价指标", "同规模横向对比，偏差超过5%即复核"],
    ]
    compare = ("箱变容量与台数比选（按需量与容量电费综合比较）、充电功率配置比选（超充、快充与慢充比例及终端电流等级配置）、电缆截面比选（按经济电流密度与年运行费综合优化）、排管路径比选、路面恢复结构比选"
               if w.charging else "结构选型比选、主要设备选型比选、管线路径比选")
    equipment = "箱变、充电机、成套配电箱" if w.charging else "主要设备"
    return Draft(blocks=[
        _p(lead),
        table(["层级", "指标对象", "控制内容", "控制方法"], rows, caption="限额设计指标分解", widths=[1.6, 2.8, 6.0, 6.0]),
        _p("限额设计的具体措施包括："),
        _p(f"（1）方案比选。对影响造价的关键环节开展技术经济比选：{compare}。每个比选形成书面结论，作为设计依据。"),
        _p("（2）标准化设计。统一" + ("箱变基础、终端基础、排管断面、接地网、配电箱系统方案" if w.charging else "常用节点与构件做法") + "，减少非标设计与定制加工；标准化构配件采用工厂预制，降低现场湿作业与材料损耗。"),
        _p(f"（3）价值工程。对占比最大的设备费（{equipment}）开展功能—成本分析，在满足招标文件参数与品牌要求的前提下，选择全生命周期成本最优的配置，避免过度设计，也避免以降低可靠性为代价的片面节约。"),
        _p("（4）动态监控。概预算人员随图计算，每完成一版图纸即出具造价对比表，与限额指标逐项对比；偏差超过3%时向设计负责人发出预警，超过5%时启动方案复核，确保限额在设计过程中受控，而不是在结算时追悔。"),
    ])


def write_design_intent(w: Writer) -> Draft:
    if w.charging:
        intent = [
            _p("本项目设计意图可概括为“分布式布局、快慢结合、开放运营、统一接入、适度超前”。分布式布局要求设计以既有停车资源为载体，就近布点、小容量多点接入，减少对电网的集中冲击；快慢结合要求在同一场站内合理配置超充、快充与慢充，兼顾周转效率与夜间谷电利用；开放运营要求场站具备独立计量、独立计费、无人值守能力；统一接入要求全部设备的数据、状态、告警上传至招标人指定运营平台，设计阶段即确定通信方式、协议与点位；适度超前要求土建、排管、箱变容量预留扩展余量，避免二次开挖与重复投资。"),
            _p("基于此，我方设计不以“满足规范最低要求”为目标，而以“满足运营、便于维护、支持扩展、控制全寿命成本”为准则，把运营需求前置到设计输入中。"),
        ]
        points = [("供电接入与容量配置", "各场站可用容量不一致、供电电源点距离远、部分场站需利用市政排管", "逐站开展负荷计算与需量分析，按充电设施总装机容量与同时系数配置变压器容量；对需穿越市政道路的场站，同步疏通原有排管，减少开挖与审批；箱变选型优先选用占地小、维护方便的预装式箱变")]
        if w.facts.get("terminal_currents"):
            top = max(w.facts["terminal_currents"])
            points.append(("大电流直流终端的电气与土建配合", f"{top}A终端对电缆截面、压接工艺、散热与防护要求高，终端基础尺寸、电缆弯曲半径与枪线收纳空间需要协调",
                           "按终端厂家安装手册确定基础尺寸并预留调整余量；电缆通道按最大截面设计，弯曲半径满足规范要求；枪线设置收纳支架，避免拖地磨损；终端基础高出地面并做排水坡度"))
        points.append(("既有场地改造的管线避让", "地下管线走向不明", "物探＋人工开挖验证；设计图纸标注管线保护范围与“禁挖区”；对无法避让的部位采用局部改迁，改迁方案报产权单位确认"))
        if w.has("drainage"):
            points.append(("排水与场地竖向", "既有场地排水不畅、新增设备基础抬高后形成积水点", "场站按不小于0.5%的坡度组织竖向，设备基础周边设排水沟并接入市政雨水系统；电缆沟做防水与排水措施；低洼场站增设排水泵坑"))
        points.append(("防雷接地与电气安全", "分散场站接地电阻难以达标", "按设计要求设置联合接地网，对土壤电阻率高的场站采用换土、添加降阻剂或增加接地极数量的措施；所有设备金属外壳、箱变外壳可靠接地，等电位连接完备"))
        if w.has("platform"):
            points.append(("平台接入与信息安全", "多品牌设备协议差异与数据稳定性", "设计阶段明确通信接口、协议与点位表；要求设备厂家提供协议文档并完成联调；网络采用专用通道，配置访问控制，确保数据上传稳定、不中断、不丢失"))
        points.append(("消防与安全防范", "充电区域火灾风险与人员安全", "按规范配置灭火器材与消防砂箱，充电区与建筑物保持安全距离；设置急停按钮、警示标识与防撞设施" + ("；视频监控覆盖充电区、出入口与设备区" if w.has("camera") else "")))
    else:
        intent = [_p("本项目设计意图是在满足使用功能与规范要求的前提下，实现安全可靠、经济合理、便于施工与运维。我方以招标人需求为设计输入核心，兼顾近期建设与远期发展。")]
        points = [("功能与规范的统一", "使用功能多样、规范要求严格", "逐项对照规范与招标人需求，形成设计输入清单并经确认"),
                  ("投资控制", "限额指标严格", "推行限额设计与价值工程，关键方案技术经济比选"),
                  ("施工可实施性", "交叉作业多、场地受限", "设计阶段开展施工可行性审查，节点做法标准化")]
    difficulties = [_p(f"（{i}）{title}。难点在于{hard}。对策：{answer}。") for i, (title, hard, answer) in enumerate(points, start=1)]
    return Draft(children=[("对设计意图的理解", intent), ("设计重点与难点及对策", difficulties)])


def write_new_tech(w: Writer) -> Draft:
    items = ["BIM正向设计与三维管线综合。对管线密集、空间受限的部位建立三维模型，开展碰撞检查与净高分析，把管线冲突解决在图纸阶段，减少现场返工；模型同步输出设备清单与工程量，提高清单准确性。",
             "标准图集与参数化设计。建立覆盖常用基础、管线、接地与配电系统的标准节点库，通过参数化调整适配不同" + ("场站" if w.charging else "部位") + "，既保证设计质量的一致性，又压缩设计周期。"]
    if w.has("transformer"):
        items.append("预装式箱式变电站。采用工厂预制、整体运输、现场吊装的预装式箱变，减少土建工作量，缩短现场安装周期，防护等级与散热性能优于传统土建配电房。")
    items.append("工厂预制基础与装配式构件。设备基础、电缆井、排水沟优先采用预制构件，现场拼装，减少湿作业与养护时间，降低扬尘与建筑垃圾产生量。")
    items.append("高性能电缆与连接技术。中压电缆选用阻燃型交联聚乙烯绝缘电缆，低压大电流回路选用阻燃铜芯电缆；电缆头采用冷缩工艺，压接采用配套模具与力矩控制，提高接头可靠性。")
    if w.charging:
        items.append("智能运维与边缘计算。充电设施配置通信网关，实现本地数据汇聚、故障预判与断网续传" + ("；箱变配置智能采集终端，实现温度、电参量在线监测，为状态检修提供数据支撑" if w.has("transformer") else "") + "。")
        if not w.has("photovoltaic"):
            items.append("光储充一体化预留。对具备条件的场站，在车棚结构与电缆通道设计中预留光伏、储能接入空间与并网点，为后续“光储充”一体化改造留出条件，避免二次改造。")
    items.append("绿色低碳材料。场站铺装优先采用透水混凝土或透水砖，照明采用LED光源并配置光控与时控，减少运行能耗；标识系统采用反光膜材料，提高夜间可视性。")
    return Draft(blocks=[_p(f"（{i}）{text}") for i, text in enumerate(items, start=1)])


# ================= 八、承包人实施计划 =================

def write_construction(w: Writer) -> Draft:
    draft = Draft()
    phases = w.construction_phases()
    days = split_days(w.days, [phase[2] for phase in phases]) if w.days else [0] * len(phases)
    overall = [_p("本项目按“分批进场、多站并行、单站闭环”的原则组织施工。以单个场站为最小管理单元，组建土建、电气、设备安装三个专业作业队跨场站流水作业：土建队负责场地整理、土方、基础、排管、路面恢复；电气队负责电缆敷设、电缆头制作、接地、配电箱安装与接线；设备安装队负责" + _join(["箱变" if w.has("transformer") else "", "充电机、充电终端", "道闸、地锁、监控与照明" if w.has("barrier", "parking_lock", "camera", "lighting") else ""]) + "安装及调试。专业队随作业面推进而流动，避免各场站同时开工造成资源摊薄。")
               if w.charging else _p("本项目按“统一规划、分区施工、流水作业”的原则组织施工，组建土建、安装、调试专业作业队，随作业面推进流动作业。")]
    overall.append(_p(f"{w.short_unit}施工按“先地下后地上、先土建后设备、先高压后低压、先安装后调试”的顺序组织，划分为{len(phases)}个阶段，相邻阶段允许合理搭接，但不允许颠倒工序；各阶段工期已计入工序搭接、天气影响与外部协调等待的机动时间。" + ("多场站并行时，各场站开工时间错开5～7天，形成“一批设计、一批采购、一批施工、一批调试”的滚动节奏。" if w.charging else "")))
    if w.days:
        overall.append(table(["阶段", "主要工作内容", "工期（日）", "关键控制点"], [[name, work, str(day), point] for (name, work, _w, point), day in zip(phases, days)],
                             caption=f"{w.short_unit}施工阶段划分与工期安排（{w.days}日历天）", widths=[2.4, 7.0, 1.6, 5.4]))
    draft.children.append(("施工总体部署", overall))
    draft.children.append(("施工准备", [
        _p("施工准备以“条件确认”为核心，做到不具备条件不开工。技术准备包括图纸会审、设计交底、施工组织设计与专项方案报审、测量控制点交接与复测、检验批划分与见证取样计划编制；现场准备包括场地移交、围挡与警示、临时道路、临水临电（三级配电、两级保护）、材料堆场与加工区布置、消防器材配置；资源准备包括劳动力进场与三级教育、机械设备进场检验、主要材料设备订货与到货计划。"),
        _p("开工前完成" + ("三" if w.has("platform") else "两") + "项确认：一是场地条件确认，核对场地范围、标高、既有管线、车辆通行与作业时段限制；二是供电条件确认，核对供电答复单、计量方案、送电时间与供电公司要求" + ("；三是接口确认，与招标人、运营平台服务商确认设备接入的通信方式、协议、点位表与调试配合方式" if w.has("platform") else "") + "。确认结果形成书面记录，作为开工报告的附件。"),
    ]))
    if w.has("site_work"):
        draft.children.append(("场地整理与拆除工程", [
            _p("场地整理按“先清障、后测量、再施工”的顺序进行。清除场地内杂物、既有标线与旧基础，对需保留的设施设置围护与标识；采用切割机对沥青或混凝土路面按设计边线切缝，切缝深度不小于面层厚度，避免拆除时产生不规则裂缝。拆除作业采用小型破碎锤配合人工，靠近管线与建筑物处一律人工凿除。"),
            _p("拆除产生的建筑垃圾按类分堆，渣土与混凝土块分别外运至指定消纳场，运输车辆密闭并办理准运手续；拆除作业全过程洒水降尘，遇四级以上大风或重污染天气预警时停止土方与拆除作业。拆除完成后对基层进行检验，软弱部位挖除换填并压实，压实度满足设计要求。"),
        ]))
    foundation = [_p("土方开挖前完成管线探测与人工探沟验证，开挖采用小型挖掘机配合人工，槽底预留200～300mm人工清底，严禁超挖；如遇地下水或积水，设置集水坑与排水泵及时排干。回填采用分层夯实，每层厚度不大于300mm，压实系数满足设计要求，回填土不得含建筑垃圾与有机杂质。")]
    if w.has("transformer"):
        foundation.append(_p("箱变基础按设计尺寸施工，钢筋规格、间距、保护层厚度经隐蔽验收合格后浇筑；模板拼缝严密、支撑牢固，浇筑时分层振捣、一次成型，避免冷缝；基础顶面标高误差控制在±5mm以内，预埋件位置误差不大于10mm。基础施工同步预埋接地扁钢、电缆保护管与排水管，预埋管口做临时封堵，防止杂物进入。"))
    if w.has("dc_charger", "ac_charger"):
        foundation.append(_p("充电终端基础与充电机基础按设备厂家安装尺寸施工，基础顶面高出完成面不小于150mm，四周做排水坡度；预埋地脚螺栓采用定位模板固定，浇筑过程随时复核，混凝土养护不少于7天，强度达到设计要求后方可安装设备。"))
    draft.children.append(("土方与基础工程", foundation))
    if w.has("duct"):
        duct = [_p("电缆排管采用设计指定的管材，管枕间距均匀、接口错缝，管道坡度不小于0.5%并坡向集水井；排管敷设完成后进行通球试验，通球不畅的管段立即处理。管道包封采用设计标号混凝土，包封厚度与配筋按图施工，包封前对管群进行隐蔽验收并留存影像。")]
        if w.has("power_supply"):
            duct.append(_p("外网市政电力排管按招标文件要求完成通管：对既有排管的疏通采用高压水射流配合人工清掏，疏通后通球检验并出具记录；穿越市政道路或无法开挖的部位，经产权单位同意后采用非开挖方式施工，确保满足供电公司各项技术规范与穿缆要求，移交时同步具备正式使用条件。"))
        draft.children.append(("电缆排管与电缆通道", duct))
    draft.children.append(("电缆敷设与电缆头制作", [
        _p("电缆敷设前核对型号、规格、长度与绝缘电阻，10kV电缆敷设前按规范完成绝缘电阻测试与耐压试验；敷设采用滚轮与牵引机具，牵引力与侧压力控制在允许范围内，弯曲半径满足规范要求。电缆在沟内排列整齐、留有裕度，挂牌标识起点、终点、型号与长度。"),
        _p("电缆头制作由持证人员在湿度受控的环境下进行，严格执行厂家工艺：剥切尺寸准确、半导电层断口平整、应力锥安装到位、压接采用配套模具并按力矩紧固，冷缩件收缩均匀无褶皱；制作完成后做绝缘电阻与耐压试验，并逐只填写制作记录。雨天与相对湿度大于80%时停止制作。"),
    ]))
    draft.children.append(("接地装置与防雷", [
        _p("接地装置按设计敷设，接地极垂直打入地下，顶部埋深满足设计要求；接地母线采用镀锌扁钢，搭接长度不小于宽度的2倍，三面施焊，焊缝饱满、无虚焊，焊后清除焊渣并做防腐处理。"),
        _p("接地网与" + _join(["箱变" if w.has("transformer") else "", "充电机、终端" if w.has("dc_charger") else "", "配电箱", "监控立杆" if w.has("camera") else ""]) + "的金属外壳可靠连接，所有连接点做防腐与标识；接地电阻实测值满足设计要求，达不到要求时增设接地极、换土或使用降阻剂，直至合格。接地装置隐蔽前经监理验收并留存影像，测试记录纳入竣工资料。"),
    ]))
    if w.charging:
        draft.children.append(("路面恢复与场站标准化", [
            _p("路面恢复按原结构层次还原：基层采用水泥稳定碎石或混凝土，面层按原材质恢复（沥青或混凝土），接缝处做台阶搭接，压实度与平整度满足规范要求。混凝土面层按设计设置缩缝与胀缝，养护不少于7天，养护期内禁止车辆通行。"),
            _p("场站标准化包括车位地坪漆、标线、车挡、隔离围栏、标识牌等设施。地坪漆施工前对基层打磨、除尘、修补，底涂、中涂、面涂分道施工；标线按交通组织图施工，直线段拉线控制、曲线段放样控制。车挡、围栏、立杆采用膨胀螺栓固定，螺栓外露部分做防锈处理。"),
        ]))
    if w.has("drainage"):
        draft.children.append(("排水工程", [
            _p("场站排水按“有组织、就近排”的原则实施。沿场地周边与设备基础设置排水沟，沟底坡度不小于0.5%；低洼处设雨水口与沉砂井，接入市政雨水系统前设置格栅拦截杂物。电缆沟、设备基础内积水通过排水管接入集水井，必要时设置小型排水泵并配置液位控制。"),
            _p("排水系统施工完成后进行通水试验，检查排水通畅、无倒灌、无渗漏；雨季施工期间对开挖基坑与电缆沟设置临时排水与覆盖，防止雨水浸泡基础与管沟。"),
        ]))
    return draft


def _brands(w: Writer, *keywords: str) -> str:
    brands = [item["brands"] for item in w.equipment(*keywords) if item.get("brands")]
    return brands[0] if brands else ""


def write_installation(w: Writer) -> Draft:
    draft = Draft()
    f = w.facts
    if w.has("transformer"):
        brands = _join([f"{item['name']}参考品牌{item['brands']}" for item in w.equipment("变压器", "开关", "仪表", "SVG") if item.get("brands")], "；")
        draft.children.append(("箱式变电站安装", [
            _p("箱变到场后进行开箱验收，核对变压器型号与容量、开关品牌、高低压柜配置、计量与保护装置、出厂试验报告与合格证" + (f"（招标文件{brands}）" if brands else "") + "，检查外观无变形、绝缘件无破损、附件齐全。基础验收合格（标高、轴线、预埋件、接地）后，采用汽车吊整体吊装就位，吊点按厂家标识设置，起吊过程设溜绳控制方向，就位后调整水平度，并与基础预埋件可靠固定。"),
            _p("箱变就位后进行高压电缆引入、低压电缆引出、接地连接与二次接线。电缆引入做防水弯，进线孔做防火封堵；二次接线按图施工，线号清晰、绑扎整齐、端子紧固；变压器做绝缘电阻、直流电阻与变比试验，高压开关做机械特性与保护整定，整定值报供电公司确认。箱变周围设置防护栏与警示标识，基础四周做散水与排水，防止积水浸泡。"),
        ]))
    if w.has("dc_charger"):
        powers = f.get("dc_powers") or []
        power_text = "、".join(f"{p}kW" for p in powers)
        intro = "本项目直流充电设施为分体式结构，由充电机（功率柜）与直流充电终端组成" if w.equipment("分体式") else "本项目直流充电设施"
        intro += f"，装机功率包括{power_text}" if power_text else ""
        intro += f"，终端电流等级包括{w.currents_text}" if f.get("terminal_currents") else ""
        intro += w.gun_clause + "。"
        brands = _brands(w, "直流")
        draft.children.append(("直流充电机与充电终端安装", [
            _p(intro + ("参考品牌为" + brands + "，" if brands else "") + "安装前核对设备铭牌参数与设计一致，检查柜体外观、门锁、散热风道、防尘网、绝缘件与元器件完好。"),
            _p("充电机安装于混凝土基础上，采用螺栓固定，柜体垂直度满足规范要求，柜前操作距离与柜后检修距离满足厂家与规范要求；柜体底部做防火封堵，进线电缆做防水弯并固定。"),
            _p("直流终端落地安装，终端与基础采用螺栓连接并做防水密封；终端间距满足车辆开门与人员操作要求，配置枪线支架，避免拖地碾压；终端安装位置避开车辆行驶轨迹，前方设置车挡与防撞设施。"),
            _p("功率分配与通信线缆按厂家要求敷设，动力电缆与控制电缆分槽敷设，交叉处做垂直交叉；终端与充电机之间的通信线采用屏蔽线，屏蔽层单端接地；所有接线完成后逐台核对相序、极性、地址码与终端编号，形成“一机一档”的接线记录。"),
        ]))
    if w.has("ac_charger"):
        draft.children.append(("交流充电桩安装", [
            _p(f"{w.ac_desc}按落地式或壁挂式安装。落地式安装在混凝土基础上，基础高出地面；壁挂式安装于承重墙体或立柱，安装高度便于操作，背部支架与墙体采用膨胀螺栓固定，螺栓数量与规格按厂家要求。"),
            _p("交流桩进线采用单相220V，配置独立回路与漏电保护，电缆穿管敷设，管口做护口；桩体接地可靠；安装完成后测试漏电保护动作电流与动作时间、充电启动与停止、扫码启动功能。"),
        ]))
    draft.children.append(("成套配电箱安装与接线", [
        _p("成套配电箱按设计系统图核对回路数、开关品牌与整定值、计量表计与互感器变比、浪涌保护器参数；落地柜安装于基础槽钢上，与基础可靠固定并做防腐；挂墙箱安装高度一致，箱面平整，进线由箱底引入并做防水弯。"),
        _p("接线执行“一机一图一记录”：按图核对回路，导线压接使用配套端子，螺栓紧固后做防松标记；相色区分清晰，N排与PE排分别设置，PE排与箱体可靠连接；箱内标识齐全，回路名称与图纸一致。"),
    ]))
    smart = []
    for item in w.equipment("车牌识别一体机"):
        smart.append(f"车牌识别一体机（{item['spec']}）安装于车道侧，镜头高度与角度保证识别区域覆盖车道，补光灯角度避免直射驾驶员")
    for item in w.equipment("挡车器"):
        smart.append(f"自动挡车器（{item['spec']}）基础预埋管线到位，地感线圈切槽深度与宽度按规范施工，雷达防砸装置调试到位")
    for item in w.equipment("管理软件"):
        smart.append(f"车牌识别管理软件（{item['spec']}）部署于管理工控机并完成与招标人指定平台对接")
    for item in w.equipment("车位锁"):
        smart.append(f"{item['name']}安装于车位中部，{item['spec'] or '与招标人指定平台对接'}")
    for item in w.equipment("物联网卡"):
        smart.append(f"物联网卡按招标文件要求“{item['spec']}”")
    for item in w.equipment("转换电源", "稳压器"):
        smart.append(f"{item['name']}（{item['spec'].split('（')[0]}）安装于防水箱内，箱体固定牢靠、进出线做防水接头")
    for item in w.equipment("摄像机"):
        smart.append(f"{item['name']}（{item['spec'].split('（')[0]}）安装于立杆，覆盖充电区、出入口与设备区，镜头避免逆光")
    for item in w.equipment("交换机"):
        smart.append(f"{item['name']}安装于弱电箱内，做防雷与接地，IP地址统一规划并与平台核对")
    for item in w.equipment("庭院灯"):
        smart.append(f"{item['name']}（{item['spec'].split('(')[0].split('（')[0]}）灯杆基础与预埋螺栓按图施工，灯杆垂直、灯具朝向一致，电缆穿管埋地，回路设置漏电保护与光控时控")
    if smart:
        draft.children.append(("道闸、车位锁、监控与照明安装" if w.has("barrier", "parking_lock") else "弱电与照明安装",
                               [_p(f"（{i}）{text}。") for i, text in enumerate(smart, start=1)]
                               + [_p("安装完成后逐台测试升降、识别、通信、断电应急等功能，测试记录纳入竣工资料。")]))
    if w.charging:
        draft.children.append(("消防器材与安全设施", [_p("按设计与规范配置灭火器、消防砂箱、消防锹与警示标识；充电区设置急停按钮与操作说明牌；设备区设置“高压危险”“禁止攀登”等警示牌，箱变防护栏设置门锁并悬挂“止步，高压危险”标示牌。")]))
    debug = [_p("调试按“单体—系统—并网—平台”四个层次推进。单体调试包括设备绝缘电阻测试、接地连续性测试、电源模块自检、风扇与加热器动作、门禁与急停功能；系统调试包括高低压回路核对、保护定值校验、计量回路检查" + ("、充电控制导引与通信协议（GB/T 27930）一致性测试" if w.charging else "") + "；并网调试包括相序核对、空载与负载试验、送电与停电操作演练。")]
    if w.has("platform"):
        debug.append(_p("平台接入调试包括通信链路建立、设备注册、点位映射、数据上传与指令下发测试；逐台核对设备编号与平台档案一致，测试启动、停止、故障上报、离线续传等功能，确保设备数据传输稳定、功能满足招标人运营管理要求。调试中发现的问题形成问题清单，逐项闭环并复测，闭环率100%后方可申请验收。"))
    rows = [["1", "10kV电缆绝缘电阻与耐压试验", "GB 50150", "绝缘电阻符合规范，耐压无击穿"]]
    if w.has("transformer"):
        rows.append(["2", "变压器绝缘电阻、直流电阻、变比", "GB 50150", "与出厂值比较无明显差异"])
    rows += [[str(len(rows) + 1), "接地电阻测试", "GB 50169、设计文件", "满足设计要求"],
             [str(len(rows) + 2), "低压回路绝缘与相序核对", "GB 50303", "绝缘合格、相序正确"]]
    for s in w.facts.get("standards", []):
        if "检验" in s["name"] or "验收" in s["name"]:
            rows.append([str(len(rows) + 1), s["name"].removeprefix("电动汽车"), s["code"], "各项检验项目合格"])
    if w.charging:
        rows.append([str(len(rows) + 1), "充电通信协议一致性", "GB/T 27930", "通信正常、无异常中断"])
    if w.has("platform"):
        rows.append([str(len(rows) + 1), "平台接入与数据上传", "招标人运营平台要求", "设备全部在线、数据传输稳定"])
    debug.append(table(["序号", "试验／调试项目", "执行标准或依据", "合格判据"], rows, caption="调试与试验项目清单", widths=[1.2, 6.0, 4.4, 4.8]))
    draft.children.append(("调试、并网与平台接入" if w.has("platform") else "调试与并网", debug))
    return draft


def write_quality_system(w: Writer) -> Draft:
    f = w.facts
    target = f.get("construction_quality") or "合格"
    controls = _join(["箱变基础混凝土强度与轴线标高" if w.has("transformer") else "基础混凝土强度与轴线标高", "电缆绝缘与耐压", "接地电阻", "设备就位精度与接线正确性",
                      "充电设施通信与计量准确性" if w.charging else "", "平台数据完整性" if w.has("platform") else "", "观感质量与标识规范性"])
    return Draft(blocks=[
        _p(f"我方按ISO 9001标准建立项目质量管理体系，质量目标为{target}。编制《项目质量计划》与《检验试验计划》，明确质量目标、组织职责、控制程序、检验批划分与验收标准，经招标人及监理审批后实施。质量管理实行项目经理第一责任人、技术负责人技术总负责、专职质检员过程把关的三级责任体系。"),
        _p(f"（1）主控项目与目标。本项目质量主控项目包括：{controls}。主控项目合格率必须达到100%，一般项目合格率不低于90%，并有完整的操作依据与质量检查记录。"),
        _p("（2）过程控制措施。执行“三检制”与工序交接检查；设置质量控制点（W点、H点、S点），对基础浇筑、接地埋设、电缆头制作、设备就位、送电调试设置停止点，未经验收不得进入下道工序；实行样板引路，先做样板，经招标人与监理确认后全面展开。"),
        _p("（3）材料设备控制。按招标文件要求，主要建设材料采购前向发包人提供供应商资料、报价与检测报告，设备采购前提供参数与指标，经发包人认可后确定；材料设备进场查验合格证、检测报告与说明书，按规定见证取样送检，不合格材料一律退场；设备开箱验收形成记录，附件、备件、专用工具登记入库。"),
        _p("（4）质量记录。检验批、隐蔽验收、试验报告、调试记录、影像资料随施工进度同步形成，做到“资料与实体同步、签字与工序同步”，杜绝事后补记；竣工时按招标人要求的份数与格式提交完整资料。"),
        _p("（5）质量通病防治。基础表面开裂（控制水灰比、加强养护、设置构造钢筋）；电缆沟积水（控制坡度、设置集水井、做好防水）；接地电阻超标（增加接地极、换土、使用降阻剂）；设备柜体渗水（进线做防水弯、孔洞封堵）" + ("；标线脱落（基层处理到位、涂料厚度受控）" if w.charging else "") + "。"),
    ])


def write_safety(w: Writer) -> Draft:
    f = w.facts
    target = (f.get("safety") or "不发生安全生产事故").rstrip("。")
    rows = [
        ["1", "临时用电", "触电、电气火灾", "三级配电两级保护、一机一闸一漏一箱、定期检测漏电保护、电缆架空或穿管埋地"],
        ["2", "基坑与沟槽", "坍塌、掩埋", "放坡或支护、设置临边防护与警示、坑边1m内不堆土、雨后检查"],
        ["3", "起重吊装", "物体打击、设备损坏", "专项方案、持证司索、设警戒区、吊装前检查吊具、严禁超载"],
    ]
    if w.has("transformer", "power_supply"):
        rows.append(["4", "高压送电", "触电伤亡", "送电方案审批、验电接地、设专人监护、操作票制度、禁止单人作业"])
    rows.append([str(len(rows) + 1), "高处作业", "坠落", "安全带高挂低用、搭设操作平台、工具袋、恶劣天气停止作业"])
    if w.charging:
        rows.append([str(len(rows) + 1), "交通与运输", "车辆伤害", "施工区与行车区隔离、设置导改与警示、夜间反光标识、专人指挥"])
    civilized = f"按{f['safety_standardization'].removesuffix('工地')}管理要求布置施工作业区与生活区，创建{f['safety_standardization']}。" if f.get("safety_standardization") else ""
    return Draft(blocks=[
        _p(f"安全目标为“{target}”。我方建立以项目经理为第一责任人的安全生产责任制，配置专职安全生产管理人员，班组设兼职安全员，逐级签订安全生产责任书，实行安全生产“一岗双责”。{civilized}"),
        _p("（1）教育培训。进场人员一律经过三级安全教育并考核合格，特种作业人员（电工、焊工、起重工、登高作业人员）持证上岗；每日班前会进行风险提示，每周开展安全活动，每月组织应急演练或专项培训。"),
        _p("（2）危险源识别与控制。逐项识别临时用电、基坑与沟槽、起重吊装、高处作业" + ("、高压送电" if w.has("transformer") else "") + ("、交通运输" if w.charging else "") + "等危险源，制定控制措施并纳入安全技术交底，重大危险源设置公示牌与警示标志。"),
        table(["序号", "危险源", "可能后果", "控制措施"], rows, caption="主要危险源与控制措施", widths=[1.2, 2.6, 3.0, 9.6]),
        _p("（3）危大工程管理。对危险性较大的分部分项工程编制专项施工方案，经技术负责人审批、监理审查后实施，超过一定规模的组织专家论证；施工前进行专项交底，施工中安排专人旁站。"),
        _p("（4）文明施工。施工现场封闭管理，材料分类堆放、标识清楚；" + ("重要地段、路口派专人疏导交通，保证交通通畅；" if w.charging else "") + "已完工程做好成品保护，场区外公共区域道路的环境卫生与文明形象由我方负责维护。"),
        _p("（5）应急管理。编制《项目安全生产应急预案》，针对触电、坍塌、火灾、中暑、机械伤害等场景明确处置流程、应急物资与救援联系方式；配备急救箱、担架、灭火器、应急照明与备用电源；定期组织应急演练并评估改进。"),
    ])


def write_environment(w: Writer) -> Draft:
    f = w.facts
    regulations = "、".join(f"《{name}》" for name in f.get("dust_regulations", []))
    standard = f.get("dust_standard") or []
    lead = "我方严格落实" + (regulations + "等" if regulations else "") + f"{w.city}有关扬尘污染防治的规定"
    if standard:
        lead += "，满足施工工地扬尘治理“8个100%”要求：" + "、".join(standard) + "。"
    else:
        lead += "，落实各项扬尘治理措施。"
    return Draft(blocks=[
        _p(lead),
        _p("（1）组织体系。项目经理为环境保护第一责任人，质量安全部设专职环保员，各班组设兼职环保员；开工前识别扬尘、噪声、污水、固废、光污染等环境因素，制定控制目标与措施，按“一站一策”编制环保专项方案。" if w.charging else "（1）组织体系。项目经理为环境保护第一责任人，设专职环保员；开工前识别环境因素并制定控制措施，编制环保专项方案。"),
        _p("（2）围挡与硬化。施工区域设置连续封闭围挡，围挡坚固、整洁、无破损；场内主要道路与材料堆场硬化，出入口设置车辆冲洗设施与沉淀池，冲洗废水经沉淀后回用或排入市政污水管网。"),
        _p("（3）湿法作业。土方开挖、装运、切割、破除等易产生扬尘的作业全程洒水降尘，配置雾炮机或喷淋设施；重污染天气预警期间按政府要求停止土方作业。"),
        _p("（4）物料与渣土管理。水泥、砂石等易扬尘材料入库或严密覆盖，渣土与建筑垃圾密闭运输，车辆出场前冲洗轮胎与车身，严禁沿途遗撒；非道路移动机械使用合格油品并达标排放。"),
        _p("（5）噪声与污水。合理安排作业时间，夜间不进行产生噪声的施工作业，确需夜间施工的办理审批并公告周边；选用低噪声设备，切割作业设置隔声措施；施工废水经沉淀处理后排放，严禁将泥浆水直接排入雨水管道。"),
        _p("（6）在线监测与考核。按属地要求安装在线视频监控与扬尘监测设备，数据超标即联动喷淋；项目部每周开展环保自查，发现问题立即整改，对因环保问题被通报的班组实行经济处罚与停工整改。"),
    ])


def write_progress(w: Writer) -> Draft:
    phases = w.construction_phases()
    blocks = [_p(f"本项目以{w.unit}{w.days}日历天为总控目标" if w.days else "本项目以合同工期为总控目标")]
    blocks[0] = _p(blocks[0]["text"] + "，编制项目总进度计划、月度计划与周作业计划三级计划体系。总进度计划按批次与" + ("场站" if w.charging else "单位工程") + "分解，明确设计出图、设备到货、土建完成、设备安装、" + ("送电并网、" if w.charging else "") + "验收移交等里程碑节点；月度计划将里程碑分解为可考核的工序节点；周计划落实到作业面、班组与人数。")
    if w.days:
        days = split_days(w.days, [phase[2] for phase in phases])
        cumulative, rows, total = [], [], 0
        for (name, _work, _weight, _point), day in zip(phases, days):
            total += day
            cumulative.append((name, total))
        names = dict(cumulative)
        marks = [("开工（场地移交、临设完成）", names.get("施工准备", 0), "开工报告批准")]
        if w.charging:
            marks += [("基础与排管完成", names["土建与基础"], "隐蔽验收合格"),
                      ("电缆敷设与接地完成", names["电缆通道与敷设"], "绝缘与接地电阻合格"),
                      ("设备安装完成", names["设备安装"], "设备就位、接线完成、单机通电"),
                      ("送电与并网" + ("、平台接入" if w.has("platform") else "") + "完成", names["调试并网"], "系统联调合格" + ("、数据上传稳定" if w.has("platform") else ""))]
        else:
            marks += [(name + "完成", day, "验收合格") for name, day in cumulative[1:-1]]
        marks.append(("竣工验收与移交", w.days, "验收合格、移交确认"))
        rows = [[str(i), name, f"第{day}日", sign] for i, (name, day, sign) in enumerate(marks, start=1)]
        blocks.append(table(["序号", "里程碑节点", "计划时间", "完成标志"], rows, caption=f"{w.short_unit}里程碑节点计划", widths=[1.2, 6.4, 3.0, 5.8]))
        # 横道图：按阶段起止日绘制
        bar_rows, start = [], 1
        for (name, work, _weight, _point), day in zip(phases, days):
            bar_rows.append([name, f"第{start}日", f"第{start + day - 1}日", str(day), work])
            start += day
        blocks.append(table(["工作阶段", "开始", "完成", "持续（日）", "主要工作"], bar_rows, caption=f"{w.short_unit}施工进度计划表", widths=[2.6, 2.0, 2.0, 1.8, 8.0]))
    blocks.append(_p("进度保证措施包括：一是资源保证，劳动力按峰值需求配置并预留20%机动，机械设备按并行作业面数量配置；二是技术保证，标准图集与预制构件缩短现场作业时间；三是协调保证，"
                     + ("供电报装、市政排管通管等外部事项设专人跟办，每周与供电公司、产权单位对接；" if w.has("power_supply") else "外部协调事项设专人跟办；")
                     + "四是考核保证，将节点完成情况与班组结算挂钩，实行节点奖罚；五是应急保证，遇连续降雨、重污染天气管控等不可控因素，及时调整工序，把室内作业、设备组装、资料整理等不受天气影响的工作前置。"))
    return Draft(blocks=blocks)


def write_risk(w: Writer) -> Draft:
    rows = []
    if w.has("power_supply"):
        rows.append(["外部协调风险", "供电答复延迟、市政排管不通、送电时间不确定", "设计启动即报装，专人驻点跟办；排管提前疏通并预留备用路径；与供电公司建立周沟通机制"])
    rows += [
        ["场地条件风险", "场地移交延迟、地下管线不明、权属争议", "提前对接产权单位，签订移交确认单；物探＋人工验证；争议部位暂缓施工并书面报告"],
        ["工期风险", "多作业面并行资源冲突、天气与管控停工", "资源动态调配、工序合理搭接、预留机动工期；停工期间转向室内作业"],
        ["设备供货风险", "长周期设备延期、参数变更", "提前锁定供货周期、设置违约条款；关键设备备选供应商；到货计划双周滚动"],
        ["质量风险", "电缆头制作缺陷、接地不达标" + ("、平台接入不畅" if w.has("platform") else ""), "持证人员作业、工艺样板引路、逐台测试；接地预留补强措施" + ("；平台联调提前介入" if w.has("platform") else "")],
        ["安全风险", "触电、坍塌、交通伤害", "专项方案＋旁站＋教育＋应急演练；高风险作业实行作业许可"],
        ["造价风险", "材料涨价、变更增加、结算争议", "价格锁定或调价条款；变更事前审批；过程结算、月度对账"],
    ]
    if w.facts.get("dust_standard"):
        rows.append(["环保风险", "扬尘超标被通报、夜间施工投诉", "8个100%落实、在线监测联动喷淋；作业时间管控与周边沟通"])
    return Draft(blocks=[
        _p("我方按“识别—评估—预控—应对—复盘”的闭环开展风险管理，开工前编制《项目风险清单》，逐项明确风险描述、发生概率、影响程度、责任人、预控措施与应急措施，每月更新并在生产例会上通报；设计、采购、施工、调试各阶段分别设置风险控制要点。"),
        table(["序号", "风险类别", "风险描述", "预控与应对措施"], [[str(i), *row] for i, row in enumerate(rows, start=1)],
              caption="主要风险及应对措施", widths=[1.2, 2.6, 5.0, 7.6]),
        _p("风险应对实行分级响应：一般风险由项目部自行处置并记录；较大风险由项目经理组织专题会商，制定专项措施并报招标人；重大风险立即启动应急预案，同时书面报告招标人与监理，做到早发现、早报告、早处置，把风险损失控制在最小范围。"),
    ])


# ================= 九、服务承诺 =================

def write_wages(w: Writer) -> Draft:
    f = w.facts
    system = f"严格执行{f['wage_system']}等相关管理制度，" if f.get("wage_system") else ""
    account = "按发包人要求设立农民工工资专用账户，专款专用并接受发包人监管，" if f.get("wage_account") else "设立农民工工资专用账户，"
    return Draft(title="农民工工资与连续施工承诺", blocks=[
        _p(f"我方承诺：本项目施工期间不拖欠农民工工资。项目部{account}{system}实行建筑工人实名制管理与工资保证金制度，配备劳资专管员，按月通过银行代发工资，工资发放表在施工现场公示，接受招标人及劳动保障部门监督；如因我方原因发生拖欠，招标人有权从应付工程款中直接支付并追究我方违约责任。"),
        _p("我方承诺：在农忙季节及招标人资金暂时不到位时，坚持连续施工，不因劳动力返乡、资金周转等原因停工或降低施工强度。为此，我方在中标后即与劳务队伍签订用工协议，农忙季节提前储备替补劳动力；同时为本项目安排自有周转资金，确保招标人资金到位前施工不间断、工序不脱节、进度不滞后。"),
    ])


def write_warranty(w: Writer) -> Draft:
    f = w.facts
    period = "质保期按合同约定执行"
    if f.get("warranty"):
        period = f"质量保修期按招标文件约定执行：{f['warranty'].rstrip('。')}，自竣工验收合格之日起计算"
    if f.get("defect_period"):
        period += f"；缺陷责任期为{f['defect_period']}"
    return Draft(blocks=[
        _p(f"（1）质保期。我方承诺{period}。质保期内因设计、施工或设备质量原因造成的损坏，我方免费修复或更换，不收取任何费用。"),
        _p("（2）响应时限。质保期内接到招标人通知后2小时内响应，24小时内到达现场；一般故障24小时内修复，影响运营的重大故障48小时内修复，不能在规定时限内修复的，提供备用设备或临时措施保障" + ("场站可用" if w.charging else "正常使用") + "。"),
        _p("（3）巡检与保养。质保期内每季度开展一次免费巡检，内容包括设备运行状态、接点温升、绝缘状况" + ("、枪线与充电接口磨损" if w.charging else "") + "、接地与防雷" + ("、软件版本与数据上传" if w.has("platform") else "") + "；每年雨季前、冬季前各开展一次专项检查，巡检报告书面提交招标人。"),
        _p("（4）质保期外服务。质保期满后，我方继续提供终身技术服务，以不高于市场价的优惠价格供应备品备件，并提供有偿维保服务；与招标人签订长期维保协议的，维保费用按成本价计取。"),
    ])


def write_coordination(w: Writer) -> Draft:
    return Draft(title="替招标人排忧解难、协调周边关系承诺", blocks=[
        _p("我方承诺主动替招标人排忧解难：对施工中涉及的场地权属协调、居民与商户诉求、交通导改、临时占道、夜间施工告知等事项，由项目部综合协调部主动对接属地办事处、社区、物业与交警、城管等部门，不让招标人承担额外协调工作。"
           + ("依据供电答复单由供电公司投资建设的内容，由我方免费无条件协调建设完成。" if w.has("power_supply") else "")),
        _p("我方承诺做好周边关系协调：开工前走访周边村民、商户与居民，发放施工告知书，公示投诉电话；施工期间控制噪声、扬尘与占道时间，为受影响的商户提供临时通道与导引标识；发生投诉24小时内到场处理，3日内形成处理结果并回访，确保不发生群体性事件与负面舆情。协调产生的费用已包含在投标报价中，不另行向招标人索取。"),
    ])


def write_cooperation(w: Writer) -> Draft:
    f = w.facts
    attendance = f"，项目经理每月在现场工作时间不少于{f['attendance_days']}天" if f.get("attendance_days") else ""
    blocks = [
        _p("（1）服从管理。我方无条件服从招标人及其委托的监理、造价咨询单位的管理，执行其指令与会议决定；对招标人提出的整改要求，在规定时限内完成并书面回复。"),
        _p("（2）信息报送。每周向招标人报送周报，内容包括进度、质量、安全、造价、外部协调事项与下周计划；每月报送月报与形象进度照片；重大事项即时报告，不隐瞒、不迟报。"),
        _p(f"（3）人员保障。项目经理、设计负责人、技术负责人按投标承诺到岗履职{attendance}，未经招标人书面同意不更换；主要管理人员离场须报备，确保随时可联系、可到场。"),
    ]
    if w.has("platform"):
        blocks.append(_p("（4）平台与数据配合。无条件配合将全部设备接入招标人指定运营平台，提供设备通信协议、点位表、账号与调试支持；平台功能调整或新增点位时，我方免费配合完成配置与联调。"))
    blocks.append(_p(f"（{len(blocks) + 1}）资料配合。按招标人要求的时间、份数与格式提交设计文件、竣工资料与结算资料，配合完成审计、备案与资产入账工作。"))
    return Draft(blocks=blocks)


def write_other_commitments(w: Writer) -> Draft:
    f = w.facts
    commitments = []
    if f.get("subcontract_allowed") is False:
        commitments.append("我方承诺不转包、不违法分包，以自有队伍完成全部设计、采购与施工内容；设备调试确需厂家参与的，由我方统一组织并承担全部责任。")
    commitments.append(f"我方承诺本项目的设计、施工、设备材料均符合国家及{w.location}现行规范与招标人要求，不降低任何标准。")
    commitments.append(f"我方承诺施工期间{(f.get('safety') or '不发生安全生产事故').rstrip('。')}，不发生因扬尘、噪声、渣土运输被行政处罚的事件。")
    for sentence in f.get("special_requirements", []):
        text = re.sub(r"^特别强调[：:]", "", sentence).rstrip("；;。").replace("承包人须", "").replace("承包人", "我方")
        commitments.append(f"我方承诺：{text}。")
    commitments.append("我方承诺中标后7日内提交项目实施策划书与总进度计划，15日内完成项目部组建与进场。")
    suggestions = []
    if w.charging:
        suggestions.append("建议按“标准图集＋场站参数表”方式组织设计。充电场站数量多、单站规模差异大，如逐站从零设计，设计周期与图纸质量都难以保证。建议由我方牵头编制统一的箱变基础、终端基础、排管、接地与配电系统标准图，各场站仅调整容量与平面参数，可明显压缩单站设计周期，并使施工工艺、备品备件、运维方式趋于一致。")
    if w.has("power_supply"):
        suggestions.append("建议将供电报装与场站设计同步启动。供电答复单是高压系统设计的输入条件，也是送电的前置条件，而其办理周期较长。建议中标后立即启动报装，并在等待答复期间先行完成土建与低压部分设计施工，以“并联”替代“串联”，有效压缩单站总工期。")
    if w.has("duct"):
        suggestions.append("建议对既有市政电力排管开展一次集中普查与疏通。排管不通是常见的工期隐患，建议在开工前统一开展通管普查，建立排管可用性台账，对不通管段提前安排疏通或改迁，避免逐站发现、逐站协调造成的被动。")
    if w.has("platform"):
        suggestions.append("建议建立统一的设备编码与数据规范。多品牌设备接入同一运营平台，若编码规则与点位定义不统一，将导致后期数据混乱、运维困难。建议在设计阶段即确定“场站—设备—终端—枪口”四级编码规则与数据字典，作为设备采购与平台接入的共同依据。")
    if w.charging and not w.has("photovoltaic"):
        suggestions.append("建议预留光储扩容条件。在箱变基础、电缆通道与车棚结构中预留光伏与储能接入空间，为后续“光储充”一体化改造留出条件，避免二次开挖与重复投资。")
    if not suggestions:
        suggestions.append("建议设计阶段开展施工可行性审查与限额设计联审，减少施工期变更。")
    return Draft(children=[
        ("其他实质性承诺", [_p(f"（{i}）{text}") for i, text in enumerate(commitments, start=1)]),
        ("合理化建议", [_p(f"（{i}）{text}") for i, text in enumerate(suggestions, start=1)]),
    ])


# ================= 路由 =================

Topic = Callable[[Writer], Draft]
# (标题关键词正则, 评分组关键词正则或空, 写法)；自上而下首个命中生效
_TOPICS: tuple[tuple[str, str, Topic], ...] = (
    (r"总体管理方案|项目详细说明", "", write_overview),
    (r"管理机构|人员配备", "", write_organization),
    (r"质量、进度、投资|信息管理", "", write_controls),
    (r"竣工验收|结算|移交", "", write_handover),
    (r"设计质量", "", write_design_quality),
    (r"风险", r"设计", write_design_risk),
    (r"设计节点", "", write_design_milestones),
    (r"设计进度", "", write_design_schedule),
    (r"限额设计", "", write_cost_limit),
    (r"设计意图|重点难点", "", write_design_intent),
    (r"新技术|新材料|新工艺", "", write_new_tech),
    (r"设备安装", "", write_installation),
    (r"施工方案|技术措施", "", write_construction),
    (r"质量管理体系|质量保证", "", write_quality_system),
    (r"安全管理|安全文明|文明施工", "", write_safety),
    (r"环境保护|扬尘|环保", "", write_environment),
    (r"农民工|连续施工", "", write_wages),
    (r"进度计划|总工期|工期计划|工期安排", "", write_progress),
    (r"风险", "", write_risk),
    (r"质保期|保修", "", write_warranty),
    (r"排忧解难|周边关系|协调", "", write_coordination),
    (r"配合", "", write_cooperation),
    (r"实质性承诺|合理化建议", "", write_other_commitments),
)


def write_score_point(ctx: ComposeContext, facts: dict, title: str, group: str) -> Draft | None:
    """评分要点 → 正文；没有对应写法时返回 None（由调用方留编写框）。"""
    for title_pattern, group_pattern, topic in _TOPICS:
        if re.search(title_pattern, title) and (not group_pattern or re.search(group_pattern, group)):
            return topic(Writer(ctx, facts))
    return None
