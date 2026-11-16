"""清单式目录（须知中“商务标书 / 技术标书应包括下列内容”）的章节编写器，不调用模型。

每个清单条目按标题找到对应写法，只写一次；评分项挂到负责响应它的条目上，不再一项评分写一节。
功能、接口、上线阶段取自招标文件的功能需求清单，工期、付款节点取自招标文件原文；
人员、业绩、服务时限等企业事实取自企业资料，没有就留“待填”，不代填。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable

from api.services.bidgen.business_response import write_performance
from api.services.bidgen.business_sections import certificate_blocks
from api.services.bidgen.document_model import (
    ComposeContext, form_table, paragraph, placeholder, table, title_line, underline,
)
from api.services.bidgen.epc_writer import Draft, split_days
from api.services.bidgen.it_writer import tech_notes
from api.services.bidgen.profile import usable_certificates
from api.services.bidgen.requirements import node_text

_PLATFORM_NODE = re.compile(r"拓展性|扩展性|平台要求|总体要求")
_INTERFACE_WORDS = re.compile(r"ERP|WMS|MES|QMS|IoT|联动|对接|接口|回传|同步|集成", re.I)
# 清单里点名的证明文件 → 企业资料证件类别
_PROOF_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"营业执照", ("business_license",)),
    (r"开户", ("bank_permit",)),
    (r"资质|认证", ("qualification", "quality_system", "environment_system", "safety_system", "itss", "isms", "cmmi", "high_tech")),
    (r"资信|信用", ("credit_report", "credit_gsxt", "credit_china", "finance_report")),
)
# 平台级要求的响应措施：(要求关键词, 我方做法)
_PLATFORM_MEASURES: tuple[tuple[str, str], ...] = (
    (r"部署", "系统集中部署在贵方总部数据中心，各单位通过网络访问同一套系统与数据库，数据实时、口径统一；传输加密、权限隔离并定时备份。"),
    (r"账号|用户数", "系统不按用户数限制授权，账号数量不设上限，可随业务需要自行增加。"),
    (r"低代码|二次开发", "平台内置低代码配置工具，表单、流程、报表、看板可由贵方人员配置扩展；提供二次开发规范与培训，配置内容随系统升级保留。"),
)
# 功能清单条目的“我方响应”：(功能名称/招标要求关键词, 实现方式)。先按功能名称、再按招标要求匹配，
# 自上而下首个命中生效，具体的写法排在笼统的前面。{erp} 由招标文件点名的 ERP 替换；招标文件未提及 ERP 时，
# 含 {erp} 的分句整句去掉，不虚构集成。
_FUNCTION_RESPONSES: tuple[tuple[str, str], ...] = (
    (r"领料|退料|领退料|补料", "工单领料、退料、补料与超额领用审批联动库存过账，同步回传{erp}"),
    (r"投料|防错|防呆", "投料前强制校验物料、批次、效期、库存与配方用量，不符合即阻断并提示原因"),
    (r"齐套|缺料", "按工单BOM与实时库存自动齐套分析，缺料清单推送采购与仓储，齐套后方可下达"),
    (r"派工", "支持按产线、班组、人员三级派工，任务推送至工位一体机与PDA，支持优先级调整与临时插单"),
    (r"排产|排程|APS|甘特", "基于产能、BOM与工艺路线自动排程，甘特图拖拽调整，冲突实时提示，确认后下发派工"),
    (r"工单|生产订单|生产计划", "订单下发、接收、拆分与状态流转（下达/投产/完工/关闭）全流程管理，完工与报废数量实时回传{erp}"),
    (r"SOP|作业指导|图纸", "按工单与工序自动调取SOP、图纸与参数标准，版本随工艺变更更新，可记录查阅与确认"),
    (r"报工|工序流转|过站", "工位扫码报工，自动记录人员、设备、工时与数量，报工数据实时回传{erp}核算"),
    (r"包装|装箱|箱码", "按包装规则生成箱码，扫码装箱校验产品与数量，箱码与产品码父子关联可追溯"),
    (r"条码|标签|二维码|编码规则", "统一标签模板中心，各类编码规则集中维护，打印记录可追溯，模板变更留痕"),
    (r"追溯", "以批次/序列号为主线记录投料、工序、设备、人员与检验结果，支持正向与反向一键追溯"),
    (r"不合格|不良|返工|返修|让步|报废", "不合格品自动触发评审，按返工、返修、让步接收、报废分流处置，处置结果关联批次并纳入质量统计"),
    (r"SPC|过程能力|控制图|CPK", "关键参数实测值自动生成控制图并计算CPK等过程能力指数，超出控制限即时预警"),
    (r"检验|质检|首检|巡检|IQC|IPQC|OQC|FQC", "检验方案按物料、工序、产品配置检验项目与上下限，检验任务按到货、报工自动生成，实测值录入后自动判定"),
    (r"点检|保养|维保", "按设备类型配置点检、保养计划，到期自动生成任务推送责任人，扫码签到后逐项填报，超期自动提醒"),
    (r"维修|报修|故障", "现场扫码报修，维修工单自动派发，记录故障原因、处理过程、备件消耗与停机时长，统计MTBF/MTTR"),
    (r"备件|备品", "备件按安全库存与上下限管理，领用与维修工单关联，低于安全库存自动预警"),
    (r"OEE|稼动|设备效率", "按设备采集运行、停机与产量数据，自动计算时间开动率、性能开动率、合格率与OEE"),
    (r"模具|工装|治具|夹具", "工装模具唯一编码建档，记录领用、使用次数与保养，寿命到期自动预警"),
    (r"设备台账|设备档案|设备管理", "设备按编码建档，记录型号、位置、责任人、技术参数与文档，运行/停机/维修状态实时更新"),
    (r"能耗|能源|水电气", "按车间、产线、设备采集水电气能耗，按产量计算单耗，超标自动预警"),
    (r"数据采集|数采|IoT|物联|PLC|SCADA", "通过采集网关对接PLC与仪表，数据经清洗、换算后按设备与参数存储，供报工、追溯与看板调用"),
    (r"安灯|异常|呼叫", "工位一键呼叫安灯，按异常类型与等级推送责任人，超时逐级升级，处理结果经确认后闭环"),
    (r"盘点", "支持全盘、动态盘点与循环盘点，PDA扫码盘点，差异自动生成调整单，审批后过账"),
    (r"调拨|移库|转库", "调拨、移库扫码执行，在途库存可追踪，库位与批次变更实时更新"),
    (r"配送|线边|叫料", "工位按工单需求叫料，系统生成配送任务，扫码交接签收，线边库存实时更新"),
    (r"出库|发货|拣货|拣选", "按出库单生成拣货任务，PDA扫码校验物料与批次，遵循先进先出，出库数据实时回传{erp}"),
    (r"入库|收货|上架", "到货扫码收货，按检验结果上架入库，系统按规则推荐库位，入库数据实时回传{erp}"),
    (r"库位|货位|仓库建模|库区", "按仓库、库区、库位三级建模，库位容量与存放规则可配置，库存精确到库位与批次"),
    (r"库存|效期|呆滞|安全库存", "按物料设置安全库存、上下限与效期规则，临期、呆滞与超储自动预警"),
    (r"委外|外协", "委外工序发料、收货、检验全程跟踪，委外进度与费用实时可查"),
    (r"工艺路线|BOM|工艺参数|配方", "工艺路线、BOM与工艺参数版本化管理，变更走审批并记录前后差异，工单下达时锁定有效版本"),
    (r"人员|技能|资质|考勤|绩效", "人员技能与上岗资质建档，派工、报工时校验资质，工时自动汇总用于绩效统计"),
    (r"看板|大屏|可视化", "看板组件化配置数据源、刷新频率与布局，车间大屏、电视与PC多端展示，异常数据高亮"),
    (r"报表|统计|分析", "内置报表设计器，按时间、产线、产品、班组多维钻取，可导出Excel/PDF并按角色授权"),
    (r"预警|消息|通知|提醒", "规则引擎配置触发条件与通知对象，站内信、移动端推送多渠道通知，处理过程留痕"),
    (r"移动端|APP|PDA|手持", "PDA、手机端与PC端共用同一业务服务，扫码作业网络中断时本地暂存，恢复后自动补传"),
    (r"权限|角色|用户|账号", "采用“用户—角色—权限”模型，权限细化到菜单、按钮与数据范围，关键操作全程留痕"),
)
_INTEGRATION_TARGETS = re.compile(r"ERP|WMS|MES|QMS|SRM|PLM|OA|IoT|SCADA|EAM|APS", re.I)


def _p(text: str) -> dict:
    return paragraph(text)


def _join(items, sep: str = "、") -> str:
    return sep.join(item for item in items if item)


class ListedWriter:
    """一次生成内各条目共用的招标事实。"""

    def __init__(self, ctx: ComposeContext, facts: dict) -> None:
        self.ctx = ctx
        self.facts = facts or {}
        self.project = self.facts.get("name") or ctx.project_name
        self.owner = self.facts.get("owner") or ctx.purchaser_names
        tree = self.facts.get("requirements") or []
        self.all_text = "\n".join(node["title"] + "\n" + node_text(node) for node in tree)
        # 功能板块：功能清单表里的一组功能（MES、WMS……）；平台级要求（拓展性等）单独处理
        self.modules = [node for node in tree if node["items"] and not _PLATFORM_NODE.search(node["title"])]
        platform = [item["text"] for node in tree if _PLATFORM_NODE.search(node["title"]) for item in node["items"]]
        self.platform_requirements = [part.strip("；; ") for text in platform
                                      for part in re.split(r"(?:^|(?<=[。；]))\s*\d+[、.．]\s*", text) if part.strip("；; ")]
        # 没有表头的概要表（“硬件 | 可包含：服务器……”）
        self.summary_rows = [row for node in tree for rows in node["tables"] for row in rows if len(row) >= 2]
        erp = re.search(r"金蝶云星空|金蝶[A-Za-z0-9一-鿿]{0,6}?(?=ERP|系统|双向|接口)|用友[A-Za-z0-9]{0,6}|SAP", self.all_text)
        self.erp = erp.group(0) if erp else ("ERP" if "ERP" in self.all_text else "")
        self.days = int(self.facts.get("schedule_days") or 0)
        phases = Counter(item.get("attrs", {}).get("上线阶段", "") for module in self.modules for item in module["items"])
        self.phases = sorted(phase for phase in phases if phase)

    # ---- 取值 ----
    def phase_of(self, module: dict) -> str:
        phases = Counter(item.get("attrs", {}).get("上线阶段", "") for item in module["items"])
        phases.pop("", None)
        return phases.most_common(1)[0][0] if phases else ""

    def function_response(self, label: str, requirement: str) -> str:
        """功能清单条目的“我方响应”：写清怎么实现，不写空洞的“满足”。"""
        response = next((answer for source in (label, requirement)
                         for pattern, answer in _FUNCTION_RESPONSES if re.search(pattern, source, re.I)), "")
        if not response and _INTERFACE_WORDS.search(label + requirement):
            targets = _join(dict.fromkeys(name.upper() if name.lower() != "iot" else "IoT"
                                          for name in _INTEGRATION_TARGETS.findall(label + requirement))) or "外部系统"
            response = f"通过统一接口服务与{targets}对接，接口具备鉴权、幂等、失败重试与日终对账，交互过程全程记录日志"
        if not response:
            note = next(iter(tech_notes(label + requirement, limit=1)), "")
            if note:
                response = re.match(r"[^。；]+", note).group(0)
        if not response:
            self.confirm(f"功能响应表中“{label}”未匹配到实现说明，已按通用表述编写，请补充具体实现方式")
            return f"在系统中按招标要求实现{label}，相关规则与参数可配置，业务数据实时记录、可查询可追溯"
        if self.erp:
            return response.replace("{erp}", self.erp)
        # 招标文件未提及 ERP：去掉含 {erp} 的分句，不虚构集成
        return re.sub(r"，[^，]*\{erp\}[^，]*", "", response)

    def phase_modules(self, phase: str) -> list[str]:
        return [module["title"] for module in self.modules if self.phase_of(module) == phase]

    @property
    def module_names(self) -> str:
        return _join(module["title"] for module in self.modules) or "各业务系统"

    def has(self, pattern: str) -> bool:
        return bool(re.search(pattern, self.all_text, re.I))

    def service_value(self, key: str, hint: str) -> str:
        return self.ctx.value(self.ctx.profile.get("service", {}).get(key), hint)

    def template_text(self) -> str:
        """招标文件给出的格式原文（报价单等），用于读取年度服务费之类的约定。"""
        return "\n".join(block.get("text", "") for item in self.ctx.tender.get("format_items", []) for block in item.get("template", []))

    def confirm(self, hint: str) -> None:
        """自动生成但须由投标人确认的内容，列入待确认清单。"""
        entry = {"section": self.ctx._section_key, "hint": hint}
        if entry not in self.ctx.pending:
            self.ctx.pending.append(entry)

    def payment_stages(self) -> list[tuple[str, str]]:
        """付款节点 → (节点, 支付比例)，按招标文件原文。"""
        stages = []
        for clause in re.split(r"[；;。]", self.facts.get("payment_terms") or ""):
            matched = re.search(r"(.+?)后?\s*\d+\s*个工作日内.*?支付总费用的\s*(\d+%)", clause)
            if matched:
                stages.append((re.sub(r"^[，,\s]+|[，,]\s*甲方.*$", "", matched.group(1)), matched.group(2)))
        return stages

    # ================= 商务标书 =================

    def legal_forms(self, item: dict, criteria: list[dict]) -> Draft:
        ctx, company = self.ctx, self.ctx.company
        legal, agent = ctx.profile["legalRep"], ctx.profile["agent"]
        identity = [
            title_line("法定代表人身份证明书"),
            paragraph(f"投标人名称：{underline(ctx.company_name)}", indent=False),
            paragraph(f"单位性质：{underline(ctx.value(company['orgStructure'].split('，')[0], '单位性质'))}", indent=False),
            paragraph(f"地址：{underline(ctx.value(company['address'], '注册地址'))}", indent=False),
            paragraph(f"成立时间：{underline(ctx.value(company['foundedDate'], '成立时间'))}", indent=False),
            _p(f"姓名：{underline(ctx.value(legal['name'], '法定代表人姓名'))}　　职务：{underline(legal['title'] or '法定代表人')}　　"
               f"身份证号：{underline(ctx.value(legal['idNumber'], '法定代表人身份证号'))}，系{underline(ctx.company_name)}的法定代表人。"),
            _p("特此证明。"),
            ctx.signature_block(),
            *certificate_blocks(ctx, ("legal_id",), "法定代表人身份证正、反面扫描件"),
        ]
        letter = [
            title_line("授权委托书"),
            paragraph(f"致：{underline(self.owner)}", indent=False),
            _p(f"本人{underline(ctx.value(legal['name'], '法定代表人姓名'))}系{underline(ctx.company_name)}的法定代表人，"
               f"现授权{underline(ctx.value(agent['name'], '委托代理人姓名'))}"
               f"（身份证号：{underline(ctx.value(agent['idNumber'], '委托代理人身份证号'))}，职务：{underline(agent['title'])}）为我方代理人，"
               f"以我方名义参加{underline(self.project)}的投标活动。代理人在投标、开标、评标、合同谈判及签约过程中所签署的一切文件和处理的一切有关事务，我方均予以承认。"),
            _p("本授权书自签署之日起生效，至本项目投标活动及合同签订事宜全部结束时止。代理人无转委托权。"),
            ctx.signature_block("法定代表人（签字或盖章）：＿＿＿＿＿＿＿＿", "委托代理人（签字）：＿＿＿＿＿＿＿＿"),
            *certificate_blocks(ctx, ("agent_id",), "委托代理人身份证正、反面扫描件"),
        ]
        return Draft(children=[("法定代表人身份证明书", identity), ("授权委托书", letter)])

    def proofs(self, item: dict, criteria: list[dict]) -> Draft:
        """清单逐项点名的证明文件：有原件就附上，没有的留粘贴位。"""
        ctx = self.ctx
        names = [name.strip() for name in re.split(r"[、，,]", re.sub(r"等$", "", item["title"])) if name.strip()]
        usable = usable_certificates(ctx.profile, ctx.tender.get("bid_deadline", ""))
        rows, children = [], []
        for index, name in enumerate(names, start=1):
            categories = next((codes for pattern, codes in _PROOF_CATEGORIES if re.search(pattern, name)), ())
            held = [certificate for certificate in usable if certificate["category"] in categories]
            rows.append([str(index), name, "已提供，扫描件附后" if held else ctx.need(f"{name}（企业资料中未找到，请上传或粘贴）")])
            children.append((name, certificate_blocks(ctx, tuple(dict.fromkeys(c["category"] for c in held)), f"{name}扫描件")
                             if held else [placeholder(f"（此处粘贴：{name}扫描件）")]))
        blocks = [_p("我方按招标文件要求提供下列证明文件，扫描件均加盖我方公章，真实有效："),
                  table(["序号", "招标文件要求的证明文件", "提供情况"], rows, caption="证明文件清单", widths=[1.4, 8.0, 7.0])]
        return Draft(blocks=blocks, children=children)

    def company_profile(self, item: dict, criteria: list[dict]) -> Draft:
        ctx, company, staff = self.ctx, self.ctx.company, self.ctx.profile["staff"]
        overview = [form_table([
            ["企业名称", ctx.company_name, "统一社会信用代码", ctx.value(company["creditCode"], "统一社会信用代码")],
            ["成立时间", ctx.value(company["foundedDate"], "成立时间"), "注册资本", ctx.value(company["registeredCapital"], "注册资本")],
            ["注册地址", ctx.value(company["address"], "注册地址"), "企业类型", company["orgStructure"] or "—"],
            ["法定代表人", ctx.value(ctx.profile["legalRep"]["name"], "法定代表人姓名"), "员工总人数", ctx.value(staff["total"], "员工总人数")],
            ["联系人", ctx.value(company["contact"], "联系人"), "联系电话", ctx.value(company["phone"], "联系电话")],
        ], caption="投标人基本情况表", widths=[3.0, 5.6, 3.4, 4.4])]
        overview.append(_p(ctx.value(company["intro"], "企业简介（发展历程、主营产品、行业地位）")))
        if company["businessScope"]:
            overview.append(_p(f"经营范围：{company['businessScope']}"))
        if company["honors"]:
            overview.append(_p(f"资质荣誉：{company['honors']}"))
        finance_rows = [[str(row.get("year", "")), str(row.get("revenue", "") or "—"), str(row.get("paidCapital", "") or "—"),
                         str(row.get("debtRatio", "") or "—")] for row in ctx.profile["finance"]]
        finance = [_p("我方财务状况良好，近年主要财务指标如下，财务报表附后："),
                   table(["年度", "主营收入（万元）", "实收资本（万元）", "资产负债率"],
                         finance_rows or [[ctx.need("近三年财务数据（年度、主营收入、资产负债率）"), "", "", ""]], caption="近年财务状况"),
                   *certificate_blocks(ctx, ("finance_report",), "近年财务报表扫描件")]
        performance_rule = next((c["rule"] for c in criteria if re.search(r"业绩|案例", c["factor"])), "")
        cases = write_performance(ctx, performance_rule, []).blocks
        return Draft(children=[("企业概况", overview), ("财务状况", finance), ("近三年制造行业实施案例", cases)])

    def commitment(self, item: dict, criteria: list[dict]) -> Draft:
        ctx = self.ctx
        clauses = ["我方已完整阅读并理解招标文件（含投标须知、评标办法、功能需求清单及全部附件）及合同条款，完全接受其全部内容，无任何保留。"]
        if self.facts.get("schedule"):
            clauses.append(f"我方承诺按招标文件要求的交付时间执行：{self.facts['schedule'].rstrip('。')}。")
        if self.facts.get("payment_terms"):
            clauses.append(f"我方接受招标文件约定的付款方式：{self.facts['payment_terms'].rstrip('。；;')}。")
        clauses += [
            "我方承诺中标后在招标文件规定的时间内按中标价与贵方签订合同，不将中标项目转让、转包或拆分转让、转包。",
            "我方承诺投标文件中的全部资料、数据真实有效，不存在串通投标、借用他人名义投标或弄虚作假情形，并接受招标文件罚则的约束。",
        ]
        blocks = [title_line("对招标文件及合同条款的承诺及补充意见"), paragraph(f"致：{self.owner}", indent=False),
                  _p(f"我方参加{self.project}的投标，现就招标文件及合同条款郑重承诺如下：")]
        blocks += [_p(f"{index}、{clause}") for index, clause in enumerate(clauses, start=1)]
        blocks += [_p("补充意见：我方对招标文件及合同条款无偏离、无补充意见。"), ctx.signature_block()]
        self.confirm("“对招标文件及合同条款的承诺及补充意见”按完全响应、无补充意见编写，对公司有约束力，请确认后再提交；如有偏离或补充请直接修改")
        return Draft(blocks=blocks)

    # ================= 技术方案各节 =================

    def overall_design(self, item: dict, criteria: list[dict]) -> Draft:
        externals = _join([self.erp])
        idea = [
            _p(f"{self.project}总体设计以招标文件“功能需求清单及说明”为依据，坚持“统一平台、数据贯通、分步上线、持续扩展”的思路："
               f"以一个平台承载{self.module_names}，以统一的主数据与编码体系打通计划、执行、仓储、质量与设备各环节"
               + (f"，以标准接口与{externals}实时交互" if externals else "") + "，使各业务环节在同一套实时数据上协同。"),
            table(["设计原则", "我方设计"], [
                ["先进性与成熟性", "采用成熟的平台化产品，功能以配置为主、定制为辅；架构分层、服务解耦，便于持续升级"],
                ["标准化与开放性", "数据编码、接口报文、单据格式统一标准；对外提供标准接口服务，新系统接入不改动核心程序"],
                ["可扩展性", "模块可按阶段逐步启用，组织、仓库、产线可随业务增长扩充；提供低代码配置能力支持后续自主扩展"],
                ["安全与稳定", "身份认证、权限最小化、操作留痕、数据备份并举，关键服务冗余部署"],
                ["易用与易维护", "按岗位定制工作台与移动端作业界面，参数、流程、报表可配置，运维监控可视化"],
            ], caption="总体设计原则", widths=[3.6, 12.8]),
        ]
        layers = [
            ["接入层", "PC浏览器、PDA/手持终端、工位终端（一体机）、车间看板统一接入"],
            ["应用层", f"{self.module_names}等业务应用，模块化部署、按阶段启用"],
            ["平台层", "权限、流程、消息预警、条码标签、报表看板、低代码配置等公共能力"],
            ["数据层", "统一主数据与业务数据库，数据字典、编码规则与数据交换服务"],
            ["基础层", "服务器、存储、网络与操作系统、数据库等运行环境"],
        ]
        architecture = [_p("系统采用分层架构，自上而下分为接入层、应用层、平台层、数据层与基础层，各层职责清晰、接口标准，单层升级不影响其他层。"),
                        table(["层级", "组成与职责"], layers, caption="系统分层架构", widths=[2.6, 13.8])]
        children = [("总体设计思路与原则", idea), ("总体架构设计", architecture)]
        steps = [name for name, pattern in (("ERP生产订单下发", "ERP"), ("生产派工与排产", "派工|排产"), ("领料与投料防错", "领料|投料"),
                                           ("工序流转报工", "报工"), ("过程检验与不良处置", "检验|不良"), ("成品入库", "入库"),
                                           ("销售发货", "发货"), ("实绩回传ERP", "回传")) if self.has(pattern)]
        if len(steps) >= 4:
            children.append(("业务主线与数据流", [
                _p("系统以订单为起点、以入库发货和实绩回传为终点组织业务主线：" + "→".join(steps) + "。每个环节的输出即下一环节的输入，单据自动流转、状态实时可见，批次信息贯穿全程。"),
            ]))
        if self.modules:
            rows = [[str(index), module["title"], str(len(module["items"])), _join(i["label"] for i in module["items"][:6]) + ("等" if len(module["items"]) > 6 else ""),
                     self.phase_of(module) or "—"] for index, module in enumerate(self.modules, start=1)]
            children.append(("建设范围总览", [
                _p(f"按招标文件功能需求清单，本项目共{len(self.modules)}个板块、{sum(len(m['items']) for m in self.modules)}项功能，全部纳入建设范围："),
                table(["序号", "板块", "功能数", "主要功能", "上线阶段"], rows, caption="建设范围总览", widths=[1.2, 3.2, 1.6, 8.4, 2.0]),
            ]))
        return Draft(children=children)

    def platform_design(self, item: dict, criteria: list[dict]) -> Draft:
        capabilities = [
            ["组织与权限", "多组织、多仓库、多产线建模；“用户—角色—权限”授权，细化到菜单、按钮与数据范围，关键操作留痕"],
            ["流程与消息", "可视化流程配置，审批、预警、超期提醒统一由消息中心推送到PC端与移动端"],
            ["条码与标签", "物料、批次、库位、容器、成品编码规则与标签模板统一管理，扫码数据实时关联业务单据"],
            ["报表与看板", "报表、看板可配置，按岗位授权，支持导出与大屏展示"],
            ["接口服务", "统一接口服务，具备鉴权、幂等、失败重试、日志与对账能力"],
            ["低代码配置", "表单、流程、报表、看板以配置方式扩展，升级不覆盖已配置内容"],
        ]
        children = [("平台公共能力", [_p("各业务系统共用同一平台底座，公共能力统一建设、统一维护，避免各系统重复建设和数据口径不一："),
                                 table(["平台能力", "设计说明"], capabilities, caption="平台公共能力", widths=[3.2, 13.2])])]
        if self.platform_requirements:
            rows = [[str(index), text, "满足。" + next((measure for pattern, measure in _PLATFORM_MEASURES if re.search(pattern, text)),
                                                     "按此要求设计与交付。")] for index, text in enumerate(self.platform_requirements, start=1)]
            children.append(("平台扩展性要求响应", [_p("对招标文件提出的系统拓展性要求，我方逐条响应如下："),
                                          table(["序号", "招标要求", "我方响应"], rows, caption="系统拓展性要求响应", widths=[1.2, 7.6, 7.6])]))
        children += [
            ("安全性与稳定性设计", [
                _p("安全方面：全站加密传输，口令复杂度、有效期与失败锁定策略可配置；权限按岗位最小化授予，数据按组织、仓库、产线隔离；登录与关键业务操作全程记录日志，可追溯到人、到时间、到数据。"),
                _p("稳定方面：应用服务与数据库分离部署，关键服务支持冗余；数据库定时全量与增量备份并定期做恢复演练；接口调用失败自动重试并告警，扫码作业在网络中断时本地暂存、恢复后补传，保证现场作业不中断。"),
            ]),
            ("易用性与易维护性设计", [
                _p("易用方面：按岗位定制工作台，登录后即见本人待办；现场作业以扫码为主，自动带出单据与批次信息，减少手工录入；PC端、PDA与工位终端操作风格一致。"),
                _p("易维护方面：参数、编码规则、流程、报表均可由管理员配置；提供运行监控与日志查询，接口、作业异常一目了然；系统升级先在测试环境验证后发布，升级不影响已有数据与配置。"),
            ]),
        ]
        return Draft(children=children)

    def functions(self, item: dict, criteria: list[dict]) -> Draft:
        ctx = self.ctx
        product = [_p(ctx.need("产品品牌、成熟度与市场应用情况（产品名称、发展历程、客户数量、制造行业典型客户）"))]
        cases = ctx.profile.get("performances", [])
        if cases:
            product.append(_p(f"我方产品已在{_join(str(case.get('buyer', '')) for case in cases[:5])}等客户成功应用，案例详见商务标书。"))
        children = [("产品成熟度与应用情况", product)]
        for module in self.modules:
            rows = [[str(index), entry["label"], entry["text"], entry.get("attrs", {}).get("上线阶段", "—"),
                     self.function_response(entry["label"], entry["text"])]
                    for index, entry in enumerate(module["items"], start=1)]
            blocks = [_p(f"{module['title']}共{len(rows)}项功能，我方逐项响应如下："),
                      table(["序号", "功能模块", "招标文件要求", "上线阶段", "我方响应"], rows, caption=f"{module['title']}功能响应表",
                            widths=[1.2, 2.8, 5.2, 1.6, 5.6])]
            notes = tech_notes(node_text(module) + module["title"])
            if notes:
                blocks.append(_p("实现要点：" + "".join(notes)))
            children.append((f"{module['title']}功能", blocks))
        children.append(("技术性能设计", [
            _p("系统性能从架构、数据、接口三个层面保障，具体性能指标在需求调研阶段与贵方共同确认并写入验收标准，上线前通过压力测试验证："),
            table(["性能方面", "设计措施"], [
                ["并发与响应", "应用服务可横向扩展；热点数据缓存，复杂统计异步计算，列表分页与按需加载"],
                ["数据处理", "业务库与历史库分离，大批量导入导出分批处理，报表查询走只读副本或汇总表"],
                ["接口时效", "实时接口采用事件触发，批量数据定时同步；接口超时、失败自动重试并告警"],
                ["现场作业", "扫码作业本地校验、即时反馈；网络中断时本地暂存，恢复后自动补传"],
                ["可用性", "关键服务冗余部署，故障自动切换；数据定时备份、定期恢复演练"],
            ], caption="技术性能设计", widths=[3.0, 13.4]),
        ]))
        return Draft(blocks=[_p("我方以招标文件“重点项目明细”为基线，对各板块功能逐项说明实现方式，全部纳入建设范围。")] if self.modules else [], children=children)

    def implementation_service(self, item: dict, criteria: list[dict]) -> Draft:
        stages = [
            ["项目启动与调研", "成立联合项目组，分部门跟班调研，梳理现状流程与单据", "调研计划、调研纪要、需求规格说明书"],
            ["蓝图设计", "业务流程、编码体系、接口与数据方案设计，原型演示确认", "业务蓝图、接口方案、原型确认单"],
            ["配置开发与测试", "系统配置、个性化开发、接口联调，功能与集成测试", "可运行系统、测试报告"],
            ["数据准备与培训", "基础数据整理导入、期初数据初始化，分岗位培训", "数据核对表、培训记录"],
            ["上线与验收", "分阶段切换上线、现场值守、问题闭环，配合验收", "上线确认单、验收报告"],
        ]
        return Draft(children=[
            ("实施方法", [_p("我方采用“调研—蓝图—实现—准备—上线”五步实施法，每一步的成果经贵方书面确认后进入下一步："),
                      table(["实施步骤", "主要工作", "交付成果"], stages, caption="实施方法与交付成果", widths=[3.2, 8.4, 4.8])]),
            ("实施服务内容", [_p(self.ctx.enumerate_clauses([
                "业务调研与流程梳理，输出经双方确认的需求与蓝图",
                "系统安装部署、参数配置与个性化开发",
                f"与{self.erp or '现有业务系统'}的接口开发与联调",
                "基础数据、期初数据的整理指导、导入与核对",
                "分岗位培训与上线现场值守",
                "上线后问题跟踪处理直至稳定运行并通过验收",
            ], shuffle=False))]),
            ("技术支持方式", [
                _p(f"实施期间项目经理与实施顾问在关键阶段驻场，上线首周安排专人现场值守；日常通过服务热线（{self.service_value('hotline', '服务热线')}）、专属服务群与远程协助提供支持，远程无法解决的问题安排工程师到现场处理。"),
            ]),
        ])

    def difficulties(self, item: dict, criteria: list[dict]) -> Draft:
        candidates = (
            (r"ERP", f"与{self.erp or 'ERP'}双向实时对接", "订单下发与完工、领退料、库存等实绩回传任一方向不一致，都会造成账实不符",
             "主数据以ERP为准，业务数据按场景实时同步；接口具备幂等、重试与每日对账，上线前用真实单据端到端联调"),
            (r"追溯|谱系", "全链批次追溯", "原料、配料、各工序、成品任一环节漏扫都会使追溯链断裂",
             "以批次号为主线设计数据模型，关键工序强制扫码校验，拆批、并批、返工保留批次关联"),
            (r"委外", "委外环节数据补录", "委外方不使用系统，纸质随行单回厂后补录，数据滞后且易出错",
             "委外发出、返回扫码登记，随行单按模板补录并与发出数量核销，缺记录按不合格处理"),
            (r"IoT|设备联网|数据采集", "设备数据采集", "设备协议各异，采集数据存在缺失、跳变",
             "先确认设备通讯协议与点位表，采集数据经清洗、换算后再供业务使用，采集异常自动告警"),
            (r"期初|初始化", "期初数据切换", "切换时点库存、在制、未完成订单不准确会影响上线后全部业务",
             "制定切换计划，切换前集中盘点，期初数据按模板导入并经贵方签字核对"),
            (r"WMS|仓储", "多系统业务协同", f"{self.module_names}之间单据、库存状态必须实时一致",
             "各系统共用同一平台与数据库，单据状态联动，不靠系统间二次同步"),
        )
        rows = [[name, analysis, measure] for pattern, name, analysis, measure in candidates if self.has(pattern)]
        if len(self.phases) >= 2:
            rows.append(["分阶段上线衔接", f"{_join(self.phases)}先后上线，前一阶段已运行的业务不能因后续上线中断",
                         "总体蓝图一次设计、分阶段实现；后续阶段在测试环境完成与已上线模块的回归验证后再切换"])
        risks = [
            ["需求变更", "范围扩大、工期延误", "需求书面确认；变更统一登记、评估影响、双方审批后执行"],
            ["基础数据质量", "上线后单据、库存错误", "提供数据模板与校验规则，上线前组织数据核对并签字确认"],
            ["接口进度", "联调受阻", "尽早确定接口规范与联调计划，对方接口未就绪时用模拟数据先行开发"],
            ["用户使用习惯", "系统上线后用不起来", "分岗位培训、关键用户带动、上线值守，配套管理制度"],
            ["工期", "里程碑延误", "成熟产品配置化实施，模块并行推进，关键路径每周跟踪纠偏"],
        ]
        return Draft(children=[
            ("技术难点分析与对策", [_p("结合招标文件功能需求，我方识别本项目技术难点及对策如下："),
                           table(["技术难点", "难点分析", "我方对策"], rows or [["需求落实", "功能点多、涉及部门多", "需求逐条分解为任务清单并经贵方确认"]],
                                 caption="技术难点分析与对策", widths=[3.2, 6.2, 7.0])]),
            ("技术风险及应对措施", [table(["风险", "影响", "应对措施"], risks, caption="技术风险及应对措施", widths=[3.0, 4.0, 9.4])]),
        ])

    def interfaces(self, item: dict, criteria: list[dict]) -> Draft:
        rows = [[module["title"], entry["label"], entry["text"]] for module in self.modules for entry in module["items"]
                if _INTERFACE_WORDS.search(entry["text"] + entry["label"])]
        external = [row for row in rows if re.search(r"ERP|金蝶", row[1] + row[2])]
        children = [("接口设计原则", [
            _p("接口按“统一入口、标准报文、全程留痕”设计：所有对内对外交互经统一接口服务，一个接口一份规范（地址、报文、字段、错误码、示例）；"
               "调用具备鉴权、幂等、失败重试与日志，接口运行状态可视化监控，异常自动告警。"),
        ])]
        if external:
            children.append((f"与{self.erp or 'ERP'}的接口", [
                _p(f"招标文件要求与{self.erp or 'ERP'}交互的功能如下，我方全部实现，主数据以ERP为准，业务实绩实时回传并每日对账："),
                table(["板块", "功能模块", "交互内容（招标要求）"], external, caption="与ERP的接口清单", widths=[2.8, 3.6, 10.0]),
            ]))
        internal = [row for row in rows if row not in external]
        if internal:
            children.append(("系统各功能模块之间的关联", [
                _p("各板块共用同一平台与数据库，下列功能之间的联动由系统自动完成，不需人工在系统间重复录入："),
                table(["板块", "功能模块", "关联内容（招标要求）"], internal, caption="功能模块关联清单", widths=[2.8, 3.6, 10.0]),
            ]))
        children.append(("接口实施与保障", [
            _p("接口实施分为规范确认、开发、单元测试、联调、试运行五步；联调用真实单据逐场景验证并形成联调记录。运行期间每日自动对账，差异数据生成清单并通知责任人处理，确保各系统数据口径一致。"),
        ]))
        return Draft(children=children)

    def migration(self, item: dict, criteria: list[dict]) -> Draft:
        opening = next((entry["text"] for module in self.modules for entry in module["items"] if re.search(r"期初|初始化", entry["label"])), "")
        scope = [
            ["基础数据", "物料、BOM与工艺路线、仓库库位、设备台账、人员与组织、检验标准等", "按模板整理，以ERP或现有台账为准"],
            ["期初数据", opening or "切换时点库存、在制品、未完成订单", "切换前集中盘点，按模板导入"],
            ["历史数据", "仍需查询的历史单据与质量记录", "按贵方确认的范围与年限导入或归档备查"],
        ]
        steps = [
            ["数据盘点", "梳理旧系统与线下台账的数据来源、口径与责任部门", "数据清单"],
            ["清洗转换", "按新系统编码规则清洗、去重、补全、转换格式", "标准数据文件"],
            ["试导入", "在测试环境导入并核对，修正问题后重复验证", "试导入报告"],
            ["正式导入", "切换时点冻结旧系统数据，正式导入并核对", "数据核对表（双方签字）"],
            ["切换保障", "保留旧数据备份，切换失败可回退到旧系统继续作业", "回退方案"],
        ]
        return Draft(children=[
            ("迁移范围", [table(["数据类别", "数据内容", "迁移方式"], scope, caption="数据迁移范围", widths=[2.6, 8.6, 5.2])]),
            ("迁移步骤", [table(["步骤", "工作内容", "成果"], steps, caption="数据迁移步骤", widths=[2.6, 9.6, 4.2])]),
            ("数据校验", [_p("导入数据从数量、金额、关键字段三个维度校验：记录数与源数据一致，库存数量与盘点结果一致，编码、单位、批次等关键字段完整合规；校验结果形成核对表，经贵方业务部门签字确认后方可上线。")]),
        ])

    def hardware(self, item: dict, criteria: list[dict]) -> Draft:
        text = next((row[-1] for row in self.summary_rows if any(cell.strip() == "硬件" for cell in row[:-1])), "")
        listed = re.search(r"可包含[:：]?(.+?)等", text)
        devices = [name.strip() for name in re.split(r"[、，,]", listed.group(1)) if name.strip()] if listed else []
        central = next((requirement for requirement in self.platform_requirements if re.search(r"部署", requirement)), "")
        deploy = [_p((f"招标文件要求：“{central.rstrip('。')}。”" if central else "") +
                     "我方按集中部署设计：应用服务器、数据库服务器部署在数据中心，各厂区、车间通过内部网络访问；车间配置工位终端、PDA与条码打印设备，经无线或有线网络接入。正式环境与测试环境分离，变更先在测试环境验证。"),
                  table(["服务器角色", "数量（建议）", "建议配置", "说明"], [
                      ["应用服务器", "2台", "8核CPU／32GB内存／200GB SSD", "承载业务服务与接口服务，可横向扩展"],
                      ["数据库服务器", "1～2台", "16核CPU／64GB内存／SSD阵列", "主备部署，定时备份"],
                      ["文件/报表服务器", "1台", "8核CPU／16GB内存／1TB存储", "附件、图纸、报表与日志"],
                      ["测试环境", "1套", "按正式环境50%配置", "功能验证、升级预演"],
                  ], caption="服务器配置建议", widths=[3.0, 2.4, 5.2, 5.8])]
        usages = {"服务器": "系统运行环境", "条码录入": "现场扫码采集", "条码打印": "物料、批次、库位、成品标签打印",
                  "终端": "工位报工、电子SOP查看", "一体机": "工位报工、电子SOP查看", "PDA": "收货、上架、拣货、盘点等移动作业"}
        rows = [[name, next((usage for key, usage in usages.items() if key in name), "按现场需要配置"),
                 self.ctx.need(f"{name}品牌、型号及主要技术参数")] for name in devices]
        terminal = [_p("硬件按招标文件要求单独报价、不计入总价，以单价形式报价，贵方按实际需要选购；品牌、型号与单价见商务标书投标报价表“硬件单独报价”。")]
        if rows:
            terminal.append(table(["设备", "用途", "品牌、型号及技术参数"], rows, caption="硬件设备清单", widths=[3.6, 5.6, 7.2]))
        self.confirm("服务器配置为按常规规模给出的建议值，请按贵公司产品实际要求核对；硬件设备的品牌、型号与技术参数需手工补充")
        return Draft(children=[("硬件部署架构", deploy), ("硬件设备及技术参数", terminal)])

    def progress_management(self, item: dict, criteria: list[dict]) -> Draft:
        total = f"合同签订生效后{self.days}个自然日内" if self.days else "招标文件要求的交付期内"
        staged = "；".join(f"{phase}上线{_join(self.phase_modules(phase))}" for phase in self.phases if self.phase_modules(phase))
        return Draft(children=[
            ("实施总体安排", [_p(f"我方承诺{total}完成全部交付" + (f"，按招标文件功能清单的上线阶段分步实施：{staged}" if staged else "")
                           + "。详细进度计划、各阶段工作内容与上线方案见技术标书“项目实施周期和计划”。")]),
            ("进度管理措施", [_p(self.ctx.enumerate_clauses([
                "计划分级：总体计划明确里程碑，阶段计划分解到周、落实到人",
                "例会与周报：每周召开项目例会，对照计划检查完成情况并提交周报",
                "里程碑确认：每个里程碑的交付成果经贵方书面确认后进入下一阶段",
                "偏差纠正：关键任务出现延误即分析原因，通过增加人员、调整并行度等措施追回",
                "变更控制：需求与计划变更统一登记、评估对工期的影响，经双方审批后执行",
            ], shuffle=False))]),
        ])

    def upgrade_service(self, item: dict, criteria: list[dict]) -> Draft:
        warranty = self.facts.get("warranty") or ""
        free_year = "第一年减免" in self.template_text()
        period = (f"质保期为{warranty}" if warranty else "质保期按招标文件及合同约定执行") + ("，项目验收后第一年免收年度服务费" if free_year else "")
        return Draft(children=[
            ("质保期承诺", [_p(f"{period}。质保期内，我方对本项目交付的全部软件（{self.module_names}）免费提供升级与技术服务，因软件自身缺陷产生的问题免费修复。")]),
            ("软件升级服务", [table(["升级类型", "内容", "方式"], [
                ["缺陷修复", "修复运行中发现的程序缺陷与安全漏洞", "补丁包，测试环境验证后发布"],
                ["功能优化", "在已交付功能范围内的易用性改进、报表与看板调整", "版本更新，经贵方确认后发布"],
                ["版本升级", "产品新版本中适用于本项目的功能", "提交升级方案，在非生产时段实施"],
            ], caption="质保期软件升级服务", widths=[2.8, 8.4, 5.2])]),
            ("技术服务内容", [_p("质保期内提供使用咨询、问题诊断、数据核查、参数与权限调整指导、新增人员操作培训等技术服务；每次升级同步更新操作手册与配置说明，保证文档与系统一致。")]),
        ])

    def owner_support(self, item: dict, criteria: list[dict]) -> Draft:
        rows = [
            ["项目组织", "成立项目领导小组，指定项目负责人，各业务部门指定关键用户参与调研、测试与培训", "项目启动时"],
            ["业务资料", "提供现有流程、单据、报表、编码规则及管理制度", "调研阶段"],
            ["基础数据", "按模板整理物料、工艺、库位、设备等基础数据，并对数据准确性负责", "上线前"],
            ["运行环境", "按我方建议准备服务器、网络、车间无线覆盖及终端设备", "部署前"],
            ["接口配合", f"协调{self.erp or '相关业务系统'}开放接口或由其服务方配合联调", "联调阶段"],
            ["确认与验收", "对需求、蓝图、测试结果及各阶段交付成果及时组织确认与验收", "各里程碑"],
            ["现场条件", "为驻场人员提供办公场地及必要的工作条件", "实施期间"],
        ]
        return Draft(blocks=[_p("为保证项目按期、按质交付，需要贵方提供以下配合："),
                             table(["配合事项", "具体内容", "时间"], rows, caption="需贵方提供的配合", widths=[2.8, 10.6, 3.0])])

    def maintenance(self, item: dict, criteria: list[dict]) -> Draft:
        response = self.service_value("responseHours", "售后响应时限（小时）")
        arrival = self.service_value("arrivalHours", "到场时限（小时）")
        return Draft(children=[
            ("日常运行维护", [_p(self.ctx.enumerate_clauses([
                "运行监控：对服务器资源、数据库、接口与定时任务运行状态进行监控，异常及时告警处理",
                "定期巡检：定期检查系统性能、日志、备份与安全状况，出具巡检报告并提出优化建议",
                "数据备份：按备份策略执行数据库全量与增量备份，定期进行恢复验证",
                "日常支持：解答使用问题，协助处理数据异常、权限与参数调整",
            ], shuffle=False))]),
            ("故障处理", [
                _p(f"故障按影响程度分级处理，接到报修后{response}小时内响应，远程无法解决时{arrival}小时内到达现场："),
                table(["故障等级", "定义", "处理方式"], [
                    ["紧急", "系统无法使用或影响生产、发货", "立即启动应急处理，先恢复业务，再分析原因"],
                    ["重要", "部分功能不可用，有临时替代办法", "优先安排处理，给出临时方案与修复计划"],
                    ["一般", "不影响业务的缺陷或使用问题", "远程指导或随版本更新修复"],
                ], caption="故障分级处理", widths=[2.4, 6.6, 7.4]),
                _p("每个故障记录原因、处理过程与结果，处理完成经贵方确认后关闭；重大故障提交书面分析报告与预防措施。"),
            ]),
        ])

    def training(self, item: dict, criteria: list[dict]) -> Draft:
        rows = [
            ["管理层", "系统整体功能、管理报表与看板的使用", "集中讲解", "上线前"],
            ["关键用户", "本部门全部业务流程、基础数据维护、常见问题处理", "讲授+上机实操", "测试与上线前"],
            ["一线操作人员", "本岗位操作（扫码报工、收发货、检验录入、点检保养等）", "分岗位现场实操", "上线前及上线首周"],
            ["系统管理员", "权限、参数、流程与报表配置，备份恢复与日常运维", "讲授+配置实操", "上线前"],
        ]
        return Draft(children=[
            ("用户培训", [_p("培训按“分层分类、学以致用”组织，提供培训教材与操作手册，培训后考核，未通过的人员安排补训："),
                      table(["培训对象", "培训内容", "培训方式", "培训时间"], rows, caption="培训计划", widths=[2.8, 7.8, 3.2, 2.6])]),
            ("技术支持", [_p(f"提供服务热线（{self.service_value('hotline', '服务热线')}）、专属服务群与远程协助三种支持渠道，问题统一登记编号、全程可查。")]),
            ("售后服务", [
                _p("售后服务流程：受理→分级→派单→处理→验证→回访。安排熟悉本项目的实施人员负责售后，关键岗位设置AB角，避免人员变动影响服务。"),
                _p(f"服务标准：接到报修后{self.service_value('responseHours', '售后响应时限（小时）')}小时内响应，一般问题远程解决，"
                   f"需现场处理的{self.service_value('arrivalHours', '到场时限（小时）')}小时内到达现场；处理完成后回访确认，服务记录全部归档。"),
            ]),
        ])

    def after_warranty(self, item: dict, criteria: list[dict]) -> Draft:
        fee = "年度服务费" in self.template_text()
        return Draft(blocks=[
            _p("质保期满后，我方继续提供长期服务，贵方可按需选择年度服务" + ("，服务费不高于商务标书投标报价表中“年度服务费”的标准" if fee else "") + "。"),
            table(["服务项目", "服务内容"], [
                ["运行维护", "故障处理、缺陷修复、数据核查、巡检与运维报告，服务标准与质保期内一致"],
                ["版本升级", "提供产品新版本及升级实施"],
                ["需求变更", "新增功能、报表与接口，按工作量评估后另行约定"],
                ["培训支持", "新增人员、新增业务的培训与上线支持"],
            ], caption="质保期外服务内容", widths=[3.2, 13.2]),
            _p("对系统缺陷与安全漏洞，无论是否续签年度服务，我方均负责修复。"),
        ])

    # ================= 技术标书其他条目 =================

    def team(self, item: dict, criteria: list[dict]) -> Draft:
        ctx = self.ctx
        requirement = next((c["rule"] for c in criteria if re.search(r"人员|项目组|团队", c["factor"])), "")
        roles = [
            ["项目经理", "1", "全面负责项目计划、资源协调、进度质量控制与对接"],
            *[[f"实施顾问（{module['title']}）", "1", f"{module['title']}的调研、方案设计、配置与培训"] for module in self.modules[:4]],
            ["开发工程师", "2", f"个性化功能与{self.erp or '外部系统'}接口开发"],
            ["测试与运维工程师", "1", "测试、部署、数据初始化与上线支持"],
        ]
        organization = [_p("我方为本项目成立专项项目组，实行项目经理负责制，核心人员全职参与本项目，未经贵方同意不更换："),
                        table(["岗位", "人数", "主要职责"], roles, caption="项目组人员配置", widths=[4.6, 1.6, 10.2])]
        manager = ctx.profile["agent"] if "项目经理" in ctx.profile["agent"].get("title", "") else {}
        members = [["项目经理", manager.get("name") or ctx.need("项目经理姓名"), ctx.need("项目经理职称/证书、从业年限、类似项目经历"), "", ""]]
        members += [[role[0], "", "", "", ""] for role in roles[1:]]
        resume = ([_p(f"招标文件要求：“{requirement.rstrip('。')}。”拟派人员均满足上述要求，成员资历如下，资质证书及证明文件附后：")] if requirement else []) + [
            table(["岗位", "姓名", "职称／证书", "从业年限", "类似项目经历"], members, caption="项目组成员资历表", widths=[4.0, 2.4, 3.6, 2.0, 4.4]),
            placeholder("（此处粘贴：项目经理及主要实施人员的资质证书、社保证明等扫描件）"),
        ]
        self.confirm("项目组人员配置为建议配置，请按实际拟派人员核对，并补全成员资历表")
        return Draft(children=[("项目组织机构", organization), ("项目经理及主要实施人员资历", resume)])

    def schedule_stages(self) -> list[tuple[str, int, str, str]]:
        """(阶段, 天数, 主要工作, 里程碑成果)；按总工期分解，有上线阶段时按阶段排。"""
        total = self.days or 150
        if len(self.phases) >= 2:
            first, second = self.phases[0], self.phases[1]
            names = [("项目启动与需求调研", 10, "启动会、分部门调研、需求确认", "需求规格说明书"),
                     ("蓝图设计与确认", 10, "流程、编码、接口、数据方案设计与原型确认", "业务蓝图、接口方案"),
                     (f"{first}配置开发与测试", 24, f"{_join(self.phase_modules(first))}配置、开发、接口联调与测试", "测试报告"),
                     (f"{first}上线", 8, "数据初始化、培训、切换上线与现场值守", f"{first}上线确认单"),
                     (f"{second}配置开发与测试", 22, f"{_join(self.phase_modules(second))}配置、开发、联调与回归测试", "测试报告"),
                     (f"{second}上线", 8, "数据初始化、培训、切换上线与现场值守", f"{second}上线确认单"),
                     ("试运行与验收", 18, "问题跟踪优化、文档移交、配合验收", "验收报告")]
        else:
            names = [("项目启动与需求调研", 12, "启动会、分部门调研、需求确认", "需求规格说明书"),
                     ("蓝图设计与确认", 12, "流程、编码、接口、数据方案设计与原型确认", "业务蓝图、接口方案"),
                     ("配置开发与测试", 40, "系统配置、开发、接口联调与测试", "测试报告"),
                     ("上线准备与切换", 16, "数据初始化、培训、切换上线与现场值守", "上线确认单"),
                     ("试运行与验收", 20, "问题跟踪优化、文档移交、配合验收", "验收报告")]
        days = split_days(total, [weight for _name, weight, _work, _output in names])
        return [(name, day, work, output) for (name, _weight, work, output), day in zip(names, days)]

    def schedule(self, item: dict, criteria: list[dict]) -> Draft:
        total = self.days
        rows, start = [], 1
        for name, day, work, output in self.schedule_stages():
            rows.append([name, f"第{start}日", f"第{start + day - 1}日", work, output])
            start += day
        period = [_p((f"招标文件要求“{self.facts['schedule'].rstrip('。')}”。" if self.facts.get("schedule") else "") +
                     (f"我方承诺项目实施周期为{total}个自然日，自合同签订生效之日起计算，" if total else "我方承诺按招标文件要求的交付期完成全部工作，") + "进度计划如下："),
                  table(["阶段", "开始", "完成", "主要工作", "里程碑成果"], rows, caption="项目实施进度计划", widths=[3.6, 1.8, 1.8, 5.8, 3.4])]
        if not total:
            self.confirm("招标文件未识别到明确工期，进度计划按150日排定，请按实际交付期调整")
        self.confirm("项目实施进度计划为按总工期自动分解的排期，请按实际资源安排核对各阶段起止时间")
        children = [("实施周期与进度计划", period)]
        if self.phases:
            stage_rows = [[phase, _join(self.phase_modules(phase)) or "—",
                           str(sum(1 for m in self.modules for i in m["items"] if i.get("attrs", {}).get("上线阶段") == phase))] for phase in self.phases]
            children.append(("各阶段工作内容", [
                _p("按招标文件功能清单标明的上线阶段，各阶段建设内容如下，各功能的阶段归属见技术方案功能响应表："),
                table(["上线阶段", "建设板块", "功能数"], stage_rows, caption="各阶段建设内容", widths=[3.0, 10.4, 3.0]),
            ]))
        if len(self.phases) >= 2:
            children.append(("本阶段与后续阶段的衔接", [
                _p(f"总体蓝图在项目初期一次完成，{_join(self.phases)}共用同一套主数据、编码体系与接口规范，后续阶段不推翻前一阶段成果。"),
                _p(f"{self.phases[0]}上线时即为{self.phases[1]}预留数据与接口：批次、检验状态、设备编码等字段先行建立；"
                   f"{self.phases[1]}上线前，在测试环境用{self.phases[0]}的真实数据完成回归验证，切换安排在不影响生产的时段进行，已上线业务不中断。"),
            ]))
        children.append(("系统上线方案", [table(["环节", "工作内容"], [
            ["上线准备", "完成测试问题清零、基础与期初数据核对、岗位培训与考核、上线检查表逐项确认"],
            ["切换策略", "按阶段、按板块逐步切换；切换时点冻结旧系统单据，期初数据导入核对后启用新系统"],
            ["上线保障", "上线首周实施人员现场值守，问题当日响应、当日处理，每日召开上线碰头会"],
            ["应急回退", "保留旧系统与数据备份，出现重大问题时按预案回退，保证生产不中断"],
            ["上线确认", "运行稳定后双方签署上线确认单，转入试运行"],
        ], caption="系统上线方案", widths=[2.8, 13.6])]))
        return Draft(children=children)

    def acceptance(self, item: dict, criteria: list[dict]) -> Draft:
        nodes = []
        for name, _ratio in self.payment_stages():
            staged = re.search(r"[一二三四五六七八九十\d]+阶段上线", name)
            if staged:
                nodes.append([f"{staged.group(0)}确认", "该阶段功能测试通过，数据初始化与培训完成，系统切换上线", "贵方签署上线确认单"])
            elif "验收" in name and "运行" not in name:
                nodes.append(["最终验收", "各阶段均已上线、试运行稳定，问题全部整改，文档移交完成", "贵方签署验收报告"])
        return Draft(children=[
            ("验收依据", [_p("验收以合同、招标文件（含功能需求清单）、我方投标文件以及经双方确认的需求规格说明书、业务蓝图为依据。")]),
            ("验收阶段", [_p("验收与招标文件约定的付款节点对应，分步进行：" if nodes else "验收分上线确认与最终验收两步进行："),
                      table(["验收节点", "前提条件", "确认方式"], nodes or [
                          ["上线确认", "功能测试通过、数据初始化完成、系统切换上线", "贵方签署上线确认单"],
                          ["最终验收", "试运行稳定、问题全部整改、文档移交完成", "贵方签署验收报告"],
                      ], caption="验收阶段", widths=[5.4, 7.0, 4.0])]),
            ("验收内容与标准", [table(["验收内容", "验收标准"], [
                ["功能", "招标文件功能需求清单逐项实现，按测试用例验证通过"],
                ["接口", f"与{self.erp or '相关系统'}的接口按规范联通，数据一致"],
                ["数据", "基础数据、期初数据导入完整准确，经业务部门核对"],
                ["性能与安全", "达到双方确认的性能指标，权限、日志、备份功能有效"],
                ["文档", "需求、设计、测试、操作与维护手册等文档齐全，与系统一致"],
                ["培训", "各岗位人员完成培训并能独立操作"],
            ], caption="验收内容与标准", widths=[3.0, 13.4])]),
            ("验收流程", [_p("验收流程：我方自检→提交验收申请与验收资料→贵方组织验收→问题整改与复验→签署验收报告。验收中发现的问题登记到问题清单，我方限期整改并经贵方复核关闭。")]),
        ])

    def other_materials(self, item: dict, criteria: list[dict]) -> Draft:
        suggestions = [
            ("先统一编码与主数据", "物料、批次、库位、设备编码规则在蓝图阶段一次定稿，避免各系统各编各的码，后期难以贯通"),
            ("建立关键用户机制", "每个业务部门指定关键用户全程参与，上线后作为内部支持与持续优化的骨干"),
            ("制度与系统同步", "系统上线同步修订作业指导书与考核办法，把扫码、报工、检验录入纳入岗位职责"),
        ]
        if self.has(r"追溯|谱系"):
            suggestions.insert(1, ("以追溯为主线验收", "用“任取一个成品批次能否正反向追到原料”作为检验系统贯通程度的总口径"))
        if any("低代码" in requirement for requirement in self.platform_requirements):
            suggestions.append(("培养自有配置能力", "利用低代码配置工具培养贵方管理员，简单报表、表单调整可自行完成，降低后期成本"))
        rows = [[str(index), name, text] for index, (name, text) in enumerate(suggestions, start=1)]
        return Draft(children=[
            ("对本项目的合理化建议", [_p(f"结合同类项目经验，我方对{self.project}提出以下建议，供贵方参考，所有建议均不改变招标范围："),
                             table(["序号", "建议", "说明"], rows, caption="合理化建议", widths=[1.2, 4.4, 10.8])]),
            ("其他材料", [placeholder("（此处粘贴：投标人认为必要的其他材料，如软件著作权、荣誉证书、客户评价等）")]),
        ])


Topic = Callable[[ListedWriter, dict, list[dict]], Draft]
# (条目标题关键词, 写法, 由该条目响应的评分要素关键词)；自上而下首个命中生效
TOPICS: tuple[tuple[str, Topic | None, str], ...] = (
    (r"法定代表人身份证明|授权委托书", ListedWriter.legal_forms, ""),
    (r"营业执照|资质认证", ListedWriter.proofs, r"资质"),
    (r"综合情况|案例介绍", ListedWriter.company_profile, r"综合实力|业绩|案例"),
    (r"报价", None, r"价格|报价"),
    (r"承诺及补充意见|合同条款的承诺", ListedWriter.commitment, ""),
    (r"总体设计|总体方案", ListedWriter.overall_design, r"先进性|总体架构|整体设计"),
    (r"平台设计|平台架构", ListedWriter.platform_design, r"安全|稳定|易用|易维护"),
    (r"主要功能|功能.*性能", ListedWriter.functions, r"功能对|符合性|性能指标|品牌|市场"),
    (r"培训|售后", ListedWriter.training, r"技术服务|售后|培训"),
    (r"实施服务|技术支持", ListedWriter.implementation_service, ""),
    (r"难点|风险", ListedWriter.difficulties, ""),
    (r"接口", ListedWriter.interfaces, r"关联|接口|集成"),
    (r"数据导入|迁移", ListedWriter.migration, ""),
    (r"硬件", ListedWriter.hardware, ""),
    (r"实施周期|周期和计划", ListedWriter.schedule, r"实施方案|实施计划|阶段|上线"),
    (r"进度管理|实施计划", ListedWriter.progress_management, ""),
    (r"质保期外", ListedWriter.after_warranty, ""),
    (r"升级", ListedWriter.upgrade_service, ""),
    (r"日常维护|运行.*维护|运维", ListedWriter.maintenance, ""),
    (r"配合", ListedWriter.owner_support, ""),
    (r"项目经理|实施人员|项目组成员", ListedWriter.team, r"人员|项目组|团队"),
    (r"验收", ListedWriter.acceptance, ""),
    (r"其他材料|必要的其他", ListedWriter.other_materials, r"建议"),
)


def _topic(title: str) -> tuple[Topic | None, str]:
    return next(((writer, factors) for pattern, writer, factors in TOPICS if re.search(pattern, title)), (None, ""))


def assign_criteria(items: list[dict], criteria: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """评分项 → 负责响应它的清单条目（按 (分卷, 编号) 索引）；每个评分项只归一个条目。

    没有条目认领的技术评分项归到第一个有写法的技术条目，保证评分项都有响应章节可查。
    """
    leaves = [item for item in items if not any(other["number"].startswith(item["number"] + ".") and other["volume"] == item["volume"]
                                                for other in items)]
    owned: dict[tuple[str, str], list[dict]] = {}
    for criterion in criteria:
        target = next((item for item in leaves if _topic(item["title"])[1]
                       and re.search(_topic(item["title"])[1], criterion["factor"])), None)
        if target is None and criterion["volume"] == "technical":
            target = next((item for item in leaves if item["volume"] == "技术文件" and _topic(item["title"])[0]), None)
        if target is not None:
            owned.setdefault((target["volume"], target["number"]), []).append(criterion)
    return owned


def write_item(writer: ListedWriter, item: dict, criteria: list[dict]) -> Draft | None:
    """清单条目的正文；招标文件已给出格式原文、或没有对应写法的条目返回 None（按格式原文 / 粘贴位处理）。"""
    topic = _topic(item["title"])[0]
    if topic is None or item.get("template"):
        return None
    return topic(writer, item, criteria)
